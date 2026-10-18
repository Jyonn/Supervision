/* Provider credentials stay in the BFF. Message bodies/drafts are memory-only. */
let groupChat={phase:'idle',status:null,messages:[],draft:'',sendKey:null,sendText:'',sending:false,error:'',sync:'',before:null,more:false,after:'0',candidates:[]};
let chatEpoch=0,chatTimer=null,chatControllers=new Set(),chatViewer=null,chatGroup=null;
function freshChat(){return {phase:'loading',status:null,messages:[],draft:'',sendKey:null,sendText:'',sending:false,error:'',sync:'',before:null,more:false,after:'0',candidates:[]}}
function stopGroupChat(){++chatEpoch;clearTimeout(chatTimer);chatTimer=null;for(const controller of chatControllers)controller.abort();chatControllers.clear();groupChat=freshChat();chatViewer=null;chatGroup=null}
function chatCurrent(epoch){return epoch===chatEpoch&&chatGroup===currentGroupId&&chatViewer===sessionUser?.id&&screen==='chat'}
async function chatAPI(path,method='GET',body,epoch=chatEpoch){
 const group=chatGroup,user=chatViewer,controller=new AbortController();chatControllers.add(controller);
 try{
  const response=await fetch(apiURL(`/api/groups/${group}/chat/${path}`),{method,credentials:'include',signal:controller.signal,headers:{'Content-Type':'application/json',...(method==='GET'?{}:{'X-CSRFToken':csrf})},...(body?{body:JSON.stringify(body)}:{})});
  const data=await response.json();if(!chatCurrent(epoch)||user!==sessionUser?.id){const error=Error('stale');error.name='AbortError';throw error}
  if(!response.ok){const error=Error(data.error||data.detail||'聊天暂不可用');error.code=data.code;error.status=response.status;throw error}
  if(data.viewer_id!==String(user)||data.group_id!==String(group)){const error=Error('账号或研究组已变化，请重新打开聊天。');error.code='account_changed';throw error}
  return data;
 }finally{chatControllers.delete(controller)}
}
function chatFailure(error,epoch){
 if(error.name==='AbortError'||!chatCurrent(epoch))return;
 if(error.code==='account_changed'){stopGroupChat();if(typeof boot==='function')boot();return}
 if(error.status===401||error.status===403||error.status===404||error.code==='account_changed'){
  groupChat.messages=[];groupChat.draft='';groupChat.sendKey=null;groupChat.phase='blocked';groupChat.error=error.message;renderAccount();return;
 }
 if(['room_changed','room_required','identity_required'].includes(error.code)){groupChat.messages=[];groupChat.phase='blocked';groupChat.error=error.message;renderAccount();return}
 groupChat.error=error.message||'暂时无法同步，请手动重试。';chatPaint();
}
async function openGroupChat(){
 stopGroupChat();if(!sessionUser||!currentGroupId||screen!=='chat')return;
 chatGroup=currentGroupId;chatViewer=sessionUser.id;const epoch=chatEpoch;renderAccount();
 try{
  groupChat.status=await chatAPI('status/','GET',undefined,epoch);if(!chatCurrent(epoch))return;
  const state=groupChat.status;
  groupChat.phase=!state.enabled?'off':state.identity_required?'identity':!state.connected?'disconnected':!state.room?'room':'ready';
  renderAccount();
  if(groupChat.phase==='ready'){await loadChatHistory(false,epoch);if(chatCurrent(epoch)&&groupChat.phase==='ready')scheduleChatPoll(epoch,0)}
 }catch(error){if(chatCurrent(epoch)){if(error.code==='account_changed'){chatFailure(error,epoch);return}groupChat.phase='blocked';groupChat.error=error.message;renderAccount()}}
}
function chatTime(value){return new Date(value*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}
function compareChatIDs(a,b){return a.length-b.length||a.localeCompare(b)}
function chatMessagesHTML(){return groupChat.messages.map(item=>`<li class="chat-message ${item.sender_id===groupChat.status?.self_id?'is-self':''}"><div class="chat-message-meta"><b>${escape(item.name)}</b><time>${chatTime(item.created_at)}</time></div><p>${item.unsupported?'此消息类型请在 Frienden 友间查看。':escape(item.content)}</p></li>`).join('')||'<li class="chat-empty">还没有可见的文字消息。</li>'}
function chatPage(){
 const state=groupChat,status=state.status;
 const header=`<header class="workspace-heading"><div><h1 tabindex="-1">组内聊天</h1><p class="workspace-lede">${escape(currentGroup()?.name||'')} · Frienden 友间</p></div>${status?.connected?'<button onclick="openChatConnection()">我的连接</button>':''}</header>`;
 if(state.phase==='idle'||state.phase==='loading')return header+'<p class="quiet-note" role="status">正在检查聊天连接…</p>';
 if(state.phase==='off')return header+'<section class="chat-state"><h2>聊天接入准备中</h2><p>接口和客户端权限尚未开通。当前不会读取或发送消息。</p></section>';
 if(state.phase==='identity')return header+`<section class="chat-state"><h2>先确认本组的空间身份</h2><p>聊天使用你本人在本组绑定空间的账号，不借用导师身份。</p>${currentGroup()?.role==='mentor'?'<button onclick="openInvitationSettings(this)">查看本组空间</button>':'<p class="quiet-note">请联系导师，使用受邀空间的同一账号加入。</p>'}</section>`;
 if(state.phase==='blocked')return header+`<section class="chat-state" role="alert"><p>${escape(state.error)}</p><button onclick="openGroupChat()">重新检查连接</button>${status?.can_configure&&status?.connected?'<button onclick="configureChatRoom(this)">选择本组群会话</button>':''}</section>`;
 if(state.phase==='disconnected')return header+'<section class="chat-state"><h2>连接你自己的聊天</h2><p>由你在 Frienden 友间同意读取聊天和发送文字；其他研究内容不会自动发到群里。</p><button class="primary" onclick="openChatConnection(this)">连接我的聊天</button></section>';
 if(state.phase==='room')return header+`<section class="chat-state"><h2>尚未选择本组会话</h2><p>仅连接已有的普通群，不会创建群或更改成员。</p>${status.can_configure?'<button class="primary" onclick="configureChatRoom(this)">选择本组群会话</button>':'<p class="quiet-note">请等导师选定本研究组的群会话，并确保你已经加入该群。</p>'}</section>`;
 return header+`<section class="group-chat-panel"><header class="chat-room-heading"><b>${escape(status.room.title)}</b>${status.can_configure?'<button class="text-button" onclick="configureChatRoom(this)">选择会话</button>':''}</header><div class="chat-toolbar"><button class="text-button" id="chat-history" onclick="loadChatHistory(true)" ${state.more?'':'disabled'}>更早消息</button><span id="chat-sync-status" role="status">${escape(state.sync||'通过增量轮询同步')}</span><button class="text-button" onclick="syncGroupChat()">同步</button></div><div class="chat-scroll" id="chat-scroll" role="log" aria-live="off" aria-label="本组聊天消息"><ol id="chat-messages">${chatMessagesHTML()}</ol></div><p class="chat-error" id="chat-error" role="alert">${escape(state.error)}</p><form class="chat-compose" onsubmit="sendGroupMessage(event)"><label class="sr-only" for="chat-draft">发送文字消息</label><textarea id="chat-draft" maxlength="512" rows="2" placeholder="写给本组的话…" oninput="setChatDraft(this.value)" onkeydown="chatComposerKey(event)" ${state.sending?'disabled':''}>${escape(state.draft)}</textarea><div><small>仅发送文字 · ⌘ / Ctrl + Enter 发送</small><button class="primary" id="chat-send" ${state.sending||!state.draft.trim()?'disabled':''}>${state.sending?'发送中…':state.sendKey?'重试发送':'发送'}</button></div></form></section>`;
}
function chatPaint(){
 const list=document.getElementById('chat-messages');if(!list)return;
 const scroll=document.getElementById('chat-scroll'),bottom=scroll&&scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<60;
 list.innerHTML=chatMessagesHTML();if(bottom)scroll.scrollTop=scroll.scrollHeight;
 const status=document.getElementById('chat-sync-status');if(status)status.textContent=groupChat.sync||'通过增量轮询同步';
 const error=document.getElementById('chat-error');if(error)error.textContent=groupChat.error;
 const history=document.getElementById('chat-history');if(history)history.disabled=!groupChat.more||groupChat.reading;
 const send=document.getElementById('chat-send');if(send){send.disabled=groupChat.sending||groupChat.reading||!groupChat.draft.trim();send.textContent=groupChat.sending?'发送中…':groupChat.sendKey?'重试发送':'发送'}
 const input=document.getElementById('chat-draft');if(input)input.disabled=groupChat.sending;
}
function mergeChatMessages(items){
 const byID=new Map(groupChat.messages.map(item=>[item.id,item]));for(const item of items)byID.set(item.id,item);
 groupChat.messages=[...byID.values()].sort((a,b)=>compareChatIDs(a.id,b.id)).slice(-500);
 for(const item of items)if(item.client_message_id&&item.client_message_id===groupChat.sendKey&&groupChat.draft.trim()===groupChat.sendText){groupChat.draft='';groupChat.sendKey=null;const input=document.getElementById('chat-draft');if(input)input.value=''}
}
async function loadChatHistory(older=false,epoch=chatEpoch){
 if(!chatCurrent(epoch)||groupChat.phase!=='ready'||groupChat.reading||groupChat.sending)return;
 groupChat.reading=true;chatPaint();
 const key=groupChat.status.room.key,before=older?groupChat.before:null;
 try{
  const data=await chatAPI('messages/?room_key='+encodeURIComponent(key)+(before?'&before='+before:''),'GET',undefined,epoch);
  if(data.room_key!==key){const error=Error('会话已变化，请重新加载。');error.code='room_changed';throw error}
  mergeChatMessages(data.messages);groupChat.before=data.next_before;groupChat.more=data.has_more&&groupChat.messages.length<500;groupChat.error='';chatPaint();
 }catch(error){chatFailure(error,epoch)}finally{if(chatCurrent(epoch)){groupChat.reading=false;chatPaint()}}
}
function scheduleChatPoll(epoch,delay=5000){clearTimeout(chatTimer);if(chatCurrent(epoch)&&groupChat.phase==='ready'&&!document.hidden)chatTimer=setTimeout(()=>pollGroupChat(epoch),delay)}
async function pollGroupChat(epoch=chatEpoch){
 if(!chatCurrent(epoch)||groupChat.phase!=='ready'||document.hidden)return;
 if(groupChat.reading||groupChat.sending){scheduleChatPoll(epoch,500);return}
 groupChat.reading=true;chatPaint();
 const key=groupChat.status.room.key;
 try{
  const data=await chatAPI('events/?room_key='+encodeURIComponent(key)+'&after='+groupChat.after,'GET',undefined,epoch);
  if(data.room_key!==key){const error=Error('会话已变化，请重新加载。');error.code='room_changed';throw error}
  const floor=groupChat.messages[0]?.id;let received=0;
  for(const event of data.events){if(event.message){if(!floor||compareChatIDs(event.message.id,floor)>=0){mergeChatMessages([event.message]);received++}}else groupChat.messages=groupChat.messages.filter(item=>item.id!==event.message_id)}
  groupChat.after=data.next_after;groupChat.error='';groupChat.sync=data.has_more?'正在追赶增量记录…':received?'消息已同步':'已同步';chatPaint();scheduleChatPoll(epoch,data.has_more?500:5000);
 }catch(error){chatFailure(error,epoch);scheduleChatPoll(epoch,10000)}finally{if(chatCurrent(epoch)){groupChat.reading=false;chatPaint()}}
}
function syncGroupChat(){clearTimeout(chatTimer);return pollGroupChat()}
function setChatDraft(value){groupChat.draft=value;if(groupChat.sendText!==value.trim()){groupChat.sendKey=null;groupChat.sendText=''}const button=document.getElementById('chat-send');if(button){button.disabled=groupChat.sending||groupChat.reading||!value.trim();button.textContent=groupChat.sendKey?'重试发送':'发送'}}
function chatComposerKey(event){if((event.ctrlKey||event.metaKey)&&event.key==='Enter'&&!event.isComposing){event.preventDefault();sendGroupMessage(event)}}
async function sendGroupMessage(event){
 event.preventDefault();if(groupChat.phase!=='ready'||groupChat.sending||groupChat.reading||!groupChat.draft.trim())return;
 const epoch=chatEpoch,text=groupChat.draft.trim(),roomKey=groupChat.status.room.key;
 if(!groupChat.sendKey||groupChat.sendText!==text){groupChat.sendKey=crypto.randomUUID();groupChat.sendText=text}
 const key=groupChat.sendKey;groupChat.sending=true;groupChat.error='';chatPaint();
 try{
  const data=await chatAPI('messages/','POST',{content:text,client_message_id:key,room_key:roomKey},epoch);
  if(data.room_key!==roomKey){const error=Error('会话已变化，请重新加载。');error.code='room_changed';throw error}
  mergeChatMessages([data.message]);groupChat.draft='';groupChat.sendKey=null;const input=document.getElementById('chat-draft');if(input)input.value='';
 }catch(error){chatFailure(error,epoch)}finally{if(chatCurrent(epoch)){groupChat.sending=false;chatPaint()}}
}
function openChatConnection(trigger){
 if(!groupChat.status?.enabled)return;
 UI.open({title:'我的聊天连接',kind:'dialog',trigger,html:groupChat.status.connected?`<div class="workspace-form"><p>仅用于本组的聊天读取与文字发送。</p><p class="quiet-note">有效至 ${new Date(groupChat.status.expires_at).toLocaleString('zh-CN')}</p><button onclick="disconnectGroupChat(this)">断开我的连接</button></div>`:'<form class="workspace-form" onsubmit="connectGroupChat(event)"><p>将在 Frienden 友间申请：</p><ul><li>读取本人在受邀空间参与的聊天</li><li>以本人身份发送文字</li></ul><label class="chat-consent"><input type="checkbox" required>我会亲自确认空间账号和授权范围。</label><button class="primary">前往 Frienden 友间授权</button></form>'});
}
async function connectGroupChat(event){event.preventDefault();const button=event.currentTarget.querySelector('button');button.disabled=true;const epoch=chatEpoch;try{const data=await chatAPI('connection/','POST',{consent:true},epoch);location.assign(data.authorize_url)}catch(error){chatFailure(error,epoch);button.disabled=false;message(error.message)}}
async function disconnectGroupChat(button){button.disabled=true;const epoch=chatEpoch;try{const data=await chatAPI('connection/','DELETE',{},epoch);UI.close();await openGroupChat();message(data.provider_revoked===false?'本地连接已移除；提供方撤销未确认，请在 Frienden 友间检查授权。':'我的聊天连接已断开。')}catch(error){chatFailure(error,epoch);button.disabled=false}}
async function configureChatRoom(trigger){
 const epoch=chatEpoch;
 try{const data=await chatAPI('room/','GET',undefined,epoch);groupChat.candidates=data.rooms;
  UI.open({title:'选择本组群会话',kind:'dialog',trigger,html:data.rooms.length?`<form class="workspace-form" onsubmit="saveChatRoom(event)"><label class="form-label">普通群会话<select name="chat_id" required>${data.rooms.map(item=>`<option value="${escape(item.id)}">${escape(item.title)} · ${item.members.length} 人</option>`).join('')}</select></label><p class="quiet-note">仅列出当前成员均已关联本研究组空间身份的普通群。选择不会创建群或加入成员。</p><button class="primary">使用此群会话</button></form>`:'<p class="quiet-note">没有符合条件的普通群。请先在 Frienden 友间建立研究组群，并加入本组成员；然后重新检查。</p>'});
 }catch(error){chatFailure(error,epoch)}
}
async function saveChatRoom(event){event.preventDefault();const button=event.currentTarget.querySelector('button');button.disabled=true;const epoch=chatEpoch;try{await chatAPI('room/','POST',{chat_id:new FormData(event.currentTarget).get('chat_id')},epoch);UI.close();await openGroupChat()}catch(error){chatFailure(error,epoch);button.disabled=false}}
document.addEventListener('visibilitychange',()=>{if(screen!=='chat')return;if(document.hidden){clearTimeout(chatTimer);for(const controller of chatControllers)controller.abort()}else openGroupChat()});
