import logging
import os
import re
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import connections
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from aplicativos.auditoria.services import AuditService
from aplicativos.configuracoes.access_control import is_system_admin

from .clients.cmdb import CMDBClient
from .clients.factory import configured_otrs_client, otrs_configuration_notice
from .forms import (
    ArionDatabaseCandidateForm, BrevoConfigurationForm, CMDBConfigurationForm, CMDBGetForm,
    CMDBSearchForm, MapboxConfigurationForm, OTRSConfigurationForm,
)
from .models import (
    CMDBLinkMirror, CMDBOperatorMirror, ExternalAPIHealthCheck, ExternalAPIIntegration,
    Integration, IntegrationLease, IntegrationRun, IntegrationSyncState, OTRSTicket,
)
from .services.errors import (
    IntegrationError,
    IntegrationNotConfigured,
    OTRSCredentialMissingError,
)
from .services.secrets import get_secret_protector
from .clients.brevo import BrevoTransactionalClient
from .clients.brevo_smtp import test_brevo_smtp_auth
from .services.external_apis import (
    decrypt_external_api_secret, decrypt_external_api_smtp_secret,
    get_mapbox_runtime_state, is_public_mapbox_token,
)
from .services.analytics import TicketQueryService
from aplicativos.qualidade.models import QualityCorrelationCheckpoint, QualityTreatment
from .services.arion_database import (
    active_database_summary, activate_candidate, candidate_config,
    migration_state, password_for_connection_test, read_bootstrap_config,
    record_candidate_test, rollback_to_previous, save_candidate_config,
    test_mysql_connection,
)
from .services.arion_database_migration import (
    CandidateStageError, copy_local_data, dry_run_migration, log_candidate_stage_failure,
    safe_stage_error_detail, prepare_candidate_schema, validate_candidate_migration,
)

logger = logging.getLogger(__name__)

INTEGRATION_DEFAULTS = {
    Integration.Key.CMDB: {"name": "CMDB ServiceUp", "kind": "API REST · GenericInterface"},
    Integration.Key.OTRS: {"name": "OTRS MySQL", "kind": "MySQL · somente leitura"},
}


def get_integration(key):
    defaults = INTEGRATION_DEFAULTS.get(key)
    if defaults is None:
        raise IntegrationNotConfigured()
    integration, _ = Integration.objects.get_or_create(key=key, defaults=defaults)
    return integration


def _sync_integration_runtime_status(integration, sync_state=None):
    if sync_state is None:
        sync_state = IntegrationSyncState.objects.filter(key=integration.key).first()
    if not sync_state or not sync_state.last_success_at:
        return integration

    needs_save = False
    update_fields = []

    if not integration.last_read_at or sync_state.last_success_at > integration.last_read_at:
        integration.last_read_at = sync_state.last_success_at
        integration.last_success_at = sync_state.last_success_at
        needs_save = True
        update_fields.extend(["last_read_at", "last_success_at"])

    if not integration.last_error_at or sync_state.last_success_at >= integration.last_error_at:
        if integration.status != Integration.Status.CONNECTED:
            integration.status = Integration.Status.CONNECTED
            needs_save = True
            update_fields.append("status")
        if integration.last_error:
            integration.last_error = ""
            needs_save = True
            update_fields.append("last_error")

    if needs_save:
        integration.save(update_fields=list(dict.fromkeys([*update_fields, "updated_at"])))
    return integration


def _admin_context_for_integration(request, section, **extra):
    context = {"active_section": section, "is_access_admin": is_system_admin(request.user), "administration": True}
    context.update(extra)
    return context


def _configuration_form(integration):
    initial = {**(integration.config or {}), "username": integration.username, "password": ""}
    if integration.key == Integration.Key.CMDB:
        return CMDBConfigurationForm(initial=initial)
    return OTRSConfigurationForm(initial=initial)


def _cmdb_client(integration):
    if not integration.credentials_configured:
        raise IntegrationNotConfigured()
    password = get_secret_protector().unprotect(integration.secret_ciphertext)
    config = integration.config or {}
    return CMDBClient(
        base_url=config.get("base_url", ""), username=integration.username, password=password,
        timeout=config.get("timeout", 15), verify_tls=config.get("verify_tls", True),
        batch_size=config.get("batch_size", 50),
    )


def _otrs_client(integration):
    return configured_otrs_client(integration)


def _request_id(request):
    return getattr(request, "request_id", request.headers.get("X-Request-ID", ""))[:80]


