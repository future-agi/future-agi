# Connector live smoke (L4)

Checks connectors in the catalog (`agentcc-gateway/catalog/connectors`) against real test accounts, then writes a drift report. This is level L4 of the conformance levels in the connectors plan (scaling.md §5).

It is key-gated. It runs only when test-account keys are present, and it only reads unless you tell it to write against a sandbox tenant. It writes one report file and never pushes a change or opens a PR.

## Run it

```
cd scripts/connector-smoke
CONNECTOR_SMOKE_LIVE=1 CONNECTOR_SMOKE_KEYS="<the keys document, below>" \
  python3 -m connector_smoke --out ./out [--provider linear/linear-mcp]... [--allow-writes]
```

It needs Python 3.12 or later and PyYAML. Before anything is read, contacted or written, it checks three gates:

| Situation                                                       | Result                                             | Exit |
| --------------------------------------------------------------- | -------------------------------------------------- | ---- |
| `CONNECTOR_SMOKE_KEYS` is unset or empty                        | Skips cleanly. Nothing contacted or written.       | 0    |
| Keys are set, but `CONNECTOR_SMOKE_LIVE` is not exactly `1`     | Refuses to run. Nothing contacted or written.      | 3    |
| The keys document is malformed, or an entry has no test account | Configuration error. Nothing contacted or written. | 2    |

After the gates, it smokes each catalog provider that has a keys entry. A provider without one is reported as `no_key`.

`--timeout` sets the seconds allowed for each request, from the address lookup to the last byte read (default 60; above 0 and at most 600). Each address of the host gets only the time left, and none is dialled once it has run out. A server that sends slowly is cut off at that deadline and reported as `timeout`. `--provider` limits the run to the named providers; naming one the catalog does not have is a configuration error.

The report is written only if at least one provider was smoked on a named test account. Exit codes from a run:

- **0:** every provider it smoked passed, or none was smoked.
- **1:** drift or a failure, including a keys entry that does not fit its manifest (nothing is sent for that provider). A provider that was smoked but where nothing could be checked is `incomplete`, which also exits 1: it is not a pass.

A pass needs at least one check that carried the test account's credential and that the vendor accepted: the MCP session (K1), or `auth.verify` (an HTTP check, or a tool called over the session). A provider without one gets the check `credential` with status `incomplete` and reason `credential_not_exercised`. It is never a pass, even when every other check passed. If another check found drift, the provider stays `drift` and the marker shows that the account itself was not checked.

- **4:** the report or the summary would have held a credential or an email address, so nothing was written.

## The keys document

The keys document comes from the CI secret store. It is never a file in the repository or a command-line argument.

```json
{
  "format": "connector-smoke-keys/v1",
  "providers": {
    "linear/linear-mcp": {
      "testAccount": "fa-ci-linear",
      "bearer": "<the test account's access token>",
      "arguments": { "list_issues": { "team": "ENG" } }
    },
    "close/close-mcp": {
      "testAccount": "fa-ci-close",
      "parts": { "Close-API-Key": "<the test account's API key>" }
    }
  }
}
```

Fields of each entry:

- **`testAccount` (required):** a name for the account, never its login, email address or credential. It follows the same rule as a cassette's `test_account`. The runner records nothing for an account it cannot name.
- **`bearer`:** the access token for an `oauth2` or `mcp_oauth` provider, sent as `Authorization: Bearer`.
- **`parts`:** for an `api_key` provider, a value for each part in the manifest's `auth.credentialParts`. Each value goes in its header, query parameter or URL variable.
  - A non-secret constant comes from the catalog and is never supplied here. Close's `Close-Scope: mcp.read` is one.
  - An entry that sets a constant part is refused.
- **`urlVariables`:** values for the URL variables the manifest declares, checked against each variable's `pattern`.
- **`sandbox`:** `true` only when the account is a sandbox tenant, where writes may run.
- **`arguments`:** live arguments for a tool, by tool name. They replace a cassette's arguments, and are needed where the cassette scrubber replaced an id.

Unknown fields are refused. Every value an entry gives is either used or reported with a named reason; none is ignored silently.

## What it checks

**A vendor MCP server (K1, Streamable HTTP).** The smoke connects at the manifest's era (2026-07-28 stateless, or a legacy session), then runs these checks:

