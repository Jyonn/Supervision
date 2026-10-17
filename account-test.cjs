const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const el={innerHTML:''};const ctx=vm.createContext({console,document:{getElementById:()=>el,addEventListener(){}},window:{addEventListener(){}},setTimeout(){},sessionStorage:{getItem(){return null},setItem(){}},Date});
vm.runInContext(fs.readFileSync('markdown.js','utf8'),ctx);vm.runInContext(fs.readFileSync('calendar.js','utf8'),ctx);vm.runInContext(fs.readFileSync('account.js','utf8').replace(/boot\(\);\s*$/,''),ctx);
vm.runInContext("sessionUser={id:1,name:'本地用户'};activeTask={id:1,title:'任务'};editorState={kind:'report',day:'2026-10-17',owner_id:1,body:'内容'}",ctx);
for(const page of ['authPage()', 'recordEditor()', 'newTaskPage()', 'groupPageReal()', 'overviewPage()', 'profilePage()', 'gravatarChoices()', 'calendarPage()','availabilityMarkup()']){let html=vm.runInContext(page,ctx);for(const match of html.matchAll(/(?:onclick|oninput|onchange|onkeydown|onsubmit)="([^"]*)"/g)){let code=match[1].replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');try{new Function('event',code)}catch(e){throw Error(page+': invalid inline handler: '+code)}}}
console.log('PASS: account page rendering and all generated editor/form handlers parse.');
vm.runInContext(`activeTask={id:1,title:'多人成果',group_id:1,description:'任务说明',can_write:true,can_feedback:true,assignees:[{id:1,name:'本人'},{id:2,name:'同学'}],entries:[{owner_id:1,kind:'report',day:'2026-10-17',body:'# 个人进展\\n第二行'},{owner_id:2,kind:'report',day:'2026-10-16',body:'另一人的记录'},{owner_id:2,kind:'summary',day:'2026-10-17',body:'独立总结'},{owner_id:2,kind:'feedback',day:'2026-10-17',body:'导师反馈'}]}`,ctx);
const detail=vm.runInContext('detailPage()',ctx);
for(const text of ['个人进展','另一人的记录','独立总结','导师反馈','本人','同学'])assert.ok(detail.includes(text));
assert.equal((detail.match(/class="record-disclosure"/g)||[]).length,4);
assert.ok(detail.includes("startEntry('report',1"));assert.ok(!detail.includes("startEntry('report',2"));
assert.ok(detail.includes("startEntry('feedback',2"));assert.ok(!detail.includes("startEntry('summary',2"));
assert.equal(vm.runInContext("recordExcerpt('# 标题'+String.fromCharCode(10)+'正文')",ctx),'标题');
for(const match of detail.matchAll(/(?:onclick|oninput|onchange|onkeydown|onsubmit)="([^"]*)"/g))new Function('event',match[1].replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&'));
console.log('PASS: grouped multi-person records preserve bodies and enforce owner/mentor edit actions.');
vm.runInContext("groupItems=[{id:1,name:'第一组',role:'mentor'},{id:2,name:'第二组',role:'student'}];groupMembers={1:[{id:1,name:'本人',role:'mentor'},{id:2,name:'同学',role:'student'}],2:[]};taskItems=[activeTask];selectedResearchGroup=1",ctx);
let groups=vm.runInContext('groupPageReal()',ctx);assert.ok(groups.includes('个人进展'));assert.ok(groups.includes('1 项可见任务'));assert.ok(groups.includes('添加学生'));
vm.runInContext('selectedResearchGroup=2',ctx);groups=vm.runInContext('groupPageReal()',ctx);assert.ok(!groups.includes('个人进展'));assert.ok(!groups.includes('添加学生'));assert.ok(groups.includes('还没有可见任务'));
for(const match of groups.matchAll(/(?:onclick|onsubmit)="([^"]*)"/g))new Function('event',match[1]);
console.log('PASS: group selection isolates progress, counts visible assignments and keeps mentor-only actions.');


(async()=>{
 const requests=[];
 const production=vm.createContext({console,document:{getElementById:()=>el,addEventListener(){}},window:{SUPERVISION_CONFIG:{apiOrigin:'https://api.abandon.qijiong.work',production:true},addEventListener(){}},fetch:async(url,options)=>{requests.push({url,options});return {ok:true,json:async()=>({csrf:'synthetic-csrf'})}},setTimeout(){},sessionStorage:{},Date});
 vm.runInContext(fs.readFileSync('account.js','utf8').replace(/boot\(\);\s*$/,''),production);
 await vm.runInContext("api('session/')",production);
 await vm.runInContext("api('profile/','PATCH',{name:'Synthetic'})",production);
 assert.equal(requests[0].url,'https://api.abandon.qijiong.work/api/session/');
 assert.equal(requests[0].options.credentials,'include');
 assert.equal(requests[1].options.headers['X-CSRFToken'],'synthetic-csrf');
 assert.equal(vm.runInContext('appPath()',production),'/');
 assert.equal(vm.runInContext("apiURL('/api/avatars/preset/35/')",production),'https://api.abandon.qijiong.work/api/avatars/preset/35/?v=chronelle-v2');
 console.log('PASS: production API origin, credentialed requests, CSRF header and Pages root route.');
})().catch(error=>{console.error(error);process.exitCode=1});
