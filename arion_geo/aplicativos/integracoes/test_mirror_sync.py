from datetime import datetime, timezone as datetime_timezone
from unittest.mock import patch
from django.test import TestCase as DjangoTestCase, override_settings

from .models import (
    CMDBLinkMirror, CMDBOperatorMirror, Integration, IntegrationRun,
    OTRSOperationalConfigItemSnapshot, OTRSOperationalSnapshot,
    OTRSOperationalTicketSnapshot, OTRSStateMapping, OTRSTicket,
    OTRSTicketConfigItem,
)
from .otrs_queues import (
    CANCELLATION_PROFILE_ACTIVE_STATES, CANCELLATION_REQUEST_QUEUE_NAME,
    COCKPIT_QUEUE_NAMES, NOC_QUEUE_NAME, queue_signature,
)
from .services.cmdb_sync import CMDBSyncService
from .services.otrs_sync import OTRSSyncService, _otrs_naive_local_datetime
from .services.otrs_snapshots import capture_daily_operational_snapshot


class FakeCMDBClient:
    batch_size = 50
    password = "DO_NOT_PERSIST_CMDB_SECRET"
    requests_count = 4

    def __init__(self):
        self.items = {
            "Operadoras": [
                {"ConfigItemID": "op-ci-7", "Number": "op-number-7", "Name": "Operadora Ágil", "Class": "Operadoras", "CIXMLData": {"CategoriaOperadora": "Regional", "TelefoneOperadora": "111", "SenhaPortalOperadora": self.password}},
            ],
            "Links de internet": [
                {"ConfigItemID": "link-ci-1", "Number": "36131000001", "Name": "Link Alpha", "Class": "Links de internet", "CreateTime": "2025-01-02 03:04:05", "CIXMLData": {"Cidade": "São Paulo", "Estado": "SP", "Circuito": "C-1", "ISP": "op-ci-7", "SenhaPPOE": "LINK_SECRET"}},
                {"ConfigItemID": "link-ci-2", "Number": "36131000002", "Name": "Link Beta", "Class": "Links de internet", "CIXMLData": {"ISP": "Operadora Ágil", "FutureField": "UNMAPPED_SECRET_VALUE"}},
                {"ConfigItemID": "link-ci-3", "Number": "36131000003", "Name": "Link Unknown", "Class": "Links de internet", "CIXMLData": {"ISP": "OPERADORA AGIL"}},
            ],
        }

    def search_item_ids(self, *, class_name, limit):
        self.last_search = (class_name, limit)
        return [item["ConfigItemID"] for item in self.items[class_name]]

    def get_items(self, identifiers):
        by_id = {item["ConfigItemID"]: item for rows in self.items.values() for item in rows}
        return [by_id[identifier] for identifier in identifiers]


