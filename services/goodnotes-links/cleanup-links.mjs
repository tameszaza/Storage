import {readFile,writeFile,rename,stat} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {connect} from './client.mjs';
let input='';for await(const chunk of process.stdin)input+=chunk;
const paths=JSON.parse(input).deleted;
const root='/srv/tamestorage/uploads/Goodnotes/';
const statePath='/srv/tamestorage/.tamestorage_state/goodnotes-obsidian-links/published.json';
const hashes=JSON.parse(await readFile(statePath,'utf8'));
const hash=s=>createHash('sha256').update(s).digest('hex');
console.log=console.info=console.warn=()=>{};
let client,removed=0,kept=0;
try {
 client=await connect();
 for(const relative of paths){
  if(relative.startsWith('/')||relative.split('/').some(p=>p==='..'||p==='')||!relative.toLowerCase().endsWith('.pdf'))throw Error('Unsafe cleanup path');
  let exists=false;try{await stat(root+relative);exists=true;}catch(e){if(e.code!=='ENOENT')throw e;}
  if(exists)continue;
  const path='PDF Library/'+relative,doc=await client.get(path);
  if(!doc||doc.deleted||doc._deleted){delete hashes[path];continue;}
  let body;
  if(doc.type==='newnote'){
   const {decodeBinary}=await import('@vrtmrz/livesync-commonlib/compat/string_and_binary/convert');
   body=Buffer.from(decodeBinary(doc.data)).toString('utf8');
  }else body=doc.data.join('');
  if(!body.startsWith('https://server.tailb7f57c.ts.net/s/')||Buffer.byteLength(body)>300||hashes[path]!==hash(body)){kept++;continue;}
  if(!await client.delete(path))throw Error('LiveSync rejected placeholder deletion');
  const check=await client.get(path);
  if(check&&!check.deleted&&!check._deleted)throw Error('Placeholder deletion readback failed');
  delete hashes[path];removed++;
 }
 await writeFile(statePath+'.tmp',JSON.stringify(hashes),{mode:0o600});await rename(statePath+'.tmp',statePath);
 await client.close();process.stdout.write(JSON.stringify({removed_placeholders:removed,kept_edited:kept})+'\n');process.exit(0);
}catch(e){if(client)await client.close().catch(()=>{});process.stderr.write('Managed PDF link cleanup failed: '+e.message.replace(/https?:\/\/\S+/g,'[endpoint]')+'\n');process.exit(1);}
