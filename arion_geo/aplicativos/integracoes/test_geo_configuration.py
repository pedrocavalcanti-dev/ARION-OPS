from datetime import date
from io import StringIO
import socket
import ssl
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from aplicativos.base_links.models import Projeto, Operadora
from aplicativos.configuracoes.models import GroupDataScope
from .clients.public_data import ARCHIVE_ENDPOINTS, PublicDataClient, PublicDataError, WeatherHTTPSConnection, validate_weather_endpoint
from .forms import WeatherConfigurationForm
from .models import CMDBLinkMirror, ExternalAPIIntegration, WeatherCollectionJob, WeatherMonth
from .services.geo_configuration import bootstrap_geo_integrations, get_geo_integration, weather_configuration, weather_source, WEATHER_DEFAULTS
from .services.geo_operations import worker_state
from .services.geo_scheduler import scheduler_tick
from .services.weather import collection_readiness, configured_weather, cached_weather, collect_month, request_collection, worker_tick, weather_secret


class GeoConfigurationTests(TestCase):
    def form_data(self, **values):
        return {**WEATHER_DEFAULTS, "enabled":"on", **values}

    def test_post_migrate_bootstrap_is_idempotent_and_has_no_network_or_jobs(self):
        with patch("aplicativos.integracoes.clients.public_data.PublicDataClient.get") as network:
            bootstrap_geo_integrations(sender=apps.get_app_config("integracoes"))
            first=get_geo_integration("open_meteo")
            bootstrap_geo_integrations()
            self.assertEqual(get_geo_integration("open_meteo").pk,first.pk)
            self.assertTrue(first.enabled)
            self.assertEqual(first.config["mode"],"public")
            self.assertFalse(first.config["automatic"])
            self.assertEqual(first.config["scheduled_ufs"],[])
            self.assertEqual(ExternalAPIIntegration.objects.filter(provider__in=("ibge","open_meteo")).count(),2)
            self.assertFalse(WeatherCollectionJob.objects.exists())
            network.assert_not_called()

    def test_bootstrap_preserves_disabled_commercial_credentials_and_configuration(self):
        row=get_geo_integration("open_meteo")
        row.enabled=False;row.secret_ciphertext="protected-existing";row.config={"mode":"commercial","timeout":27}
        row.save()
        bootstrap_geo_integrations();row.refresh_from_db()
        self.assertFalse(row.enabled)
        self.assertEqual(row.secret_ciphertext,"protected-existing")
        self.assertEqual(row.config,{"mode":"commercial","timeout":27})

    def test_public_source_needs_no_key_or_environment_confirmation(self):
        row=get_geo_integration("open_meteo")
        for production in (False,True):
            with self.subTest(production=production),override_settings(ARION_PRODUCTION=production):
                self.assertEqual(collection_readiness(row)["status"],"ready")
                self.assertEqual(configured_weather(row)["mode"],"public")
                form=WeatherConfigurationForm(self.form_data())
                self.assertTrue(form.is_valid(),form.errors)
                self.assertNotIn("license_confirmed",form.fields)
                self.assertNotIn("evaluation_authorized",form.fields)
                self.assertEqual(form.cleaned_data["api_key"],"")

    def test_commercial_source_requires_actual_key_but_no_confirmation_checkbox(self):
        data=self.form_data(mode="commercial",endpoint=ARCHIVE_ENDPOINTS["commercial"])
        self.assertFalse(WeatherConfigurationForm(data).is_valid())
        data["api_key"]="test-key"
        form=WeatherConfigurationForm(data)
        self.assertTrue(form.is_valid(),form.errors)
        existing=WeatherConfigurationForm({**data,"api_key":""},initial={"credential_configured":True,"credential_endpoint":ARCHIVE_ENDPOINTS["commercial"]})
        self.assertTrue(existing.is_valid(),existing.errors)

    def test_schedule_requires_explicit_ufs_and_never_queues_nationally_on_bootstrap(self):
        with patch("aplicativos.integracoes.services.geo_scheduler.request_collection") as queue,patch("aplicativos.integracoes.services.geo_scheduler.worker_tick"):
            scheduler_tick();queue.assert_not_called()
            row=get_geo_integration("open_meteo");row.config={**row.config,"automatic":True,"scheduled_ufs":["SP"]};row.save()
            scheduler_tick()
            self.assertEqual(queue.call_args.args[0],["SP"])
        form=WeatherConfigurationForm(self.form_data(automatic=True,scheduled_ufs=["SP"]))
        self.assertTrue(form.is_valid(),form.errors)

    def test_geo_only_worker_does_not_start_other_module_synchronizations(self):
        command="aplicativos.integracoes.management.commands.integration_worker.Command"
        observed=[]
        with patch(command+"._schedule_once") as otrs,patch(command+"._backlog_history") as backlog,patch(command+"._cancellation_intake") as cancellations,patch(command+"._geo_intelligence",side_effect=lambda:observed.append(worker_state()["active"])) as geo:
            call_command("integration_worker",once=True,geo_only=True,stdout=StringIO())
        otrs.assert_not_called();backlog.assert_not_called();cancellations.assert_not_called();geo.assert_called_once()
        self.assertEqual(observed,[True])
        self.assertFalse(worker_state()["active"])

    def test_self_hosted_endpoint_is_configurable_and_cache_identity_separates_origins(self):
        row=get_geo_integration("open_meteo")
        row.config={"mode":"self_hosted","endpoint":"https://weather.example.org/v1/archive"}
        first=weather_source(weather_configuration(row))
        row.config["endpoint"]="https://weather-other.example.org/v1/archive"
        self.assertNotEqual(first,weather_source(weather_configuration(row)))
        self.assertEqual(validate_weather_endpoint("self_hosted",row.config["endpoint"]),row.config["endpoint"])
        for endpoint in ("http://weather.example.org/v1/archive","https://user:password@weather.example.org/v1/archive","https://weather.example.org/v1/archive?apikey=secret","https://weather.example.org/v1/archive#fragment"):
            with self.subTest(endpoint=endpoint),self.assertRaises(PublicDataError):validate_weather_endpoint("self_hosted",endpoint)

    def test_self_hosted_transport_blocks_private_addresses_and_retains_tls_verification(self):
        connection=WeatherHTTPSConnection("weather.example.org",timeout=2)
        self.assertEqual(connection._context.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(connection._context.check_hostname)
        for ip in ("127.0.0.1","169.254.169.254","10.0.0.1","::1"):
            with self.subTest(ip=ip),patch("socket.getaddrinfo",return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,"",(ip,443))]),self.assertRaises(PublicDataError):
                connection.connect()

    @override_settings(GEO_WEATHER_ALLOWED_HOSTS=("weather.internal",))
    def test_explicit_internal_destination_is_pinned_without_disabling_tls(self):
        connection=WeatherHTTPSConnection("weather.internal",timeout=2)
        address=(socket.AF_INET,socket.SOCK_STREAM,6,"",("10.10.10.5",443))
        sock=Mock()
        with patch("socket.getaddrinfo",return_value=[address]) as resolve,patch("socket.socket",return_value=sock),patch("http.client.HTTPSConnection.connect",lambda obj:obj._create_connection((obj.host,obj.port),obj.timeout)):
            connection.connect()
        resolve.assert_called_once();sock.connect.assert_called_once_with(("10.10.10.5",443))
        self.assertTrue(connection._context.check_hostname)

    def test_http_429_honors_retry_after_without_immediate_repeat(self):
        opener=Mock();opener.open.side_effect=HTTPError(ARCHIVE_ENDPOINTS["public"],429,"Limited",{"Retry-After":"180"},None)
        with patch("aplicativos.integracoes.clients.public_data.build_opener",return_value=opener),patch("aplicativos.integracoes.clients.public_data.time.sleep") as sleep:
            with self.assertRaises(PublicDataError) as caught:PublicDataClient(retries=2).get(ARCHIVE_ENDPOINTS["public"])
        self.assertEqual(caught.exception.code,"rate_limited")
        self.assertEqual(caught.exception.retry_after,180)
        opener.open.assert_called_once();sleep.assert_not_called()

    def test_provider_retry_after_survives_worker_and_repeated_collection_requests(self):
        from django.utils import timezone
        from datetime import timedelta
        job=WeatherCollectionJob.objects.create(uf="SP",period=date(2026,10,1))
        failure=PublicDataError("Limite do fornecedor atingido (HTTP 429).",code="rate_limited",retry_after=172800)
        with patch("aplicativos.integracoes.services.weather.collect_month",side_effect=failure):worker_tick()
        job.refresh_from_db()
        self.assertEqual(job.status,"pending")
        self.assertGreater(job.next_attempt_at,timezone.now()+timedelta(hours=47))
        original=job.next_attempt_at
        request_collection(["SP"],"2026-10",None)
        job.refresh_from_db();self.assertEqual(job.next_attempt_at,original)

    def test_stored_credentials_never_follow_an_unrelated_or_public_destination(self):
        row=get_geo_integration("open_meteo");row.secret_ciphertext="protected-existing"
        with patch("aplicativos.integracoes.services.weather.decrypt_external_api_secret") as decrypt:
            self.assertEqual(weather_secret(row,{"mode":"public","endpoint":ARCHIVE_ENDPOINTS["public"]}),"")
            self.assertEqual(weather_secret(row,{"mode":"self_hosted","endpoint":"https://other.example.org/v1/archive","credential_endpoint":ARCHIVE_ENDPOINTS["commercial"]}),"")
        decrypt.assert_not_called()

    @override_settings(ARION_PRODUCTION=True)
    def test_legacy_public_cache_is_readable_in_production(self):
        from django.utils import timezone
        from datetime import timedelta
        WeatherMonth.objects.create(uf="SP",period=date(2026,10,1),source="open_meteo_evaluation",method="area-grid-v2",summary={"daily":[],"points":1},collected_at=timezone.now(),expires_at=timezone.now()+timedelta(days=1))
        self.assertIn("SP",cached_weather(["SP"],"2026-10"))
        self.assertEqual(request_collection(["SP"],"2026-10",None),[])
        client=Mock()
        row=collect_month("SP",date(2026,10,1),get_geo_integration("open_meteo"),client=client)
        self.assertEqual(row.source,"open_meteo_evaluation")
        client.get.assert_not_called()


class GeoCentralPermissionTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user("geo-config-scope",password="strong-password")
        group=Group.objects.create(name="Geo scoped")
        group.permissions.add(*Permission.objects.filter(content_type__app_label="configuracoes",codename__in=("view_integrations","manage_integrations","run_integrations","view_map")))
        self.user.groups.add(group)
        scope=GroupDataScope.objects.create(group=group,mode="custom")
        scope.allowed_projects.add(Projeto.objects.create(name="BRADESCO",normalized_name="bradesco"))
        scope.allowed_operators.add(Operadora.objects.create(name="Claro",normalized_name="claro"))
        CMDBLinkMirror.objects.create(source_config_item_id="geo-sp",state="SP",business_project="BRADESCO",contracting_client="Claro")
        CMDBLinkMirror.objects.create(source_config_item_id="geo-rj",state="RJ",business_project="FUST",contracting_client="Claro")
        self.client.force_login(self.user)

    def test_manual_collection_respects_backend_scope_and_enqueues_one_uf(self):
        url=reverse("configuracoes:integracoes:geo-sync",args=["open_meteo"])
        self.client.post(url,{"uf":"RJ","period":"2026-10"})
        self.assertFalse(WeatherCollectionJob.objects.exists())
        response=self.client.post(url,{"uf":"SP","period":"2026-10"})
        self.assertEqual(response.status_code,302)
        self.assertEqual(list(WeatherCollectionJob.objects.values_list("uf",flat=True)),["SP"])

    def test_configuration_and_collection_require_permissions(self):
        self.user.groups.clear()
        for name in ("geo-save","geo-sync","geo-test"):
            self.assertEqual(self.client.post(reverse("configuracoes:integracoes:"+name,args=["open_meteo"]),{}).status_code,403)

    def test_geo_posts_reject_missing_csrf(self):
        from django.test import Client
        client=Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        for name in ("geo-save","geo-sync","geo-test"):
            self.assertEqual(client.post(reverse("configuracoes:integracoes:"+name,args=["open_meteo"]),{}).status_code,403)
        self.assertFalse(WeatherCollectionJob.objects.exists())

    def test_geo_configuration_never_renders_secret(self):
        row=get_geo_integration("open_meteo");row.secret_ciphertext="private-do-not-render";row.save()
        response=self.client.get(reverse("configuracoes:integracoes:geo-detail",args=["open_meteo"]))
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,"private-do-not-render")
        self.assertContains(response,"Histórico de operações")
        self.assertContains(response,"UFs do agendamento")

    def test_public_save_preserves_commercial_key_and_endpoint_binding(self):
        row=get_geo_integration("open_meteo")
        row.config={"mode":"commercial"};row.secret_ciphertext="protected-existing";row.save()
        url=reverse("configuracoes:integracoes:geo-save",args=["open_meteo"])
        response=self.client.post(url,{**WEATHER_DEFAULTS,"enabled":"on","api_key":""})
        self.assertEqual(response.status_code,302)
        row.refresh_from_db()
        self.assertEqual(row.secret_ciphertext,"protected-existing")
        self.assertEqual(row.config["credential_endpoint"],ARCHIVE_ENDPOINTS["commercial"])
        response=self.client.post(url,{**WEATHER_DEFAULTS,"mode":"commercial","endpoint":ARCHIVE_ENDPOINTS["commercial"],"enabled":"on","api_key":""})
        self.assertEqual(response.status_code,302)
        row.refresh_from_db();self.assertEqual(collection_readiness(row)["status"],"ready")

    def test_repeated_test_during_cooldown_preserves_last_success_and_health(self):
        from django.utils import timezone
        row=get_geo_integration("open_meteo");row.health="available";row.last_test_at=timezone.now();row.save()
        original=row.last_test_at
        with patch("aplicativos.integracoes.geo_views.test_weather") as network:
            response=self.client.post(reverse("configuracoes:integracoes:geo-test",args=["open_meteo"]))
        self.assertEqual(response.status_code,302)
        row.refresh_from_db();self.assertEqual(row.health,"available");self.assertEqual(row.last_test_at,original)
        network.assert_not_called()