| Check         | What it does                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `toolsHash`   | Hashes the live `tools/list` with the gateway's `ToolsHash` (`internal/mcp/probe.go`; `tests/fixtures/toolshash-vectors.json` holds Go's own hashes) and compares it with `tools.snapshot.toolsHash`. On drift, the report gives the live hash, the count and each tool's own hash, so a maintainer can review the change and update the snapshot.                                                                                                                                                                                                                                                                                                                                    |
| `annotations` | Flags any tool the manifest calls read-only whose live annotations say it writes. It is `skipped` when the manifest declares no read-only tool, or none of them is in the live list, because then nothing was compared.                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `verify`      | Runs `auth.verify`: a tool (under the rules below) or an HTTP check.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `call`        | Repeats each `tools/call` recorded in the provider's MCP cassettes against the live server. The live result's key paths must cover the cassette's: a missing key is drift, named by path. Arrays are one segment, and scrubbed record ids match any name. A key is unverified, not missing, only when no live item holds a value there: under a `null` or empty live array, or under a record id when the live object holds no records (`{}`). Array items merge, so when one item has `null` or `[]` and another holds an object, a key that object lacks is missing. A named field under an empty live object is missing. A tool the keys entry gives arguments for is also called. |

**Our REST connector (K2).** It runs `verify` when that is an HTTP check, and `spec`, which compares the vendor's API description with `upstream.spec.sha256`. Its tools are served by `agentcc-connectors`, which only the gateway can reach, so they are not listed or called here. Its HTTP cassettes record no vendor host to repeat them against, so they are counted but not compared.

The spec is a public document, fetched without the credential, so it proves nothing about the account. A K2 provider whose `auth.verify` names a tool is reported `verify=skipped` (`tool_served_by_agentcc_connectors`) and finishes `incomplete`: this runner cannot give it an L4 pass. To smoke a K2 provider's account, its manifest declares `auth.verify.http`, such as Gmail's `GET https://gmail.googleapis.com/gmail/v1/users/me/profile`.

**Not smoked, each with a named reason:** partner providers (K3), local stdio packages (K4), SSE-only servers, a vendor MCP server whose manifest pins an era the client does not speak (`era_unsupported:<era>`; it speaks `auto`, 2026-07-28, 2025-11-25, 2025-06-18 and 2025-03-26), and the auth kinds `basic`, `service_account`, `partner` and `local_setup`.

## Which tools it calls

- **The manifest decides.** A tool the manifest does not declare is never called.
- **Read-only is the default.** A tool is read-only when its manifest annotations say `readOnlyHint: true` and not `destructiveHint`.
- **A live annotation may narrow, never widen.** A manifest read-only tool whose live server explicitly says it writes is not called.
- **Writes need two things at once:** `--allow-writes` on the run, and `sandbox: true` on the keys entry. This covers destructive tools too. The scheduled CI run never passes `--allow-writes`.

## Safety

- **HTTPS only.** It never follows a redirect, and it dials a host only at a public address. Private, loopback, link-local (cloud metadata) and reserved addresses are refused.
- **Errors are short codes.** They never carry a URL, which could hold a key.
- **No live values in the report or the output.** They hold statuses, reason codes, counts, hashes, tool names, cassette file names and the key paths a cassette recorded. Vendor text is data: a live tool name that is not a plain tool name appears only as its hash.
- **Scanned before writing.** The serialized report is checked for every credential value in the keys document, credential shapes and email addresses. Any hit means nothing is written.
- **One file.** The report (`drift-report.json`, format `connector-smoke-report/v1`) is the only file it writes. It never runs git, pushes, or opens a PR.

## CI

`.github/workflows/connector-smoke.yml` has two jobs:

- **`tests`** runs the offline tests on every change to this directory. It uses no secrets.
- **`smoke`** runs the live smoke daily and on manual dispatch. It runs only after an operator does both of these:

  1. sets the repository variable `CONNECTOR_SMOKE_ENABLED` to `true`;
  2. stores the keys document in the secret `CONNECTOR_SMOKE_KEYS` of the GitHub environment `connector-smoke`, with test accounts from OA-T, and limits that environment's deployment branches to `main`.

  The `smoke` job is read-only, never runs on a pull request, and keeps the report as an artifact. It runs only from `main`: a dispatch from another branch is skipped. That check is in the workflow file, so a writer could remove it on their own branch. The environment's branch rule is what keeps the keys from a branch, which is why the secret belongs in the environment and not at repository level.

  Every action is pinned by commit SHA, and checkouts keep no git credential.

## Tests

```
python3 -m pytest scripts/connector-smoke/tests -q
```

The tests need pytest, PyYAML and the `openssl` command, which makes a throwaway local certificate. A local HTTPS server plays each vendor host, so the runner's real TLS, HTTP and MCP code runs unchanged. No test reaches an address outside 127.0.0.1.

`tests/fixtures/catalog` is a small catalog that passes the Go loader's L0 and L1. Its cassettes pass `agentcc-cassette check`.
