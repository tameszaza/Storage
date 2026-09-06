document.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll('.sidebar-link.active').forEach(a=>a.setAttribute('aria-current','page'));
  document.querySelectorAll('time[data-local-time]').forEach(el=>{const d=new Date(el.dateTime);if(!Number.isNaN(d.valueOf())){el.title=d.toLocaleString();el.textContent=d.toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});}});
  document.querySelectorAll('.chat-options').forEach(details=>{document.addEventListener('keydown',e=>{if(e.key==='Escape'&&details.open){details.open=false;details.querySelector('summary').focus();}});document.addEventListener('click',e=>{if(!details.contains(e.target)&&!e.target.closest('.requires-file'))details.open=false;});});
  document.querySelectorAll('form.quota-form input').forEach(input=>{input.setAttribute('aria-label','Storage quota in GB; leave empty for unlimited');input.inputMode='decimal';});
});
