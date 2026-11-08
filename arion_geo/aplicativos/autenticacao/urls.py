from django.urls import path
from django.contrib.auth import views as auth_views

from .forms import LoginForm
from . import views

app_name = "autenticacao"

urlpatterns = [
    path("ativacao/", views.activate, name="activate"),
    path("redefinir-senha/", views.password_reset, name="password-reset"),
    path("senha/obrigatoria/", views.required_password, name="required-password"),
    path("login/", auth_views.LoginView.as_view(template_name="autenticacao/login.html", authentication_form=LoginForm, redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
