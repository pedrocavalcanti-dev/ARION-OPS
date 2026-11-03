from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from aplicativos.integracoes.models import Integration
from aplicativos.integracoes.services.errors import IntegrationError
from aplicativos.integracoes.services.otrs_study import OTRSStudyService
from aplicativos.integracoes.views import _cmdb_client, _otrs_client


class Command(BaseCommand):
    help = "Executa estudo estrutural e operacional somente leitura do OTRS."

    def add_arguments(self, parser):
        parser.add_argument("--queue", default="NOC - ATACADO", help="Nome exato da fila a estudar.")

    def handle(self, *args, **options):
        try:
            integration = Integration.objects.get(key=Integration.Key.OTRS)
            client = _otrs_client(integration)
        except Integration.DoesNotExist as exc:
            raise CommandError("OTRS não possui configuração existente na Central de Integrações.") from exc
        except IntegrationError as exc:
            raise CommandError(f"Não foi possível preparar o cliente OTRS ({type(exc).__name__}). {exc.diagnostic_summary()}") from exc
        except Exception as exc:
            raise CommandError(f"Não foi possível preparar o cliente OTRS ({type(exc).__name__}).") from None

        if not client.database:
            try:
                databases = client.list_databases()
            except IntegrationError as exc:
                raise CommandError(f"Não foi possível descobrir o database OTRS ({type(exc).__name__}). {exc.diagnostic_summary()}") from exc
            system_databases = {"information_schema", "mysql", "performance_schema", "sys"}
            application_databases = [name for name in databases if name.casefold() not in system_databases]
            if len(application_databases) != 1:
                raise CommandError("Database OTRS não selecionado; a descoberta não encontrou um único schema de aplicação.")
            # Use the uniquely discoverable application schema for this invocation only.
            client.database = application_databases[0]

        cmdb_client = None
        try:
            cmdb_integration = Integration.objects.get(key=Integration.Key.CMDB)
            cmdb_client = _cmdb_client(cmdb_integration)
        except (Integration.DoesNotExist, IntegrationError):
            pass
        except Exception:
            # CMDB cross-check is optional; do not block the OTRS-only study.
            pass

        study = OTRSStudyService(client, cmdb_client=cmdb_client, progress=self.stdout.write)
        try:
            with client.read_only_session():
                result = study.run(options["queue"])
        except IntegrationError as exc:
            raise CommandError(f"Estudo OTRS interrompido ({type(exc).__name__}); nenhuma escrita foi solicitada. {exc.diagnostic_summary()}") from exc
        except Exception as exc:
            # Provider exceptions may contain connection details. Never surface their text.
            raise CommandError(f"Estudo OTRS interrompido ({type(exc).__name__}); nenhuma escrita foi solicitada.") from None

        protected_values = [
            getattr(client, "password", ""), getattr(client, "username", ""),
            integration.secret_ciphertext, getattr(cmdb_client, "password", "") if cmdb_client else "",
        ]
        for value in protected_values:
            if value and (value in result["report"]):
                raise CommandError("O relatório contém credencial configurada; gravação cancelada.")

        output_dir = Path("docs") / "estudos"
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "OTRS-ESTUDO-REAL.md"
        report_path.write_text(result["report"], encoding="utf-8")
        structure_path = output_dir / "OTRS-ESTRUTURA.md"
        structure_path.write_text(self._render_structure(result), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(
            f"Estudo concluído: tabelas/views={len(result['tables'])}; "
            f"fila={result['queue'].get('id') or 'não confirmada'}; "
            f"tickets atuais={result['ticket'].get('count', 0)}; "
            f"associações={result['relationship'].get('association_count', 0)}."
        ))
        self.stdout.write(f"Relatório: {report_path.as_posix()}")
        self.stdout.write(f"Estrutura: {structure_path.as_posix()}")

    @staticmethod
    def _render_structure(result):
        lines = [
            "# Estrutura OTRS relevante para o estudo NOC - ATACADO", "",
            "Inventário obtido de `information_schema`; estimativas de linhas são metadata. Sem dump de conteúdo.", "",
            f"Banco: `{result['database'].get('database_name') or 'não informado'}`", "",
            "| Tabela/View | Tipo | Estimativa de linhas | Campos candidatos | Relevância inferida |",
            "|---|---|---:|---|---|",
        ]
        columns = {}
        for row in result.get("candidate_columns", []):
            columns.setdefault(row["table"], []).append(f"`{row['column']}` ({row['type']})")
        for table in result["candidate_tables"]:
            name = table["name"]
            role = ", ".join(table["matched_terms"])
            fields = ", ".join(columns.get(name, [])) or "ver relatório principal"
            lines.append(f"| `{name}` | {table['type']} | {table.get('estimated_rows') if table.get('estimated_rows') is not None else '—'} | {fields} | {role} |")
        ticket = result["ticket"]
        if ticket.get("table"):
            lines.extend(("", f"## Índices de `{ticket['table']}`", "", "| Índice | Coluna | Único |", "|---|---|---|") )
            for index in ticket.get("indexes", []):
                lines.append(f"| `{index['name']}` | `{index['column']}` | {'sim' if index['unique'] else 'não'} |")
        rel = result["relationship"]
        if rel.get("table"):
            lines.extend(("", f"## Índices de `{rel['table']}`", "", "| Índice | Coluna | Único |", "|---|---|---|") )
            for index in rel.get("indexes", []):
                lines.append(f"| `{index['name']}` | `{index['column']}` | {'sim' if index['unique'] else 'não'} |")
        lines.extend(("", "## Chaves estrangeiras declaradas", "", "| Tabela | Coluna | Referência |", "|---|---|---|"))
        for table_name, foreign_keys in ((ticket.get("table"), ticket.get("foreign_keys", [])), (rel.get("table"), rel.get("foreign_keys", []))):
            for key in foreign_keys:
                lines.append(f"| `{table_name}` | `{key['column']}` | `{key['referenced_table']}.{key['referenced_column']}` |")
        if not ticket.get("foreign_keys") and not rel.get("foreign_keys"):
            lines.append("| — | — | Nenhuma FK visível nas tabelas principais |")
        lines.append("")
        return "\n".join(lines)
