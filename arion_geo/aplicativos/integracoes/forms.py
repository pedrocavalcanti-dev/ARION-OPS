from django import forms
from django.conf import settings
from urllib.parse import urlsplit


class ArionDatabaseCandidateForm(forms.Form):
    host = forms.CharField(label="Host", max_length=255, initial="127.0.0.1")
    port = forms.IntegerField(label="Porta", min_value=1, max_value=65535, initial=3306)
    database = forms.CharField(label="Schema", max_length=128, initial="arion_geo")
    user = forms.CharField(label="Usuário", max_length=255)
    timeout = forms.IntegerField(label="Timeout (segundos)", min_value=1, max_value=60, initial=10)

    def clean(self):
        cleaned = super().clean()
        for field in ("host", "database", "user"):
            value = cleaned.get(field)
            if value is not None:
                cleaned[field] = value.strip()
                if not cleaned[field]:
                    self.add_error(field, "Este campo é obrigatório para MySQL.")
        return cleaned


class CMDBConfigurationForm(forms.Form):
    base_url = forms.URLField(label="URL base", max_length=500, widget=forms.URLInput(attrs={"placeholder": "https://servidor/otrs/nph-genericinterface.pl/Webservice/"}))
    username = forms.CharField(label="Usuário", max_length=255, required=False)
    password = forms.CharField(label="Senha", max_length=512, required=False, strip=False, widget=forms.PasswordInput(render_value=False))
    timeout = forms.IntegerField(label="Timeout (segundos)", min_value=1, max_value=60, initial=15)
    verify_tls = forms.BooleanField(label="Verificar certificado TLS", required=False, initial=True)
    batch_size = forms.IntegerField(label="Itens por lote", min_value=1, max_value=100, initial=50)

    def clean_base_url(self):
        value = self.cleaned_data["base_url"].strip().rstrip("/")
        if "@" in value.split("://", 1)[-1].split("/", 1)[0]:
            raise forms.ValidationError("Não inclua usuário ou senha na URL.")
        parsed = urlsplit(value)
        if parsed.scheme.casefold() != "https" or not parsed.hostname:
            raise forms.ValidationError("A URL base da CMDB deve utilizar HTTPS.")
        if parsed.query or parsed.fragment:
            raise forms.ValidationError("A URL base não pode conter parâmetros ou fragmento.")
        return value


class OTRSConfigurationForm(forms.Form):
    LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

    host = forms.CharField(label="Host / IP", max_length=255)
    port = forms.IntegerField(label="Porta", min_value=1, max_value=65535, initial=3306)
    database = forms.CharField(label="Banco", max_length=128, required=False)
    username = forms.CharField(label="Usuário", max_length=255, required=False)
    password = forms.CharField(label="Senha", max_length=512, required=False, strip=False, widget=forms.PasswordInput(render_value=False))
    timeout = forms.IntegerField(label="Timeout (segundos)", min_value=1, max_value=60, initial=10)
    use_tls = forms.BooleanField(label="Exigir TLS (obrigatório para host remoto)", required=False, initial=True)
    ssl_ca = forms.CharField(label="Certificado CA TLS (opcional)", max_length=500, required=False)
    allow_insecure_remote = forms.BooleanField(
        label="Permitir conexão legada sem TLS",
        required=False,
        initial=False,
        help_text="Compatibilidade temporária apenas para o host autorizado por ARION_OTRS_LEGACY_NO_TLS_HOST. O tráfego remoto não será criptografado.",
    )

    def clean_host(self):
        return self.cleaned_data["host"].strip()

    def clean_database(self):
        return self.cleaned_data["database"].strip()

    def clean_ssl_ca(self):
        return (self.cleaned_data.get("ssl_ca") or "").strip()

    def clean(self):
        cleaned = super().clean()
        host = (cleaned.get("host") or "").strip().casefold()
        use_tls = bool(cleaned.get("use_tls"))
        allow_insecure_remote = bool(cleaned.get("allow_insecure_remote"))
        legacy_no_tls_host = str(getattr(settings, "OTRS_LEGACY_NO_TLS_HOST", "") or "").strip().casefold()
        if host and host not in self.LOOPBACK_HOSTS:
            if not use_tls and not allow_insecure_remote:
                self.add_error("use_tls", "Host remoto do OTRS exige transporte TLS habilitado ou túnel local em 127.0.0.1.")
            elif not use_tls and (not legacy_no_tls_host or host != legacy_no_tls_host):
                self.add_error("allow_insecure_remote", "A conexão remota sem TLS só pode usar o host legado autorizado na configuração do servidor.")
        if allow_insecure_remote and legacy_no_tls_host and host != legacy_no_tls_host:
            self.add_error("allow_insecure_remote", "A exceção sem TLS não está autorizada para este host.")
        return cleaned


