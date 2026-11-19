from django.core.management.base import BaseCommand

from aplicativos.integracoes.models import CMDBPlantMonthlySnapshot
from aplicativos.integracoes.services.cmdb_plant_snapshot import (
    capture_current_cmdb_plant_snapshot,
    capture_legacy_import_plant_snapshot,
)


class Command(BaseCommand):
    help = "Captura snapshot mensal da planta CMDB de forma idempotente (sem fabricar histórico)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--include-imports",
            action="store_true",
            help="Também registra o snapshot verificado da última importação CMDB (base_links.CMDBImport), quando houver.",
        )

    def handle(self, *args, **options):
        if options.get("include_imports"):
            imported = capture_legacy_import_plant_snapshot()
            if imported:
                self.stdout.write(self.style.SUCCESS(
                    f"Snapshot importado registrado: {imported.month:%Y-%m} "
                    f"(ativos={imported.active_links}, total={imported.total_links}, cobertura={imported.coverage_status})"
                ))
        current = capture_current_cmdb_plant_snapshot(
            source=CMDBPlantMonthlySnapshot.Source.CMDB_SYNC,
        )
        self.stdout.write(self.style.SUCCESS(
            f"Snapshot atual capturado: {current.month:%Y-%m} "
            f"(ativos={current.active_links}, total={current.total_links}, parcial={current.is_partial_month})"
        ))
