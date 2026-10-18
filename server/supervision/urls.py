from django.urls import path
from core import views
from core import calendar_views
urlpatterns = [path('api/calendar/connection/',calendar_views.connection),
    path('api/calendar/authorization/start/',calendar_views.authorization_start),
    path('api/calendar/authorization/claim/',calendar_views.authorization_claim),
    path('api/calendar/authorization/cancel/',calendar_views.authorization_cancel),
    path('api/calendar/authorization/callback/',calendar_views.authorization_callback),
    path('api/groups/<int:group_id>/calendar/sharing/',calendar_views.sharing),
    path('api/groups/<int:group_id>/calendar/availability/',calendar_views.availability),
    path('api/groups/<int:group_id>/meetings/',calendar_views.meetings),
    path('api/meetings/<int:meeting_id>/',calendar_views.meeting_detail)]
urlpatterns += [path('api/verification/send/', views.verification_send)]
urlpatterns += [path('api/auth/friendom/email/start/', views.friendom_email_start)]
urlpatterns += [path('api/auth/friendom/start/', views.friendom_start), path('api/auth/friendom/callback/', views.friendom_callback), path('api/profile/', views.profile), path('api/avatars/preset/<int:preset>/', views.preset_avatar), path('', views.landing), path('app/', views.workspace), path('assets/<str:filename>', views.asset), path('api/session/', views.session), path('api/register/', views.register), path('api/login/', views.sign_in), path('api/logout/', views.sign_out), path('api/groups/', views.groups), path('api/groups/<int:group_id>/members/', views.members), path('api/tasks/', views.tasks), path('api/tasks/<int:task_id>/', views.task_detail)]

from core import group_join
urlpatterns += [path('api/groups/<int:group_id>/space/', group_join.binding), path('api/groups/<int:group_id>/invitations/', group_join.invitations), path('api/join/<str:token>/', group_join.join)]

from core import group_lifecycle
urlpatterns += [path('api/groups/<int:group_id>/lifecycle/', group_lifecycle.lifecycle)]
