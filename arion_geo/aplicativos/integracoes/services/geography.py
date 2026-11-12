"""IBGE catalog with a versioned, complete offline fallback."""
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ..clients.public_data import IBGE_BASE, PublicDataClient, PublicDataError
from ..models import GeographyCatalog

REGIONS = (
    {"id": 1, "sigla": "N", "nome": "Norte"},
    {"id": 2, "sigla": "NE", "nome": "Nordeste"},
    {"id": 3, "sigla": "SE", "nome": "Sudeste"},
    {"id": 4, "sigla": "S", "nome": "Sul"},
    {"id": 5, "sigla": "CO", "nome": "Centro-Oeste"},
)
_STATES = (
    (11,"RO","Rondônia",1),(12,"AC","Acre",1),(13,"AM","Amazonas",1),(14,"RR","Roraima",1),
    (15,"PA","Pará",1),(16,"AP","Amapá",1),(17,"TO","Tocantins",1),
    (21,"MA","Maranhão",2),(22,"PI","Piauí",2),(23,"CE","Ceará",2),(24,"RN","Rio Grande do Norte",2),
    (25,"PB","Paraíba",2),(26,"PE","Pernambuco",2),(27,"AL","Alagoas",2),(28,"SE","Sergipe",2),(29,"BA","Bahia",2),
    (31,"MG","Minas Gerais",3),(32,"ES","Espírito Santo",3),(33,"RJ","Rio de Janeiro",3),(35,"SP","São Paulo",3),
    (41,"PR","Paraná",4),(42,"SC","Santa Catarina",4),(43,"RS","Rio Grande do Sul",4),
    (50,"MS","Mato Grosso do Sul",5),(51,"MT","Mato Grosso",5),(52,"GO","Goiás",5),(53,"DF","Distrito Federal",5),
)
STATES = tuple({"id": code, "sigla": uf, "nome": name, "region_id": region} for code, uf, name, region in _STATES)
STATE_BY_UF = {row["sigla"]: row for row in STATES}


def region_states(region=""):
    region = str(region or "").upper()
    if not region:
        return list(STATE_BY_UF)
    match = next((r for r in REGIONS if str(r["id"]) == region or r["sigla"] == region), None)
    if not match:
        raise ValueError("Macrorregião inválida.")
    return [s["sigla"] for s in STATES if s["region_id"] == match["id"]]


def state_query(codes, field="state"):
    import unicodedata
    query = Q(pk__in=[])
    for code in codes:
        row = STATE_BY_UF[code]
        ascii_name = "".join(c for c in unicodedata.normalize("NFD", row["nome"]) if not unicodedata.combining(c))
        for value in {code, row["nome"], ascii_name}:
            query |= Q(**{f"{field}__iexact": value})
    return query


def catalog():
    row = GeographyCatalog.objects.filter(key="brazil").first()
    return {"regions": row.regions if row else list(REGIONS), "states": row.states if row else list(STATES),
            "source": row.source if row else "ibge_offline_v1", "synced_at": row.synced_at.isoformat() if row else None}


def fetch_catalog(client=None):
    client = client or PublicDataClient()
    regions = client.get(IBGE_BASE + "regioes")
    states = client.get(IBGE_BASE + "estados")
    try:
        normalized_regions = [{"id": int(r["id"]), "sigla": str(r["sigla"]), "nome": str(r["nome"])} for r in regions]
        normalized_states = [{"id": int(s["id"]), "sigla": str(s["sigla"]).upper(), "nome": str(s["nome"]), "region_id": int(s["regiao"]["id"])} for s in states]
        if sorted(normalized_regions, key=lambda r:r["id"]) != list(REGIONS):
            raise ValueError
        if {(s["id"],s["sigla"],s["region_id"]) for s in normalized_states} != {(s["id"],s["sigla"],s["region_id"]) for s in STATES} or len(normalized_states) != 27:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise PublicDataError("Catálogo IBGE incompleto ou incompatível; cache anterior preservado.") from None
    return normalized_regions, normalized_states


def sync_catalog(client=None):
    regions, states = fetch_catalog(client)
    with transaction.atomic():
        GeographyCatalog.objects.update_or_create(key="brazil", defaults={"regions": regions, "states": states, "source": "ibge", "synced_at": timezone.now()})
    return {"regions": len(regions), "states": len(states)}
