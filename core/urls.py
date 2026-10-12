"""URL configuration for core app."""
from django.urls import path
from .views import HealthCheckView, SchemaStatusView

urlpatterns = [
    path('health', HealthCheckView.as_view(), name='health'),
    path('migration/schema-status', SchemaStatusView.as_view(), name='schema_status'),
]
