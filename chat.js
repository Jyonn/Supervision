/* Provider credentials stay in the BFF. Message bodies/drafts are memory-only. */
let groupChat={phase:'idle',status:null,messages:[],draft:'',sendKey:null,sendText:'',sending:false,error:'',sync:'',before:null,more:false,after:'0',candidates:[]};
let collaborationData=null,collaborationBusy=false;
let chatEpoch=0,chatTimer=null,chatControllers=new Set(),chatViewer=null,chatGroup=null;
function freshChat(){return {phase:'loading',status:null,messages:[],draft:'',sendKey:null,sendText:'',sending:false,error:'',sync:'',before:null,more:false,after:'0',candidates:[]}}
function stopGroupChat(){collaborationData=null;collaborationBusy=false;++chatEpoch;clearTimeout(chatTimer);chatTimer=null;for(const controller of chatControllers)controller.abort();chatControllers.clear();groupChat=freshChat();chatViewer=null;chatGroup=null}
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
 const header=`<header class="workspace-heading"><div><h1 tabindex="-1">组内聊天</h1><p class="workspace-lede">${escape(currentGroup()?.name||'')} · Frienden 友间</p></div>${status?.connected?'<div class="chat-header-actions"><button onclick="openCollaboration(this)">会话与通讯</button><button onclick="openChatConnection()">我的连接</button></div>':''}</header>`;
 if(state.phase==='idle'||state.phase==='loading')return header+'<p class="quiet-note" role="status">正在检查聊天连接…</p>';
 if(state.phase==='off')return header+'<section class="chat-state"><h2>聊天接入准备中</h2><p>接口和客户端权限尚未开通。当前不会读取或发送消息。</p></section>';
 if(state.phase==='identity')return header+`<section class="chat-state"><h2>先确认本组的空间身份</h2><p>聊天使用你本人在本组绑定空间的账号，不借用导师身份。</p>${currentGroup()?.role==='mentor'?'<button onclick="openInvitationSettings(this)">查看本组空间</button>':'<p class="quiet-note">请联系导师，使用受邀空间的同一账号加入。</p>'}</section>`;
 if(state.phase==='blocked')return header+`<section class="chat-state" role="alert"><p>${escape(state.error)}</p><button onclick="openGroupChat()">重新检查连接</button>${status?.can_configure&&status?.connected?'<button onclick="configureChatRoom(this)">选择本组群会话</button>':''}</section>`;
 if(state.phase==='disconnected')return header+'<section class="chat-state"><h2>连接你自己的聊天</h2><p>由你在 Frienden 友间同意聊天与通讯权限；其他研究内容不会自动发到群里。</p><button class="primary" onclick="openChatConnection(this)">连接我的聊天</button></section>';
 if(state.phase==='room')return header+`<section class="chat-state"><h2>尚未选择本组会话</h2><p>可以查看组内成员的私聊与群会话，或由有资格的导师创建研究组群。</p><button class="primary" onclick="openCollaboration(this)">打开会话与通讯</button></section>`;
 return header+`<section class="group-chat-panel"><header class="chat-room-heading"><b>${escape(status.room.title)}</b>${status.room.allowed_actions?'<button class="text-button" onclick="openConversationSettings(this)">会话设置</button>':''}${status.can_configure?'<button class="text-button" onclick="configureChatRoom(this)">设为本组默认会话</button>':''}</header><div class="chat-toolbar"><button class="text-button" id="chat-history" onclick="loadChatHistory(true)" ${state.more?'':'disabled'}>更早消息</button><span id="chat-sync-status" role="status">${escape(state.sync||'通过增量轮询同步')}</span><button class="text-button" onclick="syncGroupChat()">同步</button></div><div class="chat-scroll" id="chat-scroll" role="log" aria-live="off" aria-label="当前会话消息"><ol id="chat-messages">${chatMessagesHTML()}</ol></div><p class="chat-error" id="chat-error" role="alert">${escape(state.error)}</p><form class="chat-compose" onsubmit="sendGroupMessage(event)"><label class="sr-only" for="chat-draft">发送文字消息</label><textarea id="chat-draft" maxlength="512" rows="2" placeholder="写一条消息…" oninput="setChatDraft(this.value)" onkeydown="chatComposerKey(event)" ${state.sending?'disabled':''}>${escape(state.draft)}</textarea><div><small>仅发送文字 · ⌘ / Ctrl + Enter 发送</small><button class="primary" id="chat-send" ${state.sending||!state.draft.trim()?'disabled':''}>${state.sending?'发送中…':state.sendKey?'重试发送':'发送'}</button></div></form></section>`;
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
 UI.open({title:'我的聊天连接',kind:'dialog',trigger,html:groupChat.status.connected?`<div class="workspace-form"><p>仅用于本组映射成员之间的聊天与通讯；群操作仍受你在 Frienden 友间的实际角色限制。</p><p class="quiet-note">有效至 ${new Date(groupChat.status.expires_at).toLocaleString('zh-CN')}</p><button onclick="disconnectGroupChat(this)">断开我的连接</button></div>`:'<form class="workspace-form" onsubmit="connectGroupChat(event)"><p>将在 Frienden 友间申请：</p><ul><li>聊天：读取、文字收发、好友私聊与建群；按实际资格管理群、置顶与已读。</li><li>通讯：读取组内好友、精确查找、发起及接受或拒绝好友申请。</li><li>已有授权不会自动增加权限；本人重新同意后生效，不索取手机号数据。</li></ul><label class="chat-consent"><input type="checkbox" required>我会亲自确认空间账号和授权范围。</label><button class="primary">前往 Frienden 友间授权</button></form>'});
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

