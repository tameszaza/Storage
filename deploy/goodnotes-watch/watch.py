#!/usr/bin/env python3
"""One blocking event loop: PDF catalog, write provenance and uploaded ZIP cleanup."""
import ctypes, hashlib, json, os, pwd, select, struct, subprocess, time
from pathlib import Path
from cleanup import Reconciler, ROOT, TEMP, STATE
import transcription_events as audio

BASE=Path('/home/tameszaza/.local/lib/goodnotes-links')
libc=ctypes.CDLL(None,use_errno=True)
fd=libc.inotify_init1(os.O_NONBLOCK|os.O_CLOEXEC)
fan=libc.fanotify_init(3,os.O_RDONLY|os.O_CLOEXEC)
if fd<0 or fan<0:raise OSError(ctypes.get_errno(),'Filesystem origin monitoring unavailable; cleanup not started')
MASK=0x8|0x40|0x80|0x100|0x200|0x400|0x800|0x2000
watches={};marked=set()
account=pwd.getpwnam('tameszaza')
groups=os.getgrouplist(account.pw_name,account.pw_gid)
runner={'user':account.pw_uid,'group':account.pw_gid,'extra_groups':groups}
reconciler=Reconciler()
pending_archives=set()
webdav_container=None
checked_writers={}

def webdav_writer(pid,command):
    global webdav_container
    if not command:return False
    executable=Path(os.fsdecode(command[0])).name
    if not (executable=='httpd' or (executable=='rclone' and command[1:3]==[b'serve',b'webdav'])):return False
    if pid in checked_writers:return checked_writers[pid]
    try:
        group=Path('/proc/'+str(pid)+'/cgroup').read_text()
        if not webdav_container or webdav_container not in group:
            webdav_container=subprocess.check_output(['docker','inspect','--format','{{.Id}}','tamestorage-webdav'],text=True,timeout=15).strip()
        result=bool(webdav_container and webdav_container in group)
    except (OSError,subprocess.SubprocessError):result=False
    checked_writers[pid]=result
    return result

def add_watch(path,source):
    wd=libc.inotify_add_watch(fd,os.fsencode(path),MASK)
    if wd<0:raise OSError(ctypes.get_errno(),'Cannot watch source directory')
    watches[wd]=path
    if source and path not in marked:
        result=libc.fanotify_mark(fan,1,ctypes.c_ulonglong(8|0x08000000),-100,os.fsencode(path))
        if result<0:raise OSError(ctypes.get_errno(),'Cannot identify PDF writers')
        marked.add(path)

def add_tree(path):
    for directory,dirs,_ in os.walk(path,followlinks=False):
        dirs[:]=[d for d in dirs if not (Path(directory)/d).is_symlink()]
        add_watch(Path(directory),True)

def add_inboxes():
    add_watch(TEMP,False)
    folder=TEMP/'Goodnotes-cleanup'
    if folder.is_dir() and not folder.is_symlink():add_watch(folder,False)
    if audio.INBOX.is_dir() and not audio.INBOX.is_symlink():add_watch(audio.INBOX,False)

def startup_archives():
    for folder in [TEMP,TEMP/'Goodnotes-cleanup']:
        if folder.is_dir() and not folder.is_symlink():
            pending_archives.update(p for p in folder.glob('*.zip') if not p.is_symlink())

def drain_origins():
    while select.select([fan],[],[],0)[0]:
        raw=os.read(fan,256*1024);offset=0
        while offset+24<=len(raw):
            length,version,reserved,metadata_len,mask,event_fd,pid=struct.unpack_from('IBBHQii',raw,offset)
            if length<24:raise RuntimeError('Invalid origin event')
            offset+=length
            if mask&0x4000:
                # Changed file identities are never eligible without writer proof.
                reconciler.hints.clear();print('Origin queue overflow: changed/unrecognized PDFs protected',flush=True)
                continue
            if event_fd<0:continue
            try:
                name=os.readlink('/proc/self/fd/'+str(event_fd));path=Path(name)
                if not path.is_relative_to(ROOT) or name.endswith(' (deleted)'):continue
                try:command=Path('/proc/'+str(pid)+'/cmdline').read_bytes().split(b'\0')
                except OSError:command=[]
                # This dedicated WebDAV service is the Goodnotes export channel.
                webdav=webdav_writer(pid,command)
                reconciler.observe_writer(path,os.fstat(event_fd),webdav)
            finally:os.close(event_fd)

