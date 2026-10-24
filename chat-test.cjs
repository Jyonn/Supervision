const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const handlers={},calls=[],timers=new Map();let sequence=0,html='',booted=0,storageWrites=0;
const ctx=vm.createContext({console,URL,URLSearchParams,Blob,Date,AbortController,crypto:require('node:crypto').webcrypto,location:{search:''},history:{},navigator:{},window:{addEventListener(){}},UI:{open(o){html=o.html},close(){}},document:{hidden:false,getElementById(){return null},querySelector(){return null},querySelectorAll(){return []},addEventListener(k,f){handlers[k]=f}},sessionStorage:{getItem(){return null},setItem(){storageWrites++},removeItem(){}},setTimeout(f){timers.set(++sequence,f);return sequence},clearTimeout(id){timers.delete(id)}});
for(const f of ['markdown.js','avatars.js','account.js','group-workspace.js','chat.js'])vm.runInContext(fs.readFileSync(f,'utf8'),ctx);
ctx.reboot=()=>booted++;
vm.runInContext(`sessionUser={id:1};currentGroupId=10;screen='chat';csrf='fictional-csrf';groupItems=[{id:10,name:'虚构甲组',role:'mentor'},{id:20,name:'虚构乙组',role:'mentor'}];renderAccount=function(){};boot=reboot;message=function(){};`,ctx);
const key='fixture-signed-conversation';
const message=(id='9007199254740994',content='<script>fictional</script>')=>({id,sender_id:'101',name:'虚构成员',content,created_at:1792300000,unsupported:false,client_message_id:''});
const response=(data,status=200)=>({ok:status<400,status,async json(){return data}});
let mode='ready',sendCount=0,pendingResolve,lastSignal;
ctx.fetch=async(url,options)=>{
 calls.push({url,options});lastSignal=options.signal;const group=new URL(url,'http://localhost').pathname.split('/')[3],envelope={viewer_id:'1',group_id:group};
 assert.doesNotMatch(url,/oauth|auth\/friendom|api\.sermo/);
 if(mode==='wrong-user')return response({...envelope,viewer_id:'2',connected:true});
 if(url.includes('/status/'))return response({...envelope,enabled:true,connected:mode!=='disconnected',self_id:'101',can_configure:true,space_name:'虚构空间',space_slug:'lab'});
 if(url.includes('/conversations/'))return response({...envelope,rooms:[{id:'17',key,title:'虚构群',group:true}],peers:[{id:'2',name:'学生'}]});
 if(url.includes('/messages/')&&options.method==='POST'){
  sendCount++;if(mode==='uncertain'&&sendCount===1)return response({error:'结果不明',code:'send_uncertain'},502);
  const body=JSON.parse(options.body);return response({...envelope,room_key:key,message:{...message('10000000000000000',body.content),client_message_id:body.client_message_id}});
 }
 if(mode==='deferred'&&url.includes('/messages/'))return new Promise(r=>pendingResolve=()=>r(response({...envelope,room_key:key,messages:[message('999','旧组内容')],has_more:false})));
 if(mode==='revoked')return response({error:'请重新连接',code:'connection_required'},409);
 return response({...envelope,room_key:key,messages:mode==='empty'?[]:[message()],next_before:'9007199254740994',has_more:false});
};
(async()=>{
 mode='disconnected';await vm.runInContext('openGroupChat()',ctx);assert.equal(vm.runInContext('groupChat.phase',ctx),'disconnected');vm.runInContext('openChatConnection()',ctx);assert.match(html,/type="password"/);assert.doesNotMatch(html,/oauth|前往.*授权/);
 mode='ready';await vm.runInContext('openGroupChat()',ctx);assert.match(vm.runInContext('chatPage()',ctx),/虚构群/);await vm.runInContext('selectChatRoom(0)',ctx);
 assert.equal(vm.runInContext('groupChat.messages[0].id',ctx),'9007199254740994');assert.match(vm.runInContext('chatMessagesHTML()',ctx),/&lt;script&gt;/);assert.doesNotMatch(vm.runInContext('chatMessagesHTML()',ctx),/<script>/);
 for(const m of vm.runInContext('chatPage()',ctx).matchAll(/(?:onclick|oninput|onchange|onkeydown|onsubmit)="([^"]*)"/g))new Function('event',m[1].replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&'));
 mode='empty';await vm.runInContext('syncGroupChat()',ctx);assert.equal(vm.runInContext('groupChat.messages.length',ctx),0);
 mode='uncertain';vm.runInContext("setChatDraft('虚构发送')",ctx);await vm.runInContext('sendGroupMessage({preventDefault(){}})',ctx);assert.equal(sendCount,1);const sendKey=vm.runInContext('groupChat.sendKey',ctx);assert.ok(sendKey);assert.equal(vm.runInContext('groupChat.draft',ctx),'虚构发送');
 await vm.runInContext('sendGroupMessage({preventDefault(){}})',ctx);assert.equal(sendCount,2);const posts=calls.filter(c=>c.options.method==='POST');assert.equal(JSON.parse(posts.at(-1).options.body).client_message_id,sendKey);assert.equal(JSON.parse(posts.at(-2).options.body).client_message_id,sendKey);assert.equal(vm.runInContext('groupChat.draft',ctx),'');assert.equal(posts.at(-1).options.headers['X-CSRFToken'],'fictional-csrf');assert.equal(posts.at(-1).options.headers.Authorization,undefined);assert.equal(storageWrites,0);
 mode='deferred';const waiting=vm.runInContext('syncGroupChat()',ctx);await new Promise(r=>setImmediate(r));const signal=lastSignal;vm.runInContext('stopGroupChat();currentGroupId=20',ctx);assert.equal(signal.aborted,true);pendingResolve();await waiting;assert.equal(vm.runInContext('groupChat.messages.length',ctx),0);
 mode='ready';await vm.runInContext('openGroupChat()',ctx);await vm.runInContext('selectChatRoom(0)',ctx);vm.runInContext("groupChat.draft='私密草稿'",ctx);mode='revoked';await vm.runInContext('syncGroupChat()',ctx);assert.equal(vm.runInContext('groupChat.messages.length',ctx),0);assert.equal(vm.runInContext('groupChat.draft',ctx),'');assert.equal(vm.runInContext('groupChat.phase',ctx),'disconnected');
 mode='wrong-user';await vm.runInContext('openGroupChat()',ctx);assert.equal(booted,1);
 console.log('PASS: basic conversations, safe text/large IDs, explicit account form, same-key manual send retry, no browser secret storage, CSRF, snapshot sync, aborted stale responses and disconnect cleanup.');
})().catch(e=>{console.error(e);process.exitCode=1});
