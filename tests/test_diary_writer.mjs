import {test} from 'node:test';
import assert from 'node:assert/strict';
import {appendDiary, addIndexLink, validateEntry} from '../deploy/obsidian-diary.mjs';

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
