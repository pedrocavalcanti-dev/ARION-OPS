from django.db.models import Q

from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.integracoes.models import CMDBLinkMirror

from ..models import QualityOrder


class QualityDataScopeService:
    """Adapt the existing CMDB scope to Quality Orders; missing scope fails closed."""

    @staticmethod
    def orders_for(user, queryset=None):
        queryset = queryset if queryset is not None else QualityOrder.objects.all()
        if user.is_authenticated and user.is_superuser:
            return queryset
        if DataScopeService.is_global(user):
            return queryset

        allowed_links = DataScopeService.filter_queryset(user, CMDBLinkMirror.objects.all())
        allowed_ids = allowed_links.values("source_config_item_id")
        return queryset.filter(
            (
                Q(ci_snapshot__in=allowed_ids)
                & (Q(link_id__isnull=True) | Q(link_id__in=allowed_ids))
            )
            | (Q(ci_snapshot="") & Q(link_id__in=allowed_ids))
        )