class BrevoConfigurationForm(forms.Form):
    send_method = forms.ChoiceField(label="Método de envio", choices=(("smtp", "SMTP Relay"), ("api_rest", "API REST")), initial="smtp")
    api_key = forms.CharField(
        label="API Key", max_length=512, required=False, strip=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Vazio mantém a chave protegida atual. A chave nunca será exibida novamente.",
    )
    smtp_login = forms.CharField(label="Login SMTP", max_length=254, required=False)
    smtp_key = forms.CharField(
        label="Chave SMTP", max_length=512, required=False, strip=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Vazio mantém a chave SMTP protegida atual. Cadastre uma chave SMTP nova e exclusiva para o ARION OPS.",
    )
    sender_name = forms.CharField(label="Nome do remetente", max_length=100, required=False)
    sender_email = forms.EmailField(label="E-mail do remetente", max_length=254, required=False)
    quality_recipient = forms.EmailField(label="Destinatário padrão da Qualidade", max_length=254, required=False)
    routing_test_recipient = forms.EmailField(
        label="Destinatário autorizado para homologação", max_length=254, required=False,
        help_text="Use uma caixa controlada e autorizada. O teste envia somente para este endereço, sem Cc.",
    )
    routing_operational_test_approved = forms.BooleanField(
        label="Autorizar previamente uma homologação para o destinatário operacional", required=False,
        help_text="Esta autorização vale somente para a ação individual de homologação; não habilita o envio de TQs.",
    )
    quality_cc = forms.EmailField(label="E-mail em cópia (Cc)", max_length=254, required=False)
    reply_to = forms.EmailField(label="E-mail de resposta (opcional)", max_length=254, required=False)
    quality_recipient_validated = forms.BooleanField(
        label="Endereço de entrada da fila Qualidade validado no OTRS", required=False,
        help_text="Antes do primeiro envio, use uma caixa de testes autorizada. Marque após confirmar o endereço que realmente cria tickets na fila Qualidade.",
    )
    enabled = forms.BooleanField(label="Habilitada", required=False)
    allow_real_sends = forms.BooleanField(
        label="Autorizar envio real de solicitações de TQ", required=False,
        help_text="Habilita o botão de envio explícito na Qualidade. Cada mensagem ainda exige confirmação individual.",
    )

    def clean_api_key(self):
        return self.cleaned_data["api_key"].strip()

    def clean_smtp_key(self):
        return self.cleaned_data["smtp_key"]

    def clean_sender_name(self):
        return " ".join(self.cleaned_data.get("sender_name", "").split())

    def clean(self):
        cleaned = super().clean()
        for field in ("sender_name", "sender_email", "quality_recipient", "routing_test_recipient", "quality_cc", "reply_to"):
            value = cleaned.get(field)
            if value:
                cleaned[field] = value.strip()
        if cleaned.get("enabled"):
            for field, label in (
                ("sender_name", "Nome do remetente"),
                ("sender_email", "E-mail do remetente"),
                ("quality_recipient", "Destinatário padrão da Qualidade"),
            ):
                if not cleaned.get(field):
                    self.add_error(field, f"Informe {label.lower()} antes de habilitar a integração.")
        if cleaned.get("allow_real_sends") and not cleaned.get("enabled"):
            self.add_error("allow_real_sends", "Habilite a integração Brevo antes de autorizar envios reais.")
        if cleaned.get("allow_real_sends") and not cleaned.get("quality_recipient_validated"):
            self.add_error("quality_recipient_validated", "Valide o endereço de entrada da fila Qualidade antes de autorizar envios reais.")
        if cleaned.get("send_method") == "smtp":
            if not cleaned.get("smtp_login"):
                self.add_error("smtp_login", "Informe o login SMTP.")
            if cleaned.get("enabled") and not (cleaned.get("smtp_key") or self.initial.get("smtp_credential_configured")):
                self.add_error("smtp_key", "Cadastre uma chave SMTP antes de habilitar o método SMTP.")
        elif cleaned.get("enabled") and not (cleaned.get("api_key") or self.initial.get("api_credential_configured")):
            self.add_error("api_key", "Cadastre a API Key antes de habilitar o método API REST.")
        if cleaned.get("smtp_login"):
            cleaned["smtp_login"] = cleaned["smtp_login"].strip()
        return cleaned


class MapboxConfigurationForm(forms.Form):
    public_token = forms.CharField(
        label="Token público Mapbox", max_length=512, required=False, strip=True,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Aceita somente token público pk. Vazio mantém o token cadastrado.",
    )
    enabled = forms.BooleanField(label="Disponibilizar para telas autorizadas do mapa", required=False)

    def clean_public_token(self):
        value = self.cleaned_data["public_token"]
        if value and (not value.startswith("pk.") or len(value) <= 10 or any(character.isspace() for character in value)):
            raise forms.ValidationError("Informe um token público Mapbox iniciado por pk.; tokens privados sk. não são aceitos.")
        return value


class GeographyConfigurationForm(forms.Form):
    automatic = forms.BooleanField(label="Atualizar catálogo periodicamente", required=False)
    enabled = forms.BooleanField(label="Habilitar integração", required=False)
    interval_hours = forms.IntegerField(label="Atualização do catálogo (horas)", min_value=24, max_value=8760, initial=720)
    timeout = forms.IntegerField(label="Timeout (segundos)", min_value=2, max_value=30, initial=15)


