"""Local operational decisions. These models never write to OTRS or CMDB."""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


PERMISSIONS = (
    ("view_cancellations", "Visualizar Cancelamentos"),
    ("triage_cancellations", "Aprovar e rejeitar entradas OTRS"),
    ("assign_cancellations", "Assumir e atribuir responsáveis"),
    ("change_cancellation_stage", "Registrar tratativa e alterar etapas"),
    ("reopen_cancellations", "Reabrir cancelamentos concluídos com justificativa"),
    ("manage_cancellation_pending", "Registrar pendências e equipamentos"),
    ("complete_cancellations", "Concluir operacionalmente"),
    ("override_cancellation_completion", "Concluir com exceção justificada"),
    ("view_cancellation_finance", "Consultar informações financeiras"),
    ("manage_cancellation_finance", "Registrar obrigações e encaminhamentos"),
    ("export_cancellations", "Exportar Cancelamentos"),
    ("configure_cancellations", "Configurar entrada de Cancelamentos"),
    ("import_cancellation_history", "Importar histórico em execução controlada"),
)


class IntakeSettings(models.Model):
    """Singleton; the exact queue remains in integracoes.otrs_queues."""
    singleton = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    enabled = models.BooleanField("Projetar entrada OTRS", default=True)
    max_age_minutes = models.PositiveIntegerField("Validade da observação (minutos)", default=60,
                                                 validators=[MinValueValidator(2)])

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(singleton=1), name="cancel_settings_singleton")]


class CancellationIntake(models.Model):
    class Status(models.TextChoices):
        WAITING = "WAITING", "Aguardando triagem"
        APPROVED = "APPROVED", "Aprovado"
        REJECTED = "REJECTED", "Rejeitado"

    ticket_external_id = models.PositiveBigIntegerField(unique=True)
    ticket = models.ForeignKey("integracoes.OTRSTicket", null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="cancellation_entries")
    ticket_number = models.CharField(max_length=80, db_index=True)
    title = models.CharField(max_length=500, blank=True)
    queue_external_id = models.PositiveIntegerField()
    queue_name = models.CharField(max_length=250)
    source_created_at = models.DateTimeField(null=True, blank=True)
    source_changed_at = models.DateTimeField(null=True, blank=True)
    source_synced_at = models.DateTimeField(null=True, blank=True)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    eligible = models.BooleanField(default=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.WAITING, db_index=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="cancellation_intakes")
    revision = models.PositiveIntegerField(default=0)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="cancellation_decisions")
    decision_reason = models.TextField(blank=True)
    link = models.ForeignKey("integracoes.CMDBLinkMirror", null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="cancellation_intakes")
    scope_ready = models.BooleanField(default=False)
    client_snapshot = models.CharField(max_length=300, blank=True, db_index=True)
    project_snapshot = models.CharField(max_length=300, blank=True, db_index=True)
    designation_snapshot = models.CharField(max_length=300, blank=True, db_index=True)

    class Meta:
        ordering = ("-first_seen_at", "-pk")
        indexes = [models.Index(fields=("status", "eligible"), name="cancel_intake_status_idx")]

    def __str__(self):
        return self.ticket_number


