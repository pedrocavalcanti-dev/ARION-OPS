from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from aplicativos.auditoria.services import AuditService

from .forms import PasswordSetForm, VerifiedPasswordSetForm
from .models import AccessToken, UserSecurityProfile
from .security import (
    apply_access_token_password,
    apply_verified_password_change,
    get_security_profile,
    mask_email,
    password_change_required,
    send_password_verification_code,
    valid_access_token,
)


def home(request):
    destination = "painel:index" if request.user.is_authenticated else "autenticacao:login"
    return redirect(destination)


def _invalid_link(request, *, purpose):
    is_activation = purpose == AccessToken.Purpose.ACTIVATION
    response = render(request, "autenticacao/password_flow.html", {
        "flow": "activation" if is_activation else "reset",
        "title": "Bem-vindo ao ARION OPS" if is_activation else "Redefinir senha",
        "description": "Sua conta foi criada pela administração. Defina sua senha pessoal para começar." if is_activation else "Defina uma nova senha pessoal para recuperar o acesso.",
        "invalid_link": True,
        "form": None,
    }, status=400)
    return _set_sensitive_flow_headers(response)


@require_http_methods(["GET", "POST"])
def activate(request):
    return _complete_link_password(request, AccessToken.Purpose.ACTIVATION)


@require_http_methods(["GET", "POST"])
def password_reset(request):
    return _complete_link_password(request, AccessToken.Purpose.PASSWORD_RESET)


def _complete_link_password(request, purpose):
    is_activation = purpose == AccessToken.Purpose.ACTIVATION
    flow = "activation" if is_activation else "reset"
    title = "Bem-vindo ao ARION OPS" if is_activation else "Redefinir senha"
    description = (
        "Sua conta foi criada pela administração. Para começar, defina sua senha pessoal de acesso."
        if is_activation else "Defina uma nova senha pessoal para recuperar o acesso ao ARION OPS."
    )
    if request.method == "GET":
        form = PasswordSetForm(user=None)
        context = {"flow": flow, "title": title, "description": description, "form": form, "invalid_link": False}
        response = render(request, "autenticacao/password_flow.html", context)
        _set_sensitive_flow_headers(response)
        return response

    raw_token = request.POST.get("token", "")
    token = valid_access_token(raw_token, purpose)
    if token is None:
        return _invalid_link(request, purpose=purpose)
    profile = get_security_profile(token.user)
    if (is_activation and profile.first_activation_completed_at) or (not is_activation and not profile.first_activation_completed_at):
        return _invalid_link(request, purpose=purpose)
    if request.POST.get("action") == "inspect_token":
        response = JsonResponse({"display_name": token.user.get_short_name() or token.user.username})
        return _set_sensitive_flow_headers(response)
    form = PasswordSetForm(token.user, request.POST)
    if form.is_valid():
        changed_user = apply_access_token_password(
            raw_token=raw_token,
            purpose=purpose,
            new_password=form.cleaned_data["new_password1"],
        )
        if changed_user is None:
            return _invalid_link(request, purpose=purpose)
        AuditService.record(
            request=request, category="SECURITY",
            action="ACCESS_INVITATION_CONSUMED" if is_activation else "ADMIN_PASSWORD_RESET_COMPLETED",
            module="Autenticação",
            description="Convite de primeiro acesso consumido" if is_activation else "Senha redefinida por link administrativo",
            obj=changed_user,
        )
        if is_activation:
            AuditService.record(
                request=request, category="SECURITY", action="ACCOUNT_ACTIVATED",
                module="Autenticação", description="Conta ativada pelo convite de primeiro acesso.", obj=changed_user,
            )
        response = render(request, "autenticacao/password_flow.html", {
            "flow": flow, "title": "Senha definida com sucesso", "description": "Agora você pode entrar no ARION OPS com sua nova senha.",
            "completed": True, "invalid_link": False, "form": None,
        })
        _set_sensitive_flow_headers(response)
        return response
    response = render(request, "autenticacao/password_flow.html", {
        "flow": flow, "title": title, "description": description,
        "form": form, "invalid_link": False, "display_name": token.user.get_short_name() or token.user.username,
    }, status=400)
    _set_sensitive_flow_headers(response)
    return response


