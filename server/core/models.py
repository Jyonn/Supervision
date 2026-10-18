from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower
import uuid

class User(AbstractUser):
    email = models.EmailField(unique=True, null=True, blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    avatar_source = models.CharField(max_length=12, default='preset')
    avatar_preset = models.PositiveSmallIntegerField(default=0)
    gravatar_email_source = models.CharField(max_length=12, default='local')
    class Meta:
        constraints = [models.UniqueConstraint(Lower('email'), name='unique_email_casefold')]

class ResearchGroup(models.Model):
    name = models.CharField(max_length=100)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

class GroupSpaceBinding(models.Model):
    group = models.OneToOneField(ResearchGroup, on_delete=models.CASCADE)
    space_id = models.PositiveBigIntegerField()
    admin_subject = models.CharField(max_length=100)
    revision = models.PositiveIntegerField(default=1)
    verified_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    active = models.BooleanField(default=True)

class GroupInvitation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey(ResearchGroup, on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64, unique=True)
    binding_revision = models.PositiveIntegerField()
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

class SpaceIdentity(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    space_id = models.PositiveBigIntegerField()
    subject = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'space_id'], name='one_user_space_identity')]

class InvitationAcceptance(models.Model):
    invitation = models.ForeignKey(GroupInvitation, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    accepted_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['invitation', 'user'], name='one_invitation_acceptance')]

class Membership(models.Model):
    group = models.ForeignKey(ResearchGroup, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=10, choices=[('mentor', '导师'), ('student', '学生')])
    class Meta:
        constraints = [models.UniqueConstraint(fields=['group', 'user'], name='one_group_membership')]

class ResearchTask(models.Model):
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    group = models.ForeignKey(ResearchGroup, on_delete=models.CASCADE, null=True, blank=True)
    assignees = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='research_tasks')
    created_at = models.DateTimeField(auto_now_add=True)

class Entry(models.Model):
    task = models.ForeignKey(ResearchTask, on_delete=models.CASCADE, related_name='entries')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    kind = models.CharField(max_length=10, choices=[('report', '日报'), ('summary', '总结'), ('feedback', '反馈')])
    day = models.DateField()
    body = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['task', 'owner', 'kind', 'day'], name='one_daily_entry')]

class FriendomIdentity(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='friendom_identity')
    subject = models.CharField(max_length=100, unique=True)
    avatar_url = models.URLField(max_length=2048, blank=True)
    connected_at = models.DateTimeField(auto_now_add=True)
    gravatar_email_hash = models.CharField(max_length=64, blank=True)
    gravatar_email_received_at = models.DateTimeField(null=True, blank=True)

class EmailChallenge(models.Model):
    email = models.EmailField()
    purpose = models.CharField(max_length=24, default='registration')
    session_hash = models.CharField(max_length=64)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed = models.BooleanField(default=False)
    sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

class EmailRateBucket(models.Model):
    key = models.CharField(max_length=64, unique=True)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField()

class CalendarConnection(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    # Verified provider references only. No bearer token is stored in these models.
    provider_subject = models.CharField(max_length=160, blank=True)
    calendar_id = models.CharField(max_length=160, blank=True)
    event_group_id = models.PositiveBigIntegerField(null=True, blank=True)
    capabilities = models.JSONField(default=list)
    status = models.CharField(max_length=12, default='disconnected')
    authorized_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

class CalendarCredential(models.Model):
    connection = models.OneToOneField(CalendarConnection, on_delete=models.CASCADE)
    ciphertext = models.TextField()
    grant_id = models.CharField(max_length=64)
    expires_at = models.DateTimeField(null=True, blank=True)
    group_ids = models.JSONField(default=list)

class CalendarAuthorizationAttempt(models.Model):
    request_id = models.UUIDField(unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    status = models.CharField(max_length=16,default='pending')
    expires_at = models.DateTimeField()

class CalendarSharing(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    group = models.ForeignKey(ResearchGroup, on_delete=models.CASCADE)
    visibility = models.CharField(max_length=12, default='none')
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'group'], name='one_calendar_share')]

class ResearchMeeting(models.Model):
    creation_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    group = models.ForeignKey(ResearchGroup, on_delete=models.CASCADE)
    organizer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    participants = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='research_meetings')
    title = models.CharField(max_length=160)
    agenda = models.TextField(blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    timezone = models.CharField(max_length=64, default='UTC')
    kind = models.CharField(max_length=12, default='meeting')
    status = models.CharField(max_length=12, default='scheduled')
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

class MeetingCalendarLink(models.Model):
    meeting = models.OneToOneField(ResearchMeeting, on_delete=models.CASCADE, related_name='calendar_link')
    connection = models.ForeignKey(CalendarConnection, on_delete=models.PROTECT)
    provider_subject = models.CharField(max_length=160)
    calendar_id = models.CharField(max_length=160)
    event_group_id = models.PositiveBigIntegerField(null=True, blank=True)
    external_id = models.CharField(max_length=160, blank=True)
    external_version = models.CharField(max_length=160, blank=True)
    synced_revision = models.PositiveIntegerField(default=0)

class MeetingSyncOperation(models.Model):
    meeting = models.ForeignKey(ResearchMeeting, on_delete=models.CASCADE, related_name='sync_operations')
    revision = models.PositiveIntegerField()
    action = models.CharField(max_length=12)
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=16, default='pending')
    error_code = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['meeting', 'revision'], name='one_meeting_sync_revision')]
