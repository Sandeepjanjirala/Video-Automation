"""URL configuration for Telugu Reel Studio (Stage M9)."""
from django.urls import path, re_path, include
from core.views import serve_m8_ui, serve_static_asset, serve_static_path

urlpatterns = [
    path('api/', include('core.urls')),
    re_path(r'^static/(?P<path>.*)$', serve_static_path, name='ui_static_dir'),
    path('', serve_m8_ui, name='ui_root'),
    path('index.html', serve_m8_ui, name='ui_index'),
    path('m8/', serve_m8_ui, name='ui_m8'),
    re_path(r'^(?P<filename>[^/]+\.(js|css|ico|png|svg|json|map))$', serve_static_asset, name='ui_static'),
]

