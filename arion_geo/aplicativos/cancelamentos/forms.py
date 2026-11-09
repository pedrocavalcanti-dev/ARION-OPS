from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from aplicativos.integracoes.models import CMDBLinkMirror

from .models import (CancellationProcess, ISPTreatment, CancellationPending, CancellationEquipment,
                     FinancialObligation, CancellationEvidence, FinancialHandoff, IntakeSettings)


class OperationalForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


class TriageForm(OperationalForm):
    reason = forms.CharField(label="Justificativa / observação", max_length=3000, required=False,
                             widget=forms.Textarea(attrs={"rows": 3}))
    link = forms.ModelChoiceField(label="Circuito principal validado", queryset=CMDBLinkMirror.objects.none(), required=False,
                                  empty_label="Sem vínculo confiável — manter pendente de associação")

    def __init__(self, *args, user, intake, ticket=None, **kwargs):
        super().__init__(*args, **kwargs)
        from aplicativos.integracoes.services.analytics import LinkQueryService
        ticket_id = intake.ticket_id if intake else ticket.pk if ticket else None
        candidates = LinkQueryService.base(user).filter(ticket_relations__ticket_id=ticket_id,
                                                        ticket_relations__is_present_in_source=True).distinct() if ticket_id else CMDBLinkMirror.objects.none()
        self.fields["link"].queryset = candidates
        self.fields["link"].label_from_instance = lambda link: f"{link.designation or link.name} · CI {link.source_config_item_id}"


def assignable_users():
    return get_user_model().objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(groups__permissions__content_type__app_label="cancelamentos",
                                groups__permissions__codename="view_cancellations")
        | Q(user_permissions__content_type__app_label="cancelamentos", user_permissions__codename="view_cancellations")
    ).distinct().order_by("first_name", "username")


class AssignmentForm(OperationalForm):
    owner = forms.ModelChoiceField(label="Responsável", queryset=get_user_model().objects.none(), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owner"].queryset = assignable_users()


class NoteForm(OperationalForm):
    note = forms.CharField(label="Nova observação", max_length=5000, widget=forms.Textarea(attrs={"rows": 3}))


class ProcessForm(forms.ModelForm):
    class Meta:
        model = CancellationProcess
        fields = ("final_client", "next_action_at")
        widgets = {"next_action_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")}


