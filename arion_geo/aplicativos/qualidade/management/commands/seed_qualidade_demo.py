"""SOMENTE DESENVOLVIMENTO LOCAL — Nunca executar automaticamente nem utilizar em produção."""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from aplicativos.auditoria.models import AuditEvent
from aplicativos.auditoria.services import AuditService
from aplicativos.integracoes.models import CMDBLinkMirror, OTRSTicket
from aplicativos.integracoes.services.project_names import canonical_project_name, display_project_name
from aplicativos.integracoes.services.semantics import provider_values_for_links

from ...models import (
    QUALITY_DEMO_MARKER,
    QualityAction,
    QualityAuditEvent,
    QualityContact,
    QualityMonitoringCycle,
    QualityOccurrence,
    QualityOrder,
    QualityTicket,
)
from ...services.constants import QUALITY_RREIP_DAYS
from ...services.recurrence import build_quality_recurrence_analysis
from ...services.treatments import create_default_quality_actions


SEED_ID = "quality-demo-v1"
PREFERRED_GROUPS = (
    ("Claro", "Banco do Brasil", 2),
    ("Claro", "Correios", 2),
    ("Vivo", "Bradesco", 1),
    ("Vivo", "Arcos Dourados", 1),
    ("Vivo", "Fust", 1),
)

ORDER_SCENARIOS = (
    (QualityOrder.Status.NOVA, QualityOrder.Priority.CRITICA, -2, None),
    (QualityOrder.Status.NOVA, QualityOrder.Priority.BAIXA, 22, 0),
    (QualityOrder.Status.TRIAGEM, QualityOrder.Priority.MEDIA, 18, 1),
    (QualityOrder.Status.TRATAMENTO, QualityOrder.Priority.ALTA, 2, 2),
    (QualityOrder.Status.TRATAMENTO, QualityOrder.Priority.ALTA, 3, 0),
    (QualityOrder.Status.TRATAMENTO, QualityOrder.Priority.MEDIA, 16, 1),
    (QualityOrder.Status.AGUARDANDO_TERCEIRO, QualityOrder.Priority.MEDIA, 14, 2),
    (QualityOrder.Status.MONITORAMENTO, QualityOrder.Priority.MEDIA, 20, 0),
    (QualityOrder.Status.MONITORAMENTO, QualityOrder.Priority.BAIXA, 25, 1),
    (QualityOrder.Status.CONCLUIDA, QualityOrder.Priority.MEDIA, None, 2),
    (QualityOrder.Status.CONCLUIDA, QualityOrder.Priority.BAIXA, None, 0),
)
DEMO_CREATED_DAYS_AGO = (92, 76, 63, 49, 37, 28, 20, 15, 11, 70, 42)
DEMO_MANUAL_LEVELS = (
    QualityOrder.RecurrenceLevel.R5_PLUS,
    QualityOrder.RecurrenceLevel.R1,
    QualityOrder.RecurrenceLevel.R3,
    QualityOrder.RecurrenceLevel.R2,
    QualityOrder.RecurrenceLevel.R4,
    QualityOrder.RecurrenceLevel.R1,
    QualityOrder.RecurrenceLevel.R3,
    QualityOrder.RecurrenceLevel.R2,
    QualityOrder.RecurrenceLevel.R4,
    QualityOrder.RecurrenceLevel.R1,
    QualityOrder.RecurrenceLevel.R5_PLUS,
)


