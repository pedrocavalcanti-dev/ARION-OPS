import csv
import json
import logging

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from datetime import timedelta
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .access_control import ADMIN_PERMISSIONS, GROUP_SYSTEM_ADMIN, PERMISSION_LABELS, is_system_admin, managed_permissions
from .admin_forms import AccessGroupForm, GroupDataScopeForm, UserCreateForm, UserUpdateForm
from .data_scope import DataScopeService
from .forms import AccountForm
from .models import GroupDataScope, UserPreference
from .permissions import access_required
from aplicativos.auditoria.models import AuditEvent
from config.errors import api_error, require_media_type
from django.conf import settings
from django.db import connections
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from aplicativos.auditoria.services import AuditService
from .documentation import DocumentationService
from .documentation_explorer import DocumentationExplorer
from config.releases import release_catalog
from aplicativos.autenticacao.models import AccessToken, UserSecurityProfile
from aplicativos.autenticacao.security import current_password_policy, get_security_profile, issue_access_token, password_access_status
from aplicativos.autenticacao.forms import SecurePasswordChangeForm

logger = logging.getLogger(__name__)


@login_required
def account(request):
    if request.method == "POST":
        form = AccountForm(request.POST, instance=request.user)
        if form.is_valid():
            before = {"first_name": request.user.first_name, "last_name": request.user.last_name, "email": request.user.email}
            form.save()
            after = {"first_name": request.user.first_name, "last_name": request.user.last_name, "email": request.user.email}
            if before != after:
                AuditService.record(request=request, user=request.user, category="ACCOUNT", action="ACCOUNT_UPDATED", module="Conta", description="Dados da conta atualizados", obj=request.user, metadata={"before": before, "after": after})
            messages.success(request, "Seus dados foram atualizados.")
            return redirect("configuracoes:account")
    else:
        form = AccountForm(instance=request.user)
    return render(request, "configuracoes/account.html", _settings_context(request, "account", form=form))


@login_required
def index(request):
    return render(request, "configuracoes/index.html", _settings_context(request, "index"))


@login_required
def about(request):
    return render(request, "configuracoes/about.html")


@login_required
def security(request):
    if request.method == "POST":
        form = SecurePasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            user = form.save()
            profile = get_security_profile(user)
            profile.password_changed_at = timezone.now()
            profile.must_change_password = False
            profile.change_reason = ""
            profile.save(update_fields=("password_changed_at", "must_change_password", "change_reason", "updated_at"))
            AuditService.record(request=request, user=user, category="ACCOUNT", action="PASSWORD_CHANGED", description="Senha alterada pelo próprio usuário", obj=user)
            update_session_auth_hash(request, user)
            messages.success(request, "Senha alterada com sucesso.")
            return redirect("configuracoes:security")
    else:
        form = SecurePasswordChangeForm(user=request.user)
    return render(request, "configuracoes/security.html", _settings_context(request, "security", form=form))


@login_required
def appearance(request):
    preference, _ = UserPreference.objects.get_or_create(user=request.user)
    return render(request, "configuracoes/appearance.html", _settings_context(request, "appearance", preference=preference))


def _settings_context(request, active_section, **extra):
    return {"active_section": active_section, "is_access_admin": is_system_admin(request.user), **extra}


def _admin_context(request, section, **extra):
    return _settings_context(request, section, administration=True, **extra)


def _active_system_admins():
    users = get_user_model().objects.filter(is_active=True, is_superuser=False).prefetch_related("groups__permissions")
    return [user for user in users if is_system_admin(user)]


def _groups_have_system_admin_access(groups):
    codenames = set(Permission.objects.filter(
        group__in=groups, content_type__app_label="configuracoes",
        codename__in=ADMIN_PERMISSIONS,
    ).values_list("codename", flat=True))
    return set(ADMIN_PERMISSIONS).issubset(codenames)


