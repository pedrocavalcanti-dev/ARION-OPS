from django.conf import settings
from django.db import models


class Integration(models.Model):
    class Key(models.TextChoices):
        CMDB = "cmdb", "CMDB ServiceUp"
        OTRS = "otrs", "OTRS MySQL"

    class Status(models.TextChoices):
        NOT_CONFIGURED = "not_configured", "Não configurado"
        CONNECTED = "connected", "Conectado"
        UNAVAILABLE = "unavailable", "Indisponível"
        ATTENTION = "attention", "Atenção"

    key = models.CharField(max_length=20, choices=Key.choices, unique=True)
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=80)
    enabled = models.BooleanField(default=False)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.NOT_CONFIGURED)
    # This JSON is written only by typed, validated forms. Passwords are never stored here.
    config = models.JSONField(default=dict, blank=True)
    accessible_databases = models.JSONField(default=list, blank=True)
    username = models.CharField(max_length=255, blank=True)
    secret_ciphertext = models.TextField(blank=True, editable=False)
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_read_at = models.DateTimeField(null=True, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_latency_ms = models.PositiveIntegerField(null=True, blank=True)
    last_error = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("key",)
        verbose_name = "integração"
        verbose_name_plural = "integrações"

    def __str__(self):
        return self.get_key_display()

    @property
    def credentials_configured(self):
        return bool(self.username and self.secret_ciphertext)

    @property
    def connection_status_display(self):
        if self.status == self.Status.CONNECTED:
            return "Disponível"
        return self.get_status_display()


class IntegrationRun(models.Model):
    class Operation(models.TextChoices):
        TEST = "test", "Teste de conexão"
        INSPECTION = "inspection", "Inspeção"
        READ = "read", "Leitura"
        SYNC = "sync", "Sincronização"
        COMPARE = "compare", "Comparação"
        CMDB_SYNC = "CMDB_SYNC", "Sincronização CMDB"
        OTRS_SYNC = "OTRS_SYNC", "Sincronização OTRS"
        OTRS_FAST_SYNC = "OTRS_FAST_SYNC", "Sincronização rápida OTRS"
        OTRS_FULL_SYNC = "OTRS_FULL_SYNC", "Reconciliação completa OTRS"
        OTRS_RELATION_SYNC = "OTRS_RELATION_SYNC", "Relações OTRS"
        RECONCILE = "RECONCILE", "Reconciliação"

    class Status(models.TextChoices):
        PENDING = "pending", "Pendente"
        RUNNING = "running", "Em execução"
        SUCCESS = "success", "Sucesso"
        PARTIAL = "partial", "Parcial"
        FAILED = "failed", "Falha"

    integration = models.ForeignKey(Integration, on_delete=models.CASCADE, related_name="runs")
    operation = models.CharField(max_length=20, choices=Operation.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    records_found = models.PositiveIntegerField(null=True, blank=True)
    records_processed = models.PositiveIntegerField(default=0)
    records_created = models.PositiveIntegerField(default=0)
    records_updated = models.PositiveIntegerField(default=0)
    records_unchanged = models.PositiveIntegerField(default=0)
    records_stale = models.PositiveIntegerField(default=0)
    records_failed = models.PositiveIntegerField(default=0)
    progress_total = models.PositiveIntegerField(null=True, blank=True)
    message = models.CharField(max_length=240, blank=True)
    request_id = models.CharField(max_length=80, blank=True, db_index=True)
    triggered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="integration_runs")

    class Meta:
        ordering = ("-started_at", "-pk")
        verbose_name = "execução de integração"
        verbose_name_plural = "execuções de integração"

    def __str__(self):
        return f"{self.integration} · {self.get_operation_display()} · {self.get_status_display()}"


class IntegrationSyncState(models.Model):
    key = models.CharField(max_length=32, unique=True)
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=240, blank=True)
    failure_count = models.PositiveSmallIntegerField(default=0)
    last_duration_ms = models.PositiveIntegerField(null=True, blank=True)
    records_count = models.PositiveIntegerField(null=True, blank=True)
    change_cursor = models.DateTimeField(null=True, blank=True)
    last_full_success_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("key",)


