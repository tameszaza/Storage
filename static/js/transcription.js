(() => {
  'use strict';
  const app = document.getElementById('transcriptionApp');
  if (!app) return;
  const root = app.dataset.root, jobsURL = app.dataset.jobsUrl, csrf = app.dataset.csrf;
  const list = document.getElementById('transcriptionJobs'), error = document.getElementById('jobError');
  const form = document.getElementById('transcriptionForm'), status = document.getElementById('uploadStatus');
  const input=document.getElementById('recording'),drop=document.getElementById('recordingDrop'),start=document.getElementById('startTranscription');
  let timer = null, busy = false, uploading = false;
  async function request(url, options = {}) {
    const response = await fetch(url, {cache:'no-store', credentials:'same-origin', ...options, headers:{'X-CSRF-Token':csrf,...options.headers}});
    if (response.redirected) { window.location.assign(response.url); throw new Error('Please log in again.'); }
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'The server could not complete this request.');
    return data;
  }
  function element(tag, text, cls) { const e=document.createElement(tag); if(text)e.textContent=text; if(cls)e.className=cls; return e; }
  function icon(name){const e=element('i',null,'fa-solid fa-'+name);e.setAttribute('aria-hidden','true');return e;}
  function selected(){
    const file=input.files[0];start.disabled=uploading||!file;
    document.getElementById('recordingName').textContent=file?file.name:'Drop a recording';
    document.getElementById('recordingHint').textContent=file?(file.size/1024**2).toFixed(1)+' MB':'or choose a file';
  }
  function defaults(){document.getElementById('recordingDefaults').textContent=(document.getElementById('recordingLanguage').value==='en'?'English':'Auto')+(document.getElementById('speakerLabels').checked?' · Speakers':'');}
  input.addEventListener('change',selected);
  drop.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();input.click();}});
  for(const type of ['dragenter','dragover'])drop.addEventListener(type,e=>{e.preventDefault();drop.classList.add('is-dragging');});
  for(const type of ['dragleave','drop'])drop.addEventListener(type,e=>{e.preventDefault();drop.classList.remove('is-dragging');});
  drop.addEventListener('drop',e=>{if(uploading||!e.dataTransfer.files.length)return;const files=new DataTransfer();files.items.add(e.dataTransfer.files[0]);input.files=files.files;selected();});
  for(const id of ['recordingLanguage','speakerLabels'])document.getElementById(id).addEventListener('change',defaults);
  document.addEventListener('click',e=>{for(const detail of app.querySelectorAll('details[open]'))if(!detail.contains(e.target))detail.open=false;});
  function render(jobs) {
    list.replaceChildren();
    document.getElementById('transcriptionCount').textContent=jobs.length;
    if (!jobs.length){const empty=element('div',null,'transcription-empty');empty.append(icon('wave-square'),element('span','No recordings'));list.append(empty);}
    for (const job of jobs) {
      const card=element('article',null,'transcription-job'),copy=element('div');
      const symbol=element('span',null,'transcription-job-symbol');symbol.append(icon('file-audio'));card.append(symbol);
      let stage={uploading:job.stage==='Checking inbox recording'?'Checking recording':'Uploading',queued:job.stage==='Server tasks off'?'Paused':job.stage==='Waiting for GPU recovery'?'Waiting for GPU':'Queued',completed:job.stage==='Likely instrumental'?'Likely instrumental':'Ready',failed:'Needs attention',cancelled:'Cancelled'}[job.state]||'Transcribing';
      if(job.state==='running'){
        if(!job.connected)stage='Waiting for Cyborg';
        else if(/cooling/i.test(job.stage))stage='Cooling GPU';
        else if(/waiting/i.test(job.stage))stage='Waiting for GPU';
        else if(/speaker/i.test(job.stage))stage='Speaker labels';
        else if(/compar|merge/i.test(job.stage))stage='Cross-checking';
        else if(/sav/i.test(job.stage))stage='Saving';
        stage+=' · '+Math.round(job.progress)+'%';
      }
      copy.append(element('h3',job.title),element('p',stage,'transcription-job-meta'));
      if(job.state==='running'){const progress=element('progress');progress.max=100;progress.value=job.progress;progress.setAttribute('aria-label','Transcription progress');copy.append(progress);}
      if(job.message){const detail=element('details',null,'transcription-job-details');detail.append(element('summary','Details'),element('p',job.message));copy.append(detail);}
      const actions=element('div',null,'transcription-job-actions');
      const menu=element('details',null,'transcription-job-menu'),summary=element('summary'),items=element('div');summary.setAttribute('aria-label','More actions for '+job.title);summary.append(icon('ellipsis'));menu.append(summary,items);
      if (job.state==='completed') {
        const link=element('a','Download','btn btn-sm btn-outline-secondary');link.prepend(icon('arrow-down'));
        link.href=root+'/jobs/'+job.id+'/download/transcript';actions.append(link);
        const review=element('a','Review alternatives');review.href=root+'/jobs/'+job.id+'/download/review';items.append(review);
      }
      const allowed=['queued','running','uploading'].includes(job.state)?[['cancel','Cancel']]:[];
      if (['failed','cancelled'].includes(job.state)) allowed.push(['retry','Retry']);
      if (['completed','failed','cancelled','uploading'].includes(job.state)) allowed.push(['remove','Remove entry']);
      for(const [action,label] of allowed) {
        const button=element('button',label);button.type='button';
        button.addEventListener('click',async()=>{
          if(action==='remove'&&!confirm('Remove entry and temporary audio? Saved transcripts stay.'))return;
          button.disabled=true;
          try { await request(root+'/jobs/'+job.id+'/'+action,{method:'POST'}); await refresh(); }
          catch(e){error.textContent=e.message;error.hidden=false;button.disabled=false;}
        }); items.append(button);
      }
      actions.append(menu);card.append(copy,actions);list.append(card);
    }
    clearTimeout(timer);timer=null;
    if(!document.hidden&&jobs.some(j=>['queued','running','uploading'].includes(j.state)))timer=setTimeout(refresh,8000);
  }
  async function refresh() {
    if(busy||document.hidden)return;busy=true;
    try {const data=await request(jobsURL);render(data.jobs);error.textContent='';error.hidden=true;}
    catch(e){error.textContent=e.message;error.hidden=false;clearTimeout(timer);timer=setTimeout(refresh,30000);}
    finally{busy=false;}
  }
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(uploading)return;
    const file=input.files[0];if(!file)return;
    status.hidden=false;
    if(file.size>5*1024**3){status.textContent='Choose a recording smaller than 5 GB.';return;}
    uploading=true;start.disabled=true;input.disabled=true;document.getElementById('transcriptionOptions').open=false;
    const progress=document.getElementById('uploadProgress');progress.hidden=false;progress.value=0;
    status.textContent='Preparing…';
    try {
      const data=await request(jobsURL,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename:file.name,size:file.size,title:document.getElementById('recordingTitle').value,options:{language:document.getElementById('recordingLanguage').value,vocabulary:document.getElementById('recordingVocabulary').value,llm:document.getElementById('reconcile').checked,speakers:document.getElementById('speakerLabels').checked}})});
      await new Promise((resolve,reject)=>{
        const xhr=new XMLHttpRequest();xhr.open('PUT',data.upload_url);xhr.setRequestHeader('X-CSRF-Token',csrf);xhr.setRequestHeader('Content-Type','application/octet-stream');
        xhr.upload.onprogress=e=>{if(e.lengthComputable){progress.value=e.loaded/e.total*100;status.textContent='Uploading… '+Math.round(progress.value)+'%';}};
        xhr.onload=()=>{if(xhr.status>=200&&xhr.status<300)resolve();else{let message='Upload failed. The unfinished entry can be removed below.';try{message=JSON.parse(xhr.responseText).error||message;}catch(_){}reject(new Error(message));}};
        xhr.onerror=()=>reject(new Error('Upload interrupted. Please select the recording again.'));
        xhr.send(file);
      });
      status.textContent='Queued.';form.reset();defaults();
    }catch(e){status.textContent=e.message;}
    finally{uploading=false;input.disabled=false;selected();progress.hidden=true;await refresh();}
  });
  document.getElementById('refreshTranscriptions').addEventListener('click',refresh);
  document.addEventListener('visibilitychange',()=>{clearTimeout(timer);timer=null;if(!document.hidden)refresh();});
  window.addEventListener('pagehide',()=>clearTimeout(timer));
  refresh();
})();