class CMDBMirrorSyncTests(DjangoTestCase):
    def test_sync_resolves_isp_by_id_then_exact_name_and_never_persists_secrets(self):
        client = FakeCMDBClient()
        with self.assertLogs("aplicativos.integracoes.services.cmdb_sync", level="WARNING") as captured:
            result = CMDBSyncService(client).run(apply=True)
        self.assertEqual(result["operators"]["created"], 1)
        self.assertEqual(result["links"]["created"], 3)
        self.assertEqual(result["isp_resolution"]["CONFIG_ITEM_ID"], 1)
        self.assertEqual(result["isp_resolution"]["NAME"], 1)
        self.assertEqual(result["isp_resolution"]["UNRESOLVED"], 1)
        link_id = CMDBLinkMirror.objects.get(source_config_item_id="link-ci-1")
        self.assertEqual(link_id.operator.source_config_item_id, "op-ci-7")
        self.assertEqual(link_id.source_number, "36131000001")
        self.assertEqual(link_id.isp_raw, "op-ci-7")
        self.assertEqual(link_id.operator_resolution_method, CMDBLinkMirror.OperatorResolution.CONFIG_ITEM_ID)
        link_name = CMDBLinkMirror.objects.get(source_config_item_id="link-ci-2")
        self.assertEqual(link_name.operator_resolution_method, CMDBLinkMirror.OperatorResolution.NAME)
        self.assertEqual(CMDBLinkMirror.objects.get(source_config_item_id="link-ci-3").operator_id, None)
        values = list(CMDBOperatorMirror.objects.values()) + list(CMDBLinkMirror.objects.values())
        self.assertNotIn(client.password, str(values))
        self.assertNotIn("DO_NOT_PERSIST_PORTAL_USER", str(values))
        self.assertNotIn("LINK_SECRET", str(values))
        self.assertIn("futurefield", " ".join(captured.output))
        self.assertNotIn("UNMAPPED_SECRET_VALUE", " ".join(captured.output))
        self.assertFalse(any("password" in field.name.casefold() or "senha" in field.name.casefold() for field in CMDBOperatorMirror._meta.fields))
        self.assertFalse(any(field.name == "portal_username" for field in CMDBOperatorMirror._meta.fields))

    def test_dry_run_does_not_write_mirrors_and_searches_without_limit(self):
        client = FakeCMDBClient()
        result = CMDBSyncService(client).run(apply=False)
        self.assertEqual(client.last_search, ("Links de internet", None))
        self.assertEqual(result["links"]["created"], 3)
        self.assertEqual(CMDBLinkMirror.objects.count(), 0)
        self.assertEqual(CMDBOperatorMirror.objects.count(), 0)

    def test_incomplete_fetch_and_duplicate_numbers_fail_before_reconciliation(self):
        CMDBSyncService(FakeCMDBClient()).run(apply=True)
        original_count = CMDBLinkMirror.objects.count()
        class MissingGet(FakeCMDBClient):
            def get_items(self, identifiers):
                return []
        with self.assertRaises(ValueError):
            CMDBSyncService(MissingGet()).run(apply=True)
        self.assertEqual(CMDBLinkMirror.objects.count(), original_count)
        self.assertEqual(CMDBLinkMirror.objects.filter(is_present_in_source=True).count(), original_count)
        client = FakeCMDBClient()
        client.items["Links de internet"][1]["Number"] = "36131000001"
        with self.assertRaises(ValueError):
            CMDBSyncService(client).run(apply=True)
        self.assertEqual(CMDBLinkMirror.objects.count(), original_count)

    def test_updates_unchanged_and_complete_run_stale_reconciliation(self):
        client = FakeCMDBClient()
        CMDBSyncService(client).run(apply=True)
        unchanged = CMDBSyncService(client).run(apply=True)
        self.assertEqual(unchanged["operators"]["unchanged"], 1)
        self.assertEqual(unchanged["links"]["unchanged"], 3)

        client.items["Operadoras"][0]["CIXMLData"]["TelefoneOperadora"] = "222"
        client.items["Links de internet"][0]["CIXMLData"]["Circuito"] = "C-UPDATED"
        updated = CMDBSyncService(client).run(apply=True)
        self.assertEqual(updated["operators"]["updated"], 1)
        self.assertEqual(updated["links"]["updated"], 1)
        self.assertEqual(CMDBLinkMirror.objects.get(source_config_item_id="link-ci-1").circuit, "C-UPDATED")

        client.items["Links de internet"].pop()
        reconciled = CMDBSyncService(client).run(apply=True)
        self.assertEqual(reconciled["links"]["stale"], 1)
        self.assertFalse(CMDBLinkMirror.objects.get(source_config_item_id="link-ci-3").is_present_in_source)
        self.assertTrue(CMDBLinkMirror.objects.filter(source_config_item_id="link-ci-3").exists())


