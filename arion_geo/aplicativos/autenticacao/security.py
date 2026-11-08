"""Password lifecycle, one-time links and verified password updates."""

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import AccessToken, PasswordPolicy, UserSecurityProfile, VerificationChallenge

INVITATION_TTL = timedelta(hours=48)
OTP_TTL = timedelta(minutes=10)
OTP_RESEND_DELAY = timedelta(seconds=60)
OTP_WINDOW = timedelta(hours=1)
OTP_MAX_SENDS = 5
OTP_MAX_ATTEMPTS = 5
_POLICY_UNSET = object()


def token_digest(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def issue_access_token(*, user, purpose, actor=None, now=None):
    now = now or timezone.now()
    raw_token = secrets.token_urlsafe(32)
    expires_at = now + INVITATION_TTL
    with transaction.atomic():
        AccessToken.objects.select_for_update().filter(
            user=user, purpose=purpose, consumed_at__isnull=True, revoked_at__isnull=True,
        ).update(revoked_at=now)
        token = AccessToken.objects.create(
            user=user, purpose=purpose, token_hash=token_digest(raw_token),
            created_by=actor, expires_at=expires_at,
        )
    route = "autenticacao:activate" if purpose == AccessToken.Purpose.ACTIVATION else "autenticacao:password-reset"
    url = f"{settings.ARION_PUBLIC_BASE_URL}{reverse(route)}#{raw_token}"
    return token, url


def valid_access_token(raw_token, purpose, *, now=None, lock=False):
    if not raw_token or len(raw_token) > 128:
        return None
    now = now or timezone.now()
    query = AccessToken.objects.select_related("user").filter(
        token_hash=token_digest(raw_token), purpose=purpose,
        consumed_at__isnull=True, revoked_at__isnull=True, expires_at__gt=now,
        user__is_active=True,
    )
    if lock:
        query = query.select_for_update()
    return query.first()


def apply_access_token_password(*, raw_token, purpose, new_password, now=None):
    """Atomically consume a link, set the password and update the security state."""
    now = now or timezone.now()
    with transaction.atomic():
        token = valid_access_token(raw_token, purpose, now=now, lock=True)
        if token is None:
            return None
        profile, _ = UserSecurityProfile.objects.select_for_update().get_or_create(user=token.user)
        if purpose == AccessToken.Purpose.ACTIVATION and profile.first_activation_completed_at:
            return None
        if purpose == AccessToken.Purpose.PASSWORD_RESET and not profile.first_activation_completed_at:
            return None
        used = AccessToken.objects.filter(
            pk=token.pk, consumed_at__isnull=True, revoked_at__isnull=True, expires_at__gt=now,
        ).update(consumed_at=now)
        if used != 1:
            return None
        token.user.set_password(new_password)
        token.user.save(update_fields=("password",))
        profile.password_changed_at = now
        profile.must_change_password = False
        profile.change_reason = ""
        if purpose == AccessToken.Purpose.ACTIVATION and not profile.first_activation_completed_at:
            profile.first_activation_completed_at = now
        profile.save(update_fields=(
            "password_changed_at", "must_change_password", "change_reason",
            "first_activation_completed_at", "updated_at",
        ))
        return token.user


def current_password_policy():
    return PasswordPolicy.objects.filter(pk=1).first()


def password_change_required(user, *, now=None):
    if not user.is_authenticated:
        return False
    policy = current_password_policy()
    now = now or timezone.now()
    profile, _ = UserSecurityProfile.objects.get_or_create(
        user=user,
        defaults={
            "must_change_password": bool(policy and policy.active),
            "change_reason": UserSecurityProfile.ChangeReason.INITIAL,
        },
    )
    if profile.is_service_account:
        return False
    if profile.must_change_password and profile.change_reason == UserSecurityProfile.ChangeReason.ADMIN:
        return True
    if not policy or not policy.active:
        return False
    if profile.must_change_password:
        return True
    if not profile.password_changed_at:
        if not profile.must_change_password:
            profile.must_change_password = True
            profile.change_reason = UserSecurityProfile.ChangeReason.INITIAL
            profile.save(update_fields=("must_change_password", "change_reason", "updated_at"))
        return True
    expires_at = profile.password_expires_at(max_age_days=policy.max_age_days)
    if expires_at and now >= expires_at:
        if profile.change_reason != UserSecurityProfile.ChangeReason.EXPIRED:
            profile.must_change_password = True
            profile.change_reason = UserSecurityProfile.ChangeReason.EXPIRED
            profile.save(update_fields=("must_change_password", "change_reason", "updated_at"))
        return True
    return False


def password_access_status(user, *, now=None, policy=_POLICY_UNSET):
    if not user.is_active:
        return "inactive", "Inativo"
    try:
        profile = user.security_profile
    except UserSecurityProfile.DoesNotExist:
        profile = None
    if profile and profile.is_service_account:
        return "active", "Ativo"
    if profile and profile.first_activation_completed_at is None:
        invitation = user.access_tokens.filter(purpose=AccessToken.Purpose.ACTIVATION).first()
        if invitation and invitation.revoked_at:
            return "pending", "Aguardando ativação"
        if invitation and invitation.expires_at <= (now or timezone.now()):
            return "invitation_expired", "Convite expirado"
        return "pending", "Aguardando ativação"
    if not profile:
        return "pending", "Troca obrigatória"
    if policy is _POLICY_UNSET:
        policy = current_password_policy()
    if profile.must_change_password:
        if profile.change_reason == UserSecurityProfile.ChangeReason.INITIAL and not (policy and policy.active):
            return "active", "Ativo"
        if profile.change_reason == UserSecurityProfile.ChangeReason.EXPIRED:
            return "password_expired", "Senha expirada"
        return "required", "Troca obrigatória"
    if policy and policy.active:
        expires_at = profile.password_expires_at(max_age_days=policy.max_age_days)
        if not expires_at or expires_at <= (now or timezone.now()):
            return "password_expired", "Senha expirada"
    return "active", "Ativo"


def mask_email(email):
    email = str(email or "").strip()
    if "@" not in email:
        return ""
    local, domain = email.rsplit("@", 1)
    visible = local[:1]
    return f"{visible}{'*' * max(2, len(local) - 1)}@{domain}"


def send_verification_code(user, code):
    """Send an account security code only through the configured, enabled Brevo transport."""
    from aplicativos.integracoes.clients.brevo import BrevoTransactionalClient
    from aplicativos.integracoes.clients.brevo_smtp import send_brevo_smtp
    from aplicativos.integracoes.models import ExternalAPIIntegration
    from aplicativos.integracoes.services.external_apis import (
        decrypt_external_api_secret,
        decrypt_external_api_smtp_secret,
    )

    if not user.email:
        return False
    integration = ExternalAPIIntegration.objects.filter(
        provider=ExternalAPIIntegration.Provider.BREVO,
        enabled=True,
        allow_real_sends=True,
    ).first()
    if not integration:
        return False
    config = integration.config or {}
    subject = "Código de segurança do ARION OPS"
    text = (
        "Olá.\n\n"
        f"Seu código para confirmar a troca de senha no ARION OPS é: {code}\n"
        "O código expira em 10 minutos e só pode ser usado uma vez.\n\n"
        "Se você não iniciou esta solicitação, ignore esta mensagem."
    )
    html = (
        "<p>Olá,</p><p>Use este código para confirmar a troca de senha no ARION OPS:</p>"
        f"<p style=\"font-size:24px;font-weight:700;letter-spacing:4px\">{code}</p>"
        "<p>Ele expira em 10 minutos e só pode ser usado uma vez.</p>"
        "<p>Se você não iniciou esta solicitação, ignore esta mensagem.</p>"
    )
    method = config.get("send_method", "smtp")
    try:
        if method == "smtp":
            password = decrypt_external_api_smtp_secret(integration)
            login = str(config.get("smtp_login") or "").strip()
            sender_email = str(config.get("sender_email") or "").strip()
            if not password or not login or not sender_email:
                return False
            result = send_brevo_smtp(
                username=login,
                password=password,
                config={"sender_name": config.get("sender_name") or "ARION OPS", "sender_email": sender_email,
                        "quality_recipient": user.email},
                subject=subject,
                text_content=text,
                html_content=html,
            )
            return bool(result.ok)
        if method == "api_rest":
            api_key = str(decrypt_external_api_secret(integration) or "").strip()
            if not api_key:
                return False
            payload = BrevoTransactionalClient.build_transactional_payload(
                sender_email=config.get("sender_email"),
                sender_name=config.get("sender_name") or "ARION OPS",
                to_email=user.email,
                subject=subject,
                text_content=text,
                html_content=html,
                tags=["arion-ops", "account-security"],
            )
            result = BrevoTransactionalClient(api_key).send_transactional(
                payload, allow_real_sends=integration.allow_real_sends,
            )
            return bool(result.ok and result.message_id)
    except Exception:
        return False
    return False


def send_password_verification_code(user, *, now=None):
    now = now or timezone.now()
    code = f"{secrets.randbelow(100_000_000):08d}"
    with transaction.atomic():
        challenge, _ = VerificationChallenge.objects.select_for_update().get_or_create(user=user)
        if challenge.window_started_at is None or now - challenge.window_started_at >= OTP_WINDOW:
            challenge.window_started_at = now
            challenge.send_count = 0
        if challenge.last_sent_at and now - challenge.last_sent_at < OTP_RESEND_DELAY:
            return False, "aguarde"
        if challenge.send_count >= OTP_MAX_SENDS:
            return False, "limite"
        challenge.code_hash = make_password(code)
        challenge.expires_at = now + OTP_TTL
        challenge.consumed_at = None
        challenge.last_sent_at = now
        challenge.send_count += 1
        challenge.attempt_count = 0
        challenge.save()
        issued_hash = challenge.code_hash
    if send_verification_code(user, code):
        return True, "enviado"
    VerificationChallenge.objects.filter(
        user=user, code_hash=issued_hash, consumed_at__isnull=True,
    ).update(code_hash="", expires_at=now)
    return False, "indisponivel"


def apply_verified_password_change(user, *, code, new_password, now=None):
    """Consume an OTP and update the password and policy state in one transaction."""
    now = now or timezone.now()
    with transaction.atomic():
        profile, _ = UserSecurityProfile.objects.select_for_update().get_or_create(user=user)
        try:
            challenge = VerificationChallenge.objects.select_for_update().get(user=user)
        except VerificationChallenge.DoesNotExist:
            return False, "invalido"
        if challenge.consumed_at or not challenge.code_hash:
            return False, "invalido"
        if not challenge.expires_at or challenge.expires_at <= now:
            return False, "expirado"
        if challenge.attempt_count >= OTP_MAX_ATTEMPTS:
            return False, "limite"
        if not check_password(code, challenge.code_hash):
            challenge.attempt_count += 1
            challenge.save(update_fields=("attempt_count", "created_at"))
            return False, "invalido"
        challenge.consumed_at = now
        challenge.code_hash = ""
        challenge.save(update_fields=("consumed_at", "code_hash", "created_at"))
        user.set_password(new_password)
        user.save(update_fields=("password",))
        profile.must_change_password = False
        profile.change_reason = ""
        profile.password_changed_at = now
        profile.save(update_fields=("must_change_password", "change_reason", "password_changed_at", "updated_at"))
        return True, "alterado"
