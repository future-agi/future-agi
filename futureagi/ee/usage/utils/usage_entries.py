import json
import math
import traceback
from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from math import ceil
from typing import Optional  # Import ceil to use for rounding up

import av
import requests
import structlog
import tiktoken
from django.contrib.auth import get_user_model  # Importing the custom user model
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from PIL import Image

from accounts.models.aws_marketplace import (
    AWSMarketplaceCustomer,
    SubscriptionStatus,
)
from accounts.models.organization import Organization
from accounts.services.aws_marketplace_metering import (
    aws_marketplace_metering,
)
from agentic_eval.core.utils.functions import detect_input_type

logger = structlog.get_logger(__name__)
from model_hub.utils import call_websocket
from tfc.settings.settings import (
    BUSINESS_MONTHLY_STRIPE_PRICE_ID,
    BUSINESS_MONTHLY_STRIPE_PRICE_IDS_ALL,
    BUSINESS_YEARLY_STRIPE_PRICE_ID,
    BUSINESS_YEARLY_STRIPE_PRICE_IDS_ALL,
)
from tfc.utils.error_codes import get_error_message
from tfc.utils.storage import download_audio_from_url, upload_audio_to_s3
from tracer.models.project import ProjectSourceChoices
from ee.usage.models.usage import (
    APICallLog,
    APICallStatusChoices,
    APICallType,
    APICallTypeChoices,
    OrganizationSubscription,
    PlanChoices,
    Pricing,
    RateLimit,
    ResourceLimits,
    ResourceType,
    ResourceTypeChoices,
    SubscriptionTier,
    SubscriptionTierChoices,
)

User = get_user_model()

DATASET_LIMIT_REACHED_MESSAGE = "Dataset limit reached. \
      Please delete existing datasets or upgrade to a higher tier to \
      create more datasets."

ROW_LIMIT_REACHED_MESSAGE = "Row limit reached. \
      Please upgrade to a higher tier to \
      avail more rows."

TRACES_LIMIT_REACHED_MESSAGE = "Traces limit reached. \
      Please delete existing traces or upgrade to a higher tier to \
      avail more traces."


EVALUATOR_CALLS = [
    APICallTypeChoices.TURING_LARGE_EVALUATOR.value,
    APICallTypeChoices.TURING_SMALL_EVALUATOR.value,
    APICallTypeChoices.TURING_FLASH_EVALUATOR.value,
    APICallTypeChoices.PROTECT_EVALUATOR.value,
    APICallTypeChoices.PROTECT_FLASH_EVALUATOR.value,
]

subscription_tier_entries = [
    {
        "name": SubscriptionTierChoices.FREE.value,
        "description": "Free Tier",
        "wallet_refill_amount": 5,
        "stripe_price_id": "",
        "stripe_price_ids_all": [],
    },
    {
        "name": SubscriptionTierChoices.BUSINESS.value,
        "description": "Basic Monthly Tier",
        "wallet_refill_amount": 0,
        "stripe_price_id": BUSINESS_MONTHLY_STRIPE_PRICE_ID,
        "stripe_price_ids_all": BUSINESS_MONTHLY_STRIPE_PRICE_IDS_ALL,
    },
    {
        "name": SubscriptionTierChoices.BUSINESS_YEARLY.value,
        "description": "Basic Yearly Tier",
        "wallet_refill_amount": 0,
        "stripe_price_id": BUSINESS_YEARLY_STRIPE_PRICE_ID,
        "stripe_price_ids_all": BUSINESS_YEARLY_STRIPE_PRICE_IDS_ALL,
    },
    {
        "name": SubscriptionTierChoices.CUSTOM.value,
        "description": "Custom Tier",
        "wallet_refill_amount": 0,
        "stripe_price_id": "",
        "stripe_price_ids_all": [],
    },
]

api_call_type_entries = [
    {
        "name": APICallTypeChoices.DATASET_EVALUATION.value,
        "description": "This is the API for running evals",
    },
    {
        "name": APICallTypeChoices.DATASET_PROTECT.value,
        "description": "This is the API for protect",
    },
    {
        "name": APICallTypeChoices.PROMPT_BENCH.value,
        "description": "This is the API for PROMPT BENCH",
    },
    {
        "name": APICallTypeChoices.AUTO_ANNOTATION.value,
        "description": "This is the API for auto annotation",
    },
    {
        "name": APICallTypeChoices.EXPERIMENT_EVALUATION.value,
        "description": "This is the API for running evals",
    },
    {
        "name": APICallTypeChoices.OPTIMISATION_EVALUATION.value,
        "description": "This is the API for running optimisation evals",
    },
    {
        "name": APICallTypeChoices.DATASET_RUN_PROMPT.value,
        "description": "This is the API for running prompts",
    },
    {
        "name": APICallTypeChoices.DATASET_OPTIMIZATION.value,
        "description": "This is the API for optimization",
    },
    # For synthetic data generation
    {
        "name": APICallTypeChoices.SYNTHETIC_DATA_GENERATION.value,
        "description": "This is the API for synthetic data generation",
    },
    {
        "name": APICallTypeChoices.USER_ADD.value,
        "description": "This is the API for adding users",
    },
    # Adding Traces
    {
        "name": APICallTypeChoices.OBSERVE_ADD.value,
        "description": "This is the API for adding observe",
    },
    # Adding Prototypes
    {
        "name": APICallTypeChoices.PROTOTYPE_ADD.value,
        "description": "This is the API for adding prototypes",
    },
    {
        "name": APICallTypeChoices.ERROR_LOCALIZER.value,
        "description": "This is the API for running error localizer",
    },
    {
        "name": APICallTypeChoices.WALLET_REFUND.value,
        "description": "This is the API for refunding costs on api calls",
    },
    {
        "name": APICallTypeChoices.WALLET_REFILL.value,
        "description": "This is the API for refilling monthly credits",
    },
    {
        "name": APICallTypeChoices.WALLET_ADD_FUNDS.value,
        "description": "This is the API for adding funds to the wallet",
    },
    {
        "name": APICallTypeChoices.WALLET_AUTO_RECHARGE.value,
        "description": "This is the API for auto-recharging the wallet every month",
    },
    {
        "name": APICallTypeChoices.DATASET_ADD.value,
        "description": "This is the API for adding datasets",
    },
    {
        "name": APICallTypeChoices.ROW_ADD.value,
        "description": "This is the API for adding rows",
    },
    {
        "name": APICallTypeChoices.KNOWLEDGE_BASE.value,
        "description": "This is the API adding knowledge base",
    },
    {
        "name": APICallTypeChoices.EVAL_EXPLANATION.value,
        "description": "This is the API for explanation of evals",
    },
    {
        "name": APICallTypeChoices.VOICE_CALL.value,
        "description": "This is the API for voice call charges based on duration",
    },
    {
        "name": APICallTypeChoices.TEXT_CALL.value,
        "description": "This is the API for text call charges based on number of turns",
    },
    {
        "name": APICallTypeChoices.CODE_EVALUATOR.value,
        "description": "This is the API for code/function evaluator",
    },
    {
        "name": APICallTypeChoices.TRACE_ERROR_ANALYSIS.value,
        "description": "This is the API for trace error analysis",
    },
    {
        "name": APICallTypeChoices.TURING_LARGE_EVALUATOR.value,
        "description": "This is the API for turing large evaluator",
    },
    {
        "name": APICallTypeChoices.TURING_SMALL_EVALUATOR.value,
        "description": "This is the API for turing small evaluator",
    },
    {
        "name": APICallTypeChoices.TURING_FLASH_EVALUATOR.value,
        "description": "This is the API for turing flash evaluator",
    },
    {
        "name": APICallTypeChoices.PROTECT_EVALUATOR.value,
        "description": "This is the API for protect evaluator",
    },
    {
        "name": APICallTypeChoices.PROTECT_FLASH_EVALUATOR.value,
        "description": "This is the API for protect flash evaluator",
    },
]


resource_type_choices = [
    {
        "name": ResourceTypeChoices.DATASET.value,
        "description": "Number of datasets in account",
    },
    {
        "name": ResourceTypeChoices.ROWS.value,
        "description": "Number of rows in account",
    },
    {
        "name": ResourceTypeChoices.USERS.value,
        "description": "Number of users in account",
    },
    {
        "name": ResourceTypeChoices.OBSERVE.value,
        "description": "Number of traces for observe",
    },
    {
        "name": ResourceTypeChoices.PROTOTYPES.value,
        "description": "Number of prototypes for prototype",
    },
    {
        "name": ResourceTypeChoices.KNOWLEDGE_BASE.value,
        "description": "Number of knowledge base in organization",
    },
    {
        "name": ResourceTypeChoices.TRACES.value,
        "description": "Number of traces across all projects",
    },
]


def _get_rate_limit_entries_for_eval_calls():
    rate_limits = []

    for call in EVALUATOR_CALLS:
        for tier in SubscriptionTierChoices:
            if tier.value == SubscriptionTierChoices.CUSTOM.value:
                rate_limits.append(
                    {
                        "api_call_type": call,
                        "description": "This is the API for running evals",
                        "subscription_tier": tier.value,
                        "minute_limit": 5000,
                        "hour_limit": 30000,
                        "day_limit": 100000,
                        "month_limit": 3000000,
                    }
                )
            else:
                rate_limits.append(
                    {
                        "api_call_type": call,
                        "description": "This is the API for running evals",
                        "subscription_tier": tier.value,
                        "minute_limit": 50,
                        "hour_limit": 300,
                        "day_limit": 1000,
                        "month_limit": 30000,
                    }
                )
    return rate_limits