@login_required
@access_required("manage_users")
def user_list(request):
    query = request.GET.get("q", "").strip()
    group_id = request.GET.get("group", "")
    status = request.GET.get("status", "")
    scope_type = request.GET.get("scope", "")
    users = get_user_model().objects.all().select_related("security_profile").prefetch_related(
        "groups__data_scope__allowed_operators", "groups__data_scope__allowed_projects", "access_tokens",
    )
    if query:
        users = users.filter(Q(first_name__icontains=query) | Q(last_name__icontains=query) | Q(username__icontains=query) | Q(email__icontains=query))
    if group_id.isdigit():
        users = users.filter(groups__id=int(group_id))
    if status == "active":
        users = users.filter(is_active=True)
    elif status == "inactive":
        users = users.filter(is_active=False)
    if scope_type in (GroupDataScope.Mode.ALL, GroupDataScope.Mode.CUSTOM):
        users = users.filter(groups__data_scope__mode=scope_type)
    page_obj = Paginator(users.distinct().order_by("username"), 25).get_page(request.GET.get("page"))
    can_view_access_status = request.user.has_perm("configuracoes.view_access_status")
    access_policy = current_password_policy() if can_view_access_status else None
    for item in page_obj:
        memberships = sorted(item.groups.all(), key=lambda group: group.name)
        item.primary_group = memberships[0] if len(memberships) == 1 else None
        item.multiple_profiles = len(memberships) > 1
        try:
            item.group_data_scope = item.primary_group.data_scope if item.primary_group else None
        except GroupDataScope.DoesNotExist:
            item.group_data_scope = None
        if can_view_access_status:
            item.access_status_code, item.access_status_label = password_access_status(item, policy=access_policy)
            item.can_receive_invitation = item.is_active and not item.has_usable_password() and item.access_status_code in {"pending", "invitation_expired"}
    filters = {"q": query, "group": group_id, "status": status, "scope": scope_type}
    return render(request, "configuracoes/admin/users.html", _admin_context(request, "users", page_obj=page_obj, groups=Group.objects.order_by("name"), filters=filters))


@login_required
@access_required("create_users")
@access_required("manage_users")
def user_create(request):
    form = UserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save()
        AuditService.record(request=request, category="ADMINISTRATION", action="USER_CREATED", description="Usuário criado", obj=user, metadata={
            "user_id": user.pk, "username": user.username, "group": user.groups.first().name,
        })
        messages.success(request, "Usuário criado com sucesso. Revise o perfil e o escopo abaixo e gere o convite de primeiro acesso.")
        return redirect("configuracoes:user-detail", pk=user.pk)
    return render(request, "configuracoes/admin/user_form.html", _admin_context(request, "users", form=form, page_title="Novo usuário", user_object=None))


@login_required
@access_required("manage_users")
@access_required("view_access_status")
def user_detail(request, pk):
    user = get_object_or_404(get_user_model().objects.select_related("security_profile").prefetch_related(
        "groups__data_scope__allowed_operators", "groups__data_scope__allowed_projects", "access_tokens",
    ), pk=pk)
    memberships = list(user.groups.order_by("name"))
    group = memberships[0] if len(memberships) == 1 else None
    policy = current_password_policy()
    profile = get_security_profile(user, policy=policy)
    status_code, status_label = password_access_status(user, policy=policy)
    max_age_days = policy.max_age_days if policy else 90
    expires_at = profile.password_expires_at(max_age_days=max_age_days) if profile.password_changed_at else None
    active_invitation = user.access_tokens.filter(
        purpose=AccessToken.Purpose.ACTIVATION, consumed_at__isnull=True,
        revoked_at__isnull=True, expires_at__gt=timezone.now(),
    ).first()
    return render(request, "configuracoes/admin/user_detail.html", _admin_context(
        request, "users", user_object=user, primary_group=group, multiple_profiles=len(memberships) > 1,
        data_scope=DataScopeService.get_scope(user), access_status_code=status_code,
        access_status_label=status_label, security_profile=profile, password_expires_at=expires_at,
        active_invitation=active_invitation,
    ))