def catalog_signature():
    if not os.path.ismount('/srv/tamestorage') or not ROOT.is_dir():raise RuntimeError('NAS unavailable; preserving library')
    names=[]
    for directory,dirs,files in os.walk(ROOT,followlinks=False):
        dirs[:]=[d for d in dirs if not (Path(directory)/d).is_symlink()]
        for name in files:
            p=Path(directory)/name
            if name.lower().endswith('.pdf') and not p.is_symlink() and p.is_file():names.append(p.relative_to(ROOT).as_posix())
    if not names:raise RuntimeError('PDF catalog empty; preserving existing links')
    return hashlib.sha256(json.dumps(sorted(names),ensure_ascii=False).encode()).hexdigest()

def node(script,input=None):
    r=subprocess.run([str(BASE/'node_modules/node/bin/node'),str(BASE/script)],cwd=BASE,
                     input=input,text=True,timeout=240,**runner)
    if r.returncode:raise RuntimeError('Managed library operation failed; pending work kept')

def settle(previous,startup):
    if not os.path.ismount('/srv/tamestorage') or not ROOT.is_dir():raise RuntimeError('NAS unavailable; preserving files and links')
    drain_origins()
    for job in list(reconciler.state['jobs']):reconciler.finish_files(job)
    for archive in sorted(pending_archives):
        if archive.exists():reconciler.process(archive)
        pending_archives.discard(archive)
    for job in list(reconciler.state['jobs']):node('cleanup-links.mjs',json.dumps({'deleted':job['deleted']}))
    signature=catalog_signature()
    if startup or signature!=previous or reconciler.state['jobs']:
        node('publish.mjs')
        temp=STATE/'catalog.sha256.tmp';temp.write_text(signature);temp.replace(STATE/'catalog.sha256')
    for job in list(reconciler.state['jobs']):
        print('Goodnotes cleanup: '+json.dumps(reconciler.complete(job)),flush=True)
    if reconciler.dirty:reconciler.save()
    # Notify the optional semantic index after a settled PDF event; no extra watcher.
    marker=Path("/srv/tamestorage/.tamestorage_state/lecture-search/pdf-changes")
    if marker.is_file():marker.touch()
    return signature

if not ROOT.is_dir() or not TEMP.is_dir():raise RuntimeError('Goodnotes source/inbox unavailable')
add_tree(ROOT);add_inboxes()
seed=reconciler.bootstrap()
if seed:print('Initial legacy iPad export provenance: '+json.dumps(seed),flush=True)
for path in ROOT.rglob('*.pdf'):reconciler.observe_path(path)
reconciler.publish_import_status()
startup_archives();audio.seed()
previous=None
try:previous=(STATE/'catalog.sha256').read_text().strip()
except FileNotFoundError:pass
due=time.monotonic()+2;first_dirty=time.monotonic();retry=30;startup=True
print('Goodnotes watcher ready: blocks on filesystem events; ZIP cleanup enabled',flush=True)
while True:
    drain_origins()
    now=time.monotonic()
    if due is not None and now>=due:
        try:
            previous=settle(previous,startup);startup=False;due=None;first_dirty=None;retry=30
        except Exception as error:
            print(str(error),flush=True);due=time.monotonic()+retry;retry=min(retry*2,600)
    ready,_,_=select.select([fd,fan],[],[],None if due is None else max(0,due-time.monotonic()))
    if fan in ready:drain_origins()
    if fd not in ready:continue
    raw=os.read(fd,1024*1024);offset=0;changed=False
    while offset+16<=len(raw):
        wd,mask,cookie,length=struct.unpack_from('iIII',raw,offset);offset+=16
        name=os.fsdecode(raw[offset:offset+length].split(b'\0',1)[0]);offset+=length
        parent=watches.get(wd)
        if mask&0x4000:add_tree(ROOT);add_inboxes();startup_archives();audio.seed();changed=True;continue
        if mask&0x2000:raise RuntimeError('NAS filesystem unmounted')
        if mask&0x8000:
            if parent in [ROOT,TEMP]:raise RuntimeError('Required filesystem watch lost')
            watches.pop(wd,None);marked.discard(parent);continue
        if not parent:continue
        path=parent/name
        if audio.event(parent,path,mask):continue
        if parent in [TEMP,TEMP/'Goodnotes-cleanup']:
            if path==audio.INBOX and path.is_dir():add_inboxes();audio.seed()
            elif path==TEMP/'Goodnotes-cleanup' and path.is_dir():add_inboxes();startup_archives();changed=True
            elif name.lower().endswith('.zip') and mask&(0x8|0x80):pending_archives.add(path);changed=True
            continue
        if mask&0x40000000:
            if mask&(0x100|0x80) and path.is_dir():add_tree(path)
            changed=True
        elif name.lower().endswith('.pdf') or not name:
            if mask&(0x8|0x80):drain_origins();reconciler.observe_path(path)
            changed=True
    if changed:
        now=time.monotonic()
        if first_dirty is None:first_dirty=now
        due=min(now+20,first_dirty+120)