rate_limit_entries = [
    # evaluation stand-alone
    {
        "api_call_type": APICallTypeChoices.DATASET_EVALUATION.value,
        "description": "This is the API for running evals",
        "subscription_tier": SubscriptionTierChoices.FREE.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 30000,
    },
    {
        "api_call_type": APICallTypeChoices.DATASET_EVALUATION.value,
        "description": "This is the API for running evals",
        "subscription_tier": SubscriptionTierChoices.BUSINESS.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 30000,
    },
    {
        "api_call_type": APICallTypeChoices.DATASET_EVALUATION.value,
        "description": "This is the API for running evals",
        "subscription_tier": SubscriptionTierChoices.BUSINESS_YEARLY.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 3000,
    },
    {
        "api_call_type": APICallTypeChoices.DATASET_EVALUATION.value,
        "description": "This is the API for running evals",
        "subscription_tier": SubscriptionTierChoices.CUSTOM.value,
        "minute_limit": 5000,
        "hour_limit": 30000,
        "day_limit": 100000,
        "month_limit": 300000,
    },
    {
        "api_call_type": APICallTypeChoices.AUTO_ANNOTATION.value,
        "description": "This is the API for running evals",
        "subscription_tier": SubscriptionTierChoices.FREE.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 30000,
    },
    {
        "api_call_type": APICallTypeChoices.AUTO_ANNOTATION.value,
        "description": "This is the API for running evals",
        "subscription_tier": SubscriptionTierChoices.BUSINESS.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 30000,
    },
    {
        "api_call_type": APICallTypeChoices.AUTO_ANNOTATION.value,
        "description": "This is the API for running evals",
        "subscription_tier": SubscriptionTierChoices.BUSINESS_YEARLY.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 3000,
    },
    {
        "api_call_type": APICallTypeChoices.AUTO_ANNOTATION.value,
        "description": "This is the API for running evals",
        "subscription_tier": SubscriptionTierChoices.CUSTOM.value,
        "minute_limit": 5000,
        "hour_limit": 30000,
        "day_limit": 100000,
        "month_limit": 300000,
    },
    # run prompt limit
    {
        "api_call_type": APICallTypeChoices.DATASET_PROTECT.value,
        "description": "This is the API for running prompts",
        "subscription_tier": SubscriptionTierChoices.FREE.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 3000,
    },
    {
        "api_call_type": APICallTypeChoices.DATASET_PROTECT.value,
        "description": "This is the API for running prompts",
        "subscription_tier": SubscriptionTierChoices.BUSINESS.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 3000,
    },
    {
        "api_call_type": APICallTypeChoices.DATASET_PROTECT.value,
        "description": "This is the API for running prompts",
        "subscription_tier": SubscriptionTierChoices.BUSINESS_YEARLY.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 3000,
    },
    {
        "api_call_type": APICallTypeChoices.DATASET_PROTECT.value,
        "description": "This is the API for running prompts",
        "subscription_tier": SubscriptionTierChoices.CUSTOM.value,
        "minute_limit": 5000,
        "hour_limit": 30000,
        "day_limit": 100000,
        "month_limit": 300000,
    },
    {
        "api_call_type": APICallTypeChoices.PROMPT_BENCH.value,
        "description": "This is the API for running prompts",
        "subscription_tier": SubscriptionTierChoices.FREE.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 3000,
    },
    {
        "api_call_type": APICallTypeChoices.PROMPT_BENCH.value,
        "description": "This is the API for running prompts",
        "subscription_tier": SubscriptionTierChoices.BUSINESS.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 3000,
    },
    {
        "api_call_type": APICallTypeChoices.PROMPT_BENCH.value,
        "description": "This is the API for running prompts",
        "subscription_tier": SubscriptionTierChoices.BUSINESS_YEARLY.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 3000,
    },
    {
        "api_call_type": APICallTypeChoices.PROMPT_BENCH.value,
        "description": "This is the API for running prompts",
        "subscription_tier": SubscriptionTierChoices.CUSTOM.value,
        "minute_limit": 5000,
        "hour_limit": 30000,
        "day_limit": 100000,
        "month_limit": 300000,
    },
    # run prompt limit : SYNTHETIC DATA GENERATION
    {
        "api_call_type": APICallTypeChoices.SYNTHETIC_DATA_GENERATION.value,
        "description": "This is the API for running prompts",
        "subscription_tier": SubscriptionTierChoices.FREE.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 3000,
    },
    {
        "api_call_type": APICallTypeChoices.SYNTHETIC_DATA_GENERATION.value,
        "description": "This is the API for running prompts",
        "subscription_tier": SubscriptionTierChoices.BUSINESS.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 3000,
    },
    {
        "api_call_type": APICallTypeChoices.SYNTHETIC_DATA_GENERATION.value,
        "description": "This is the API for running prompts",
        "subscription_tier": SubscriptionTierChoices.BUSINESS_YEARLY.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 3000,
    },
    {
        "api_call_type": APICallTypeChoices.SYNTHETIC_DATA_GENERATION.value,
        "description": "This is the API for running prompts",
        "subscription_tier": SubscriptionTierChoices.CUSTOM.value,
        "minute_limit": 5000,
        "hour_limit": 30000,
        "day_limit": 100000,
        "month_limit": 300000,
    },
    # run prompt limit : ERROR LOCALIZER
    {
        "api_call_type": APICallTypeChoices.ERROR_LOCALIZER.value,
        "description": "This is the API for running prompts",
        "subscription_tier": SubscriptionTierChoices.FREE.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 3000,
    },
    {
        "api_call_type": APICallTypeChoices.ERROR_LOCALIZER.value,
        "description": "This is the API for running prompts",
        "subscription_tier": SubscriptionTierChoices.BUSINESS.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 3000,
    },
    {
        "api_call_type": APICallTypeChoices.ERROR_LOCALIZER.value,
        "description": "This is the API for running prompts",
        "subscription_tier": SubscriptionTierChoices.BUSINESS_YEARLY.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 3000,
    },
    {
        "api_call_type": APICallTypeChoices.ERROR_LOCALIZER.value,
        "description": "This is the API for running prompts",
        "subscription_tier": SubscriptionTierChoices.CUSTOM.value,
        "minute_limit": 5000,
        "hour_limit": 30000,
        "day_limit": 100000,
        "month_limit": 300000,
    },
    # run prompt limit
    {
        "api_call_type": APICallTypeChoices.DATASET_RUN_PROMPT.value,
        "description": "This is the API for running prompts",
        "subscription_tier": SubscriptionTierChoices.FREE.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 3000,
    },
    {
        "api_call_type": APICallTypeChoices.DATASET_RUN_PROMPT.value,
        "description": "This is the API for running prompts",
        "subscription_tier": SubscriptionTierChoices.BUSINESS.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 3000,
    },
    {
        "api_call_type": APICallTypeChoices.DATASET_RUN_PROMPT.value,
        "description": "This is the API for running prompts",
        "subscription_tier": SubscriptionTierChoices.BUSINESS_YEARLY.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 3000,
    },
    {
        "api_call_type": APICallTypeChoices.DATASET_RUN_PROMPT.value,
        "description": "This is the API for running prompts",
        "subscription_tier": SubscriptionTierChoices.CUSTOM.value,
        "minute_limit": 5000,
        "hour_limit": 30000,
        "day_limit": 100000,
        "month_limit": 300000,
    },
    # run optimisation limit
    {
        "api_call_type": APICallTypeChoices.DATASET_OPTIMIZATION.value,
        "description": "This is the API for running optimisation",
        "subscription_tier": SubscriptionTierChoices.FREE.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 3000,
    },
    {
        "api_call_type": APICallTypeChoices.DATASET_OPTIMIZATION.value,
        "description": "This is the API for running optimisation",
        "subscription_tier": SubscriptionTierChoices.BUSINESS.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 3000,
    },
    {
        "api_call_type": APICallTypeChoices.DATASET_OPTIMIZATION.value,
        "description": "This is the API for running optimisation",
        "subscription_tier": SubscriptionTierChoices.BUSINESS_YEARLY.value,
        "minute_limit": 50,
        "hour_limit": 300,
        "day_limit": 1000,
        "month_limit": 3000,
    },
    {
        "api_call_type": APICallTypeChoices.DATASET_OPTIMIZATION.value,
        "description": "This is the API for running optimisation",
        "subscription_tier": SubscriptionTierChoices.CUSTOM.value,
        "minute_limit": 5000,
        "hour_limit": 30000,
        "day_limit": 100000,
        "month_limit": 300000,
    },
]

rate_limit_entries.extend(_get_rate_limit_entries_for_eval_calls())

resource_limit_entries = [
    # LIMITS FOR DATASETS
    {
        "resource_type": ResourceTypeChoices.DATASET.value,
        "description": "Number of datasets in account",
        "subscription_tier": SubscriptionTierChoices.FREE.value,
        "limit": 5,
    },
    {
        "resource_type": ResourceTypeChoices.DATASET.value,
        "description": "Number of datasets in account",
        "subscription_tier": SubscriptionTierChoices.BUSINESS.value,
        "limit": 20,
    },
    {
        "resource_type": ResourceTypeChoices.DATASET.value,
        "description": "Number of datasets in account",
        "subscription_tier": SubscriptionTierChoices.BUSINESS_YEARLY.value,
        "limit": 20,
    },
    {
        "resource_type": ResourceTypeChoices.DATASET.value,
        "description": "Number of datasets in account",
        "subscription_tier": SubscriptionTierChoices.CUSTOM.value,
        "limit": 20,
    },
    # LIMITS FOR TRACES
    # Same limits for OBSERVE
    {
        "resource_type": ResourceTypeChoices.OBSERVE.value,
        "description": "Number of observe in account",
        "subscription_tier": SubscriptionTierChoices.FREE.value,
        "limit": 3,
    },
    {
        "resource_type": ResourceTypeChoices.OBSERVE.value,
        "description": "Number of observe in account",
        "subscription_tier": SubscriptionTierChoices.BUSINESS.value,
        "limit": 3,
    },
    {
        "resource_type": ResourceTypeChoices.OBSERVE.value,
        "description": "Number of observe in account",
        "subscription_tier": SubscriptionTierChoices.BUSINESS_YEARLY.value,
        "limit": 3,
    },
    {
        "resource_type": ResourceTypeChoices.OBSERVE.value,
        "description": "Number of observe in account",
        "subscription_tier": SubscriptionTierChoices.CUSTOM.value,
        "limit": 3,
    },
    # LIMITS FOR PROTOTYPES
    # Same limits for OBSERVE
    {
        "resource_type": ResourceTypeChoices.PROTOTYPES.value,
        "description": "Number of prototype in account",
        "subscription_tier": SubscriptionTierChoices.FREE.value,
        "limit": 3,
    },
    {
        "resource_type": ResourceTypeChoices.PROTOTYPES.value,
        "description": "Number of prototype in account",
        "subscription_tier": SubscriptionTierChoices.BUSINESS.value,
        "limit": 3,
    },
    {
        "resource_type": ResourceTypeChoices.PROTOTYPES.value,
        "description": "Number of prototype in account",
        "subscription_tier": SubscriptionTierChoices.BUSINESS_YEARLY.value,
        "limit": 3,
    },
    {
        "resource_type": ResourceTypeChoices.PROTOTYPES.value,
        "description": "Number of prototype in account",
        "subscription_tier": SubscriptionTierChoices.CUSTOM.value,
        "limit": 3,
    },
    # LIMITS FOR TRACE CREATION
    {
        "resource_type": ResourceTypeChoices.TRACES.value,
        "description": "Number of traces in account",
        "subscription_tier": SubscriptionTierChoices.FREE.value,
        "limit": 10000,
    },
    {
        "resource_type": ResourceTypeChoices.TRACES.value,
        "description": "Number of traces in account",
        "subscription_tier": SubscriptionTierChoices.BUSINESS.value,
        "limit": 100000,
    },
    {
        "resource_type": ResourceTypeChoices.TRACES.value,
        "description": "Number of traces in account",
        "subscription_tier": SubscriptionTierChoices.BUSINESS_YEARLY.value,
        "limit": 100000,
    },
    {
        "resource_type": ResourceTypeChoices.TRACES.value,
        "description": "Number of traces in account",
        "subscription_tier": SubscriptionTierChoices.CUSTOM.value,
        "limit": 1000000000,
    },
    # LIMITS FOR ROWS
    {
        "resource_type": ResourceTypeChoices.ROWS.value,
        "description": "Number of rows in account",
        "subscription_tier": SubscriptionTierChoices.FREE.value,
        "limit": 2000,
    },
    {
        "resource_type": ResourceTypeChoices.ROWS.value,
        "description": "Number of rows in account",
        "subscription_tier": SubscriptionTierChoices.BUSINESS.value,
        "limit": 100000,
    },
    {
        "resource_type": ResourceTypeChoices.ROWS.value,
        "description": "Number of rows in account",
        "subscription_tier": SubscriptionTierChoices.BUSINESS_YEARLY.value,
        "limit": 100000,
    },
    {
        "resource_type": ResourceTypeChoices.ROWS.value,
        "description": "Number of rows in account",
        "subscription_tier": SubscriptionTierChoices.CUSTOM.value,
        "limit": 1000000,
    },
    # LIMITS FOR USERS
    {
        "resource_type": ResourceTypeChoices.USERS.value,
        "description": "Number of users in account",
        "subscription_tier": SubscriptionTierChoices.FREE.value,
        "limit": 3,
    },
    {
        "resource_type": ResourceTypeChoices.USERS.value,
        "description": "Number of users in account",
        "subscription_tier": SubscriptionTierChoices.BUSINESS.value,
        "limit": 5,
    },
    {
        "resource_type": ResourceTypeChoices.USERS.value,
        "description": "Number of users in account",
        "subscription_tier": SubscriptionTierChoices.BUSINESS_YEARLY.value,
        "limit": 5,
    },
    {
        "resource_type": ResourceTypeChoices.USERS.value,
        "description": "Number of users in account",
        "subscription_tier": SubscriptionTierChoices.CUSTOM.value,
        "limit": 50,
    },
    # LIMITS FOR KB
    {
        "resource_type": ResourceTypeChoices.KNOWLEDGE_BASE.value,
        "description": "Number of knowledge base in account",
        "subscription_tier": SubscriptionTierChoices.FREE.value,
        "limit": 2,
    },
    {
        "resource_type": ResourceTypeChoices.KNOWLEDGE_BASE.value,
        "description": "Number of knowledge base in account",
        "subscription_tier": SubscriptionTierChoices.BUSINESS.value,
        "limit": 10,
    },
    {
        "resource_type": ResourceTypeChoices.KNOWLEDGE_BASE.value,
        "description": "Number of knowledge base in account",
        "subscription_tier": SubscriptionTierChoices.BUSINESS_YEARLY.value,
        "limit": 10,
    },
    {
        "resource_type": ResourceTypeChoices.KNOWLEDGE_BASE.value,
        "description": "Number of knowledge base in account",
        "subscription_tier": SubscriptionTierChoices.CUSTOM.value,
        "limit": 20,
    },
]


