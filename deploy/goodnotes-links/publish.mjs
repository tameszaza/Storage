import {readFile,writeFile,mkdir,rename} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {connect} from './client.mjs';
const base=process.env.HOME+'/.local/lib/goodnotes-links/';
const stateDir='/srv/tamestorage/.tamestorage_state/goodnotes-obsidian-links';
const statePath=stateDir+'/published.json';
const hash=s=>createHash('sha256').update(s).digest('hex');
let client;
try {
 const manifest=JSON.parse(execFileSync('docker',['exec','-i','tamestorage','python','-'],{input:await readFile(base+'manifest.py'),timeout:90000,maxBuffer:4*1024*1024}));
 if(!manifest.entries?.length)throw Error('No PDFs found; database left unchanged');
 let previous={};try{previous=JSON.parse(await readFile(statePath,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
 const entries=manifest.entries;
 const prefix='PDF Library/';
 const managedUrl='https://server.tailb7f57c.ts.net/s/';
 const groups=new Map();
 const desired=[];
 for(const entry of entries){
  if(entry.path.startsWith('/')||entry.path.split('/').includes('..')||!entry.path.toLowerCase().endsWith('.pdf')||!entry.url.startsWith(managedUrl)||Buffer.byteLength(entry.url)>300)throw Error('Unsafe placeholder entry');
  desired.push({path:prefix+entry.path,body:entry.url,binary:true});
  const idx=entry.path.lastIndexOf('/');const group=idx<0?'General':entry.path.slice(0,idx);
  if(!groups.has(group))groups.set(group,[]);groups.get(group).push(entry.path);
 }
 const lines=['# Goodnotes PDF Library','','These PDF links open the originals on the server. The vault contains only small URL placeholders.','','Keep Tailscale connected. Enable Tames PDF on each reviewing device. PDFs are read-only here; keep comments in Obsidian notes and handwriting in Goodnotes.','','After Goodnotes sync finishes, close and reopen an existing PDF tab to load its latest version. Renaming files or rearranging pages can break links.','','New exported PDFs are linked automatically by the server after the Goodnotes sync burst settles. No Mac refresh is required.',''];
 for(const [group,paths] of [...groups].sort(([a],[b])=>a<b?-1:a>b?1:0)){
  lines.push('## '+group,'');
  for(const path of paths){const label=path.split('/').at(-1).replace(/\.pdf$/i,'').replaceAll('|',' / ');const target=(prefix+path).replaceAll('#','%23').replaceAll('|','%7C');lines.push('- [['+target+'|'+label+']]');}lines.push('');
 }
 desired.push({path:'Goodnotes PDF Library.md',body:lines.join('\n'),binary:false});
 const desiredPaths=new Set(desired.map(d=>d.path));
 const stale=Object.keys(previous).filter(path=>path.startsWith(prefix)&&path.toLowerCase().endsWith('.pdf')&&!desiredPaths.has(path)).map(path=>path.slice(prefix.length));
 if(stale.length){
  execFileSync(process.execPath,[base+'cleanup-links.mjs'],{input:JSON.stringify({deleted:stale}),timeout:240000,maxBuffer:1024*1024});
  previous=JSON.parse(await readFile(statePath,'utf8'));
 }
 const changed=desired.filter(d=>previous[d.path]!==hash(d.body));
 if(!changed.length){console.log(JSON.stringify({links:entries.length,changed:0}));process.exit(0);}
 client=await connect();
 let writes=0,verified=0;
 for(const d of changed){
  const old=await client.get(d.path);
  let existing;
  if(old){
   if(old.type==='newnote'){
    const {decodeBinary}=await import('@vrtmrz/livesync-commonlib/compat/string_and_binary/convert');
    existing=Buffer.from(decodeBinary(old.data)).toString('utf8');
   }else existing=old.data.join('');
   if(d.binary&&(!existing.startsWith(managedUrl)||Buffer.byteLength(existing)>300))throw Error('Refusing to overwrite unmanaged file');
   if(!d.binary&&!existing.startsWith('# Goodnotes PDF Library\n'))throw Error('Refusing to overwrite unrelated index');
  }
  if(existing!==d.body){
   const now=Date.now();const blob=new Blob([d.body],{type:d.binary?'application/octet-stream':'text/plain'});
   if(!await client.put(d.path,blob,{ctime:old?.ctime??now,mtime:Math.max(now,(old?.mtime??0)+1),size:Buffer.byteLength(d.body)},d.binary?'newnote':'plain'))throw Error('LiveSync rejected library update');
   const check=await client.get(d.path);
   let restored;
   if(check?.type==='newnote'){
    const {decodeBinary}=await import('@vrtmrz/livesync-commonlib/compat/string_and_binary/convert');restored=Buffer.from(decodeBinary(check.data)).toString('utf8');
   }else restored=check?.data.join('');
   if(restored!==d.body)throw Error('Library readback differs');writes++;
  }
  previous[d.path]=hash(d.body);verified++;
 }
 await mkdir(stateDir,{recursive:true,mode:0o700});
 await writeFile(statePath+'.tmp',JSON.stringify(previous),{mode:0o600});await rename(statePath+'.tmp',statePath);
 console.log(JSON.stringify({links:entries.length,changed:writes,verified}));
 await client.close();process.exit(0);
}catch(e){if(client)await client.close().catch(()=>{});console.error('Goodnotes link publishing failed:',e?.message?.replace(/https?:\/\/\S+/g,'[endpoint]'));process.exit(1);}
