"""Typed server settings, served by the existing private host-control broker."""
import copy
import configparser
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time
from zoneinfo import ZoneInfo
import yaml
import tamestorage_containers as controls

BASE=Path('/srv/docker')
STATE=Path('/var/lib/tamestorage-control/settings.json')
OPS=Path('/etc/tamestorage-operations.conf')
APP_ENV=Path('/etc/tamestorage.env')
UNIT_ROOT=Path('/etc/systemd/system')
GROUPS=[('server','Server','sliders'),('storage','Storage & backup','hard-drive'),('music','Music sync','music'),('navidrome','Navidrome','headphones'),('grammar','LanguageTool','spell-check'),('gateway','Cyborg AI gateway','wand-magic-sparkles'),('search','Lecture search','magnifying-glass'),('goodnotes','Goodnotes & WebDAV','file-pdf'),('calendar','Calendar','calendar'),('livesync','Obsidian LiveSync','arrows-rotate'),('transcription','Transcription','microphone'),('uptime','Uptime Kuma','heart-pulse'),('video','Video downloads','film')]
DOCKERS={
 'tamestorage':('server','tamestorage','web','Web',256),
 'tamestorage-playlist-worker':('music','tamestorage','playlist-worker','Music worker',256),
 'tamestorage-calendar-worker':('calendar','tamestorage','calendar-worker','Calendar sync',128),
 'tamestorage-video-worker':('video','tamestorage','video-worker','Video worker',256),
 'tamestorage-calendar':('calendar','tamestorage','radicale','Calendar server',64),
 'tamestorage-webdav':('goodnotes','webdav','webdav','WebDAV',64),
 'livesync-couchdb':('livesync','obsidian-livesync','couchdb','CouchDB',256),
 'languagetool':('grammar','languagetool','languagetool','LanguageTool',512),
 'navidrome':('navidrome','navidrome','navidrome','Navidrome',96),
 'uptime-kuma':('uptime','uptime-kuma','uptime-kuma','Uptime Kuma',256),
 'tamestorage-control-check':('verification','tamestorage-control-check','check','Control verification',32),
}
UNITS={
 'goodnotes-obsidian-links.service':('goodnotes','PDF library watcher',384,25,'live'),
 'lecture-search@.service':('search','Search request',256,75,'next'),
 'lecture-search-index.service':('search','Indexer',256,75,'next'),
 'lecture-search-ocr.service':('search','OCR',256,100,'next'),
 'cyborg-grammar-gateway@.service':('gateway','AI gateway request',64,50,'next'),
 'tamestorage-transcription.service':('transcription','Dispatcher',128,0,'next'),
 'tamestorage-transcription-inbox.service':('transcription','Recording inbox',128,50,'next'),
}
INI={
 'couchdb':BASE/'obsidian-livesync/config/livesync.ini',
 'radicale':Path('/srv/tamestorage/.tamestorage_state/radicale/config/config'),
}
FIELDS={}
class SettingsError(ValueError):pass

def field(key,group,label,kind,default,source,*,minimum=None,maximum=None,unit='',choices=None,advanced=False,help='',secret=False):
 FIELDS[key]=dict(key=key,group=group,label=label,type=kind,default=default,source=source,min=minimum,max=maximum,unit=unit,choices=choices,advanced=advanced,help=help,secret=secret)

def env(key,group,label,name,default,targets,kind='number',fmt='',**kw):
 field(key,group,label,kind,default,('env',targets,name,fmt),**kw)

def hostenv(key,group,label,name,default,unitname,kind='number',**kw):
 field(key,group,label,kind,default,('hostenv',unitname,name),**kw)

