const $=s=>document.querySelector(s);let token=localStorage.getItem('evervault_token')||'',items=[],view='all',album='',currentIndex=0,uploading=false;
const authHeaders=()=>({'Authorization':'Bearer '+token,'Content-Type':'application/json'});
async function api(path,opts={}){let headers={...(opts.headers||{})};if(token)headers.Authorization='Bearer '+token;if(opts.body&&typeof opts.body==='string')headers['Content-Type']='application/json';let r=await fetch(path,{...opts,headers});if(r.status===401){logout(false);throw Error('Your session expired. Please sign in again.')}let data=await r.json().catch(()=>({}));if(!r.ok)throw Error(data.detail||'Something went wrong');return data}
function toast(t){$('#toast').textContent=t;$('#toast').classList.add('show');setTimeout(()=>$('#toast').classList.remove('show'),2500)}

function nativeBackupPlugin(){return window.Capacitor?.Plugins?.EvervaultBackup||null}
function showBackupSettings(){
  const modal=$('#backupModal');modal.classList.remove('hidden');
  $('#backupServerUrl').value=location.origin;
  const plugin=nativeBackupPlugin();
  if(!plugin){$('#backupStateTitle').textContent='Android app required';$('#backupStateText').textContent='Install the Evervault Android APK with the native backup module to enable background backup.';$('#backupEnable').disabled=true;$('#backupDisable').disabled=true;return}
  $('#backupEnable').disabled=false;$('#backupDisable').disabled=false;
  plugin.getStatus().then(s=>{const enabled=!!s.enabled;$('#backupStateTitle').textContent=enabled?'Automatic backup is enabled':'Automatic backup is paused';$('#backupStateText').textContent=enabled?`Charging constraint active · ${s.network||'unmetered network'} · Last run: ${s.lastRun||'not reported'}`:'Enable to schedule background backup while charging.'}).catch(()=>{ $('#backupStateTitle').textContent='Ready to configure';$('#backupStateText').textContent='Grant media access when Android asks.'});
}
$('#backupSettingsBtn')?.addEventListener('click',e=>{e.preventDefault();e.stopImmediatePropagation();showBackupSettings()});
$('#backupClose')?.addEventListener('click',()=>$('#backupModal').classList.add('hidden'));
$('#backupModal')?.addEventListener('click',e=>{if(e.target===$('#backupModal'))$('#backupModal').classList.add('hidden')});
$('#backupEnable')?.addEventListener('click',async()=>{
  const plugin=nativeBackupPlugin();if(!plugin){toast('Install the Android app with background backup support');return}
  const serverUrl=$('#backupServerUrl').value.trim().replace(/\/$/,'');
  if(!/^https:\/\//i.test(serverUrl)){toast('Enter a valid HTTPS Evervault address');return}
  if(!token){toast('Sign in before enabling backup');return}
  try{
    $('#backupEnable').disabled=true;
    await plugin.configure({serverUrl,token,network:$('#backupNetwork').value});
    const result=await plugin.enable();
    $('#backupStateTitle').textContent='Automatic backup enabled';
    $('#backupStateText').textContent='Android will back up new photos and videos while charging under your selected network condition.';
    toast(result.message||'Automatic backup enabled');
  }catch(err){toast(err.message||'Could not enable automatic backup')}
  finally{$('#backupEnable').disabled=false}
});
$('#backupDisable')?.addEventListener('click',async()=>{
  const plugin=nativeBackupPlugin();if(!plugin){toast('Android app required');return}
  try{await plugin.disable();$('#backupStateTitle').textContent='Automatic backup paused';$('#backupStateText').textContent='No background uploads will be scheduled.';toast('Automatic backup paused')}catch(err){toast(err.message||'Could not pause backup')}
});

function showAuth(setup=false){$('#app').classList.add('hidden');$('#authScreen').classList.remove('hidden');$('#authTitle').textContent=setup?'Make it yours.':'Welcome home.';$('#authDesc').textContent=setup?'Create the owner account for your private library.':'Your photos. Your videos. Your storage.';$('#authSubmit').textContent=setup?'Create private library':'Sign in';$('#password').autocomplete=setup?'new-password':'current-password';$('#password').minLength=setup?12:1;$('#authError').textContent='';$('#authForm').dataset.setup=setup?'1':'0'}
function logout(show=true){token='';localStorage.removeItem('evervault_token');for(const url of objectUrls.values())URL.revokeObjectURL(url);objectUrls.clear();if(show)toast('Signed out');showAuth(false)}
async function init(){try{let s=await fetch('/api/status').then(r=>r.json());if(!token){showAuth(s.setup_required);return}await api('/api/me');$('#authScreen').classList.add('hidden');$('#app').classList.remove('hidden');await refresh()}catch(e){if(!token)showAuth(true);else showAuth(false)}}
$('#authForm').addEventListener('submit',async e=>{e.preventDefault();let setup=$('#authForm').dataset.setup==='1';$('#authError').textContent='';try{let data=await api(setup?'/api/setup':'/api/login',{method:'POST',body:JSON.stringify({username:$('#username').value.trim(),password:$('#password').value})});token=data.token;localStorage.setItem('evervault_token',token);await init()}catch(err){$('#authError').textContent=err.message}});
$('#logout').onclick=()=>logout();
function humanSize(n){if(n<1024)return n+' B';let u=['KB','MB','GB','TB'],i=-1;do{n/=1024;i++}while(n>=1024&&i<u.length-1);return n.toFixed(n>=10?0:1)+' '+u[i]}
function dateLabel(ts){return new Date(ts*1000).toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'})}
function isVideo(x){return x.mime.startsWith('video/')}
const objectUrls = new Map();
async function loadMediaBlob(x){
  if(objectUrls.has(x.id)) return objectUrls.get(x.id);
  const response = await fetch('/api/media/'+encodeURIComponent(x.id)+'/file', {
    headers: { Authorization: 'Bearer '+token }
  });
  if(!response.ok) throw Error(response.status===401?'Please sign in again':'Media request failed ('+response.status+')');
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  objectUrls.set(x.id,url);
  return url;
}
function mediaUrl(x){return '/api/media/'+x.id+'/file'}
function render(){let filtered=items.filter(x=>view!=='favorites'||x.favorite);$('#grid').innerHTML='';$('#empty').classList.toggle('hidden',filtered.length!==0);$('#grid').classList.toggle('hidden',filtered.length===0);$('#countText').textContent=`${filtered.length} ${filtered.length===1?'memory':'memories'} · Original quality`;$('#gridTitle').textContent=view==='favorites'?'Your favourites':album||'Recently added';filtered.forEach((x,i)=>{let card=document.createElement('article');card.className='media-card';let thumb=document.createElement('div');thumb.className='thumb';if(x.mime.startsWith('image/')){let img=document.createElement('img');img.loading='lazy';img.alt=x.name;img.dataset.mediaId=x.id;img.onerror=()=>{img.remove();thumb.innerHTML='<span>▧</span>'};thumb.append(img);loadMediaBlob(x).then(url=>{if(img.isConnected)img.src=url}).catch(()=>{if(img.isConnected){img.remove();thumb.innerHTML='<span title="Could not load media">▧</span>'}})}else{thumb.innerHTML=`<span style="font-size:35px;color:#b5f36b">▷</span>${isVideo(x)?'<span class="video-mark">▶ VIDEO</span>':'<span class="video-mark">FILE</span>'}`}thumb.onclick=()=>openViewer(x.id);let menu=document.createElement('div');menu.className='card-menu';menu.innerHTML=`<button title="${x.favorite?'Remove favourite':'Add favourite'}">${x.favorite?'♥':'♡'}</button><button title="More options">···</button>`;menu.children[0].onclick=async e=>{e.stopPropagation();await api('/api/media/'+x.id+'/favorite',{method:'PATCH',body:JSON.stringify({favorite:!x.favorite})});x.favorite=!x.favorite;render()};menu.children[1].onclick=e=>{e.stopPropagation();let a=prompt('Album name (leave blank to remove from album):',x.album||'');if(a===null)return;api('/api/media/'+x.id+'/album',{method:'PATCH',body:JSON.stringify({album:a})}).then(refresh).then(()=>toast('Album updated')).catch(err=>toast(err.message))};let info=document.createElement('div');info.className='card-info';info.innerHTML=`<div class="file-icon">${isVideo(x)?'▷':'▧'}</div><div class="file-text"><b title="${escapeHtml(x.name)}">${escapeHtml(x.name)}</b><small>${dateLabel(x.created)} · ${humanSize(x.size)}</small></div>`;card.append(thumb,menu,info);$('#grid').append(card)});renderAlbums()}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function refresh(){let q=$('#search').value.trim();let data=await api('/api/media?q='+encodeURIComponent(q));items=data.items;render()}
async function renderAlbums(){try{let d=await api('/api/albums');$('#albumNav').innerHTML=d.items.map(a=>`<button class="album-link" data-album="${escapeHtml(a.album)}">▱ &nbsp;${escapeHtml(a.album)} <small> ${a.count}</small></button>`).join('');document.querySelectorAll('.album-link').forEach(b=>b.onclick=()=>{album=b.dataset.album;view='album';$('#viewTitle').textContent=album;document.querySelectorAll('.nav').forEach(n=>n.classList.remove('active'));api('/api/media?album='+encodeURIComponent(album)).then(d=>{items=d.items;render()})})}catch{}}
document.querySelectorAll('.nav').forEach(b=>b.onclick=()=>{if(b.dataset.view==='backup'){showBackupSettings();return}document.querySelectorAll('.nav').forEach(n=>n.classList.remove('active'));b.classList.add('active');view=b.dataset.view;album='';$('#viewTitle').textContent=view==='favorites'?'Favourites':'All memories';refresh()});
$('#search').addEventListener('input',()=>{clearTimeout(window.searchTimer);window.searchTimer=setTimeout(refresh,220)});
$('#uploadBtn').onclick=()=>$('#fileInput').click();
$('#fileInput').onchange=async e=>{let files=[...e.target.files];e.target.value='';if(files.length)uploadFiles(files)};
async function uploadFiles(files){if(uploading)return;uploading=true;$('#uploadStatus').classList.remove('hidden');let done=0,total=files.reduce((n,f)=>n+f.size,0),sent=0;try{for(let f of files){$('#uploadLabel').textContent='Uploading '+f.name;$('#uploadSub').textContent=`File ${done+1} of ${files.length} · Keep this window open`;let session=await api('/api/uploads',{method:'POST',body:JSON.stringify({name:f.name,size:f.size,mime:f.type})});let offset=0,chunkSize=session.chunk_size;while(offset<f.size){let chunk=f.slice(offset,Math.min(offset+chunkSize,f.size));let r=await fetch('/api/uploads/'+session.upload_id+'?offset='+offset,{method:'PUT',headers:{Authorization:'Bearer '+token,'Content-Type':'application/octet-stream'},body:chunk});let d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.detail||'Upload interrupted');offset=d.offset;sent+=chunk.size;$('#progressBar').style.width=Math.round(sent/total*100)+'%';$('#uploadPercent').textContent=Math.round(sent/total*100)+'%'}await api('/api/uploads/'+session.upload_id+'/finish',{method:'POST'});done++}toast(`Uploaded ${done} ${done===1?'memory':'memories'}`);await refresh()}catch(err){toast('Upload paused: '+err.message)}finally{uploading=false;setTimeout(()=>$('#uploadStatus').classList.add('hidden'),1800)}}
function openViewer(id){let filtered=items.filter(x=>view!=='favorites'||x.favorite);currentIndex=filtered.findIndex(x=>x.id===id);renderViewer(filtered)}
function renderViewer(filtered){if(!filtered)filtered=items.filter(x=>view!=='favorites'||x.favorite);if(!filtered.length)return;let x=filtered[currentIndex];$('#viewer').classList.remove('hidden');$('#viewerContent').innerHTML='';let el;if(x.mime.startsWith('image/')){el=document.createElement('img');el.alt=x.name}else if(isVideo(x)){el=document.createElement('video');el.controls=true;el.autoplay=true;el.playsInline=true}else{el=document.createElement('div');el.textContent='Preview is not available for this file type.'}if(el.tagName==='IMG'||el.tagName==='VIDEO'){el.onerror=()=>toast('Could not load this media file');$('#viewerContent').append(el);loadMediaBlob(x).then(url=>{if(el.isConnected){el.src=url;if(el.tagName==='VIDEO')el.load()}}).catch(err=>toast(err.message||'Could not load this media file'))}else{$('#viewerContent').append(el)}$('#viewerMeta').textContent=x.name+' · '+dateLabel(x.created)+' · '+humanSize(x.size)}
function stepViewer(n){let filtered=items.filter(x=>view!=='favorites'||x.favorite);currentIndex=(currentIndex+n+filtered.length)%filtered.length;renderViewer(filtered)}
$('#closeViewer').onclick=()=>{$('#viewer').classList.add('hidden');$('#viewerContent').innerHTML=''};$('#prevViewer').onclick=()=>stepViewer(-1);$('#nextViewer').onclick=()=>stepViewer(1);document.addEventListener('keydown',e=>{if(e.key==='Escape')$('#closeViewer').click();if(e.key==='ArrowLeft'&&!$('#viewer').classList.contains('hidden'))stepViewer(-1);if(e.key==='ArrowRight'&&!$('#viewer').classList.contains('hidden'))stepViewer(1)});
init();
