from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from aplicativos.auditoria.services import AuditService
from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.integracoes.models import OTRSTicket, OTRSTicketConfigItem, CMDBLinkMirror
from aplicativos.integracoes.services.semantics import link_designation

from .. import forms
from ..models import (CancellationIntake, CancellationProcess, CancellationEvent, ISPTreatment,
                     CancellationPending, CancellationEquipment, FinancialObligation, CancellationEvidence,
                     FinancialHandoff, HandoffObligation)
from .data_scope import CancellationScope
from .otrs_intake import source_status, intake_settings, ticket_is_eligible
from .identity import normalize_ticket_number


class Conflict(ValidationError):
    pass


def require(user, permission):
    if not user.has_perm(f"cancelamentos.{permission}"):
        raise PermissionDenied


def valid(form):
    if not form.is_valid():
        raise ValidationError([f"{form.fields[k].label or k}: {error}" if k in form.fields else str(error)
                               for k, errors in form.errors.items() for error in errors])
    return form.cleaned_data


def event(*, request, intake, action, description, process=None, financial=False, metadata=None, details=None):
    # Business text belongs in the scoped timeline, never in central audit metadata.
    audit = AuditService.record(request=request, category="cancellation", module="cancelamentos", action=action,
                                description="Operação local de Cancelamentos registrada.", obj=process or intake,
                                metadata={"intake_id": intake.pk if intake else None, "process_id": process.pk if process else None,
                                          "ticket_external_id": intake.ticket_external_id if intake else process.external_ticket_id if process else None, **(metadata or {})})
    return CancellationEvent.objects.create(intake=intake, process=process, actor=request.user, action=action,
                                             description=description, details=details or {}, financial=financial, audit_event=audit)


def check_revision(obj, revision):
    if obj.revision != revision:
        raise Conflict("Este registro mudou. Recarregue a página e revise os dados antes de continuar.")


