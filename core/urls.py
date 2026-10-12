"""URL configuration for core app (Stage M4)."""
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
    AssetListCreateView,
    AssetDetailView,
    AssetMediaView,
    AssetThumbnailView,
    StockSearchView,
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

    # Stock Provider Proxy (M4)
    path('stock', StockSearchView.as_view(), name='stock_search'),

    # Media Assets & Delivery (M4)
    path('assets', AssetListCreateView.as_view(), name='asset_list_create'),
    path('assets/<str:aid>', AssetDetailView.as_view(), name='asset_detail'),
    path('assets/<str:aid>/media', AssetMediaView.as_view(), name='asset_media'),
    path('assets/<str:aid>/file', AssetMediaView.as_view(), name='asset_file'),
    path('assets/<str:aid>/thumbnail', AssetThumbnailView.as_view(), name='asset_thumbnail'),
]

