from tfc.ee_loader import has_ee

from .test import *  # noqa: F403

if has_ee("ee.cloud.control_plane") and not any(  # noqa: F405
    app == "ee.cloud.control_plane" or app.endswith(".CloudControlPlaneConfig")
    for app in INSTALLED_APPS  # noqa: F405
):
    INSTALLED_APPS.append(  # noqa: F405
        "ee.cloud.control_plane.apps.CloudControlPlaneConfig"
    )

ROOT_URLCONF = "tfc.openapi_urls"

# Schema-only: always advertise optional F44 metadata, independent of deploy env.
# Runtime settings retain their default-OFF gate; this profile is for generation.
DSX_DATASET_LIST_COLUMNS_ENABLED = True
