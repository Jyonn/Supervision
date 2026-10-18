const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const handlers={},calls=[],timers=new Map();let sequence=0,html='',booted=0,rendered=0,storageWrites=0;
const ctx=vm.createContext({console,URL,URLSearchParams,Blob,Date,AbortController,crypto:require('node:crypto').webcrypto,location:{search:'',assign(v){ctx.redirect=v}},history:{},navigator:{},window:{addEventListener(k,f){handlers[k]=f},UI:{open(o){html=o.html},close(){}}},UI:{open(o){html=o.html},close(){}},document:{hidden:false,getElementById(){return null},querySelector(){return null},querySelectorAll(){return []},addEventListener(k,f){handlers[k]=f}},sessionStorage:{getItem(){return null},setItem(){storageWrites++},removeItem(){}},setTimeout(f){timers.set(++sequence,f);return sequence},clearTimeout(id){timers.delete(id)}});
for(const f of ['markdown.js','avatars.js','account.js','group-workspace.js','chat.js'])vm.runInContext(fs.readFileSync(f,'utf8'),ctx);
ctx.rendered=()=>rendered++;ctx.reboot=()=>booted++;
vm.runInContext(`sessionUser={id:1};currentGroupId=10;screen='chat';csrf='fictional-csrf';groupItems=[{id:10,name:'虚构甲组',role:'mentor'},{id:20,name:'虚构乙组',role:'mentor'}];renderAccount=rendered;boot=reboot;message=function(){};`,ctx);
const roomKey='11111111-1111-4111-8111-111111111111';
const message=(id='9007199254740994',content='<script>fictional</script>')=>({id,sender_id:'101',name:'虚构成员',content,created_at:1792300000,unsupported:false,client_message_id:''});
const response=(data,status=200)=>({ok:status<400,status,async json(){return data}});
let mode='ready',sendCount=0,pendingResolve,lastSignal;
ctx.fetch=async(url,options)=>{
 calls.push({url,options});lastSignal=options.signal;const group=new URL(url,'http://localhost').pathname.split('/')[3];
 const envelope={viewer_id:'1',group_id:group};
 if(mode==='deferred'&&url.includes('/events/'))return new Promise(r=>pendingResolve=()=>r(response({...envelope,room_key:roomKey,events:[{id:'8',message_id:'999',message:message('999','旧组绝不能出现')}],next_after:'8',has_more:false})));
 if(mode==='wrong-user')return response({...envelope,viewer_id:'2',enabled:true,connected:true,room:{key:roomKey,title:'不应展示'}});
 if(mode==='revoked')return response({error:'授权已失效',code:'authorization_required'},401);
 if(url.includes('/status/'))return response({...envelope,enabled:mode!=='off',connected:true,self_id:'101',can_configure:true,room:{key:roomKey,title:'虚构群'}});
 if(url.includes('/messages/')&&options.method==='POST'){
  sendCount++;if(mode==='uncertain'&&sendCount===1)return response({error:'结果不明',code:'send_uncertain'},502);
  const body=JSON.parse(options.body);return response({...envelope,room_key:roomKey,message:{...message('10000000000000000',body.content),client_message_id:body.client_message_id}});
 }
 if(url.includes('/messages/'))return response({...envelope,room_key:roomKey,messages:[message()],next_before:'9007199254740994',has_more:false});
 if(url.includes('/events/'))return response({...envelope,room_key:roomKey,events:[{id:'1',message_id:'9007199254740994',message:null}],next_after:'1',has_more:false});
 return response({...envelope,rooms:[]});
};
(async()=>{
 mode='off';await vm.runInContext('openGroupChat()',ctx);assert.equal(vm.runInContext('groupChat.phase',ctx),'off');assert.match(vm.runInContext('chatPage()',ctx),/不会读取或发送消息/);assert.equal(calls.filter(c=>c.options.method==='POST').length,0);
 mode='ready';await vm.runInContext('openGroupChat()',ctx);assert.equal(vm.runInContext('groupChat.messages[0].id',ctx),'9007199254740994');assert.match(vm.runInContext('chatPage()',ctx),/&lt;script&gt;/);assert.doesNotMatch(vm.runInContext('chatMessagesHTML()',ctx),/<script>/);
 await vm.runInContext('pollGroupChat()',ctx);assert.equal(vm.runInContext('groupChat.messages.length',ctx),0);assert.equal(vm.runInContext('groupChat.after',ctx),'1');
 await vm.runInContext('loadChatHistory()',ctx);mode='uncertain';sendCount=0;vm.runInContext("setChatDraft('虚构发送');",ctx);
 await vm.runInContext('sendGroupMessage({preventDefault(){}})',ctx);assert.equal(sendCount,1);const key=vm.runInContext('groupChat.sendKey',ctx);assert.ok(key);assert.equal(vm.runInContext('groupChat.draft',ctx),'虚构发送');
 await vm.runInContext('sendGroupMessage({preventDefault(){}})',ctx);assert.equal(sendCount,2);const posts=calls.filter(c=>c.options.method==='POST');assert.equal(JSON.parse(posts.at(-1).options.body).client_message_id,key);assert.equal(JSON.parse(posts.at(-2).options.body).client_message_id,key);assert.equal(vm.runInContext('groupChat.draft',ctx),'');
 assert.equal(posts.at(-1).options.credentials,'include');assert.equal(posts.at(-1).options.headers['X-CSRFToken'],'fictional-csrf');assert.equal(posts.at(-1).options.headers.Authorization,undefined);assert.equal(storageWrites,0);
 mode='deferred';const waiting=vm.runInContext('pollGroupChat()',ctx);await new Promise(r=>setImmediate(r));const signal=lastSignal;vm.runInContext('stopGroupChat();currentGroupId=20;',ctx);assert.equal(signal.aborted,true);pendingResolve();await waiting;assert.equal(vm.runInContext('groupChat.messages.length',ctx),0);
 mode='ready';await vm.runInContext('openGroupChat()',ctx);vm.runInContext("groupChat.draft='私密草稿';groupChat.sendKey='fictional-key'",ctx);mode='revoked';await vm.runInContext('pollGroupChat()',ctx);assert.equal(vm.runInContext('groupChat.phase',ctx),'blocked');assert.equal(vm.runInContext('groupChat.messages.length',ctx),0);assert.equal(vm.runInContext('groupChat.draft',ctx),'');
 mode='wrong-user';await vm.runInContext('openGroupChat()',ctx);assert.equal(booted,1);assert.equal(vm.runInContext('groupChat.messages.length',ctx),0);
 assert.ok(rendered>0);console.log('PASS: off gate, exact BFF-only calls, escaped/64-bit-safe history, current visibility events, explicit same-key send retry, CSRF, no message storage, abort/stale group guard, revoke and account-change cleanup.');
})().catch(error=>{console.error(error);process.exitCode=1});
