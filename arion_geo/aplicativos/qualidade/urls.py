from django.urls import path

from . import views

app_name = "qualidade"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("links-prioritarios/", views.priority_links, name="links_prioritarios"),
    path("links-prioritarios/<str:identificador>/", views.priority_link_detail, name="priority_link_detail"),
    path("links/opcoes/", views.link_options, name="link_options"),
    path("ordens/", views.orders, name="ordens"),
    path("ordens/nova/", views.order_create, name="ordem_create"),
    path("ordens/<str:codigo>/atualizar/", views.order_update, name="ordem_update"),
    path("ordens/<str:codigo>/arquivar/", views.quality_order_archive, name="ordem_arquivar"),
    path("ordens/<str:codigo>/ocorrencias/<int:ticket_external_id>/revisar/", views.occurrence_review, name="ocorrencia_revisar"),
    path("ordens/<str:codigo>/tratativas/vincular/", views.quality_ticket_link, name="tq_vincular"),
    path("ordens/<str:codigo>/tratativas/gerar/", views.quality_treatment_create, name="tq_gerar"),
    path("ordens/<str:codigo>/tratativas/<int:treatment_id>/resumo/", views.quality_treatment_summary_update, name="tq_atualizar_resumo"),
    path("ordens/<str:codigo>/tratativas/<int:treatment_id>/concluir/", views.quality_treatment_complete, name="tq_concluir"),
    path("ordens/<str:codigo>/tratativas/<int:treatment_id>/cancelar/", views.quality_treatment_cancel, name="tq_cancelar"),
    path("ordens/<str:codigo>/tratativas/<int:treatment_id>/pre-visualizar/", views.quality_treatment_email_preview, name="tq_email_preview"),
    path("ordens/<str:codigo>/tratativas/<int:treatment_id>/enviar/", views.quality_treatment_send, name="tq_email_send"),
    path("ordens/<str:codigo>/tratativas/<int:treatment_id>/reconciliar-envio/", views.quality_treatment_reconcile_send, name="tq_send_reconcile"),
    path("ordens/<str:codigo>/checklist/adicionar/", views.quality_action_add, name="checklist_adicionar"),
    path("ordens/<str:codigo>/checklist/<int:action_id>/", views.quality_action_toggle, name="checklist_toggle"),
    path("ordens/<str:codigo>/monitoramento/iniciar/", views.monitoring_cycle_start, name="monitoramento_iniciar"),
    path("ordens/<str:codigo>/monitoramento/<int:cycle_id>/resultado/", views.monitoring_cycle_finish, name="monitoramento_resultado"),
    path("ordens/<str:codigo>/", views.order_detail, name="ordem_detail"),
    path("monitoramento/", views.monitoring, name="monitoramento"),
    path("contatos/", views.contacts, name="contatos"),
    path("contatos/salvar/", views.contact_save, name="contato_salvar"),
    path("contatos/<int:contact_id>/status/", views.contact_toggle, name="contato_status"),
    path("historico/", views.history, name="historico"),
]
