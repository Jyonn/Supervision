const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
(async()=>{
let clock=1800000000000,sequence=0;const timers=new Map(),calls=[];class Clock extends Date{static now(){return clock}}
const ctx=vm.createContext({console,Date:Clock,Intl,URLSearchParams,setTimeout:(fn,ms)=>{const id=++sequence;timers.set(id,{fn,ms});return id},clearTimeout:id=>timers.delete(id),window:{addEventListener(){}},document:{addEventListener(){}},sessionUser:{id:1},screen:'home',groupItems:[],renderAccount(){},message(){},api:async()=>{}});
vm.runInContext(fs.readFileSync('calendar.js','utf8'),ctx);
function pending(){vm.runInContext(`calendarState.connection={pending:true,status:'disconnected',authorization_expires_at:${clock/1000+600}};calendarState.authError='';calendarState.members=[]`,ctx)}
let count=0;ctx.api=async path=>{calls.push(path);if(path==='calendar/authorization/claim/')return ++count===1?{pending:true,poll_interval:1}:{connected:true};if(path==='calendar/connection/')return {status:'active',pending:false};throw Error(path)};
pending();await vm.runInContext('pollCalendarAuthorization()',ctx);assert.equal(count,1);assert.ok([...timers.values()].every(t=>t.ms>=3000));assert.ok(!calls.includes('calendar/authorization/start/'));
clock+=3000;await vm.runInContext('pollCalendarAuthorization()',ctx);assert.equal(count,2);assert.equal(vm.runInContext('calendarState.connection.status',ctx),'active');assert.equal(timers.size,0);
let failures=0;ctx.api=async path=>{if(path==='calendar/authorization/claim/'){failures++;const error=Error('uncertain');error.code='claim_uncertain';throw error}return {pending:false,status:'disconnected'}};
pending();clock+=3000;await vm.runInContext('pollCalendarAuthorization()',ctx);assert.equal(failures,1);assert.match(vm.runInContext('calendarState.authError',ctx),/停止自动重试/);assert.equal(timers.size,0);
let finish;ctx.api=async path=>{if(path==='calendar/authorization/claim/')return new Promise(resolve=>finish=resolve);if(path==='calendar/authorization/cancel/')return {cancelled:true};return {status:'disconnected',pending:false}};
pending();clock+=3000;const inFlight=vm.runInContext('pollCalendarAuthorization()',ctx);await vm.runInContext('cancelCalendarAuthorization()',ctx);finish({connected:true});await inFlight;assert.equal(vm.runInContext('calendarState.connection.status',ctx),'disconnected');assert.equal(timers.size,0);
vm.runInContext('calendarState.connection={pending:true,authorization_expires_at:1}',ctx);await vm.runInContext('pollCalendarAuthorization()',ctx);assert.match(vm.runInContext('calendarState.authError',ctx),/过期/);
const starts=[];ctx.window.open=()=>({opener:{},location:{assign(){}},close(){}});ctx.api=async(path,method,body)=>{if(path==='calendar/authorization/start/'){starts.push(body);return {authorize_url:'https://chronelle.example.test/#/authorize-delegated/synthetic',poll_interval:3}}return {status:'disconnected',pending:false}};
await vm.runInContext('beginCalendarAuthorization(true)',ctx);assert.equal(starts[0].include_calendar_names,true);
await vm.runInContext('beginCalendarAuthorization()',ctx);assert.equal(starts[1].include_calendar_names,undefined);
console.log('PASS: automatic claim respects 3s minimum, resumes existing consent only, stops on uncertainty/expiry and ignores cancelled race.');
})().catch(error=>{console.error(error);process.exitCode=1});