def _issue_one_time_access_link(*, user, purpose, actor):
    token, url = issue_access_token(user=user, purpose=purpose, actor=actor)
    return token, url


def _access_link_issued_response(request, *, user, token, url, purpose):
    is_activation = purpose == AccessToken.Purpose.ACTIVATION
    response = render(request, "autenticacao/access_link_issued.html", {
        "title": "Convite de primeiro acesso" if is_activation else "Link de redefinição de senha",
        "description": "Encaminhe o convite ao destinatário por um canal corporativo seguro." if is_activation else "Encaminhe o link ao e-mail corporativo por um canal confiável.",
        "access_url": url,
        "expires_at_label": timezone.localtime(token.expires_at).strftime("%d/%m/%Y %H:%M"),
        "return_url": reverse("configuracoes:user-detail", args=(user.pk,)),
        "users_url": reverse("configuracoes:users"),
    })
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "private, no-store"
    response["Content-Security-Policy"] = (
        "default-src 'none'; img-src 'self'; style-src 'self'; script-src 'self'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@access_required("generate_access_invites")
@require_POST
def user_issue_invitation(request, pk):
    user = get_object_or_404(get_user_model(), pk=pk)
    profile = get_security_profile(user)
    if not user.is_active or profile.first_activation_completed_at:
        messages.error(request, "Não é possível emitir convite para esta conta.")
        return redirect("configuracoes:user-detail", pk=user.pk)
    token, url = _issue_one_time_access_link(user=user, purpose=AccessToken.Purpose.ACTIVATION, actor=request.user)
    AuditService.record(
        request=request, category="SECURITY", action="ACCESS_INVITATION_ISSUED",
        module="Autenticação", description="Convite de primeiro acesso emitido.", obj=user,
        metadata={"expires_at": token.expires_at},
    )
    messages.success(request, "Convite gerado. Copie e encaminhe o link ao destinatário pelo canal corporativo.")
    return _access_link_issued_response(
        request, user=user, token=token, url=url, purpose=AccessToken.Purpose.ACTIVATION,
    )


@login_required
@access_required("revoke_access_invites")
@require_POST
def user_revoke_invitation(request, pk):
    user = get_object_or_404(get_user_model(), pk=pk)
    now = timezone.now()
    revoked = AccessToken.objects.filter(
        user=user, purpose=AccessToken.Purpose.ACTIVATION,
        consumed_at__isnull=True, revoked_at__isnull=True,
    ).update(revoked_at=now)
    AuditService.record(
        request=request, category="SECURITY", action="ACCESS_INVITATION_REVOKED",
        module="Autenticação", description="Convite de primeiro acesso revogado.", obj=user,
        metadata={"revoked_count": revoked},
    )
    messages.success(request, "Convite revogado." if revoked else "Não havia convite ativo para revogar.")
    return redirect("configuracoes:user-detail", pk=user.pk)


@login_required
@access_required("generate_password_reset_links")
@require_POST
def user_issue_password_reset(request, pk):
    user = get_object_or_404(get_user_model(), pk=pk)
    profile = get_security_profile(user)
    if not user.is_active or not profile.first_activation_completed_at:
        messages.error(request, "A conta precisa estar ativa e ter concluído o primeiro acesso para receber um link de redefinição.")
        return redirect("configuracoes:user-detail", pk=user.pk)
    token, url = _issue_one_time_access_link(user=user, purpose=AccessToken.Purpose.PASSWORD_RESET, actor=request.user)
    AuditService.record(
        request=request, category="SECURITY", action="ADMIN_PASSWORD_RESET_LINK_ISSUED",
        module="Autenticação", description="Link de redefinição de senha emitido pela administração.", obj=user,
        metadata={"expires_at": token.expires_at},
    )
    messages.success(request, "Link de redefinição gerado. Encaminhe-o ao e-mail corporativo por um canal confiável.")
    return _access_link_issued_response(
        request, user=user, token=token, url=url, purpose=AccessToken.Purpose.PASSWORD_RESET,
    )


@login_required
@access_required("require_password_change")
@require_POST
def user_require_password_change(request, pk):
    user = get_object_or_404(get_user_model(), pk=pk)
    profile = get_security_profile(user)
    if profile.is_service_account:
        messages.error(request, "Contas de serviço não podem receber troca obrigatória interativa.")
        return redirect("configuracoes:user-detail", pk=user.pk)
    if not profile.first_activation_completed_at:
        messages.error(request, "Contas que aguardam primeiro acesso devem utilizar o convite de ativação.")
        return redirect("configuracoes:user-detail", pk=user.pk)
    profile.must_change_password = True
    profile.change_reason = UserSecurityProfile.ChangeReason.ADMIN
    profile.save(update_fields=("must_change_password", "change_reason", "updated_at"))
    AuditService.record(
        request=request, category="SECURITY", action="ADMIN_REQUIRED_PASSWORD_CHANGE",
        module="Autenticação", description="A administração exigiu uma nova senha para a conta.", obj=user,
    )
    messages.success(request, "A troca de senha foi exigida para a próxima solicitação da conta.")
    return redirect("configuracoes:user-detail", pk=user.pk)


@login_required
@access_required("manage_users")
def user_edit(request, pk):
    user = get_object_or_404(get_user_model(), pk=pk)
    was_active_system_admin = user.is_active and is_system_admin(user)
    before_profile = {field: getattr(user, field) for field in ("first_name", "last_name", "email")}
    groups_before = list(user.groups.order_by("name").values_list("name", flat=True))
    was_active = user.is_active
    form = UserUpdateForm(request.POST or None, instance=user)
    if request.method == "POST" and form.is_valid():
        if was_active_system_admin and len(_active_system_admins()) <= 1:
            if not form.cleaned_data["is_active"] or not _groups_have_system_admin_access([form.cleaned_data["group"]]):
                form.add_error(None, "Não é possível remover ou desativar o último Administrador do Sistema ativo.")
        if not form.errors:
            with transaction.atomic():
                form.save()
            profile_after = {field: getattr(user, field) for field in ("first_name", "last_name", "email")}
            if before_profile != profile_after:
                AuditService.record(request=request, category="ADMINISTRATION", action="USER_UPDATED", description="Dados gerais do usuário atualizados", obj=user, metadata={"before": before_profile, "after": profile_after})
            groups_after = list(user.groups.order_by("name").values_list("name", flat=True))
            if groups_before != groups_after:
                AuditService.record(request=request, category="ADMINISTRATION", action="GROUP_CHANGED", description="Perfil de acesso alterado", obj=user, metadata={"before": groups_before[0] if groups_before else None, "after": groups_after[0] if groups_after else None})
            if was_active != user.is_active:
                action = "USER_ACTIVATED" if user.is_active else "USER_DEACTIVATED"
                AuditService.record(request=request, category="ADMINISTRATION", action=action, description="Conta de usuário ativada" if user.is_active else "Conta de usuário desativada", obj=user)
            messages.success(request, "Acesso do usuário atualizado.")
            return redirect("configuracoes:user-detail", pk=user.pk)
    memberships = list(user.groups.order_by("name"))
    primary_group = memberships[0] if len(memberships) == 1 else None
    return render(request, "configuracoes/admin/user_form.html", _admin_context(request, "users", form=form, page_title="Editar usuário", user_object=user, primary_group=primary_group, multiple_profiles=len(memberships) > 1, data_scope=DataScopeService.get_scope(user)))


@login_required
@access_required("manage_groups")
def group_list(request):
    groups = Group.objects.select_related("data_scope").prefetch_related("data_scope__allowed_operators", "data_scope__allowed_projects").annotate(member_count=Count("user", distinct=True)).order_by("name")
    return render(request, "configuracoes/admin/groups.html", _admin_context(request, "groups", groups=groups))


@login_required
@access_required("manage_groups")
def group_edit(request, pk=None):
    group = get_object_or_404(Group, pk=pk) if pk else None
    form = AccessGroupForm(request.POST or None, instance=group)
    if request.method == "POST" and form.is_valid():
        managed_ids = managed_permissions().values_list("pk", flat=True)
        permissions_before = sorted(group.permissions.filter(pk__in=managed_ids).values_list("content_type__app_label", "codename")) if group else []
        selected = set(form.cleaned_data["permissions"].values_list("codename", flat=True))
        if group and len(_active_system_admins()) <= 1 and group.name == GROUP_SYSTEM_ADMIN and not set(ADMIN_PERMISSIONS).issubset(selected):
            form.add_error(None, "Mantenha as permissões administrativas do último perfil de sistema ativo.")
        if not form.errors:
            with transaction.atomic():
                saved = form.save()
                GroupDataScope.objects.get_or_create(group=saved, defaults={"mode": GroupDataScope.Mode.CUSTOM})
            permissions_after = sorted(saved.permissions.filter(pk__in=managed_permissions().values_list("pk", flat=True)).values_list("content_type__app_label", "codename"))
            if group and permissions_before != permissions_after:
                AuditService.record(request=request, category="ADMINISTRATION", action="PERMISSIONS_CHANGED", description="Permissões do grupo alteradas", obj=saved, metadata={"group": saved.name, "permissions_before": permissions_before, "permissions_after": permissions_after})
            messages.success(request, "Perfil de acesso salvo.")
            return redirect("configuracoes:group-edit", pk=saved.pk)
    scope = getattr(group, "data_scope", None) if group else None
    permission_groups = form.grouped_permissions()
    permission_count = sum(len(rows) for rows in permission_groups.values())
    selected_permission_count = sum(
        row["selected"] for rows in permission_groups.values() for row in rows
    )
    return render(request, "configuracoes/admin/group_form.html", _admin_context(
        request, "groups", form=form, group=group, scope=scope,
        permission_groups=permission_groups,
        permission_count=permission_count,
        selected_permission_count=selected_permission_count,
    ))


@login_required
@access_required("manage_data_scopes")
def scope_list(request):
    query = request.GET.get("q", "").strip()
    groups = Group.objects.select_related("data_scope").prefetch_related("data_scope__allowed_operators", "data_scope__allowed_projects").annotate(member_count=Count("user", distinct=True))
    if query:
        groups = groups.filter(name__icontains=query)
    page_obj = Paginator(groups.order_by("name"), 25).get_page(request.GET.get("page"))
    return render(request, "configuracoes/admin/scopes.html", _admin_context(request, "scopes", page_obj=page_obj, query=query))


def _scope_snapshot(scope):
    return {
        "mode": scope.mode,
        "operators": list(scope.allowed_operators.order_by("name").values_list("name", flat=True)),
        "projects": list(scope.allowed_projects.order_by("name").values_list("name", flat=True)),
    }


@login_required
@access_required("manage_data_scopes")
def scope_edit(request, pk):
    group = get_object_or_404(Group, pk=pk)
    scope, _ = GroupDataScope.objects.get_or_create(group=group, defaults={"mode": GroupDataScope.Mode.CUSTOM})
    before = _scope_snapshot(scope)
    form = GroupDataScopeForm(request.POST or None, instance=scope)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            scope = form.save()
        after = _scope_snapshot(scope)
        if before != after:
            AuditService.record(request=request, category="ADMINISTRATION", action="DATA_SCOPE_CHANGED", description="Escopo de dados do grupo alterado", obj=group, metadata={"before": before, "after": after})
        messages.success(request, "Escopo de dados atualizado.")
        return redirect("configuracoes:scope-edit", pk=group.pk)
    return render(request, "configuracoes/admin/scope_form.html", _admin_context(request, "scopes", form=form, group=group, scope=scope, member_count=group.user_set.count()))


@login_required
@access_required("view_audit_log")
def audit_list(request):
    events = AuditEvent.objects.select_related("user").all()
    filters = {key: request.GET.get(key, "").strip() for key in ("user", "category", "action", "result", "module", "from", "to", "q", "period", "page_size", "order")}
    if filters["user"].isdigit():
        events = events.filter(user_id=int(filters["user"]))
    if filters["category"]:
        events = events.filter(category=filters["category"])
    if filters["action"]:
        events = events.filter(action=filters["action"])
    if filters["result"] in ("SUCCESS", "FAILED", "BLOCKED"):
        events = events.filter(result=filters["result"])
    if filters["module"]:
        events = events.filter(module=filters["module"])
    now = timezone.now()
    period_start = {"today": timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0), "24h": now - timedelta(hours=24), "7d": now - timedelta(days=7), "30d": now - timedelta(days=30)}.get(filters["period"])
    if period_start:
        events = events.filter(timestamp__gte=period_start)
    date_field = forms.DateField(required=False, input_formats=["%Y-%m-%d"])
    try:
        date_from = date_field.clean(filters["from"]) if filters["from"] else None
        date_to = date_field.clean(filters["to"]) if filters["to"] else None
    except ValidationError:
        date_from = date_to = None
    if date_from:
        events = events.filter(timestamp__date__gte=date_from)
    if date_to:
        events = events.filter(timestamp__date__lte=date_to)
    query = filters["q"]
    if query:
        events = events.filter(Q(description__icontains=query) | Q(action__icontains=query) | Q(object_type__icontains=query) | Q(object_id__icontains=query) | Q(object_label__icontains=query) | Q(module__icontains=query) | Q(user__username__icontains=query) | Q(user__first_name__icontains=query) | Q(user__last_name__icontains=query))
    events = events.order_by("timestamp", "pk") if filters["order"] == "oldest" else events.order_by("-timestamp", "-pk")
    if request.GET.get("export") == "csv":
        rows = list(events)
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response.write("\ufeff")
        writer = csv.writer(response, delimiter=";")
        writer.writerow(("Data", "Usu\u00e1rio", "Categoria", "A\u00e7\u00e3o", "Resultado", "Objeto", "Descri\u00e7\u00e3o", "IP"))
        def safe(value):
            value = str(value or "")
            return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value
        for event in rows:
            writer.writerow(tuple(safe(value) for value in (event.timestamp.strftime("%d/%m/%Y %H:%M:%S"), event.user.get_username() if event.user else "Sistema", event.category, event.action, event.result, event.object_label or " ".join(filter(None, (event.object_type, event.object_id))), event.description, event.ip_address or "")))
        AuditService.record(request=request, category="EXPORT", action="AUDIT_EXPORT", module="Auditoria", description="Exporta\u00e7\u00e3o do hist\u00f3rico de auditoria", metadata={"format": "csv", "rows": len(rows), "filters": {key: filters[key] for key in ("user", "category", "action", "result", "module", "from", "to", "period") if filters[key]}})
        return response
    filters["users"] = get_user_model().objects.filter(auditevent__isnull=False).distinct().order_by("username")
    labels = {"AUTHENTICATION": "Autentica\u00e7\u00e3o", "ADMINISTRATION": "Administra\u00e7\u00e3o", "INTEGRATION": "Integra\u00e7\u00e3o", "EXPORT": "Exporta\u00e7\u00e3o", "ANALYTICS": "Analytics", "COCKPIT": "Cockpit", "ACCOUNT": "Conta", "ARI": "ARI"}
    filters["categories"] = [(value, labels.get(value, value.title())) for value in AuditEvent.objects.order_by("category").values_list("category", flat=True).distinct()]
    filters["actions"] = AuditEvent.objects.order_by("action").values_list("action", flat=True).distinct()
    filters["modules"] = AuditEvent.objects.exclude(module="").order_by("module").values_list("module", flat=True).distinct()
    filters["page_sizes"] = (25, 50, 100)
    page_size = int(filters["page_size"]) if filters["page_size"].isdigit() and int(filters["page_size"]) in (25, 50, 100) else 50
    page_query = request.GET.copy()
    page_query.pop("page", None)
    page_query.pop("export", None)
    page_obj = Paginator(events, page_size).get_page(request.GET.get("page"))
    for event in page_obj.object_list:
        event.category_label = dict(filters["categories"]).get(event.category, event.category.title())
    context = _admin_context(request, "audit", page_obj=page_obj, filters=filters, page_query=page_query.urlencode(), total_events=events.count(), failures=events.filter(result="FAILED").count(), admin_actions=events.filter(category="ADMINISTRATION").count(), exports=events.filter(category="EXPORT").count())
    return render(request, "configuracoes/admin/audit.html", context)


