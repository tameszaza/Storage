import asyncio,base64,io,json,sys
from pathlib import Path
from email.parser import BytesParser
from email import policy
from urllib.parse import urlsplit,unquote
import mimetypes
from PIL import Image
from playwright.async_api import async_playwright,expect

if len(sys.argv) != 2: raise SystemExit('Usage: browser_assistant_images.py FIXTURE_DIRECTORY')
ROOT=Path(sys.argv[1])
ORIGIN='http://tamestorage:5000'

def png(color):
    image=Image.new('RGB',(180,120),color);out=io.BytesIO();image.save(out,'PNG');return out.getvalue()
RED=png('tomato');BLUE=png('royalblue');GREEN=png('seagreen')
def file(name,data=RED):return {'name':name,'mimeType':'image/png','buffer':data}
def multipart(request):
    raw=request.post_data_buffer or b''
    msg=BytesParser(policy=policy.default).parsebytes(b'Content-Type: '+request.headers['content-type'].encode()+b'\r\nMIME-Version: 1.0\r\n\r\n'+raw)
    fields={};files=[]
    for part in msg.iter_parts():
        name=part.get_param('name',header='content-disposition')
        if part.get_filename():files.append(part.get_filename())
        else:fields[name]=part.get_content()
    return fields,files

async def exercise(browser,width,height,label):
    context=await browser.new_context(viewport={'width':width,'height':height},color_scheme='dark',reduced_motion='reduce',is_mobile=width<500,has_touch=width<500)
    page=await context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    requests=[];plans=[];previews=[];preview_load={"active":0,"max":0}
    await page.add_init_script('''localStorage.setItem('theme','dark');window.__revoked=[];const revoke=URL.revokeObjectURL.bind(URL);URL.revokeObjectURL=url=>{window.__revoked.push(url);revoke(url);};''')
    async def handler(route):
        request=route.request
        if request.method=='GET':return await route.fulfill(body=(ROOT/'page.html').read_bytes(),content_type='text/html')
        fields,files=multipart(request)
        if fields.get('preview')=='1':
            previews.append(files);preview_load['active']+=1;preview_load['max']=max(preview_load['max'],preview_load['active']);await asyncio.sleep(.08);preview_load['active']-=1;return await route.fulfill(body=GREEN,content_type='image/jpeg')
        requests.append({'files':files,'id':fields['request_id'],'text':fields.get('msg'),'regenerate':fields.get('archive_refresh')})
        plan=plans.pop(0) if plans else {}
        if plan.get('event'):await plan['event'].wait()
        await route.fulfill(status=plan.get('status',200),json=plan.get('response',{'response':'Compared all attached images.'}))
    async def asset(route):
        rel=unquote(urlsplit(route.request.url).path).removeprefix('/static/')
        if '..' in Path(rel).parts:return await route.abort()
        target=ROOT/'static'/rel
        if target.is_file():return await route.fulfill(path=str(target),content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
        await route.abort()
    await page.route('**/static/**',asset)
    await page.route('**/chat',handler)
    await page.route('**/static/js/chat.js*',lambda route:route.fulfill(body=(ROOT/'chat.js').read_bytes(),content_type='application/javascript'))
    await page.route('**/static/css/chat-page.css*',lambda route:route.fulfill(body=(ROOT/'chat-page.css').read_bytes(),content_type='text/css'))
    await page.route('**/obsidian-images/**',lambda route:route.fulfill(body=RED,content_type='image/png'))
    await page.goto(ORIGIN+'/chat',wait_until='networkidle')
    await expect(page.locator('#chatForm')).to_be_visible()
    assert await page.locator('#chatForm div').count()==0,'Nested composer div'
    assert 'Times' not in await page.locator('body').evaluate('(body)=>getComputedStyle(body).fontFamily'),'Production styles did not load'
    async with page.expect_file_chooser() as info:await page.get_by_role('button',name='Attach images',exact=True).click()
    chooser=await info.value;assert chooser.is_multiple()
    await chooser.set_files([file('red.png'),file('blue.png',BLUE)])
    await expect(page.locator('#imagePreviews li')).to_have_count(2)
    await expect(page.locator('#sendButton')).to_be_enabled()
    assert await page.locator('#imagePreviews img').evaluate_all('(images)=>images.every(image=>image.complete&&image.naturalWidth>0)')
    await page.get_by_role('button',name='Remove red.png',exact=True).click()
    await expect(page.locator('#imagePreviews li')).to_have_count(1)
    assert await page.evaluate('window.__revoked.length')>=1
    await page.locator('#imageInput').set_input_files([file('green.png',GREEN)])
    await expect(page.locator('#imagePreviews li')).to_have_count(2)
    # Paste multiple files into the same flat composer.
    await page.locator('#chatInput').evaluate('''(input,images)=>{const dt=new DataTransfer();for(const image of images){const bytes=Uint8Array.from(atob(image.data),x=>x.charCodeAt(0));dt.items.add(new File([bytes],image.name,{type:'image/png'}));}input.dispatchEvent(new ClipboardEvent('paste',{bubbles:true,cancelable:true,clipboardData:dt}));}''',[{'name':'pasted-a.png','data':base64.b64encode(RED).decode()},{'name':'pasted-b.png','data':base64.b64encode(BLUE).decode()}])
    await expect(page.locator('#imagePreviews li')).to_have_count(4)
    await page.locator('#chatDropZone').evaluate('''(zone,image)=>{const dt=new DataTransfer();dt.items.add(new File([Uint8Array.from(atob(image),x=>x.charCodeAt(0))],'dropped.png',{type:'image/png'}));zone.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:dt}));}''',base64.b64encode(GREEN).decode())
    await expect(page.locator('#imagePreviews li')).to_have_count(5)
    await expect(page.locator('#sendButton')).to_be_enabled()
    await page.locator('#chatInput').fill('Compare these five images')
    await page.locator('#chatForm').scroll_into_view_if_needed()
    await page.screenshot(path=str(ROOT/(label+'.png')),full_page=True)
    metrics=await page.evaluate('''()=>{const form=document.querySelector('#chatForm'),input=document.querySelector('#chatInput'),previews=document.querySelector('#imagePreviews');return {viewport:innerWidth,page:document.documentElement.scrollWidth,previews:previews.clientWidth,removeWidth:document.querySelector('.chat-image-remove').getBoundingClientRect().width,display:getComputedStyle(form).display,formWidth:form.clientWidth,inputWidth:input.clientWidth,previewBottom:previews.getBoundingClientRect().bottom,inputTop:input.getBoundingClientRect().top};}''')
    assert metrics['page']<=metrics['viewport']+1,metrics
    assert metrics['display']=='grid' and metrics['inputWidth']>=metrics['formWidth']*.8 and metrics['previewBottom']<=metrics['inputTop'],metrics
    if width<500:assert metrics['removeWidth']>=44,metrics
    gate=asyncio.Event();plans.append({'event':gate})
    await page.locator('#sendButton').click()
    await expect(page.locator('#imagePreviews')).to_be_hidden()
    await expect(page.locator('.message.user .message-image')).to_have_count(5)
    await page.wait_for_function('document.querySelector("#sendButton").disabled')
    await page.locator('#imageInput').set_input_files([file('next-draft.png',GREEN)])
    gate.set()
    await expect(page.locator('#sendButton')).to_be_enabled()
    await expect(page.locator('#imagePreviews li')).to_have_count(1)
    assert requests[0]['files']==['blue.png','green.png','pasted-a.png','pasted-b.png','dropped.png'],requests
    await page.get_by_role('button',name='Remove next-draft.png').click()
    # Failed request retains all files for an exact retry, with the same receipt ID.
    await page.locator('#imageInput').set_input_files([file('retry-one.png'),file('retry-two.png',BLUE)])
    await expect(page.locator('#sendButton')).to_be_enabled();await page.locator('#chatInput').fill('Retry this batch')
    plans.append({'status':503,'response':{'response':'Fixture unavailable'}})
    await page.locator('#sendButton').click()
    await expect(page.locator('.message.assistant').last).to_contain_text('Regenerate')
    await expect(page.locator('#sendButton')).to_be_enabled()
    await page.locator('.message.user').last.get_by_role('button',name='Regenerate',exact=False).click()
    await expect(page.locator('.message.assistant').last).to_contain_text('Compared all')
    assert requests[-1]['files']==requests[-2]['files'] and requests[-1]['id']==requests[-2]['id']
    # Browser-unreadable files get a temporary preview, without an AI request.
    await page.locator('#imageInput').set_input_files([{'name':name,'mimeType':'image/heic','buffer':b'fixture-unreadable-heic'} for name in ['camera.heic','camera-2.heic']])
    await expect(page.locator('#sendButton')).to_be_enabled()
    await expect(page.locator('#imagePreviews img')).to_have_count(2)
    assert await page.locator('#imagePreviews img').evaluate_all('(images)=>images.every(image=>image.complete&&image.naturalWidth>0)')
    assert previews[-2:]==[['camera.heic'],['camera-2.heic']] and preview_load['max']==1
    await page.get_by_role('button',name='Remove camera.heic',exact=True).click();await page.get_by_role('button',name='Remove camera-2.heic',exact=True).click()
    # Selection cap is visible and does not overflow the mobile page.
    await page.locator('#imageInput').set_input_files([file(f'limit-{i}.png') for i in range(9)])
    await expect(page.locator('#imagePreviews li')).to_have_count(8)
    await expect(page.locator('#attachmentStatus')).to_contain_text('8 images')
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    for _ in range(8):await page.locator('.chat-image-remove').first.click()
    # Partial separate saves map each stored photo back to its original position.
    await page.locator('#imageInput').set_input_files([file('first.png',RED),file('second.png',GREEN),file('third.png',BLUE)])
    await expect(page.locator('#sendButton')).to_be_enabled();await page.locator('#chatInput').fill('log these 3 separately')
    stored=['https://server.tailb7f57c.ts.net/obsidian-images/'+c*64+'.png' for c in ('a','b','c')]
    actions=[{'action':'diary.saved','entry_id':str(i),'title':'Same title','image_numbers':[i],'image_urls':[stored[i-1]]} for i in (1,3)]
    plans.append({'response':{'response':'Two saved; retry the unfinished image.','workspace_result':True,'actions':actions}})
    await page.locator('#sendButton').click();await expect(page.locator('.message.assistant').last).to_contain_text('Two saved')
    history=await page.evaluate('JSON.parse(localStorage.getItem("tamestorage.ai.chat.v5"))')
    saved=[m for m in history if m['role']=='user'][-1]['images']
    assert [v['url'] for v in saved]==[stored[0],'',stored[2]],saved
    plans.append({'response':{'response':'All three saved.','workspace_result':True,'actions':actions+[{'action':'diary.saved','entry_id':'2','title':'Meal','image_numbers':[2],'image_urls':[stored[1]]}]}})
    await page.locator('.message.user').last.get_by_role('button',name='Regenerate',exact=False).click()
    await expect(page.locator('.message.assistant').last).to_contain_text('All three saved')
    assert requests[-1]['id']==requests[-2]['id'] and requests[-1]['regenerate']=='1'
    assert requests[-1]['files']==['first.png','second.png','third.png']
    history=await page.evaluate('JSON.parse(localStorage.getItem("tamestorage.ai.chat.v5"))')
    assert [[v['url'] for v in m['images']] for m in history if m['role']=='user'][-1]==stored
    # Existing single-image history remains readable; no files/blob URLs enter localStorage.
    history=await page.evaluate('JSON.parse(localStorage.getItem("tamestorage.ai.chat.v5"))')
    assert all('file' not in image and not image.get('url','').startswith('blob:') for m in history for image in m.get('images',[]))
    assert not errors,errors
    result={'viewport':label,'pickerPasteDrop':True,'perImageRemoval':True,'orderedMultipart':True,'nextDraftPreserved':True,'sameIdRetry':True,'fallbackPreview':True,'limitAndLayout':True,'noNestedComposerDivs':True,'requests':len(requests),'pageErrors':errors}
    await context.close();return result

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        try:
            results=[]
            for width,height,label in [(1440,1000,'desktop'),(390,844,'mobile')]:results.append(await exercise(browser,width,height,label))
            print(json.dumps(results))
        except Exception:
            for context in browser.contexts:
                for page in context.pages:
                    await page.screenshot(path=str(ROOT/'failure.png'),full_page=True)
            raise
        finally:await browser.close()

asyncio.run(main())