@transaction.atomic
def decide_intake(*, request, pk, action, data):
    require(request.user, "view_cancellations")
    require(request.user, "assign_cancellations" if action in ("assume", "assign") else "triage_cancellations")
    entry = get_object_or_404(CancellationScope.intakes(request.user).select_for_update(), pk=pk)
    form = forms.AssignmentForm(data) if action == "assign" else forms.TriageForm(data, user=request.user, intake=entry)
    values = valid(form)
    check_revision(entry, values["revision"])
    if entry.status != CancellationIntake.Status.WAITING:
        raise Conflict("A entrada já possui decisão. Ela permanece disponível no histórico.")
    if CancellationProcess.objects.filter(Q(intake=entry) | Q(ticket_number=normalize_ticket_number(entry.ticket_number))
                                          | Q(source_ticket__external_id=entry.ticket_external_id)).exists():
        raise Conflict("Este ticket já possui processo oficial, inclusive por importação histórica. Consulte o registro existente.")
    if action not in ("approve", "reject", "wait", "assume", "assign"):
        raise ValidationError("Ação de triagem inválida.")
    if action in ("assign", "assume") and (not entry.eligible or not ticket_is_eligible(entry.ticket)):
        raise ValidationError("A entrada saiu do KPI atual. Consulte seu histórico.")
    old_owner = entry.assigned_to_id
    if not CancellationIntake.objects.filter(pk=entry.pk, revision=values["revision"]).update(revision=F("revision") + 1):
        raise Conflict("Outro analista alterou esta entrada. Recarregue a página.")
    if action == "assume":
        if entry.assigned_to_id and entry.assigned_to_id != request.user.pk:
            raise Conflict("Outro analista já assumiu esta entrada.")
        if entry.assigned_to_id == request.user.pk:
            CancellationIntake.objects.filter(pk=entry.pk).update(revision=entry.revision)
            return entry
        entry.assigned_to = request.user
        description = "Entrada assumida para análise."
    elif action == "assign":
        owner = values["owner"]
        if owner and (not owner.has_perm("cancelamentos.view_cancellations") or not CancellationScope.intakes(owner).filter(pk=entry.pk).exists()):
            raise ValidationError("O responsável selecionado não possui acesso a esta entrada.")
        if entry.assigned_to_id == (owner.pk if owner else None):
            # No duplicate event for an unchanged assignment; revert the CAS increment too.
            CancellationIntake.objects.filter(pk=entry.pk).update(revision=entry.revision)
            return entry
        entry.assigned_to = owner
        description = f"Responsável pela triagem: {(owner.get_full_name() or owner.get_username()) if owner else 'não atribuído'}."
    elif action == "reject":
        if not values["reason"].strip():
            raise ValidationError("Informe a justificativa da rejeição.")
        entry.status = CancellationIntake.Status.REJECTED
        entry.decision_reason = values["reason"]
        entry.decided_by, entry.decided_at = request.user, timezone.now()
        description = f"Entrada rejeitada: {values['reason']}"
    elif action == "wait":
        if not values["reason"].strip():
            raise ValidationError("Registre o motivo para manter a análise pendente.")
        description = f"Análise mantida pendente: {values['reason']}"
    else:
        ok, notice, _ = source_status()
        ticket = OTRSTicket.objects.select_for_update().filter(pk=entry.ticket_id).first()
        if not ok:
            raise ValidationError(notice)
        if not entry.eligible or not ticket_is_eligible(ticket):
            raise ValidationError("O ticket não pertence mais ao KPI 1 de entrada. Atualize a análise.")
        if (not ticket.source_synced_at or ticket.source_synced_at < timezone.now() - timedelta(minutes=intake_settings().max_age_minutes)
                or entry.source_changed_at != ticket.source_changed_at or entry.source_synced_at != ticket.source_synced_at):
            raise Conflict("O espelho mudou ou está desatualizado. Aguarde a projeção e revise a entrada.")
        link = values.get("link")
        if link:
            link = CMDBLinkMirror.objects.select_for_update().select_related("operator").get(pk=link.pk)
            if not link.is_present_in_source or not DataScopeService.filter_queryset(request.user, CMDBLinkMirror.objects.filter(pk=link.pk)).exists():
                raise Conflict("O circuito mudou ou saiu do seu escopo. Revise a entrada.")
        if not link and not DataScopeService.is_global(request.user):
            raise ValidationError("É necessário um vínculo CMDB confiável dentro do escopo.")
        if not link and not values["reason"].strip():
            raise ValidationError("Justifique a aprovação sem circuito validado; a conclusão exigirá completar o vínculo.")
        # Only an explicitly selected, current Ticket–CI relation can be admitted.
        if link and not OTRSTicketConfigItem.objects.filter(ticket=ticket, cmdb_link=link, is_present_in_source=True).exists():
            raise Conflict("O vínculo CMDB mudou. Revise os candidatos.")
        process = CancellationProcess.objects.create(
            intake=entry, ticket_number=normalize_ticket_number(entry.ticket_number), owner=request.user, link=link,
            config_item_id=link.source_config_item_id if link else "",
            contracting_client=link.contracting_client if link else "",
            project=(link.business_project or link.legacy_project) if link else "",
            designation=link_designation(link) if link else "", isp=link.operator.name if link and link.operator else "",
            city=link.city if link else "", state=link.state if link else "",
        )
        ISPTreatment.objects.create(process=process)
        if entry.assigned_to and entry.assigned_to.has_perm("cancelamentos.view_cancellations") and CancellationScope.processes(entry.assigned_to).filter(pk=process.pk).exists():
            process.owner = entry.assigned_to
            process.save(update_fields=("owner", "updated_at"))
        entry.status = CancellationIntake.Status.APPROVED
        entry.decision_reason = values["reason"]
        entry.decided_by, entry.decided_at = request.user, timezone.now()
        description = f"Pedido {process.code} criado por aprovação humana. {values['reason']}".strip()
    entry.revision += 1
    entry.save()
    event(request=request, intake=entry, action=f"triage_{action}", description=description,
          process=process if action == "approve" else None,
          metadata={"queue_external_id": entry.queue_external_id, "revision": entry.revision,
                    **({"previous_owner_id": old_owner, "owner_id": entry.assigned_to_id} if action in ("assign", "assume") else {})},
          details={"queue_name": entry.queue_name, "queue_external_id": entry.queue_external_id,
                   "source_changed_at": entry.source_changed_at.isoformat() if entry.source_changed_at else None,
                   "config_item_id": process.config_item_id if action == "approve" else ""})
    return entry