class FakeOTRSReadOnlyClient:
    def __init__(self, *, states=None, relations=None, queue_id=107, queues=None):
        self.select_queries_count = 0
        self.states = states or [{"id": 10, "name": "em atendimento", "type_id": 1}]
        self.relations = relations if relations is not None else [
            {"source_object_id": 4, "source_key": "ci-1", "target_object_id": 1, "target_key": "100", "type_id": 3, "state_id": 1, "create_time": datetime(2025, 2, 3, tzinfo=datetime_timezone.utc)},
            {"source_object_id": 4, "source_key": "ci-bad-state", "target_object_id": 1, "target_key": "100", "type_id": 3, "state_id": 2, "create_time": None},
            {"source_object_id": 4, "source_key": "ci-bad-type", "target_object_id": 1, "target_key": "100", "type_id": 4, "state_id": 1, "create_time": None},
        ]
        self.ticket = {"id": 100, "tn": "2026000000000100", "title": "Link service", "queue_id": queue_id, "state_id": self.states[0]["id"], "priority_id": 2, "type_id": 3, "service_id": 4, "sla_id": 5, "responsible_user_id": 13, "customer_id": "ACME", "timeout": 12, "until_time": 20, "escalation_time": -2, "escalation_update_time": 3, "escalation_response_time": 4, "escalation_solution_time": 5, "create_time": datetime(2025, 1, 1, tzinfo=datetime_timezone.utc), "change_time": datetime(2025, 1, 2)}
        self.ticket_rows = [self.ticket]
        self.queues = queues or [
            {"id": queue_id, "name": NOC_QUEUE_NAME},
            {"id": 108, "name": CANCELLATION_REQUEST_QUEUE_NAME},
            {"id": 109, "name": "NOC - RETIRA EQUIPAMENTO"},
            {"id": 110, "name": "NOC \u2013 PENDENTE PAGAMENTO"},
            {"id": 222, "name": "OUTSIDE QUEUE"},
        ]
        self.last_changed_since = None
        self.last_changed_since_parameter = None
        self.dynamic_values = []
        self.history_rows = []

    def get_all_columns(self, database=None):
        names = {
            "ticket": "id tn title queue_id ticket_state_id ticket_priority_id type_id service_id sla_id responsible_user_id customer_id timeout until_time escalation_time escalation_update_time escalation_response_time escalation_solution_time create_time change_time",
            "queue": "id name", "ticket_state": "id name type_id", "ticket_state_type": "id name",
            "ticket_priority": "id name", "ticket_type": "id name", "service": "id name", "sla": "id name",
            "link_relation": "source_object_id source_key target_object_id target_key type_id state_id create_time",
            "link_object": "id name", "link_type": "id name", "link_state": "id name",
            "dynamic_field": "id name field_type object_type", "dynamic_field_value": "object_id field_id value_text value_date",
            "ticket_history": "ticket_id state_id history_type_id create_time", "ticket_history_type": "id name",
        }
        return [{"TABLE_NAME": table, "COLUMN_NAME": col} for table, fields in names.items() for col in fields.split()]

    def execute_select(self, sql, params=()):
        self.select_queries_count += 1
        normalized = sql.casefold()
        if "from `queue`" in normalized:
            if " = %s" in normalized:
                return [row for row in self.queues if row["name"] == params[0]]
            if " in (" in normalized:
                selected_ids = set(params)
                return [row for row in self.queues if row["id"] in selected_ids]
            return list(self.queues)
        if "from `ticket_state_type`" in normalized:
            return [{"id": 1, "name": "open"}, {"id": 2, "name": "pending auto"}]
        if "from `dynamic_field`" in normalized:
            return [{"id": 101, "name": "CodigoFechamento", "field_type": "Dropdown", "object_type": "Ticket"}, {"id": 27, "name": "ticketResolvido", "field_type": "DateTime", "object_type": "Ticket"}, {"id": 129, "name": "RvalidacaoProcedenteImprocedente", "field_type": "Dropdown", "object_type": "Ticket"}]
        if "from `dynamic_field_value`" in normalized:
            ids = set(params[3:])
            fields = set(params[:3])
            return [row for row in self.dynamic_values if row["object_id"] in ids and row["field_id"] in fields]
        if "from `ticket_history_type`" in normalized:
            return [{"id": 27, "name": "StateUpdate"}]
        if "from `ticket_history`" in normalized:
            terminal_state_ids = {state["id"] for state in self.states if state["name"].casefold() in {"resolvido", "closed successful", "encerrado com viabilidade", "cancelado"}}
            ticket_ids = set(params[1 + len(terminal_state_ids):])
            return [row for row in self.history_rows if row["ticket_id"] in ticket_ids and row["state_id"] in terminal_state_ids]
        if "from `ticket_state`" in normalized:
            return self.states
        if "from `ticket_priority`" in normalized:
            return [{"id": 2, "name": "Alto"}]
        if "from `ticket_type`" in normalized:
            return [{"id": 3, "name": "Solicitação"}]
        if "from `service`" in normalized:
            return [{"id": 4, "name": "Internet"}]
        if "from `sla`" in normalized:
            return [{"id": 5, "name": "Padrão"}]
        if "from `ticket`" in normalized:
            if " >= %s" in normalized:
                first_in = normalized.split(" in (", 1)[1].split(")", 1)[0]
                queue_ids_count = first_in.count("%s")
                queue_ids = set(params[:queue_ids_count])
                self.last_changed_since_parameter = params[-1]
                cutoff = params[-1]
                # PyMySQL serializes aware datetime fields as wall time, dropping tzinfo.
                if cutoff.tzinfo is not None:
                    cutoff = cutoff.replace(tzinfo=None)
                self.last_changed_since = cutoff
                known_ids = set(params[queue_ids_count:-1])
                rows = [row for row in self.ticket_rows
                        if (row["queue_id"] in queue_ids or row["id"] in known_ids)
                        and row["change_time"] >= cutoff]
            else:
                queue_ids = set(params)
                rows = [row for row in self.ticket_rows if row["queue_id"] in queue_ids]
            return [dict(row) for row in rows]
        if "from `link_object`" in normalized:
            return [{"id": 1, "name": "Ticket"}, {"id": 4, "name": "ITSMConfigItem"}]
        if "from `link_type`" in normalized:
            return [{"id": 3, "name": "DependsOn"}, {"id": 4, "name": "AlternativeTo"}]
        if "from `link_state`" in normalized:
            return [{"id": 1, "name": "Valid"}, {"id": 2, "name": "Invalid"}]
        if "from `link_relation`" in normalized:
            return self.relations
        raise AssertionError(sql)


