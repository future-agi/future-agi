from .test import *  # noqa: F403

from tfc.ee_loader import has_ee

if has_ee("ee.cloud.control_plane") and not any(  # noqa: F405
    app == "ee.cloud.control_plane"
    or app.endswith(".CloudControlPlaneConfig")
    for app in INSTALLED_APPS  # noqa: F405
):
    INSTALLED_APPS.append(  # noqa: F405
        "ee.cloud.control_plane.apps.CloudControlPlaneConfig"
    )

ROOT_URLCONF = "tfc.openapi_urls"
