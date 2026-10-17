const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const listeners={},windowListeners={},summary={innerHTML:''};let focused=null,renders=0,keys=0,requests=0;
const start='2026-10-20T14:00:00.000Z',end='2026-10-20T14:30:00.000Z',next='2026-10-20T14:30:00.000Z',nextEnd='2026-10-20T15:00:00.000Z';
function button(value){return {dataset:{slotStart:value},attrs:{},classes:new Set(),classList:{toggle(name,on){on?this.owner.classes.add(name):this.owner.classes.delete(name)}},setAttribute(name,value){this.attrs[name]=value},focus(){focused=this},getClientRects(){return [{}]}}}
const buttons=[button(start),button(next)];for(const b of buttons)b.classList.owner=b;
const confirm={focus(){focused=this}};
const ctx=vm.createContext({console,Date,Intl,URLSearchParams,crypto:{randomUUID(){keys++;return 'synthetic-key'}},document:{addEventListener(name,fn){listeners[name]=fn},querySelectorAll(){return buttons},getElementById(id){return id==='calendar-slot-selection'?summary:id==='confirm-calendar-slot'?confirm:null}},window:{addEventListener(name,fn){windowListeners[name]=fn}},sessionUser:{id:1},screen:'calendar',renderAccount(){renders++},message(){},api(){requests++;throw Error('unexpected network')},escape:String,setTimeout(){},clearTimeout(){}});
vm.runInContext(fs.readFileSync('calendar.js','utf8'),ctx);vm.runInContext("calendarState.members=[{id:1}];calendarState.selectedMembers=[1];calendarState.availability={members:[{user_id:1,name:'虚构',state:'known',busy:[]}]};calendarState.meetings=[]",ctx);
function activate(a=start,b=end,detail=1){ctx.event={detail};ctx.start=a;ctx.end=b;vm.runInContext('activateCalendarSlot(event,start,end)',ctx)}
activate();assert.equal(vm.runInContext('calendarState.editing',ctx),null);assert.equal(keys,0);assert.equal(renders,0);assert.equal(buttons[0].attrs['aria-pressed'],'true');assert.match(summary.innerHTML,/预约此时段/);
activate(next,nextEnd);assert.equal(buttons[0].attrs['aria-pressed'],'false');assert.equal(buttons[1].attrs['aria-pressed'],'true');activate(next,nextEnd);assert.equal(keys,0);assert.equal(vm.runInContext('calendarState.editing',ctx),null);
listeners.keydown({key:'Escape',preventDefault(){}});assert.equal(vm.runInContext('calendarState.selectedSlot',ctx),null);assert.equal(focused,buttons[1]);assert.equal(summary.innerHTML,'');
activate();listeners.pointerdown({target:{closest(){return null}}});assert.equal(vm.runInContext('calendarState.selectedSlot',ctx),null);
const target={closest(selector){return selector==='.week-slot'?buttons[0]:null}};
listeners.pointerdown({target,pointerId:7,clientX:10,clientY:20});listeners.pointermove({pointerId:7,clientX:10,clientY:90});activate();assert.equal(vm.runInContext('calendarState.selectedSlot',ctx),null);assert.equal(keys,0);
listeners.pointercancel({pointerId:7});activate();assert.equal(vm.runInContext('calendarState.selectedSlot',ctx),null);
listeners.pointerdown({target,pointerId:8,clientX:10,clientY:90});activate();assert.equal(vm.runInContext('calendarState.editing',ctx),null);
activate(start,end,0);assert.equal(focused,confirm);windowListeners.popstate();assert.equal(vm.runInContext('calendarState.selectedSlot',ctx),null);
activate(start,end,0);vm.runInContext("calendarState.availability.members[0].busy=[{start:'2026-10-20T14:00:00Z',end:'2026-10-20T14:30:00Z'}];confirmCalendarSlot()",ctx);assert.equal(keys,0);assert.equal(vm.runInContext('calendarState.selectedSlot',ctx),null);
vm.runInContext("calendarState.availability.members[0].busy=[];calendarState.availability.members[0].state='unknown'",ctx);activate(start,end,0);assert.match(summary.innerHTML,/未知/);assert.equal(keys,0);vm.runInContext('confirmCalendarSlot()',ctx);assert.equal(keys,1);assert.equal(renders,1);assert.equal(vm.runInContext('calendarState.editing.start',ctx),start);assert.match(vm.runInContext('calendarState.editing.availability_warning',ctx),/未知/);assert.equal(requests,0);
console.log('PASS: first tap only selects, explicit confirmation opens form, switching/repeat/Escape/outside/back never create; drag/cancel suppressed, keyboard reachable and busy revalidated.');