# Service-independent controls use existing native files and timers.
field('storage.temp_hours','storage','Temporary files','number',24,('ops','TEMP_RETENTION_HOURS'),minimum=1,maximum=8760,unit='hours')
field('storage.audio_hours','storage','Completed recordings','number',24,('ops','RECORDING_RETENTION_HOURS'),minimum=1,maximum=8760,unit='hours',help='Pending and retryable recordings stay protected.')
field('storage.backup_days','storage','Backup versions','number',30,('ops','BACKUP_RETENTION_DAYS'),minimum=1,maximum=3650,unit='days')
field('storage.backup_interval','storage','Backup interval','number',6,('timer','tamestorage-backup.timer','OnUnitActiveSec'),minimum=1,maximum=168,unit='hours')
field('storage.backup_jitter','storage','Backup random delay','number',300,('timer','tamestorage-backup.timer','RandomizedDelaySec'),minimum=0,maximum=3600,unit='seconds',advanced=True)
field('storage.recovery_interval','storage','Disk recovery interval','number',60,('timer','tamestorage-primary-recover.timer','OnUnitInactiveSec'),minimum=30,maximum=3600,unit='seconds',advanced=True)
# App controls: environment overrides remain in the original Compose files.
web=['tamestorage'];music=['tamestorage','tamestorage-playlist-worker']
env('server.log_mb','server','Server log file size','SERVER_LOG_MAX_BYTES',10,web,fmt='mib',minimum=1,maximum=1024,unit='MB',advanced=True)
env('server.log_files','server','Server log files','SERVER_LOG_BACKUP_COUNT',5,web,minimum=1,maximum=50,advanced=True)
for key,label,flag,default,lo,hi in [('workers','Web workers','--workers',2,1,8),('threads','Threads per worker','--threads',4,1,16),('timeout','Web request timeout','--timeout',3600,30,86400),('graceful','Graceful shutdown','--graceful-timeout',30,5,300),('keepalive','Keep-alive','--keep-alive',2,0,75)]:field('server.'+key,'server',label,'number',default,('flag','tamestorage',flag,''),minimum=lo,maximum=hi,unit='seconds' if key in ['timeout','graceful','keepalive'] else '',advanced=True)
env('server.timezone','server','Time zone','APP_TIMEZONE','Asia/Singapore',list(k for k,v in DOCKERS.items() if v[1]=='tamestorage'),kind='timezone')
env('server.upload_gb','server','File upload limit','MAX_CONTENT_LENGTH',5,web,fmt='gib',minimum=0.01,maximum=100,unit='GB')
env('server.quota_gb','server','New account quota','DEFAULT_USER_QUOTA_BYTES',5,web,fmt='gib',minimum=0,maximum=10000,unit='GB',help='Existing account quotas stay unchanged. Zero is unlimited.')
env('server.gemini_model','server','Cloud model','GEMINI_MODEL','gemini-1.5-flash',web,kind='text',advanced=True)
env('calendar.interval','calendar','Sync interval','CALDAV_SYNC_INTERVAL_SECONDS',120,['tamestorage','tamestorage-calendar-worker'],minimum=30,maximum=86400,unit='seconds')
env('calendar.public_url','calendar','Calendar URL','CALDAV_PUBLIC_URL','',['tamestorage','tamestorage-calendar-worker'],kind='url',advanced=True)
env('calendar.username','calendar','Calendar sync username','CALDAV_USERNAME','',['tamestorage','tamestorage-calendar-worker'],kind='text',advanced=True)
env('calendar.password','calendar','Calendar sync password','CALDAV_PASSWORD','',['tamestorage','tamestorage-calendar-worker'],kind='password',secret=True,advanced=True)
for key,label,name,default,lo,hi in [('poll','Check interval','PLAYLIST_WORKER_POLL_SECONDS',10,5,3600),('delete_wait','Deletion confirmation','PLAYLIST_DELETE_CONFIRM_SECONDS',300,30,86400),('snapshot_timeout','Snapshot timeout','PLAYLIST_SNAPSHOT_TIMEOUT_SECONDS',900,60,7200),('lyrics_delay','Lyrics request spacing','LYRICS_REQUEST_DELAY_SECONDS',.35,.05,60),('lyrics_retry','Missing lyrics retry','LYRICS_MISSING_RETRY_SECONDS',604800,3600,31536000),('error_retry','Lyrics error retry','LYRICS_ERROR_RETRY_SECONDS',21600,60,604800)]:
 env('music.'+key,'music',label,name,default,music,minimum=lo,maximum=hi,unit='seconds',advanced=key not in ['poll'])
env('music.lyrics','music','Fetch lyrics','LYRICS_ENABLED',True,music,kind='boolean')
env('music.providers','music','Lyrics source order','LYRICS_PROVIDER_ORDER','lrclib,simpmusic,netease',music,kind='providers',advanced=True)
for key,label,name,default in [('url','Music server URL','NAVIDROME_URL','http://127.0.0.1:4533'),('lyrics_url','LRCLIB URL','LYRICS_API_URL','https://lrclib.net'),('simpmusic_url','SimpMusic URL','LYRICS_SIMPMUSIC_API_URL','https://api-lyrics.simpmusic.org/v1'),('netease_url','NetEase URL','LYRICS_NETEASE_API_URL','https://music.163.com/api')]:
 env('music.'+key,'music',label,name,default,music,kind='url',advanced=True)
env('music.username','music','Music server username','NAVIDROME_USERNAME','',music,kind='text',advanced=True)
env('music.password','music','Music server password','NAVIDROME_PASSWORD','',music,kind='password',secret=True,advanced=True)
# LanguageTool's supported image environment keys.
for key,label,name,value,lo,hi,fmt in [('heap','Java maximum heap','Java_Xmx',1024,256,8192,'mb_m'),('heap_min','Java initial heap','Java_Xms',128,32,4096,'mb_m'),('threads','Check threads','langtool_maxCheckThreads',2,1,16,''),('text_threads','Text checker threads','langtool_maxTextCheckerThreads',2,1,16,''),('cache','Result cache entries','langtool_cacheSize',256,0,100000,''),('cache_ttl','Result cache lifetime','langtool_cacheTTLSeconds',300,0,86400,''),('pipeline_ttl','Pipeline lifetime','langtool_pipelineExpireTimeInSeconds',300,0,86400,''),('pool','Pipeline pool size','langtool_maxPipelinePoolSize',2,1,16,'')]:
 env('grammar.'+key,'grammar',label,name,value,['languagetool'],fmt=fmt,minimum=lo,maximum=hi,unit='MB' if 'heap' in key else 'seconds' if 'ttl' in key else '',advanced=key not in ['heap','threads'])
for key,label,name,value in [('caching','Cache pipelines','langtool_pipelineCaching',True),('prewarm','Prewarm pipelines','langtool_pipelinePrewarming',False)]:env('grammar.'+key,'grammar',label,name,value,['languagetool'],kind='boolean',advanced=True)
# Navidrome overrides avoid editing its data database or source music.
for key,label,name,value,kind,fmt,lo,hi in [('image_cache','Artwork cache','ND_IMAGECACHESIZE',500,'number','mb',16,8192),('artwork_requests','Artwork requests','ND_DEVARTWORKMAXREQUESTS',4,'number','',1,32),('backlog','Artwork backlog','ND_DEVARTWORKTHROTTLEBACKLOGLIMIT',1024,'number','',16,10000),('backlog_timeout','Artwork backlog timeout','ND_DEVARTWORKTHROTTLEBACKLOGTIMEOUT',120,'number','duration',1,3600),('precache','Precache artwork','ND_ENABLEARTWORKPRECACHE',True,'boolean','',None,None),('buffer','Buffer artwork requests','ND_DEVARTWORKTHROTTLEBUFFERED',True,'boolean','',None,None),('session','Login session','ND_SESSIONTIMEOUT',172800,'number','duration',300,31536000),('log_level','Log level','ND_LOGLEVEL','info','select','',None,None),('downloads','Allow music downloads','ND_ENABLEDOWNLOADS',True,'boolean','',None,None),('sharing','Allow public sharing','ND_ENABLESHARING',False,'boolean','',None,None)]:
 env('navidrome.'+key,'navidrome',label,name,value,['navidrome'],kind=kind,fmt=fmt,minimum=lo,maximum=hi,unit='MB' if fmt=='mb' else 'seconds' if fmt=='duration' else '',advanced=key not in ['image_cache','precache','downloads'],choices=['error','warn','info','debug','trace'] if kind=='select' else None)
