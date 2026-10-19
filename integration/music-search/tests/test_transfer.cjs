const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const transfer=require('../library_transfer.js');
const track=(id='1')=>({id,kind:'track',source:'soundcloud',title:'Song',artist:'Artist',permalink:'https://soundcloud.com/artist/song'});
const library=()=>({likes:[],dislikes:[],playlists:[]});
test('validated backup roundtrip and duplicate-free merge preserve existing preferences',()=>{
 const current={likes:[track()],dislikes:[track('2')],playlists:[{id:'p',name:'Old name',tracks:[track()]}]};
 const incoming=transfer.validate({format:'musiclab-library',version:1,library:{likes:[track(),track('2')],dislikes:[track()],playlists:[{id:'p',name:'New name',tracks:[track(),track('3')]}]}});
 const merged=transfer.merge(current,incoming);
 assert.deepEqual(merged.likes.map(t=>t.id),['1']);assert.deepEqual(merged.dislikes.map(t=>t.id),['2']);
 assert.equal(merged.playlists[0].name,'Old name');assert.deepEqual(merged.playlists[0].tracks.map(t=>t.id),['1','3']);
 assert.deepEqual(transfer.merge(merged,incoming),merged);
 assert.equal(current.playlists[0].tracks.length,1);
});
test('malformed, oversized, unknown-source and future backups are rejected',()=>{
 for(const input of [{}, {format:'musiclab-library',version:2,library:library()},
 {format:'musiclab-library',version:1,library:{...library(),likes:[{...track(),source:'spotify'}]}},
 {format:'musiclab-library',version:1,library:{...library(),likes:Array(10001).fill(track())}},
 {format:'musiclab-library',version:1,library:{...library(),playlists:[{id:'x',name:'Empty',tracks:'bad'}]}}])assert.throws(()=>transfer.validate(input));
});
const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
const functions=html.slice(html.indexOf('function telegramShareLink('),html.indexOf('function trackMenuButton('));
test('Telegram shares public track URL and escaped title without private access or tracking data',()=>{
 const context={URL,URLSearchParams};vm.createContext(context);vm.runInContext(functions,context);
 const shared=new URL(context.telegramShareLink({...track(),title:'Song & <title>',permalink:'https://soundcloud.com/a/b?secret=value#fragment'}));
 assert.equal(shared.origin,'https://t.me');assert.equal(shared.searchParams.get('url'),'https://soundcloud.com/a/b');assert.equal(shared.searchParams.get('text'),'Song & <title> — Artist');
 const audius=new URL(context.telegramShareLink({...track(),source:'audius',permalink:'/artist/song'}));assert.equal(audius.searchParams.get('url'),'https://audius.co/artist/song');
 for(const url of ['http://127.0.0.1:8765/open/secret','https://soundcloud.com.evil.test/a/b','javascript:alert(1)','https://user:secret@soundcloud.com/a/b'])assert.throws(()=>context.telegramShareLink({...track(),permalink:url}));
});
test('Telegram Mini App and ordinary-browser actions use the supported sharing link',()=>{
 const opened=[];const context={URL,URLSearchParams,window:{Telegram:{WebApp:{openTelegramLink:url=>opened.push(url)}}},status:()=>{},$:()=>({})};vm.createContext(context);vm.runInContext(functions,context);
 context.shareTrackTelegram(track());assert.equal(opened.length,1);
 context.window={open:(...args)=>{opened.push(args);return null;}};context.shareTrackTelegram(track());assert.match(opened[1][0],/^https:\/\/t.me\/share\/url\?/);assert.equal(opened[1][2],'noopener,noreferrer');
});