class IntegrationLease(models.Model):
    key = models.CharField(max_length=32, unique=True)
    owner_token = models.CharField(max_length=64, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ("key",)


class ExternalAPIIntegration(models.Model):
    """Credentials and health state for fixed, application-managed APIs."""

    class Provider(models.TextChoices):
        BREVO = "brevo", "Brevo"
        MAPBOX = "mapbox", "Mapbox"

    class Health(models.TextChoices):
        NOT_TESTED = "not_tested", "Ainda não testada"
        AVAILABLE = "available", "Disponível"
        ERROR = "error", "Erro"

    provider = models.CharField(max_length=20, choices=Provider.choices, unique=True)
    name = models.CharField(max_length=100)
    enabled = models.BooleanField(default=False)
    # Only non-secret, validated settings belong here. Private API keys are encrypted below.
    config = models.JSONField(default=dict, blank=True)
    secret_ciphertext = models.TextField(blank=True, editable=False)
    smtp_secret_ciphertext = models.TextField(blank=True, editable=False)
    allow_real_sends = models.BooleanField(default=False)
    health = models.CharField(max_length=20, choices=Health.choices, default=Health.NOT_TESTED)
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_latency_ms = models.PositiveIntegerField(null=True, blank=True)
    last_error = models.CharField(max_length=240, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("provider",)
        verbose_name = "integração de API externa"
        verbose_name_plural = "integrações de APIs externas"

    @property
    def credential_configured(self):
        if self.provider == self.Provider.BREVO:
            return bool(self.secret_ciphertext or self.smtp_secret_ciphertext)
        return bool((self.config or {}).get("public_token"))

    @property
    def status_display(self):
        if not self.credential_configured:
            return "Não configurada"
        if self.health == self.Health.ERROR:
            return "Erro"
        if self.health == self.Health.AVAILABLE:
            return "Disponível"
        return "Configurada"

    def __str__(self):
        return self.name


class ExternalAPIHealthCheck(models.Model):
    class Status(models.TextChoices):
        SUCCESS = "success", "Sucesso"
        ERROR = "error", "Erro"

    integration = models.ForeignKey(ExternalAPIIntegration, on_delete=models.CASCADE, related_name="checks")
    method = models.CharField(max_length=16, blank=True, choices=(("smtp", "SMTP Relay"), ("api_rest", "API REST"), ("mapbox", "Mapbox")))
    status = models.CharField(max_length=12, choices=Status.choices)
    message = models.CharField(max_length=240)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    checked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        verbose_name = "teste de saúde de API externa"
        verbose_name_plural = "testes de saúde de APIs externas"


class CMDBOperatorMirror(models.Model):
    source_config_item_id = models.CharField(max_length=40, unique=True)
    source_number = models.CharField(max_length=40, unique=True, null=True, blank=True)
    name = models.CharField(max_length=300)
    category = models.CharField(max_length=160, blank=True)
    cnpj = models.CharField(max_length=80, blank=True)
    business_hours = models.CharField(max_length=300, blank=True)
    phone = models.CharField(max_length=300, blank=True)
    whatsapp = models.CharField(max_length=300, blank=True)
    opening_email = models.CharField(max_length=300, blank=True)
    followup_email = models.CharField(max_length=300, blank=True)
    portal_url = models.CharField(max_length=500, blank=True)
    escalation_1 = models.CharField(max_length=500, blank=True)
    escalation_2 = models.CharField(max_length=500, blank=True)
    escalation_3 = models.CharField(max_length=500, blank=True)
    escalation_4 = models.CharField(max_length=500, blank=True)
    escalation_5 = models.CharField(max_length=500, blank=True)
    deployment_state = models.CharField(max_length=160, blank=True)
    deployment_state_type = models.CharField(max_length=100, blank=True)
    incident_state = models.CharField(max_length=160, blank=True)
    incident_state_type = models.CharField(max_length=100, blank=True)
    definition_id = models.CharField(max_length=40, blank=True)
    version_id = models.CharField(max_length=40, blank=True)
    last_version_id = models.CharField(max_length=40, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    source_last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)
    source_synced_at = models.DateTimeField(null=True, blank=True)
    is_present_in_source = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("name", "source_config_item_id")
        indexes = [models.Index(fields=("name", "is_present_in_source"), name="opmirror_name_present_idx")]
        verbose_name = "espelho de operadora CMDB"
        verbose_name_plural = "espelhos de operadoras CMDB"

    def __str__(self):
        return self.name or self.source_config_item_id


class CMDBLinkMirror(models.Model):
    class OperatorResolution(models.TextChoices):
        CONFIG_ITEM_ID = "CONFIG_ITEM_ID", "ConfigItemID"
        NAME = "NAME", "Nome exato"
        UNRESOLVED = "UNRESOLVED", "Não resolvido"
        EMPTY = "EMPTY", "Vazio"

    class ProjectSource(models.TextChoices):
        LEGACY_XLSX = "LEGACY_XLSX", "Base XLSX legada"

    class LegacyOperator(models.TextChoices):
        VIVO = "VIVO", "VIVO"
        CLARO = "CLARO", "CLARO"
        ALGAR = "ALGAR", "ALGAR"

    source_config_item_id = models.CharField(max_length=40, unique=True)
    source_number = models.CharField(max_length=40, unique=True, null=True, blank=True)
    name = models.CharField(max_length=500, blank=True)
    contracting_client_raw = models.CharField(max_length=300, blank=True, db_index=True)
    contracting_client = models.CharField(max_length=300, blank=True, db_index=True)
    business_project = models.CharField(max_length=300, blank=True, db_index=True)
    business_account = models.CharField(max_length=300, blank=True)
    business_id = models.CharField(max_length=80, blank=True)
    business_identity_confidence = models.CharField(max_length=16, blank=True, default="UNCLASSIFIED")
    business_identity_source = models.CharField(max_length=32, blank=True, default="CMDB_NAME_ORIGINAL")
    city = models.CharField(max_length=200, blank=True)
    state = models.CharField(max_length=100, blank=True)
    address = models.CharField(max_length=500, blank=True)
    circuit = models.CharField(max_length=300, blank=True)
    designation = models.CharField(max_length=300, blank=True)
    operator_designation = models.CharField(max_length=300, blank=True)
    identification = models.CharField(max_length=300, blank=True)
    link_type = models.CharField(max_length=160, blank=True)
    speed_raw = models.CharField(max_length=160, blank=True)
    ip_range = models.CharField(max_length=300, blank=True)
    equipment = models.CharField(max_length=300, blank=True)
    mac_address = models.CharField(max_length=160, blank=True)
    cnpj = models.CharField(max_length=100, blank=True)
    operator_cnpj = models.CharField(max_length=100, blank=True)
    contract_number = models.CharField(max_length=200, blank=True)
    business_hours = models.CharField(max_length=300, blank=True)
    isp_notes = models.TextField(blank=True)
    customer_id = models.CharField(max_length=200, blank=True)
    isp_raw = models.CharField(max_length=300, blank=True)
    operator = models.ForeignKey(CMDBOperatorMirror, null=True, blank=True, on_delete=models.SET_NULL, related_name="links")
    operator_resolution_method = models.CharField(max_length=20, choices=OperatorResolution.choices, default=OperatorResolution.EMPTY, db_index=True)
    legacy_project = models.CharField(max_length=300, blank=True)
    project_source = models.CharField(max_length=20, choices=ProjectSource.choices, blank=True)
    legacy_operator_classification = models.CharField(max_length=16, choices=LegacyOperator.choices, blank=True)
    deployment_state = models.CharField(max_length=160, blank=True, db_index=True)
    deployment_state_type = models.CharField(max_length=100, blank=True)
    incident_state = models.CharField(max_length=160, blank=True, db_index=True)
    incident_state_type = models.CharField(max_length=100, blank=True)
    definition_id = models.CharField(max_length=40, blank=True)
    version_id = models.CharField(max_length=40, blank=True)
    last_version_id = models.CharField(max_length=40, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    source_last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)
    source_synced_at = models.DateTimeField(null=True, blank=True)
    is_present_in_source = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("source_number", "source_config_item_id")
        indexes = [
            models.Index(fields=("operator", "is_present_in_source"), name="linkmirror_operator_idx"),
            models.Index(fields=("state", "city"), name="linkmirror_state_city_idx"),
            models.Index(fields=("legacy_project", "is_present_in_source"), name="linkmirror_project_idx"),
        ]
        verbose_name = "espelho de link CMDB"
        verbose_name_plural = "espelhos de links CMDB"

    def __str__(self):
        return self.name or self.source_number or self.source_config_item_id


class OTRSStateMapping(models.Model):
    class BusinessStatus(models.TextChoices):
        OPEN = "OPEN", "Aberto"
        PENDING = "PENDING", "Pendente"
        SCHEDULED = "SCHEDULED", "Agendado"
        RESOLVED = "RESOLVED", "Resolvido"
        CLOSED = "CLOSED", "Fechado"
        CANCELED = "CANCELED", "Cancelado"
        UNKNOWN = "UNKNOWN", "Desconhecido"

    external_state_id = models.PositiveIntegerField(unique=True)
    external_name = models.CharField(max_length=250)
    external_type = models.CharField(max_length=120, blank=True)
    business_status = models.CharField(max_length=16, choices=BusinessStatus.choices, default=BusinessStatus.UNKNOWN, db_index=True)
    is_configured = models.BooleanField(default=False, db_index=True)
    source_last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("external_name", "external_state_id")
        verbose_name = "mapeamento de estado OTRS"
        verbose_name_plural = "mapeamentos de estados OTRS"

    def __str__(self):
        return f"{self.external_name} → {self.business_status}"


class OTRSBacklogHistory(models.Model):
    """Coverage of the last atomic, read-only NOC history reconstruction."""

    queue_name = models.CharField(max_length=250, unique=True)
    earliest_at = models.DateTimeField()
    through = models.DateTimeField()
    ticket_count = models.PositiveIntegerField(default=0)
    event_count = models.PositiveIntegerField(default=0)
    diagnostics = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)


class OTRSBacklogInterval(models.Model):
    """Proven active membership or an explicitly unknown lifecycle interval."""

    coverage = models.ForeignKey(OTRSBacklogHistory, on_delete=models.CASCADE, related_name="intervals")
    ticket_external_id = models.PositiveBigIntegerField(db_index=True)
    source_event_id = models.PositiveBigIntegerField(default=0)
    source_created_at = models.DateTimeField()
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField()
    known = models.BooleanField(default=True)
    business_status = models.CharField(max_length=16, blank=True)
    external_state_name = models.CharField(max_length=250, blank=True)
    responsibility = models.CharField(max_length=20, blank=True)

    class Meta:
        indexes = [models.Index(fields=("coverage", "started_at", "ended_at"), name="otrs_backlog_interval_idx")]
        constraints = [models.UniqueConstraint(
            fields=("coverage", "ticket_external_id", "source_event_id", "started_at"),
            name="uniq_otrs_backlog_interval",
        )]


class OTRSTicket(models.Model):
    external_id = models.PositiveBigIntegerField(unique=True)
    ticket_number = models.CharField(max_length=80, unique=True)
    title = models.CharField(max_length=500, blank=True)
    queue_external_id = models.PositiveIntegerField(db_index=True)
    queue_name = models.CharField(max_length=250)
    external_state_id = models.PositiveIntegerField(db_index=True)
    external_state_name = models.CharField(max_length=250)
    external_state_type = models.CharField(max_length=120, blank=True)
    business_status = models.CharField(max_length=16, choices=OTRSStateMapping.BusinessStatus.choices, default=OTRSStateMapping.BusinessStatus.UNKNOWN, db_index=True)
    is_business_open = models.BooleanField(default=False, db_index=True)
    external_priority_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    priority_name = models.CharField(max_length=160, blank=True)
    external_type_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    type_name = models.CharField(max_length=160, blank=True)
    external_service_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    service_name = models.CharField(max_length=250, blank=True)
    external_sla_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    sla_name = models.CharField(max_length=250, blank=True)
    responsible_user_id = models.PositiveIntegerField(null=True, blank=True)
    customer_id = models.CharField(max_length=250, blank=True)
    timeout = models.PositiveBigIntegerField(null=True, blank=True)
    until_time = models.BigIntegerField(null=True, blank=True)
    escalation_time = models.BigIntegerField(null=True, blank=True)
    escalation_update_time = models.BigIntegerField(null=True, blank=True)
    escalation_response_time = models.BigIntegerField(null=True, blank=True)
    escalation_solution_time = models.BigIntegerField(null=True, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True, db_index=True)
    source_changed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    closing_code_raw = models.CharField(max_length=250, blank=True, db_index=True)
    responsibility_raw = models.CharField(max_length=120, blank=True, db_index=True)
    responsibility_normalized = models.CharField(max_length=20, default="INDETERMINADO", db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True, db_index=True)
    terminal_state_at = models.DateTimeField(null=True, blank=True, db_index=True)
    canceled_at = models.DateTimeField(null=True, blank=True, db_index=True)
    source_last_seen_at = models.DateTimeField(null=True, blank=True, db_index=True)
    source_synced_at = models.DateTimeField(null=True, blank=True)
    is_present_in_queue = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("-source_created_at", "-external_id")
        indexes = [
            models.Index(fields=("queue_external_id", "is_present_in_queue"), name="otrsticket_queue_present_idx"),
            models.Index(fields=("business_status", "source_created_at"), name="otrsticket_status_date_idx"),
        ]
        verbose_name = "ticket espelhado OTRS"
        verbose_name_plural = "tickets espelhados OTRS"

    def __str__(self):
        return self.ticket_number

    @property
    def operational_status_label(self):
        from aplicativos.integracoes.services.otrs_status import business_status_label

        return business_status_label(self.business_status)


class OTRSTicketConfigItem(models.Model):
    ticket = models.ForeignKey(OTRSTicket, on_delete=models.CASCADE, related_name="config_items")
    config_item_id = models.CharField(max_length=40, db_index=True)
    cmdb_link = models.ForeignKey(CMDBLinkMirror, null=True, blank=True, on_delete=models.SET_NULL, related_name="ticket_relations")
    relation_type = models.CharField(max_length=100, db_index=True)
    relation_state = models.CharField(max_length=100, db_index=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    source_last_seen_at = models.DateTimeField(null=True, blank=True)
    source_synced_at = models.DateTimeField(null=True, blank=True)
    is_present_in_source = models.BooleanField(default=True, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("ticket", "config_item_id", "relation_type"), name="uniq_ticket_ci_relation_type")]
        indexes = [
            models.Index(fields=("ticket", "is_present_in_source"), name="ticketci_ticket_seen_idx"),
            models.Index(fields=("cmdb_link", "is_present_in_source"), name="ticketci_link_seen_idx"),
        ]
        verbose_name = "relação OTRS Ticket–ConfigItem"
        verbose_name_plural = "relações OTRS Ticket–ConfigItem"


class OTRSOperationalSnapshot(models.Model):
    """One immutable daily observation from a successful full queue sync."""

    scope_signature = models.CharField(max_length=512)
    snapshot_date = models.DateField(db_index=True)
    captured_at = models.DateTimeField(db_index=True)
    source_run = models.OneToOneField(
        IntegrationRun, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="operational_snapshot",
    )
    ticket_count = models.PositiveIntegerField(default=0)
    backlog_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=("scope_signature", "snapshot_date"),
            name="uniq_otrs_oper_snapshot_day",
        )]
        indexes = [models.Index(fields=("scope_signature", "captured_at"), name="otrs_ops_snap_scope_at_idx")]
        ordering = ("captured_at", "pk")
        verbose_name = "snapshot operacional OTRS"
        verbose_name_plural = "snapshots operacionais OTRS"


