import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError

from ...services.legacy_reconciliation import reconcile_workbook


class Command(BaseCommand):
    help = "Reconcilia relatório posterior em processos históricos existentes no DEV; preview por padrão."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True)
        parser.add_argument("--actor", required=True, type=int)
        parser.add_argument("--report", required=True, help="JSON privado em var/.")
        parser.add_argument("--principal-values", help="JSON privado de campos principais com evidência revisada.")
        parser.add_argument("--report-timezone", default="America/Sao_Paulo")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--approved-report", help="Preview previamente revisado, necessário no Apply.")

    def handle(self, *args, **options):
        actor = get_user_model().objects.filter(pk=options["actor"], is_active=True).first()
        target = Path(options["report"]).resolve()
        if not actor or not target.is_relative_to((Path(settings.BASE_DIR).parent / "var").resolve()) or target.suffix != ".json":
            raise CommandError("Informe ator ativo e relatório JSON privado dentro de var/.")
        try:
            approved_path = options.get("approved_report")
            approved = json.loads(Path(approved_path).read_text(encoding="utf-8")) if approved_path else None
            if options["apply"] and (not approved or target == Path(approved_path).resolve()):
                raise CommandError("Apply exige preview aprovado e relatório de saída em outro arquivo.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8"):
                pass
            report = reconcile_workbook(options["file"], actor, apply=options["apply"], approved=approved,
                principal_path=options.get("principal_values"), report_timezone=options["report_timezone"])
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except (ValidationError, PermissionDenied) as exc:
            raise CommandError(" ".join(exc.messages) if isinstance(exc, ValidationError) else "Ator sem permissão ou escopo global.") from None
        except (OSError, ValueError):
            raise CommandError("Não foi possível ler entrada/preview ou gravar relatório privado.") from None
        self.stdout.write(json.dumps({k: v for k, v in report.items() if k not in {"plan", "issues"}}, ensure_ascii=False))
        if report["issues"]:
            self.stdout.write(self.style.WARNING(f"{len(report['issues'])} conflitos; Apply bloqueado. Consulte o relatório privado."))
