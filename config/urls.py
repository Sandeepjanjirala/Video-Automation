"""URL configuration for Telugu Reel Studio (Phase M1)."""
from django.urls import path, include

urlpatterns = [
    path('api/', include('core.urls')),
]
