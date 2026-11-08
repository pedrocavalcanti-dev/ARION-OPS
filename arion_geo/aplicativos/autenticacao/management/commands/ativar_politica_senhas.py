from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from aplicativos.auditoria.services import AuditService
from aplicativos.configuracoes.access_control import is_system_admin

from aplicativos.autenticacao.models import PasswordPolicy, UserSecurityProfile


class Command(BaseCommand):
    help = "Ativa de forma controlada a troca inicial e a renovação periódica de senhas."

    def add_arguments(self, parser):
        parser.add_argument("--confirm", action="store_true", help="Grava a ativação; sem esta opção o comando apenas simula.")
        parser.add_argument("--actor-username", help="Usuário administrador responsável pela operação auditada.")
        parser.add_argument("--exclude-usernames", nargs="+", default=[], help="Contas de serviço não interativas identificadas previamente.")
        parser.add_argument("--include-usernames", nargs="+", default=[], help="Contas anteriormente excluídas que devem voltar a ser tratadas como humanas.")
        parser.add_argument("--max-age-days", type=int, help="Prazo corporativo em dias completos; o padrão inicial é 90.")

    def handle(self, *args, **options):
        max_age_days = options["max_age_days"]
        if max_age_days is not None and not 1 <= max_age_days <= 3650:
            raise CommandError("--max-age-days deve estar entre 1 e 3650.")
        excluded = {name.strip() for name in options["exclude_usernames"] if name.strip()}
        included = {name.strip() for name in options["include_usernames"] if name.strip()}
        if excluded & included:
            raise CommandError("Um login não pode aparecer em --exclude-usernames e --include-usernames ao mesmo tempo.")
        User = get_user_model()
        missing_names = excluded | included
        missing = missing_names - set(User.objects.filter(username__in=missing_names).values_list("username", flat=True))
        if missing:
            raise CommandError("Contas informadas para exclusão não encontradas: " + ", ".join(sorted(missing)))

        policy = PasswordPolicy.objects.filter(pk=1).first()
        cutoff = policy.activated_at if policy and policy.activated_at else timezone.now()
        cohort = User.objects.filter(date_joined__lte=cutoff)
        if excluded or included:
            cohort = (cohort | User.objects.filter(username__in=excluded | included)).distinct()
        users = list(cohort.order_by("pk"))
        if options["confirm"]:
            actor_name = options.get("actor_username")
            if not actor_name:
                raise CommandError("Informe --actor-username ao confirmar uma alteração auditada.")
            actor = User.objects.filter(username=actor_name, is_active=True).first()
            if not actor or not is_system_admin(actor):
                raise CommandError("O responsável deve ser um usuário ativo do Administrador do Sistema.")
            now = timezone.now()
            with transaction.atomic():
                policy, _ = PasswordPolicy.objects.select_for_update().get_or_create(pk=1)
                if not policy.activated_at:
                    policy.activated_at = now
                policy.active = True
                if max_age_days is not None:
                    policy.max_age_days = max_age_days
                elif not policy.max_age_days:
                    policy.max_age_days = 90
                policy.updated_by = actor
                policy.save()
                cutoff = policy.activated_at
                marked = 0
                service_accounts = 0
                affected = User.objects.filter(date_joined__lte=cutoff)
                if excluded or included:
                    affected = (affected | User.objects.filter(username__in=excluded | included)).distinct()
                for user in affected.order_by("pk"):
                    profile, _ = UserSecurityProfile.objects.get_or_create(user=user)
                    if user.username in included:
                        profile.is_service_account = False
                        profile.must_change_password = True
                        profile.change_reason = UserSecurityProfile.ChangeReason.INITIAL
                        if not profile.first_activation_completed_at:
                            profile.first_activation_completed_at = user.date_joined
                        profile.save(update_fields=("is_service_account", "first_activation_completed_at", "must_change_password", "change_reason", "updated_at"))
                        marked += 1
                        continue
                    if user.username in excluded:
                        profile.is_service_account = True
                        profile.must_change_password = False
                        profile.change_reason = ""
                        profile.save(update_fields=("is_service_account", "must_change_password", "change_reason", "updated_at"))
                        service_accounts += 1
                        continue
                    if profile.is_service_account:
                        service_accounts += 1
                        continue
                    if profile.password_changed_at and profile.password_changed_at >= cutoff and not profile.must_change_password:
                        continue
                    if not profile.first_activation_completed_at:
                        profile.first_activation_completed_at = user.date_joined
                    profile.must_change_password = True
                    profile.change_reason = UserSecurityProfile.ChangeReason.INITIAL
                    profile.save(update_fields=("first_activation_completed_at", "must_change_password", "change_reason", "updated_at"))
                    marked += 1
                AuditService.record(
                    user=actor, category="SECURITY", action="PASSWORD_POLICY_ACTIVATED",
                    module="Autenticação", description="Política corporativa de renovação de senha ativada.",
                    obj=policy, metadata={"accounts_marked": marked, "service_accounts_excluded": service_accounts,
                                          "max_age_days": policy.max_age_days, "activated_at": cutoff},
                )
            self.stdout.write(self.style.SUCCESS(
                f"Política ativa. {marked} contas obrigadas a renovar; {service_accounts} contas de serviço excluídas; "
                f"prazo de {policy.max_age_days} dias."
            ))
            return

        service_ids = set(UserSecurityProfile.objects.filter(
            user_id__in=[user.pk for user in users], is_service_account=True,
        ).values_list("user_id", flat=True))
        pending = sum(1 for user in users if user.username not in excluded and (user.username in included or user.pk not in service_ids))
        self.stdout.write(
            f"Simulação: {pending} contas serão marcadas e {len(excluded)} excluídas; "
            f"{len(included)} voltam ao fluxo humano. "
            f"Prazo: {max_age_days or (policy.max_age_days if policy else 90)} dias."
        )
        self.stdout.write("Nenhuma alteração foi gravada. Revise as contas de serviço e use --confirm --actor-username <admin> para ativar.")
