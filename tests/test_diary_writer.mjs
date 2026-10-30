import {test} from 'node:test';
import assert from 'node:assert/strict';
import {appendDiary, addIndexLink, validateEntry, entryBlock, noteOperation, textHash} from '../deploy/obsidian-diary.mjs';

const input = {date: '2026-10-22', timestamp: '2026-10-22T23:59:00+08:00', id: 'a'.repeat(32),
  title: 'Receipt analysis', markdown: 'Visible total: **$7.50**. The date is unreadable.',
  image: 'https://server.tailb7f57c.ts.net/obsidian-images/' + 'b'.repeat(64) + '.png'};

class FakeClient {
  constructor() {
    this.notes = new Map(); this.writes = 0; this.beforePut = null;
    this.liveSyncLocalDB = {localDatabase: {get: async path => {
      const row = this.notes.get(path);
      if (!row) throw {status: 404};
      return structuredClone(row.meta);
    }}, putDBEntryWithLiveBaseRevision: async (note, base) => {
      if (this.beforePut) await this.beforePut(note);
      const old = this.notes.get(note.path);
      if (old?.meta._rev !== base) return false;
      this.seed(note.path, await note.data.text()); this.writes++;
      return {ok: true};
    }};
  }
  seed(path, text, extra = {}) {
    const rev = String(Number(this.notes.get(path)?.meta._rev || 0) + 1);
    this.notes.set(path, {meta: {_id: path, path, _rev: rev, type: 'plain', size: Buffer.byteLength(text),
      ctime: 1, mtime: 1, ...extra}, text});
  }
  async path2id(path) { return path; }
  async getByMeta(meta) {
    const row = this.notes.get(meta.path);
    return {data: [row.text]};
  }
  text(path) { return this.notes.get(path)?.text; }
}

test('creates tagged daily note, image first, timestamp, body and linked index', async () => {
  const client = new FakeClient();
  assert.equal((await appendDiary(client, input)).status, 'saved');
  const text = client.text('Diary/2026-10-22.md');
  assert.ok(text.startsWith('---\ntags:\n  - diary\n---\n'));
  assert.ok(text.includes('## 23:59:00 — Receipt analysis'));
  assert.ok(text.indexOf('![Image]') < text.indexOf('Visible total'));
  assert.ok(client.text('Diary.md').includes('- [[2026-10-22]]'));
});

test('preserves existing diary prose/frontmatter and index closing prose', async () => {
  const client = new FakeClient(), old = '---\ntags: [diary, personal]\ncustom: keep\n---\nOriginal words.\n';
  const index = 'Intro\n- [[2026-10-19]]\n\nFuture plans stay here.\n';
  client.seed('Diary/2026-10-22.md', old); client.seed('Diary.md', index);
  await appendDiary(client, input);
  assert.ok(client.text('Diary/2026-10-22.md').startsWith(old));
  assert.equal(client.text('Diary.md'), 'Intro\n- [[2026-10-19]]\n- [[2026-10-22]]\n\nFuture plans stay here.\n');
});

test('same request retries do not duplicate or replace the original entry', async () => {
  const client = new FakeClient(); await appendDiary(client, input);
  const old = client.text('Diary/2026-10-22.md'), writes = client.writes;
  assert.equal((await appendDiary(client, {...input, markdown: 'A different model reply'})).status, 'already_saved');
  assert.equal(client.text('Diary/2026-10-22.md'), old); assert.equal(client.writes, writes);
});

test('a concurrent edit changes the live revision and is retained on retry', async () => {
  const client = new FakeClient(); client.seed('Diary/2026-10-22.md', 'Original\n');
  client.beforePut = async note => {
    if (note.path === 'Diary/2026-10-22.md') {
      client.beforePut = null; client.seed(note.path, client.text(note.path) + 'Concurrent user edit\n');
    }
  };
  await appendDiary(client, input);
  assert.ok(client.text('Diary/2026-10-22.md').startsWith('Original\nConcurrent user edit\n'));
});

test('interrupted index publication resumes without duplicating a committed entry', async () => {
  const client = new FakeClient();
  client.beforePut = async note => {
    if (note.path === 'Diary.md') throw Error('Connection interrupted');
  };
  await assert.rejects(appendDiary(client, input));
  client.beforePut = null; await appendDiary(client, input);
  assert.equal(client.text('Diary/2026-10-22.md').split('^log-').length, 2);
  assert.ok(client.text('Diary.md').includes('[[2026-10-22]]'));
});

test('refuses unresolved conflicts rather than overwriting them', async () => {
  const client = new FakeClient(); client.seed('Diary/2026-10-22.md', 'Preserve', {_conflicts: ['other-rev']});
  await assert.rejects(appendDiary(client, input), /conflict/);
  assert.equal(client.text('Diary/2026-10-22.md'), 'Preserve'); assert.equal(client.writes, 0);
});

test('supports existing qualified/aliased diary links and does not duplicate them', async () => {
  const text = '- [[Diary/2026-10-22|Today]]\n';
  assert.equal(addIndexLink(text, input.date), text);
  const client = new FakeClient(); client.seed('Diary.md', text);
  await appendDiary(client, input); assert.equal(client.text('Diary.md'), text);
});

test('text-only logs work and invalid dates/paths/images/anchors cannot enter the writer', async () => {
  const client = new FakeClient(); await appendDiary(client, {...input, image: ''});
  assert.ok(!client.text('Diary/2026-10-22.md').includes('![Image]'));
  for (const bad of [{date: '../../secrets'}, {date: '2026-02-30'}, {image: 'https://evil.example/file.png'},
    {id: 'wrong'}, {markdown: '^log-' + 'a'.repeat(32)}])
    assert.throws(() => validateEntry({...input, ...bad}));
});


