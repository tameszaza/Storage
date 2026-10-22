import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile,mkdir,rm} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const tmp=await mkdtemp('/tmp/tames-prune-check-');
try{
 await mkdir(tmp+'/source');await writeFile(tmp+'/source/still.pdf','current');
 const url='https://server.tailb7f57c.ts.net/s/fixture';
 const doc=body=>({type:'plain',data:[body]});
 const docs={'PDF Library/deleted.pdf':doc(url),'PDF Library/edited.pdf':doc('user text'),'PDF Library/still.pdf':doc(url)};
 const hash=s=>createHash('sha256').update(s).digest('hex');
 const hashes=Object.fromEntries(Object.keys(docs).map(k=>[k,hash(url)]));
 await writeFile(tmp+'/hashes.json',JSON.stringify(hashes));await writeFile(tmp+'/docs.json',JSON.stringify(docs));
 await writeFile(tmp+'/client.mjs',`import {readFile,writeFile} from 'node:fs/promises';const path=${JSON.stringify(tmp+'/docs.json')};let docs=JSON.parse(await readFile(path));export async function connect(){return {get:async p=>docs[p],delete:async p=>{delete docs[p];return true;},close:async()=>writeFile(path,JSON.stringify(docs))};}`);
 let script=await readFile(new URL('./cleanup-links.mjs',import.meta.url),'utf8');
 script=script.replace("'/srv/tamestorage/uploads/Goodnotes/'",JSON.stringify(tmp+'/source/')).replace("'/srv/tamestorage/.tamestorage_state/goodnotes-obsidian-links/published.json'",JSON.stringify(tmp+'/hashes.json'));
 await writeFile(tmp+'/cleanup.mjs',script);
 const result=JSON.parse(execFileSync(process.execPath,[tmp+'/cleanup.mjs'],{input:JSON.stringify({deleted:['deleted.pdf','edited.pdf','still.pdf']}),encoding:'utf8'}));
 assert.equal(result.removed_placeholders,1);assert.equal(result.kept_edited,1);
 const after=JSON.parse(await readFile(tmp+'/docs.json'));assert(!after['PDF Library/deleted.pdf']);assert.equal(after['PDF Library/edited.pdf'].data[0],'user text');assert(after['PDF Library/still.pdf']);
 assert.throws(()=>execFileSync(process.execPath,[tmp+'/cleanup.mjs'],{input:JSON.stringify({deleted:['../private.pdf']}),stdio:['pipe','pipe','pipe']}));
 console.log('Managed orphan pruning: removed exact pointer; preserved edited/existing source; rejected traversal.');
}finally{await rm(tmp,{recursive:true,force:true});}
