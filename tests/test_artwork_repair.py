import sys,tempfile,subprocess,importlib.util,json
from pathlib import Path
from PIL import Image
sys.path.insert(0,'/app')
spec=importlib.util.spec_from_file_location('cover_test','/app/lib/artwork_repair.py');w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
with tempfile.TemporaryDirectory() as d:
 root=Path(d);p=root/'test [abcdefghijk].opus';report=root/'report.tsv';state=root/'retry.json'
 subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=0.2','-c:a','libopus','-metadata','title=Repair test','-metadata','purl=https://www.youtube.com/watch?v=abcdefghijk',str(p)],check=True)
 def digest():return subprocess.check_output(['ffmpeg','-v','error','-i',str(p),'-map','0:a:0','-c','copy','-f','hash','-hash','sha256','-']).strip()
 original=digest();assert not w.valid_cover(p)
 calls=[]
 def fake(identifier,output):calls.append(identifier);Image.new('RGB',(256,256),'blue').save(output,'JPEG')
 w.thumbnail=fake;report.write_text('abcdefghijk\t'+str(p)+'\n')
 assert w.repair_imported_covers(report,root,state)['repaired']==1
 assert w.valid_cover(p) and digest()==original
 import mutagen
 assert mutagen.File(p)['title']==['Repair test']
 assert w.repair_imported_covers(report,root,state)['valid']==1 and len(calls)==1
 legacy=root/'old filename.opus';p.rename(legacy)
 assert w.video_identifier(legacy)=='abcdefghijk'
 a=mutagen.File(legacy);del a['metadata_block_picture'];a.save()
 report.write_text('abcdefghijk\t'+str(legacy)+'\n')
 def failed(identifier,output):calls.append(identifier);raise ValueError('Unavailable thumbnail')
 w.thumbnail=failed
 assert w.repair_imported_covers(report,root,state)['errors']==1
 assert not w.valid_cover(legacy)
 before=len(calls);report.write_text('')
 assert w.repair_imported_covers(report,root,state)['deferred']==1 and len(calls)==before
 # A valid unrelated file isn't audited on an empty report and no pending entries.
 state.write_text('{}')
 assert w.repair_imported_covers(report,root,state)=={'valid':0,'repaired':0,'deferred':0,'errors':0}
 def changed(identifier,output):fake(identifier,output);legacy.touch()
 w.thumbnail=changed
 try:w.repair(legacy,'abcdefghijk');raise AssertionError('Concurrent change overwritten')
 except ValueError as error:assert 'changed during repair' in str(error)
print('Passed: event-only repair, unchanged audio/title, legacy source URL, deferred retry, empty job no-op, concurrent-write guard')
