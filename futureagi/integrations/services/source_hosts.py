from dataclasses import dataclass
from typing import Protocol

from integrations.models import IntegrationConnection, IntegrationPlatform


@dataclass(frozen=True)
class Repository:
    id: int
    full_name: str
    default_branch: str
    private: bool


@dataclass(frozen=True)
class RepositoryPage:
    repositories: list[Repository]
    next_page: int | None


@dataclass(frozen=True)
class Branch:
    name: str
    is_default: bool


class SourceHostError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 502,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


class SourceHost(Protocol):
    def list_repositories(
        self, connection: IntegrationConnection, *, query: str, page: int
    ) -> RepositoryPage: ...

    def list_branches(
        self, connection: IntegrationConnection, *, repository_id: int
    ) -> list[Branch] | None: ...


def source_host(platform: str) -> SourceHost | None:
    from integrations.services.github_app import GitHubSourceHost

    hosts: dict[str, SourceHost] = {IntegrationPlatform.GITHUB: GitHubSourceHost()}
    return hosts.get(platform)
