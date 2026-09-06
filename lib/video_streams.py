"""Open supported pages normally, then save complete unencrypted VOD streams."""
import ipaddress
import shutil
import socket
import subprocess
import time
from urllib.parse import urlsplit
import m3u8
from playwright.sync_api import sync_playwright
from lib import video_downloads as jobs

def cancelled(key):
    if jobs.deleting(key):
        raise ValueError('Download cancelled.')

def fetch(url, headers, limit):
    conn, response = jobs.public_response(url, headers)
    try:
        body=response.read(limit+1)
        expected=int(response.getheader('Content-Length','0'))
        if expected and len(body)!=expected:
            raise ValueError('The source ended the response before all bytes arrived.')
        if len(body)>limit:
            raise ValueError('The stream returned an unexpectedly large response.')
        return body
    finally:
        conn.close()

def vod_manifest(url, headers, depth=0):
    if depth>4:
        raise ValueError('Too many nested playlists.')
    text=fetch(url,headers,4*1024**2).decode('utf-8-sig')
    if not text.lstrip().startswith('#EXTM3U'):
        raise ValueError('The server did not return a video playlist.')
    manifest=m3u8.loads(text,uri=url)
    if any(k and k.method!='NONE' for k in [*manifest.keys,*manifest.session_keys]):
        raise ValueError('Encrypted streams are not supported.')
    if manifest.is_variant:
        choices=sorted(manifest.playlists,key=lambda p:p.stream_info.bandwidth or 0,reverse=True)
        for choice in choices:
            # This downloader handles video with its audio in the same stream.
            if not choice.stream_info.audio:
                return vod_manifest(choice.absolute_uri,headers,depth+1)
        raise ValueError('Separate audio and video streams are not supported yet.')
    if not manifest.is_endlist or not manifest.segments:
        raise ValueError('The server did not provide a complete on-demand video.')
    if any(s.byterange or s.init_section or s.discontinuity for s in manifest.segments):
        raise ValueError('This server uses a stream format not supported yet.')
    if len(manifest.segments)>20000:
        raise ValueError('The stream is too long.')
    return manifest

def stream_choices(url, headers):
    """Prefer 1080p, permitting 720p but never a lower rendition."""
    body=fetch(url,headers,4*1024**2).decode('utf-8-sig')
    if not body.lstrip().startswith('#EXTM3U'):
        raise ValueError('The server did not return a video playlist.')
    manifest=m3u8.loads(body,uri=url)
    if any(k and k.method!='NONE' for k in [*manifest.keys,*manifest.session_keys]):
        raise ValueError('Encrypted streams are not supported.')
    if not manifest.is_variant:
        return [url]
    choices=[p for p in manifest.playlists if not p.stream_info.audio and p.stream_info.resolution and p.stream_info.resolution[1] in (720,1080)]
    if not choices:
        raise ValueError('No 720p or 1080p stream is available.')
    return [p.absolute_uri for p in sorted(choices,key=lambda p:(p.stream_info.resolution[1],p.stream_info.bandwidth or 0),reverse=True)]

def save_stream(row,url,headers,partial,server):
    key=row['id']
    manifest=vod_manifest(url,headers)
    path=jobs.folder(key)
    transport=path/'segments.ts'
    received=0
    missing=[]
    duration=sum(s.duration for s in manifest.segments)
    with transport.open('wb') as output:
        for i,segment in enumerate(manifest.segments):
            cancelled(key)
            for attempt in range(6):
                try:
                    data=fetch(segment.absolute_uri,headers,32*1024**2)
                    if not data:
                        raise ValueError('The source returned an empty segment.')
                    break
                except Exception as exc:
                    print(f'{server} part {i+1}: {type(exc).__name__}: {str(exc)[:160]}',flush=True)
                    cancelled(key)
                    if attempt==5:
                        missing.append((i+1,segment.duration))
                        if sum(d for _,d in missing)>min(30,duration*.02):
                            raise ValueError('Too much video is missing (over 30 seconds or 2%); refusing an incomplete download.')
                        data=b''
                        break
                    for _ in range(min(2**attempt,8)):
                        cancelled(key)
                        time.sleep(1)
            received+=len(data)
            if received>jobs.LIMIT or shutil.disk_usage(path).free<jobs.RESERVE+received:
                raise ValueError('Not enough space to download and assemble this video.')
            output.write(data)
            jobs.update(key,received=received,message=f'{server} · {i+1}/{len(manifest.segments)} parts')
    cancelled(key)
    jobs.update(key,message='Assembling video…')
    result=path/'remux.mp4'
    with (path/'stream.log').open('wb') as log:
        proc=subprocess.Popen(['ffmpeg','-nostdin','-v','error','-y','-protocol_whitelist','file','-i',str(transport),'-map','0:v:0','-map','0:a:0?','-c','copy','-movflags','+faststart',str(result)],stdout=log,stderr=log)
        deadline=time.monotonic()+900
        try:
            while proc.poll() is None:
                cancelled(key)
                if time.monotonic()>deadline:
                    raise ValueError('Video assembly timed out.')
                time.sleep(.5)
            if proc.returncode:
                raise ValueError('This stream could not be assembled into an MP4.')
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
    expected=sum(s.duration for s in manifest.segments)
    probe=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(result)],capture_output=True,text=True,timeout=30)
    if probe.returncode or abs(float(probe.stdout)-expected)>max(4,expected*.015)+sum(d for _,d in missing):
        raise ValueError('The saved video duration does not match the complete stream.')
    result.replace(partial)
    transport.unlink()
    row['_ready_message']=('Saved with gaps: '+str(len(missing))+' missing part(s), approximately '+str(round(sum(d for _,d in missing),1))+' seconds (parts '+', '.join(str(i) for i,_ in missing)+').') if missing else 'Ready to watch'
    return partial.stat().st_size

