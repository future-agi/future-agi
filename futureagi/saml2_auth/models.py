import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from accounts.models.organization import Organization
from tfc.utils.base_model import BaseModel

# from tfc.utils.custom_metrics import QueryMixin
# from tfc.utils.functions import base36encode, random_alphanumeric


class SAMLMetadataModel(BaseModel):
    IDENTITY_AWS = 1
    IDENTITY_OKTA = 2
    IDENTITY_GOOGLE = 3
    IDENTITY_CHOICES = (
        (
            IDENTITY_AWS,
            "AWS",
        ),
        (
            IDENTITY_OKTA,
            "OKTA",
        ),
        (
            IDENTITY_GOOGLE,
            "Google",
        ),
    )

    id = models.CharField(
        max_length=100, primary_key=True, default=uuid.uuid4, editable=False
    )
    name = models.CharField(max_length=250, default=None, blank=True, null=True)
    # meta = CompressedTextField(compress_level=9)  # Range from 0 to 9, 9 being the highest compression.
    identity_type = models.PositiveSmallIntegerField(choices=IDENTITY_CHOICES)
    relay_state = models.CharField(max_length=100, unique=True, null=False, blank=False)
    is_enabled = models.BooleanField(default=False)
    security_generation = models.IntegerField(default=1)
    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, unique=True
    )
    meta = models.TextField(blank=True, help_text="To store content of XML file.")

    # objects = QueryMixin('saml_meta').as_manager()

    class Meta:
        db_table = "saml_meta"
        verbose_name = _("SAML Meta")
        verbose_name_plural = _("SAML Meta")
        ordering = ("-created_at",)

    @property
    def get_identity_type(self):
        return dict(self.IDENTITY_CHOICES).get(self.identity_type)

    # def save(self, **kwargs):
    #     if not self.uuid:
    #         """Base 36 of current timestamp with some random char of 4 digits to make it unique always."""
    #         self.uuid = base36encode(int(timezone.now().strftime('%s'))).lower() + random_alphanumeric(4)
    #     super(SAMLMetadataModel, self).save(**kwargs)

    @staticmethod
    def get_attributes(identity_type) -> list:
        # if identity_type == SAMLMetadataModel.IDENTITY_AWS:
        #     return ['email', 'name', ]
        if identity_type == SAMLMetadataModel.IDENTITY_OKTA:
            return [
                "email",
                "first_name",
                "last_name",
            ]
        elif identity_type == SAMLMetadataModel.IDENTITY_GOOGLE:
            return [
                "email",
                "first_name",
                "last_name",
            ]
