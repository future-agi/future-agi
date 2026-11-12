"""The reference rules under one name, so the case builders can write ``R.<name>``.

Standard library only. These modules exist to GENERATE and CHECK the vector files; they are not the webhooks
core. The core must pass the vectors on its own and must not import them.
"""

from .ref_addresses import (  # noqa: F401
    _DOTTED_QUAD,
    _LABEL,
    _NUMERIC_LABEL,
    ANOTHER_ADDRESS,
    BLOCKED_PORTS,
    METADATA_HOSTNAMES,
    PSL_ICANN,
    SPECIAL_USE_SUFFIXES,
    _deny,
    _host_matches,
    _public_suffix,
    redirect_decision,
    url_decision,
)
from .ref_fanout import (  # noqa: F401
    BACKLOG_CAP,
    DEFAULT_FAILED_STATUSES,
    _filters_pass,
    _type_matches,
    fan_out,
)
from .ref_ids import (  # noqa: F401
    CROCKFORD,
    iso_ms,
    parse_iso_ms,
    ulid,
    uuid7,
)
from .ref_ipclass import (  # noqa: F401
    GLOBAL_UNICAST_V6,
    NEVER_NETS,
    PRIVATE_NETS,
    RANK,
    _class_plain,
    _in_cidrs,
    address_decision,
    classify,
    embedded_ipv4,
)
from .ref_payloads import (  # noqa: F401
    FULL_COMMIT_CHARS,
    NIL_UUID,
    REDACTED,
    SECRET_SHAPE_ANY,
    SECRET_SHAPES,
    TEST_DEP,
    THIN_COMMIT_CHARS,
    _add_content,
    _content,
    _cut,
    _label,
    build_prompt_label_changed,
    build_prompt_version_created,
    canonical,
    envelope,
    es_key_order,
    es_number,
    has_secret_shape,
    person,
    scrub,
)
from .ref_run_event import (  # noqa: F401
    InvalidHeader,
    _pct_decode,
    build_deployment_run,
)
from .ref_schedule import (  # noqa: F401
    FAILED_FOR_HEALTH,
    MAX_ATTEMPTS,
    NON_HTTP_OUTCOMES,
    RATE_HALVE_S,
    REJECTED_DELAYS_S,
    REJECTED_STATUSES,
    RETRY_AFTER_CAP_S,
    STANDARD_DELAYS_S,
    classify_answer,
    endpoint_health,
    jitter_permille,
    next_action,
    parse_retry_after,
)
from .ref_signing import (  # noqa: F401
    SECRET_PREFIX,
    TOLERANCE_S,
    SecretInvalid,
    attempt_headers,
    secret_key,
    sign,
    signature_header,
    verify,
)
