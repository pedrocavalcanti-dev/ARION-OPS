from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class UserSecurityProfile(models.Model):
    class ChangeReason(models.TextChoices):
        INITIAL = "initial", "Troca inicial obrigatória"
        EXPIRED = "expired", "Senha expirada"
        ADMIN = "admin", "Exigida pela administração"
        INVITATION = "invitation", "Primeiro acesso por convite"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="security_profile",
    )
    is_service_account = models.BooleanField(default=False)
    must_change_password = models.BooleanField(default=False, db_index=True)
    change_reason = models.CharField(max_length=20, choices=ChangeReason.choices, blank=True)
    password_changed_at = models.DateTimeField(null=True, blank=True)
    first_activation_completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def password_expires_at(self, *, max_age_days=90):
        if not self.password_changed_at:
            return None
        return self.password_changed_at + timedelta(days=max_age_days)


class AccessToken(models.Model):
    class Purpose(models.TextChoices):
        ACTIVATION = "activation", "Ativação de conta"
        PASSWORD_RESET = "password_reset", "Redefinição administrativa"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="access_tokens",
    )
    purpose = models.CharField(max_length=20, choices=Purpose.choices, db_index=True)
    token_hash = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="issued_access_tokens",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=("user", "purpose", "created_at"))]
        ordering = ("-created_at", "-pk")


class VerificationChallenge(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="password_verification_challenge",
    )
    code_hash = models.CharField(max_length=256, blank=True)
    created_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    window_started_at = models.DateTimeField(null=True, blank=True)
    send_count = models.PositiveSmallIntegerField(default=0)
    attempt_count = models.PositiveSmallIntegerField(default=0)


class PasswordPolicy(models.Model):
    active = models.BooleanField(default=False)
    activated_at = models.DateTimeField(null=True, blank=True)
    max_age_days = models.PositiveSmallIntegerField(default=90)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_password_policies",
    )

    class Meta:
        verbose_name = "política de senha"
        verbose_name_plural = "políticas de senha"

    @classmethod
    def current(cls):
        policy, _ = cls.objects.get_or_create(pk=1)
        return policy
