"""Workspace provider/queue UI checks using rendered production HTML and assets."""
import asyncio,hashlib,json,mimetypes,os,sys
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import urlsplit,unquote
from playwright.async_api import async_playwright,expect

ROOT=Path(sys.argv[1]);ORIGIN='http://tamestorage:5000'
os.nice(19)

def fields(request):
    raw=b'Content-Type: '+request.headers['content-type'].encode()+b'\r\nMIME-Version: 1.0\r\n\r\n'+request.post_data_buffer
    result={}
    for part in BytesParser(policy=policy.default).parsebytes(raw).iter_parts():
        name=part.get_param('name',header='content-disposition')
        if not part.get_filename():result[name]=part.get_content()
    return result

async def exercise(browser,width,height,theme):
    context=await browser.new_context(viewport={'width':width,'height':height},color_scheme=theme,service_workers='block',reduced_motion='reduce')
    page=await context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    await page.add_init_script('localStorage.setItem("theme",'+json.dumps(theme)+');if(!localStorage.getItem("tamestorage.ai.chat.v5"))localStorage.setItem("tamestorage.ai.chat.v5",JSON.stringify([{id:"old-archive",role:"assistant",text:"ARCHIVE_PRIVATE_SENTINEL",provider:"excerpts"}]))')
    jobs={};requests=[];cancels=[]
    async def handler(route):
        request=route.request;path=unquote(urlsplit(request.url).path)
        if path.startswith('/static/'):
            file=ROOT/path.lstrip('/')
            if '..' not in Path(path).parts and file.is_file():return await route.fulfill(path=str(file),content_type=mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
            return await route.abort()
        if path=='/chat' and request.method=='GET':return await route.fulfill(path=str(ROOT/'page.html'),content_type='text/html')
        if path=='/chat' and request.method=='POST':
            data=fields(request);requests.append(data)
            key=hashlib.sha256(data['request_id'].encode()).hexdigest()
            provider='Google' if data['archive_mode']=='workspace_google' else 'Cyborg'
            jobs[key]={'ready':False,'provider':provider,'question':data['msg']}
            return await route.fulfill(json={'response':'Finding task: Layout check','workspace_job':key,'workspace_state':'queued','activity':{'step':'Finding task: Layout check','title':'Create Layout check','completed':0,'total':1},'provider':provider})
        if path.startswith('/chat/jobs/'):
            key=path.split('/')[3];job=jobs[key]
            if path.endswith('/cancel'):
                assert fields(request)['csrf']=='ui-synthetic-token'
                cancels.append(key);job['canceled']=True
            result=None
            if job.get('canceled'):result={'response':'Stopped.','workspace_result':True,'provider':job['provider'],'actions':[]}
            elif job['ready']:result={'response':'✓ Created task: Layout check','workspace_result':True,'provider':job['provider'],'actions':[{'action':'task.create','title':'Layout check','url':'/planner'}]}
            return await route.fulfill(json={'id':key,'status':'canceled' if job.get('canceled') else 'ready' if job['ready'] else 'running','provider':job['provider'],'activity':{'step':'Checking the saved task','title':job['question'],'completed':0,'total':1},'result':result})
        return await route.fulfill(json={})
    await page.route('**/*',handler)
    await page.goto(ORIGIN+'/chat',wait_until='networkidle')
    await expect(page.locator('#archiveMode')).to_have_value('workspace')
    assert await page.locator('optgroup[label="Workspace"] option').count()==2
    assert 'Times' not in await page.locator('body').evaluate('(e)=>getComputedStyle(e).fontFamily')
    await page.locator('#chatInput').fill('Create a task Layout check')
    await page.locator('#sendButton').click()
    await expect(page.get_by_title('Stop Workspace task')).to_be_visible()
    await expect(page.locator('.message.assistant .message-body').last).to_contain_text('Finding task: Layout check')
    assert 'Queued' not in await page.locator('.message.assistant').last.inner_text()
    first=next(iter(jobs));assert requests[-1]['archive_mode']=='workspace'
    await page.locator('.message.user').last.get_by_title('Regenerate from this message').click()
    assert len(requests)==1
    await expect(page.get_by_title('Stop Workspace task')).to_have_count(1)
    assert 'ARCHIVE_PRIVATE_SENTINEL' not in requests[-1]['history']
    await page.locator('#chatInput').fill('Keep this next draft')
    jobs[first]['ready']=True
    await expect(page.locator('.message.assistant .message-body').last).to_have_text('✓ Created task: Layout check',timeout=12000)
    await expect(page.locator('#chatInput')).to_have_value('Keep this next draft')
    assert await page.locator('.message.assistant .message-body').last.evaluate('(e)=>e.textContent.match(/Layout check/g).length')==1
    await page.reload(wait_until='networkidle')
    await expect(page.locator('.message.assistant .message-body').last).to_have_text('✓ Created task: Layout check')
    await page.locator('#archiveMode').select_option('workspace_google')
    await expect(page.locator('#composerContext')).to_contain_text('Automatic')
    await page.locator('#chatInput').fill('Create another task Layout check')
    await page.locator('#sendButton').click()
    await expect(page.get_by_title('Stop Workspace task')).to_be_visible()
    second=list(jobs)[-1];assert requests[-1]['archive_mode']=='workspace_google'
    assert first in json.loads(requests[-1]['context_jobs'])
    await page.reload(wait_until='networkidle')
    await expect(page.get_by_title('Stop Workspace task')).to_be_visible()
    jobs[second]['ready']=True
    await expect(page.get_by_title('Stop Workspace task')).to_have_count(0,timeout=12000)
    # Cancellation survives the asynchronous result path and keeps the chat usable.
    await page.locator('#chatInput').fill('Create a queued task to stop')
    await page.locator('#sendButton').click()
    await page.get_by_title('Stop Workspace task').click()
    await expect(page.locator('.message.assistant .message-body').last).to_have_text('Stopped.')
    assert len(cancels)==1
    await page.locator('#chatInput').fill('Create another queued task to stop')
    await page.locator('#sendButton').click()
    await expect(page.get_by_title('Stop Workspace task')).to_be_visible()
    await page.locator('.chat-options > summary').click()
    dialogs=[]
    async def accept(dialog):dialogs.append(dialog.message);await dialog.accept()
    page.once('dialog',accept)
    await page.locator('#clearChatBtn').click()
    await expect(page.locator('.welcome-message')).to_be_visible()
    assert len(cancels)==2 and 'stop' in dialogs[0]
    await page.locator('.chat-options > summary').click()
    geometry=await page.evaluate('''()=>{
      const h=document.querySelector('.chat-page-header').getBoundingClientRect(),a=document.querySelector('.chat-header-actions').getBoundingClientRect(),f=document.querySelector('#chatForm').getBoundingClientRect();
      return {width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight,header:h.right,actions:a.right,composer:f.bottom};}''')
    assert geometry['width']<=width+1 and geometry['height']<=height+1,geometry
    assert geometry['actions']<=geometry['header']+1 and geometry['composer']<=height+1,geometry
    assert await page.locator('#chatForm div').count()==0
    await page.screenshot(path=str(ROOT/f'workspace-{width}-{theme}.png'))
    assert not errors,errors
    await context.close()
    return {'viewport':f'{width}x{height}','theme':theme,'passed':True,'requests':len(requests),'cancellations':len(cancels),'geometry':geometry}

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(args=['--no-sandbox']);results=[]
        for width,height,theme in [(1440,1000,'dark'),(390,844,'dark'),(390,844,'light'),(320,568,'dark')]:results.append(await exercise(browser,width,height,theme))
        await browser.close();print(json.dumps(results))

asyncio.run(main())
