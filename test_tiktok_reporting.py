import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
import time
from decimal import Decimal
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo
from bot import tiktok as t, report
UTC=timezone.utc

def dt(s):return datetime.fromisoformat(s)
def snap(rows):return {"rows":rows,"cached_at":time.time(),"fetched_at":datetime.now(UTC)}
def row(hour,amount='1.19',**metrics):
    return {'dimensions':{'advertiser_id':t.ADVERTISER,'stat_time_hour':hour},'metrics':{'advertiser_id':t.ADVERTISER,'currency':'USD','timezone':'Etc/GMT+9','spend':amount,**metrics}}

class SpendTests(unittest.TestCase):
    def setUp(self):t._CACHE.clear();t._REPORT_CACHE.clear();t._ACCOUNT=None
    def test_real_utc_fixture_ignores_account_zone_for_hour(self):
        rows=[row('2026-11-13 17:00:00','0.83'),row('2026-11-13 18:00:00')]
        self.assertEqual(t.total_rows(rows,dt('2026-11-13T05:00:00+00:00'),dt('2026-11-13T18:38:00+00:00')),Decimal('2.02'))
    def test_exact_et_midnight_boundaries(self):
        start=dt('2026-11-13T00:00:00-05:00');end=start+timedelta(days=1)
        rows=[row('2026-11-13 04:00:00','500'),row('2026-11-13 05:00:00','2'),row('2026-11-14 04:00:00','3'),row('2026-11-14 05:00:00','900')]
        self.assertEqual(t.total_rows(rows,start,end),Decimal('5'))
    def test_dst_days_are_23_and_25_hours(self):
        tz=ZoneInfo('America/New_York')
        for day,hours in [('2026-03-08',23),('2026-11-01',25)]:
            start=datetime.fromisoformat(day).replace(tzinfo=tz);end=start+timedelta(days=1)
            cursor=start.astimezone(UTC);rows=[]
            while cursor<end.astimezone(UTC):rows.append(row(cursor.strftime('%Y-%m-%d %H:%M:%S'),'1'));cursor+=timedelta(hours=1)
            self.assertEqual(t.total_rows(rows,start,end),Decimal(hours))
    def test_reject_invalid_money(self):
        for value in [None,'NaN','Infinity','-0.01','bad',True]:
            with self.subTest(value=value),self.assertRaises(t.Unavailable):t.total_rows([row('2026-11-13 05:00:00',value)],dt('2026-11-13T05:00:00+00:00'),dt('2026-11-14T05:00:00+00:00'))
    def test_empty_and_outside_interval_not_zero(self):
        for rows in [[],[row('2026-11-13 04:00:00')]]:
            with self.assertRaises(t.Unavailable):t.total_rows(rows,dt('2026-11-13T05:00:00+00:00'),dt('2026-11-14T05:00:00+00:00'))
    def test_duplicate_currency_account_fail(self):
        rows=[row('2026-11-13 05:00:00')]*2
        for values in [rows,[row('2026-11-13 05:00:00',currency='EUR')],[row('2026-11-13 05:00:00',advertiser_id='wrong')]]:
            with self.assertRaises(t.Unavailable):t.total_rows(values,dt('2026-11-13T05:00:00+00:00'),dt('2026-11-14T05:00:00+00:00'))
    def test_no_write_method(self):
        with self.assertRaises(ValueError):t._call('campaign_update',{})
    @patch.object(t,'_call')
    def test_pagination_mismatch(self,call):
        call.return_value={'list':[row('2026-11-13 05:00:00')],'page_info':{'page':1,'total_page':1,'total_number':2}}
        with self.assertRaises(t.Unavailable):t._hourly('2026-11-13','2026-11-13')
    @patch.object(t,'_call')
    def test_pagination_continues_and_selects_utc(self,call):
        call.side_effect=[{'list':[row('2026-11-13 05:00:00')],'page_info':{'page':1,'total_page':2,'total_number':2}}, {'list':[row('2026-11-13 06:00:00')],'page_info':{'page':2,'total_page':2,'total_number':2}}]
        self.assertEqual(len(t._hourly('2026-11-13','2026-11-13')),2)
        self.assertTrue(all(c.args[1]['multi_adv_report_in_utc_time'] is True for c in call.call_args_list))
    @patch.object(t,'_account')
    @patch.object(t,'_report_rows')
    def test_success_cache_and_expired_failure_no_fallback(self,hourly,account):
        hourly.side_effect=lambda first,last:snap([row('2026-11-13 17:00:00')] if first=='2026-11-13' else [])
        start=dt('2026-11-13T05:00:00+00:00');now=dt('2026-11-13T18:38:00+00:00')
        self.assertEqual(t.spend(start,now,now)['amount'],Decimal('1.19'))
        t.spend(start,now+timedelta(seconds=20),now+timedelta(seconds=20));self.assertEqual(hourly.call_count,2)
        for v in t._CACHE.values():v['cached_at']=0
        hourly.side_effect=t.Unavailable('network')
        with self.assertRaises(t.Unavailable):t.spend(start,now,now)
    def test_historical_partial_hour_rejected(self):
        with self.assertRaises(t.Unavailable):t.spend(dt('2026-11-13T05:00:00+00:00'),dt('2026-11-13T06:30:00+00:00'),dt('2026-11-13T18:38:00+00:00'))

    @patch.object(t,'_account')
    @patch.object(t,'_report_rows')
    def test_date_query_covers_account_and_utc_boundaries(self,hourly,account):
        hourly.side_effect=lambda first,last:snap([row('2026-11-13 05:00:00')] if first=='2026-11-13' else [])
        start=dt('2026-11-13T00:00:00-05:00');end=dt('2026-11-14T00:00:00-05:00')
        t.spend(start,end,end)
        self.assertEqual([c.args for c in hourly.call_args_list],[(d,d) for d in ('2026-11-12','2026-11-13','2026-11-14')])
    @patch.object(t,'_account')
    @patch.object(t,'_call')
    def test_month_single_day_contract_and_overlapping_day_reuse(self,call,account):
        def fixture(name,args):
            self.assertEqual(args['start_date'],args['end_date'])
            stamp=args['start_date']+' 17:00:00'
            return {'list':[row(stamp,'1')],'page_info':{'page':1,'total_page':1,'total_number':1}}
        call.side_effect=fixture
        start=dt('2026-11-01T04:00:00+00:00');end=dt('2026-12-01T05:00:00+00:00')
        self.assertEqual(t.spend(start,end,end)['amount'],Decimal('30'))
        self.assertEqual(call.call_count,32)
        t.spend(dt('2026-11-13T05:00:00+00:00'),dt('2026-11-14T05:00:00+00:00'),end)
        self.assertEqual(call.call_count,32)
    def test_multiday_hourly_query_rejected_before_network(self):
        with patch.object(t,'_call') as call:
            with self.assertRaises(t.Unavailable):t._hourly('2026-11-12','2026-11-13')
            call.assert_not_called()
    @patch.object(t,'_account')
    @patch.object(t,'_call')
    def test_budget_and_rate_failure_stop_without_fallback(self,call,account):
        call.side_effect=t.Unavailable('TikTok reporting data unavailable')
        with self.assertRaises(t.Unavailable):t.spend(dt('2026-11-01T04:00:00+00:00'),dt('2026-11-13T18:38:00+00:00'),dt('2026-11-13T18:38:00+00:00'))
        self.assertEqual(call.call_count,1);self.assertEqual(t._CACHE,{})
        t._BUDGET.deadline=time.monotonic()-1
        try:
            with self.assertRaises(t.Unavailable):t._timeout()
        finally:del t._BUDGET.deadline
    @patch.object(t,'_account')
    @patch.object(t,'_call')
    def test_snapshot_never_extends_raw_cache_age(self,call,account):
        old=time.time()-290
        for day in ('2026-11-12','2026-11-13'):
            t._REPORT_CACHE[(day,day)]={'rows':[row('2026-11-13 17:00:00')] if day.endswith('13') else [],'cached_at':old,'fetched_at':datetime.now(UTC)-timedelta(seconds=290)}
        value=t.spend(dt('2026-11-13T05:00:00+00:00'),dt('2026-11-13T18:38:00+00:00'),dt('2026-11-13T18:38:00+00:00'))
        self.assertEqual(value['cached_at'],old);call.assert_not_called()
    @patch.object(t,'_token',return_value='fixture-access')
    @patch.object(t.requests,'post')
    def test_json_and_sse_parse_and_provider_error_redacted(self,post,token):
        payload={'code':0,'data':{'list':[]}}
        envelope={'jsonrpc':'2.0','id':1,'result':{'content':[{'type':'text','text':json.dumps(payload)}]}}
        for content_type in ['application/json','text/event-stream']:
            post.return_value=Mock(headers={'Content-Type':content_type},text='data: '+json.dumps(envelope)+'\n',json=lambda:envelope)
            self.assertEqual(t._call('report_integrated_get',{}),{'list':[]})
        envelope['error']={'message':'fixture-secret'}
        post.return_value.headers={'Content-Type':'application/json'}
        with self.assertRaises(t.Unavailable) as e:t._call('report_integrated_get',{})
        self.assertNotIn('secret',str(e.exception))
    @patch.object(t,'_call')
    def test_account_mismatch_rejected(self,call):
        for key,value in [('advertiser_id','wrong'),('currency','EUR'),('timezone','America/Anchorage')]:
            account={'advertiser_id':t.ADVERTISER,'currency':'USD','timezone':'Etc/GMT+9'};account[key]=value
            call.return_value={'list':[account]}
            with self.assertRaises(t.Unavailable):t._account()

class TokenTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();os.mkdir(self.tmp.name+'/tiktok')
        self.path=self.tmp.name+'/tiktok/tokens.json'
        self.env=patch.dict(os.environ,{'TIKTOK_DATA_DIR':self.tmp.name});self.env.start()
        self.token={'created_at':1000,'expires_in':100,'refresh_token_expires_in':3000000,'expected_advertiser':t.ADVERTISER,'scope':'mcp:tt4b','client_id':'client','access_token':'fixture-access','refresh_token':'fixture-refresh'}
        self.save()
    def save(self):t.auth._write(self.path,self.token)
    def tearDown(self):self.env.stop();self.tmp.cleanup()
    @patch.object(t.time,'time',return_value=2000)
    @patch.object(t.requests,'post')
    def test_refresh_rotates_and_keeps_original_deadline(self,post,clock):
        post.return_value=Mock(json=lambda:{'access_token':'fixture-new','refresh_token':'fixture-rotated','expires_in':86400,'refresh_token_expires_in':3000000})
        self.assertEqual(t._token(),'fixture-new')
        with open(self.path) as f:saved=json.load(f)
        self.assertEqual(saved['refresh_expires_at'],3001000);self.assertEqual(saved['refresh_token'],'fixture-rotated');self.assertEqual(os.stat(self.path).st_mode&0o777,0o600)
    @patch.object(t.time,'time',return_value=4000000)
    @patch.object(t.requests,'post')
    def test_expired_requires_owner_reauthorization(self,post,clock):
        with self.assertRaises(t.Unavailable):t._token()
        post.assert_not_called()
    @patch.object(t.time,'time',return_value=2000)
    @patch.object(t.requests,'post',side_effect=RuntimeError('fixture-secret must not escape'))
    def test_failure_redacted(self,post,clock):
        with self.assertRaises(t.Unavailable) as e:t._token()
        self.assertNotIn('secret',str(e.exception))

    @patch.object(t.time,'time',return_value=2000)
    @patch.object(t.requests,'post')
    def test_absolute_only_token_expiries(self,post,clock):
        self.token.pop('created_at');self.token.pop('expires_in');self.token.pop('refresh_token_expires_in')
        self.token.update(expires_at=90000,refresh_expires_at=3001000);self.save()
        self.assertEqual(t._token(),'fixture-access');post.assert_not_called()
    @patch.object(t.time,'time',return_value=2000)
    def test_invalid_expiry_never_used(self,clock):
        for expiry in ['NaN','Infinity',-1]:
            self.token['expires_at']=expiry;self.save()
            with self.assertRaises(t.Unavailable):t._token()

