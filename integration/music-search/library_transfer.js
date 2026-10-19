/* Versioned, validated Music Lab library backups. No account tokens or audio. */
(function(root){
'use strict';
const id=t=>t.source+':'+t.id;
function entity(ref,source,kind){
if(!ref||ref.source!==source||ref.kind!==kind||typeof ref.id!=='string'||!ref.id||ref.id.length>2048||typeof ref.title!=='string'||ref.title.length>1000)return null;
return {id:ref.id,source,kind,title:ref.title};
}
function track(t){
if(!t||!['soundcloud','bandcamp','audius'].includes(t.source)||typeof t.id!=='string'||!t.id||t.id.length>2048||
typeof t.title!=='string'||!t.title.trim()||t.title.length>1000||typeof t.artist!=='string'||t.artist.length>1000||
(t.kind&&t.kind!=='track'))throw Error('В копии есть некорректный трек. Импорт отменён.');
if(t.duration!==undefined&&(!Number.isFinite(t.duration)||t.duration<0||t.duration>86400))throw Error('Некорректная длительность трека.');
const artwork={};for(const size of ['480x480','150x150']){const url=t.artwork?.[size];if(typeof url==='string'&&url.startsWith('https://')&&url.length<2048)artwork[size]=url;}
// Backward-compatible version 1 backup: the legacy preview flag was also set
// for genuine short songs. Treat it as unverified, not proof of a snippet.
const statuses=['playable','preview_only','blocked','unknown','temporary_error'];
const availability=t.unavailable===true?'blocked':statuses.includes(t.availability)?t.availability:'unknown';
return {id:t.id,source:t.source,kind:'track',title:t.title,artist:t.artist,duration:t.duration||0,
artwork:Object.keys(artwork).length?artwork:null,permalink:typeof t.permalink==='string'?t.permalink.slice(0,2048):'',preview:availability==='preview_only',unavailable:availability==='blocked',availability,artist_ref:entity(t.artist_ref,t.source,'artist'),album_ref:entity(t.album_ref,t.source,'album')};
}
function unique(items){return [...new Map(items.map(t=>[id(t),t])).values()];}
function validate(data){
if(data?.format!=='musiclab-library'||data.version!==1)throw Error('Выбери JSON-копию библиотеки Music Lab версии 1.');
const lib=data.library;if(!lib||!Array.isArray(lib.likes)||!Array.isArray(lib.dislikes)||!Array.isArray(lib.playlists))throw Error('Неполная копия библиотеки.');
if(lib.playlists.length>500||lib.likes.length+lib.dislikes.length+lib.playlists.reduce((n,p)=>n+(Array.isArray(p?.tracks)?p.tracks.length:10001),0)>10000)throw Error('В одной копии допустимо до 10 000 записей и 500 плейлистов.');
const playlists=lib.playlists.map(p=>{if(!p||typeof p.id!=='string'||!p.id||p.id.length>200||typeof p.name!=='string'||!p.name.trim()||p.name.length>200||!Array.isArray(p.tracks))throw Error('Некорректный плейлист в копии.');return {id:p.id,name:p.name,tracks:unique(p.tracks.map(track))};});
if(new Set(playlists.map(p=>p.id)).size!==playlists.length)throw Error('В копии повторяются идентификаторы плейлистов.');
return {likes:unique(lib.likes.map(track)),dislikes:unique(lib.dislikes.map(track)),playlists};
}
function merge(current,incoming){
const existingLikes=new Set(current.likes.map(id)),existingDislikes=new Set(current.dislikes.map(id));
const likes=unique([...current.likes,...incoming.likes.filter(t=>!existingDislikes.has(id(t)))]);
const liked=new Set(likes.map(id));
const dislikes=unique([...current.dislikes,...incoming.dislikes.filter(t=>!existingLikes.has(id(t))&&!liked.has(id(t)))]);
const playlists=current.playlists.map(p=>({...p,tracks:p.tracks.slice()}));
for(const p of incoming.playlists){const present=playlists.find(x=>x.id===p.id);if(present)present.tracks=unique([...present.tracks,...p.tracks]);else playlists.push({...p,tracks:p.tracks.slice()});}
return {likes,dislikes,playlists};
}
function summary(library){
return {likes:library.likes.length,dislikes:library.dislikes.length,
playlists:library.playlists.length,
playlistTracks:library.playlists.reduce((n,p)=>n+p.tracks.length,0)};
}
function prepareBackup(library,exportedAt=new Date().toISOString()){
// Normalize a snapshot, then check that the *serialized* file is restorable.
// Do not change the current browser library or its localStorage contents.
const safe=validate({format:'musiclab-library',version:1,library});
const copy={format:'musiclab-library',version:1,exported_at:exportedAt,library:safe};
const content=JSON.stringify(copy,null,2);
const restored=validate(JSON.parse(content));
if(JSON.stringify(safe)!==JSON.stringify(restored))
throw Error('Проверка целостности копии не пройдена. Данные не изменены.');
return {content,summary:summary(restored)};
}
const api={validate,merge,unique,summary,prepareBackup};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.LibraryTransfer=api;
})(typeof window!=='undefined'?window:globalThis);