class Command(BaseCommand):
    help = "SOMENTE DESENVOLVIMENTO LOCAL: cria ou remove um conjunto pequeno de dados demonstrativos locais da Qualidade."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Remove somente OQs, contatos e eventos identificados por este seed.",
        )

    def handle(self, *args, **options):
        self._ensure_local_mysql()
        if options["clear"]:
            self._clear_seed()
            return
        self._create_seed()

    def _ensure_local_mysql(self):
        host = str(connection.settings_dict.get("HOST") or "").strip().casefold()
        local_hosts = {"", "localhost", "127.0.0.1", "::1"}
        if (
            settings.DEBUG is not True
            or getattr(settings, "ARION_PRODUCTION", True) is not False
            or connection.vendor != "mysql"
            or host not in local_hosts
        ):
            raise CommandError(
                "seed_qualidade_demo permitido somente com DEBUG=True e MySQL local de desenvolvimento."
            )

    def _seed_audit_ids(self):
        return [
            event.pk
            for event in AuditEvent.objects.filter(category="QUALITY").only("pk", "metadata").iterator(chunk_size=500)
            if isinstance(event.metadata, dict) and event.metadata.get("seed") == SEED_ID
        ]

    @transaction.atomic
    def _clear_seed(self):
        order_ids = list(
            QualityOrder.objects.filter(internal_notes__startswith=QUALITY_DEMO_MARKER)
            .values_list("pk", flat=True)
        )
        contacts = QualityContact.objects.filter(notes__startswith=QUALITY_DEMO_MARKER)
        audit_ids = self._seed_audit_ids()

        # Child Quality rows cascade from these tagged OQs; ordinary OQs are never selected.
        deleted_contacts, _ = contacts.delete()
        QualityOrder.objects.filter(pk__in=order_ids).delete()
        deleted_audits, _ = AuditEvent.objects.filter(pk__in=audit_ids).delete()
        self.stdout.write(self.style.SUCCESS(
            f"Seed removido: {len(order_ids)} OQs, {deleted_contacts} contatos e {deleted_audits} eventos centrais."
        ))

    @transaction.atomic
    def _create_seed(self):
        if (
            QualityOrder.objects.filter(internal_notes__startswith=QUALITY_DEMO_MARKER).exists()
            or QualityContact.objects.filter(notes__startswith=QUALITY_DEMO_MARKER).exists()
            or self._seed_audit_ids()
        ):
            raise CommandError("O seed já existe. Execute seed_qualidade_demo --clear antes de recriá-lo.")

        User = get_user_model()
        actor = User.objects.filter(is_active=True, is_superuser=True).order_by("pk").first()
        if actor is None:
            raise CommandError("É necessário um superusuário local ativo para atribuir os eventos demonstrativos.")
        owners = list(User.objects.filter(is_active=True).order_by("pk")[:3])

        now = timezone.now()
        analysis = build_quality_recurrence_analysis(actor, now=now)
        used_link_ids = set(
            QualityOrder.objects.exclude(link_id__isnull=True).values_list("link_id", flat=True)
        )
        live_links = list(
            CMDBLinkMirror.objects.filter(
                is_present_in_source=True,
                source_config_item_id__in=analysis["links"],
            ).exclude(source_config_item_id__in=used_link_ids).select_related("operator")
        )
        if not live_links:
            raise CommandError("Não há links CMDB presentes no espelho com tickets válidos para demonstrar a Qualidade.")
        semantics = provider_values_for_links(live_links)

        selected_active = self._select_active_links(live_links, analysis)
        selected_quiet = self._select_quiet_links(
            live_links,
            analysis,
            excluded={link.source_config_item_id for link in selected_active},
            cutoff=now - timedelta(days=45),
            count=4,
        )
        selected_links = selected_active + selected_quiet
        if len(selected_links) != len(ORDER_SCENARIOS):
            raise CommandError(
                "Não foi possível encontrar 11 links presentes, sem OQ existente, com histórico suficiente "
                "para ilustrar reincidência e ciclos RREIP. Nenhum dado foi gravado."
            )

        ticket_ids = {
            item["external_id"]
            for link in selected_links
            for item in self._occurrence_items(analysis["links"][link.source_config_item_id])
        }
        tickets = {
            ticket.external_id: ticket
            for ticket in OTRSTicket.objects.filter(external_id__in=ticket_ids)
        }
        missing_tickets = ticket_ids - set(tickets)
        if missing_tickets:
            raise CommandError("Um ticket do espelho mudou durante a seleção. Execute novamente; nenhum dado foi gravado.")

        created_orders = []
        occurrences_by_order = {}
        for index, (link, scenario) in enumerate(zip(selected_links, ORDER_SCENARIOS)):
            status, priority, deadline_offset, owner_offset = scenario
            owner = owners[owner_offset % len(owners)] if owner_offset is not None and owners else None
            completed_at = now - timedelta(days=15 + (index % 2) * 20) if status == QualityOrder.Status.CONCLUIDA else None
            created_at = now - timedelta(days=DEMO_CREATED_DAYS_AGO[index])
            order = QualityOrder.objects.create(
                link=link,
                status=status,
                priority=priority,
                owner=owner,
                # This demonstration classification is assigned manually and does not
                # derive from the mirror recurrence count.
                recurrence_level=DEMO_MANUAL_LEVELS[index],
                treatment_deadline=(now + timedelta(days=deadline_offset)) if deadline_offset is not None else None,
                completed_at=completed_at,
                current_reason=(
                    f"{QUALITY_DEMO_MARKER} · recorrência observada no espelho; contexto local de demonstração."
                ),
                current_root_cause=f"{QUALITY_DEMO_MARKER} · causa ainda não classificada.",
                internal_notes=(
                    f"{QUALITY_DEMO_MARKER}\nRegistro local para avaliação do fluxo de Qualidade. "
                    "Nível R1–R5 definido manualmente para demonstração, sem derivação da contagem de reincidências. "
                    "Não altera nem representa uma nova gravação no OTRS ou na CMDB."
                ),
            )
            QualityOrder.objects.filter(pk=order.pk).update(created_at=created_at)
            order.created_at = created_at
            created_orders.append(order)

            self._record_order_event(
                order, actor, QualityAuditEvent.EventType.OQ_CREATED,
                "Ordem demonstrativa criada no ambiente local.", {"status": status},
                occurred_at=created_at,
            )
            if priority != QualityOrder.Priority.MEDIA:
                self._record_order_event(
                    order, actor, QualityAuditEvent.EventType.PRIORITY_CHANGED,
                    f"Prioridade demonstrativa definida como {order.get_priority_display()}.",
                    {"from": QualityOrder.Priority.MEDIA, "to": priority},
                    occurred_at=created_at,
                )
            if status != QualityOrder.Status.NOVA:
                self._record_order_event(
                    order, actor, QualityAuditEvent.EventType.STATUS_CHANGED,
                    f"Status demonstrativo atualizado para {order.get_status_display()}.",
                    {"from": QualityOrder.Status.NOVA, "to": status},
                    occurred_at=completed_at or created_at,
                )
            if owner:
                self._record_order_event(
                    order, actor, QualityAuditEvent.EventType.OWNER_CHANGED,
                    f"Responsável demonstrativo atribuído: {owner.get_full_name() or owner.get_username()}.",
                    {"from": None, "to": owner.pk},
                    occurred_at=created_at,
                )

            rows = []
            for position, item in enumerate(self._occurrence_items(analysis["links"][link.source_config_item_id])):
                ticket = tickets[item["external_id"]]
                row = QualityOccurrence.objects.create(
                    quality_order=order,
                    otrs_ticket=ticket,
                    ticket_external_id_snapshot=ticket.external_id,
                    ticket_number=ticket.ticket_number,
                    occurred_at=item["occurred_at"],
                    is_initial_occurrence=position == 0,
                    recurrence_number=position if position else None,
                    reason=f"{QUALITY_DEMO_MARKER} · vínculo demonstrativo ao ticket espelhado.",
                    root_cause=f"{QUALITY_DEMO_MARKER} · revisar causa no fluxo local.",
                    counts_as_recurrence=position > 0,
                    same_root_cause=False,
                    ticket_status_snapshot=ticket.external_state_name,
                    queue_snapshot=ticket.queue_name,
                )
                rows.append(row)
            occurrences_by_order[order.pk] = rows

            create_default_quality_actions(order)
            completed_actions = (4, 2, 3, 4, 2, 3, 2, 4, 2, 6, 6)[index]
            for action in QualityAction.objects.filter(quality_order=order).order_by("order")[:completed_actions]:
                action.completed = True
                action.completed_at = now - timedelta(days=max(1, index % 5))
                action.completed_by = owner or actor
                action.save(update_fields=("completed", "completed_at", "completed_by"))
            self._record_order_event(
                order, actor, QualityAuditEvent.EventType.CHECKLIST_ITEM_CHANGED,
                f"Checklist demonstrativo: {completed_actions} de 6 ações concluídas.",
                {"completed": completed_actions, "total": 6},
                occurred_at=created_at + timedelta(days=1),
            )

            if index in {3, 4, 5}:
                self._link_demo_quality_ticket(order, occurrences_by_order[order.pk][-1], actor)

        self._create_demo_cycles(created_orders, occurrences_by_order, actor, now)
        self._create_demo_contacts(selected_links, semantics, actor)

        self.stdout.write(self.style.SUCCESS(
            "Seed criado: 11 OQs (9 abertas e 2 concluídas), 5 ciclos RREIP e 3 contatos demonstrativos. "
            "Os vínculos e tickets usados são somente leitura dos espelhos locais."
        ))

    def _select_active_links(self, links, analysis):
        existing = set(
            QualityOrder.objects.exclude(link_id__isnull=True).values_list("link_id", flat=True)
        )
        grouped = {}
        for link in links:
            config_item_id = link.source_config_item_id
            summary = analysis["links"].get(config_item_id, {})
            if config_item_id in existing or not summary.get("is_active_recurrence"):
                continue
            if len(summary.get("current_episode").occurrences if summary.get("current_episode") else ()) < 2:
                continue
            key = (link.contracting_client.casefold(), canonical_project_name(link.business_project or link.legacy_project))
            grouped.setdefault(key, []).append(link)

        selected = []
        for client, project, count in PREFERRED_GROUPS:
            key = (client.casefold(), canonical_project_name(project))
            candidates = grouped.get(key, [])
            candidates.sort(key=lambda link: self._link_rank(link, analysis))
            selected.extend(candidates[:count])

        selected_ids = {link.source_config_item_id for link in selected}
        if len(selected) < 7:
            fallbacks = [
                link for group in grouped.values() for link in group
                if link.source_config_item_id not in selected_ids
            ]
            fallbacks.sort(key=lambda link: self._link_rank(link, analysis))
            selected.extend(fallbacks[:7 - len(selected)])
        return selected[:7]

    def _select_quiet_links(self, links, analysis, *, excluded, cutoff, count):
        candidates = []
        for link in links:
            config_item_id = link.source_config_item_id
            if config_item_id in excluded:
                continue
            occurrences = analysis["links"].get(config_item_id, {}).get("occurrences", ())
            if len(occurrences) < 2 or any(item["occurred_at"] > cutoff for item in occurrences):
                continue
            candidates.append((len(occurrences), link))
        candidates.sort(key=lambda row: (-row[0], row[1].source_config_item_id))
        return [link for _count, link in candidates[:count]]

    @staticmethod
    def _link_rank(link, analysis):
        summary = analysis["links"].get(link.source_config_item_id, {})
        return (
            -summary.get("recurrence_count", 0),
            -summary.get("valid_occurrence_count", 0),
            link.source_config_item_id,
        )

    @staticmethod
    def _occurrence_items(summary):
        episode = summary.get("current_episode")
        source = episode.occurrences if episode else summary.get("occurrences", ())
        return tuple(source[-3:])

    def _link_demo_quality_ticket(self, order, occurrence, actor):
        ticket = occurrence.otrs_ticket
        record = QualityTicket.objects.create(
            quality_order=order,
            otrs_ticket=ticket,
            ticket_external_id_snapshot=ticket.external_id,
            ticket_number=ticket.ticket_number,
            ticket_type=QualityTicket.TicketType.TREATMENT,
            queue_snapshot=ticket.queue_name,
            state_snapshot=ticket.external_state_name,
            owner_snapshot="",
            opened_at=ticket.source_created_at,
            closed_at=ticket.resolved_at or ticket.terminal_state_at or ticket.canceled_at,
            result=ticket.closing_code_raw or ticket.get_business_status_display(),
        )
        self._record_order_event(
            order, actor, QualityAuditEvent.EventType.QUALITY_TICKET_LINKED,
            f"Ticket espelhado {record.ticket_number} associado como referência demonstrativa local.",
            {"ticket_number": record.ticket_number, "ticket_type": record.ticket_type},
        )

    def _create_demo_cycles(self, orders, occurrences_by_order, actor, now):
        active_specs = (
            (7, 12, QualityMonitoringCycle.Result.IN_PROGRESS),
            (8, 28, QualityMonitoringCycle.Result.IN_PROGRESS),
        )
        for order_index, elapsed_days, result in active_specs:
            order = orders[order_index]
            started_at = now - timedelta(days=elapsed_days)
            cycle = QualityMonitoringCycle.objects.create(
                quality_order=order,
                cycle_number=1,
                started_at=started_at,
                expected_end_at=started_at + timedelta(days=QUALITY_RREIP_DAYS),
                result=result,
            )
            self._record_order_event(
                order, actor, QualityAuditEvent.EventType.MONITORING_STARTED,
                f"Ciclo RREIP demonstrativo iniciado há {elapsed_days} dias.",
                {"cycle_number": cycle.cycle_number, "duration_days": QUALITY_RREIP_DAYS},
                occurred_at=started_at,
            )

        failed_order = orders[4]
        trigger_occurrence = max(
            occurrences_by_order[failed_order.pk], key=lambda row: row.occurred_at,
        )
        started_at = trigger_occurrence.occurred_at - timedelta(days=8)
        failed_cycle = QualityMonitoringCycle.objects.create(
            quality_order=failed_order,
            cycle_number=1,
            started_at=started_at,
            expected_end_at=started_at + timedelta(days=QUALITY_RREIP_DAYS),
            ended_at=trigger_occurrence.occurred_at,
            result=QualityMonitoringCycle.Result.RECURRENCE,
            trigger_occurrence=trigger_occurrence,
        )
        self._record_order_event(
            failed_order, actor, QualityAuditEvent.EventType.MONITORING_FAILED,
            "Ciclo RREIP demonstrativo registrou reincidência após 8 dias.",
            {"cycle_number": failed_cycle.cycle_number, "elapsed_days": 8,
             "ticket_number": trigger_occurrence.ticket_number},
            occurred_at=trigger_occurrence.occurred_at,
        )

        for order_index in (9, 10):
            order = orders[order_index]
            expected_end_at = order.completed_at
            started_at = expected_end_at - timedelta(days=QUALITY_RREIP_DAYS)
            cycle = QualityMonitoringCycle.objects.create(
                quality_order=order,
                cycle_number=1,
                started_at=started_at,
                expected_end_at=expected_end_at,
                ended_at=expected_end_at,
                result=QualityMonitoringCycle.Result.SUCCESS,
            )
            QualityOrder.objects.filter(pk=order.pk).update(completed_at=expected_end_at)
            order.completed_at = expected_end_at
            self._record_order_event(
                order, actor, QualityAuditEvent.EventType.MONITORING_SUCCEEDED,
                "Ciclo RREIP demonstrativo concluído com sucesso após 30 dias sem ocorrência válida.",
                {"cycle_number": cycle.cycle_number, "duration_days": QUALITY_RREIP_DAYS},
                occurred_at=expected_end_at,
            )

    def _create_demo_contacts(self, selected_links, semantics, actor):
        for index in (0, 2, 5):
            link = selected_links[index]
            provider = semantics[link.pk].provider_name
            project = display_project_name(link.business_project or link.legacy_project)
            contact = QualityContact.objects.create(
                company=provider,
                region=link.state,
                client=link.contracting_client,
                project=project,
                contact_type="QUALIDADE",
                name=f"DEMO · Escalonamento {link.contracting_client} / {project}",
                role="Contato demonstrativo local",
                notes=(
                    f"{QUALITY_DEMO_MARKER}\nRegistro sem pessoa, e-mail, telefone ou canal real. "
                    "Serve apenas para visualizar recomendações e filtros de contatos."
                ),
                escalation_level=(index % 3) + 1,
                active=True,
            )
            AuditService.record(
                user=actor,
                category="QUALITY",
                action="QUALITY_CONTACT_CREATED",
                module="Qualidade",
                description=f"{QUALITY_DEMO_MARKER} · contato demonstrativo criado localmente.",
                obj=contact,
                metadata={"seed": SEED_ID, "client": contact.client, "project": contact.project},
            )

    @staticmethod
    def _record_order_event(order, actor, event_type, description, metadata, *, occurred_at=None):
        safe_metadata = {"seed": SEED_ID, **metadata}
        local_event = QualityAuditEvent.objects.create(
            quality_order=order,
            event_type=event_type,
            actor=actor,
            description=f"{QUALITY_DEMO_MARKER} · {description}",
            metadata=safe_metadata,
        )
        audit_event = AuditService.record(
            user=actor,
            category="QUALITY",
            action=event_type,
            module="Qualidade",
            description=f"{QUALITY_DEMO_MARKER} · {description}",
            obj=order,
            metadata={"seed": SEED_ID, "code": order.code, **metadata},
        )
        if occurred_at:
            QualityAuditEvent.objects.filter(pk=local_event.pk).update(created_at=occurred_at)
            AuditEvent.objects.filter(pk=audit_event.pk).update(timestamp=occurred_at)
