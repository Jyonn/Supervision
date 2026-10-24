const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let options,posts=0,deletes=0,copied='',selected=false,revoked=[],sequence=0;
const nodes={'invitation-copy-status':{textContent:''},'invitation-link':{select(){selected=true}}};
const ctx=vm.createContext({console,URL:class extends URL{static createObjectURL(){return 'blob:fixture-'+(++sequence)}static revokeObjectURL(v){revoked.push(v)}},URLSearchParams,Blob,Date,setTimeout(){},clearTimeout(){},document:{getElementById:id=>nodes[id],addEventListener(){},querySelector(){return null}},window:{addEventListener(){}},location:{search:''},history:{},sessionStorage:{getItem(){return null},setItem(){}},navigator:{clipboard:{async writeText(v){copied=v}}},UI:{open(o){options=o}},crypto:require('node:crypto').webcrypto});
for(const f of ['markdown.js','avatars.js','account.js','group-workspace.js','chat.js'])vm.runInContext(fs.readFileSync(f,'utf8'),ctx);
ctx.testAPI=async(path,method='GET')=>{if(method==='POST'){posts++;return {id:'fixture-'+posts,join_url:'https://example.test/?invite=fictional-'+posts,expires_at:'2099-01-01T00:00:00Z',card_svg:'<svg/>'}}if(method==='DELETE'){deletes++;return {ok:true}}return path.endsWith('/space/')?{bound:true,verified:true,space_name:'虚构空间',space_slug:'test-lab',space_avatar_url:'https://res.6-79.cn/sermo/avatar/fixture.png'}:[]};
vm.runInContext(`sessionUser={id:1};currentGroupId=1;groupItems=[{id:1,name:'虚构研究组',role:'mentor'}];api=testAPI;message=function(){};invitationPNG=async()=>URL.createObjectURL(new Blob(['fixture']));`,ctx);
const button={disabled:false,isConnected:true};ctx.button=button;
(async()=>{
 await vm.runInContext('openInvitationSettings()',ctx);assert.match(options.html,/@test-lab/);assert.match(options.html,/space-avatar/);assert.doesNotMatch(options.html,/空间编号|重新绑定|invitation-result/);
 const creating=vm.runInContext('makeInvitation(button)',ctx);await vm.runInContext('makeInvitation(button)',ctx);await creating;assert.equal(posts,1);assert.equal(options.kind,'invitation');assert.match(options.html,/readonly value="https:\/\/example.test/);assert.match(options.html,/download="板凳-研究组邀请.png"/);assert.equal((options.html.match(/撤销邀请/g)||[]).length,1);assert.doesNotMatch(options.html,/24 小时|有效至/);
 await vm.runInContext('copyInvitationLink(button)',ctx);assert.equal(copied,'https://example.test/?invite=fictional-1');assert.equal(button.textContent,'已复制');assert.equal(nodes['invitation-copy-status'].textContent,'邀请链接已复制');
 vm.runInContext('navigator.clipboard.writeText=async()=>{throw Error("unavailable")}',ctx);await vm.runInContext('copyInvitationLink(button)',ctx);assert.equal(selected,true);
 await vm.runInContext('revokeInvitation(invitationPreview.id,button)',ctx);assert.equal(deletes,1);assert.match(options.html,/邀请已撤销/);assert.doesNotMatch(options.html,/download=|invitation-link/);assert.equal(vm.runInContext('invitationPreview.join_url',ctx),'');
 await vm.runInContext('makeInvitation(button)',ctx);assert.equal(posts,2);assert.match(options.html,/分享|invitation-share/);
 vm.runInContext('invitationPreview.expires_at="2000-01-01T00:00:00Z";showInvitationResult()',ctx);assert.match(options.html,/邀请已过期/);assert.match(options.html,/onclick="openInvitationSettings\(\)">继续邀请/);assert.doesNotMatch(options.html,/download=|invitation-link/);
 vm.runInContext('disposeInvitationPreview()',ctx);assert.equal(vm.runInContext('invitationPreview',ctx),null);assert.ok(revoked.length>=4);
 const old=options;vm.runInContext('currentGroupId=2;showInvitationResult()',ctx);assert.equal(options,old);
 vm.runInContext('currentGroupId=1;api=testAPI;',ctx);await vm.runInContext('openInvitationSettings()',ctx);
 let release;ctx.pendingInvite=new Promise(r=>release=r);vm.runInContext(`api=async()=>await pendingInvite`,ctx);
 const loading=vm.runInContext('makeInvitation(button)',ctx);options.onClose();const closed=options;
 release({id:'cancelled-stage',join_url:'https://example.test/?invite=fictional-closed',expires_at:'2099-01-01T00:00:00Z',card_svg:'<svg/>'});await loading;assert.equal(options,closed);
 vm.runInContext('disposeInvitationPreview()',ctx);

 console.log('PASS: dedicated share dialog, same-row space identity, guarded generation, link copy/fallback, PNG action, regeneration, revoke/expiry and blob cleanup.');
})().catch(e=>{console.error(e);process.exitCode=1});
