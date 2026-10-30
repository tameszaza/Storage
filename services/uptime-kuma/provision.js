// One-shot/idempotent provisioning using Uptime Kuma's own Socket.IO API.
const fs=require('fs'),{io}=require('socket.io-client');
(async()=>{
const plan=JSON.parse(fs.readFileSync(0,'utf8'));
const socket=io('http://127.0.0.1:3001',{transports:['websocket'],reconnection:false,timeout:10000});
const ready=new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Kuma initialization timed out')),90000);socket.once('info',()=>{clearTimeout(timer);resolve();});});
let monitors={},dockerHosts={};socket.on('monitorList',list=>monitors=list);socket.on('dockerHostList',list=>dockerHosts=list);
const defaults={conditions:[],rabbitmqNodes:[],kafkaProducerBrokers:[],kafkaProducerSaslOptions:{}};
const call=(event,...args)=>new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(event+' timed out')),90000);socket.emit(event,...args,res=>{clearTimeout(timer);res?.ok?resolve(res):reject(Error(event+': '+(res?.msg||'failed')));});});
await new Promise((resolve,reject)=>{socket.once('connect',resolve);socket.once('connect_error',reject);});
await ready;
if(plan.action==='setup')try{await call('setup',plan.credentials.username,plan.credentials.password);}catch(error){if(!error.message.includes('initialized'))throw error;}
await call('login',{username:plan.credentials.username,password:plan.credentials.password,token:''});
if(plan.action==='setup'){
 const settings=(await call('getSettings')).data;
 await call('setSettings',{...settings,disableAuth:false,trustProxy:true,keepDataPeriodDays:30,checkUpdate:false,checkBeta:false,serverTimezone:'Asia/Singapore'},plan.credentials.password);
 console.log(JSON.stringify({account:'ready',login:'verified',retentionDays:30}));
}else if(plan.action==='password'){
 await call('changePassword',{currentPassword:plan.credentials.password,newPassword:plan.newPassword});
 console.log(JSON.stringify({passwordChanged:true}));
}else if(plan.action==='monitors'){
 await new Promise(resolve=>setTimeout(resolve,400));
 let dockerID=plan.dockerID||Object.values(dockerHosts).find(h=>h.dockerDaemon==='http://127.0.0.1:9780')?.id;
 if(!dockerID)dockerID=(await call('addDockerHost',{name:'Server · read only',dockerType:'tcp',dockerDaemon:'http://127.0.0.1:9780'},null)).id;
 const connection=await call('testDockerHost',{dockerType:'tcp',dockerDaemon:'http://127.0.0.1:9780'});
 const groups={};const saved=[];
 for(const item of plan.monitors){
  let group=Object.values(monitors).find(m=>m.name===item.group&&m.type==='group');
  if(!groups[item.group]){
   const response=group?{monitorID:group.id}:await call('add',{...defaults,name:item.group,type:'group',interval:60,retryInterval:60,active:true,maxretries:0,notificationIDList:{},accepted_statuscodes:['200-299']});
   groups[item.group]={id:response.monitorID,name:item.group,monitorList:[]};
  }
  const existing=Object.values(monitors).find(m=>m.name===item.name&&m.type!=='group');
  const config={...item};delete config.group;
  const data={...defaults,interval:60,retryInterval:30,active:true,maxretries:1,resendInterval:0,timeout:8,method:'GET',ignoreTls:false,upsideDown:false,notificationIDList:{},accepted_statuscodes:['200-299'],...config,parent:groups[item.group].id};
  if(data.type==='docker')data.docker_host=dockerID;
  const id=existing?.id||(await call('add',data)).monitorID;
  saved.push({id,name:item.name,group:item.group,active:item.active!==false});groups[item.group].monitorList.push({id,sendUrl:false});
 }
 try{await call('addStatusPage','Services','services');}catch(error){if(!error.message.includes('already')&&!error.message.includes('UNIQUE'))throw error;}
 await call('saveStatusPage','services',{slug:'services',title:'Services',description:'',logo:'',autoRefreshInterval:60,theme:'auto',showTags:false,footerText:'',customCSS:'',showPoweredBy:false,rssTitle:'Services',showOnlyLastHeartbeat:false,showCertificateExpiry:false,analyticsId:'',analyticsType:null,analyticsScriptUrl:'',domainNameList:[]},'',Object.values(groups).map(({name,monitorList})=>({name,monitorList})));
 console.log(JSON.stringify({dockerID,monitors:saved,statusPage:'/status/services'}));
}else if(plan.action==='test-monitor'){
 const existing=Object.values(monitors).find(m=>m.name==='Control verification');
 const response=existing?{monitorID:existing.id}:await call('add',{...defaults,name:'Control verification',type:'docker',docker_container:'tamestorage-control-check',docker_host:plan.dockerID,interval:20,retryInterval:20,maxretries:0,notificationIDList:{},accepted_statuscodes:['200-299'],active:true});
 console.log(JSON.stringify({monitorID:response.monitorID}));
}else if(plan.action==='resume'){
 await call('resumeMonitor',plan.monitorID);console.log(JSON.stringify({resumed:true}));
}else if(plan.action==='delete-test'){
 await call('deleteMonitor',plan.monitorID);console.log(JSON.stringify({testMonitor:'removed'}));
}
socket.disconnect();process.exit(0);
})().catch(error=>{console.error(error.message);process.exit(1);});
