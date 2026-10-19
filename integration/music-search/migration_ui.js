/* Automatic cross-service import. Only uncertain rows need user attention. */
window.MigrationUI={mount({getLibrary,commitLibrary,onChange,playTrack}){
const $=id=>document.getElementById(id),screen=$('migrationView'),notice=$('migrationStatus');
const names={soundcloud:'SoundCloud',bandcamp:'Bandcamp',audius:'Audius'};
let opener=null,busy=false,aborter=null,pending=null,previewPage=0,reviewLimit=25,missingLimit=25;
const message=text=>{notice.textContent=text;};
async function request(path,body,signal){
 const response=await fetch('/api/migration/'+path,{signal,...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
 const data=await response.json();if(!response.ok){const error=Error(data.error||'Не удалось получить данные.');error.code=data.code;throw error;}return data;
}
function download(name,data){const content=typeof data==='string'?data:JSON.stringify(data,null,2);const url=URL.createObjectURL(new Blob([content],{type:'application/json;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function setBusy(value){busy=value;for(const id of ['publicPlaylistLoad','publicPlaylistUrl','migrationMatch','migrationApply'])$(id).disabled=value;$('migrationCancel').hidden=!value;}
async function run(fn){if(busy)return;setBusy(true);aborter=new AbortController();try{await fn(aborter.signal);}catch(e){message(e.name==='AbortError'?'Поиск остановлен. Найденные треки сохранены до закрытия страницы.':e.message);}finally{setBusy(false);if(pending)render();}}
async function loadPlaylist(signal){
 pending=null;$('migrationPreview').hidden=true;message('Получаю список из публичного плейлиста…');
 const data=await request('public-playlist',{url:$('publicPlaylistUrl').value.trim()},signal);
 pending={name:data.name,tracks:data.data,choices:Array(data.data.length).fill(null),results:Array(data.data.length).fill(null),errors:{},excluded:new Set(),skipped:data.skipped,source_url:data.source_url,total:data.total,complete:data.complete};
 previewPage=0;reviewLimit=25;missingLimit=25;$('migrationReview').open=false;$('migrationMissing').open=false;
 $('migrationPreview').hidden=false;$('migrationPlaylistName').textContent=data.name;$('migrationOriginalLink').href=data.source_url;
 $('publicPlaylistCompleteness').textContent=data.complete?'Получен полный список: '+data.received+' из '+data.total+' треков.':data.total===null?'Получено '+data.received+' треков. Полноту списка Spotify подтвердить не удалось.':'Получено только '+data.received+' из '+data.total+' треков. Будет импортирована только полученная часть.';
 if(data.skipped)$('publicPlaylistCompleteness').textContent+=' Пропущено недоступных записей или подкастов: '+data.skipped+'.';
 render();if(data.data.length)await match(signal);else message('В плейлисте нет доступных музыкальных записей.');
}
function groups(){const found=[],review=[],missing=[];let waiting=0;pending.tracks.forEach((t,i)=>{const result=pending.results[i];if(pending.choices[i])found.push(i);else if(result?.best)review.push(i);else if(result||pending.errors[i])missing.push(i);else waiting++;});return {found,review,missing,waiting};}
function time(seconds){return Number.isFinite(seconds)&&seconds>0?Math.floor(seconds/60)+':'+String(Math.round(seconds%60)).padStart(2,'0'):'';}
function row(i,kind){
 const original=pending.tracks[i],result=pending.results[i],chosen=pending.choices[i],candidate=chosen||result?.best;
 const root=document.createElement('div');root.className='migration-row';root.dataset.originalIndex=i;
 const title=document.createElement('strong');title.textContent=original.title+' — '+original.artist;root.append(title);
 const meta=document.createElement('span');meta.className='match-meta';
 if(chosen){meta.classList.add('match-found');meta.textContent=(result?.state==='matched'?'✓ Найдено':'✓ Подтверждено тобой')+' · '+(names[chosen.source]||chosen.source)+(time(chosen.duration)?' · '+time(chosen.duration):'');}
 else if(candidate){meta.classList.add('match-warning');meta.textContent=pending.excluded.has(i)?'Пропущено тобой':(candidate.assessment?.reasons||[]).join(' · ')||'Нужно подтвердить версию записи';}
 else meta.textContent=pending.errors[i]|| (result?.state==='incomplete'?'Не все сервисы ответили — можно повторить поиск':'Подходящая запись не найдена');
 root.append(meta);
 if(candidate&&!chosen){const suggestion=document.createElement('span');suggestion.className='match-meta';suggestion.textContent='Возможный вариант: '+candidate.title+' — '+candidate.artist+' · '+(names[candidate.source]||candidate.source)+(time(candidate.duration)?' · '+time(candidate.duration):'');root.append(suggestion);}
 const actions=document.createElement('div');actions.className='match-actions';
 if(candidate&&typeof playTrack==='function'){const play=document.createElement('button');play.className='minor';play.textContent='▶ Послушать';play.onclick=()=>playTrack(candidate);actions.append(play);}
 if(candidate){const choose=document.createElement('button');choose.className='minor';choose.disabled=busy||!!pending.applied;choose.textContent=chosen?'Пропустить':pending.excluded.has(i)?'Вернуть':'Использовать вариант';choose.onclick=()=>{if(chosen){pending.choices[i]=null;pending.excluded.add(i);}else{pending.choices[i]=candidate;pending.excluded.delete(i);}render();};actions.append(choose);}
 const backlink=document.createElement('a');backlink.href=original.source_url;backlink.target='_blank';backlink.rel='noopener noreferrer';backlink.textContent='Оригинал в Spotify ↗';actions.append(backlink);root.append(actions);return root;
}
function render(){
 if(!pending)return;const g=groups();const pageCount=Math.max(1,Math.ceil(g.found.length/25));previewPage=Math.min(previewPage,pageCount-1);
 $('migrationRows').replaceChildren(...g.found.slice(previewPage*25,previewPage*25+25).map(i=>row(i,'found')));
 $('migrationPage').textContent='Страница '+(previewPage+1)+' из '+pageCount;$('migrationPrev').disabled=previewPage===0;$('migrationNext').disabled=previewPage+1>=pageCount;
 $('migrationReview').hidden=!g.review.length;$('migrationReviewTitle').textContent='Нужна проверка ('+g.review.length+')';$('migrationReviewRows').replaceChildren(...g.review.slice(0,reviewLimit).map(i=>row(i,'review')));$('migrationReviewMore').hidden=g.review.length<=reviewLimit;
 $('migrationMissing').hidden=!g.missing.length;$('migrationMissingTitle').textContent='Не найдены или не проверены ('+g.missing.length+')';$('migrationMissingRows').replaceChildren(...g.missing.slice(0,missingLimit).map(i=>row(i,'missing')));$('migrationMissingMore').hidden=g.missing.length<=missingLimit;
 $('migrationCounts').textContent='Найдено '+g.found.length+' из '+pending.tracks.length+' · проверить '+g.review.length+' · не найдено '+g.missing.length+(g.waiting?' · осталось '+g.waiting:'');
 $('migrationApply').textContent='Импортировать '+g.found.length+' треков';$('migrationApply').disabled=busy||!g.found.length||!!pending.applied;$('migrationMatch').disabled=busy||!!pending.applied;
 const failed=[...new Set(pending.results.flatMap(r=>r?.failed_sources||[]))];$('migrationSourceWarning').hidden=!failed.length;$('migrationSourceWarning').textContent=failed.length?'Не удалось полностью проверить: '+failed.map(x=>names[x]||x).join(', ')+'. Их записи могут отсутствовать в результатах.':'';
}
async function match(signal){
 if(!pending)return;let paused=false;
 for(let i=0;i<pending.tracks.length;i++){
  if(pending.choices[i]||pending.excluded.has(i))continue;
  message('Ищу во всех сервисах: '+(i+1)+' из '+pending.tracks.length+'…');
  try{const result=await request('resolve',{track:pending.tracks[i]},signal);pending.results[i]=result;pending.choices[i]=result.selected||null;delete pending.errors[i];}
  catch(e){if(e.name==='AbortError')throw e;pending.errors[i]=e.message;if(e.code==='sources_unavailable'){paused=true;render();break;}}
  render();
 }
 message(paused?'Сервисы временно недоступны. Найденное сохранено — можно повторить поиск позднее.':'Поиск завершён. Можно импортировать найденные треки. Сомнительные варианты — в разделе «Нужна проверка».');
}
$('publicPlaylistLoad').onclick=()=>run(loadPlaylist);$('publicPlaylistUrl').onkeydown=e=>{if(e.key==='Enter')run(loadPlaylist);};
$('migrationMatch').onclick=()=>run(match);$('migrationCancel').onclick=()=>aborter?.abort();
$('migrationPrev').onclick=()=>{if(previewPage>0){previewPage--;render();}};$('migrationNext').onclick=()=>{previewPage++;render();};
$('migrationReviewMore').onclick=()=>{reviewLimit+=25;render();};$('migrationMissingMore').onclick=()=>{missingLimit+=25;render();};
$('migrationApply').onclick=()=>{
 if(!pending||busy||pending.applied)return;const selected=LibraryTransfer.unique(pending.choices.filter(Boolean)).map(({score,assessment,...t})=>t);if(!selected.length)return;
 try{const lib=getLibrary();const playlist={id:'import-'+Date.now()+'-'+Math.random().toString(36).slice(2),name:pending.name.slice(0,200),tracks:selected};commitLibrary({...lib,playlists:[...lib.playlists,playlist]});onChange();pending.applied=true;message('Создан плейлист «'+pending.name+'»: '+selected.length+' треков. Остальные записи не добавлены.');render();}catch(e){message('Импорт не сохранён: '+e.message);}
};
$('migrationExportSource').onclick=()=>{if(pending)download('spotify-track-list.json',{format:'musiclab-source-list',version:1,source:'spotify',source_url:pending.source_url,name:pending.name,tracks:pending.tracks,total:pending.total,complete:pending.complete,skipped:pending.skipped});};
$('libraryExport').onclick=()=>{try{
const backup=LibraryTransfer.prepareBackup(getLibrary());
if(new Blob([backup.content]).size>2*1024*1024)throw Error('Размер копии больше 2 МБ. Экспорт не выполнен; библиотека не изменена.');
download('musiclab-library-'+new Date().toISOString().slice(0,10)+'.json',backup.content);
const n=backup.summary;
message('JSON проверен на восстановление: '+n.playlists+' плейлистов, '+n.playlistTracks+' записей в них, '+n.likes+' лайков, '+n.dislikes+' дизлайков. Сохрани файл в «Файлы» на устройстве. Настройки и очередь в копию не входят.');
}catch(e){message('Не удалось подготовить копию: '+e.message+' Библиотека не изменена.');}};
let incoming=null;
$('libraryImport').onchange=async()=>{incoming=null;$('libraryImportApply').hidden=true;const file=$('libraryImport').files?.[0];if(!file)return;try{if(file.size>2*1024*1024)throw Error('Выбери JSON-копию размером до 2 МБ.');incoming=LibraryTransfer.validate(JSON.parse(await file.text()));const n=LibraryTransfer.summary(incoming);$('libraryImportSummary').textContent='Копия читается: '+n.playlists+' плейлистов, '+n.playlistTracks+' записей в них, '+n.likes+' лайков, '+n.dislikes+' дизлайков. Пока ничего не изменено. Для объединения нажми «Добавить к моей библиотеке».';$('libraryImportApply').hidden=false;}catch(e){message('Копия не импортирована: '+e.message);}finally{$('libraryImport').value='';}};
$('libraryImportApply').onclick=()=>{if(!incoming)return;try{const next=LibraryTransfer.merge(getLibrary(),incoming);commitLibrary(next);onChange();incoming=null;$('libraryImportApply').hidden=true;$('libraryImportSummary').textContent='Копия добавлена к текущей библиотеке.';message('Импорт библиотеки завершён.');}catch(e){message('Изменения не сохранены: '+e.message);}};
function close(){aborter?.abort();screen.hidden=true;document.body.style.overflow='';if(opener?.isConnected)opener.focus();}
function open(){opener=document.activeElement;screen.hidden=false;document.body.style.overflow='hidden';$('closeMigration').focus();}
$('openMigration').onclick=open;$('closeMigration').onclick=close;
screen.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close();}if(e.key==='Tab'){const items=[...screen.querySelectorAll('button,input,select,a[href]')].filter(x=>!x.disabled&&x.getClientRects().length);const first=items[0],last=items.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}});
}};
