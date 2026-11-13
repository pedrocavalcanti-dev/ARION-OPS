import json
from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from aplicativos.base_links.models import Projeto, Operadora
from aplicativos.configuracoes.models import GroupDataScope
from aplicativos.integracoes.models import CMDBLinkMirror, CMDBOperatorMirror, OTRSTicket, OTRSTicketConfigItem, ExternalAPIIntegration, WeatherCollectionJob
from aplicativos.integracoes.services.incidents import IncidentRules
from .services.analytics import MapAnalyticsService
from .services.presentation import project_distribution, presentation_ufs


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
        self.assertIsNone(data["climate"]["coincident_days"])
        self.assertEqual(data["collection"]["configuration_status"],"ready")
        self.assertTrue(data["collection"]["can_collect"])

    def test_collection_states_follow_queue_without_network_or_simulated_cache(self):
        ExternalAPIIntegration.objects.update_or_create(provider="open_meteo",defaults={"name":"Weather","enabled":True,"config":{"mode":"public"}})
        url=reverse("mapa:intelligence")
        params={"period":"2026-10","uf":"SP"}
        with patch("aplicativos.integracoes.clients.public_data.PublicDataClient.get") as network:
            data=self.client.get(url,params).json()
            self.assertEqual(data["collection"]["status"],"awaiting_collection")
            self.assertTrue(data["collection"]["can_collect"])
            job=WeatherCollectionJob.objects.create(uf="SP",period=date(2026,10,1))
            for stored,expected in (("pending","queued"),("running","running"),("error","failed"),("success","awaiting_collection")):
                job.status=stored;job.save(update_fields=["status"])
                result=self.client.get(url,params).json()
                self.assertEqual(result["collection"]["status"],expected)
                self.assertFalse(result["climate"]["available"])
                self.assertFalse(result["collection"]["cache_available"])
            network.assert_not_called()

    def test_monthly_map_reuses_incident_aggregation_for_weather_comparison(self):
        self.ticket(1)
        with patch.object(MapAnalyticsService,"query",wraps=MapAnalyticsService.query) as query:
            response=self.client.get(reverse("base_links:map-aggregates"),{"mode":"calls","period":"2026-10","include_intelligence":"1"})
        self.assertEqual(response.status_code,200)
        self.assertEqual(query.call_count,1)
        payload=response.json()
        self.assertEqual(payload["ticket_count"],payload["geo_intelligence"]["summary"]["ticket_count"])

    def test_collection_reports_explicitly_disabled_configuration_before_any_network(self):
        ExternalAPIIntegration.objects.filter(provider="open_meteo").update(enabled=False)
        with patch("aplicativos.integracoes.clients.public_data.PublicDataClient.get") as network:
            response=self.client.post(reverse("mapa:weather-collect"),{"period":"2026-10","mode":"calls"})
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json()["status"],"disabled")
        self.assertIn("desabilitada",response.json()["message"])
        network.assert_not_called()

    def test_focused_motives_and_weather_share_created_month_even_if_resolved_later(self):
        ticket=self.ticket(211,reason="RompimentoFibra")
        ticket.terminal_state_at=datetime(2026,11,3,tzinfo=dt_timezone.utc)
        ticket.save(update_fields=["terminal_state_at"])
        params={"mode":"motives","cohort":"created","period":"2026-10","category":"rompimento","uf":"SP"}
        result=self.client.get(reverse("base_links:map-aggregates"),params).json()
        intelligence=self.client.get(reverse("mapa:intelligence"),params).json()
        detail=self.client.get(reverse("base_links:map-state-detail"),{**params,"tab":"tickets"}).json()
        self.assertEqual(result["ticket_count"],1)
        self.assertEqual(result["terminal_ticket_count"],intelligence["summary"]["ticket_count"])
        self.assertEqual(detail["ticket_total"],1)

    def test_combined_client_project_region_month_reason_category_and_isp(self):
        isp=CMDBOperatorMirror.objects.create(source_config_item_id="geo-provider",name="ISP do teste")
        link=self.links["BA"]
        link.contracting_client="Vivo";link.business_project="FUST";link.operator=isp
        link.save(update_fields=["contracting_client","business_project","operator"])
        self.ticket(301,"BA",reason="RompimentoFibra")
        self.ticket(302,"BA",reason="EquipamentoTravado")
        self.ticket(303,"SP",reason="RompimentoFibra")
        params={"client":"Vivo","project":"FUST","region":"NE","period":"2026-10","reason":"rompimento_fibra","category":"rompimento","provider":str(isp.pk)}
        data=self.client.get(reverse("mapa:intelligence"),params).json()
        self.assertEqual(data["summary"]["ticket_count"],1)
        self.assertEqual(data["summary"]["affected_link_count"],1)
        self.assertEqual(set(data["summary"]["states"]),{"BA"})
        self.assertEqual(sum(d["tickets"] for d in data["daily"]),1)
        self.assertEqual(data["summary"]["ticket_provider_counts"],{"ISP do teste":1})

    def test_reconciliation_keeps_unknown_geography_and_deduplicates_multiple_states(self):
        ticket=self.ticket(201)
        OTRSTicketConfigItem.objects.create(ticket=ticket,cmdb_link=self.links["RJ"],config_item_id="geo-1")
        self.links["BA"].state="Local não identificado"
        self.links["BA"].save(update_fields=["state"])
        self.ticket(202,"BA",reason="Motivo ainda desconhecido")
        result=MapAnalyticsService.query(self.user,{"period":"2026-10","mode":"calls"})
        q=result["reconciliation"]
        self.assertEqual(q,{"incidents":2,"classified":1,"unclassified":1,"with_uf":1,"without_uf":1,"affected_links":3,"recurrent_links":0,"state_occurrences":2})
        self.assertNotIn("",result["states"])
        self.assertEqual(sum(result["daily_counts"].values()),2)
        self.assertEqual(sum(result["technical_daily_counts"].values()),1)

    def test_unknown_classifications_remain_incidents_but_never_factual_weather_coincidence(self):
        self.ticket(401,reason="Classificação ainda não mapeada")
        self.ticket(402,reason="PendenciaFinanceira")
        self.ticket(403,reason="RompimentoFibra")
        result=self.client.get(reverse("mapa:intelligence"),{"period":"2026-10","uf":"SP"}).json()
        self.assertEqual(result["summary"]["ticket_count"],3)
        self.assertEqual(sum(result["summary"]["daily_counts"].values()),3)
        self.assertEqual(sum(result["summary"]["technical_daily_counts"].values()),1)
        self.assertEqual(sum(result["summary"]["states"]["SP"]["technical_daily_counts"].values()),1)

    def test_presentation_varies_month_project_and_modes_but_cannot_expand_client_scope(self):
        CMDBLinkMirror.objects.create(source_config_item_id="second-project",name="CLARO - FUST",state="BA",contracting_client="Claro",business_project="FUST")
        self.ticket(201,reason="PendenciaFinanceira")
        key=self.start()
        url=reverse("mapa:presentation-data",args=[key])
        for mode in ("base","projects","calls","repeat","motives","climate"):
            response=self.client.get(url,{"mode":mode,"project":"FUST","period":"2026-09","region":"NE"})
            self.assertEqual(response.status_code,200,(mode,response.content))
            self.assertEqual(response.json()["selected_project"],"FUST")
            self.assertNotIn("financeir",response.content.decode().casefold())
        self.assertEqual(self.client.get(url,{"client":"Vivo"}).status_code,403)
        self.assertEqual(self.client.get(url,{"project":"Projeto inexistente"}).status_code,403)
        self.assertEqual(self.client.get(url,{"category":"financeiro"}).status_code,403)
        self.assertEqual(self.client.get(url,{"reason":"pendencia_financeira"}).status_code,403)

    def start(self):
        response=self.client.post(reverse("mapa:presentation-start"),{"client":"Claro","project":"BRADESCO","period":"2026-10"})
        self.assertEqual(response.status_code,302)
        return self.client.session["geo_presentation"]["key"]

    def test_vivo_presentation_catalogs_and_endpoints_stay_within_client(self):
        response=self.client.post(reverse("mapa:presentation-start"),{"client":"Vivo","project":"FUST","period":"2026-10"})
        self.assertEqual(response.status_code,302)
        key=self.client.session["geo_presentation"]["key"]
        page=self.client.get(reverse("mapa:presentation",args=[key]))
        self.assertContains(page,"Cliente fixado: Vivo")
        self.assertNotContains(page,'data-client="Claro"')
        self.assertNotContains(page,'data-client="all"')
        for name in ("presentation-data","presentation-intelligence","presentation-detail"):
            url=reverse("mapa:"+name,args=[key])
            for mode in ("base","projects","calls","repeat","motives","climate"):
                response=self.client.get(url,{"mode":mode,"period":"2026-10","uf":"SP"})
                self.assertEqual(response.status_code,200,(name,mode,response.content))
                payload=response.json()
                if name=="presentation-data":
                    self.assertEqual(payload["clients"],["Vivo"])
                    self.assertEqual(payload["projects"],["FUST"])
                    self.assertEqual(payload["allowed_ufs"],["SP"])
                elif name=="presentation-intelligence":
                    self.assertEqual([s["sigla"] for s in payload["catalog"]["states"]],["SP"])
            for params in ({"client":"Claro"},{"client":"all"},{"project":"BRADESCO"},{"uf":"BA"},{"uf":"UNKNOWN"}):
                self.assertEqual(self.client.get(url,params).status_code,403,(name,params))

    def test_presentation_start_project_options_follow_selected_client(self):
        response=self.client.get(reverse("mapa:presentation-start"),{"client":"Vivo"})
        self.assertEqual(list(response.context["form"].fields["project"].choices),[("","Todos os projetos"),("FUST","FUST")])
        response=self.client.post(reverse("mapa:presentation-start"),{"client":"Vivo","project":"BRADESCO","period":"2026-10"})
        self.assertEqual(response.status_code,200)
        self.assertNotIn("geo_presentation",self.client.session)

    def test_presentation_link_without_state_does_not_grant_geography(self):
        self.other.state=""
        self.other.save(update_fields=["state"])
        self.assertEqual(presentation_ufs(self.user,{"client":"Vivo"}),[])

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

    def test_presentation_counts_define_distinct_incidents_and_respect_operational_filters(self):
        self.ticket(501,reason="FalhaMassiva")
        self.ticket(502,reason="PendenciaFinanceira")
        self.ticket(503,status="OPEN",reason="RompimentoFibra")
        key=self.start()
        url=reverse("mapa:presentation-data",args=[key])
        result=self.client.get(url,{"period":"2026-10","mode":"calls","status":"RESOLVED","include_intelligence":"1"}).json()
        self.assertEqual(result["source_total"],5)
        self.assertEqual(result["filtered_source_total"],5)
        self.assertEqual(result["ticket_count"],2)
        self.assertEqual(sum(result["daily_counts"].values()),2)
        self.assertEqual(sum(result["category_counts"].values()),1)
        self.assertEqual(sum(result["technical_daily_counts"].values()),1)
        self.assertEqual(sum(result["distribution_projected"].values()),1)
        self.assertIn("TicketID",result["definitions"]["ticket_count"])
        raw=json.dumps(result).casefold()
        for reserved in ("financeir","closing_code_raw","title","assignees","account_counts"):
            self.assertNotIn(reserved,raw)

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
