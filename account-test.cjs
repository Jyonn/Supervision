const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const el={innerHTML:''};const ctx=vm.createContext({console,document:{getElementById:()=>el,addEventListener(){}},window:{addEventListener(){}},setTimeout(){},sessionStorage:{getItem(){return null},setItem(){}},Date});
vm.runInContext(fs.readFileSync('markdown.js','utf8'),ctx);vm.runInContext(fs.readFileSync('calendar.js','utf8'),ctx);vm.runInContext(fs.readFileSync('account.js','utf8').replace(/boot\(\);\s*$/,''),ctx);
vm.runInContext("sessionUser={id:1,name:'本地用户'};activeTask={id:1,title:'任务'};editorState={kind:'report',day:'2026-10-17',owner_id:1,body:'内容'}",ctx);
for(const page of ['authPage()', 'recordEditor()', 'newTaskPage()', 'groupPageReal()', 'overviewPage()', 'profilePage()', 'gravatarChoices()', 'calendarPage()','availabilityMarkup()']){let html=vm.runInContext(page,ctx);for(const match of html.matchAll(/(?:onclick|oninput|onchange|onkeydown|onsubmit)="([^"]*)"/g)){let code=match[1].replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');try{new Function('event',code)}catch(e){throw Error(page+': invalid inline handler: '+code)}}}
console.log('PASS: account page rendering and all generated editor/form handlers parse.');
(async()=>{
 const requests=[];
 const production=vm.createContext({console,document:{getElementById:()=>el,addEventListener(){}},window:{SUPERVISION_CONFIG:{apiOrigin:'https://api.supervision.qijiong.work',production:true},addEventListener(){}},fetch:async(url,options)=>{requests.push({url,options});return {ok:true,json:async()=>({csrf:'synthetic-csrf'})}},setTimeout(){},sessionStorage:{},Date});
 vm.runInContext(fs.readFileSync('account.js','utf8').replace(/boot\(\);\s*$/,''),production);
 await vm.runInContext("api('session/')",production);
 await vm.runInContext("api('profile/','PATCH',{name:'Synthetic'})",production);
 assert.equal(requests[0].url,'https://api.supervision.qijiong.work/api/session/');
 assert.equal(requests[0].options.credentials,'include');
 assert.equal(requests[1].options.headers['X-CSRFToken'],'synthetic-csrf');
 assert.equal(vm.runInContext('appPath()',production),'/');
 console.log('PASS: production API origin, credentialed requests, CSRF header and Pages root route.');
})().catch(error=>{console.error(error);process.exitCode=1});
