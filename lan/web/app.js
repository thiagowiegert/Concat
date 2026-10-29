// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Concat Plus contributors
'use strict';
const $=id=>document.getElementById(id);
let state=null, selected=null, connected=false, busy=false, previewNumber=0;
const jobs=new Map();
function message(text=''){ $('message').textContent=text;$('message').hidden=!text; }
async function request(path,body){
 const r=await fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Concat-LAN':'1'},body:JSON.stringify(body)});
 const data=await r.json();
 if(!r.ok){if(r.status===401){connected=false;$('pairing').hidden=false;$('connected').hidden=true;}throw Error(data.error||'La PC rechazó la operación');}
 return data;
}
const api=(op,params={})=>request('/api',{op,...params});
function element(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function button(text,action){const b=element('button',text);b.type='button';b.onclick=()=>run(action);return b;}
function option(select,value,text){const o=element('option',text);o.value=value;select.append(o);}
async function run(action){
 if(busy)return;
 busy=true;message();document.body.setAttribute('aria-busy','true');
 try{await action();}catch(e){message(e.message);$('connection').textContent='Revisa el mensaje';}
 finally{busy=false;document.body.removeAttribute('aria-busy');}
}
function timeline(){return state.view.project.timelines.find(t=>t.id===state.view.project.activeTimelineId)||state.view.project.timelines[0];}
function duration(){return Math.max(1,...timeline().clips.map(c=>c.start+c.duration));}
function currentClip(){return timeline().clips.find(c=>c.id===selected);}
function accept(value){state=value;sessionStorage.setItem('concat-project',state.project);render();}
async function mutate(op,params={}){
 const value=await api(op,{project:state.project,revision:state.revision,...params});accept(value);$('saved').textContent='Guardado en la PC';
}
const edit=command=>mutate('edit',{command});
function render(){
 $('editor').hidden=false;$('project-title').textContent=state.view.settings.name;
 $('undo').disabled=!state.view.canUndo;$('redo').disabled=!state.view.canRedo;
 $('seek').max=duration();$('timelines').replaceChildren();
 for(const t of state.view.project.timelines)option($('timelines'),t.id,t.name);
 $('timelines').value=timeline().id;
 $('media').replaceChildren();
 for(const m of state.view.project.media){const row=element('div',undefined,'item');row.append(element('b',m.name),button('Añadir al final',()=>edit({op:'addClipAtFirstFree',mediaId:m.id,start:timeline().clips.length?duration():0})));$('media').append(row);}
 $('tracks').replaceChildren();
 for(const track of timeline().tracks){const lane=element('div',undefined,'track');const title=element('div',undefined,'track-title');title.append(element('span',track.name||track.id),button(track.muted?'Activar audio':'Silenciar',()=>edit({op:'setTrackFlag',trackId:track.id,flag:'muted',value:!track.muted})));lane.append(title);
 for(const clip of timeline().clips.filter(c=>c.trackId===track.id).sort((a,b)=>a.start-b.start)){const b=button(clip.name,async()=>{selected=clip.id;$('seek').value=clip.start;render();await preview();});b.className='clip '+clip.kind+(selected===clip.id?' selected':'');b.append(element('small',`${clip.start.toFixed(2)} s → ${(clip.start+clip.duration).toFixed(2)} s · ${clip.duration.toFixed(2)} s`));lane.append(b);}$('tracks').append(lane);}
 inspector();
}
function inspector(){
 const c=currentClip();$('inspector').hidden=!c;if(!c)return;
 $('clip-name').textContent=c.name;$('clip-start').value=c.start;$('clip-track').replaceChildren();
 for(const t of timeline().tracks)option($('clip-track'),t.id,t.name||t.id);$('clip-track').value=c.trackId;
 $('clip-volume').value=Math.round((c.volume??1)*100);$('clip-opacity').value=Math.round((c.opacity??1)*100);
 $('clip-speed').value=c.speed??1;$('clip-scale').value=c.scale??1;$('clip-rotation').value=c.rotation??0;
 $('fade-in').value=c.fadeIn??0;$('fade-out').value=c.fadeOut??0;$('trim-start').value=0;$('trim-end').value=0;
 $('title-field').hidden=!c.text;$('title-text').value=c.text?.content||'';
}
async function preview(){
 if(!state)return;const serial=++previewNumber;const t=Number($('seek').value);$('time-label').textContent=t.toFixed(2)+' s';
 const data=await api('preview',{project:state.project,time:t});if(serial!==previewNumber)return;
 $('frame').src='data:image/png;base64,'+data.png;$('frame').hidden=false;$('playback').pause();$('playback').hidden=true;$('preview-empty').hidden=true;
}
async function files(){const values=await api('files');$('files').replaceChildren();for(const f of values)option($('files'),f.file,`${f.name} · ${(f.size/1048576).toFixed(1)} MB`);}
async function refresh(){
 const data=await api('info');connected=true;$('connection').textContent='Conectado · motor en la PC';$('pairing').hidden=true;$('connected').hidden=false;
 $('projects').replaceChildren();for(const p of data.projects)option($('projects'),p,p);
 for(const job of data.jobs)jobs.set(job.id,job);renderJobs();await files();
 const remembered=state?.project||sessionStorage.getItem('concat-project');
 if(remembered&&data.projects.includes(remembered)){ $('projects').value=remembered;accept(await api('open',{project:remembered})); }
 const effects=await api('catalogue');$('effects').replaceChildren();for(const e of effects)option($('effects'),e.id,e.name);
}
async function exportVideo(previewMode){const job=await api('export',{project:state.project,preview:previewMode});jobs.set(job.id,job);renderJobs();message('La PC está trabajando. Puedes desconectar el teléfono; mantén el host abierto en la PC.');}
function renderJobs(){
 $('jobs').replaceChildren();if(!jobs.size){$('jobs').textContent='Todavía no hay exportaciones.';return;}
 for(const job of [...jobs.values()].reverse()){const item=element('div',undefined,'item');item.append(element('strong',`${job.preview?'Previsualización':'Exportación'} · ${job.project}`));
 const status=job.state==='running'?`${job.stage||'Preparando'} · ${Math.round(job.fraction*100)} %`:({done:'Lista',cancelled:'Cancelada',failed:'Error'}[job.state]||job.state);item.append(element('p',status));
 if(job.state==='running'){const p=element('progress');p.value=job.fraction;p.max=1;item.append(p,button('Cancelar',async()=>{await api('cancel',{job:job.id});}));}
 if(job.state==='failed')item.append(element('p',job.error?.message||'No se pudo exportar'));
 if(job.state==='done'){const a=element('a','Descargar video');a.href='/download/'+job.id+'?save=1';item.append(a,button('Reproducir',async()=>{$('playback').src='/download/'+job.id;$('playback').hidden=false;$('frame').hidden=true;$('preview-empty').hidden=true;$('playback').scrollIntoView({behavior:'smooth'});await $('playback').play();}));}
 $('jobs').append(item);}
}
$('pair-form').onsubmit=e=>{e.preventDefault();run(async()=>{await request('/pair',{code:$('code').value});$('code').value='';await refresh();});};
$('create-form').onsubmit=e=>{e.preventDefault();run(async()=>{const [width,height]=$('format').value.split(',').map(Number);accept(await api('create',{name:$('project-name').value.trim(),width,height,fps:Number($('fps').value)}));await refresh();});};
$('refresh').onclick=()=>run(refresh);$('refresh-files').onclick=()=>run(files);
$('open').onclick=()=>run(async()=>{accept(await api('open',{project:$('projects').value}));selected=null;render();await preview();});
$('import').onclick=()=>run(async()=>{await mutate('import',{file:$('files').value});});
$('undo').onclick=()=>run(()=>mutate('undo'));$('redo').onclick=()=>run(()=>mutate('redo'));
$('frame-button').onclick=()=>run(preview);$('seek').oninput=()=>{$('time-label').textContent=Number($('seek').value).toFixed(2)+' s';};$('seek').onchange=()=>run(preview);
$('add-track').onclick=()=>run(()=>edit({op:'addTrack'}));
$('add-title').onclick=()=>run(async()=>{await edit({op:'addTextClip',trackId:null,above:true,start:Number($('seek').value),style:{content:'Tu título'},duration:4});selected=state.view.createdId;render();});
$('timelines').onchange=()=>run(()=>edit({op:'selectTimeline',timelineId:$('timelines').value}));
$('split').onclick=()=>run(()=>edit({op:'splitClips',clipIds:[selected],time:Number($('seek').value)}));
$('remove').onclick=()=>run(async()=>{await edit({op:'removeClips',clipIds:[selected],ripple:false});selected=null;render();});
$('clip-form').onsubmit=e=>{e.preventDefault();run(async()=>{
 const c={...currentClip()};const n=id=>Number($(id).value);
 const values={start:n('clip-start'),track:$('clip-track').value,volume:n('clip-volume')/100,opacity:n('clip-opacity')/100,speed:n('clip-speed'),scale:n('clip-scale'),rotation:n('clip-rotation'),head:n('trim-start'),tail:n('trim-end'),fadeIn:n('fade-in'),fadeOut:n('fade-out'),text:$('title-text').value};
 const patch={volume:values.volume,opacity:values.opacity,fadeIn:values.fadeIn,fadeOut:values.fadeOut};if(c.text)patch.text={...c.text,content:values.text};
 // One atomic command and undo step. The server validates every nested operation.
 const commands=[{op:'moveClips',moves:[{clipId:c.id,start:values.start,trackId:values.track}]},{op:'updateClip',clipId:c.id,patch},{op:'setClipTransform',clipId:c.id,scale:values.scale,rotation:values.rotation}];
 if(values.speed!==c.speed)commands.push({op:'setClipSpeed',clipId:c.id,speed:values.speed});
 if(values.head)commands.push({op:'trimClip',clipId:c.id,edge:'start',delta:values.head});
 if(values.tail)commands.push({op:'trimClip',clipId:c.id,edge:'end',delta:-values.tail});
 await edit({op:'batch',commands});await preview();
});};
$('add-effect').onclick=()=>run(async()=>{const c=currentClip();await edit({op:'updateClip',clipId:c.id,patch:{videoEffects:[...(c.videoEffects||[]),{id:$('effects').value,params:{}}]}});await preview();});
$('clear-effects').onclick=()=>run(async()=>{await edit({op:'updateClip',clipId:selected,patch:{videoEffects:[]}});await preview();});
$('export').onclick=()=>run(()=>exportVideo(false));$('preview-button').onclick=()=>run(()=>exportVideo(true));
$('upload').onchange=()=>run(async()=>{
 const list=Array.from($('upload').files);$('upload-progress').hidden=false;
 try{for(const file of list){const uploaded=await new Promise((resolve,reject)=>{
  const xhr=new XMLHttpRequest();xhr.open('POST','/upload');xhr.setRequestHeader('X-Concat-LAN','1');xhr.setRequestHeader('X-Filename',encodeURIComponent(file.name));
  xhr.upload.onprogress=e=>{if(e.lengthComputable)$('upload-progress').value=e.loaded/e.total*100;};
  xhr.onerror=()=>reject(Error('Se perdió la conexión durante la subida. Vuelve a seleccionar el archivo.'));
  xhr.onload=()=>{try{const data=JSON.parse(xhr.responseText);if(xhr.status>=400)reject(Error(data.error));else resolve(data);}catch(e){reject(e);}};xhr.send(file);
 });await mutate('import',{file:uploaded.file});}await files();}
 finally{$('upload').value='';$('upload-progress').hidden=true;}
});
async function poll(){
 if(connected&&!busy){try{for(const [id,job]of jobs)if(job.state==='running')jobs.set(id,await api('status',{job:id}));renderJobs();$('connection').textContent='Conectado · motor en la PC';}catch(e){$('connection').textContent='Sin conexión · vuelve a conectar';}}
 setTimeout(poll,1800);
}
refresh().catch(e=>{if(!connected){$('pairing').hidden=false;$('connection').textContent='Introduce el código de la PC';}else message(e.message);});poll();
