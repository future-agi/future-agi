"""The provider customer screens name for a request.

A managed call is Future
AGI's, so customer screens name its provider "Future AGI managed". The
upstream the gateway bought it from shows only in the request's technical
attempt details (and the docs' public subprocessors list).

The label follows the billing mode, never the provider's name: an
organization's own OpenRouter key is a provider of its own and keeps its
name. The console's frontend/src/sections/gateway/utils/providerLabel.js
applies the same rule to rows it renders; this module applies it to the
views the server makes (analytics groups, model comparison, the Logs export,
the Logs provider filter and search). The filter and search match the
provider as shown: an upstream's id never finds a managed row.
"""

from django.db.models import Case, CharField, F, Q, Value, When
from django.db.models.functions import Coalesce, NullIf

MANAGED = "managed"
BYOK = "byok"
MANAGED_PROVIDER_LABEL = "Future AGI"
# The billing modes' names, one pair on every screen (the console's
# BILLING_MODE_LABELS in utils/providerLabel.js). The managed mode is
# "Managed"; the provider of a managed call is MANAGED_PROVIDER_LABEL.
MANAGED_MODE_LABEL = "Future AGI credits"
OWN_KEY_LABEL = "Own key"
UNKNOWN_BILLING_MODE = "unknown"


def _normalized(text):
    return " ".join(str(text or "").split()).casefold()


_MANAGED_LABEL_KEY = _normalized(MANAGED_PROVIDER_LABEL)


def provider_label(provider, billing_mode):
    """The provider to show: "Future AGI managed" for a managed call."""
    if _normalized(billing_mode) == MANAGED:
        return MANAGED_PROVIDER_LABEL
    return provider or ""


def is_managed_label(value):
    """Whether a filter value names the managed label (any case, any
    spacing)."""
    return _normalized(value) in {_MANAGED_LABEL_KEY, _normalized("Future AGI managed")}


# A search finds the label from the start of one of its words ("fut", "agi",
# "managed", "agi man"), never from inside one: "ed" or "re" would otherwise
# pull in every managed row on top of the rows that match on their own.
_LABEL_WORD_STARTS = tuple(
    " ".join(_MANAGED_LABEL_KEY.split()[i:])
    for i in range(len(_MANAGED_LABEL_KEY.split()))
)
MIN_LABEL_SEARCH_LENGTH = 3


def label_matches_search(query):
    """Whether a search for ``query`` matches the managed label: at least
    three characters that start a word of it, in order."""
    key = _normalized(query)
    return len(key) >= MIN_LABEL_SEARCH_LENGTH and any(
        start.startswith(key) for start in _LABEL_WORD_STARTS
    )


def provider_search(query):
    """The Logs search's provider clause: it matches the provider as shown.
    A managed row reads "Future AGI managed", so the upstream's id does not
    find it (its model, request id and error still do); a part of the label
    does. A row recorded before billing modes (none stored) keeps matching
    on its provider id."""
    match = Q(provider__icontains=query) & ~Q(billing_mode=MANAGED)
    if label_matches_search(query):
        match |= Q(billing_mode=MANAGED)
    return match


def provider_label_expression():
    """provider_label as a database expression over a request log."""
    return Case(
        When(billing_mode=MANAGED, then=Value(MANAGED_PROVIDER_LABEL)),
        default=F("provider"),
        output_field=CharField(),
    )


def billing_mode_label_expression():
    """A request's billing mode as a group name: "Managed", "Own key", or
    the stored mode ("unknown" when none was recorded)."""
    return Case(
        When(billing_mode=MANAGED, then=Value(MANAGED_MODE_LABEL)),
        When(billing_mode=BYOK, then=Value(OWN_KEY_LABEL)),
        default=Coalesce(
            NullIf(F("billing_mode"), Value("")), Value(UNKNOWN_BILLING_MODE)
        ),
        output_field=CharField(),
    )


def provider_filter(values):
    """The Logs provider filter, which matches the Provider column it
    filters: "Future AGI managed" matches the managed rows, and a provider id
    matches that provider's other rows (own key, or none recorded), never a
    managed one. None when nothing is asked."""
    names = [value for value in values if not is_managed_label(value)]
    match = Q()
    if names:
        match |= Q(provider__in=names) & ~Q(billing_mode=MANAGED)
    if len(names) < len(values):
        match |= Q(billing_mode=MANAGED)
    return match or None
