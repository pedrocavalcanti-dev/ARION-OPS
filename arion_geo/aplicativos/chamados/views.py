from datetime import timedelta
import json
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from aplicativos.auditoria.services import AuditService
from aplicativos.configuracoes.permissions import access_required
from aplicativos.integracoes.models import IntegrationSyncState, OTRSTicketConfigItem
from aplicativos.integracoes.services.analytics import LinkQueryService, TicketQueryService
from aplicativos.integracoes.services.closing_reason import ClosingReasonCanonicalizer
from aplicativos.integracoes.services.otrs_status import (
    business_status_label, normalize_business_status,
)
from aplicativos.integracoes.services.semantics import provider_label

from .forms import (
    TicketTrackingAssumeForm, TicketTrackingNoteForm, TicketTrackingStatusForm, TrackingUpdateForm,
)
from .models import TicketQueueView, TicketTracking, TicketTrackingEvent
from .services import (
    ACTIVE_STATES, QUEUE_FILTER_KEYS, RECURRENCE_DAYS, STATUS_LABELS, TicketQueueService,
    TicketTrackingService, TrackingConflict, format_age, otrs_queue_url, otrs_ticket_url,
    recurrent_link_ids_for_user, sanitize_queue_filters,
)


def _scoped_active_ticket(user, external_id):
    links = LinkQueryService.base(user)
    ticket = get_object_or_404(
        TicketQueryService.base(user, links=links).filter(
            business_status__in=ACTIVE_STATES,
            is_present_in_queue=True,
        ),
        external_id=external_id,
    )
    relations = list(
        OTRSTicketConfigItem.objects.filter(
            ticket=ticket,
            is_present_in_source=True,
            cmdb_link__in=links,
        ).select_related("cmdb_link", "cmdb_link__operator")
        .order_by("cmdb_link__designation", "config_item_id")
    )
    return ticket, relations


def _scoped_ticket_for_detail(user, external_id):
    links = LinkQueryService.base(user)
    ticket = get_object_or_404(
        TicketQueryService.base(user, links=links),
        external_id=external_id,
    )
    relations = list(
        OTRSTicketConfigItem.objects.filter(
            ticket=ticket,
            is_present_in_source=True,
            cmdb_link__in=links,
        ).select_related("cmdb_link", "cmdb_link__operator")
        .order_by("cmdb_link__designation", "config_item_id")
    )
    return ticket, relations


@login_required
@access_required("view_operational_tickets")
@require_GET
def index(request):
    params, active_view = _queue_params(request)
    data = TicketQueueService.build(request.user, params)
    sync = IntegrationSyncState.objects.filter(key="otrs").first()
    last_success = sync.last_success_at if sync else None
    show_sync_error = bool(sync and sync.last_error and sync.last_error_at and
                           (not last_success or sync.last_error_at > last_success))
    tab_urls = {}
    for tab in ("all", "OPEN", "PENDING", "SCHEDULED", "mine", "unassigned"):
        tab_params = QueryDict("", mutable=True)
        for key, value in data["saved_filter_values"].items():
            if value not in (None, ""):
                if tab in {"all", "OPEN", "PENDING", "SCHEDULED"} and key == "otrs_status":
                    continue
                tab_params.setlist(key, ["1" if value is True else "0" if value is False else str(value)])
        tab_params["tab"] = tab
        tab_urls[tab] = "?" + tab_params.urlencode()
    pagination_urls = {}
    for direction, page_number in (
        ("previous", data["page"].previous_page_number() if data["page"].has_previous() else None),
        ("next", data["page"].next_page_number() if data["page"].has_next() else None),
    ):
        if page_number is None:
            continue
        page_params = QueryDict("", mutable=True)
        for key, value in data["saved_filter_values"].items():
            if value not in (None, ""):
                page_params.setlist(key, ["1" if value is True else "0" if value is False else str(value)])
        page_params["page"] = str(page_number)
        if active_view:
            page_params["saved_view"] = str(active_view.pk)
        pagination_urls[direction] = "?" + page_params.urlencode()
    return render(request, "chamados/index.html", {
        **data,
        "last_sync": last_success,
        "sync_error": sync.last_error if show_sync_error else "",
        "sync_error_at": sync.last_error_at if show_sync_error else None,
        "saved_views": TicketQueueView.objects.filter(user=request.user),
        "active_saved_view_id": active_view.pk if active_view else "",
        "tab_urls": tab_urls,
        "pagination_urls": pagination_urls,
        "clear_url": reverse("chamados:index") + "?ignore_default=1",
        "otrs_queue_url": otrs_queue_url(),
        "queue_views_action_url": reverse("chamados:queue_view_action"),
    })