pricing_entries = [
    {
        "api_call_type": APICallTypeChoices.DATASET_EVALUATION.value,
        "price_per_call": 0.001 * 10,
    },
    {
        "api_call_type": APICallTypeChoices.DATASET_PROTECT.value,
        "price_per_call": 0.000001 * 2.5,
    },
    {
        "api_call_type": APICallTypeChoices.PROMPT_BENCH.value,
        "price_per_call": 0.000001 * 5,
    },
    {
        "api_call_type": APICallTypeChoices.AUTO_ANNOTATION.value,
        "price_per_call": 0.000001 * 10,
    },
    {
        "api_call_type": APICallTypeChoices.EXPERIMENT_EVALUATION.value,
        "price_per_call": 0.001 * 10,
    },
    {
        "api_call_type": APICallTypeChoices.OPTIMISATION_EVALUATION.value,
        "price_per_call": 0.001 * 10,
    },
    {
        "api_call_type": APICallTypeChoices.DATASET_OPTIMIZATION.value,
        "price_per_call": 0.000001 * 25.0,
    },
    {"api_call_type": APICallTypeChoices.USER_ADD.value, "price_per_call": 40},
    {
        "api_call_type": APICallTypeChoices.SYNTHETIC_DATA_GENERATION.value,
        "price_per_call": 0.000001 * 100.0,
    },
    # For error localizer
    {
        "api_call_type": APICallTypeChoices.ERROR_LOCALIZER.value,
        "price_per_call": 0.001 * 5,
    },
    # For observe add
    {
        "api_call_type": APICallTypeChoices.OBSERVE_ADD.value,
        "price_per_call": 0.001,
    },  # $10 per 10K traces for free, $10 per 100K traces for business
    # For prototype add
    {"api_call_type": APICallTypeChoices.PROTOTYPE_ADD.value, "price_per_call": 0.001},
    # For eval explanation
    {
        "api_call_type": APICallTypeChoices.EVAL_EXPLANATION.value,
        "price_per_call": 0.001 * 5,
    },
    # for voice call
    {
        "api_call_type": APICallTypeChoices.VOICE_CALL.value,
        "price_per_call": 0.15,
    },
    {
        "api_call_type": APICallTypeChoices.TEXT_CALL.value,
        "price_per_call": 0.005,
    },
    {
        "api_call_type": APICallTypeChoices.TRACE_ERROR_ANALYSIS.value,
        "price_per_call": 0.01,
    },
    {
        "api_call_type": APICallTypeChoices.TURING_LARGE_EVALUATOR.value,
        "price_per_call": 0.01,
    },
    {
        "api_call_type": APICallTypeChoices.TURING_SMALL_EVALUATOR.value,
        "price_per_call": 0.005,
    },
    {
        "api_call_type": APICallTypeChoices.TURING_FLASH_EVALUATOR.value,
        "price_per_call": 0.002,
    },
    {
        "api_call_type": APICallTypeChoices.PROTECT_EVALUATOR.value,
        "price_per_call": 0.01,
    },
    {
        "api_call_type": APICallTypeChoices.PROTECT_FLASH_EVALUATOR.value,
        "price_per_call": 0.005,
    },
]


# Dataclass for Websocket alert message
@dataclass
class WebSocketMessage:
    alert_title: str
    alert_description: str
    subscription_title: str
    subscription_description: str


def insert_subscription_tier_entries(subscription_tier_entries):
    try:
        for tier in subscription_tier_entries:
            if not SubscriptionTier.objects.filter(name=tier["name"]).exists():
                SubscriptionTier.objects.create(
                    name=tier["name"],
                    description=tier["description"],
                    wallet_refill_amount=tier["wallet_refill_amount"],
                    stripe_price_id=tier["stripe_price_id"],
                    stripe_price_ids_all=tier["stripe_price_ids_all"],
                )
            else:
                existing_tier = SubscriptionTier.objects.get(name=tier["name"])
                existing_tier.description = tier["description"]
                existing_tier.wallet_refill_amount = tier["wallet_refill_amount"]
                existing_tier.stripe_price_id = tier["stripe_price_id"]
                existing_tier.stripe_price_ids_all = tier["stripe_price_ids_all"]
                existing_tier.save()
    except Exception as e:
        logger.exception(
            f"WARNING : Wasn't able to update the entries in \
               subscription tier table: {str(e)}"
        )


def insert_api_call_type_entries(api_call_type_entries):
    try:
        for call_type in api_call_type_entries:
            if not APICallType.objects.filter(name=call_type["name"]).exists():
                APICallType.objects.create(
                    name=call_type["name"], description=call_type["description"]
                )
            else:
                existing_call_type = APICallType.objects.get(name=call_type["name"])
                existing_call_type.description = call_type["description"]
                existing_call_type.save()
    except Exception as e:
        logger.exception(
            f"WARNING : Wasn't able to update the entries in \
               api call type table: {str(e)}"
        )


def insert_resource_type_entries(resource_type_entries):
    try:
        for resource in resource_type_entries:
            if not ResourceType.objects.filter(name=resource["name"]).exists():
                ResourceType.objects.create(
                    name=resource["name"], description=resource["description"]
                )
            else:
                existing_resource = ResourceType.objects.get(name=resource["name"])
                existing_resource.description = resource["description"]
                existing_resource.save()
    except Exception as e:
        logger.exception(
            f"WARNING : Wasn't able to update the entries in \
               resource type table: {str(e)}"
        )


def insert_rate_limit_entries(rate_limit_entries):
    try:
        for limit in rate_limit_entries:
            api_call_type = APICallType.objects.get(name=limit["api_call_type"])
            subscription_tier = SubscriptionTier.objects.get(
                name=limit["subscription_tier"]
            )
            if not RateLimit.objects.filter(
                api_call_type=api_call_type,
                subscription_tier=subscription_tier,
                organization=None,
            ).exists():
                RateLimit.objects.create(
                    api_call_type=api_call_type,
                    subscription_tier=subscription_tier,
                    minute_limit=limit["minute_limit"],
                    hour_limit=limit["hour_limit"],
                    day_limit=limit["day_limit"],
                    month_limit=limit["month_limit"],
                )
            else:
                existing_limit = RateLimit.objects.filter(
                    api_call_type=api_call_type,
                    subscription_tier=subscription_tier,
                    organization=None,
                ).first()
                existing_limit.minute_limit = limit["minute_limit"]
                existing_limit.hour_limit = limit["hour_limit"]
                existing_limit.day_limit = limit["day_limit"]
                existing_limit.month_limit = limit["month_limit"]
                existing_limit.save()
    except Exception as e:
        from django.db.utils import IntegrityError

        # Foreign key violations during teardown/cleanup are expected and can be ignored
        if isinstance(e, IntegrityError) and "foreign key constraint" in str(e).lower():
            logger.debug(
                f"Skipping rate limit update due to foreign key constraint (likely during cleanup): {str(e)}"
            )
        else:
            logger.exception(
                f"WARNING : Wasn't able to update the entries in \
                   rate limit table: {str(e)}"
            )


def insert_resource_limit_entries(resource_limit_entries):
    try:
        for limit in resource_limit_entries:
            resource_type = ResourceType.objects.get(name=limit["resource_type"])
            subscription_tier = SubscriptionTier.objects.get(
                name=limit["subscription_tier"]
            )
            if not ResourceLimits.objects.filter(
                resource_type=resource_type,
                subscription_tier=subscription_tier,
                organization=None,
            ).exists():
                ResourceLimits.objects.create(
                    resource_type=resource_type,
                    subscription_tier=subscription_tier,
                    limit=limit["limit"],
                )
            else:
                existing_limit = ResourceLimits.objects.filter(
                    resource_type=resource_type,
                    subscription_tier=subscription_tier,
                    organization=None,
                ).first()
                existing_limit.limit = limit["limit"]
                existing_limit.save()
    except Exception as e:
        from django.db.utils import IntegrityError

        # Foreign key violations during teardown/cleanup are expected and can be ignored
        if isinstance(e, IntegrityError) and "foreign key constraint" in str(e).lower():
            logger.debug(
                f"Skipping resource limit update due to foreign key constraint (likely during cleanup): {str(e)}"
            )
        else:
            logger.exception(
                f"WARNING : Wasn't able to update the entries in \
                   resource limit table: {str(e)}"
            )


def insert_pricing_entries(pricing_entries):
    try:
        for price in pricing_entries:
            api_call_type = APICallType.objects.get(name=price["api_call_type"])
            if not Pricing.objects.filter(
                api_call_type=api_call_type, organization=None
            ).exists():
                Pricing.objects.create(
                    api_call_type=api_call_type, price_per_call=price["price_per_call"]
                )
            else:
                existing_price = Pricing.objects.filter(
                    api_call_type=api_call_type, organization=None
                ).first()
                existing_price.price_per_call = price["price_per_call"]
                existing_price.save()
    except Exception as e:
        from django.db.utils import IntegrityError

        # Foreign key violations during teardown/cleanup are expected and can be ignored
        if isinstance(e, IntegrityError) and "foreign key constraint" in str(e).lower():
            logger.debug(
                f"Skipping pricing update due to foreign key constraint (likely during cleanup): {str(e)}"
            )
        else:
            logger.exception(
                f"WARNING : Wasn't able to update the entries in \
                   pricing table: {str(e)}"
            )


def create_organization_subscription_if_not_exists(organization):
    organization_subscription = OrganizationSubscription.objects.filter(
        organization=organization
    ).first()
    if organization_subscription is None:
        # This is a new organization
        wallet_refill_amount = SubscriptionTier.objects.get(
            name=SubscriptionTierChoices.FREE.value
        ).wallet_refill_amount
        subscription_tier = SubscriptionTier.objects.get(
            name=SubscriptionTierChoices.FREE.value
        )
        (
            organization_subscription,
            created,
        ) = OrganizationSubscription.all_objects.update_or_create(
            organization=organization,
            defaults={
                "subscription_tier": subscription_tier,
                "wallet_refill_amount": wallet_refill_amount,
                "wallet_balance": wallet_refill_amount,
                "deleted": False,
            },
        )
    return organization_subscription


