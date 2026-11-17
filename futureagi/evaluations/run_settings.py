"""The run settings of an evaluation binding.

A binding's settings (the ``config`` of a dataset, simulation, tracing or
external evaluation) are run settings: mappings, thresholds, choices, the
model choice and evaluator options. ``run_settings`` returns a copy of a
settings value that holds only those, for every place that shows, returns or
records it: each field named in ``CREDENTIAL_FIELDS``, or starting with
``CREDENTIAL_FIELD_PREFIXES``, is left out at any depth, and so is ``key``
inside an object that holds one of those fields or a field of
``CREDENTIAL_MARKERS`` (a service-account configuration, a custom model's
endpoint settings). Elsewhere ``key`` is an ordinary name and is kept.

The names inside a ``mapping`` or ``choice_scores`` object are the user's own
(template variables, choice labels), so they are kept whatever they are; the
values under them are filtered like any other.

Nothing here imports a model, so serializers, views and tasks of every app
can use it.
"""

CREDENTIAL_FIELDS = frozenset(
    {
        "api_key",
        "private_key",
        "client_secret",
        "vertex_credentials",
        "service_account",
        "credentials",
    }
)
CREDENTIAL_FIELD_PREFIXES = ("aws_",)

# Fields that are not credentials themselves but mark the object holding
# them as a credential configuration: a service account's, or a custom
# model's endpoint settings ({"key": ..., "api_base": ...}).
CREDENTIAL_MARKERS = frozenset({"client_email", "private_key_id", "api_base"})

# Objects whose field names are the user's own names, never settings.
USER_NAMED_OBJECTS = frozenset({"mapping", "choice_scores"})

# What an evaluation's usage record also leaves out of the settings it holds,
# at the top level and in the evaluator settings: the provider that goes with
# the model's stored settings.
_USAGE_RECORD_ALSO_LEAVES_OUT = ("provider",)


def is_credential_field(name):
    """True when ``name`` is a credential field."""
    return isinstance(name, str) and (
        name in CREDENTIAL_FIELDS or name.startswith(CREDENTIAL_FIELD_PREFIXES)
    )


def _is_credential_object(value):
    return any(
        is_credential_field(name) or name in CREDENTIAL_MARKERS for name in value
    )


def run_settings(value, *, _user_named=False):
    """A copy of ``value`` holding only run settings.

    Dicts, lists and tuples are copied; any other value is returned as it
    is. ``value`` itself is never changed.
    """
    if isinstance(value, dict):
        credential_object = not _user_named and _is_credential_object(value)
        kept = {}
        for name, item in value.items():
            if not _user_named and (
                is_credential_field(name) or (credential_object and name == "key")
            ):
                continue
            kept[name] = run_settings(
                item,
                _user_named=not _user_named and name in USER_NAMED_OBJECTS,
            )
        return kept
    if isinstance(value, list):
        return [run_settings(item) for item in value]
    if isinstance(value, tuple):
        return tuple(run_settings(item) for item in value)
    return value


def usage_record_settings(config):
    """``config`` as an evaluation's usage record holds it.

    ``run_settings(config)`` without ``provider`` at the top level and in the
    evaluator settings (``config["config"]``). Anything other than a dict is
    returned as it is; ``config`` is never changed.
    """
    if not isinstance(config, dict):
        return config
    kept = run_settings(config)
    for name in _USAGE_RECORD_ALSO_LEAVES_OUT:
        kept.pop(name, None)
    evaluator_settings = kept.get("config")
    if isinstance(evaluator_settings, dict):
        for name in _USAGE_RECORD_ALSO_LEAVES_OUT:
            evaluator_settings.pop(name, None)
    return kept
