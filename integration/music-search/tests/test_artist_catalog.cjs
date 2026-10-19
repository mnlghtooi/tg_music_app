const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const availability = require('../track_availability.js');

const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');

test('artist overview uses a separate merged endpoint, with original provider card preserved', () => {
  assert.match(html, /\/api\/artists\/merged\?/);
  assert.match(html, /\/api\/entity\?/);
  assert.match(html, /Из других музыкальных сервисов/);
  assert.match(html, /Подлинность оригинальной записи не подтверждена|Совпадения по имени из других сервисов/);
  assert.match(html, /block\.section!=='discovered'/);
  assert.match(html, /aggregation\?\.failed_sources/);
});

test('artist play queue includes all sources once, without blocked or preview-only tracks', () => {
  const start = html.indexOf('function entityTracks(){');
  const end = html.indexOf('function playArtist(', start);
  assert.ok(start !== -1 && end > start);
  const code = html.slice(start, end);
  const t = (source, id, availability) => ({source, id, kind:'track', title:'Roads', artist:'Portishead', availability});
  const collectionOverview = {blocks: [
    {section:'popular',data:[t('soundcloud','1','playable')]},
    {section:'tracks',data:[t('soundcloud','1','playable'),t('soundcloud','2','preview_only')]},
    {section:'discovered',data:[
      t('bandcamp','b','unknown'),t('audius','c','playable'),t('audius','d','blocked')]}
  ]};
  const context={collectionOverview,availability,Map,key:t=>t.source+':'+t.id};
  vm.createContext(context);
  vm.runInContext(code+'; globalThis.queueForArtist=entityTracks;',context);
  const result=context.queueForArtist();
  assert.deepEqual(Array.from(result,t=>t.source+':'+t.id),['soundcloud:1','bandcamp:b','audius:c']);
  assert.equal(collectionOverview.blocks[1].data.length,2,'no saved or provider rows deleted');
});

test('artist discovered entries keep provider links and avoid audio reset on navigation',()=>{
  assert.match(html,/renderResultRows\(list,availability\.catalogView\(block\.data\)\.visible\.slice\(0,20\),false\)/);
  const openCollection = html.slice(html.indexOf('function openCollection('), html.indexOf('function closeCollection('));
  assert.doesNotMatch(openCollection,/audio\.pause\(/);
  assert.doesNotMatch(openCollection,/audio\.removeAttribute\(/);
});

test('unverified grouped artist search rows expose provider alternatives without treating them as original IDs', () => {
  const start = html.indexOf('function renderResultRows(');
  const end = html.indexOf('function sourceName(', start);
  const block = html.slice(start, end);
  assert.match(block, /item\.artist_profiles\.length>1/);
  assert.match(block, /с таким именем · не подтверждены/);
  assert.match(block, /openCollection\(item,title\)/);
  const overview = html.slice(html.indexOf('function renderArtistOverview('), html.indexOf('function renderCollectionContent('));
  assert.match(overview, /Профили с таким именем/);
  assert.match(overview, /не подтверждённые аккаунты/);
  assert.match(overview, /openCollection\(profile,button\)/);
  const load = html.slice(html.indexOf('async function loadCollection('), html.indexOf("$('entityPlay').onclick"));
  assert.match(load, /source:collectionItem\.source/);
  assert.match(load, /id:collectionItem\.id/);
  assert.doesNotMatch(overview, /audio\.pause\(/);
});

test('search cache preserves all original provider profile ids after grouped artist result serialization', () => {
  const line = html.match(/function storeTrack\(t\)\{[^\n]+/)[0];
  const context = {availability};
  vm.createContext(context);
  vm.runInContext(line + '; globalThis.saveGrouped=storeTrack',context);
  const grouped={
    source:'soundcloud',id:'a',kind:'artist',title:'Portishead',
    grouping:'name_only',artist_match_count:2,identity_verified:false,
    artist_profiles:[
      {source:'soundcloud',id:'a',kind:'artist',title:'Portishead'},
      {source:'bandcamp',id:'b',kind:'artist',title:'Portishead'}
    ]
  };
  const saved=JSON.parse(JSON.stringify(context.saveGrouped(grouped)));
  assert.equal(saved.artist_profiles.length,2);
  assert.deepEqual(saved.artist_profiles.map(x=>x.source+':'+x.id),['soundcloud:a','bandcamp:b']);
  assert.equal(saved.grouping,'name_only');
  assert.equal(saved.identity_verified,false);
});

test('artist search rows show entity type, not a misleading single-service label', () => {
  const start = html.indexOf('if([\'artist\',\'playlist\',\'album\'].includes(item.kind))');
  const end = html.indexOf('title.append(name,label)', start);
  assert.ok(start !== -1 && end > start, 'artist search label exists');
  const code = html.slice(start, end);
  assert.match(code, /if\(item\.kind==='artist'\)/);
  assert.match(code, /label\.textContent\+=' · '\+sourceName\(item\.source\)/);
  assert.match(code, /\}else\{label\.textContent\+=' · '\+sourceName\(item\.source\)/);
  assert.match(code, /не подтверждены/);
  assert.doesNotMatch(code, /item\.kind==='artist'\?'SoundCloud'/);
});

test('MusicBrainz check is opt-in and does not claim name-based canonical identity',()=>{
  const view=html.slice(html.indexOf('function renderArtistOverview('),html.indexOf('function renderCollectionContent('));
  assert.match(view,/Проверить профиль в MusicBrainz/);
  assert.match(view,/\/api\/artists\/identity\?/);
  assert.match(view,/info\.identity_verified===true/);
  assert.match(view,/Другие одноимённые профили этим не подтверждены/);
  assert.match(view,/Объединение не выполняется/);
  assert.doesNotMatch(view,/audio\.pause\(/);
  assert.doesNotMatch(view,/audio\.removeAttribute\(/);
  assert.doesNotMatch(view,/localStorage\.setItem/);
  const player=html.slice(html.indexOf('function streamUrl('),html.indexOf('async function api('));
  assert.doesNotMatch(player,/musicbrainz/);
  const storage=html.match(/const KEY='([^']+)'/);
  assert.equal(storage[1],'musiclab_library_v1');
});

test('MusicBrainz UI script compiles without changing source-owned player functions',()=>{
  const start=html.lastIndexOf('<script>');
  const end=html.indexOf('</script>',start);
  assert.ok(start>=0&&end>start);
  assert.doesNotThrow(()=>new vm.Script(html.slice(start+8,end)));
});
