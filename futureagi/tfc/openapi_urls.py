from django.urls import include, path

from tfc.ee_gating import is_oss
from tfc.ee_loader import has_ee
from tfc.urls import urlpatterns as runtime_urlpatterns

urlpatterns = list(runtime_urlpatterns)

if has_ee("ee.cloud.control_plane"):
    urlpatterns.append(path("", include("ee.cloud.control_plane.urls")))

if has_ee("ee.cloud"):
    urlpatterns += [
        path("usage/", include("ee.cloud.urls")),
        path("telemetry/", include("ee.cloud.telemetry.urls")),
    ]

# Cloud-only routes a self-hosted runtime does not mount.
if is_oss():
    from accounts.urls import marketplace_urls

    urlpatterns.append(path("accounts/", include(marketplace_urls)))
