from django.core.management.base import BaseCommand

from aplicativos.integracoes.services.operational_identity import OperationalIdentityService


class Command(BaseCommand):
    help = "Reconcilia a identidade operacional de links CMDB com evidências CMDB, OTRS e histórico local."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Persiste o resultado idempotente no mirror e na camada operacional.")
        parser.add_argument("--all", action="store_true", help="Inclui links ausentes da origem no mirror.")

    def handle(self, *args, **options):
        if not options["apply"]:
            self.stdout.write("Modo de simulação (prévia): use --apply para persistir a classificação.")
        summary = OperationalIdentityService.reconcile_all(
            apply=options["apply"],
            include_absent=options["all"],
        )
        self.stdout.write(self.style.SUCCESS(f"Identidade operacional CMDB/OTRS: {summary}"))
