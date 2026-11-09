# Omega gateway

The fail-closed edge between a FutureAGI browser session and Omega EE. The browser calls
`/omega/api/<route>`; the gateway admits only the routes in the route manifest and sends EE
exactly one credential: a customer host assertion minted by FutureAGI's
`POST /accounts/omega/session-exchange/`.

## Request path

Each step is its own module under `src/`.

1. **Trace and request id** (`trace.mjs`). An inbound W3C `traceparent` is continued, or a
   trace starts. The trace id is the `x-request-id` the browser sees and the `requestId` in
   every error body; Django and EE receive the same trace as children of the gateway span.
2. **Probes and drain** (`lifecycle.mjs`). `/healthz` is liveness. `/readyz` is ready only when
   EE's readiness path answers HTTP 200 with an `application/json` object containing
   `status: "ready"` and `ready: true` (at most 64 KiB), and the unauthenticated session
   exchange GET answers 401 (cached 2 s). Redirects and other responses fail closed.
   While draining, new requests get `503 omega_gateway_draining`.
3. **Route decision, wall W2** (`routes.mjs`, `manifest.mjs`). Generated from
   `manifest/console-route-manifest.v1.json`, byte-identical with Omega EE's copy (its sha256 is
   pinned in `manifest/console-route-manifest.v1.sha256`). Default deny: a method and path with no
   `routes` entry is `403 route_not_allowed`. The `/os` namespace, every `eeOnly` family
   (`/auth`, `/billing`, `/files`, the operator projections), member and invite writes and the
   billing POSTs are closed because the manifest admits none of them to a browser. Paths are
   matched raw after the `%` rule: `%20` opens, an encoded `/ \ . %`, NUL or control character
   is refused, as are `//`, `.` and `..` segments, an encoded unreserved character (`A-Z a-z
0-9 - . _ ~`, which `encodeURIComponent` never produces: `%73ettings` would otherwise spell
   the `settings` literal past the `{sandbox}` pattern) and `#` (fetch would cut the path
   there). A request target containing `#` anywhere is `400 bad_request_target`. EE's
   `hostPathEncodingAllowed` (lane E-AUTH) does not have the last two refusals yet; until it
   does, W2 is the stricter wall.

   **Hidden features** (manifest `hidden`, HIDE-API). Skills, schedules, agent teams and
   environments are not shown in the Console, and EE's embedded Console registry
   (`shownFeatures.ts`) reads the same list, so screens and routes are one switch. Their 24
   routes keep their manifest classes; after the credential, the product wall and the rate limit,
   and before the role floor, the gateway answers each of them `404` in EE's own envelope
   (`schemaVersion`, `error: { code: "not_found", message }`, `code`, `message`, `request_id`),
   the body EE gives an Integrations app that is not offered, for every role, and never forwards
   it (refusal reason `feature_hidden`). EE's wall W3 answers them the same way. Deleting a
   feature's entry from all three manifest copies brings its routes back at their role floor.

4. **Credential** (`credential.mjs`). Either:

   - `Authorization: Bearer omega_delegate_…`: a customer assertion minted elsewhere (the Console
     key pool). It is checked (audience `omega-squad`, role owner/admin/member, lifetime at most
     45 s and unexpired, replay ids, signature against `OMEGA_ASSERTION_JWKS_FILE`) and forwarded
     once, unchanged. `X-Organization-Id`/`X-Workspace-Id`, if sent, must match its claims
     (`403 omega_scope_mismatch`).
   - `Authorization: Bearer <FutureAGI browser token>`: exchanged for a fresh assertion. Django's
     refusals keep their meaning: a viewer is `403 omega_viewer_not_admitted`, a throttled user
     `429` with Django's `Retry-After`, an unreachable or failing issuer `503
