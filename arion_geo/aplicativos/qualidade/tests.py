from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aplicativos.base_links.models import Operadora, Projeto
from aplicativos.auditoria.models import AuditEvent
from aplicativos.configuracoes.models import GroupDataScope
from aplicativos.integracoes.models import CMDBLinkMirror, CMDBOperatorMirror, ExternalAPIHealthCheck, ExternalAPIIntegration, Integration, OTRSStateMapping, OTRSTicket, OTRSTicketConfigItem
from aplicativos.integracoes.clients.brevo import BrevoResult
from aplicativos.integracoes.clients.brevo_smtp import SMTPResult
from aplicativos.integracoes.otrs_queues import NOC_QUEUE_NAME

from .models import QualityAction, QualityAuditEvent, QualityContact, QualityCorrelationCheckpoint, QualityMonitoringCycle, QualityOccurrence, QualityOrder, QualityTicket, QualityTreatment, QualityTreatmentIdentity
from .services.data_scope import QualityDataScopeService
from .services.dashboard import _allowed_granularities, _make_buckets, _resolve_granularity, get_dashboard_data
from .services.contacts import get_quality_contacts, recommended_contacts_for_order
from .services.history import get_global_quality_history, get_order_history
from .services.monitoring import finish_monitoring_cycle, get_monitoring_queue, start_monitoring_cycle
from .services.orders import create_quality_order, update_quality_order_field
from .services.orders_queue import get_quality_orders_queue
from .services.priority_links import business_priority, get_priority_links
from .services.recurrence import build_occurrence_episodes, build_quality_recurrence_analysis
from .services.order_detail import get_quality_order_detail
from .services.tq_delivery import claim_quality_treatment_send, record_tq_send_accepted, record_tq_send_failure
from .services.tq_email import build_treatment_email
from .services.tq_correlation import build_quality_treatment_email_payload, extract_correlation_ids, link_quality_ticket_from_article
from .services.tq_otrs_correlation import reconcile_quality_articles, refresh_linked_quality_ticket_states
from .services.treatments import complete_quality_treatment, create_quality_treatment, link_quality_ticket, toggle_quality_action


User = get_user_model()


class _IframeSrcdocParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.srcdoc = ""

    def handle_starttag(self, tag, attrs):
        if tag == "iframe":
            self.srcdoc = dict(attrs).get("srcdoc", "")


class QualityModuleTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.view_permission = Permission.objects.get(
            content_type__app_label="qualidade",
            codename="view_quality",
        )

    def create_authorized_user(self, username="quality-user"):
        user = User.objects.create_user(username=username, password="unused-test-password")
        user.user_permissions.add(self.view_permission)
        all_data_group = Group.objects.create(name=f"Quality global test {username}")
        GroupDataScope.objects.create(group=all_data_group, mode=GroupDataScope.Mode.ALL)
        user.groups.add(all_data_group)
        return user

    def create_ticket(self, *, link, external_id, status=OTRSStateMapping.BusinessStatus.OPEN, responsibility="PROCEDENTE", closing_reason="", created_at=None):
        ticket = OTRSTicket.objects.create(
            external_id=external_id,
            ticket_number=f"2026{external_id:010d}",
            title=f"Ticket {external_id}",
            queue_external_id=1,
            queue_name=NOC_QUEUE_NAME,
            external_state_id=1,
            external_state_name=status,
            business_status=status,
            responsibility_normalized=responsibility,
            closing_code_raw=closing_reason,
            source_created_at=created_at or timezone.now(),
        )
        OTRSTicketConfigItem.objects.create(
            ticket=ticket,
            config_item_id=link.source_config_item_id,
            cmdb_link=link,
            relation_type="DependsOn",
            relation_state="Valid",
        )
        return ticket

    def prepare_treatment_for_rreip(self, user, link, order, *, ticket_id):
        treatment = create_quality_treatment(quality_order=order, reason="Tratativa homologada", actor=user)
        ticket = self.create_ticket(link=link, external_id=ticket_id)
        treatment.status = QualityTreatment.Status.IN_PROGRESS
        treatment.send_status = QualityTreatment.SendStatus.SENT
        treatment.quality_ticket_external_id = ticket.external_id
        treatment.quality_ticket_number = ticket.ticket_number
        treatment.quality_ticket_queue_snapshot = ticket.queue_name
        treatment.management_summary = "Ações corretivas registradas e acompanhadas no OTRS."
        treatment.management_situation = QualityTreatment.ManagementSituation.READY_MONITORING
        treatment.save(update_fields=("status", "send_status", "quality_ticket_external_id", "quality_ticket_number", "quality_ticket_queue_snapshot", "management_summary", "management_situation", "active_order_key", "updated_at"))
        return treatment, ticket


