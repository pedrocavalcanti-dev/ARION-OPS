from datetime import date, datetime, timedelta, timezone as utc
from unittest.mock import Mock, patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from .clients.public_data import PublicDataError
from .models import WeatherCollectionJob, WeatherLocalCache, IntegrationSyncState
from .services.geo_configuration import get_geo_integration
from .services.weather import collect_month, request_collection, worker_tick
from .services.weather_coverage import control_jobs, collection_plan
from .services.weather_local import incident_window, parse_hourly, HOURLY, request_local
from .test_geo_intelligence import weather_response


class NationalWeatherTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser("national","national@example.invalid","strong-password")
        self.integration=get_geo_integration("open_meteo")
        self.client.force_login(self.user)

    def test_preview_never_queues_and_batch_requires_signed_preview(self):
        url=reverse("configuracoes:integracoes:geo-sync",args=["open_meteo"])
        values={"scope":"brazil","period":"2026-10","operation":"preview"}
        with patch("aplicativos.integracoes.clients.public_data.PublicDataClient.get",side_effect=AssertionError("network")):
            response=self.client.post(url,values)
            self.assertEqual(response.status_code,200)
            preview=response.context["geo_preview"]
            self.assertEqual(preview["expected_ufs"],27)
            self.assertGreater(preview["estimated_requests"],27)
        self.assertFalse(WeatherCollectionJob.objects.exists())
        self.client.post(url,{**values,"operation":"collect"})
        self.assertFalse(WeatherCollectionJob.objects.exists())
        self.client.post(url,{**values,"operation":"collect","preview_token":preview["token"]})
        self.assertEqual(WeatherCollectionJob.objects.count(),27)
        self.client.post(url,{**values,"operation":"collect","preview_token":preview["token"]})
        self.assertEqual(WeatherCollectionJob.objects.count(),27)

    def test_pause_cancel_resume_preserve_progress_and_other_period(self):
        request_collection(["SP","RJ"],"2026-10",self.user)
        other=WeatherCollectionJob.objects.create(uf="BA",period=date(2026,9,1))
        job=WeatherCollectionJob.objects.get(uf="SP")
        job.progress={"samples":{"0":{"sample":"kept"}}};job.save()
        self.assertEqual(control_jobs(["SP","RJ"],"2026-10","pause"),2)
        self.assertEqual(control_jobs(["SP"],"2026-10","cancel"),1)
        self.assertEqual(control_jobs(["SP","RJ"],"2026-10","resume"),2)
        job.refresh_from_db();other.refresh_from_db()
        self.assertIn("0",job.progress["samples"])
        self.assertEqual(other.status,"pending")

    def test_checkpoint_reuses_completed_cell_after_quota(self):
        # A one-cell batch deliberately exercises a quota between checkpoints.
        self.integration.config={**self.integration.config,"batch_size":1};self.integration.save()
        job=WeatherCollectionJob.objects.create(uf="SP",period=date(2026,10,1),status="running")
        points=[{"latitude":-23,"longitude":-46,"area_km2":1},{"latitude":-24,"longitude":-47,"area_km2":2}]
        client=Mock();client.get.side_effect=[weather_response(),PublicDataError("Quota",code="rate_limited",retry_after=600)]
        with patch("aplicativos.integracoes.services.weather.sampling_points",return_value=points):
            with self.assertRaises(PublicDataError):collect_month("SP",job.period,self.integration,client=client,job=job)
            job.refresh_from_db();self.assertEqual(len(job.progress["samples"]),1)
            client.get.reset_mock();client.get.side_effect=None;client.get.return_value=weather_response()
            row=collect_month("SP",job.period,self.integration,client=client,job=job)
            self.assertEqual(client.get.call_count,1)
            self.assertEqual(row.completeness,1)

    def test_provider_quota_backs_off_entire_queue_without_exhausting_attempts(self):
        request_collection(["SP","RJ"],"2026-10",self.user)
        with patch("aplicativos.integracoes.services.weather.collect_month",side_effect=PublicDataError("Quota",code="rate_limited",retry_after=600)) as collect:
            worker_tick();worker_tick()
            self.assertEqual(collect.call_count,1)
        job=WeatherCollectionJob.objects.get(uf="RJ")
        self.assertEqual((job.status,job.attempts),("pending",0))
        self.assertGreater(IntegrationSyncState.objects.get(key="weather-cooldown").change_cursor,timezone.now())

    def test_locality_reuses_normalized_city_and_utc_window_does_not_infer_cause(self):
        row=request_local("SÃO PAULO","SP","2026-10",self.user)
        again=request_local("sao paulo","SP","2026-10",self.user)
        self.assertEqual(row.pk,again.pk)
        at=datetime(2026,10,15,18,30,tzinfo=utc.utc)
        times=[int(datetime(2026,10,15,0,tzinfo=utc.utc).timestamp())+i*3600 for i in range(24)]
        row.status="success";row.payload={"hourly":{"time":times,**{k:[2 if k=="precipitation" else 20]*24 for k in HOURLY}}}
        result=incident_window(row,at)
        self.assertEqual(result["state"],"coincidence")
        self.assertFalse(result["causality"])
        self.assertEqual(len(result["hourly"]),8)
        row.payload["hourly"]["precipitation"][13]=None
        self.assertEqual(incident_window(row,at)["state"],"insufficient")

    def test_hourly_units_and_missing_hours_are_rejected(self):
        first=datetime(2026,10,1,tzinfo=utc.utc);end=first+timedelta(hours=2)
        hourly={"time":[int(first.timestamp()),int(first.timestamp())+3600],**{k:[0,0] for k in HOURLY}}
        payload={"hourly":hourly,"hourly_units":dict(HOURLY)}
        self.assertEqual(len(parse_hourly(payload,first,end)["time"]),2)
        payload["hourly_units"]["precipitation"]="in"
        with self.assertRaises(PublicDataError):parse_hourly(payload,first,end)

    def test_schedule_checks_actual_capacity_without_creating_jobs(self):
        from .forms import WeatherConfigurationForm
        from .services.geo_configuration import WEATHER_DEFAULTS
        from .services.geography import STATE_BY_UF
        values={**WEATHER_DEFAULTS,"enabled":True,"automatic":True,"scheduled_ufs":list(STATE_BY_UF),"daily_budget":1}
        form=WeatherConfigurationForm(values,user=self.user)
        self.assertFalse(form.is_valid());self.assertIn("daily_budget",form.errors)
        form=WeatherConfigurationForm({**values,"daily_budget":250},user=self.user)
        self.assertTrue(form.is_valid(),form.errors)
        self.assertFalse(WeatherCollectionJob.objects.exists())

    def test_municipal_ibge_normalization_never_guesses_ambiguous_or_routes(self):
        from django.utils import timezone
        from .models import GeographyCatalog
        from .services.weather_local import load_municipal_catalogs, resolve_municipality
        now=timezone.now()
        GeographyCatalog.objects.create(key="municipios-SP",states=[{"id":"3536208","nome":"Pariquera-Açu"},{"id":"3550308","nome":"São Paulo"}],source="ibge",synced_at=now)
        GeographyCatalog.objects.create(key="municipios-ES",states=[{"id":"3200201","nome":"Alegre"}],source="ibge",synced_at=now)
        GeographyCatalog.objects.create(key="municipios-BA",states=[{"id":"2925303","nome":"Porto Seguro"}],source="ibge",synced_at=now)
        GeographyCatalog.objects.create(key="municipios-AL",states=[{"id":"2704302","nome":"Maceió"}],source="ibge",synced_at=now)
        catalogs=load_municipal_catalogs(["SP","ES","BA","AL"])
        for raw,uf,expected_code in (("PARIQUERA - AÇU","SP","3536208"),("Alegre/ES","ES","3200201"),("CIDADE: Alegre","ES","3200201"),("São Paulo - SP","SP","3550308")):
            res=resolve_municipality(raw,uf,catalogs=catalogs)
            self.assertEqual(res["status"],"matched")
            self.assertEqual(res["ibge_code"],expected_code)
            self.assertTrue(res["eligible_for_collection"])
        for bad,uf,expected_status in (
            ("PONTA A: SÃO PAULO | PONTA B: SANTOS","SP","ambiguous_route"),
            ("N/A","SP","placeholder"),
            ("TRANCOSO","BA","unmatched"),
            ("ANTARES","AL","unmatched"),
        ):
            res=resolve_municipality(bad,uf,catalogs=catalogs)
            self.assertEqual(res["status"],expected_status)
            self.assertFalse(res["eligible_for_collection"])

    def test_enqueue_prioritized_locals_orders_by_incidents_and_skips_ineligible(self):
        from django.utils import timezone
        from .models import GeographyCatalog, WeatherLocalCache
        from .services.weather_local import enqueue_prioritized_locals
        now=timezone.now()
        GeographyCatalog.objects.create(key="municipios-SP",states=[{"id":"3550308","nome":"São Paulo"}],source="ibge",synced_at=now)
        GeographyCatalog.objects.create(key="municipios-RJ",states=[{"id":"3304557","nome":"Rio de Janeiro"}],source="ibge",synced_at=now)
        localities=[
            {"city":"Rio de Janeiro","uf":"RJ","incidents":1,"available":False,"eligible_for_collection":True},
            {"city":"TRANCOSO","uf":"BA","incidents":20,"available":False,"eligible_for_collection":False,"normalization_status":"unmatched"},
            {"city":"São Paulo","uf":"SP","incidents":9,"available":False,"eligible_for_collection":True},
        ]
        res=enqueue_prioritized_locals(self.user,localities,"2026-10",limit=20)
        self.assertEqual(res["queued"],2)
        self.assertEqual(res["items"][0]["uf"],"SP")
        self.assertEqual(WeatherLocalCache.objects.get(uf="SP",ibge_code="3550308").progress["priority_incidents"],9)