def check_if_api_call_is_rate_limited(organization, api_call_type):
    # get the subscription tier of the organization
    try:
        create_organization_subscription_if_not_exists(organization)
        organization_subscription = OrganizationSubscription.objects.filter(
            organization=organization
        ).first()

        # RateLimit.*_limit is PositiveIntegerField, so unlimited can't be stored — bypass for plans billing.yaml grants -1.
        if organization_subscription.plan in (
            PlanChoices.ENTERPRISE.value,
            PlanChoices.CUSTOM.value,
        ):
            return False, {}

        subscription_tier = organization_subscription.subscription_tier
        eval_calls = [
            APICallTypeChoices.DATASET_EVALUATION.value,
            APICallTypeChoices.DATASET_PROTECT.value,
            APICallTypeChoices.AUTO_ANNOTATION.value,
            APICallTypeChoices.EXPERIMENT_EVALUATION.value,
            APICallTypeChoices.OPTIMISATION_EVALUATION.value,
            APICallTypeChoices.PROMPT_BENCH.value,
            # For synthetic data generation
            APICallTypeChoices.SYNTHETIC_DATA_GENERATION.value,
            # For error localizer
            APICallTypeChoices.ERROR_LOCALIZER.value,
        ]

        # get the rate limit for the api call type and subscription tier
        # note that for evals, we constraint rate limit on all 3 evals combined
        # be it for stand alone evals, experiment evals or optimisation evals
        # so whichever api call we get in these 3, we will constraint rate limit on all 3
        # so we reset api_call_type to dataset_evaluation, if it is any of the 3
        call_type = api_call_type
        if api_call_type in eval_calls:
            api_call_type = APICallTypeChoices.DATASET_EVALUATION.value

        api_call_type_instance = APICallType.objects.get(name=api_call_type)

        is_custom_tier = (
            organization_subscription.subscription_tier.name
            == SubscriptionTierChoices.CUSTOM.value
        )

        # get org-specific OR tier default, ordered so org-specific comes first
        rate_limit_query = Q(
            api_call_type=api_call_type_instance,
            subscription_tier=subscription_tier,
            organization=None,
        )
        if is_custom_tier:
            rate_limit_query |= Q(
                api_call_type=api_call_type_instance,
                organization=organization,
            )

        rate_limit = (
            RateLimit.objects.filter(rate_limit_query)
            .order_by(F("organization").asc(nulls_last=True))
            .first()
        )

        if rate_limit is None:
            logger.warning(
                "no_rate_limit_found",
                api_call_type=api_call_type,
                subscription_tier=subscription_tier.name,
                organization_id=str(organization.id),
            )
            return False, {}

        minute_limit = rate_limit.minute_limit
        hour_limit = rate_limit.hour_limit
        day_limit = rate_limit.day_limit
        month_limit = rate_limit.month_limit

        logger.info(
            f"API CALL LIMITS FOUND: {minute_limit} {hour_limit} {day_limit} {month_limit}"
        )
        if api_call_type not in eval_calls:
            # get the number of api calls made in the last minute, hour, day, month
            api_calls_made_in_last_minute = APICallLog.objects.filter(
                api_call_type=api_call_type_instance,
                organization=organization,
                created_at__gte=timezone.now() - timedelta(minutes=1),
                status__in=[
                    APICallStatusChoices.SUCCESS.value,
                    APICallStatusChoices.PROCESSING.value,
                ],
            ).count()
            api_calls_made_in_last_hour = APICallLog.objects.filter(
                api_call_type=api_call_type_instance,
                organization=organization,
                created_at__gte=timezone.now() - timedelta(hours=1),
                status__in=[
                    APICallStatusChoices.SUCCESS.value,
                    APICallStatusChoices.PROCESSING.value,
                ],
            ).count()
            api_calls_made_in_last_day = APICallLog.objects.filter(
                api_call_type=api_call_type_instance,
                organization=organization,
                created_at__gte=timezone.now() - timedelta(days=1),
                status__in=[
                    APICallStatusChoices.SUCCESS.value,
                    APICallStatusChoices.PROCESSING.value,
                ],
            ).count()
            api_calls_made_in_last_month = APICallLog.objects.filter(
                api_call_type=api_call_type_instance,
                organization=organization,
                created_at__gte=timezone.now() - timedelta(days=30),
                status__in=[
                    APICallStatusChoices.SUCCESS.value,
                    APICallStatusChoices.PROCESSING.value,
                ],
            ).count()
        else:
            # api_call made in last miniute is all api call s whose instance are any of the 3
            api_calls_made_in_last_minute = APICallLog.objects.filter(
                api_call_type__name__in=eval_calls,
                organization=organization,
                created_at__gte=timezone.now() - timedelta(minutes=1),
                status__in=[
                    APICallStatusChoices.SUCCESS.value,
                    APICallStatusChoices.PROCESSING.value,
                ],
            ).count()
            api_calls_made_in_last_hour = APICallLog.objects.filter(
                api_call_type__name__in=eval_calls,
                organization=organization,
                created_at__gte=timezone.now() - timedelta(hours=1),
                status__in=[
                    APICallStatusChoices.SUCCESS.value,
                    APICallStatusChoices.PROCESSING.value,
                ],
            ).count()
            api_calls_made_in_last_day = APICallLog.objects.filter(
                api_call_type__name__in=eval_calls,
                organization=organization,
                created_at__gte=timezone.now() - timedelta(days=1),
                status__in=[
                    APICallStatusChoices.SUCCESS.value,
                    APICallStatusChoices.PROCESSING.value,
                ],
            ).count()
            api_calls_made_in_last_month = APICallLog.objects.filter(
                api_call_type__name__in=eval_calls,
                organization=organization,
                created_at__gte=timezone.now() - timedelta(days=30),
                status__in=[
                    APICallStatusChoices.SUCCESS.value,
                    APICallStatusChoices.PROCESSING.value,
                ],
            ).count()

        config = {}
        config["api_call_type"] = api_call_type
        # check if the number of api calls made in the last minute, hour, day,
        # month is greater than the limit

        if api_calls_made_in_last_minute >= minute_limit:
            config["time"] = "minutely"
            config["title"] = " ".join(call_type.split("_"))
            config["duration"] = "minute"
            return True, config
        if api_calls_made_in_last_hour >= hour_limit:
            config["time"] = "hourly"
            config["title"] = " ".join(call_type.split("_"))
            config["duration"] = "hour"
            config["limit"] = hour_limit
            return True, config
        if api_calls_made_in_last_day >= day_limit:
            config["time"] = "daily"
            config["title"] = " ".join(call_type.split("_"))
            config["duration"] = "day"
            config["limit"] = day_limit
            return True, config
        if api_calls_made_in_last_month >= month_limit:
            config["time"] = "monthly"
            config["title"] = " ".join(call_type.split("_"))
            config["duration"] = "month"
            config["limit"] = month_limit
            return True, config

        return False, {}
    except Exception as e:
        logger.exception(f"Error checking if api call is rate limited: {str(e)}")
        # print the error traceback

        return False, {}


def check_if_api_call_is_billing_api_call(api_call_type, config=None):
    if config is None:
        config = {}
    if api_call_type == APICallTypeChoices.DATASET_RUN_PROMPT.value:
        return False
    elif (
        api_call_type == APICallTypeChoices.DATASET_EVALUATION.value
        or api_call_type == APICallTypeChoices.EXPERIMENT_EVALUATION.value
        or api_call_type == APICallTypeChoices.DATASET_PROTECT.value
        or api_call_type == APICallTypeChoices.OPTIMISATION_EVALUATION.value
    ):
        if config.get("is_futureagi_eval"):
            return True
        return False
    elif api_call_type == APICallTypeChoices.SYNTHETIC_DATA_GENERATION.value:
        return True
    elif api_call_type == APICallTypeChoices.ERROR_LOCALIZER.value:
        return True
    elif api_call_type == APICallTypeChoices.DATASET_OPTIMIZATION.value:
        return True
    # elif api_call_type == APICallTypeChoices.DATASET_LOGGING.value:
    #     return True
    elif api_call_type == APICallTypeChoices.AUTO_ANNOTATION.value:
        return True
    elif api_call_type == APICallTypeChoices.USER_ADD.value:
        return True
    elif api_call_type == APICallTypeChoices.OBSERVE_ADD.value:
        return True
    elif api_call_type == APICallTypeChoices.PROTOTYPE_ADD.value:
        return True
    elif api_call_type == APICallTypeChoices.PROMPT_BENCH.value:
        return True
    elif api_call_type == APICallTypeChoices.TRACE_ERROR_ANALYSIS.value:
        return True
    elif api_call_type in EVALUATOR_CALLS:
        return True
    return False


def log_api_call(
    api_call_type,
    organization,
    config=None,
    source=None,
    source_id=None,
    workspace=None,
):
    try:
        if config is None:
            config = {}
        api_call_type_instance = APICallType.objects.get(name=api_call_type)
        input_tokens = config.get("input_tokens", 0)

        is_api_call_rate_limited, detail = check_if_api_call_is_rate_limited(
            organization, api_call_type
        )
        if is_api_call_rate_limited:
            config.update(detail)
            api_call_log_row = APICallLog.objects.create(
                api_call_type=api_call_type_instance,
                organization=organization,
                cost=0,
                status=APICallStatusChoices.RATE_LIMITED.value,
                deducted_cost=0,
                config=json.dumps(config, default=str),
                reference_id=config.get("reference_id", ""),
                input_token_count=int(input_tokens),
                source=source,
                source_id=source_id,
                workspace=workspace,
            )
            message = WebSocketMessage(
                alert_title=get_error_message("API_ALERT_TITLE").format(
                    config.get("time").capitalize(),
                    config.get("title").capitalize(),
                ),
                alert_description=get_error_message("API_ALERT_TEMPLATE").format(
                    config.get("time"),
                    config.get("title"),
                    config.get("duration"),
                ),
                subscription_title=get_error_message("API_SUBS_TITLE").format(
                    config.get("duration"),
                ),
                subscription_description=get_error_message("API_SUBS_TEMPLATE"),
            )
            logger.info(f"FORMATTED MSG IS:{asdict(message)}")
            call_websocket(organization_id=organization.id, message=asdict(message))

        else:
            api_call_log_row = APICallLog.objects.create(
                api_call_type=api_call_type_instance,
                organization=organization,
                cost=0,
                status=APICallStatusChoices.PROCESSING.value,
                deducted_cost=0,
                config=json.dumps(config, default=str),
                reference_id=config.get("reference_id", ""),
                input_token_count=int(input_tokens),
                source=source,
                source_id=source_id,
                workspace=workspace,
            )

        return api_call_log_row

    except Exception as e:
        logger.exception(f"Error logging api call: {str(e)}")
        return None


# Function to log api call request in APICallLog table
def log_and_deduct_cost_for_api_request(
    organization,
    api_call_type,
    config=None,
    source=None,
    source_id=None,
    workspace=None,
):
    if config is None:
        config = {}

    # Ensure source and source_id are strings for CharField compatibility
    if source is not None:
        source = str(source)
    if source_id is not None:
        source_id = str(source_id)

    try:
        has_audio, audio_second = check_audio_in_mappings(config)
        api_call_type_instance = APICallType.objects.get(name=api_call_type)

        if has_audio:
            input_tokens = ceil(audio_second) * 40
        else:
            input_tokens = config.get("input_tokens", 0)

        is_api_call_rate_limited, detail = check_if_api_call_is_rate_limited(
            organization, api_call_type
        )

        if is_api_call_rate_limited:
            config.update(detail)
            api_call_log_row = APICallLog.objects.create(
                api_call_type=api_call_type_instance,
                organization=organization,
                cost=0,
                status=APICallStatusChoices.RATE_LIMITED.value,
                deducted_cost=0,
                config=json.dumps(config, default=str),
                reference_id=config.get("reference_id", ""),
                input_token_count=int(input_tokens),
                source=source,
                source_id=source_id,
                workspace=workspace,
            )
            message = WebSocketMessage(
                alert_title=get_error_message("API_ALERT_TITLE").format(
                    config.get("time").capitalize(),
                    config.get("title").capitalize(),
                ),
                alert_description=get_error_message("API_ALERT_TEMPLATE").format(
                    config.get("time"),
                    config.get("title"),
                    config.get("duration"),
                ),
                subscription_title=get_error_message("API_SUBS_TITLE").format(
                    config.get("duration"),
                ),
                subscription_description=get_error_message("API_SUBS_TEMPLATE"),
            )
            logger.info(f"FORMATTED MSG IS:{asdict(message)}")
            call_websocket(organization_id=organization.id, message=asdict(message))
            return api_call_log_row

        is_billing_api_call = check_if_api_call_is_billing_api_call(
            api_call_type, config
        )

        if not is_billing_api_call:
            api_call_log_row = APICallLog.objects.create(
                api_call_type=api_call_type_instance,
                organization=organization,
                cost=0,
                status=APICallStatusChoices.PROCESSING.value,
                deducted_cost=0,
                config=json.dumps(config, default=str),
                reference_id=config.get("reference_id", ""),
                input_token_count=int(input_tokens),
                source=source,
                source_id=source_id,
                workspace=workspace,
            )
            return api_call_log_row

        return deduct_cost_for_request(
            organization,
            api_call_type,
            api_call_type_instance,
            input_tokens,
            config,
            source,
            source_id,
            workspace,
        )
    except Exception as e:
        traceback.print_exc()
        logger.exception(f"Error logging and deducting cost for api call: {str(e)}")
        return None