def _require_permission(request, codename):
    if not request.user.has_perm(f"configuracoes.{codename}"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied


def _run_operation(*, request, integration, operation, callback):
    run = IntegrationRun.objects.create(
        integration=integration, operation=operation, status=IntegrationRun.Status.RUNNING,
        request_id=_request_id(request), triggered_by=request.user,
    )
    started = time.perf_counter()
    try:
        result = callback()
        run.status = IntegrationRun.Status.SUCCESS
        run.message = "Operação concluída com sucesso."
        run.records_found = len(result) if isinstance(result, (list, tuple)) else None
        run.records_processed = run.records_found or 0
        integration.last_success_at = timezone.now()
        integration.last_read_at = integration.last_success_at
        integration.last_error = ""
        error = ""
    except IntegrationError as exc:
        result = None
        error = exc.safe_message
        if integration.key == Integration.Key.CMDB and exc.error_code:
            error = f"{error} Código técnico: {exc.error_code}"
        run.status = IntegrationRun.Status.FAILED
        run.message = error
        if isinstance(exc, (IntegrationNotConfigured, OTRSCredentialMissingError)):
            integration.status = Integration.Status.NOT_CONFIGURED
        else:
            integration.status = Integration.Status.UNAVAILABLE
        integration.last_error = error
        integration.last_error_at = timezone.now()
        if integration.key == Integration.Key.CMDB:
            logger.warning(
                "CMDB operation failed integration=cmdb operation=%s request_id=%s http_status=%s exception=%s error_code=%s duration_ms=%s",
                operation, run.request_id, exc.http_status or "unknown", type(exc).__name__, exc.error_code or "", max(0, int((time.perf_counter() - started) * 1000)),
            )
        else:
            logger.warning(
                "External integration operation failed integration=%s operation=%s exception=%s duration_ms=%s",
                integration.key, operation, type(exc).__name__, max(0, int((time.perf_counter() - started) * 1000)),
            )
    except Exception as exc:
        # Do not log exception text or traceback: a provider error could include connection secrets.
        logger.warning(
            "External integration operation failed integration=%s operation=%s request_id=%s exception=%s duration_ms=%s",
            integration.key, operation, run.request_id, type(exc).__name__, max(0, int((time.perf_counter() - started) * 1000)),
        )
        result = None
        error = "Não foi possível concluir a operação da integração."
        run.status = IntegrationRun.Status.FAILED
        run.message = error
        integration.status = Integration.Status.UNAVAILABLE
        integration.last_error = error
        integration.last_error_at = timezone.now()
    else:
        integration.status = Integration.Status.CONNECTED
    finally:
        elapsed = max(0, int((time.perf_counter() - started) * 1000))
        run.duration_ms = elapsed
        run.finished_at = timezone.now()
        run.save(update_fields=("status", "message", "records_found", "records_processed", "duration_ms", "finished_at"))
        if operation == IntegrationRun.Operation.TEST:
            integration.last_test_at = run.finished_at
            integration.last_latency_ms = elapsed
            integration.save(update_fields=("status", "last_test_at", "last_latency_ms", "last_success_at", "last_error_at", "last_error", "last_read_at", "updated_at"))
        else:
            integration.save(update_fields=("status", "last_success_at", "last_error_at", "last_error", "last_read_at", "updated_at"))
    AuditService.record(
        request=request, user=request.user, category="INTEGRATION", action="INTEGRATION_TESTED" if operation == IntegrationRun.Operation.TEST else "INTEGRATION_READ",
        module="Integrações", description="Teste de integração concluído" if operation == IntegrationRun.Operation.TEST else "Leitura de diagnóstico concluída",
        obj=integration, result="FAILED" if error else "SUCCESS",
        metadata={"integration": integration.key, "operation": operation, "status": run.status, "duration_ms": run.duration_ms, "records_found": run.records_found},
    )
    return run, result, error


def _api_card(provider):
    integration = ExternalAPIIntegration.objects.filter(provider=provider).first()
    if provider == ExternalAPIIntegration.Provider.MAPBOX:
        runtime = get_mapbox_runtime_state()
        configured = runtime["configured"]
        if integration is None:
            return {
                "status": "available" if configured else "not_configured",
                "status_label": runtime["status"],
                "credential_status": "Token público disponível" if configured else "Não configurada",
                "last_test_at": None, "last_latency_ms": None, "usage": "Disponível para telas autorizadas" if configured else "Sem uso",
            }
        valid = is_public_mapbox_token((integration.config or {}).get("public_token", ""))
        status = "error" if integration.health == ExternalAPIIntegration.Health.ERROR or (integration.credential_configured and not valid) else "available" if integration.health == ExternalAPIIntegration.Health.AVAILABLE and valid else "configured" if valid else "not_configured"
        return {
            "status": status, "status_label": integration.status_display if valid else "Token público inválido ou ausente",
            "credential_status": "Token público cadastrado" if valid else "Não configurada",
            "last_test_at": integration.last_test_at, "last_latency_ms": integration.last_latency_ms,
            "usage": "Disponível para telas autorizadas" if integration.enabled and valid else "Desabilitada",
        }
    if integration is None:
        return {"status": "not_configured", "status_label": "Não configurada", "credential_status": "Não configurada", "last_test_at": None, "last_latency_ms": None, "usage": "Envio de TQ desabilitado"}
    return {
        "status": integration.health if integration.health in ("available", "error") else "configured" if integration.credential_configured else "not_configured",
        "status_label": integration.status_display,
        "credential_status": "Chave protegida cadastrada" if integration.credential_configured else "Não configurada",
        "last_test_at": integration.last_test_at, "last_latency_ms": integration.last_latency_ms,
        "usage": "Envio de TQ sob confirmação individual" if integration.enabled and integration.allow_real_sends else "Envio de TQ desabilitado",
    }


@login_required
@require_GET
def home(request):
    _require_permission(request, "view_integrations")
    cmdb = get_integration(Integration.Key.CMDB)
    otrs = get_integration(Integration.Key.OTRS)
    cmdb_state = IntegrationSyncState.objects.filter(key="cmdb").first()
    otrs_state = IntegrationSyncState.objects.filter(key="otrs").first()
    otrs_setup_notice = otrs_configuration_notice(otrs)
    _sync_integration_runtime_status(cmdb, cmdb_state)
    _sync_integration_runtime_status(otrs, otrs_state)
    worker_lease = IntegrationLease.objects.filter(key="worker").first()
    worker_alive = bool(worker_lease and worker_lease.expires_at > timezone.now())
    cmdb_age = timezone.now() - cmdb_state.last_success_at if cmdb_state and cmdb_state.last_success_at else None
    otrs_age = timezone.now() - otrs_state.last_success_at if otrs_state and otrs_state.last_success_at else None
    quality_checkpoints = list(QualityCorrelationCheckpoint.objects.all())
    quality_successes = [item.last_success_at for item in quality_checkpoints if item.last_success_at]
    quality_errors = [item.last_error_at for item in quality_checkpoints if item.last_error_at]
    quality_last_read = max(quality_successes) if quality_successes else None
    quality_last_attempt = max([*quality_successes, *quality_errors]) if quality_successes or quality_errors else None
    quality_last_error = next((item.last_error for item in sorted(
        (item for item in quality_checkpoints if item.last_error_at),
        key=lambda item: item.last_error_at, reverse=True,
    )), "")
    if otrs_setup_notice:
        quality_correlation_status = "Suspensa · configuração pendente"
    elif quality_last_error:
        quality_correlation_status = "Com falha"
    elif quality_last_read:
        quality_correlation_status = "Leitura realizada"
    else:
        quality_correlation_status = "Aguardando primeira leitura"
    otrs_retry_at = None
    if otrs_state and otrs_state.last_error_at and otrs_state.failure_count:
        retry_minutes = min(30, 2 ** min(max(otrs_state.failure_count, 1), 5))
        otrs_retry_at = otrs_state.last_error_at + timedelta(minutes=retry_minutes)
    freshness = {
        "cmdb": "fresh" if cmdb_age and cmdb_age.total_seconds() <= 20 * 60 else "stale" if cmdb_state and cmdb_state.last_success_at else "unavailable",
        "otrs": "fresh" if otrs_age and otrs_age.total_seconds() <= 3 * 60 else "stale" if otrs_state and otrs_state.last_success_at else "unavailable",
    }
    database_health = {"status": "Indisponível", "online": False, "latency_ms": None, "engine": "Desconhecido", "environment": "Desenvolvimento" if settings.DEBUG else "Produção", "name": ""}
    engine = connections["default"].settings_dict.get("ENGINE", "")
    database_health["engine"] = "SQLite" if "sqlite" in engine else "MySQL" if "mysql" in engine else connections["default"].vendor.upper()
    database_health["name"] = str(connections["default"].settings_dict.get("NAME", "")).split("/")[-1].split("\\")[-1]
    started = time.perf_counter()
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        database_health.update(status="Online", online=True, latency_ms=max(0, int((time.perf_counter() - started) * 1000)))
    except Exception:
        database_health["status"] = "Indisponível"
    context = _admin_context_for_integration(
        request, "integrations", cmdb=cmdb, otrs=otrs, database_health=database_health,
        otrs_setup_notice=otrs_setup_notice,
        cmdb_sync=cmdb_state, otrs_sync=otrs_state, freshness=freshness,
        otrs_retry_at=otrs_retry_at,
        quality_correlation_status=quality_correlation_status,
        quality_last_read=quality_last_read,
        quality_last_attempt=quality_last_attempt,
        quality_last_error=quality_last_error,
        quality_confirmed_links=QualityTreatment.objects.filter(quality_ticket_external_id__isnull=False).count(),
        worker_alive=worker_alive,
        cmdb_links=CMDBLinkMirror.objects.filter(is_present_in_source=True).count(),
        cmdb_operators=CMDBOperatorMirror.objects.filter(is_present_in_source=True).count(),
        otrs_tickets=TicketQueryService.noc_tickets().count(),
        otrs_open=TicketQueryService.business_open().count(),
        otrs_fast=IntegrationRun.objects.filter(operation=IntegrationRun.Operation.OTRS_FAST_SYNC).first(),
        otrs_full=IntegrationRun.objects.filter(operation=IntegrationRun.Operation.OTRS_FULL_SYNC).first(),
        mapbox_configured=get_mapbox_runtime_state()["configured"],
        brevo_api=_api_card(ExternalAPIIntegration.Provider.BREVO),
        mapbox_api=_api_card(ExternalAPIIntegration.Provider.MAPBOX),
    )
    return render(request, "integracoes/home.html", context)


@login_required
@require_GET
def arion_detail(request):
    _require_permission(request, "view_integrations")
    candidate = candidate_config(decrypt=False) or {}
    initial = {key: candidate.get(key, "") for key in ("database", "host", "port", "user", "timeout")}
    initial.setdefault("database", "arion_geo")
    initial.setdefault("host", "127.0.0.1")
    initial.setdefault("port", 3306)
    initial.setdefault("timeout", 10)
    return render(request, "integracoes/arion.html", _arion_context(request, ArionDatabaseCandidateForm(initial=initial)))


def _arion_context(request, form):
    active = active_database_summary(settings.DATABASES["default"])
    bootstrap, _ = read_bootstrap_config()
    if os.environ.get("DATABASE_URL", "").strip():
        next_boot = active
    else:
        active_config = bootstrap["active"]
        next_name = str(active_config.get("database", ""))
        next_boot = {
            "engine": "MySQL" if active_config.get("engine") == "mysql" else "SQLite",
            "name": next_name if active_config.get("engine") == "mysql" else next_name.replace("\\", "/").rsplit("/", 1)[-1],
        }
    health = {"status": "Indisponivel", "online": False, "latency_ms": None,
              "engine": active["engine"], "environment": "Desenvolvimento" if settings.DEBUG else "Producao",
              "name": active["name"]}
    started = time.perf_counter()
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        health.update(status="Online", online=True, latency_ms=max(0, int((time.perf_counter() - started) * 1000)))
    except Exception:
        pass
    return _admin_context_for_integration(
        request, "integrations", database_health=health, active_database=active,
        next_boot_database=next_boot,
        restart_required=(active["engine"], active["name"]) != (next_boot["engine"], next_boot["name"]),
        pending_database=candidate_config(decrypt=False) or {}, database_form=form,
        migration_state=migration_state(), rollback_available=bool(
            (bootstrap.get("rollback") or {}).get("engine") == "sqlite"
            or (active["engine"] == "MySQL" and not os.environ.get("DATABASE_URL", "").strip()
                and (settings.BASE_DIR / "db.sqlite3").is_file())
        ),
        database_url_override=bool(os.environ.get("DATABASE_URL", "").strip()),
    )


@login_required
@require_POST
def arion_save_configuration(request):
    _require_permission(request, "view_integrations")
    _require_permission(request, "manage_integrations")
    form = ArionDatabaseCandidateForm(request.POST)
    if form.is_valid():
        try:
            saved = save_candidate_config(form.cleaned_data)
        except Exception as exc:
            form.add_error(None, exc.messages[0] if hasattr(exc, "messages") else "Nao foi possivel salvar o candidato MySQL.")
        else:
            AuditService.record(request=request, category="INTEGRATION", action="ARION_DATABASE_CONFIG_SAVED",
                                module="Banco Arion", description="Banco MySQL candidato salvo",
                                result="SUCCESS", metadata={"engine": saved["engine"]})
            messages.success(request, "MySQL salvo como candidato. O banco ativo permanece inalterado.")
            return redirect("configuracoes:integracoes:arion")
    return render(request, "integracoes/arion.html", _arion_context(request, form))


@login_required
@require_POST
def arion_test_connection(request):
    _require_permission(request, "view_integrations")
    _require_permission(request, "manage_integrations")
    form = ArionDatabaseCandidateForm(request.POST)
    if not form.is_valid():
        return render(request, "integracoes/arion.html", _arion_context(request, form))
    values = form.cleaned_data
    try:
        save_candidate_config(values)
        candidate = candidate_config(decrypt=False)
        password = password_for_connection_test(candidate)
        result = test_mysql_connection(candidate, password)
        record_candidate_test()
        messages.success(request, f"Conexao do candidato OK - MySQL {result['version'].split('.', 1)[0]}.x - Banco: {result['database']}.")
        AuditService.record(request=request, category="INTEGRATION", action="ARION_DATABASE_TESTED",
                            module="Banco Arion", description="Teste do MySQL candidato concluido",
                            result="SUCCESS", metadata={"engine": "mysql"})
    except Exception as exc:
        safe_message = exc.messages[0] if hasattr(exc, "messages") else "Falha segura ao testar o Banco Arion."
        messages.error(request, safe_message)
        AuditService.record(request=request, category="INTEGRATION", action="ARION_DATABASE_TESTED",
                            module="Banco Arion", description="Teste do MySQL candidato falhou",
                            result="FAILURE", metadata={"engine": "mysql", "error_type": type(exc).__name__})
    return render(request, "integracoes/arion.html", _arion_context(request, form))


@login_required
@require_POST
def arion_migration_action(request, action):
    _require_permission(request, "view_integrations")
    _require_permission(request, "manage_integrations")
    outcome = "SUCCESS"
    exception_type = ""
    technical_error = None
    try:
        if action == "prepare":
            report = prepare_candidate_schema()
            if report["newly_applied"]:
                messages.success(request, f"Schema MySQL preparado com sucesso. {report['newly_applied']} migrations aplicadas.")
            else:
                messages.success(request, "Schema ja estava atualizado. Nenhuma migration pendente.")
        elif action == "copy":
            report = copy_local_data()
            messages.success(request, f"Dados locais copiados. Models preservados: {len(report['copied_counts'])}.")
        elif action == "validate":
            validate_candidate_migration()
            messages.success(request, "Validacao aprovada. MySQL pode ser ativado para o proximo inicio.")
        elif action == "activate":
            activate_candidate()
            messages.success(request, "MySQL ativado para o proximo inicio. Reinicie o servidor web e integration_worker.")
        elif action == "rollback":
            rollback_to_previous()
            messages.success(request, "SQLite restaurado como banco ativo para o proximo inicio.")
        elif action == "dry-run":
            report = dry_run_migration()
            privileges = report["required_privileges"]
            if privileges["sufficient"] is False:
                messages.warning(request, "Dry run: o usuario MySQL nao possui todos os privilegios necessarios para preparar o schema.")
            elif privileges["sufficient"] is None:
                messages.warning(request, "Dry run: conexao OK, mas nao foi possivel verificar grants do usuario MySQL.")
            else:
                messages.success(request, f"Dry run OK. Schema: {'presente' if report['migration_table_exists'] else 'sem migrations'}; conflitos de usuario: {report['username_conflict_count']}.")
        else:
            messages.error(request, "Acao de banco invalida.")
    except Exception as exc:
        outcome = "FAILURE"
        exception_type = type(exc).__name__
        if not isinstance(exc, CandidateStageError):
            log_candidate_stage_failure(action, exc)
        if isinstance(exc, CandidateStageError):
            detail = exc.technical_detail
            if request.user.is_superuser or is_system_admin(request.user):
                technical_error = {"stage": action, "detail": detail}
                messages.error(request, f"Falha na etapa {action}. Veja os detalhes tecnicos abaixo.")
            else:
                messages.error(request, "A etapa do Banco Arion falhou. Consulte o log tecnico seguro.")
        elif hasattr(exc, "messages"):
            messages.error(request, exc.messages[0])
        elif request.user.is_superuser or is_system_admin(request.user):
            technical_error = {"stage": action, "detail": safe_stage_error_detail(exc)}
            messages.error(request, f"Falha na etapa {action}. Veja os detalhes tecnicos abaixo.")
        else:
            messages.error(request, "A etapa do Banco Arion falhou. Consulte o log tecnico seguro.")
    AuditService.record(request=request, category="INTEGRATION", action="ARION_DATABASE_STAGE",
                        module="Banco Arion", description="Etapa do candidato MySQL executada",
                        result=outcome, metadata={"stage": action, "exception_type": exception_type})
    if technical_error:
        form = ArionDatabaseCandidateForm(initial=candidate_config(decrypt=False) or {})
        return render(request, "integracoes/arion.html",
                      {**_arion_context(request, form), "technical_error": technical_error})
    return redirect("configuracoes:integracoes:arion")


@login_required
@require_GET
def cmdb_detail(request):
    _require_permission(request, "view_integrations")
    integration = get_integration(Integration.Key.CMDB)
    cmdb_state = IntegrationSyncState.objects.filter(key="cmdb").first()
    _sync_integration_runtime_status(integration, cmdb_state)
    context = _admin_context_for_integration(
        request, "integrations", integration=integration, form=_configuration_form(integration),
        search_form=CMDBSearchForm(), get_form=CMDBGetForm(), active_tab=request.GET.get("tab", "overview"),
        runs=integration.runs.all()[:8], results=None, sync_state=cmdb_state,
    )
    return render(request, "integracoes/detail.html", context)


@login_required
@require_GET
def otrs_detail(request):
    _require_permission(request, "view_integrations")
    integration = get_integration(Integration.Key.OTRS)
    otrs_state = IntegrationSyncState.objects.filter(key="otrs").first()
    _sync_integration_runtime_status(integration, otrs_state)
    context = _admin_context_for_integration(
        request, "integrations", integration=integration, form=_configuration_form(integration),
        active_tab=request.GET.get("tab", "overview"), runs=integration.runs.all()[:8], results=None,
        sync_state=otrs_state, otrs_setup_notice=otrs_configuration_notice(integration),
    )
    return render(request, "integracoes/detail.html", context)


@login_required
@require_POST
def save_configuration(request, key):
    # Keep the permission check at the view boundary with the existing Django permission system.
    return _save_configuration_guarded(request, key)


def _save_configuration_guarded(request, key):
    _require_permission(request, "manage_integrations")
    if key not in INTEGRATION_DEFAULTS:
        from django.http import Http404
        raise Http404
    integration = get_integration(key)
    form_class = CMDBConfigurationForm if key == Integration.Key.CMDB else OTRSConfigurationForm
    form = form_class(request.POST)
    if form.is_valid():
        values = dict(form.cleaned_data)
        new_password = values.pop("password")
        username = values.pop("username")
        encrypted = integration.secret_ciphertext
        if new_password:
            # Encrypt before touching the row. On non-Windows production hosts this fails closed.
            try:
                encrypted = get_secret_protector().protect(new_password)
            except IntegrationError as exc:
                form.add_error("password", exc.safe_message)
                context = _admin_context_for_integration(request, "integrations", integration=integration, form=form, active_tab="configuration", runs=integration.runs.all()[:8], results=None)
                if key == Integration.Key.CMDB:
                    context.update(search_form=CMDBSearchForm(), get_form=CMDBGetForm())
                return render(request, "integracoes/detail.html", context)
        changed = sorted(values.keys())
        merged_config = {**(integration.config or {}), **values}
        host_changed = values.get("host") != (integration.config or {}).get("host")
        user_changed = bool(username and username != integration.username)
        integration.config = merged_config
        integration.username = username
        integration.secret_ciphertext = encrypted
        integration.status = Integration.Status.NOT_CONFIGURED
        integration.last_error = ""
        update_fields = ["config", "username", "secret_ciphertext", "status", "last_error", "updated_at"]
        if key == Integration.Key.OTRS and (host_changed or user_changed):
            integration.accessible_databases = []
            update_fields.append("accessible_databases")
        integration.save(update_fields=update_fields)
        AuditService.record(request=request, user=request.user, category="INTEGRATION", action="INTEGRATION_CONFIG_UPDATED", module="Integrações", description="Configuração da integração atualizada", obj=integration, metadata={"integration": key, "fields": changed})
        if new_password:
            AuditService.record(request=request, user=request.user, category="INTEGRATION", action="INTEGRATION_CREDENTIALS_UPDATED", module="Integrações", description="Credenciais da integração atualizadas", obj=integration, metadata={"integration": key, "credentials_configured": True})
        messages.success(request, "Configuração salva com segurança.")
        return redirect("configuracoes:integracoes:cmdb" if key == Integration.Key.CMDB else "configuracoes:integracoes:otrs")
    detail_name = "configuracoes:integracoes:cmdb" if key == Integration.Key.CMDB else "configuracoes:integracoes:otrs"
    context = _admin_context_for_integration(request, "integrations", integration=integration, form=form, active_tab="configuration", runs=integration.runs.all()[:8], results=None)
    if key == Integration.Key.CMDB:
        context.update(search_form=CMDBSearchForm(), get_form=CMDBGetForm())
    return render(request, "integracoes/detail.html", context)


@login_required
@require_POST
def toggle_integration(request, key):
    _require_permission(request, "manage_integrations")
    integration = get_integration(key) if key in INTEGRATION_DEFAULTS else None
    if integration is None:
        from django.http import Http404
        raise Http404
    integration.enabled = not integration.enabled
    integration.save(update_fields=("enabled", "updated_at"))
    action = "INTEGRATION_ENABLED" if integration.enabled else "INTEGRATION_DISABLED"
    AuditService.record(request=request, user=request.user, category="INTEGRATION", action=action, module="Integrações", description="Integração ativada" if integration.enabled else "Integração desativada", obj=integration, metadata={"integration": key, "enabled": integration.enabled})
    messages.success(request, "Integração ativada." if integration.enabled else "Integração desativada.")
    return redirect("configuracoes:integracoes:cmdb" if key == Integration.Key.CMDB else "configuracoes:integracoes:otrs")


@login_required
@require_POST
def remove_credentials(request, key):
    _require_permission(request, "manage_integrations")
    if request.POST.get("confirm") != "yes":
        messages.error(request, "Confirme a remoção das credenciais.")
        return redirect("configuracoes:integracoes:cmdb" if key == Integration.Key.CMDB else "configuracoes:integracoes:otrs")
    integration = get_object_or_404(Integration, key=key)
    integration.username = ""
    integration.secret_ciphertext = ""
    integration.status = Integration.Status.NOT_CONFIGURED
    integration.last_error = ""
    integration.save(update_fields=("username", "secret_ciphertext", "status", "last_error", "updated_at"))
    AuditService.record(request=request, user=request.user, category="INTEGRATION", action="INTEGRATION_CREDENTIALS_UPDATED", module="Integrações", description="Credenciais da integração removidas", obj=integration, metadata={"integration": key, "credentials_configured": False})
    messages.success(request, "Credenciais removidas.")
    return redirect("configuracoes:integracoes:cmdb" if key == Integration.Key.CMDB else "configuracoes:integracoes:otrs")


@login_required
@require_POST
def cmdb_test(request):
    _require_permission(request, "view_integrations")
    _require_permission(request, "run_integrations")
    integration = get_integration(Integration.Key.CMDB)
    run, _, error = _run_operation(request=request, integration=integration, operation=IntegrationRun.Operation.TEST, callback=lambda: _cmdb_client(integration).test_connection())
    messages.error(request, error) if error else messages.success(request, f"Conectado. Latência: {run.duration_ms} ms.")
    return redirect("configuracoes:integracoes:cmdb")


@login_required
@require_POST
def otrs_test(request):
    _require_permission(request, "view_integrations")
    _require_permission(request, "run_integrations")
    integration = get_integration(Integration.Key.OTRS)

    def test():
        client = _otrs_client(integration)
        client.test_connection()
        integration.accessible_databases = client.list_databases()
        integration.save(update_fields=("accessible_databases", "updated_at"))
        return integration.accessible_databases

    run, databases, error = _run_operation(request=request, integration=integration, operation=IntegrationRun.Operation.TEST, callback=test)
    if error:
        messages.error(request, error)
    else:
        integration.refresh_from_db()
        messages.success(request, f"Conectado. Latência: {run.duration_ms} ms. Bancos acessíveis: {len(integration.accessible_databases)}.")
    return redirect("configuracoes:integracoes:otrs")


@login_required
@require_POST
def cmdb_diagnose(request):
    _require_permission(request, "view_integrations")
    _require_permission(request, "run_integrations")
    integration = get_integration(Integration.Key.CMDB)
    operation = request.POST.get("operation")
    if operation == "get":
        form = CMDBGetForm(request.POST)
        search_form = CMDBSearchForm()
        if form.is_valid():
            run, items, error = _run_operation(request=request, integration=integration, operation=IntegrationRun.Operation.READ, callback=lambda: _cmdb_client(integration).get_items(form.cleaned_data["config_item_ids"]))
            results = CMDBClient.sanitize_items(items or [])
        else:
            run, error, results = None, "Revise os ConfigItemID informados.", None
    else:
        search_form = CMDBSearchForm(request.POST)
        form = CMDBGetForm()
        if search_form.is_valid():
            values = search_form.cleaned_data
            callback = lambda: _cmdb_client(integration).search_items(class_name=values["class_name"], number=values["number"], name=values["name"], cixml_data=values["cixml_data"], limit=values["limit"])
            run, items, error = _run_operation(request=request, integration=integration, operation=IntegrationRun.Operation.READ, callback=callback)
            results = CMDBClient.sanitize_items(items or [])
        else:
            run, error, results = None, "Revise os filtros informados.", None
    if error:
        messages.error(request, error)
    elif results is not None:
        messages.success(request, f"Consulta concluída. Registros encontrados: {len(results)}.")
    context = _admin_context_for_integration(request, "integrations", integration=integration, form=_configuration_form(integration), search_form=search_form, get_form=form, active_tab="diagnostics", runs=integration.runs.all()[:8], results=results, diagnostic_run=run)
    return render(request, "integracoes/detail.html", context)


@login_required
@require_GET
def otrs_schema(request):
    _require_permission(request, "view_integrations")
    _require_permission(request, "inspect_integrations")
    integration = get_integration(Integration.Key.OTRS)
    tables, error, run = None, "", None
    if integration.credentials_configured and integration.config.get("database"):
        run, tables, error = _run_operation(request=request, integration=integration, operation=IntegrationRun.Operation.INSPECTION, callback=lambda: _otrs_client(integration).list_tables())
    elif not integration.config.get("database"):
        error = "Selecione um banco na configuração para inspecionar as tabelas."
    context = _admin_context_for_integration(request, "integrations", integration=integration, form=_configuration_form(integration), active_tab="schema", runs=integration.runs.all()[:8], tables=tables, results=None, schema_error=error, search=request.GET.get("q", "").strip())
    return render(request, "integracoes/detail.html", context)


@login_required
@require_GET
def otrs_table_detail(request, table_name):
    _require_permission(request, "view_integrations")
    _require_permission(request, "inspect_integrations")
    integration = get_integration(Integration.Key.OTRS)

    def inspect():
        client = _otrs_client(integration)
        return {
            "columns": client.get_table_schema(table_name),
            "indexes": client.get_table_indexes(table_name),
            "foreign_keys": client.get_foreign_keys(table_name),
            "sample_rows": client.get_sample_rows(table_name, page=request.GET.get("page", 1), page_size=50),
        }

    run, detail, error = _run_operation(request=request, integration=integration, operation=IntegrationRun.Operation.INSPECTION, callback=inspect)
    page = request.GET.get("page", "1")
    context = _admin_context_for_integration(request, "integrations", integration=integration, form=_configuration_form(integration), active_tab="schema", runs=integration.runs.all()[:8], table_name=table_name, table_detail=detail, schema_error=error, results=None, sample_page=page)
    return render(request, "integracoes/detail.html", context)


@login_required
@require_GET
def run_history(request):
    _require_permission(request, "view_integrations")
    runs = IntegrationRun.objects.select_related("integration", "triggered_by")[:100]
    return render(request, "integracoes/history.html", _admin_context_for_integration(request, "integrations", runs=runs))


@login_required
@require_GET
def run_detail(request, pk):
    _require_permission(request, "view_integrations")
    run = get_object_or_404(IntegrationRun.objects.select_related("integration", "triggered_by"), pk=pk)
    return render(request, "integracoes/run_detail.html", _admin_context_for_integration(request, "integrations", run=run))


def _external_api_page_context(request, provider, form=None):
    integration = ExternalAPIIntegration.objects.filter(provider=provider).first()
    has_database_record = integration is not None
    can_manage = request.user.has_perm("configuracoes.manage_integrations")
    if integration is None:
        name = "Brevo" if provider == ExternalAPIIntegration.Provider.BREVO else "Mapbox"
        integration = ExternalAPIIntegration(provider=provider, name=name)
    if provider == ExternalAPIIntegration.Provider.BREVO:
        method = (integration.config or {}).get("send_method")
        if not method:
            method = "api_rest" if integration.secret_ciphertext and not integration.smtp_secret_ciphertext else "smtp"
        if form is None and can_manage:
            form = BrevoConfigurationForm(initial={
                **(integration.config or {}), "enabled": integration.enabled,
                "send_method": method,
                "allow_real_sends": integration.allow_real_sends,
                "quality_recipient_validated": bool((integration.config or {}).get("quality_recipient_validated")),
                "routing_test_recipient": (integration.config or {}).get("routing_test_recipient", ""),
                "routing_operational_test_approved": bool((integration.config or {}).get("routing_operational_test_approved")),
                "quality_cc": (integration.config or {}).get("quality_cc", ""),
                "api_credential_configured": bool(integration.secret_ciphertext),
                "smtp_credential_configured": bool(integration.smtp_secret_ciphertext),
            })
        credential_configured = bool(integration.smtp_secret_ciphertext if method == "smtp" else integration.secret_ciphertext)
    else:
        if form is None and can_manage:
            enabled = integration.enabled if has_database_record else get_mapbox_runtime_state()["enabled"]
            form = MapboxConfigurationForm(initial={"enabled": enabled})
        credential_configured = is_public_mapbox_token((integration.config or {}).get("public_token", ""))
    return {
        "integration": integration,
        "api_form": form if can_manage else None,
        "can_manage_integrations": can_manage,
        "can_test_integrations": request.user.has_perm("configuracoes.run_integrations"),
        "credential_configured": credential_configured,
        "api_credential_configured": bool(integration.secret_ciphertext) if provider == ExternalAPIIntegration.Provider.BREVO else False,
        "smtp_credential_configured": bool(integration.smtp_secret_ciphertext) if provider == ExternalAPIIntegration.Provider.BREVO else False,
        "checks": integration.checks.all()[:10] if integration.pk else (),
        "mapbox_runtime": get_mapbox_runtime_state() if provider == ExternalAPIIntegration.Provider.MAPBOX else None,
        "allow_real_sends": integration.allow_real_sends if provider == ExternalAPIIntegration.Provider.BREVO else False,
        "brevo_send_method": method if provider == ExternalAPIIntegration.Provider.BREVO else "",
        "routing_test_id": f"ARION-ROUTE-{uuid.uuid4().hex.upper()}" if provider == ExternalAPIIntegration.Provider.BREVO else "",
    }


@login_required
@require_GET
def brevo_detail(request):
    _require_permission(request, "view_integrations")
    return render(request, "integracoes/brevo.html", _external_api_page_context(request, ExternalAPIIntegration.Provider.BREVO))


@login_required
@require_POST
def brevo_save(request):
    _require_permission(request, "view_integrations")
    _require_permission(request, "manage_integrations")
    current = ExternalAPIIntegration.objects.filter(provider=ExternalAPIIntegration.Provider.BREVO).first()
    form = BrevoConfigurationForm(request.POST, initial={
        "api_credential_configured": bool(current and current.secret_ciphertext),
        "smtp_credential_configured": bool(current and current.smtp_secret_ciphertext),
    })
    if form.is_valid():
        values = form.cleaned_data
        previous_recipient = (current.config or {}).get("quality_recipient", "") if current else ""
        recipient_changed = str(previous_recipient).strip().casefold() != str(values["quality_recipient"] or "").strip().casefold()
        if recipient_changed and values["quality_recipient_validated"]:
            form.add_error(
                "quality_recipient_validated",
                "A mudança do destinatário remove a validação atual. Salve o endereço e marque como validado somente após confirmar seu roteamento no OTRS.",
            )
        elif values["send_method"] == "api_rest" and values["enabled"] and not (values["api_key"] or (current and current.secret_ciphertext)):
            form.add_error("api_key", "Cadastre a API Key antes de habilitar a integração.")
        else:
            integration = current or ExternalAPIIntegration(provider=ExternalAPIIntegration.Provider.BREVO, name="Brevo")
            previous_enabled, previous_config = integration.enabled, integration.config or {}
            previous_allow_real_sends = integration.allow_real_sends
            try:
                if values["api_key"]:
                    integration.secret_ciphertext = get_secret_protector().protect(values["api_key"])
                if values["smtp_key"]:
                    integration.smtp_secret_ciphertext = get_secret_protector().protect(values["smtp_key"])
                integration.config = {
                    "send_method": values["send_method"],
                    "smtp_login": values["smtp_login"],
                    "smtp_host": "smtp-relay.brevo.com", "smtp_port": 587, "smtp_starttls": True,
                    "quality_recipient_validated": values["quality_recipient_validated"],
                    "sender_name": values["sender_name"], "sender_email": values["sender_email"],
                    "quality_recipient": values["quality_recipient"], "reply_to": values["reply_to"],
                    "routing_test_recipient": values["routing_test_recipient"],
                    "routing_operational_test_approved": values["routing_operational_test_approved"],
                    "quality_cc": values["quality_cc"],
                }
                integration.enabled = values["enabled"]
                integration.allow_real_sends = values["allow_real_sends"]
                integration.updated_by = request.user
                integration.save()
            except Exception:
                form.add_error(None, "Não foi possível proteger e salvar a configuração Brevo neste processo.")
            else:
                if values["api_key"]:
                    AuditService.record(request=request, category="INTEGRATION", action="EXTERNAL_API_CREDENTIAL_SAVED",
                        module="APIs Externas", description="Credencial da Brevo cadastrada ou substituída", obj=integration,
                        metadata={"provider": "brevo", "credential_configured": True})
                if values["smtp_key"]:
                    AuditService.record(request=request, category="INTEGRATION", action="BREVO_SMTP_CREDENTIAL_SAVED",
                        module="APIs Externas", description="Credencial SMTP da Brevo cadastrada ou substituída", obj=integration,
                        metadata={"provider": "brevo", "credential_configured": True, "method": "smtp"})
                if previous_config != integration.config:
                    AuditService.record(request=request, category="INTEGRATION", action="EXTERNAL_API_CONFIG_UPDATED",
                        module="APIs Externas", description="Configuração da Brevo atualizada", obj=integration,
                        metadata={"provider": "brevo", "fields": sorted(integration.config.keys())})
                if previous_enabled != integration.enabled:
                    AuditService.record(request=request, category="INTEGRATION", action="EXTERNAL_API_TOGGLED",
                        module="APIs Externas", description="Estado da integração Brevo alterado", obj=integration,
                        metadata={"provider": "brevo", "enabled": integration.enabled})
                if previous_allow_real_sends != integration.allow_real_sends:
                    AuditService.record(request=request, category="INTEGRATION", action="BREVO_REAL_SEND_AUTHORIZATION_CHANGED",
                        module="APIs Externas", description="Autorização de envio real da Brevo alterada", obj=integration,
                        metadata={"provider": "brevo", "allow_real_sends": integration.allow_real_sends})
                messages.success(request, "Configuração Brevo salva. A autorização de envio real foi atualizada.")
                return redirect("configuracoes:integracoes:brevo")
    return render(request, "integracoes/brevo.html", _external_api_page_context(request, ExternalAPIIntegration.Provider.BREVO, form))


@login_required
@require_POST
def brevo_test(request):
    _require_permission(request, "view_integrations")
    _require_permission(request, "run_integrations")
    integration = ExternalAPIIntegration.objects.filter(provider=ExternalAPIIntegration.Provider.BREVO).first()
    if not integration or not integration.secret_ciphertext:
        messages.warning(request, "Cadastre uma API Key antes de testar a conexão.")
        return redirect("configuracoes:integracoes:brevo")
    latency = None
    status_code = None
    error_code = ""
    try:
        api_key = decrypt_external_api_secret(integration)
    except Exception:
        message, success = "Não foi possível recuperar a credencial protegida por DPAPI neste processo.", False
    else:
        try:
            outcome = BrevoTransactionalClient(api_key).test_connection()
            message, latency, success = outcome.message, outcome.latency_ms, outcome.ok
            status_code, error_code = outcome.status_code, outcome.error_code
        except Exception:
            message, success = "Falha segura ao iniciar o teste de autenticação Brevo.", False
    integration.last_test_at = timezone.now()
    integration.last_latency_ms = latency
    integration.health = ExternalAPIIntegration.Health.AVAILABLE if success else ExternalAPIIntegration.Health.ERROR
    integration.last_error = "" if success else message
    if success:
        integration.last_success_at = integration.last_test_at
    integration.save(update_fields=("last_test_at", "last_latency_ms", "health", "last_error", "last_success_at", "updated_at"))
    check = ExternalAPIHealthCheck.objects.create(
        integration=integration,
        method="api_rest",
        status=ExternalAPIHealthCheck.Status.SUCCESS if success else ExternalAPIHealthCheck.Status.ERROR,
        message=message, latency_ms=latency, checked_by=request.user,
    )
    AuditService.record(request=request, category="INTEGRATION", action="EXTERNAL_API_TESTED",
        module="APIs Externas", description="Teste de autenticação da Brevo executado",
        obj=integration, result="SUCCESS" if success else "FAILURE",
        metadata={"provider": "brevo", "latency_ms": latency, "check_id": check.pk,
                  "http_status": status_code, "brevo_error_code": error_code or None})
    (messages.success if success else messages.error)(request, message)
    return redirect("configuracoes:integracoes:brevo")


@login_required
@require_POST
def brevo_smtp_test(request):
    _require_permission(request, "view_integrations")
    _require_permission(request, "run_integrations")
    integration = ExternalAPIIntegration.objects.filter(provider=ExternalAPIIntegration.Provider.BREVO).first()
    config = integration.config or {} if integration else {}
    if not integration or not integration.smtp_secret_ciphertext or not config.get("smtp_login"):
        messages.warning(request, "Cadastre login e chave SMTP antes de testar a conexão.")
        return redirect("configuracoes:integracoes:brevo")
    started = timezone.now()
    try:
        password = decrypt_external_api_smtp_secret(integration)
        if not password:
            raise ValueError("empty")
        outcome = test_brevo_smtp_auth(config["smtp_login"], password)
        message, latency, success, status_code = outcome.message, outcome.latency_ms, outcome.ok, outcome.error_code
    except Exception:
        message, latency, success, status_code = "Não foi possível recuperar a credencial SMTP protegida por DPAPI.", None, False, None
    integration.last_test_at = started
    integration.last_latency_ms = latency
    integration.health = ExternalAPIIntegration.Health.AVAILABLE if success else ExternalAPIIntegration.Health.ERROR
    integration.last_error = "" if success else message
    if success:
        integration.last_success_at = started
    integration.save(update_fields=("last_test_at", "last_latency_ms", "health", "last_error", "last_success_at", "updated_at"))
    check = ExternalAPIHealthCheck.objects.create(
        integration=integration, status=ExternalAPIHealthCheck.Status.SUCCESS if success else ExternalAPIHealthCheck.Status.ERROR,
        method="smtp",
        message=message, latency_ms=latency, checked_by=request.user,
    )
    AuditService.record(request=request, category="INTEGRATION", action="BREVO_SMTP_AUTH_TESTED",
        module="APIs Externas", description="Teste de autenticação SMTP Brevo executado",
        obj=integration, result="SUCCESS" if success else "FAILURE",
        metadata={"provider": "brevo", "method": "smtp", "latency_ms": latency,
                  "check_id": check.pk, "smtp_status_code": status_code})
    (messages.success if success else messages.error)(request, message)
    return redirect("configuracoes:integracoes:brevo")


@login_required
@require_POST
def brevo_routing_test(request):
    """Send one confirmed routing message to the configured test mailbox only."""
    _require_permission(request, "manage_integrations")
    integration = ExternalAPIIntegration.objects.filter(provider=ExternalAPIIntegration.Provider.BREVO).first()
    config = integration.config or {} if integration else {}
    test_id = str(request.POST.get("routing_test_id", "")).strip().upper()
    if not re.fullmatch(r"ARION-ROUTE-[0-9A-F]{32}", test_id):
        messages.error(request, "Identificador de homologa\u00e7\u00e3o inv\u00e1lido. Recarregue a p\u00e1gina antes de tentar novamente.")
        return redirect("configuracoes:integracoes:brevo")
    if ExternalAPIHealthCheck.objects.filter(method="smtp", message__contains=test_id).exists():
        messages.warning(request, "Este teste de roteamento j\u00e1 foi processado; nenhum novo e-mail foi enviado.")
        return redirect("configuracoes:integracoes:brevo")
    if request.POST.get("confirm_routing_test") != "yes":
        messages.error(request, "Confirme explicitamente o envio da mensagem de HOMOLOGA\u00c7\u00c3O.")
        return redirect("configuracoes:integracoes:brevo")
    recipient = str(config.get("routing_test_recipient") or "").strip()
    is_operational_target = bool(recipient and recipient.casefold() == str(config.get("quality_recipient") or "").strip().casefold())
    if is_operational_target and not config.get("routing_operational_test_approved"):
        messages.error(request, "Teste bloqueado: o destinatário operacional exige autorização administrativa prévia para homologação.")
        return redirect("configuracoes:integracoes:brevo")
    if (
        not integration or not integration.enabled or config.get("send_method") != "smtp"
        or not integration.smtp_secret_ciphertext or not config.get("smtp_login") or not recipient
    ):
        messages.error(request, "Teste bloqueado: habilite SMTP e configure login, credencial protegida e destinat\u00e1rio de homologa\u00e7\u00e3o.")
        return redirect("configuracoes:integracoes:brevo")

    from django.core.validators import validate_email
    from .clients.brevo_smtp import send_brevo_smtp

    try:
        validate_email(recipient)
        password = decrypt_external_api_smtp_secret(integration)
        if not password:
            raise ValueError("empty")
    except Exception:
        messages.error(request, "Teste bloqueado: credencial SMTP ou destinat\u00e1rio de homologa\u00e7\u00e3o indispon\u00edvel.")
        return redirect("configuracoes:integracoes:brevo")

    subject = f"[HOMOLOGA\u00c7\u00c3O ARION OPS] Teste de roteamento {test_id}"
    text_content = (
        "HOMOLOGA\u00c7\u00c3O DE ROTEAMENTO ARION OPS\n"
        "Mensagem controlada para validar recebimento e encaminhamento.\n"
        "Este teste n\u00e3o representa uma TQ e n\u00e3o cont\u00e9m QID de TQ real.\n"
        f"Identificador exclusivo do teste: {test_id}\n"
        "O aceite SMTP n\u00e3o confirma entrega nem cria\u00e7\u00e3o de ticket OTRS."
    )
    html_content = (
        "<!doctype html><html lang=\"pt-BR\"><meta charset=\"utf-8\"><body>"
        "<h1>HOMOLOGA\u00c7\u00c3O DE ROTEAMENTO ARION OPS</h1>"
        "<p>Mensagem controlada para validar recebimento e encaminhamento.</p>"
        "<p>Este teste n\u00e3o representa uma TQ e n\u00e3o cont\u00e9m QID de TQ real.</p>"
        f"<p>Identificador exclusivo do teste: <strong>{test_id}</strong></p>"
        "<p>O aceite SMTP n\u00e3o confirma entrega nem cria\u00e7\u00e3o de ticket OTRS.</p>"
        "</body></html>"
    )
    started = time.perf_counter()
    try:
        smtp_config = {**config, "quality_recipient": recipient, "quality_cc": "", "reply_to": ""}
        outcome = send_brevo_smtp(
            username=str(config["smtp_login"]).strip(), password=password, config=smtp_config,
            subject=subject, text_content=text_content, html_content=html_content,
        )
    except Exception:
        outcome = None
    latency = getattr(outcome, "latency_ms", None)
    if latency is None:
        latency = max(0, int((time.perf_counter() - started) * 1000))
    accepted = bool(outcome and outcome.ok)
    result_text = "aceita pelo relay SMTP; confirme recebimento e fila manualmente" if accepted else "falha sanitizada no envio SMTP"
    check = ExternalAPIHealthCheck.objects.create(
        integration=integration, method="smtp",
        status=ExternalAPIHealthCheck.Status.SUCCESS if accepted else ExternalAPIHealthCheck.Status.ERROR,
        message=f"HOMOLOGA\u00c7\u00c3O {test_id}: {result_text}"[:240],
        latency_ms=latency, checked_by=request.user,
    )
    AuditService.record(
        request=request, category="INTEGRATION", action="BREVO_ROUTING_TEST_SENT",
        module="APIs Externas", description="Teste controlado de roteamento Brevo processado",
        obj=integration, result="SUCCESS" if accepted else "FAILURE",
        metadata={"provider": "brevo", "method": "smtp", "test_id": test_id,
                  "check_id": check.pk, "relay_accepted": accepted, "latency_ms": latency},
    )
    if accepted:
        messages.success(request, f"O relay SMTP aceitou o e-mail de HOMOLOGA\u00c7\u00c3O {test_id}. Confirme o recebimento e a fila no OTRS; a valida\u00e7\u00e3o n\u00e3o foi alterada.")
    else:
        messages.error(request, f"O teste de HOMOLOGA\u00c7\u00c3O {test_id} n\u00e3o foi aceito. Consulte o hist\u00f3rico sanitizado.")
    return redirect("configuracoes:integracoes:brevo")


@login_required
@require_POST
def brevo_revoke(request):
    _require_permission(request, "view_integrations")
    _require_permission(request, "manage_integrations")
    integration = get_object_or_404(ExternalAPIIntegration, provider=ExternalAPIIntegration.Provider.BREVO)
    integration.secret_ciphertext = ""
    integration.smtp_secret_ciphertext = ""
    integration.enabled = False
    integration.allow_real_sends = False
    integration.save(update_fields=("secret_ciphertext", "smtp_secret_ciphertext", "enabled", "allow_real_sends", "updated_at"))
    AuditService.record(request=request, category="INTEGRATION", action="EXTERNAL_API_CREDENTIAL_REVOKED",
        module="APIs Externas", description="Credencial da Brevo revogada", obj=integration,
        metadata={"provider": "brevo", "credential_configured": False})
    messages.success(request, "Credencial Brevo revogada.")
    return redirect("configuracoes:integracoes:brevo")


@login_required
@require_GET
def mapbox_detail(request):
    _require_permission(request, "view_integrations")
    return render(request, "integracoes/mapbox.html", _external_api_page_context(request, ExternalAPIIntegration.Provider.MAPBOX))


@login_required
@require_POST
def mapbox_save(request):
    _require_permission(request, "view_integrations")
    _require_permission(request, "manage_integrations")
    current = ExternalAPIIntegration.objects.filter(provider=ExternalAPIIntegration.Provider.MAPBOX).first()
    form = MapboxConfigurationForm(request.POST)
    if form.is_valid():
        values = form.cleaned_data
        old_config = (current.config or {}) if current else {}
        public_token = values["public_token"] or old_config.get("public_token", "")
        runtime = get_mapbox_runtime_state()
        if current is None and not values["public_token"] and values["enabled"] and runtime["source"] == "environment":
            messages.info(request, "A configuração legada permanece ativa. Informe o token público para migrá-lo para o banco.")
            return redirect("configuracoes:integracoes:mapbox")
        if values["enabled"] and not is_public_mapbox_token(public_token):
            form.add_error("public_token", "Cadastre um token público pk. válido antes de habilitar o Mapbox.")
        else:
            integration = current or ExternalAPIIntegration(provider=ExternalAPIIntegration.Provider.MAPBOX, name="Mapbox")
            previous_enabled = integration.enabled
            integration.config = {"public_token": public_token} if public_token else {}
            integration.enabled = values["enabled"]
            integration.updated_by = request.user
            integration.save()
            if values["public_token"]:
                AuditService.record(request=request, category="INTEGRATION", action="EXTERNAL_API_PUBLIC_TOKEN_SAVED",
                    module="APIs Externas", description="Token público Mapbox cadastrado ou substituído", obj=integration,
                    metadata={"provider": "mapbox", "token_type": "public"})
            if previous_enabled != integration.enabled:
                AuditService.record(request=request, category="INTEGRATION", action="EXTERNAL_API_TOGGLED",
                    module="APIs Externas", description="Estado da integração Mapbox alterado", obj=integration,
                    metadata={"provider": "mapbox", "enabled": integration.enabled})
            messages.success(request, "Configuração Mapbox salva para uso em runtime.")
            return redirect("configuracoes:integracoes:mapbox")
    return render(request, "integracoes/mapbox.html", _external_api_page_context(request, ExternalAPIIntegration.Provider.MAPBOX, form))


@login_required
@require_POST
def mapbox_test(request):
    _require_permission(request, "view_integrations")
    _require_permission(request, "run_integrations")
    integration = ExternalAPIIntegration.objects.filter(provider=ExternalAPIIntegration.Provider.MAPBOX).first()
    token = (integration.config or {}).get("public_token", "") if integration else ""
    success = bool(integration and is_public_mapbox_token(token))
    message = "Token público válido." if success else "Token público ausente ou inválido."
    if integration:
        integration.last_test_at = timezone.now()
        integration.last_latency_ms = None
        integration.health = ExternalAPIIntegration.Health.AVAILABLE if success else ExternalAPIIntegration.Health.ERROR
        integration.last_error = "" if success else message
        if success:
            integration.last_success_at = integration.last_test_at
        integration.save(update_fields=("last_test_at", "last_latency_ms", "health", "last_error", "last_success_at", "updated_at"))
        ExternalAPIHealthCheck.objects.create(
            integration=integration,
            method="mapbox",
            status=ExternalAPIHealthCheck.Status.SUCCESS if success else ExternalAPIHealthCheck.Status.ERROR,
            message=message, checked_by=request.user,
        )
        AuditService.record(request=request, category="INTEGRATION", action="EXTERNAL_API_TESTED",
            module="APIs Externas", description="Validação local do token Mapbox executada",
            obj=integration, result="SUCCESS" if success else "FAILURE", metadata={"provider": "mapbox"})
    (messages.success if success else messages.error)(request, message)
    return redirect("configuracoes:integracoes:mapbox")


@login_required
@require_POST
def mapbox_revoke(request):
    _require_permission(request, "view_integrations")
    _require_permission(request, "manage_integrations")
    integration = get_object_or_404(ExternalAPIIntegration, provider=ExternalAPIIntegration.Provider.MAPBOX)
    integration.config = {}
    integration.enabled = False
    integration.save(update_fields=("config", "enabled", "updated_at"))
    AuditService.record(request=request, category="INTEGRATION", action="EXTERNAL_API_PUBLIC_TOKEN_REVOKED",
        module="APIs Externas", description="Token público Mapbox revogado", obj=integration,
        metadata={"provider": "mapbox", "token_type": "public"})
    messages.success(request, "Token Mapbox removido. A configuração de ambiente volta a ser usada somente se o registro da API for removido.")
    return redirect("configuracoes:integracoes:mapbox")
