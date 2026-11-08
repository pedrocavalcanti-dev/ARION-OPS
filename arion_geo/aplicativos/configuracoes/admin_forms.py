from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError

from aplicativos.base_links.models import Operadora, Projeto
from .access_control import PERMISSION_LABELS, managed_permissions
from .models import GroupDataScope
from aplicativos.autenticacao.models import UserSecurityProfile


class ScopedUserFieldsMixin:
    def configure_group_field(self, instance=None):
        self.fields.setdefault("group", forms.ModelChoiceField(queryset=Group.objects.none(), required=True, label="Perfil de acesso", empty_label="Selecione um perfil"))
        self.fields["group"].queryset = Group.objects.order_by("name")
        self.fields["group"].widget.attrs.update({"class": "settings-select"})
        if instance and instance.pk:
            if instance.is_superuser:
                self.fields["group"].required = False
            memberships = list(instance.groups.order_by("name"))
            if len(memberships) == 1:
                self.fields["group"].initial = memberships[0].pk

    def save_access(self, user):
        selected_group = self.cleaned_data.get("group")
        user.groups.set([selected_group] if selected_group else [])


class UserCreateForm(ScopedUserFieldsMixin, forms.ModelForm):
    first_name = forms.CharField(max_length=150, required=False, label="Nome")
    last_name = forms.CharField(max_length=150, required=False, label="Sobrenome")
    email = forms.EmailField(required=True, label="E-mail corporativo")
    is_active = forms.BooleanField(required=False, initial=True, label="Conta ativa")

    class Meta:
        model = get_user_model()
        fields = ("username", "first_name", "last_name", "email", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configure_group_field()
        self.fields["is_active"].widget.attrs.update({"class": "switch-input"}) if "is_active" in self.fields else None

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_unusable_password()
        user.is_active = self.cleaned_data["is_active"]
        if commit:
            user.save()
            self.save_access(user)
            UserSecurityProfile.objects.create(
                user=user,
                change_reason=UserSecurityProfile.ChangeReason.INVITATION,
            )
        return user


class UserUpdateForm(ScopedUserFieldsMixin, forms.ModelForm):
    class Meta:
        model = get_user_model()
        fields = ("first_name", "last_name", "email", "is_active")
        labels = {"first_name": "Nome", "last_name": "Sobrenome", "email": "E-mail", "is_active": "Conta ativa"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configure_group_field(self.instance)
        self.fields["is_active"].widget.attrs["class"] = "switch-input"

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            self.save_access(user)
        return user


class AccessGroupForm(forms.ModelForm):
    permissions = forms.ModelMultipleChoiceField(
        queryset=Permission.objects.none(), required=False, widget=forms.CheckboxSelectMultiple,
        label="Permissões",
    )

    class Meta:
        model = Group
        fields = ("name",)
        labels = {"name": "Nome do perfil de acesso"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.managed_permissions = managed_permissions().select_related("content_type").order_by("content_type__app_label", "codename")
        self.fields["permissions"].queryset = self.managed_permissions
        if self.instance.pk:
            self.fields["permissions"].initial = self.instance.permissions.filter(pk__in=self.managed_permissions.values("pk"))

    def grouped_permissions(self):
        groups = {}
        selected_values = {str(value) for value in (self["permissions"].value() or [])}
        for permission in self.managed_permissions:
            module, label = PERMISSION_LABELS[permission.codename]
            groups.setdefault(module, []).append({"permission": permission, "label": label, "selected": str(permission.pk) in selected_values})
        return groups

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if self.instance.pk and self.instance.name == "Administrador do Sistema" and name != self.instance.name:
            raise ValidationError("O nome do perfil Administrador do Sistema é reservado.")
        return name

    def save(self, commit=True):
        group = super().save(commit=commit)
        if commit:
            managed_ids = self.managed_permissions.values_list("pk", flat=True)
            group.permissions.remove(*Permission.objects.filter(pk__in=managed_ids))
            group.permissions.add(*self.cleaned_data["permissions"])
        return group


class GroupDataScopeForm(forms.ModelForm):
    class Meta:
        model = GroupDataScope
        fields = ("mode", "allowed_operators", "allowed_projects")
        labels = {"mode": "Escopo dos dados", "allowed_operators": "Operadoras permitidas", "allowed_projects": "Projetos permitidos"}
        widgets = {
            "allowed_operators": forms.CheckboxSelectMultiple(attrs={"class": "scope-choice-list"}),
            "allowed_projects": forms.CheckboxSelectMultiple(attrs={"class": "scope-choice-list"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["allowed_operators"].queryset = Operadora.objects.filter(active=True).order_by("name")
        self.fields["allowed_projects"].queryset = Projeto.objects.filter(active=True).order_by("name")
        self.fields["mode"].widget.attrs["data-scope-mode"] = ""

    def save(self, commit=True):
        scope = super().save(commit=commit)
        if commit and scope.mode == GroupDataScope.Mode.ALL:
            scope.allowed_operators.clear()
            scope.allowed_projects.clear()
        return scope
