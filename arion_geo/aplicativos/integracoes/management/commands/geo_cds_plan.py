"""Prepare an explicit bounded CDS acquisition, never invoked by HTTP GET."""
import json
from pathlib import Path
from django.core.management import BaseCommand, CommandError
from aplicativos.integracoes.services.weather_cds import acquisition_plan, download_plan


class Command(BaseCommand):
    help="Prepara manifesto CDS ERA5; --download exige cdsapi, credencial local e aceite do dataset. Não importa cache automaticamente."

    def add_arguments(self,parser):
        parser.add_argument("--start",required=True)
        parser.add_argument("--end",required=True)
        parser.add_argument("--area",nargs=4,type=float,required=True,metavar=("N","W","S","E"))
        parser.add_argument("--output",required=True)
        parser.add_argument("--download",action="store_true")

    def handle(self,*args,**options):
        try:
            plan=acquisition_plan(options["start"],options["end"],options["area"])
            target=Path(options["output"])
            if target.exists(): raise ValueError("O manifesto de destino já existe; escolha outro caminho.")
            if options["download"]: plan=download_plan(plan,target.parent/(target.stem+"-data"))
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding="utf-8")
        except ValueError as error:
            raise CommandError(str(error)) from None
        except Exception:
            raise CommandError("Aquisição CDS indisponível; verifique credencial, aceite e dependências opcionais. Detalhes externos omitidos.") from None
        self.stdout.write("Manifesto CDS preparado; etapa de aquisição, sem alteração do cache operacional.")
