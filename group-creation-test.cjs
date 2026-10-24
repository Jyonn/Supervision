const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const storage=new Map();let html='',sent,calls=0,resolve;
const button={disabled:false};
const ctx=vm.createContext({console,URL,URLSearchParams,crypto:require('node:crypto').webcrypto,location:{search:''},history:{},document:{getElementById:()=>null,addEventListener(){}},window:{addEventListener(){}},sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},setTimeout(){},clearTimeout(){},FormData:class{constructor(f){this.f=f}get(k){return this.f.elements[k].value}},UI:{open(v){html=v.html},close(){}},Date});
for(const f of ['markdown.js','avatars.js','account.js','group-workspace.js','chat.js'])vm.runInContext(fs.readFileSync(f,'utf8'),ctx);
ctx.form={elements:{name:{value:'虚构新组'},space_slug:{value:'lab'},account_name:{value:'Official'},password:{value:'synthetic-private-password'}},querySelector:()=>button};
ctx.mockAPI=async(path,method,body)=>{calls++;sent=structuredClone(body);return new Promise(r=>resolve=r)};
vm.runInContext(`sessionUser={id:3};api=mockAPI;message=function(){};enterGroup=async function(){};openInvitationSettings=async function(){};`,ctx);
(async()=>{
 vm.runInContext('openCreateGroup()',ctx);assert.match(html,/验证并创建/);assert.match(html,/name="space_slug"/);assert.match(html,/name="account_name"/);assert.match(html,/type="password"/);assert.doesNotMatch(html,/oauth|authorize_url/);
 const pending=vm.runInContext('createGroup({preventDefault(){},currentTarget:form})',ctx);await vm.runInContext('createGroup({preventDefault(){},currentTarget:form})',ctx);
 assert.equal(calls,1);assert.equal(sent.space_slug,'lab');assert.equal(sent.account_name,'Official');assert.equal(sent.password,'synthetic-private-password');assert.equal(ctx.form.elements.password.value,'');
 for(const value of storage.values())assert.doesNotMatch(value,/synthetic-private-password|Official|space_slug/);
 resolve({completed:true,group_id:5});await pending;assert.equal(storage.size,0);
 vm.runInContext('keepCreationDraft(form)',ctx);const old=JSON.parse([...storage.values()][0]).request_key;ctx.form.elements.name.value='换个名字';const next=vm.runInContext('keepCreationDraft(form)',ctx);assert.notEqual(next.request_key,old);
 vm.runInContext('sessionUser={id:4};openCreateGroup()',ctx);assert.doesNotMatch(html,/换个名字/);
 console.log('PASS: explicit official-account form, single submit, password cleared and never stored, account-isolated name-only drafts.');
})().catch(e=>{console.error(e);process.exitCode=1});
