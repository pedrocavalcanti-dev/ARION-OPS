import json
from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from aplicativos.base_links.models import Projeto, Operadora
from aplicativos.configuracoes.models import GroupDataScope
from aplicativos.integracoes.models import CMDBLinkMirror, OTRSTicket, OTRSTicketConfigItem
from aplicativos.integracoes.services.incidents import IncidentRules
from .services.analytics import MapAnalyticsService
from .services.presentation import project_distribution


class GeoIntelligenceTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_superuser("geo-map","map@example.invalid","strong-password")
        self.links={}
        for i,uf in enumerate(("SP","RJ","MG","ES","BA")):
            self.links[uf]=CMDBLinkMirror.objects.create(source_config_item_id=f"geo-{i}",name=f"CLARO - BRADESCO - {uf}",state=uf,contracting_client="Claro",business_project="BRADESCO")
        self.other=CMDBLinkMirror.objects.create(source_config_item_id="vivo-link",name="VIVO - FUST",state="SP",contracting_client="Vivo",business_project="FUST")
        self.client.force_login(self.user)

    def ticket(self,number,uf="SP",reason="FalhaMassiva",status="RESOLVED",responsibility="PROCEDENTE"):
        stamp=datetime(2026,10,15,tzinfo=dt_timezone.utc)
        ticket=OTRSTicket.objects.create(external_id=number,ticket_number=str(number),queue_name="NOC - ATACADO",queue_external_id=107,external_state_id=number,external_state_name=status,business_status=status,responsibility_normalized=responsibility,source_created_at=stamp,terminal_state_at=stamp,closing_code_raw=reason)
        link=self.links[uf]
        OTRSTicketConfigItem.objects.create(ticket=ticket,cmdb_link=link,config_item_id=link.source_config_item_id)
        return ticket

    def test_region_filters_backend_links_tickets_and_rejects_incompatible_uf(self):
        self.ticket(1,"SP");self.ticket(2,"BA")
        result=MapAnalyticsService.query(self.user,{"region":"SE","mode":"calls","period":"2026-10"})
        self.assertEqual(set(result["states"]),{"SP","RJ","MG","ES"})
        self.assertEqual(result["ticket_count"],1)
        self.assertEqual(result["filtered_source_total"],5) # Four Claro + one Vivo in SP
        with self.assertRaises(ValueError):MapAnalyticsService.query(self.user,{"region":"SE","uf":"BA"})

    def test_valid_resolved_history_deduplicated_and_all_operational_exclusions(self):
        ticket=self.ticket(1)
        OTRSTicketConfigItem.objects.create(ticket=ticket,cmdb_link=self.links["RJ"],config_item_id=self.links["RJ"].source_config_item_id)
        for i,reason in enumerate(("Cancelado","SemIntervencaoTecnica","SemAtuacao","UNKNOWN"),2):self.ticket(i,reason=reason)
        self.ticket(10,status="UNKNOWN");self.ticket(11,responsibility="IMPROCEDENTE")
        self.ticket(12,reason="PendenciaFinanceira")
        result=MapAnalyticsService.query(self.user,{"mode":"calls","period":"2026-10","client":"Claro"})
        self.assertEqual(result["ticket_count"],2)
        self.assertEqual(sum(result["daily_counts"].values()),2)
        self.assertEqual(sum(result["technical_daily_counts"].values()),1)
        self.assertEqual(result["states"]["RJ"]["ticket_count"],1)

    def test_reason_and_category_use_same_selection_in_api_and_drawer(self):
        self.ticket(1,reason="FalhaMassiva");self.ticket(2,reason="RompimentoFibra")
        params={"mode":"calls","period":"2026-10","uf":"SP","category":"massiva"}
        response=self.client.get(reverse("base_links:map-aggregates"),params)
        self.assertEqual(response.json()["ticket_count"],1)
        params["tab"]="tickets"
        response=self.client.get(reverse("base_links:map-state-detail"),params)
        self.assertEqual(response.json()["ticket_total"],1)

    def test_weather_unavailable_does_not_break_operational_analysis(self):
        self.ticket(1)
        response=self.client.get(reverse("mapa:intelligence"),{"period":"2026-10","region":"SE","uf":"SP"})
        self.assertEqual(response.status_code,200)
        data=response.json()
        self.assertEqual(data["summary"]["ticket_count"],1)
        self.assertFalse(data["climate"]["available"])
        self.assertTrue(all(r["rain_mm"] is None for r in data["daily"]))

    def test_monthly_map_reuses_incident_aggregation_for_weather_comparison(self):
        self.ticket(1)
        with patch.object(MapAnalyticsService,"query",wraps=MapAnalyticsService.query) as query:
            response=self.client.get(reverse("base_links:map-aggregates"),{"mode":"calls","period":"2026-10","include_intelligence":"1"})
        self.assertEqual(response.status_code,200)
        self.assertEqual(query.call_count,1)
        payload=response.json()
        self.assertEqual(payload["ticket_count"],payload["geo_intelligence"]["summary"]["ticket_count"])

    def start(self):
        response=self.client.post(reverse("mapa:presentation-start"),{"client":"Claro","project":"BRADESCO","period":"2026-10"})
        self.assertEqual(response.status_code,302)
        return self.client.session["geo_presentation"]["key"]

    def test_presentation_sanitizes_payload_and_locks_scope_until_exit(self):
        self.ticket(1,reason="PendenciaFinanceira");self.ticket(2,reason="FalhaMassiva")
        key=self.start()
        page=self.client.get(reverse("mapa:presentation",args=[key]))
        self.assertEqual(page.status_code,200)
        data=self.client.get(reverse("mapa:presentation-data",args=[key]),{"client":"Claro","project":"BRADESCO","period":"2026-10","mode":"calls"})
        self.assertEqual(data.status_code,200)
        raw=data.content.decode().casefold()
        for reserved in ("financeir","pendenciafinanceira","closing_code_raw","title","account_counts"):
            self.assertNotIn(reserved,raw)
        self.assertEqual(data.json()["ticket_count"],2)
        self.assertEqual(sum(data.json()["distribution_projected"].values()),1)
        self.assertEqual(OTRSTicket.objects.get(external_id=1).closing_code_raw,"PendenciaFinanceira")
        self.assertEqual(self.client.get(reverse("mapa:presentation-data",args=[key]),{"client":"Vivo"}).status_code,403)
        self.assertEqual(self.client.get(reverse("base_links:map-aggregates")).status_code,403)
        self.assertEqual(self.client.get(reverse("mapa:presentation-data",args=["another-session"])).status_code,403)
        response=self.client.post(reverse("mapa:presentation-stop",args=[key]))
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.client.get(reverse("base_links:map-aggregates")).status_code,200)

    def test_presentation_requires_permission_and_scoped_client_project(self):
        user=get_user_model().objects.create_user("scoped-presenter",password="strong-password")
        group=Group.objects.create(name="Scoped presenters")
        group.permissions.add(*Permission.objects.filter(content_type__app_label="configuracoes",codename__in=("view_map","present_geo")))
        scope=GroupDataScope.objects.create(group=group,mode="custom")
        project=Projeto.objects.create(name="BRADESCO",normalized_name="bradesco")
        scope.allowed_projects.add(project)
        operator=Operadora.objects.create(name="Claro",normalized_name="claro")
        scope.allowed_operators.add(operator)
        user.groups.add(group)
        self.client.force_login(user)
        result=self.client.get(reverse("mapa:intelligence"),{"client":"Vivo","period":"2026-10"}).json()
        self.assertEqual(result["summary"]["ticket_count"],0)
        response=self.client.post(reverse("mapa:presentation-start"),{"client":"Vivo","project":"FUST","period":"2026-10"})
        self.assertEqual(response.status_code,200)
        self.assertNotIn("geo_presentation",self.client.session)
        group.permissions.remove(*Permission.objects.filter(codename="present_geo"))
        self.assertEqual(self.client.get(reverse("mapa:presentation-start")).status_code,403)

    def test_presentation_does_not_block_native_logout(self):
        self.start()
        response=self.client.post(reverse("autenticacao:logout"))
        self.assertEqual(response.status_code,302)
        self.assertNotIn("geo_presentation",self.client.session)

    def test_largest_remainder_projection_exact_total_and_invalid_weights(self):
        self.assertEqual(project_distribution(72,{"Massiva":40,"Rompimento":30,"Equipamento":30}),{"Massiva":29,"Rompimento":22,"Equipamento":21})
        for total in (0,1,2,3,71,10001):
            values=project_distribution(total,{"Massiva":40,"Rompimento":30,"Equipamento":30})
            self.assertEqual(sum(values.values()),total)
            self.assertTrue(all(v>=0 for v in values.values()))
        for weights in ({"Massiva":-1},{"Massiva":0},{"Massiva":"NaN"},{"Pendência Financeira":1}):
            with self.assertRaises(ValueError):project_distribution(1,weights)

    def test_main_authenticated_routes_and_connectors_render(self):
        for path in ("/painel/","/dashboard/","/cockpit/","/mapa/","/rankings/","/base/","/configuracoes/"):
            with self.subTest(path=path):
                response=self.client.get(path,follow=True)
                self.assertEqual(response.status_code,200)