# Function to log resource addition in APICallLog table
def log_and_deduct_cost_for_resource_request(
    organization, api_call_type, config=None, sdk_source=False, workspace=None
):
    create_organization_subscription_if_not_exists(organization)
    if config is None:
        config = {}

    try:
        # Validate api_call_type is resource-related
        valid_resource_types = [
            APICallTypeChoices.USER_ADD.value,
            APICallTypeChoices.OBSERVE_ADD.value,
            APICallTypeChoices.PROTOTYPE_ADD.value,
            APICallTypeChoices.DATASET_ADD.value,
            APICallTypeChoices.ROW_ADD.value,
            APICallTypeChoices.KNOWLEDGE_BASE.value,
        ]
        if api_call_type not in valid_resource_types:
            logger.error(f"Invalid resource api_call_type: {api_call_type}")
            return None

        # Validate required config parameters
        if (
            api_call_type == APICallTypeChoices.ROW_ADD.value
            and "total_rows" not in config
        ):
            logger.error("Missing total_rows in config for ROW_ADD")
            return None

        api_call_type_instance = APICallType.objects.get(name=api_call_type)
        input_tokens = config.get("input_tokens", 0)

        with transaction.atomic():
            request_status = True
            detail = {}

            # Check if resource creation is allowed
            match api_call_type:
                case APICallTypeChoices.USER_ADD.value:
                    request_status, detail = check_if_user_creation_is_allowed(
                        organization, config
                    )
                case APICallTypeChoices.OBSERVE_ADD.value:
                    request_status, detail = check_if_observe_creation_is_allowed(
                        organization, config.get("existing", False)
                    )
                case APICallTypeChoices.PROTOTYPE_ADD.value:
                    request_status, detail = check_if_prototype_creation_is_allowed(
                        organization, config.get("existing", False)
                    )
                case APICallTypeChoices.DATASET_ADD.value:
                    request_status, detail = check_if_dataset_creation_is_allowed(
                        organization, config
                    )
                case APICallTypeChoices.ROW_ADD.value:
                    request_status, detail = check_if_row_limit_reached(
                        organization, config.get("total_rows")
                    )
                case APICallTypeChoices.KNOWLEDGE_BASE.value:
                    request_status, detail = check_if_kb_creation_is_allowed(
                        organization
                    )
                case _:
                    logger.error(f"Unhandled api_call_type: {api_call_type}")
                    return None

            is_billing_api_call = check_if_api_call_is_billing_api_call(
                api_call_type, config
            )

            # Log and send to websocket if resource limited and not billable
            if not request_status and not is_billing_api_call:
                config.update(detail)
                api_call_log_row = APICallLog.objects.create(
                    api_call_type=api_call_type_instance,
                    organization=organization,
                    cost=0,
                    status=APICallStatusChoices.RESOURCE_LIMIT.value,
                    deducted_cost=0,
                    config=json.dumps(config, default=str),
                    reference_id=config.get("reference_id", ""),
                    input_token_count=int(input_tokens),
                    workspace=workspace,
                )

                # Send websocket message about resource limit
                try:
                    message = WebSocketMessage(
                        alert_title=get_error_message("RESOURCE_ALERT_TITLE").format(
                            config.get("resource_name", "").capitalize()
                        ),
                        alert_description=get_error_message(
                            "RESOURCE_ALERT_TEMPLATE"
                        ).format(
                            config.get("limit", 0), config.get("resource_name", "")
                        ),
                        subscription_title=get_error_message(
                            "RESOURCE_SUBS_TITLE"
                        ).format(config.get("resource_name", "")),
                        subscription_description=get_error_message(
                            "RESOURCE_SUBS_TEMPLATE"
                        ).format(config.get("resource_name", "")),
                    )
                    if not sdk_source:
                        call_websocket(str(organization.id), message=asdict(message))
                except Exception as e:
                    logger.error(f"Failed to send websocket message: {str(e)}")

                return api_call_log_row

            # If not resource limited but billable or if not resource limited and not billable, add entry directly
            if not is_billing_api_call or not detail:
                api_call_log_row = APICallLog.objects.create(
                    api_call_type=api_call_type_instance,
                    organization=organization,
                    cost=0,
                    status=APICallStatusChoices.PROCESSING.value,
                    deducted_cost=0,
                    config=json.dumps(config, default=str),
                    reference_id=config.get("reference_id", ""),
                    input_token_count=int(input_tokens),
                    workspace=workspace,
                )
                return api_call_log_row

            # If resource limited and is billable
            config.update(detail)
            return deduct_cost_for_request(
                organization,
                api_call_type,
                api_call_type_instance,
                input_tokens,
                config,
                workspace=workspace,
            )

    except APICallType.DoesNotExist:
        logger.exception(f"Invalid api_call_type: {api_call_type}")
        return None
    except Exception as e:
        traceback.print_exc()
        logger.exception(
            f"Error logging and deducting cost for resource request {api_call_type}: {str(e)}"
        )
        return None
    else:
        # Emit usage event for successful resource creation (storage metering).
        # Only covers dataset/row/KB — other resource types (USER_ADD, OBSERVE_ADD,
        # PROTOTYPE_ADD) are not storage-metered and are intentionally excluded.
        try:
            # dataset_creation / dataset_row_add are not yet in billing.yaml —
            # the consumer will skip them. Kept for tracking; will be billed later.
            # knowledge_base_upload → BillingEventType.KB_STORAGE (billed).
            from ee.usage.schemas.event_types import BillingEventType
            from ee.usage.schemas.events import UsageEvent
            from ee.usage.services.emitter import emit

            _resource_event_type_map = {
                APICallTypeChoices.DATASET_ADD.value: "dataset_creation",
                APICallTypeChoices.ROW_ADD.value: "dataset_row_add",
                APICallTypeChoices.KNOWLEDGE_BASE.value: BillingEventType.KB_STORAGE,
            }
            event_type = _resource_event_type_map.get(api_call_type)
            if event_type and organization:
                total_rows = config.get("total_rows", 0) if config else 0
                emit(
                    UsageEvent(
                        org_id=str(organization.id),
                        event_type=event_type,
                        amount=max(total_rows, 1),
                        properties={
                            "source": api_call_type,
                            "total_rows": total_rows,
                        },
                    )
                )
        except Exception:
            logger.debug("emit_resource_event_failed", api_call_type=api_call_type)


