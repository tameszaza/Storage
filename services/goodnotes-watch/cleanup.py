"""Goodnotes inventory reconciliation. No ZIP extraction or periodic scanning."""
import json, os, pwd, re, stat, subprocess, time, unicodedata, zipfile
from pathlib import Path, PurePosixPath

ROOT=Path('/srv/tamestorage/uploads/Goodnotes')
TEMP=Path('/srv/tamestorage/uploads/temp')
STATE=Path('/srv/tamestorage/.tamestorage_state/goodnotes-obsidian-links')
LEDGER=STATE/'cleanup-state.json'
AUDIT=Path('/srv/tamestorage/.tamestorage_state/activity_log.json')

def key(value): return unicodedata.normalize('NFC',value).casefold()
def fingerprint(st): return [st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns]
def safe_pdf(path):
    if path.is_symlink() or path.suffix.lower()!='.pdf':return False
    try:return path.resolve().is_relative_to(ROOT.resolve()) and stat.S_ISREG(path.stat().st_mode)
    except OSError:return False
def atomic_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    with temp.open('w') as f:
        os.chmod(temp,0o600);json.dump(value,f,ensure_ascii=False)
    temp.replace(path)

def inventory(archive):
    if archive.is_symlink() or archive.parent not in [TEMP,TEMP/'Goodnotes-cleanup']:
        raise ValueError('Archive is outside the cleanup inbox')
    before=fingerprint(archive.stat())
    with zipfile.ZipFile(archive) as z:
        infos=z.infolist()
        metadata=z.getinfo('.goodnotesmeta')
        if metadata.file_size>1024*1024:raise ValueError('Unexpected Goodnotes metadata size')
        meta=json.loads(z.read(metadata))
        if meta.get('compatibilityVersion')!=1 or not isinstance(meta.get('items'),list):
            raise ValueError('Unsupported Goodnotes inventory format')
        documents={}
        for entry in infos:
            name=entry.filename
            parts=PurePosixPath(name).parts
            if name.startswith('/') or '\\' in name or '..' in parts:
                raise ValueError('Unsafe archive path')
            if entry.is_dir() or name=='.goodnotesmeta' or parts[-1] in ['.DS_Store']:continue
            if not name.lower().endswith('.goodnotes'):
                raise ValueError('Not a complete Goodnotes-format backup')
            target=name[:-10]+'.pdf'
            if key(target) in documents:raise ValueError('Ambiguous document names in backup')
            documents[key(target)]=target
        if not documents:raise ValueError('Empty backup: no cleanup performed')
    if fingerprint(archive.stat())!=before:raise ValueError('Backup is still changing')
    return documents,before