@login_required
@access_required("view_audit_log")
def audit_detail(request, pk):
    event = get_object_or_404(AuditEvent.objects.select_related("user"), pk=pk)
    changes = []
    metadata = event.metadata or {}
    if isinstance(metadata.get("before"), dict) and isinstance(metadata.get("after"), dict):
        for key in sorted(set(metadata["before"]) | set(metadata["after"])):
            if metadata["before"].get(key) != metadata["after"].get(key):
                changes.append((key.replace("_", " ").title(), metadata["before"].get(key), metadata["after"].get(key)))
    elif isinstance(metadata, dict):
        for key, value in metadata.items():
            if key.endswith("_before"):
                after_key = key[:-7] + "_after"
                if after_key in metadata:
                    changes.append((key[:-7].replace("_", " ").title(), value, metadata[after_key]))
    technical_metadata = json.dumps(metadata, ensure_ascii=False, indent=2) if metadata else ""
    return render(request, "configuracoes/admin/audit_detail.html", _admin_context(request, "audit", event=event, changes=changes, technical_metadata=technical_metadata))


@login_required
@access_required("view_integrations")
def integrations(request):
    from aplicativos.integracoes.views import home
    return home(request)


@login_required
@access_required("view_system_info")
def system_info(request):
    from aplicativos.integracoes.services.external_apis import get_mapbox_runtime_state
    return render(request, "configuracoes/admin/system.html", _admin_context(request, "system", db_engine=connections["default"].vendor, timezone=settings.TIME_ZONE, environment="Desenvolvimento" if settings.DEBUG else "Produção", mapbox_configured=get_mapbox_runtime_state()["configured"]))


