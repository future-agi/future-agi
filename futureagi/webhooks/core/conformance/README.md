# Webhooks conformance vectors (conformance 1.4.0)

Plain JSON that the Python webhooks core (`futureagi/webhooks/core/`) and any port of it (for example a TypeScript
core in the OSS server) must pass byte for byte. Frozen from the webhook core design and B8 event contract `b8/1`; 1.1.0 adds cases and the `b8/1.1` rule named in `b8/fixtures.json`; 1.2.0 also unwraps the IPv4-translated prefix `::ffff:0:0:0/96` in `addresses.json`, so `::ffff:0:7f00:1` is `never`
like 127.0.0.1, and pins the hex spelling `::7f00:1` of the IPv4-compatible form. 1.3.0 adds NAT64 loopback cases: `64:ff9b::7f00:1` is `never` on hosted and under a self-hosted profile that lists
`64:ff9b::/96` and `127.0.0.0/8` in its private CIDRs (NEVER beats any CIDR). No rule and no existing expected value changed. 1.4.0
makes admission stricter: an embedded IPv4 address is judged as that address, so a NAT64, 6to4
or Teredo prefix in `private_cidrs` admits public targets behind it, and a private embedded IPv4 address must be inside
an IPv4 `private_cidrs` entry itself. The `self_hosted` profile also lists `2002::/16` and `2001::/32`; no existing
expected value changed.

| File | What it pins | Inputs → expected |
|---|---|---|
| `signing-v1.json` | v1 signing vectors shared by the SDK and server | secrets A/B/C, id, `t`, body → `v1` signatures, overlap header, must-pass and must-fail cases |
| `signing.json` | Standard Webhooks v1 sign and verify | `{secret(s), msg_id, timestamp, body_bytes_b64}` → `{signatures, webhook_signature, headers}`; verify cases → `ok` or a reason id; secret-format cases |
| `schedule.json` | retries and endpoint health | an attempt history → the next action (`retry` with `retry_at_ms`, `give_up`, `disable_endpoint`, `done`); an endpoint's attempts → disable reason, Failing, slow lane |
| `addresses.json` | URL rules (create, update, every attempt) and resolved-address rules | profile + URL (+ policy) → allow, or the API code and rule id; profile + answers + port → allow and the addresses to connect, or `ssrf_blocked` with the rule id |
| `payloads.json` | event bodies and fan-out | domain inputs + variant → exact body bytes; events + endpoints → deliveries with per-endpoint sequence numbers |
| `schemas/` | JSON Schema 2020-12 for every v1 event type and the B8 bodies | resolve `$ref` by `$id` across this folder |
| `b8/` | B8 notice validation fixtures | `valid/*.json` and `invalid/*.json` are raw notice bodies (no trailing newline; some invalid ones hold bytes no serializer writes); `fixtures.json` lists each one's expected status, code, field and the intake step that answers, with the intake rules; `delivered/run-completed.json` is the signed deployment run completion event |
| `manifest.json` | sha256 and size of every file above | vendored copies (EE, OSS server, SDK, ports) compare against it |

Each file has a `rules` block that states, in words, every rule its cases depend on. Where the design left a choice
open, that block says which one these files pin, so a port needs nothing outside this folder to follow them.

## Running them

- `python3 -I tools/vectors.py --check` regenerates every file in memory and compares bytes (CI-able);
  `--write` rewrites them. Standard library only.
- `node tools/check_node.mjs` is a second-language runner (Node standard library only): every signature and
  verify reason, every retry's jitter, the manifest, that each body equals `JSON.stringify(JSON.parse(body))`, the
  B8 intake steps before the schema, the context size rule, the deployment-name header rule, scrub-before-cut and
  the URL character rule.
- `tests/test_conformance_vectors*.py` (standard library only:
  `python3 -I -m unittest discover -s ../tests -p 'test_conformance_vectors*.py'`) and
  `tests/test_conformance_schemas*.py` (need `jsonschema`) check the files themselves; none imports Django. The
  address classes are also checked against a table typed in by hand, so `--write` cannot quietly change them.
- `tools/whconf/ref_*.py` restate the rules so the builder (`tools/whconf/cases_*.py`) can compute expected values.
  They are not the core: the core
  must pass these files on its own and must not import them.

A port writes a small runner per file: load the JSON, feed each case's inputs to its own function, compare with
`expected` (for bodies, compare `body_bytes_b64` after base64 decoding). Header names in `verify` cases are matched
case-insensitively; header order in `sign` cases is the send order.

## Changing them

- Additive cases, or a stricter rule that changes no existing expected value, bump the minor version (`1.2.0`); any
  change to an existing expected value bumps the major.
  `conformance_version` is in every file and in `tools/vectors.py`.
- B8 fixtures, schemas and `signing-v1.json` also follow the contract's own versioning (`b8/1`, `b8/1.1`, `b8/2`).
- After an edit: `python3 -I tools/vectors.py --write`, then run both test files, then commit the files and
  `manifest.json` together.

## B8 fixture path mapping

The contract names `futureagi/webhooks/contracts/...`; the canonical copy is here. The mapping:

| Contract path | Here |
|---|---|
| `contracts/b8/notice.v1.schema.json` | `schemas/b8/notice.v1.schema.json` |
| `contracts/b8/accepted.v1.schema.json` | `schemas/b8/accepted.v1.schema.json` |
| `contracts/b8/error.v1.schema.json` | `schemas/b8/error.v1.schema.json` |
| `contracts/events/deployment.run.v1.schema.json` | `schemas/events/deployment.run.v1.schema.json` |
| `contracts/b8/valid/*.json`, `contracts/b8/invalid/*.json` | `b8/valid/*.json`, `b8/invalid/*.json` (expected codes in `b8/fixtures.json`) |
| `contracts/b8/delivered/run-completed.json` | `b8/delivered/run-completed.json` |
