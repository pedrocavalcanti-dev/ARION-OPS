"""Institutional email rendering for Quality Treatments."""
from django.contrib.staticfiles import finders
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.templatetags.static import static
from django.template.loader import render_to_string

from aplicativos.integracoes.services.project_names import canonical_project_name, project_comparison_key
from aplicativos.integracoes.models import Integration
from aplicativos.integracoes.clients.factory import otrs_configuration_notice

from ..models import QualityCorrelationCheckpoint, QualityTreatment
from .tq_correlation import build_quality_treatment_email_payload


# Only fixed, repository-owned assets may be embedded in an email.
CLIENT_LOGO_ASSETS = {
    "BANCO DO BRASIL": "branding/clientes/banco-do-brasil.png",
    "CORREIOS": "branding/clientes/correios.png",
    "CAIXA": "branding/clientes/caixa.png",
    "CARREFOUR": "branding/clientes/carrefour.png",
    "BRADESCO": "branding/clientes/bradesco.png",
    "ARCOS DOURADOS": "branding/clientes/arcos-dourados.png",
}
ARION_LOGO_ASSET = "branding/arion/arion-ops-logo-light.png"
REQUESTED_ACTIONS = (
    "Identificar e fundamentar a causa raiz da reincidência.",
    "Apresentar o diagnóstico técnico do circuito.",
    "Descrever as ações corretivas realizadas e as medidas preventivas planejadas.",
    "Anexar evidências técnicas e os resultados obtidos.",
    "Informar o responsável técnico e o prazo previsto para conclusão.",
)
QUALITY_FOLLOWUP = (
    "Esta solicitação integra o acompanhamento de Qualidade do NOC ATACADO. Após a análise e validação das ações corretivas, "
    "o circuito poderá seguir para Monitoramento RREIP, visando verificar a estabilidade e reduzir novas reincidências. "
    "A resolução do chamado de suporte relacionado não conclui automaticamente esta TQ."
)
QUALITY_PURPOSE = (
    "Solicitação para análise de qualidade e prevenção de novas reincidências. "
    "Não é um pedido de restauração do serviço nem de abertura de chamado de reparo."
)
QUALITY_ROUTING_NOTICE = "O texto de encaminhamento não confirma, por si só, a criação do ticket na fila do OTRS."


def _client_logo_path(client, project):
    for value in (project, client):
        normalized = canonical_project_name(value)
        asset = CLIENT_LOGO_ASSETS.get(normalized) or CLIENT_LOGO_ASSETS.get(project_comparison_key(normalized))
        if asset and finders.find(asset):
            return asset
    return ""


def _https_static_url(request, asset):
    if not request or not request.is_secure():
        return ""
    try:
        return request.build_absolute_uri(static(asset))
    except (ValueError, RuntimeError):
        return ""


def build_treatment_email(treatment, *, detail, request=None):
    """Return final subject, HTML and text using scoped Quality Order data."""
    order = treatment.quality_order
    # The detail service supplies a link only after applying this user's Data Scope.
    link = detail.get("link")
    designation = detail.get("designation") or order.designation_snapshot
    if not designation:
        designation = order.ci_snapshot or (link.source_config_item_id if link else "") or "Designação não informada"

    client = detail.get("client") or order.client_snapshot or "Não informado"
    project = detail.get("project") or order.project_snapshot or "Não informado"
    operator = detail.get("operator_name") or "Não informada"
    provider = detail.get("provider_name") or order.isp_snapshot or "Não informado"
    valid_count = detail.get("valid_occurrence_count", 0)
    recurrence_count = detail.get("recurrence_count", 0)
    same_cause_count = detail.get("same_cause_count", 0)
    if valid_count:
        recurrence_summary = f"{valid_count} ocorrências válidas; {recurrence_count} reincidências classificadas"
        if same_cause_count:
            recurrence_summary += f"; {same_cause_count} com mesma causa confirmada"
    else:
        recurrence_summary = "Nenhuma ocorrência válida registrada na base local da OQ"

    first_occurrence_row = detail.get("first_occurrence")
    first_occurrence = first_occurrence_row.source_created_at if first_occurrence_row else detail.get("current_episode_started_at")
    latest_occurrence = detail.get("current_episode_last_at")
    if not latest_occurrence and detail.get("latest_occurrence"):
        latest_occurrence = detail["latest_occurrence"].source_created_at
    state = "Não confirmada pelos dados disponíveis"
    if link:
        state = link.incident_state or link.deployment_state or state
    elif order.status:
        state = order.get_status_display()

    support_record = detail.get("support_ticket")
    support_ticket = support_record.ticket_number if support_record else "Não informado"
    subject_payload = build_quality_treatment_email_payload(
        treatment,
        designation=designation,
        client=client,
        project=project,
        operator=operator,
        provider=provider,
        recurrence_summary=recurrence_summary,
        support_ticket_number=support_ticket,
    )
    project_logo = _client_logo_path(client, project)
    reason_context = "\n\n".join(dict.fromkeys(
        value.strip() for value in (treatment.reason, treatment.description) if value and value.strip()
    )) or "Não informado."
    context = {
        "treatment": treatment,
        "order": order,
        "subject": subject_payload["subject"],
        "designation": designation,
        "client": client,
        "project": project,
        "operator": operator,
        "provider": provider,
        "request_date": treatment.created_at,
        "owner": detail.get("owner_label") or "Não atribuído",
        "treatment_owner": (
            treatment.owner.get_full_name() or treatment.owner.get_username()
            if treatment.owner_id else "Não atribuído"
        ),
        "priority": order.get_priority_display() if order.priority else "Não definida",
        "valid_occurrence_count": valid_count,
        "recurrence_count": recurrence_count,
        "same_cause_count": same_cause_count,
        "first_occurrence": first_occurrence,
        "latest_occurrence": latest_occurrence,
        "operational_state": state,
        "support_ticket": support_ticket,
        "support_ticket_state": support_record.external_state_name if support_record else "Não informado",
        "reason": reason_context,
        "reason_context": reason_context,
        "requested_treatment": "",
        "requested_actions": REQUESTED_ACTIONS,
        "quality_followup": QUALITY_FOLLOWUP,
        "quality_purpose": QUALITY_PURPOSE,
        "requested_destination": "NOC - QUALIDADE ATACADO",
        "quality_routing_notice": QUALITY_ROUTING_NOTICE,
        "client_logo_url": _https_static_url(request, project_logo) if project_logo else "",
        "arion_logo_url": _https_static_url(request, ARION_LOGO_ASSET),
    }
    return {
        "subject": context["subject"],
        "html_content": render_to_string("qualidade/emails/treatment_request.html", context),
        "text_content": render_to_string("qualidade/emails/treatment_request.txt", context),
        "context": context,
    }


