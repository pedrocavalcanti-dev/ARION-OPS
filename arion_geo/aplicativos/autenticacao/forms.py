from django import forms
from django.contrib.auth.forms import AuthenticationForm

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