class OTRSMirrorSyncTests(DjangoTestCase):
    def setUp(self):
        self.operator = CMDBOperatorMirror.objects.create(source_config_item_id="op-1", source_number="operator-number", name="Operadora")
        CMDBLinkMirror.objects.create(source_config_item_id="ci-1", source_number="36131000001", name="Link local", isp_raw="op-1", operator=self.operator)

    def test_full_snapshot_is_immutable_per_day_and_deduplicates_ticket_and_config_item(self):
        from datetime import datetime, timezone as utc

        link = CMDBLinkMirror.objects.get(source_config_item_id="ci-1")
        link.state = "SP"
        link.business_project = "Projeto Snapshot"
        link.contracting_client = "Cliente Snapshot"
        link.save(update_fields=("state", "business_project", "contracting_client"))
        ticket = OTRSTicket.objects.create(
            external_id=900, ticket_number="SNAP-900", queue_external_id=107,
            queue_name=NOC_QUEUE_NAME, external_state_id=10,
            external_state_name="em atendimento", business_status="OPEN",
            is_business_open=True, is_present_in_queue=True,
            source_created_at=datetime(2026, 3, 2, 9, tzinfo=utc.utc),
        )
        OTRSTicketConfigItem.objects.create(
            ticket=ticket, config_item_id="ci-1", cmdb_link=link,
            relation_type="DependsOn", relation_state="Valid",
        )
        integration = Integration.objects.create(
            key=Integration.Key.OTRS, name="OTRS", kind="mysql",
        )
        signature = queue_signature(COCKPIT_QUEUE_NAMES)
        first_run = IntegrationRun.objects.create(
            integration=integration, operation=IntegrationRun.Operation.OTRS_FULL_SYNC,
            status=IntegrationRun.Status.SUCCESS,
            message=f"Apply; queues={signature}; tickets=1",
            finished_at=datetime(2026, 3, 2, 12, tzinfo=utc.utc),
        )

        first = capture_daily_operational_snapshot(first_run, COCKPIT_QUEUE_NAMES)
        ticket.business_status = "RESOLVED"
        ticket.save(update_fields=("business_status",))
        later_run = IntegrationRun.objects.create(
            integration=integration, operation=IntegrationRun.Operation.OTRS_FULL_SYNC,
            status=IntegrationRun.Status.SUCCESS,
            message=f"Apply; queues={signature}; tickets=1",
            finished_at=datetime(2026, 3, 2, 18, tzinfo=utc.utc),
        )
        repeated = capture_daily_operational_snapshot(later_run, COCKPIT_QUEUE_NAMES)

        self.assertTrue(first["created"])
        self.assertFalse(repeated["created"])
        self.assertEqual(OTRSOperationalSnapshot.objects.count(), 1)
        self.assertEqual(OTRSOperationalSnapshot.objects.get().ticket_count, 1)
        self.assertEqual(OTRSOperationalSnapshot.objects.get().backlog_count, 1)
        historical_ticket = OTRSOperationalTicketSnapshot.objects.get()
        self.assertEqual(historical_ticket.business_status, "OPEN")
        historical_link = OTRSOperationalConfigItemSnapshot.objects.get()
        self.assertEqual(historical_link.provider_name, "Operadora")
        self.assertEqual(historical_link.uf, "SP")
        self.assertEqual(historical_link.project, "Projeto Snapshot")
        self.assertEqual(historical_link.client, "Cliente Snapshot")
        fast_run = IntegrationRun.objects.create(
            integration=integration, operation=IntegrationRun.Operation.OTRS_FAST_SYNC,
            status=IntegrationRun.Status.SUCCESS, message=f"Apply; queues={signature}",
        )
        self.assertEqual(
            capture_daily_operational_snapshot(fast_run, COCKPIT_QUEUE_NAMES)["reason"],
            "run_not_eligible",
        )

    def test_explicit_business_status_rule_and_unknown_is_not_open(self):
        cases = {
            "new": (OTRSStateMapping.BusinessStatus.OPEN, True),
            "merged": (OTRSStateMapping.BusinessStatus.RESOLVED, False),
            "em atendimento": (OTRSStateMapping.BusinessStatus.OPEN, True),
            "Pendente": (OTRSStateMapping.BusinessStatus.PENDING, True),
            "pendente operadora": (OTRSStateMapping.BusinessStatus.PENDING, True),
            "pendente aprova\u00e7\u00e3o": (OTRSStateMapping.BusinessStatus.PENDING, True),
            "aguardando aprova\u00e7\u00e3o do gestor": (OTRSStateMapping.BusinessStatus.PENDING, True),
            "Projeto Especial": (OTRSStateMapping.BusinessStatus.OPEN, True),
            "agendado": (OTRSStateMapping.BusinessStatus.SCHEDULED, True),
            "resolvido": (OTRSStateMapping.BusinessStatus.RESOLVED, False),
            "closed successful": (OTRSStateMapping.BusinessStatus.RESOLVED, False),
            "cancelado": (OTRSStateMapping.BusinessStatus.CANCELED, False),
            "Encerrado com viabilidade": (OTRSStateMapping.BusinessStatus.RESOLVED, False),
            "Estado Novo Qualquer": (OTRSStateMapping.BusinessStatus.UNKNOWN, False),
        }
        for name, (expected, is_open) in cases.items():
            with self.subTest(name=name):
                status = OTRSSyncService._state_status(name)
                self.assertEqual(status, expected)
                self.assertEqual(status in {"OPEN", "PENDING", "SCHEDULED"}, is_open)

        for name in CANCELLATION_PROFILE_ACTIVE_STATES:
            with self.subTest(profile_state=name):
                self.assertNotEqual(
                    OTRSSyncService._state_status(name),
                    OTRSStateMapping.BusinessStatus.UNKNOWN,
                )

    def test_snapshot_upserts_dimensions_and_relations_and_excludes_invalid_relationships(self):
        client = FakeOTRSReadOnlyClient()
        result = OTRSSyncService(client).run(apply=True)
        ticket = OTRSTicket.objects.get(external_id=100)
        relation = OTRSTicketConfigItem.objects.get(ticket=ticket)
        self.assertEqual(ticket.ticket_number, "2026000000000100")
        self.assertEqual(ticket.business_status, OTRSStateMapping.BusinessStatus.OPEN)
        self.assertTrue(ticket.is_business_open)
        self.assertEqual(ticket.title, "Link service")
        self.assertEqual(ticket.customer_id, "ACME")
        self.assertEqual(relation.config_item_id, "ci-1")
        self.assertEqual(relation.cmdb_link_id, CMDBLinkMirror.objects.get(source_config_item_id="ci-1").pk)
        self.assertEqual(result["relations"]["found"], 1)
        self.assertEqual(result["business_open"], 1)
        self.assertEqual(result["business_status"]["UNKNOWN"], 0)

    def test_multi_queue_snapshot_imports_cockpit_queues_and_reconciles_only_that_scope(self):
        client = FakeOTRSReadOnlyClient(relations=[])
        cancellation_ticket = dict(client.ticket)
        cancellation_ticket.update(
            id=200, tn="2026000000000200", title="Solicitação de cancelamento",
            queue_id=108,
        )
        client.ticket_rows.append(cancellation_ticket)
        unrelated = OTRSTicket.objects.create(
            external_id=999, ticket_number="2026000000000999", queue_external_id=999,
            queue_name="OUTRA FILA", external_state_id=1,
            external_state_name="em atendimento", is_present_in_queue=True,
        )
        stale = OTRSTicket.objects.create(
            external_id=300, ticket_number="2026000000000300", queue_external_id=109,
            queue_name="NOC - RETIRA EQUIPAMENTO", external_state_id=1,
            external_state_name="em atendimento", is_present_in_queue=True,
        )
        stale_relation = OTRSTicketConfigItem.objects.create(
            ticket=stale, config_item_id="ci-stale", relation_type="DependsOn",
            relation_state="Valid", is_present_in_source=True,
        )

        result = OTRSSyncService(client).run(queue_names=COCKPIT_QUEUE_NAMES, apply=True)

        imported = OTRSTicket.objects.get(external_id=200)
        stale.refresh_from_db()
        stale_relation.refresh_from_db()
        unrelated.refresh_from_db()
        self.assertEqual(imported.queue_name, CANCELLATION_REQUEST_QUEUE_NAME)
        self.assertTrue(imported.is_present_in_queue)
        self.assertFalse(stale.is_present_in_queue)
        self.assertFalse(stale_relation.is_present_in_source)
        self.assertTrue(unrelated.is_present_in_queue)
        self.assertEqual(result["tickets"]["created"], 2)
        self.assertEqual(result["tickets"]["stale"], 1)
        self.assertEqual([queue["name"] for queue in result["queues"]], list(COCKPIT_QUEUE_NAMES))

    def test_duplicate_source_relations_are_counted_but_reconciled_once(self):
        relation = {"source_object_id": 4, "source_key": "ci-1", "target_object_id": 1, "target_key": "100", "type_id": 3, "state_id": 1, "create_time": None}
        client = FakeOTRSReadOnlyClient(relations=[dict(relation), dict(relation)])

        result = OTRSSyncService(client).run(apply=True)

        self.assertEqual(result["relations"]["found"], 2)
        self.assertEqual(result["relations"]["processed"], 1)
        self.assertEqual(result["relations"]["created"], 1)
        self.assertEqual(result["relations"]["updated"], 0)
        self.assertEqual(result["relations"]["unchanged"], 0)
        self.assertEqual(result["relation_stats"]["tickets_one_ci"], 1)
        self.assertEqual(OTRSTicketConfigItem.objects.count(), 1)

    def test_stale_existing_relation_is_reactivated_by_canonical_key(self):
        OTRSSyncService(FakeOTRSReadOnlyClient(relations=[])).run(apply=True)
        ticket = OTRSTicket.objects.get(external_id=100)
        relation = OTRSTicketConfigItem.objects.create(
            ticket=ticket, config_item_id="CI-1", relation_type="dependson",
            relation_state="Invalid", is_present_in_source=False,
        )

        result = OTRSSyncService(FakeOTRSReadOnlyClient()).run(apply=True)

        relation.refresh_from_db()
        self.assertEqual(result["relations"]["found"], 1)
        self.assertEqual(result["relations"]["processed"], 1)
        self.assertEqual(result["relations"]["created"], 0)
        self.assertEqual(result["relations"]["updated"], 1)
        self.assertTrue(relation.is_present_in_source)
        self.assertEqual(relation.config_item_id, "ci-1")
        self.assertEqual(relation.relation_type, "DependsOn")
        self.assertEqual(relation.relation_state, "Valid")
        self.assertEqual(relation.cmdb_link_id, CMDBLinkMirror.objects.get(source_config_item_id="ci-1").pk)
        self.assertEqual(OTRSTicketConfigItem.objects.count(), 1)

    def test_resolved_state_is_never_business_open_and_queue_departure_is_stale_not_closed(self):
        resolved_client = FakeOTRSReadOnlyClient(states=[{"id": 20, "name": "resolvido", "type_id": 2}])
        resolved_client.ticket["state_id"] = 20
        OTRSSyncService(resolved_client).run(apply=True)
        ticket = OTRSTicket.objects.get(external_id=100)
        self.assertEqual(ticket.business_status, OTRSStateMapping.BusinessStatus.RESOLVED)
        self.assertFalse(ticket.is_business_open)

        empty_client = FakeOTRSReadOnlyClient(relations=[])
        # Return a complete but empty queue snapshot; prior ticket leaves this queue without deletion.
        service = OTRSSyncService(empty_client)
        original_select = empty_client.execute_select
        def empty_ticket(sql, params=()):
            if "from `ticket`" in sql.casefold():
                empty_client.select_queries_count += 1
                return []
            return original_select(sql, params)
        empty_client.execute_select = empty_ticket
        outcome = service.run(apply=True)
        ticket.refresh_from_db()
        self.assertEqual(outcome["tickets"]["stale"], 1)
        self.assertFalse(ticket.is_present_in_queue)
        self.assertFalse(ticket.is_business_open)
        self.assertTrue(OTRSTicket.objects.filter(pk=ticket.pk).exists())
        relation = OTRSTicketConfigItem.objects.get(ticket=ticket)
        self.assertFalse(relation.is_present_in_source)

    def test_wrong_queue_name_and_reverse_relation_keep_external_ci_without_local_link(self):
        with self.assertRaises(ValueError):
            OTRSSyncService(FakeOTRSReadOnlyClient()).run("Another queue", apply=True)
        reverse = [{"source_object_id": 1, "source_key": "100", "target_object_id": 4, "target_key": "ci-not-local", "type_id": 3, "state_id": 1, "create_time": None}]
        result = OTRSSyncService(FakeOTRSReadOnlyClient(relations=reverse)).run(apply=True)
        association = OTRSTicketConfigItem.objects.get(config_item_id="ci-not-local")
        self.assertIsNone(association.cmdb_link_id)
        self.assertEqual(result["relations"]["unresolved_link"], 1)
        self.assertEqual(result["relation_stats"]["unresolved_local_links"], 1)


    def test_fast_sync_inserts_new_queue_ticket_and_supports_multiple_config_items(self):
        client = FakeOTRSReadOnlyClient(relations=[
            {"source_object_id": 4, "source_key": "ci-1", "target_object_id": 1, "target_key": "100", "type_id": 3, "state_id": 1, "create_time": None},
            {"source_object_id": 4, "source_key": "ci-2", "target_object_id": 1, "target_key": "100", "type_id": 3, "state_id": 1, "create_time": None},
        ])
        CMDBLinkMirror.objects.create(source_config_item_id="ci-2", source_number="36131000002", name="Link 2", operator=self.operator)
        result = OTRSSyncService(client).run(apply=True, changed_since=datetime(2025, 1, 2, tzinfo=datetime_timezone.utc))
        ticket = OTRSTicket.objects.get(external_id=100)
        self.assertTrue(ticket.is_present_in_queue)
        self.assertEqual(result["tickets"]["created"], 1)
        self.assertEqual(ticket.config_items.filter(is_present_in_source=True).count(), 2)
        self.assertEqual(result["relation_stats"]["tickets_multiple_ci"], 1)

    @override_settings(TIME_ZONE="America/Sao_Paulo", USE_TZ=True)
    def test_fast_converts_utc_cursor_to_naive_otrs_local_datetime(self):
        cursor = datetime(2026, 10, 26, 13, 0, tzinfo=datetime_timezone.utc)
        self.assertEqual(_otrs_naive_local_datetime(cursor), datetime(2026, 10, 26, 10, 0))

    @override_settings(TIME_ZONE="America/Sao_Paulo", USE_TZ=True)
    def test_fast_finds_new_ticket_after_cursor_without_config_item(self):
        client = FakeOTRSReadOnlyClient(relations=[])
        client.ticket["change_time"] = datetime(2026, 10, 26, 10, 5)
        cursor = datetime(2026, 10, 26, 13, 2, tzinfo=datetime_timezone.utc)

        result = OTRSSyncService(client).run(apply=True, changed_since=cursor)

        self.assertEqual(client.last_changed_since_parameter, datetime(2026, 10, 26, 10, 0))
        self.assertEqual(result["tickets"]["found"], 1)
        self.assertEqual(result["tickets"]["created"], 1)
        self.assertTrue(OTRSTicket.objects.get(external_id=100).is_present_in_queue)
        self.assertEqual(result["relations"]["found"], 0)

    @override_settings(TIME_ZONE="America/Sao_Paulo", USE_TZ=True)
    def test_fast_safety_window_recovers_ticket_changed_before_cursor(self):
        client = FakeOTRSReadOnlyClient(relations=[])
        client.ticket["change_time"] = datetime(2026, 10, 26, 10, 4)
        cursor = datetime(2026, 10, 26, 13, 5, tzinfo=datetime_timezone.utc)

        result = OTRSSyncService(client).run(apply=True, changed_since=cursor, safety_window=120)

        self.assertEqual(client.last_changed_since_parameter, datetime(2026, 10, 26, 10, 3))
        self.assertEqual(result["tickets"]["found"], 1)
        self.assertEqual(result["tickets"]["created"], 1)

    def test_fast_repeated_delta_is_idempotent(self):
        client = FakeOTRSReadOnlyClient(relations=[])
        cursor = datetime(2025, 1, 2, tzinfo=datetime_timezone.utc)

        first = OTRSSyncService(client).run(apply=True, changed_since=cursor)
        second = OTRSSyncService(client).run(apply=True, changed_since=cursor)

        self.assertEqual(first["tickets"]["created"], 1)
        self.assertEqual(second["tickets"]["created"], 0)
        self.assertEqual(second["tickets"]["unchanged"], 1)
        self.assertEqual(OTRSTicket.objects.filter(external_id=100).count(), 1)

    def test_fast_sync_reconciles_ticket_leaving_queue_without_deleting_it(self):
        client = FakeOTRSReadOnlyClient()
        OTRSSyncService(client).run(apply=True)
        client.ticket["queue_id"] = 222
        client.relations = []
        result = OTRSSyncService(client).run(apply=True, changed_since=datetime(2025, 1, 2, tzinfo=datetime_timezone.utc))
        ticket = OTRSTicket.objects.get(external_id=100)
        self.assertEqual(result["tickets"]["updated"], 1)
        self.assertFalse(ticket.is_present_in_queue)
        self.assertTrue(OTRSTicket.objects.filter(pk=ticket.pk).exists())
        self.assertFalse(ticket.config_items.get().is_present_in_source)

    def test_fast_sync_adds_and_removes_only_affected_ticket_relations(self):
        client = FakeOTRSReadOnlyClient(relations=[])
        OTRSSyncService(client).run(apply=True)
        cursor = datetime(2025, 1, 2, tzinfo=datetime_timezone.utc)
        client.relations = [{"source_object_id": 4, "source_key": "ci-1", "target_object_id": 1, "target_key": "100", "type_id": 3, "state_id": 1, "create_time": None}]
        added = OTRSSyncService(client).run(apply=True, changed_since=cursor)
        relation = OTRSTicketConfigItem.objects.get(ticket__external_id=100)
        self.assertEqual(added["relations"]["created"], 1)
        self.assertTrue(relation.is_present_in_source)
        client.relations = []
        removed = OTRSSyncService(client).run(apply=True, changed_since=cursor)
        relation.refresh_from_db()
        self.assertEqual(removed["relations"]["stale"], 1)
        self.assertFalse(relation.is_present_in_source)

    def test_empty_fast_delta_advances_cursor_without_loading_all_local_tickets(self):
        client = FakeOTRSReadOnlyClient(relations=[])
        original_select = client.execute_select

        def no_changed_tickets(sql, params=()):
            if "from `ticket`" in sql.casefold():
                client.select_queries_count += 1
                return []
            return original_select(sql, params)

        client.execute_select = no_changed_tickets
        cursor = datetime(2025, 1, 2, tzinfo=datetime_timezone.utc)
        with patch.object(OTRSTicket.objects, "all", side_effect=AssertionError("FAST must not load every local ticket")):
            result = OTRSSyncService(client).run(apply=True, changed_since=cursor)
        self.assertEqual(result["tickets"]["found"], 0)
        self.assertGreater(result["cursor"], cursor)

    def test_unknown_state_is_stored_unknown_and_closed_for_business_open(self):
        client = FakeOTRSReadOnlyClient(states=[{"id": 90, "name": "Estado Novo Qualquer", "type_id": 2}], relations=[])
        client.ticket["state_id"] = 90
        result = OTRSSyncService(client).run(apply=True)
        ticket = OTRSTicket.objects.get(external_id=100)
        self.assertEqual(ticket.business_status, OTRSStateMapping.BusinessStatus.UNKNOWN)
        self.assertFalse(ticket.is_business_open)
        self.assertTrue(result["warnings"])

    def test_full_sync_imports_reason_and_terminal_history_date(self):
        at = datetime(2026, 10, 12, 10, tzinfo=datetime_timezone.utc)
        client = FakeOTRSReadOnlyClient(states=[{"id": 20, "name": "resolvido", "type_id": 2}], relations=[])
        client.ticket["state_id"] = 20
        client.dynamic_values = [
            {"object_id": 100, "field_id": 101, "value_text": "PendenciaFinanceira", "value_date": None},
            {"object_id": 100, "field_id": 27, "value_text": None, "value_date": at},
            {"object_id": 100, "field_id": 129, "value_text": "Improcedente", "value_date": None},
        ]
        client.history_rows = [{"ticket_id": 100, "state_id": 20, "terminal_at": at}]
        result = OTRSSyncService(client).run(apply=True)
        ticket = OTRSTicket.objects.get(external_id=100)
        self.assertEqual(ticket.closing_code_raw, "PendenciaFinanceira")
        self.assertEqual(ticket.resolved_at, at)
        self.assertEqual(ticket.terminal_state_at, at)
        self.assertEqual(ticket.responsibility_raw, "Improcedente")
        self.assertEqual(ticket.responsibility_normalized, "IMPROCEDENTE")
        self.assertEqual(result["ticket_terminal_fields"]["terminal_at_filled"], 1)
        self.assertEqual(result["ticket_terminal_fields"]["responsibility_filled"], 1)

    def test_unrecognized_responsibility_keeps_raw_value_and_maps_to_indeterminate(self):
        client = FakeOTRSReadOnlyClient()
        client.dynamic_values = [{"object_id": 100, "field_id": 129, "value_text": "Em análise", "value_date": None}]
        OTRSSyncService(client).run(apply=True)
        ticket = OTRSTicket.objects.get(external_id=100)
        self.assertEqual(ticket.responsibility_raw, "Em análise")
        self.assertEqual(ticket.responsibility_normalized, "INDETERMINADO")

    def test_fast_sync_updates_reason_and_empty_delta_is_idempotent(self):
        OTRSSyncService(FakeOTRSReadOnlyClient()).run(apply=True)
        cursor = datetime(2025, 1, 2, tzinfo=datetime_timezone.utc)
        client = FakeOTRSReadOnlyClient()
        client.dynamic_values = [{"object_id": 100, "field_id": 101, "value_text": "PendenciaFinanceira", "value_date": None}]
        updated = OTRSSyncService(client).run(apply=True, changed_since=cursor)
        self.assertEqual(updated["tickets"]["updated"], 1)
        self.assertEqual(OTRSTicket.objects.get(external_id=100).closing_code_raw, "PendenciaFinanceira")
        empty = FakeOTRSReadOnlyClient()
        empty.execute_select = lambda sql, params=(): [] if "from `ticket`" in sql.casefold() else FakeOTRSReadOnlyClient.execute_select(empty, sql, params)
        result = OTRSSyncService(empty).run(apply=True, changed_since=cursor)
        self.assertEqual(result["tickets"]["found"], 0)
        self.assertEqual(result["ticket_terminal_fields"]["closing_code_filled"], 0)

