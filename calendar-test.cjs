const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
(async()=>{
const el={innerHTML:'',style:{}};
const context=vm.createContext({console,document:{getElementById:()=>el},setTimeout(){},crypto:require('node:crypto').webcrypto,URLSearchParams,Date,Intl});
const html=fs.readFileSync('calendar-review.html','utf8');
for(const match of html.matchAll(/<script(?: src="([^"]+)")?>([\s\S]*?)<\/script>/g)){
  await vm.runInContext(match[1]?fs.readFileSync(match[1],'utf8'):match[2],context);
}
assert.match(el.innerHTML,/会议/);
for(const expression of ['calendarPage()', 'calendarState.editing={request_key:crypto.randomUUID()};meetingForm()', 'calendarState.editing={cancel:1};meetingForm()', 'meetingMarkup(calendarState.meetings[0])']){
 const markup=vm.runInContext(expression,context);
 for(const match of markup.matchAll(/(?:onclick|oninput|onchange|onkeydown|onsubmit)="([^"]*)"/g))new Function('event',match[1].replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&'));
}
vm.runInContext('calendarState.editing=null',context);
// Render actual resolved response; the demo has no network transport.
await vm.runInContext("api('groups/1/calendar/availability/?start=test').then(value=>{calendarState.availability=value;renderAccount()})",context);
assert.match(el.innerHTML,/未知/);assert.doesNotMatch(el.innerHTML,/class="calendar-slot"/);
assert.equal(vm.runInContext('calendarState.availability.suggestions.length',context),0);
assert.match(html,/所有读写仅内存模拟/);
const settingsMarkup=vm.runInContext('calendarSettings()',context);
assert.match(settingsMarkup,/选定的日历/);
assert.match(settingsMarkup,/至少30天/);
assert.match(settingsMarkup,/本人明确选择永久/);
assert.match(settingsMarkup,/旧授权不会自动续期/);
assert.doesNotMatch(settingsMarkup,/最长30天/);
assert.match(settingsMarkup,/不发送参会者邀请/);assert.doesNotMatch(settingsMarkup,/类别组 #/);assert.match(settingsMarkup,/role="radiogroup"/);
const range=vm.runInContext("availabilityDateRange('2026-10-17','2026-10-17')",context);
assert.equal(new Date(range.end)-new Date(range.start),86400000);
assert.equal(new Date(range.start).getHours(),0);assert.equal(new Date(range.end).getDate(),18);
assert.throws(()=>vm.runInContext("availabilityDateRange('2026-10-18','2026-10-17')",context),/结束日期/);
const queryMarkup=vm.runInContext('calendarPage()',context);
assert.equal((queryMarkup.match(/type="date"/g)||[]).length,1);assert.doesNotMatch(queryMarkup,/找时间|查看忙闲/);assert.match(queryMarkup,/merged-calendar/);assert.doesNotMatch(queryMarkup,/datetime-local/);
assert.equal((vm.runInContext('calendarState.editing={};meetingForm()',context).match(/type="datetime-local"/g)||[]).length,2);
console.log('PASS: inclusive local-date query, reversed range rejection, appointment time inputs preserved.');

vm.runInContext("calendarState.editing=null;calendarState.selectedMembers=[1,2];calendarState.busyLoading=false;calendarState.busyError='';calendarState.availability={members:[{user_id:1,name:'甲',state:'known',busy:[{start:'2026-10-20T09:00:00Z',end:'2026-10-20T10:00:00Z'}]},{user_id:2,name:'乙',state:'unknown',busy:[]}],complete:false};calendarState.meetings=[]",context);
assert.equal(vm.runInContext("calendarSlotState(new Date('2026-10-20T09:30:00Z'),new Date('2026-10-20T10:00:00Z')).busy.length",context),1);
assert.equal(vm.runInContext("calendarSlotState(new Date('2026-10-20T10:00:00Z'),new Date('2026-10-20T10:30:00Z')).known",context),false);
vm.runInContext("pickCalendarSlot('2026-10-20T09:30:00Z','2026-10-20T10:00:00Z')",context);assert.equal(vm.runInContext('calendarState.editing',context),null);
vm.runInContext("pickCalendarSlot('2026-10-20T10:00:00Z','2026-10-20T10:30:00Z')",context);assert.equal(vm.runInContext('calendarState.editing',context),null);assert.match(vm.runInContext('calendarSlotConfirmation()',context),/预约此时段/);vm.runInContext('confirmCalendarSlot()',context);assert.equal(vm.runInContext('calendarState.editing.start',context),'2026-10-20T10:00:00Z');assert.match(vm.runInContext('calendarState.editing.availability_warning',context),/未知/);assert.match(vm.runInContext('meetingForm()',context),/invitations.send/);
vm.runInContext("calendarState.editing=null;calendarState.selectedMembers=[1]",context);assert.equal(vm.runInContext("calendarSlotState(new Date('2026-10-20T10:00:00Z'),new Date('2026-10-20T10:30:00Z')).known",context),true);
vm.runInContext("calendarState.selectedMembers=[];pickCalendarSlot('2026-10-20T10:00:00Z','2026-10-20T10:30:00Z')",context);assert.equal(vm.runInContext('calendarState.editing',context),null);
console.log('PASS: merged busy intervals, unknown coverage, participant filtering, blank-slot prefill and explicit invitation capability gap.');
vm.runInContext("calendarState.loading=true",context);assert.match(vm.runInContext('calendarPage()',context),/role="status"/);
vm.runInContext("calendarState.loading=false;calendarState.loadError='读取失败'",context);assert.match(vm.runInContext('calendarPage()',context),/重试/);
vm.runInContext("calendarState.loadError='';calendarState.busyError='忙闲暂不可用'",context);assert.match(vm.runInContext('calendarPage()',context),/忙闲暂不可用/);assert.match(vm.runInContext('calendarPage()',context),/disabled/);
vm.runInContext("calendarState.busyError='';calendarState.members=[];calendarState.selectedMembers=[];calendarState.meetings=[]",context);assert.match(vm.runInContext('calendarPage()',context),/还没有会议/);
assert.doesNotMatch(vm.runInContext('calendarPage()',context),/id="calendar-share"/);
console.log('PASS: loading/error/retry/empty calendar states and single settings form ownership.');
vm.runInContext("calendarState.connection={status:'active',auth_ready:true,event_group_id:3,available_groups:[{id:3,name:'实验日历',color:'#789670'}]};calendarState.sharing='none'",context);const named=vm.runInContext('calendarSettings()',context);assert.match(named,/实验日历/);assert.doesNotMatch(named,/类别组 #3|核对状态/);
vm.runInContext("calendarState.selectedMembers=[1];calendarState.availability={members:[{user_id:1,name:'甲',state:'partial',busy:[{start:'2026-10-19T10:00:00Z',end:'2026-10-21T12:00:00Z',all_day:true,start_date:'2026-10-20',end_date:'2026-10-21',timezone_uncertain:true}],all_day:[{start:'2026-10-19T10:00:00Z',end:'2026-10-21T12:00:00Z',all_day:true,start_date:'2026-10-20',end_date:'2026-10-21',timezone_uncertain:true}]}]};calendarState.busyLoading=false;calendarState.busyError='';calendarState.editing=null",context);
assert.equal(vm.runInContext("calendarSlotState(new Date('2026-10-20T10:00:00Z'),new Date('2026-10-20T10:30:00Z')).allDay.length",context),1);assert.equal(vm.runInContext("calendarSlotState(new Date('2026-10-20T10:00:00Z'),new Date('2026-10-20T10:30:00Z')).busy.length",context),0);
vm.runInContext("pickCalendarSlot('2026-10-20T10:00:00Z','2026-10-20T10:30:00Z')",context);assert.equal(vm.runInContext('calendarState.editing',context),null);
assert.match(vm.runInContext("allDayCalendarRow([new Date('2026-10-20T00:00:00')],0)",context),/全天占用/);
console.log('PASS: real calendar names, direct visibility choices and separate all-day rendering without freeing busy time.');


console.log('PASS: isolated calendar preview loads, forms/handlers parse, unknown availability prevents free-slot claims.');
})().catch(error=>{console.error(error);process.exitCode=1});