class CancellationProcess(models.Model):
    class Stage(models.TextChoices):
        ORDER = "ORDER", "Pedido"
        REQUEST = "REQUEST", "Solicitação"
        PENDING = "PENDING", "Pendências"
        COMPLETED = "COMPLETED", "Concluído"

    class OperationalStatus(models.TextChoices):
        ANALYSIS = "ANALYSIS", "Em análise"
        WAITING = "WAITING", "Aguardando retorno"
        BLOCKED = "BLOCKED", "Pendência identificada"

    intake = models.OneToOneField(CancellationIntake, null=True, blank=True, on_delete=models.PROTECT, related_name="process")
    origin = models.CharField(max_length=20, default="ARION_OPS", choices=(("ARION_OPS", "ARION OPS"), ("ARIONBROKER", "Importado do ArionBroker")))
    source_ticket = models.ForeignKey("integracoes.OTRSTicket", null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="legacy_cancellation_processes")
    ticket_number = models.CharField(max_length=80, null=True, blank=True, unique=True)
    ticket_opened_on = models.DateField(null=True, blank=True)
    stage = models.CharField(max_length=12, choices=Stage.choices, default=Stage.ORDER, db_index=True)
    operational_status = models.CharField(max_length=12, choices=OperationalStatus.choices, blank=True, default="")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="cancellation_processes")
    link = models.ForeignKey("integracoes.CMDBLinkMirror", null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="cancellation_processes")
    config_item_id = models.CharField(max_length=40, blank=True)
    contracting_client = models.CharField("Cliente contratante", max_length=300, blank=True, db_index=True)
    final_client = models.CharField("Cliente final", max_length=300, blank=True)
    project = models.CharField("Projeto", max_length=300, blank=True, db_index=True)
    designation = models.CharField("Designação", max_length=300, blank=True, db_index=True)
    isp = models.CharField("ISP", max_length=300, blank=True, db_index=True)
    city = models.CharField("Cidade", max_length=200, blank=True)
    state = models.CharField("UF", max_length=100, blank=True)
    next_action_at = models.DateTimeField("Próxima ação", null=True, blank=True)
    revision = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="completed_cancellations")
    completion_exception = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        permissions = PERMISSIONS
        constraints = [models.CheckConstraint(condition=models.Q(intake__isnull=False) | models.Q(origin="ARIONBROKER"), name="cancel_process_admission_origin")]

    @property
    def code(self):
        return f"CANC-{self.pk:06d}"

    @property
    def display_ticket_number(self):
        return self.ticket_number or (self.intake.ticket_number if self.intake_id else "")

    @property
    def external_ticket_id(self):
        return self.source_ticket.external_id if self.source_ticket_id else self.intake.ticket_external_id if self.intake_id else None

    @property
    def observed_queue_name(self):
        return self.source_ticket.queue_name if self.source_ticket_id else self.intake.queue_name if self.intake_id else "Não localizado"

    def __str__(self):
        return self.code


class ISPTreatment(models.Model):
    process = models.OneToOneField(CancellationProcess, on_delete=models.CASCADE, related_name="treatment")
    protocol = models.CharField("Protocolo ISP", max_length=200, blank=True)
    contact = models.CharField("Contato ISP", max_length=300, blank=True)
    requested_at = models.DateTimeField("Solicitado em", null=True, blank=True)
    response = models.TextField("Retorno do provedor", blank=True)
    service_confirmed = models.BooleanField("Cancelamento confirmado pelo ISP", default=False)
    confirmed_at = models.DateTimeField("Confirmação recebida em", null=True, blank=True)
    effective_date = models.DateField("Data efetiva do cancelamento", null=True, blank=True)
    billing_closed = models.BooleanField("Encerramento do faturamento confirmado", default=False)
    billing_closed_date = models.DateField("Fim do faturamento", null=True, blank=True)
    evidence_reference = models.CharField("Evidência / referência do distrato", max_length=1000, blank=True)


class CancellationPending(models.Model):
    class Kind(models.TextChoices):
        OVERDUE = "OVERDUE", "Mensalidades atrasadas"
        EQUIPMENT_FINE = "EQUIPMENT_FINE", "Multa equipamento"
        CONTRACT_FINE = "CONTRACT_FINE", "Multa contrato"
        AGREEMENT = "AGREEMENT", "Aguardando distrato"
        RELEASE = "RELEASE", "Aguardando termo para baixa"
        COLLECTION = "COLLECTION", "Retirada de equipamento"
        BILL = "BILL", "Boleto pendente"
        DOCUMENT = "DOCUMENT", "Documentação pendente"
        OTHER = "OTHER", "Outros"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Aberta"
        RESOLVED = "RESOLVED", "Resolvida"
        REFERRED = "REFERRED", "Encaminhada à área responsável"
        DISCARDED = "DISCARDED", "Descartada"

    process = models.ForeignKey(CancellationProcess, on_delete=models.CASCADE, related_name="pendings")
    kind = models.CharField("Tipo", max_length=20, choices=Kind.choices)
    description = models.TextField("Descrição")
    amount = models.DecimalField("Valor (R$), se aplicável", max_digits=14, decimal_places=2,
                                null=True, blank=True, validators=[MinValueValidator(Decimal("0"))])
    expected_date = models.DateField("Data prevista", null=True, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="cancellation_pendings")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution = models.TextField(blank=True)
    evidence_reference = models.CharField("Evidência / referência", max_length=1000, blank=True)

    class Meta:
        ordering = ("status", "expected_date", "pk")
        constraints = [models.CheckConstraint(condition=models.Q(amount__isnull=True) | models.Q(amount__gte=0), name="cancel_pending_nonnegative")]


