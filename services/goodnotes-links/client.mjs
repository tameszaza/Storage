import {readFile} from 'node:fs/promises';
import {DirectFileManipulator} from '@vrtmrz/livesync-commonlib';
export class CompatibleClient extends DirectFileManipulator {
  get settings(){const s=super.settings;return {...s,idDerivationVersion:this.options.idDerivationVersion??0,idDerivationKey:this.options.idDerivationKey??'',encryptInternalMetadata:this.options.encryptInternalMetadata??false};}
}
export async function connect(){
 const options=JSON.parse(await readFile(process.env.HOME+'/.config/goodnotes-links/livesync.json','utf8'));
 if(options.idDerivationVersion!==0||options.encryptInternalMetadata!==false)throw Error('Unsupported vault format; refusing automatic migration');
 const client=new CompatibleClient(options,{fetch:globalThis.fetch});
 await Promise.race([client.ready.promise,new Promise((_,reject)=>{const t=setTimeout(()=>reject(Error('LiveSync readiness timeout')),30000);t.unref();})]);
 return client;
}