class Reconciler:
    def __init__(self):
        try:self.state=json.loads(LEDGER.read_text())
        except FileNotFoundError:self.state={'version':1,'pdfs':{},'archives':{},'jobs':[]}
        if self.state.get('version')!=1:raise ValueError('Unsupported provenance state')
        self.hints={};self.dirty=False
        self.migrate_presence()

    def save(self):
        atomic_json(LEDGER,self.state);self.dirty=False
        self.publish_import_status()

    def presence(self,relative,record,fp,known=None,absent=None):
        # Presence is independent of a PDF byte/version fingerprint. Re-exporting or
        # manually replacing a known notebook must never invite a duplicate import.
        snapshot=self.state.get('latest_inventory',{})
        known={key(v) for v in snapshot.get('paths',[])} if known is None else known
        absent={key(v) for v in snapshot.get('absent',[])} if absent is None else absent
        if key(relative) in known:return True
        if record.get('ipad_present') is True:return True
        if record.get('ipad_present') is False:return False
        if record.get('ipad_fp'):return True
        if key(relative) in absent:return False
        if snapshot.get('complete') and fp[3]<=snapshot.get('source_mtime_ns',0):return False
        if record.get('origin')=='goodnotes' and record.get('fp')==fp:return True
        return None

    def publish_import_status(self):
        if not os.path.ismount('/srv/tamestorage') or not ROOT.is_dir():return
        files=[]
        snapshot=self.state.get('latest_inventory',{})
        known={key(v) for v in snapshot.get('paths',[])};absent={key(v) for v in snapshot.get('absent',[])}
        for relative,record in self.state['pdfs'].items():
            path=ROOT/relative
            if not safe_pdf(path):continue
            fp=fingerprint(path.stat());present=self.presence(relative,record,fp,known,absent)
            if present is True:continue
            files.append({'path':relative,'reason':'manual' if record.get('origin')=='server' else 'unconfirmed',
                          'confidence':'missing' if present is False else 'unconfirmed'})
        snapshot=self.state.get('latest_inventory',{})
        report=STATE/'ipad-import-status.json'
        atomic_json(report,{'version':2,'inventory_at':snapshot.get('completed_ns',0)/1e9 or None,
                            'inventory_complete':snapshot.get('complete',False),
                            'files':sorted(files,key=lambda item:key(item['path']))})
        os.chmod(report,0o644)

    def migrate_presence(self):
        if 'latest_inventory' in self.state:return
        try:
            report=json.loads((STATE/'cleanup-last.json').read_text())
            completed=(STATE/'cleanup-last.json').stat().st_mtime_ns
            absent=report['preserved']
            if not isinstance(absent,list) or not all(isinstance(v,str) for v in absent):return
        except (OSError,ValueError,KeyError):return
        known=[relative for relative,record in self.state['pdfs'].items() if record.get('ipad_fp') and relative not in absent]
        self.state['latest_inventory']={'complete':False,'completed_ns':completed,'source_mtime_ns':completed,
                                        'notebooks':report.get('notebooks'),'paths':known,'absent':absent}
        for relative,record in self.state['pdfs'].items():
            if relative in known:record['ipad_present']=True
            elif relative in absent:
                record['ipad_present']=False;record.pop('ipad_fp',None)
        self.save()

    def protect_audit(self):
        try:entries=json.loads(AUDIT.read_text())
        except FileNotFoundError:return
        for item in entries:
            if item.get('action') not in ['file.upload','file.rename','file.move','file.copy','file.edit']:continue
            paths=[item.get('target',''),item.get('details',{}).get('new_path','')]
            for target in paths:
                if target.startswith('Goodnotes/') and target.lower().endswith('.pdf'):
                    relative=target[len('Goodnotes/'):]
                    old=self.state['pdfs'].setdefault(relative,{})
                    old['protected']=True;old['explicit']=True;old['origin']='server';self.dirty=True

    def bootstrap(self):
        # One explicit installation-only seed for the preexisting iPad export tree.
        # Ordinary operation never infers provenance from PDF producer strings.
        if self.state.get('bootstrapped'):return
        self.protect_audit()
        counts={'goodnotes':0,'protected':0}
        for path in ROOT.rglob('*'):
            if not safe_pdf(path):continue
            relative=path.relative_to(ROOT).as_posix();old=self.state['pdfs'].get(relative,{})
            if not old.get('protected'):
                account=pwd.getpwnam('tameszaza')
                runner={'user':account.pw_uid,'group':account.pw_gid,'extra_groups':[]} if os.geteuid()==0 else {}
                info=subprocess.run(['pdfinfo',str(path)],capture_output=True,text=True,timeout=20,**runner)
                producer=next((l.split(':',1)[1].strip() for l in info.stdout.splitlines() if l.startswith('Producer:')),'')
                owned=bool(re.fullmatch(r'iOS Version .+ Quartz PDFContext',producer)) and info.returncode==0
                old={'origin':'goodnotes' if owned else 'unknown','protected':not owned,'explicit':not owned}
            old['fp']=fingerprint(path.stat());self.state['pdfs'][relative]=old
            counts['protected' if old.get('protected') else 'goodnotes']+=1
        self.state['bootstrapped']=True;self.save()
        return counts

    def observe_writer(self,path,st,webdav):
        # inode hints handle server uploads which are atomically renamed into place.
        inode=(st.st_dev,st.st_ino)
        self.hints[inode]={'fp':fingerprint(st),'webdav':webdav,'time':time.monotonic()}
        if len(self.hints)>2048:self.hints.pop(next(iter(self.hints)))
        if safe_pdf(path):self.observe_path(path)

    def observe_path(self,path):
        if not safe_pdf(path):return
        relative=path.relative_to(ROOT).as_posix();st=path.stat();fp=fingerprint(st)
        previous=self.state['pdfs'].get(relative)
        hint=self.hints.get((st.st_dev,st.st_ino))
        confirmed=bool(hint and hint['fp']==fp and time.monotonic()-hint['time']<30)
        if previous and previous.get('fp')==fp and not (confirmed and (previous.get('origin')=='unknown' or (hint['webdav'] and previous.get('ipad_fp')!=fp))):return
        webdav=confirmed and hint['webdav']
        explicit=bool(previous and previous.get('explicit')) or bool(confirmed and not webdav)
        protected=explicit or not webdav
        self.state['pdfs'][relative]={'fp':fp,'origin':'goodnotes' if webdav and not explicit else 'server' if explicit else 'unknown','protected':protected,'explicit':explicit}
        if previous:
            for field in ('ipad_present','ipad_fp'):
                if field in previous:self.state['pdfs'][relative][field]=previous[field]
        if webdav:
            snapshot=self.state.get('latest_inventory',{})
            stale=key(relative) in {key(v) for v in snapshot.get('absent',[])} and fp[3]<=snapshot.get('source_mtime_ns',0)
            if not stale:self.state['pdfs'][relative].update(ipad_fp=fp,ipad_present=True)
        self.dirty=True

    def plan(self,archive):
        documents,archive_fp=inventory(archive)
        self.protect_audit()
        deleted=[];preserved=[];confirmed=[]
        for path in ROOT.rglob('*'):
            if not safe_pdf(path):continue
            relative=path.relative_to(ROOT).as_posix()
            if key(relative) in documents:
                if path.stat().st_mtime_ns<=archive_fp[3]:confirmed.append({'path':relative,'fp':fingerprint(path.stat())})
                continue
            fp=fingerprint(path.stat());record=self.state['pdfs'].get(relative,{})
            if record.get('origin')=='goodnotes' and not record.get('protected') and record.get('fp')==fp and path.stat().st_mtime_ns<=archive_fp[3]:
                deleted.append({'path':relative,'fp':fp,'bytes':fp[2]})
            else:preserved.append(relative)
        return {'archive':str(archive),'archive_fp':archive_fp,'documents':len(documents),'delete':deleted,'preserved':preserved,'confirmed':confirmed,'inventory_paths':list(documents.values())}

    def process(self,archive):
        if not os.path.ismount('/srv/tamestorage'):raise RuntimeError('NAS unavailable; no cleanup')
        fp=fingerprint(archive.stat())
        if self.state['archives'].get(str(archive))==fp:return None
        try:job=self.plan(archive)
        except (ValueError,KeyError,zipfile.BadZipFile,json.JSONDecodeError):
            self.state['archives'][str(archive)]=fp;self.save()
            print('ZIP is not a validated Goodnotes library backup; kept unchanged',flush=True)
            return None
        # Journal the specific operations first; restart can finish link cleanup.
        job['deleted']=[];job['removed_bytes']=0
        self.state['jobs'].append(job);self.save()
        self.finish_files(job)
        return job

    def finish_files(self,job):
        if not os.path.ismount('/srv/tamestorage'):raise RuntimeError('NAS unavailable; no cleanup')
        archive=Path(job['archive'])
        if not archive.exists() or fingerprint(archive.stat())!=job['archive_fp']:
            raise RuntimeError('Pending inventory ZIP changed; preserving files')
        for item in job['delete']:
            relative=item['path'];path=ROOT/relative
            quarantine=STATE/'cleanup-current'/relative
            if relative in job['deleted']:continue
            if quarantine.exists():
                if fingerprint(quarantine.stat())!=item['fp']:raise RuntimeError('Changed pending transaction PDF; preserved')
                quarantine.unlink();job['removed_bytes']+=item['bytes']
            if path.exists():
                if not safe_pdf(path) or fingerprint(path.stat())!=item['fp']:
                    job['preserved'].append(relative);continue
                # Same-filesystem private transaction; verify inode before disposal.
                quarantine.parent.mkdir(parents=True,exist_ok=True)
                path.rename(quarantine)
                if fingerprint(quarantine.stat())!=item['fp']:
                    if not path.exists():quarantine.rename(path)
                    raise RuntimeError('PDF changed during cleanup; stopped')
                quarantine.unlink();job['removed_bytes']+=item['bytes']
            job['deleted'].append(relative);self.state['pdfs'].pop(relative,None);self.save()
        # Remove only empty directories in the transaction and source tree.
        for base in [STATE/'cleanup-current',ROOT]:
            if not base.exists():continue
            for directory,_,_ in os.walk(base,topdown=False,followlinks=False):
                p=Path(directory)
                if p!=ROOT:
                    try:p.rmdir()
                    except OSError:pass
        self.save()

    def complete(self,job):
        archive=Path(job['archive'])
        if archive.exists():
            if fingerprint(archive.stat())!=job['archive_fp']:raise RuntimeError('ZIP changed before final cleanup')
            archive.unlink()
        if 'inventory_paths' in job:
            paths=job['inventory_paths'];keys={key(v) for v in paths}
            self.state['latest_inventory']={'complete':True,'completed_ns':time.time_ns(),
                 'source_mtime_ns':job['archive_fp'][3],'notebooks':len(paths),'paths':paths,
                 'absent':[v for v in self.state['pdfs'] if key(v) not in keys]}
            for relative,record in self.state['pdfs'].items():
                if key(relative) in keys:record['ipad_present']=True
                elif record.get('fp',[0,0,0,0])[3]<=job['archive_fp'][3]:
                    record['ipad_present']=False;record.pop('ipad_fp',None)
        confirmed_paths={item['path'] for item in job.get('confirmed',[])}
        for relative,record in self.state['pdfs'].items():
            if relative not in confirmed_paths and record.get('fp',[0,0,0,0])[3]<=job['archive_fp'][3]:record.pop('ipad_fp',None)
        for item in job.get('confirmed',[]):
            record=self.state['pdfs'].get(item['path'])
            if record and record.get('fp')==item['fp']:record['ipad_fp']=item['fp']
        self.state['jobs'].remove(job);self.save()
        summary={'notebooks':job['documents'],'removed_pdfs':len(job['deleted']),
                 'pdf_bytes':job['removed_bytes'],'protected_or_unknown':len(job['preserved']),
                 'temporary_zip_removed':True}
        atomic_json(STATE/'cleanup-last.json',{**summary,'deleted':job['deleted'],'preserved':job['preserved']})
        return summary
