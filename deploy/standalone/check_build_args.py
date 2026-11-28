"""Check literal arguments against the Dockerfile of each local image recipe."""

import os
import re
from collections import defaultdict
from pathlib import Path

DOCKERFILES = {
    "standalone": "deploy/standalone/Dockerfile",
    "future-agi": "futureagi/Dockerfile.oss",
    "frontend": "frontend/Dockerfile",
    "fi-collector": "fi-collector/Dockerfile",
    "agentcc-gateway": "agentcc-gateway/Dockerfile",
}
ARG = re.compile(r"""--build-arg['",\s]+([A-Za-z_][A-Za-z0-9_]*)=""")
RECIPE = re.compile(
    r"""^\s*(?:build_local_image|Build-Image)\s+['"]futureagi/([\w-]+):"""
)


def recipes(source, *, powershell):
    # Collapse one recipe invocation at a time, not all args across both files.
    # The backend variant is the only argument vector prepared separately.
    lines = iter(source.splitlines())
    variant = "$backendVariant" if powershell else "${backend_variant[@]}"
    pending = set()
    used_variant = False
    for line in lines:
        if not powershell:
            while line.endswith("\\"):
                line = line[:-1] + next(lines)
        elif RECIPE.match(line):
            while line.count("(") > line.count(")"):
                line += " " + next(lines)
        match = RECIPE.match(line)
        if "--build-arg" not in line and not match:
            continue
        args = ARG.findall(line)
        if line.count("--build-arg") != len(args):
            raise ValueError("unrecognized build argument syntax: " + line)
        if match:
            image = match[1]
            if image not in DOCKERFILES:
                raise ValueError("unknown local Dockerfile for " + image)
            if variant in line:
                args.extend(pending)
                used_variant = True
            yield image, args
        elif re.search(r"(?:\$backendVariant\s*=\s*@|backend_variant=)\(", line):
            pending.update(args)
        else:
            raise ValueError("build arguments outside an image recipe: " + line)
    if pending and not used_variant:
        raise ValueError("backend variant arguments did not reach an image recipe")


def main():
    arguments = defaultdict(set)
    for path in (Path("bin/lib/build-local.sh"), Path("bin/lib/build-local.ps1")):
        for image, names in recipes(path.read_text(), powershell=path.suffix == ".ps1"):
            arguments[image].update(names)
    for arg in os.environ["STANDALONE_BUILD_ARGS"].splitlines():
        if arg.strip():
            arguments["standalone"].add(arg.strip().split("=", 1)[0])
    for image, names in arguments.items():
        dockerfile = Path(DOCKERFILES[image])
        declared = set(re.findall(r"^\s*ARG\s+(\w+)", dockerfile.read_text(), re.M))
        if unknown := names - declared:
            raise SystemExit(
                f"undeclared build args in {dockerfile}: {sorted(unknown)}"
            )
        print(f"{dockerfile}: {sorted(names)}")


if __name__ == "__main__":
    main()
