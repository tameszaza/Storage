/** Test-container-only mount. Production never imports this namespace adapter. */
import {CompatibleClient as NativeClient} from '/native-livesync/client.mjs';
export class CompatibleClient extends NativeClient {
 constructor(...args){
  const prefix=process.env.DIARY_TEST_PREFIX;
  if(!/^Integration checks\/diary-[0-9a-f-]+\/$/.test(prefix||''))throw Error('Disposable namespace required');
  super(...args);
  const nativePath=this.path2id.bind(this),database=this.liveSyncLocalDB;
  const allowed=path=>/^Diary\/(?:\d{4}-\d{2}-\d{2})\.md$/.test(path)||path==='Diary.md';
  this.path2id=path=>{if(!allowed(path))throw Error('Unsafe test path');return nativePath(prefix+path)};
  this.liveSyncLocalDB=new Proxy(database,{get(target,key){
   if(key==='putDBEntryWithLiveBaseRevision')return async(note,rev)=>{
    if(!allowed(note.path)||note._id!==await nativePath(prefix+note.path))throw Error('Unsafe fixture write');
    return target.putDBEntryWithLiveBaseRevision({...note,path:prefix+note.path},rev);
   };
   const value=Reflect.get(target,key,target);return typeof value==='function'?value.bind(target):value;
  }});
 }
 put(){throw Error('Fixture permits only revision-checked append');}
 delete(){throw Error('Fixture cannot delete notes');}
}
