from django.db.models import Case, CharField, Exists, F, OuterRef, Q, When

from aplicativos.base_links.models import Operadora, Projeto
from aplicativos.configuracoes.data_scope import DataScopeService
from aplicativos.integracoes.models import CMDBLinkMirror, OTRSTicketConfigItem

from ..models import CancellationIntake, CancellationProcess


class CancellationScope:
    """Fail closed; a mixed/unresolved ticket is reserved for global triage."""

    @staticmethod
    def _identity(user, queryset, client_field, project_field):
        operator_ids = DataScopeService.allowed_operator_ids(user)
        project_ids = DataScopeService.allowed_project_ids(user)
        clients = Operadora.objects.filter(pk__in=operator_ids or ()).values_list("name", flat=True)
        projects = Projeto.objects.filter(pk__in=project_ids or ()).values_list("name", flat=True)
        cq, pq = Q(pk__in=[]), Q(pk__in=[])
        for name in clients:
            cq |= Q(**{f"{client_field}__iexact": name})
        for name in projects:
            pq |= Q(**{f"{project_field}__iexact": name})
        return queryset.filter(cq, pq)

    @classmethod
    def _current_identity(cls, user, qs):
        qs = qs.annotate(_current_scope_project=Case(
            When(link__business_project="", then=F("link__legacy_project")),
            default=F("link__business_project"), output_field=CharField(),
        ))
        return cls._identity(user, qs, "link__contracting_client", "_current_scope_project")

    @classmethod
    def intakes(cls, user, queryset=None):
        qs = queryset if queryset is not None else CancellationIntake.objects.all()
        if not user.is_authenticated:
            return qs.none()
        if DataScopeService.is_global(user):
            return qs
        allowed = DataScopeService.filter_queryset(user, CMDBLinkMirror.objects.filter(is_present_in_source=True))
        # Do not expose the ticket when even one current relation is unresolved or outside scope.
        invalid = OTRSTicketConfigItem.objects.filter(ticket_id=OuterRef("ticket_id"), is_present_in_source=True).filter(
            Q(cmdb_link__isnull=True) | ~Q(cmdb_link__in=allowed)
        )
        current = OTRSTicketConfigItem.objects.filter(ticket_id=OuterRef("ticket_id"),
                                                     cmdb_link_id=OuterRef("link_id"), is_present_in_source=True)
        qs = qs.filter(scope_ready=True, link__in=allowed).annotate(
            _invalid_scope=Exists(invalid), _current_link=Exists(current),
        ).filter(_invalid_scope=False, _current_link=True)
        return cls._current_identity(user, cls._identity(user, qs, "client_snapshot", "project_snapshot"))

    @classmethod
    def processes(cls, user, queryset=None):
        qs = queryset if queryset is not None else CancellationProcess.objects.all()
        if not user.is_authenticated:
            return qs.none()
        if DataScopeService.is_global(user):
            return qs
        allowed_links = DataScopeService.filter_queryset(user, CMDBLinkMirror.objects.filter(is_present_in_source=True))
        # An imported official record may have no intake. Its verified current CI
        # and both identity dimensions still have to be authorized independently.
        invalid = OTRSTicketConfigItem.objects.filter(ticket_id=OuterRef("source_ticket_id"), is_present_in_source=True).filter(
            Q(cmdb_link__isnull=True) | ~Q(cmdb_link__in=allowed_links))
        current = OTRSTicketConfigItem.objects.filter(ticket_id=OuterRef("source_ticket_id"), cmdb_link_id=OuterRef("link_id"), is_present_in_source=True)
        qs = qs.annotate(_legacy_invalid=Exists(invalid), _legacy_current=Exists(current)).filter(
            Q(intake__in=cls.intakes(user)) | Q(origin="ARIONBROKER", intake__isnull=True,
                _legacy_invalid=False) & (Q(source_ticket__isnull=True) | Q(_legacy_current=True)), link__in=allowed_links)
        return cls._current_identity(user, cls._identity(user, qs, "contracting_client", "project"))
