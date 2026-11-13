from datetime import date,datetime,timedelta,timezone as utc
from unittest.mock import Mock,patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from .clients.public_data import PublicDataError
from .models import WeatherMonth,WeatherLocalCache,WeatherCollectionJob,WeatherGridCache,WeatherHistoryRevision
from .services.geo_configuration import get_geo_integration,weather_configuration,weather_source_info
from .services.weather import collect_month,parse_daily,summarize,cached_weather,request_collection,VARIABLES
from .services.weather_grid import fetch_samples,save_cell
from .services.weather_adapters import get_adapter
from .services.weather_history import consolidate,needs_collection,request_revision,persist,maintain_history,history_metrics
from .test_geo_intelligence import weather_response


class PersistentWeatherHistoryTests(TestCase):
    def setUp(self):
        self.integration=get_geo_integration("open_meteo");self.cfg=weather_configuration(self.integration)
        self.point={"latitude":-23.5,"longitude":-46.5,"area_km2":100}

    def month(self,first=date(2025,1,1),days=31,collected=None,uf="SP",rain=2):
        sample=parse_daily(weather_response(start=first,days=days,rain=rain),first,first+timedelta(days=days-1))
        sample.update(area_km2=100,requested_coordinates=[[-23.5,-46.5]])
        summary=summarize([sample]);summary.update(source_info=weather_source_info(self.cfg),partial=days<(first.replace(day=28)+timedelta(days=4)).replace(day=1).toordinal()-first.toordinal())
        return WeatherMonth.objects.create(uf=uf,region="3",period=first,source="open_meteo_public",model="era5",method="area-grid-v2",samples=[sample],summary=summary,completeness=summary["completeness"],collected_at=collected or timezone.make_aware(datetime(2025,5,1)),expires_at=timezone.now()-timedelta(days=1))

    def test_consolidated_history_never_expires_or_calls_provider(self):
        row=consolidate(self.month(),self.cfg)
        self.assertTrue(row.consolidated_at);self.assertFalse(needs_collection(row,self.cfg))
        with patch("aplicativos.integracoes.clients.public_data.PublicDataClient.get",side_effect=AssertionError("network")):
            for _ in range(2):self.assertEqual(collect_month("SP",row.period,self.integration).pk,row.pk)
            self.assertEqual(request_collection(["SP"],"2025-01",None),[])
            self.assertFalse(cached_weather(["SP"],"2025-01")["SP"]["stale"])

    def test_recent_closed_complete_month_reused_after_http_expiry_without_false_consolidation(self):
        first=(timezone.localdate().replace(day=1)-timedelta(days=1)).replace(day=1)
        import calendar
        row=consolidate(self.month(first,calendar.monthrange(first.year,first.month)[1],timezone.now()),self.cfg)
        self.assertTrue(row.history_complete);self.assertIsNone(row.consolidated_at)
        with patch("aplicativos.integracoes.clients.public_data.PublicDataClient.get",side_effect=AssertionError("network")):
            self.assertEqual(collect_month("SP",first,self.integration).pk,row.pk)

    def test_source_review_requires_post_window_collection_before_consolidation(self):
        row=consolidate(self.month(collected=timezone.make_aware(datetime(2025,2,1))),self.cfg)
        self.assertTrue(row.history_complete);self.assertIsNone(row.consolidated_at);self.assertTrue(needs_collection(row,self.cfg))

    def test_integrity_mismatch_never_consolidates(self):
        row=consolidate(self.month(),self.cfg)
        row.summary["precipitation_mm"]=999;row.save(update_fields=["summary"])
        row=consolidate(row,self.cfg)
        self.assertFalse(row.history_complete)

    def test_explicit_provisional_source_data_is_not_frozen(self):
        row=self.month(collected=timezone.now())
        row.summary["provider_metadata"]={"provisional":True};row.save(update_fields=["summary"])
        row=consolidate(row,self.cfg)
        self.assertTrue(row.history_complete);self.assertIsNone(row.consolidated_at)

    def test_shared_cell_and_region_filters_reuse_expired_history(self):
        first=date(2025,1,1);end=date(2025,1,31)
        sample=parse_daily(weather_response(start=first),first,end)
        save_cell(self.cfg,get_adapter(self.cfg),self.point,first,end,sample,collected_at=timezone.make_aware(datetime(2025,5,1)),expires_at=timezone.now()-timedelta(days=1))
        client=Mock();client.get.side_effect=AssertionError("network")
        for _ in range(2):self.assertEqual(len(fetch_samples(self.integration,self.cfg,[self.point],first,end,client=client)["0"]["daily"]["time"]),31)
        client.get.assert_not_called()

    def test_90_day_read_only_local_months_when_supplier_offline(self):
        import calendar
        for month in (1,2,3):self.month(date(2025,month,1),calendar.monthrange(2025,month)[1])
        from aplicativos.mapa.services.geo_intelligence import weather_range
        with patch("aplicativos.integracoes.clients.public_data.PublicDataClient.get",side_effect=AssertionError("offline")):
            for count in (30,60,90):
                end=date(2025,3,31)
                result=weather_range(["SP"],end-timedelta(days=count-1),end)
                self.assertEqual(len(result["SP"]["daily"]),count)

    def test_current_month_fetches_only_missing_suffix(self):
        now=timezone.make_aware(datetime(2026,11,13,12))
        with patch("django.utils.timezone.now",return_value=now),patch("aplicativos.integracoes.services.weather.sampling_points",return_value=[self.point]):
            row=self.month(date(2026,11,1),6,now)
            row.expires_at=now+timedelta(days=1);row.save(update_fields=["expires_at"])
            client=Mock();client.get.return_value=weather_response(start=date(2026,11,7),days=2)
            updated=collect_month("SP",row.period,self.integration,client=client)
            self.assertEqual(client.get.call_count,1)
            self.assertEqual(client.get.call_args.args[1]["start_date"],"2026-11-07")
            self.assertEqual(len(updated.summary["daily"]),8)

    def test_current_provisional_tail_only_and_old_prefix_intact(self):
        now=timezone.make_aware(datetime(2026,11,13,12))
        with patch("django.utils.timezone.now",return_value=now),patch("aplicativos.integracoes.services.weather.sampling_points",return_value=[self.point]):
            row=self.month(date(2026,11,1),8,now);row.expires_at=now-timedelta(days=1);row.save(update_fields=["expires_at"])
            client=Mock();client.get.return_value=weather_response(start=date(2026,11,2),days=7,rain=3)
            updated=collect_month("SP",row.period,self.integration,client=client)
            self.assertEqual(client.get.call_args.args[1]["start_date"],"2026-11-02")
            self.assertEqual(updated.summary["daily"][0]["precipitation_sum"],2)

    def test_missing_day_inside_old_month_fetches_single_day(self):
        row=self.month();row.samples[0]["daily"]["precipitation_sum"][10]=None;row.summary=summarize(row.samples);row.save()
        client=Mock();client.get.return_value=weather_response(start=date(2025,1,11),days=1)
        with patch("aplicativos.integracoes.services.weather.sampling_points",return_value=[self.point]):
            collect_month("SP",row.period,self.integration,client=client)
        self.assertEqual(client.get.call_count,1)
        self.assertEqual(client.get.call_args.args[1]["start_date"],"2025-01-11")
        self.assertEqual(client.get.call_args.args[1]["end_date"],"2025-01-11")

    def test_partial_post_review_cell_cannot_validate_old_days(self):
        old=timezone.make_aware(datetime(2025,2,1));new=timezone.make_aware(datetime(2025,5,1))
        adapter=get_adapter(self.cfg)
        sample=parse_daily(weather_response(start=date(2025,1,1)),date(2025,1,1),date(2025,1,31))
        save_cell(self.cfg,adapter,self.point,date(2025,1,1),date(2025,1,31),sample,collected_at=old)
        one=parse_daily(weather_response(start=date(2025,1,11),days=1),date(2025,1,11),date(2025,1,11))
        save_cell(self.cfg,adapter,self.point,date(2025,1,11),date(2025,1,11),one,collected_at=new)
        def response(endpoint,params,**kwargs):
            first=date.fromisoformat(params["start_date"]);last=date.fromisoformat(params["end_date"])
            return weather_response(start=first,days=(last-first).days+1)
        client=Mock();client.get.side_effect=response
        fetch_samples(self.integration,self.cfg,[self.point],date(2025,1,1),date(2025,1,31),client=client)
        self.assertEqual({(c.args[1]["start_date"],c.args[1]["end_date"]) for c in client.get.call_args_list},{("2025-01-01","2025-01-10"),("2025-01-12","2025-01-31")})
        client.get.reset_mock()
        fetch_samples(self.integration,self.cfg,[self.point],date(2025,1,1),date(2025,1,31),client=client)
        client.get.assert_not_called()

    def test_revoked_revision_permission_stops_before_network(self):
        row=consolidate(self.month(),self.cfg)
        user=get_user_model().objects.create_superuser("history-revoked","revoked@example.invalid","password")
        request_revision(["SP"],"2025-01",user,"Revisão oficial do fornecedor.")
        user.is_active=False;user.save(update_fields=["is_active"])
        client=Mock()
        with self.assertRaises(PublicDataError):collect_month("SP",row.period,self.integration,job=WeatherCollectionJob.objects.get(),client=client)
        client.get.assert_not_called()

    def test_manual_revision_retains_before_and_after_and_requires_matching_version(self):
        user=get_user_model().objects.create_superuser("history","history@example.invalid","password")
        row=consolidate(self.month(),self.cfg);old_digest=row.content_digest
        self.assertEqual(request_revision(["SP"],"2025-01",user,"Revisão oficial de precipitação."),1)
        job=WeatherCollectionJob.objects.get();job.status="running";job.save()
        client=Mock();client.get.return_value=weather_response(start=date(2025,1,1),rain=3)
        with patch("aplicativos.integracoes.services.weather.sampling_points",return_value=[self.point]):updated=collect_month("SP",row.period,self.integration,client=client,job=job)
        self.assertEqual(updated.revision,2);self.assertNotEqual(old_digest,updated.content_digest)
        versions=WeatherHistoryRevision.objects.filter(kind="month",object_id=row.pk)
        self.assertEqual(set(versions.values_list("version",flat=True)),{1,2})
        self.assertEqual(versions.get(version=1).snapshot["summary"]["precipitation_mm"],62)
        self.assertEqual(versions.get(version=2).actor,user)
        with self.assertRaises(PublicDataError):persist(updated,{"summary":{}},self.cfg)
        with self.assertRaises(PublicDataError):persist(updated,{"summary":{}},self.cfg,refresh=job.refresh_request,expected_revision=1)

    def test_failed_revision_keeps_active_values_and_consolidation(self):
        row=consolidate(self.month(),self.cfg);original=row.content_digest
        user=get_user_model().objects.create_superuser("history2","history2@example.invalid","password")
        request_revision(["SP"],"2025-01",user,"Revisão oficial do fornecedor.")
        job=WeatherCollectionJob.objects.get();job.status="running";job.save()
        client=Mock();client.get.side_effect=PublicDataError("Offline")
        with patch("aplicativos.integracoes.services.weather.sampling_points",return_value=[self.point]):
            with self.assertRaises(PublicDataError):collect_month("SP",row.period,self.integration,client=client,job=job)
        row.refresh_from_db();self.assertEqual(row.content_digest,original);self.assertTrue(row.consolidated_at)

    def test_maintenance_is_idempotent_and_preserves_pending_checkpoint(self):
        row=self.month();job=WeatherCollectionJob.objects.create(uf="RJ",period=row.period,progress={"samples":{"0":{"kept":True}}})
        with patch("aplicativos.integracoes.clients.public_data.PublicDataClient.get",side_effect=AssertionError("network")):
            for _ in range(2):maintain_history(ufs=["SP","RJ"],period=row.period,enqueue=True)
        self.assertEqual(WeatherCollectionJob.objects.count(),1);job.refresh_from_db();self.assertTrue(job.progress["samples"]["0"]["kept"])
        self.assertEqual(history_metrics(["SP"])["complete_ufs"],1)

    def test_revision_preview_is_csrf_permission_and_scope_protected(self):
        user=get_user_model().objects.create_superuser("history3","history3@example.invalid","password")
        self.month();self.client.force_login(user)
        url=reverse("configuracoes:integracoes:geo-sync",args=["open_meteo"])
        values={"operation":"revise","scope":"uf","uf":"SP","period":"2025-01","revision_reason":"Revisão oficial do fornecedor."}
        self.client.post(url,values);self.assertFalse(WeatherCollectionJob.objects.exists())
        preview=self.client.post(url,{**values,"operation":"preview_revision"})
        self.assertEqual(preview.status_code,200)
        self.client.post(url,{**values,"preview_token":preview.context["geo_preview"]["token"]})
        self.assertTrue(WeatherCollectionJob.objects.get().refresh_request)

    def local_response(self,start,end,*,daily=True,hourly=True,rain=2):
        from zoneinfo import ZoneInfo
        from .services.weather_local import HOURLY
        zone=ZoneInfo("America/Sao_Paulo")
        result=weather_response(start=start,days=(end-start).days+1,rain=rain)
        result["timezone"]=str(zone)
        if daily:
            result["daily"]["time"]=[int(datetime.combine(start+timedelta(days=i),datetime.min.time(),tzinfo=zone).timestamp()) for i in range((end-start).days+1)]
        else: result.pop("daily");result.pop("daily_units")
        if hourly:
            a=datetime.combine(start,datetime.min.time(),tzinfo=zone).astimezone(utc.utc)
            b=datetime.combine(end+timedelta(days=1),datetime.min.time(),tzinfo=zone).astimezone(utc.utc)
            times=[int(a.timestamp())+i*3600 for i in range(int((b-a).total_seconds()/3600))]
            result["hourly"]={"time":times,**{key:[rain if key=="precipitation" else 20]*len(times) for key in HOURLY}}
            result["hourly_units"]=dict(HOURLY)
        return result

    def local(self,first=date(2025,1,1),days=31,collected=None):
        from .services.weather_local import parse_hourly,HOURLY
        sample=parse_daily(weather_response(start=first,days=days),first,first+timedelta(days=days-1));sample["area_km2"]=1
        raw=self.local_response(first-timedelta(days=1),first+timedelta(days=days))
        raw["hourly"]["units"]=dict(HOURLY)
        return WeatherLocalCache.objects.create(location_key="SP:sao paulo",city="São Paulo",uf="SP",ibge_code="3550308",latitude=-23.5,longitude=-46.5,period=first,source="open_meteo_public",model="era5",status="success",payload={"summary":summarize([sample]),"hourly":raw["hourly"],"timezone":"America/Sao_Paulo","cell":[-23.5,-46.5]},collected_at=collected or timezone.make_aware(datetime(2025,5,1)),expires_at=timezone.now()-timedelta(days=1))

    def test_hourly_consolidated_local_history_survives_http_expiry_and_queue_status(self):
        from .services.weather_local import collect_local,incident_window
        row=consolidate(self.local(),self.cfg)
        self.assertTrue(row.history_complete);self.assertTrue(row.consolidated_at)
        client=Mock();client.get.side_effect=AssertionError("network")
        for _ in range(2):collect_local(row,self.integration,client=client)
        client.get.assert_not_called()
        row.status="pending"
        self.assertNotEqual(incident_window(row,timezone.make_aware(datetime(2025,1,15,12)))["state"],"insufficient")

    def test_current_hourly_gap_fetches_only_affected_day_without_daily_request(self):
        from .services.weather_local import collect_local
        now=timezone.make_aware(datetime(2026,11,13,12))
        with patch("django.utils.timezone.now",return_value=now):
            row=self.local(date(2026,11,1),8,now)
            from zoneinfo import ZoneInfo
            at=int(datetime(2026,11,4,12,tzinfo=ZoneInfo("America/Sao_Paulo")).timestamp())
            row.payload["hourly"]["precipitation"][row.payload["hourly"]["time"].index(at)]=None
            row.expires_at=now+timedelta(days=1);row.save()
            client=Mock();client.get.return_value=self.local_response(date(2026,11,4),date(2026,11,4),daily=False)
            updated=collect_local(row,self.integration,client=client)
            self.assertEqual(client.get.call_count,1)
            params=client.get.call_args.args[1]
            self.assertNotIn("daily",params);self.assertEqual(params["start_date"],"2026-11-04");self.assertEqual(params["end_date"],"2026-11-04")
            self.assertEqual(updated.payload["summary"]["precipitation_mm"],16)

    def test_manual_local_revision_keeps_immutable_hourly_version(self):
        from .services.weather_local import collect_local
        row=consolidate(self.local(),self.cfg)
        user=get_user_model().objects.create_superuser("local-history","local-history@example.invalid","password")
        self.assertEqual(request_revision(["SP"],"2025-01",user,"Revisão oficial das séries horárias."),1)
        row.refresh_from_db()
        def response(endpoint,params,**kwargs):
            return self.local_response(date.fromisoformat(params["start_date"]),date.fromisoformat(params["end_date"]),daily="daily" in params,hourly="hourly" in params,rain=3)
        client=Mock();client.get.side_effect=response
        updated=collect_local(row,self.integration,client=client)
        self.assertEqual(updated.revision,2);self.assertTrue(updated.consolidated_at)
        old=WeatherHistoryRevision.objects.get(kind="local",object_id=row.pk,version=1)
        self.assertEqual(old.snapshot["payload"]["hourly"]["precipitation"][0],2)
        self.assertEqual(updated.payload["hourly"]["precipitation"][0],3)
