from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.conf import settings
import json
from pathlib import Path

from aplicativos.integracoes.services.arion_database import activate_candidate, rollback_to_previous
from aplicativos.integracoes.services.arion_database_migration import (
    CandidateStageError, copy_local_data, dry_run_migration, log_candidate_stage_failure,
    prepare_candidate_schema, sqlite_source_database, validate_candidate_migration,
)


class Command(BaseCommand):
    help = "Prepara, migra, valida ou ativa com seguranca o banco candidato do Arion."

    def add_arguments(self, parser):
        parser.add_argument("--target", choices=("mysql",), required=True)
        phases = parser.add_mutually_exclusive_group(required=True)
        phases.add_argument("--dry-run", action="store_true")
        phases.add_argument("--prepare", action="store_true")
        phases.add_argument("--copy-local-data", action="store_true")
        phases.add_argument("--validate", action="store_true")
        phases.add_argument("--activate", action="store_true")
        phases.add_argument("--rollback", action="store_true")
        parser.add_argument("--source-sqlite", help="Arquivo SQLite imutavel usado como origem.")
        parser.add_argument("--full-snapshot", action="store_true", help="Todos os models/M:N para candidato vazio; sem sessões, leases ou secrets.")
        parser.add_argument("--report", help="JSON privado em var/, obrigatório no snapshot completo.")
        parser.add_argument("--approved-report", help="Preview aprovado para cópia completa.")

    def handle(self, *args, **options):
        try:
            if options["full_snapshot"] and not options["prepare"] and (not options.get("source_sqlite") or not any(options[k] for k in ("dry_run", "copy_local_data", "validate"))):
                raise CommandError("--full-snapshot exige --prepare ou --source-sqlite com --dry-run, --copy-local-data ou --validate.")
            if options.get("source_sqlite") and not any(options[key] for key in ("dry_run", "copy_local_data", "validate")):
                raise CommandError("--source-sqlite so pode ser usado com --dry-run, --copy-local-data ou --validate.")
            if options["rollback"]:
                rollback_to_previous()
                self.stdout.write(self.style.SUCCESS("SQLite configurado como ativo para o proximo inicio."))
                return
            if options["activate"]:
                activate_candidate()
                self.stdout.write(self.style.SUCCESS("MySQL validado configurado para o proximo inicio; reinicie web e worker."))
                return
            if options["prepare"]:
                report = prepare_candidate_schema(full_snapshot=True) if options["full_snapshot"] else prepare_candidate_schema()
                if report["newly_applied"]:
                    message = f"Schema preparado; migrations aplicadas nesta etapa: {report['newly_applied']} (total: {report['applied_migrations']})."
                else:
                    message = f"Schema ja estava atualizado; nenhuma migration pendente ({report['applied_migrations']} registradas)."
                self.stdout.write(self.style.SUCCESS(message))
                return
            source_path = options.get("source_sqlite")
            if source_path:
                with sqlite_source_database(source_path) as source_alias:
                    self._run_data_phase(options, source_alias)
            else:
                self._run_data_phase(options, "default")
        except CommandError:
            raise
        except ValidationError as exc:
            if not isinstance(exc, CandidateStageError):
                log_candidate_stage_failure(self._selected_stage(options), exc)
            raise CommandError(" ".join(str(item) for item in exc.messages)) from None
        except Exception as exc:
            log_candidate_stage_failure(self._selected_stage(options), exc)
            raise CommandError("Falha segura na etapa do Banco Arion. Consulte logs tecnicos sem incluir credenciais.") from None

    @staticmethod
    def _selected_stage(options):
        return next((name for name in ("dry_run", "prepare", "copy_local_data", "validate", "activate", "rollback")
                     if options.get(name)), "unknown")

    def _run_data_phase(self, options, source_alias):
        if options.get("full_snapshot"):
            from aplicativos.integracoes.services.arion_database_snapshot import run_snapshot_phase
            path = Path(options.get("report") or "").resolve()
            if not path.is_relative_to((Path(settings.BASE_DIR).parent / "var").resolve()) or path.suffix != ".json":
                raise CommandError("Snapshot completo exige --report JSON privado dentro de var/.")
            approved_path = options.get("approved_report")
            approved = json.loads(Path(approved_path).read_text(encoding="utf-8")) if approved_path else None
            if approved_path and Path(approved_path).resolve() == path:
                raise CommandError("Preserve o preview aprovado em arquivo separado.")
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8"):
                pass
            report = run_snapshot_phase(source_alias, self._selected_stage(options), approved=approved)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Snapshot completo: {self._selected_stage(options)}; relatório privado gravado; conflitos: {len(report.get('conflicts', []))}."))
            return
        if options["dry_run"]:
            report = dry_run_migration(source_alias=source_alias)
            self.stdout.write(self.style.SUCCESS(
                f"Dry run OK; conexao: OK; schema: {'OK' if report['schema_exists'] else 'invalido'}; "
                f"django_migrations: {'presente' if report['migration_table_exists'] else 'ausente'}; "
                f"migrations pendentes: {report['migrations']['pending_migrations']}; "
                f"privilegios suficientes: {report['required_privileges']['sufficient']}; "
                f"models locais: {len(report['source_counts'])}; conflitos de usuario: {report['username_conflict_count']}."
            ))
        elif options["copy_local_data"]:
            report = copy_local_data(source_alias=source_alias)
            backup = "" if not report.get("sqlite_backup") else f" Backup SQLite: {report['sqlite_backup']}"
            self.stdout.write(self.style.SUCCESS(f"Dados locais copiados em {len(report['copied_counts'])} models.{backup}"))
        elif options["validate"]:
            report = validate_candidate_migration(source_alias=source_alias)
            self.stdout.write(self.style.SUCCESS(f"Validacao aprovada: {len(report['checks'])} verificacoes."))
