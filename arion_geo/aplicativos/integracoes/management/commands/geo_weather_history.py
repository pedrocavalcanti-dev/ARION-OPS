"""Local inventory/consolidation; explicit scoped enqueue, no HTTP in this command."""
import json
from django.core.management import BaseCommand,CommandError
from aplicativos.integracoes.services.weather_history import maintain_history
from aplicativos.integracoes.services.weather import month_bounds
from aplicativos.integracoes.services.geography import STATE_BY_UF


class Command(BaseCommand):
    help="Verifica e consolida histórico local. --enqueue exige --ufs e --period; preserva fila/checkpoints e limites."

    def add_arguments(self,parser):
        parser.add_argument("--period")
        parser.add_argument("--ufs",nargs="+",choices=sorted(STATE_BY_UF))
        parser.add_argument("--enqueue",action="store_true")
        parser.add_argument("--limit",type=int,default=500)

    def handle(self,*args,**options):
        if options["enqueue"] and not (options["ufs"] and options["period"]):
            raise CommandError("Enfileiramento exige período e UFs explícitos.")
        try:
            first=month_bounds(options["period"])[0] if options["period"] else None
            result=maintain_history(ufs=options["ufs"],period=first,enqueue=options["enqueue"],limit=options["limit"])
        except ValueError as error:
            raise CommandError(str(error)) from None
        self.stdout.write(json.dumps(result))
