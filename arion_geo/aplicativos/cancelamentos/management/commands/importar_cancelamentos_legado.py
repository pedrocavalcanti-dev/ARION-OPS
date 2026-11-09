import json
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from aplicativos.cancelamentos.services.legacy_dump import DumpError
from aplicativos.cancelamentos.services.legacy_import import (
    apply_plan, authorize_import, build_plan, destination_status, load_source, private_report_path, source_summary, write_private_report,
)


class Command(BaseCommand):
    help = "Preview, dry-run e importação controlada ArionBroker exclusivamente em DEV (MySQL 8 ou SQLite autorizado). Nunca executa SQL do dump."

    def add_arguments(self, parser):
        parser.add_argument("--source-dir", help="Ou configure ARION_CANCELAMENTOS_MIGRATION_DIR.")
        parser.add_argument("--dump", default="cancelamentos-dados-20261109-133631.sql")
        parser.add_argument("--source-timezone", required=True, help="Fuso dos datetime do legado, explicitamente verificado (ex.: UTC).")
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--preview", action="store_true")
        mode.add_argument("--dry-run", action="store_true")
        mode.add_argument("--apply", action="store_true")
        parser.add_argument("--allow-sqlite-dev", action="store_true", help="Autoriza expressamente importação no banco SQLite do ambiente DEV.")
        parser.add_argument("--actor-id", type=int, help="Usuário ARION autorizado, apenas ator da importação; não vira responsável.")
        parser.add_argument("--report", required=True, help="Relatório privado JSON fora de static/media/Git.")
        parser.add_argument("--approved-report", help="JSON do dry-run tecnicamente aprovado para este destino/snapshot.")
        parser.add_argument("--backup-file", help="Backup DEV (MySQL dump ou SQLite) com restauração verificada.")
        parser.add_argument("--backup-sha256")
        parser.add_argument("--restore-verified", action="store_true")

    def handle(self, *args, **options):
        if settings.ARION_PRODUCTION or getattr(settings, "ARION_ENV", "") != "development":
            raise CommandError("Importação histórica bloqueada em produção.")
        allow_sqlite_dev = options.get("allow_sqlite_dev", False)
        try:
            private_report_path(options["report"])
            source = load_source(options["source_dir"], options["dump"], options["source_timezone"])
            if options["preview"]:
                report = {**source_summary(source), "mode": "preview", "ready_for_apply": False}
            else:
                status = destination_status(allow_sqlite_dev=allow_sqlite_dev)
                if options["apply"] and status["issues"]:
                    if "destination_requires_mysql8_dev" in status["issues"]:
                        raise CommandError("Apply bloqueado: configure/valide o MySQL 8 DEV ou passe --allow-sqlite-dev em desenvolvimento.")
                    raise CommandError(f"Apply bloqueado por pendências de destino: {', '.join(status['issues'])}.")
                actor = get_user_model().objects.filter(pk=options["actor_id"]).first() if options["actor_id"] else None
                authorize_import(actor)
                if options["dry_run"]:
                    report = {**build_plan(source, allow_sqlite_dev=allow_sqlite_dev), "mode": "dry-run"}
                else:
                    if not all(options[key] for key in ("approved_report", "backup_file", "backup_sha256")):
                        raise CommandError("Apply exige --approved-report, --backup-file e --backup-sha256.")
                    approved = json.loads(Path(options["approved_report"]).read_text(encoding="utf-8"))
                    report = {**apply_plan(source, actor, approved, options["backup_file"],
                                          options["backup_sha256"], options["restore_verified"],
                                          allow_sqlite_dev=allow_sqlite_dev), "mode": "apply"}
            report["generated_at"] = timezone.now().isoformat()
            write_private_report(options["report"], report)
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
            raise CommandError(f"Importação interrompida ({type(exc).__name__}); registros de lotes completos permanecem rastreáveis. Nenhuma credencial será exibida.") from None
