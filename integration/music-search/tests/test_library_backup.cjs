const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const transfer = require('../library_transfer.js');

const song=(source,id,title)=>({
  source,id,kind:'track',title,artist:'Portishead',
  duration:278,availability:'playable',
  artist_ref:{source,id:'artist-'+source,kind:'artist',title:'Portishead'}
});
function fixture(){
  return {
    likes:[song('soundcloud','a','Roads'),song('bandcamp','b','Glory Box')],
    dislikes:[song('audius','c','Cover')],
    playlists:[
      {id:'pl-1',name:'Ночное прослушивание',tracks:[
        song('soundcloud','a','Roads'),song('audius','d','Sour Times')]},
      {id:'pl-2',name:'Пустой',tracks:[]}
    ]
  };
}
const ids=items=>items.map(t=>t.source+':'+t.id);

test('real JSON string roundtrip keeps playlists, order, likes, dislikes and original source IDs',()=>{
  const old=fixture();
  const untouched=JSON.stringify(old);
  const prepared=transfer.prepareBackup(old,'2026-10-19T00:00:00.000Z');
  assert.deepEqual(prepared.summary,
    {likes:2,dislikes:1,playlists:2,playlistTracks:2});
  assert.equal(JSON.stringify(old),untouched,'export must not mutate local library');
  const file=JSON.parse(prepared.content);
  assert.equal(file.version,1);
  assert.equal(file.format,'musiclab-library');
  assert.equal(file.exported_at,'2026-10-19T00:00:00.000Z');
  assert.equal('settings' in file,false,'settings not supported or leaked');
  assert.equal('queue' in file,false,'queue not supported');
  const restored=transfer.validate(file);
  assert.deepEqual(ids(restored.likes),['soundcloud:a','bandcamp:b']);
  assert.deepEqual(ids(restored.dislikes),['audius:c']);
  assert.deepEqual(restored.playlists.map(p=>p.name),
    ['Ночное прослушивание','Пустой']);
  assert.deepEqual(ids(restored.playlists[0].tracks),['soundcloud:a','audius:d']);
  assert.deepEqual(restored.playlists[0].tracks[0].artist_ref,old.playlists[0].tracks[0].artist_ref);
  const merged=transfer.merge({likes:[],dislikes:[],playlists:[]},restored);
  assert.deepEqual(merged,restored);
  assert.deepEqual(transfer.merge(merged,restored),restored,'repeated import is idempotent');
});

test('validation failure never mutates original library or issues a corrupt file',()=>{
  const bad=fixture();
  bad.likes.push({source:'invalid',id:'secret',title:'Bad',artist:'No'});
  const before=JSON.stringify(bad);
  assert.throws(()=>transfer.prepareBackup(bad),/некорректный трек/);
  assert.equal(JSON.stringify(bad),before);
});

test('read-only import preview and explicit apply are separate actions',async()=>{
  const ui=fs.readFileSync(path.join(__dirname,'../migration_ui.js'),'utf8');
  const elements=new Map();
  const downloads=[];
  const element=id=>{
    if(!elements.has(id)){
      elements.set(id,{
        id,hidden:false,disabled:false,value:'',textContent:'',style:{},
        files:[],addEventListener(){},getClientRects(){return [1]},
        replaceChildren(){},append(){},remove(){},focus(){}
      });
    }
    return elements.get(id);
  };
  const document={
    getElementById:element,
    querySelectorAll(){return []},
    createElement(tag){
      if(tag==='a')return {click(){downloads.push(this)},remove(){}};
      return {style:{},append(){},addEventListener(){}};
    },
    body:{append(){},style:{}}
  };
  const urls={createObjectURL:()=> 'blob:local-backup',revokeObjectURL:()=>{}};
  let current=fixture(),writes=0;
  const context={
    window:{},
    document,
    LibraryTransfer:transfer,
    Blob,
    URL:urls,
    Date,JSON,
    setTimeout:()=>{},
    AbortController
  };
  vm.createContext(context);
  vm.runInContext(ui,context);
  context.window.MigrationUI.mount({
    getLibrary:()=>current,
    commitLibrary:next=>{writes++;current=next},
    onChange:()=>{},
    playTrack:()=>{}
  });
  element('libraryExport').onclick();
  assert.equal(writes,0,'export cannot touch localStorage/library');
  assert.equal(downloads.length,1);
  const snapshot=downloads[0];
  assert.match(snapshot.download,/^musiclab-library-\d{4}-\d{2}-\d{2}\.json$/);
  const msg=element('migrationStatus').textContent;
  assert.match(msg,/JSON проверен на восстановление/);
  assert.match(msg,/Настройки и очередь в копию не входят/);
  const restored=transfer.prepareBackup(fixture(),'2026-10-19T00:00:00.000Z').content;
  element('libraryImport').files=[{size:Buffer.byteLength(restored),text:async()=>restored}];
  await element('libraryImport').onchange();
  assert.equal(writes,0,'import preview must not commit');
  assert.match(element('libraryImportSummary').textContent,/Пока ничего не изменено/);
  assert.match(element('libraryImportSummary').textContent,/2 плейлистов/);
  assert.equal(element('libraryImportApply').hidden,false);
  element('libraryImportApply').onclick();
  assert.equal(writes,1,'explicit apply commits exactly once');
  assert.equal(current.playlists.length,2);
  assert.deepEqual(ids(current.likes),['soundcloud:a','bandcamp:b']);
  assert.equal(element('libraryImportApply').hidden,true);
});

test('invalid file preview refuses to apply and keeps library intact',async()=>{
  const ui=fs.readFileSync(path.join(__dirname,'../migration_ui.js'),'utf8');
  const els=new Map();
  const el=id=>{
    if(!els.has(id))els.set(id,{hidden:true,disabled:false,value:'',textContent:'',style:{},files:[],
      addEventListener(){},getClientRects(){return [1]},replaceChildren(){},append(){},focus(){}});
    return els.get(id);
  };
  const context={window:{},document:{getElementById:el,body:{style:{}}},
    LibraryTransfer:transfer,Blob,URL,JSON,Date,setTimeout:()=>{},AbortController};
  vm.createContext(context);
  vm.runInContext(ui,context);
  let writes=0;
  context.window.MigrationUI.mount({
    getLibrary:fixture,commitLibrary:()=>writes++,onChange:()=>{},playTrack:()=>{}
  });
  el('libraryImport').files=[{size:4,text:async()=>'{nope'}];
  await el('libraryImport').onchange();
  assert.equal(writes,0);
  assert.equal(el('libraryImportApply').hidden,true);
  assert.match(el('migrationStatus').textContent,/Копия не импортирована/);
});
