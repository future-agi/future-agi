#!/usr/bin/env bash
# Package the chart the way .github/workflows/helm-release.yml publishes it.
# The chart is copied to a temporary directory and only that copy is changed:
#   * values.yaml image.digests gets the digest of every Future AGI image at
#     the chart's appVersion (the components are the keys of image.digests in
#     values.schema.json; the chart applies a digest only while that
#     component's tag is the appVersion and image.pinDigests is on)
#   * Chart.yaml gets the Artifact Hub annotations written at release time:
#     artifacthub.io/images (every image, with digests), artifacthub.io/changes
#     (from the release notes, when given) and
#     artifacthub.io/containsSecurityUpdates
# then `helm package` writes futureagi-<version>.tgz. The path of the package
# is the only thing printed on stdout.
#
#   deploy/helm/futureagi/hack/package.sh [options]
#
# Options:
#   --destination DIR     where the .tgz goes (default: the current directory)
#   --version X.Y.Z       fail unless Chart.yaml has this version and
#                         appVersion vX.Y.Z
#   --no-digests          stamp no digest: a dry run, e.g. before the images
#                         exist
#   --digests-from-local  take each digest from the local Docker daemon (the
#                         RepoDigests of images pulled at the appVersion tag)
#                         instead of the registry
#   --digests-file FILE   take them from FILE, `component=sha256:...` per line
#   --changes FILE        release notes (release-please Markdown) to turn into
#                         artifacthub.io/changes; (helm)-scoped entries first
#   --security            mark the release as containing security updates
#                         (also set when the notes have a security entry)
#
# By default the digests come from the registry (docker buildx imagetools,
# crane or oras). HELM and PYTHON (Python 3 with PyYAML) name the binaries
# (default: from PATH).
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh
. "$here/lib.sh"
chart=$(cd "$here/.." && pwd)
helm=${HELM:-helm}
python=${PYTHON:-python3}
export PYTHON="$python" HELM="$helm"

destination=.
expected_version=""
digests=registry
digests_file=""
changes=""
security=false

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2
  exit "${1:-2}"
}
fail() {
  echo "package: $*" >&2
  exit 1
}
log() { echo "package: $*" >&2; }

while [ $# -gt 0 ]; do
  case $1 in
    --destination | --version | --digests-file | --changes)
      [ $# -ge 2 ] || usage
      case $1 in
        --destination) destination=$2 ;;
        --version) expected_version=$2 ;;
        --digests-file)
          digests="file"
          digests_file=$2
          ;;
        --changes) changes=$2 ;;
      esac
      shift
      ;;
    --no-digests) digests=none ;;
    --digests-from-local) digests=local ;;
    --security) security=true ;;
    -h | --help) usage 0 ;;
    *) fail "unknown argument $1 (see --help)" ;;
  esac
  shift
done

version=$(sed -nE 's/^version: *"?([^" #]+)"?.*/\1/p' "$chart/Chart.yaml")
app_version=$(sed -nE 's/^appVersion: *"?([^" #]+)"?.*/\1/p' "$chart/Chart.yaml")
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "Chart.yaml version '$version' is not X.Y.Z"
[ "$app_version" = "v$version" ] || fail "Chart.yaml appVersion '$app_version' must be v$version"
if [ -n "$expected_version" ] && [ "${expected_version#v}" != "$version" ]; then
  fail "Chart.yaml has version $version, not ${expected_version#v}"
fi
[ -z "$changes" ] || [ -f "$changes" ] || fail "no release notes at $changes"
[ -z "$digests_file" ] || [ -f "$digests_file" ] || fail "no digests file at $digests_file"
mkdir -p "$destination"
destination=$(cd "$destination" && pwd)

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cp -R "$chart" "$work/futureagi"
copy=$work/futureagi

# component registry/repository, one per line, for every key of image.digests.
"$python" - "$copy/values.yaml" "$copy/values.schema.json" >"$work/components.txt" <<'PY' ||
import json
import sys

import yaml

with open(sys.argv[1]) as f:
    values = yaml.safe_load(f)
with open(sys.argv[2]) as f:
    schema = json.load(f)