env('navidrome.lyrics','navidrome','Lyrics priority','ND_LYRICSPRIORITY','.ttml,.yaml,.yml,.elrc,.lrc,.srt,.txt,embedded',['navidrome'],kind='text',advanced=True)
# Existing on-demand processes take updated environment on their next invocation.
gateway='cyborg-grammar-gateway@.service'
for key,label,name,default,lo,hi in [('baseline_timeout','LanguageTool timeout','TAMES_GRAMMAR_BASELINE_TIMEOUT',45,5,120),('budget','Grammar request budget','TAMES_GRAMMAR_REQUEST_BUDGET',85,15,300),('verify','Verification timeout','TAMES_GRAMMAR_VERIFY_TIMEOUT',6,1,30),('assist','Writing assistant timeout','TAMES_GRAMMAR_ASSIST_TIMEOUT',250,30,600)]:hostenv('gateway.'+key,'gateway',label,name,default,gateway,minimum=lo,maximum=hi,unit='seconds',advanced=key not in ['budget'])
hostenv('gateway.ai','gateway','Use Cyborg AI','TAMES_GRAMMAR_AI_ENABLED',True,gateway,kind='boolean',help='Cyborg’s Server tasks switch still controls admission.')
for key,label,name,default,lo,hi,unit in [('results','Maximum search results','TAMES_SEARCH_MAX_RESULTS',8,1,32,''),('pages','Pages per read','TAMES_SEARCH_MAX_PAGES',12,1,48,''),('characters','Text per read','TAMES_SEARCH_MAX_CHARS',24000,1000,200000,'characters'),('edge','Page image long edge','TAMES_SEARCH_IMAGE_EDGE',1500,480,2400,'px'),('quality','Page image quality','TAMES_SEARCH_IMAGE_QUALITY',82,40,95,'%'),('timeout','Request timeout','TAMES_SEARCH_TIMEOUT',90,15,300,'seconds')]:hostenv('search.'+key,'search',label,name,default,'lecture-search@.service',minimum=lo,maximum=hi,unit=unit,advanced=key not in ['results'])
for key,label,name,default,lo,hi in [('retry','Unavailable Cyborg retry','TAMES_TRANSCRIPTION_RETRY_SECONDS',60,15,3600),('lease_retry','Worker lease retry','TAMES_TRANSCRIPTION_LEASE_RETRY_SECONDS',15,5,300)]:hostenv('transcription.'+key,'transcription',label,name,default,'tamestorage-transcription.service',minimum=lo,maximum=hi,unit='seconds',advanced=True)
# Native INI values; paths, auth providers and database identities are managed.
for key,label,section,name,default,kind,lo,hi,fmt in [('document_mb','Maximum document','couchdb','max_document_size',50,'number',1,2048,'mib'),('request_mb','Maximum request','chttpd','max_http_request_size',500,'number',1,4096,'mib'),('cors','Cross-origin access','chttpd','enable_cors',True,'boolean',None,None,''),('cors_credentials','Cross-origin credentials','cors','credentials',True,'boolean',None,None,''),('origins','Allowed app origins','cors','origins','app://obsidian.md,capacitor://localhost,http://localhost','text',None,None,''),('log_level','Log level','log','level','info','select',None,None,'')]:field('livesync.'+key,'livesync',label,kind,default,('ini','couchdb',section,name,fmt),minimum=lo,maximum=hi,unit='MB' if fmt else '',advanced=key not in ['document_mb','request_mb'],choices=['debug','info','notice','warning','error','critical','alert','emergency','none'] if kind=='select' else None)
for key,label,section,name,default,kind,lo,hi in [('connections','Maximum connections','server','max_connections',20,'number',1,256),('timeout','Request timeout','server','timeout',30,'number',5,3600),('auth_cache','Cache authentication','auth','htpasswd_cache',True,'boolean',None,None),('log_level','Log level','logging','level','warning','select',None,None)]:field('calendar.'+key,'calendar',label,kind,default,('ini','radicale',section,name,''),minimum=lo,maximum=hi,unit='seconds' if key=='timeout' else '',advanced=True,choices=['debug','info','warning','error','critical'] if kind=='select' else None)
field('goodnotes.read_timeout','goodnotes','Request timeout','number',3600,('apache','Timeout'),minimum=30,maximum=86400,unit='seconds',advanced=True)
field('goodnotes.workers','goodnotes','Request workers','number',8,('apache','MaxRequestWorkers'),minimum=2,maximum=32,advanced=True)
field('goodnotes.log_level','goodnotes','Log level','select','warn',('apache','LogLevel'),choices=['debug','info','notice','warn','error','crit'],advanced=True)
env('uptime.heap','uptime','Node maximum heap','NODE_OPTIONS',192,['uptime-kuma'],fmt='node_heap',minimum=128,maximum=2048,unit='MB',advanced=True)
env('uptime.timezone','uptime','Time zone','TZ','Asia/Singapore',['uptime-kuma'],kind='timezone',advanced=True)
# The same limited controls across every current Docker service.
for name,(group,project,service,label,min_memory) in DOCKERS.items():
 for suffix,title,kind,default,lo,hi,choices in [('memory','Memory limit','number',0,min_memory,16384,None),('cpu','CPU limit','number',0,.1,8,None),('pids','Process limit','number',0,32,8192,None),('restart','Restart policy','select','unless-stopped',None,None,['unless-stopped','always','on-failure','no']),('log_mb','Log file size','number',0,1,1000,None),('log_files','Log files','number',1,1,20,None)]:
  field(name+'.'+suffix,group,label+' · '+title,kind,default,('docker',name,suffix),minimum=lo,maximum=hi,unit='MB' if suffix in ['memory','log_mb'] else 'cores' if suffix=='cpu' else '',choices=choices,advanced=suffix not in ['memory','cpu'],help='Zero is unlimited.' if suffix in ['memory','cpu','pids','log_mb'] else '')