@login_required
@require_http_methods(["GET", "POST"])
def required_password(request):
    if not password_change_required(request.user):
        return redirect("painel:index")
    profile, _ = UserSecurityProfile.objects.get_or_create(user=request.user)
    reason = "Sua senha completou o prazo de 90 dias." if profile.change_reason == UserSecurityProfile.ChangeReason.EXPIRED else "A política de segurança exige que você defina uma senha pessoal antes de continuar."
    if request.method == "POST" and request.POST.get("action") == "send_code":
        sent, result = send_password_verification_code(request.user)
        if sent:
            AuditService.record(
                request=request, category="SECURITY", action="PASSWORD_VERIFICATION_CODE_SENT",
                module="Autenticação", description="Código de segurança enviado para confirmação de identidade.", obj=request.user,
            )
            messages.success(request, "Se o e-mail corporativo estiver configurado, o código chegará em instantes.")
        else:
            AuditService.record(
                request=request, category="SECURITY", action="PASSWORD_VERIFICATION_CODE_SEND_FAILED",
                result="FAILURE", module="Autenticação",
                description="Não foi possível enviar o código de segurança.", obj=request.user,
                metadata={"reason": result},
            )
            messages.error(request, "Não foi possível enviar o código. Verifique a configuração de e-mail com a administração.")
        return redirect("autenticacao:required-password")

    form = VerifiedPasswordSetForm(request.user, request.POST or None) if request.method == "POST" else VerifiedPasswordSetForm(request.user)
    if request.method == "POST" and request.POST.get("action") == "change_password" and form.is_valid():
        changed, result = apply_verified_password_change(
            request.user,
            code=form.cleaned_data["verification_code"],
            new_password=form.cleaned_data["new_password1"],
        )
        if changed:
            AuditService.record(
                request=request, category="SECURITY", action="PASSWORD_VERIFICATION_CODE_VALIDATED",
                module="Autenticação", description="Código de segurança validado para alteração de senha.", obj=request.user,
            )
            AuditService.record(
                request=request, user=request.user, category="SECURITY",
                action="PASSWORD_CHANGED_INITIAL" if profile.change_reason != UserSecurityProfile.ChangeReason.EXPIRED else "PASSWORD_RENEWED",
                module="Autenticação", description="Senha alterada após confirmação de identidade.", obj=request.user,
            )
            logout(request)
            messages.success(request, "Senha atualizada. Entre novamente com sua nova senha.")
            return redirect("autenticacao:login")
        AuditService.record(
            request=request, category="SECURITY", action="PASSWORD_VERIFICATION_CODE_INVALID",
            result="FAILURE", module="Autenticação", description="Código de segurança inválido, expirado ou bloqueado.",
            obj=request.user, metadata={"reason": result},
        )
        form.add_error("verification_code", {
            "expirado": "O código expirou. Solicite outro código.",
            "limite": "O limite de tentativas foi atingido. Solicite um novo código mais tarde.",
        }.get(result, "O código não é válido. Confira os dígitos ou solicite um novo código."))
    response = render(request, "autenticacao/password_flow.html", {
        "flow": "required", "title": "Atualização obrigatória de senha",
        "description": reason, "form": form, "masked_email": mask_email(request.user.email),
        "invalid_link": False,
    })
    _set_sensitive_flow_headers(response)
    return response


def _set_sensitive_flow_headers(response):
    response["Referrer-Policy"] = "same-origin"
    response["Cache-Control"] = "private, no-store"
    response["Content-Security-Policy"] = (
        "default-src 'none'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response
