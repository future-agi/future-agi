from django.conf import settings
from django.db.models import Q
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import OrgApiKey
from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.serializers.contracts import (
    ACCOUNTS_ERROR_RESPONSES,
    AccountsPaginatedUserResponseSerializer,
    AccountsTokenPairResponseSerializer,
    AppsmithPasswordUpdateResponseSerializer,
    AppsmithUserCreateResponseSerializer,
)
from accounts.serializers.user import (
    PasswordValidationSerializer,
    SOSLoginSerializer,
    UserCreateSerializer,
    UserSerializer,
)
from accounts.services.sos_service import start_sos_session
from tfc.constants.roles import OrganizationRoles
from tfc.permissions.permissions import APIKeyPermission
from tfc.utils.api_contracts import validated_request
from tfc.utils.email import email_helper
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination


class UserApiView(APIView):
    permission_classes = [APIKeyPermission]

    @swagger_auto_schema(
        responses={
            200: AccountsPaginatedUserResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        }
    )
    def get(
        self,
        request,
    ):
        search_query = request.query_params.get("search_query", "")

        users = User.objects.select_related("organization").order_by(
            "-organization__created_at"
        )
        if search_query and len(search_query) > 0:
            users = users.filter(
                Q(name__icontains=search_query) | Q(email__icontains=search_query)
            )

        paginator = ExtendedPageNumberPagination()
        result_page = paginator.paginate_queryset(users, request)
        result_page = UserSerializer(result_page, many=True).data

        return paginator.get_paginated_response(list(result_page))

    @validated_request(
        request_serializer=UserCreateSerializer,
        responses={
            201: AppsmithUserCreateResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request):
        data = request.validated_data

        organization = Organization.objects.create(
            name=data["organization_name"], region=settings.REGION
        )

        # Create the user
        user = User.objects.create(
            email=data["email"],
            name=data["email"],
            organization=organization,
            organization_role=OrganizationRoles.OWNER,
            is_active=True,  # This is redundant as it's already True by default, but included for clarity
        )

        # Set the user's password (you should use a secure method to generate or obtain the password)
        user.set_password(data["password"])
        user.save()
        apiKeys = OrgApiKey.no_workspace_objects.filter(
            organization=organization, type="system", enabled=True
        )
        if len(apiKeys) == 0:
            OrgApiKey.no_workspace_objects.create(
                organization=organization, type="system"
            )

        if data["send_credential"]:
            email_helper(
                "Your Future AGI credentials",
                "send_credentials.html",
                {"email": user.email, "password": data["password"]},
                [user.email],
            )

        return Response(data, status=status.HTTP_201_CREATED)

    @validated_request(
        request_serializer=PasswordValidationSerializer,
        responses={
            201: AppsmithPasswordUpdateResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def patch(self, request, user_id):
        data = request.validated_data

        user = User.objects.get(id=user_id)

        # Set the user's password (you should use a secure method to generate or obtain the password)
        user.set_password(data["password"])
        user.save()

        email_helper(
            "Your Future AGI credentials",
            "send_credentials.html",
            {"email": user.email, "password": data["password"]},
            [user.email],
        )

        return Response(data, status=status.HTTP_201_CREATED)


class SOSLoginView(APIView):
    permission_classes = [APIKeyPermission]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=SOSLoginSerializer,
        responses={
            200: AccountsTokenPairResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request):
        try:
            data = request.validated_data
            email = data.get("email", "").lower()
            try:
                user = User.objects.get(email=email, is_active=True)
            except User.DoesNotExist:
                raise Exception("User not found") from None

            tokens = start_sos_session(user, source="appsmith_api")

            return Response(
                {
                    "access": tokens["access"],
                    "refresh": tokens["refresh"],
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            return self._gm.bad_request(f"Failed to login: {str(e)}")
