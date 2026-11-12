import functools
import importlib

import structlog
from rest_framework.views import APIView

from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import (
    ApiTextErrorResponseSerializer,
    DeploymentInfoResponseSerializer,
)
from tfc.utils.general_methods import GeneralMethods
from tfc.views import deployment_mode

logger = structlog.get_logger(__name__)

# The billing package (ee.cloud) is installed apart from core and may be an
# older or newer revision. Each budget feature works only when the package
# provides the names core relies on for it: the total-spend catch-up the
# budget scheduler calls (without it a total-spend budget never pauses
# usage), and the recipient roles that let a budget e-mail that names no one
# reach the organization's Owners and Admins (without them it goes to no one).
#
# A total-spend budget also counts as enforced only when the package says it
# compares the threshold with this billing period's usage charges, the Usage
# charges figure on the Usage summary page, which is what Billing tells the user a
# total-spend budget counts. A package that measures total spend another
# way, or does not say, answers not enforced.
_BUDGET_FEATURES = {
    "total_spend_budgets_enforced": (
        "ee.cloud.billing.budget_enforcement",
        ("evaluate_total_spend_budgets_catchup",),
        {"TOTAL_SPEND_BUDGET_MEASURE": "period_usage_charges"},
    ),
    "budget_notices_delivered": (
        "ee.cloud.billing.notifications",
        ("BUDGET_NOTICE_RECIPIENT_ROLES",),
        {},
    ),
}


def _billing_package_provides(
    module_name: str, names: tuple[str, ...], values: dict[str, str]
) -> bool:
    """Whether the module has every one of ``names`` and each of ``values``
    set to exactly the value given."""
    try:
        module = importlib.import_module(module_name)
        return all(hasattr(module, name) for name in names) and all(
            getattr(module, name, None) == value for name, value in values.items()
        )
    except ImportError:
        # No billing package (OSS and self-hosted), or one without this module.
        return False
    except Exception:
        logger.warning(
            "budget_feature_check_failed",
            module=module_name,
            names=[*names, *values],
            exc_info=True,
        )
        return False


@functools.cache
def budget_support() -> dict[str, bool]:
    """Whether the installed billing package pauses usage at a total-spend
    budget (measured as this billing period's usage charges) and sends budget
    e-mail notices.

    Checked once per process. A check that fails for any reason answers
    False, so Billing says the budget is not enforced or not e-mailed rather
    than the request failing.
    """
    return {
        feature: _billing_package_provides(module_name, names, values)
        for feature, (module_name, names, values) in _BUDGET_FEATURES.items()
    }


class DeploymentInfoView(APIView):
    """Public deployment-mode probe used by the frontend to gate UI.

    Returns ``{"mode": "oss"|"ee"|"cloud", "total_spend_budgets_enforced":
    bool, "budget_notices_delivered": bool}``. No auth — public config: the
    two booleans describe the installed billing package, not any
    organization's data.
    """

    authentication_classes = []
    permission_classes = []

    @validated_request(
        responses={
            200: DeploymentInfoResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        }
    )
    def get(self, request, *args, **kwargs):
        mode = deployment_mode.get_deployment_mode()
        gm = GeneralMethods(request)
        return gm.success_response({"mode": mode, **budget_support()})
