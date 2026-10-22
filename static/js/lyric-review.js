(() => {
  'use strict';
  const dialog = document.getElementById('lyricReview');
  if (!dialog) return;
  const song = document.getElementById('lrSong'), audio = document.getElementById('lrAudio');
  const lines = document.getElementById('lrLines'), all = document.getElementById('lrAll');
  const status = document.getElementById('lrStatus'), save = document.getElementById('lrSave');
  let current = null, dirty = false, busy = false, generation = 0, stopAt = null, controller = null;
  const url = id => '/music-sync/lyrics/' + encodeURIComponent(id);
  function message(text, error = false) { status.textContent = text; status.classList.toggle('lr-error', error); }
  async function api(path, options = {}) {
    const response = await fetch(path, {cache: 'no-store', credentials: 'same-origin', signal: controller?.signal, ...options});
    if (response.redirected) throw new Error('Your session expired. Log in again before saving.');
    let body; try { body = await response.json(); } catch (_) { throw new Error('The server could not respond. Please retry.'); }
    if (!response.ok) throw new Error(body.error || 'Could not load lyrics. Please retry.');
    return body;
  }
  function markDirty() { dirty = true; save.disabled = busy || !current; }
  function progress() {
    const uncertain = current.lines.filter(line => line.editable && line.uncertain.length);
    const remaining = uncertain.filter(line => !line.confirmed).length;
    message(uncertain.length ? `${remaining} of ${uncertain.length} uncertain lines left${dirty ? ' · Unsaved changes' : ''}` : `No uncertain lines${dirty ? ' · Unsaved changes' : ''}`);
  }
  function element(tag, text, className) {
    const node = document.createElement(tag); if (text !== undefined) node.textContent = text;
    if (className) node.className = className; return node;
  }
  function render() {
    lines.replaceChildren();
    const editable = current.lines.filter(line => line.editable);
    const visible = editable.filter(line => all.checked || line.uncertain.length);
    if (!visible.length) lines.append(element('p', 'No uncertain lines to check. Enable “Show all lyrics” to edit other lines.', 'lr-empty'));
    visible.forEach(line => {
      const row = element('section', undefined, 'lr-line');
      const top = element('div', undefined, 'lr-line-head');
      const play = element('button', `▶ ${line.time.slice(1,-1)}`, 'btn btn-outline-secondary lr-play');
      play.type = 'button'; play.setAttribute('aria-label', `Listen from ${line.time.slice(1,-1)}`);
      play.addEventListener('click', async () => {
        const index = current.lines.indexOf(line); const next = current.lines.slice(index + 1).find(value => value.start > line.start);
        stopAt = next ? next.start + .3 : null;
        try { audio.currentTime = Math.max(0,line.start - .4); await audio.play(); } catch (_) { message('Press Play in the audio player to listen.', true); }
      });
      top.append(play);
      const checkLabel = element('label', undefined, 'lr-confirm'); const check = element('input');
      check.type = 'checkbox'; check.checked = line.confirmed; check.disabled = busy;
      checkLabel.append(check, document.createTextNode('Confirmed')); top.append(checkLabel);
      const input = element('textarea'); input.rows = 2; input.maxLength = 2000; input.value = line.text;
      input.placeholder = 'Leave blank if no words are sung'; input.disabled = busy; input.setAttribute('aria-label', `Lyrics at ${line.time.slice(1,-1)}`);
      check.addEventListener('change', () => { line.confirmed = check.checked; row.classList.toggle('lr-confirmed',check.checked); markDirty(); progress(); });
      input.addEventListener('input', () => { line.text = input.value; line.confirmed = true; check.checked = true; row.classList.add('lr-confirmed'); markDirty(); progress(); });
      row.classList.toggle('lr-confirmed',line.confirmed); row.append(top,input);
      if (line.uncertain.length) row.append(element('p', 'Check: ' + line.uncertain.join(' · '), 'lr-uncertain'));
      lines.append(row);
    });
  }
  async function waitSave(id, token, turn) {
    // Only an open, actively saving editor polls. Closing aborts requests and releases data.
    for (let attempt = 0; attempt < 30 && dialog.open && turn === generation; attempt++) {
      const state = await api(url(id) + '/status?token=' + encodeURIComponent(token));
      if (state.state === 'error') throw new Error(state.error);
      if (state.state === 'saved') return true;
      message('Saving… Your edits are queued with the music worker.');
      await new Promise(resolve => setTimeout(resolve,2000));
    }
    return false;
  }
  async function load(id) {
    const turn = ++generation; busy = true; save.disabled = true; song.disabled = true;
    audio.pause(); audio.removeAttribute('src'); audio.load(); lines.replaceChildren(); message('Loading lyrics…');
    try {
      const result = await api(url(id)); if (turn !== generation || !dialog.open) return;
      current = result; dirty = false; audio.src = url(id) + '/audio'; stopAt = null;
      all.checked = !result.lines.some(line => line.editable && line.uncertain.length);
      busy = Boolean(result.pending); render(); progress();
      if (result.pending) {
        if (await waitSave(id,result.pending_token,turn)) { await load(id); message('Saved · Lyrics and confirmations updated.'); }
        else if (turn === generation && dialog.open) message('Save queued. Reopen this song after the current music sync finishes.');
      } else if (result.save_error) message(result.save_error,true);
    } catch (error) { if (error.name !== 'AbortError' && turn === generation) { current = null; message(error.message,true); } }
    finally { if (turn === generation) { song.disabled = false; if (!current?.pending) busy = false; } }
  }
  function mayLeave() { return !dirty || window.confirm('Discard your unsaved lyric edits?'); }
  function close() {
    if (!mayLeave()) return;
    ++generation; controller?.abort(); audio.pause(); audio.removeAttribute('src'); audio.load();
    lines.replaceChildren(); song.replaceChildren(); current = null; dirty = false; busy = false; dialog.close();
  }
  document.getElementById('lrClose').addEventListener('click',close);
  dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  audio.addEventListener('timeupdate', () => { if (stopAt !== null && audio.currentTime >= stopAt) { audio.pause(); stopAt = null; } });
  audio.addEventListener('error', () => { if (current && dialog.open) message('Audio is unavailable. Your lyric edits can still be saved.',true); });
  all.addEventListener('change', () => { if (current) render(); });
  song.addEventListener('change', () => {
    if (!mayLeave()) { song.value = current?.id || ''; return; } load(song.value);
  });
  document.querySelectorAll('[data-lyric-review]').forEach(button => button.addEventListener('click',async () => {
    controller?.abort(); controller = new AbortController(); current = null; dirty = false; busy = true;
    save.disabled = true; song.disabled = true; all.disabled = false; dialog.showModal(); lines.replaceChildren(); message('Loading songs…');
    const turn = ++generation;
    try {
      const result = await api('/music-sync/' + encodeURIComponent(button.dataset.lyricReview) + '/lyrics');
      if (turn !== generation || !dialog.open) return;
      song.replaceChildren();
      result.songs.forEach(item => { const option = element('option',item.title + (item.artist ? ' — ' + item.artist : '')); option.value = item.id; song.append(option); });
      if (!result.songs.length) { song.append(element('option','No AI lyrics yet')); message('Cybrog-generated lyrics will appear here when ready.'); busy = false; return; }
      await load(song.value);
    } catch (error) { if (error.name !== 'AbortError') message(error.message,true); }
  }));
  save.addEventListener('click',async () => {
    if (!current || busy || !dirty) return;
    const turn = generation; const id = current.id;
    busy = true; save.disabled = true; song.disabled = true; all.disabled = true; render(); message('Saving…');
    try {
      const result = await api(url(id), {method: 'POST', headers: {'Content-Type':'application/json','X-CSRF-Token':dialog.dataset.csrf}, body:JSON.stringify({revision:current.revision,lines:current.lines.filter(line => line.editable).map(({index,text,confirmed}) => ({index,text,confirmed}))})});
      // Once accepted, closing does not cancel the durable server save.
      dirty = false; current.pending = true;
      if (!await waitSave(id,result.token,turn)) { if (turn === generation && dialog.open) message('Save queued. Reopen this song after the current music sync finishes.'); return; }
      if (turn !== generation || !dialog.open) return;
      await load(id); message('Saved · Lyrics and confirmations updated.');
    } catch (error) { if (error.name !== 'AbortError' && turn === generation) { dirty = true; message(error.message,true); } }
    finally { if (turn === generation && dialog.open) { busy = Boolean(current?.pending); song.disabled = false; all.disabled = false; save.disabled = busy || !dirty; render(); } }
  });
})();
