/** Append-only diary writes using the existing LiveSync codec and revision CAS. */
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';

const MAX_NOTE = 1024 * 1024;
export function validateEntry(input) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(input.date || '') ||
      new Date(input.date + 'T00:00:00Z').toISOString().slice(0, 10) !== input.date)
    throw Error('Invalid diary date');
  if (!/^[0-9a-f]{32}$/.test(input.id || '')) throw Error('Invalid entry ID');
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$/.test(input.timestamp || '') ||
      (input.timestamp.slice(0, 10) !== input.date && input.target_date !== input.date)) throw Error('Invalid timestamp');
  if (typeof input.markdown !== 'string' || !input.markdown.trim() || input.markdown.length > 16000 ||
      /\^log-[0-9a-f]{32}\b/.test(input.markdown)) throw Error('Invalid entry text');
  const images = input.images ?? (input.image ? [input.image] : []);
  if (!Array.isArray(images) || images.length > 8 || images.some(image => typeof image !== 'string' ||
      !/^https:\/\/server\.tailb7f57c\.ts\.net\/obsidian-images\/[0-9a-f]{64}\.(png|jpg|gif|webp|avif)$/.test(image)))
    throw Error('Invalid stored images');
  input = {...input, images};
  return {...input, title: String(input.title || 'Log').replace(/[\r\n#\[\]<>`]/g, ' ').trim().slice(0, 120) || 'Log'};
}

export function entryBlock(input) {
  const image = (input.images ?? (input.image ? [input.image] : [])).map(url => `![Image](${url})\n\n`).join('');
  return `## ${input.timestamp.slice(0,10)===input.date?'':input.timestamp.slice(0,10)+' '}${input.timestamp.slice(11, 19)} — ${input.title}\n\n${image}${input.markdown.trim()}\n^log-${input.id}\n`;
}

export function addIndexLink(text, date) {
  const linked = new RegExp('\\[\\[(?:Diary/)?' + date + '(?:\\.md)?(?:\\|[^\\]]*)?\\]\\]');
  if (linked.test(text)) return text;
  const link = `- [[${date}]]\n`;
  const rows = [...text.matchAll(/^[-*] \[\[(?:Diary\/)?\d{4}-\d{2}-\d{2}(?:\.md)?(?:\|[^\]]*)?\]\][^\n]*(?:\n|$)/gm)];
  if (rows.length) {
    const last = rows.at(-1), offset = last.index + last[0].length;
    return text.slice(0, offset) + (last[0].endsWith('\n') ? '' : '\n') + link + text.slice(offset);
  }
  return text + (text.endsWith('\n\n') ? '' : '\n\n') + link;
}

export async function readNote(client, path) {
  const id = await client.path2id(path);
  let meta;
  try { meta = await client.liveSyncLocalDB.localDatabase.get(id, {conflicts: true}); }
  catch (error) { if (error.status === 404) return null; throw error; }
  if (meta._conflicts?.length || meta.deleted || meta._deleted) throw Error('Resolve the existing note conflict first');
  if (meta.type !== 'plain' || meta.size > MAX_NOTE) throw Error('Unsupported diary note');
  const loaded = await client.getByMeta(meta);
  if (!loaded || !Array.isArray(loaded.data)) throw Error('Diary text could not be read');
  const text = loaded.data.join('');
  if (Buffer.byteLength(text) > MAX_NOTE) throw Error('Diary exceeds the safe append limit');
  return {meta, text};
}

export async function updateNote(client, path, transform) {
  for (let attempt = 0; attempt < 5; attempt++) {
    const old = await readNote(client, path);
    const text = transform(old?.text ?? null);
    if (old && text === old.text) return {changed: false, text};
    if (Buffer.byteLength(text) > MAX_NOTE) throw Error('Diary exceeds the safe append limit');
    const now = Date.now();
    const data = new Blob([text], {type: 'text/plain'});
    const note = {_id: await client.path2id(path), path, data, ctime: old?.meta.ctime ?? now,
      mtime: Math.max(now, (old?.meta.mtime ?? 0) + 1), size: Buffer.byteLength(text),
      type: 'plain', datatype: 'plain', eden: {}, children: []};
    // Unlike ordinary put(), this refuses to overwrite a newer live revision.
    const result = await client.liveSyncLocalDB.putDBEntryWithLiveBaseRevision(note, old?.meta._rev);
    if (result?.ok) return {changed: true, text};
  }
  throw Error('The note is changing; retry the same request to finish safely');
}

export async function appendDiary(client, value) {
  const input = validateEntry(value), path = `Diary/${input.date}.md`;
  const marker = `^log-${input.id}`, block = entryBlock(input);
  const result = await updateNote(client, path, old => {
    if (old?.split(/\r?\n/).includes(marker)) return old;
    const text = old ?? '---\ntags:\n  - diary\n---\n';
    return text + (text.endsWith('\n\n') ? '' : text.endsWith('\n') ? '\n' : '\n\n') + block;
  });
  // Safe to resume here if the entry committed but index publication was interrupted.
  await updateNote(client, 'Diary.md', old => addIndexLink(old ?? '---\ntags:\n  - diary\n---\nYou may find my Diaries in this file.\n', input.date));
  const check = await readNote(client, path), index = await readNote(client, 'Diary.md');
  if (!check?.text.split(/\r?\n/).includes(marker) || !index || addIndexLink(index.text, input.date) !== index.text)
    throw Error('Diary readback could not be verified');
  return {success: true, path, status: result.changed ? 'saved' : 'already_saved', timestamp: input.timestamp};
}

function noteError(message,code='conflict') {
  const error=Error(message);error.safeCode=code;throw error;
}
export function notePath(value) {
  if(typeof value!=='string' || value.length>500 || value.includes('\\') || /[\x00-\x1f]/.test(value)) noteError('Use a visible Markdown note path','input');
  const parts=value.split('/');
  if(!value.endsWith('.md') || parts.some(p=>!p || p.startsWith('.')))noteError('Use a visible Markdown note path','input');
  return value;
}
export function textHash(text) {return createHash('sha256').update(text).digest('hex');}
export async function searchNotes(client,input) {
  const query=String(input.query||'').toLocaleLowerCase().slice(0,200),limit=Math.max(1,Math.min(50,Number(input.limit)||20));
  const ranges=[['','h:'],['h:\u{10FFFF}','i:'],['i:\u{10FFFF}','ix:'],['ix:\u{10FFFF}','ps:'],['ps:\u{10FFFF}','\u{10FFFF}']];
  let cursor={range:0,key:''};
  if(input.cursor) {
    try {cursor=JSON.parse(Buffer.from(input.cursor,'base64url').toString());}catch{noteError('Invalid note search cursor','input');}
    if(!Number.isInteger(cursor.range)||cursor.range<0||cursor.range>=ranges.length||typeof cursor.key!=='string'||cursor.key.length>2000)noteError('Invalid note search cursor','input');
  }
  const items=[];let scanned=0,next='';
  outer:for(let range=cursor.range;range<ranges.length;range++) {
    const lower=range===cursor.range&&cursor.key?cursor.key:ranges[range][0];
    for await(const meta of client._enumerate(lower,ranges[range][1],{metaOnly:true})) {
      if(range===cursor.range&&meta._id===cursor.key)continue;
      scanned++;
      if(!meta.deleted&&!meta._deleted) {
        let valid=true;try{notePath(meta.path);}catch{valid=false;}
        if(valid&&String(meta.path).toLocaleLowerCase().includes(query))items.push({path:meta.path,title:meta.path.split('/').at(-1).slice(0,-3),modified:meta.mtime,size:meta.size});
      }
      if(scanned>=500||items.length>=limit) {next=Buffer.from(JSON.stringify({range,key:meta._id})).toString('base64url');break outer;}
    }
  }
  return {success:true,items,scanned,truncated:!!next,next_cursor:next};
}

export async function noteOperation(client,input) {
  if(input.operation==='search')return searchNotes(client,input);
  const path=notePath(input.path),operation=input.operation;
  if(operation==='read') {
    const old=await readNote(client,path);
    if(!old)return {success:true,exists:false,path};
    const lines=old.text.split('\n'),start=Math.max(1,Math.min(100000,Number(input.start_line)||1)),count=Math.max(1,Math.min(300,Number(input.max_lines)||160));
    const text=lines.slice(start-1,start-1+count).join('\n').slice(0,16000);
    return {success:true,exists:true,path,text,start_line:start,total_lines:lines.length,truncated:start-1+count<lines.length||text.length>=16000,revision:textHash(old.text)};
  }
  if(operation!=='write' || !['create','append','replace','edit'].includes(input.mode))noteError('Choose a supported note operation','input');
  if(!/^[0-9a-f]{32}$/.test(input.id||'') || typeof input.content!=='string' || input.content.length>16000 || /<!-- tames-agent:[0-9a-f]{32} -->/.test(input.content))noteError('Invalid note text or operation identity','input');
  const marker=`<!-- tames-agent:${input.id} -->`;
  const result=await updateNote(client,path,old=>{
    if(old?.includes(marker))return old;
    if(input.mode==='create' && old!==null)noteError('That note already exists. Read it, then choose append/edit/replace explicitly.');
    if(input.mode!=='create' && old===null)noteError('That note does not exist. Choose create or the correct path.','missing');
    if(old!==null && (!/^[0-9a-f]{64}$/.test(input.revision||'') || textHash(old)!==input.revision))noteError('The note changed. Read it again and apply the change to its current revision.');
    let text=input.mode==='append'?old+(old.endsWith('\n')?'':'\n')+input.content:input.content;
    if(input.mode==='edit') {
      if(typeof input.find!=='string'||!input.find||input.find.length>16000)noteError('Supply the exact text to edit','input');
      const matches=old.split(input.find).length-1;
      if(!matches || matches>1 && !input.replace_all)noteError('The edit target is missing or appears more than once. Read the relevant lines and specify it exactly.');
      text=input.replace_all?old.split(input.find).join(input.content):old.replace(input.find,input.content);
    }
    return text+(text.endsWith('\n')?'':'\n')+marker+'\n';
  });
  const check=await readNote(client,path);
  if(!check?.text.includes(marker))throw Error('Note write could not be read back');
  return {success:true,path,status:result.changed?'saved':'already_saved',revision:textHash(check.text),operation_id:input.id};
}

async function main() {
  let source = '';
  for await (const part of process.stdin) {
    source += part;
    if (Buffer.byteLength(source) > 100000) throw Error('Request too large');
  }
  const raw = JSON.parse(source);
  const input = raw.operation ? raw : validateEntry(raw);
  const options = JSON.parse(await readFile('/run/obsidian-livesync.json', 'utf8'));
  if (options.idDerivationVersion !== 0 || options.encryptInternalMetadata !== false)
    throw Error('Unsupported vault format');
  const {CompatibleClient} = await import('/opt/livesync/client.mjs');
  // Reuse the existing codec/profile. Host loopback is not container loopback.
  const client = new CompatibleClient({...options, url: 'http://livesync-couchdb:5984'}, {
    fetch: (url, init) => fetch(url, {...init, signal: AbortSignal.timeout(20000)})
  });
  try {
    await Promise.race([client.ready.promise, new Promise((_, reject) => {
      const timer = setTimeout(() => reject(Error('Vault connection timeout')), 25000); timer.unref();
    })]);
    const result = input.operation ? await noteOperation(client,input) : await appendDiary(client, input);
    console.log((input.operation?'TAMES_NOTE_RESULT=':'TAMES_DIARY_RESULT=') + JSON.stringify(result));
  } finally { await client.close(); }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().then(() => process.exit(0)).catch(error => {
    if(error?.safeCode) console.log('TAMES_NOTE_RESULT='+JSON.stringify({success:false,error:error.message,code:error.safeCode,no_change:true}));
    console.error('Diary append could not finish safely. Retry the same request; existing entries are preserved.');
    process.exit(1);
  });
}
