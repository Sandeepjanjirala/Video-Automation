"""URL configuration for core app (Stage M3)."""
from django.urls import path
from .views import (
    HealthCheckView,
    SchemaStatusView,
    BootstrapView,
    ProjectListView,
    ProjectDetailView,
    ProjectHistoryView,
    ProjectDraftView,
    ProjectPreflightView,
    ProjectJobsView,
    ProjectTakesView,
    VoiceCapabilitiesView,
    AssetListView,
    AssetDetailView,
)

urlpatterns = [
    # Diagnostics & Health
    path('health', HealthCheckView.as_view(), name='health'),
    path('migration/schema-status', SchemaStatusView.as_view(), name='schema_status'),

    # Handshake
    path('bootstrap', BootstrapView.as_view(), name='bootstrap'),

    # Projects & Revisions
    path('projects', ProjectListView.as_view(), name='project_list'),
    path('projects/<str:pid>', ProjectDetailView.as_view(), name='project_detail'),
    path('projects/<str:pid>/history', ProjectHistoryView.as_view(), name='project_history'),
    path('projects/<str:pid>/draft', ProjectDraftView.as_view(), name='project_draft'),
    path('projects/<str:pid>/preflight', ProjectPreflightView.as_view(), name='project_preflight'),

    # Jobs & Voice Takes
    path('projects/<str:pid>/jobs', ProjectJobsView.as_view(), name='project_jobs'),
    path('projects/<str:pid>/takes', ProjectTakesView.as_view(), name='project_takes'),

    # Voice Lab
    path('voice/capabilities', VoiceCapabilitiesView.as_view(), name='voice_capabilities'),

    # Media Assets
    path('assets', AssetListView.as_view(), name='asset_list'),
    path('assets/<str:aid>', AssetDetailView.as_view(), name='asset_detail'),
]
