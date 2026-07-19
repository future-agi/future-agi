import pytest

from common.resource_manager import (
    ResourceExhaustedException,
    ResourceManager,
    ResourceQuota,
    ResourceType,
)


class InMemoryResourceManager(ResourceManager):
    def get_quota(self, org_id: str) -> ResourceQuota:
        return self._quota_by_org.get(org_id, self.default_quota)

    def set_test_quota(self, org_id: str, quota: ResourceQuota):
        self._quota_by_org[org_id] = quota


@pytest.mark.asyncio
async def test_resource_limit_blocks_second_acquire_for_same_org():
    manager = InMemoryResourceManager()
    manager.set_test_quota(
        "org-a",
        ResourceQuota(max_optimization_workers=1),
    )

    async with manager.acquire(ResourceType.OPTIMIZATION_WORKER, "org-a"):
        with pytest.raises(ResourceExhaustedException, match="optimization_worker"):
            async with manager.acquire(ResourceType.OPTIMIZATION_WORKER, "org-a"):
                pass


@pytest.mark.asyncio
async def test_resource_usage_released_after_context_exit():
    manager = InMemoryResourceManager()
    manager.set_test_quota(
        "org-a",
        ResourceQuota(max_optimization_workers=1),
    )

    async with manager.acquire(ResourceType.OPTIMIZATION_WORKER, "org-a"):
        assert not await manager.can_acquire(ResourceType.OPTIMIZATION_WORKER, "org-a")

    assert await manager.can_acquire(ResourceType.OPTIMIZATION_WORKER, "org-a")


@pytest.mark.asyncio
async def test_resource_limits_are_scoped_per_org():
    manager = InMemoryResourceManager()
    manager.set_test_quota(
        "org-a",
        ResourceQuota(max_optimization_workers=1),
    )
    manager.set_test_quota(
        "org-b",
        ResourceQuota(max_optimization_workers=1),
    )

    async with manager.acquire(ResourceType.OPTIMIZATION_WORKER, "org-a"):
        assert await manager.can_acquire(ResourceType.OPTIMIZATION_WORKER, "org-b")
        async with manager.acquire(ResourceType.OPTIMIZATION_WORKER, "org-b"):
            assert manager.get_usage("org-a").optimization_workers == 1
            assert manager.get_usage("org-b").optimization_workers == 1


@pytest.mark.asyncio
async def test_resource_released_when_create_resource_fails():
    class FailingResourceManager(InMemoryResourceManager):
        async def _create_resource(self, resource_type: ResourceType, org_id: str):
            raise RuntimeError("boom")

    manager = FailingResourceManager()
    manager.set_test_quota(
        "org-a",
        ResourceQuota(max_optimization_workers=1),
    )

    with pytest.raises(RuntimeError, match="boom"):
        async with manager.acquire(ResourceType.OPTIMIZATION_WORKER, "org-a"):
            pass

    assert await manager.can_acquire(ResourceType.OPTIMIZATION_WORKER, "org-a")