for name,(group,label,memory,cpu,mode) in UNITS.items():
 field(name+'.memory',group,label+' · Memory limit','number',memory,('unit',name,'MemoryMax'),minimum=32,maximum=4096,unit='MB',advanced=False,help='Zero is unlimited.')
 field(name+'.cpu',group,label+' · CPU limit','number',cpu,('unit',name,'CPUQuota'),minimum=1,maximum=200,unit='%',advanced=True,help='Zero is unlimited.')


def command(args,*,timeout=20,input=None):
 try:
  p=subprocess.run(args,input=input,text=True,capture_output=True,timeout=timeout)
  if p.returncode:raise SettingsError('The service configuration did not validate. No changes applied.')
  return p.stdout
 except (OSError,subprocess.SubprocessError):raise SettingsError('The service did not respond. Try again shortly.')

def load_yaml(path):
 if path.is_symlink():raise SettingsError('Service configuration is unavailable.')
 try:
  value=yaml.safe_load(path.read_text())
  if not isinstance(value,dict):raise ValueError()
  return value
 except (OSError,ValueError,yaml.YAMLError):raise SettingsError('Service configuration is unavailable.')

def compose_path(name):return BASE/DOCKERS[name][1]/'compose.yaml'
def unit_path(name):return UNIT_ROOT/(name+'.d')/'60-tamestorage-settings.conf'
def timer_path(name):return UNIT_ROOT/(name+'.d')/'60-tamestorage-settings.conf'

def lines_values(path):
 result={}
 if path.exists():
  if path.is_symlink():raise SettingsError('Service configuration is unavailable.')
  for line in path.read_text().splitlines():
   if '=' in line and not line.lstrip().startswith(('#',';')):
    k,v=line.split('=',1);result[k.strip()]=v.strip().strip('"').strip("'")
 return result

def state():
 if not STATE.exists():return {'pending':[],'audit':[]}
 try:
  value=json.loads(STATE.read_text());assert isinstance(value,dict) and isinstance(value.get('pending',[]),list)
  return value
 except (OSError,ValueError,AssertionError):raise SettingsError('Saved settings status is unavailable.')

def duration(value):
 text=str(value).strip()
 if text in ['','0','0s']:return 0
 if re.fullmatch(r'[0-9.]+',text):return float(text)
 matches=re.findall(r'([0-9.]+)(ms|s|min|m|h|d)',text)
 if not matches:raise ValueError()
 return sum(float(n)*{'ms':.001,'s':1,'m':60,'min':60,'h':3600,'d':86400}[u] for n,u in matches)

def decode(value,f,fmt=''):
 if f['type']=='boolean':return str(value).lower() in ['true','1','yes','on']
 if f['type']!='number':return str(value)
 if fmt=='gib':return float(value)/1024**3
 if fmt=='mib':return float(value)/1024**2
 if fmt in ['mb_m','mb']:
  match=re.fullmatch(r'([0-9.]+)\s*([gGmMkK]?)(?:[bB])?',str(value));assert match
  return float(match[1])*{'g':1024,'m':1,'k':1/1024,'':1}.get(match[2].lower(),1)
 if fmt=='node_heap':return int(re.search(r'--max-old-space-size=(\d+)',str(value))[1])
 if fmt=='duration':return duration(value)
 return float(value)

def encode(value,f,fmt=''):
 if f['type']=='boolean':return 'true' if value else 'false'
 if fmt in ['gib','mib']:return str(round(value*(1024**3 if fmt=='gib' else 1024**2)))
 if fmt in ['mb_m','mb']:return str(int(value))+('m' if fmt=='mb_m' else 'MB')
 if fmt=='node_heap':return '--max-old-space-size='+str(int(value))
 if fmt=='duration':return str(int(value))+'s'
 if isinstance(value,(int,float)):return str(int(value)) if int(value)==value else str(value)
 return str(value)

