from django import forms
from django.contrib.auth import get_user_model
from django.db import models

from aplicativos.integracoes.models import CMDBLinkMirror, OTRSTicket

from .models import QualityContact, QualityMonitoringCycle, QualityOrder, QualityTicket, QualityTreatment
from .services.contacts import CONTACT_TYPES, ESCALATION_LEVELS
from aplicativos.integracoes.services.semantics import link_designation


User = get_user_model()


class CMDBLinkChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return link_designation(obj)


class QualityOrderCreateForm(forms.Form):
    link = CMDBLinkChoiceField(
        queryset=CMDBLinkMirror.objects.none(),
        label="Link / designação",
        empty_label="Selecione um link do CMDB",
    )
    priority = forms.ChoiceField(label="Prioridade", choices=QualityOrder.Priority.choices, initial=QualityOrder.Priority.MEDIA)
    owner = forms.ModelChoiceField(
        queryset=User._default_manager.none(), required=False,
        label="Responsável", empty_label="Não atribuído",
    )
    treatment_deadline = forms.DateField(
        required=False, label="Prazo", input_formats=("%Y-%m-%d",),
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    initial_note = forms.CharField(
        required=False, label="Observação inicial", max_length=4000,
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": 4000}),
    )

    def __init__(self, *args, link_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["link"].queryset = link_queryset if link_queryset is not None else CMDBLinkMirror.objects.none()
        self.fields["owner"].queryset = User._default_manager.filter(is_active=True).order_by("first_name", "last_name", "username")

class QualityOrderOwnerForm(forms.Form):
    owner = forms.ModelChoiceField(
        queryset=User._default_manager.none(), required=False,
        label="Responsável", empty_label="Não atribuído",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owner"].queryset = User._default_manager.filter(is_active=True).order_by("first_name", "last_name", "username")


class QualityOrderPriorityForm(forms.Form):
    priority = forms.ChoiceField(label="Prioridade", choices=QualityOrder.Priority.choices)


class QualityOrderDetailForm(forms.Form):
    status = forms.ChoiceField(label="Status", choices=QualityOrder.Status.choices)
    recurrence_level = forms.ChoiceField(
        label="Nível", required=False,
        choices=(("", "A classificar"), *QualityOrder.RecurrenceLevel.choices),
    )
    priority = forms.ChoiceField(label="Prioridade", choices=QualityOrder.Priority.choices)
    owner = forms.ModelChoiceField(
        queryset=User._default_manager.none(), required=False,
        label="Responsável", empty_label="Não atribuído",
    )
    treatment_deadline = forms.DateField(
        required=False, label="Prazo de tratamento", input_formats=("%Y-%m-%d",),
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    internal_notes = forms.CharField(
        required=False, label="Observação interna", max_length=4000,
        widget=forms.Textarea(attrs={"rows": 4, "maxlength": 4000}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owner"].queryset = User._default_manager.filter(is_active=True).order_by(
            "first_name", "last_name", "username",
        )


class QualityOccurrenceReviewForm(forms.Form):
    class Classification(models.TextChoices):
        INITIAL = "INITIAL", "Ocorrência inicial"
        RECURRENCE = "RECURRENCE", "Reincidência"
        EXCLUDED = "EXCLUDED", "Desconsiderada"

    classification = forms.ChoiceField(label="Classificação", choices=Classification.choices)
    same_root_cause = forms.BooleanField(required=False, label="Mesma causa raiz confirmada")
    reason = forms.CharField(required=False, label="Motivo", max_length=4000)
    root_cause = forms.CharField(required=False, label="Causa raiz", max_length=4000)
    exclusion_reason = forms.ChoiceField(
        required=False, label="Motivo da desconsideração",
        choices=(
            ("", "Selecione um motivo"),
            ("CANCELADO", "Cancelado"),
            ("IMPROCEDENTE", "Improcedente"),
            ("DUPLICADO", "Duplicado"),
            ("FORA_ESCOPO", "Fora do escopo"),
            ("OUTRO", "Outro motivo"),
        ),
    )

    def clean(self):
        cleaned = super().clean()
        classification = cleaned.get("classification")
        if classification == self.Classification.EXCLUDED and not cleaned.get("exclusion_reason"):
            self.add_error("exclusion_reason", "Informe o motivo da desconsideração.")
        if classification != self.Classification.RECURRENCE:
            cleaned["same_root_cause"] = False
        if classification != self.Classification.EXCLUDED:
            cleaned["exclusion_reason"] = ""
        return cleaned


class QualityTicketLinkForm(forms.Form):
    ticket_identifier = forms.CharField(
        label="Número do ticket OTRS", max_length=80,
        widget=forms.TextInput(attrs={"placeholder": "Número do TQ espelhado no OTRS", "autocomplete": "off"}),
    )
    ticket_type = forms.ChoiceField(label="Tipo de vínculo", choices=QualityTicket.TicketType.choices)


class QualityTreatmentCreateForm(forms.Form):
    reason = forms.CharField(
        label="Motivo da tratativa", max_length=4000,
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": 4000}),
    )
    description = forms.CharField(
        label="Observações adicionais", required=False, max_length=4000,
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": 4000}),
    )
    support_ticket = forms.ModelChoiceField(
        queryset=OTRSTicket.objects.none(),
        required=False, label="Chamado de suporte relacionado", empty_label="Sem chamado relacionado",
    )

    def __init__(self, *args, support_tickets=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["support_ticket"].queryset = support_tickets if support_tickets is not None else self.fields["support_ticket"].queryset


class QualityActionForm(forms.Form):
    title = forms.CharField(
        label="Nova ação", max_length=240,
        widget=forms.TextInput(attrs={"placeholder": "Descreva a validação ou ação gerencial"}),
    )


class QualityMonitoringStartForm(forms.Form):
    quality_ticket = forms.ModelChoiceField(
        queryset=QualityTicket.objects.none(), required=False, label="TQ relacionado",
        empty_label="Sem TQ de tratamento vinculado",
    )
    financial_regularization_confirmed = forms.BooleanField(
        required=False, label="Pagamento ou regularização definitiva confirmado",
    )
    financial_evidence = forms.CharField(
        required=False, max_length=2000, label="Evidência da regularização",
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": 2000, "placeholder": "Referência ou confirmação registrada pela Qualidade"}),
    )

    def __init__(self, *args, order=None, **kwargs):
        super().__init__(*args, **kwargs)
        if order is not None:
            self.fields["quality_ticket"].queryset = QualityTicket.objects.filter(
                quality_order=order, ticket_type=QualityTicket.TicketType.TREATMENT,
            ).order_by("-created_at", "-pk")


class QualityMonitoringFinishForm(forms.Form):
    RESULT_CHOICES = (
        (QualityMonitoringCycle.Result.SUCCESS, "Concluído com sucesso"),
        (QualityMonitoringCycle.Result.RECURRENCE, "Reincidiu"),
        (QualityMonitoringCycle.Result.CANCELED, "Cancelado"),
    )
    result = forms.ChoiceField(label="Resultado", choices=RESULT_CHOICES)
    trigger_ticket_identifier = forms.CharField(
        required=False, label="Ticket da reincidência", max_length=80,
        widget=forms.TextInput(attrs={"placeholder": "Número do ticket válido espelhado"}),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("result") == QualityMonitoringCycle.Result.RECURRENCE and not cleaned.get("trigger_ticket_identifier"):
            self.add_error("trigger_ticket_identifier", "Informe o ticket válido que causou a reincidência.")
        return cleaned


class QualityContactForm(forms.ModelForm):
    contact_type = forms.ChoiceField(
        label="Tipo", required=False,
        choices=(("", "Selecione"), *((key, label) for key, label in CONTACT_TYPES.items())),
    )
    escalation_level = forms.ChoiceField(
        label="Nível de escalonamento",
        choices=tuple((str(level), label) for level, label in ESCALATION_LEVELS.items()),
    )

    class Meta:
        model = QualityContact
        fields = (
            "company", "region", "client", "project", "contact_type", "name", "role",
            "email", "phone", "teams", "notes", "escalation_level", "active",
        )
        labels = {
            "company": "Empresa / ISP",
            "region": "Região",
            "client": "Cliente / Operadora",
            "project": "Projeto",
            "name": "Nome",
            "role": "Cargo / função",
            "email": "E-mail",
            "phone": "Telefone",
            "teams": "Teams",
            "notes": "Observações",
            "active": "Contato ativo",
        }
        widgets = {
            "company": forms.TextInput(attrs={"placeholder": "Claro, ARION..."}),
            "region": forms.TextInput(attrs={"placeholder": "Norte, SP..."}),
            "client": forms.TextInput(attrs={"placeholder": "Cliente ou operadora"}),
            "project": forms.TextInput(attrs={"placeholder": "Projeto, quando aplicável"}),
            "email": forms.EmailInput(attrs={"autocomplete": "email"}),
            "phone": forms.TextInput(attrs={"autocomplete": "tel"}),
            "teams": forms.TextInput(attrs={"placeholder": "Usuário ou canal Teams"}),
            "notes": forms.Textarea(attrs={"rows": 3, "maxlength": 4000, "placeholder": "Contexto operacional para o escalonamento"}),
            "active": forms.CheckboxInput(),
        }

    def clean_company(self):
        company = str(self.cleaned_data.get("company") or "").strip()
        if company.isdigit():
            from aplicativos.integracoes.services.semantics import UNCLASSIFIED, provider_names_for_values

            resolved = provider_names_for_values([company]).get(company, UNCLASSIFIED)
            if resolved == UNCLASSIFIED:
                raise forms.ValidationError("Informe o nome operacional do provedor, não um ID numérico.")
            return resolved
        return company

    def clean_escalation_level(self):
        return int(self.cleaned_data["escalation_level"])
