import math
from datetime import UTC, datetime

import structlog
from django.utils.functional import SimpleLazyObject

from accounts.models.aws_marketplace import AWSMarketplaceCustomer
from accounts.services.aws_marketplace import AWSMarketplaceService

logger = structlog.get_logger(__name__)


class AWSMarketplaceUsageMetering:
    """
    Utility class for metering usage to AWS Marketplace.
    Only supports a single dimension: 'unit'.
    """

    def __init__(self):
        self.aws_service = AWSMarketplaceService()

    def meter_api_calls(self, aws_customer_id: str, api_calls: int) -> bool:
        """
        Meter 'unit' usage (API calls) for a specific organization to AWS Marketplace.

        Args:
            organization_id: Organization UUID
            api_calls: Number of API calls made
            timestamp: Timestamp for the usage (defaults to now)

        Returns:
            Boolean indicating success
        """
        try:
            # Get AWS Marketplace customer for this organization
            try:
                aws_customer = AWSMarketplaceCustomer.objects.get(
                    id=aws_customer_id,
                )
            except AWSMarketplaceCustomer.DoesNotExist:
                logger.warning(
                    f"No active AWS Marketplace customer found for organization {aws_customer_id}"
                )
                return False

            # Prepare usage record
            usage_records = [
                {
                    "Timestamp": datetime.now(UTC),
                    "CustomerAWSAccountId": aws_customer.customer_aws_account_id,
                    "Dimension": "pro_plan",  # have some doubt , should we change it to enterprice_plan ??
                    "Quantity": math.ceil(api_calls * 1000),
                }
            ]

            # Send to AWS Marketplace
            success = self.aws_service.batch_meter_usage(
                aws_customer.product_code, usage_records
            )

            if success:
                logger.info(
                    f"Successfully metered {api_calls} API calls for AWS customer {aws_customer.customer_identifier}"
                )
            else:
                logger.error(
                    f"Failed to meter API calls for AWS customer {aws_customer.customer_identifier}"
                )

            return success

        except Exception as e:
            logger.error(
                f"Error metering usage for AWS customer {aws_customer_id}: {str(e)}"
            )
            return False


# Lazy: building boto3 clients at import costs ~8 MB RSS in every process
# and only AWS Marketplace deployments use them.
aws_marketplace_metering = SimpleLazyObject(AWSMarketplaceUsageMetering)
