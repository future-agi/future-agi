"""Assert every platform image in the deployment values files carries one version.

The release bump moves the tags it is told to (a fixed yq path list). This is
the check in the other direction: a tag that exists in a values file but is
missing from that path list is caught here, in the release job, instead of at
deploy time when the chart refuses to render.

Usage: verify-platform-image-tags.py VERSION FILE [FILE...]
Exit 0 when clean, 1 with one line per stale image otherwise.
"""

import re
import sys

import yaml

# Every image the platform release lane publishes at the same version. Anything
# else in a values file (nginx, rabbitmq, flower, busybox) rides its own tag.
PLATFORM_IMAGES = (
    "future-agi-ee",
    "fi-collector",
    "agentcc-gateway",
    "code-executor",
    "serving",
    "future-agi-simulation-runner",
    "omega-error-feed-worker",
)

# Repositories may be prefixed by a registry mirror, so match on the trailing
# futureagi/<image> segment only.
PLATFORM_REPOSITORY = re.compile(
    r"(?:^|/)futureagi/(" + "|".join(map(re.escape, PLATFORM_IMAGES)) + r")$"
)


def image_entries(node, path=""):
    if isinstance(node, dict):
        if "repository" in node and "tag" in node:
            yield path, node
        for key, value in node.items():
            yield from image_entries(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from image_entries(value, f"{path}[{index}]")


def stale_images(document, version):
    return [
        (path, str(node["repository"]), str(node["tag"]))
        for path, node in image_entries(document)
        if PLATFORM_REPOSITORY.search(str(node["repository"]))
        and str(node["tag"]) != version
    ]


def main(argv):
    if len(argv) < 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    version, files = argv[1], argv[2:]
    rc = 0
    for file in files:
        with open(file, encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
        stale = stale_images(document, version)
        if stale:
            rc = 1
            print(f"::error::{file} has platform images not bumped to {version}:")
            for path, repository, tag in stale:
                print(f"  {path}: {repository} = {tag}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