ACTION_PERMISSIONS = {
    "assign": "assign_cancellations", "assume": "assign_cancellations",
    "update": "change_cancellation_stage", "start": "change_cancellation_stage",
    "treatment": "change_cancellation_stage", "note": "change_cancellation_stage", "link": "change_cancellation_stage",
    "pending_add": "manage_cancellation_pending", "pending_resolve": "manage_cancellation_pending",
    "equipment_add": "manage_cancellation_pending", "equipment_update": "manage_cancellation_pending",
    "obligation_add": "manage_cancellation_finance", "obligation_update": "manage_cancellation_finance",
    "handoff": "manage_cancellation_finance", "evidence": "change_cancellation_stage",
    "complete": "complete_cancellations",
}


def _assign(process, user):
    if user and (not user.has_perm("cancelamentos.view_cancellations") or not CancellationScope.processes(user).filter(pk=process.pk).exists()):
        raise ValidationError("O responsável selecionado não possui acesso a este processo.")
    process.owner = user


def pending_is_financial(pending):
    return (pending.kind in forms.FINANCIAL_PENDING_KINDS or pending.amount is not None
            or getattr(pending, "legacy_reference", None) is not None)


def financial_summary(process):
    treatment = process.treatment
    lines = [f"Assunto: Cancelamento {process.display_ticket_number} | {process.code} | {process.isp} | {process.designation}",
             "", f"Cliente: {process.contracting_client}", f"Cliente final: {process.final_client or 'Não informado'}",
             f"Projeto: {process.project}", f"Circuito: {process.designation}", f"Localidade: {process.city} / {process.state}",
             f"Data efetiva: {treatment.effective_date or 'Não confirmada'}",
             f"Encerramento de faturamento: {'Confirmado' if treatment.billing_closed else 'Não confirmado'}",
             f"Evidência ISP: {treatment.evidence_reference or 'Não informada'}", "", "Obrigações registradas:"]
    for obligation in process.obligations.order_by("pk"):
        lines.append(f"- #{obligation.pk} {obligation.get_kind_display()}: {obligation.description}; "
                     f"previsto R$ {obligation.estimated_amount if obligation.estimated_amount is not None else 'não informado'}; "
                     f"cobrado R$ {obligation.charged_amount if obligation.charged_amount is not None else 'não informado'}; "
                     f"vencimento {obligation.due_date or 'não informado'}; {obligation.get_document_status_display()}; "
                     f"documento {obligation.document_reference or 'não disponível'}; referência {obligation.reference_number or 'não informada'}.")
        if obligation.legacy_field:
            lines.append(f"  Origem histórica: {obligation.legacy_field}; {obligation.get_reconciliation_state_display()}. Não somar com pendências sem conciliação.")
    if not process.obligations.exists():
        lines.append("- Nenhuma obrigação registrada.")
    lines += ["", "Documentos e evidências:"]
    lines += [f"- {e.label}: {e.reference}" for e in process.evidence.order_by("pk")]
    lines += ["", "Observações operacionais:"]
    lines += [e.description for e in process.events.filter(action="note", financial=False).order_by("created_at", "pk")]
    lines += ["", "Encaminhamento para análise do Financeiro. Este registro não confirma agendamento ou pagamento."]
    return "\n".join(lines)


