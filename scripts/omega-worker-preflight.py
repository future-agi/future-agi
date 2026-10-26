"""Refuse to publish the private runtime to a public repo or replace a release."""

import argparse
import json
import os
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPOSITORY_URL = (
    "https://hub.docker.com/v2/repositories/futureagi/omega-error-feed-worker"
)


def check_release(
    version: str, username: str, password: str, *, allow_existing: bool = False
) -> bool:
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Expected a stable platform version")
    if not username or not password:
        raise ValueError(
            "Docker Hub credentials are required to check the private worker"
        )
    request = Request(
        "https://hub.docker.com/v2/auth/token",
        data=json.dumps({"identifier": username, "secret": password}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        token = json.load(response)["access_token"]
    if not isinstance(token, str) or not token:
        raise ValueError("Docker Hub returned no access token")
    headers = {"Authorization": f"Bearer {token}"}
    with urlopen(Request(REPOSITORY_URL, headers=headers), timeout=30) as response:
        repository = json.load(response)
    if repository.get("is_private") is not True:
        raise RuntimeError("Worker registry must already exist and be private")
    # A 404 is absence only after authenticated access to the repo succeeded.
    try:
        with urlopen(
            Request(f"{REPOSITORY_URL}/tags/{version}", headers=headers), timeout=30
        ):
            pass
    except HTTPError as error:
        if error.code == 404:
            return False
        raise
    if allow_existing:
        return True
    raise RuntimeError(f"Worker tag {version} already exists; refusing to rebuild it")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-existing", action="store_true")
    args = parser.parse_args()
    exists = check_release(
        os.environ["VERSION"],
        os.environ.get("DOCKERHUB_USERNAME", ""),
        os.environ.get("DOCKERHUB_TOKEN", ""),
        allow_existing=args.allow_existing,
    )
    if args.allow_existing:
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"exists={str(exists).lower()}\n")
