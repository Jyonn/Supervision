const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const el={innerHTML:''};const handlers={};let url='http://127.0.0.1:8003/app/';const location={get search(){return new URL(url).search},assign(v){url=new URL(v,url).href}};const history={state:null,pushState(s,_,u){this.state=s;url=new URL(u,url).href},replaceState(s,_,u){this.state=s;url=new URL(u,url).href}};
const ctx=vm.createContext({console,URL,URLSearchParams,location,history,Blob,document:{getElementById:()=>el,querySelector(){return null},querySelectorAll(){return []},addEventListener(){}},window:{history,addEventListener(k,v){handlers[k]=v},UI:{close(){},announce(){}}},setTimeout(){},sessionStorage:{getItem(){return null},setItem(){}},Date});
for(const f of ['markdown.js','avatars.js','calendar.js','account.js','group-workspace.js'])vm.runInContext(fs.readFileSync(f,'utf8'),ctx);
vm.runInContext(`sessionUser={id:1,name:'虚构导师'};groupItems=[{id:10,name:'甲组',role:'mentor'},{id:20,name:'乙组',role:'student'}];currentGroupId=10;groupMembers={10:[{id:1,name:'导师',role:'mentor'}]};taskItems=[{id:8,group_id:10,title:'甲组任务',can_write:true,assignees:[{id:1,name:'导师'}],entries:[]}];screen='home'`,ctx);
for(const fn of ['groupChooser()','overviewPage()','membersPage()','activityPage()','roadmapPage()','chatPage()','newTaskPage()','joinPage()']){const html=vm.runInContext(fn,ctx);for(const m of html.matchAll(/(?:onclick|oninput|onchange|onkeydown|onsubmit)="([^"]*)"/g))new Function('event',m[1].replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&'));}
assert.match(vm.runInContext('overviewPage()',ctx),/甲组任务/);
assert.doesNotMatch(vm.runInContext('newTaskPage()',ctx),/<select name="group_id"/);
assert.match(vm.runInContext('routeURL("tasks")',ctx),/group=10&tab=tasks/);
vm.runInContext("screen='detail';activeTask={id:8};editorState={kind:'report',day:'2026-10-18',owner_id:1,body:'未保存研究记录'};openProfile();",ctx);history.replaceState(null,'','/app/?group=10&tab=detail&task=8');handlers.popstate();assert.equal(vm.runInContext('editorState.body',ctx),'未保存研究记录');

vm.runInContext("currentGroupId=20;screen='tasks';renderAccount()",ctx);assert.doesNotMatch(el.innerHTML,/新建任务/);assert.doesNotMatch(el.innerHTML,/<select aria-label="研究组"/);
vm.runInContext('resetGroupContext()',ctx);assert.equal(vm.runInContext('taskItems.length',ctx),0);assert.equal(vm.runInContext('calendarState.meetings.length',ctx),0);
(async()=>{
 vm.runInContext(`api=async function(path){if(path==='groups/')return [{id:10,name:'甲组',role:'mentor'}];throw Error('unexpected '+path)};currentGroupId=999;`,ctx);
 await vm.runInContext('loadWorkspace()',ctx);assert.match(vm.runInContext('workspaceError',ctx),/没有访问权限/);assert.equal(vm.runInContext('currentGroupId',ctx),999);assert.equal(vm.runInContext('taskItems.length',ctx),0);
 let release;ctx.delayed=new Promise(r=>release=r);vm.runInContext(`workspaceError='';currentGroupId=10;api=async function(path){if(path==='groups/')return [{id:10,name:'甲组',role:'mentor'}];if(path.startsWith('tasks/'))return await delayed;if(path.endsWith('space/'))return {bound:false};return []};`,ctx);
 const loading=vm.runInContext('loadWorkspace()',ctx);await new Promise(r=>setImmediate(r));vm.runInContext('resetGroupContext();currentGroupId=20;',ctx);release([{id:8,title:'过时甲组任务'}]);await loading;assert.equal(vm.runInContext('taskItems.length',ctx),0);
 console.log('PASS: group route, contextual forms, student actions, inaccessible deep-link, cleared state and late old-group response isolation; generated handlers parse.');
})().catch(e=>{console.error(e);process.exitCode=1});
