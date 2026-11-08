from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, SetPasswordForm
from django.core.exceptions import ValidationError

from .throttle import clear_login_failures, is_login_throttled, record_login_failure


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        label="Usuário",
        widget=forms.TextInput(attrs={"autocomplete": "username", "autofocus": True, "placeholder": "Seu usuário"}),
    )
    password = forms.CharField(
        label="Senha",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "placeholder": "Sua senha"}),
    )

    def clean(self):
        username = self.cleaned_data.get("username")
        if is_login_throttled(self.request, username):
            raise self.get_invalid_login_error()
        try:
            cleaned = super().clean()
        except forms.ValidationError:
            record_login_failure(self.request, username)
            raise
        clear_login_failures(self.request, username)
        return cleaned


class PasswordSetForm(SetPasswordForm):
    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("new_password2")
        if password and self.user.check_password(password):
            self.add_error("new_password2", ValidationError("A nova senha deve ser diferente da senha atual."))
        return cleaned


class VerifiedPasswordSetForm(PasswordSetForm):
    verification_code = forms.RegexField(
        regex=r"^\d{8}$",
        label="Código de verificação",
        error_messages={"invalid": "Informe o código de 8 dígitos recebido por e-mail."},
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric", "maxlength": "8"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(("verification_code", "new_password1", "new_password2"))


class SecurePasswordChangeForm(PasswordChangeForm):
    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("new_password2")
        if password and self.user.check_password(password):
            self.add_error("new_password2", ValidationError("A nova senha deve ser diferente da senha atual."))
        return cleaned
