from django.urls import path
from . import views

urlpatterns = [
    path("query/", views.analytics_query, name="analytics-query"),
    path("backlog-at/", views.analytics_backlog_at, name="analytics-backlog-at"),
    path("export/", views.analytics_export, name="analytics-export"),
    path("saved-views/", views.saved_views, name="analytics-saved-views"),
]
