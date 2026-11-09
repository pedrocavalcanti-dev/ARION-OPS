from django.core.management.base import BaseCommand, CommandError

from aplicativos.cancelamentos.services.otrs_intake import project_intakes


class Command(BaseCommand):
    help = "Reconcilia a Entrada OTRS exclusivamente a partir do espelho local, sem criar pedidos."

    def handle(self, *args, **options):
        try:
            result = project_intakes(force=True)
        except Exception as exc:
            raise CommandError(f"Projeção não concluída ({type(exc).__name__}); consulte Integrações.") from None
        self.stdout.write(str(result))