@login_required
@access_required("manage_groups")
def documentation_home(request):
    categories = DocumentationService.categories()
    category_map = {category["name"]: category["documents"] for category in categories}
    card_definitions = (
        ("inicio", "Começar", "Visão geral, arquitetura e fontes de dados.", ("Começar",)),
        ("modulos", "Funcionalidades", "Cancelamentos, Qualidade, Chamados, Dashboard, Cockpit, Mapa, Rankings, Base, Relatórios e ARI.", ("Funcionalidades",)),
        ("dados", "Dados e Integrações", "Banco de dados, OTRS, worker e dicionário.", ("Dados e Integrações",)),
        ("otrs", "OTRS", "Referência técnica da integração, sincronização e dados.", ("OTRS",)),
        ("cmdb", "CMDB ServiceUp", "Referência técnica da origem, espelhos e consumidores.", ("CMDB ServiceUp",)),
        ("administracao", "Administração", "Usuários, grupos, escopos, configurações e auditoria.", ("Administração",)),
        ("operacao", "Segurança e operação", "Login, convites, senhas, Windows Server, backup, recuperação e padrões.", ("Segurança e operação", "Padrões")),
    )
    home_cards = []
    for anchor, title, description, names in card_definitions:
        groups = [category for category in categories if category["name"] in names]
        documents = [item for name in names for item in category_map.get(name, [])]
        home_cards.append({"anchor": anchor, "title": title, "description": description, "groups": groups, "documents": documents})
    navigation = DocumentationService.navigation()
    popular_slugs = ("arquitetura", "banco-de-dados", "otrs", "cmdb", "worker", "chamados")
    return render(request, "documentacao/inicio.html", {
        "home_cards": home_cards,
        "popular_documents": [item for slug in popular_slugs for item in navigation if item["slug"] == slug],
        "categories": categories,
        "navigation": navigation,
        "active_category": "Começar",
    })


