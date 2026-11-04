"""Stable TQ email identifiers and conflict-safe correlation from article bodies."""
from html.parser import HTMLParser
import logging
import re
import unicodedata

from django.db import IntegrityError, transaction
from django.utils import timezone

from aplicativos.auditoria.services import AuditService

from ..models import QualityAuditEvent, QualityOrder, QualityTreatment


logger = logging.getLogger(__name__)
CORRELATION_PATTERN = re.compile(r"(?<![A-Za-z0-9_-])ARION-QID-[0-9A-Fa-f]{32}(?![A-Za-z0-9_-])")
MAX_SUBJECT_LENGTH = 255


class _ArticleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def extract_correlation_ids(article_body):
    """Extract complete IDs from plain text or HTML article content."""
    body = str(article_body or "")
    parser = _ArticleText()
    try:
        parser.feed(body)
        parser.close()
    except Exception:
        parser.parts = []
    text = f"{body}\n{' '.join(parser.parts)}"
    return tuple(sorted({match.upper() for match in CORRELATION_PATTERN.findall(text)}))


def _clean_subject_part(value, fallback):
    text = " ".join(str(value or "").split())
    text = "".join(char for char in text if unicodedata.category(char) != "Cc")
    text = text.replace("|", "-").strip(" -")
    return text or fallback


def _subject_text_with_limit(value, limit):
    """Shorten descriptive labels visibly while keeping identifiers intact."""
    if len(value) <= limit:
        return value
    if limit <= 1:
        return "…"
    return f"{value[:limit - 1].rstrip()}…"


def _build_treatment_subject(*, order_code, treatment_code, project, operator, designation):
    prefix = "SOLICITAÇÃO DE TRATATIVA DE QUALIDADE"
    essential = (prefix, order_code, treatment_code, designation)
    fixed_length = sum(map(len, essential)) + 5 * 3
    available_for_names = MAX_SUBJECT_LENGTH - fixed_length
    if available_for_names < 2:
        raise ValueError("Os códigos da OQ, TQ e designação excedem o limite de assunto do e-mail.")

    name_total = len(project) + len(operator)
    if name_total > available_for_names:
        project_limit = max(1, min(available_for_names - 1, round(available_for_names * len(project) / name_total)))
        operator_limit = min(len(operator), available_for_names - project_limit)
        remaining = available_for_names - project_limit - operator_limit
        if remaining:
            project_limit = min(len(project), project_limit + remaining)
            remaining = available_for_names - project_limit - operator_limit
        if remaining:
            operator_limit = min(len(operator), operator_limit + remaining)
        project = _subject_text_with_limit(project, project_limit)
        operator = _subject_text_with_limit(operator, operator_limit)

    subject = " | ".join((prefix, order_code, treatment_code, project, operator, designation))
    if len(subject) > MAX_SUBJECT_LENGTH:
        raise ValueError("O assunto do e-mail excede o limite operacional de 255 caracteres.")
    return subject