class WeatherConfigurationForm(forms.Form):
    enabled = forms.BooleanField(label="Disponibilizar meteorologia no GEO", required=False, initial=True)
    mode = forms.ChoiceField(label="Fonte meteorológica", choices=(("public","Pública — Open-Meteo"),("commercial","Comercial — Open-Meteo"),("self_hosted","Self-hosted — instância própria")))
    endpoint = forms.URLField(label="Endpoint HTTPS", max_length=500, initial="https://archive-api.open-meteo.com/v1/archive", help_text="O destino deve responder ao contrato histórico Open-Meteo. TLS permanece obrigatório.")
    api_key = forms.CharField(label="Chave da fonte (quando necessária)", max_length=512, required=False, strip=True, widget=forms.PasswordInput(render_value=False, attrs={"autocomplete":"new-password"}), help_text="Pública não exige chave. Vazio mantém a credencial protegida do destino atual; nenhuma chave é enviada ao endpoint público.")
    automatic = forms.BooleanField(label="Agendar atualização do último mês completo", required=False)
    scheduled_ufs = forms.MultipleChoiceField(label="UFs do agendamento", required=False, widget=forms.CheckboxSelectMultiple, help_text="Nenhuma UF é agendada automaticamente. Escolha explicitamente os recortes desejados.")
    interval_hours = forms.IntegerField(label="Intervalo entre atualizações (horas)", min_value=24, max_value=8760, initial=168)
    timeout = forms.IntegerField(label="Timeout (segundos)", min_value=2, max_value=30, initial=15)
    retries = forms.IntegerField(label="Tentativas adicionais", min_value=0, max_value=2, initial=1)
    cache_hours = forms.IntegerField(label="Validade do cache (horas)", min_value=24, max_value=8760, initial=720)
    daily_budget = forms.IntegerField(label="Limite diário de requisições HTTP", min_value=1, max_value=1000, initial=250)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from .services.geography import STATES
        from .services.geo_operations import allowed_weather_ufs
        allowed = set(allowed_weather_ufs(user)) if user is not None else {s["sigla"] for s in STATES}
        self.fields["scheduled_ufs"].choices = [(s["sigla"],s["sigla"]) for s in sorted(STATES,key=lambda s:s["sigla"]) if s["sigla"] in allowed]

    def clean(self):
        from .clients.public_data import PublicDataError, validate_weather_endpoint
        cleaned = super().clean()
        mode, endpoint = cleaned.get("mode"), cleaned.get("endpoint")
        if mode == "public":
            cleaned["api_key"] = ""
        if mode and endpoint:
            try:
                cleaned["endpoint"] = validate_weather_endpoint(mode, endpoint)
            except PublicDataError as error:
                self.add_error("endpoint", str(error))
        stored_for_endpoint = self.initial.get("credential_configured") and self.initial.get("credential_endpoint")==endpoint
        if cleaned.get("enabled") and mode=="commercial" and not (cleaned.get("api_key") or stored_for_endpoint):
            self.add_error("api_key", "Cadastre uma chave para o endpoint comercial selecionado.")
        if cleaned.get("automatic") and not cleaned.get("scheduled_ufs"):
            self.add_error("scheduled_ufs", "Selecione pelo menos uma UF para o agendamento.")
        return cleaned


class WeatherCollectionForm(forms.Form):
    uf = forms.ChoiceField(label="UF")
    period = forms.CharField(label="Mês da coleta", widget=forms.DateInput(attrs={"type":"month"}))

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        from .services.geo_operations import allowed_weather_ufs
        self.fields["uf"].choices = [(uf,uf) for uf in sorted(allowed_weather_ufs(user))]

    def clean_period(self):
        from .services.weather import month_bounds
        try:
            return month_bounds(self.cleaned_data["period"])[0].strftime("%Y-%m")
        except ValueError as error:
            raise forms.ValidationError(str(error)) from None


class CMDBSearchForm(forms.Form):
    class_name = forms.CharField(label="Classe", max_length=120, required=False, widget=forms.TextInput(attrs={"list": "cmdb-class-options", "placeholder": "Selecione ou informe a classe exata"}))
    number = forms.CharField(label="Número", max_length=120, required=False)
    name = forms.CharField(label="Nome", max_length=180, required=False)
    cixml_data = forms.CharField(label="Filtro CIXMLData", max_length=180, required=False)
    limit = forms.IntegerField(label="Limite", min_value=1, max_value=100, initial=25)


class CMDBGetForm(forms.Form):
    config_item_ids = forms.CharField(label="ConfigItemID", max_length=5000, widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Informe um ou mais IDs separados por vírgula ou espaço"}))

    def clean_config_item_ids(self):
        values = self.cleaned_data["config_item_ids"].replace("\n", " ").replace(",", " ").split()
        unique = list(dict.fromkeys(values))
        if not unique or len(unique) > 500:
            raise forms.ValidationError("Informe de 1 a 500 ConfigItemID.")
        return unique
