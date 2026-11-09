import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.management.base import BaseCommand, CommandError

from ...services.legacy_people import import_people


class Command(BaseCommand):
    help = "Preview/importação DEV de nomes históricos, sem criar contas e sem reimportar cancelamentos."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True)
        parser.add_argument("--actor", type=int, required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        actor = get_user_model().objects.filter(pk=options["actor"], is_active=True).first()
        if actor is None:
            raise CommandError("Informe um ator ativo autorizado.")
        try:
            result = import_people(options["file"], actor, apply=options["apply"])
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from None
        except PermissionDenied:
            raise CommandError("Ator sem permission de importação e escopo global.") from None
        except (OSError, UnicodeError):
            raise CommandError("Não foi possível ler o arquivo privado de pessoas.") from None
        self.stdout.write(json.dumps({"apply": options["apply"], **result}))