def _queue_params(request):
    query = request.GET
    explicit_filters = any(key not in {"saved_view", "ignore_default"} for key in query)
    selected_view = None
    base_filters = {}
    if "ignore_default" not in query and query.get("saved_view", "").isdigit():
        selected_view = TicketQueueView.objects.filter(
            user=request.user, pk=int(query["saved_view"]),
        ).first()
        if selected_view:
            base_filters = sanitize_queue_filters(selected_view.filters)
    elif not query:
        selected_view = TicketQueueView.objects.filter(user=request.user, is_default=True).first()
        if selected_view:
            base_filters = sanitize_queue_filters(selected_view.filters)

    if not explicit_filters and not query.get("saved_view") and selected_view is None:
        return {}, None
    params = QueryDict("", mutable=True)
    for key, value in base_filters.items():
        params.setlist(key, [str(value).lower() if isinstance(value, bool) else str(value)])
    for key in query:
        if key in {"saved_view", "ignore_default"}:
            continue
        params.setlist(key, query.getlist(key))
    return params, selected_view


@login_required
@access_required("view_operational_tickets")
@require_GET
def detail(request, external_id):
    ticket, relations = _scoped_ticket_for_detail(request.user, external_id)
    links_scope = LinkQueryService.base(request.user)
    recurrent_ids = recurrent_link_ids_for_user(request.user, links_scope, timezone.now())
    tracking = TicketTracking.objects.filter(ticket_external_id=ticket.external_id).select_related(
        "assigned_to", "updated_by",
    ).first()
    linked_by_ci = {}
    for relation in relations:
        link = relation.cmdb_link
        if not link:
            continue
        linked_by_ci.setdefault(relation.config_item_id, {
            "config_item_id": relation.config_item_id,
            "number": link.source_number or "",
            "designation": link.designation or link.name or relation.config_item_id,
            "client": link.contracting_client or "Não identificado",
            "project": link.business_project or "Não identificado",
            "provider": provider_label(link),
            "provider_id": link.operator.source_config_item_id if link.operator else "",
            "city": link.city or "",
            "state": link.state or "",
            "deployment_state": link.deployment_state or "",
            "incident_state": link.incident_state or "",
            "recurrent": link.pk in recurrent_ids,
            "base_url": reverse("base_links:index") + "?" + urlencode({
                "view": "links", "config_item_id": relation.config_item_id,
            }),
        })
    linked = list(linked_by_ci.values())
    age = max(timedelta(0), timezone.now() - ticket.source_created_at) if ticket.source_created_at else None
    notes = list(tracking.notes.select_related("author")[:50]) if tracking else []
    events = list(tracking.events.select_related("actor")[:50]) if tracking else []
    state_choices = TicketTracking.InternalStatus.choices
    default_status = TicketTracking.InternalStatus.OPEN
    status_labels = dict(state_choices)
    business_status = normalize_business_status(ticket.business_status)
    initial = {
        "internal_status": tracking.internal_status if tracking else default_status,
        "assigned_to": tracking.assigned_to_id if tracking else None,
        "internal_priority": tracking.internal_priority if tracking else "",
        "next_action_at": timezone.localtime(tracking.next_action_at).strftime("%Y-%m-%dT%H:%M")
        if tracking and tracking.next_action_at else "",
        "revision": tracking.revision if tracking else 0,
    }
    return JsonResponse({
        "ticket": {
            "external_id": ticket.external_id,
            "source_ticket_id": ticket.external_id,
            "ticket_number": ticket.ticket_number,
            "otrs_url": otrs_ticket_url(ticket.external_id),
            "title": ticket.title,
            "queue": ticket.queue_name,
            "external_state": ticket.external_state_name,
            "business_status": business_status,
            "operational": business_status in ACTIVE_STATES,
            "status_label": business_status_label(business_status),
            "priority": ticket.priority_name or "Não informada",
            "created_at": timezone.localtime(ticket.source_created_at).isoformat() if ticket.source_created_at else None,
            "age_label": format_age(age) if age is not None else "Sem data de criação",
            "age_hours": round(age.total_seconds() / 3600, 1) if age else None,
            "over_24h": bool(age and age > timedelta(hours=24)),
            "recurrent": any(item["recurrent"] for item in linked),
            "closing_reason": ticket.closing_code_raw or "",
            "closing_reason_label": ClosingReasonCanonicalizer.display(ticket.closing_code_raw) if ticket.closing_code_raw else "",
        },
        "tracking": {
            "internal_status": tracking.internal_status if tracking else default_status,
            "internal_status_label": tracking.get_internal_status_display() if tracking else status_labels[default_status],
            "assigned_to": tracking.assigned_to.get_full_name() or tracking.assigned_to.get_username() if tracking and tracking.assigned_to else "",
            "assigned_to_id": tracking.assigned_to_id if tracking else None,
            "internal_priority": tracking.internal_priority if tracking else "",
            "internal_priority_label": dict(TicketTracking.InternalPriority.choices).get(
                tracking.internal_priority, "",
            ) if tracking else "",
            "next_action_at": timezone.localtime(tracking.next_action_at).isoformat() if tracking and tracking.next_action_at else None,
            "revision": tracking.revision if tracking else 0,
            "updated_at": timezone.localtime(tracking.updated_at).isoformat() if tracking else None,
            "form": {
                "statuses": [{"value": value, "label": label} for value, label in state_choices],
                "priorities": [{"value": value, "label": label} for value, label in TicketTracking.InternalPriority.choices],
                "assignees": list(request.user.__class__.objects.filter(is_active=True).values(
                    "id", "username", "first_name", "last_name",
                ).order_by("first_name", "last_name", "username"))
                if request.user.has_perm("configuracoes.manage_operational_tickets") else [],
                "initial": initial,
            },
        },
        "links": linked,
        "notes": [{
            "author": note.author.get_full_name() or note.author.get_username(),
            "body": note.body,
            "created_at": timezone.localtime(note.created_at).isoformat(),
        } for note in notes],
        "events": [{
            "type": event.get_event_type_display(),
            "field": {
                "internal_status": "Status ARION", "assigned_to": "Responsável",
                "internal_priority": "Prioridade ARION", "next_action_at": "Próxima ação ARION",
            }.get(event.field_name, ""),
            "old_value": event.old_value,
            "new_value": event.new_value,
            "actor": event.actor.get_full_name() or event.actor.get_username() if event.actor else "Sistema",
            "created_at": timezone.localtime(event.created_at).isoformat(),
        } for event in events],
        "urls": {
            "tracking": reverse("chamados:tracking_update", args=(ticket.external_id,)),
            "notes": reverse("chamados:note_create", args=(ticket.external_id,)),
            "base": reverse("base_links:index") + "?" + urlencode({
                "view": "links", "config_item_id": linked[0]["config_item_id"],
            }) if linked else "",
            "cockpit": reverse("cockpit") + "?" + urlencode({"ticket_search": ticket.ticket_number}),
        },
        "recurrence_days": RECURRENCE_DAYS,
    })


