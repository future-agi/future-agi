"""Deployment mode exposed by the public probe and operator status command."""

import os

from tfc.ee_loader import is_cloud_env


def get_deployment_mode() -> str:
    if is_cloud_env():
        return "cloud"
    if os.environ.get("EE_LICENSE_KEY", ""):
        return "ee"
    return "oss"