class QualityTreatmentCreationTests(QualityModuleTestCase):
    def setUp(self):
        self.user = self.create_authorized_user('quality-tq-manager')
        self.user.user_permissions.add(Permission.objects.get(
            content_type__app_label='qualidade', codename='manage_quality_orders'))
        self.link = CMDBLinkMirror.objects.create(source_config_item_id=
            'QUALITY-TQ-CI', designation='VIVO.PI.LIVR000267', contracting_client=
            'Operadora Alfa', business_project='Projeto Alfa')
        self.order = QualityOrder.objects.create(link=self.link, current_reason=
            'ReincidÃªncia confirmada')
        self.support_ticket = self.create_ticket(link=self.link, external_id=91231)
        from .services.tq_otrs_correlation import quality_queue_names
        Integration.objects.create(key=Integration.Key.OTRS, name='OTRS', kind=
            'MySQL', enabled=True, username='test-reader', secret_ciphertext=
            'protected-test', config={'host': 'otrs.test', 'database': 'otrs'})
        for queue_name in quality_queue_names():
            QualityCorrelationCheckpoint.objects.create(queue_name=queue_name,
                last_success_at=timezone.now())
        self.client.force_login(self.user)
        return None

    def test_quality_dashboard_loads_its_es_module_entrypoint_and_modern_pwa_meta(self):
        response = self.client.get(reverse('qualidade:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response,
            'id="arion-echarts-vendor" src="/static/vendor/echarts/echarts.min.js"')
        self.assertContains(response, 'name="mobile-web-app-capable" content="yes"')
        self.assertContains(response,
            'name="apple-mobile-web-app-capable" content="yes"')
        html = response.content.decode('utf-8')
        self.assertRegex(html,
            r'type="module" src="/static/(?:js/qualidade/dashboard\.js|arion_build/qualidade/dashboard\.[A-Za-z0-9_.-]+\.js)"')
        self.assertLess(html.index('id="arion-echarts-vendor"'), html.index(
            'type="module" src="/static/'))
        source_root = Path(__file__).resolve().parents[3
            ] / 'frontend_src' / 'js' / 'qualidade'
        chart_source = (source_root / 'charts.js').read_text(encoding='utf-8')
        dashboard_source = (source_root / 'dashboard.js').read_text(encoding='utf-8')
        self.assertNotRegex(chart_source, 'from\\s+[\'\\"]echarts/')
        self.assertIn('window.echarts', chart_source)
        self.assertNotIn('echarts.use(', chart_source)
        self.assertIn('from "./charts.js"', dashboard_source)
        return None

    def test_authorized_post_creates_unique_immutable_tq_and_keeps_support_separate(self):
        response = self.client.post(reverse('qualidade:tq_gerar', kwargs={'codigo':
            self.order.code}), {'reason': 'Investigar a causa raiz confirmada',
            'description': 'Validar aÃ§Ã£o corretiva', 'support_ticket': self.
            support_ticket.pk})
        self.assertEqual(response.status_code, 302)
        treatment = QualityTreatment.objects.get()
        self.assertRegex(treatment.code, '^TQ-\\d{4}-\\d{6}$')
        self.assertEqual(treatment.quality_order, self.order)
        self.assertEqual(treatment.support_ticket, self.support_ticket)
        self.assertEqual(treatment.support_ticket_number, self.support_ticket.
            ticket_number)
        self.assertEqual(treatment.status, QualityTreatment.Status.PENDING_SEND)
        self.assertEqual(treatment.send_status, QualityTreatment.SendStatus.NOT_SENT)
        self.assertFalse(treatment.sent_at)
        self.assertFalse(treatment.brevo_message_id)
        self.assertEqual(treatment.created_by, self.user)
        old_code = treatment.code
        treatment.code = 'TQ-1999-000001'
        treatment.save()
        treatment.refresh_from_db()
        self.assertEqual(treatment.code, old_code)
        self.assertTrue(self.order.audit_events.filter(event_type=
            'TREATMENT_CREATED').exists())
        return None

    def test_uncertain_send_reconciliation_unlocks_exactly_one_manual_retry(self):
        treatment = create_quality_treatment(quality_order=self.order, reason="Reconciliacao controlada", actor=self.user)
        first_claim = claim_quality_treatment_send(treatment.pk, method="smtp")
        self.assertEqual(first_claim["status"], "CLAIMED")
        self.assertTrue(record_tq_send_failure(treatment.pk, "Resultado incerto.", uncertain=True))
        treatment.refresh_from_db()
        qid = treatment.correlation_id

        response = self.client.post(reverse("qualidade:tq_send_reconcile", kwargs={
            "codigo": self.order.code, "treatment_id": treatment.pk,
        }), {
            "decision": "not_delivered", "confirm_brevo": "yes",
            "confirm_mailbox": "yes", "confirm_otrs": "yes",
            "evidence_reference": "CASE-2026-1104",
        })
        self.assertEqual(response.status_code, 302)
        treatment.refresh_from_db()
        self.assertEqual(treatment.send_status, QualityTreatment.SendStatus.FAILED)
        self.assertEqual(treatment.correlation_id, qid)
        self.assertTrue(AuditEvent.objects.filter(
            action="TREATMENT_UNCERTAIN_RECONCILED_NOT_DELIVERED",
            object_id=str(treatment.pk),
        ).exists())

        retry = claim_quality_treatment_send(treatment.pk, allow_retry=True, method="smtp")
        self.assertEqual(retry["status"], "CLAIMED")
        self.assertTrue(record_tq_send_failure(treatment.pk, "Recusa SMTP definitiva.", uncertain=False))
        second_retry = claim_quality_treatment_send(treatment.pk, allow_retry=True, method="smtp")
        self.assertEqual(second_retry["status"], "NOT_CLAIMED")
        treatment.refresh_from_db()
        self.assertEqual(treatment.send_attempt_count, 2)
        self.assertEqual(treatment.correlation_id, qid)

    def test_tq_send_claim_is_idempotent_and_ambiguous_result_stays_non_retryable(self):
        treatment = create_quality_treatment(quality_order=self.order, reason=
            'Teste', actor=self.user)
        first = claim_quality_treatment_send(treatment.pk)
        second = claim_quality_treatment_send(treatment.pk)
        self.assertEqual(first['status'], 'CLAIMED')
        self.assertEqual(second['status'], 'NOT_CLAIMED')
        treatment.refresh_from_db()
        self.assertEqual(treatment.send_attempt_count, 1)
        self.assertEqual(treatment.send_status, QualityTreatment.SendStatus.IN_PROGRESS
            )
        self.assertTrue(record_tq_send_failure(treatment.pk,
            'Resultado de rede incerto.', uncertain=True))
        retry = claim_quality_treatment_send(treatment.pk)
        treatment.refresh_from_db()
        self.assertEqual(retry['status'], 'NOT_CLAIMED')
        self.assertEqual(treatment.send_status, QualityTreatment.SendStatus.UNCERTAIN)
        self.assertEqual(treatment.send_attempt_count, 1)
        self.assertFalse(treatment.quality_ticket_external_id)
        return None

    def test_confirmed_failure_allows_only_an_explicit_controlled_retry(self):
        treatment = create_quality_treatment(quality_order=self.order, reason=
            'Teste', actor=self.user)
        self.assertEqual(claim_quality_treatment_send(treatment.pk)['status'],
            'CLAIMED')
        self.assertTrue(record_tq_send_failure(treatment.pk,
            'A Brevo recusou a solicitaÃ§Ã£o.'))
        self.assertEqual(claim_quality_treatment_send(treatment.pk)['status'],
            'NOT_CLAIMED')
        retry = claim_quality_treatment_send(treatment.pk, allow_retry=True)
        self.assertEqual(retry['status'], 'CLAIMED')
        treatment.refresh_from_db()
        self.assertEqual(treatment.send_attempt_count, 2)
        self.assertEqual(treatment.send_status, QualityTreatment.SendStatus.IN_PROGRESS
            )
        return None

    @override_settings(ARION_CSP_REPORT_ONLY=True)
    @override_settings(ARION_CSP_REPORT_ONLY=True)
    def test_email_preview_works_without_brevo_and_escapes_user_content(self):
        treatment = create_quality_treatment(
            quality_order=self.order, reason="<script>alert('x')</script>\nVerificar enlace", actor=self.user,
        )
        response = self.client.get(reverse("qualidade:tq_email_preview", kwargs={
            "codigo": self.order.code, "treatment_id": treatment.pk,
        }))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, treatment.code)
        self.assertContains(response, treatment.correlation_id)
        self.assertContains(response, 'sandbox=""')
        self.assertNotContains(response, "<script>alert")
        srcdoc_parser = _IframeSrcdocParser()
        srcdoc_parser.feed(response.content.decode("utf-8"))
        self.assertIn("<!doctype html>", srcdoc_parser.srcdoc.lower())
        self.assertIn("&lt;script&gt;", srcdoc_parser.srcdoc)
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy-Report-Only"])
        self.assertFalse(ExternalAPIIntegration.objects.filter(
            provider=ExternalAPIIntegration.Provider.BREVO,
        ).exists())
        detail = get_quality_order_detail(self.user, self.order, tab="tratativas")
        email = build_treatment_email(treatment, detail=detail)
        self.assertEqual(response.context["email_subject"], email["subject"])
        utf8_example = build_treatment_email(
            treatment, detail={**detail, "client": "Vivo", "project": "FUST", "operator_name": "Vivo"},
        )
        self.assertIn("Vivo &middot; FUST", utf8_example["html_content"])
        self.assertEqual(response["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(len(email["context"]["requested_actions"]), 5)
        self.assertIn("reparo", email["text_content"])
        self.assertIn("Respons" + chr(0xe1) + "vel", email["text_content"])
        self.assertIn("NOC - QUALIDADE ATACADO", email["html_content"])
        solicitacao = "SOLICITA" + chr(0xc7) + chr(0xc3) + "O"
        self.assertIn(solicitacao + " DE TRATATIVA DE QUALIDADE", email["html_content"])
        self.assertIn("Identifica" + chr(0xe7) + chr(0xe3) + "o", email["html_content"])
        self.assertIn("Respons" + chr(0xe1) + "vel", email["html_content"])
        self.assertIn("GEST" + chr(0xc3) + "O DA QUALIDADE", email["html_content"])
        self.assertIn(solicitacao + " DE TRATATIVA DE QUALIDADE", email["subject"])
        self.assertEqual(email["html_content"].encode("utf-8").decode("utf-8"), email["html_content"])
        self.assertNotIn(treatment.correlation_id, email["subject"])
        self.assertEqual(email["html_content"].count(treatment.correlation_id), 1)
    def test_checklist_toggle_returns_confirmed_state_without_full_page_redirect(self):
        from aplicativos.qualidade.models import QualityAction
        action = QualityAction.objects.create(quality_order=self.order, title="Validar circuito", order=1)
        page = self.client.get(reverse("qualidade:ordem_detail", kwargs={"codigo": self.order.code}), {"tab": "tratativas"})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "/static/js/qualidade/checklist.js")
        self.assertContains(page, "/static/css/qualidade/qualidade.css")
        self.assertTrue(Path("arion_geo/static/js/qualidade/checklist.js").is_file())
        self.assertTrue(Path("arion_geo/static/css/qualidade/qualidade.css").is_file())
        response = self.client.post(
            reverse("qualidade:checklist_toggle", kwargs={"codigo": self.order.code, "action_id": action.pk}),
            {"completed": "true", "csrfmiddlewaretoken": "test"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result["ok"])
        self.assertTrue(result["completed"])
        self.assertEqual(result["completed_count"], 1)
        action.refresh_from_db()
        self.assertTrue(action.completed)
        self.assertIsNotNone(action.completed_at)
        self.assertEqual(action.completed_by, self.user)
        self.assertTrue(QualityAuditEvent.objects.filter(
            quality_order=self.order, event_type=QualityAuditEvent.EventType.CHECKLIST_ITEM_CHANGED,
        ).exists())

    def test_detail_date_input_renders_iso_value(self):
        self.order.treatment_deadline = timezone.make_aware(datetime(2026, 11, 30, 12, 0))
        self.order.save(update_fields=("treatment_deadline", "updated_at"))

        response = self.client.get(reverse("qualidade:ordem_detail", kwargs={"codigo": self.order.code}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'type="date"')
        self.assertContains(response, 'value="2026-11-30"')
        self.assertNotContains(response, 'value="30/11/2026"')

    def test_otrs_blocker_points_to_otrs_instead_of_brevo(self):
        treatment = create_quality_treatment(quality_order=self.order, reason="Verificar reincid" + chr(0xea) + "ncia", actor=self.user)
        integration_permission = Permission.objects.get(content_type__app_label="configuracoes", codename="manage_integrations")
        self.user.user_permissions.add(integration_permission)
        Integration.objects.filter(key=Integration.Key.OTRS).update(enabled=False)
        response = self.client.get(
            reverse("qualidade:ordem_detail", kwargs={"codigo": self.order.code}), {"tab": "tratativas"},
        )
        self.assertEqual(response.status_code, 200)
        otrs_label = "Integra" + chr(0xe7) + chr(0xe3) + "o OTRS"
        self.assertContains(response, otrs_label + " somente leitura")
        self.assertContains(response, "Ver integra" + chr(0xe7) + chr(0xe3) + "o OTRS")
        self.assertContains(response, reverse("configuracoes:integracoes:otrs"))
        self.assertNotContains(response, "Configurar Brevo")
        self.assertEqual(response.context["treatment_send_configuration_label"], "Ver integra" + chr(0xe7) + chr(0xe3) + "o OTRS")
        self.assertEqual(treatment.send_attempt_count, 0)

    def test_send_without_brevo_configuration_is_blocked_before_claim(self):
        treatment = create_quality_treatment(quality_order=self.order, reason=
            'Teste', actor=self.user)
        url = reverse('qualidade:tq_email_send', kwargs={'codigo': self.order.code,
            'treatment_id': treatment.pk})
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {'confirm_send': 'yes'})
        self.assertEqual(response.status_code, 400)
        treatment.refresh_from_db()
        self.assertEqual(treatment.send_status, QualityTreatment.SendStatus.NOT_SENT)
        self.assertEqual(treatment.send_attempt_count, 0)
        return None

    def test_mocked_brevo_send_records_message_and_second_post_cannot_repeat(self):
        treatment = create_quality_treatment(quality_order=self.order, reason=
            'Investigar', actor=self.user)
        ExternalAPIIntegration.objects.create(provider=ExternalAPIIntegration.
            Provider.BREVO, name='Brevo', enabled=True, allow_real_sends=True,
            secret_ciphertext='encrypted-test-key', config={'sender_name':
            'ARION OPS', 'sender_email': 'noc@example.com', 'quality_recipient':
            'quality@example.com', 'quality_cc': 'noc@example.com',
            'quality_recipient_validated': True, 'reply_to': ''})
        url = reverse('qualidade:tq_email_send', kwargs={'codigo': self.order.code,
            'treatment_id': treatment.pk})
        with patch('aplicativos.qualidade.views.decrypt_external_api_secret',
            return_value='mock-key') as __temp_135:
            with patch(
                'aplicativos.qualidade.views.BrevoTransactionalClient.send_transactional'
                , return_value=BrevoResult(True, 'SolicitaÃ§Ã£o aceita pela Brevo.',
                message_id='<mock-tq-message>')) as __temp_138:
                mocked_send = __temp_138
                response = self.client.post(url, {'confirm_send': 'yes'})
                self.assertEqual(response.status_code, 302)
                treatment.refresh_from_db()
                self.assertEqual(treatment.send_status, QualityTreatment.SendStatus
                    .SENT)
                self.assertEqual(treatment.status, QualityTreatment.Status.WAITING_OTRS
                    )
                self.assertEqual(treatment.brevo_message_id, '<mock-tq-message>')
                self.assertFalse(treatment.quality_ticket_external_id)
                duplicate = self.client.post(url, {'confirm_send': 'yes'})
                self.assertEqual(duplicate.status_code, 400)
                self.assertEqual(mocked_send.call_count, 1)
                self.assertEqual(mocked_send.call_args.args[0]['cc'], [{'email':
                    'noc@example.com'}])
                return None

    def test_smtp_message_build_uses_utf8_without_opening_connection(self):
        from django.core.mail import EmailMultiAlternatives
        from aplicativos.integracoes.clients.brevo_smtp import send_brevo_smtp

        captured = {}
        def capture_message(message, fail_silently=False):
            captured["message"] = message.message()
            return 1

        config = {
            "sender_name": "ARION OPS", "sender_email": "noc@example.com",
            "quality_recipient": "quality@example.com", "quality_cc": "noc@example.com",
            "reply_to": "reply@example.com",
        }
        with patch("aplicativos.integracoes.clients.brevo_smtp.get_connection", return_value=object()) as connection:
            with patch.object(EmailMultiAlternatives, "send", autospec=True, side_effect=capture_message):
                result = send_brevo_smtp(
                    username="login", password="secret-never-used", config=config,
                    subject="Solicita\u00e7\u00e3o de tratativa | TQ-2026-000099",
                    text_content="Identifica\u00e7\u00e3o do circuito: VIVO.PI. \u00c1rvore de correla\u00e7\u00e3o ARION-QID-0123456789ABCDEF0123456789ABCDEF",
                    html_content="<p>Identifica\u00e7\u00e3o UTF-8</p><strong>ARION-QID-0123456789ABCDEF0123456789ABCDEF</strong>",
                )
        self.assertTrue(result.ok)
        self.assertEqual(result.stage, "smtp_send")
        connection.assert_called_once()
        mime = captured["message"]
        self.assertEqual(mime["To"], "quality@example.com")
        self.assertEqual(mime["Cc"], "noc@example.com")
        self.assertEqual(mime["Reply-To"], "reply@example.com")
        plain = mime.get_payload(0).get_payload(decode=True).decode("utf-8")
        html = mime.get_payload(1).get_payload(decode=True).decode("utf-8")
        self.assertIn("Identifica\u00e7\u00e3o do circuito", plain)
        self.assertIn("ARION-QID-0123456789ABCDEF0123456789ABCDEF", plain)
        self.assertIn("Identifica\u00e7\u00e3o UTF-8", html)
        self.assertIn("ARION-QID-0123456789ABCDEF0123456789ABCDEF", html)

    def test_mocked_smtp_send_records_local_attempt_without_fake_brevo_id(self):
        treatment = create_quality_treatment(quality_order=self.order, reason=
            'SMTP teste', actor=self.user)
        ExternalAPIIntegration.objects.create(provider=ExternalAPIIntegration.
            Provider.BREVO, name='Brevo', enabled=True, allow_real_sends=True,
            smtp_secret_ciphertext='encrypted-smtp-test', config={'send_method':
            'smtp', 'smtp_login': 'login', 'sender_name': 'ARION OPS', 'sender_email':
            'noc@example.com', 'quality_recipient': 'testbox@example.com', 'quality_cc':
            'noc-copy@example.com', 'quality_recipient_validated': True, 'reply_to': ''})
        ExternalAPIHealthCheck.objects.create(integration=ExternalAPIIntegration.
            objects.get(provider=ExternalAPIIntegration.Provider.BREVO), method=
            'smtp', status=ExternalAPIHealthCheck.Status.SUCCESS, message=
            'Autentica??o SMTP aceita')
        url = reverse('qualidade:tq_email_send', kwargs={'codigo': self.order.code,
            'treatment_id': treatment.pk})
        with patch('aplicativos.qualidade.views.decrypt_external_api_smtp_secret',
            return_value='smtp-secret') as __temp_164:
            with patch('aplicativos.qualidade.views.send_brevo_smtp', return_value=
                SMTPResult(True, 'accepted')) as __temp_167:
                send = __temp_167
                preview = self.client.get(url)
                self.assertEqual(preview.status_code, 200)
                self.assertContains(preview, '(Cc)')
                self.assertContains(preview, 'noc-copy@example.com')
                self.assertNotContains(preview, 'SMTP Relay')
                send.assert_not_called()
                response = self.client.post(url, {'confirm_send': 'yes'})
                self.assertEqual(response.status_code, 302)
                treatment.refresh_from_db()
                self.assertEqual(treatment.send_status, QualityTreatment.SendStatus
                    .SENT)
                self.assertEqual(treatment.send_method, 'smtp')
                self.assertIsNotNone(treatment.send_attempt_id)
                self.assertEqual(treatment.brevo_message_id, '')
                self.assertEqual(send.call_args.kwargs['config']['quality_cc'],
                    'noc-copy@example.com')
                self.assertFalse(treatment.quality_ticket_external_id)
                duplicate = self.client.post(url, {'confirm_send': 'yes'})
                self.assertEqual(duplicate.status_code, 400)
                self.assertEqual(send.call_count, 1)
                return None

    def test_brevo_acceptance_keeps_tq_waiting_for_actual_otrs_ticket(self):
        treatment = create_quality_treatment(quality_order=self.order, reason=
            'Teste', actor=self.user)
        self.assertEqual(claim_quality_treatment_send(treatment.pk)['status'],
            'CLAIMED')
        self.assertTrue(record_tq_send_accepted(treatment.pk, '<mock-message-id>'))
        treatment.refresh_from_db()
        self.assertEqual(treatment.send_status, QualityTreatment.SendStatus.SENT)
        self.assertEqual(treatment.status, QualityTreatment.Status.WAITING_OTRS)
        self.assertEqual(treatment.brevo_message_id, '<mock-message-id>')
        self.assertIsNone(treatment.quality_ticket_external_id)
        return None

    def test_active_tq_blocks_duplicate_and_preserves_first_cycle(self):
        treatment = create_quality_treatment(quality_order=self.order, reason=
            'Ciclo inicial', support_ticket=self.support_ticket, actor=self.user)
        response = self.client.post(reverse('qualidade:tq_gerar', kwargs={'codigo':
            self.order.code}), {'reason': 'Tentativa duplicada', 'support_ticket': self
            .support_ticket.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(QualityTreatment.objects.filter(quality_order=self.order).
            count(), 1)
        treatment.refresh_from_db()
        self.assertEqual(treatment.reason, 'Ciclo inicial')
        detail = self.client.get(reverse('qualidade:ordem_detail', kwargs={'codigo':
            self.order.code}), {'tab': 'tratativas'})
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.context['local_treatment'], treatment)
        self.assertContains(detail, treatment.code)
        return None

    def test_treatment_card_shows_safe_block_message_and_hides_transport_method(self):
        create_quality_treatment(quality_order=self.order, reason="Investigar reincidencia", actor=self.user)
        response = self.client.get(reverse("qualidade:ordem_detail", kwargs={"codigo": self.order.code}), {"tab": "tratativas"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Envio indispon")
        self.assertContains(response, 'aria-disabled="true"')
        self.assertNotContains(response, "SMTP Relay")
    def test_only_active_support_ticket_related_to_oq_can_be_selected(self):
        other_link = CMDBLinkMirror.objects.create(source_config_item_id=
            'QUALITY-TQ-OTHER-CI')
        unrelated_ticket = self.create_ticket(link=other_link, external_id=91232)
        response = self.client.post(reverse('qualidade:tq_gerar', kwargs={'codigo':
            self.order.code}), {'reason': 'Investigar reincidÃªncia', 'support_ticket':
            unrelated_ticket.pk})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(QualityTreatment.objects.exists())
        return None

    def test_user_without_manage_permission_cannot_generate_tq(self):
        self.user.user_permissions.remove(Permission.objects.get(
            content_type__app_label='qualidade', codename='manage_quality_orders'))
        response = self.client.post(reverse('qualidade:tq_gerar', kwargs={'codigo':
            self.order.code}), {'reason': 'Sem autorizaÃ§Ã£o'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(QualityTreatment.objects.exists())
        return None

    def test_support_resolution_does_not_close_or_change_tq(self):
        treatment = create_quality_treatment(quality_order=self.order, reason=
            'Investigar causa', support_ticket=self.support_ticket, actor=self.user)
        self.support_ticket.business_status = OTRSStateMapping.BusinessStatus.RESOLVED
        self.support_ticket.is_business_open = False
        self.support_ticket.save(update_fields=('business_status', 'is_business_open'))
        treatment.refresh_from_db()
        self.assertEqual(treatment.status, QualityTreatment.Status.PENDING_SEND)
        self.assertTrue(treatment.is_active)
        self.assertEqual(treatment.support_ticket_number, self.support_ticket.
            ticket_number)
        return None

    def test_payload_uses_readable_subject_and_stable_qid_only_in_body(self):
        treatment = create_quality_treatment(quality_order=self.order, reason="Confirmar a causa raiz", actor=self.user)
        qid = treatment.correlation_id
        self.assertRegex(qid, r"^ARION-QID-[0-9A-F]{32}$")
        payload = build_quality_treatment_email_payload(
            treatment, designation="VIVO.PI.LIVR000267", client="Operadora Alfa",
            project="Projeto Alfa", operator="Operadora Alfa", provider="Vivo",
            recurrence_summary="4 ocorrencias validas; 3 reincidencias",
            support_ticket_number=self.support_ticket.ticket_number,
        )
        self.assertTrue(payload["subject"].startswith("SOLICITA"))
        self.assertIn(self.order.code, payload["subject"])
        self.assertIn(treatment.code, payload["subject"])
        self.assertIn("Projeto Alfa | Operadora Alfa | VIVO.PI.LIVR000267", payload["subject"])
        self.assertLessEqual(len(payload["subject"]), 255)
        self.assertNotIn(qid, payload["subject"])
        for expected in ("ENCAMINHAMENTO SOLICITADO: NOC - QUALIDADE ATACADO", "Contexto da", "causa raiz", "diagn", "evid", self.support_ticket.ticket_number):
            self.assertIn(expected, payload["body"])
        self.assertEqual(payload["body"].count(qid), 1)
        unsafe = build_quality_treatment_email_payload(treatment, designation="VIVO\r\nBcc: attacker@example.com")["subject"]
        self.assertNotIn("\r", unsafe)
        self.assertNotIn("\n", unsafe)
        treatment.correlation_id = "ARION-QID-00000000000000000000000000000000"
        treatment.save()
        treatment.refresh_from_db()
        self.assertEqual(treatment.correlation_id, qid)
    def test_article_correlation_requires_exact_body_id_and_configured_queue(self):
        treatment = create_quality_treatment(quality_order=self.order, reason=
            'Investigar', actor=self.user)
        self.support_ticket.queue_name = 'NOC - QUALIDADE ATACADO'
        self.support_ticket.external_state_name = 'Pendente'
        self.support_ticket.save(update_fields=('queue_name', 'external_state_name'))
        self.assertEqual(extract_correlation_ids('<p>ID: ' + str(treatment.
            correlation_id) + '</p>'), (treatment.correlation_id,))
        self.assertEqual(extract_correlation_ids('prefixo-' + str(treatment.
            correlation_id) + '0'), ())
        self.assertEqual(extract_correlation_ids(str(treatment.correlation_id) +
            'G'), ())
        missing = link_quality_ticket_from_article(ticket=self.support_ticket,
            article_body='sem identificador', allowed_queue_names=(
            'NOC - QUALIDADE ATACADO',))
        self.assertEqual(missing['status'], 'MISSING_CORRELATION_ID')
        not_configured = link_quality_ticket_from_article(ticket=self.
            support_ticket, article_body=treatment.correlation_id,
            allowed_queue_names=())
        self.assertEqual(not_configured['status'], 'QUEUE_NOT_CONFIGURED')
        result = link_quality_ticket_from_article(ticket=self.support_ticket,
            article_body='SolicitaÃ§Ã£o ' + str(treatment.correlation_id),
            allowed_queue_names=('NOC - QUALIDADE ATACADO',), actor=self.user)
        self.assertEqual(result['status'], 'LINKED')
        treatment.refresh_from_db()
        self.assertEqual(treatment.quality_ticket_external_id, self.support_ticket.
            external_id)
        self.assertEqual(treatment.quality_ticket_number, self.support_ticket.
            ticket_number)
        self.assertEqual(treatment.quality_ticket_queue_snapshot,
            'NOC - QUALIDADE ATACADO')
        self.assertEqual(treatment.status, QualityTreatment.Status.PENDING_SEND)
        self.assertEqual(treatment.send_status, QualityTreatment.SendStatus.NOT_SENT)
        self.assertTrue(treatment.quality_ticket_linked_at)
        return None

    def test_article_queue_and_ticket_conflicts_never_overwrite_existing_link(self):
        first = create_quality_treatment(quality_order=self.order, reason='Ciclo 1',
            actor=self.user)
        self.support_ticket.queue_name = 'OUTRA FILA'
        self.support_ticket.save(update_fields=('queue_name',))
        mismatch = link_quality_ticket_from_article(ticket=self.support_ticket,
            article_body=first.correlation_id, allowed_queue_names=(
            'NOC - QUALIDADE ATACADO',))
        self.assertEqual(mismatch['status'], 'QUEUE_MISMATCH')
        self.support_ticket.queue_name = 'NOC - QUALIDADE ATACADO'
        self.support_ticket.save(update_fields=('queue_name',))
        linked = link_quality_ticket_from_article(ticket=self.support_ticket,
            article_body=first.correlation_id, allowed_queue_names=(
            'NOC - QUALIDADE ATACADO',), actor=self.user)
        self.assertEqual(linked['status'], 'LINKED')
        first.refresh_from_db()
        first.status = QualityTreatment.Status.RECURRENCE
        first.completed_at = timezone.now()
        first.save(update_fields=('status', 'completed_at', 'active_order_key',
            'updated_at'))
        QualityOccurrence.objects.create(quality_order=self.order,
            ticket_external_id_snapshot=91234, ticket_number='REC-91234',
            occurred_at=first.completed_at, counts_as_recurrence=True)
        self.order.status = QualityOrder.Status.CONCLUIDA
        self.order.completed_at = first.completed_at
        self.order.save(update_fields=('status', 'completed_at', 'updated_at'))
        second = create_quality_treatment(quality_order=self.order, reason=
            'Novo ciclo apÃ³s reincidÃªncia', actor=self.user)
        self.assertNotEqual(first.correlation_id, second.correlation_id)
        second_ticket = self.create_ticket(link=self.link, external_id=91233)
        second_ticket.queue_name = 'NOC - QUALIDADE ATACADO'
        second_ticket.save(update_fields=('queue_name',))
        collision = link_quality_ticket_from_article(ticket=second_ticket,
            article_body=first.correlation_id, allowed_queue_names=(
            'NOC - QUALIDADE ATACADO',))
        self.assertEqual(collision['status'], 'TREATMENT_TICKET_CONFLICT')
        second.refresh_from_db()
        self.assertIsNone(second.quality_ticket_external_id)
        return None


    # FAILED test_incremental_article_batch_links_waiting_tq_by_body_qid: DecompilationError: Failed to decompile instruction Instruction(opcode=71, opname='LOAD_BUILD_CLASS', arg=None, argval=None, argrepr='', offset=234, starts_line=588, is_jump_target=False) in test_incremental_article_batch_links_waiting_tq_by_body_qid

    def test_formal_completion_and_new_valid_recurrence_are_required_for_next_tq(self):
        first = create_quality_treatment(quality_order=self.order, reason=
            'Primeiro ciclo', actor=self.user)
        first.status = QualityTreatment.Status.COMPLETED
        first.completed_at = timezone.now()
        first.save(update_fields=('status', 'completed_at', 'active_order_key',
            'updated_at'))
        with self.assertRaisesRegex(ValidationError, 'nova reincid') as __temp_319:
            create_quality_treatment(quality_order=self.order, reason=
                'Sem reincidÃªncia', actor=self.user)
            recurrence_time = first.completed_at + timedelta(seconds=1)
            QualityOccurrence.objects.create(quality_order=self.order,
                ticket_number='REC-NEW', occurred_at=recurrence_time,
                counts_as_recurrence=True, reason='ReincidÃªncia vÃ¡lida')
            self.order.status = QualityOrder.Status.CONCLUIDA
            self.order.completed_at = first.completed_at
            self.order.save(update_fields=('status', 'completed_at', 'updated_at'))
            second = create_quality_treatment(quality_order=self.order, reason=
                'Novo ciclo', actor=self.user)
            self.assertNotEqual(first.correlation_id, second.correlation_id)
            self.assertRegex(first.code, '^TQ-\\d{4}-\\d{6}$')
            self.assertRegex(second.code, '^TQ-\\d{4}-\\d{6}$')
            self.assertEqual(int(second.code[-6:None]), int(first.code[-6:None]) + 1)
            self.order.refresh_from_db()
            self.assertEqual(self.order.status, QualityOrder.Status.TRATAMENTO)
            self.assertIsNone(self.order.completed_at)
            return None

    def test_recurrence_during_monitoring_closes_current_tq_as_recurrence(self):
        treatment, _initial_ticket = self.prepare_treatment_for_rreip(
            self.user, self.link, self.order, ticket_id=99120,
        )
        started_at = timezone.now() - timedelta(days=2)
        cycle = start_monitoring_cycle(quality_order=self.order, actor=self.user, started_at=started_at,
                                      actions_analyzed=True, otrs_actions_summary="Ações verificadas no OTRS.")
        treatment.refresh_from_db()
        self.assertEqual(treatment.status, QualityTreatment.Status.MONITORING)
        recurrence_ticket = self.create_ticket(link=self.link, external_id=99121, created_at=timezone.now() - timedelta(hours=1))
        cycle = finish_monitoring_cycle(
            cycle=cycle, result=QualityMonitoringCycle.Result.RECURRENCE,
            trigger_ticket_identifier=recurrence_ticket.ticket_number, actor=self.user,
            recurrence_description="Same root cause; evidence in the validated ticket.",
            same_root_cause_confirmed=True,
        )
        treatment.refresh_from_db()
        self.assertEqual(cycle.result, QualityMonitoringCycle.Result.RECURRENCE)
        self.assertEqual(treatment.status, QualityTreatment.Status.RECURRENCE)
        self.assertEqual(treatment.completed_at, recurrence_ticket.source_created_at)
        next_treatment = create_quality_treatment(quality_order=self.order, reason="New cycle after recurrence", actor=self.user)
        self.assertNotEqual(treatment.correlation_id, next_treatment.correlation_id)

    def test_email_uses_structured_operator_and_keeps_isp_separate(self):
        regional_provider = CMDBOperatorMirror.objects.create(source_config_item_id="CMDB-TQ-ISP", name="GIGANET TELECOM")
        self.link.operator = regional_provider
        self.link.contracting_client = "VIVO"
        self.link.business_project = "FUST"
        self.link.isp_raw = str(regional_provider.pk)
        self.link.save(update_fields=("operator", "contracting_client", "business_project", "isp_raw"))
        self.order.owner = self.user
        self.order.save(update_fields=("owner", "updated_at"))
        treatment = create_quality_treatment(quality_order=self.order, reason="Validar operadora e provedor", actor=self.user)
        other_owner = User.objects.create_user(username="tq-owner-different", first_name="TQ", last_name="Owner")
        treatment.owner = other_owner
        treatment.save(update_fields=("owner", "updated_at"))
        email = build_treatment_email(treatment, detail=get_quality_order_detail(self.user, self.order, tab="tratativas"))
        self.assertIn("| FUST | VIVO | VIVO.PI.LIVR000267", email["subject"])
        self.assertNotIn("GIGANET", email["subject"])
        self.assertIn("Operadora: VIVO", email["text_content"])
        self.assertIn("ISP/provedor: GIGANET TELECOM", email["text_content"])
        self.assertIn("pela OQ:", email["text_content"])
        self.assertIn("pela TQ: TQ Owner", email["text_content"])
        self.assertEqual(email["text_content"].splitlines()[0], "ARION OPS - QUALIDADE OPERACIONAL")
        self.assertEqual(email["text_content"].splitlines()[1], "ENCAMINHAMENTO SOLICITADO: NOC - QUALIDADE ATACADO")
        self.assertNotIn(treatment.correlation_id, email["subject"])
        self.assertIn(treatment.correlation_id, email["text_content"])
    def test_tq_codes_and_qids_remain_reserved_after_local_tq_deletion(self):
        first = create_quality_treatment(quality_order=self.order, reason="Ciclo descartado", actor=self.user)
        first_code, first_qid = first.code, first.correlation_id
        first_sequence = int(first_code.rsplit("-", 1)[1])
        first.delete()
        second = create_quality_treatment(quality_order=self.order, reason="Novo ciclo", actor=self.user)
        self.assertNotEqual(second.code, first_code)
        self.assertNotEqual(second.correlation_id, first_qid)
        self.assertGreater(int(second.code.rsplit("-", 1)[1]), first_sequence)
        self.assertTrue(QualityTreatmentIdentity.objects.filter(code=first_code, correlation_id=first_qid, sequence_number=first_sequence).exists())
        other_order = QualityOrder.objects.create(current_reason="Outro circuito")
        attempted = QualityTreatment.objects.create(quality_order=other_order, reason="Teste", correlation_id=first_qid)
        self.assertNotEqual(attempted.correlation_id, first_qid)
        self.assertRegex(attempted.correlation_id, r"^ARION-QID-[0-9A-F]{32}$")

    def test_missing_linked_ticket_is_marked_unavailable_and_audited_without_erasing_id(self):
        treatment = create_quality_treatment(quality_order=self.order, reason="Ticket removido", actor=self.user)
        treatment.quality_ticket_external_id = 99887766
        treatment.quality_ticket_number = "202611040000099"
        treatment.quality_ticket_queue_snapshot = "NOC - QUALIDADE ATACADO"
        treatment.quality_ticket_state_snapshot = "Open"
        treatment.save(update_fields=("quality_ticket_external_id", "quality_ticket_number", "quality_ticket_queue_snapshot", "quality_ticket_state_snapshot", "updated_at"))
        class MissingTicketClient:
            def fetch_quality_ticket_states(inner_self, ticket_ids, *, queue_names, limit):
                return []
        self.assertEqual(refresh_linked_quality_ticket_states(MissingTicketClient(), queue_names=("NOC - QUALIDADE ATACADO",)), 1)
        treatment.refresh_from_db()
        self.assertEqual(treatment.quality_ticket_external_id, 99887766)
        self.assertEqual(treatment.quality_ticket_number, "202611040000099")
        self.assertEqual(treatment.quality_ticket_state_snapshot, "Indisponivel no OTRS")
        self.assertTrue(AuditEvent.objects.filter(action="QUALITY_TICKET_UNAVAILABLE", object_id=str(self.order.pk)).exists())
        response = self.client.get(reverse("qualidade:ordem_detail", kwargs={"codigo": self.order.code}), {"tab": "tratativas"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Indisponivel no OTRS")



class QualityOrderDetailAndReviewTests(QualityModuleTestCase):
    def create_manager(self, username="quality-detail-manager"):
        user = self.create_authorized_user(username)
        manage = Permission.objects.get(content_type__app_label="qualidade", codename="manage_quality_orders")
        user.user_permissions.add(manage)
        return user

    def test_master_data_labels_show_designation_and_provider_name_across_quality_views(self):
        user = self.create_manager("quality-master-data-labels")
        provider = CMDBOperatorMirror.objects.create(
            pk=11610, source_config_item_id="CMDB-PROVIDER-VIVO", name="Vivo",
        )
        link = CMDBLinkMirror.objects.create(
            source_config_item_id="QUALITY-CI-MASTER-1",
            source_number="36131000060",
            designation="BD0001200",
            contracting_client="Cliente Alfa",
            business_project="Arcos Dourados",
            isp_raw=str(provider.pk),
            state="SP",
        )
        self.create_ticket(link=link, external_id=84101)
        self.create_ticket(link=link, external_id=84102)
        self.client.force_login(user)

        priority_page = self.client.get(reverse("qualidade:links_prioritarios"), {"isp": "Vivo"})
        self.assertEqual(priority_page.status_code, 200)
        self.assertContains(priority_page, "Vivo")
        self.assertContains(priority_page, 'data-provider-name="Vivo"')
        self.assertNotContains(priority_page, f'data-provider-name="{provider.pk}"')

        order = QualityOrder.objects.create(link=link)
        detail = self.client.get(reverse("qualidade:ordem_detail", kwargs={"codigo": order.code}))

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.context["provider_id"], provider.pk)
        self.assertEqual(detail.context["provider_name"], "Vivo")
        self.assertContains(detail, "BD0001200")
        self.assertContains(detail, "Vivo")
        self.assertContains(detail, "Arcos Dourados")
        self.assertNotContains(detail, f"<h1>36131000060</h1>")
        self.assertNotContains(detail, f"<dd>{provider.pk}</dd>")

        options = self.client.get(reverse("qualidade:link_options"), {"q": "QUALITY-CI-MASTER-1"}).json()["results"]
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["provider_id"], provider.pk)
        self.assertEqual(options[0]["provider_name"], "Vivo")
        self.assertIn("BD0001200", options[0]["label"])
        self.assertNotIn(str(provider.pk), options[0]["label"])

        priority = get_priority_links(user, {"isp": "Vivo"})
        self.assertEqual(priority["rows"][0].designation_display, "BD0001200")
        self.assertEqual(priority["rows"][0].provider_name, "Vivo")
        orders = get_quality_orders_queue(user, {"isp": "Vivo"})
        self.assertEqual(orders["rows"][0].provider_name, "Vivo")
        dashboard = get_dashboard_data(user, {"isp": "Vivo"})
        self.assertIn("Vivo", dashboard["filter_options"]["isp"])
        self.assertNotIn(str(provider.pk), dashboard["filter_options"]["isp"])

    def test_order_detail_tabs_summary_counts_and_occurrence_sequence(self):
        manager = self.create_manager("quality-detail-tabs")
        link = CMDBLinkMirror.objects.create(
            source_config_item_id="QUALITY-CI-TABS", designation="BL30000074375",
            contracting_client="Vivo", business_project="Arcos Dourados", isp_raw="Elo SoluÃ§Ãµes", state="SP",
        )
        now = timezone.now()
        initial = self.create_ticket(link=link, external_id=84201, created_at=now - timedelta(days=3))
        recurrence = self.create_ticket(link=link, external_id=84202, created_at=now - timedelta(days=2))
        pending = self.create_ticket(link=link, external_id=84203, created_at=now - timedelta(days=1))
        canceled = self.create_ticket(
            link=link, external_id=84204,
            status=OTRSStateMapping.BusinessStatus.CANCELED,
            created_at=now,
        )
        order = QualityOrder.objects.create(
            link=link, status=QualityOrder.Status.TRATAMENTO, priority=QualityOrder.Priority.ALTA,
            recurrence_level=QualityOrder.RecurrenceLevel.R2,
            current_reason="OscilaÃ§Ã£o registrada", current_root_cause="Falha no enlace",
        )
        QualityOccurrence.objects.create(
            quality_order=order, otrs_ticket=initial, occurred_at=initial.source_created_at,
            is_initial_occurrence=True,
        )
        QualityOccurrence.objects.create(
            quality_order=order, otrs_ticket=recurrence, occurred_at=recurrence.source_created_at,
            counts_as_recurrence=True, same_root_cause=True,
            reason="OscilaÃ§Ã£o", root_cause="Falha no enlace",
        )
        self.client.force_login(manager)

        for tab in ("resumo", "ocorrencias", "tratativas", "monitoramento", "historico"):
            response = self.client.get(reverse("qualidade:ordem_detail", kwargs={"codigo": order.code}), {"tab": tab})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["active_tab"], tab)
        summary = self.client.get(reverse("qualidade:ordem_detail", kwargs={"codigo": order.code}), {"tab": "resumo"})
        self.assertEqual(summary.context["valid_occurrence_count"], 3)
        self.assertEqual(summary.context["recurrence_count"], 2)
        self.assertEqual(summary.context["same_cause_count"], 1)
        self.assertEqual(summary.context["pending_review_count"], 1)
        self.assertEqual(summary.context["excluded_history_count"], 1)
        self.assertContains(summary, "BL30000074375")
        self.assertContains(summary, "R2")
        self.assertContains(summary, "Elo SoluÃ§Ãµes")

        pending_response = self.client.get(
            reverse("qualidade:ordem_detail", kwargs={"codigo": order.code}),
            {"tab": "ocorrencias", "history": "pending"},
        )
        self.assertEqual(pending_response.context["occurrence_page"].paginator.count, 1)
        self.assertContains(pending_response, "Revisar")
        all_response = self.client.get(
            reverse("qualidade:ordem_detail", kwargs={"codigo": order.code}), {"tab": "ocorrencias"},
        )
        self.assertContains(all_response, "Ocorr\u00eancia inicial")
        self.assertContains(all_response, "Reincid\u00eancia #1")
        self.assertContains(all_response, "Ocorr\u00eancias desconsideradas")

    def test_occurrence_exclusion_requires_reason_and_review_recalculates_metrics_with_audit(self):
        from aplicativos.auditoria.models import AuditEvent

        manager = self.create_manager("quality-review-audit")
        link = CMDBLinkMirror.objects.create(
            source_config_item_id="QUALITY-CI-REVIEW", designation="AJU/IP/08541", isp_raw="Claro",
        )
        first = self.create_ticket(link=link, external_id=84301)
        second = self.create_ticket(link=link, external_id=84302)
        third = self.create_ticket(link=link, external_id=84303)
        order = QualityOrder.objects.create(link=link)
        QualityOccurrence.objects.create(
            quality_order=order, otrs_ticket=first, is_initial_occurrence=True,
        )
        QualityOccurrence.objects.create(
            quality_order=order, otrs_ticket=second, counts_as_recurrence=True,
        )
        url = reverse("qualidade:ocorrencia_revisar", kwargs={
            "codigo": order.code, "ticket_external_id": third.external_id,
        })
        detail_url = f"{reverse('qualidade:ordem_detail', kwargs={'codigo': order.code})}?tab=ocorrencias&history=all"
        self.client.force_login(manager)

        invalid = self.client.post(url, {
            "classification": "EXCLUDED", "exclusion_reason": "", "next": detail_url,
        })
        self.assertEqual(invalid.status_code, 302)
        self.assertFalse(order.occurrences.filter(ticket_external_id_snapshot=third.external_id).exists())

        response = self.client.post(url, {
            "classification": "EXCLUDED", "exclusion_reason": "DUPLICADO",
            "reason": "Ticket duplicado", "root_cause": "", "next": detail_url,
        })
        self.assertEqual(response.status_code, 302)
        reviewed = order.occurrences.get(ticket_external_id_snapshot=third.external_id)
        self.assertTrue(reviewed.excluded)
        self.assertEqual(reviewed.exclusion_reason, "DUPLICADO")
        self.assertEqual(order.audit_events.filter(event_type="OCCURRENCE_REVIEWED").count(), 1)
        self.assertEqual(AuditEvent.objects.filter(category="QUALITY", action="OCCURRENCE_REVIEWED").count(), 1)

        summary = get_quality_order_detail(manager, order)
        self.assertEqual(summary["valid_occurrence_count"], 2)
        self.assertEqual(summary["recurrence_count"], 1)
        self.assertEqual(summary["pending_review_count"], 0)

    def test_quality_manager_edits_oq_fields_with_field_level_audit(self):
        from aplicativos.auditoria.models import AuditEvent

        manager = self.create_manager("quality-order-edit")
        owner = User.objects.create_user(username="quality-order-owner", password="unused-test-password")
        order = QualityOrder.objects.create()
        self.client.force_login(manager)

        response = self.client.post(reverse("qualidade:ordem_update", kwargs={"codigo": order.code}), {
            "field": "detail",
            "recurrence_level": QualityOrder.RecurrenceLevel.R3,
            "priority": QualityOrder.Priority.CRITICA,
            "owner": owner.pk,
            "treatment_deadline": "2026-11-03",
            "internal_notes": "Aguardar retorno formal do provedor.",
            "next": reverse("qualidade:ordem_detail", kwargs={"codigo": order.code}),
        })

        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, QualityOrder.Status.NOVA)
        self.assertEqual(order.recurrence_level, QualityOrder.RecurrenceLevel.R3)
        self.assertEqual(order.priority, QualityOrder.Priority.CRITICA)
        self.assertEqual(order.owner_id, owner.pk)
        self.assertEqual(order.internal_notes, "Aguardar retorno formal do provedor.")
        events = set(order.audit_events.values_list("event_type", flat=True))
        self.assertTrue({"LEVEL_CHANGED", "PRIORITY_CHANGED", "OWNER_CHANGED", "DEADLINE_CHANGED", "NOTES_CHANGED"}.issubset(events))
        self.assertEqual(AuditEvent.objects.filter(category="QUALITY").count(), 5)

    def test_view_only_quality_user_cannot_edit_or_review(self):
        viewer = self.create_authorized_user("quality-detail-view-only")
        link = CMDBLinkMirror.objects.create(source_config_item_id="QUALITY-CI-VIEW-ONLY", designation="Link leitura")
        ticket = self.create_ticket(link=link, external_id=84401)
        order = QualityOrder.objects.create(link=link)
        self.client.force_login(viewer)

        edit_response = self.client.post(reverse("qualidade:ordem_update", kwargs={"codigo": order.code}), {"field": "detail"})
        review_response = self.client.post(reverse("qualidade:ocorrencia_revisar", kwargs={
            "codigo": order.code, "ticket_external_id": ticket.external_id,
        }), {"classification": "INITIAL"})

        self.assertEqual(edit_response.status_code, 403)
        self.assertEqual(review_response.status_code, 403)
        self.assertFalse(order.occurrences.exists())

    def test_occurrence_review_rechecks_data_scope_on_post(self):
        view_permission = Permission.objects.get(content_type__app_label="qualidade", codename="view_quality")
        manage_permission = Permission.objects.get(content_type__app_label="qualidade", codename="manage_quality_orders")
        allowed_operator = Operadora.objects.create(name="Operadora permitida", normalized_name="operadora permitida")
        allowed_project = Projeto.objects.create(name="Projeto permitido", normalized_name="projeto permitido")
        denied_project = Projeto.objects.create(name="Projeto negado", normalized_name="projeto negado")
        group = Group.objects.create(name="Escopo Qualidade revisÃ£o")
        scope = GroupDataScope.objects.create(group=group, mode=GroupDataScope.Mode.CUSTOM)
        scope.allowed_operators.add(allowed_operator)
        scope.allowed_projects.add(allowed_project)
        user = User.objects.create_user(username="quality-review-data-scope", password="unused-test-password")
        user.user_permissions.add(view_permission, manage_permission)
        user.groups.add(group)
        link = CMDBLinkMirror.objects.create(
            source_config_item_id="QUALITY-CI-REVIEW-SCOPE",
            contracting_client=allowed_operator.name,
            business_project=denied_project.name,
            designation="Link fora do escopo",
        )
        ticket = self.create_ticket(link=link, external_id=84402)
        order = QualityOrder.objects.create(link=link)
        self.client.force_login(user)

        response = self.client.post(reverse("qualidade:ocorrencia_revisar", kwargs={
            "codigo": order.code, "ticket_external_id": ticket.external_id,
        }), {"classification": "INITIAL", "next": reverse("qualidade:ordens")})

        self.assertEqual(response.status_code, 404)
        self.assertFalse(order.occurrences.exists())


class QualityOrderTests(QualityModuleTestCase):
    def test_create_order_generates_unique_human_code_and_event(self):
        actor = self.create_authorized_user()
        order = create_quality_order(actor=actor, priority=QualityOrder.Priority.ALTA)
        another = QualityOrder.objects.create()

        self.assertRegex(order.code, r"^OQ-\d{4}-\d{6,}$")
        self.assertNotEqual(order.code, another.code)
        self.assertEqual(order.audit_events.count(), 1)
        self.assertEqual(order.audit_events.get().event_type, "OQ_CREATED")

    def test_manual_creation_prevents_a_second_active_order_for_the_same_link(self):
        from .services.orders import ActiveQualityOrderExists

        link = CMDBLinkMirror.objects.create(source_config_item_id="OQ-DUP-1", designation="Link duplicado")
        created = create_quality_order(link=link)
        with self.assertRaises(ActiveQualityOrderExists) as raised:
            create_quality_order(link=link)
        self.assertEqual(raised.exception.order.pk, created.pk)
        self.assertEqual(QualityOrder.objects.filter(link=link).count(), 1)

    def test_owner_and_priority_updates_write_quality_and_central_audit(self):
        from aplicativos.auditoria.models import AuditEvent
        from .services.orders import update_quality_order_field

        actor = self.create_authorized_user("quality-manager-audit")
        new_owner = User.objects.create_user(username="quality-owner", password="unused-test-password")
        order = QualityOrder.objects.create()

        update_quality_order_field(order=order, field_name="owner", value=new_owner, actor=actor)
        update_quality_order_field(order=order, field_name="priority", value=QualityOrder.Priority.CRITICA, actor=actor)

        self.assertEqual(set(order.audit_events.values_list("event_type", flat=True)), {"OWNER_CHANGED", "PRIORITY_CHANGED"})
        self.assertEqual(AuditEvent.objects.filter(category="QUALITY").count(), 2)

    def test_scope_is_applied_before_quality_order_visibility(self):
        operator = Operadora.objects.create(name="Operadora Norte", normalized_name="operadora norte")
        allowed_project = Projeto.objects.create(name="Projeto A", normalized_name="projeto a")
        other_project = Projeto.objects.create(name="Projeto B", normalized_name="projeto b")
        group = Group.objects.create(name="Escopo Qualidade Teste")
        data_scope = GroupDataScope.objects.create(group=group, mode=GroupDataScope.Mode.CUSTOM)
        data_scope.allowed_operators.add(operator)
        data_scope.allowed_projects.add(allowed_project)
        user = self.create_authorized_user("quality-scoped")
        user.groups.add(group)

        allowed_link = CMDBLinkMirror.objects.create(
            source_config_item_id="Q-SCOPE-1",
            contracting_client=operator.name,
            business_project=allowed_project.name,
            designation="Link autorizado",
        )
        denied_link = CMDBLinkMirror.objects.create(
            source_config_item_id="Q-SCOPE-2",
            contracting_client=operator.name,
            business_project=other_project.name,
            designation="Link fora do escopo",
        )
        visible = QualityOrder.objects.create(link=allowed_link)
        hidden = QualityOrder.objects.create(link=denied_link)

        result = QualityDataScopeService.orders_for(user)
        self.assertEqual(list(result.values_list("pk", flat=True)), [visible.pk])
        self.assertNotIn(hidden.pk, result.values_list("pk", flat=True))

    def test_detail_route_returns_not_found_for_out_of_scope_order(self):
        user = User.objects.create_user(username="quality-scope-detail", password="unused-test-password")
        user.user_permissions.add(self.view_permission)
        order = QualityOrder.objects.create(designation_snapshot="Restricted snapshot")
        self.client.force_login(user)

        response = self.client.get(reverse("qualidade:ordem_detail", kwargs={"codigo": order.code}))

        self.assertEqual(response.status_code, 404)


class QualityDashboardGranularityTests(SimpleTestCase):
    def test_periods_allow_only_the_requested_temporal_detail(self):
        self.assertEqual(_allowed_granularities(7), ("day", "week"))
        self.assertEqual(_allowed_granularities(30), ("day", "week", "month"))
        self.assertEqual(_allowed_granularities(60), ("day", "week", "month"))
        self.assertEqual(_allowed_granularities(90), ("day", "week", "month"))
        self.assertEqual(_allowed_granularities(180), ("week", "month"))
        self.assertEqual(_allowed_granularities(365), ("month",))

    def test_automatic_granularity_and_invalid_overrides(self):
        for days, expected in ((7, "day"), (30, "day"), (60, "week"), (90, "week"), (180, "month"), (365, "month")):
            with self.subTest(days=days):
                self.assertEqual(_resolve_granularity(days, "auto")[1], expected)
        self.assertEqual(_resolve_granularity(180, "day")[:2], ("auto", "month"))
        self.assertEqual(_resolve_granularity(90, "day")[:2], ("day", "day"))

    def test_daily_weekly_and_monthly_buckets_use_real_intervals(self):
        daily = _make_buckets(date(2026, 10, 3), date(2026, 10, 4), "day")
        self.assertEqual([item["label"] for item in daily], ["03/10", "04/10"])
        self.assertEqual(daily[0]["tooltip"], "03/10/2026")

        weekly = _make_buckets(date(2026, 9, 30), date(2026, 10, 6), "week")
        self.assertEqual([item["label"] for item in weekly], ["30 set\u201304 out", "05\u201306 out"])
        self.assertEqual(weekly[0]["tooltip"], "30/09/2026 \u2013 04/10/2026")
        self.assertEqual(weekly[1]["tooltip"], "05/10/2026 \u2013 06/10/2026")

        monthly = _make_buckets(date(2026, 11, 30), date(2027, 1, 2), "month")
        self.assertEqual([item["label"] for item in monthly], ["nov 2026", "dez 2026", "jan 2027"])

    def test_management_granularity_keeps_the_operational_30_day_episode_boundary(self):
        within_30_days = build_occurrence_episodes([
            {"external_id": 1, "occurred_at": timezone.make_aware(datetime(2026, 10, 1))},
            {"external_id": 2, "occurred_at": timezone.make_aware(datetime(2026, 10, 31))},
        ], now=timezone.make_aware(datetime(2026, 11, 1)))
        after_30_days = build_occurrence_episodes([
            {"external_id": 3, "occurred_at": timezone.make_aware(datetime(2026, 10, 1))},
            {"external_id": 4, "occurred_at": timezone.make_aware(datetime(2026, 11, 2))},
        ], now=timezone.make_aware(datetime(2026, 11, 3)))

        self.assertEqual(len(within_30_days), 1)
        self.assertEqual(within_30_days[0].recurrence_count, 1)
        self.assertEqual(len(after_30_days), 2)
        self.assertEqual(after_30_days[-1].recurrence_count, 0)


class QualityDashboardTests(QualityModuleTestCase):
    def test_quality_permission_protects_dashboard(self):
        user = User.objects.create_user(username="without-quality", password="unused-test-password")
        self.client.force_login(user)

        response = self.client.get(reverse("qualidade:dashboard"))

        self.assertEqual(response.status_code, 403)

    def test_empty_dashboard_has_zero_counts_and_no_demo_series(self):
        user = self.create_authorized_user()
        result = get_dashboard_data(user)

        self.assertEqual(result["kpis"], {
            "new_recurrences": 0,
            "active_links": 0,
            "stabilized_links": 0,
            "reopened_after_stability": 0,
            "open_orders": 0,
            "opened": 0,
            "completed": 0,
            "monitoring": 0,
            "rreip_success": 0,
            "post_treatment_recurrence": 0,
            "critical": 0,
            "overdue": 0,
        })
        self.assertFalse(result["has_status_distribution"])
        self.assertFalse(result["has_recurring_links"])
        self.assertFalse(result["has_evolution"])
        self.assertFalse(result["effectiveness"]["has_data"])
        self.assertFalse(result["has_concentration"])
        self.assertFalse(result["has_stabilized_links"])

    def test_dashboard_aggregates_real_order_occurrence_and_cycle_data(self):
        user = self.create_authorized_user()
        now = timezone.now()
        link = CMDBLinkMirror.objects.create(
            source_config_item_id="DASH-1", designation="Link em tratamento",
            contracting_client="Claro", business_project="Banco do Brasil", isp_raw="Claro",
        )
        initial = self.create_ticket(link=link, external_id=84501, created_at=now - timedelta(days=2))
        recurrence_ticket = self.create_ticket(link=link, external_id=84502, created_at=now - timedelta(days=1))
        order = QualityOrder.objects.create(
            link=link,
            status=QualityOrder.Status.TRATAMENTO,
            priority=QualityOrder.Priority.CRITICA,
            treatment_deadline=now - timedelta(days=1),
            designation_snapshot="Link em tratamento",
            ci_snapshot="DASH-1",
        )
        QualityOccurrence.objects.create(
            quality_order=order,
            otrs_ticket=initial,
            occurred_at=initial.source_created_at,
            is_initial_occurrence=True,
        )
        QualityOccurrence.objects.create(
            quality_order=order,
            otrs_ticket=recurrence_ticket,
            occurred_at=recurrence_ticket.source_created_at,
            counts_as_recurrence=True,
            same_root_cause=True,
        )
        QualityMonitoringCycle.objects.create(
            quality_order=order,
            cycle_number=1,
            started_at=now - timedelta(days=31),
            expected_end_at=now - timedelta(days=1),
            ended_at=now,
            result=QualityMonitoringCycle.Result.RECURRENCE,
        )

        result = get_dashboard_data(user)

        self.assertEqual(result["kpis"]["opened"], 1)
        self.assertEqual(result["kpis"]["open_orders"], 1)
        self.assertEqual(result["kpis"]["new_recurrences"], 1)
        self.assertEqual(result["kpis"]["active_links"], 1)
        self.assertEqual(result["kpis"]["critical"], 1)
        self.assertEqual(result["kpis"]["overdue"], 1)
        self.assertEqual(result["kpis"]["post_treatment_recurrence"], 1)
        self.assertEqual(result["recurring_links"][0]["label"], "Link em tratamento")
        self.assertTrue(result["has_evolution"])
        self.assertEqual(result["status_distribution"], [{"name": "Tratamento", "value": 1}])
        self.assertEqual(sum(result["evolution"]["recurrences"]), 1)
        self.assertEqual(sum(result["evolution"]["post_treatment"]), 1)
        self.assertEqual(result["effectiveness"]["percentage"], "0,0")
        self.assertEqual(result["effectiveness"]["total"], 1)

    def test_dashboard_period_uses_completion_events_and_only_concluded_cycles_for_effectiveness(self):
        user = self.create_authorized_user("quality-dashboard-outcomes")
        now = timezone.now()
        concluded_at = now - timedelta(days=2)
        closed_order = QualityOrder.objects.create(
            status=QualityOrder.Status.CONCLUIDA,
            completed_at=None,
        )
        QualityOrder.objects.filter(pk=closed_order.pk).update(created_at=now - timedelta(days=50))
        closure_event = QualityAuditEvent.objects.create(
            quality_order=closed_order,
            event_type=QualityAuditEvent.EventType.STATUS_CHANGED,
            description="ConcluÃ­da no perÃ­odo.",
            metadata={"status": QualityOrder.Status.CONCLUIDA},
        )
        QualityAuditEvent.objects.filter(pk=closure_event.pk).update(created_at=concluded_at)
        opened_order = QualityOrder.objects.create(status=QualityOrder.Status.NOVA)
        QualityMonitoringCycle.objects.create(
            quality_order=closed_order,
            cycle_number=1,
            started_at=concluded_at - timedelta(days=30),
            expected_end_at=concluded_at,
            ended_at=concluded_at,
            result=QualityMonitoringCycle.Result.SUCCESS,
        )
        failed_order = QualityOrder.objects.create(status=QualityOrder.Status.TRATAMENTO)
        QualityMonitoringCycle.objects.create(
            quality_order=failed_order,
            cycle_number=1,
            started_at=now - timedelta(days=3),
            expected_end_at=now + timedelta(days=27),
            ended_at=now - timedelta(days=1),
            result=QualityMonitoringCycle.Result.RECURRENCE,
        )
        QualityMonitoringCycle.objects.create(
            quality_order=opened_order,
            cycle_number=1,
            started_at=now - timedelta(days=1),
            expected_end_at=now + timedelta(days=29),
            result=QualityMonitoringCycle.Result.IN_PROGRESS,
        )

        result = get_dashboard_data(user, {"period": "7"})

        self.assertEqual(result["kpis"]["opened"], 2)
        self.assertEqual(result["kpis"]["completed"], 1)
        self.assertEqual(result["kpis"]["monitoring"], 1)
        self.assertEqual(result["kpis"]["rreip_success"], 1)
        self.assertEqual(result["kpis"]["post_treatment_recurrence"], 1)
        self.assertEqual(result["effectiveness"]["percentage"], "50,0")
        self.assertEqual(result["effectiveness"]["total"], 2)
        self.assertEqual(sum(result["evolution"]["completed"]), 1)
        self.assertEqual(sum(result["evolution"]["post_treatment"]), 1)
        self.assertEqual(result["evolution"]["granularity"], "Di\u00e1ria")

    def test_rreip_cycle_uses_configured_default_duration(self):
        actor = self.create_authorized_user("quality-rreip-default-duration")
        link = CMDBLinkMirror.objects.create(source_config_item_id="QUALITY-RREIP-DEFAULT", designation="Circuito RREIP")
        order = create_quality_order(actor=actor, link=link)
        self.prepare_treatment_for_rreip(actor, link, order, ticket_id=99220)
        started_at = timezone.now()
        cycle = start_monitoring_cycle(quality_order=order, started_at=started_at, actor=actor,
                                      actions_analyzed=True, otrs_actions_summary="Ações verificadas no OTRS.")
        self.assertEqual(cycle.expected_end_at - cycle.started_at, timedelta(days=30))
        order.refresh_from_db()
        self.assertEqual(order.status, QualityOrder.Status.MONITORAMENTO)
        self.assertTrue(order.audit_events.filter(event_type="MONITORING_STARTED").exists())


    def test_empty_quality_dashboard_does_not_load_chart_assets(self):
        user = self.create_authorized_user("empty-quality-dashboard")
        self.client.force_login(user)
        response = self.client.get(reverse("qualidade:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "/static/vendor/echarts/echarts.min.js")
        self.assertNotContains(response, "/static/js/qualidade/dashboard.js")



class QualityRecurrenceIntegrationTests(QualityModuleTestCase):
    @staticmethod
    def aware(year, month, day):
        return timezone.make_aware(datetime(year, month, day, 12, 0))

    def test_occurrences_are_grouped_in_30_day_episodes_and_reset_after_a_gap(self):
        # Case A: three valid falls remain together; the operational counter is 1, 2.
        now = self.aware(2026, 10, 25)
        case_a = build_occurrence_episodes([
            {"external_id": 1, "occurred_at": self.aware(2026, 10, 1)},
            {"external_id": 2, "occurred_at": self.aware(2026, 10, 10)},
            {"external_id": 3, "occurred_at": self.aware(2026, 10, 20)},
        ], now=now)
        self.assertEqual(len(case_a), 1)
        self.assertEqual(len(case_a[0].occurrences), 3)
        self.assertEqual(case_a[0].recurrence_count, 2)
        self.assertTrue(case_a[0].active)
        self.assertEqual([item["operational_recurrence_level"] for item in case_a[0].occurrences], ["Inicial", "R1", "R2"])

        # Case B: a recurrent episode stabilizes; a later single fall starts a new episode.
        case_b = build_occurrence_episodes([
            {"external_id": 4, "occurred_at": self.aware(2026, 8, 1)},
            {"external_id": 5, "occurred_at": self.aware(2026, 8, 10)},
            {"external_id": 6, "occurred_at": self.aware(2026, 8, 15)},
            {"external_id": 7, "occurred_at": self.aware(2026, 9, 20)},
        ], now=self.aware(2026, 10, 20))
        self.assertEqual([len(item.occurrences) for item in case_b], [3, 1])
        self.assertEqual([item.recurrence_count for item in case_b], [2, 0])
        self.assertEqual(case_b[0].state, "STABILIZED")
        self.assertEqual(case_b[-1].state, "NEW_EPISODE")
        self.assertEqual(case_b[-1].operational_recurrence_level, "Inicial")
        self.assertFalse(case_b[-1].occurrences[0]["is_recurrence"])

        # Case C: a new episode resets its sequence even when the previous one had R1.
        case_c = build_occurrence_episodes([
            {"external_id": 8, "occurred_at": self.aware(2026, 8, 1)},
            {"external_id": 9, "occurred_at": self.aware(2026, 8, 10)},
            {"external_id": 10, "occurred_at": self.aware(2026, 9, 11)},
            {"external_id": 11, "occurred_at": self.aware(2026, 9, 18)},
        ], now=self.aware(2026, 9, 20))
        self.assertEqual([item.recurrence_count for item in case_c], [1, 1])
        self.assertTrue(case_c[-1].active)
        self.assertTrue(case_c[-1].occurrences[0]["is_new_episode_after_stability"])
        self.assertEqual(case_c[-1].occurrences[1]["operational_recurrence_level"], "R1")

        stable_episode = build_occurrence_episodes([
            {"external_id": 12, "occurred_at": self.aware(2026, 10, 1)},
            {"external_id": 13, "occurred_at": self.aware(2026, 10, 10)},
        ], now=self.aware(2026, 11, 10))[0]
        self.assertEqual(stable_episode.state, "STABILIZED")
        self.assertEqual(stable_episode.days_since_last, 31)
        self.assertEqual(stable_episode.stabilized_at, self.aware(2026, 11, 9))

        isolated = build_occurrence_episodes([
            {"external_id": 14, "occurred_at": self.aware(2026, 8, 1)},
        ], now=self.aware(2026, 10, 20))[0]
        self.assertEqual(isolated.state, "SINGLE_OCCURRENCE")
        self.assertFalse(isolated.active)

    def test_mirror_tickets_drive_dashboard_and_different_causes_remain_operational_recurrence(self):
        user = self.create_authorized_user("quality-recurrence-source")
        now = timezone.now()
        link = CMDBLinkMirror.objects.create(
            source_config_item_id="QUALITY-RECURRENCE-SOURCE", designation="BD0001200",
            contracting_client="Claro", business_project="PROJETO BB", isp_raw="Claro", state="SP",
        )
        initial = self.create_ticket(
            link=link, external_id=84601, closing_reason="Rompimento de Fibra",
            created_at=now - timedelta(days=20),
        )
        recurrence = self.create_ticket(
            link=link, external_id=84602, closing_reason="PendÃªncia financeira",
            created_at=now - timedelta(days=2),
        )
        order = QualityOrder.objects.create(link=link, status=QualityOrder.Status.NOVA)
        QualityOccurrence.objects.create(
            quality_order=order, otrs_ticket=recurrence, occurred_at=recurrence.source_created_at,
            counts_as_recurrence=True, same_root_cause=False,
        )

        analysis = build_quality_recurrence_analysis(user)
        summary = analysis["links"][link.source_config_item_id]
        self.assertEqual(summary["current_episode_occurrence_count"], 2)
        self.assertEqual(summary["recurrence_count"], 1)
        self.assertTrue(summary["is_active_recurrence"])
        dashboard = get_dashboard_data(user, {"period": "90"})
        self.assertEqual(dashboard["status_distribution"], [{"name": "Nova", "value": 1}])
        self.assertEqual(dashboard["kpis"]["new_recurrences"], 1)
        self.assertEqual(sum(dashboard["evolution"]["recurrences"]), 1)
        self.assertEqual(dashboard["recurring_links"][0]["designation"], "BD0001200")
        self.assertEqual(dashboard["recurring_links"][0]["project"], "Banco do Brasil")
        self.assertEqual(dashboard["recurring_links"][0]["value"], 1)
        queue = get_quality_orders_queue(user, {"project": "BANCO DO BRASIL"})
        self.assertEqual(queue["page_obj"].paginator.count, 1)
        self.assertEqual(queue["rows"][0].project_display, "Banco do Brasil")
        AuditEvent.objects.create(
            user=user, category="QUALITY", action="OQ_CREATED", module="Qualidade",
            object_type="QualityOrder", object_id=str(order.pk), description="Ordem criada.",
        )
        history = get_global_quality_history(user, {"project": "Banco do Brasil"})
        self.assertEqual(history["history_page"].paginator.count, 1)
        self.assertEqual(history["project_options"], ["Banco do Brasil"])

    def test_dashboard_and_priority_queue_distinguish_active_stabilized_and_reopened_links(self):
        user = self.create_authorized_user("quality-operational-link-states")
        now = timezone.now()
        stable_link = CMDBLinkMirror.objects.create(
            source_config_item_id="QUALITY-STABLE", designation="BD0002001",
            contracting_client="Vivo", business_project="Arcos Dourados", isp_raw="Vivo", state="SP",
        )
        self.create_ticket(link=stable_link, external_id=84701, created_at=now - timedelta(days=42))
        self.create_ticket(link=stable_link, external_id=84702, created_at=now - timedelta(days=35))

        active_link = CMDBLinkMirror.objects.create(
            source_config_item_id="QUALITY-ACTIVE", designation="BD0002002",
            contracting_client="Claro", business_project="Correios", isp_raw="Claro", state="RJ",
        )
        self.create_ticket(link=active_link, external_id=84703, created_at=now - timedelta(days=12))
        self.create_ticket(link=active_link, external_id=84704, created_at=now - timedelta(days=1))

        reopened_link = CMDBLinkMirror.objects.create(
            source_config_item_id="QUALITY-REOPENED", designation="BD0002003",
            contracting_client="Vivo", business_project="Bradesco", isp_raw="Vivo", state="SP",
        )
        self.create_ticket(link=reopened_link, external_id=84705, created_at=now - timedelta(days=40))
        self.create_ticket(link=reopened_link, external_id=84706, created_at=now - timedelta(days=35))
        self.create_ticket(link=reopened_link, external_id=84707, created_at=now - timedelta(days=2))

        isolated_link = CMDBLinkMirror.objects.create(
            source_config_item_id="QUALITY-ISOLATED", designation="BD0002004",
            contracting_client="Claro", business_project="Banco do Brasil", isp_raw="Claro", state="SP",
        )
        self.create_ticket(link=isolated_link, external_id=84708, created_at=now - timedelta(days=2))

        dashboard = get_dashboard_data(user, {"period": "7"})
        self.assertEqual(dashboard["kpis"]["new_recurrences"], 1)
        self.assertEqual(dashboard["kpis"]["active_links"], 1)
        self.assertEqual(dashboard["kpis"]["stabilized_links"], 2)
        self.assertEqual(dashboard["kpis"]["reopened_after_stability"], 1)
        self.assertEqual(sum(dashboard["evolution"]["stabilized"]), 2)
        self.assertEqual(dashboard["stabilized_links"][0]["designation"], "BD0002001")

        active_queue = get_priority_links(user, {})
        stable_queue = get_priority_links(user, {"state": "stabilized"})
        all_queue = get_priority_links(user, {"state": "all"})
        self.assertEqual([row.source_config_item_id for row in active_queue["rows"]], ["QUALITY-ACTIVE"])
        self.assertEqual([row.source_config_item_id for row in stable_queue["rows"]], ["QUALITY-STABLE"])
        self.assertEqual(all_queue["page_obj"].paginator.count, 4)
        reopened = next(row for row in all_queue["rows"] if row.source_config_item_id == "QUALITY-REOPENED")
        self.assertEqual(reopened.operational_state, "NEW_EPISODE")
        self.assertEqual(reopened.operational_recurrence_level, "Inicial")
        self.assertTrue(reopened.reopened_after_stability)

    def test_invalid_and_duplicate_tickets_do_not_enter_the_recurrence_sequence(self):
        user = self.create_authorized_user("quality-recurrence-exclusions")
        link = CMDBLinkMirror.objects.create(source_config_item_id="QUALITY-RECURRENCE-EXCLUSIONS", designation="Link")
        now = timezone.now()
        valid = self.create_ticket(link=link, external_id=84611, created_at=now - timedelta(days=2))
        self.create_ticket(
            link=link, external_id=84612, status=OTRSStateMapping.BusinessStatus.CANCELED,
            created_at=now - timedelta(days=1),
        )
        self.create_ticket(
            link=link, external_id=84613, responsibility="IMPROCEDENTE", created_at=now,
        )
        self.create_ticket(
            link=link, external_id=84614, closing_reason="Chamado duplicado", created_at=now,
        )
        analysis = build_quality_recurrence_analysis(user)
        summary = analysis["links"][link.source_config_item_id]
        self.assertEqual(summary["valid_occurrence_count"], 1)
        self.assertEqual(summary["recurrence_count"], 0)
        self.assertFalse(summary["is_active_recurrence"])

    def test_priority_portfolio_uses_canonical_aliases_and_keeps_fust_visible_below_strategic(self):
        user = self.create_authorized_user("quality-priority-portfolios")
        now = timezone.now()
        strategic = CMDBLinkMirror.objects.create(
            source_config_item_id="QUALITY-PRIORITY-BB", designation="BB0001",
            contracting_client="Claro", business_project="PROJETO BB", isp_raw="Claro",
        )
        fust = CMDBLinkMirror.objects.create(
            source_config_item_id="QUALITY-PRIORITY-FUST", designation="FUST0001",
            contracting_client="FUST", business_project="FUST", isp_raw="FUST",
        )
        self.create_ticket(link=strategic, external_id=84621, created_at=now - timedelta(days=8))
        self.create_ticket(link=strategic, external_id=84622, created_at=now - timedelta(days=2))
        self.create_ticket(link=fust, external_id=84623, created_at=now - timedelta(days=3))
        self.create_ticket(link=fust, external_id=84624, created_at=now - timedelta(days=1))
        stale = CMDBLinkMirror.objects.create(
            source_config_item_id="QUALITY-PRIORITY-STALE", designation="Antigo",
            contracting_client="Vivo", business_project="Bradesco", isp_raw="Vivo",
        )
        self.create_ticket(link=stale, external_id=84625, created_at=now - timedelta(days=50))
        self.create_ticket(link=stale, external_id=84626, created_at=now - timedelta(days=40))

        result = get_priority_links(user, {"period": "90"})
        self.assertEqual([row.source_config_item_id for row in result["rows"]], [strategic.source_config_item_id, fust.source_config_item_id])
        self.assertTrue(all(row.is_active_recurrence for row in result["rows"]))
        self.assertEqual(result["rows"][0].business_priority, 10)
        self.assertEqual(result["rows"][0].project_display, "Banco do Brasil")
        self.assertEqual(result["rows"][1].business_priority, 90)
        self.assertEqual(result["rows"][1].portfolio, "other")
        self.assertEqual(business_priority("Claro", "PROJETO BB"), 10)
        self.assertEqual(business_priority("Claro", "Correios"), 20)
        self.assertEqual(business_priority("Vivo", "Bradesco"), 30)
        self.assertEqual(business_priority("Vivo", "Arcos Dourados"), 40)

        portfolio = get_priority_links(user, {"period": "90", "portfolio": "claro", "project": "Banco do Brasil"})
        self.assertEqual([row.source_config_item_id for row in portfolio["rows"]], [strategic.source_config_item_id])


class QualityQueueTests(QualityModuleTestCase):
    def create_ticket(self, *, link, external_id, status=OTRSStateMapping.BusinessStatus.OPEN, responsibility="PROCEDENTE", created_at=None):
        ticket = OTRSTicket.objects.create(
            external_id=external_id,
            ticket_number=f"2026{external_id:010d}",
            title=f"Ticket {external_id}",
            queue_external_id=1,
            queue_name=NOC_QUEUE_NAME,
            external_state_id=1,
            external_state_name=status,
            business_status=status,
            responsibility_normalized=responsibility,
            source_created_at=created_at or timezone.now(),
        )
        OTRSTicketConfigItem.objects.create(
            ticket=ticket,
            config_item_id=link.source_config_item_id,
            cmdb_link=link,
            relation_type="DependsOn",
            relation_state="Valid",
        )
        return ticket

    def test_priority_links_show_unopened_candidates_with_occurrences_minus_initial_recurrences(self):
        user = self.create_authorized_user("quality-priority-candidate")
        candidate = CMDBLinkMirror.objects.create(
            source_config_item_id="Q-PRIORITY-1", source_number="BL30000000001",
            contracting_client="Cliente A", business_project="Projeto A", isp_raw="ISP A", state="GO",
        )
        first = self.create_ticket(link=candidate, external_id=81001)
        self.create_ticket(link=candidate, external_id=81002)
        self.create_ticket(link=candidate, external_id=81005)
        self.create_ticket(link=candidate, external_id=81006)
        canceled_link = CMDBLinkMirror.objects.create(source_config_item_id="Q-PRIORITY-2")
        self.create_ticket(link=canceled_link, external_id=81003, status=OTRSStateMapping.BusinessStatus.CANCELED)
        improper_link = CMDBLinkMirror.objects.create(source_config_item_id="Q-PRIORITY-3")
        self.create_ticket(link=improper_link, external_id=81004, responsibility="IMPROCEDENTE")

        result = get_priority_links(user, {"period": "60"})

        self.assertEqual([row.source_config_item_id for row in result["rows"]], [candidate.source_config_item_id])
        self.assertEqual(result["rows"][0].valid_occurrence_count, 4)
        self.assertEqual(result["rows"][0].recurrence_count, 3)
        self.assertEqual(result["rows"][0].same_cause_recurrences, 0)
        self.assertEqual(first.business_status, OTRSStateMapping.BusinessStatus.OPEN)

    def test_same_cause_uses_valid_in_scope_quality_occurrence_flags(self):
        user = self.create_authorized_user("quality-priority-confirmed")
        link = CMDBLinkMirror.objects.create(source_config_item_id="Q-PRIORITY-R", source_number="BL-REC")
        now = timezone.now()
        self.create_ticket(link=link, external_id=81101, created_at=now - timedelta(days=3))
        order = QualityOrder.objects.create(link=link)
        excluded_ticket = self.create_ticket(link=link, external_id=81102)
        QualityOccurrence.objects.create(
            quality_order=order, otrs_ticket=excluded_ticket, occurred_at=timezone.now(),
            counts_as_recurrence=True, excluded=True, exclusion_reason="DUPLICADO",
        )
        recurrence_ticket = self.create_ticket(link=link, external_id=81103, created_at=now - timedelta(days=1))
        QualityOccurrence.objects.create(
            quality_order=order, otrs_ticket=recurrence_ticket, occurred_at=recurrence_ticket.source_created_at,
            counts_as_recurrence=True, same_root_cause=True,
        )

        result = get_priority_links(user, {"period": "60"})

        row = result["rows"][0]
        self.assertEqual(row.valid_occurrence_count, 2)
        self.assertEqual(row.recurrence_count, 1)
        self.assertEqual(row.same_cause_recurrences, 1)
        self.assertEqual(row.active_order_code, order.code)

    def test_priority_links_apply_period_search_and_filters(self):
        user = self.create_authorized_user("quality-priority-filters")
        now = timezone.now()
        recent_link = CMDBLinkMirror.objects.create(
            source_config_item_id="Q-PRIORITY-FILTER-1", source_number="BL-FILTER-1",
            contracting_client="Cliente A", isp_raw="ISP A",
        )
        self.create_ticket(link=recent_link, external_id=81301, created_at=now - timedelta(days=40))
        self.create_ticket(link=recent_link, external_id=81302, created_at=now - timedelta(days=10))
        older_link = CMDBLinkMirror.objects.create(
            source_config_item_id="Q-PRIORITY-FILTER-2", source_number="BL-FILTER-2",
            contracting_client="Cliente B", isp_raw="ISP B",
        )
        self.create_ticket(link=older_link, external_id=81303, created_at=now - timedelta(days=80))
        self.create_ticket(link=older_link, external_id=81304, created_at=now - timedelta(days=70))

        filtered = get_priority_links(user, {
            "period": "60", "q": "BL-FILTER-1", "client": "Cliente A", "isp": "ISP A",
        })
        thirty_days = get_priority_links(user, {"period": "30"})
        ninety_days = get_priority_links(user, {"period": "90"})

        self.assertEqual([row.pk for row in filtered["rows"]], [recent_link.pk])
        self.assertEqual(filtered["rows"][0].valid_occurrence_count, 2)
        self.assertEqual(thirty_days["candidate_count"], 1)
        self.assertEqual(thirty_days["rows"][0].valid_occurrence_count, 1)
        self.assertEqual(thirty_days["rows"][0].recurrence_count, 1)
        self.assertEqual(ninety_days["candidate_count"], 1)

    def test_priority_pagination_preserves_search_period_and_page_size(self):
        user = self.create_authorized_user("quality-priority-pagination")
        for index in range(26):
            link = CMDBLinkMirror.objects.create(
                source_config_item_id=f"Q-PAGE-{index:02d}", source_number=f"PAGE-{index:02d}",
            )
            self.create_ticket(link=link, external_id=82000 + index * 2)
            self.create_ticket(link=link, external_id=82001 + index * 2)

        self.client.force_login(user)
        response = self.client.get(reverse("qualidade:links_prioritarios"), {
            "q": "PAGE-", "period": "60", "per_page": "25",
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["page_obj"].has_next())
        self.assertIn("q=PAGE-", response.context["page_query"])
        self.assertIn("period=60", response.context["page_query"])
        self.assertIn("per_page=25", response.context["page_query"])
        self.assertContains(response, "page=2")

    def test_priority_links_apply_custom_data_scope_before_candidate_aggregation(self):
        operator = Operadora.objects.create(name="Operadora Qualidade", normalized_name="operadora qualidade")
        allowed_project = Projeto.objects.create(name="Projeto Qualidade", normalized_name="projeto qualidade")
        denied_project = Projeto.objects.create(name="Projeto Restrito", normalized_name="projeto restrito")
        scope_group = Group.objects.create(name="Escopo Links PrioritÃ¡rios")
        scope = GroupDataScope.objects.create(group=scope_group, mode=GroupDataScope.Mode.CUSTOM)
        scope.allowed_operators.add(operator)
        scope.allowed_projects.add(allowed_project)
        user = self.create_authorized_user("quality-priority-scope")
        manage_permission = Permission.objects.get(
            content_type__app_label="qualidade", codename="manage_quality_orders",
        )
        user.user_permissions.add(manage_permission)
        user.groups.add(scope_group)
        allowed = CMDBLinkMirror.objects.create(
            source_config_item_id="Q-SCOPE-PRIORITY-1", contracting_client=operator.name,
            business_project=allowed_project.name,
        )
        denied = CMDBLinkMirror.objects.create(
            source_config_item_id="Q-SCOPE-PRIORITY-2", contracting_client=operator.name,
            business_project=denied_project.name,
        )
        shared_ticket = self.create_ticket(link=allowed, external_id=81201)
        self.create_ticket(link=allowed, external_id=81202)
        for external_id in (81203, 81204):
            self.create_ticket(link=denied, external_id=external_id)
        OTRSTicketConfigItem.objects.create(
            ticket=shared_ticket, config_item_id=denied.source_config_item_id,
            cmdb_link=denied, relation_type="DependsOn", relation_state="Valid",
        )
        hidden_order = QualityOrder.objects.create(link=denied)
        QualityOccurrence.objects.create(
            quality_order=hidden_order, otrs_ticket=shared_ticket,
            occurred_at=timezone.now(), excluded=True, exclusion_reason="DUPLICADO",
        )

        result = get_priority_links(user, {"period": "60"})

        self.assertEqual([row.source_config_item_id for row in result["rows"]], [allowed.source_config_item_id])
        self.assertEqual(result["rows"][0].valid_occurrence_count, 2)
        self.client.force_login(user)
        response = self.client.get(reverse("qualidade:link_options"), {"q": "Q-SCOPE-PRIORITY"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()["results"]], [allowed.pk])

    def test_priority_and_orders_pages_render_for_quality_viewers(self):
        user = self.create_authorized_user("quality-list-render")
        self.client.force_login(user)

        priority_response = self.client.get(reverse("qualidade:links_prioritarios"))
        orders_response = self.client.get(reverse("qualidade:ordens"))

        self.assertEqual(priority_response.status_code, 200)
        self.assertContains(priority_response, "Links priorit\u00e1rios")
        self.assertContains(priority_response, "ARION OPS \u00b7 Links priorit\u00e1rios \u00b7 Qualidade Operacional")
        self.assertEqual(priority_response.headers["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(orders_response.status_code, 200)
        self.assertContains(orders_response, "Ordens de Qualidade")
        self.assertContains(orders_response, "ARION OPS \u00b7 Ordens de Qualidade")
        for response in (priority_response, orders_response):
            rendered = response.content.decode("utf-8")
            mojibake_sequences = (
                (0x00C3, 0x0192),
                (0x00C3, 0x201A, 0x00C2, 0x00B7),
                (0x00C3, 0x00A2, 0x00E2, 0x201A, 0x00AC, 0x00C2, 0x00A6),
                (0x00EF, 0x00BF, 0x00BD),
            )
            for codes in mojibake_sequences:
                mojibake = "".join(chr(codepoint) for codepoint in codes)
                self.assertNotIn(mojibake, rendered)

    def test_priority_link_detail_reuses_metrics_and_shows_reviewed_and_excluded_history(self):
        user = self.create_authorized_user("quality-link-detail-history")
        link = CMDBLinkMirror.objects.create(
            source_config_item_id="Q-DETAIL-HISTORY", source_number="BL-DETAIL-1",
            designation="Circuito de teste", contracting_client="Cliente Alfa",
            business_project="Projeto Alfa", isp_raw="ISP Alfa", state="GO",
        )
        now = timezone.now()
        first = self.create_ticket(link=link, external_id=83101, created_at=now - timedelta(days=12))
        recurrence = self.create_ticket(link=link, external_id=83102, created_at=now - timedelta(days=4))
        improper = self.create_ticket(
            link=link, external_id=83103, responsibility="IMPROCEDENTE", created_at=now - timedelta(days=3),
        )
        canceled = self.create_ticket(
            link=link, external_id=83104, status=OTRSStateMapping.BusinessStatus.CANCELED,
            created_at=now - timedelta(days=2),
        )
        duplicate = self.create_ticket(link=link, external_id=83105, created_at=now - timedelta(days=1))
        order = QualityOrder.objects.create(
            link=link, status=QualityOrder.Status.TRATAMENTO, priority=QualityOrder.Priority.ALTA,
        )
        QualityOccurrence.objects.create(
            quality_order=order, otrs_ticket=recurrence, occurred_at=now - timedelta(days=4),
            counts_as_recurrence=True, same_root_cause=True,
            reason="OscilaÃ§Ã£o confirmada", root_cause="Falha no enlace de acesso",
        )
        QualityOccurrence.objects.create(
            quality_order=order, otrs_ticket=duplicate, occurred_at=now - timedelta(days=1),
            excluded=True, exclusion_reason="DUPLICADO",
        )

        self.client.force_login(user)
        response = self.client.get(reverse("qualidade:priority_link_detail", kwargs={
            "identificador": link.source_config_item_id,
        }), {"period": "60"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["valid_occurrence_count"], 2)
        self.assertEqual(response.context["recurrence_count"], 1)
        self.assertEqual(response.context["same_cause_recurrences"], 1)
        self.assertEqual(response.context["period"], "60")
        self.assertContains(response, "Ocorr\u00eancia inicial")
        self.assertContains(response, "Reincid\u00eancia #1")
        self.assertContains(response, "OscilaÃ§Ã£o confirmada")
        self.assertContains(response, "Falha no enlace de acesso")
        self.assertContains(response, "Mesma causa confirmada")
        self.assertContains(response, "Improcedente \u00b7 n\u00e3o contabilizado")
        self.assertContains(response, "Cancelado \u00b7 n\u00e3o contabilizado")
        self.assertContains(response, "Duplicado \u00b7 n\u00e3o contabilizado")
        self.assertContains(response, order.code)
        self.assertContains(response, "Abrir OQ")
        self.assertEqual(response.context["history_count"], 5)
        self.assertEqual(response.context["excluded_history_count"], 3)

        excluded_response = self.client.get(reverse("qualidade:priority_link_detail", kwargs={
            "identificador": link.source_config_item_id,
        }), {"period": "60", "history": "excluded", "direction": "oldest"})
        self.assertEqual(excluded_response.status_code, 200)
        self.assertEqual(excluded_response.context["history_count"], 3)
        self.assertTrue(all(not ticket.is_valid_occurrence for ticket in excluded_response.context["tickets"]))
        self.assertEqual(first.external_id, 83101)
        self.assertEqual(improper.external_id, 83103)
        self.assertEqual(canceled.external_id, 83104)

    def test_priority_link_detail_enforces_data_scope_for_direct_requests(self):
        operator = Operadora.objects.create(name="Operadora detalhe", normalized_name="operadora detalhe")
        allowed_project = Projeto.objects.create(name="Projeto autorizado", normalized_name="projeto autorizado")
        denied_project = Projeto.objects.create(name="Projeto restrito", normalized_name="projeto restrito")
        group = Group.objects.create(name="Escopo detalhe links")
        scope = GroupDataScope.objects.create(group=group, mode=GroupDataScope.Mode.CUSTOM)
        scope.allowed_operators.add(operator)
        scope.allowed_projects.add(allowed_project)
        user = User.objects.create_user(username="quality-link-detail-scoped", password="unused-test-password")
        user.user_permissions.add(self.view_permission)
        user.groups.add(group)
        allowed = CMDBLinkMirror.objects.create(
            source_config_item_id="Q-DETAIL-ALLOWED", contracting_client=operator.name,
            business_project=allowed_project.name,
        )
        denied = CMDBLinkMirror.objects.create(
            source_config_item_id="Q-DETAIL-DENIED", contracting_client=operator.name,
            business_project=denied_project.name,
        )
        for external_id in (83201, 83202):
            self.create_ticket(link=allowed, external_id=external_id)
        for external_id in (83203, 83204):
            self.create_ticket(link=denied, external_id=external_id)
        self.client.force_login(user)

        allowed_response = self.client.get(reverse("qualidade:priority_link_detail", kwargs={
            "identificador": allowed.source_config_item_id,
        }))
        denied_response = self.client.get(reverse("qualidade:priority_link_detail", kwargs={
            "identificador": denied.source_config_item_id,
        }))

        self.assertEqual(allowed_response.status_code, 200)
        self.assertEqual(denied_response.status_code, 404)
        self.assertNotContains(allowed_response, denied.source_config_item_id)

    def test_create_order_from_priority_link_detail_returns_to_link_and_shows_active_order(self):
        manager = self.create_authorized_user("quality-detail-create-manager")
        manage_permission = Permission.objects.get(
            content_type__app_label="qualidade", codename="manage_quality_orders",
        )
        manager.user_permissions.add(manage_permission)
        link = CMDBLinkMirror.objects.create(
            source_config_item_id="Q-DETAIL-CREATE", source_number="BL-DETAIL-CREATE",
            designation="Circuito para nova OQ", contracting_client="Cliente", business_project="Projeto",
        )
        self.create_ticket(link=link, external_id=83301)
        self.create_ticket(link=link, external_id=83302)
        detail_url = reverse("qualidade:priority_link_detail", kwargs={
            "identificador": link.source_config_item_id,
        })
        self.client.force_login(manager)

        detail_response = self.client.get(detail_url, {"period": "60"})
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Criar OQ")
        self.assertContains(detail_response, "quality-create-order-dialog")

        create_response = self.client.post(reverse("qualidade:ordem_create"), {
            "link": link.pk,
            "priority": QualityOrder.Priority.ALTA,
            "owner": "",
            "treatment_deadline": "",
            "initial_note": "Avaliar o histÃ³rico do circuito",
            "next": f"{detail_url}?period=60",
        })
        order = QualityOrder.objects.get(link=link)
        self.assertRedirects(create_response, f"{detail_url}?period=60", fetch_redirect_response=False)
        refreshed_detail = self.client.get(create_response.url)
        self.assertContains(refreshed_detail, order.code)
        self.assertContains(refreshed_detail, "Abrir OQ")

    def test_queue_pages_and_filters_scoped_quality_orders(self):
        user = self.create_authorized_user("quality-orders-queue")
        visible = QualityOrder.objects.create(
            status=QualityOrder.Status.TRATAMENTO,
            priority=QualityOrder.Priority.CRITICA,
            designation_snapshot="Fila visÃ­vel",
            ci_snapshot="Q-QUEUE-1",
        )
        QualityOrder.objects.create(designation_snapshot="Outra fila", ci_snapshot="Q-QUEUE-2")

        result = get_quality_orders_queue(user, {"tab": "critical", "per_page": "25"})

        self.assertEqual(result["page_obj"].paginator.count, 1)
        self.assertEqual(result["rows"][0].pk, visible.pk)
        self.assertEqual(result["counts"]["all"], 2)
        self.assertEqual(result["counts"]["critical"], 1)

    def test_orders_queue_searches_code_and_filters_status_and_priority(self):
        user = self.create_authorized_user("quality-orders-search")
        matching = QualityOrder.objects.create(
            status=QualityOrder.Status.TRIAGEM,
            priority=QualityOrder.Priority.CRITICA,
            client_snapshot="Cliente Alfa",
            project_snapshot="Projeto Alfa",
        )
        QualityOrder.objects.create(
            status=QualityOrder.Status.NOVA,
            priority=QualityOrder.Priority.BAIXA,
            client_snapshot="Cliente Beta",
        )

        result = get_quality_orders_queue(user, {
            "q": matching.code,
            "status": QualityOrder.Status.TRIAGEM,
            "priority": QualityOrder.Priority.CRITICA,
        })

        self.assertEqual(result["page_obj"].paginator.count, 1)
        self.assertEqual(result["rows"][0].pk, matching.pk)
        self.assertEqual(result["rows"][0].status_label, "Triagem")

    def test_orders_queue_applies_custom_scope_before_counts(self):
        operator = Operadora.objects.create(name="Operadora OQ", normalized_name="operadora oq")
        allowed_project = Projeto.objects.create(name="Projeto OQ", normalized_name="projeto oq")
        other_project = Projeto.objects.create(name="Projeto fora", normalized_name="projeto fora")
        group = Group.objects.create(name="Escopo Fila OQ")
        scope = GroupDataScope.objects.create(group=group, mode=GroupDataScope.Mode.CUSTOM)
        scope.allowed_operators.add(operator)
        scope.allowed_projects.add(allowed_project)
        user = self.create_authorized_user("quality-order-scope-queue")
        user.groups.add(group)
        allowed_link = CMDBLinkMirror.objects.create(
            source_config_item_id="Q-QUEUE-SCOPE-1", contracting_client=operator.name,
            business_project=allowed_project.name,
        )
        denied_link = CMDBLinkMirror.objects.create(
            source_config_item_id="Q-QUEUE-SCOPE-2", contracting_client=operator.name,
            business_project=other_project.name,
        )
        allowed = QualityOrder.objects.create(link=allowed_link)
        QualityOrder.objects.create(link=denied_link)

        result = get_quality_orders_queue(user, {"tab": "all"})

        self.assertEqual(result["counts"]["all"], 1)
        self.assertEqual([order.pk for order in result["rows"]], [allowed.pk])

    def test_priority_page_and_order_queue_require_quality_view_permission(self):
        user = User.objects.create_user(username="quality-pages-no-view", password="unused-test-password")
        self.client.force_login(user)

        self.assertEqual(self.client.get(reverse("qualidade:links_prioritarios")).status_code, 403)
        self.assertEqual(self.client.get(reverse("qualidade:ordens")).status_code, 403)
        self.assertEqual(self.client.get(reverse("qualidade:priority_link_detail", kwargs={
            "identificador": "outside-scope",
        })).status_code, 403)

    def test_order_creation_is_manual_scoped_and_audited(self):
        manager = self.create_authorized_user("quality-create-manager")
        manage_permission = Permission.objects.get(content_type__app_label="qualidade", codename="manage_quality_orders")
        manager.user_permissions.add(manage_permission)
        link = CMDBLinkMirror.objects.create(
            source_config_item_id="Q-CREATE-1", source_number="BL-CREATE",
            contracting_client="Cliente", business_project="Projeto", state="SP",
        )
        self.client.force_login(manager)
        self.assertEqual(self.client.get(reverse("qualidade:ordens")).status_code, 200)
        self.assertEqual(self.client.get(reverse("qualidade:links_prioritarios")).status_code, 200)

        response = self.client.post(reverse("qualidade:ordem_create"), {
            "link": link.pk,
            "priority": QualityOrder.Priority.ALTA,
            "owner": "",
            "treatment_deadline": "",
            "initial_note": "Avaliar reincidÃªncia",
            "next": reverse("qualidade:ordens"),
        })

        order = QualityOrder.objects.get(link=link)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(order.status, QualityOrder.Status.NOVA)
        self.assertEqual(order.client_snapshot, "Cliente")
        self.assertEqual(order.internal_notes, "Avaliar reincidÃªncia")
        self.assertEqual(order.audit_events.get().event_type, "OQ_CREATED")
        duplicate_response = self.client.post(reverse("qualidade:ordem_create"), {
            "link": link.pk,
            "priority": QualityOrder.Priority.ALTA,
            "owner": "",
            "treatment_deadline": "",
            "initial_note": "",
            "next": reverse("qualidade:ordens"),
        })
        self.assertEqual(duplicate_response.status_code, 302)
        self.assertEqual(duplicate_response.url, reverse("qualidade:ordem_detail", kwargs={"codigo": order.code}))
        self.assertEqual(QualityOrder.objects.filter(link=link).count(), 1)

    def test_quality_viewer_without_management_permission_cannot_create_or_search_links(self):
        viewer = self.create_authorized_user("quality-view-only")
        self.client.force_login(viewer)

        create_response = self.client.post(reverse("qualidade:ordem_create"), {})
        options_response = self.client.get(reverse("qualidade:link_options"), {"q": "CI"})

        self.assertEqual(create_response.status_code, 403)
        self.assertEqual(options_response.status_code, 403)

    def test_quality_routes_only_activate_quality_global_navigation(self):
        user = self.create_authorized_user("quality-navigation-active")
        dashboard_permission = Permission.objects.get(
            content_type__app_label="configuracoes", codename="view_dashboard",
        )
        user.user_permissions.add(dashboard_permission)
        order = QualityOrder.objects.create()
        self.client.force_login(user)

        paths = (
            reverse("qualidade:dashboard"),
            reverse("qualidade:links_prioritarios"),
            reverse("qualidade:ordens"),
            reverse("qualidade:ordem_detail", kwargs={"codigo": order.code}),
            reverse("qualidade:monitoramento"),
            reverse("qualidade:contatos"),
            reverse("qualidade:historico"),
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                dashboard_link = html.split('href="/dashboard/"', 1)[1].split(">", 1)[0]
                self.assertNotIn("is-active", dashboard_link)
                self.assertNotIn('href="/dashboard/" aria-current="page"', html)
                self.assertIn('href="/qualidade/" class="header-link is-active"', html)

        dashboard_response = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard_response.status_code, 200)
        dashboard_html = dashboard_response.content.decode()
        dashboard_link = dashboard_html.split('href="/dashboard/"', 1)[1].split(">", 1)[0]
        self.assertIn("is-active", dashboard_link)
        self.assertNotIn('href="/qualidade/" class="header-link is-active"', dashboard_html)


class QualityPhaseFourWorkflowTests(QualityModuleTestCase):
    def create_manager(self, username="quality-phase-four-manager"):
        user = self.create_authorized_user(username)
        permissions = Permission.objects.filter(
            content_type__app_label="qualidade",
            codename__in=("manage_quality_orders", "manage_quality_contacts"),
        )
        user.user_permissions.add(*permissions)
        return user

    def create_link_and_order(self, user, *, key="PHASE4", provider="Claro", isp_raw=None, reason="", client="Cliente Fase 4", project="Projeto Fase 4"):
        link = CMDBLinkMirror.objects.create(
            source_config_item_id=f"QUALITY-CI-{key}",
            source_number=f"36131{CMDBLinkMirror.objects.count():07d}",
            designation="BD0001200",
            contracting_client=client,
            business_project=project,
            isp_raw=isp_raw if isp_raw is not None else provider,
            state="SP",
        )
        order = create_quality_order(actor=user, link=link, current_reason=reason)
        return link, order

    def test_active_treatment_phase_and_compact_summary_update(self):
        manager = self.create_manager("quality-treatment-summary")
        link, order = self.create_link_and_order(manager, key="TREATMENT-SUMMARY")
        treatment, _ticket = self.prepare_treatment_for_rreip(manager, link, order, ticket_id=85100)
        order.status = QualityOrder.Status.NOVA
        order.save(update_fields=("status", "updated_at"))
        self.client.force_login(manager)

        page = self.client.get(reverse("qualidade:ordem_detail", kwargs={"codigo": order.code}), {"tab": "tratativas"})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["lifecycle_label"], "Em tratamento")
        self.assertTrue(next(step for step in page.context["lifecycle_steps"] if step["label"] == "Tratamento")["current"])
        self.assertContains(page, "RESUMO GERENCIAL")
        self.assertContains(page, treatment.quality_ticket_number)

        response = self.client.post(reverse("qualidade:tq_atualizar_resumo", kwargs={
            "codigo": order.code, "treatment_id": treatment.pk,
        }), {
            "management_summary": "A operadora confirmou a correção e a Qualidade acompanha a estabilidade.",
            "management_situation": QualityTreatment.ManagementSituation.ACTIONS_IN_PROGRESS,
            "next_action": "Revisar atualização no OTRS",
            "next_action_due": "2026-11-10",
            "update_note": "Atualização curta recebida da operadora.",
            "next": f"/qualidade/ordens/{order.code}/?tab=tratativas",
        })
        self.assertEqual(response.status_code, 302)
        treatment.refresh_from_db()
        self.assertEqual(treatment.management_summary, "A operadora confirmou a correção e a Qualidade acompanha a estabilidade.")
        self.assertEqual(treatment.next_action_due, date(2026, 11, 10))
        event = order.audit_events.filter(event_type=QualityAuditEvent.EventType.NOTES_CHANGED).latest("created_at")
        self.assertEqual(event.metadata["treatment_code"], treatment.code)
        self.assertTrue(AuditEvent.objects.filter(action="TREATMENT_UPDATED", object_id=str(order.pk)).exists())

    def test_treatment_link_history_checklist_and_empty_state(self):
        manager = self.create_manager("quality-treatment-workflow")
        link, order = self.create_link_and_order(manager, key="TREATMENTS")
        closed = self.create_ticket(
            link=link, external_id=85101, status=OTRSStateMapping.BusinessStatus.CLOSED,
            created_at=timezone.now() - timedelta(days=3),
        )
        closed.source_changed_at = timezone.now() - timedelta(days=2)
        closed.terminal_state_at = closed.source_changed_at
        closed.save(update_fields=("source_changed_at", "terminal_state_at"))
        link_quality_ticket(
            quality_order=order, ticket_identifier=closed.ticket_number,
            ticket_type=QualityTicket.TicketType.TREATMENT, actor=manager,
        )
        active = self.create_ticket(link=link, external_id=85102)
        self.client.force_login(manager)
        response = self.client.post(reverse("qualidade:tq_vincular", kwargs={"codigo": order.code}), {
            "ticket_identifier": active.ticket_number,
            "ticket_type": QualityTicket.TicketType.TREATMENT,
            "next": f"/qualidade/ordens/{order.code}/?tab=tratativas",
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(QualityTicket.objects.filter(quality_order=order).count(), 2)
        detail = self.client.get(reverse("qualidade:ordem_detail", kwargs={"codigo": order.code}), {"tab": "tratativas"})
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, active.ticket_number)
        self.assertContains(detail, closed.ticket_number)
        self.assertContains(detail, NOC_QUEUE_NAME)
        self.assertContains(detail, "Abrir no OTRS")
        self.assertContains(detail, "Checklist da Qualidade")
        self.assertContains(detail, "Acompanhar o retorno")
        self.assertEqual(detail.context["current_treatment"]["ticket_number"], active.ticket_number)
        self.assertEqual(detail.context["treatment_history"][0]["ticket_number"], closed.ticket_number)
        self.assertTrue(AuditEvent.objects.filter(action="QUALITY_TICKET_LINKED").exists())

        empty_link, empty_order = self.create_link_and_order(manager, key="NO-TREATMENT")
        empty_response = self.client.get(
            reverse("qualidade:ordem_detail", kwargs={"codigo": empty_order.code}), {"tab": "tratativas"},
        )
        self.assertEqual(empty_response.status_code, 200)
        self.assertContains(empty_response, "Nenhuma tratativa de Qualidade criada.")
        self.assertEqual(empty_link.source_config_item_id, empty_order.ci_snapshot)

    def test_treatment_link_rejects_ticket_from_another_circuit_and_viewer_cannot_post(self):
        manager = self.create_manager("quality-treatment-scope-link")
        link, order = self.create_link_and_order(manager, key="TREATMENT-SCOPE")
        other_link, _ = self.create_link_and_order(manager, key="TREATMENT-OTHER")
        ticket = self.create_ticket(link=other_link, external_id=85103)
        with self.assertRaises(ValidationError):
            link_quality_ticket(
                quality_order=order, ticket_identifier=ticket.ticket_number,
                ticket_type=QualityTicket.TicketType.TREATMENT, actor=manager,
            )
        viewer = self.create_authorized_user("quality-treatment-viewer")
        self.client.force_login(viewer)
        response = self.client.post(reverse("qualidade:tq_vincular", kwargs={"codigo": order.code}), {
            "ticket_identifier": ticket.ticket_number, "ticket_type": QualityTicket.TicketType.TREATMENT,
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(QualityTicket.objects.filter(quality_order=order).count(), 0)

    def test_monitoring_queue_counts_progress_provider_names_and_filters(self):
        manager = self.create_manager("quality-monitoring-queue")
        provider = CMDBOperatorMirror.objects.create(
            pk=11610, source_config_item_id="CMDB-PROVIDER-11610", name="Vivo",
        )
        link, order = self.create_link_and_order(manager, key="MONITORING-QUEUE", isp_raw=str(provider.pk))
        now = timezone.now()
        ending = QualityMonitoringCycle.objects.create(
            quality_order=order, cycle_number=1, started_at=now - timedelta(days=28),
            expected_end_at=now + timedelta(days=2),
        )
        QualityMonitoringCycle.objects.create(
            quality_order=order, cycle_number=2, started_at=now - timedelta(days=45),
            expected_end_at=now - timedelta(days=15), ended_at=now - timedelta(days=37),
            result=QualityMonitoringCycle.Result.RECURRENCE,
        )
        QualityMonitoringCycle.objects.create(
            quality_order=order, cycle_number=3, started_at=now - timedelta(days=70),
            expected_end_at=now - timedelta(days=40), ended_at=now - timedelta(days=40),
            result=QualityMonitoringCycle.Result.SUCCESS,
        )
        self.client.force_login(manager)
        response = self.client.get(reverse("qualidade:monitoramento"), {"tab": "ending", "isp": "Vivo"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_count"], 1)
        self.assertEqual(response.context["ending_count"], 1)
        self.assertEqual(response.context["recurrence_count"], 1)
        self.assertEqual(response.context["completed_count"], 1)
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        self.assertEqual(response.context["cycles"][0].pk, ending.pk)
        self.assertEqual(response.context["cycles"][0].provider_name, "Vivo")
        self.assertEqual(response.context["cycles"][0].days_elapsed, 28)
        self.assertEqual(response.context["cycles"][0].days_remaining, 2)
        self.assertContains(response, "BD0001200")
        self.assertContains(response, "Vivo")
        self.assertContains(response, "Termina em 2 dias")
        self.assertNotContains(response, str(provider.pk))
        self.assertNotIn(str(provider.pk), response.context["filter_options"]["isp"])

    def test_financial_monitoring_requires_evidence_and_recurrence_is_manual(self):
        manager = self.create_manager("quality-monitoring-financial")
        link, order = self.create_link_and_order(
            manager, key="FINANCIAL", reason="PendÃªncia financeira",
        )
        self.client.force_login(manager)
        page = self.client.get(reverse("qualidade:ordem_detail", kwargs={"codigo": order.code}), {"tab": "monitoramento"})
        self.assertContains(page, "ações registradas")
        self.assertNotContains(page, "Iniciar ciclo RREIP</button>")
        treatment, _initial_ticket = self.prepare_treatment_for_rreip(manager, link, order, ticket_id=85105)
        with self.assertRaises(ValidationError):
            start_monitoring_cycle(quality_order=order, actor=manager)
        with self.assertRaises(ValidationError):
            start_monitoring_cycle(
                quality_order=order, actor=manager,
                actions_analyzed=True, otrs_actions_summary="Ações verificadas no OTRS.",
                financial_regularization_confirmed=True, financial_evidence="",
            )


        cycle = start_monitoring_cycle(
            quality_order=order,
            actor=manager,
            actions_analyzed=True,
            otrs_actions_summary="Operadora confirmou a correção; Qualidade verificou o circuito.",
            financial_regularization_confirmed=True,
            financial_evidence="Comprovante validado pela Qualidade",
        )
        self.assertTrue(cycle.financial_regularization_confirmed)
        self.assertEqual(order.__class__.objects.get(pk=order.pk).status, QualityOrder.Status.MONITORAMENTO)
        cycle.started_at = timezone.now() - timedelta(days=10)
        cycle.expected_end_at = timezone.now() - timedelta(days=1)
        cycle.save(update_fields=("started_at", "expected_end_at"))
        ticket = self.create_ticket(
            link=link, external_id=85104, created_at=timezone.now() - timedelta(days=2),
        )
        with self.assertRaises(ValidationError):
            finish_monitoring_cycle(
                cycle=cycle, result=QualityMonitoringCycle.Result.SUCCESS, actor=manager,
            )
        failed = finish_monitoring_cycle(
            cycle=cycle, result=QualityMonitoringCycle.Result.RECURRENCE,
            trigger_ticket_identifier=ticket.ticket_number, actor=manager,
            recurrence_description="Mesmo defeito constatado no período; evidência no ticket.",
            same_root_cause_confirmed=True,
        )

        self.assertEqual(failed.result, QualityMonitoringCycle.Result.RECURRENCE)
        self.assertEqual(failed.trigger_occurrence.ticket_number, ticket.ticket_number)
        self.assertEqual(failed.ended_at, ticket.source_created_at)
        self.assertTrue(failed.trigger_occurrence.counts_as_recurrence)
        self.assertTrue(failed.trigger_occurrence.same_root_cause)
        self.assertEqual(order.__class__.objects.get(pk=order.pk).status, QualityOrder.Status.TRATAMENTO)
        self.assertTrue(AuditEvent.objects.filter(action="STATUS_CHANGED", object_id=str(order.pk)).exists())
        self.assertTrue(AuditEvent.objects.filter(action="MONITORING_FAILED").exists())

    def test_monitoring_success_and_cancellation_are_recorded_locally(self):
        manager = self.create_manager("quality-monitoring-results")
        link, order = self.create_link_and_order(manager, key="MONITORING-RESULTS")
        treatment, _ticket = self.prepare_treatment_for_rreip(manager, link, order, ticket_id=85106)
        self.client.force_login(manager)
        monitor_page = self.client.get(reverse("qualidade:ordem_detail", kwargs={"codigo": order.code}), {"tab": "monitoramento"})
        self.assertEqual(monitor_page.status_code, 200)
        self.assertContains(monitor_page, "data-quality-monitoring-dialog")
        self.assertContains(monitor_page, "Confirmar in&#237;cio do RREIP")
        start_response = self.client.post(reverse("qualidade:monitoramento_iniciar", kwargs={"codigo": order.code}), {
            "actions_analyzed": "on",
            "otrs_actions_summary": "Ações verificadas no ticket OTRS.",
            "next": f"/qualidade/ordens/{order.code}/?tab=monitoramento",
        })
        self.assertEqual(start_response.status_code, 302)
        successful = order.monitoring_cycles.get(result=QualityMonitoringCycle.Result.IN_PROGRESS)
        self.assertTrue(order.actions.get(title="Liberar para monitoramento RREIP").completed)
        successful.started_at = timezone.now() - timedelta(days=35)
        successful.expected_end_at = timezone.now() - timedelta(days=5)
        successful.save(update_fields=("started_at", "expected_end_at"))
        successful = finish_monitoring_cycle(
            cycle=successful, result=QualityMonitoringCycle.Result.SUCCESS, actor=manager,
            final_analysis="Análise final estável.", stability_validated=True,
            occurrence_review="Ocorrências avaliadas; sem reincidência da causa tratada.",
        )
        self.assertEqual(successful.result, QualityMonitoringCycle.Result.SUCCESS)
        self.assertTrue(order.actions.get(title="Avaliar a estabilidade ao fim do período").completed)
        order.refresh_from_db()
        self.assertEqual(order.status, QualityOrder.Status.MONITORAMENTO)
        self.assertIsNone(order.completed_at)
        treatment.refresh_from_db()
        self.assertEqual(treatment.status, QualityTreatment.Status.MONITORING)
        treatment = complete_quality_treatment(
            treatment=treatment, actor=manager, confirmed=True, observations="Homologado localmente.",
        )
        self.assertEqual(treatment.status, QualityTreatment.Status.COMPLETED)
        self.assertTrue(order.actions.get(title="Concluir a avaliação da Qualidade").completed)
        order.refresh_from_db()
        self.assertEqual(order.status, QualityOrder.Status.CONCLUIDA)
        self.assertIsNotNone(order.completed_at)
        with self.assertRaises(ValidationError):
            start_monitoring_cycle(quality_order=order, actor=manager)

        canceled_link, canceled_order = self.create_link_and_order(manager, key="MONITORING-CANCELLED")
        canceled_treatment, _ = self.prepare_treatment_for_rreip(manager, canceled_link, canceled_order, ticket_id=85107)
        canceled = start_monitoring_cycle(quality_order=canceled_order, actor=manager,
                                          actions_analyzed=True, otrs_actions_summary="Ações verificadas no ticket OTRS.")
        canceled = finish_monitoring_cycle(
            cycle=canceled, result=QualityMonitoringCycle.Result.CANCELED, actor=manager,
        )
        self.assertEqual(canceled.result, QualityMonitoringCycle.Result.CANCELED)
        canceled_treatment.refresh_from_db()
        self.assertEqual(canceled_treatment.status, QualityTreatment.Status.IN_PROGRESS)
        self.assertTrue(AuditEvent.objects.filter(action="MONITORING_SUCCEEDED").exists())
        self.assertTrue(AuditEvent.objects.filter(action="MONITORING_CANCELED").exists())

    def test_custom_data_scope_filters_monitoring_history_contacts_and_contact_writes(self):
        manager = self.create_manager("quality-scope-source")
        allowed_operator = Operadora.objects.create(name="Operadora Escopo", normalized_name="operadora-escopo")
        allowed_project = Projeto.objects.create(name="Projeto Escopo", normalized_name="projeto-escopo")
        visible_link, visible_order = self.create_link_and_order(
            manager, key="SCOPE-VISIBLE", client=allowed_operator.name, project=allowed_project.name,
        )
        hidden_link, hidden_order = self.create_link_and_order(
            manager, key="SCOPE-HIDDEN", client="Outra Operadora", project="Outro Projeto",
        )
        QualityMonitoringCycle.objects.create(
            quality_order=visible_order, cycle_number=1,
            started_at=timezone.now() - timedelta(days=8), expected_end_at=timezone.now() + timedelta(days=22),
        )
        QualityMonitoringCycle.objects.create(
            quality_order=hidden_order, cycle_number=1,
            started_at=timezone.now() - timedelta(days=8), expected_end_at=timezone.now() + timedelta(days=22),
        )

        scoped_group = Group.objects.create(name="Quality custom scope")
        scope = GroupDataScope.objects.create(group=scoped_group, mode=GroupDataScope.Mode.CUSTOM)
        scope.allowed_operators.add(allowed_operator)
        scope.allowed_projects.add(allowed_project)
        scoped_user = User.objects.create_user(username="quality-custom-scope", password="unused-test-password")
        scoped_user.user_permissions.add(
            self.view_permission,
            *Permission.objects.filter(
                content_type__app_label="qualidade",
                codename__in=("manage_quality_orders", "manage_quality_contacts"),
            ),
        )
        scoped_user.groups.add(scoped_group)

        visible_contact = QualityContact.objects.create(
            name="Contato dentro do escopo", client=allowed_operator.name, project=allowed_project.name,
        )
        hidden_contact = QualityContact.objects.create(
            name="Contato fora do escopo", client=allowed_operator.name, project="Outro Projeto",
        )
        for contact in (visible_contact, hidden_contact):
            AuditEvent.objects.create(
                category="QUALITY", action="QUALITY_CONTACT_CREATED", module="Qualidade",
                object_type="QualityContact", object_id=str(contact.pk), object_label=contact.name,
            )

        monitor = get_monitoring_queue(scoped_user, {"tab": "active"})
        contacts = get_quality_contacts(scoped_user, {})
        history = get_global_quality_history(scoped_user, {})
        self.assertEqual(monitor["page_obj"].paginator.count, 1)
        self.assertEqual(monitor["cycles"][0].quality_order_id, visible_order.pk)
        self.assertEqual(contacts["page_obj"].paginator.count, 1)
        self.assertEqual(contacts["contacts"][0].pk, visible_contact.pk)
        self.assertTrue(all(event.object_id != str(hidden_order.pk) for event in history["history_events"]))
        self.assertTrue(all(event.object_id != str(hidden_contact.pk) for event in history["history_events"]))

        self.client.force_login(scoped_user)
        denied_write = self.client.post(reverse("qualidade:contato_salvar"), {
            "name": "Contato nÃ£o permitido", "client": allowed_operator.name,
            "project": "Outro Projeto", "escalation_level": "1", "active": "on",
        })
        self.assertEqual(denied_write.status_code, 302)
        self.assertFalse(QualityContact.objects.filter(name="Contato nÃ£o permitido").exists())

    def test_order_and_global_history_are_filtered_paginated_and_formatted(self):
        manager = self.create_manager("quality-history-paging")
        link, order = self.create_link_and_order(manager, key="HISTORY", provider="Elo SoluÃ§Ãµes")
        QualityAuditEvent.objects.create(
            quality_order=order, event_type="LEVEL_CHANGED", actor=manager,
            description="NÃ­vel alterado de R1 para R2.", metadata={"previous_level": "R1", "level": "R2"},
        )
        local = get_order_history(manager, order, category="order")
        self.assertEqual(local["history_page"].paginator.count, 2)
        self.assertEqual(local["history_events"][0]["label"], "N\u00edvel alterado")

        for index in range(26):
            AuditEvent.objects.create(
                user=manager, category="QUALITY", action="STATUS_CHANGED", module="Qualidade",
                object_type="QualityOrder", object_id=str(order.pk),
                description=f"Status {index}: Triagem â†’ Tratamento.",
                metadata={"old": "TRIAGEM", "new": "TRATAMENTO"},
            )
        first = get_global_quality_history(manager, {"per_page": "25", "q": "BD0001200"})
        second = get_global_quality_history(manager, {"per_page": "25", "q": "BD0001200", "page": "2"})
        self.assertEqual(first["history_page"].paginator.count, 27)
        self.assertEqual(first["history_page"].number, 1)
        self.assertEqual(len(first["history_events"]), 25)
        self.assertEqual(second["history_page"].number, 2)
        self.assertEqual(len(second["history_events"]), 2)
        self.assertEqual(first["history_events"][0].designation, "BD0001200")
        self.assertEqual(first["history_events"][0].provider_name, "Elo SoluÃ§Ãµes")
        self.client.force_login(manager)
        global_page = self.client.get(reverse("qualidade:historico"))
        self.assertContains(global_page, "BD0001200")
        self.assertNotContains(global_page, link.source_number)

    def test_contacts_crud_rbac_recommendation_and_provider_name_presentation(self):
        manager = self.create_manager("quality-contacts-directory")
        provider = CMDBOperatorMirror.objects.create(
            pk=11610, source_config_item_id="CMDB-PROVIDER-CONTACT", name="Vivo",
        )
        link, order = self.create_link_and_order(manager, key="CONTACTS", isp_raw=str(provider.pk))
        self.client.force_login(manager)
        response = self.client.post(reverse("qualidade:contato_salvar"), {
            "name": "Ana Silva", "role": "Qualidade Regional", "company": str(provider.pk),
            "region": "SP", "client": "Cliente Fase 4", "project": "Projeto Fase 4",
            "contact_type": "QUALIDADE", "escalation_level": "2", "email": "ana@example.com",
            "phone": "11999990000", "teams": "ana.silva", "active": "on",
            "next": "/qualidade/contatos/",
        })
        self.assertEqual(response.status_code, 302)
        contact = QualityContact.objects.get(name="Ana Silva")
        self.assertEqual(contact.company, "Vivo")
        self.assertTrue(AuditEvent.objects.filter(action="QUALITY_CONTACT_CREATED", object_id=str(contact.pk)).exists())

        directory = self.client.get(reverse("qualidade:contatos"), {"company": "Vivo", "q": "Ana"})
        self.assertEqual(directory.status_code, 200)
        self.assertContains(directory, "Vivo")
        self.assertContains(directory, "R2")
        legacy_contact = QualityContact.objects.create(
            name="Contato legado", company=str(provider.pk), client="Cliente Fase 4",
            project="Projeto Fase 4", contact_type="QUALIDADE", escalation_level=1,
        )
        AuditEvent.objects.create(
            user=manager, category="QUALITY", action="QUALITY_CONTACT_UPDATED", module="Qualidade",
            object_type="QualityContact", object_id=str(legacy_contact.pk), description="Contato atualizado.",
        )
        directory = self.client.get(reverse("qualidade:contatos"), {"company": "Vivo"})
        detail = self.client.get(
            reverse("qualidade:ordem_detail", kwargs={"codigo": order.code}), {"tab": "tratativas"},
        )
        self.assertContains(directory, "Contato legado")
        self.assertContains(detail, "Contato legado")
        self.assertContains(detail, "Vivo")
        self.assertNotContains(directory, str(provider.pk))
        self.assertNotContains(detail, str(provider.pk))
        provider_history = get_global_quality_history(manager, {"isp": "Vivo"})
        legacy_history_event = next(
            event for event in provider_history["history_events"]
            if event.object_type == "QualityContact" and event.object_id == str(legacy_contact.pk)
        )
        self.assertEqual(legacy_history_event.provider_name, "Vivo")
        self.client.force_login(manager)
        global_history_page = self.client.get(reverse("qualidade:historico"), {"isp": "Vivo"})
        self.assertContains(global_history_page, "Vivo")
        self.assertNotContains(global_history_page, str(provider.pk))
        self.assertEqual(directory.context["page_obj"].paginator.count, 2)
        recommendations = recommended_contacts_for_order(
            manager, client=order.client_snapshot, project=order.project_snapshot,
            provider_name="Vivo", region="SP",
        )
        self.assertEqual({item.pk for item in recommendations}, {contact.pk, legacy_contact.pk})

        update_response = self.client.post(reverse("qualidade:contato_salvar"), {
            "contact_id": str(contact.pk), "name": "Ana Silva", "role": "CoordenaÃ§Ã£o",
            "company": "Vivo", "region": "SP", "client": "Cliente Fase 4", "project": "Projeto Fase 4",
            "contact_type": "COORDENACAO", "escalation_level": "3", "email": "ana@example.com",
            "active": "on", "next": "/qualidade/contatos/",
        })
        self.assertEqual(update_response.status_code, 302)
        contact.refresh_from_db()
        self.assertEqual(contact.role, "CoordenaÃ§Ã£o")
        self.assertEqual(contact.escalation_level, 3)
        deactivate = self.client.post(reverse("qualidade:contato_status", kwargs={"contact_id": contact.pk}), {
            "active": "false", "next": "/qualidade/contatos/",
        })
        self.assertEqual(deactivate.status_code, 302)
        contact.refresh_from_db()
        self.assertFalse(contact.active)
        self.assertTrue(AuditEvent.objects.filter(action="QUALITY_CONTACT_DEACTIVATED", object_id=str(contact.pk)).exists())

        viewer = self.create_authorized_user("quality-contact-viewer")
        self.client.force_login(viewer)
        denied = self.client.post(reverse("qualidade:contato_salvar"), {"name": "Nope"})
        self.assertEqual(denied.status_code, 403)
