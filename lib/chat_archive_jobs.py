"""Bounded steps called by the existing dispatcher; no additional daemon."""
import base64
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit

from lib.chat_archive import Archive, GUIDE, SCHEMA, evidence_payload, validated_answer, exact_answer

def helper(route,body=None,timeout=6,retry=False,blob=None,method='POST'):
    config=json.loads(Path('/run/chat-archive-helper.json').read_text())
    url=urlsplit(config['url']);connection=http.client.HTTPConnection(url.hostname,url.port,timeout=2)
    try:
        connection.connect();connection.sock.settimeout(timeout)
        connection.request(method,route,body=blob if blob is not None else json.dumps(body).encode() if body is not None else None,headers={'Authorization':'Bearer '+config['token'],'Content-Type':'application/octet-stream' if blob is not None else 'application/json'})
        response=connection.getresponse();raw=response.read(2*1024*1024+1)
        if response.status!=200 or len(raw)>2*1024*1024:raise OSError('Cyborg paused')
        return json.loads(raw)
    finally:connection.close()

def cyborg_answer(question,sources):
    payload={'action':'archive','question':question,'sources':evidence_payload(question,sources)['sources']}
    connection=http.client.HTTPConnection('100.66.32.22',9019,timeout=3)
    try:
        connection.connect();connection.sock.settimeout(240)
        connection.request('POST','/v1/assist',json.dumps(payload).encode(),{'Content-Type':'application/json'})
        response=connection.getresponse();raw=response.read(100000)
        if response.status!=200:raise OSError('Cyborg paused')
        value=json.loads(raw)
        return validated_answer(value['result'],sources,'Cyborg')
    finally:connection.close()

def step(root=None):
    with Archive(root) as archive:
        archive.backfill(8)
        row=archive.db.execute("SELECT * FROM archive_answers WHERE status IN ('queued','running') AND lease<? ORDER BY created LIMIT 1",(time.time(),)).fetchone()
        if row:
            with archive.db:
                claimed=archive.db.execute("UPDATE archive_answers SET status='running',lease=?,attempts=attempts+1 WHERE id=? AND lease<?",(time.time()+300,row['id'],time.time())).rowcount
            if claimed:
                try:
                    sources=json.loads(row['evidence'])
                    if not sources:
                        lookup=archive.search(row['question'],semantic=True,helper=helper)
                        sources=lookup['sources']
                        if not sources and (lookup['semantic']=='waiting for Cyborg' or archive.db.execute('SELECT 1 FROM archive_embeddings LIMIT 1').fetchone()):
                            raise OSError('Waiting for searchable vectors')
                    # Source changes invalidate queued evidence before sending it.
                    for source in sources:
                        current=archive.db.execute('SELECT digest FROM archive_chunks WHERE id=?',(source['id'],)).fetchone()
                        if not current or current[0]!=source['digest'] or archive.db.execute('SELECT 1 FROM archive_dirty WHERE scope=? AND cid=?',(source['scope'],source['cid'])).fetchone():raise ValueError('Evidence changed')
                    result=cyborg_answer(row['question'],sources) if sources else exact_answer([])
                    with archive.db:archive.db.execute("UPDATE archive_answers SET status='ready',result=?,code='',lease=0 WHERE id=?",(json.dumps(result,ensure_ascii=False),row['id']))
                    return archive.pending()
                except ValueError:
                    with archive.db:archive.db.execute("UPDATE archive_answers SET status='failed',code='evidence-changed',lease=0 WHERE id=?",(row['id'],))
                except Exception:
                    with archive.db:archive.db.execute("UPDATE archive_answers SET status='queued',code='waiting-cyborg',lease=? WHERE id=?",(time.time()+60,row['id']))
        rows=archive.db.execute('SELECT c.id,c.text,c.digest FROM archive_embeddings j JOIN archive_chunks c ON c.id=j.id AND c.digest=j.digest ORDER BY j.id LIMIT 32').fetchall()
        if rows:
            try:
                result=helper('/jobs/embed',{'texts':[r['text'] for r in rows]},timeout=120)
                raw=base64.b64decode(result['vectors'],validate=True)
                if result.get('dimensions')!=256 or result.get('count')!=len(rows) or len(raw)!=len(rows)*1024:raise ValueError('Bad vectors')
                with archive.db:
                    for i,row in enumerate(rows):
                        archive.db.execute('UPDATE archive_chunks SET vector=? WHERE id=? AND digest=?',(raw[i*1024:(i+1)*1024],row['id'],row['digest']))
                        archive.db.execute('DELETE FROM archive_embeddings WHERE id=? AND digest=?',(row['id'],row['digest']))
            except Exception:return archive.pending()
        epoch=archive.db.execute("SELECT value FROM archive_meta WHERE key='epoch'").fetchone()[0]
        try:
            health=helper('/health',method='GET',timeout=3)
            if health.get('archive_epoch')!=epoch:
                with archive.db:archive.db.execute('INSERT OR IGNORE INTO archive_mirror SELECT scope,cid,1 FROM archive_docs')
        except Exception:return archive.pending()
        # Separate mirror namespace: lecture vectors and epochs are never changed.
        documents=archive.db.execute('SELECT scope,cid,version FROM archive_mirror WHERE NOT EXISTS(SELECT 1 FROM archive_chunks c JOIN archive_embeddings j ON j.id=c.id WHERE c.scope=archive_mirror.scope AND c.cid=archive_mirror.cid) LIMIT 4').fetchall()
        for scope,cid,version in documents:
            lines=[json.dumps({'path':scope+'/'+cid,'epoch':epoch})]
            lines.extend(json.dumps([row[0],base64.b64encode(row[1]).decode()]) for row in archive.db.execute('SELECT id,vector FROM archive_chunks WHERE scope=? AND cid=? AND vector IS NOT NULL',(scope,cid)))
            try:
                helper('/jobs/archive-vectors',timeout=60,blob=('\n'.join(lines)+'\n').encode())
                with archive.db:archive.db.execute('DELETE FROM archive_mirror WHERE scope=? AND cid=? AND version=?',(scope,cid,version))
            except Exception:break
        return archive.pending()

def google_answer(question,sources,model='gemini-3.8-flash'):
    from google.genai import types
    from lib.ai_client import get_client
    response=get_client().models.generate_content(model=model,contents=json.dumps(evidence_payload(question,sources),ensure_ascii=False),
      config=types.GenerateContentConfig(temperature=0,system_instruction=GUIDE,response_mime_type='application/json',response_schema=SCHEMA,max_output_tokens=4096,
         thinking_config=types.ThinkingConfig(thinking_level='low')))
    return validated_answer(json.loads(response.text),sources,model)