# Function to deduct cost for api call or resource request
def deduct_cost_for_request(
    organization,
    api_call_type,
    api_call_type_instance,
    input_tokens,
    config,
    source=None,
    source_id=None,
    workspace=None,
):
    # Ensure source and source_id are strings for CharField compatibility
    if source is not None:
        source = str(source)
    if source_id is not None:
        source_id = str(source_id)

    try:
        organization_subscription = OrganizationSubscription.objects.filter(
            organization=organization
        ).first()
        wallet_balance = organization_subscription.wallet_balance

        if api_call_type == "voice_call":
            # Always compute the duration-based cost from the Pricing table
            # (org-specific or global rate).  This serves as the minimum
            # charge (ceiling) and as the sole cost when no component-based
            # cost is available (VAPI / legacy calls).
            duration_minutes = Decimal(config.get("duration_minutes", 0))
            voice_call_pricing = None
            if (
                organization_subscription.subscription_tier.name
                == SubscriptionTierChoices.CUSTOM.value
            ):
                voice_call_pricing = Pricing.objects.filter(
                    api_call_type=api_call_type_instance,
                    organization=organization,
                ).first()
            if voice_call_pricing is None:
                voice_call_pricing = Pricing.objects.filter(
                    api_call_type=api_call_type_instance, organization=None
                ).first()
            if voice_call_pricing is None:
                logger.info("No pricing found for voice call; defaulting cost to 0")
                cost_per_minute = Decimal("0.15")
            else:
                logger.info(
                    f"Voice call pricing found: {voice_call_pricing.price_per_call}"
                )
                cost_per_minute = voice_call_pricing.price_per_call or Decimal("0.15")
                if not isinstance(cost_per_minute, Decimal):
                    cost_per_minute = Decimal(str(cost_per_minute))
            duration_cost = duration_minutes * cost_per_minute

            if "total_cost" in config:
                # Component-based cost (STT + LLM + TTS + storage).
                # Use the *higher* of component cost vs duration-based cost
                # so we never charge less than the per-minute Pricing rate.
                component_cost = Decimal(str(config["total_cost"]))
                cost = max(component_cost, duration_cost)
                logger.info(
                    f"Voice call cost: component=${component_cost}, "
                    f"duration=${duration_cost} (ceiling), charged=${cost}"
                )
            else:
                cost = duration_cost
                logger.info(
                    f"Voice call cost: {duration_minutes} min × "
                    f"${cost_per_minute} = ${cost}"
                )
            api_call_log_row = APICallLog.objects.create(
                api_call_type=api_call_type_instance,
                organization=organization,
                cost=float(cost),
                status=APICallStatusChoices.SUCCESS.value,
                deducted_cost=float(cost),
                input_token_count=int(input_tokens),
                config=json.dumps(config, default=str),
                reference_id=config.get("reference_id", ""),
                source=source,
                source_id=config.get("reference_id", ""),
                workspace=workspace,
            )
            # --- Legacy wallet deduction (deprecated — postpaid model) ---
            # if api_call_log_row is not None and api_call_log_row.deducted_cost > 0:
            #     with transaction.atomic():
            #         OrganizationSubscription.objects.filter(
            #             organization=organization
            #         ).update(
            #             wallet_balance=F("wallet_balance")
            #             - api_call_log_row.deducted_cost
            #         )

            return api_call_log_row

        if api_call_type == "text_call":
            no_of_agent_turns = config.get("no_of_agent_turns", 0)

            chat_pricing = fetch_pricing_details_for_api_call_type(
                api_call_type_instance, organization
            )

            if chat_pricing is None:
                logger.info(
                    "No pricing found for Text call; defaulting cost to 0.005 per turn"
                )
                cost_per_turn = Decimal("0.005")
            else:
                logger.info(f"Chat call pricing found: {chat_pricing.price_per_call}")
                cost_per_turn = chat_pricing.price_per_call or Decimal("0.005")
                if not isinstance(cost_per_turn, Decimal):
                    cost_per_turn = Decimal(str(cost_per_turn))
            cost = no_of_agent_turns * cost_per_turn
            logger.info(
                f"Chat call cost calculation: {no_of_agent_turns} turns * ${cost_per_turn} = ${cost}"
            )
            api_call_log_row = APICallLog.objects.create(
                api_call_type=api_call_type_instance,
                organization=organization,
                cost=cost,
                status=APICallStatusChoices.SUCCESS.value,
                deducted_cost=cost,
                input_token_count=int(input_tokens),
                config=json.dumps(config, default=str),
                reference_id=config.get("reference_id", ""),
                source=source,
                source_id=config.get("reference_id", ""),
                workspace=workspace,
            )
            # --- Legacy wallet deduction (deprecated — postpaid model) ---
            # if api_call_log_row is not None and api_call_log_row.deducted_cost > 0:
            #     with transaction.atomic():
            #         OrganizationSubscription.objects.filter(
            #             organization=organization
            #         ).update(
            #             wallet_balance=F("wallet_balance")
            #             - api_call_log_row.deducted_cost
            #         )

            return api_call_log_row

        cost = None
        if api_call_type == "user_add":
            pricing = None
            if (
                organization_subscription.subscription_tier.name
                == SubscriptionTierChoices.CUSTOM.value
            ):
                pricing = Pricing.objects.filter(
                    api_call_type=api_call_type_instance, organization=organization
                ).first()
            if pricing is None:
                pricing = Pricing.objects.filter(
                    api_call_type=api_call_type_instance, organization=None
                ).first()
            extra_users = config["extra_users"]
            if pricing is None:
                logger.info("No pricing found; defaulting cost to 0")
                cost = 0
            else:
                cost = pricing.price_per_call * extra_users
        elif api_call_type == "observe_add" or api_call_type == "prototype_add":
            source = (
                "observe_add" if api_call_type == "observe_add" else "prototype_add"
            )
            source_id = config.get("reference_id", "")
            extra_traces = config.get("extra_traces", 0)
            sub_tier = organization_subscription.subscription_tier.name

            if sub_tier == SubscriptionTierChoices.FREE.value:
                unit_price = 0.001  # $10 per 10K extra traces
            elif sub_tier == SubscriptionTierChoices.BUSINESS.value:
                unit_price = 0.0001  # $10 per 100K extra traces
            else:
                unit_price = 0.0  # For custom, extra traces are free
            cost = unit_price * extra_traces
        elif api_call_type in [
            "dataset_evaluation",
            "optimisation_evaluation",
            "experiment_evaluation",
            "turing_large_evaluator",
            "turing_small_evaluator",
            "turing_flash_evaluator",
            "protect_evaluator",
            "protect_flash_evaluator",
        ]:
            pricing = None
            cost = 0

            if (
                organization_subscription.subscription_tier.name
                == SubscriptionTierChoices.CUSTOM.value
            ):
                pricing = Pricing.objects.filter(
                    api_call_type=api_call_type_instance, organization=organization
                ).first()
            if pricing is None:
                pricing = Pricing.objects.filter(
                    api_call_type=api_call_type_instance, organization=None
                ).first()
            cost += pricing.price_per_call

        elif (
            api_call_type == "error_localizer"
            or api_call_type == "trace_error_analysis"
        ):
            pricing = None
            if (
                organization_subscription.subscription_tier.name
                == SubscriptionTierChoices.CUSTOM.value
            ):
                pricing = Pricing.objects.filter(
                    api_call_type=api_call_type_instance, organization=organization
                ).first()
            if pricing is None:
                pricing = Pricing.objects.filter(
                    api_call_type=api_call_type_instance, organization=None
                ).first()
            cost = pricing.price_per_call

        else:
            pricing = None
            if (
                organization_subscription.subscription_tier.name
                == SubscriptionTierChoices.CUSTOM.value
            ):
                pricing = Pricing.objects.filter(
                    api_call_type=api_call_type_instance, organization=organization
                ).first()
            if pricing is None:
                pricing = Pricing.objects.filter(
                    api_call_type=api_call_type_instance, organization=None
                ).first()
            cost = pricing.price_per_call * input_tokens

        # Wallet-based billing is deprecated. All plans (including free) use
        # the postpaid usage pipeline: emit() → Redis → UsageSummary → Stripe.
        # Free tier limits are enforced by check_usage() in metering.py.
        # Always allow the action; usage tracking handles billing.
        status = APICallStatusChoices.PROCESSING.value
        deducted_cost = 0

        # --- Legacy wallet check (deprecated) ---
        # is_active_aws_customer = False
        # try:
        #     is_active_aws_customer = AWSMarketplaceCustomer.objects.get(
        #         organization=organization,
        #         subscription_status=SubscriptionStatus.SUBSCRIBE_SUCCESS,
        #     )
        # except AWSMarketplaceCustomer.DoesNotExist:
        #     is_active_aws_customer = False
        #
        # if is_active_aws_customer:
        #     status = APICallStatusChoices.PROCESSING.value
        #     deducted_cost = cost
        # elif wallet_balance < cost:
        #     status = APICallStatusChoices.INSUFFICIENT_CREDITS.value
        #     deducted_cost = 0
        # elif wallet_balance >= cost:
        #     status = APICallStatusChoices.PROCESSING.value
        #     deducted_cost = cost
        # --- End legacy wallet check ---

        if api_call_type in [
            APICallTypeChoices.OBSERVE_ADD.value,
            APICallTypeChoices.USER_ADD.value,
            APICallTypeChoices.PROTOTYPE_ADD.value,
        ]:
            if "insufficient" in status or config.get("type", None):
                status = APICallStatusChoices.RESOURCE_LIMIT.value
                logger.info(f"CONFIG FOUND: {config}")
                message = WebSocketMessage(
                    alert_title=get_error_message("RESOURCE_ALERT_TITLE").format(
                        config.get("resource_name").capitalize()
                    ),
                    alert_description=get_error_message(
                        "RESOURCE_ALERT_TEMPLATE"
                    ).format(config.get("limit"), config.get("resource_name")),
                    subscription_title=get_error_message("RESOURCE_SUBS_TITLE").format(
                        config.get("resource_name")
                    ),
                    subscription_description=get_error_message(
                        "RESOURCE_SUBS_TEMPLATE"
                    ).format(config.get("resource_name")),
                )
                call_websocket(str(organization.id), message=asdict(message))

        api_call_log_row = APICallLog.objects.create(
            api_call_type=api_call_type_instance,
            organization=organization,
            cost=float(cost),
            status=status,
            deducted_cost=float(deducted_cost),
            input_token_count=int(input_tokens),
            config=json.dumps(config, default=str),
            reference_id=config.get("reference_id", ""),
            source=source,
            source_id=source_id,
            workspace=workspace,
        )

        # --- Legacy wallet deduction + AWS metering (deprecated) ---
        # if is_active_aws_customer:
        #     success = aws_marketplace_metering.meter_api_calls(
        #         str(is_active_aws_customer.id), api_call_log_row.deducted_cost
        #     )
        #     if success:
        #         return api_call_log_row
        #     else:
        #         return None
        #
        # if api_call_log_row is not None and api_call_log_row.deducted_cost > 0:
        #     with transaction.atomic():
        #         OrganizationSubscription.objects.filter(
        #             organization=organization
        #         ).update(
        #             wallet_balance=F("wallet_balance") - api_call_log_row.deducted_cost
        #         )
        # --- End legacy ---

        return api_call_log_row
    except Exception as e:
        logger.exception(f"Error deducting cost for request {api_call_type}: {str(e)}")
        return None


def refund_cost_for_api_call(api_call_log_row, config=None):
    if config is None:
        config = {}
    try:
        if not api_call_log_row or api_call_log_row.deducted_cost <= 0:
            return None

        api_call_type_instance = APICallType.objects.filter(
            name=APICallTypeChoices.WALLET_REFUND.value
        ).first()
        refund_api_call_log_row = APICallLog.objects.create(
            api_call_type=api_call_type_instance,
            organization=api_call_log_row.organization,
            cost=float(api_call_log_row.deducted_cost),
            status=APICallStatusChoices.SUCCESS.value,
            deducted_cost=0,
            refund_parent_id=api_call_log_row.id,
            reference_id=api_call_log_row.reference_id,
            config=json.dumps(config, default=str),
        )

        OrganizationSubscription.objects.filter(
            organization=api_call_log_row.organization
        ).first()
        # if organization_subscription:
        #     print(f"Wallet balance before refund: {organization_subscription.wallet_balance}")
        #     organization_subscription.wallet_balance += api_call_log_row.deducted_cost
        #     organization_subscription.save()
        #     print(f"Wallet balance after refund: {organization_subscription.wallet_balance}")
        # else:
        #     print(f"Organization subscription not found for organization: {api_call_log_row.organization}")

        # --- Legacy wallet refund (deprecated — postpaid model) ---
        # with transaction.atomic():
        #     OrganizationSubscription.objects.filter(
        #         organization=api_call_log_row.organization
        #     ).update(
        #         wallet_balance=F("wallet_balance") + api_call_log_row.deducted_cost
        #     )

        return refund_api_call_log_row
    except Exception as e:
        logger.exception(f"Error refunding cost for api call: {str(e)}")
        return None


def count_image_tokens(image_url):
    # we observed that in gpt4o, it either charges 479 tokens or 876 tokens
    # if the image is small, however small it is, if it is under 512x512,
    # will charge 479 tokens, and beyond 512x512 area, it will charge 876 tokens
    # and however big the image is, it wont go beyond 876 tokens

    # to keep it simple, we made a linear relationship so as to charge a 512x512 image with
    # with 512 tokens, and a 768x768 image with 768 tokens and so on
    # for this, we calculate the area of the image and then take the square root of the area
    # and charge it with those many tokens

    # also the requests.get is throwing a lot of errors, and in such cases
    # we are charging them with 876 tokens which is the maximum tokens for
    # gpt4o, we are charging a minium of 479 tokens for any image

    tokens = 876

    try:
        response = requests.get(image_url, timeout=30)
        image = Image.open(BytesIO(response.content))
        image_width, image_height = image.size
        area = image_width * image_height
        tokens = math.sqrt(area)
        tokens = min(tokens, 876)
        tokens = max(tokens, 479)
    except Exception:
        logger.exception("Unable to download the image, charging with 876 tokens")
        tokens = 876
    return int(tokens)


def count_text_tokens(input_string):
    encoding = tiktoken.get_encoding("cl100k_base")
    tokens = encoding.encode(input_string)
    return len(tokens)


def count_tiktoken_tokens(input_string, input_image_urls=None):
    text_tokens = 0
    image_tokens = 0

    text_tokens = count_text_tokens(input_string)

    if input_image_urls:
        i = 1
        if isinstance(input_image_urls, str):
            input_image_urls = input_image_urls
            image_tokens += 1000

        elif isinstance(input_image_urls, list) and len(input_image_urls) > 0:
            for image_url in input_image_urls:
                image_url = image_url.strip() if image_url else ""
                if len(image_url) > 0:
                    # for now we are charging 1000 tokens for each image,
                    # irrespective of the image size
                    image_tokens += 1000
                    i += 1
                    # we can use this to charge based on the image size,
                    # we are charging 1000 tokens for each image for now as shown above
                    # image_tokens += count_image_tokens(image_url)
        else:
            raise ValueError(
                " ----- Invalid URL Type : Should be a string or a list of stings  -----"
            )

    num_tokens = text_tokens + image_tokens
    return int(num_tokens)


def create_usage_entries():
    insert_subscription_tier_entries(subscription_tier_entries)
    insert_api_call_type_entries(api_call_type_entries)
    insert_resource_type_entries(resource_type_choices)
    insert_rate_limit_entries(rate_limit_entries)
    insert_resource_limit_entries(resource_limit_entries)
    insert_pricing_entries(pricing_entries)


def check_if_user_creation_is_allowed(organization, config):
    try:
        create_organization_subscription_if_not_exists(organization)
        organization_subscription = OrganizationSubscription.objects.filter(
            organization=organization
        ).first()
        resource_limit_instance = None
        if (
            organization_subscription.subscription_tier.name
            == SubscriptionTierChoices.CUSTOM.value
        ):
            resource_limit_instance = ResourceLimits.objects.filter(
                organization=organization,
                resource_type__name=ResourceTypeChoices.USERS.value,
            ).first()
        if resource_limit_instance is None:
            resource_limit_instance = ResourceLimits.objects.filter(
                subscription_tier=organization_subscription.subscription_tier,
                resource_type__name=ResourceTypeChoices.USERS.value,
                organization=None,
            ).first()
        if resource_limit_instance is None:
            detail = {}
            detail["resource_name"] = ResourceTypeChoices.USERS.value
            detail["limit"] = 0
            detail["subscription_name"] = (
                organization_subscription.subscription_tier.name
            )
            return False, detail

        user_count = config.get("user_count", 1)
        extra_users = config.get("extra_users", 0)

        if user_count + extra_users > resource_limit_instance.limit:
            detail = {}
            detail["resource_name"] = ResourceTypeChoices.USERS.value
            detail["limit"] = resource_limit_instance.limit
            detail["subscription_name"] = (
                organization_subscription.subscription_tier.name
            )
            return False, detail
        return True, {}
    except Exception as e:
        logger.exception(f"Error checking if user creation is allowed: {str(e)}")
        detail = {}
        detail["resource_name"] = ResourceTypeChoices.USERS.value
        detail["limit"] = 0
        detail["subscription_name"] = organization_subscription.subscription_tier.name
        return False, detail