let collaborationDialogVersion=0;
async function collaborationRequest(body,epoch=chatEpoch){return chatAPI('collaboration/',body?'POST':'GET',body,epoch)}
function collaborationListHTML(data){
 const friendIDs=new Set(data.friends.map(r=>r.id));
 return `<div class="collaboration-directory"><section><h3>本组会话</h3>${data.conversations.map((r,i)=>`<button class="context-row" onclick="selectConversation(${i},this)"><span>${escape(r.title)}<small>${r.group?'群聊':'私聊'} · ${r.members.length} 人</small></span><span aria-hidden="true">›</span></button>`).join('')||'<p class="quiet-note">还没有符合本组成员范围的会话。</p>'}${data.create_group?'<button onclick="createConversationForm(this)">创建本组群聊</button>':''}</section><section><h3>研究组成员</h3>${data.peers.map((r,i)=>`<div class="collaboration-person"><span>${escape(r.name)}</span><button onclick="collaborationPeerAction(${i},'${friendIDs.has(r.id)?'direct':'friend_request'}',this)">${friendIDs.has(r.id)?'私聊':'添加好友'}</button></div>`).join('')||'<p class="quiet-note">暂无已关联空间身份的其他组员。</p>'}</section><section><h3>好友申请</h3>${data.requests.map((r,i)=>`<div class="collaboration-person"><span>${escape(r.peer.name)}<small>${r.outgoing?'已发出申请':'收到申请'} · ${r.status===0?'待处理':r.status===1?'已接受':'已处理'}</small></span>${!r.outgoing&&r.status===0?`<button onclick="respondCollaborationFriend(${i},true,this)">接受</button><button onclick="respondCollaborationFriend(${i},false,this)">拒绝</button>`:''}</div>`).join('')||'<p class="quiet-note">暂无组内好友申请。</p>'}</section>${data.invitations.length?`<section><h3>本组群邀请</h3>${data.invitations.map((r,i)=>`<div class="collaboration-person"><span>${escape(r.title)}</span><button onclick="respondCollaborationInvite(${i},true,this)">加入</button><button onclick="respondCollaborationInvite(${i},false,this)">拒绝</button></div>`).join('')}</section>`:''}<form class="workspace-form" onsubmit="searchCollaborationPeer(event)"><label class="form-label">精确查找本组成员的友间账号名<input name="name" required maxlength="20" autocomplete="off"></label><button>查找</button><p id="collaboration-search-result" role="status"></p></form><p class="auth-error" id="collaboration-error" role="alert"></p></div>`;
}
async function openCollaboration(trigger){
 if(collaborationBusy)return;const epoch=chatEpoch,version=++collaborationDialogVersion;
 collaborationBusy=true;if(trigger)trigger.disabled=true;
 try{const data=await collaborationRequest(undefined,epoch);if(!chatCurrent(epoch)||version!==collaborationDialogVersion)return;collaborationData=data;
  UI.open({title:'会话与通讯',kind:'dialog',trigger,onClose(){++collaborationDialogVersion},html:collaborationListHTML(data)});
 }catch(error){chatFailure(error,epoch)}finally{if(chatCurrent(epoch))collaborationBusy=false;if(trigger)trigger.disabled=false}
}
async function runCollaboration(body,button,after){
 if(collaborationBusy)return;const epoch=chatEpoch;collaborationBusy=true;if(button)button.disabled=true;
 const form=button?.closest?.('form');if(form)for(const el of form.elements)el.disabled=true;
 try{const result=await collaborationRequest(body,epoch);if(!chatCurrent(epoch))return;
  groupChat.error='';if(after)await after(result,epoch);else{UI.close();collaborationBusy=false;await openCollaboration()}
 }catch(error){if(!chatCurrent(epoch))return;if(error.status===401||error.status===403||error.status===404){UI.close();chatFailure(error,epoch)}else{
  const target=document.getElementById('collaboration-error');if(target)target.textContent=error.code==='send_uncertain'?'操作结果尚未确认，请先刷新检查，不要重复提交。':error.message;else message(error.message);
 }}finally{if(chatCurrent(epoch)){collaborationBusy=false;if(form)for(const el of form.elements)el.disabled=false;if(button)button.disabled=false}}
}
async function activateConversation(value,epoch=chatEpoch){
 if(!chatCurrent(epoch))return;
 // Abort in-flight reads and discard the old conversation's cursor, messages and draft.
 for(const controller of chatControllers)controller.abort();clearTimeout(chatTimer);
 collaborationBusy=false;++chatEpoch;const next=chatEpoch,status=groupChat.status;groupChat=freshChat();groupChat.status={...status,room:value};groupChat.phase='ready';collaborationData=null;UI.close();renderAccount();
 await loadChatHistory(false,next);if(chatCurrent(next)&&groupChat.phase==='ready')scheduleChatPoll(next,0);
}
async function selectConversation(index,button){const item=collaborationData?.conversations[index];if(item)return runCollaboration({action:'select',key:item.key},button,(r,e)=>activateConversation(r.conversation,e))}
function collaborationPeerAction(index,action,button){
 const peer=collaborationData?.peers[index];if(!peer)return;
 if(action==='direct')return runCollaboration({action,peer_user_id:peer.id},button,(r,e)=>activateConversation(r.conversation,e));
 if(action!=='friend_request')return;
 UI.open({title:'添加好友',kind:'dialog',trigger:button,html:`<form class="workspace-form" onsubmit="confirmCollaborationFriend(event,'${escape(peer.id)}')"><p>以你本人身份向 ${escape(peer.name)} 发出好友申请。Frienden 友间的协管自动接受规则可能立即建立关系。</p><button class="primary">确认发出申请</button><p class="auth-error" id="collaboration-error" role="alert"></p></form>`});
}
function confirmCollaborationFriend(event,id){event.preventDefault();return runCollaboration({action:'friend_request',peer_user_id:id},event.currentTarget.querySelector('button'))}
function respondCollaborationFriend(index,accept,button){const row=collaborationData?.requests[index];if(row)return runCollaboration({action:'friend_respond',request_id:row.id,accept},button)}
function respondCollaborationInvite(index,accept,button){const row=collaborationData?.invitations[index];if(row)return runCollaboration({action:'invitation',key:row.key,accept},button)}
function createConversationForm(trigger){
 if(!collaborationData?.create_group)return;
 const friends=new Set(collaborationData.friends.map(r=>r.id));
 UI.open({title:'创建研究组群聊',kind:'dialog',trigger,html:`<form class="workspace-form" onsubmit="submitConversationCreate(event)"><label class="form-label">群名称<input name="title" maxlength="50" value="${escape(currentGroup()?.name||'研究讨论')}" required></label><fieldset><legend>邀请本组好友（可留空）</legend>${collaborationData.peers.filter(r=>friends.has(r.id)).map(r=>`<label class="chat-consent"><input type="checkbox" name="user_ids" value="${escape(r.id)}">${escape(r.name)}</label>`).join('')||'<p class="quiet-note">可先创建仅你自己的群，再由本人添加本组好友。</p>'}</fieldset><p class="quiet-note">将在 Frienden 友间真实建群；添加成员按其规则执行，可能直接加入。</p><button class="primary">确认创建群聊</button><p class="auth-error" id="collaboration-error" role="alert"></p></form>`});
}
function submitConversationCreate(event){event.preventDefault();const data=new FormData(event.currentTarget);return runCollaboration({action:'create_group',title:data.get('title'),user_ids:data.getAll('user_ids')},event.currentTarget.querySelector('button'),(r,e)=>activateConversation(r.conversation,e))}
function openConversationSettings(trigger){
 const room=groupChat.status?.room;if(!room?.allowed_actions)return;
 const labels={invite:'邀请成员',remove:'移除成员',transfer_owner:'转让群主',rename:'修改群名',background:'修改群背景',mute:'限时禁言',unmute:'解除禁言',leave:'退出此群'};
 UI.open({title:'会话设置',kind:'dialog',trigger,html:`<div class="workspace-form"><p>${escape(room.title)} · ${room.members.length} 人</p><button onclick="runCollaboration({action:'read',key:groupChat.status.room.key},this,()=>{UI.close();message('已标为已读。')})">标为已读</button><button onclick="conversationPreference(this,true)">置顶我的会话</button><button onclick="conversationPreference(this,false)">取消我的置顶</button>${room.allowed_actions.map(action=>`<button onclick="conversationActionForm('${action}',this)">${labels[action]||''}</button>`).join('')}<p class="auth-error" id="collaboration-error" role="alert"></p></div>`});
}
function conversationPreference(button,pinned){return runCollaboration({action:'pinned',key:groupChat.status.room.key,pinned},button,()=>{UI.close();message(pinned?'已置顶。':'已取消置顶。')})}
async function conversationActionForm(action,trigger){
 const room=groupChat.status?.room;if(!room?.allowed_actions?.includes(action))return;
 const epoch=chatEpoch;
 let data=collaborationData;
 if(action==='invite'){try{data=await collaborationRequest(undefined,epoch)}catch(e){chatFailure(e,epoch);return}}
 if(!chatCurrent(epoch))return;
 const own=groupChat.status.self_id,existing=new Set(room.members.map(r=>r.id));
 const targets=action==='invite'?data.peers.filter(r=>data.friends.some(f=>f.id===r.id)&&!existing.has(r.id)):room.members.filter(r=>r.id!==own&&(action!=='remove'&&action!=='mute'||r.role!==1));
 let fields='';
 if(['invite','remove'].includes(action))fields=`<fieldset><legend>选择本组成员</legend>${targets.map(r=>`<label class="chat-consent"><input type="checkbox" name="user_ids" value="${escape(r.id)}">${escape(r.name)}</label>`).join('')||'<p class="quiet-note">没有符合条件的目标。</p>'}</fieldset>`;
 if(['transfer_owner','mute','unmute'].includes(action))fields=`<label class="form-label">选择成员<select name="peer_user_id" required><option value="">请选择</option>${targets.map(r=>`<option value="${escape(r.id)}">${escape(r.name)}</option>`).join('')}</select></label>`;
 if(action==='rename')fields=`<label class="form-label">群名称<input name="title" maxlength="50" value="${escape(room.title)}" required></label>`;
 if(action==='background')fields='<label class="form-label">群背景<select name="background_theme"><option value="">默认</option><option value="paper">纸张</option><option value="mint">薄荷</option><option value="comic">漫画</option><option value="bauhaus">包豪斯</option><option value="dragon">龙纹</option><option value="zen">禅意</option><option value="mosaic">马赛克</option><option value="newsprint">报纸</option><option value="aurora-sky">极光</option><option value="hologram">全息</option><option value="frienden-night">友间之夜</option><option value="frienden-garden">友间花园</option><option value="starry-night">星夜</option><option value="noir-film">黑白电影</option></select></label><p class="quiet-note">仅可使用你已解锁的背景，未解锁的选择会被友间拒绝。</p>';
 if(action==='mute')fields+='<label class="form-label">时长<select name="duration"><option value="10s">10 秒</option><option value="2m">2 分钟</option><option value="10m">10 分钟</option><option value="1h">1 小时</option><option value="6h">6 小时</option><option value="1d">1 天</option><option value="7d">7 天</option></select></label>';
 const warnings={invite:'邀请可能直接将选中的好友加入群聊。',remove:'选中的成员将失去此群访问权限。',transfer_owner:'转让后你将失去群主权限。',leave:'你将退出此群；你的板凳研究组成员身份保留。'};
 UI.open({title:'确认群操作',kind:'dialog',trigger,html:`<form class="workspace-form" onsubmit="submitConversationAction(event,'${action}')">${warnings[action]?`<p>${warnings[action]}</p>`:''}${fields}<button class="primary">确认执行</button><p class="auth-error" id="collaboration-error" role="alert"></p></form>`});
}
function submitConversationAction(event,action){
 event.preventDefault();const form=event.currentTarget,data=new FormData(form),body={action,key:groupChat.status.room.key};
 if(['invite','remove'].includes(action))body.user_ids=data.getAll('user_ids');
 for(const key of ['peer_user_id','title','background_theme','duration'])if(data.has(key))body[key]=data.get(key);
 return runCollaboration(body,form.querySelector('button'),async(r,e)=>{if(r.left){UI.close();await openGroupChat()}else await activateConversation(r.conversation,e)});
}
async function searchCollaborationPeer(event){event.preventDefault();const form=event.currentTarget;return runCollaboration({action:'search',name:new FormData(form).get('name')},form.querySelector('button'),r=>{const el=document.getElementById('collaboration-search-result');if(el)el.textContent=r.user?'找到本组成员：'+r.user.name:'未找到匹配的本组成员。'})}
