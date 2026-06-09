from django.core.validators import EmailValidator
from rest_framework import serializers

from accounts.models import User
from accounts.serializers.organization import OrganizationSerializer
from tfc.constants.roles import OrganizationRoles


class UpdateUserSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    email = serializers.EmailField(required=False)
    name = serializers.CharField(required=False)
    organization_role = serializers.ChoiceField(
        choices=OrganizationRoles.choices, required=False
    )


class UserSignupSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(write_only=True, allow_blank=True)
    full_name = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ["email", "full_name", "password", "company_name"]

    def create(self, validated_data):
        validated_data.pop("company_name")
        name = validated_data.pop("full_name")
        user = User.objects.create_user(
            email=validated_data["email"],
            name=name,
            password=validated_data["password"],
            organization_role=OrganizationRoles.OWNER,
        )
        return user

    def update(self, instance, validated_data):
        raise NotImplementedError("UserSignupSerializer does not support updates.")


class UserSerializer(serializers.ModelSerializer):
    organization = OrganizationSerializer(read_only=True)
    status = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "name",
            "organization_role",
            "organization",
            "created_at",
            "status",
            "role",
            "goals",
        ]

    def get_status(self, obj):
        """Get user status with proper invitation state handling"""
        # Check if user is active
        if not obj.is_active:
            return "Inactive"

        # Check if user has been invited but not yet activated
        if obj.invited_by and not obj.is_active:
            return "Request Pending"

        # Check if user is a primary member (has organization)
        if obj.organization:
            return "Active"

        # Check if user is an invited member
        if obj.invited_organizations.exists():
            # Check if any invitation is still active
            try:
                from accounts.models.organization_membership import (
                    OrganizationMembership,
                )

                active_invitations = OrganizationMembership.objects.filter(
                    user=obj, is_active=True
                )
                if active_invitations.exists():
                    return "Request Pending"
                else:
                    return "Request Expired"
            except Exception:
                return "Request Expired"

        return "Active"


class UserCreateSerializer(serializers.Serializer):
    email = serializers.EmailField(
        validators=[EmailValidator()], max_length=255, required=True
    )
    password = serializers.CharField(
        min_length=8, max_length=128, required=True, write_only=True
    )
    organization_name = serializers.CharField(max_length=255, required=True)
    send_credential = serializers.BooleanField(required=True)

    def validate_email(self, value):
        # You can add custom email validation here if needed
        return value.lower()

    def validate_password(self, value):
        # You can add custom password validation here if needed
        return value

    def validate_organization_name(self, value):
        # You can add custom organization name validation here if needed
        return value.strip()


class PasswordValidationSerializer(serializers.Serializer):
    password = serializers.CharField(
        min_length=8, max_length=128, required=True, write_only=True
    )

    def validate_password(self, value):
        # You can add custom password validation here if needed
        return value


class CreateMemberSerializer(serializers.Serializer):
    email = serializers.EmailField(
        validators=[EmailValidator()], max_length=255, required=True
    )

    # Support both 'role' (new format) and 'organization_role' (old format) for backward compatibility
    role = serializers.ChoiceField(
        choices=OrganizationRoles.choices,
        required=False,
        allow_null=True,
    )

    organization_role = serializers.ChoiceField(
        choices=[
            OrganizationRoles.OWNER,
            OrganizationRoles.ADMIN,
            OrganizationRoles.MEMBER,
            OrganizationRoles.MEMBER_VIEW_ONLY,
        ],
        required=False,
        allow_null=True,
    )

    name = serializers.CharField(max_length=255, required=True)

    def validate(self, data):
        """Validate that at least one role is provided and it's valid"""
        role = data.get("role") or data.get("organization_role")

        if not role:
            raise serializers.ValidationError(
                "Either 'role' or 'organization_role' must be provided. "
                "Role must be either an organization-level role (owner, admin, member, member_view_only) "
                "or a workspace-level role (workspace_admin, workspace_member, workspace_viewer)"
            )

        # Define valid roles
        organization_level_roles = [
            OrganizationRoles.OWNER,
            OrganizationRoles.ADMIN,
            OrganizationRoles.MEMBER,
            OrganizationRoles.MEMBER_VIEW_ONLY,
        ]
        workspace_level_roles = [
            OrganizationRoles.WORKSPACE_ADMIN,
            OrganizationRoles.WORKSPACE_MEMBER,
            OrganizationRoles.WORKSPACE_VIEWER,
        ]

        # Validate that the role is either organization-level or workspace-level
        if role not in organization_level_roles and role not in workspace_level_roles:
            raise serializers.ValidationError(
                f"Invalid role '{role}'. Must be either an organization-level role "
                f"({', '.join([r.value for r in organization_level_roles])}) "
                f"or a workspace-level role ({', '.join([r.value for r in workspace_level_roles])})"
            )

        # Store the validated role in both fields for consistency
        data["role"] = role
        if role in organization_level_roles:
            data["organization_role"] = role
        else:
            data["organization_role"] = None

        return data


class SOSLoginSerializer(serializers.Serializer):
    email = serializers.EmailField(
        validators=[EmailValidator()], max_length=255, required=True
    )


class UserOnboardingSerializer(serializers.Serializer):
    """Serializer for user onboarding data (role and goals)"""

    ROLE_CHOICES = [
        ("Data Scientist", "Data Scientist"),
        ("ML Engineer", "ML Engineer"),
        ("Subject matter expert", "Subject matter expert"),
        ("Product Manager", "Product Manager"),
        ("Business Manager", "Business Manager"),
    ]

    GOAL_CHOICES = [
        ("Uptime monitoring", "Uptime monitoring"),
        ("LLM Observability", "LLM Observability"),
        ("Evaluations", "Evaluations"),
        ("Agent Simulation", "Agent Simulation"),
        ("Prompt Management", "Prompt Management"),
        ("Data Annotation", "Data Annotation"),
    ]

    role = serializers.CharField(max_length=255, required=True)
    goals = serializers.ListField(
        child=serializers.CharField(max_length=255), required=True, allow_empty=True
    )

    def validate_role(self, value):
        """Validate that role is provided and not empty"""
        if not value or not value.strip():
            raise serializers.ValidationError("Role is required")
        return value.strip()

    def validate_goals(self, value):
        """Validate and clean goals list"""
        # Allow empty list
        if not value:
            return []
        # Remove duplicates and strip whitespace
        return list(set([goal.strip() for goal in value if goal.strip()]))
