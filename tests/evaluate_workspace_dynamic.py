"""Opt-in live model checks against synthetic local files and mocked vault publishing."""
import argparse,hashlib,io,json,os,time
from pathlib import Path
from unittest.mock import patch
from PIL import Image,ImageDraw
from lib.config import Config
from lib.workspace_store import Store
import test_workspace_agent as base

parser=argparse.ArgumentParser();parser.add_argument('provider',choices=['cyborg','google']);parser.add_argument('--cases',default='separate,report,chain')
args=parser.parse_args();os.nice(19)
fixture=base.AgentTests();fixture.setUp();fixture.app.config.update(GEMINI_MODEL=Config.GEMINI_MODEL,GEMINI_CONFIG_PATH=Config.GEMINI_CONFIG_PATH,ASSISTANT_DIARY_STATE=fixture.root/'diary.sqlite')
results=[];saves=[];image_urls={}

def publish(value):
    saves.append(value)
    return {'success':True,'path':'Diary/'+value['date']+'.md','status':'saved'}
def image_store(raw):return {'url':image_urls[raw]}

def run(label,question,check,images=None,owner='alice'):
    started=time.monotonic()
    if images:
        with Store(fixture.queue) as q:job=q.enqueue(owner,label,args.provider,question,dict(include_context=True,clock=base.CLOCK,history=[],context='',preferences={}),images)
    else:job=fixture.enqueue(question,key=label,owner=owner,provider=args.provider)
    for _ in range(220):
        current=fixture.get(job['id'],owner)
        if current['result']:break
        fixture.tick();current=fixture.get(job['id'],owner)
        with Store(fixture.queue) as q:state=json.loads(q.db.execute('SELECT state FROM jobs WHERE id=?',(job['id'],)).fetchone()[0])
        print(json.dumps({'case':label,'step':current['activity']['step'],'code':current['code'],'feedback':state.get('feedback','')[:300]}),flush=True)
        if current['code'] in ('waiting-provider','provider-cooldown','waiting-previous','waiting-storage'):
            if time.monotonic()-started>600:break
            with Store(fixture.queue) as q:retry=q.db.execute('SELECT retry_at FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]
            time.sleep(max(.1,min(10,retry-time.time()+.1)))
    current=fixture.get(job['id'],owner)
    item={'case':label,'passed':current['status']=='ready' and check(current),'status':current['status'],'code':current['code'],'seconds':round(time.monotonic()-started,2),'response':(current.get('result') or {}).get('response','')}
    results.append(item);print(json.dumps(item,ensure_ascii=False),flush=True)

try:
 with patch('lib.assistant_diary._publish',side_effect=publish),patch('routes.obsidian_images.store_image',side_effect=image_store):
    cases=set(args.cases.split(','))
    if 'separate' in cases or 'group' in cases:
        photos=[]
        for color,label in [('red','WORKOUT 25:00'),('green','LUNCH 7.50'),('blue','BUILDING A')]:
            image=Image.new('RGB',(360,180),'white');draw=ImageDraw.Draw(image);draw.rectangle((10,10,350,110),fill=color);draw.text((20,130),label,fill='black')
            stream=io.BytesIO();image.save(stream,'PNG');raw=stream.getvalue();photos.append(raw);image_urls[raw]='https://server.tailb7f57c.ts.net/obsidian-images/'+hashlib.sha256(raw).hexdigest()+'.png'
        if 'separate' in cases:
            run('separate','Log these 3 separately.',lambda r:len(saves)==3 and sorted(v['images'] for v in saves)==sorted([[image_urls[p]] for p in photos]) and len({v['id'] for v in saves})==3,photos,'Admin')
        if 'group' in cases:
            before=len(saves)
            run('group','Log the first two together and the third separately.',lambda r:len(saves)==before+2 and [v['images'] for v in saves[before:]]==[[image_urls[p] for p in photos[:2]],[image_urls[photos[2]]]],photos,'Admin')
    if 'report' in cases:
        folder=fixture.root/'uploads/alice';(folder/'sales.csv').write_text('Topic,Minutes\nCircuits,30\nMechanics,45\n')
        run('report','Turn alice/sales.csv into a Markdown report at alice/report.md. Include both topics and their minutes.',lambda r:(folder/'report.md').is_file() and all(v in (folder/'report.md').read_text().lower() for v in ['circuits','mechanics','30','45']))
    if 'chain' in cases:
        folder=fixture.root/'uploads/alice'
        run('chain','Create a folder alice/Reports, write a file alice/Reports/study.md with heading Study log and the line Circuits: 30 min, then add a task called Check study report due 2026-10-30.',lambda r:not (folder/'alice').exists() and (folder/'Reports/study.md').is_file() and '30' in (folder/'Reports/study.md').read_text() and any(t['title'].casefold()=='check study report' and t['due_date']=='2026-10-30' for t in fixture.tasks()))
finally:
 print(json.dumps({'provider':args.provider,'results':results,'diary_outputs':[{'title':v['title'],'images':v['images'],'characters':len(v['markdown'])} for v in saves]},ensure_ascii=False),flush=True)
 fixture.doCleanups()
raise SystemExit(not results or not all(r['passed'] for r in results))