def check_if_dataset_creation_is_allowed(organization, config=None):
    from model_hub.models.develop_dataset import Dataset

    try:
        from usage.services.entitlements import Entitlements

        dataset_count = Dataset.objects.filter(
            organization=organization, source__in=["build", "observe"], deleted=False
        ).count()
        result = Entitlements.can_create(str(organization.id), "datasets", dataset_count)
        if not result.allowed:
            detail = {
                "resource_name": ResourceTypeChoices.DATASET.value,
                "limit": int(result.limit) if result.limit else 0,
            }
            return False, detail
        return True, {}
    except Exception as e:
        logger.exception(f"Error checking if dataset creation is allowed: {str(e)}")
        return True, {}


def check_if_row_limit_reached(organization, row_count):
    pass

    try:
        create_organization_subscription_if_not_exists(organization)
        organization_subscription = OrganizationSubscription.objects.filter(
            organization=organization
        ).first()
        resource_type = ResourceType.objects.filter(
            name=ResourceTypeChoices.ROWS.value
        ).first()

        resource_limit_instance = None
        if (
            organization_subscription.subscription_tier.name
            == SubscriptionTierChoices.CUSTOM.value
        ):
            resource_limit_instance = ResourceLimits.objects.filter(
                organization=organization, resource_type=resource_type
            ).first()
        if resource_limit_instance is None:
            resource_limit_instance = ResourceLimits.objects.filter(
                subscription_tier=organization_subscription.subscription_tier,
                resource_type=resource_type,
                organization=None,
            ).first()
        if resource_limit_instance is None:
            detail = {}
            detail["resource_name"] = ResourceTypeChoices.ROWS.value
            detail["limit"] = 0
            return False, detail

        logger.info(f"rows found: {row_count} > limit: {resource_limit_instance.limit}")
        if row_count >= resource_limit_instance.limit:
            detail = {}
            detail["resource_name"] = ResourceTypeChoices.ROWS.value
            detail["limit"] = resource_limit_instance.limit
            return False, detail
        return True, {}
    except Exception as e:
        logger.exception(
            f"Error checking total number of rows per dataset allowed: {str(e)}"
        )
        detail = {}
        detail["resource_name"] = ResourceTypeChoices.ROWS.value
        detail["limit"] = 0
        return False, detail


# This function is used for OBSERVE Projects
def check_if_observe_creation_is_allowed(organization, existing=False):
    """
    Check if a new trace can be created based on the organization's subscription.
    Pricing scheme:
      - Free subscription: 10k traces allowed – additional trace creation is not allowed.
      - Business subscription: 100k traces allowed free; after that, cost $10 per 100K traces,
        which translates to a cost of 0.0001 per extra trace.
      - Custom subscription: Unlimited traces allowed (set to a very high limit).

    Wallet deductions are performed using log_and_deduct_cost_for_api_request.

    Returns:
      (allowed: bool, cost: float)
    """
    from tracer.models.project import Project
    from tracer.models.trace import Trace

    try:
        # Ensure that an organization subscription exists.
        create_organization_subscription_if_not_exists(organization)
        organization_subscription = OrganizationSubscription.objects.filter(
            organization=organization
        ).first()
        organization_id = organization.id

        # Count the current traces across all projects of the organization.
        projects = Project.objects.filter(organization=organization).exclude(
            source=ProjectSourceChoices.DEMO.value
        )
        Trace.objects.filter(project__in=projects).count()

        project_count = (
            Project.objects.filter(
                organization_id=organization_id, trace_type="observe"
            )
            .exclude(source=ProjectSourceChoices.DEMO.value)
            .count()
        )

        resource_type = ResourceType.objects.filter(
            name=ResourceTypeChoices.OBSERVE.value
        ).first()

        if not existing:
            resource_limit_instance = None
            if (
                organization_subscription.subscription_tier.name
                == SubscriptionTierChoices.CUSTOM.value
            ):
                resource_limit_instance = ResourceLimits.objects.filter(
                    organization=organization, resource_type=resource_type
                ).first()

            if resource_limit_instance is None:
                resource_limit_instance = ResourceLimits.objects.filter(
                    subscription_tier=organization_subscription.subscription_tier,
                    resource_type=resource_type,
                    organization=None,
                ).first()

            if resource_limit_instance is None:
                detail = {}
                detail["type"] = "project_count"
                detail["resource_name"] = ResourceTypeChoices.OBSERVE.value
                detail["limit"] = 0
                logger.info("Resource tracker not found")
                return False, detail

            if project_count >= resource_limit_instance.limit:
                detail = {}
                detail["type"] = "project_count"
                detail["resource_name"] = ResourceTypeChoices.OBSERVE.value
                detail["limit"] = resource_limit_instance.limit
                return False, detail

        return check_if_trace_creation_is_allowed(organization)
    except Exception as e:
        logger.exception(f"Error checking observe creation allowance: {str(e)}")
        detail["type"] = "project_count"
        detail["resource_name"] = ResourceTypeChoices.OBSERVE.value
        detail["limit"] = 0
        return False, detail


# This function is used for PROTOTYPE Projects
def check_if_prototype_creation_is_allowed(organization, existing=False):
    """
    Check if a new trace can be created based on the organization's subscription.
    Pricing scheme:
      - Free subscription: 10k traces allowed – additional trace creation is not allowed.
      - Business subscription: 100k traces allowed free; after that, cost $10 per 100K traces,
        which translates to a cost of 0.0001 per extra trace.
      - Custom subscription: Unlimited traces allowed (set to a very high limit).

    Wallet deductions are performed using log_and_deduct_cost_for_api_request.

    Returns:
      (allowed: bool, cost: float)
    """
    from tracer.models.project import Project

    try:
        # Ensure that an organization subscription exists.
        create_organization_subscription_if_not_exists(organization)
        organization_subscription = OrganizationSubscription.objects.filter(
            organization=organization
        ).first()
        organization_id = organization.id

        project_count = (
            Project.objects.filter(
                organization_id=organization_id, trace_type="experiment"
            )
            .exclude(source=ProjectSourceChoices.DEMO.value)
            .count()
        )

        resource_type = ResourceType.objects.filter(
            name=ResourceTypeChoices.PROTOTYPES.value
        ).first()

        if not existing:
            resource_limit_instance = None
            if (
                organization_subscription.subscription_tier.name
                == SubscriptionTierChoices.CUSTOM.value
            ):
                resource_limit_instance = ResourceLimits.objects.filter(
                    organization=organization, resource_type=resource_type
                ).first()

            if resource_limit_instance is None:
                resource_limit_instance = ResourceLimits.objects.filter(
                    subscription_tier=organization_subscription.subscription_tier,
                    resource_type=resource_type,
                    organization=None,
                ).first()

            if resource_limit_instance is None:
                detail = {}
                detail["type"] = "project_count"
                detail["resource_name"] = ResourceTypeChoices.PROTOTYPES.value
                detail["limit"] = 0
                logger.info("Resource tracker not found")
                return False, detail

            if project_count >= resource_limit_instance.limit:
                detail = {}
                detail["type"] = "project_count"
                detail["resource_name"] = ResourceTypeChoices.PROTOTYPES.value
                detail["limit"] = resource_limit_instance.limit
                return False, detail

        return check_if_trace_creation_is_allowed(organization)
    except Exception as e:
        logger.exception(f"Error checking prototype creation allowance: {str(e)}")
        detail["type"] = "project_count"
        detail["resource_name"] = ResourceTypeChoices.PROTOTYPES.value
        detail["limit"] = 0
        return False, detail


def check_if_trace_creation_is_allowed(organization):
    from django.utils import timezone  # Import timezone to work with dates

    from tracer.models.trace import Trace

    try:
        organization_subscription = OrganizationSubscription.objects.filter(
            organization=organization
        ).first()

        # Count the current traces across all projects of the organization.
        current_trace_count = (
            Trace.objects.filter(project__organization=organization)
            .exclude(project__source=ProjectSourceChoices.DEMO.value)
            .count()
        )

        resource_type_entry = ResourceType.objects.filter(
            name=ResourceTypeChoices.TRACES.value
        ).first()
        resource_limit_instance = None
        if (
            organization_subscription.subscription_tier.name
            == SubscriptionTierChoices.CUSTOM.value
        ):
            resource_limit_instance = ResourceLimits.objects.filter(
                organization=organization, resource_type=resource_type_entry
            ).first()

        if resource_limit_instance is None:
            resource_limit_instance = ResourceLimits.objects.filter(
                subscription_tier=organization_subscription.subscription_tier,
                resource_type=resource_type_entry,
                organization=None,
            ).first()

        if resource_limit_instance is None:
            detail = {}
            detail["resource_name"] = ResourceTypeChoices.TRACES.value
            detail["limit"] = 0
            return False, detail

        subscription = organization_subscription.subscription_tier.name

        # Total trace limit checks across all projects of the organization.
        trace_limit = resource_limit_instance.limit

        # Calculate the difference in days between the organization's creation date and the current date.
        current_date = timezone.now()
        date_difference = (current_date - organization.created_at).days

        # The final allowed number of traces is equal to trace_limit * (date_difference / 30).
        final_trace_limit = trace_limit * ceil(date_difference / 30)

        # Allow creation without any charge if under the allowed limit.
        if current_trace_count < final_trace_limit:
            return True, {}
        else:
            # For free and business subscriptions, calculate the extra traces and perform wallet deduction.
            extra_traces = current_trace_count - final_trace_limit + 1
            if subscription in [
                SubscriptionTierChoices.FREE.value,
                SubscriptionTierChoices.BUSINESS.value,
            ]:
                detail = {"extra_traces": extra_traces}
                detail["limit"] = final_trace_limit
                detail["resource_name"] = ResourceTypeChoices.TRACES.value
                return False, detail
            else:
                # For custom subscriptions, allow trace creation without any wallet deduction.
                detail["limit"] = final_trace_limit
                detail["resource_name"] = ResourceTypeChoices.TRACES.value
                return True, detail
    except Exception as e:
        logger.exception(f"Error checking trace creation allowance: {str(e)}")
        detail["resource_name"] = ResourceTypeChoices.TRACES.value
        detail["limit"] = 0
        return False, detail


# Check KB limit
def check_if_kb_creation_is_allowed(organization):
    from model_hub.models.develop_dataset import KnowledgeBaseFile

    try:
        create_organization_subscription_if_not_exists(organization)
        logger.info(f"organization id : {organization}")
        organization_subscription = OrganizationSubscription.objects.filter(
            organization=organization
        ).first()
        resource_type = ResourceType.objects.filter(
            name=ResourceTypeChoices.KNOWLEDGE_BASE.value
        ).first()
        logger.info(f"resource type: {resource_type}")
        logger.info(f"resource type id : {resource_type.name}")
        resource_limit_instance = None
        if (
            organization_subscription.subscription_tier.name
            == SubscriptionTierChoices.CUSTOM.value
        ):
            resource_limit_instance = ResourceLimits.objects.filter(
                organization=organization, resource_type=resource_type
            ).first()
        if resource_limit_instance is None:
            resource_limit_instance = ResourceLimits.objects.filter(
                subscription_tier=organization_subscription.subscription_tier,
                resource_type=resource_type,
                organization=None,
            ).first()

        if resource_limit_instance is None:
            logger.info("Resource tracker not found")
            detail = {}
            detail["resource_name"] = "knowledge base"
            detail["limit"] = 0
            return False, detail

        logger.info(f"Kb limit: {resource_limit_instance.limit}")
        config = {}
        kb_count = KnowledgeBaseFile.objects.filter(
            organization=organization, deleted=False
        ).count()
        config["kb_count"] = kb_count
        if kb_count >= resource_limit_instance.limit:
            detail = {}
            detail["resource_name"] = "knowledge base"
            detail["limit"] = resource_limit_instance.limit
            return False, detail
        return True, {}
    except Exception as e:
        logger.exception(f"Error checking if kb creation is allowed: {str(e)}")
        detail = {}
        detail["resource_name"] = "knowledge base"
        detail["limit"] = 0
        return False, detail