class Snapshot:
 def __init__(self,*,inspect=True):
  self.docs={};self.containers={};self.paths={STATE,OPS,APP_ENV};self.memo=state()
  names=[]
  for name in DOCKERS:
   p=compose_path(name)
   if p.exists():names.append(name);self.paths.add(p)
  if inspect and names:
   try:self.containers={r['Name'].lstrip('/'):r for r in json.loads(command(['docker','inspect',*names],timeout=12))}
   except SettingsError:
    for name in names:
     try:self.containers[name]=json.loads(command(['docker','inspect',name],timeout=4))[0]
     except SettingsError:pass
  self.environment={k:dict(v.split('=',1) for v in r['Config'].get('Env',[]) if '=' in v) for k,r in self.containers.items()}
  self.values={};self.available={}
  for key,f in FIELDS.items():
   try:
    value=self.read(f);self.values[key]=value;self.available[key]=True
   except (OSError,ValueError,KeyError,TypeError,AssertionError,IndexError,SettingsError,configparser.Error):self.values[key]=f['default'];self.available[key]=False
  self.revision=self.fingerprint()
 def document(self,path):
  self.paths.add(path)
  if path not in self.docs:self.docs[path]=load_yaml(path)
  return self.docs[path]
 def service(self,name):return self.document(compose_path(name))['services'][DOCKERS[name][2]]
 def unit_values(self,name):
  merged={}
  self.paths.add(unit_path(name))
  paths=[UNIT_ROOT/name]
  folder=paths[0].with_name(name+'.d')
  if folder.is_dir():paths+=sorted(folder.glob('*.conf'))
  for p in paths:self.paths.add(p);merged.update(lines_values(p))
  return merged
 def read(self,f):
  source=f['source'];kind=source[0];default=f['default']
  if kind=='env':
   _,names,key,fmt=source;name=names[0];row=self.service(name);environment=row.get('environment',{}) or {}
   if isinstance(environment,list):environment=dict(v.split('=',1) for v in environment if '=' in v)
   value=environment.get(key,None)
   if isinstance(value,str):value=value.replace('$$','$')
   if value is None:value=self.environment.get(name,{}).get(key,None)
   if value is None:return default
   return decode(value,f,fmt)
  if kind=='docker':
   _,name,key=source;r=self.service(name);actual=self.containers.get(name,{}).get('HostConfig',{})
   if key=='memory':return int(bytes_value(r.get('mem_limit',actual.get('Memory',0))))/1024**2
   if key=='cpu':return float(r.get('cpus',actual.get('NanoCpus',0)/1e9))
   if key=='pids':return max(0,int(r.get('pids_limit',actual.get('PidsLimit') or 0)))
   if key=='restart':return r.get('restart',actual.get('RestartPolicy',{}).get('Name','unless-stopped'))
   options=r.get('logging',{}).get('options',actual.get('LogConfig',{}).get('Config',{}))
   return bytes_value(options.get('max-size',0))/1024**2 if key=='log_mb' else int(options.get('max-file',1))
  if kind=='ops':return int(lines_values(OPS).get(source[1],default))
  if kind in ['unit','hostenv','timer']:
   name=source[1];values=self.unit_values(name)
   if kind=='hostenv':
    env={}
    for p in [UNIT_ROOT/name,unit_path(name)]:
     if p.exists():
      for line in p.read_text().splitlines():
       if line.startswith('Environment='):
        for v in shlex.split(line.split('=',1)[1]):
         if '=' in v:k,v=v.split('=',1);env[k]=v
    return decode(env.get(source[2],default),f)
   value=values.get(source[2],None)
   if value is None:return default
   if kind=='unit':return bytes_value(value)/1024**2 if source[2]=='MemoryMax' else (0 if value=='' else float(value.rstrip('%')))
   return duration(value)/(3600 if f['unit']=='hours' else 1)
  if kind=='ini':
   p=INI[source[1]];self.paths.add(p);c=configparser.RawConfigParser(strict=False);c.read(p)
   if not p.exists():raise OSError()
   return decode(c.get(source[2],source[3],fallback=default),f,source[4])
  if kind=='apache':
   path=BASE/'webdav/httpd.conf';self.paths.add(path)
   matches=re.findall(r'(?m)^[ \t]*'+re.escape(source[1])+r'[ \t]+([^\r\n]+)',path.read_text())
   if not matches:raise SettingsError('WebDAV configuration is unavailable.')
   return decode(matches[-1].strip(),f)
  if kind=='flag':
   _,name,flag,fmt=source;args=self.service(name).get('command',self.containers.get(name,{}).get('Config',{}).get('Cmd',[]));args=shlex.split(args) if isinstance(args,str) else args
   for i,arg in enumerate(args):
    if arg==flag:return decode(args[i+1],f,fmt)
    if arg.startswith(flag+'='):return decode(arg.split('=',1)[1],f,fmt)
   return default
  raise ValueError()
 def fingerprint(self):
  h=hashlib.sha256()
  for p in sorted(self.paths):
   h.update(str(p).encode());h.update(b'\0')
   if p.is_symlink():raise SettingsError('Service configuration is unavailable.')
   h.update(p.read_bytes() if p.exists() else b'<missing>')
  return h.hexdigest()
 def public(self):
  groups=[dict(id=k,label=label,icon=icon) for k,label,icon in GROUPS]
  if compose_path('tamestorage-control-check').exists():groups.append(dict(id='verification',label='Control verification',icon='flask'))
  fields=[]
  for k,f in FIELDS.items():
   if not self.available[k]:continue
   item={x:v for x,v in f.items() if x not in ['source','default'] and v is not None};item['value']=None if f['secret'] else self.values[k]
   if f['secret']:item['configured']=bool(self.values[k])
   fields.append(item)
  pending=[k for k in self.memo.get('pending',[]) if k in FIELDS]
  return dict(revision=self.revision,groups=groups,fields=fields,pending=pending,history=self.memo.get('audit',[])[:8],links={'uptime':'https://server.tailb7f57c.ts.net:3001/dashboard','music':'https://server.tailb7f57c.ts.net:10000/','vault':'https://server.tailb7f57c.ts.net:9443/'})

def bytes_value(value):
 if isinstance(value,(float,int)):return value
 text=str(value).strip().lower()
 if text in ['infinity','max','-1','']:return 0
 match=re.fullmatch(r'([0-9.]+)\s*([kmgt]?)(?:i?b)?',text)
 if not match:raise ValueError()
 return float(match[1])*1024**({'':0,'k':1,'m':2,'g':3,'t':4}[match[2]])