class OTRSOperationalTicketSnapshot(models.Model):
    snapshot = models.ForeignKey(
        OTRSOperationalSnapshot, on_delete=models.CASCADE, related_name="tickets",
    )
    external_id = models.PositiveBigIntegerField()
    ticket_number = models.CharField(max_length=80)
    queue_name = models.CharField(max_length=250)
    external_state_name = models.CharField(max_length=250)
    external_state_type = models.CharField(max_length=120, blank=True)
    business_status = models.CharField(max_length=16, db_index=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)
    responsibility_normalized = models.CharField(max_length=20, blank=True)
    closing_code_raw = models.CharField(max_length=250, blank=True)
    priority_name = models.CharField(max_length=160, blank=True)
    service_name = models.CharField(max_length=250, blank=True)
    sla_name = models.CharField(max_length=250, blank=True)
    responsible_user_id = models.PositiveIntegerField(null=True, blank=True)
    cancellation_stage = models.CharField(max_length=24, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=("snapshot", "external_id"), name="uniq_otrs_snap_ticket",
        )]
        indexes = [
            models.Index(fields=("snapshot", "queue_name", "business_status"), name="otrs_snap_q_status_idx"),
            models.Index(fields=("snapshot", "source_created_at"), name="otrs_snap_created_idx"),
        ]
        verbose_name = "ticket no snapshot OTRS"


class OTRSOperationalConfigItemSnapshot(models.Model):
    ticket_snapshot = models.ForeignKey(
        OTRSOperationalTicketSnapshot, on_delete=models.CASCADE, related_name="config_items",
    )
    config_item_id = models.CharField(max_length=40)
    provider_name = models.CharField(max_length=300, blank=True)
    uf = models.CharField(max_length=100, blank=True)
    project = models.CharField(max_length=300, blank=True)
    legacy_project = models.CharField(max_length=300, blank=True)
    client = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=("ticket_snapshot", "config_item_id"), name="uniq_otrs_snap_ticket_ci",
        )]
        indexes = [
            models.Index(fields=("config_item_id", "ticket_snapshot"), name="otrs_snap_ci_ticket_idx"),
            models.Index(fields=("provider_name", "uf"), name="otrs_snap_provider_uf_idx"),
        ]
        verbose_name = "ConfigItem no snapshot OTRS"
