from django.contrib import admin

from .models import (
    QualityAuditEvent,
    QualityContact,
    QualityDeletedOrder,
    QualityEvidence,
    QualityMonitoringCycle,
    QualityOccurrence,
    QualityOrder,
    QualityTicket,
    QualityTreatment,
)


@admin.register(QualityOrder)
class QualityOrderAdmin(admin.ModelAdmin):
    list_display = ("code", "status", "priority", "recurrence_level", "designation_snapshot", "project_snapshot", "owner", "created_at")
    list_filter = ("status", "priority", "recurrence_level", "uf_snapshot")
    search_fields = ("code", "designation_snapshot", "ci_snapshot", "client_snapshot", "project_snapshot", "isp_snapshot")
    autocomplete_fields = ("owner",)
    readonly_fields = ("code", "created_at", "updated_at")


@admin.register(QualityOccurrence)
class QualityOccurrenceAdmin(admin.ModelAdmin):
    list_display = ("quality_order", "ticket_number", "occurred_at", "counts_as_recurrence", "excluded", "queue_snapshot")
    list_filter = ("counts_as_recurrence", "same_root_cause", "excluded", "ticket_status_snapshot")
    search_fields = ("quality_order__code", "ticket_number", "ticket_external_id_snapshot", "queue_snapshot")
    autocomplete_fields = ("quality_order",)


@admin.register(QualityTicket)
class QualityTicketAdmin(admin.ModelAdmin):
    list_display = ("quality_order", "ticket_number", "ticket_type", "state_snapshot", "queue_snapshot", "opened_at", "closed_at")
    list_filter = ("ticket_type", "state_snapshot", "queue_snapshot")
    search_fields = ("quality_order__code", "ticket_number", "ticket_external_id_snapshot")
    autocomplete_fields = ("quality_order",)


@admin.register(QualityTreatment)
class QualityTreatmentAdmin(admin.ModelAdmin):
    list_display = ("code", "quality_order", "status", "support_ticket_number", "created_by", "created_at")
    list_filter = ("status", "send_status", "created_at")
    search_fields = ("code", "quality_order__code", "support_ticket_number", "reason")
    readonly_fields = ("code", "correlation_id", "created_at", "updated_at", "active_order_key")
    autocomplete_fields = ("quality_order", "owner", "created_by")


@admin.register(QualityMonitoringCycle)
class QualityMonitoringCycleAdmin(admin.ModelAdmin):
    list_display = ("quality_order", "cycle_number", "result", "started_at", "expected_end_at", "ended_at")
    list_filter = ("result",)
    search_fields = ("quality_order__code", "quality_order__designation_snapshot")
    autocomplete_fields = ("quality_order", "quality_ticket", "trigger_occurrence")


@admin.register(QualityContact)
class QualityContactAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "client", "project", "contact_type", "escalation_level", "active")
    list_filter = ("active", "contact_type", "escalation_level", "region")
    search_fields = ("name", "company", "client", "project", "email", "phone")


@admin.register(QualityAuditEvent)
class QualityAuditEventAdmin(admin.ModelAdmin):
    list_display = ("quality_order", "event_type", "actor", "created_at")
    list_filter = ("event_type", "created_at")
    search_fields = ("quality_order__code", "description", "actor__username")
    readonly_fields = ("quality_order", "event_type", "actor", "description", "metadata", "created_at")


@admin.register(QualityEvidence)
class QualityEvidenceAdmin(admin.ModelAdmin):
    list_display = ("quality_order", "quality_treatment", "title", "original_filename", "uploaded_by", "created_at")
    search_fields = ("quality_order__code", "quality_treatment__code", "title", "original_filename")
    autocomplete_fields = ("quality_order", "quality_treatment", "uploaded_by")


@admin.register(QualityDeletedOrder)
class QualityDeletedOrderAdmin(admin.ModelAdmin):
    list_display = ("order_code", "original_order_id", "deleted_at")
    search_fields = ("order_code",)
    readonly_fields = ("order_code", "original_order_id", "deleted_at")
