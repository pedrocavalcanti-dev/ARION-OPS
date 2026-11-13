from datetime import timedelta

from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone

from aplicativos.base_links.models import Operadora, Projeto
from aplicativos.auditoria.models import AuditEvent
from aplicativos.configuracoes.models import GroupDataScope
from aplicativos.integracoes.models import CMDBLinkMirror
from .models import QualityMonitoringCycle, QualityOrder
from .services.dashboard import get_dashboard_data
from .services.filters import quality_period
from .services.monitoring import get_monitoring_queue
from .services.orders_queue import get_quality_orders_queue
from .services.priority_links import get_priority_links, business_priority
from .tests import QualityModuleTestCase


class QualityFilterTests(QualityModuleTestCase):
    def setUp(self):
        self.user=self.create_authorized_user("quality-filter-user")
        self.now=timezone.now()
        self.links=[]
        for index,(client,project,uf) in enumerate((("Claro","PROJETO BB","São Paulo"),("Claro","BB","SP"),("Vivo","BDN","RJ"),("Vivo","Arcos Dourados","AM"),("Algar","FUST","RS"))):
            link=CMDBLinkMirror.objects.create(source_config_item_id=f"FILTER-{index}",designation=f"Circuito {index}",contracting_client=client,business_project=project,state=uf,isp_raw="ISP independente")
            self.links.append(link)
            self.create_ticket(link=link,external_id=97000+index*10,created_at=self.now-timedelta(days=12))
            self.create_ticket(link=link,external_id=97001+index*10,created_at=self.now-timedelta(days=2))
        self.client.force_login(self.user)

    def test_default_window_and_counts_match_dashboard_and_priority(self):
        self.create_ticket(link=self.links[0],external_id=97999,created_at=self.now-timedelta(days=45))
        default=get_priority_links(self.user,{"client":"Claro","project":"BB"})
        sixty=get_priority_links(self.user,{"client":"Claro","project":"BB","period":"60"})
        self.assertEqual(default["period"],"30")
        self.assertEqual(sum(row.valid_occurrence_count for row in default["rows"]),4)
        self.assertEqual(sum(row.valid_occurrence_count for row in sixty["rows"]),5)
        dashboard=get_dashboard_data(self.user,{"cliente":"Claro","projeto":"BB"})
        self.assertEqual(dashboard["filters"]["period"],"30")
        self.assertEqual(dashboard["period_label"],"30 dias")
        self.assertEqual({v for v,_ in default["period_options"]},{"7","15","30","60","90","custom"})

    def test_custom_window_is_inclusive_and_shared_by_views(self):
        end=(self.now-timedelta(days=10)).date().isoformat()
        start=(self.now-timedelta(days=14)).date().isoformat()
        params={"client":"Claro","project":"BB","period":"custom","date_from":start,"date_to":end}
        data=get_priority_links(self.user,params)
        self.assertEqual(sum(row.valid_occurrence_count for row in data["rows"]),2)
        dashboard=get_dashboard_data(self.user,{**params,"cliente":"Claro","projeto":"BB"})
        self.assertEqual(dashboard["period_label"],data["period_label"])
        detail=self.client.get(reverse("qualidade:priority_link_detail",args=[self.links[0].source_config_item_id]),params)
        self.assertEqual(detail.status_code,200)
        self.assertEqual(detail.context["valid_occurrence_count"],1)

    def test_fifteen_days_and_invalid_custom_have_controlled_feedback(self):
        self.assertEqual(quality_period({"period":"15"})["period_label"],"15 dias")
        for dates in (("2026-99-99","2026-01-01"),("2026-12-31","2026-01-01"),("", "")):
            window=quality_period({"period":"custom","date_from":dates[0],"date_to":dates[1]})
            self.assertEqual(window["period"],"30")
            self.assertTrue(window["notices"])

    def test_projects_depend_on_contracting_client_and_aliases_are_not_duplicated(self):
        data=get_priority_links(self.user,{"client":"Claro","project":"BB"})
        self.assertEqual(data["filter_options"]["projects"],["Banco do Brasil"])
        self.assertEqual(data["candidate_count"],2)
        vivo=get_priority_links(self.user,{"client":"Vivo","project":"BDN"})
        self.assertEqual(vivo["selected"]["project"],"Bradesco")
        self.assertEqual(vivo["candidate_count"],1)
        self.assertNotIn("Banco do Brasil",vivo["filter_options"]["projects"])

    def test_operator_change_clears_incompatible_project_with_feedback(self):
        data=get_priority_links(self.user,{"client":"Vivo","project":"BB"})
        self.assertEqual(data["selected"]["project"],"")
        self.assertEqual(data["candidate_count"],2)
        self.assertTrue(data["filter_notices"])

    def test_official_regions_and_state_names_filter_consistently(self):
        north=get_priority_links(self.user,{"region":"N","client":"Vivo"})
        self.assertEqual(north["filter_options"]["ufs"],["AM"])
        self.assertEqual([row.source_config_item_id for row in north["rows"]],[self.links[3].source_config_item_id])
        southeast=get_priority_links(self.user,{"region":"SE","uf":"SP"})
        self.assertEqual(southeast["candidate_count"],2)
        corrected=get_priority_links(self.user,{"region":"N","uf":"SP"})
        self.assertEqual(corrected["selected"]["uf"],"")
        self.assertTrue(corrected["filter_notices"])

    def test_valid_combination_without_records_is_empty(self):
        data=get_priority_links(self.user,{"client":"Claro","project":"BB","region":"N"})
        self.assertEqual(data["selected"]["project"],"Banco do Brasil")
        self.assertEqual(data["candidate_count"],0)
        self.assertFalse(data["filter_notices"])

    def test_portfolio_uses_client_instead_of_isp_and_preserves_priority(self):
        data=get_priority_links(self.user,{"client":"Vivo","project":"Bradesco","portfolio":"vivo"})
        self.assertEqual(data["candidate_count"],1)
        self.assertEqual(data["rows"][0].business_priority,30)
        self.assertEqual(business_priority("Outro ISP","BB","Claro"),10)
        self.assertEqual(business_priority("Vivo","BB","Algar"),99)
        self.assertEqual(business_priority("Vivo","Bradesco",""),99)
        self.assertEqual(business_priority("Outro ISP","FUST","Algar"),90)
        conflict=get_priority_links(self.user,{"client":"Claro","portfolio":"vivo"})
        self.assertEqual(conflict["candidate_count"],0)
        self.assertTrue(conflict["filter_notices"])

    def test_old_open_order_and_monitoring_remain_in_operational_stock(self):
        link=CMDBLinkMirror.objects.create(source_config_item_id="FILTER-OLD",designation="Ordem antiga",contracting_client="Vivo",business_project="Bradesco",state="SP")
        order=QualityOrder.objects.create(link=link,ci_snapshot=link.source_config_item_id,client_snapshot="Vivo",project_snapshot="Bradesco",uf_snapshot="SP")
        QualityOrder.objects.filter(pk=order.pk).update(created_at=self.now-timedelta(days=100))
        cycle=QualityMonitoringCycle.objects.create(quality_order=order,cycle_number=1,started_at=self.now-timedelta(days=80),expected_end_at=self.now-timedelta(days=50))
        priority=get_priority_links(self.user,{"client":"Vivo","project":"Bradesco"})
        self.assertIn(link.source_config_item_id,{row.source_config_item_id for row in priority["rows"]})
        self.assertIn(order.pk,{row.pk for row in get_quality_orders_queue(self.user,{})["rows"]})
        self.assertIn(cycle.pk,{row.pk for row in get_monitoring_queue(self.user,{})["cycles"]})
        self.assertEqual(get_dashboard_data(self.user)["kpis"]["open_orders"],1)
        historical={"period":"custom","date_from":"2025-01-01","date_to":"2025-01-31"}
        self.assertIn(cycle.pk,{row.pk for row in get_monitoring_queue(self.user,historical)["cycles"]})

    def test_history_window_preserves_system_authors_and_excludes_old_events(self):
        order=QualityOrder.objects.create(link=self.links[0],ci_snapshot=self.links[0].source_config_item_id)
        AuditEvent.objects.create(category="QUALITY",action="ORDER_CREATED",object_type="QualityOrder",object_id=str(order.pk),description="Evento automático")
        old=AuditEvent.objects.create(category="QUALITY",action="ORDER_CREATED",object_type="QualityOrder",object_id=str(order.pk),description="Evento antigo")
        AuditEvent.objects.filter(pk=old.pk).update(timestamp=self.now-timedelta(days=45))
        response=self.client.get(reverse("qualidade:historico"))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,"Sistema")
        self.assertNotContains(response,"Evento antigo")
        response=self.client.get(reverse("qualidade:historico"),{"period":"60"})
        self.assertContains(response,"Evento antigo")

    def test_dimensions_cannot_expand_custom_data_scope(self):
        self.user.groups.clear()
        group=Group.objects.create(name="Quality filters Claro scope")
        scope=GroupDataScope.objects.create(group=group,mode=GroupDataScope.Mode.CUSTOM)
        scope.allowed_operators.add(Operadora.objects.create(name="Claro",normalized_name="claro"))
        scope.allowed_projects.add(Projeto.objects.create(name="BANCO DO BRASIL",normalized_name="banco do brasil"))
        self.user.groups.add(group)
        data=get_priority_links(self.user,{"client":"Vivo"})
        self.assertEqual(data["candidate_count"],0)
        self.assertNotIn("Vivo",data["filter_options"]["clients"])
        self.assertNotIn("Bradesco",data["filter_catalog"]["projects"])

    def test_default_pages_and_period_chip_clear_to_thirty_days(self):
        for name in ("dashboard","links_prioritarios","monitoramento","historico","ordens"):
            response=self.client.get(reverse("qualidade:"+name))
            self.assertEqual(response.status_code,200,name)
            if name!="ordens":
                self.assertContains(response,'value="30" selected')
        response=self.client.get(reverse("qualidade:links_prioritarios"),{"period":"60","client":"Vivo","region":"SE"})
        chips=response.context["filter_chips"]
        self.assertEqual(len(chips),3)
        self.assertIn("period=30",next(chip["remove_url"] for chip in chips if chip["label"]=="Período"))

    def test_quality_permission_is_still_required(self):
        self.user.user_permissions.clear()
        self.assertEqual(self.client.get(reverse("qualidade:links_prioritarios")).status_code,403)

    def test_unclassified_project_keeps_old_active_order_in_other_portfolio(self):
        link=CMDBLinkMirror.objects.create(source_config_item_id="NO-PROJECT",contracting_client="Outra operadora",state="SP")
        QualityOrder.objects.create(link=link,ci_snapshot=link.source_config_item_id,client_snapshot=link.contracting_client)
        data=get_priority_links(self.user,{"client":"Outra operadora","portfolio":"other"})
        self.assertEqual(data["candidate_count"],1)
        self.assertFalse(data["filter_notices"])

    def test_primary_filters_do_not_force_advanced_popover_open(self):
        for name in ("ordens", "monitoramento", "historico"):
            response = self.client.get(reverse("qualidade:" + name), {"client": "Vivo"})
            self.assertEqual(response.status_code, 200, name)
            self.assertNotContains(response, '<details class="quality-list-filter-details" open>')
