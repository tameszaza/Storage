/** Opt-in encrypted CouchDB check. Only disposable, uniquely namespaced notes. */
import {readFile,writeFile,unlink} from 'node:fs/promises';
import {createHash,randomUUID} from 'node:crypto';
import {CompatibleClient} from '/opt/livesync/client.mjs';
import {appendDiary,readNote} from '../deploy/obsidian-diary.mjs';

const options=JSON.parse(await readFile('/run/obsidian-livesync.json','utf8'));
const client=new CompatibleClient({...options,url:'http://livesync-couchdb:5984'},
  {fetch:(url,init)=>fetch(url,{...init,signal:AbortSignal.timeout(15000)})});
const hash=text=>createHash('sha256').update(text).digest('hex');
const statePath='/checks/state.json';
try {
 await client.ready.promise;
 if(['create','append'].includes(process.argv[2])){
  const existing=process.argv[2]==='append'?JSON.parse(await readFile(statePath,'utf8')):null;
  const prefix=existing?.prefix||'Integration checks/diary-'+randomUUID()+'/';
  if(!/^Integration checks\/diary-[0-9a-f-]+\/$/.test(prefix))throw Error('Unsafe fixture prefix');
  const wrapper={path2id:path=>client.path2id(prefix+path),getByMeta:meta=>client.getByMeta(meta),
   liveSyncLocalDB:{localDatabase:client.liveSyncLocalDB.localDatabase,
    putDBEntryWithLiveBaseRevision:(note,rev)=>client.liveSyncLocalDB.putDBEntryWithLiveBaseRevision({...note,path:prefix+note.path},rev)}};
  const input={id:randomUUID().replaceAll('-',''),date:'2099-12-31',timestamp:'2099-12-31T12:34:56+08:00',
    title:'Temporary integration check',markdown:'Synthetic diary integration test. Safe to remove after verification.',image:''};
  const before=await readNote(wrapper,'Diary/2099-12-31.md');
  if(existing&&hash(before.text)!==existing.hashes[prefix+'Diary/2099-12-31.md'])throw Error('Changed fixture; preserving it');
  await appendDiary(wrapper,input);await appendDiary(wrapper,input);
  const paths=[prefix+'Diary/2099-12-31.md',prefix+'Diary.md'];
  const hashes={};for(const path of paths)hashes[path]=hash((await readNote(client,path)).text);
  const day=await readNote(client,paths[0]);
  if(day.text.split('^log-').length!==(before?before.text.split('^log-').length+1:2))throw Error('Duplicate entry');
  if(before&&!day.text.startsWith(before.text))throw Error('Existing text changed');
  await writeFile(statePath,JSON.stringify({prefix,hashes}),{mode:0o600});
  console.log('PASS encrypted '+(before?'existing-note append':'creation')+'/readback and idempotent retry; two disposable notes ready for native sync check.');
 }else if(process.argv[2]==='cleanup'){
  const state=JSON.parse(await readFile(statePath,'utf8'));
  if(!/^Integration checks\/diary-[0-9a-f-]+\/$/.test(state.prefix))throw Error('Unsafe cleanup prefix');
  for(const [path,expected] of Object.entries(state.hashes)){
   if(!path.startsWith(state.prefix))throw Error('Unsafe fixture path');
   const current=await readNote(client,path);
   if(current&&hash(current.text)!==expected)throw Error('Fixture was changed; preserving it');
   if(current&&!await client.delete(path))throw Error('Fixture removal failed');
  }
  console.log('PASS checksum-verified fixture removal; user diary/index were never modified.');
 }else throw Error('Use create or cleanup');
}finally{await client.close();}
process.exit(0);