@login_required
@access_required("manage_groups")
def documentation_page(request, slug):
    try:
        document = DocumentationService.get(slug)
    except (OSError, UnicodeError):
        logger.exception("Falha ao carregar documento da documentação: %s", slug)
        document = None
    if document is None:
        from django.http import Http404
        raise Http404("Documento não encontrado.")
    navigation = DocumentationService.navigation()
    current_index = next(index for index, item in enumerate(navigation) if item["slug"] == slug)
    return render(request, "documentacao/documento.html", {
        "document": document,
        "categories": DocumentationService.categories(),
        "navigation": navigation,
        "active_category": document["category"],
        "previous_document": navigation[current_index - 1] if current_index > 0 else None,
        "next_document": navigation[current_index + 1] if current_index + 1 < len(navigation) else None,
    })


@login_required
@access_required("manage_groups")
def documentation_explorer(request):
    source = request.GET.get("source", "otrs").strip().casefold()
    if source not in {"otrs", "cmdb", "cross"}:
        source = "otrs"
    try:
        explorer = DocumentationExplorer.build()
    except (OSError, LookupError, ValueError):
        logger.exception("Falha ao montar o catálogo do explorador documental")
        explorer = {"otrs": {"items": []}, "cmdb": {"items": []}, "diagrams": {}}
    return render(request, "documentacao/explorador.html", {
        "explorer": explorer,
        "source": source,
        "categories": DocumentationService.categories(),
        "navigation": DocumentationService.navigation(),
        "active_category": "OTRS" if source == "otrs" else "CMDB ServiceUp",
    })