class TreatmentForm(forms.ModelForm):
    class Meta:
        model = ISPTreatment
        fields = ("protocol", "contact", "requested_at", "response", "service_confirmed", "confirmed_at",
                  "effective_date", "billing_closed", "billing_closed_date", "evidence_reference")
        widgets = {"response": forms.Textarea(attrs={"rows": 3}),
                   **{f: forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M") for f in ("requested_at", "confirmed_at")},
                   **{f: forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d") for f in ("effective_date", "billing_closed_date")}}

    def clean(self):
        data = super().clean()
        if data.get("service_confirmed") and not all(data.get(k) for k in ("effective_date", "confirmed_at", "evidence_reference")):
            raise forms.ValidationError("Informe data efetiva, data da confirmação e evidência do ISP.")
        if data.get("billing_closed") and not data.get("billing_closed_date"):
            self.add_error("billing_closed_date", "Informe a data do encerramento do faturamento.")
        if data.get("confirmed_at") and data["confirmed_at"] > timezone.now():
            self.add_error("confirmed_at", "A confirmação não pode estar no futuro.")
        return data


class PendingForm(forms.ModelForm):
    class Meta:
        model = CancellationPending
        fields = ("kind", "description", "amount", "expected_date", "owner", "evidence_reference")
        widgets = {"description": forms.Textarea(attrs={"rows": 3}),
                   "expected_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def __init__(self, *args, financial=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owner"].queryset = assignable_users()
        if not financial:
            self.fields.pop("amount")
            self.fields["kind"].choices = [(k, v) for k, v in self.fields["kind"].choices if k not in FINANCIAL_PENDING_KINDS]


FINANCIAL_PENDING_KINDS = ("OVERDUE", "EQUIPMENT_FINE", "CONTRACT_FINE", "BILL")


class ResolvePendingForm(OperationalForm):
    status = forms.ChoiceField(label="Situação", choices=CancellationPending.Status.choices[1:])
    resolution = forms.CharField(label="Providência / justificativa", max_length=3000, widget=forms.Textarea(attrs={"rows": 3}))
    evidence_reference = forms.CharField(label="Evidência / referência do encaminhamento ou resolução", max_length=1000)


class EquipmentForm(forms.ModelForm):
    class Meta:
        model = CancellationEquipment
        fields = ("description", "status", "scheduled_at", "returned_at", "evidence_reference", "notes")
        widgets = {"notes": forms.Textarea(attrs={"rows": 3}),
                   **{f: forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M") for f in ("scheduled_at", "returned_at")}}

    def clean(self):
        data = super().clean()
        if data.get("status") == "SCHEDULED" and not data.get("scheduled_at"):
            self.add_error("scheduled_at", "Informe o agendamento.")
        if data.get("status") in ("RETURNED", "WAIVED") and not data.get("evidence_reference"):
            self.add_error("evidence_reference", "Informe evidência da devolução ou dispensa pelo ISP.")
        if data.get("status") == "RETURNED" and not data.get("returned_at"):
            self.add_error("returned_at", "Informe a data da devolução.")
        return data


class ObligationForm(forms.ModelForm):
    class Meta:
        model = FinancialObligation
        fields = ("kind", "description", "estimated_amount", "charged_amount", "due_date", "reference_number", "document_status", "document_reference")
        widgets = {"due_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def clean(self):
        data = super().clean()
        if data.get("document_status") == "AVAILABLE" and not data.get("document_reference"):
            self.add_error("document_reference", "Informe onde consultar o documento.")
        return data


class EvidenceForm(forms.ModelForm):
    class Meta:
        model = CancellationEvidence
        fields = ("label", "reference", "financial")


class HandoffForm(forms.ModelForm):
    class Meta:
        model = FinancialHandoff
        fields = ("sent_at", "recipient", "reference", "evidence_reference")
        widgets = {"sent_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")}

    def clean_sent_at(self):
        value = self.cleaned_data["sent_at"]
        if value > timezone.now():
            raise forms.ValidationError("Registre um envio já realizado; agendamento não é encaminhamento.")
        return value


class CompletionForm(OperationalForm):
    exception_reason = forms.CharField(label="Justificativa de exceção (somente com permissão específica)",
                                       max_length=3000, required=False, widget=forms.Textarea(attrs={"rows": 3}))
    exception_evidence = forms.CharField(label="Evidência da exceção", max_length=1000, required=False)


class SettingsForm(forms.ModelForm):
    class Meta:
        model = IntakeSettings
        fields = ("enabled", "max_age_minutes")

    def clean_max_age_minutes(self):
        value = self.cleaned_data["max_age_minutes"]
        if not 2 <= value <= 1440:
            raise forms.ValidationError("Use entre 2 e 1440 minutos.")
        return value


class QueryForm(forms.Form):
    tab = forms.ChoiceField(required=False, choices=(("intake", "Entrada OTRS"), ("order", "Pedido"),
                                                   ("request", "Solicitação"), ("pending", "Pendências"), ("completed", "Concluídos")))
    q = forms.CharField(required=False, max_length=200, label="Buscar", widget=forms.TextInput(attrs={"placeholder": "Ticket, pedido ou circuito…", "type": "search"}))
    context = forms.CharField(required=False, max_length=300, label="Cliente / Projeto", widget=forms.TextInput(attrs={"placeholder": "Cliente ou projeto"}))
    client = forms.CharField(required=False, max_length=300, label="Cliente")
    project = forms.CharField(required=False, max_length=300, label="Projeto")
    isp = forms.CharField(required=False, max_length=300, label="ISP")
    owner = forms.ChoiceField(required=False, label="Responsável")
    situation = forms.ChoiceField(required=False, label="Situação da entrada", choices=(("", "Aguardando triagem"), ("all", "Todas as decisões"),
                             ("WAITING", "Aguardando triagem"), ("APPROVED", "Aprovados"), ("REJECTED", "Rejeitados")))
    presence = forms.ChoiceField(required=False, label="Presença no KPI 1", choices=(("", "Entradas atuais"), ("history", "Histórico — fora do KPI 1"), ("all", "Atuais e históricas")))
    stage = forms.ChoiceField(required=False, label="Etapa principal", choices=(("", "Todas"), *CancellationProcess.Stage.choices))
    pending_kind = forms.ChoiceField(required=False, label="Tipo de pendência", choices=(("", "Qualquer pendência"), *CancellationPending.Kind.choices))
    sent = forms.BooleanField(required=False, label="Encaminhados ao Financeiro")
    start = forms.DateField(required=False, label="Desde", widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(required=False, label="Até", widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, owners=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owner"].choices = (("", "Todos"), ("me", "Meus registros"), ("none", "Sem responsável"),
                                          *((str(pk), name) for pk, name in owners))

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end") and data["start"] > data["end"]:
            raise forms.ValidationError("O início do período deve ser anterior ao fim.")
        return data
