import uuid

from django.db import models

from accounts.models.organization import Organization
from tfc.utils.base_model import BaseModel


class SubscriptionStatus(models.TextChoices):
    SUBSCRIBE_SUCCESS = ("subscribe-success", "Subscribe Success")
    SUBSCRIBE_PENDING = ("subscribe-pending", "Subscribe Pending")
    SUBSCRIBE_FAIL = ("subscribe-fail", "Subscribe Fail")
    UNSUBSCRIBE_PENDING = ("unsubscribe-pending", "Unsubscribe Pending")
    UNSUBSCRIBE_SUCCESS = ("unsubscribe-success", "Unsubscribe Success")


class AWSMarketplaceCustomer(BaseModel):
    """
    Model to store AWS Marketplace customer information
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # AWS Marketplace fields
    customer_identifier = models.CharField(
        max_length=255, unique=True, help_text="AWS Marketplace Customer Identifier"
    )
    customer_aws_account_id = models.CharField(
        max_length=255, help_text="Customer's AWS Account ID"
    )
    product_code = models.CharField(
        max_length=255, help_text="AWS Marketplace Product Code"
    )

    # Additional AWS Marketplace data
    product_id = models.CharField(
        max_length=255, null=True, blank=True, help_text="AWS Marketplace Product ID"
    )
    agreement_id = models.CharField(
        max_length=255, null=True, blank=True, help_text="AWS Marketplace Agreement ID"
    )

    # Subscription details
    subscription_status = models.CharField(
        max_length=50,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.SUBSCRIBE_PENDING,
    )

    # Entitlement information (for contract products)
    entitlements = models.JSONField(
        default=dict, help_text="Current entitlements from AWS Marketplace"
    )

    # Links to our system
    organization = models.OneToOneField(
        Organization,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="aws_marketplace_customer",
    )

    # Metadata
    registration_token = models.TextField(
        null=True, blank=True, help_text="Last registration token received"
    )

    def __str__(self):
        return (
            f"AWS Customer {self.customer_identifier} - {self.customer_aws_account_id}"
        )

    class Meta:
        verbose_name = "AWS Marketplace Customer"
        verbose_name_plural = "AWS Marketplace Customers"
