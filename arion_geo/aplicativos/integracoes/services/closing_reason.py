"""Canonical closing-reason labels shared by operational analytics."""

from dataclasses import dataclass
from functools import lru_cache

from django.db.models import Case, CharField, F, Q, Value, When


@dataclass(frozen=True)
class ClosingReason:
    key: str
    label: str
    raw_values: tuple[str, ...]


class ClosingReasonCanonicalizer:
    """Map explicitly confirmed OTRS values to stable keys and display labels.

    Unknown values remain distinct and keep their original text as their key and
    label. Matching is exact apart from surrounding whitespace and letter case;
    punctuation and spaces are never stripped to infer an alias.
    """

    REASONS = (
        ClosingReason("sem_atuacao", "Sem atuação", ("SemAtuacao", "Sem Atuação", "Sem atuacao", "Sem atuação")),
        ClosingReason("improcedente", "Improcedente", ("Improcedente",)),
        ClosingReason("reset_remoto", "Reset remoto", ("ResetRemoto", "Reset remoto", "Reset Remoto")),
        ClosingReason("falha_massiva", "Falha Massiva", ("FalhaMassiva", "Falha Massiva", "Defeito Massivo")),
        ClosingReason("pendencia_financeira", "Pendência Financeira", ("PendenciaFinanceira", "Pendência Financeira")),
        ClosingReason("configuracao_vlan", "Configuração de VLAN", ("ConfiguracaoVlan", "Configuração de VLAN")),
        ClosingReason("defeito_ponto_rede", "Defeito no Ponto de Rede", ("DefeitoPontoRede", "Defeito no Ponto de Rede")),
        ClosingReason("equipamento_substituido", "Equipamento Substituído", ("EquipamentoSubstituido", "Equipamento Substituido", "Equipamento Substituído")),
        ClosingReason("equipamento_travado", "Equipamento Travado", ("Equipamento travado/reinicilizado", "EquipamentoTravado", "Equipamento Travado")),
        ClosingReason("erro_configuracao_ip", "Erro de Configuração de IP", ("ErroConfigIP", "Erro de Configuração de IP")),
        ClosingReason("erro_monitoramento", "Erro de Monitoramento", ("ErroMonitoramento", "Erro de Monitoramento")),
        ClosingReason("falha_eletrica", "Falha Elétrica", ("FalhaEletrica", "Falha elétrica", "Falha Elétrica")),
        ClosingReason("falha_infra_aeroporto", "Falha na Infraestrutura do Aeroporto", ("FalhaInfraAeroporto", "Falha na Infraestrutura do Aeroporto")),
        ClosingReason("falha_infra_cliente", "Falha na Infraestrutura do Cliente", ("FalhaInfraCliente", "Falha na Infraestrutura do Cliente")),
        ClosingReason("falha_logica_operadora", "Falha Lógica na Operadora", ("FalhaLogica", "Falha Lógica na Operadora")),
        ClosingReason("sem_intervencao_tecnica", "Sem Intervenção Técnica", ("SemIntervencaoTecnica", "Sem Intervenção Técnica")),
        ClosingReason("atualizacao_configuracao", "Atualização de Configuração", ("AttDeConfiguracao", "Atualização de Configuração", "Atualização de configuração")),
        ClosingReason("rompimento_fibra", "Rompimento de Fibra", ("Rompimento/manut Fibra", "RompimentoFibra", "Rompimento de Fibra")),
        ClosingReason("outros_especificar", "Outros (Especificar)", ("Outros", "Outros (Especificar)")),
        ClosingReason("outros_legado", "Outros (catálogo legado)", ("zOutros",)),
        ClosingReason("configuracao_rede", "Configuração de Rede", ("ConfiguracaoRede", "Configuração de Rede")),
        ClosingReason("cancelamento_servico", "Cancelamento de Serviço", ("CancelamentoServico", "Cancelamento de Serviço")),
        ClosingReason("chamado_cancelado", "Chamado Cancelado", ("Cancelado", "ChamadoCancelado", "Chamado Cancelado")),
        ClosingReason("conexao_incorreta_cabo_interface", "Conexão Incorreta - Cabo/Interface", ("Conexão Incorreta - Cabeamento/porta", "ConexaoIncorreta", "Conexão Incorreta - Cabo/Interface")),
        ClosingReason("falha_pppoe", "Falha no PPPOE", ("FalhaPPPOE", "Falha no PPPOE")),
        ClosingReason("equipamento_desligado", "Equipamento Desligado", ("EquipamentoDesligado", "Equipamento Desligado")),
        ClosingReason("cabo_danificado", "Cabo Danificado", ("CaboDanificado", "Cabo Danificado")),
        ClosingReason("cabo_desconectado", "Cabo Desconectado", ("CaboDesconectado", "Cabo Desconectado")),
        ClosingReason("oscilacao_rede", "Oscilação na Rede", ("OscilacaoRede", "Oscilação na Rede")),
        ClosingReason("tunnel_mtu", "Tunnel / MTU", ("TunnelMTU", "Tunnel / MTU")),
        ClosingReason("fonte_queimada", "Fonte Queimada", ("FonteQueimada", "Fonte Queimada")),
        ClosingReason("sinal_fraco", "Sinal Fraco", ("SinalFraco", "Sinal Fraco")),
        ClosingReason("falha_operadora", "Falha na Operadora", ("FalhaOperadora", "Falha na Operadora")),
        ClosingReason("alta_temperatura_sala_tecnica", "Alta Temperatura da Sala Técnica", ("AltaTempSalaTecnica", "Alta Temperatura da Sala Técnica")),
        ClosingReason("falha_monitoramento", "Falha no Monitoramento", ("FalhaMonitoramento", "Falha no Monitoramento")),
        ClosingReason("preventiva_falha", "Preventiva com Falha", ("PreventivaFalha", "Preventiva com Falha")),
        ClosingReason("ataque_cibernetico", "Ataque Cibernético", ("AtaqueCibernetico", "Ataque Cibernético")),
        ClosingReason("vandalismo", "Vandalismo", ("Vandalismo",)),
        ClosingReason("vlan", "Troca de VLAN", ("Vlan", "Troca de VLAN")),
    )

    _BY_RAW = {
        raw.strip().casefold(): reason
        for reason in REASONS
        for raw in reason.raw_values
    }
    _BY_KEY = {reason.key: reason for reason in REASONS}

    @classmethod
    @lru_cache(maxsize=512)
    def resolve(cls, value) -> ClosingReason:
        raw = str(value or "").strip()
        if not raw:
            return ClosingReason("", "Não classificado", ())
        if raw in cls._BY_KEY:
            return cls._BY_KEY[raw]
        return cls._BY_RAW.get(raw.casefold(), ClosingReason(raw, raw, (raw,)))

    @classmethod
    def key(cls, value) -> str:
        return cls.resolve(value).key

    @classmethod
    def display(cls, value) -> str:
        return cls.resolve(value).label

    @classmethod
    def raw_values(cls, key_or_raw) -> tuple[str, ...]:
        raw = str(key_or_raw or "").strip()
        reason = cls._BY_KEY.get(raw) or cls.resolve(raw)
        return reason.raw_values or (raw,)

    @classmethod
    def options(cls, raw_values):
        reasons = {cls.key(value): cls.resolve(value) for value in raw_values if str(value or "").strip()}
        return [
            {"value": key, "label": reasons[key].label}
            for key in sorted(reasons, key=lambda item: (reasons[item].label.casefold(), item))
        ]

    @classmethod
    def annotate_key(cls, queryset, field="closing_code_raw", output="closing_reason_key"):
        """Annotate the canonical key in SQL for bounded aggregation/grouping."""
        conditions = [
            When(Q(**{f"{field}__in": reason.raw_values}), then=Value(reason.key))
            for reason in cls.REASONS
        ]
        return queryset.annotate(**{
            output: Case(*conditions, default=F(field), output_field=CharField(max_length=250)),
        })
