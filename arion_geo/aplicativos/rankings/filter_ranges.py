"""Data-driven range options for the Rankings table filters (pure, in-memory)."""
import math

# Operational bands for resolution time (hours); only bands with data are offered.
AVG_TIME_BANDS = (
    ("Até 12h", None, 12),
    ("12–24h", 12, 24),
    ("24–48h", 24, 48),
    ("48–72h", 48, 72),
    ("Acima de 72h", 72, None),
)

# Level labels by number of bands actually generated (4 quartiles, fewer when values collapse).
LEVEL_PICK = {4: (0, 1, 2, 3), 3: (0, 2, 3), 2: (0, 3)}

METRIC_META = {
    "links": {
        "chip": "Links", "unit": " links", "integer": True, "zero": None, "with": None,
        "levels": ("Baixa exposição", "Média exposição", "Alta exposição", "Muito alta exposição"),
    },
    "tickets": {
        "chip": "Chamados", "unit": " chamados", "integer": True, "zero": "Sem chamados", "with": None,
        "levels": ("Baixo volume", "Volume moderado", "Alto volume", "Volume muito alto"),
    },
    "incidence": {
        "chip": "Incidência", "unit": "", "integer": False, "zero": "Sem incidência", "with": None,
        "levels": ("Baixa incidência", "Incidência moderada", "Alta incidência", "Incidência crítica"),
    },
    "backlog": {
        "chip": "Backlog", "unit": "", "integer": True, "zero": "Sem backlog", "with": "Com backlog",
        "levels": ("Backlog baixo", "Backlog moderado", "Backlog alto", "Backlog crítico"),
    },
    "aged": {
        "chip": "Resolvidos >24h", "unit": "", "integer": True, "zero": "Nenhuma resolu\u00e7\u00e3o >24h", "with": "Possui resolu\u00e7\u00f5es >24h",
        "levels": ("Resolvidos >24h baixo", "Resolvidos >24h moderado", "Resolvidos >24h alto", "Resolvidos >24h cr\u00edtico"),
    },
    "avg_time": {"chip": "Tempo médio", "unit": "h", "integer": False, "zero": None, "with": None, "levels": ()},
    "risk": {
        "chip": "Score operacional", "unit": "", "integer": False, "zero": None, "with": None,
        "levels": ("Baixo", "Moderado", "Alto", "Crítico"),
    },
}


def format_number(value, integer=False):
    """pt-BR display: 1.234 / 28,1."""
    if value is None:
        return ""
    if integer or float(value).is_integer():
        return f"{int(round(value)):,}".replace(",", ".")
    return f"{value:.1f}".replace(".", ",")


def format_param(value):
    return "" if value is None else f"{value:g}"


def _percentile(sorted_values, fraction):
    index = max(0, math.ceil(fraction * len(sorted_values)) - 1)
    return sorted_values[index]


def _option(key, label, low, high, detail=""):
    return {
        "key": key, "label": label, "detail": detail, "min": low, "max": high,
        "value": f"{format_param(low)}:{format_param(high)}",
        "text": f"{label} · {detail}" if detail else label,
    }


def build_metric_ranges(values, metric):
    """Return ready-made filter options for `metric` derived from the real value distribution.

    Zero gets its own option when it exists; level bands (quartiles) are computed only over
    positive values (or all values for metrics without a zero option) and are skipped when
    there is not enough variation to make them useful.
    """
    meta = METRIC_META[metric]
    numbers = sorted(float(v) for v in values if v is not None)
    if not numbers:
        return []
    options = []
    if metric == "avg_time":
        for label, low, high in AVG_TIME_BANDS:
            if any((low is None or v >= low) and (high is None or v <= high) for v in numbers):
                options.append(_option(f"{low}-{high}", label, low, high))
        return options

    integer = meta["integer"]
    zeros = [v for v in numbers if v == 0]
    positives = [v for v in numbers if v > 0] if meta["zero"] else numbers
    if meta["zero"] and zeros:
        options.append(_option("zero", meta["zero"], 0, 0))
    if meta["with"] and zeros and positives:
        options.append(_option("any", meta["with"], 1, None))

    if len(set(positives)) < 4:
        return options
    top = positives[-1]
    cuts = []
    for fraction in (0.25, 0.5, 0.75):
        cut = _percentile(positives, fraction)
        cut = int(cut) if integer else round(cut, 1)
        if cut < top and cut not in cuts:
            cuts.append(cut)
    if not cuts:
        return options
    labels = [meta["levels"][i] for i in LEVEL_PICK[len(cuts) + 1]]
    low = int(positives[0]) if integer else round(positives[0], 1)
    for index, label in enumerate(labels):
        high = cuts[index] if index < len(cuts) else None
        if high is None:
            detail = f"{format_number(low, integer)}+"
        else:
            detail = f"{format_number(low, integer)}–{format_number(high, integer)}"
        options.append(_option(f"level{index}", label, low, high, detail + meta["unit"] if metric in {"links", "tickets"} else detail))
        if high is not None:
            # Integers never overlap; decimals share the boundary so no value falls in a gap.
            low = high + 1 if integer else high
    return options

