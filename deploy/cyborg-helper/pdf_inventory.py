"""Bounded Poppler inventory, checkpointed beside the existing cached PDF."""
import json,os,tempfile,subprocess
from pathlib import Path
POLICY='poppler-significant100k-v1'
BATCH=64

def parse_inventory(stream,first,last):
 pages=set();header=False
 for line in stream:
  fields=line.split()
  if fields[:3]==['page','num','type']:header=True;continue
  if not fields or not fields[0].isdigit():continue
  if len(fields)<5:raise ValueError('Incomplete image inventory row')
  page=int(fields[0])
  if not first<=page<=last:raise ValueError('Unexpected inventory page')
  if fields[2]!='image':continue
  width,height=int(fields[3]),int(fields[4])
  if min(width,height)>=100 and width*height>=100000:pages.add(page)
 if not header:raise ValueError('Incomplete image inventory output')
 return sorted(pages)

def scan(file,total,run,jobs):
 file=Path(file)
 if type(total) is not int or not 1<=total<=20000:raise ValueError('Invalid PDF length')
 stat=file.stat();fp=[stat.st_size,stat.st_mtime_ns];cache=file.with_suffix('.inventory.json')
 state={'fingerprint':fp,'policy':POLICY,'total':total,'through':0,'pages':[]}
 try:
  saved=json.loads(cache.read_text())
  if saved.get('fingerprint')==fp and saved.get('policy')==POLICY and saved.get('total')==total:
   through=saved.get('through');pages=saved.get('pages')
   if type(through) is not int or not 0<=through<=total or not isinstance(pages,list) or len(pages)>total or any(type(n) is not int or not 1<=n<=through for n in pages):raise ValueError('Invalid inventory checkpoint')
   state=saved
 except FileNotFoundError:pass
 first=state['through']+1;last=min(total,first+BATCH-1)
 if first<=total:
  with tempfile.TemporaryDirectory(dir=jobs,prefix='inventory-') as directory:
   output=Path(directory)/'images.txt'
   with output.open('wb') as stream:
    run(['/usr/bin/pdfimages','-f',str(first),'-l',str(last),'-list',str(file)],timeout=120,log=stream,stderr=subprocess.DEVNULL)
   if output.stat().st_size>8*1024*1024:raise ValueError('Inventory output too large')
   with output.open() as stream:found=parse_inventory(stream,first,last)
  if [file.stat().st_size,file.stat().st_mtime_ns]!=fp:raise RuntimeError('Source changed')
  state['pages']=sorted(set(state['pages'])|set(found));state['through']=last
  temporary=cache.with_suffix('.next')
  try:
   with temporary.open('w') as stream:json.dump(state,stream);stream.flush();os.fsync(stream.fileno())
   temporary.replace(cache)
  finally:temporary.unlink(missing_ok=True)
 return {'pages':state['pages'],'through':state['through'],'total':total,'complete':state['through']==total}