digests = schema["properties"]["image"]["properties"].get("digests", {}).get("properties")
if not digests or "digests" not in values.get("image", {}):
    sys.exit("values.yaml and values.schema.json have no image.digests: this chart cannot apply stamped digests")
default_registry = values["image"].get("registry") or "docker.io"
for component in digests:
    image = (values.get(component) or {}).get("image") or {}
    if not image.get("repository"):
        sys.exit(f"image.digests.{component} has no {component}.image.repository in values.yaml")
    registry = image.get("registry") or default_registry
    print(component, f"{registry.rstrip('/')}/{image['repository']}")
PY
  fail "cannot read the image.digests components"

resolve_local() { # registry/repository, tag -> digest from RepoDigests
  local repo=$1 tag=$2 short
  short=${repo#docker.io/}
  short=${short#library/}
  docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$short:$tag" 2>/dev/null |
    awk -F@ -v a="$repo" -v b="$short" \
      '$1 == a || $1 == b || $1 == "docker.io/" b || $1 == "library/" b || $1 == "docker.io/library/" b { print $2; exit }'
}

: >"$work/digests.txt"
case $digests in
  registry) origin="the registry (docker buildx imagetools, crane or oras)" ;;
  local) origin="the local Docker daemon (RepoDigests)" ;;
  *) origin=$digests_file ;;
esac
if [ "$digests" != none ]; then
  while read -r component repo; do
    case $digests in
      registry) digest=$(resolve_digest "$repo:$app_version") || digest="" ;;
      local) digest=$(resolve_local "$repo" "$app_version") || digest="" ;;
      file) digest=$(sed -nE "s/^$component=(sha256:[a-f0-9]{64})[[:space:]]*$/\1/p" "$digests_file" | head -n1) || digest="" ;;
    esac
    [[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]] ||
      fail "no digest for $component ($repo:$app_version) from $origin: publish or pull that image first, or pass --no-digests"
    log "$component $repo:$app_version@$digest"
    echo "$component=$digest" >>"$work/digests.txt"
  done <"$work/components.txt"
else
  log "no digests stamped (--no-digests)"
fi

# Stamp values.yaml: rewrite only the image.digests lines, keeping every
# comment (helm package ships values.yaml byte for byte).
"$python" - "$copy/values.yaml" "$work/digests.txt" <<'PY' || fail "cannot write image.digests into values.yaml"
import re
import sys

import yaml

path, digests_path = sys.argv[1], sys.argv[2]
with open(digests_path) as f:
    digests = dict(line.strip().split("=", 1) for line in f if line.strip())
if not digests:
    sys.exit(0)
with open(path) as f:
    lines = f.read().splitlines(keepends=True)
start = next(i for i, line in enumerate(lines) if re.match(r"image:\s*(#.*)?$", line))
end = next((i for i in range(start + 1, len(lines)) if re.match(r"\S", lines[i])), len(lines))
key = [i for i in range(start + 1, end) if re.match(r"  digests:\s*(\{\s*\})?\s*(#.*)?$", lines[i])]
if len(key) != 1:
    sys.exit("values.yaml: expected one `  digests:` line under the top-level image:")
first = key[0]
last = first + 1
while last < end and re.match(r"    \S", lines[last]):
    last += 1
stamped = ["  digests:\n"] + [f"    {component}: \"{digest}\"\n" for component, digest in digests.items()]
lines[first:last] = stamped
text = "".join(lines)
if yaml.safe_load(text)["image"]["digests"] != digests:
    sys.exit("values.yaml: image.digests did not round-trip")
with open(path, "w") as f:
    f.write(text)
PY

# The packaged README's values table then shows the stamped default, so the
# package is self-consistent (helm-ci.yml and helm-release.yml run
# hack/check.sh, values_docs.py --check included, against it). The schema must
# not change.
if [ -s "$work/digests.txt" ]; then
  "$python" "$copy/hack/values_docs.py" >&2 || fail "cannot regenerate the README values table"
  cmp -s "$chart/values.schema.json" "$copy/values.schema.json" ||
    fail "stamping the digests changed values.schema.json"
fi

# Every image of the stamped chart, for artifacthub.io/images. With registry
# digests, the third-party images are pinned too.
list_args=()
[ "$digests" = registry ] && list_args+=(--resolve)
# ${a[@]+...}: an empty array is "unbound" under set -u in bash 3.2 (macOS).
"$here/list-images.sh" ${list_args[@]+"${list_args[@]}"} "$copy" >"$work/images.txt"