@login_required
@access_required("manage_operational_tickets")
@require_POST
def tracking_update(request, external_id):
    ticket, _ = _scoped_active_ticket(request.user, external_id)
    action = request.POST.get("action", "drawer")
    if action == "inline_status":
        form = TicketTrackingStatusForm(request.POST)
        if not form.is_valid():
            return JsonResponse({"ok": False, "errors": form.errors.get_json_data()}, status=400)
        changes = {"internal_status": form.cleaned_data["internal_status"]}
        revision = form.cleaned_data["revision"]
        assume = False
        unassign = False
    elif action == "assume":
        form = TicketTrackingAssumeForm(request.POST)
        if not form.is_valid():
            return JsonResponse({"ok": False, "errors": form.errors.get_json_data()}, status=400)
        changes = {"assigned_to": request.user}
        revision = form.cleaned_data["revision"]
        assume = True
        unassign = False
    elif action == "unassign":
        form = TicketTrackingAssumeForm(request.POST)
        if not form.is_valid():
            return JsonResponse({"ok": False, "errors": form.errors.get_json_data()}, status=400)
        changes = {"assigned_to": None}
        revision = form.cleaned_data["revision"]
        assume = False
        unassign = True
    elif action == "drawer":
        form = TrackingUpdateForm(request.POST, user_model=request.user.__class__)
        if not form.is_valid():
            return JsonResponse({"ok": False, "errors": form.errors.get_json_data()}, status=400)
        changes = {field: form.cleaned_data[field] for field in (
            "internal_status", "assigned_to", "internal_priority", "next_action_at",
        )}
        revision = form.cleaned_data["revision"]
        assume = False
        unassign = False
    else:
        return JsonResponse({"ok": False, "message": "Ação de acompanhamento inválida."}, status=400)
    try:
        tracking, changed = TicketTrackingService.update(
            request=request, ticket=ticket, changes=changes, revision=revision,
            assume=assume, unassign=unassign,
        )
    except TrackingConflict as exc:
        return JsonResponse({"ok": False, "conflict": True, "message": str(exc)}, status=409)
    return JsonResponse({
        "ok": True, "changed": changed, "revision": tracking.revision if tracking else 0,
        "internal_status": tracking.internal_status if tracking else TicketTracking.InternalStatus.OPEN,
        "internal_status_label": tracking.get_internal_status_display() if tracking else dict(
            TicketTracking.InternalStatus.choices,
        )[TicketTracking.InternalStatus.OPEN],
        "assigned_to": (
            tracking.assigned_to.get_full_name() or tracking.assigned_to.get_username()
        ) if tracking and tracking.assigned_to else "",
        "assigned_to_id": tracking.assigned_to_id if tracking else None,
    })


