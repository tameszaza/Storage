"""Opt-in real-provider multi-step/context evaluation; synthetic accounts only."""
import argparse,json,os,time
from pathlib import Path
from lib.config import Config
from lib.workspace_store import Store
import test_workspace_agent as base

parser=argparse.ArgumentParser();parser.add_argument('provider',choices=['cyborg','google']);parser.add_argument('--cases',default='compound,followup,files,clarification,vision')
args=parser.parse_args();os.nice(19)
fixture=base.AgentTests();fixture.setUp()
fixture.app.config.update(GEMINI_MODEL=Config.GEMINI_MODEL,GEMINI_CONFIG_PATH=Config.GEMINI_CONFIG_PATH)
results=[];prior=None

def run(label,question,check,**extra):
    started=time.monotonic();job=fixture.enqueue(question,key=label,provider=args.provider,**extra)
    for count in range(180):
        current=fixture.get(job['id'])
        if current['result']:break
        fixture.tick();current=fixture.get(job['id'])
        with Store(fixture.queue) as q:state=json.loads(q.db.execute('SELECT state FROM jobs WHERE id=?',(job['id'],)).fetchone()[0])
        print(json.dumps({'case':label,'step':current['activity']['step'],'outcomes':str(current['activity']['completed'])+'/'+str(current['activity']['total']),'code':current['code'],'feedback':state.get('feedback','')[:400]}),flush=True)
        if current['code'] in ('waiting-provider','provider-cooldown','waiting-previous'):
            if time.monotonic()-started>600:break
            with Store(fixture.queue) as q:retry=q.db.execute('SELECT retry_at FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]
            time.sleep(max(.1,min(10,retry-time.time()+.1)))
    current=fixture.get(job['id']);passed=current['status']=='ready' and check(current)
    results.append({'case':label,'passed':passed,'status':current['status'],'code':current['code'],'seconds':round(time.monotonic()-started,2),'response':(current.get('result') or {}).get('response','')})
    print(json.dumps(results[-1],ensure_ascii=False),flush=True)
    return job['id']

try:
    cases=set(args.cases.split(','))
    if 'compound' in cases:
        prior=run('compound','Create a task called Review circuits due 2026-10-28 and create a task called Practice phasors due 2026-10-30.',
            lambda r:all(any(t['title'].casefold()==title and t['due_date']==day for t in fixture.tasks()) for title,day in [('review circuits','2026-10-28'),('practice phasors','2026-10-30')]))
    if 'followup' in cases and prior:
        prior=run('followup','Set the first one to high priority.',lambda r:any(t['title'].casefold()=='review circuits' and t['priority']=='high' for t in fixture.tasks()),context_jobs=[prior])
        run('followup-done','Mark it done.',lambda r:any(t['title'].casefold()=='review circuits' and t['completed'] for t in fixture.tasks()),context_jobs=[prior])
    if 'files' in cases:
        folder=fixture.root/'uploads/alice';(folder/'circuits-a.txt').write_text('Synthetic circuits A');(folder/'circuits-b.txt').write_text('Synthetic circuits B')
        run('files','Organize both circuits-a.txt and circuits-b.txt in alice into a new course folder called Circuits. Do all of the file moves.',
            lambda r:(folder/'Circuits/circuits-a.txt').is_file() and (folder/'Circuits/circuits-b.txt').is_file() and not (folder/'circuits-a.txt').exists() and not (folder/'circuits-b.txt').exists())
    if 'clarification' in cases:
        run('clarification','Inspect my tasks and propose a sensible study order. Do not modify any task.',lambda r:all(t['title'].casefold() in ('review circuits','practice phasors') for t in fixture.tasks()))
    if 'vision' in cases:
        import io
        from PIL import Image
        buffer=io.BytesIO();Image.new('RGB',(120,120),'blue').save(buffer,'PNG')
        with Store(fixture.queue) as q:job=q.enqueue('alice','vision',args.provider,'What color is this image? Chat only; do not save.',dict(include_context=False,clock=base.CLOCK,history=[],context='',preferences={}),[buffer.getvalue()])
        for _ in range(20):
            current=fixture.get(job['id'])
            if current['result']:break
            fixture.tick()
        current=fixture.get(job['id']);results.append({'case':'vision','passed':current['status']=='ready' and 'blue' in (current.get('result') or {}).get('response','').lower(),'status':current['status']})
        print(json.dumps(results[-1]),flush=True)
finally:
    print(json.dumps({'provider':args.provider,'results':results},ensure_ascii=False),flush=True);fixture.doCleanups()
raise SystemExit(not results or not all(r['passed'] for r in results))
