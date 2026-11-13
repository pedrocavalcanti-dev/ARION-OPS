from datetime import date, datetime, timedelta, timezone as utc
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from aplicativos.integracoes.models import WeatherMonth, WeatherLocalCache, OTRSTicketConfigItem
from aplicativos.integracoes.services.weather import parse_daily, summarize
from aplicativos.integracoes.services.weather_local import HOURLY
from aplicativos.integracoes.test_geo_intelligence import weather_response
from . import test_geo_intelligence as geo_fixtures
from .services.geo_intelligence import intelligence_payload, weather_range, climate_rankings
from .services.analytics import MapAnalyticsService


class WeatherContextTests(TestCase):
    setUp=geo_fixtures.GeoIntelligenceTests.setUp
    ticket=geo_fixtures.GeoIntelligenceTests.ticket

    def cache(self,uf,first,days=31,rain=2,area=1):
        sample=parse_daily(weather_response(first,days,rain),first,first+timedelta(days=days-1));sample["area_km2"]=area
        summary=summarize([sample]);summary["partial"]=False
        return WeatherMonth.objects.create(uf=uf,region="3",period=first,source="open_meteo_public",model="era5",method="area-grid-v2",samples=[sample],summary=summary,completeness=1,collected_at=timezone.now(),expires_at=timezone.now()+timedelta(days=2))

    def test_national_denominator_is_27_and_partial_is_explicit(self):
        self.cache("SP",date(2026,10,1))
        result=intelligence_payload(self.user,{"period":"2026-10"})
        self.assertEqual(result["climate"]["expected_ufs"],27)
        self.assertEqual(result["climate"]["complete_ufs"],1)
        self.assertTrue(result["climate"]["partial"])
        self.assertIn("1 de 27 UFs",result["climate"]["coverage_label"])
        self.assertLess(result["climate"]["territorial_percent"],4)
        self.assertEqual(result["weather"]["SP"]["precipitation_mm"],62)
        self.assertEqual(result["collection"]["requests_today"],0)
        self.assertIsNotNone(result["climate"]["last_update"])

    def test_multi_month_clips_days_and_deduplicates_ticket_ids(self):
        self.cache("SP",date(2026,9,1),days=30,rain=0)
        self.cache("SP",date(2026,10,1),rain=2)
        ticket=self.ticket(201)
        OTRSTicketConfigItem.objects.create(ticket=ticket,cmdb_link=self.links["RJ"],config_item_id=self.links["RJ"].source_config_item_id)
        params={"period":"custom","date_from":"2026-09-15","date_to":"2026-10-15"}
        result=intelligence_payload(self.user,params)
        self.assertEqual(len(result["daily"]),31)
        self.assertEqual(result["weather"]["SP"]["precipitation_mm"],30)
        self.assertEqual(result["summary"]["ticket_count"],1)
        self.assertEqual(sum(result["summary"]["daily_counts"].values()),1)
        self.assertEqual(result["daily"][0]["rain_mm"],0)
        self.assertNotIn("RJ",result["weather"])

    def test_rankings_exclude_partial_and_old_cache(self):
        rows={"SP":{"precipitation_mm":100,"rainy_days":10,"completeness":1,"partial":False,"stale":False},
              "RJ":{"precipitation_mm":200,"completeness":.5,"partial":False,"stale":False},
              "MG":{"precipitation_mm":300,"completeness":1,"partial":False,"stale":True}}
        self.assertEqual(climate_rankings(rows)["precipitation_mm"],[{"uf":"SP","value":100}])

    def test_local_weather_security_and_financial_projection_never_correlate(self):
        link=self.links["SP"];link.city="São Paulo";link.save()
        self.ticket(301,reason="RompimentoFibra")
        self.ticket(302,reason="PendenciaFinanceira")
        at=datetime(2026,10,14,tzinfo=utc.utc)
        times=[int(at.timestamp())+i*3600 for i in range(48)]
        WeatherLocalCache.objects.create(location_key="SP:sao paulo",city="São Paulo",uf="SP",ibge_code="3550308",period=date(2026,10,1),source="open_meteo_public",status="success",collected_at=timezone.now(),expires_at=timezone.now()+timedelta(days=1),payload={"hourly":{"time":times,**{k:[2 if k=="precipitation" else 20]*48 for k in HOURLY}}})
        result=MapAnalyticsService.query(self.user,{"mode":"calls","period":"2026-10"})["correlation"]
        self.assertEqual((result["eligible_tickets"],result["covered_tickets"],result["rain"]),(1,1,1))
        self.assertEqual(result["ruptures_rain"],1)
        self.assertFalse(result["causality"])
        url=reverse("mapa:weather-local")
        self.assertEqual(self.client.get(url,{"uf":"SP","city":"Cidade inventada","period":"2026-10"}).status_code,403)
        response=self.client.get(url,{"uf":"SP","city":"São Paulo","period":"2026-10","ticket":"301"})
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json()["window"]["hourly"])
        self.assertEqual(self.client.get(url,{"uf":"SP","city":"São Paulo","period":"2026-10","ticket":"302"}).status_code,403)

    def test_base_ignores_otrs_filters_and_providers_follow_project(self):
        params={"mode":"base","category":"rompimento","status":"OPEN","reason":"RompimentoFibra"}
        self.assertEqual(MapAnalyticsService.query(self.user,params)["filtered_source_total"],6)
        with self.assertRaises(ValueError):
            MapAnalyticsService.query(self.user,{"mode":"calls","period":"custom","date_from":"2026-10-01","date_to":"2099-01-01"})

    def test_map_batch_requires_preview_and_does_not_enqueue_on_first_post(self):
        from aplicativos.integracoes.models import WeatherCollectionJob
        url=reverse("mapa:weather-collect")
        values={"period":"2026-10"}
        first=self.client.post(url,values)
        self.assertEqual(first.json()["status"],"preview")
        self.assertFalse(WeatherCollectionJob.objects.exists())
        second=self.client.post(url,{**values,"preview_token":first.json()["preview_token"]})
        self.assertEqual(second.status_code,202)
        self.assertEqual(WeatherCollectionJob.objects.count(),27)

    def test_presentation_has_fixed_client_and_no_foreign_localities(self):
        link=self.links["SP"];link.city="São Paulo";link.save()
        self.other.city="Campinas";self.other.save()
        self.ticket(401)
        self.client.post(reverse("mapa:presentation-start"),{"client":"Claro","project":"","period":"2026-10"})
        key=self.client.session["geo_presentation"]["key"]
        response=self.client.get(reverse("mapa:presentation-intelligence",args=[key]))
        self.assertEqual(response.status_code,200)
        data=response.json()
        self.assertFalse(any(x["city"]=="Campinas" for x in data["correlation"]["localities"]))
        self.assertEqual(self.client.get(reverse("mapa:presentation-intelligence",args=[key]),{"client":"Vivo"}).status_code,403)
        self.assertEqual(self.client.get(reverse("mapa:weather-local"),{"uf":"SP","city":"São Paulo","period":"2026-10"}).status_code,403)

    def test_local_endpoint_rechecks_scope_permissions_and_csrf(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group, Permission
        from django.test import Client
        from aplicativos.base_links.models import Projeto, Operadora
        from aplicativos.configuracoes.models import GroupDataScope
        link=self.links["SP"];link.city="São Paulo";link.save()
        self.other.city="Campinas";self.other.save()
        user=get_user_model().objects.create_user("local-scoped",password="strong-password")
        group=Group.objects.create(name="Municipal scoped")
        group.permissions.add(Permission.objects.get(codename="view_map",content_type__app_label="configuracoes"))
        scope=GroupDataScope.objects.create(group=group,mode="custom")
        scope.allowed_projects.add(Projeto.objects.create(name="BRADESCO",normalized_name="bradesco"))
        scope.allowed_operators.add(Operadora.objects.create(name="Claro",normalized_name="claro"))
        user.groups.add(group);self.client.force_login(user)
        url=reverse("mapa:weather-local");values={"uf":"SP","city":"São Paulo","period":"2026-10"}
        self.assertEqual(self.client.get(url,values).status_code,200)
        self.assertEqual(self.client.get(url,{**values,"city":"Campinas"}).status_code,403)
        self.assertEqual(self.client.post(url,values).status_code,403)
        csrf=Client(enforce_csrf_checks=True);csrf.force_login(self.user)
        self.assertEqual(csrf.post(url,values).status_code,403)

    def test_incident_near_month_boundary_uses_application_local_calendar(self):
        from .services.weather_context import correlate
        stamp=datetime(2026,11,1,2,tzinfo=utc.utc)  # 31/10, 23h in São Paulo.
        times=[int((stamp-timedelta(hours=6)).timestamp())+i*3600 for i in range(9)]
        WeatherLocalCache.objects.create(location_key="SP:sao paulo",city="São Paulo",uf="SP",period=date(2026,10,1),source="open_meteo_public",status="success",payload={"hourly":{"time":times,**{key:[2 if key=="precipitation" else 20]*9 for key in HOURLY}}})
        events=[{"city":"São Paulo","uf":"SP","at":stamp,"ticket":1,"link":1,"category":"rompimento"}]
        result=correlate(events,date(2026,10,1),date(2026,10,31),1)
        self.assertEqual((result["covered_tickets"],result["rain"]),(1,1))

    def test_multi_level_correlation_states_denominators_and_local_rankings(self):
        from .services.weather_context import correlate
        stamp=datetime(2026,10,14,15,tzinfo=utc.utc)
        times=[int((stamp-timedelta(hours=6)).timestamp())+i*3600 for i in range(10)]
        WeatherLocalCache.objects.create(
            location_key="SP:sao paulo",city="São Paulo",uf="SP",ibge_code="3550308",period=date(2026,10,1),source="open_meteo_public",status="success",
            payload={"summary":{"precipitation_mm":45.0,"rainy_days":4,"daily":[{"date":"2026-10-14","precipitation_sum":15.0,"wind_gusts_10m_max":48.0}]},"hourly":{"time":times,**{key:[3 if key=="precipitation" else 20]*10 for key in HOURLY}}},
        )
        WeatherLocalCache.objects.create(
            location_key="RJ:rio de janeiro",city="Rio de Janeiro",uf="RJ",ibge_code="3304557",period=date(2026,10,1),source="open_meteo_public",status="success",
            payload={"summary":{"precipitation_mm":10.0,"rainy_days":1,"daily":[{"date":"2026-10-14","precipitation_sum":0.0,"wind_gusts_10m_max":15.0}]},"hourly":{"time":times,**{key:[0.0 if key=="precipitation" else 20]*10 for key in HOURLY}}},
        )
        events=[
            {"city":"São Paulo","uf":"SP","at":stamp,"ticket":1,"link":1,"category":"rompimento"},
            {"city":"Rio de Janeiro","uf":"RJ","at":stamp,"ticket":2,"link":2,"category":"massiva"},
            {"city":"","uf":"MG","at":stamp,"ticket":3,"link":3,"category":"energia"},
            {"city":"Curitiba","uf":"PR","at":stamp,"ticket":4,"link":4,"category":"equipamento"},
        ]
        result=correlate(events,date(2026,10,1),date(2026,10,31),4,scoped_cities={("SP","São Paulo"),("RJ","Rio de Janeiro")})
        self.assertEqual(result["status_counts"],{"insufficient":2,"none":1,"coincidence":1,"confirmed_cause":0})
        self.assertEqual(result["by_category"]["rompimento"]["covered"],1)
        self.assertEqual(result["by_category"]["rompimento"]["coincident"],1)
        self.assertEqual(result["by_category"]["energia"]["without_location"],1)
        self.assertEqual(result["by_category"]["equipamento"]["without_cache"],1)
        self.assertEqual(result["uf_only_tickets"],1)
        self.assertFalse(result["causality"])
        self.assertEqual(result["local_rankings"]["ruptures_rain"][0]["city"],"São Paulo")
        self.assertEqual(result["local_rankings"]["precipitation_mm"][0]["value"],45.0)