@login_required
@access_required("manage_operational_tickets")
@require_POST
def note_create(request, external_id):
    ticket, _ = _scoped_active_ticket(request.user, external_id)
    form = TicketTrackingNoteForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"ok": False, "errors": form.errors.get_json_data()}, status=400)
    try:
        with transaction.atomic():
            tracking = TicketTracking.objects.select_for_update().filter(
                ticket_external_id=ticket.external_id,
            ).first()
            current_revision = tracking.revision if tracking else 0
            if form.cleaned_data["revision"] != current_revision:
                return JsonResponse({
                    "ok": False, "conflict": True,
                    "message": "O acompanhamento mudou. Recarregue o chamado e tente novamente.",
                }, status=409)
            if tracking is None:
                tracking = TicketTracking(ticket_external_id=ticket.external_id)
                tracking.save()
            note = tracking.notes.create(author=request.user, body=form.cleaned_data["body"])
            tracking.updated_by = request.user
            tracking.revision = current_revision + 1
            tracking.save(update_fields=("updated_by", "revision", "updated_at"))
            TicketTrackingEvent.objects.create(
                tracking=tracking,
                event_type=TicketTrackingEvent.EventType.NOTE_ADDED,
                actor=request.user,
            )
            AuditService.record(
                request=request, category="operational_ticket", action="internal_note_added",
                description="Nota interna adicionada ao acompanhamento local.", module="chamados",
                metadata={"ticket_external_id": ticket.external_id, "note_id": note.pk},
            )
    except IntegrityError:
        return JsonResponse({
            "ok": False, "conflict": True,
            "message": "O acompanhamento mudou. Recarregue o chamado e tente novamente.",
        }, status=409)
    return JsonResponse({
        "ok": True,
        "note": {
            "author": request.user.get_full_name() or request.user.get_username(),
            "body": note.body,
            "created_at": timezone.localtime(note.created_at).isoformat(),
        },
        "revision": tracking.revision,
    }, status=201)


