"""Allowlisted presentation of immutable source facts after process scope checks."""
from .legacy_import import SOURCE
from .legacy_people import names_for


GROUPS = (
    ("Identidade histórica", (("id", "ID ArionBroker"), ("ticket_arion", "Ticket original"),
        ("fila", "Fila original"), ("status", "Status detalhado original"), ("cod_cliente", "Código do cliente"),
        ("responsavel_id", "Responsável legado — ID"), ("atendente_id", "Atendente legado — ID"))),
    ("Datas da origem", (("data_criacao_chamado", "Abertura do chamado (valor bruto)"), ("criado_em", "Cadastro no ArionBroker"),
        ("atualizado_em", "Última atualização no ArionBroker"), ("concluido_em", "Conclusão registrada no legado"),
        ("apropriado_em", "Apropriação"), ("agendamento", "Agendamento"), ("data_cancelamento_confirmado", "Data de confirmação no legado"),
        ("data_primeiro_contato", "Primeiro contato"), ("data_proxima_acao", "Data da próxima ação"), ("data_ultima_interacao", "Última interação"))),
    ("Contato e localização históricos", (("cliente", "Cliente original"), ("cliente_final", "Cliente final original"),
        ("designacao_circuito", "Circuito original"), ("isp", "ISP original"), ("contato_isp", "Contato ISP"),
        ("protocolo", "Protocolo"), ("canal_contato", "Canal de contato"), ("proxima_acao", "Próxima ação"),
        ("endereco", "Endereço"), ("numero", "Número"), ("complemento", "Complemento"), ("bairro", "Bairro"),
        ("cep", "CEP"), ("cidade", "Cidade"), ("uf", "UF"), ("ticket_url", "URL histórica (somente texto)"))),
)
FINANCIAL_FIELDS = (("valor_multa_rescisao", "Multa de rescisão informada"), ("valor_equipamento", "Valor de equipamentos informado"),
    ("valor_outros", "Outros valores informados"), ("confirmacao_sem_nova_cobranca", "Confirmação sem nova cobrança — valor legado"),
    ("data_baixa_financeira", "Baixa financeira no legado (não comprova pagamento)"),
    ("ultima_competencia_faturada", "Última competência faturada"), ("observacao_financeira", "Observação financeira original"),
    ("observacao", "Observação principal original"))


def legacy_context(process, user):
    reference = process.legacy_references.select_related("import_run").filter(source=SOURCE, entity="cancelamento").first()
    if reference is None:
        return None
    data = reference.source_data
    names = names_for([v for v in (data.get("responsavel_id"), data.get("atendente_id")) if v is not None])
    groups = [{"title": title, "fields": [(label, data.get(key)) for key, label in fields]} for title, fields in GROUPS]
    if user.has_perm("cancelamentos.view_cancellation_finance"):
        groups.append({"title": "Informações e observações históricas restritas", "fields": [(label, data.get(key)) for key, label in FINANCIAL_FIELDS]})
    return {"id": reference.legacy_pk, "groups": groups, "batch": reference.batch,
            "responsible_id": data.get("responsavel_id"),
            "responsible_name": names.get(data.get("responsavel_id"), "Nome não disponível"),
            "attendant_name": names.get(data.get("atendente_id"), "Nome não disponível"),
            "contact_channel": data.get("canal_contato"), "first_contact": data.get("data_primeiro_contato"),
            "last_contact": data.get("data_ultima_interacao"), "next_action": data.get("proxima_acao"),
            "address": " ".join(str(data.get(k) or "") for k in ("endereco", "numero", "complemento", "bairro", "cep")).strip(),
            "observation": data.get("observacao") if user.has_perm("cancelamentos.view_cancellation_finance") else "",
            "opening_needs_review": reference.reconciliation.get("opening_date_needs_review"),
            "completion_needs_review": reference.reconciliation.get("historical_completion_needs_review"),
            "otrs_matched": reference.reconciliation.get("otrs_matched"),
            "source_timezone": reference.import_run.counts.get("source_timezone", "Consultar relatório do lote") if reference.import_run_id else "Consultar relatório do lote"}


EVENT_TITLES = {
    "legacy_reconcile": "Estado histórico reconciliado com o ArionBroker",
    "legacy_historicofila": "Etapa alterada", "legacy_observacaocancelamento": "Observação registrada",
    "legacy_historicocancelamento": "Atualização do atendimento", "pending_add": "Pendência adicionada",
    "pending_update": "Pendência atualizada", "pending_resolve": "Pendência resolvida ou encaminhada",
    "assign": "Responsável alterado", "assume": "Atendimento assumido", "note": "Observação registrada",
    "move": "Etapa alterada", "start": "Tratativa iniciada", "status": "Status alterado",
    "treatment": "Tratativa com o ISP registrada", "complete": "Atendimento concluído", "reopen": "Atendimento reaberto",
    "triage_approve": "Pedido aprovado", "triage_reject": "Entrada rejeitada", "triage_wait": "Análise mantida pendente",
    "link": "Vínculo CMDB atualizado",
    "equipment_add": "Equipamento adicionado", "equipment_update": "Equipamento atualizado",
    "obligation_add": "Cobrança adicionada", "obligation_update": "Cobrança atualizada",
    "update": "Dados atualizados", "handoff": "Encaminhado ao Financeiro", "evidence": "Evidência registrada",
}


def present_events(events):
    events = list(events)
    names = names_for([event.details["legacy_actor_id"] for event in events if event.details.get("legacy_actor_id") is not None])
    for event in events:
        historical = event.action.startswith("legacy_")
        event.display_actor = names.get(event.details.get("legacy_actor_id"), "Nome não disponível") if historical else (
            (event.actor.get_full_name() or event.actor.get_username()) if event.actor else "Nome não disponível")
        event.display_title = EVENT_TITLES.get(event.action, "Atendimento atualizado")
        kind = str(event.details.get("legacy_type", ""))
        if historical and "PENDENCIA" in kind:
            event.display_title = "Pendência adicionada" if "CRIAD" in kind else "Pendência atualizada"
        elif historical and "CONCLU" in kind:
            event.display_title = "Atendimento concluído"
    return events
