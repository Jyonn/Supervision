"""Explicit group-owner deletion and self-service membership exit; no remote I/O."""
import uuid
from django.db import transaction
from django.db.models import F
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from .models import (ResearchGroup, Membership, GroupActionReceipt, InvitationAcceptance,
                     CalendarSharing, MeetingSyncOperation)


@api_view(['POST'])
def lifecycle(request, group_id):
    action = request.data.get('action')
    expected = {'action', 'confirm_name', 'request_key'} | ({'confirm_permanent'} if action == 'delete' else set())
    if action not in ('leave', 'delete') or set(request.data) != expected:
        raise ValidationError('研究组操作无效')
    name = request.data.get('confirm_name')
    if not isinstance(name, str) or not 1 <= len(name) <= 100:
        raise ValidationError('请输入完整研究组名称')
    if action == 'delete' and request.data.get('confirm_permanent') is not True:
        raise ValidationError('必须明确确认永久删除，不能恢复')
    try:
        key = uuid.UUID(str(request.data.get('request_key')))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError('操作凭据无效')
    with transaction.atomic():
        # Acquire the write lock before reads, including on SQLite. This also
        # serializes deletion against meeting synchronization's group lock.
        ResearchGroup.objects.filter(pk=group_id).update(name=F('name'))
        receipt = GroupActionReceipt.objects.filter(request_key=key).first()
        if receipt:
            if (receipt.user_id, receipt.group_id, receipt.action) != (request.user.pk, group_id, action):
                raise PermissionDenied('操作凭据不属于当前账号或研究组')
            return Response({'completed': True, 'action': action, 'group_id': group_id, 'replayed': True})
        group = ResearchGroup.objects.select_for_update().filter(pk=group_id).first()
        if not group or not Membership.objects.filter(group=group, user=request.user).exists():
            raise NotFound('研究组不存在，或你已没有访问权限')
        if name != group.name:
            raise ValidationError('研究组名称不匹配，请完整输入当前名称')
        owner = group.owner_id == request.user.pk
        if action == 'leave':
            if owner:
                return Response({'error': '组主不能直接退出并留下无主组。当前可选择永久删除；转让功能尚未提供。', 'code': 'owner_cannot_leave'}, status=409)
            Membership.objects.filter(group=group, user=request.user).delete()
            CalendarSharing.objects.filter(group=group, user=request.user).delete()
        else:
            if not owner:
                raise PermissionDenied('仅研究组创建者／组主可以永久删除，导师角色不等于组主')
            if MeetingSyncOperation.objects.filter(meeting__group=group, status='running').exists():
                return Response({'error': '本组仍有会议正在同步，请完成后再删除；不会自动取消外部事件。', 'code': 'meeting_sync_running'}, status=409)
            # Acceptance protects its invitation; remove both inside the same
            # transaction before normal group-owned cascades. Shared identities,
            # calendar credentials and remote events are intentionally untouched.
            InvitationAcceptance.objects.filter(invitation__group=group).delete()
            group.delete()
        GroupActionReceipt.objects.create(request_key=key, user=request.user, group_id=group_id, action=action)
    for field in ('group_oauth_pending', 'group_oauth_completed'):
        pending = request.session.get(field)
        if isinstance(pending, dict) and pending.get('group_id') == group_id:
            request.session.pop(field, None)
    return Response({'completed': True, 'action': action, 'group_id': group_id})