@login_required
@access_required("manage_groups")
def documentation_search(request):
    query = request.GET.get("q", "").strip()[:120]
    results = []
    if query:
        try:
            results = DocumentationService.search(query)
        except (OSError, UnicodeError):
            logger.exception("Falha durante a busca na documentação")
    return render(request, "documentacao/busca.html", {
        "query": query,
        "results": results,
        "categories": DocumentationService.categories(),
        "navigation": DocumentationService.navigation(),
        "active_category": "Funcionalidades",
    })


@login_required
def documentation_releases(request):
    catalog = release_catalog()
    selected_version = request.GET.get("version", "").strip()
    selected_release = next(
        (release for release in catalog["releases"] if release["version"] == selected_version),
        catalog["current_release"],
    )
    return render(request, "documentacao/releases.html", {
        **catalog,
        "selected_release": selected_release,
        "release_only": True,
        "active_section": "releases",
    })


@login_required
@require_GET
def preferences(request):
    preference, _ = UserPreference.objects.get_or_create(user=request.user)
    return JsonResponse({"theme": preference.theme})


@login_required
@require_POST
def update_theme(request):
    media_error = require_media_type(request, "application/json")
    if media_error:
        return media_error
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return api_error(request, "BAD_REQUEST")
    if not isinstance(payload, dict) or payload.get("theme") not in UserPreference.Theme.values:
        return api_error(request, "VALIDATION_ERROR", status=400)

    preference, _ = UserPreference.objects.get_or_create(user=request.user)
    preference.theme = payload["theme"]
    preference.save(update_fields=("theme", "updated_at"))
    return JsonResponse({"theme": preference.theme})
