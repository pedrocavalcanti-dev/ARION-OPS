import json
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from aplicativos.cancelamentos.services.legacy_dump import DumpError
from aplicativos.cancelamentos.services.legacy_import import (
    apply_plan, authorize_import, build_plan, destination_status, load_source, private_report_path, source_summary, write_private_report,
)
from aplicativos.cancelamentos.services.legacy_import_policy import (
    PRODUCTION_CONFIRMATION, production_environment, production_source, external_private_file,
)


class Command(BaseCommand):
    help = "Importação ArionBroker: DEV preservado e modo produtivo explícito restrito ao MySQL arion_geo. Nunca executa SQL do dump."

    def add_arguments(self, parser):
        parser.add_argument("--source-dir", help="Ou configure ARION_CANCELAMENTOS_MIGRATION_DIR.")
        parser.add_argument("--dump", default="cancelamentos-dados-20261109-133631.sql")
        parser.add_argument("--source-timezone", required=True, help="Fuso dos datetime do legado, explicitamente verificado (ex.: UTC).")
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--preview", action="store_true")
        mode.add_argument("--dry-run", action="store_true")
        mode.add_argument("--apply", action="store_true")
        mode.add_argument("--validate", action="store_true", help="Reconcilia e exige todos os processos existentes, sem gravação de domínio.")
        parser.add_argument("--production-import", action="store_true", help="Autoriza exclusivamente o modo ARION_ENV=production/MySQL arion_geo.")
        parser.add_argument("--snapshot-sha256", help="SHA256 previamente conferido do dump; obrigatório no modo produtivo.")
        parser.add_argument("--confirm-production-apply", choices=[PRODUCTION_CONFIRMATION], help="Confirmação literal específica do Apply produtivo.")
        parser.add_argument("--restore-evidence", help="JSON privado da restauração testada, obrigatório no Apply produtivo.")
        parser.add_argument("--allow-sqlite-dev", action="store_true", help="Autoriza expressamente importação no banco SQLite do ambiente DEV.")
        parser.add_argument("--actor-id", type=int, help="Usuário ARION autorizado, apenas ator da importação; não vira responsável.")
        parser.add_argument("--report", required=True, help="Relatório privado JSON fora de static/media/Git.")
        parser.add_argument("--approved-report", help="JSON do dry-run tecnicamente aprovado para este destino/snapshot.")
        parser.add_argument("--backup-file", help="Backup do destino confirmado, com restauração verificada.")
        parser.add_argument("--backup-sha256")
        parser.add_argument("--restore-verified", action="store_true")

    def handle(self, *args, **options):
        allow_sqlite_dev = options.get("allow_sqlite_dev", False)
        production = options["production_import"]
        plan_options = {"allow_sqlite_dev": allow_sqlite_dev, "production": production,
                        "snapshot_checksum": options["snapshot_sha256"]}
        try:
            production_environment(production, allow_sqlite_dev)
            private_report_path(options["report"])
            source = load_source(options["source_dir"], options["dump"], options["source_timezone"])
            actor = None
            if production:
                external_private_file(options["report"], must_exist=False)
                production_source(source, options["snapshot_sha256"])
                status = destination_status(allow_sqlite_dev=allow_sqlite_dev, production=True)
                if status["issues"]:
                    raise CommandError("Destino produtivo não está apto: " + ", ".join(status["issues"]))
                actor = get_user_model().objects.filter(pk=options["actor_id"]).first() if options["actor_id"] else None
                authorize_import(actor)
                for name in ("approved_report", "backup_file", "restore_evidence"):
                    if options.get(name):
                        external_private_file(options[name])
            if options["preview"]:
                report = {**source_summary(source), "mode": "preview", "ready_for_apply": False}
                if production:
                    report.update(production_import=True, destination=status,
                                  source_identity=production_source(source, options["snapshot_sha256"]))
            else:
                status = destination_status(allow_sqlite_dev=allow_sqlite_dev, production=production)
                if options["apply"] and status["issues"]:
                    if "destination_requires_mysql8_dev" in status["issues"]:
                        raise CommandError("Apply bloqueado: configure/valide o MySQL 8 DEV ou passe --allow-sqlite-dev em desenvolvimento.")
                    raise CommandError(f"Apply bloqueado por pendências de destino: {', '.join(status['issues'])}.")
                actor = get_user_model().objects.filter(pk=options["actor_id"]).first() if options["actor_id"] else None
                authorize_import(actor)
                if options["dry_run"] or options["validate"]:
                    report = {**build_plan(source, **plan_options), "mode": "validate" if options["validate"] else "dry-run"}
                    if options["validate"]:
                        report["validated"] = bool(report["ready_for_apply"] and report["reconciliation"].get("existing", 0) == report["counts"]["cancelamento"])
                else:
                    if not all(options[key] for key in ("approved_report", "backup_file", "backup_sha256")):
                        raise CommandError("Apply exige --approved-report, --backup-file e --backup-sha256.")
                    approved = json.loads(Path(options["approved_report"]).read_text(encoding="utf-8"))
                    report = {**apply_plan(source, actor, approved, options["backup_file"],
                                          options["backup_sha256"], options["restore_verified"],
                                          **plan_options,
                                          confirm_production_apply=options["confirm_production_apply"] == PRODUCTION_CONFIRMATION,
                                          restore_evidence=options["restore_evidence"]), "mode": "apply"}
            report["generated_at"] = timezone.now().isoformat()
            write_private_report(options["report"], report)
            if options["validate"] and not report["validated"]:
                raise CommandError("Reconciliação incompleta; consulte o relatório privado de validação.")
            if production and (report.get("errors") or report.get("blocking_conflicts")):
                raise CommandError("Conflitos bloqueantes identificados; relatório privado gravado, Apply não autorizado.")
            self.stdout.write(json.dumps({key: report[key] for key in ("mode", "total", "counts", "ready_for_apply")}, ensure_ascii=False))
            if "reconciliation" in report:
                self.stdout.write(json.dumps({"reconciliation": report["reconciliation"], "destination_issues": report["destination"]["issues"]}, ensure_ascii=False))
        except CommandError:
            raise
        except (DumpError, ValidationError, PermissionDenied) as exc:
            messages = exc.messages if isinstance(exc, ValidationError) else [str(exc)]
            raise CommandError(" ".join(messages)) from None
        except (OSError, UnicodeError, ValueError, ZoneInfoNotFoundError):
            raise CommandError("Arquivo, encoding, JSON ou fuso inválido. Verifique os caminhos privados e o contrato de exportação.") from None
        except Exception as exc:
            recovery = "transação produtiva revertida" if production else "lotes completos permanecem rastreáveis"
            raise CommandError(f"Importação interrompida ({type(exc).__name__}); {recovery}. Nenhuma credencial será exibida.") from None