def download_page(row,partial):
    key=row['id']
    jobs.update(key,message='Opening video page…')
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--disable-dev-shm-usage'])
        context=browser.new_context(service_workers='block',accept_downloads=False)
        cache={}
        def guard(route):
            u=urlsplit(route.request.url)
            if u.scheme not in ('https','http'):
                return route.abort()
            try:
                if u.hostname not in cache:
                    cache[u.hostname]=all(ipaddress.ip_address(a[4][0]).is_global for a in socket.getaddrinfo(u.hostname,u.port or 443,type=socket.SOCK_STREAM))
                if not cache[u.hostname] or u.port not in (None,80,443):
                    return route.abort()
                return route.continue_()
            except Exception:
                return route.abort()
        context.route('**/*',guard)
        page=context.new_page()
        context.on('page',lambda popup:popup.close() if popup!=page else None)
        page.set_default_timeout(1200)
        try:
            page.goto(row['url'],wait_until='domcontentloaded',timeout=30000)
            page.locator('a.sv-item').first.wait_for(timeout=15000)
            sources=page.locator('a.sv-item').evaluate_all('(links)=>links.map(a=>a.getAttribute("data-id"))')
            count=len(sources)
            order=([1] if count>1 else [])+[i for i in range(min(count,6)) if i!=1]
            errors=[]
            for index in order:
                cancelled(key)
                server=f'Server {index+1}'
                jobs.update(key,message=f'Checking {server.lower()}…',received=0,total=0)
                candidates=[]
                def observe(response):
                    if response.status==200 and '.m3u8' in urlsplit(response.url).path:
                        if response.url not in [c[0] for c in candidates]:
                            ref=response.request.headers.get('referer',row['url'])
                            candidates.append((response.url,{'Referer':ref,'User-Agent':response.request.headers.get('user-agent','Mozilla/5.0')}))
                page.on('response',observe)
                try:
                    # Open the same embed as the server link. This also avoids
                    # racing the page's asynchronously registered click handler.
                    jobs.validate_url(sources[index])
                    page.goto(sources[index],referer=row['url'],wait_until='domcontentloaded',timeout=30000)
                    deadline=time.monotonic()+55
                    clicked=set()
                    while time.monotonic()<deadline and not candidates:
                        cancelled(key)
                        for frame in page.frames:
                            if frame.url in clicked:
                                continue
                            play=frame.locator('#bigPlay, button[aria-label="Play"], .jw-icon-display').first
                            try:
                                if play.is_visible():
                                    play.click(timeout=1200)
                                    clicked.add(frame.url)
                            except Exception:
                                pass
                        page.wait_for_timeout(500)
                    if not candidates:
                        raise ValueError('No playable stream was found.')
                    # Stop playback to save bandwidth; capture has finished.
                    for frame in page.frames:
                        try:
                            frame.locator('video').evaluate_all('(videos)=>videos.forEach(v=>v.pause())')
                        except Exception:
                            pass
                    seen=set()
                    for manifest_url,headers in list(candidates):
                        try:
                            choices=stream_choices(manifest_url,headers)
                        except Exception as exc:
                            errors.append(f'{server}: {str(exc)[:100]}')
                            continue
                        for url in choices:
                            identity=(urlsplit(url).hostname,urlsplit(url).path)
                            if identity in seen:
                                continue
                            seen.add(identity)
                            try:
                                return save_stream(row,url,headers,partial,server)
                            except Exception as exc:
                                cancelled(key)
                                print(f'{server}: {type(exc).__name__}: {str(exc)[:200]}',flush=True)
                                jobs.update(key,message='Trying another 720p/1080p source…',received=0)
                                errors.append(f'{server}: {exc}' if isinstance(exc,ValueError) else f'{server}: stream request failed.')
                    raise ValueError('The stream could not be saved.')
                except Exception as exc:
                    cancelled(key)
                    errors.append(f'{server}: {exc}' if isinstance(exc,ValueError) else f'{server}: player unavailable.')
                finally:
                    page.remove_listener('response',observe)
            raise ValueError('No usable server. '+(' '.join(errors))[-250:])
        finally:
            context.close()
            browser.close()
