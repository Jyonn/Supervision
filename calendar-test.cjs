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
assert.match(el.innerHTML,/未知/);assert.doesNotMatch(el.innerHTML,/calendar-slot/);
assert.equal(vm.runInContext('calendarState.availability.suggestions.length',context),0);
assert.match(html,/所有读写仅内存模拟/);
const settingsMarkup=vm.runInContext('calendarSettings()',context);
assert.match(settingsMarkup,/选定的类别组/);
assert.match(settingsMarkup,/至少30天/);
assert.match(settingsMarkup,/本人明确选择永久/);
assert.match(settingsMarkup,/旧授权不会自动续期/);
assert.doesNotMatch(settingsMarkup,/最长30天/);
assert.match(settingsMarkup,/不发送参会者邀请/);
const range=vm.runInContext("availabilityDateRange('2026-10-17','2026-10-17')",context);
assert.equal(new Date(range.end)-new Date(range.start),86400000);
assert.equal(new Date(range.start).getHours(),0);assert.equal(new Date(range.end).getDate(),18);
assert.throws(()=>vm.runInContext("availabilityDateRange('2026-10-18','2026-10-17')",context),/结束日期/);
const queryMarkup=vm.runInContext('calendarPage()',context);
assert.equal((queryMarkup.match(/type="date"/g)||[]).length,2);assert.doesNotMatch(queryMarkup,/datetime-local/);
assert.equal((vm.runInContext('calendarState.editing={};meetingForm()',context).match(/type="datetime-local"/g)||[]).length,2);
console.log('PASS: inclusive local-date query, reversed range rejection, appointment time inputs preserved.');
console.log('PASS: isolated calendar preview loads, forms/handlers parse, unknown availability prevents free-slot claims.');
})().catch(error=>{console.error(error);process.exitCode=1});