def process_audio(value):
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        container_source = value
    else:
        if isinstance(value, (bytes, bytearray)):
            audio_bytes = bytes(value)
        else:
            audio_url = upload_audio_to_s3(value)
            audio_bytes = download_audio_from_url(audio_url)
        container_source = BytesIO(audio_bytes)

    # Get audio duration using PyAV
    with av.open(container_source) as container:
        # Try container duration first (most reliable)
        if container.duration:
            duration_seconds = container.duration / 1000000.0
        elif container.streams.audio:
            # Fallback to stream duration
            stream = container.streams.audio[0]
            duration_seconds = (
                float(stream.duration * stream.time_base) if stream.duration else 0
            )
        else:
            duration_seconds = 0

    # total_audio_length += duration_seconds
    return duration_seconds


def check_audio_in_mappings(config):
    """
    Check config mappings for audio content and calculate total audio length.

    Args:
        config (dict): Configuration dictionary containing mappings

    Returns:
        tuple: (bool, float) - (has_audio, total_audio_length_in_seconds)
    """
    try:
        if not config or "mappings" not in config:
            return False, 0.0

        mappings = config["mappings"]
        has_audio = False
        total_audio_length = 0.0

        for key, value in mappings.items():
            # Detect input type for the current value
            if key == "few_shots" or key == "criteria" or key == "eval_name":
                continue

            input_type = detect_input_type(value)

            if isinstance(input_type, dict):
                # Check if audio type is detected
                if input_type.get("type") == "audio":
                    has_audio = True
                    # Download and process audio to get length
                    try:
                        total_audio_length += process_audio(value)

                    except Exception as e:
                        logger.exception(
                            f"Error processing audio for key {key}: {str(e)}"
                        )
                        continue

                # If the value is a list or dictionary, recursively check its contents
                elif isinstance(value, list | dict):
                    if isinstance(value, list):
                        for item in value:
                            item_type = detect_input_type(item)
                            if (
                                isinstance(item_type, dict)
                                and item_type.get("type") == "audio"
                            ):
                                has_audio = True
                                try:
                                    total_audio_length += process_audio(item)
                                except Exception as e:
                                    logger.exception(
                                        f"Error processing audio in list: {str(e)}"
                                    )
                                    continue
                    else:  # dict
                        for sub_value in value.values():
                            sub_type = detect_input_type(sub_value)
                            if (
                                isinstance(sub_type, dict)
                                and sub_type.get("type") == "audio"
                            ):
                                has_audio = True
                                try:
                                    total_audio_length += process_audio(sub_value)
                                except Exception as e:
                                    logger.exception(
                                        f"Error processing audio in dict: {str(e)}"
                                    )
                                    continue

        return has_audio, total_audio_length

    except Exception as e:
        logger.exception(f"Error checking audio in mappings: {str(e)}")
        return False, 0.0


from ee.usage.models.usage import (  # noqa: E402
    APICallTypeChoices,
    ResourceTypeChoices,
    SubscriptionTierChoices,
)


def get_plan_details():
    """
    Get details of all subscription plans including their resources and rate limits
    Returns a dictionary with plan details
    """
    try:
        plans = {}

        # Get all subscription tiers
        subscription_tiers = SubscriptionTier.objects.filter(
            name__in=[
                SubscriptionTierChoices.FREE.value,
                SubscriptionTierChoices.BUSINESS.value,
                SubscriptionTierChoices.BUSINESS_YEARLY.value,
                SubscriptionTierChoices.CUSTOM.value,
            ]
        )

        features = {
            "free": [
                "Upto 5 datasets",
                "Upto 2000 rows per dataset",
                "Upto 1 seat per team",
                "Upto 2 annotation jobs",
                "Unlimited prompt runs",
                "Unlimited experiments",
                "Evals at $1.5 per million tokens",
                "Optimizations at $5 per million tokens",
            ],
            "basic": [
                "Upto 20 datasets",
                "Upto 100,000 rows per dataset",
                "Upto 3 seat per team",
                "$20/month per additional seat",
                "Unlimited annotation jobs",
                "20 monthly credits",
            ],
            "basic_yearly": [
                "Upto 20 datasets",
                "Upto 100,000 rows per dataset",
                "Upto 3 seat per team",
                "$20/month per additional seat",
                "Unlimited annotation jobs",
                "20 monthly credits",
            ],
            "custom": [
                "Unlimited datasets",
                "Unlimited rows per dataset",
                "Unlimited seats per team",
                "Data protection",
                "Single Sign On (SSO)",
                "On premise deployement",
            ],
        }
        for tier in subscription_tiers:
            plan_info = {
                "name": tier.name,
                "description": tier.description,
                "wallet_refill_amount": tier.wallet_refill_amount,
                # Resource Limits
                "resources": {
                    "datasets": ResourceLimits.objects.filter(
                        subscription_tier=tier,
                        resource_type__name=ResourceTypeChoices.DATASET.value,
                        organization=None,
                    )
                    .first()
                    .limit,
                    "rows": ResourceLimits.objects.filter(
                        subscription_tier=tier,
                        resource_type__name=ResourceTypeChoices.ROWS.value,
                        organization=None,
                    )
                    .first()
                    .limit,
                    "users": ResourceLimits.objects.filter(
                        subscription_tier=tier,
                        resource_type__name=ResourceTypeChoices.USERS.value,
                        organization=None,
                    )
                    .first()
                    .limit,
                    "observe": ResourceLimits.objects.filter(
                        subscription_tier=tier,
                        resource_type__name=ResourceTypeChoices.OBSERVE.value,
                        organization=None,
                    )
                    .first()
                    .limit,
                    "knowledge_base": ResourceLimits.objects.filter(
                        subscription_tier=tier,
                        resource_type__name=ResourceTypeChoices.KNOWLEDGE_BASE.value,
                        organization=None,
                    )
                    .first()
                    .limit,
                    "prototypes": ResourceLimits.objects.filter(
                        subscription_tier=tier,
                        resource_type__name=ResourceTypeChoices.PROTOTYPES.value,
                        organization=None,
                    )
                    .first()
                    .limit,
                },
                # Rate Limits for different API calls
                "rate_limits": {
                    "dataset_evaluation": {
                        "minute": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.DATASET_EVALUATION.value,
                            organization=None,
                        )
                        .first()
                        .minute_limit,
                        "hour": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.DATASET_EVALUATION.value,
                            organization=None,
                        )
                        .first()
                        .hour_limit,
                        "day": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.DATASET_EVALUATION.value,
                            organization=None,
                        )
                        .first()
                        .day_limit,
                        "month": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.DATASET_EVALUATION.value,
                            organization=None,
                        )
                        .first()
                        .month_limit,
                    },
                    "dataset_protect": {
                        "minute": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.DATASET_PROTECT.value,
                            organization=None,
                        )
                        .first()
                        .minute_limit,
                        "hour": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.DATASET_PROTECT.value,
                            organization=None,
                        )
                        .first()
                        .hour_limit,
                        "day": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.DATASET_PROTECT.value,
                            organization=None,
                        )
                        .first()
                        .day_limit,
                        "month": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.DATASET_PROTECT.value,
                            organization=None,
                        )
                        .first()
                        .month_limit,
                    },
                    "prompt_bench": {
                        "minute": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.PROMPT_BENCH.value,
                            organization=None,
                        )
                        .first()
                        .minute_limit,
                        "hour": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.PROMPT_BENCH.value,
                            organization=None,
                        )
                        .first()
                        .hour_limit,
                        "day": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.PROMPT_BENCH.value,
                            organization=None,
                        )
                        .first()
                        .day_limit,
                        "month": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.PROMPT_BENCH.value,
                            organization=None,
                        )
                        .first()
                        .month_limit,
                    },
                    "auto_annotation": {
                        "minute": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.AUTO_ANNOTATION.value,
                            organization=None,
                        )
                        .first()
                        .minute_limit,
                        "hour": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.AUTO_ANNOTATION.value,
                            organization=None,
                        )
                        .first()
                        .hour_limit,
                        "day": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.AUTO_ANNOTATION.value,
                            organization=None,
                        )
                        .first()
                        .day_limit,
                        "month": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.AUTO_ANNOTATION.value,
                            organization=None,
                        )
                        .first()
                        .month_limit,
                    },
                    "synthetic_data_generation": {
                        "minute": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.SYNTHETIC_DATA_GENERATION.value,
                            organization=None,
                        )
                        .first()
                        .minute_limit,
                        "hour": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.SYNTHETIC_DATA_GENERATION.value,
                            organization=None,
                        )
                        .first()
                        .hour_limit,
                        "day": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.SYNTHETIC_DATA_GENERATION.value,
                            organization=None,
                        )
                        .first()
                        .day_limit,
                        "month": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.SYNTHETIC_DATA_GENERATION.value,
                            organization=None,
                        )
                        .first()
                        .month_limit,
                    },
                    "error_localizer": {
                        "minute": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.ERROR_LOCALIZER.value,
                            organization=None,
                        )
                        .first()
                        .minute_limit,
                        "hour": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.ERROR_LOCALIZER.value,
                            organization=None,
                        )
                        .first()
                        .hour_limit,
                        "day": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.ERROR_LOCALIZER.value,
                            organization=None,
                        )
                        .first()
                        .day_limit,
                        "month": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.ERROR_LOCALIZER.value,
                            organization=None,
                        )
                        .first()
                        .month_limit,
                    },
                    "dataset_optimization": {
                        "minute": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.DATASET_OPTIMIZATION.value,
                            organization=None,
                        )
                        .first()
                        .minute_limit,
                        "hour": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.DATASET_OPTIMIZATION.value,
                            organization=None,
                        )
                        .first()
                        .hour_limit,
                        "day": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.DATASET_OPTIMIZATION.value,
                            organization=None,
                        )
                        .first()
                        .day_limit,
                        "month": RateLimit.objects.filter(
                            subscription_tier=tier,
                            api_call_type__name=APICallTypeChoices.DATASET_OPTIMIZATION.value,
                            organization=None,
                        )
                        .first()
                        .month_limit,
                    },
                },
            }
            plan_info.update({"features": features[tier.name]})
            plans[tier.name] = plan_info

        return {"status": "success", "data": plans}

    except Exception as e:
        traceback.print_exc()
        return {"status": "error", "message": str(e)}


def fetch_pricing_details_for_api_call_type(
    api_call_type: APICallType, organization: Optional[Organization] = None
) -> Optional[Pricing]:
    try:
        if not api_call_type:
            return None

        organization_subscription = OrganizationSubscription.objects.filter(
            organization=organization, deleted=False
        ).first()

        if (
            organization_subscription.subscription_tier.name
            == SubscriptionTierChoices.CUSTOM.value
        ):
            pricing_obj = Pricing.objects.filter(
                api_call_type=api_call_type, organization=organization
            ).first()
            if not pricing_obj:
                pricing_obj = Pricing.objects.filter(
                    api_call_type=api_call_type, organization=None
                ).first()
        else:
            pricing_obj = Pricing.objects.filter(
                api_call_type=api_call_type, organization=None
            ).first()

        return pricing_obj

    except Exception as e:
        logger.exception(
            f"Error fetching pricing details for api call type: {api_call_type}"
        )
        return None