def brevo_send_readiness(treatment, integration):
    """Return a safe send decision and reasons without decrypting the API key."""
    config = integration.config or {} if integration else {}
    method = config.get("send_method", "api_rest")
    blockers = []
    if not integration or not integration.enabled:
        blockers.append("Integração Brevo desabilitada")
    if method == "smtp":
        if not integration or not integration.smtp_secret_ciphertext:
            blockers.append("Chave SMTP não configurada")
        if not config.get("smtp_login"):
            blockers.append("Login SMTP não configurado")
        if integration:
            latest_smtp_check = integration.checks.filter(method="smtp").exclude(
                message__startswith="HOMOLOGAÇÃO ARION-ROUTE-",
            ).order_by("-created_at", "-pk").first()
            if not latest_smtp_check:
                blockers.append("Autenticação SMTP ainda não validada")
            elif latest_smtp_check.status != "success":
                blockers.append("Último teste de autenticação SMTP falhou")
    elif not integration or not integration.secret_ciphertext:
        blockers.append("API Key não configurada")
    if not config.get("sender_name") or not config.get("sender_email"):
        blockers.append("Remetente não configurado")
    if not config.get("quality_recipient"):
        blockers.append("Destinatário da Qualidade não configurado")
    if not config.get("quality_recipient_validated"):
        blockers.append("Roteamento do destinatário operacional ainda não validado no OTRS")
    else:
        try:
            validate_email(str(config["quality_recipient"]).strip())
        except ValidationError:
            blockers.append("Destinatário da Qualidade inválido")
    if config.get("quality_cc"):
        try:
            validate_email(str(config["quality_cc"]).strip())
        except ValidationError:
            blockers.append("E-mail em cópia inválido")
    if config.get("sender_email"):
        try:
            validate_email(str(config["sender_email"]).strip())
        except ValidationError:
            blockers.append("E-mail do remetente inválido")
    if not integration or not integration.allow_real_sends:
        blockers.append("Envio real desabilitado administrativamente")
    otrs_integration = Integration.objects.filter(key=Integration.Key.OTRS).first()
    if otrs_configuration_notice(otrs_integration):
        blockers.append("Integra\u00e7\u00e3o OTRS somente leitura n\u00e3o configurada para correla\u00e7\u00e3o")
    else:
        from .tq_otrs_correlation import quality_queue_names

        queue_names = quality_queue_names()
        successful_queues = set(QualityCorrelationCheckpoint.objects.filter(
            queue_name__in=queue_names, last_success_at__isnull=False,
        ).values_list("queue_name", flat=True))
        if not queue_names or successful_queues != set(queue_names):
            blockers.append("Leitura incremental da fila Qualidade ainda n\u00e3o validada pelo worker")
    if not treatment or treatment.quality_ticket_external_id:
        blockers.append("TQ já vinculada a um ticket OTRS")
    elif not (
        treatment.status == QualityTreatment.Status.PENDING_SEND
        and treatment.send_status == QualityTreatment.SendStatus.NOT_SENT
        or treatment.status == QualityTreatment.Status.SEND_FAILED
        and treatment.send_status == QualityTreatment.SendStatus.FAILED
    ):
        blockers.append("Estado da TQ não permite envio ou nova tentativa controlada")
    return {"ready": not blockers, "blockers": blockers, "config": config, "method": method}


def treatment_send_block_message(blockers):
    if not blockers:
        return ""
    return "Envio indisponível: " + "; ".join(blockers) + "."
