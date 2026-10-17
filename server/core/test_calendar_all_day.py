import uuid
from datetime import datetime
from unittest.mock import patch
from django.test import TestCase
from .calendar_adapter import ChronelleDelegatedAdapter
from .calendar_service import group_availability
from .models import User, ResearchGroup, Membership, CalendarConnection, CalendarSharing


class AllDayAvailabilityTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user(username='synthetic-all-day',first_name='测试')
        self.group=ResearchGroup.objects.create(name='测试',owner=self.user)
        Membership.objects.create(user=self.user,group=self.group,role='mentor')
        self.connection=CalendarConnection.objects.create(user=self.user,status='active',calendar_id=str(uuid.uuid4()))
        CalendarSharing.objects.create(user=self.user,group=self.group,visibility='details')
        self.start='2026-10-19T00:00:00+00:00';self.end='2026-10-23T00:00:00+00:00'

    def adapter(self,rows):
        def public(*args):
            return 200,{'calendar_ref':self.connection.calendar_id,'coverage':'complete','intervals':rows}
        return ChronelleDelegatedAdapter(lambda *args:self.fail('Private transport must not be used'), public_transport=public)

    def test_unknown_zone_dates_preserved_separately_and_still_block(self):
        adapter=self.adapter([{'start':'2026-10-20','end':'2026-10-21','all_day':True,'visibility':'busy','title':'private'}])
        result=group_availability(self.group,self.start,self.end,adapter)
        row=result['members'][0]
        self.assertEqual(row['state'],'partial');self.assertEqual(row['reason'],'all_day_timezone_unknown')
        self.assertFalse(result['complete']);self.assertEqual(result['suggestions'],[])
        item=row['all_day'][0]
        self.assertEqual(item['start_date'],'2026-10-20');self.assertEqual(item['end_date'],'2026-10-21')
        self.assertTrue(item['all_day']);self.assertTrue(item['timezone_uncertain'])
        self.assertEqual(item.get('title'),'忙碌')
        self.assertEqual(result['busy_union'],[{'start':'2026-10-19T10:00:00+00:00','end':'2026-10-21T12:00:00+00:00'}])
        self.assertEqual(row['busy'],row['all_day'])

    def test_published_title_requires_local_details_sharing(self):
        adapter=self.adapter([{'start':'2026-10-20','end':'2026-10-21','all_day':True,'visibility':'published','title':'published'}])
        self.assertEqual(group_availability(self.group,self.start,self.end,adapter)['members'][0]['all_day'][0]['title'],'published')
        CalendarSharing.objects.filter(user=self.user).update(visibility='busy')
        self.assertNotIn('title',group_availability(self.group,self.start,self.end,adapter)['members'][0]['all_day'][0])

    def test_timed_offsets_preserved_no_invented_all_day_or_free_semantics(self):
        adapter=self.adapter([{'start':'2026-10-20T09:00:00+08:00','end':'2026-10-20T10:00:00+08:00','all_day':False,'visibility':'busy','task':True,'transparent':True}])
        result=group_availability(self.group,self.start,self.end,adapter)
        self.assertTrue(result['complete']);self.assertEqual(result['members'][0]['all_day'],[])
        self.assertEqual(result['busy_union'][0]['start'],'2026-10-20T01:00:00+00:00')
        self.assertEqual(result['busy_union'][0]['end'],'2026-10-20T02:00:00+00:00')
