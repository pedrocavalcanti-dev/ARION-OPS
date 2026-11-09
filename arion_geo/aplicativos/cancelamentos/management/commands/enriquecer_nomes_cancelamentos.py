import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError

from ...services.legacy_workbook import import_workbook


class Command(BaseCommand):
    help = "Concilia nomes históricos do Excel original; preview por padrão, sem reimportar processos."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True)
        parser.add_argument("--actor", required=True, type=int)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--report", required=True, help="Arquivo JSON privado dentro de var/.")
        parser.add_argument("--report-timezone", default="America/Sao_Paulo")

    def handle(self, *args, **options):
        actor = get_user_model().objects.filter(pk=options["actor"], is_active=True).first()
        if actor is None:
            raise CommandError("Informe um ator ativo autorizado.")
        target = Path(options["report"]).resolve()
        private = Path(settings.BASE_DIR).parent / "var"
        if not target.is_relative_to(private.resolve()) or target.suffix.lower() != ".json":
            raise CommandError("O relatório deve ser um arquivo JSON privado dentro de var/.")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Check report storage before any enrichment writes.
            with target.open("a", encoding="utf-8"):
                pass
            result = import_workbook(options["file"], actor, apply=options["apply"], report_timezone=options["report_timezone"])
            target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from None
        except PermissionDenied:
            raise CommandError("Ator sem permissão de importação e escopo global.") from None
        except OSError:
            raise CommandError("Não foi possível acessar o arquivo ou o relatório privado.") from None
        self.stdout.write(json.dumps({key: value for key, value in result.items() if key != "issues"}, ensure_ascii=False))
