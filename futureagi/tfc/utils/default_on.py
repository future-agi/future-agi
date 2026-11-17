"""Switches that are on unless a deployment turns them off.

A switch read here is on while its environment variable is unset or blank
and no settings override names it. Once the variable holds a value, or a
settings override (a test's override_settings or settings fixture) names the
switch, the parsed setting decides: only a value the settings read as True
keeps it on, so "false", "0", "off" or a typo turns it off.

The settings module parses each variable at start-up and stays the single
place that reads the environment's value; this module only decides whether
an unset variable means on.
"""

import os

from django.conf import UserSettingsHolder, settings


def _overridden(name: str) -> bool:
    holder = getattr(settings, "_wrapped", None)
    while isinstance(holder, UserSettingsHolder):
        if name in holder.__dict__ or name in holder.__dict__.get("_deleted", ()):
            return True
        holder = holder.default_settings
    return False


def on_unless_turned_off(name: str) -> bool:
    """True unless the deployment or a settings override turns ``name`` off."""

    if os.environ.get(name, "").strip() or _overridden(name):
        return getattr(settings, name, False) is True
    return True
