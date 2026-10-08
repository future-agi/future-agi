from abc import ABC, abstractmethod
from typing import Any, Optional

# Registry mapping platform name -> service class instance
_SERVICE_REGISTRY: dict[str, "BaseIntegrationService"] = {}


def register_service(platform: str, service: "BaseIntegrationService"):
    """Register a platform integration service."""
    _SERVICE_REGISTRY[platform] = service


def get_integration_service(platform: str) -> "BaseIntegrationService":
    """Get the service instance for a given platform."""
    service = _SERVICE_REGISTRY.get(platform)
    if service is None:
        raise ValueError(
            f"No integration service registered for platform '{platform}'. "
            f"Available: {list(_SERVICE_REGISTRY.keys())}"
        )
    return service


class BaseIntegrationService(ABC):
    """Abstract base class for platform API clients.

    Each platform (Langfuse, LangSmith, etc.) implements this interface.
    """

    # Host used when a connection is created without ``host_url``. Empty means
    # the platform has no default: it needs an explicit host or uses none.
    default_host_url: str = ""

    @abstractmethod
    def validate_credentials(
        self,
        host_url: str,
        credentials: dict,
        ca_certificate: Optional[str] = None,
    ) -> dict:
        """Validate credentials against the platform API.

        Returns:
            dict with keys:
                - valid (bool): Whether credentials are valid
                - project_name (str): Platform project name (if valid)
                - total_traces (int): Total trace count for backfill estimation
                - error (str): Error message (if invalid)
        """
        ...

    @abstractmethod
    def fetch_traces(
        self,
        host_url: str,
        credentials: dict,
        from_timestamp: Optional[str] = None,
        to_timestamp: Optional[str] = None,
        page: int = 1,
        limit: int = 100,
        ca_certificate: Optional[str] = None,
    ) -> dict:
        """Fetch a page of traces from the platform.

        Returns:
            dict with keys:
                - traces (list): List of trace objects
                - has_more (bool): Whether more pages exist
                - next_page (int): Next page number (if has_more)
                - total_items (int): Total traces matching query
        """
        ...

    @abstractmethod
    def fetch_trace_detail(
        self,
        host_url: str,
        credentials: dict,
        trace_id: str,
        ca_certificate: Optional[str] = None,
    ) -> dict[str, Any]:
        """Fetch a single trace with full details (including observations).

        Returns:
            Full trace object with nested observations.
        """
        ...
