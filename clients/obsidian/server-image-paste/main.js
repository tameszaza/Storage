const {Plugin,Notice,Menu,PluginSettingTab,Setting,requestUrl}=require('obsidian');
const imageExtension=/\.(png|jpe?g|gif|webp|avif|bmp|tiff?|svg|heic|heif|hif|ico|icns|jp2|jpx|psd|tga|ppm|qoi|pcx|sgi|mpo)$/i;
const imageMime={png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',gif:'image/gif',webp:'image/webp',avif:'image/avif',bmp:'image/bmp',tif:'image/tiff',tiff:'image/tiff',svg:'image/svg+xml',heic:'image/heic',heif:'image/heif',hif:'image/heif',ico:'image/x-icon',icns:'image/icns',jp2:'image/jp2',jpx:'image/jpx',psd:'image/vnd.adobe.photoshop',tga:'image/x-tga',ppm:'image/x-portable-pixmap',qoi:'image/qoi',pcx:'image/x-pcx',sgi:'image/sgi',mpo:'image/jpeg'};
const ocrBlocks=()=>/(?:\n\n)?<!-- tames-image-ocr:([a-f0-9]{64}\.[a-z]+)\n[\s\S]*?\n-->/g;
const imageMarkdown=()=>/!\[((?:\\.|[^\]\\\n])*)\]\((<?https?:\/\/[^)\s>]+>?)(?:[ \t]+("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'))?[ \t]*\)/g;
const OCR_LABEL='OCR (machine): ';
function imageName(url,origin){try{const u=new URL(url.replace(/^<|>$/g,''));return u.origin===origin?u.pathname.match(/^\/obsidian-images\/([a-f0-9]{64}\.(?:png|jpg|jpeg|gif|webp|avif))$/)?.[1]:null;}catch{return null;}}
function mapImageFields(text,transform){
  // Keep code examples intact; update annotation note strings as JSON, never raw literals.
  let out='',at=0;const annotation=text.includes('lpa-annotations:');
  const fences=/(^ {0,3}(`{3,}|~{3,})[^\n]*\n)([\s\S]*?)(^ {0,3}\2[ \t]*(?:\n|$))/gm;
  for(const m of text.matchAll(fences)){
    out+=transform(text.slice(at,m.index));let block=m[0];
    if(annotation&&/^ {0,3}`{3,}json\s*\n/.test(m[1])){
      try{const data=JSON.parse(m[3]);let changed=false;
        for(const h of data.highlights||[])if(typeof h.note==='string'){const next=transform(h.note);if(next!==h.note){h.note=next;changed=true;}}
        if(changed)block=m[1]+JSON.stringify(data,null,2)+'\n'+m[4];
      }catch{} // Preserve malformed or unrelated JSON verbatim.
    }
    out+=block;at=m.index+m[0].length;
  }
  return out+transform(text.slice(at));
}
function imageNames(text,origin,missingOnly=false){
  const names=new Set();
  mapImageFields(text.replace(ocrBlocks(),''),raw=>raw.replace(imageMarkdown(),(all,alt,url)=>{const name=imageName(url,origin);if(name&&(!missingOnly||!alt.includes(OCR_LABEL)))names.add(name);return all;}));
  return names;
}
function withOcrMetadata(text,images,origin){
  const rows=new Map(images.map(r=>[r.name,r]));
  // Migrate only our own prior comments, without recognizing the image again.
  for(const m of text.matchAll(ocrBlocks()))if(!rows.has(m[1])){
    const body=m[0].replace(/^[\s\S]*?\nMachine OCR[^\n]*\n/,'').replace(/\n-->$/,'').replace(/&lt;!--/g,'<!--').replace(/--&gt;/g,'-->');
    rows.set(m[1],{name:m[1],status:body.trim()==='No readable text found.'?'no-text':'ready',text:body});
  }
  const raw=text.replace(ocrBlocks(),'');
  return mapImageFields(raw,part=>part.replace(imageMarkdown(),(all,alt,url,title)=>{
    const row=rows.get(imageName(url,origin));
    if(!row||!['ready','forced','no-text','skipped'].includes(row.status)){
      if(!alt.includes(OCR_LABEL))return all;
      const at=alt.indexOf(OCR_LABEL)+OCR_LABEL.length;
      const fixed=alt.slice(0,at)+alt.slice(at).replace(/\\\[/g,'&#91;').replace(/\\\]/g,'&#93;');
      return fixed===alt?all:all.slice(0,2)+fixed+all.slice(2+alt.length);
    }
    // Replace only our generated suffix. Keep the manual label, width, URL and title.
    const width=alt.match(/\|\d+(?:x\d+)?$/)?.[0]||'';
    let label=width?alt.slice(0,-width.length):alt;
    if(label.includes(OCR_LABEL))label=label.slice(0,label.indexOf(OCR_LABEL)).replace(/ · $/,'');
    const recognized=['ready','forced'].includes(row.status)?String(row.text||''):'';
    const safe=recognized.replace(/\s+/g,' ').trim().replace(/\\/g,'\\\\').replace(/\[/g,'&#91;').replace(/\]/g,'&#93;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\|/g,'&#124;');
    return '!['+label+(safe?(label?' · ':'')+OCR_LABEL+safe:'')+width+']('+url+(title?' '+title:'')+')';
  }));
}

const digest=async data=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',data))).map(x=>x.toString(16).padStart(2,'0')).join('');
const isImage=file=>file&&(file.type.startsWith('image/')||imageExtension.test(file.name||''));
function clipboardImages(event){
  const items=Array.from(event.clipboardData?.items||[]);
  const images=items.filter(x=>x.kind==='file').map(x=>x.getAsFile()).filter(isImage);
  return images.length?images:Array.from(event.clipboardData?.files||[]).filter(isImage);
}
module.exports=class ServerImagePaste extends Plugin{
  async onload(){
    this.settings=Object.assign({endpoint:'https://server.tailb7f57c.ts.net/obsidian-images/upload',uploadKey:''},await this.loadData());
    this.registerEvent(this.app.workspace.on('editor-paste',(event,editor,info)=>this.handlePaste(event,editor,info)));
    this.addCommand({id:'paste-without-formatting',name:'Paste without formatting',hotkeys:[{modifiers:['Mod','Shift'],key:'V'}],editorCallback:editor=>this.pastePlainText(editor)});
    this.addSettingTab(new ImageSettings(this.app,this));
    this.registerObsidianProtocolHandler('tames-diary',params=>this.openDiary(params));
    this.ocrQueue=new Map();this.ocrKnown=new Map();this.ocrKnownChars=0;this.ocrStopped=false;
    this.registerEvent(this.app.vault.on('modify',file=>{if(!this.ocrWriting?.has(file.path))this.queueOcr(file);}));
    this.registerEvent(this.app.workspace.on('file-open',file=>this.queueOcr(file)));
    this.app.workspace.onLayoutReady(()=>this.queueOcr(this.app.workspace.getActiveFile()));
    this.addCommand({id:'image-ocr',name:'Add searchable OCR to pasted images',callback:()=>this.backfillOcr()});
    this.registerImageOcrMenu(document);
    this.registerEvent(this.app.workspace.on('window-open',(_,win)=>this.registerImageOcrMenu(win.document)));
    this.registerEvent(this.app.workspace.on('url-menu',(menu,url)=>this.ocrMenu(menu,imageName(url,new URL(this.settings.endpoint).origin))));
    this.registerEvent(this.app.workspace.on('editor-menu',(menu,editor)=>this.ocrMenu(menu,this.cursorImage(editor))));
    for(const action of ['skip','retry'])this.addCommand({id:action+'-image-ocr',name:(action==='skip'?'Skip OCR':'Retry OCR (keep low-confidence text)')+' for image at cursor',editorCheckCallback:(checking,editor)=>{const name=this.cursorImage(editor);if(!name)return false;if(!checking)this.changeImageOcr(name,action);return true;}});
  }
  onunload(){this.ocrStopped=true;this.diaryCancel?.();clearTimeout(this.ocrTimer);this.ocrQueue?.clear();this.ocrKnown?.clear();}
  async pastePlainText(editor){
    const file=this.app.workspace.activeEditor?.file,selections=JSON.stringify(editor.listSelections());
    try{
      const text=await navigator.clipboard.readText();
      // Clipboard reads are asynchronous. Never insert into a changed selection or another note.
      if(this.ocrStopped||this.app.workspace.activeEditor?.file!==file||this.app.workspace.activeEditor?.editor!==editor||JSON.stringify(editor.listSelections())!==selections){
        new Notice('Selection changed. Paste again at the intended position.');return;
      }
      if(text)editor.replaceSelection(text.replace(/[\u00a0\u202f]/g,' '));
    }catch{new Notice('Could not read clipboard text. Copy the text and try again.');}
  }
  async openDiary(params){
    const date=String(params.date||'');
    if(!/^\d{4}-\d{2}-\d{2}$/.test(date)||!Number.isFinite(Date.parse(date+'T00:00:00Z'))||new Date(date+'T00:00:00Z').toISOString().slice(0,10)!==date)return;
    if(params.vault&&params.vault!==this.app.vault.getName()){new Notice('Open the '+params.vault+' vault to receive this diary.');return;}
    if(this.diaryOpening)return this.diaryOpening;
    this.diaryOpening=(async()=>{
      let timer,ref;
      try{
        await new Promise(resolve=>this.app.workspace.onLayoutReady(resolve));
        if(this.ocrStopped)return;
        const command=this.app.commands?.commands?.['obsidian-livesync:livesync-replicate'];
        if(typeof command?.callback!=='function')throw Error('Enable Self-hosted LiveSync to receive your saved diary.');
        const path='Diary/'+date+'.md';
        let arrived;
        const created=new Promise(resolve=>{arrived=resolve;});
        ref=this.app.vault.on('create',file=>{if(file.path===path)arrived(file);});
        const stopped=new Promise((_,reject)=>{this.diaryCancel=()=>reject(Error('Stopped'));timer=setTimeout(()=>reject(Error('Sync took too long. The entry is saved on the server; try Open diary again.')),60000);});
        await Promise.race([command.callback(),stopped]);
        const file=this.app.vault.getAbstractFileByPath(path)||await Promise.race([created,stopped]);
        if(!this.ocrStopped&&file?.extension==='md')await this.app.workspace.getLeaf(false).openFile(file);
      }catch(error){if(!this.ocrStopped)new Notice(error.message||'Could not receive the diary. Retry after LiveSync reconnects.');}
      finally{clearTimeout(timer);if(ref)this.app.vault.offref(ref);this.diaryCancel=null;}
    })();
    try{await this.diaryOpening;}finally{this.diaryOpening=null;}
  }
  queueOcr(file,attempt=0,delay=1000){
    if(this.ocrStopped||file?.extension!=='md'||file.path.endsWith('.previous.md')||file.stat?.size>2000000)return;
    this.ocrQueue.set(file.path,{file,attempt});
    if(!this.ocrTimer&&!this.ocrRunning)this.ocrTimer=setTimeout(()=>{this.ocrTimer=null;this.drainOcr();},delay);
  }
  async drainOcr(){
    if(this.ocrRunning||this.ocrStopped)return;this.ocrRunning=true;
    const jobs=[...this.ocrQueue.values()];this.ocrQueue.clear();
    try{for(const {file,attempt} of jobs){
      if(this.ocrStopped)break;
      try{const result=await this.addOcrComments(file);if(result.pending&&attempt<3)this.ocrQueue.set(file.path,{file,attempt:attempt+1});}catch{} // Offline: keep the image; retry on the next note-open/save or manual command.
    }}finally{this.ocrRunning=false;if(!this.ocrStopped&&this.ocrQueue.size&&!this.ocrTimer)this.ocrTimer=setTimeout(()=>{this.ocrTimer=null;this.drainOcr();},5000);}
  }
  registerImageOcrMenu(doc){
    this.registerDomEvent(doc,'contextmenu',event=>{
      const image=event.target?.closest?.('img'),name=image&&imageName(image.getAttribute('src')||'',new URL(this.settings.endpoint).origin);
      // Join the native image menu rather than replacing its existing actions.
      if(name&&typeof Menu.forEvent==='function')this.ocrMenu(Menu.forEvent(event),name);
    });
  }
  rememberOcr(row){
    this.ocrKnownChars-=(this.ocrKnown.get(row.name)?.text||'').length;
    this.ocrKnown.set(row.name,row);this.ocrKnownChars+=(row.text||'').length;
    // Bounded session-only results, not another persistent index or image cache.
    while(this.ocrKnown.size>128||this.ocrKnownChars>128000){const name=this.ocrKnown.keys().next().value;this.ocrKnownChars-=(this.ocrKnown.get(name).text||'').length;this.ocrKnown.delete(name);}
  }
  cursorImage(editor){
    const cursor=editor.getCursor(),line=editor.getLine(cursor.line),origin=new URL(this.settings.endpoint).origin;
    for(const match of line.matchAll(imageMarkdown()))if(cursor.ch>=match.index&&cursor.ch<=match.index+match[0].length)return imageName(match[2],origin);
    return null;
  }
  ocrMenu(menu,name){
    if(!name||menu.__tamesOcr)return;menu.__tamesOcr=true;
    menu.addItem(item=>item.setTitle('Skip image OCR').setIcon('scan-line').onClick(()=>this.changeImageOcr(name,'skip')));
    menu.addItem(item=>item.setTitle('Retry OCR (keep low-confidence text)').setIcon('rotate-cw').onClick(()=>this.changeImageOcr(name,'retry')));
  }
  async changeImageOcr(name,action){
    try{
      const row=(await this.imageOcr([name],false,action))[0];
      if(!row||row.status==='busy')throw Error('OCR is busy. Try this action again shortly.');
      if(row.status==='missing')throw Error('Image unavailable; OCR was not changed.');
      this.ocrKnownChars-=(this.ocrKnown.get(name)?.text||'').length;this.ocrKnown.delete(name);
      // Apply to every existing reference, not to unrelated prose or image bytes.
      const origin=new URL(this.settings.endpoint).origin;
      for(const file of this.app.vault.getMarkdownFiles()){
        if(this.ocrStopped||file.path.endsWith('.previous.md'))continue;
        const text=await this.app.vault.cachedRead(file);if(!text.includes(name)||!imageNames(text,origin).has(name))continue;
        if(action==='skip'){
          (this.ocrWriting??=new Set()).add(file.path);
          try{await this.app.vault.process(file,current=>withOcrMetadata(current,[row],origin));}finally{this.ocrWriting.delete(file.path);}
        }else this.queueOcr(file,0,100);
      }
      if(action==='skip')this.rememberOcr(row);
      new Notice(action==='skip'?'Image OCR skipped.':'OCR retry queued; low-confidence text will be kept.');
    }catch(error){new Notice(error.message||'Could not change image OCR.');}
  }
  async imageOcr(names,recognize=false,action){
    const url=new URL(this.settings.endpoint).origin+'/semantic-search/image-text';let timer;
    try{
      const response=await Promise.race([requestUrl({url,method:'POST',contentType:'application/json',body:JSON.stringify({names,recognize,...(action?{action}:{})}),throw:false}),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Image OCR timed out; the image is safe.')),45000);})]);
      if(response.status!==200)throw Error('Image OCR unavailable ('+response.status+').');
      if(this.ocrStopped)throw Error('Stopped.');
      return response.json.images||[];
    }finally{clearTimeout(timer);}
  }
  async addOcrComments(file){
    if(this.ocrStopped||!this.app.vault.getAbstractFileByPath(file.path)||file.stat?.size>2000000)return {updated:0,pending:false};
    const text=await this.app.vault.cachedRead(file),origin=new URL(this.settings.endpoint).origin;
    if(!text.includes('/obsidian-images/')&&!text.includes('<!-- tames-image-ocr:'))return {updated:0,pending:false};
    const migrated=withOcrMetadata(text,[],origin);
    const allNames=[...imageNames(migrated,origin)],names=allNames.filter(name=>!this.ocrKnown.has(name));let images=allNames.map(name=>this.ocrKnown.get(name)).filter(Boolean),pending=false;
    for(let start=0;start<names.length;start+=8){
      let batch=await this.imageOcr(names.slice(start,start+8));
      for(let image of batch){
        if(this.ocrStopped)return {updated:0,pending:false};
        if(['pending','pending-force','error'].includes(image.status))image=(await this.imageOcr([image.name],true))[0]||image;
        pending ||= ['pending','pending-force'].includes(image.status);if(['ready','forced','no-text','skipped'].includes(image.status))this.rememberOcr(image);images.push(image);
      }
    }
    if(this.ocrStopped||!this.app.vault.getAbstractFileByPath(file.path))return {updated:0,pending:false};
    let updated=0;
    if(withOcrMetadata(text,images,origin)!==text){
      (this.ocrWriting??=new Set()).add(file.path);
      try{await this.app.vault.process(file,current=>{if(this.ocrStopped)return current;const next=withOcrMetadata(current,images,origin);updated=Number(next!==current);return next;});}
      finally{this.ocrWriting.delete(file.path);}
    }
    return {updated,pending};
  }
  async backfillOcr(){
    if(this.ocrRunning||this.ocrStopped){new Notice('Image OCR is already working.');return;}
    this.ocrRunning=true;this.ocrKnown.clear();this.ocrKnownChars=0;let updated=0,failed=0;
    new Notice('Adding searchable image metadata…');
    try{for(const file of this.app.vault.getMarkdownFiles()){
      if(this.ocrStopped)break;if(file.path.endsWith('.previous.md'))continue;
      try{const result=await this.addOcrComments(file);updated+=result.updated;if(result.pending)this.ocrQueue.set(file.path,{file,attempt:0});}catch{failed++;}
    }}finally{this.ocrRunning=false;if(!this.ocrStopped){new Notice('Image OCR: '+updated+' note(s) updated.'+(failed?' '+failed+' could not reach OCR; retry later.':''));if(this.ocrQueue.size&&!this.ocrTimer)this.ocrTimer=setTimeout(()=>{this.ocrTimer=null;this.drainOcr();},5000);}}
    return {updated,failed};
  }
  clipboardImages(event){return clipboardImages(event);}
  async uploadImage(image){
    if(!this.settings.uploadKey)throw new Error('Configure Server Image Paste on this device first.');
    if(image.size>20*1024*1024)throw new Error('Image exceeds the 20 MB upload limit.');
    const bytes=await image.arrayBuffer();
    const extension=(image.name||'').split('.').pop().toLowerCase();
    const response=await requestUrl({url:this.settings.endpoint,method:'POST',contentType:image.type||imageMime[extension]||'application/octet-stream',headers:{Authorization:'Bearer '+this.settings.uploadKey},body:bytes,throw:false});
    if(response.status!==200)throw new Error(response.status===415?'The server could not read this image. Try exporting it as PNG or JPEG.':response.status===413?'Image exceeds the 20 MB limit after conversion.':'Image upload failed ('+response.status+'). Check Tailscale and the server.');
    const result=response.json;
    const expected=new URL(this.settings.endpoint),url=new URL(result.url);
    if(url.origin!==expected.origin||!/^\/obsidian-images\/[a-f0-9]{64}\.[a-z]+$/.test(url.pathname)||!/^[a-f0-9]{64}$/.test(result.sha256)||!url.pathname.split('/').pop().startsWith(result.sha256+'.'))throw new Error('Unexpected server image response.');
    const sourceHash=await digest(bytes);
    if(result.sourceSha256?result.sourceSha256!==sourceHash:result.sha256!==sourceHash)throw new Error('Image upload verification failed. Local files were kept.');
    return result;
  }
  async uploadImages(images){
    const embeds=[];
    for(const image of images){
      const result=await this.uploadImage(image);
      embeds.push('![]('+result.url+')');
    }
    return embeds.join('\n');
  }
  async pasteImages(images,source){
    try{return await this.uploadImages(images);}
    catch(error){
      const embeds=[];
      for(const image of images){
        const named=(image.name||'').match(imageExtension);
        const ext=named?named[1].toLowerCase():Object.keys(imageMime).find(key=>imageMime[key]===image.type)||'png';
        const path=await this.app.fileManager.getAvailablePathForAttachment("Pasted image "+new Date().toISOString().replace(/[-:TZ.]/g,"")+"."+ext,source);
        const file=await this.app.vault.createBinary(path,await image.arrayBuffer());
        embeds.push("![["+this.app.metadataCache.fileToLinktext(file,source,false)+"]]");
      }
      new Notice("Image saved locally because upload failed. Use Server Image Paste → Image cleanup when the server is available.");
      return embeds.join("\n");
    }
  }
  async cleanupImages(selectedPaths){
    if(this.cleaning)return;
    this.cleaning=true;
    const annotationId=this.app.plugins.plugins['tames-pdf']?'tames-pdf':'local-pdf-annotator';
    const annotation=this.app.plugins.plugins[annotationId];
    let paused=false;
    try{
      const selected=Array.isArray(selectedPaths)?new Set(selectedPaths):null;
      const images=this.app.vault.getFiles().filter(file=>imageExtension.test(file.name)&&(!selected||selected.has(file.path)));
      if(!images.length){new Notice('No local images to clean up.');return {uploaded:0,removed:0};}
      new Notice('Uploading '+images.length+' local image(s)…');
      const mappings=[];
      for(const file of images){
        const bytes=await this.app.vault.readBinary(file);
        const mime=imageMime[file.extension.toLowerCase()]||'application/octet-stream';
        const uploaded=await this.uploadImage(new File([bytes],file.name,{type:mime}));
        const url=uploaded.url;
        const response=await requestUrl({url,throw:false});
        if(response.status!==200)throw new Error('Could not verify uploaded image. Local files were kept.');
        const hash=await digest(bytes);
        if((uploaded.sourceSha256||uploaded.sha256)!==hash||await digest(response.arrayBuffer)!==uploaded.sha256)throw new Error('Image verification failed. Local files were kept.');
        mappings.push({file,path:file.path,name:file.name,url,hash});
      }
      // Pause annotation stores only during the manual rewrite, so stale in-memory
      // annotation JSON cannot overwrite the new image links.
      if(annotation){
        annotation.annotationRichEditor?.close();
        for(const leaf of this.app.workspace.getLeavesOfType('pdf'))await annotation.nativeOverlays?.overlayFor(leaf)?.store?.flush();
        await this.app.plugins.disablePlugin(annotationId);paused=true;
      }
      let updated=0;
      const documents=this.app.vault.getFiles().filter(file=>['md','json','canvas'].includes(file.extension));
      for(const file of documents){
        const current=await this.app.vault.cachedRead(file);
        if(replaceImageReferences(this.app,current,file.path,mappings)===current)continue;
        let changed=false;
        await this.app.vault.process(file,text=>{
          const next=replaceImageReferences(this.app,text,file.path,mappings);
          changed=next!==text;
          // Reject any accidental annotation JSON damage before writing.
          if(text.includes('lpa-annotations:')){
            const oldJson=Array.from(text.matchAll(/```json\s*\n([\s\S]*?)\n```/g)).at(-1);
            const newJson=Array.from(next.matchAll(/```json\s*\n([\s\S]*?)\n```/g)).at(-1);
            if(oldJson){JSON.parse(oldJson[1]);JSON.parse(newJson[1]);}
          }
          return next;
        });
        if(changed)updated++;
      }
      // Scan once for unresolved references; keep an image if anything still
      // references its filename. This is a manual action, never a scheduled job.
      const remaining=await Promise.all(documents.map(file=>this.app.vault.cachedRead(file)));
      let removed=0;
      for(const mapping of mappings){
        if(remaining.some(text=>text.includes(mapping.name)))continue;
        const live=this.app.vault.getAbstractFileByPath(mapping.path);
        if(live){
          const bytes=await this.app.vault.readBinary(live);
          const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(x=>x.toString(16).padStart(2,'0')).join('');
          if(hash!==mapping.hash)continue;
          await this.app.vault.delete(live);removed++;
        }
      }
      new Notice('Image cleanup complete: '+removed+' local image(s) removed, '+updated+' file(s) updated.'+(removed<mappings.length?' Some files were kept because references remain.':''));
      return {uploaded:mappings.length,removed,updated};
    }catch(error){new Notice(error.message||'Image cleanup failed; retry when the server is available.');throw error;}
    finally{if(paused)await this.app.plugins.enablePlugin(annotationId);this.cleaning=false;}
  }
  handlePaste(event,editor,info){
    if(event.defaultPrevented)return;
    const images=clipboardImages(event);if(!images.length)return;
    event.preventDefault();
    const marker='<!-- image-upload-'+crypto.randomUUID()+' -->';
    const file=info.file;
    editor.replaceSelection(marker);
    const replace=async(text)=>{
      if(info.file===file&&editor.getValue().includes(marker)){
        const start=editor.getValue().indexOf(marker);
        editor.replaceRange(text,editor.offsetToPos(start),editor.offsetToPos(start+marker.length));
      }else if(file&&this.app.vault.getAbstractFileByPath(file.path)){
        await this.app.vault.process(file,current=>current.replace(marker,text));
      }
    };
    this.pasteImages(images,file?.path||'').then(replace).catch(async error=>{await replace('');new Notice(error.message||'Could not upload image. No local attachment was saved.');});
  }
};
class ImageSettings extends PluginSettingTab{
  display(){
    const {containerEl}=this;containerEl.empty();
    containerEl.createEl('p',{text:'Pasted images upload to the server. If unavailable, they save locally. Image cleanup uploads local images and removes verified local copies. It runs only when clicked. Viewing image links needs Tailscale, but no upload key.'});
    new Setting(containerEl).setName('Image cleanup').setDesc('Upload local images, update note links, then remove verified local copies. Keep other devices synced first.').addButton(button=>button.setButtonText('Image cleanup').onClick(async()=>{button.setDisabled(true);try{await this.plugin.cleanupImages();}catch{}finally{button.setDisabled(false);}}));
    new Setting(containerEl).setName('Upload key').addText(text=>{text.inputEl.type='password';text.setValue(this.plugin.settings.uploadKey).onChange(async value=>{this.plugin.settings.uploadKey=value.trim();await this.plugin.saveData(this.plugin.settings);});});
  }
}

function replaceImageReferences(app,text,source,mappings){
  const lookup=target=>{
    let path;try{path=decodeURIComponent(target.replace(/^<|>$/g,''));}catch{path=target;}
    const resolved=app.metadataCache.getFirstLinkpathDest(path,source);
    return mappings.find(m=>resolved?.path===m.path||path===m.path||path===m.name);
  };
  const wiki=(all,bang,target,alias)=>{
    const mapping=lookup(target);if(!mapping)return all;
    const label=alias&&/^\d+(x\d+)?$/.test(alias)?'|'+alias:alias||'';
    return bang?'!['+label+']('+mapping.url+')':'['+(label||'Image')+']('+mapping.url+')';
  };
  text=text.replace(/(!?)\[\[([^\]\n|]+)(?:\|([^\]\n]*))?\]\]/g,wiki);
  text=text.replace(/(!?)&#91;&#91;([^\n]*?)&#93;&#93;/g,(all,bang,body)=>{
    const [target,alias]=body.split('|');
    return wiki(all,bang,target,alias);
  });
  text=text.replace(/(!?)\[([^\]\n]*)\]\((<[^>]+>|[^)\s]+)(?:\s+"[^"\n]*")?\)/g,(all,bang,label,target)=>{
    const mapping=lookup(target);return mapping?(bang?'!':'')+'['+label+']('+mapping.url+')':all;
  });
  return text;
}
