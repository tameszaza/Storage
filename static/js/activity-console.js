(() => {
'use strict';
const root=document.getElementById('activityConsole');if(!root)return;
const el=id=>document.getElementById(id),list=el('activityTasks'),rows=new Map();
let snapshot=null,controller=null,timer=null,epoch=0,paused=false,alive=true;
const labels={running:'Running',queued:'Queued',waiting:'Waiting',paused:'Paused',failed:'Needs attention',idle:'Idle',available:'Available',unknown:'Unknown'};
const waiting=new Set(['queued','waiting','paused']);
function node(tag,content,cls){const n=document.createElement(tag);if(content!==undefined)n.textContent=content;if(cls)n.className=cls;return n;}
function set(n,value){const v=String(value??'');if(n.textContent!==v)n.textContent=v;}
function localTime(value){return value?new Date(value*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'}):'—';}
function localDate(value){return value?new Date(value*1000).toLocaleString():'—';}
function connection(state,label){el('activityConnection').dataset.state=state;set(el('activityConnection'),label);}
function message(value,error=false){set(el('activityMessage'),value);el('activityMessage').dataset.error=String(error);}
function matching(row){
 const state=el('activityState').value,host=el('activityHost').value,q=el('activitySearch').value.trim().toLocaleLowerCase();
 if(host!=='all'&&row.host!==host)return false;
 if(q&&!([row.label,row.title,row.stage,row.host,...(row.items||[])].join(' ').toLocaleLowerCase().includes(q)))return false;
 if(state==='all')return true;
 if(state==='active')return !['idle','available'].includes(row.state);
 if(state==='waiting')return waiting.has(row.state);
 if(state==='idle')return ['idle','available'].includes(row.state);
 if(state==='failed')return ['failed','unknown'].includes(row.state);
 return row.state===state;
}
function buildRow(id){
 const card=node('details',undefined,'activity-task');card.dataset.task=id;
 const summary=node('summary'),main=node('div'),heading=node('div',undefined,'activity-task-heading');
 const label=node('strong'),host=node('span',undefined,'activity-task-host'),title=node('p',undefined,'activity-task-title'),stage=node('p',undefined,'activity-task-stage');
 heading.append(label,host);main.append(heading,title,stage);
 const end=node('div',undefined,'activity-task-end'),pill=node('span'),progressBox=node('div',undefined,'activity-task-progress'),progressText=node('span'),progress=node('progress');
 progress.max=100;progress.setAttribute('aria-label','Task progress');progressBox.append(progressText,progress);end.append(pill,progressBox);
 summary.append(main,end);const detail=node('div',undefined,'activity-task-details');card.append(summary,detail);
 return {card,label,host,title,stage,pill,progressBox,progressText,progress,detail,signature:''};
}
function updateRow(row){
 let n=rows.get(row.id);if(!n){n=buildRow(row.id);rows.set(row.id,n);}
 set(n.label,row.label);set(n.host,row.host);set(n.title,row.title?.split('/').at(-1));n.title.title=row.title||'';n.title.hidden=!row.title;set(n.stage,row.stage);
 set(n.pill,labels[row.state]||'Unknown');n.pill.className='activity-pill '+(Object.hasOwn(labels,row.state)?row.state:'unknown');
 let progress=Number.isFinite(row.progress)?row.progress:null;
 let progressText=progress!==null?Math.round(progress)+'%':'';
 if(Number.isFinite(row.done)&&Number.isFinite(row.total)&&row.total>0){progress=100*row.done/row.total;progressText=row.done+' / '+row.total+' pages';}
 n.progressBox.hidden=progress===null;
 if(progress!==null){n.progress.value=Math.max(0,Math.min(100,progress));set(n.progressText,progressText);}
 const details=[...(row.details||[])];if(row.title?.includes('/'))details.unshift(['File',row.title]);
 if(row.queued!==undefined)details.push(['Queued',String(row.queued)]);
 if(row.started)details.push(['Started',localDate(row.started)]);
 if(row.updated)details.push(['Last report',localDate(row.updated)]);
 const signature=JSON.stringify([details,row.items]);
 if(signature!==n.signature){
  n.signature=signature;const dl=node('dl');
  for(const [name,value] of details)dl.append(node('dt',name),node('dd',value));
  if(!details.length)dl.append(node('dt','Status'),node('dd',row.stage));
  const nodes=[dl];if(row.items?.length){const ul=node('ul');for(const name of row.items)ul.append(node('li',name));nodes.push(ul);}
  n.detail.replaceChildren(...nodes);
 }
 return n.card;
}
function hostCards(){
 const container=el('activityHosts');container.replaceChildren();
 for(const host of snapshot.hosts){
  const tasks=snapshot.tasks.filter(row=>row.host===host.id),card=node('section',undefined,'activity-host'),heading=node('div',undefined,'activity-host-heading');
  const state=host.state==='paused'?'Server tasks Off':host.state==='offline'?'Not reachable':host.state==='online'?'Online':'Unknown';
  heading.append(node('strong',host.id),node('small',state));card.append(heading);
  const metrics=node('div',undefined,'activity-host-metrics');
  for(const [label,count] of [['Running',tasks.filter(r=>r.state==='running').length],['Queued',tasks.filter(r=>waiting.has(r.state)&&r.workPending!==false).reduce((n,r)=>n+Math.max(1,r.queued||1),0)],['Attention',tasks.filter(r=>['failed','unknown'].includes(r.state)).length]]){
   const metric=node('div',undefined,'activity-host-metric');metric.append(node('b',count),node('span',label));metrics.append(metric);
  }
  card.append(metrics);container.append(card);
 }
}
function render(){
 if(!snapshot)return;
 const rank={running:0,failed:1,unknown:2,queued:3,waiting:4,paused:5,available:6,idle:7};
 const ordered=snapshot.tasks.filter(matching).sort((a,b)=>(rank[a.state]??8)-(rank[b.state]??8)||a.label.localeCompare(b.label)||String(a.id).localeCompare(String(b.id)));
 const keep=new Set(ordered.map(row=>row.id));
 for(const child of [...list.children])if(!keep.has(child.dataset.task))child.remove();
 ordered.forEach((row,index)=>{const card=updateRow(row);if(list.children[index]!==card)list.insertBefore(card,list.children[index]||null);});
 for(const [id] of rows)if(!snapshot.tasks.some(row=>row.id===id))rows.delete(id);
 el('activityEmpty').hidden=ordered.length>0;
 if(!ordered.length)set(el('activityEmpty'),el('activityState').value==='active'&&!el('activitySearch').value?'Nothing running or waiting. Choose All tasks to see watchers.':'No matching tasks.');
 set(el('activityTaskCount'),ordered.length+' shown');
}
function feed(){
 const log=el('activityEvents'),host=el('activityHost').value;
 const events=snapshot.events.filter(e=>host==='all'||e.host===host);
 const signature=JSON.stringify(events);if(log.dataset.signature===signature)return;log.dataset.signature=signature;
 const nodes=events.map(event=>{const li=node('li',undefined,'activity-event'),time=node('time',localTime(event.at));time.dateTime=new Date(event.at*1000).toISOString();time.title=localDate(event.at);const body=node('div');body.append(node('strong',event.host+' · '+event.label),node('p',event.message));li.append(time,body);return li;});
 if(!nodes.length)nodes.push(node('li','No recent worker reports.','activity-empty'));
 log.replaceChildren(...nodes);set(el('activityEventCount'),events.length);
}
function stop(){clearTimeout(timer);timer=null;++epoch;controller?.abort();controller=null;}
async function refresh(){
 if(!alive||paused||document.hidden||controller)return;
 clearTimeout(timer);const turn=++epoch;controller=new AbortController();const active=controller;el('activityRefresh').disabled=true;
 const deadline=setTimeout(()=>active.abort(),16000);
 try{
  const response=await fetch(root.dataset.url,{credentials:'same-origin',cache:'no-store',signal:active.signal,headers:{Accept:'application/json'}});
  if(response.redirected||response.status===401||response.status===403){paused=true;throw Error('Sign in again to view activity.');}
  const data=await response.json();if(!response.ok||!data.ok||!data.activity)throw Error(data.error||'Activity unavailable. Keeping the last snapshot.');
  if(turn!==epoch)return;
  snapshot=data.activity;hostCards();render();feed();
  set(el('activityUpdated'),'Updated '+localTime(snapshot.generated));
  connection(snapshot.stale?'stale':'live',snapshot.stale?'Last snapshot':'Live');
  message(snapshot.unavailable?.length?'Some sources unavailable: '+snapshot.unavailable.join(', '):'');
 }catch(error){
  if(turn!==epoch)return;
  connection('stale',snapshot?'Last snapshot':'Disconnected');message(error.name==='AbortError'?'Refresh timed out. Keeping the last snapshot.':error.message,true);
 }finally{
  clearTimeout(deadline);
  if(turn===epoch){controller=null;el('activityRefresh').disabled=false;if(alive&&!paused&&!document.hidden)timer=setTimeout(refresh,5000);}
 }
}
el('activitySearch').addEventListener('input',render);el('activityState').addEventListener('change',render);el('activityHost').addEventListener('change',()=>{render();if(snapshot)feed();});
el('activityRefresh').addEventListener('click',()=>{if(!paused){stop();refresh();}});
el('activityPause').addEventListener('click',()=>{paused=!paused;stop();el('activityPause').setAttribute('aria-pressed',String(paused));set(el('activityPause').querySelector('span'),paused?'Resume':'Pause');el('activityRefresh').disabled=paused;connection(paused?'paused':'live',paused?'Paused':'Connecting');if(!paused)refresh();});
document.addEventListener('visibilitychange',()=>{stop();el('activityRefresh').disabled=paused;if(!document.hidden)refresh();else connection('paused','Hidden tab');});
window.addEventListener('pagehide',()=>{alive=false;stop();});window.addEventListener('pageshow',()=>{if(!alive){alive=true;refresh();}});
refresh();
})();
