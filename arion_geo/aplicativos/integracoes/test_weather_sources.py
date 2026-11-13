from copy import deepcopy
from datetime import date, timedelta
from unittest.mock import Mock, patch
from django.test import TestCase
from django.utils import timezone
from .models import WeatherGridCache, WeatherRequestLog, IntegrationSyncState, WeatherCollectionJob, WeatherMonth, IntegrationLease
from .clients.public_data import PublicDataError
from .services.geo_configuration import get_geo_integration, weather_configuration
from .services.weather_adapters import NASAPowerAdapter
from .services.weather_grid import fetch_samples
from .services.weather import _count_request, worker_tick
from .services.weather_coverage import control_jobs
from .services.weather_cds import acquisition_plan
from .services.geo_operations import worker_state
from .test_geo_intelligence import weather_response


class WeatherSourcesTests(TestCase):
    def setUp(self):
        self.integration=get_geo_integration("open_meteo")
        self.cfg=weather_configuration(self.integration)
        self.start,self.end=date(2026,10,1),date(2026,10,31)
        self.points=[{"latitude":-23.5,"longitude":-46.5,"area_km2":1},{"latitude":-24,"longitude":-47,"area_km2":2}]

    def test_batch_validates_all_cells_and_reuses_across_consumers(self):
        client=Mock();client.get.return_value=[weather_response(),weather_response()]
        samples=fetch_samples(self.integration,self.cfg,self.points,self.start,self.end,client=client)
        self.assertEqual(len(samples),2);self.assertEqual(client.get.call_count,1)
        self.assertEqual(client.get.call_args.args[1]["latitude"],"-23.5,-24")
        self.assertEqual(WeatherGridCache.objects.count(),2)
        client.get.reset_mock()
        fetch_samples(self.integration,self.cfg,self.points,self.start,self.end,client=client)
        client.get.assert_not_called()

    def test_invalid_batch_never_persists_partial_members(self):
        invalid=weather_response();invalid["daily_units"]["precipitation_sum"]="in"
        client=Mock();client.get.return_value=[weather_response(),invalid]
        with self.assertRaises(PublicDataError):fetch_samples(self.integration,self.cfg,self.points,self.start,self.end,client=client)
        self.assertFalse(WeatherGridCache.objects.exists())

    def test_connectivity_diagnostic_does_not_claim_cached_data_is_live(self):
        client=Mock();client.get.return_value=weather_response()
        fetch_samples(self.integration,self.cfg,self.points[:1],self.start,self.end,client=client)
        client.get.reset_mock()
        fetch_samples(self.integration,self.cfg,self.points[:1],self.start,self.end,client=client,force_refresh=True)
        client.get.assert_called_once()

    def test_incremental_suffix_reuses_prefix_and_duplicates(self):
        client=Mock();client.get.return_value=weather_response(days=15)
        fetch_samples(self.integration,self.cfg,self.points[:1],self.start,date(2026,10,15),client=client)
        client.get.reset_mock();client.get.return_value=weather_response(start=date(2026,10,16),days=16)
        result=fetch_samples(self.integration,self.cfg,[self.points[0],self.points[0]],self.start,self.end,client=client)
        self.assertEqual(client.get.call_count,1)
        self.assertEqual(client.get.call_args.args[1]["start_date"],"2026-10-16")
        self.assertEqual(len(result["1"]["daily"]["time"]),31)

    def test_http_budget_distinct_from_supplier_units_and_provider_429(self):
        cfg={**self.cfg,"daily_budget":1}
        log=_count_request(self.integration,cfg,locations=8,days=31,variables=7)
        self.assertEqual(log.locations,8);self.assertGreater(log.estimated_units,8)
        with self.assertRaises(PublicDataError) as error:_count_request(self.integration,cfg)
        self.assertEqual(error.exception.code,"budget")
        self.assertEqual(WeatherRequestLog.objects.count(),1)

    def test_resume_never_clears_external_delay(self):
        job=WeatherCollectionJob.objects.create(uf="SP",period=self.start,status="pending")
        until=timezone.now()+timedelta(hours=2)
        IntegrationSyncState.objects.create(key="weather-cooldown",change_cursor=until,last_error="rate_limited")
        control_jobs(["SP"],"2026-10","resume")
        job.refresh_from_db();self.assertEqual(job.next_attempt_at,until)

    def test_fallback_never_used_for_quota_or_authentication(self):
        self.integration.config={**self.cfg,"allow_fallback":True,"secondary_provider":"nasa_power"};self.integration.save()
        for code in ("rate_limited","budget","authentication","contract"):
            WeatherCollectionJob.objects.all().delete();IntegrationSyncState.objects.filter(key="weather-cooldown").delete()
            WeatherCollectionJob.objects.create(uf="SP",period=self.start)
            with patch("aplicativos.integracoes.services.weather.collect_month",side_effect=PublicDataError("Falha",code=code,retry_after=300)) as collect:
                worker_tick();self.assertEqual(collect.call_count,1)

    def test_fallback_authorized_only_for_transport(self):
        self.integration.config={**self.cfg,"allow_fallback":True,"secondary_provider":"nasa_power"};self.integration.save()
        WeatherCollectionJob.objects.create(uf="SP",period=self.start)
        with patch("aplicativos.integracoes.services.weather.collect_month",side_effect=[PublicDataError("Conexão",code="unavailable"),Mock()]) as collect:
            worker_tick();self.assertEqual(collect.call_count,2)
            self.assertEqual(collect.call_args.args[2].config["preferred_provider"],"nasa_power")

    def test_nasa_units_fill_time_and_missing_wind_are_explicit(self):
        from .services.weather_adapters import NASA_PARAMETERS
        payload={"header":{"fill_value":-999,"time_standard":"UTC","start":"20261001","end":"20261002","sources":["MERRA2"]},
            "geometry":{"coordinates":[-46.25,-23.5,0]},"parameters":{k:{"units":"mm/day" if k=="PRECTOTCORR" else "C"} for k in NASA_PARAMETERS},
            "properties":{"parameter":{k:{"20261001":-999,"20261002":2} for k in NASA_PARAMETERS}}}
        client=Mock();client.get.return_value=payload
        sample=NASAPowerAdapter().fetch(client,{},self.points[:1],self.start,date(2026,10,2),"",None)[0]
        self.assertIsNone(sample["daily"]["precipitation_sum"][0]);self.assertIsNone(sample["daily"]["wind_speed_10m_max"][1])
        self.assertIn("hourly",sample["missing_capabilities"])
        payload["header"]["time_standard"]="LST"
        with self.assertRaises(PublicDataError):NASAPowerAdapter().fetch(client,{},self.points[:1],self.start,date(2026,10,2),"",None)

    def test_cds_plan_is_bounded_and_never_declares_import_ready(self):
        plan=acquisition_plan("2026-09-30","2026-10-02",[-23,-47,-24,-46])
        self.assertEqual(len(plan["requests"]),2);self.assertFalse(plan["import_ready"])
        with self.assertRaises(ValueError):acquisition_plan("2026-01-01","2026-10-31",[-23,-47,-24,-46])
        with self.assertRaises(ValueError):acquisition_plan("2026-10-01","2026-10-31",[90,-180,-90,180])

    def test_worker_expired_heartbeat_is_not_active(self):
        IntegrationLease.objects.create(key="worker",owner_token="test",heartbeat_at=timezone.now()-timedelta(seconds=70),expires_at=timezone.now()+timedelta(seconds=100))
        self.assertFalse(worker_state()["active"])

    def test_worker_resumes_due_job_after_budget_cooldown_without_post(self):
        past=timezone.now()-timedelta(seconds=10)
        IntegrationSyncState.objects.create(key="weather-cooldown",change_cursor=past,last_error="budget")
        job=WeatherCollectionJob.objects.create(uf="SP",period=self.start,next_attempt_at=past)
        with patch("aplicativos.integracoes.services.weather.collect_month") as collect:
            worker_tick();collect.assert_called_once()
        job.refresh_from_db();self.assertEqual(job.status,"success")

    def test_sources_never_share_grid_cache_even_for_same_coordinates(self):
        from .services.weather_adapters import get_adapter
        from .services.weather_grid import save_cell,cached_cell
        cfg={**self.cfg,"preferred_provider":"nasa_power"}
        save_cell(cfg,get_adapter(cfg),self.points[0],self.start,self.end,{"daily":{}})
        self.assertIsNone(cached_cell(self.cfg,get_adapter(self.cfg),self.points[0],self.start,self.end))

    def test_legacy_merged_cell_seeds_only_the_recoverable_request(self):
        from .services.weather_grid import seed_months
        from .services.weather_adapters import get_adapter
        from .services.weather import parse_daily
        sample=parse_daily(weather_response(),self.start,self.end)
        sample["requested_coordinates"]=[[-23.5,-46.5],[-23.6,-46.6]]
        WeatherMonth.objects.create(uf="SP",period=self.start,source="open_meteo_public",method="area-grid-v2",samples=[sample],summary={},collected_at=timezone.now(),expires_at=timezone.now()+timedelta(days=1))
        seed_months(self.cfg,get_adapter(self.cfg),self.start)
        self.assertEqual(WeatherGridCache.objects.count(),1)
        self.assertEqual(WeatherGridCache.objects.get().latitude,-23.5)

    def test_mixed_model_daily_series_and_rankings_are_not_combined(self):
        from aplicativos.mapa.services.geo_intelligence import aggregate_weather,climate_rankings
        rows={"SP":{"model":"era5","source_info":{"timezone":"America/Sao_Paulo"}},
              "RJ":{"model":"power_meteorology","source_info":{"timezone":"UTC"}}}
        self.assertFalse(aggregate_weather(rows)["available"])
        self.assertEqual(climate_rankings(rows),{})
