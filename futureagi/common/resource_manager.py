"""
Resource Management System for Future AGI

This module provides connection pooling, resource quotas, and backpressure handling
to prevent database connection exhaustion and system overload under concurrent load.

Key Features:
- Async-safe database connection pooling
- Per-organization resource quotas
- Backpressure handling with graceful degradation
- Circuit breaker pattern for downstream services
- Resource monitoring and alerting

Usage:
    from common.resource_manager import ResourceManager, ResourceType

    resource_manager = ResourceManager()

    # Acquire resources with automatic cleanup
    async with resource_manager.acquire(ResourceType.DB_CONNECTION, org_id="123") as conn:
        # Use connection safely
        pass

    # Check resource availability before starting expensive operations
    if resource_manager.can_acquire(ResourceType.OPTIMIZATION_WORKER, org_id="123"):
        # Start optimization
        pass
"""

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum

import structlog
from django.core.cache import cache

logger = structlog.get_logger(__name__)


class ResourceType(Enum):
    """Types of resources that can be managed"""
    DB_CONNECTION = "db_connection"
    OPTIMIZATION_WORKER = "optimization_worker"
    EVALUATION_WORKER = "evaluation_worker"
    LLM_REQUEST = "llm_request"
    ASYNC_TASK = "async_task"


@dataclass
class ResourceQuota:
    """Resource quota configuration per organization"""
    max_db_connections: int = 10
    max_optimization_workers: int = 5
    max_evaluation_workers: int = 10
    max_llm_requests_per_minute: int = 100
    max_async_tasks: int = 20


@dataclass
class ResourceUsage:
    """Current resource usage tracking"""
    db_connections: int = 0
    optimization_workers: int = 0
    evaluation_workers: int = 0
    llm_requests_last_minute: int = 0
    async_tasks: int = 0
    last_reset_time: float = 0