def completion_blockers(process):
    blockers = []
    t = process.treatment
    if not (process.link_id and process.config_item_id and process.contracting_client and process.project and process.designation and process.isp):
        blockers.append("Complete a identificação do circuito e valide o vínculo CMDB.")
    if not (t.service_confirmed and t.effective_date and t.confirmed_at and t.evidence_reference):
        blockers.append("Confirme o cancelamento do serviço com data efetiva e evidência do ISP.")
    if not t.billing_closed:
        blockers.append("Confirme o encerramento do faturamento.")
    if process.pendings.filter(status="OPEN").exists():
        blockers.append("Existem pendências abertas; resolva ou encaminhe com evidência.")
    if process.equipment.exclude(status__in=("RETURNED", "WAIVED")).exists():
        blockers.append("Existem equipamentos sem devolução ou dispensa comprovada.")
    covered = HandoffObligation.objects.filter(
        obligation_id=OuterRef("pk"), obligation_updated_at=OuterRef("updated_at"),
        document_status=OuterRef("document_status"), document_reference=OuterRef("document_reference"),
        charged_amount=OuterRef("charged_amount"),
    )
    if process.obligations.annotate(_forwarded=Exists(covered)).filter(
        Q(charged_amount__isnull=True) | ~Q(document_status="AVAILABLE") | Q(document_reference="") | Q(_forwarded=False)
    ).exists():
        blockers.append("Há cobranças sem valores/documentos completos ou sem encaminhamento atualizado ao Financeiro.")
    return blockers