def validate_value(f,value):
 kind=f['type']
 if kind=='boolean':
  if type(value) is not bool:raise SettingsError(f['label']+': choose on or off.')
 elif kind=='number':
  if type(value) not in [float,int] or not math.isfinite(value):raise SettingsError(f['label']+': enter a number.')
  unlimited=f['source'][0] in ['docker','unit'] and f['source'][-1] in ['memory','cpu','pids','log_mb','CPUQuota','MemoryMax']
  if not (unlimited and value==0):
   if f['min'] is not None and value<f['min'] or f['max'] is not None and value>f['max']:raise SettingsError(f['label']+': value is out of range.')
  if f['unit'] not in ['GB','cores'] and not (f['source'][0]=='ini' and f['source'][-1]=='mib') and f['key']!='music.lyrics_delay' and int(value)!=value:raise SettingsError(f['label']+': enter a whole number.')
 else:
  if not isinstance(value,str) or len(value)>(2048 if kind in ['password','url'] else 1000) or any(ord(c)<32 or c=='\x7f' for c in value):raise SettingsError(f['label']+': invalid text.')
  if kind=='select' and value not in f['choices']:raise SettingsError(f['label']+': choose a listed option.')
  if kind=='timezone':
   try:ZoneInfo(value)
   except (ValueError,KeyError):raise SettingsError('Choose a valid IANA time zone.')
  if kind=='url' and value:
   from urllib.parse import urlsplit
   u=urlsplit(value)
   if u.scheme not in ['http','https'] or not u.hostname or u.username or u.password or u.fragment:raise SettingsError(f['label']+': enter an HTTP(S) URL without credentials.')
  if kind=='providers' and (not value or any(v.strip() not in ['lrclib','simpmusic','netease'] for v in value.split(','))):raise SettingsError('Choose LRCLIB, SimpMusic and/or NetEase in order.')
 return value

def plan(snapshot,changes,revision):
 if revision!=snapshot.revision:raise SettingsError('Settings changed elsewhere. Refresh before saving.')
 if not isinstance(changes,dict) or len(changes)>len(FIELDS):raise SettingsError('Invalid settings request.')
 validated={}
 for key,value in changes.items():
  if key not in FIELDS or not snapshot.available.get(key):raise SettingsError('Unknown or unavailable setting.')
  value=validate_value(FIELDS[key],value)
  if value!=snapshot.values[key]:validated[key]=value
 all_values={**snapshot.values,**validated}
 for name in DOCKERS:
  if name+'.log_files' in validated and all_values[name+'.log_files']>1 and not all_values[name+'.log_mb']:raise SettingsError('Set a log file size before enabling log rotation.')
 if all_values['grammar.heap_min']>all_values['grammar.heap']:raise SettingsError('Java initial heap must not exceed maximum heap.')
 memory=all_values['languagetool.memory']
 if memory and memory<all_values['grammar.heap']+256:raise SettingsError('LanguageTool memory must allow Java heap plus 256 MB.')
 memory=all_values['uptime-kuma.memory']
 if memory and memory<all_values['uptime.heap']+128:raise SettingsError('Uptime Kuma memory must allow Node heap plus 128 MB.')
 if all_values['gateway.budget']<all_values['gateway.baseline_timeout']+all_values['gateway.verify']+5:raise SettingsError('Grammar budget must allow the baseline timeout, verification and 5 seconds.')
 return validated

def impacts(keys):
 docker=set();units=set();timers=set();next_job=False
 for key in keys:
  if key not in FIELDS:continue
  f=FIELDS[key];s=f['source'];kind=s[0]
  if kind=='env':docker.update(s[1])
  elif kind in ['docker','flag']:docker.add(s[1])
  elif kind=='apache':docker.add('tamestorage-webdav')
  elif kind=='ini':docker.add('livesync-couchdb' if s[1]=='couchdb' else 'tamestorage-calendar')
  elif kind in ['unit','hostenv']:units.add(s[1]);next_job|=UNITS[s[1]][4]=='next'
  elif kind=='timer':timers.add(s[1])
 return dict(containers=sorted(docker,key=lambda n:n=='tamestorage'),units=sorted(units),timers=sorted(timers),next_job=next_job,next_cleanup=any(FIELDS.get(k,{}).get('source',[''])[0]=='ops' for k in keys))

def expanded_keys(keys,pending):
 result=set(keys)
 for _ in range(2):
  affected=impacts(result)
  for key in pending:
   if key not in FIELDS:continue
   related=impacts([key])
   if any(set(affected[k])&set(related[k]) for k in ['containers','units','timers']):result.add(key)
 return result

def preview(payload):
 snap=Snapshot();changes=plan(snap,payload.get('changes'),payload.get('revision'))
 keys=snap.memo.get('pending',[]) if payload.get('pending_only') else expanded_keys(changes,snap.memo.get('pending',[]))
 return dict(changes=[dict(key=k,label=FIELDS[k]['label'],before='Configured' if FIELDS[k]['secret'] and snap.values[k] else 'Empty' if FIELDS[k]['secret'] else snap.values[k],after='Updated' if FIELDS[k]['secret'] else v) for k,v in changes.items()],impact=impacts(keys),pending=snap.memo.get('pending',[]))

def atomic(path,data,mode=None):
 if path.is_symlink():raise SettingsError('Configuration path is unavailable.')
 path.parent.mkdir(parents=True,exist_ok=True)
 previous=path.stat() if path.exists() else None
 if mode is None:mode=previous.st_mode&0o777 if previous else 0o600
 fd,name=tempfile.mkstemp(dir=path.parent,prefix='.settings-')
 try:
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.chmod(name,mode)
  if previous:os.chown(name,previous.st_uid,previous.st_gid)
  os.replace(name,path)
 finally:
  if os.path.exists(name):os.unlink(name)

