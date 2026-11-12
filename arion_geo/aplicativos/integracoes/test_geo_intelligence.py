from datetime import date, timedelta
import gzip
from io import BytesIO
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .clients.public_data import ARCHIVE_ENDPOINTS, PublicDataClient, PublicDataError
from .forms import WeatherConfigurationForm
from .models import ExternalAPIIntegration, GeographyCatalog, WeatherMonth, WeatherCollectionJob
from .services.geography import REGIONS, STATES, catalog, fetch_catalog, region_states, sync_catalog
from .services.weather import collect_month, licensed_configuration, month_bounds, parse_daily, request_collection, sampling_points, summarize, worker_tick


def ibge_client():
    client = Mock()
    client.get.side_effect = [list(REGIONS),[{"id":s["id"],"sigla":s["sigla"],"nome":s["nome"],"regiao":{"id":s["region_id"]}} for s in STATES]]
    return client


def weather_response(start=date(2026,10,1),days=31,rain=2):
    return {"latitude":-23.5,"longitude":-46.5,"daily_units":{"precipitation_sum":"mm","wind_speed_10m_max":"km/h","wind_gusts_10m_max":"km/h"},
            "daily":{"time":[(start+timedelta(days=i)).isoformat() for i in range(days)],"precipitation_sum":[rain]*days,"wind_speed_10m_max":[20]*days,"wind_gusts_10m_max":[50]*days}}


class GeographyWeatherTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("geo-admin","geo@example.invalid","strong-test-password")
        self.integration = ExternalAPIIntegration.objects.create(provider="open_meteo",name="Weather",enabled=True,config={"mode":"evaluation","evaluation_authorized":True,"daily_budget":250})

    def test_offline_catalog_complete_and_southeast_exact(self):
        self.assertEqual(len(catalog()["regions"]),5)
        self.assertEqual(len(catalog()["states"]),27)
        self.assertEqual(set(region_states("SE")),{"SP","RJ","MG","ES"})
        self.assertEqual(region_states("3"),region_states("SE"))

    def test_ibge_validation_and_persistent_cache_preserved_on_failure(self):
        self.assertEqual(sync_catalog(ibge_client()),{"regions":5,"states":27})
        old = GeographyCatalog.objects.get().synced_at
        client = Mock();client.get.side_effect = PublicDataError("Serviço indisponível.")
        with self.assertRaises(PublicDataError):sync_catalog(client)
        self.assertEqual(GeographyCatalog.objects.get().synced_at,old)
        client = ibge_client();client.get.side_effect=[list(REGIONS),[]]
        with self.assertRaises(PublicDataError):fetch_catalog(client)

    @override_settings(ARION_PRODUCTION=True)
    def test_evaluation_cannot_collect_in_production_or_automatically(self):
        with self.assertRaises(PublicDataError):licensed_configuration(self.integration)
        with self.assertRaises(PublicDataError):licensed_configuration(self.integration,automatic=True)

    def test_commercial_license_key_and_endpoint_are_required(self):
        self.integration.config={"mode":"commercial","license_confirmed":True}
        with self.assertRaises(PublicDataError):licensed_configuration(self.integration)
        self.integration.secret_ciphertext="protected"
        self.integration.config["endpoint"]="https://attacker.invalid/"
        with self.assertRaises(PublicDataError):licensed_configuration(self.integration)
        with self.assertRaises(PublicDataError):PublicDataClient().get("http://127.0.0.1/admin")

    def test_http_client_accepts_bounded_gzip_without_exposing_request_secrets(self):
        response=Mock()
        response.headers={"Content-Encoding":"gzip"}
        response.read.return_value=gzip.compress(b'{"ok":true}')
        response.__enter__=Mock(return_value=response)
        response.__exit__=Mock(return_value=False)
        opener=Mock();opener.open.return_value=response
        with patch("aplicativos.integracoes.clients.public_data.build_opener",return_value=opener):
            result=PublicDataClient().get(ARCHIVE_ENDPOINTS["commercial"],{"apikey":"test-only-private"})
        self.assertEqual(result,{"ok":True})

    def test_parser_rejects_wrong_units_lengths_and_negative_values(self):
        for change in ("unit","length","negative"):
            payload=weather_response()
            if change=="unit":payload["daily_units"]["wind_speed_10m_max"]="m/s"
            elif change=="length":payload["daily"]["precipitation_sum"].pop()
            else:payload["daily"]["precipitation_sum"][0]=-1
            with self.assertRaises(PublicDataError):parse_daily(payload,date(2026,10,1),date(2026,10,31))

    def test_missing_data_is_not_zero_and_spatial_weights_not_sum_of_mm(self):
        a=parse_daily(weather_response(days=2,rain=2),date(2026,10,1),date(2026,10,2))
        b=parse_daily(weather_response(days=2,rain=6),date(2026,10,1),date(2026,10,2))
        a["area_km2"],b["area_km2"]=3,1
        result=summarize([a,b])
        self.assertEqual(result["precipitation_mm"],6) # 3 mm/day, not 8 mm/day
        self.assertEqual(result["rainy_days_percent"],100)
        self.assertEqual(result["gust_max_kmh"],50)
        for sample in (a,b):sample["daily"]["precipitation_sum"]=[None,None]
        self.assertIsNone(summarize([a,b])["precipitation_mm"])

    @patch("aplicativos.integracoes.services.weather.sampling_points",return_value=[{"latitude":-23.5,"longitude":-46.5,"area_km2":100}])
    def test_month_cache_avoids_duplicate_requests_and_jobs_are_idempotent(self,_points):
        client=Mock();client.get.return_value=weather_response()
        row=collect_month("SP",date(2026,10,1),self.integration,client=client)
        again=collect_month("SP",date(2026,10,1),self.integration,client=client)
        self.assertEqual(row.pk,again.pk)
        self.assertEqual(client.get.call_count,1)
        self.assertEqual(request_collection(["SP"],"2026-10",self.user),[])
        request_collection(["RJ"],"2026-10",self.user)
        request_collection(["RJ"],"2026-10",self.user)
        self.assertEqual(WeatherCollectionJob.objects.count(),1)

    def test_state_sampling_uses_multiple_interior_points_including_df(self):
        for uf in ("SP","DF"):
            points=sampling_points(uf)
            self.assertGreater(len(points),1)
            self.assertLessEqual(len(points),16)
            self.assertTrue(all(p["area_km2"]>0 for p in points))

    def test_failed_job_retains_cache_and_records_only_safe_error(self):
        job=WeatherCollectionJob.objects.create(uf="SP",period=date(2026,10,1))
        with patch("aplicativos.integracoes.services.weather.collect_month",side_effect=RuntimeError("apikey=PRIVATE-EXAMPLE")):
            worker_tick()
        job.refresh_from_db()
        self.assertNotIn("PRIVATE",job.last_error)
        self.assertEqual(job.status,"pending")
        self.assertEqual(job.attempts,1)

    def test_form_never_enables_automatic_evaluation(self):
        data={"enabled":"on","mode":"evaluation","endpoint":ARCHIVE_ENDPOINTS["evaluation"],"evaluation_authorized":"on","automatic":"on","interval_hours":168,"timeout":15,"retries":1,"cache_hours":720,"daily_budget":250}
        form=WeatherConfigurationForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn("automatic",form.errors)

    def test_connector_pages_and_secret_rendering(self):
        self.client.force_login(self.user)
        self.integration.secret_ciphertext="DO-NOT-RENDER-SECRET"
        self.integration.save()
        for provider in ("ibge","open_meteo"):
            response=self.client.get(reverse("configuracoes:integracoes:geo-detail",args=[provider]))
            self.assertEqual(response.status_code,200)
            self.assertNotContains(response,"DO-NOT-RENDER-SECRET")

    @patch("aplicativos.integracoes.services.weather.timezone.localdate",return_value=date(2026,11,12))
    def test_last_complete_month_and_partial_current(self,_today):
        self.assertEqual(month_bounds(),(date(2026,10,1),date(2026,10,31)))
        self.assertEqual(month_bounds("2026-11"),(date(2026,11,1),date(2026,11,12)))