class CancellationEquipment(models.Model):
    class Status(models.TextChoices):
        TO_COLLECT = "TO_COLLECT", "A retirar"
        REQUESTED = "REQUESTED", "Retirada solicitada"
        SCHEDULED = "SCHEDULED", "Agendada"
        RETURNED = "RETURNED", "Retirado / devolvido"
        WAIVED = "WAIVED", "Dispensada pelo ISP"
        IMPOSSIBLE = "IMPOSSIBLE", "Impossibilidade de retirada"
        NOT_RETURNED = "NOT_RETURNED", "Não devolvido"

    process = models.ForeignKey(CancellationProcess, on_delete=models.CASCADE, related_name="equipment")
    description = models.CharField("Equipamento / identificação", max_length=300)
    status = models.CharField("Situação", max_length=16, choices=Status.choices, default=Status.TO_COLLECT)
    scheduled_at = models.DateTimeField("Agendamento", null=True, blank=True)
    returned_at = models.DateTimeField("Retirado / devolvido em", null=True, blank=True)
    evidence_reference = models.CharField("Evidência / referência", max_length=1000, blank=True)
    notes = models.TextField("Observações", blank=True)


class FinancialObligation(models.Model):
    class Kind(models.TextChoices):
        PRORATED = "PRORATED", "Mensalidade proporcional"
        OVERDUE = "OVERDUE", "Mensalidade atrasada"
        CONTRACT = "CONTRACT", "Multa contratual"
        EQUIPMENT = "EQUIPMENT", "Indenização de equipamento"
        OTHER = "OTHER", "Outras cobranças"

    class DocumentStatus(models.TextChoices):
        WAITING = "WAITING", "Aguardando documento / boleto"
        AVAILABLE = "AVAILABLE", "Documento disponível"

    class FinancialStatus(models.TextChoices):
        UNKNOWN = "UNKNOWN", "Não confirmado"
        PENDING = "PENDING", "Pendente de confirmação financeira"
        SCHEDULED = "SCHEDULED", "Agendado pelo Financeiro"
        PAID = "PAID", "Pagamento confirmado pelo Financeiro"

    process = models.ForeignKey(CancellationProcess, on_delete=models.CASCADE, related_name="obligations")
    kind = models.CharField("Tipo", max_length=16, choices=Kind.choices)
    description = models.CharField("Descrição", max_length=500)
    estimated_amount = models.DecimalField("Valor previsto (R$)", max_digits=14, decimal_places=2,
                                          null=True, blank=True, validators=[MinValueValidator(Decimal("0"))])
    charged_amount = models.DecimalField("Valor cobrado (R$)", max_digits=14, decimal_places=2,
                                        null=True, blank=True, validators=[MinValueValidator(Decimal("0"))])
    due_date = models.DateField("Vencimento", null=True, blank=True)
    provider = models.ForeignKey("integracoes.CMDBOperatorMirror", null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="cancellation_obligations")
    provider_snapshot = models.CharField(max_length=300, blank=True)
    reference_number = models.CharField("Número de referência", max_length=200, blank=True)
    document_status = models.CharField("Situação documental", max_length=12, choices=DocumentStatus.choices, default=DocumentStatus.WAITING)
    document_reference = models.CharField("Documento / referência privada", max_length=1000, blank=True)
    financial_status = models.CharField(max_length=12, choices=FinancialStatus.choices, default=FinancialStatus.UNKNOWN)
    legacy_field = models.CharField(max_length=40, null=True, blank=True)
    reconciliation_state = models.CharField(max_length=20, default="NOT_REQUIRED", choices=(
        ("NOT_REQUIRED", "Sem reconciliação legada"), ("UNRECONCILED", "Valor legado a reconciliar"),
        ("POSSIBLE_DUPLICATE", "Possível correspondência com pendência legada")))
    paid_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="confirmed_cancellation_payments")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("process", "legacy_field"), name="cancel_legacy_amount_unique"),
            models.CheckConstraint(condition=models.Q(estimated_amount__isnull=True) | models.Q(estimated_amount__gte=0), name="cancel_estimated_nonnegative"),
            models.CheckConstraint(condition=models.Q(charged_amount__isnull=True) | models.Q(charged_amount__gte=0), name="cancel_charged_nonnegative"),
        ]


