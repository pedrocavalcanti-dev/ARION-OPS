QUALITY_RREIP_DAYS = 30
QUALITY_MONITORING_END_WARNING_DAYS = 3
QUALITY_PRIORITY_DEFAULT_PERIOD = "30"
QUALITY_LIST_PAGE_SIZES = (25, 50, 100)

# Lower values are shown first in the "Maior atenção" queue. These labels
# influence ordering only; they never change occurrence or recurrence counts.
QUALITY_BUSINESS_PRIORITY = {
    ("CLARO", "BANCO DO BRASIL"): 10,
    ("CLARO", "CORREIOS"): 20,
    ("VIVO", "BRADESCO"): 30,
    ("VIVO", "ARCOS DOURADOS"): 40,
    ("FUST", ""): 90,
    ("", "FUST"): 90,
}
QUALITY_PRIORITY_PERIOD_OPTIONS = (
    ("7", "7 dias"),
    ("15", "15 dias"),
    ("30", "30 dias"),
    ("60", "60 dias"),
    ("90", "90 dias"),
    ("custom", "Personalizado"),
)
