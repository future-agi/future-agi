# Gateway supply chain: how a release is built and how to check it

The gateway holds every provider key and tool grant it routes for, so a
tampered build is worth more to an attacker than a tampered app. This page
says what the release chain proves, where, and how you check a release
yourself. Two kinds of claim are kept apart:

- **Proven offline.** `agentcc-gateway/scripts/release/verify-local.sh` runs
  it on a laptop and in CI, and fails if any part fails.
- **CI only.** Signing, provenance and the checksum bundle need GitHub's OIDC
  identity and Sigstore. They run in
  `.github/workflows/release-agentcc-gateway.yml` and cannot be run or
  proven locally. Nothing on this page claims they have run until a release
  has been made with them.

## What verify-local.sh proves

Run it from the repository root. It needs no network
(Go runs with `GOPROXY=off` unless you set `GOPROXY`), and prints PASS, FAIL
or BLOCKED for each check, then exits 1 unless all five pass:

```bash
bash agentcc-gateway/scripts/release/verify-local.sh
```

| Check              | What passes means                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | Script                  |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------- |
| `go mod verify`    | Every module of the build that the local module cache holds matches its hash in `go.sum`, and the whole module graph loads.                                                                                                                                                                                                                                                                                                                                                                                                                                          | Go                      |
| Reproducible build | `cmd/agentcc`, built twice from two copies of the source in two temp directories with two empty build caches, gives the same sha256. Flags: `CGO_ENABLED=0 go build -trimpath -buildvcs=false -ldflags="-s -w"`, with a fixed `SOURCE_DATE_EPOCH`.                                                                                                                                                                                                                                                                                                                   | `build-reproducible.sh` |
| SBOM               | The CycloneDX 1.5 SBOM lists every module that `go list -m all` lists, and names the main module as its subject.                                                                                                                                                                                                                                                                                                                                                                                                                                                     | `sbom.sh`               |
| Secrets lint       | The lint reports exactly the lines its fixture `testdata/leaky.yaml` should give (`testdata/leaky.expected`), stays quiet on `testdata/clean.yaml`, and finds no key-shaped value in the shipped configs: `agentcc-gateway/config*.yaml` and `scripts/scenarios/config/**`.                                                                                                                                                                                                                                                                                          | `secrets-lint.sh`       |
| Release workflow   | The checker first reports exactly what `testdata/unpinned-workflow.expected` lists for a fixture that breaks every rule. Then: every `uses:` in `release-agentcc-gateway.yml` names an action at a full 40-character commit SHA (a reusable workflow in this repository, `./...`, runs at the same commit and is allowed; the actions it uses are printed as a NOTE), the workflow has steps with the ids `sbom`, `checksums`, `provenance` and `sign` that run the SBOM script, `sha256sum`, `cosign attest` and `cosign sign`, and a job grants `id-token: write`. | `verify-local.sh`       |

BLOCKED means a Go command needed a module that is not in the module cache,
and offline mode may not fetch it. Fetch it once with network
(`cd agentcc-gateway && go mod download all`) and run again.

When the cache holds every module the build needs but not the `go.mod` file of
every module in the graph, `go list -m all` fails offline: it reads each
module's `go.mod` for its Go version. The script then compares the SBOM with
`go list -m -e all`, which prints the same module list, and says so in its
output. It does this only when every error is a failed lookup and
`go mod verify` has loaded the whole module graph. CI runs
`go mod download all` first, so there the plain command is used.

### Reproducible build

`build-reproducible.sh [--out DIR]` builds for the host by default; set
`GOOS` and `GOARCH` for another platform. With `--out`, it keeps the binary
as `DIR/agentcc-gateway_<goos>_<goarch>` and writes its checksum line next to
it. The flags `-trimpath` and `-s -w` are the Dockerfile's, and with
`GOTOOLCHAIN` set to the `go.mod` toolchain, as the release workflow sets it,
so is the toolchain: the binary is built the way the image builds it.

What this proves: the build does not depend on the checkout's location or on
cached build state, for one toolchain on one host. It does not prove that two
different hosts produce the same bytes. Go builds with `CGO_ENABLED=0` and
`-trimpath` are designed not to depend on the host, so a rebuild at the same
commit with the same toolchain should match a release's `SHA256SUMS`. You can
check that yourself (below); this repository does not test it.