class CancellationEvidence(models.Model):
    process = models.ForeignKey(CancellationProcess, on_delete=models.CASCADE, related_name="evidence")
    label = models.CharField("Descrição da evidência", max_length=200)
    reference = models.CharField("Referência privada / localização", max_length=1000)
    financial = models.BooleanField("Documento financeiro", default=False)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)


class FinancialHandoff(models.Model):
    process = models.ForeignKey(CancellationProcess, on_delete=models.PROTECT, related_name="handoffs")
    sent_at = models.DateTimeField("Encaminhado em", default=timezone.now)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    recipient = models.CharField("Destinatário / área", max_length=300)
    reference = models.CharField("Referência do envio", max_length=500)
    evidence_reference = models.CharField("Evidência do encaminhamento", max_length=1000)
    summary = models.TextField()
    process_revision = models.PositiveIntegerField()
    obligations = models.ManyToManyField(FinancialObligation, through="HandoffObligation", related_name="handoffs")

    class Meta:
        ordering = ("-sent_at", "-pk")


class HandoffObligation(models.Model):
    handoff = models.ForeignKey(FinancialHandoff, on_delete=models.CASCADE)
    obligation = models.ForeignKey(FinancialObligation, on_delete=models.PROTECT)
    charged_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    document_reference = models.CharField(max_length=1000)
    document_status = models.CharField(max_length=12)
    obligation_updated_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=("handoff", "obligation"), name="cancel_handoff_obligation_unique")]


class CancellationEvent(models.Model):
    intake = models.ForeignKey(CancellationIntake, null=True, blank=True, on_delete=models.PROTECT, related_name="events")
    process = models.ForeignKey(CancellationProcess, null=True, blank=True, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    action = models.CharField(max_length=64)
    description = models.TextField()
    details = models.JSONField(default=dict, blank=True)
    financial = models.BooleanField(default=False)
    audit_event = models.OneToOneField("auditoria.AuditEvent", null=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ("-created_at", "-pk")

    @property
    def origin_label(self):
        return {"legacy_historicocancelamento": "Histórico ArionBroker",
                "legacy_historicofila": "Movimentação ArionBroker",
                "legacy_observacaocancelamento": "Observação ArionBroker"}.get(self.action, "Ação ARION OPS")


class LegacyImportRun(models.Model):
    batch = models.CharField(max_length=80, unique=True)
    snapshot_checksum = models.CharField(max_length=64)
    schema_checksum = models.CharField(max_length=64)
    mapping_version = models.CharField(max_length=40)
    status = models.CharField(max_length=16, default="RUNNING", choices=(("RUNNING", "Em execução"), ("SUCCESS", "Concluída"), ("FAILED", "Falhou")))
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True)
    counts = models.JSONField(default=dict)


class LegacyPerson(models.Model):
    """Historical labels only: never an account, grant or Django user mapping."""
    source = models.CharField(max_length=80)
    external_id = models.PositiveBigIntegerField()
    username = models.CharField(max_length=150, blank=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("source", "external_id"), name="cancel_legacy_person_identity")]

    @property
    def display_name(self):
        return " ".join(filter(None, (self.first_name.strip(), self.last_name.strip()))) or self.username or "Nome não disponível"


class LegacyCancellationReference(models.Model):
    """Immutable source facts and identity; exposed only through scoped services."""
    source = models.CharField(max_length=80)
    entity = models.CharField(max_length=80)
    legacy_pk = models.CharField(max_length=160)
    process = models.ForeignKey(CancellationProcess, on_delete=models.PROTECT, related_name="legacy_references")
    batch = models.CharField(max_length=80)
    mapping_version = models.CharField(max_length=40)
    checksum = models.CharField(max_length=64)
    imported_at = models.DateTimeField(default=timezone.now)
    source_data = models.JSONField(default=dict)
    reconciliation = models.JSONField(default=dict)
    import_run = models.ForeignKey(LegacyImportRun, null=True, blank=True, on_delete=models.PROTECT, related_name="references")
    event = models.OneToOneField(CancellationEvent, null=True, blank=True, on_delete=models.PROTECT, related_name="legacy_reference")
    pending = models.OneToOneField(CancellationPending, null=True, blank=True, on_delete=models.PROTECT, related_name="legacy_reference")

    class Meta:
        constraints = [models.UniqueConstraint(fields=("source", "entity", "legacy_pk"), name="cancel_legacy_identity_unique")]
