import structlog
from django.db.models import Count

logger = structlog.get_logger(__name__)
from simulate.models import CallExecution
from simulate.models.run_test import CreateCallExecution


class CallLimitService:
    """
    Service to handle call limiting logic for both application-wide and organization-specific limits.

    Limits:
    - Application: Maximum 100 concurrent calls across the entire application
    - Organization: Maximum 5 concurrent calls per organization
    """

    MAX_APPLICATION_CALLS = 100
    MAX_ORGANIZATION_CALLS = 25

    @classmethod
    def get_ongoing_calls_count(cls):
        """Get total ongoing calls across the entire application"""
        return CallExecution.no_workspace_objects.filter(
            status=CallExecution.CallStatus.ONGOING,
            simulation_call_type=CallExecution.SimulationCallType.VOICE,
        ).count()

    @classmethod
    def get_organization_ongoing_calls_count(cls, org_id):
        """Get ongoing calls count for a specific organization"""
        return CallExecution.no_workspace_objects.filter(
            status=CallExecution.CallStatus.ONGOING,
            test_execution__run_test__organization_id=org_id,
            simulation_call_type=CallExecution.SimulationCallType.VOICE,
        ).count()

    @classmethod
    def get_multiple_organizations_ongoing_calls_count(cls, org_ids):
        """
        Get ongoing calls count for multiple organizations in a single query.
        Returns a dictionary mapping org_id to count.
        """
        if not org_ids:
            return {}

        counts = (
            CallExecution.no_workspace_objects.filter(
                status=CallExecution.CallStatus.ONGOING,
                test_execution__run_test__organization_id__in=org_ids,
                simulation_call_type=CallExecution.SimulationCallType.VOICE,
            )
            .values("test_execution__run_test__organization_id")
            .annotate(ongoing_count=Count("id"))
        )

        return {
            item["test_execution__run_test__organization_id"]: item["ongoing_count"]
            for item in counts
        }

    @classmethod
    def can_create_call_for_application(cls):
        """Check if application can create more calls"""
        ongoing_calls = cls.get_ongoing_calls_count()
        return ongoing_calls < cls.MAX_APPLICATION_CALLS

    @classmethod
    def can_create_call_for_organization(cls, org_id):
        """Check if organization can create more calls"""
        org_calls = cls.get_organization_ongoing_calls_count(org_id)
        return org_calls < cls.MAX_ORGANIZATION_CALLS

    @classmethod
    def can_create_call(cls, org_id):
        """
        Check if a call can be created for the given organization.
        Returns a dictionary with can_create flag and reason if not allowed.
        """
        # Check application limit
        if not cls.can_create_call_for_application():
            return {
                "can_create": False,
                "reason": f"Application call limit reached ({cls.get_ongoing_calls_count()}/{cls.MAX_APPLICATION_CALLS})",
            }

        # Check organization limit
        if not cls.can_create_call_for_organization(org_id):
            org_calls = cls.get_organization_ongoing_calls_count(org_id)
            return {
                "can_create": False,
                "reason": f"Organization call limit reached ({org_calls}/{cls.MAX_ORGANIZATION_CALLS})",
            }

        return {"can_create": True, "reason": "Call can be created"}

    @classmethod
    def get_available_calls_for_organization(cls, org_id):
        """Get available call slots for organization"""
        org_calls = cls.get_organization_ongoing_calls_count(org_id)
        return max(0, cls.MAX_ORGANIZATION_CALLS - org_calls)

    @classmethod
    def get_available_calls_for_application(cls):
        """Get available call slots for application"""
        app_calls = cls.get_ongoing_calls_count()
        return max(0, cls.MAX_APPLICATION_CALLS - app_calls)

    @classmethod
    def get_call_limits_status(cls):
        """
        Get comprehensive status of call limits across application and organizations.
        Optimized with single query for organization data.
        """
        # Get application status
        app_ongoing = cls.get_ongoing_calls_count()
        app_available = cls.get_available_calls_for_application()

        # Get organization status with single optimized query
        orgs_with_calls = (
            CallExecution.no_workspace_objects.filter(
                status=CallExecution.CallStatus.ONGOING,
                simulation_call_type=CallExecution.SimulationCallType.VOICE,
            )
            .select_related("test_execution__run_test__organization")
            .values(
                "test_execution__run_test__organization_id",
                "test_execution__run_test__organization__name",
            )
            .annotate(ongoing_calls=Count("id"))
            .order_by("-ongoing_calls")
        )

        organizations = []
        for org_data in orgs_with_calls:
            org_id = org_data["test_execution__run_test__organization_id"]
            org_name = org_data["test_execution__run_test__organization__name"]
            ongoing_calls = org_data["ongoing_calls"]
            available_calls = max(0, cls.MAX_ORGANIZATION_CALLS - ongoing_calls)

            organizations.append(
                {
                    "organization_id": org_id,
                    "organization_name": org_name,
                    "ongoing_calls": ongoing_calls,
                    "limit": cls.MAX_ORGANIZATION_CALLS,
                    "available_calls": available_calls,
                    "can_create_calls": available_calls > 0,
                }
            )

        return {
            "application": {
                "ongoing_calls": app_ongoing,
                "limit": cls.MAX_APPLICATION_CALLS,
                "available_calls": app_available,
                "can_create_calls": app_available > 0,
            },
            "organizations": organizations,
        }

    @classmethod
    def get_prioritized_call_executions(cls, limit=None):
        """
        Get CreateCallExecution records that can be processed based on limits.
        Returns records ordered by creation time (FIFO) that can be processed.
        Optimized with bulk queries and prefetch_related to reduce database hits.
        """
        # Get all registered call executions with organization info (limit to 100)
        registered_calls = (
            CreateCallExecution.objects.filter(
                status=CreateCallExecution.CallStatus.REGISTERED
            )
            .select_related("call_execution__test_execution__run_test__organization")
            .order_by("created_at")[:100]
        )

        # Early return if no calls to process
        if not registered_calls.exists():
            return []

        # Get application-wide ongoing calls count (single query)
        app_ongoing_calls = cls.get_ongoing_calls_count()

        # Check if application limit is reached
        if app_ongoing_calls >= cls.MAX_APPLICATION_CALLS:
            logger.info(
                f"Application call limit reached ({app_ongoing_calls}/{cls.MAX_APPLICATION_CALLS})"
            )
            return []

        # Get all unique organization IDs from registered calls
        org_ids = set()
        for call_exec in registered_calls:
            org_id = call_exec.call_execution.test_execution.run_test.organization_id
            org_ids.add(org_id)

        # Bulk fetch ongoing calls count per organization (single query)
        org_counts_lookup = cls.get_multiple_organizations_ongoing_calls_count(org_ids)

        processable_calls = []

        for call_exec in registered_calls:
            org_id = call_exec.call_execution.test_execution.run_test.organization_id
            org_ongoing_count = org_counts_lookup.get(org_id, 0)

            # Check organization limit
            if org_ongoing_count >= cls.MAX_ORGANIZATION_CALLS:
                logger.info(
                    f"Skipping call execution {call_exec.id} for org {org_id}: "
                    f"Organization call limit reached ({org_ongoing_count}/{cls.MAX_ORGANIZATION_CALLS})"
                )
                continue

            processable_calls.append(call_exec)

            # Apply limit if specified
            if limit and len(processable_calls) >= limit:
                break
        logger.info(
            f"Processing {len(processable_calls)} call executions based on call limits"
        )

        return processable_calls

    @classmethod
    def log_call_limits_status(cls):
        """Log current call limits status for monitoring"""
        status = cls.get_call_limits_status()

        # Log application status
        app_status = status["application"]
        logger.info(
            f"Application call status: {app_status['ongoing_calls']}/{app_status['limit']} ({app_status['available_calls']} available)"
        )

        # Log organization status
        for org in status["organizations"]:
            logger.info(
                f"Org {org['organization_name']} ({org['organization_id']}): {org['ongoing_calls']}/{org['limit']} calls ({org['available_calls']} available)"
            )

            # Log warning if approaching limit
            if org["ongoing_calls"] >= org["limit"] * 0.8:  # 80% of limit
                logger.warning(
                    f"Organization {org['organization_name']} call limit approaching: {org['ongoing_calls']}/{org['limit']}"
                )

        # Log warning if application approaching limit
        if app_status["ongoing_calls"] >= app_status["limit"] * 0.9:  # 90% of limit
            logger.warning(
                f"Application call limit approaching: {app_status['ongoing_calls']}/{app_status['limit']}"
            )
