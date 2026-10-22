(()=>{
  const root=document.querySelector('.duplicates-page');if(!root)return;
  const status=document.getElementById('duplicateStatus');
  const summary=document.getElementById('duplicateSummary');
  const list=document.getElementById('duplicateGroups');
  const pages=document.getElementById('duplicatePages');
  const scanButton=document.getElementById('duplicateScanButton');
  let currentPage=1,timer;
  const el=(tag,className,text)=>{const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node};
  function show(data){
    clearTimeout(timer);list.replaceChildren();summary.replaceChildren();pages.replaceChildren();
    summary.hidden=true;pages.hidden=true;status.classList.remove('is-error');
    if(data.status==='running'){
      status.textContent='Scanning in the background. You can leave this page and return later.';
      timer=setTimeout(()=>load(1),2500);return;
    }
    if(data.status==='error'){
      status.classList.add('is-error');status.textContent=data.message||'The scan could not finish. Try again.';return;
    }
    if(data.status!=='ready'){
      status.textContent='No scan has run yet. Select Scan again to start.';return;
    }
    const date=data.finished_at?new Date(data.finished_at):null;
    status.textContent=date&&!Number.isNaN(date.getTime())?'Last scanned '+date.toLocaleString(): 'Scan complete';
    summary.hidden=false;
    const groups=el('span','');groups.append(el('strong','',String(data.total_groups)),document.createTextNode(' duplicate groups'));
    const bytes=el('span','');bytes.append(el('strong','',data.reclaimable_label),document.createTextNode(' in additional copies'));
    summary.append(groups,bytes);
    if(!data.total_groups){list.append(el('p','duplicate-status','No exact duplicates found.'));return;}
    for(const group of data.groups){
      const section=el('details','duplicate-group');
      const heading=el('summary','duplicate-group-heading');
      const filename=group.files[0]?.path?.split('/').pop()||'File';
      heading.append(el('strong','duplicate-name',filename),el('span','',group.files.length+' copies · '+group.size_label+' each'));
      section.append(heading);
      for(const file of group.files){
        const link=el('a','duplicate-file',file.path);
        link.href='/preview?'+new URLSearchParams({target:file.path});
        section.append(link);
      }
      list.append(section);
    }
    if(data.pages>1){
      pages.hidden=false;
      const prev=el('button','btn btn-outline-secondary','Previous');prev.type='button';prev.disabled=data.page<=1;
      prev.addEventListener('click',()=>load(data.page-1));
      const next=el('button','btn btn-outline-secondary','Next');next.type='button';next.disabled=data.page>=data.pages;
      next.addEventListener('click',()=>load(data.page+1));
      pages.append(prev,el('span','',`Page ${data.page} of ${data.pages}`),next);
    }
  }
  async function load(page){
    currentPage=page;
    try{
      const response=await fetch(root.dataset.statusUrl+'?'+new URLSearchParams({page}),{credentials:'same-origin'});
      if(!response.ok)throw new Error('The results could not load.');
      show(await response.json());
    }catch(error){status.classList.add('is-error');status.textContent=error.message;timer=setTimeout(()=>load(currentPage),5000)}
  }
  scanButton.addEventListener('click',async()=>{
    scanButton.disabled=true;status.textContent='Starting a new scan…';
    try{
      const response=await fetch(root.dataset.scanUrl,{method:'POST',credentials:'same-origin'});
      if(!response.ok)throw new Error('The scan could not start.');
      show(await response.json());
      if(status.textContent.startsWith('Scanning'))load(1);
    }catch(error){status.classList.add('is-error');status.textContent=error.message}
    finally{scanButton.disabled=false}
  });
  load(1);
})();