def build_quality_treatment_email_payload(
    treatment, *, designation="", client="", project="", operator="", provider="",
    recurrence_summary="", support_ticket_number="",
):
    """Compose future email fields without sending or placing the QID in its subject."""
    order = treatment.quality_order
    link = order.link if order.link_id else None
    if link:
        from aplicativos.integracoes.services.project_names import display_project_name
        from aplicativos.integracoes.services.semantics import link_designation, provider_values_for_links

        designation = designation or link_designation(link)
        client = client or link.contracting_client or order.client_snapshot
        project = project or display_project_name(link.business_project or link.legacy_project) or order.project_snapshot
        # Keep the contracting operator separate from the ISP/provider.
        operator = operator or (link.contracting_client.strip() if link.contracting_client else "")
        if not provider:
            provider = provider_values_for_links([link])[link.pk].provider_name
    else:
        designation = designation or order.designation_snapshot
        client = client or order.client_snapshot
        project = project or order.project_snapshot
        operator = operator or "Não informada"
        provider = provider or order.isp_snapshot
    if not recurrence_summary:
        from ..models import QualityOccurrence

        recurrence_count = QualityOccurrence.objects.filter(
            quality_order=order, excluded=False, counts_as_recurrence=True,
        ).count()
        same_cause_count = QualityOccurrence.objects.filter(
            quality_order=order, excluded=False, counts_as_recurrence=True, same_root_cause=True,
        ).count()
        recurrence_summary = f"{recurrence_count} reincidências classificadas; {same_cause_count} com mesma causa confirmada"
    support_ticket_number = support_ticket_number or treatment.support_ticket_number
    designation = _clean_subject_part(designation, "Designação não informada")
    order_code = _clean_subject_part(order.code, "OQ não informada")
    treatment_code = _clean_subject_part(treatment.code, "TQ não informada")
    project = _clean_subject_part(project, "Projeto não informado")
    operator = _clean_subject_part(operator, "Operadora não informada")
    subject = _build_treatment_subject(
        order_code=order_code,
        treatment_code=treatment_code,
        project=project,
        operator=operator,
        designation=designation,
    )
    context_values = []
    for value in (treatment.reason, treatment.description):
        value = " ".join(str(value or "").split())
        if value and value.casefold() not in {item.casefold() for item in context_values}:
            context_values.append(value)
    request_actions = (
        "Identificar e fundamentar a causa raiz da reincidência.",
        "Apresentar o diagnóstico técnico do circuito.",
        "Descrever as ações corretivas realizadas e as medidas preventivas planejadas.",
        "Anexar evidências técnicas e os resultados obtidos.",
        "Informar o responsável técnico e o prazo previsto para conclusão.",
    )
    lines = [
        "ENCAMINHAMENTO SOLICITADO: NOC - QUALIDADE ATACADO",
        "Esta solicitação destina-se à análise de reincidências e melhoria da qualidade. Não se trata de pedido de restauração ou reparo do serviço.",
        "O encaminhamento no corpo não confirma, por si só, a criação do ticket na fila do OTRS.",
        "",
        "Solicitação de Tratativa de Qualidade",
        f"TQ: {treatment_code}",
        f"OQ: {order_code}",
        f"Designação do link: {_clean_subject_part(designation, 'Não informada')}",
        f"Cliente: {_clean_subject_part(client, 'Não informado')}",
        f"Projeto: {_clean_subject_part(project, 'Não informado')}",
        f"Operadora: {_clean_subject_part(operator, 'Não informada')}",
        f"Provedor: {_clean_subject_part(provider, 'Não informado')}",
        f"Histórico resumido de reincidências: {_clean_subject_part(recurrence_summary, 'Sem resumo disponível')}",
        f"Chamado de suporte relacionado: {_clean_subject_part(support_ticket_number, 'Não informado')}",
        "",
        "Contexto da solicitação:",
        "\n\n".join(context_values) or "Não informado.",
        "",
        "Solicitações à operadora:",
    ]
    lines.extend(f"{index}. {action}" for index, action in enumerate(request_actions, start=1))
    lines.extend(("", f"ID de correlação ARION OPS: {treatment.correlation_id}"))
    return {"subject": subject, "body": "\n".join(lines)}


