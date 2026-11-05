from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, models, transaction
from django.utils import timezone
import uuid

from aplicativos.auditoria.services import sanitize_metadata


QUALITY_DEMO_MARKER = "DEMO-QUALIDADE: quality-demo-v1"


def generate_quality_tq_correlation_id():
    return f"ARION-QID-{uuid.uuid4().hex.upper()}"


class QualityOrder(models.Model):
    class Status(models.TextChoices):
        NOVA = "NOVA", "Nova"
        TRIAGEM = "TRIAGEM", "Triagem"
        TRATAMENTO = "TRATAMENTO", "Tratamento"
        AGUARDANDO_TERCEIRO = "AGUARDANDO_TERCEIRO", "Aguardando terceiro"
        MONITORAMENTO = "MONITORAMENTO", "Monitoramento"
        CONCLUIDA = "CONCLUIDA", "Concluída"
        CANCELADA = "CANCELADA", "Cancelada"

    class RecurrenceLevel(models.TextChoices):
        R1 = "R1", "R1 · 1ª reincidência"
        R2 = "R2", "R2 · 2ª reincidência"
        R3 = "R3", "R3 · 3ª reincidência"
        R4 = "R4", "R4 · 4ª reincidência"
        R5_PLUS = "R5+", "R5+ · Crônico"

    class Priority(models.TextChoices):
        BAIXA = "BAIXA", "Baixa"
        MEDIA = "MEDIA", "Média"
        ALTA = "ALTA", "Alta"
        CRITICA = "CRITICA", "Crítica"

    code = models.CharField("código", max_length=24, unique=True, blank=True, null=True)
    status = models.CharField("status", max_length=24, choices=Status.choices, default=Status.NOVA, db_index=True)
    recurrence_level = models.CharField("nível de reincidência", max_length=4, choices=RecurrenceLevel.choices, blank=True, db_index=True)
    priority = models.CharField("prioridade", max_length=10, choices=Priority.choices, default=Priority.MEDIA, db_index=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="quality_orders")

    created_at = models.DateTimeField("criada em", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("atualizada em", auto_now=True)
    treatment_deadline = models.DateTimeField("prazo de tratamento", null=True, blank=True, db_index=True)
    completed_at = models.DateTimeField("concluída em", null=True, blank=True)
    archived_at = models.DateTimeField("arquivada em", null=True, blank=True, db_index=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="archived_quality_orders",
    )
    archive_reason = models.TextField("motivo do arquivamento", blank=True)

    link = models.ForeignKey(
        "integracoes.CMDBLinkMirror",
        to_field="source_config_item_id",
        db_column="link_config_item_id",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="quality_orders",
    )
    designation_snapshot = models.CharField("designação", max_length=300, blank=True)
    ci_snapshot = models.CharField("ConfigItem", max_length=40, blank=True, db_index=True)
    client_snapshot = models.CharField("cliente", max_length=300, blank=True, db_index=True)
    project_snapshot = models.CharField("projeto", max_length=300, blank=True, db_index=True)
    isp_snapshot = models.CharField("provedor / ISP", max_length=300, blank=True, db_index=True)
    uf_snapshot = models.CharField("UF", max_length=100, blank=True, db_index=True)

    current_reason = models.TextField("motivo atual", blank=True)
    current_root_cause = models.TextField("causa raiz atual", blank=True)
    internal_notes = models.TextField("observação interna", blank=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [
            models.Index(fields=("status", "treatment_deadline"), name="quality_order_due_idx"),
            models.Index(fields=("priority", "status"), name="quality_order_priority_idx"),
        ]
        permissions = (
            ("view_quality", "Can view Quality module"),
            ("manage_quality_orders", "Can manage Quality Orders"),
        )
        verbose_name = "Ordem de Qualidade"
        verbose_name_plural = "Ordens de Qualidade"

    def __str__(self):
        return self.code or f"Ordem de Qualidade #{self.pk or 'nova'}"

    @property
    def is_demo(self):
        return self.internal_notes.startswith(QUALITY_DEMO_MARKER)

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if self.link_id:
            link = self.link
            from aplicativos.integracoes.services.semantics import UNCLASSIFIED, LinkSemantics, link_designation

            semantics = LinkSemantics.from_link(link)
            snapshots = {
                "designation_snapshot": link_designation(link),
                "ci_snapshot": link.source_config_item_id,
                "client_snapshot": link.contracting_client,
                "project_snapshot": link.business_project or link.legacy_project,
                "isp_snapshot": "" if semantics.provider_name == UNCLASSIFIED else semantics.provider_name,
                "uf_snapshot": link.state,
            }
            for field_name, value in snapshots.items():
                if not getattr(self, field_name) and value:
                    setattr(self, field_name, value)
                    if update_fields is not None:
                        update_fields = set(update_fields)
                        update_fields.add(field_name)
        if update_fields is not None:
            kwargs["update_fields"] = update_fields

        super().save(*args, **kwargs)
        if not self.code:
            year = timezone.localtime(self.created_at).year
            self.code = f"OQ-{year}-{self.pk:06d}"
            type(self).objects.filter(pk=self.pk).update(code=self.code)


class QualityOccurrence(models.Model):
    quality_order = models.ForeignKey(QualityOrder, on_delete=models.CASCADE, related_name="occurrences")
    otrs_ticket = models.ForeignKey(
        "integracoes.OTRSTicket",
        to_field="external_id",
        db_column="otrs_external_id",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="quality_occurrences",
    )
    ticket_external_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    ticket_number = models.CharField(max_length=80, blank=True, db_index=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    is_initial_occurrence = models.BooleanField(default=False)
    recurrence_number = models.PositiveSmallIntegerField(null=True, blank=True)
    reason = models.TextField(blank=True)
    root_cause = models.TextField(blank=True)
    counts_as_recurrence = models.BooleanField(default=False, db_index=True)
    same_root_cause = models.BooleanField(default=False)
    excluded = models.BooleanField(default=False, db_index=True)
    exclusion_reason = models.CharField(max_length=32, blank=True)
    ticket_status_snapshot = models.CharField(max_length=120, blank=True)
    queue_snapshot = models.CharField(max_length=250, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("occurred_at", "pk")
        indexes = [
            models.Index(fields=("quality_order", "occurred_at"), name="quality_occurrence_date_idx"),
            models.Index(fields=("excluded", "counts_as_recurrence"), name="quality_occurrence_valid_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("quality_order", "ticket_external_id_snapshot"),
                name="quality_order_ticket_once",
            ),
        ]
        verbose_name = "ocorrência de qualidade"
        verbose_name_plural = "ocorrências de qualidade"

    def save(self, *args, **kwargs):
        if self.otrs_ticket_id and not self.ticket_external_id_snapshot:
            self.ticket_external_id_snapshot = self.otrs_ticket_id
        if self.otrs_ticket_id and not self.ticket_number:
            self.ticket_number = self.otrs_ticket.ticket_number
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.quality_order} · {self.ticket_number or 'ocorrência'}"


class QualityTicket(models.Model):
    class TicketType(models.TextChoices):
        TREATMENT = "TRATAMENTO", "Tratamento"
        MONITORING = "MONITORAMENTO", "Monitoramento"

    quality_order = models.ForeignKey(QualityOrder, on_delete=models.CASCADE, related_name="quality_tickets")
    otrs_ticket = models.ForeignKey(
        "integracoes.OTRSTicket",
        to_field="external_id",
        db_column="otrs_external_id",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="quality_tickets",
    )
    ticket_external_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    ticket_number = models.CharField(max_length=80, blank=True, db_index=True)
    ticket_type = models.CharField(max_length=16, choices=TicketType.choices)
    queue_snapshot = models.CharField(max_length=250, blank=True)
    state_snapshot = models.CharField(max_length=250, blank=True)
    owner_snapshot = models.CharField(max_length=250, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    result = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        verbose_name = "ticket de qualidade"
        verbose_name_plural = "tickets de qualidade"

    def save(self, *args, **kwargs):
        if self.otrs_ticket_id and not self.ticket_external_id_snapshot:
            self.ticket_external_id_snapshot = self.otrs_ticket_id
        if self.otrs_ticket_id and not self.ticket_number:
            self.ticket_number = self.otrs_ticket.ticket_number
        super().save(*args, **kwargs)

    def __str__(self):
        return self.ticket_number or f"Ticket de Qualidade #{self.pk or 'novo'}"


class QualityTreatment(models.Model):
    """A locally tracked TQ cycle; separate from mirrored OTRS tickets."""

    class Status(models.TextChoices):
        PENDING_SEND = "PENDING_SEND", "Pendente de envio"
        WAITING_OTRS = "WAITING_OTRS", "Aguardando ticket OTRS"
        IN_PROGRESS = "IN_PROGRESS", "Em tratativa"
        MONITORING = "MONITORING", "Em monitoramento"
        COMPLETED = "COMPLETED", "Concluída"
        RECURRENCE = "RECURRENCE", "Reincidiu"
        SEND_FAILED = "SEND_FAILED", "Falha no envio"
        CANCELED = "CANCELED", "Cancelada"

    class SendStatus(models.TextChoices):
        NOT_SENT = "NOT_SENT", "Ainda não enviado"
        IN_PROGRESS = "IN_PROGRESS", "Envio em andamento"
        SENT = "SENT", "Aceito pelo Brevo"
        FAILED = "FAILED", "Falha no envio"
        UNCERTAIN = "UNCERTAIN", "Resultado incerto"

    class ManagementSituation(models.TextChoices):
        WAITING_RESPONSE = "WAITING_RESPONSE", "Aguardando retorno"
        ANALYSIS = "ANALYSIS", "Em análise"
        ACTIONS_IN_PROGRESS = "ACTIONS_IN_PROGRESS", "Ações em execução"
        READY_MONITORING = "READY_MONITORING", "Pronta para monitoramento"

    code = models.CharField("código TQ", max_length=24, unique=True, null=True, blank=True)
    correlation_id = models.CharField(
        max_length=44, unique=True, default=generate_quality_tq_correlation_id, editable=False,
    )
    quality_order = models.ForeignKey(QualityOrder, on_delete=models.PROTECT, related_name="treatments")
    support_ticket = models.ForeignKey(
        "integracoes.OTRSTicket", to_field="external_id", db_column="support_ticket_external_id",
        null=True, blank=True, on_delete=models.SET_NULL, related_name="quality_treatments",
    )
    support_ticket_external_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    support_ticket_number = models.CharField(max_length=80, blank=True, db_index=True)
    support_ticket_state_snapshot = models.CharField(max_length=250, blank=True)
    support_ticket_opened_at = models.DateTimeField(null=True, blank=True)
    operational_state_snapshot = models.CharField(max_length=40, default="UNCONFIRMED")
    reason = models.TextField()
    description = models.TextField(blank=True)
    management_summary = models.TextField(blank=True)
    management_situation = models.CharField(
        max_length=24, choices=ManagementSituation.choices,
        default=ManagementSituation.WAITING_RESPONSE,
    )
    next_action = models.CharField(max_length=500, blank=True)
    next_action_due = models.DateField(null=True, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="quality_treatments")
    deadline = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING_SEND, db_index=True)
    active_order_key = models.PositiveBigIntegerField(null=True, blank=True, unique=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_quality_treatments")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    send_status = models.CharField(max_length=16, choices=SendStatus.choices, default=SendStatus.NOT_SENT)
    sent_at = models.DateTimeField(null=True, blank=True)
    send_started_at = models.DateTimeField(null=True, blank=True)
    send_attempt_count = models.PositiveSmallIntegerField(default=0)
    send_method = models.CharField(max_length=16, blank=True)
    send_attempt_id = models.UUIDField(null=True, blank=True, editable=False)
    send_error = models.CharField(max_length=240, blank=True)
    brevo_message_id = models.CharField(max_length=250, blank=True)
    quality_ticket_number = models.CharField(max_length=80, blank=True, db_index=True)
    quality_ticket_external_id = models.PositiveBigIntegerField(null=True, blank=True, unique=True)
    quality_ticket_queue_snapshot = models.CharField(max_length=250, blank=True)
    quality_ticket_state_snapshot = models.CharField(max_length=250, blank=True)
    quality_ticket_last_read_at = models.DateTimeField(null=True, blank=True)
    quality_ticket_linked_at = models.DateTimeField(null=True, blank=True)

    ACTIVE_STATUSES = (Status.PENDING_SEND, Status.WAITING_OTRS, Status.IN_PROGRESS, Status.MONITORING, Status.SEND_FAILED)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [models.Index(fields=("quality_order", "-created_at"), name="quality_treat_order_date_idx")]
        verbose_name = "tratativa de qualidade"
        verbose_name_plural = "tratativas de qualidade"

    def __str__(self):
        return self.code or f"Tratativa de Qualidade #{self.pk or 'nova'}"

    @property
    def is_active(self):
        return self.status in self.ACTIVE_STATUSES

    def save(self, *args, **kwargs):
        using = kwargs.get("using") or self._state.db or DEFAULT_DB_ALIAS
        kwargs["using"] = using
        if self.pk:
            previous_correlation_id = type(self).objects.using(using).filter(pk=self.pk).values_list(
                "correlation_id", flat=True).first()
            if previous_correlation_id and self.correlation_id != previous_correlation_id:
                self.correlation_id = previous_correlation_id
        if self.pk:
            previous = type(self).objects.using(using).filter(pk=self.pk).values("code").first()
            if previous and previous["code"] and self.code != previous["code"]:
                self.code = previous["code"]
            self.active_order_key = self.quality_order_id if self.status in self.ACTIVE_STATUSES else None
            super().save(*args, **kwargs)
            return

        # Keep identifiers in a durable ledger so deleting a local TQ can never
        # make its global sequence number or QID available to a later TQ.
        with transaction.atomic(using=using):
            sequence = QualityTreatmentSequence.objects.using(using).select_for_update().get(key="tq")
            sequence.last_value += 1
            sequence.save(update_fields=("last_value",), using=using)
            year = timezone.localtime(self.created_at).year
            self.code = f"TQ-{year}-{sequence.last_value:06d}"
            while QualityTreatmentIdentity.objects.using(using).filter(correlation_id=self.correlation_id).exists():
                self.correlation_id = generate_quality_tq_correlation_id()
            self.active_order_key = self.quality_order_id if self.status in self.ACTIVE_STATUSES else None
            super().save(*args, **kwargs)
            QualityTreatmentIdentity.objects.using(using).create(
                sequence_number=sequence.last_value,
                code=self.code,
                correlation_id=self.correlation_id,
            )


class QualityTreatmentSequence(models.Model):
    key = models.CharField(max_length=16, primary_key=True)
    last_value = models.PositiveBigIntegerField(default=0)

    class Meta:
        verbose_name = "sequência global de TQ"
        verbose_name_plural = "sequências globais de TQ"


class QualityTreatmentIdentity(models.Model):
    """Permanent identifier registry; deliberately has no FK to a deletable TQ."""

    sequence_number = models.PositiveBigIntegerField(unique=True)
    code = models.CharField(max_length=24, unique=True)
    correlation_id = models.CharField(max_length=44, unique=True)
    reserved_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("sequence_number",)
        verbose_name = "identificador reservado de TQ"
        verbose_name_plural = "identificadores reservados de TQ"


class QualityCorrelationCheckpoint(models.Model):
    """Incremental cursor for read-only Quality queue article correlation."""

    queue_name = models.CharField(max_length=250, unique=True)
    last_article_id = models.PositiveBigIntegerField(default=0)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=120, blank=True)
    failure_count = models.PositiveSmallIntegerField(default=0)
    records_processed = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "checkpoint de correlação de qualidade"
        verbose_name_plural = "checkpoints de correlação de qualidade"


class QualityMonitoringCycle(models.Model):
    class Result(models.TextChoices):
        IN_PROGRESS = "EM_ANDAMENTO", "Em andamento"
        SUCCESS = "SUCESSO", "Concluído com sucesso"
        RECURRENCE = "REINCIDIU", "Reincidiu"
        CANCELED = "CANCELADO", "Cancelado"

    quality_order = models.ForeignKey(QualityOrder, on_delete=models.CASCADE, related_name="monitoring_cycles")
    active_order_key = models.PositiveBigIntegerField(null=True, blank=True, unique=True, editable=False)
    quality_treatment = models.ForeignKey(QualityTreatment, null=True, blank=True, on_delete=models.PROTECT, related_name="monitoring_cycles")
    quality_ticket = models.ForeignKey(QualityTicket, null=True, blank=True, on_delete=models.SET_NULL, related_name="monitoring_cycles")
    cycle_number = models.PositiveSmallIntegerField()
    started_at = models.DateTimeField(default=timezone.now)
    expected_end_at = models.DateTimeField(db_index=True)
    otrs_actions_summary = models.TextField(blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    result = models.CharField(max_length=16, choices=Result.choices, default=Result.IN_PROGRESS, db_index=True)
    trigger_occurrence = models.ForeignKey(QualityOccurrence, null=True, blank=True, on_delete=models.SET_NULL, related_name="triggered_monitoring_cycles")
    financial_regularization_confirmed = models.BooleanField(default=False)
    financial_evidence = models.TextField(blank=True)
    final_analysis = models.TextField(blank=True)
    stability_validated = models.BooleanField(default=False)
    occurrence_review = models.TextField(blank=True)
    validated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="validated_quality_monitoring_cycles")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-cycle_number",)
        constraints = [
            models.UniqueConstraint(fields=("quality_order", "cycle_number"), name="quality_cycle_number_once"),
        ]
        indexes = [
            models.Index(fields=("result", "expected_end_at"), name="quality_cycle_due_idx"),
        ]
        verbose_name = "ciclo de monitoramento"
        verbose_name_plural = "ciclos de monitoramento"

    def __str__(self):
        return f"{self.quality_order} · RREIP {self.cycle_number}"


class QualityContact(models.Model):
    company = models.CharField(max_length=200, blank=True)
    region = models.CharField(max_length=160, blank=True)
    client = models.CharField(max_length=300, blank=True, db_index=True)
    project = models.CharField(max_length=300, blank=True, db_index=True)
    contact_type = models.CharField(max_length=120, blank=True)
    name = models.CharField(max_length=200)
    role = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=80, blank=True)
    teams = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    escalation_level = models.PositiveSmallIntegerField(default=1)
    active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("client", "project", "escalation_level", "name")
        permissions = (("manage_quality_contacts", "Can manage Quality Contacts"),)
        verbose_name = "contato de qualidade"
        verbose_name_plural = "contatos de qualidade"

    def __str__(self):
        return self.name

    @property
    def is_demo(self):
        return self.notes.startswith(QUALITY_DEMO_MARKER)


class QualityAction(models.Model):
    quality_order = models.ForeignKey(QualityOrder, on_delete=models.CASCADE, related_name="actions")
    title = models.CharField(max_length=240)
    completed = models.BooleanField(default=False, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="completed_quality_actions",
    )
    order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("order", "pk")
        indexes = [models.Index(fields=("quality_order", "completed", "order"), name="quality_action_order_idx")]
        verbose_name = "ação de qualidade"
        verbose_name_plural = "ações de qualidade"

    def __str__(self):
        return self.title


class QualityAuditEvent(models.Model):
    class EventType(models.TextChoices):
        OQ_CREATED = "OQ_CREATED", "Ordem criada"
        STATUS_CHANGED = "STATUS_CHANGED", "Status alterado"
        OWNER_CHANGED = "OWNER_CHANGED", "Responsável alterado"
        PRIORITY_CHANGED = "PRIORITY_CHANGED", "Prioridade alterada"
        OCCURRENCE_LINKED = "OCCURRENCE_LINKED", "Ocorrência vinculada"
        RECURRENCE_CONFIRMED = "RECURRENCE_CONFIRMED", "Reincidência confirmada"
        QUALITY_TICKET_LINKED = "QUALITY_TICKET_LINKED", "Ticket de Qualidade vinculado"
        TREATMENT_CREATED = "TREATMENT_CREATED", "TQ criada"
        TREATMENT_TICKET_LINKED = "TREATMENT_TICKET_LINKED", "Ticket OTRS vinculado à TQ"
        TREATMENT_COMPLETED = "TREATMENT_COMPLETED", "TQ concluída"
        TREATMENT_CANCELED = "TREATMENT_CANCELED", "TQ cancelada"
        MONITORING_STARTED = "MONITORING_STARTED", "Monitoramento iniciado"
        MONITORING_FAILED = "MONITORING_FAILED", "Monitoramento reincidiu"
        MONITORING_SUCCEEDED = "MONITORING_SUCCEEDED", "Monitoramento concluído com sucesso"
        MONITORING_CANCELED = "MONITORING_CANCELED", "Monitoramento cancelado"
        CHECKLIST_ITEM_ADDED = "CHECKLIST_ITEM_ADDED", "Item de checklist adicionado"
        CHECKLIST_ITEM_CHANGED = "CHECKLIST_ITEM_CHANGED", "Checklist atualizado"
        LEVEL_CHANGED = "LEVEL_CHANGED", "Nível alterado"
        DEADLINE_CHANGED = "DEADLINE_CHANGED", "Prazo alterado"
        NOTES_CHANGED = "NOTES_CHANGED", "Observação atualizada"
        ORDER_CLOSED = "ORDER_CLOSED", "Ordem encerrada"
        ORDER_ARCHIVED = "ORDER_ARCHIVED", "Ordem arquivada"

    quality_order = models.ForeignKey(QualityOrder, on_delete=models.CASCADE, related_name="audit_events")
    event_type = models.CharField(max_length=32, choices=EventType.choices, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="quality_audit_events")
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        verbose_name = "evento de qualidade"
        verbose_name_plural = "eventos de qualidade"

    def save(self, *args, **kwargs):
        self.metadata = sanitize_metadata(self.metadata or {})
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_event_type_display()} · {self.quality_order}"