@login_required
@access_required("manage_operational_tickets")
@require_POST
def queue_view_action(request):
    action = str(request.POST.get("action", "")).strip()
    view_id = str(request.POST.get("id", "")).strip()
    try:
        if action == "save":
            name = str(request.POST.get("name", "")).strip()
            if not name or len(name) > 80:
                return JsonResponse({"ok": False, "message": "Informe um nome de até 80 caracteres."}, status=400)
            try:
                raw_filters = json.loads(request.POST.get("filters", "{}"))
            except json.JSONDecodeError:
                return JsonResponse({"ok": False, "message": "Os filtros enviados não são válidos."}, status=400)
            if not isinstance(raw_filters, dict) or set(raw_filters) - set(QUEUE_FILTER_KEYS):
                return JsonResponse({"ok": False, "message": "A visualização contém filtros não permitidos."}, status=400)
            filters = sanitize_queue_filters(raw_filters)
            if "page_size" in filters:
                filters["per_page"] = filters.pop("page_size")
            if "over_24h" in filters:
                filters["aging"] = filters.pop("over_24h")
            view, created = TicketQueueView.objects.get_or_create(
                user=request.user, name=name, defaults={"filters": filters},
            )
            if not created:
                return JsonResponse({"ok": False, "message": "Você já tem uma fila salva com esse nome."}, status=409)
            AuditService.record(
                request=request, category="operational_ticket", action="queue_view_created",
                description="Visualização pessoal da fila criada.", module="chamados",
                metadata={"view_id": view.pk, "filter_count": len(filters)},
            )
            return JsonResponse({"ok": True, "id": view.pk, "name": view.name})

        if not view_id.isdigit():
            return JsonResponse({"ok": False, "message": "Fila salva não encontrada."}, status=404)
        view = TicketQueueView.objects.filter(user=request.user, pk=int(view_id)).first()
        if view is None:
            return JsonResponse({"ok": False, "message": "Fila salva não encontrada."}, status=404)
        if action == "rename":
            name = str(request.POST.get("name", "")).strip()
            if not name or len(name) > 80:
                return JsonResponse({"ok": False, "message": "Informe um nome de até 80 caracteres."}, status=400)
            if TicketQueueView.objects.filter(user=request.user, name=name).exclude(pk=view.pk).exists():
                return JsonResponse({"ok": False, "message": "Você já tem uma fila com esse nome."}, status=409)
            old_name = view.name
            view.name = name
            view.save(update_fields=("name", "updated_at"))
            audit_action = "queue_view_renamed"
            metadata = {"view_id": view.pk, "old_name": old_name, "name": name}
        elif action == "default":
            with transaction.atomic():
                request.user.__class__.objects.select_for_update().get(pk=request.user.pk)
                view = TicketQueueView.objects.select_for_update().get(user=request.user, pk=view.pk)
                TicketQueueView.objects.filter(user=request.user, is_default=True).exclude(pk=view.pk).update(is_default=False)
                view.is_default = not view.is_default
                view.save(update_fields=("is_default", "updated_at"))
            audit_action = "queue_view_default_changed"
            metadata = {"view_id": view.pk, "is_default": view.is_default}
        elif action == "delete":
            metadata = {"view_id": view.pk, "name": view.name}
            view.delete()
            audit_action = "queue_view_deleted"
        else:
            return JsonResponse({"ok": False, "message": "Ação inválida."}, status=400)
        AuditService.record(
            request=request, category="operational_ticket", action=audit_action,
            description="Visualização pessoal da fila atualizada.", module="chamados", metadata=metadata,
        )
        return JsonResponse({"ok": True, "id": int(view_id), "name": view.name if action != "delete" else "", "is_default": view.is_default if action != "delete" else False})
    except IntegrityError:
        return JsonResponse({"ok": False, "message": "Já existe uma fila salva com esse nome."}, status=409)
