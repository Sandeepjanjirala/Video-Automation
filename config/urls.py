"""URL configuration for Telugu Reel Studio (Stage M7)."""
from django.urls import path, re_path, include
from core.views import serve_ui, serve_static_asset

urlpatterns = [
    path('api/', include('core.urls')),
    path('', serve_ui, name='ui_root'),
    path('index.html', serve_ui, name='ui_index'),
    re_path(r'^(?P<filename>[^/]+\.(js|css|ico|png|svg|json|map))$', serve_static_asset, name='ui_static'),
]

