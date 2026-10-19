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
