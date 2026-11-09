"""Allowlisted presentation of immutable source facts after process scope checks."""
from .legacy_import import SOURCE


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
    groups = [{"title": title, "fields": [(label, data.get(key)) for key, label in fields]} for title, fields in GROUPS]
    if user.has_perm("cancelamentos.view_cancellation_finance"):
        groups.append({"title": "Informações e observações históricas restritas", "fields": [(label, data.get(key)) for key, label in FINANCIAL_FIELDS]})
    return {"id": reference.legacy_pk, "groups": groups, "batch": reference.batch,
            "responsible_id": data.get("responsavel_id"),
            "opening_needs_review": reference.reconciliation.get("opening_date_needs_review"),
            "completion_needs_review": reference.reconciliation.get("historical_completion_needs_review"),
            "otrs_matched": reference.reconciliation.get("otrs_matched"),
            "source_timezone": reference.import_run.counts.get("source_timezone", "Consultar relatório do lote") if reference.import_run_id else "Consultar relatório do lote"}