test('multiple diary images stay ordered and retry does not duplicate them', async () => {
  const second='https://server.tailb7f57c.ts.net/obsidian-images/'+'c'.repeat(64)+'.jpg';
  const data={...input,images:[input.image,second]}, client=new FakeClient();
  await appendDiary(client,data);await appendDiary(client,data);
  const text=client.notes.get('Diary/'+input.date+'.md').text;
  assert.equal((text.match(/!\[Image\]/g)||[]).length,2);assert.ok(text.indexOf(input.image)<text.indexOf(second));
});

test('image arrays enforce count and stored URL authority', () => {
  assert.throws(()=>validateEntry({...input,images:Array(9).fill(input.image)}));
  assert.throws(()=>validateEntry({...input,images:['https://untrusted.invalid/image.jpg']}));
});


test('distinct diary operations create independent entries with selected images', async()=>{
 const client=new FakeClient();
 for(let i=1;i<=3;i++)await appendDiary(client,{...input,id:String(i).repeat(32),title:'Image '+i,images:[`https://server.tailb7f57c.ts.net/obsidian-images/${String(i).repeat(64)}.png`]});
 const text=client.text('Diary/'+input.date+'.md');assert.equal(text.split('^log-').length,4);
 for(let i=1;i<=3;i++)assert.equal(text.split('^log-'+String(i).repeat(32)).length,2);
});
test('past diary date keeps actual recorded timestamp without inventing an event time',async()=>{
 const client=new FakeClient();await appendDiary(client,{...input,date:'2026-10-21',target_date:'2026-10-21'});
 assert.ok(client.text('Diary/2026-10-21.md').includes('2026-10-22 23:59:00'));
});
test('generic note create read append edit preserves content and returns revisions',async()=>{
 const client=new FakeClient();
 await noteOperation(client,{operation:'write',mode:'create',path:'Projects/Test.md',content:'# Draft',id:'1'.repeat(32)});
 const old=await noteOperation(client,{operation:'read',path:'Projects/Test.md'});assert.ok(old.exists);assert.equal(old.revision,textHash(client.text(old.path)));
 await noteOperation(client,{operation:'write',mode:'append',path:old.path,content:'Body',revision:old.revision,id:'2'.repeat(32)});
 const next=await noteOperation(client,{operation:'read',path:old.path});
 await noteOperation(client,{operation:'write',mode:'edit',path:old.path,find:'# Draft',content:'# Final',revision:next.revision,id:'3'.repeat(32)});
 assert.ok(client.text(old.path).startsWith('# Final'));assert.ok(client.text(old.path).includes('Body'));
});
test('generic note lost-response retries never append twice',async()=>{
 const client=new FakeClient();client.seed('Test.md','Original');
 const value={operation:'write',mode:'append',path:'Test.md',content:'Added',revision:textHash('Original'),id:'1'.repeat(32)};
 await noteOperation(client,value);const text=client.text('Test.md'),writes=client.writes;
 assert.equal((await noteOperation(client,value)).status,'already_saved');assert.equal(client.writes,writes);assert.equal(client.text('Test.md'),text);
});
test('generic note never overwrites an unseen concurrent revision',async()=>{
 const client=new FakeClient();client.seed('Test.md','Original');const revision=textHash('Original');
 client.beforePut=async note=>{client.beforePut=null;client.seed(note.path,'External change');};
 await assert.rejects(noteOperation(client,{operation:'write',mode:'replace',path:'Test.md',content:'Proposed',revision,id:'1'.repeat(32)}),/changed/);
 assert.equal(client.text('Test.md'),'External change');
});
test('generic note create does not overwrite and hidden/traversal paths are rejected',async()=>{
 const client=new FakeClient();client.seed('Test.md','Keep');
 await assert.rejects(noteOperation(client,{operation:'write',mode:'create',path:'Test.md',content:'Other',id:'1'.repeat(32)}),/exists/);
 for(const path of ['../Test.md','.obsidian/config.md','/Test.md','Folder/../Test.md','script.js'])await assert.rejects(noteOperation(client,{operation:'read',path}));
 assert.equal(client.text('Test.md'),'Keep');
});
test('generic note edit requires an exact unambiguous match unless all is requested',async()=>{
 const client=new FakeClient();client.seed('Test.md','Word Word');const base={operation:'write',mode:'edit',path:'Test.md',find:'Word',content:'Text',revision:textHash('Word Word'),id:'1'.repeat(32)};
 await assert.rejects(noteOperation(client,base),/more than once/);await noteOperation(client,{...base,replace_all:true});assert.ok(client.text('Test.md').startsWith('Text Text'));
});


test('note discovery scans metadata with continuation and never loads note bodies',async()=>{
 const client=new FakeClient();client.seed('Physics/A.md','Secret body A');client.seed('Physics/B.md','Secret body B');client.seed('.obsidian/Private.md','Hidden');
 let reads=0;client.getByMeta=async()=>{reads++;throw Error('Metadata discovery must not load note text');};
 client._enumerate=async function*(start,end){for(const [id,note] of [...this.notes].sort())if(id>=start&&id<=end)yield structuredClone(note.meta);};
 const first=await noteOperation(client,{operation:'search',query:'physics',limit:1});assert.equal(first.items[0].path,'Physics/A.md');assert.ok(first.next_cursor);
 const next=await noteOperation(client,{operation:'search',query:'physics',limit:1,cursor:first.next_cursor});assert.equal(next.items[0].path,'Physics/B.md');assert.equal(reads,0);
 assert.ok(!JSON.stringify(first).includes('Secret'));
});