The Dockerfile builds the image's own binary inside its pinned builder image
with the `go.mod` toolchain (it sets `GOTOOLCHAIN`; `cmd/agentcc`
`TestImageBuildsTheGatewayWithGoModToolchain` fails when the two differ) and
the same flags. On 2026-10-23 the release gate found the image's binary
byte-identical to this script's at the same commit, on linux/amd64 and
linux/arm64. The release binaries, the SBOM and the image digest are still
each signed or checksummed on their own.

### SBOM

`sbom.sh [--binary FILE] [--out FILE]` writes `agentcc-gateway.cdx.json` by
hand from the Go toolchain's own output, so no SBOM tool is installed:

- one component per module in `go list -m -json all`, with its version and
  `pkg:golang/...` package URL;
- each module's `h1:` hashes from `go.sum`, as the properties `golang:h1`
  (the module zip) and `golang:gomod-h1` (its `go.mod`). An `h1:` value is a
  SHA-256 over a sorted file list, not a digest of one file, so it is not put
  in CycloneDX `hashes`, where a consumer would expect a file digest;
- `golang:linked` and `scope` (`required` or `excluded`) from
  `go version -m` on the binary: whether the module is linked into it;
- the binary's sha256, its Go toolchain and its build settings on the
  subject, `metadata.component`.

It fails if the `h1:` of a module linked into the binary differs from
`go.sum`. Its timestamp is `SOURCE_DATE_EPOCH` and its serial number is
derived from the binary's sha256, so the same binary and `go.sum` always give
the same SBOM, byte for byte.

### Secrets lint

`secrets-lint.sh <file>...` fails on YAML values shaped like provider keys:
`sk-ant-`, `sk-` (20 or more key characters, so `sk-proj-` and `sk-svcacct-`
too), `AIza`, `xoxb-` and `xoxp-`, `ghp_`, `github_pat_`, AWS access key ids
(`AKIA` and `ASIA`), and private-key PEM blocks. It prints
`<file>:<line>: <rule> (<first 4 characters>...)` and never the value.

It reads values, not comments, so the placeholder keys in
`config.example.yaml`'s comments pass. A `#` inside a quoted value does not
start a comment, and every line of a `|` or `>` block scalar is read. These
pass on purpose:

- `${NAME}` environment references. The default in `${NAME:-literal}` is a
  literal, and is still read.
- Any key whose name ends in `_ref`, which names where a secret lives.
- Short placeholders such as `sk-...`.
- PEM blocks that hold no private key. `config.license_auth.example.yaml`
  ships a `PUBLIC KEY` block, which is not a secret.

Exit status: 0 clean, 1 findings, 2 for no file or an unreadable one.

## What the release workflow does (CI only)

`release-agentcc-gateway.yml` is started by hand (`workflow_dispatch`) with a
version and a branch. A `prepare` job refuses a run dispatched from any ref
but `main`, resolves the branch to one commit and reads the Go toolchain from
`go.mod`; every later job uses that commit.

| Job        | Steps                                                                                                                                                                                                                                                                                                                                                   |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `release`  | Builds and publishes the multi-arch image through `build-image-multiarch.yml`, the build every image uses, only after `binaries` passed, so `verify-local.sh` gates the image tag too.                                                                                                                                                                  |
| `binaries` | `go mod download all` (checked against `go.sum`); `verify-local.sh` as a gate; reproducible `linux/amd64` and `linux/arm64` binaries; the SBOM (`id: sbom`); `SHA256SUMS` over the two binaries and the SBOM (`id: checksums`); a keyless Sigstore bundle for `SHA256SUMS`. All of them are uploaded as the run's artifact `agentcc-gateway-<version>`. |
| `attest`   | Checks that the published image's `org.opencontainers.image.revision` label is the commit `prepare` resolved, then adds a SLSA v1 provenance attestation (`cosign attest --type slsaprovenance1`, `id: provenance`) and a keyless signature (`cosign sign`, `id: sign`) to the image digest.                                                            |

No release has been made with this workflow yet. A release run starts from
`main`, and on `main` (last checked at `86f1c7c4fa`, 2026-10-12) neither
this workflow nor `release-images.yml`, which publishes the tagged gateway
image, signs or attests a gateway image; `release-images.yml` signs only the
Helm chart. So no published gateway image or binary is signed, attested or
covered by a `SHA256SUMS` bundle yet.

