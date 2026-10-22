// Add one monitor using Kuma's native API, preserving live monitors and page groups.
const fs=require('fs'),http=require('http'),{io}=require('socket.io-client');
(async()=>{
 const credentials=JSON.parse(fs.readFileSync(0,'utf8'));
 const socket=io('http://127.0.0.1:3001',{transports:['websocket'],reconnection:false,timeout:10000});
 let monitors={};socket.on('monitorList',list=>monitors=list);
 const ready=new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Kuma not ready')),30000);socket.once('info',()=>{clearTimeout(timer);resolve();});});
 const call=(event,...args)=>new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(event+' timed out')),30000);socket.emit(event,...args,res=>{clearTimeout(timer);res?.ok?resolve(res):reject(Error(event+' failed'));});});
 await ready;
 await call('login',{username:credentials.username,password:credentials.password,token:''});
 await new Promise(resolve=>setTimeout(resolve,500));
 const name='Digital circuit simulator · on demand';
 const parent=Object.values(monitors).find(m=>m.type==='group'&&m.name==='Cyborg');
 if(!parent)throw Error('Existing Cyborg group missing');
 const existing=Object.values(monitors).find(m=>m.name===name);
 const config={name,type:'http',url:'http://127.0.0.1:9780/cyborg/digital',parent:parent.id,interval:60,retryInterval:30,timeout:12,maxretries:1,active:true,method:'GET',accepted_statuscodes:['200-299'],notificationIDList:{},conditions:[],rabbitmqNodes:[],kafkaProducerBrokers:[],kafkaProducerSaslOptions:{}};
 const id=existing?.id||(await call('add',config)).monitorID;
 const page=await call('getStatusPage','services');
 const publicPage=await new Promise((resolve,reject)=>http.get('http://127.0.0.1:3001/api/status-page/services',res=>{let raw='';res.on('data',chunk=>raw+=chunk);res.on('end',()=>{try{resolve(JSON.parse(raw));}catch(error){reject(error);}});}).on('error',reject));
 const groups=publicPage.publicGroupList;
 const group=groups.find(g=>g.name==='Cyborg');
 if(!group)throw Error('Existing public Cyborg group missing');
 if(!group.monitorList.some(m=>m.id===id))group.monitorList.push({id,sendUrl:false});
 await call('saveStatusPage','services',page.config,page.config.logo||'',groups);
 console.log(JSON.stringify({id,name,totalMonitors:Object.values(monitors).filter(m=>m.type!=='group').length,statusPage:'/status/services'}));
 socket.disconnect();process.exit(0);
})().catch(()=>{console.error('Kuma Digital provisioning failed; no credential output');process.exit(1);});
