# Initial local schema for Quality Operational.
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("integracoes", "0009_remove_cmdboperatormirror_portal_username"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="QualityOrder",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(blank=True, max_length=24, null=True, unique=True, verbose_name="código")),
                ("status", models.CharField(choices=[("NOVA", "Nova"), ("TRIAGEM", "Triagem"), ("TRATAMENTO", "Tratamento"), ("AGUARDANDO_TERCEIRO", "Aguardando terceiro"), ("MONITORAMENTO", "Monitoramento"), ("CONCLUIDA", "Concluída"), ("CANCELADA", "Cancelada")], db_index=True, default="NOVA", max_length=24, verbose_name="status")),
                ("recurrence_level", models.CharField(blank=True, choices=[("R1", "R1 · 1ª reincidência"), ("R2", "R2 · 2ª reincidência"), ("R3", "R3 · 3ª reincidência"), ("R4", "R4 · 4ª reincidência"), ("R5+", "R5+ · Crônico")], db_index=True, max_length=4, verbose_name="nível de reincidência")),
                ("priority", models.CharField(choices=[("BAIXA", "Baixa"), ("MEDIA", "Média"), ("ALTA", "Alta"), ("CRITICA", "Crítica")], db_index=True, default="MEDIA", max_length=10, verbose_name="prioridade")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="criada em")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="atualizada em")),
                ("treatment_deadline", models.DateTimeField(blank=True, db_index=True, null=True, verbose_name="prazo de tratamento")),
                ("completed_at", models.DateTimeField(blank=True, null=True, verbose_name="concluída em")),
                ("designation_snapshot", models.CharField(blank=True, max_length=300, verbose_name="designação")),
                ("ci_snapshot", models.CharField(blank=True, db_index=True, max_length=40, verbose_name="ConfigItem")),
                ("client_snapshot", models.CharField(blank=True, db_index=True, max_length=300, verbose_name="cliente")),
                ("project_snapshot", models.CharField(blank=True, db_index=True, max_length=300, verbose_name="projeto")),
                ("isp_snapshot", models.CharField(blank=True, db_index=True, max_length=300, verbose_name="provedor / ISP")),
                ("uf_snapshot", models.CharField(blank=True, db_index=True, max_length=100, verbose_name="UF")),
                ("current_reason", models.TextField(blank=True, verbose_name="motivo atual")),
                ("current_root_cause", models.TextField(blank=True, verbose_name="causa raiz atual")),
                ("internal_notes", models.TextField(blank=True, verbose_name="observação interna")),
                ("link", models.ForeignKey(blank=True, db_column="link_config_item_id", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="quality_orders", to="integracoes.cmdblinkmirror", to_field="source_config_item_id")),
                ("owner", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="quality_orders", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Ordem de Qualidade",
                "verbose_name_plural": "Ordens de Qualidade",
                "ordering": ("-created_at", "-pk"),
                "permissions": (("view_quality", "Can view Quality module"),),
            },
        ),
        migrations.CreateModel(
            name="QualityContact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("company", models.CharField(blank=True, max_length=200)),
                ("region", models.CharField(blank=True, max_length=160)),
                ("client", models.CharField(blank=True, db_index=True, max_length=300)),
                ("project", models.CharField(blank=True, db_index=True, max_length=300)),
                ("contact_type", models.CharField(blank=True, max_length=120)),
                ("name", models.CharField(max_length=200)),
                ("role", models.CharField(blank=True, max_length=200)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("phone", models.CharField(blank=True, max_length=80)),
                ("teams", models.CharField(blank=True, max_length=200)),
                ("escalation_level", models.PositiveSmallIntegerField(default=1)),
                ("active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "contato de qualidade", "verbose_name_plural": "contatos de qualidade", "ordering": ("client", "project", "escalation_level", "name")},
        ),
        migrations.CreateModel(
            name="QualityOccurrence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ticket_external_id_snapshot", models.PositiveBigIntegerField(blank=True, db_index=True, null=True)),
                ("ticket_number", models.CharField(blank=True, db_index=True, max_length=80)),
                ("occurred_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("is_initial_occurrence", models.BooleanField(default=False)),
                ("recurrence_number", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("reason", models.TextField(blank=True)),
                ("root_cause", models.TextField(blank=True)),
                ("counts_as_recurrence", models.BooleanField(db_index=True, default=False)),
                ("same_root_cause", models.BooleanField(default=False)),
                ("excluded", models.BooleanField(db_index=True, default=False)),
                ("exclusion_reason", models.CharField(blank=True, max_length=32)),
                ("ticket_status_snapshot", models.CharField(blank=True, max_length=120)),
                ("queue_snapshot", models.CharField(blank=True, max_length=250)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("otrs_ticket", models.ForeignKey(blank=True, db_column="otrs_external_id", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="quality_occurrences", to="integracoes.otrsticket", to_field="external_id")),
                ("quality_order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="occurrences", to="qualidade.qualityorder")),
            ],
            options={"verbose_name": "ocorrência de qualidade", "verbose_name_plural": "ocorrências de qualidade", "ordering": ("occurred_at", "pk")},
        ),
        migrations.CreateModel(
            name="QualityTicket",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ticket_external_id_snapshot", models.PositiveBigIntegerField(blank=True, db_index=True, null=True)),
                ("ticket_number", models.CharField(blank=True, db_index=True, max_length=80)),
                ("ticket_type", models.CharField(choices=[("TRATAMENTO", "Tratamento"), ("MONITORAMENTO", "Monitoramento")], max_length=16)),
                ("queue_snapshot", models.CharField(blank=True, max_length=250)),
                ("state_snapshot", models.CharField(blank=True, max_length=250)),
                ("owner_snapshot", models.CharField(blank=True, max_length=250)),
                ("opened_at", models.DateTimeField(blank=True, null=True)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("result", models.CharField(blank=True, max_length=160)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("otrs_ticket", models.ForeignKey(blank=True, db_column="otrs_external_id", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="quality_tickets", to="integracoes.otrsticket", to_field="external_id")),
                ("quality_order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="quality_tickets", to="qualidade.qualityorder")),
            ],
            options={"verbose_name": "ticket de qualidade", "verbose_name_plural": "tickets de qualidade", "ordering": ("-created_at", "-pk")},
        ),
        migrations.CreateModel(
            name="QualityAuditEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_type", models.CharField(choices=[("OQ_CREATED", "Ordem criada"), ("STATUS_CHANGED", "Status alterado"), ("OWNER_CHANGED", "Responsável alterado"), ("PRIORITY_CHANGED", "Prioridade alterada"), ("OCCURRENCE_LINKED", "Ocorrência vinculada"), ("RECURRENCE_CONFIRMED", "Reincidência confirmada"), ("QUALITY_TICKET_LINKED", "Ticket de Qualidade vinculado"), ("MONITORING_STARTED", "Monitoramento iniciado"), ("MONITORING_FAILED", "Monitoramento reincidiu"), ("MONITORING_SUCCEEDED", "Monitoramento concluído com sucesso"), ("ORDER_CLOSED", "Ordem encerrada")], db_index=True, max_length=32)),
                ("description", models.TextField(blank=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="quality_audit_events", to=settings.AUTH_USER_MODEL)),
                ("quality_order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="audit_events", to="qualidade.qualityorder")),
            ],
            options={"verbose_name": "evento de qualidade", "verbose_name_plural": "eventos de qualidade", "ordering": ("-created_at", "-pk")},
        ),
        migrations.CreateModel(
            name="QualityMonitoringCycle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cycle_number", models.PositiveSmallIntegerField()),
                ("started_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("expected_end_at", models.DateTimeField(db_index=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("result", models.CharField(choices=[("EM_ANDAMENTO", "Em andamento"), ("SUCESSO", "Sucesso"), ("REINCIDIU", "Reincidiu"), ("CANCELADO", "Cancelado")], db_index=True, default="EM_ANDAMENTO", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("quality_order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="monitoring_cycles", to="qualidade.qualityorder")),
                ("quality_ticket", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="monitoring_cycles", to="qualidade.qualityticket")),
                ("trigger_occurrence", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="triggered_monitoring_cycles", to="qualidade.qualityoccurrence")),
            ],
            options={"verbose_name": "ciclo de monitoramento", "verbose_name_plural": "ciclos de monitoramento", "ordering": ("-cycle_number",)},
        ),
        migrations.AddIndex(
            model_name="qualityorder",
            index=models.Index(fields=["status", "treatment_deadline"], name="quality_order_due_idx"),
        ),
        migrations.AddIndex(
            model_name="qualityorder",
            index=models.Index(fields=["priority", "status"], name="quality_order_priority_idx"),
        ),
        migrations.AddConstraint(
            model_name="qualityoccurrence",
            constraint=models.UniqueConstraint(fields=("quality_order", "ticket_external_id_snapshot"), name="quality_order_ticket_once"),
        ),
        migrations.AddIndex(
            model_name="qualityoccurrence",
            index=models.Index(fields=["quality_order", "occurred_at"], name="quality_occurrence_date_idx"),
        ),
        migrations.AddIndex(
            model_name="qualityoccurrence",
            index=models.Index(fields=["excluded", "counts_as_recurrence"], name="quality_occurrence_valid_idx"),
        ),
        migrations.AddConstraint(
            model_name="qualitymonitoringcycle",
            constraint=models.UniqueConstraint(fields=("quality_order", "cycle_number"), name="quality_cycle_number_once"),
        ),
        migrations.AddIndex(
            model_name="qualitymonitoringcycle",
            index=models.Index(fields=["result", "expected_end_at"], name="quality_cycle_due_idx"),
        ),
    ]