Signing is keyless: cosign gets a short-lived certificate for the job's
GitHub OIDC identity, and the signature is logged in Sigstore's public
transparency log. No signing key is stored anywhere.

Pinning and installs:

- Every action the workflow names is pinned by full commit SHA, with the
  release tag in a comment.
- Go and cosign are not installed by actions. The runner's own Go (on `PATH`
  or in the runner's tool cache) fetches the toolchain `go.mod` names, and
  `go install github.com/sigstore/cosign/v3/cmd/cosign@v3.1.3` builds cosign.
  The Go command checks both against the public checksum database
  (`sum.golang.org`).

What the first release run proves. None of the CI steps can run outside
GitHub Actions, so these assumptions are unchecked until a release runs with
this workflow:

- The runner has a Go 1.21 or newer on `PATH` or in its tool cache
  (`/opt/hostedtoolcache/go`); the job fails with "no bootstrap Go on this
  runner" otherwise.
- `github.com/sigstore/cosign/v3/cmd/cosign@v3.1.3` is cosign's module path
  at that version and installs with `go install`. The version comes from a
  package index that names the git tag, not the Go module path.
- cosign 3.x takes the flags used: `sign-blob --yes --bundle`,
  `attest --yes --type slsaprovenance1 --predicate`, and `sign --yes`.
- Docker Hub stores the signature and attestation cosign 3.x pushes for the
  image digest.
- `build-image-multiarch.yml` builds the same way when its `ref` is a commit
  SHA, which this workflow now passes, as when it is a branch name.

Also covered by `verify-local.sh`:

- The image build runs through `build-image-multiarch.yml`, which every image
  release shares; `verify-local.sh` prints the actions it uses as a NOTE on
  every run.
- Stable releases go through `release-images.yml`, which does not run these
  steps. This workflow is the manual and `-dev` path.

## Checking a release yourself

You need `cosign` 3.x (the release signs with v3.1.3 and its bundle format),
Go, and the run's artifact. The signer identity is the release workflow in
this repository, run from `main`: a manual run takes its workflow file from
the ref it is dispatched from, so the identity is anchored to that ref (a
copy of the workflow changed on another branch signs as that branch), and
`prepare` refuses a run dispatched from any other ref. The `branch` input
only chooses the source that `main`'s workflow builds.

```bash
ID='^https://github.com/future-agi/future-agi/\.github/workflows/release-agentcc-gateway\.yml@refs/heads/main$'
ISSUER=https://token.actions.githubusercontent.com
IMAGE=docker.io/futureagi/agentcc-gateway@sha256:<digest>

# The image digest is signed by the release workflow.
cosign verify --certificate-identity-regexp "$ID" --certificate-oidc-issuer "$ISSUER" "$IMAGE"

# It carries a SLSA v1 provenance attestation from the same workflow. The
# attestation's resolvedDependencies names the commit.
cosign verify-attestation --type slsaprovenance1 \
  --certificate-identity-regexp "$ID" --certificate-oidc-issuer "$ISSUER" "$IMAGE"

# The checksums are signed, and the files match them.
cosign verify-blob --bundle SHA256SUMS.sigstore.json \
  --certificate-identity-regexp "$ID" --certificate-oidc-issuer "$ISSUER" SHA256SUMS
sha256sum -c SHA256SUMS

# Rebuild at that commit with the release's toolchain, and compare.
git checkout <commit>
TOOLCHAIN=$(jq -r '.metadata.component.properties[] | select(.name == "golang:toolchain") | .value' agentcc-gateway.cdx.json)
(cd agentcc-gateway && GOTOOLCHAIN=$TOOLCHAIN go mod download all) # the one online step
GOTOOLCHAIN=$TOOLCHAIN GOOS=linux GOARCH=amd64 \
  bash agentcc-gateway/scripts/release/build-reproducible.sh --out /tmp/rebuild
cat /tmp/rebuild/agentcc-gateway_linux_amd64.sha256
```

Set `GOTOOLCHAIN` as above: with the default `auto`, a Go newer than the one
`go.mod` names builds with itself, and its bytes differ. The script prints the
toolchain it used.
