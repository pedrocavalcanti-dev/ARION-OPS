from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from aplicativos.auditoria.services import AuditService
from aplicativos.configuracoes.access_control import is_system_admin

from aplicativos.autenticacao.models import AccessToken, UserSecurityProfile
from aplicativos.autenticacao.security import issue_access_token


class Command(BaseCommand):
    help = "Emite um link único de redefinição para recuperar uma conta de administrador pelo servidor local."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True, help="Login da conta de administrador que será recuperada.")
        parser.add_argument("--actor-username", required=True, help="Administrador responsável pela emissão, para auditoria.")
        parser.add_argument("--confirm-email", required=True, help="E-mail corporativo informado e confirmado pelo canal de suporte.")

    def handle(self, *args, **options):
        User = get_user_model()
        actor = User.objects.filter(username=options["actor_username"], is_active=True).first()
        target = User.objects.filter(username=options["username"], is_active=True).first()
        if not actor or not is_system_admin(actor):
            raise CommandError("O responsável deve ser um usuário ativo do Administrador do Sistema.")
        if not target or not is_system_admin(target):
            raise CommandError("A conta de destino precisa ser um administrador ativo.")
        if options["confirm_email"].strip().casefold() != target.email.strip().casefold():
            raise CommandError("O e-mail confirmado não corresponde ao e-mail corporativo cadastrado.")
        profile, _ = UserSecurityProfile.objects.get_or_create(user=target)
        if profile.is_service_account:
            raise CommandError("Contas de serviço não podem receber redefinição interativa.")
        if not profile.first_activation_completed_at:
            raise CommandError("A conta ainda não concluiu o primeiro acesso por convite.")
        token, url = issue_access_token(user=target, purpose=AccessToken.Purpose.PASSWORD_RESET, actor=actor)
        AuditService.record(
            user=actor, category="SECURITY", action="ADMIN_PASSWORD_RESET_LINK_ISSUED",
            module="Autenticação", description="Link de redefinição de administrador emitido pelo comando local.",
            obj=target, metadata={"expires_at": token.expires_at, "local_recovery": True},
        )
        self.stdout.write(self.style.SUCCESS("Link único de redefinição (validade de 48 horas):"))
        self.stdout.write(url)
        self.stdout.write("Encaminhe o link apenas ao e-mail confirmado por canal corporativo seguro.")