"$python" - "$copy/Chart.yaml" "$work/images.txt" "${changes:-}" "$security" <<'PY' || fail "cannot write the Artifact Hub annotations"
import re
import sys

import yaml

chart_path, images_path, changes_path, security = sys.argv[1:5]


class Literal(str):
    pass


yaml.SafeDumper.add_representer(
    Literal, lambda dumper, data: dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
)


def dump(value):
    return Literal(yaml.safe_dump(value, sort_keys=False, default_flow_style=False, width=1000))


with open(chart_path) as f:
    chart = yaml.safe_load(f)
annotations = chart.setdefault("annotations", {})
for key, value in annotations.items():
    if isinstance(value, str) and "\n" in value:
        annotations[key] = Literal(value)

images, seen = [], set()
with open(images_path) as f:
    for ref in (line.strip() for line in f):
        if not ref:
            continue
        name = ref.split("@")[0].rsplit(":", 1)[0].rsplit("/", 1)[-1]
        if name in seen:
            name = f"{name}-{ref.split('@')[0].rsplit(':', 1)[1]}"
        seen.add(name)
        images.append({"name": name, "image": ref})
annotations["artifacthub.io/images"] = dump(images)

# release-please notes: "### Features" / "### Bug Fixes" / ... sections of
# "* **scope:** text ([#123](url)) ([abc1234](url))" bullets.
kinds = {"features": "added", "bug fixes": "fixed", "security": "security", "reverts": "changed"}
changes, contains_security = [], security == "true"
# A fix merged twice (e.g. to dev and main) is listed twice, with different
# commits: one entry per (kind, description), with the PR links of every copy.
by_entry = {}
if changes_path:
    section = ""
    with open(changes_path) as f:
        for line in f:
            heading = re.match(r"#{2,4}\s+(.*?)\s*$", line)
            if heading:
                section = heading.group(1).strip().lower()
                continue
            bullet = re.match(r"\s*[*-]\s+(.*\S)\s*$", line)
            if not bullet or not section or re.match(r"\[?\d+\.\d+\.\d+", section):
                continue
            text = bullet.group(1)
            scope_match = re.match(r"\*\*([^*:]+):\*\*\s*(.*)", text)
            scope, text = (scope_match.group(1).strip(), scope_match.group(2)) if scope_match else ("", text)
            links = [
                {"name": name, "url": url}
                for name, url in re.findall(r"\[(#\d+)\]\((https?://[^)\s]+)\)", text)
            ]
            text = re.sub(r"\(\[[^\]]*\]\([^)]*\)\)", "", text)  # ([#123](url)) and ([sha](url))
            text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # remaining links: keep the text
            text = re.sub(r"\s{2,}", " ", text).strip().rstrip(",;")
            if not text:
                continue
            kind = kinds.get(section, "changed")
            if "security" in scope.lower() or re.search(r"\bCVE-\d{4}-\d+", text):
                kind = "security"
            contains_security = contains_security or kind == "security"
            change = {"kind": kind, "description": f"{scope}: {text}" if scope else text}
            seen_change = by_entry.get((kind, change["description"]))
            if seen_change is not None:
                for link in links:
                    if link not in seen_change.setdefault("links", []):
                        seen_change["links"].append(link)
                continue
            by_entry[(kind, change["description"])] = change
            if links:
                change["links"] = links
            helm = scope.lower() in ("helm", "chart", "charts") or scope.lower().startswith("helm")
            changes.append((0 if helm else 1, change))
    if changes:
        annotations["artifacthub.io/changes"] = dump([c for _, c in sorted(changes, key=lambda c: c[0])])
annotations["artifacthub.io/containsSecurityUpdates"] = "true" if contains_security else "false"

with open(chart_path, "w") as f:
    yaml.safe_dump(chart, f, sort_keys=False, default_flow_style=False, width=1000)
PY

"$helm" package "$copy" --destination "$destination" >&2
package="$destination/futureagi-$version.tgz"
[ -f "$package" ] || fail "helm package did not write $package"
printf '%s\n' "$package"