omega_issuer_unavailable`; any other refusal is `401 omega_session_unavailable`.

   Either way the caller is proven, admitted (step 5) and limited **before the body is read**, so
   a stranger can never make the gateway buffer an upload. If reading a slow upload left a
   browser-token key with under 20 s, it is minted again before it goes to EE. A presented
   assertion cannot be minted again: if it expired while its upload was read, it is refused
   `401 omega_assertion_rejected` with reason `expired_assertion` (the code the Future AGI
   client retries with a fresh key) and never forwarded. A presented assertion that is still
   valid when the body ends is forwarded as is, so a key with a few seconds left can still
   expire on its way to EE; streaming the body to EE is the full fix (owed).

5. **Role floor and rate limit** (`routes.mjs`, `ratelimit.mjs`). The route's manifest `role`
   floor is checked against the assertion (`403 omega_role_not_permitted`), then one token is
   taken from every bucket of the route's rate class:

   | Class          | Limit                                         | Keyed on                                                                          |
   | -------------- | --------------------------------------------- | --------------------------------------------------------------------------------- |
   | `read`         | 30/s, burst 60                                | user and workspace                                                                |
   | `write`        | 5/s, burst 10                                 | user and workspace                                                                |
   | `model-start`  | 20/min per user **and** 120/min per workspace | user and workspace; workspace                                                     |
   | `public-share` | 60/min                                        | client IP (closed until CP wave 3: `OMEGA_GATEWAY_PUBLIC_SHARE`)                  |
   | `channel`      | 40/s, burst 120                               | user and workspace of the key that minted the channel token (channel routes only) |

   Over the limit: `429 omega_rate_limited` with `Retry-After` and `retryAfterSeconds`. Buckets
   live in Redis (`ratelimit-redis.mjs`, one Lua script on Redis's clock), so a limit holds
   across replicas. The connection is opened at start. If Redis fails or misses its 150 ms
   command deadline, each class follows owner decision OD-07 (READINESS CH-08):

   | Class                  | Redis down or wedged                                                                                                                   |
   | ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
   | `read`                 | this replica's own buckets (reads keep working; the limit is per replica for the outage)                                               |
   | `public-share`         | the same as `read` (it is a read; our reading of OD-07, and the class is closed until wave 3)                                          |
   | `channel`              | the same as `read`: a channel token is only known to the replica that carried its mint, so its requests already reach that one replica |
   | `write`, `model-start` | refused: `503 omega_rate_limit_unavailable`, reason `rate_store_unavailable`, `Retry-After: 2`; nothing reaches EE                     |
   | any other class        | refused (fail closed)                                                                                                                  |

   Fallbacks and refusals are counted (`rateLimiter.stats`) and each logged at most once a
   minute. `/readyz` deliberately does not include Redis: an outage stops writes and model
   starts, it does not take the gateway (and its reads) out of service. Receipt:
   `.omega/program/F-GATE/component/ch08-redis-outage.jsonl`.

6. **EE** (`upstream.mjs`). What W2 matched is what EE receives: if the URL fetch would send
   has any other path (a character the URL parser re-encodes, a dot segment, a fragment), the
   request is `400 bad_request_target` and nothing is sent. Only `accept`, `content-type`, `idempotency-key`, `if-match`,
   `if-none-match`, `last-event-id` and the trace context reach EE beside the assertion. Bodies
   follow the manifest `body` class (`none` forwards nothing, `json` is capped at
   `OMEGA_MAX_BODY_BYTES`, `bundle` at `OMEGA_MAX_BUNDLE_BYTES`). The `timeout` class bounds
   the wait for EE's response headers (`read` and `stream` 15 s, `write` 30 s, `model` 35 s);
   after that every response is **piped** with backpressure, never buffered, and a browser that
   leaves cancels the EE stream. Responses are `no-store` and carry `server-timing: mint, gw, ee`.

## Channel routes: the conversation's computer (CX-WALL, owner ruling X-1)

A person's shell in the agent's computer (EE's conversation-computer routes, CX-BRIDGE) cannot
spend a one-use key per keystroke, and an `EventSource` cannot attach a header. So the manifest
marks the computer's four terminal stream and input routes `"credential": "channel"`, and their
mint `"credential": "channel-mint"`; every other route, the mint included, keeps one key per
request. `src/channel-token/` holds it all; `server.mjs` only calls it.

- **Mint** (`POST .../conversations/{id}/computer/channel`, a key route): EE answers 201 with a
  `cct_` token bound to the conversation, the person and the computer generation, alive 60 s.
  The gateway passes the answer on unchanged and learns the token from it: by its SHA-256 only,
  with the conversation the mint's path named (it must equal EE's `conversationId`) and the
  verified claims of the key that asked (`bindings.mjs`, `taps.mjs`).
- **A channel request** (`serve.mjs`), before a byte of its body is read: an `Authorization`
  header of any kind is `401 channel_token_invalid` (reason `credential_on_channel_route`; a host
  key is never admitted here and is never spent); exactly one well-formed `token`, no other
  credential in the query; a token this gateway saw minted, still live (`unknown_channel_token`,
  `expired_channel_token`), for the same conversation (`404`, `channel_token_other_scope`). Then
  the role floor, the product wall and the rate limit, judged by the minting key's claims as the
  mint was. EE then gets the request with the token in its query and **no assertion**, and checks
  everything again (EE is authoritative: the person's access, the generation, the terminal).
- **Renewal and refusal follow EE.** An open terminal stream carries EE's `event: channel`
  renewals every 20 s; the gateway reads those short frames as they pass (output events are piped,
  never buffered) and moves its expiry. EE's 401, a 409 `computer_stopped`, or an `event: end` for
  either forgets the token, so the next use is refused here.
- **Never anywhere else.** On any other route `token=` is `400 credential_in_query` (the generic
  rule), and a `Bearer cct_...` is `401 channel_token_invalid` (`channel_token_as_bearer`) before
  anything is sent to the session exchange.
- **Rate class `channel`**: 40/s, burst 120 per user and workspace (a shell sends about one request
  per keystroke; `write`'s 5/s would cut it). On a Redis outage it falls back to this replica's
  buckets like `read`, so an open shell keeps working. One token's requests reach only the replica
  that carried its mint, but a person with terminals open in conversations pinned to N replicas
  gets up to N times the budget for the outage (at most one replica per conversation whose
  computer they run). The manifest compilers allow the class on channel routes only.
- **Bounded memory.** At most 8 bindings per person and computer (EE's own bound: a ninth mint
  retires the oldest at EE and here alike) and 50,000 in all, so one person minting at the mint's
  rate cannot push other people's bindings out.
- **One replica's memory (decided).** A channel request must reach the replica that carried its
  mint, sticky on the conversation id, which EE's own single-replica token store already requires.
  A miss is refused 401 and the client mints again; it is never forwarded on trust. A gateway
  restart forgets every token the same way.
- **Logs.** No line holds a token: the access line has the route template, the refusal line the
  path without its query, and both sinks also replace any `cct_` value or `...token=` pair
  (`redact.mjs`; EE's access sink does the same).
- **Hosted launch gate: the front of the gateway.** The token travels in the URL, so anything in
  front of the gateway that writes query strings to its access log would record it. Before a
  hosted launch, the load balancer or ingress for `/omega/api/**` (at least
  `.../computer/terminals/**`) must log no query string, checked on the hosted stack by sending a
  well-formed unknown `cct_` value and finding it in no log. This repository holds no hosted
  ingress for the gateway, so it is not checked here (X-1 review F-2).

## Console-origin listener (CP P6, D1)

Set `OMEGA_CONSOLE_ORIGIN` to run a second listener (`OMEGA_CONSOLE_PORT`, default 8081) for the
Console's own origin, same site as Future AGI but a different origin. It serves the Console build
(`OMEGA_CONSOLE_STATIC_DIR`) with `Content-Security-Policy: default-src 'self'; script-src 'self';
… frame-ancestors <Future AGI origin>` and the other security headers, and the same API under the
base path, which here **accepts pre-minted assertions only**: a Future AGI browser token is
`401 omega_assertion_required` and never reaches the issuer from this origin. Trusted Types are
report-only until `OMEGA_CONSOLE_TRUSTED_TYPES=enforce`. CSP reports go to
`/__omega/csp-report` and are logged as directive and blocked origin only.

## Observability

One JSON access line per request on stdout: request id, route template (never the raw path),
status, durations (`mintMs`, `eeMs`), rate class and store, refusal reason, and the org and
workspace as hashes. Never bodies, tokens, headers or query strings. With
`OMEGA_GATEWAY_OTLP_TRACES_URL` set, the same request becomes an OTLP span sent to a Future AGI
Observe project (`OMEGA_GATEWAY_OTLP_PROJECT`, default `omega-console-ops`), with the key pair
read from `OMEGA_GATEWAY_OTLP_CREDENTIALS_FILE` (`{"apiKey","secretKey"}`). A 5xx is an error
span. Export is batched and bounded and never blocks a request.

## Shutdown

`SIGTERM`: stop accepting, end open event streams at once with a clean end (clients reconnect
with `Last-Event-ID` to another replica), give other in-flight requests up to
`OMEGA_GATEWAY_DRAIN_MS` (10 s), flush spans, exit 0.

## Session exchange (Django)

`POST /accounts/omega/session-exchange/` (`futureagi/accounts/views/omega_session.py`) takes an
opaque FutureAGI browser access token in `Authorization: Bearer …` and the body
`{"audience":"omega-squad", "count"?: 1-8}`, and returns
`{"accessToken":"omega_delegate_<RS256 JWS>","tokenType":"Bearer","expiresIn":45}`, plus
`accessTokens` (every key, the first being `accessToken`) when `count` is sent, with
`Cache-Control: no-store`.

- Claims: `iss`, `aud`, `sub` (host user UUID), `org_id`, `workspace_id`, `role`, `iat`,
  `exp = iat + 45`, and fresh random `jti` and `nonce` per key that EE consumes once.
- Roles: organization owner (15) maps to `owner`, admin (8) to `admin`, member (3) with a
  workspace member/admin grant to `member` (a workspace admin is never promoted). Viewers are
  refused with `403`, code `omega_viewer_not_admitted`.
- Limited to 120 calls a minute per user (`accounts/omega_throttles.py`, Django's cache), 429
  with `Retry-After`. The observe-target lookup is limited to 60 a minute per API key, counted
  before the secret is compared, and the secret is compared in constant time after a lookup by
  the key's public id.
- Refused: API keys, cookies alone, refresh/revoked/expired tokens, inactive users, and
  `X-Organization-Id`/`X-Workspace-Id` selections that differ from the resolved context.
- Off (503) unless `OMEGA_SQUAD_DELEGATION_ISSUER` (https), `OMEGA_SQUAD_DELEGATION_KEY_ID`
  and `OMEGA_SQUAD_DELEGATION_PRIVATE_KEY_FILE` (absolute path to a regular RSA-2048+ PEM
  owned by the backend user, mode 0400/0600, no symlink) are all set.
- EE trusts the issuer through a public-only JWKS with the matching `kid`; the same JWKS is
  given to this gateway as `OMEGA_ASSERTION_JWKS_FILE`.

## Modes

|                                      | `cloud` (default)                    | `local` (`OMEGA_GATEWAY_MODE=local`)  |
| ------------------------------------ | ------------------------------------ | ------------------------------------- |
| EE, issuer, collector URLs           | HTTPS, not loopback                  | loopback only (http or https)         |
| Browser and Console origins          | HTTPS, not loopback                  | loopback only                         |
| `OMEGA_ASSERTION_JWKS_FILE`          | required                             | optional                              |
| `OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL` | required (`redis://` or `rediss://`) | optional (loopback); else per process |
| Listener                             | `HOST` or `0.0.0.0`                  | `127.0.0.1`                           |

`config/local.env.example` is the loopback template. It contains no credential.

## Checks

```sh
node --test services/omega-gateway/test/*.test.mjs
# with a real Redis (two-replica limits) and EE's manifest copy (sha256 parity):
OMEGA_GATEWAY_TEST_REDIS_URL=redis://127.0.0.1:6379 \
OMEGA_EE_ROUTE_MANIFEST=<EE>/packages/server/src/managed-auth/console-route-manifest.v1.json \
  node --test services/omega-gateway/test/*.test.mjs
```

`futureagi/accounts/tests/test_omega_gateway_bridge.py` runs the real Django exchange, this
gateway as a subprocess and a stub EE, and proves what EE receives.

## Provenance

Ported from Squad `services/omega-squad-gateway` at `7debd6482`, then rebuilt by lane F-GATE
(CP P3-P10, READINESS 1.3): the hand-written route lists were replaced by the manifest compiler,
and the request path was split into the modules above. `test/policy.test.mjs` keeps Squad's
config assertions; route assertions live in `test/wall.test.mjs` and
`test/customer-routes.test.mjs`, and `test/forward-target.test.mjs` proves on real sockets that
what W2 matched is what EE receives.
