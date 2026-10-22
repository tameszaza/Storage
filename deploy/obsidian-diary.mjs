/** Append-only diary writes using the existing LiveSync codec and revision CAS. */
import {readFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';

const MAX_NOTE = 1024 * 1024;
export function validateEntry(input) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(input.date || '') ||
      new Date(input.date + 'T00:00:00Z').toISOString().slice(0, 10) !== input.date)
    throw Error('Invalid diary date');
  if (!/^[0-9a-f]{32}$/.test(input.id || '')) throw Error('Invalid entry ID');
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$/.test(input.timestamp || '') ||
      input.timestamp.slice(0, 10) !== input.date) throw Error('Invalid timestamp');
  if (typeof input.markdown !== 'string' || !input.markdown.trim() || input.markdown.length > 16000 ||
      /\^log-[0-9a-f]{32}\b/.test(input.markdown)) throw Error('Invalid entry text');
  if (input.image && !/^https:\/\/server\.tailb7f57c\.ts\.net\/obsidian-images\/[0-9a-f]{64}\.(png|jpg|gif|webp|avif)$/.test(input.image))
    throw Error('Invalid stored image');
  return {...input, title: String(input.title || 'Log').replace(/[\r\n#\[\]<>`]/g, ' ').trim().slice(0, 120) || 'Log'};
}

export function entryBlock(input) {
  const image = input.image ? `![Image](${input.image})\n\n` : '';
  return `## ${input.timestamp.slice(11, 19)} — ${input.title}\n\n${image}${input.markdown.trim()}\n^log-${input.id}\n`;
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

async function main() {
  let source = '';
  for await (const part of process.stdin) {
    source += part;
    if (Buffer.byteLength(source) > 100000) throw Error('Request too large');
  }
  const input = validateEntry(JSON.parse(source));
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
    const result = await appendDiary(client, input);
    console.log('TAMES_DIARY_RESULT=' + JSON.stringify(result));
  } finally { await client.close(); }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().then(() => process.exit(0)).catch(() => {
    console.error('Diary append could not finish safely. Retry the same request; existing entries are preserved.');
    process.exit(1);
  });
}
