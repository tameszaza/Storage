"""Production shell/assets, synthetic conversations and mocked assistant transport."""
import asyncio,json,sys
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright,expect

FIXTURE=json.loads(Path(sys.argv[1]).read_text())
ORIGIN='http://tamestorage:5000'
SOURCE={'url':'/admin/chat-archive/fixture/speaker#m-user','title':'Cyborg speaker mode','created':'2026-10-05T10:00:00+00:00','role':'user','text':'Keep speaker mode manual. Do not enable automatic startup.','quote':'Keep speaker mode manual.','id':1}

async def exercise(browser,width,theme):
    context=await browser.new_context(viewport={'width':width,'height':900 if width>500 else 844},color_scheme=theme,is_mobile=width<500,has_touch=width<500,reduced_motion='reduce',service_workers='block')
    page=await context.new_page();errors=[];requests=[];jobs=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    await page.add_init_script('localStorage.setItem("theme",'+json.dumps(theme)+');')
    async def handler(route):
        request=route.request;url=urlsplit(request.url)
        if url.path.startswith('/static/'):
            import mimetypes
            path=Path('/srv/tamestorage/.tamestorage_state/chat-archive/ui-fixture/static')/url.path.removeprefix('/static/')
            if path.is_file():return await route.fulfill(path=str(path),content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
            return await route.abort()
        if url.path=='/chat':
            if request.method=='GET':return await route.fulfill(body=FIXTURE['chat'],content_type='text/html')
            body=request.post_data_buffer or b'';requests.append(body.decode(errors='replace'))
            if b'cyborg' in body:
                return await route.fulfill(json={'response':'Queued for Cyborg.','archive_sources':[SOURCE],'archive_job':'a'*64,'archive_state':'queued','provider':'Cyborg'})
            return await route.fulfill(json={'response':'Matching historical messages.','archive_sources':[SOURCE],'provider':'excerpts'})
        if url.path.startswith('/admin/chat-archive/jobs/'):
            jobs.append(url.path)
            return await route.fulfill(json={'id':'a'*64,'status':'ready','result':{'response':'Speaker mode was kept manual [1].','archive_sources':[SOURCE],'provider':'Cyborg'}})
        if url.path=='/admin/chat-archive/search':return await route.fulfill(json={'sources':[SOURCE],'semantic':'not requested'})
        if url.path=='/admin/chat-archive':return await route.fulfill(body=FIXTURE['archive'],content_type='text/html')
        if url.path.startswith('/admin/chat-archive/fixture/'):return await route.fulfill(body=FIXTURE['conversation'],content_type='text/html')
        await route.continue_()
    await page.route('**/*',handler)
    await page.goto(ORIGIN+'/chat?archive=1',wait_until='networkidle')
    await expect(page.locator('#archiveMode')).to_have_value('cyborg')
    assert await page.locator('#chatForm div').count()==0
    assert 'Times' not in await page.locator('body').evaluate('(body)=>getComputedStyle(body).fontFamily')
    await page.locator('#archiveMode').select_option('excerpts')
    await page.locator('#chatInput').fill('How was speaker startup configured?')
    await page.locator('#sendButton').click()
    await expect(page.locator('.archive-source-summary').last).to_have_text('1 source')
    await page.locator('.archive-source-summary').last.click()
    await expect(page.locator('.archive-source-list a').last).to_have_attribute('href',SOURCE['url'])
    await expect(page.locator('.archive-source-list blockquote').last).to_have_text(SOURCE['quote'])
    await expect(page.locator('.archive-source-list small').last).to_contain_text('2026-10-05')
    assert 'excerpts' in requests[-1]
    await page.locator('.chat-options > summary').click()
    if await page.locator('#exportChatBtn').is_visible():
        async with page.expect_download() as exported:await page.locator('#exportChatBtn').click()
        download=await exported.value
        exported_text=Path(await download.path()).read_text()
        assert SOURCE['quote'] in exported_text and SOURCE['url'] in exported_text
    await page.locator('.chat-options > summary').click()
    await page.screenshot(path=f'/tmp/archive-chat-{width}-{theme}.png',full_page=True)
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    await page.locator('#archiveMode').select_option('cyborg')
    await page.locator('#chatInput').fill('What did I decide about speaker mode?')
    await page.locator('#sendButton').click()
    await expect(page.locator('.archive-status').last).to_have_text('Queued · Cyborg')
    await expect(page.locator('.message-body').last).to_contain_text('Speaker mode was kept manual',timeout=15000)
    assert jobs and 'cyborg' in requests[-1]
    assert not await page.locator('.archive-status').count()
    await page.reload(wait_until='networkidle')
    await expect(page.locator('.message-body').last).to_contain_text('Speaker mode was kept manual')
    page.on('dialog',lambda dialog:dialog.accept())
    await page.locator('.chat-options > summary').click()
    await page.locator('#clearChatBtn').click()
    await page.locator('.chat-options > summary').click()
    await page.get_by_role('button',name='Plan my week',exact=True).click()
    await expect(page.locator('#archiveMode')).to_have_value('workspace')
    await page.goto(ORIGIN+'/admin/chat-archive',wait_until='networkidle')
    await page.locator('#archiveSearchForm input[name=q]').fill('speaker')
    await page.locator('#archiveSearchForm button[type=submit]').click()
    await expect(page.locator('#archiveResults .card')).to_have_count(1)
    await expect(page.locator('#archiveResults time')).to_contain_text('2026-10-05')
    await page.screenshot(path=f'/tmp/archive-browser-{width}-{theme}.png',full_page=True)
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    assert not errors,errors
    print(json.dumps({'viewport':width,'theme':theme,'passed':True,'assistant_requests':len(requests),'queue_checks':len(jobs)}),flush=True)
    await context.close()

async def main():
    async with async_playwright() as play:
        browser=await play.chromium.launch(headless=True,args=['--no-sandbox'])
        for width,theme in [(1440,'dark'),(390,'dark'),(1440,'light'),(390,'light')]:await exercise(browser,width,theme)
        await browser.close()

asyncio.run(main())