@transaction.atomic
def act_on_process(*, request, pk, action, data):
    require(request.user, "view_cancellations")
    if action not in ACTION_PERMISSIONS:
        raise ValidationError("Ação inválida.")
    require(request.user, ACTION_PERMISSIONS[action])
    if action in ("obligation_add", "obligation_update", "handoff"):
        require(request.user, "view_cancellation_finance")
    process = get_object_or_404(CancellationScope.processes(request.user).select_for_update(), pk=pk)
    revision = valid(forms.OperationalForm(data))["revision"]
    check_revision(process, revision)
    if process.stage == CancellationProcess.Stage.COMPLETED and action not in ("assign", "assume"):
        raise ValidationError("Processo concluído: o histórico permanece preservado para consulta.")
    if action in ("pending_resolve", "equipment_update", "obligation_update"):
        try:
            child_id = int(data.get("child_id", ""))
            if child_id <= 0:
                raise ValueError
        except (TypeError, ValueError):
            raise ValidationError("Identificador do item inválido.") from None
    # CAS complements row locks and makes stale submissions fail on every supported backend.
    if not CancellationProcess.objects.filter(pk=pk, revision=revision).update(revision=F("revision") + 1):
        raise Conflict("Processo alterado por outro usuário. Recarregue a página.")
    financial = False
    details = {}
    metadata = {"revision": revision + 1}
    description = ""
    if action in ("assign", "assume"):
        if action == "assume" and process.owner_id and process.owner_id != request.user.pk:
            raise Conflict("Outro analista já é responsável por este processo.")
        owner = request.user if action == "assume" else valid(forms.AssignmentForm(data))["owner"]
        if process.owner_id == (owner.pk if owner else None):
            CancellationProcess.objects.filter(pk=process.pk).update(revision=revision)
            return process
        metadata.update(previous_owner_id=process.owner_id, owner_id=owner.pk if owner else None)
        _assign(process, owner)
        description = f"Responsável: {owner.get_full_name() or owner.get_username() if owner else 'não atribuído'}."
    elif action == "update":
        valid(forms.ProcessForm(data, instance=process))
        next_action = timezone.localtime(process.next_action_at).strftime("%d/%m/%Y %H:%M") if process.next_action_at else "não definida"
        description = f"Informações atualizadas: cliente final {process.final_client or 'não informado'}; próxima ação {next_action}."
    elif action == "link":
        # Explicit reassociation uses only current Ticket–CI candidates and the same scope as triage.
        require(request.user, "triage_cancellations")
        values = valid(forms.TriageForm(data, user=request.user, intake=process.intake, ticket=process.source_ticket))
        link = values.get("link")
        if not link or not values["reason"].strip():
            raise ValidationError("Selecione um circuito atual e justifique a associação.")
        process.link, process.config_item_id = link, link.source_config_item_id
        process.contracting_client = link.contracting_client
        process.project = link.business_project or link.legacy_project
        process.designation, process.city, process.state = link_designation(link), link.city, link.state
        process.isp = link.operator.name if link.operator else ""
        description = f"Vínculo CMDB validado: CI {link.source_config_item_id}. {values['reason']}"
    elif action == "start":
        if process.stage not in (CancellationProcess.Stage.ORDER, CancellationProcess.Stage.PENDING):
            raise ValidationError("A tratativa já foi iniciada.")
        process.stage = CancellationProcess.Stage.REQUEST
        description = "Tratativa com o ISP iniciada."
    elif action == "treatment":
        if process.stage != CancellationProcess.Stage.REQUEST:
            raise ValidationError("Inicie a tratativa antes de registrar confirmação do ISP.")
        form = forms.TreatmentForm(data, instance=process.treatment)
        valid(form)
        form.save()
        treatment = form.instance
        requested = timezone.localtime(treatment.requested_at).strftime("%d/%m/%Y %H:%M") if treatment.requested_at else "não informado"
        effective = treatment.effective_date.strftime("%d/%m/%Y") if treatment.effective_date else "não informada"
        description = (f"Tratativa ISP: protocolo {treatment.protocol or 'não informado'}; contato {treatment.contact or 'não informado'}; "
                       f"solicitado em {requested}; serviço {'confirmado' if treatment.service_confirmed else 'não confirmado'}; "
                       f"data efetiva {effective}; faturamento {'encerrado' if treatment.billing_closed else 'não confirmado'}. "
                       f"Retorno: {treatment.response or 'não informado'}. Evidência: {treatment.evidence_reference or 'não informada'}.")
    elif action == "note":
        description = valid(forms.NoteForm(data))["note"]
    elif action == "pending_add":
        can_finance = request.user.has_perm("cancelamentos.view_cancellation_finance") and request.user.has_perm("cancelamentos.manage_cancellation_finance")
        form = forms.PendingForm(data, financial=can_finance)
        values = valid(form)
        pending = form.save(commit=False)
        _owner = pending.owner
        if _owner and not CancellationScope.processes(_owner).filter(pk=process.pk).exists():
            raise ValidationError("O responsável pela pendência não possui acesso ao processo.")
        pending.process = process
        pending.save()
        financial = pending.kind in forms.FINANCIAL_PENDING_KINDS or pending.amount is not None
        description = f"Pendência #{pending.pk} criada: {pending.get_kind_display()}. {pending.description}"
    elif action == "pending_resolve":
        pending = get_object_or_404(process.pendings.select_related("legacy_reference"), pk=child_id)
        financial = pending_is_financial(pending)
        if financial:
            require(request.user, "view_cancellation_finance")
            require(request.user, "manage_cancellation_finance")
        if pending.status != "OPEN":
            raise Conflict("Esta pendência já foi resolvida ou encaminhada.")
        values = valid(forms.ResolvePendingForm(data))
        pending.status = values["status"]
        pending.resolution = values["resolution"]
        pending.evidence_reference = values["evidence_reference"]
        pending.resolved_at = timezone.now()
        pending.save()
        description = f"Pendência #{pending.pk}: {pending.get_status_display()}. {pending.resolution}"
    elif action in ("equipment_add", "equipment_update"):
        equipment = get_object_or_404(process.equipment, pk=child_id) if action.endswith("update") else None
        form = forms.EquipmentForm(data, instance=equipment)
        valid(form)
        equipment = form.save(commit=False)
        equipment.process = process
        equipment.save()
        description = f"Equipamento #{equipment.pk}: {equipment.description} — {equipment.get_status_display()}. {equipment.notes}"
    elif action in ("obligation_add", "obligation_update"):
        obligation = get_object_or_404(process.obligations, pk=child_id) if action.endswith("update") else None
        previous_amounts = {"Valor previsto anterior": str(obligation.estimated_amount) if obligation.estimated_amount is not None else "Não informado",
                            "Valor cobrado anterior": str(obligation.charged_amount) if obligation.charged_amount is not None else "Não informado"} if obligation else {}
        form = forms.ObligationForm(data, instance=obligation)
        valid(form)
        obligation = form.save(commit=False)
        obligation.process = process
        obligation.provider = process.link.operator if process.link_id else None
        obligation.provider_snapshot = process.isp
        obligation.save()
        financial = True
        details = {**previous_amounts, "Valor previsto registrado": str(obligation.estimated_amount) if obligation.estimated_amount is not None else "Não informado",
                   "Valor cobrado registrado": str(obligation.charged_amount) if obligation.charged_amount is not None else "Não informado",
                   "Vencimento registrado": obligation.due_date.strftime("%d/%m/%Y") if obligation.due_date else "Não informado"}
        # No amount, document contents or banking reference is logged, even in the local event.
        description = f"Obrigação #{obligation.pk} registrada/atualizada. Pagamento não confirmado."
    elif action == "evidence":
        form = forms.EvidenceForm(data)
        values = valid(form)
        if values["financial"]:
            require(request.user, "view_cancellation_finance")
            require(request.user, "manage_cancellation_finance")
        evidence = form.save(commit=False)
        evidence.process, evidence.created_by = process, request.user
        evidence.save()
        financial = evidence.financial
        description = f"Referência de evidência #{evidence.pk} registrada."
    elif action == "handoff":
        if not process.treatment.service_confirmed:
            raise ValidationError("Confirme o cancelamento com o ISP antes de registrar o encaminhamento.")
        form = forms.HandoffForm(data)
        valid(form)
        handoff = form.save(commit=False)
        handoff.process, handoff.actor = process, request.user
        handoff.process_revision = revision
        handoff.summary = financial_summary(process)
        handoff.save()
        HandoffObligation.objects.bulk_create([
            HandoffObligation(handoff=handoff, obligation=o, charged_amount=o.charged_amount,
                              document_reference=o.document_reference, document_status=o.document_status,
                              obligation_updated_at=o.updated_at) for o in process.obligations.all()
        ])
        financial = True
        description = f"Encaminhamento manual #{handoff.pk} ao Financeiro registrado com evidência. Não confirma pagamento."
    elif action == "complete":
        if process.stage != CancellationProcess.Stage.REQUEST:
            raise ValidationError("Inicie e registre a tratativa antes de concluir.")
        values = valid(forms.CompletionForm(data))
        blockers = completion_blockers(process)
        if blockers:
            if values["exception_reason"]:
                require(request.user, "override_cancellation_completion")
            if not values["exception_reason"].strip() or not values["exception_evidence"].strip():
                raise ValidationError(blockers + ["Exceção exige permissão específica, justificativa e evidência."])
            process.completion_exception = f"{values['exception_reason']}\nEvidência: {values['exception_evidence']}"
        process.stage = CancellationProcess.Stage.COMPLETED
        process.completed_at, process.completed_by = timezone.now(), request.user
        description = "Conclusão operacional NOC registrada. Situação de pagamento permanece independente."
        if blockers:
            description += f" Exceção autorizada: {values['exception_reason']}"
            metadata["exception"] = True
    process.revision = revision + 1
    process.save()
    event(request=request, intake=process.intake, process=process, action=action, description=description,
          financial=financial, metadata=metadata, details=details)
    return process
