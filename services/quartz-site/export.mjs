import {mkdir,readFile,writeFile,rename,rm} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {connect} from '../goodnotes-links/client.mjs';
const root='/srv/tamestorage/.tamestorage_state/quartz-site', source=root+'/source';
const hash=x=>createHash('sha256').update(x).digest('hex');
const quiet=()=>{};console.log=console.info=console.warn=quiet;
const allowed=p=>!p.split('/').some(x=>x.startsWith('.')||x==='..')&&/\.(md|pdf|png|jpe?g|gif|webp|svg|canvas)$/i.test(p);
await mkdir(source,{recursive:true,mode:0o700});
let old={};try{old=JSON.parse(await readFile(root+'/export.json','utf8'))}catch(e){if(e.code!=='ENOENT')throw e}
const client=await connect(), docs=[],next={},pdfs=new Map();let changed=0;
try {
 for await(const meta of client.enumerateAllNormalDocs({metaOnly:true})) {
  if(meta.deleted||!allowed(meta.path))continue;
  let bytes;
  const previous=old[meta._id];
  if(previous?.rev===meta._rev) {
   try{bytes=Buffer.from(previous.original,'base64')}catch{}
  }
  if(!bytes){const d=await client.getByMeta(meta);if(!d)throw Error('Missing note chunks');bytes=d.type==='plain'?Buffer.from(d.data.join(''),'utf8'):Buffer.from(d.data.join(''),'base64');}
  if(meta.path.endsWith('.pdf')){
   if(bytes.length>4096)continue;const url=bytes.toString('utf8').trim();if(!/^https?:\/\/\S+$/.test(url))continue;pdfs.set(meta.path,url);
  }
  docs.push({meta,bytes});
 }
 function rewrite(text){return text.replace(/(!?)\[\[([^\]\n]+)\]\]/g,(all,embed,link)=>{
  const [target,label]=link.split('|');const i=target.indexOf('#');const name=i<0?target:target.slice(0,i),fragment=i<0?'':target.slice(i);
  let resolved=name,url=pdfs.get(name);if(!url){const matches=[...pdfs].filter(([p])=>p===name||p.endsWith('/'+name));if(matches.length===1){resolved=matches[0][0];url=matches[0][1];}}
  if(!url)return all;
  const href=url+fragment;return embed?`\n<iframe src="${href.replaceAll('&','&amp;').replaceAll('"','&quot;')}" title="PDF" loading="lazy" style="width:100%;height:75vh;border:0"></iframe>\n`:`[[${resolved}.md${fragment}|${label||path.posix.basename(name)}]]`;
 });}
 const desired=new Set();
 for(const {meta,bytes} of docs){
  let filename=meta.path,output=bytes;
  if(filename.endsWith('.pdf')){filename+='.md';const url=pdfs.get(meta.path);output=Buffer.from(`---\ntitle: ${JSON.stringify(path.posix.basename(meta.path,'.pdf'))}\n---\n\n[Open PDF](${url})\n\n<iframe src="${url.replaceAll('&','&amp;').replaceAll('"','&quot;')}" title="PDF" loading="lazy" style="width:100%;height:80vh;border:0"></iframe>\n`);}
  else if(filename.endsWith('.md')){
   let text=bytes.toString('utf8');
   if(filename.startsWith('PDF annotations/')){
    const block=text.match(/^```json[^\n]*\n([\s\S]*?)^```/m);
    if(block){const data=JSON.parse(block[1]);if(Array.isArray(data.highlights)){
     const front=text.match(/^---\n[\s\S]*?\n---/);const heading=text.match(/^# .+$/m);
     text=(front?.[0]||'')+'\n\n'+(heading?.[0]||'# Annotations')+'\n\n'+(data.pdf?'[['+data.pdf+']]\n\n':'')+data.highlights.map(h=>'## Page '+h.page+'\n\n'+(h.text?'> '+h.text.replaceAll('\n','\n> ')+'\n\n':'')+(h.note||'')).join('\n\n');
    }}
   }
   output=Buffer.from(rewrite(text));
  }
  desired.add(filename);const digest=hash(output),previous=old[meta._id];
  next[meta._id]={rev:meta._rev,path:filename,hash:digest,original:bytes.toString('base64')};
  const destination=path.join(source,filename);if(!destination.startsWith(source+'/'))throw Error('Unsafe note path');
  let exists=true;try{await readFile(destination)}catch{exists=false}
  if(!exists||previous?.hash!==digest||previous?.path!==filename){await mkdir(path.dirname(destination),{recursive:true,mode:0o700});await writeFile(destination+'.tmp',output,{mode:0o600});await rename(destination+'.tmp',destination);changed++;}
 }
 for(const previous of Object.values(old)){if(previous.path&&!desired.has(previous.path)){const p=path.join(source,previous.path);if(p.startsWith(source+'/')){await rm(p,{force:true});changed++;}}}
 // The home page reuses the user's existing Life note; no invented website content.
 const home=docs.find(x=>x.meta.path==='Life.md')||docs.find(x=>x.meta.path==='NTU.md');
 if(home){const text=rewrite(home.bytes.toString('utf8'));let before='';try{before=await readFile(source+'/index.md','utf8')}catch{}if(before!==text){await writeFile(source+'/index.md',text,{mode:0o600});changed++;}}
 if(changed)await writeFile(root+'/build-required','1',{mode:0o600});
 await writeFile(root+'/export.json.tmp',JSON.stringify(next),{mode:0o600});await rename(root+'/export.json.tmp',root+'/export.json');
 process.stdout.write(JSON.stringify({files:docs.length,pdfs:pdfs.size,changed})+'\n');
} finally{await client.close()}

process.exit(0);
