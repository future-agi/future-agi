"""Limits on the three features that run on Future AGI's platform models.

The smart filter, the criteria helper and the AI eval writer each have their own
limit, counted after sign-in: one budget per organization, and one per user within
that organization. Only POST counts, the method that runs the feature. Rates are
DRF-style strings (``60/min``) read from Django settings on each request:

| Feature | Applied by | Organization | User |
|---|---|---|---|
| Smart filter | ``AIFilterView`` | 60/min | 30/min |
| Criteria helper | ``OptimizedDatasetKbView``, ``OptimizedDatasetView`` | 60/min | 30/min |
| AI eval writer | ``AIEvalWriterView`` | 60/min | 30/min |

Settings: ``<FEATURE>_ORG_THROTTLE_RATE`` and ``<FEATURE>_USER_THROTTLE_RATE``, where
``<FEATURE>`` is ``SMART_FILTER``, ``CRITERIA_HELPER`` or ``AI_EVAL_WRITER``.

A request past either budget gets the standard 429 envelope with ``Retry-After``,
code ``throttled`` and one sentence that says which budget ran out (the user's or
the organization's) and how many seconds to wait, the ``Retry-After`` value. The
sentences are in ``tfc.utils.error_codes`` under each throttle's ``messages``; the
criteria helper's speak of Optimize runs, where people start it. A refused request
counts against neither budget. The budgets are the atomic sliding windows of
``accounts.omega_rate_window`` in the default cache's Redis; when that store is down,
the request is refused 503 with ``Retry-After`` rather than let through unlimited.
"""

import math
from typing import NoReturn

from django.conf import settings
from rest_framework.exceptions import Throttled
from rest_framework.request import Request
from rest_framework.throttling import BaseThrottle

from accounts.omega_rate_window import (
    AtomicRateThrottle,
    Window,
    parse_rate,
    window_key,
)
from tfc.utils.error_codes import get_error_message
from tfc.utils.tenant_scope import caller_organization

ORGANIZATION_RATE = "60/min"
USER_RATE = "30/min"
ORGANIZATION = "organization"
USER = "user"


def _rate(setting, default):
    return parse_rate(getattr(settings, setting, None) or default)


def _wait_in_words(seconds):
    """The wait as a refusal's sentence reads it: ``1 second`` or ``<N> seconds``."""
    return "1 second" if seconds == 1 else f"{seconds} seconds"


class PlatformFeatureThrottled(Throttled):
    """A 429 whose message already says when to try again.

    DRF's ``Throttled`` would add its own wait sentence; this one keeps the message
    as given, and ``wait`` still becomes the ``Retry-After`` header.
    """

    def __init__(self, message: str, wait: int) -> None:
        super().__init__(detail=message)
        self.wait = wait


class PlatformFeatureThrottle(AtomicRateThrottle):
    """One feature's budgets: per organization, and per user within it."""

    scope = ""
    organization_rate = ("", ORGANIZATION_RATE)
    user_rate = ("", USER_RATE)
    # Budget -> error_codes key of the sentence a refusal shows.
    messages = {ORGANIZATION: "", USER: ""}
    # The budget of each window, in the order windows() returns them.
    budgets = ()

    def windows(self, request, view):
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return None
        organization_id = getattr(caller_organization(request), "pk", None)
        user_id = getattr(user, "pk", None)
        windows = []
        self.budgets = []
        if organization_id is not None:
            # One Redis Cluster slot for both of the organization's windows.
            tag = f"{{o:{organization_id}}}"
            limit, seconds = _rate(*self.organization_rate)
            windows.append(Window(window_key(self.scope, tag, "org"), limit, seconds))
            self.budgets.append(ORGANIZATION)
        else:
            tag = f"{{u:{user_id}}}"
        if user_id is not None:
            limit, seconds = _rate(*self.user_rate)
            key = window_key(self.scope, tag, "user", str(user_id))
            windows.append(Window(key, limit, seconds))
            self.budgets.append(USER)
        return windows or None

    def refusal(self, wait: float) -> PlatformFeatureThrottled | None:
        """The 429 for the budget that refused this request, or None if it admitted it.

        ``wait`` is the view's wait across all its throttles. When both budgets are
        full the organization's is named: it is the first window refused.
        """
        if self.refused_by is None:
            return None
        seconds = math.ceil(wait)
        key = self.messages[self.budgets[self.refused_by]]
        return PlatformFeatureThrottled(
            get_error_message(key).format(wait=_wait_in_words(seconds)), seconds
        )


class PlatformFeatureLimited:
    """View mixin: each POST draws on ``platform_throttle``, and a refusal says which
    budget ran out and when to try again."""

    platform_throttle = None

    def get_throttles(self) -> list[BaseThrottle]:
        throttles = super().get_throttles()
        self._platform_throttle = None
        if self.request.method == "POST":
            self._platform_throttle = self.platform_throttle()
            throttles.append(self._platform_throttle)
        return throttles

    def throttled(self, request: Request, wait: float) -> NoReturn:
        throttle = getattr(self, "_platform_throttle", None)
        refusal = throttle.refusal(wait) if throttle is not None else None
        if refusal is not None:
            raise refusal
        super().throttled(request, wait)


class SmartFilterThrottle(PlatformFeatureThrottle):
    scope = "platform_smart_filter"
    organization_rate = ("SMART_FILTER_ORG_THROTTLE_RATE", ORGANIZATION_RATE)
    user_rate = ("SMART_FILTER_USER_THROTTLE_RATE", USER_RATE)
    messages = {
        ORGANIZATION: "SMART_FILTER_ORGANIZATION_THROTTLED",
        USER: "SMART_FILTER_USER_THROTTLED",
    }


class CriteriaHelperThrottle(PlatformFeatureThrottle):
    scope = "platform_criteria_helper"
    organization_rate = ("CRITERIA_HELPER_ORG_THROTTLE_RATE", ORGANIZATION_RATE)
    user_rate = ("CRITERIA_HELPER_USER_THROTTLE_RATE", USER_RATE)
    messages = {
        ORGANIZATION: "OPTIMIZE_RUN_ORGANIZATION_THROTTLED",
        USER: "OPTIMIZE_RUN_USER_THROTTLED",
    }


class AIEvalWriterThrottle(PlatformFeatureThrottle):
    scope = "platform_ai_eval_writer"
    organization_rate = ("AI_EVAL_WRITER_ORG_THROTTLE_RATE", ORGANIZATION_RATE)
    user_rate = ("AI_EVAL_WRITER_USER_THROTTLE_RATE", USER_RATE)
    messages = {
        ORGANIZATION: "AI_EVAL_WRITER_ORGANIZATION_THROTTLED",
        USER: "AI_EVAL_WRITER_USER_THROTTLED",
    }