def make_writes(snap,changes):
 documents=copy.deepcopy(snap.docs);writes={};unit_names=set();timers=set();inis={};ops_changed=False
 values={**snap.values,**changes}
 for key,value in changes.items():
  f=FIELDS[key];source=f['source'];kind=source[0]
  if kind in ['env','docker','flag']:
   names=source[1] if kind=='env' else [source[1]]
   for name in names:
    path=compose_path(name)
    if path not in documents:documents[path]=copy.deepcopy(snap.document(path))
    service=documents[path]['services'][DOCKERS[name][2]]
    if kind=='env':
     environment=service.setdefault('environment',{})
     if isinstance(environment,list):environment=dict(v.split('=',1) for v in environment if '=' in v);service['environment']=environment
     encoded=encode(value,f,source[3])
     if source[3]=='node_heap':
      previous=environment.get(source[2],snap.environment.get(name,{}).get(source[2],''))
      encoded=re.sub(r'--max-old-space-size=\d+',encoded,str(previous)) if '--max-old-space-size=' in str(previous) else (str(previous)+' '+encoded).strip()
     environment[source[2]]=encoded.replace('$','$$')
    elif kind=='docker':
     setting=source[2]
     if setting=='memory':service['mem_limit']=str(round(value*1024**2))
     elif setting=='cpu':service['cpus']=value
     elif setting=='pids':service['pids_limit']=int(value) if value else -1
     elif setting=='restart':service['restart']=value
     else:
      logging=service.setdefault('logging',{'driver':'json-file'});options=logging.setdefault('options',{})
      if setting=='log_mb':
       if value:options['max-size']=str(round(value*1024**2))
       else:options.pop('max-size',None);options.pop('max-file',None)
      elif value==1 and not values[name+'.log_mb']:options.pop('max-file',None)
      else:options['max-file']=str(int(value))
    else:
     args=service.get('command',snap.containers.get(name,{}).get('Config',{}).get('Cmd',[]));args=shlex.split(args) if isinstance(args,str) else list(args);flag=source[2];encoded=encode(value,f,source[3]);found=False
     for i,arg in enumerate(args):
      if arg==flag:args[i+1]=encoded;found=True;break
      if arg.startswith(flag+'='):args[i]=flag+'='+encoded;found=True;break
     if not found:args.extend([flag,encoded])
     service['command']=args
  elif kind=='apache':
   path=BASE/'webdav/httpd.conf';body=writes.get(path,path.read_bytes()).decode()
   directives={source[1]:encode(value,f)}
   if source[1]=='MaxRequestWorkers':directives.update(ThreadsPerChild=str(int(value)),MaxSpareThreads=str(max(8,int(value))))
   for directive,encoded in directives.items():
    body,count=re.subn(r'(?m)^([ \t]*)'+re.escape(directive)+r'[ \t]+[^\r\n]+$',lambda m:m[1]+directive+' '+encoded,body)
    if not count:raise SettingsError('WebDAV configuration is unavailable.')
   writes[path]=body.encode()
  elif kind in ['unit','hostenv']:unit_names.add(source[1])
  elif kind=='timer':timers.add(source[1])
  elif kind=='ops':ops_changed=True
  elif kind=='ini':
   path=INI[source[1]]
   if path not in inis:c=configparser.RawConfigParser(strict=False);c.optionxform=str;c.read(path);inis[path]=c
   c=inis[path]
   if not c.has_section(source[2]):c.add_section(source[2])
   c.set(source[2],source[3],encode(value,f,source[4]))
 for path,data in documents.items():
  if data!=snap.docs.get(path):writes[path]=yaml.safe_dump(data,sort_keys=False,allow_unicode=True).encode()
 for name in unit_names:
  lines=['[Service]']
  for key,f in FIELDS.items():
   s=f['source']
   if s[0] not in ['unit','hostenv'] or s[1]!=name:continue
   value=values[key]
   if s[0]=='hostenv':lines.append('Environment='+json.dumps(s[2]+'='+encode(value,f)))
   elif s[2]=='MemoryMax':lines.append('MemoryMax='+('infinity' if value==0 else str(round(value*1024**2))))
   else:lines.append('CPUQuota='+('' if value==0 else str(value)+'%'))
  if name=='lecture-search@.service':lines.append('RuntimeMaxSec='+str(int(values['search.timeout'])+10))
  if name=='cyborg-grammar-gateway@.service':lines.append('RuntimeMaxSec='+str(int(max(values['gateway.budget'],values['gateway.assist']))+20))
  writes[unit_path(name)]=('\n'.join(lines)+'\n').encode()
 for name in timers:
  lines=['[Timer]']
  for key,f in FIELDS.items():
   s=f['source']
   if s[0]!='timer' or s[1]!=name:continue
   value=values[key]*(3600 if f['unit']=='hours' else 1)
   lines.extend([s[2]+'=',s[2]+'='+str(int(value))+'s'])
  writes[timer_path(name)]=('\n'.join(lines)+'\n').encode()
 if ops_changed:
  lines=['# Managed by Tamestorage Server settings. Numeric values only.']
  for key,f in FIELDS.items():
   if f['source'][0]=='ops':lines.append(f['source'][1]+'='+str(int(values[key])))
  writes[OPS]=('\n'.join(lines)+'\n').encode()
 import io
 for path,c in inis.items():out=io.StringIO();c.write(out);writes[path]=out.getvalue().encode()
 return writes

def validate_files(writes):
 for path,data in writes.items():
  if path==BASE/'webdav/httpd.conf':
   fd,name=tempfile.mkstemp(dir='/srv/tamestorage/.tamestorage_state/webdav',prefix='.settings-validate-',suffix='.conf')
   try:
    with os.fdopen(fd,'wb') as f:f.write(data);os.fchmod(f.fileno(),0o644)
    command(['docker','exec','tamestorage-webdav','httpd','-t','-f','/state/'+Path(name).name],timeout=20)
   finally:os.unlink(name)
   continue
  if path.name!='compose.yaml':continue
  fd,name=tempfile.mkstemp(dir=path.parent,prefix='.settings-validate-',suffix='.yaml')
  try:
   with os.fdopen(fd,'wb') as f:f.write(data)
   command(['docker','compose','--project-directory',str(path.parent),'-f',name,'config','--quiet'],timeout=20)
  finally:os.unlink(name)

