import os

import boto3
import structlog

logger = structlog.get_logger(__name__)


class AWSMarketplaceNotConfigured(RuntimeError):
    """AWS_MARKETPLACE_ACCESS_KEY_ID / AWS_MARKETPLACE_SECRET_ACCESS_KEY unset."""


class AWSMarketplaceService:
    """
    Service class to handle AWS Marketplace integration
    """

    def __init__(self):
        access_key_id = (os.getenv("AWS_MARKETPLACE_ACCESS_KEY_ID") or "").strip()
        secret_access_key = (
            os.getenv("AWS_MARKETPLACE_SECRET_ACCESS_KEY") or ""
        ).strip()
        # Without its own keys boto3 would fall back to the default credential
        # chain, and sign with whatever AWS keys or instance role the operator
        # gave the app for something else (Bedrock, S3).
        if not access_key_id or not secret_access_key:
            raise AWSMarketplaceNotConfigured(
                "AWS Marketplace is not configured: set "
                "AWS_MARKETPLACE_ACCESS_KEY_ID and AWS_MARKETPLACE_SECRET_ACCESS_KEY"
            )
        region_name = os.getenv("AWS_MARKETPLACE_REGION", "us-east-1")

        self.marketplace_metering = boto3.client(
            "meteringmarketplace",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region_name,
        )

        self.marketplace_entitlement = boto3.client(
            "marketplace-entitlement",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region_name,
        )

    def resolve_customer_token(self, registration_token: str) -> tuple[str, str, str]:
        """
        Resolve the registration token to get customer information

        Args:
            registration_token : The token from AWS Marketplace

        Returns:
            Tuple of (customer_identifier, customer_aws_account_id, product_code)
        """
        try:
            response = self.marketplace_metering.resolve_customer(
                RegistrationToken=registration_token
            )

            customer_identifier = response["CustomerIdentifier"]
            customer_aws_account_id = response["CustomerAWSAccountId"]
            product_code = response["ProductCode"]

            return (customer_identifier, customer_aws_account_id, product_code)
        except Exception as e:
            logger.error(f"Failed to resolve customer token: {str(e)}")
            raise

    def get_customer_entitlements(
        self, customer_identifier: str, product_code: str
    ) -> dict:
        """
        Get customer entitlements for contract products

        Args:
            customer_identifier: AWS Marketplace customer identifier
            product_code: AWS Marketplace product code

        Returns:
            Dictionary of entitlements
        """
        try:
            response = self.marketplace_entitlement.get_entitlements(
                ProductCode=product_code,
                Filter={"CUSTOMER_IDENTIFIER": [customer_identifier]},
            )

            entitlements = response.get("Entitlements", [])
            return entitlements
        except Exception as e:
            logger.error(
                f"Failed to get entitlements for customer {customer_identifier}: {str(e)}"
            )
            return []

    def batch_meter_usage(self, product_code: str, usage_records: list) -> bool:
        """
        Send usage records to AWS Marketplace for subscription products

        Args:
            product_code: AWS Marketplace product code
            usage_records: List of usage records

        Returns:
            Boolean indicating success
        """
        try:
            response = self.marketplace_metering.batch_meter_usage(
                UsageRecords=usage_records, ProductCode=product_code
            )

            unprocessed = response.get("UnprocessedRecords", [])
            if unprocessed:
                logger.warning(f"Some usage records were not processed: {unprocessed}")

            success = len(unprocessed) == 0
            return success
        except Exception as e:
            logger.error(f"Failed to send usage records: {str(e)}")
            return False