@transaction.atomic
def link_quality_ticket_from_article(*, ticket, article_body, allowed_queue_names, actor=None, request=None, trusted_source=False):
    """Link one mirrored ticket using the QID from its article body only."""
    allowed_queue_names = {str(name).strip() for name in (allowed_queue_names or ()) if str(name).strip()}
    if not allowed_queue_names:
        return {"status": "QUEUE_NOT_CONFIGURED"}
    if not trusted_source:
        from aplicativos.integracoes.models import OTRSTicket

        ticket = OTRSTicket.objects.filter(
            external_id=ticket.external_id, is_present_in_queue=True,
        ).first()
        if ticket is None:
            return {"status": "TICKET_NOT_AVAILABLE"}
    elif not getattr(ticket, "is_present_in_queue", False):
        return {"status": "TICKET_NOT_AVAILABLE"}
    if ticket.queue_name not in allowed_queue_names:
        return {"status": "QUEUE_MISMATCH"}

    identifiers = extract_correlation_ids(article_body)
    if not identifiers:
        logger.info("Quality ticket %s awaits a complete article correlation ID.", ticket.external_id)
        return {"status": "MISSING_CORRELATION_ID"}
    if len(identifiers) != 1:
        logger.warning("Quality ticket %s article has multiple correlation IDs.", ticket.external_id)
        AuditService.record(
            user=actor, category="QUALITY", action="TREATMENT_CORRELATION_CONFLICT",
            module="Qualidade", description="Artigo OTRS contém múltiplos IDs de correlação de TQ.",
            metadata={"ticket_external_id": ticket.external_id, "issue": "multiple_correlation_ids",
                      "correlation_id_count": len(identifiers)},
        )
        return {"status": "MULTIPLE_CORRELATION_IDS"}

    treatment = QualityTreatment.objects.filter(correlation_id=identifiers[0]).first()
    if treatment is None:
        return {"status": "TREATMENT_NOT_FOUND"}
    treatment = QualityTreatment.objects.select_for_update().get(pk=treatment.pk)
    if treatment.quality_ticket_external_id == ticket.external_id:
        treatment.quality_ticket_last_read_at = timezone.now()
        treatment.quality_ticket_state_snapshot = ticket.external_state_name
        treatment.quality_ticket_queue_snapshot = ticket.queue_name
        treatment.save(update_fields=(
            "quality_ticket_last_read_at", "quality_ticket_state_snapshot",
            "quality_ticket_queue_snapshot", "updated_at",
        ))
        return {"status": "ALREADY_LINKED", "treatment": treatment}
    if treatment.quality_ticket_external_id:
        logger.warning("TQ %s already has a different Quality ticket.", treatment.code)
        AuditService.record(
            user=actor, category="QUALITY", action="TREATMENT_TICKET_CONFLICT",
            module="Qualidade", description=f"Conflito de correlação detectado para a {treatment.code}.",
            obj=treatment.quality_order,
            metadata={"treatment_code": treatment.code, "ticket_external_id": ticket.external_id,
                      "existing_ticket_external_id": treatment.quality_ticket_external_id,
                      "issue": "treatment_already_linked"},
        )
        return {"status": "TREATMENT_TICKET_CONFLICT", "treatment": treatment}
    if QualityTreatment.objects.filter(quality_ticket_external_id=ticket.external_id).exclude(pk=treatment.pk).exists():
        logger.warning("Quality ticket %s is already linked to another TQ.", ticket.external_id)
        AuditService.record(
            user=actor, category="QUALITY", action="TREATMENT_TICKET_CONFLICT",
            module="Qualidade", description=f"O ticket de Qualidade já está associado a outra TQ.",
            obj=treatment.quality_order,
            metadata={"treatment_code": treatment.code, "ticket_external_id": ticket.external_id,
                      "issue": "ticket_already_linked"},
        )
        return {"status": "TICKET_TREATMENT_CONFLICT", "treatment": treatment}

    now = timezone.now()
    treatment.quality_ticket_external_id = ticket.external_id
    treatment.quality_ticket_number = ticket.ticket_number
    treatment.quality_ticket_queue_snapshot = ticket.queue_name
    treatment.quality_ticket_state_snapshot = ticket.external_state_name
    treatment.quality_ticket_linked_at = now
    treatment.quality_ticket_last_read_at = now
    if treatment.status == QualityTreatment.Status.WAITING_OTRS:
        treatment.status = QualityTreatment.Status.IN_PROGRESS
    try:
        with transaction.atomic():
            treatment.save(update_fields=(
                "quality_ticket_external_id", "quality_ticket_number", "quality_ticket_queue_snapshot",
                "quality_ticket_state_snapshot", "quality_ticket_linked_at", "quality_ticket_last_read_at",
                "status", "active_order_key", "updated_at",
            ))
    except IntegrityError:
        return {"status": "TICKET_TREATMENT_CONFLICT", "treatment": treatment}

    description = f"Ticket de Qualidade {ticket.ticket_number} vinculado à TQ {treatment.code}."
    QualityAuditEvent.objects.create(
        quality_order=treatment.quality_order,
        event_type=QualityAuditEvent.EventType.TREATMENT_TICKET_LINKED,
        actor=actor,
        description=description,
        metadata={"treatment_code": treatment.code, "ticket_number": ticket.ticket_number,
                  "ticket_external_id": ticket.external_id, "queue": ticket.queue_name},
    )
    AuditService.record(
        request=request, user=actor, category="QUALITY", action="TREATMENT_TICKET_LINKED",
        module="Qualidade", description=description, obj=treatment.quality_order,
        metadata={"code": treatment.quality_order.code, "treatment_code": treatment.code,
                  "ticket_number": ticket.ticket_number, "ticket_external_id": ticket.external_id,
                  "queue": ticket.queue_name},
    )
    return {"status": "LINKED", "treatment": treatment}