class CircuitBreakerState(Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"      # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreaker:
    """Circuit breaker for downstream services"""
    failure_threshold: int = 5
    recovery_timeout: int = 60  # seconds
    failure_count: int = 0
    last_failure_time: float = 0
    state: CircuitBreakerState = CircuitBreakerState.CLOSED


class ResourceManagerError(Exception):
    """Base exception for resource manager errors"""
    pass


class ResourceExhaustedException(ResourceManagerError):
    """Raised when resource quota is exceeded"""
    pass


class CircuitBreakerOpenError(ResourceManagerError):
    """Raised when circuit breaker is open"""
    pass


class ResourceManager:
    """
    Thread-safe resource manager with connection pooling and quotas.

    Prevents resource exhaustion by enforcing per-organization limits
    and providing graceful degradation under load.
    """

    def __init__(self):
        self._usage_by_org: dict[str, ResourceUsage] = {}
        self._quota_by_org: dict[str, ResourceQuota] = {}
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

        # Default quotas (can be overridden per organization)
        self.default_quota = ResourceQuota()

        # Initialize metrics
        self._start_time = time.time()
        self._total_requests = 0
        self._rejected_requests = 0

    async def get_or_create_lock(self, org_id: str) -> asyncio.Lock:
        """Get or create a lock for an organization"""
        async with self._global_lock:
            if org_id not in self._locks:
                self._locks[org_id] = asyncio.Lock()
            return self._locks[org_id]

    def get_quota(self, org_id: str) -> ResourceQuota:
        """Get resource quota for organization"""
        if org_id not in self._quota_by_org:
            # Load from cache or database, fall back to default
            cached_quota = cache.get(f"resource_quota:{org_id}")
            if cached_quota:
                self._quota_by_org[org_id] = cached_quota
            else:
                # TODO: Load from database Organization model
                self._quota_by_org[org_id] = self.default_quota
                # Cache for 5 minutes
                cache.set(f"resource_quota:{org_id}", self.default_quota, 300)

        return self._quota_by_org[org_id]

    def get_usage(self, org_id: str) -> ResourceUsage:
        """Get current resource usage for organization"""
        if org_id not in self._usage_by_org:
            self._usage_by_org[org_id] = ResourceUsage(last_reset_time=time.time())

        usage = self._usage_by_org[org_id]

        # Reset LLM request counter every minute
        current_time = time.time()
        if current_time - usage.last_reset_time >= 60:
            usage.llm_requests_last_minute = 0
            usage.last_reset_time = current_time

        return usage

    async def can_acquire(self, resource_type: ResourceType, org_id: str) -> bool:
        """Check if resource can be acquired without actually acquiring it"""
        lock = await self.get_or_create_lock(org_id)
        async with lock:
            return self._can_acquire_unlocked(resource_type, org_id)

    def _can_acquire_unlocked(self, resource_type: ResourceType, org_id: str) -> bool:
        """Check availability while the caller already holds the org lock."""
        quota = self.get_quota(org_id)
        usage = self.get_usage(org_id)

        if resource_type == ResourceType.DB_CONNECTION:
            return usage.db_connections < quota.max_db_connections
        elif resource_type == ResourceType.OPTIMIZATION_WORKER:
            return usage.optimization_workers < quota.max_optimization_workers
        elif resource_type == ResourceType.EVALUATION_WORKER:
            return usage.evaluation_workers < quota.max_evaluation_workers
        elif resource_type == ResourceType.LLM_REQUEST:
            return usage.llm_requests_last_minute < quota.max_llm_requests_per_minute
        elif resource_type == ResourceType.ASYNC_TASK:
            return usage.async_tasks < quota.max_async_tasks

        return False

    @asynccontextmanager
    async def acquire(self, resource_type: ResourceType, org_id: str, timeout: float = 30.0):
        """
        Acquire a resource with automatic cleanup.

        Args:
            resource_type: Type of resource to acquire
            org_id: Organization ID for quota enforcement
            timeout: Maximum time to wait for resource acquisition

        Raises:
            ResourceExhaustedException: If quota exceeded
            CircuitBreakerOpenError: If circuit breaker is open
            asyncio.TimeoutError: If timeout exceeded

        Example:
            async with resource_manager.acquire(ResourceType.DB_CONNECTION, "org123") as conn:
                # Use connection
                pass
        """
        self._total_requests += 1

        # Check circuit breaker
        if resource_type in [ResourceType.DB_CONNECTION, ResourceType.LLM_REQUEST]:
            circuit_key = f"{resource_type.value}:{org_id}"
            if not self._check_circuit_breaker(circuit_key):
                self._rejected_requests += 1
                raise CircuitBreakerOpenError(f"Circuit breaker open for {circuit_key}")

        acquired = False

        try:
            await asyncio.wait_for(
                self._reserve_resource(resource_type, org_id),
                timeout=timeout,
            )
            acquired = True

            resource = await self._create_resource(resource_type, org_id)

            logger.info(
                "Resource acquired",
                resource_type=resource_type.value,
                org_id=org_id,
                current_usage=self._get_usage_for_type(
                    self.get_usage(org_id), resource_type
                ),
            )

            yield resource

        except TimeoutError:
            self._rejected_requests += 1
            logger.warning(
                "Resource acquisition timeout",
                resource_type=resource_type.value,
                org_id=org_id,
                timeout=timeout
            )
            raise
        except Exception:
            if resource_type in [ResourceType.DB_CONNECTION, ResourceType.LLM_REQUEST]:
                circuit_key = f"{resource_type.value}:{org_id}"
                self._record_failure(circuit_key)
            raise
        else:
            if resource_type in [ResourceType.DB_CONNECTION, ResourceType.LLM_REQUEST]:
                circuit_key = f"{resource_type.value}:{org_id}"
                self._record_success(circuit_key)
        finally:
            if acquired:
                lock = await self.get_or_create_lock(org_id)
                async with lock:
                    self._decrement_usage(org_id, resource_type)
                    current_usage = self._get_usage_for_type(
                        self.get_usage(org_id), resource_type
                    )

                logger.info(
                    "Resource released",
                    resource_type=resource_type.value,
                    org_id=org_id,
                    current_usage=current_usage,
                )

    async def _reserve_resource(self, resource_type: ResourceType, org_id: str):
        """Reserve a resource slot under the organization's lock."""
        lock = await self.get_or_create_lock(org_id)
        async with lock:
            if not self._can_acquire_unlocked(resource_type, org_id):
                self._rejected_requests += 1
                quota = self.get_quota(org_id)
                usage = self.get_usage(org_id)
                raise ResourceExhaustedException(
                    f"Resource quota exceeded for {resource_type.value} in org {org_id}. "
                    f"Usage: {self._get_usage_for_type(usage, resource_type)}, "
                    f"Quota: {self._get_quota_for_type(quota, resource_type)}"
                )

            self._increment_usage(org_id, resource_type)

    async def _create_resource(self, resource_type: ResourceType, org_id: str):
        """Create the actual resource object"""
        if resource_type == ResourceType.DB_CONNECTION:
            # Use Django's connection handling but ensure it's properly managed
            from django.db import connection
            # Ensure connection is ready
            connection.ensure_connection()
            return connection

        elif resource_type == ResourceType.OPTIMIZATION_WORKER:
            return {"type": "optimization_worker", "org_id": org_id}

        elif resource_type == ResourceType.EVALUATION_WORKER:
            return {"type": "evaluation_worker", "org_id": org_id}

        elif resource_type == ResourceType.LLM_REQUEST:
            return {"type": "llm_request", "org_id": org_id, "timestamp": time.time()}

        elif resource_type == ResourceType.ASYNC_TASK:
            return {"type": "async_task", "org_id": org_id}

        else:
            return {"type": resource_type.value, "org_id": org_id}

    def _increment_usage(self, org_id: str, resource_type: ResourceType):
        """Increment usage counter for resource type"""
        usage = self.get_usage(org_id)

        if resource_type == ResourceType.DB_CONNECTION:
            usage.db_connections += 1
        elif resource_type == ResourceType.OPTIMIZATION_WORKER:
            usage.optimization_workers += 1
        elif resource_type == ResourceType.EVALUATION_WORKER:
            usage.evaluation_workers += 1
        elif resource_type == ResourceType.LLM_REQUEST:
            usage.llm_requests_last_minute += 1
        elif resource_type == ResourceType.ASYNC_TASK:
            usage.async_tasks += 1

    def _decrement_usage(self, org_id: str, resource_type: ResourceType):
        """Decrement usage counter for resource type"""
        usage = self.get_usage(org_id)

        if resource_type == ResourceType.DB_CONNECTION:
            usage.db_connections = max(0, usage.db_connections - 1)
        elif resource_type == ResourceType.OPTIMIZATION_WORKER:
            usage.optimization_workers = max(0, usage.optimization_workers - 1)
        elif resource_type == ResourceType.EVALUATION_WORKER:
            usage.evaluation_workers = max(0, usage.evaluation_workers - 1)
        elif resource_type == ResourceType.ASYNC_TASK:
            usage.async_tasks = max(0, usage.async_tasks - 1)
        # Note: LLM requests are not decremented as they're time-window based

    def _get_usage_for_type(self, usage: ResourceUsage, resource_type: ResourceType) -> int:
        """Get current usage count for a specific resource type"""
        if resource_type == ResourceType.DB_CONNECTION:
            return usage.db_connections
        elif resource_type == ResourceType.OPTIMIZATION_WORKER:
            return usage.optimization_workers
        elif resource_type == ResourceType.EVALUATION_WORKER:
            return usage.evaluation_workers
        elif resource_type == ResourceType.LLM_REQUEST:
            return usage.llm_requests_last_minute
        elif resource_type == ResourceType.ASYNC_TASK:
            return usage.async_tasks
        return 0

    def _get_quota_for_type(self, quota: ResourceQuota, resource_type: ResourceType) -> int:
        """Get quota limit for a specific resource type"""
        if resource_type == ResourceType.DB_CONNECTION:
            return quota.max_db_connections
        elif resource_type == ResourceType.OPTIMIZATION_WORKER:
            return quota.max_optimization_workers
        elif resource_type == ResourceType.EVALUATION_WORKER:
            return quota.max_evaluation_workers
        elif resource_type == ResourceType.LLM_REQUEST:
            return quota.max_llm_requests_per_minute
        elif resource_type == ResourceType.ASYNC_TASK:
            return quota.max_async_tasks
        return 0

    def _check_circuit_breaker(self, circuit_key: str) -> bool:
        """Check if circuit breaker allows requests"""
        if circuit_key not in self._circuit_breakers:
            self._circuit_breakers[circuit_key] = CircuitBreaker()

        breaker = self._circuit_breakers[circuit_key]
        current_time = time.time()

        if breaker.state == CircuitBreakerState.OPEN:
            if current_time - breaker.last_failure_time >= breaker.recovery_timeout:
                breaker.state = CircuitBreakerState.HALF_OPEN
                logger.info("Circuit breaker half-open", circuit_key=circuit_key)
                return True
            return False

        return True

    def _record_failure(self, circuit_key: str):
        """Record a failure for circuit breaker"""
        if circuit_key not in self._circuit_breakers:
            self._circuit_breakers[circuit_key] = CircuitBreaker()

        breaker = self._circuit_breakers[circuit_key]
        breaker.failure_count += 1
        breaker.last_failure_time = time.time()

        if breaker.failure_count >= breaker.failure_threshold:
            breaker.state = CircuitBreakerState.OPEN
            logger.warning(
                "Circuit breaker opened",
                circuit_key=circuit_key,
                failure_count=breaker.failure_count
            )

    def _record_success(self, circuit_key: str):
        """Record a success for circuit breaker"""
        if circuit_key not in self._circuit_breakers:
            return

        breaker = self._circuit_breakers[circuit_key]
        if breaker.state == CircuitBreakerState.HALF_OPEN:
            breaker.state = CircuitBreakerState.CLOSED
            breaker.failure_count = 0
            logger.info("Circuit breaker closed", circuit_key=circuit_key)

    def get_metrics(self) -> dict:
        """Get resource manager metrics for monitoring"""
        uptime = time.time() - self._start_time
        success_rate = (self._total_requests - self._rejected_requests) / max(1, self._total_requests)

        return {
            "uptime_seconds": uptime,
            "total_requests": self._total_requests,
            "rejected_requests": self._rejected_requests,
            "success_rate": success_rate,
            "active_organizations": len(self._usage_by_org),
            "circuit_breakers": {
                key: {
                    "state": breaker.state.value,
                    "failure_count": breaker.failure_count,
                    "last_failure_time": breaker.last_failure_time
                }
                for key, breaker in self._circuit_breakers.items()
            },
            "usage_by_org": {
                org_id: {
                    "db_connections": usage.db_connections,
                    "optimization_workers": usage.optimization_workers,
                    "evaluation_workers": usage.evaluation_workers,
                    "llm_requests_last_minute": usage.llm_requests_last_minute,
                    "async_tasks": usage.async_tasks
                }
                for org_id, usage in self._usage_by_org.items()
            }
        }

    async def set_quota(self, org_id: str, quota: ResourceQuota):
        """Set custom resource quota for organization"""
        self._quota_by_org[org_id] = quota
        cache.set(f"resource_quota:{org_id}", quota, 300)

        logger.info(
            "Resource quota updated",
            org_id=org_id,
            quota=quota.__dict__
        )


# Global instance
_resource_manager = None

def get_resource_manager() -> ResourceManager:
    """Get the global resource manager instance"""
    global _resource_manager
    if _resource_manager is None:
        _resource_manager = ResourceManager()
    return _resource_manager


# Convenience decorators and context managers
def with_resource_management(resource_type: ResourceType):
    """
    Decorator to automatically manage resources for a function.

    Usage:
        @with_resource_management(ResourceType.OPTIMIZATION_WORKER)
        async def run_optimization(org_id: str, ...):
            # Function body
            pass
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            # Extract org_id from function arguments
            org_id = kwargs.get('org_id') or kwargs.get('organization_id')
            if not org_id and args:
                # Try to extract from first argument if it's an object with organization
                first_arg = args[0]
                if hasattr(first_arg, 'organization_id'):
                    org_id = str(first_arg.organization_id)
                elif hasattr(first_arg, 'organization'):
                    org_id = str(first_arg.organization.id)

            if not org_id:
                raise ValueError("Could not extract org_id for resource management")

            resource_manager = get_resource_manager()
            async with resource_manager.acquire(resource_type, org_id):
                return await func(*args, **kwargs)

        return wrapper
    return decorator