@patch.dict(os.environ,{"META_AD_ACCOUNT_ID":"fixture-account"})
class ReportingTests(unittest.TestCase):
    def test_subset_not_all_missing_sources(self):
        orders=[{'customerJourneySummary':{'lastVisit':{'utmParameters':{'source':source}}},'cancelledAt':None} for source in ['tiktok','TikTok','facebook','',None]]
        orders+=[{'customerJourneySummary':{'lastVisit':{'utmParameters':{'source':'tiktok'}}},'cancelledAt':'cancelled'}]
        with patch.object(report.costs,'order_costs',return_value={'revenue':10}):self.assertEqual(report._tagged_tiktok(orders,{}),(20,2))
    @patch.object(report.meta,'_get',return_value={'currency':'USD','timezone_name':'America/New_York'})
    @patch.object(report.meta,'spend',return_value=10)
    @patch.object(t,'spend',return_value={'amount':Decimal('2.02'),'fetched_at':dt('2026-11-13T18:38:00+00:00')})
    def test_adds_only_spend(self,*mocks):
        start=dt('2026-11-13T00:00:00-05:00');now=dt('2026-11-13T13:38:00-05:00')
        ad=report._ad_totals(start,now,now);self.assertEqual(ad['total'],12.02);self.assertIn('may lag',ad['note'])
    @patch.object(report.meta,'_get',return_value={'currency':'USD','timezone_name':'America/New_York'})
    @patch.object(report.meta,'spend',return_value=10)
    @patch.object(t,'spend',side_effect=t.Unavailable('TikTok no rows; zero not verified'))
    def test_missing_spend_not_zero(self,*mocks):
        start=dt('2026-11-13T00:00:00-05:00');now=dt('2026-11-13T13:38:00-05:00')
        ad=report._ad_totals(start,now,now);self.assertIsNone(ad['total']);self.assertIsNone(ad['tiktok']);self.assertEqual(ad['meta'],10)
    def test_scoreboard_no_double_revenue_and_failure(self):
        start=dt('2026-11-13T00:00:00-05:00');now=dt('2026-11-13T13:38:00-05:00')
        order={'cancelledAt':None,'customerJourneySummary':{'lastVisit':{'utmParameters':{'source':'tiktok'}}}}
        costs={'revenue':100,'fees':2,'cogs':20,'shipping':3,'returns':0,'units_cost':18,'packaging':2,'unmapped':[],'assumed_books':[],'tags':[],'lines':1,'missing_cogs':[]}
        ad={'total':12.02,'meta':10,'tiktok':2.02,'note':'TikTok fetched 1:38 PM; may lag'}
        with patch.object(report,'_range',return_value=(start,None,'Today',now)),patch.object(report.shopify,'orders',return_value=[order]),patch.object(report.costs,'split_marketplace',return_value=([order],[])),patch.object(report.costs,'split_wholesale',return_value=([order],0)),patch.object(report,'_mp_cohort',return_value=[]),patch.object(report.costs,'load_cogs',return_value={}),patch.object(report.costs,'order_costs',return_value=costs),patch.object(report,'_subs_line',return_value=''),patch.object(report.costs,'marketplace_line',return_value=''),patch.object(report,'_ad_totals',return_value=ad):
            msg=report.scoreboard();self.assertIn('Website sales $100',msg);self.assertNotIn('Website sales $200',msg);self.assertIn('Profit $63',msg);self.assertIn('TikTok-tagged sales $100',msg)
            ad['total']=0;ad['meta']=0;ad['tiktok']=0
            msg=report.scoreboard();self.assertIn('ROAS unavailable',msg);self.assertNotIn('0.00x',msg)
            ad['total']=None;ad['tiktok']=None
            msg=report.scoreboard();self.assertIn('Profit / ROI / ROAS unavailable',msg);self.assertIn('ROAS unavailable',msg);self.assertIn('TikTok unavailable',msg)

    def test_weekly_recap_complete_and_missing(self):
        from copy import deepcopy
        totals={'revenue':100,'costs':25,'orders':1,'books':{},'unexpanded':{},'prods':{}}
        ad={'total':12.02,'meta':10,'tiktok':2.02,'note':'snapshot'}
        with patch.object(report.shopify,'orders',return_value=[]),patch.object(report.costs,'split_marketplace',return_value=([],[])),patch.object(report.costs,'split_wholesale',return_value=([],0)),patch.object(report,'_mp_cohort',return_value=[]),patch.object(report.costs,'load_cogs',return_value={}),patch.object(report,'_week_totals',side_effect=lambda *a:deepcopy(totals)),patch.object(report.klaviyo,'new_profiles',return_value=None),patch.object(report.costs,'marketplace_line',return_value=''),patch.object(report,'_ad_totals',side_effect=lambda *a:deepcopy(ad)):
            msg=report.weekly_recap();self.assertIn('Profit $63',msg);self.assertIn('Ads $12.02',msg);self.assertIn('Meta $10.00 / TikTok $2.02',msg)
            ad['total']=0;ad['meta']=0;ad['tiktok']=0
            msg=report.weekly_recap();self.assertIn('ROAS unavailable',msg);self.assertNotIn('0.00x',msg)
            ad['total']=None;ad['tiktok']=None
            msg=report.weekly_recap();self.assertIn('comparison unavailable',msg);self.assertNotIn('Profit $',msg)
    @patch.object(report.meta,'_get')
    def test_strict_meta_missing_and_invalid_never_zero(self,get):
        for response in [{},{'data':[]},{'data':[{}]},{'data':[{'spend':'NaN'}]},{'data':[{'spend':'-1'}]},{'data':[{'spend':'2'}],'paging':{'next':'another-page'}}]:
            get.return_value=response
            with self.assertRaises(Exception):report.meta.spend('2026-11-13','2026-11-13',strict=True)
        get.return_value={'data':[{'spend':'0'}]}
        self.assertEqual(report.meta.spend('2026-11-13','2026-11-13',strict=True),0)

    def test_compact_marketplace_preserves_missing_and_verified_values(self):
        with patch.object(report.costs,'marketplace_line',return_value='🛒 Amazon: 3 order(s) placed this period - sales unavailable pending amount validation'):
            self.assertEqual(report._compact_marketplace([]),'🛒 Amazon: 3 orders · sales unavailable')
        with patch.object(report.costs,'marketplace_line',return_value='🛒 Amazon revenue $123'):
            self.assertEqual(report._compact_marketplace([]),'🛒 Amazon revenue $123')
        with patch.object(report.costs,'marketplace_line',return_value=None):self.assertEqual(report._compact_marketplace([]),'')

if __name__=='__main__':unittest.main()