def save_files(snap,changes):
 writes=make_writes(snap,changes);validate_files(writes)
 if snap.fingerprint()!=snap.revision:raise SettingsError('Settings changed elsewhere. Refresh before saving.')
 old={p:p.read_bytes() if p.exists() else None for p in writes}
 old_modes={p:p.stat().st_mode&0o777 for p in writes if p.exists()}
 memo=copy.deepcopy(snap.memo);memo['pending']=sorted((set(memo.get('pending',[]))|set(changes))-{k for k in changes if FIELDS[k]['source'][0]=='ops'});memo['audit']=([dict(at=int(time.time()),keys=list(changes))]+memo.get('audit',[]))[:20]
 old_state=STATE.read_bytes() if STATE.exists() else None
 try:
  # Pending IDs survive a broker/power interruption; no secret values in metadata.
  atomic(STATE,json.dumps(memo).encode(),0o600)
  for path,data in writes.items():atomic(path,data,0o600 if path.name=='compose.yaml' else 0o644 if path.suffix=='.conf' and path!=OPS else None)
 except Exception:
  for path,data in old.items():
   if data is None:path.unlink(missing_ok=True)
   else:atomic(path,data,old_modes[path])
  if old_state is None:STATE.unlink(missing_ok=True)
  else:atomic(STATE,old_state,0o600)
  raise
 return memo

def check_nas(name):
 if name in controls.NAS:controls.mounts_ready(name)

def apply_saved(keys,job):
 impact=impacts(keys)
 if impact['units'] or impact['timers']:command(['systemctl','daemon-reload'])
 for name in impact['units']:
  group,label,mem,cpu,mode=UNITS[name]
  if mode=='live':
   value=Snapshot(inspect=False);memory=value.values[name+'.memory'];quota=value.values[name+'.cpu']
   if memory:
    used=int(command(['systemctl','show',name,'-p','MemoryCurrent','--value']).strip() or 0)
    if used>memory*1024**2:raise SettingsError('Memory limit is below current usage. The saved limit will apply on the next service start.')
   command(['systemctl','set-property','--runtime',name,'MemoryMax='+('infinity' if memory==0 else str(round(memory*1024**2))),'CPUQuota='+('' if quota==0 else str(quota)+'%')])
 for name in impact['timers']:command(['systemctl','restart',name])
 for name in impact['containers']:
  check_nas(name)
  row=controls.inspect(name);project=compose_path(name);service=DOCKERS[name][2]
  image_file=project.with_name('compose.settings-images.yaml')
  image_values=load_yaml(image_file) if image_file.exists() else {'services':{}}
  image_values.setdefault('services',{})[service]={'image':row['Image']}
  atomic(image_file,yaml.safe_dump(image_values,sort_keys=False).encode(),0o644)
  with controls.LOCK:job['message']='Applying '+DOCKERS[name][3]
  args=['docker','compose','--project-directory',str(project.parent),'-f',str(project),'-f',str(image_file),'up','-d','--no-deps','--no-build','--pull','never']
  running=row.get('State',{}).get('Running') and name not in controls.stopped()
  if not running:args.append('--no-start')
  elif ((row.get('Config',{}).get('Healthcheck') or {}).get('Test') or ['NONE'])[0]!='NONE':args.extend(['--wait','--wait-timeout','90'])
  args.append(service);command(args,timeout=120)
  if running and not controls.inspect(name).get('State',{}).get('Running'):raise SettingsError('The service did not stay running. Saved settings are pending; check service status.')
 return impact

def _run(ident,revision,changes,apply,pending_only):
 time.sleep(.7)
 with controls.LOCK:job=controls.JOBS[ident];job['state']='running';job['message']='Saving settings'
 try:
  snap=Snapshot()
  changes=plan(snap,changes,revision)
  memo=save_files(snap,changes) if changes else snap.memo
  with controls.LOCK:job['saved']=True
  keys=set(memo.get('pending',[])) if pending_only else expanded_keys(changes,memo.get('pending',[]))
  if apply and keys:
   apply_saved(keys,job)
   memo=state();memo['pending']=sorted(set(memo.get('pending',[]))-keys);atomic(STATE,json.dumps(memo).encode(),0o600)
  with controls.LOCK:job['state']='done';job['message']='Applied.' if apply else 'Saved.'
 except Exception as error:
  with controls.LOCK:job['state']='error';job['error']=str(error) if isinstance(error,(SettingsError,controls.ControlError)) else 'Settings could not be saved. Refresh and check pending changes.'

def save(payload):
 if type(payload.get('apply',False)) is not bool or type(payload.get('pending_only',False)) is not bool:raise SettingsError('Invalid settings request.')
 snap=Snapshot();changes=plan(snap,payload.get('changes',{}),payload.get('revision'))
 pending_only=payload.get('pending_only',False);keys=set(snap.memo.get('pending',[])) if pending_only else expanded_keys(changes,snap.memo.get('pending',[]))
 if not keys:return dict(unchanged=True)
 targets=impacts(keys)['containers']
 with controls.LOCK:
  for ident in list(controls.JOBS):
   if time.monotonic()-controls.JOBS[ident]['at']>600 and controls.JOBS[ident]['state'] not in ['queued','running']:del controls.JOBS[ident]
  if any(v['state'] in ['queued','running'] for v in controls.JOBS.values()):raise SettingsError('A service change is in progress. Try again shortly.')
  ident=os.urandom(12).hex();controls.JOBS[ident]=dict(name='settings',action='settings',targets=targets,state='queued',message='Preparing settings',at=time.monotonic())
  controls.EXECUTOR.submit(_run,ident,snap.revision,changes,payload.get('apply',False),pending_only)
 return dict(operation=ident)

def dispatch(payload):
 action=payload.get('action')
 if action=='settings-get':return Snapshot().public()
 if action=='settings-preview':return preview(payload)
 if action=='settings-save':return save(payload)
 raise SettingsError('Unknown settings action.')
