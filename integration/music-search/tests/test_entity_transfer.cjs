const test=require('node:test'),assert=require('node:assert/strict');
const {validate}=require('../library_transfer.js');
test('library backups preserve source-owned artist and album references and discard malformed references',()=>{
const t={id:'3',kind:'track',source:'soundcloud',title:'Song',artist:'Artist',artist_ref:{id:'1',source:'soundcloud',kind:'artist',title:'Artist'},album_ref:{id:'2',source:'soundcloud',kind:'album',title:'Album'}};
const backup={format:'musiclab-library',version:1,library:{likes:[t],dislikes:[],playlists:[]}};
assert.deepEqual(validate(backup).likes[0].album_ref,t.album_ref);assert.deepEqual(validate(backup).likes[0].artist_ref,t.artist_ref);
t.album_ref.source='bandcamp';assert.equal(validate(backup).likes[0].album_ref,null);t.artist_ref.id={bad:true};assert.equal(validate(backup).likes[0].artist_ref,null);
});
