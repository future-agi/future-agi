# Omega EE attach contract, gateway side (`omega-ee-attach/v1`)

## Availability and existing credentials

Enterprise **broker = gateway** provisioning is unavailable by default. Every
route under `/agentcc/mcp/service/` returns HTTP 503 `feature_unavailable`
before authentication, tenant lookup, request validation, or idempotency replay.
This includes ensure, detach, mint, revoke, credential read/create/rotate/revoke,
workspace policy read/write, agent-key list/find, and refusal reporting. The
response is identical for absent, unknown, and previously issued credentials;
`provision/error-unavailable.response.json` defines its exact envelope. No route
returns a credential or provisions a replacement while unavailable.

Previously issued service credentials and gateway keys are retired by their
Enterprise provisioning records, including historical records without an issuer
or person. Rolling back an image does not restore them. Ordinary explicitly
created personal keys are preserved. After migration, operators run
`python manage.py retire_ee_credentials` until `gateway_pending` is zero. This
command retries every purpose-bound key after missed publication or restart and
requires gateway readback of revocation or absence; database flags alone are not
confirmation. Gateway reload payloads omit Enterprise-bound keys while disabled.

The sections below retain the explicitly enabled compatibility protocol for
consumer validation. They are not a statement that provisioning is available.
The compatibility design attaches a connector to a gateway virtual server rather
than keeping vendor tokens in Enterprise. The remaining protocol covers:

- how EE gets a virtual server for one of its connections;
- which credential it calls with in each account mode;
- the request and result shapes, and the typed results it must handle;
- what it must never do.

Self-hosted EE and OSS Omega keep their own brokers and are out of scope
(section 9).

This contract covers per-call credentials and broker = gateway compatibility.
The golden fixtures are in
`agentcc-gateway/scripts/scenarios/fixtures/omega-ee-attach/`, checked by
`check.sh` (section 10). `GW:` citations below refer to
`agentcc-gateway/internal/`; Django citations use repository-relative paths.
No private source checkout is needed to validate the public contract.

## 1. Gateway behavior and availability

Enterprise provisioning is unavailable by default, as described above. The
remaining sections define compatibility requirements, not hosted availability.
When the MCP endpoint is enabled, the gateway provides these behaviors:

| Piece                                  | Behavior                                                                                                                                                                                                                                                               |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `{endpoint}/v/{vs_id}`                 | Resolved only in the caller's org; an unknown, foreign or not-for-you id gets the same 404.                                                                                                                                                                            |
| Prefixed tool names `<server>__<tool>` | At most 64 characters.                                                                                                                                                                                                                                                 |
| Virtual-key callers                    | `Authorization: Bearer`, else `x-api-key`; with MCP client sign-in disabled, the key gate rejects an unknown credential (section 4.2).                                                                                                                                 |
| Request authentication                 | With request auth on and MCP client sign-in disabled, a missing or unrecognized key gets the model path's 401 before MCP; with MCP enabled and both `mcp.client_oauth_enabled` and `mcp.oauth.enabled` true, the gate forwards it to MCP authentication (section 4.2). |
| IP rules                               | An address refused by the gateway's or org's `ip_acl`, or the key's `allowed_ips`, gets the model path's 403.                                                                                                                                                          |
| Sessions                               | Bound to the caller and virtual server; an unknown session gets a 404 with no body.                                                                                                                                                                                    |
| Key synchronization                    | Creation reaches another replica at its next key sync; revocation is broadcast.                                                                                                                                                                                        |
| Startup readiness                      | A replica with control-plane key sync waits for its first key list before `/readyz` succeeds.                                                                                                                                                                          |
| MCP disabled                           | The router answers the `url` with a 404.                                                                                                                                                                                                                               |
| Denials and metering                   | In-band denials include `tool_denied`; every result and denial carries `_meta["io.futureagi/tool_call"]`.                                                                                                                                                              |
| Upstream metadata                      | An upstream's own `io.futureagi/*` `_meta` keys are dropped.                                                                                                                                                                                                           |

Provisioning and the user-mode, approval and budget requirements are described
in the numbered sections below. Their presence in this compatibility contract
does not enable `/agentcc/mcp/service/` or issue credentials while it is disabled.

## 2. The two account modes

EE chooses the account mode per connection. Each EE connection gets its
own virtual server. That keeps it under EE's 128-tool ceiling and keeps two
FutureAGI workspaces of one org apart.

|                                    | Agent mode: one shared account                                                                                          | User mode: each person's own account                                                                                           |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| Who connects the vendor account    | An org admin, once, through the gateway (shared-account authorization)                                                  | Each person, through the gateway's connect page (the vendor connect flow)                                                      |
| What EE holds                      | An org gateway virtual key for this workspace, minted through the provisioning API (sections 3.1 and 3.7), in its vault | Per person, an OAuth refresh token for the gateway, in `IntegrationUserAccount {credentialId, refreshCredentialId, expiresAt}` |
| What EE never holds                | A vendor token                                                                                                          | A vendor token                                                                                                                 |
| Call credential                    | `Authorization: Bearer <virtual key>`                                                                                   | `Authorization: Bearer <OAuth access token>`, valid at most 15 minutes                                                         |
| Who may address the virtual server | `access.keys = [<agent_key_id>]`                                                                                        | Only OAuth principals who are members of that workspace now (live workspace membership)                                        |
| Members' `credential.mode`         | `shared` or `none`                                                                                                      | `per_user`                                                                                                                     |
| When the vendor account is missing | `auth_required` (`revoked`, `expired` or `needs_reauth`); an admin reconnects                                           | `auth_required` (`no_token` and the rest) with that person's `connect_url`                                                     |

## 3. Provisioning a virtual server

### 3.1 Who may call

Two different credentials meet here, and they are not interchangeable.

- **Provisioning calls Django with EE's service credential**. It
  is a credential per org, made for EE alone: scoped to the provisioning API (ensure and
  detach, sections 3.2 to 3.4, and EE's agent keys, section 3.7, including
  reading the workspace's agent-key list to find a key by its
  Idempotency-Key) and nothing else, rotatable and revocable on its own, and
  audited as EE, never as a person. EE sends it as `X-Api-Key` and
  `X-Secret-Key`, the header pair of Django's API keys
  (`futureagi/accounts/authentication.py:179-181`). It has its own
  model, authentication class and rotation. It is never
  the org's `system` `OrgApiKey`, a broad key shared with ordinary SDK and API
  use that authenticates as the org's earliest-created user, and
  never the gateway admin token.

- **Calling the virtual server uses a gateway credential** (section 4.2).
  Neither EE's service credential nor any other Django `OrgApiKey` is a
  gateway key, and the MCP endpoint refuses them.

- **The agent key comes from the provisioning API** (section 3.7). EE mints it with its
  service credential, one per workspace, not one per connection (rule 3). The
  answer carries the raw `key` once and its `agent_key_id`, which ensure
  takes. EE stores the raw key in its vault as the credential of that
  workspace's agent-mode connections, never logs it, and records with it the
  `agent_key_id` and the time it minted the key. EE's store names one
  **current** key per workspace: every client EE builds, and every ensure it
  sends, uses that key.
- **A new key reaches the other gateway replicas only on their next key
  sync.** The provisioning API mints it through Django's key service, which creates it through
  one gateway base URL (`futureagi/agentcc/services/auth_bridge.py:120-127`,
  `futureagi/agentcc/services/gateway_client.py:293-297`), so on one replica:
  `POST /-/keys` adds it to that replica's own key store
  (`GW:server/admin_keys.go:99`, `GW:auth/keystore.go:333-366`), and a replica
  authenticates only from its own store. Each other replica loads
  the key on its next key sync, which pulls `/agentcc/api-keys/bulk/` every
  `control_plane.sync_interval` (`GW:auth/sync.go:28`,
  `agentcc-gateway/cmd/agentcc/main.go:627`): 60 s in docker-compose and Helm
  (`deploy/env-reference.toml:1524-1527`). At the gateway's own default of 0
  there is no periodic sync at all (`GW:tenant/spend_sync.go:92-94`), so more
  than one replica needs an interval set. Creation is not broadcast;
  revocation is, over Redis, to every replica at once
  (`GW:server/admin_keys.go:212-216`, `GW:redisstate/revocation.go:11-16`).
  Until a replica has synced, its key gate answers the new key with 401
  `invalid_api_key` (section 4.2). A sync that fails, while the app is down,
  leaves a replica's keys as they were, so the window below is a bound EE
  keeps, not a promise the gateway makes. `check.sh` fails if key creation
  starts being broadcast, so this section is revisited then.
- **A replica that starts knows no synced key until its startup sync has
  loaded them.** A rollout, a scale-out or a restart starts a replica that
  serves only its `config.yaml` keys until its startup sync has loaded the
  keys from the app (`GW:tenant/startup_sync.go:27-31`). That sync runs in a
  goroutine started before the server
  (`agentcc-gateway/cmd/agentcc/main.go:627,671`) and retries from 2 s,
  doubling to 30 s, for as long as the app does not answer, which on a first
  boot can be minutes of migrations (`startup_sync.go:13-21`). Helm and
  docker-compose turn it on (`deploy/helm/futureagi/templates/_env.tpl:412`,
  `docker-compose.yml:220`); without it, a replica loads keys only at its
  first periodic sync, one interval after it starts (`startup_sync.go:129-133`,
  `GW:tenant/spend_sync.go:103-113`).
  A gateway whose control plane syncs keys answers `/readyz` 503
  ("waiting for the first key sync from the control plane") until its key
  store has loaded a key list from the app, or the app reports that it has no
  key table (`agentcc-gateway/internal/server/server.go` `readyHandler`,
  `agentcc-gateway/internal/auth/keystore.go` `KeyListLoaded`). Behind Helm's
  readiness probe, a new pod takes no traffic before it knows the app's keys.
  `check.sh` fails when readiness stops waiting for that sync. EE's rule
  (section 4.2) does not rely on it: a key minted after a replica's last sync
  is still unknown there until its next one, and not every deployment routes
  by `/readyz`.
- **The window.** Two sync intervals: an EE setting, because EE cannot read
  the gateway's configuration, 120 s by default (twice the 60 s interval). An
  operator who raises `control_plane.sync_interval` raises EE's setting with
  it, to at least twice the interval. Two windows apply to a key. Its
  **minting window** runs from when EE minted it. Its **401 window** runs from
  the first key-gate 401 for it since a request with it last succeeded: a
  replica that has just started refuses a long-current key (above), which the
  minting window alone would miss. While either is open, the key gate's 401
  for the current key is transient (section 4.2). A change's window runs from
  an ensure that created a virtual server or changed its `servers` or
  `agent_key_id`; inside it, a replica that has not read the change answers
  the virtual server's 404 (rule 9, section 5.3). A recovery ensure, which
  changes nothing, starts no window.
- **Rotating the agent key.** EE runs one rotation per workspace at a time, in
  this order:

  1. Mint the new key, K2 (section 3.7), and record it. The old key, K1,
     stays the current key.
  2. Wait out K2's minting window, so every replica has K2 before a virtual
     server names it.
  3. Make K2 the workspace's current key in EE's store; keep K1's
     `agent_key_id` only to revoke it. From here, every ensure EE sends
     carries K2 and every client EE builds uses K2.
  4. Call ensure with K2 for each of the workspace's agent-mode connections.
     Each virtual server keeps its `vs_id` and `url` (rule 1).
  5. Rebuild every client built with K1: a new client and a new `initialize`
     with K2 (section 4.1). Inside the change's window, a K1 request on a
     replica that has read the change and a K2 request on one that has not
     both get the virtual server's 404, which section 5.3 retries.
  6. One window after the last ensure, revoke K1:
     `POST /agentcc/mcp/service/agent-keys/{agent_key_id}/revoke/` with K1's
     id (section 3.7). The revocation reaches every replica at once, so a
     client still holding K1 gets the key gate's 401, and the first step of
     section 4.2 moves it to K2.

  Step 3 comes before step 4 on purpose. From then on, a client still built
  with K1 is rebuilt with K2 when it meets the key gate's 401 or the virtual
  server's 404 (sections 4.2 and 5.3), and every recovery ensure reads the
  current key from EE's store, K2. Were a recovery ensure to send K1, rule 1
  would move the virtual server back to K1, and clients on the two keys would
  move it back and forth.

### 3.2 Ensure

`POST /agentcc/mcp/service/virtual-servers/ensure/` (fixtures
`provision/ensure-*.json`); the user-mode fixtures (`ensure-user.*`) give
the compatibility response shape, not current user-mode availability.

| Field           | Type                                                   | Rule                                                                                                                         |
| --------------- | ------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| `workspace`     | FutureAGI workspace id (UUID)                          | A workspace of the caller's org; any other is 404                                                                            |
| `connection_id` | EE's `IntegrationConnection.id`, opaque to the gateway | With the workspace, the idempotency key                                                                                      |
| `servers`       | 1 to 8 org server ids                                  | Each must exist in the org's MCP config. One is the normal case: one catalog app is one server                               |
| `mode`          | `agent` or `user`                                      | Fixed for the life of the virtual server. Ensure accepts `agent` today and answers 400 `invalid` (`attr` `mode`) for `user`. |
| `agent_key_id`  | Gateway key id, `key_` and 24 hex digits               | Required in agent mode, refused in user mode; a key the provisioning API minted for this workspace (rule 3)                  |

The response is `{"status": true, "result": {vs_id, url, mode, workspace,
connection_id, created}}`. `created` is true when this call made the virtual
server. Ensure answers 200 only once the gateway accepted the virtual server
(rule 9).

### 3.3 Provisioning rules

1. **Idempotent** per (org, workspace, `connection_id`). A repeat returns the
   same `vs_id` and `url` with `created: false`, and pushes the virtual server
   to the gateway again even when nothing changed (rule 9). A repeat with new
   `servers` or a new `agent_key_id` updates the virtual server in place, so a
   key rotation keeps the id. EE always sends the workspace's current
   `agent_key_id` from its store (section 3.1), never the key a client was
   built with: an ensure with an older key would move the virtual server back
   to it. An update changes only `members` and `access.keys`. It keeps
   `access.rules`, `approval` and every other field an org admin set on that
   virtual server: the gateway judges a call only by the rules of the virtual
   server it addresses (`GW:mcp/hook_acl.go:121,146`), so a rule an admin adds
   for EE's calls lives there (section 6). A different `mode` is 409
   `connection_mode_conflict`: detach first.
2. **The mode matches the members' credentials.** Agent mode needs members
   whose `credential.mode` is `shared` or `none`, and user mode needs
   `per_user`; anything else is 400 `mode_mismatch`.
   For a per_user upstream, no_user_identity applies when the authenticated
   principal has no UserID; carrying a user identity alone does not supply a
   vendor credential. A shared upstream uses its shared account.
3. **The agent key** must be a key the provisioning API minted for this workspace
   (section 3.7) that is still active: not revoked, deleted or past its expiry
   (`futureagi/agentcc/services/auth_bridge.py:26-34`), else 400
   `agent_key_invalid`.
   An authenticated gateway key can carry a person identity: the MCP key
   principal copies UserID from the key store. The key store supplies it only
   for control-plane-issued (sync) or verified delegated keys whose reserved
   user_id is a canonical UUID. Config seeds and keys with absent, malformed or
   untrusted user_id carry no user identity; a caller header or an owner label
   does not establish one.
   (`GW:mcp/principal.go:120-137`; `GW:auth/keystore.go:325-353`).
   These identity rules do not change provisioning-key eligibility. Ensure
   refuses, with 400 `agent_key_invalid`:

   - **A key of another workspace.** One agent key serves one workspace: the provisioning API
     records the workspace it minted a key for, and refuses the key for any
     other (fixture `provision/error-agent-key-other-workspace.response.json`).
     Access lists match keys, agents and users, with no workspace dimension
     for keys (`GW:mcp/vserver/access.go:11-28`), so a key that two
     workspaces' virtual servers both named could address both, and only EE's
     routing would keep the workspaces apart.
   - **A key the provisioning API did not mint**, such as one an admin made in the console:
     EE's credential can revoke only the keys it minted (section 3.7), so
     ensure binds no other.
   - **A legacy id**, `key_<n>`. Static config keys get one
     (`GW:auth/keystore.go:196-197`), and gateways before random key ids
     reissued ids after a restart
     (`futureagi/agentcc/services/auth_bridge.py:88-98`), so an access list
     could come to name another key. The provisioning API never mints one.

   **IP rules.** The key gate checks the caller's address before the MCP layer
   sees the call (section 5.3): the gateway's `ip_acl`, the org's `ip_acl`
   (also with the gateway-wide rule off, on a gateway that receives org
   configs and resolves client IPs: `agentcc-gateway/internal/server/keygate_ipacl.go`
   `keyGateIPACL`) and the key's `allowed_ips`
   (`GW:middleware/auth.go:56-63,81-86`, `GW:plugins/ipacl/ipacl.go:82-101`).
   The provisioning API mints the agent key with no `allowed_ips` (section 3.7), and the org's
   `ip_acl` must admit hosted EE's egress addresses. Only a static
   config key carries `allowed_ips` at the gateway; Django's `allowed_ips`
   setting is not applied to a synced key (`GW:auth/keystore.go:215,550-560`;
   `GW:middleware/clientip.go:128-129`), so today EE meets the 403 only from an
   `ip_acl`.

4. **Names stay safe for EE.** The virtual server uses `naming: prefixed` and
   no `overrides.*.name`. A flat or overridden name keeps whatever first
   character the upstream or the override chose
   (`GW:mcp/vserver/compose.go:299-307`), and EE refuses a name that does not
   start with a letter or digit.
5. **Posture stays EE's**. The provisioning API creates the virtual server with
   `approval.require: none` and no deny rule of its own; an update never
   resets what an admin set since (rule 1).
6. **Access.** In agent mode, `access.keys = [agent_key_id]`, so every other
   key gets the same 404 (`GW:mcp/vserver/access.go:20-28`). Today's gateway
   access rules have no workspace or membership dimension. User-mode
   compatibility requires workspace-aware authorization: admission of an OAuth
   principal requires FutureAGI's live membership to confirm that the person
   belongs to the virtual server's workspace; anyone else must get the same 404.
   A synced `access.users` list is only a short-lived, fail-closed cache, not
   an authority. Removal from a workspace must revoke access within seconds
   through FutureAGI revocation events, rather than waiting for the next sync.
7. **No credential in ensure's or detach's answer.** Neither returns a key,
   token or secret. Only the agent-key mint returns one: the raw key, once
   (section 3.7).
8. **`url`** is the gateway's public HTTPS origin, its MCP endpoint path, then
   `/v/<vs_id>` (`GW:server/routes_mcp_vs.go:16`). It equals the `resource`
   published in OAuth resource metadata and the `aud` issued by the authorization server. It carries no query,
   fragment, IP literal or private host name, and the gateway serves it
   without a redirect (section 4.1).
9. **Answer only once the gateway has it.** The provisioning API writes through the org config
   writer, which keeps a config whose push failed and reports
   `gateway_sync_failed`; the gateway then pulls it on its next sync
   (`futureagi/agentcc/services/org_config_writer.py:63,394-412`). Ensure turns
   that warning into 503 `gateway_sync_failed` (fixture
   `provision/error-sync-failed.response.json`): the saved virtual server
   stands, and EE calls ensure again with backoff, which pushes it again
   (rule 1). A 200 means only that one gateway replica has the virtual
   server: the others read it on their next sync, every 60 s by default
   (`control_plane.sync_interval`, `deploy/env-reference.toml:1524-1527`). So
   inside the change's window (section 3.1), a replica may answer the virtual
   server's 404, on `tools/list` or `tools/call`, and section 5.3 retries it.
   `initialize` does not look the virtual server up, so it succeeds even on
   such a replica.

### 3.4 Detach

`DELETE /agentcc/mcp/service/virtual-servers/{vs_id}/` removes that
connection's virtual server and nothing else: the member servers, other
connections' virtual servers and every vault row stay. The response is
`{"status": true, "result": {vs_id, connection_id, removed: true}}`. A repeat,
or a virtual server the caller did not provision, is 404.

EE detaches only when a person or an admin disconnects the connection. It
never detaches to recover from an error: a new `vs_id` gets a new `url`, and in
user mode every person's grant names the old `url` as its audience, so each of
them would have to sign in and consent again (section 5.3).

### 3.5 Errors

Errors use Django's envelope (`futureagi/tfc/utils/api_errors.py:225-238`):
`{status: false, type, code, detail, message, error, result}`. When a field is
at fault, `attr` names it and `details` maps it to its messages
(`{"agent_key_id": ["..."]}`); the envelope never sets `attr` without `details`. The agent-key endpoints (section 3.7) answer with the same
envelope and codes.

| HTTP | `code`                     | When                                                                                                                    |
| ---- | -------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| 400  | `mode_mismatch`            | Rule 2                                                                                                                  |
| 400  | `server_not_found`         | A listed server is not in the org's config                                                                              |
| 400  | `agent_key_invalid`        | Rule 3: not an active key the provisioning API minted for this workspace; a legacy id                                   |
| 400  | `invalid`                  | Any other validation failure                                                                                            |
| 400  | `idempotency_key_invalid`  | a mint without an `Idempotency-Key` header, or with one that is not a UUIDv7 (`attr` `Idempotency-Key`)                 |
| 401  | `not_authenticated`        | No provisioning credential, or one that is not the org's current EE service credential (revoked, or rotated out)        |
| 403  | `permission_denied`        | A credential that is not EE's service credential, such as a person's session or the `system` `OrgApiKey`                |
| 403  | `key_scope_denied`         | an agent key sent to any Django route (it is a gateway key, scoped to MCP and the model endpoints, section 3.7)         |
| 404  | `not_found`                | The workspace, the virtual server or the agent key is unknown or not the caller's (the same answer either way)          |
| 409  | `connection_mode_conflict` | Rule 1, a change of mode                                                                                                |
| 409  | `agent_key_cap_reached`    | a mint while the org holds its cap of live agent keys (section 3.7); nothing was made                                   |
| 409  | `agent_key_mint_replayed`  | a mint whose Idempotency-Key made a key in the last 24 hours; nothing was made, and the answer names that key           |
| 422  | `idempotency_key_reused`   | a mint whose Idempotency-Key was sent with another request (another workspace) in the last 24 hours; nothing was made   |
| 503  | `gateway_sync_failed`      | Rule 9 and section 3.7: the gateway has not accepted the change yet, and what was saved stands. Call again with backoff |
| 503  | `gateway_url_not_public`   | Rule 8: this deployment's gateway URL would not give EE a public HTTPS `url`; nothing was written. An operator must act |

### 3.6 Provisioning and call credentials

The ensure request carries `{workspace, connection_id, servers[], mode}` and,
for agent mode, `agent_key_id`. The provisioning credential is EE's service
credential; the MCP endpoint accepts no Django credential. The gateway key
EE calls with is therefore named separately and minted through the
provisioning API (section 3.7).

That service credential is scoped to provisioning. Mint and revoke use
Django's key service (`futureagi/agentcc/services/auth_bridge.py:102-176,215-238`),
not the console's key API.

The console key API requires an interactive personal session and active
organization membership to create a key, binds it to the signed-in person, and
returns the raw key only in the creation response. Members may revoke or delete
their own keys. Owner/Admin permission is required for update, partial_update
and sync, and administrators may manage other members' keys.
(`futureagi/agentcc/views/api_key.py:74-103,143-196`). Ordinary personal-key
self-service does not authorize Enterprise service minting (section 3.7).
The error codes, `created` flag, echoed fields and agent-key endpoints are
part of this contract. Rules 1, 3 and 9 preserve admin settings, require a
workspace-bound non-legacy key and require gateway acceptance before a 200.

The rotation in section 3.1 uses rule 1 and section 3.7: ensure with a new
`agent_key_id` replaces `access.keys` and keeps `vs_id`, `url` and admin
settings. `test_matches_omega_ee_attach_fixtures` checks provisioning against
the golden fixtures.

### 3.7 Agent keys

EE mints and revokes its agent keys through the provisioning API, with its service credential
(fixtures `provision/agent-key-*.json`).

**Mint.** `POST /agentcc/mcp/service/agent-keys/` with `{"workspace": <id>}`,
a workspace of the caller's org (any other is 404), and an
`Idempotency-Key` header (below). It answers 201 with
`{"status": true, "result": {agent_key_id, key, workspace}}`: a new gateway
virtual key of the org, made through Django's key service
(`futureagi/agentcc/services/auth_bridge.py:102-176`), and its raw `key`,
returned this once and never again. The provisioning API records the workspace with the key
(rule 3) and mints it not personal and with no `allowed_ips`, named for EE and
its workspace so that an admin can tell it apart in the console. The answer also carries the key's `expires_at` (below). EE records the key in
its vault before it first uses it. When the gateway cannot be reached nothing
is made, because the gateway creates the key before Django stores it
(`auth_bridge.py:120-167`): the provisioning API answers 503 `gateway_sync_failed`, and EE
mints again with backoff, with the same Idempotency-Key.

**An idempotent mint.** A mint whose answer EE lost (a dropped
connection, EE's 15-second timeout, a 502 or 504 after the key was made) must
never leave a key nobody holds:

- **The header.** Every mint carries `Idempotency-Key: <UUIDv7>` (RFC 9562;
  fixture `provision/agent-key-mint.request.json`). EE makes a new one for
  each new mint and stores it before the call; a retry of that mint sends the
  same one. A mint without it, or with anything but a UUIDv7, is 400
  `idempotency_key_invalid` naming the header (fixture
  `provision/error-idempotency-key-invalid.response.json`), and nothing is
  made. Case does not matter.
- **The record.** The mint stores, with the key, in the same transaction and
  under the org lock, a record keyed by the org, EE's service credential and
  the SHA-256 of the Idempotency-Key. It holds only hashes (of the
  Idempotency-Key and of the request body), never the key or its secret, and
  lives 24 hours; each later mint of the org deletes the records past it. Two
  mints with one Idempotency-Key at the same moment make one key: the second
  waits on the org lock, then finds the record.
- **A replay.** Within those 24 hours, a mint with the same Idempotency-Key
  and the same body makes nothing and answers 409 `agent_key_mint_replayed`:
  Django's envelope plus `agent_key_id`, `created_at`, `expires_at` and
  `revoked` of the key the first mint made (fixture
  `provision/error-agent-key-mint-replayed.response.json`). Never the key: it
  was in the first answer only. The replay comes before the cap, so the retry
  of a mint that filled the cap is still a replay. The same Idempotency-Key
  with another body (another workspace) is 422 `idempotency_key_reused`
  (fixture `provision/error-idempotency-key-reused.response.json`), and
  nothing is made. A mint that made nothing (503, 409 `agent_key_cap_reached`, 401) keeps no record: its retry mints. After 24 hours the Idempotency-Key
  is accepted again and makes a new key.
- **The find.** `GET /agentcc/mcp/service/workspaces/{workspace}/agent-keys/`
  lists every agent key of the workspace (without a raw key), each
  with the `mint_idempotency_key` it was minted with (null for keys minted before idempotent minting), and
  `?mint_idempotency_key=<UUIDv7>` keeps the keys minted with that one
  (fixtures `provision/agent-keys-find.request.json` and `.response.json`).
  EE's service credential may read it for its own org's workspaces, and an
  Owner or Admin may. The binding keeps the Idempotency-Key itself for this,
  an identifier EE made, not a credential: with EE's service credential it
  gets at most a 409 that names a key, never the key.
- **EE's reconcile after an uncertain mint.** Retry with the same
  Idempotency-Key. A 201 means the key is in hand. A 409
  `agent_key_mint_replayed` means the key exists but its secret is lost:
  revoke that `agent_key_id` (**Revoke**, above), then mint with a new
  Idempotency-Key. An empty find means no mint committed with it: retry with
  the same one (a mint still in flight then answers with a replay). A
  revoked key leaves room under the cap at once.
- **Scope of a record.** Records are per EE service credential: after a
  rotation the same Idempotency-Key mints again under the new credential. The
  find is per workspace, not per credential, so it lists the keys of both;
  EE reconciles an uncertain mint across a rotation with the find before it
  retries.
- **Audit.** Each replay is audited (`agentcc.ee_agent_key.mint_replayed`)
  with the workspace, the key's id, the Idempotency-Key and EE as the actor;
  a revoke of a key whose mint retry was answered with a replay is marked
  `reconcile: true` in its revoke entry, with the Idempotency-Key.

**Revoke.** `POST /agentcc/mcp/service/agent-keys/{agent_key_id}/revoke/`
revokes a key this credential minted and answers 200 with
`{"status": true, "result": {agent_key_id, revoked: true}}`. A repeat is safe
and answers the same. A key it did not mint, or an unknown id, is 404. Django
marks the key revoked even when the gateway cannot be reached
(`auth_bridge.py:215-238`); the provisioning API then answers 503 `gateway_sync_failed`, and
EE calls again with backoff. Until a call gets through, the gateway drops the
key at its next key sync, because the list it pulls holds live keys only
(`futureagi/agentcc/views/api_key_bulk.py:39`, `auth_bridge.py:26-45`).

An admin who revokes an agent key in the console stops EE's calls with it
until someone reconnects in EE (section 4.2). To keep EE out for good, an admin
revokes EE's service credential or disconnects EE's connections. Revoking the
credential also revokes every live agent key EE holds in the org (minted under
that credential or one it rotated out, in any workspace), and the admin's
answer lists them (`agent_keys_revoked`, with `gateway_synced` false when the
gateway drops them only at its next key sync); rotating it revokes no key.

**A scoped key.** The agent key is not an org-wide key:

- **Scope.** It may call the virtual servers (the gateway's MCP endpoint and
  everything under it) and the model endpoints `POST /v1/chat/completions`,
  `POST /v1/embeddings` and `POST /v1/responses`, and nothing else. Any other
  gateway route, key-gated or not (`/v1/models`, the admin routes, the
  Gemini routes under `/v1beta` with `x-goog-api-key` or `?key=`), answers 403
  `key_scope_denied` before a handler runs (fixture
  `protocol/403-key-scope-denied.json`), and any Django route answers 403
  `key_scope_denied` in Django's envelope (fixture
  `provision/error-agent-key-out-of-scope.response.json`). The scope rides on
  key metadata only Django sets, from the binding (never from what a tenant
  can write), on every key sync.
- **Models.** The key carries no model or provider list of its own, so the
  gateway confines it to the org's own providers (BYOK) and the managed
  models the org's `managed_models` lists allow, judged on every request: a
  change an admin makes applies to the next call, with no new key.
- **Budget.** Each agent key may spend a budget per period (daily, weekly or
  monthly, from 00:00 UTC), counted on the cost the gateway prices for each
  model call, streamed or not (managed calls are debited to the org's wallet
  as usual). A stream that ends before the upstream reports its usage (the
  client left, the upstream failed, or an upstream on the org's own key
  sends none) is counted on an estimate, about 4 characters a token, and the
  request is marked `usage_estimated`. An
  org Owner or Admin sets it per workspace
  (`PUT /agentcc/mcp/service/workspaces/{workspace}/agent-key-policy/`);
  without one it is USD 50 a month. It reaches live keys at the gateway's
  next key sync. A call once the period's spend reaches it is 402
  `agent_key_budget_exhausted` (`gateway_key_budget_error`, in
  error-402-v1's shape though not in its list of codes: `limit_source`
  `agentcc_key_limit`, `scope` `key:<agent_key_id>`, `resets_at` the period's
  end; fixture `protocol/402-agent-key-budget-exhausted.json`). EE fails the
  call and shows the workspace's agent budget as spent until `resets_at`.
  Each key's budget and this period's spend are listed at
  `GET /agentcc/mcp/service/workspaces/{workspace}/agent-keys/`.
- **Cap.** An org holds at most a set number of live agent keys (20 unless
  the operator sets `AGENTCC_EE_AGENT_KEY_CAP`); a revoked or expired key
  leaves room. A mint past it is 409 `agent_key_cap_reached` (fixture
  `provision/error-agent-key-cap.response.json`), and nothing is made.
- **Expiry and rotation.** A key expires 90 days after its mint
  (`expires_at` in the mint's answer; ensure's answer names the bound key's
  `agent_key_expires_at`). Before then EE rotates by ensure: it mints a new
  key for the workspace, ensures each of the workspace's connections with
  the new `agent_key_id` (rule 1 keeps each `vs_id` and `url`), moves its
  model calls to the new key, then revokes the old one. Rotating at least 14
  days before expiry leaves room for a gateway outage. An old key left live
  counts against the cap until it is revoked or expires. An admin cannot
  remove an agent key's expiry or move it later in the console. Django holds
  the expiry; the gateway enforces it from its next key sync (60 s by
  default), which carries the expiry and drops a key past it, because the
  gateway's key create takes no expiry. With key sync off the gateway never
  learns it. Legacy agent keys receive an expiry 90 days from their mint through
  the expiry migration.
- **Audit.** Every mint, rotation (an ensure that moves a virtual server to
  another key), cap refusal, scope refusal and budget refusal is in the org's
  audit log with the workspace and EE as the actor. The gateway reports its
  refusals to Django within seconds, grouped per key and route: at most 16
  routes per key per 10-second window, then one entry for the rest
  (method `OTHER`). A method other than an HTTP method is reported as
  `OTHER`, and a path loses its control characters and is cut to 300
  characters, so every refusal is audited.

## 4. Calling the virtual server

### 4.1 Endpoint and transport

- **Address.** The `url` from ensure, over MCP's Streamable HTTP transport.
- **Public HTTPS, no redirect.** EE registers an exact public HTTPS origin
  and a path, re-checks them before every request, resolves DNS once, refuses
  a private answer and follows no redirect. Hosted EE therefore reaches the
  gateway at its public origin. A private, in-cluster gateway address requires
  an operator-pinned trusted-origin setting; it is not a public ensure URL.
- **Protocol.** The compatibility client initializes with `2025-06-18` and
  keeps the session id. Legacy requests name the revision only in
  `params._meta.protocolVersion`, not the `MCP-Protocol-Version` header.
  The gateway reads that header or
  `_meta["io.modelcontextprotocol/protocolVersion"]`
  (`GW:mcp/protocol/era.go:127-139`); without either, it serves `2025-03-26`,
  its default legacy revision (`era.go:31`). The default
  `mcp.protocol.versions` includes it (`GW:config/config.go:452`). Clients
  using per-call metadata send the protocol header after `initialize`
  (section 4.4); requests without it keep the legacy behavior.
- **Sessions.** `initialize` opens a session bound to the caller and the
  virtual server: the principal's kind, key id, user id, client id and org,
  and the `vs_id` in the path (`GW:mcp/session.go:74-84,109-131`). Every later
  request on it must present the same binding, or the gateway treats the
  session as unknown. A session ends after `mcp.session_ttl`
  without use (30 minutes by default, renewed by use:
  `GW:config/config.go:1660`, `GW:mcp/session.go:215-220`), and 24 hours after
  `initialize` however active. A request on a session the gateway
  does not hold for the caller gets a 404 with no body
  (`GW:mcp/method_initialize.go:92-117`; section 5.3). So:
  - after a key rotation EE connects again, with a new client and a new
    `initialize` (section 3.1, step 5): the old session stays bound to the old
    key;
  - in user mode each person needs their own session on each virtual server.
    The per-call credential adapter must never send one person's session id with another
    person's token. A refreshed OAuth token keeps its session, because the
    binding is the person, the client and the org, not the token
    (`GW:mcp/session.go:109-113`);
  - `initialize` never checks a session id it is sent
    (`GW:mcp/method_initialize.go:28-55`), so a client that marks itself
    uninitialized and initializes again recovers, even if a legacy client
    still sends the old id on that `initialize` after `close()`.
- **Timeouts.** EE's client waits 30 s for each request
  and its discovery probe 15 s. A
  member's default `timeout_ms` is also 30,000, so a slow upstream can
  time EE out before the gateway's `upstream_error` arrives.
- **Sizes.** EE keeps results up to 1 MiB and input schemas up to 64 KiB.

### 4.2 Agent mode

EE sends `Authorization: Bearer <org virtual key>`
(`GW:mcp/http_auth.go:78-83`), built from the workspace's current key
(section 3.1); the key's id is the virtual server's `access.keys` entry.

With request auth on and a configured key store, requests not already admitted
by license authorization or a delegated principal use the key gate. When MCP
client sign-in is disabled, a missing or unrecognized gateway key receives HTTP
401 with the model-path invalid_api_key envelope. When MCP is enabled and both
mcp.client_oauth_enabled and mcp.oauth.enabled are true, the gate forwards
missing or unrecognized credentials on the MCP endpoint to MCP authentication;
a refused credential then receives MCP's JSON-RPC authentication denial. A
configured deployment key awaiting the first control-plane key list instead
receives HTTP 503 key_list_not_loaded before that handoff.

The model-path error object is `{"error": {message,
type: "authentication_error", param: null, code: "invalid_api_key"}}`,
not JSON-RPC (`GW:middleware/auth.go:119-152`; fixture
`protocol/401-invalid-api-key.json`). Unknown includes a key the
answering replica has not loaded yet: one minted since its last key sync, or
any synced key on a replica that has just started and is waiting for its
startup sync (section 3.1). With request auth off or the MCP client sign-in handoff enabled as above,
the MCP layer answers refused keys with JSON-RPC -32001 and `data.code`
`invalid_api_key` or `key_expired` (`GW:mcp/principal.go:27-38`), and EE handles that the same way.
EE handles this 401 in order:

1. **A key that is no longer current.** If the key EE sent is not the
   workspace's current key in its store, as for a client built before a
   rotation, EE rebuilds the client from the current key (a new client and a
   new `initialize`) and retries. No window applies.
2. **Inside a window.** If it is the current key and its minting window or
   its 401 window is open (section 3.1), the 401 is transient: a replica has
   not loaded the key yet. EE backs off and retries with the same key. A call
   that cannot wait that long fails as a transient gateway error, never as a
   refusal.
3. **After the windows, ask Django.** EE calls ensure for the connection,
   with the current `agent_key_id` (section 3.2), and its answer decides; the
   key gate's 401 alone never does:
   - 400 `agent_key_invalid`: the key is no longer active (rule 3: revoked,
     deleted or expired). The connection is Refused, as EE marks it for a 401
     today.
   - 200: the key is live, and a replica is behind. The connection shows the
     gateway as unavailable, not as refused; EE keeps backing off and
     retrying, and asks ensure again once per window. If ensure has answered
     200 for a whole window and the 401 goes on, a replica is not loading keys
     at all (for example, more than one replica at `control_plane.sync_interval`
     0, section 3.1): EE also flags the connection for an admin, still as the
     gateway unavailable and never as Refused, and keeps retrying once per
     window.
   - No answer, or a 5xx (`gateway_sync_failed` included): the app is down,
     as it is during a rollout's migrations. The same: the gateway shows as
     unavailable, and EE keeps backing off.
   - Any other error is about EE's provisioning itself (section 3.5): its
     service credential refused, a member server removed, or its members'
     credential mode changed. The connection needs an admin, and EE says
     which, never as a refused sign-in.

**After Refused.** Nothing is deleted. When someone reconnects the connection
in EE, EE calls ensure with the stored current key. If it answers 400
`agent_key_invalid`, EE mints a new key (section 3.7), makes it the
workspace's current key, calls ensure with it for each of the workspace's
agent-mode connections and rebuilds their clients (as in a rotation's steps 4
and 5, section 3.1), which clears Refused on every connection the dead key
refused. The new key's minting window applies. EE never does this on its own,
so an admin's revocation stands until a person reconnects (section 3.7).

EE's discovery probe must follow the same order. A single-attempt probe that
reports every 401 as a refused sign-in cannot distinguish an invalid key from
a replica that has not synced. The compatibility protocol requires retries
inside the windows and an ensure response before reporting a refusal.

An address the IP rules refuse gets the same gate's 403, `{"error": {...,
type: "permission_error", code: "permission_denied"}}` (fixture
`protocol/403-ip-not-allowed.json`; section 3.3, rule 3).

### 4.3 User mode

The user-mode compatibility protocol uses EE as a pre-approved first-party
OAuth client of the gateway's authorization server. Ensure rejects `user`
mode as described in section 3.2. Provisioning remains subject to the
availability restriction at the top of this page.

1. **Discover.** The gateway's
   `/.well-known/oauth-protected-resource{endpoint}/v/{vs_id}` names the
   resource and the issuer. The issuer's metadata is at
   `/.well-known/oauth-authorization-server/agentcc/mcp-oauth` on FutureAGI. EE reads the authorize and token endpoints from there.
2. **Authorize.** Authorization code with PKCE S256 only; `resource` is the
   provisioned `url`; scope `mcp:tools`, plus `mcp:resources` and
   `mcp:prompts` if wanted; the redirect URI EE registered for its client,
   matched exactly. The person signs in to FutureAGI. The authorization server shows EE's client no
   consent page, so each person consents once, at the vendor (step 6), and
   still sees in EE which account is connected and can disconnect it. EE
   registers exact redirect URIs only, never a wildcard: its Console's OAuth
   callback page, the value EE reads from `OMEGA_INTEGRATIONS_REDIRECT_URL`, on the hosted Console's origin,
   plus loopback for local development.
3. **Token.** The access token is an ES256 JWT,
   `mcp-access-token-claims-v1` (fixture `tokens/m24-access-token-claims.json`):
   `aud` is the provisioned `url`, `sub` the FutureAGI user, and `exp - iat`
   at most 900 s. Refresh tokens rotate on every use, and reusing one revokes
   the chain.
4. **Refresh.** EE serializes refreshes per (person, virtual server) across its
   replicas: two replicas refreshing with the same token trip reuse detection
   and revoke the person's grant. On a 401 `invalid_token` (fixture
   `protocol/401-invalid-token.json`), EE refreshes once and retries once. A
   second 401 means "Connect yours" again. MCP client sign-in is implemented but disabled by default. With MCP enabled
   and both mcp.client_oauth_enabled and mcp.oauth.enabled set, the key gate
   passes credentials it does not authenticate to MCP's bearer verification;
   with sign-in disabled, the gate refuses them first.
5. **Bind the token to the right person.** The OAuth token's `sub` is the FutureAGI user
   id, the same value EE binds as `subject` in its host identity, which it
   keys by (issuer, subject, org, workspace). The two
   issuers differ, and EE configures both. Before storing a token, EE checks
   it against the run's bound host identity: `sub` must equal its subject,
   `org_id` its org, and `aud` the `url` that ensure returned for this
   connection, which EE provisioned in that workspace (fixture
   `tokens/ee-host-identity.json`). It refuses a token that fails any of
   them, so someone signed in to FutureAGI as another user cannot store that
   user's grant under their own EE account.
6. **Vendor account.** The person's first call without a vendor account answers
   `auth_required` with a `connect_url` (section 5). That is the person's one
   consent, at the vendor, through the gateway.

### 4.4 Headers

EE's client sends `content-type: application/json`,
`accept: application/json, text/event-stream`, `authorization`, and, after
initialize, `mcp-session-id`. A session is valid only with the same principal
that opened it (the key, or the person and EE's client) and the same `url`
(section 4.1). A refreshed OAuth token is the same principal.

The per-call metadata request shapes include `mcp-protocol-version` after
`initialize` (fixtures `mcp/tools-call.approval-retry.request.json` and
`mcp/tools-call.with-agent-context.request.json`). They may also send:

- `traceparent` and `tracestate`;
- these canonical agent-context headers:
  `x-agentcc-agent-id`, `-agent-version`, `-run-id`, `-parent-run-id`,
  `-task-id`, `-trace-id`, `-workflow`, `-turn`, `-budget-scope`,
  `-budget-usd`, `-budget-remaining-usd`, `-max-cost-usd`, `-attempt-id`,
  `-deadline-ms`, `-env`, `-framework`, `-session-id` and `-generation-id`;
- `x-agentcc-mcp-allow-tools`, which can only narrow the tools of one request
  (`GW:mcp/hook_acl.go:20-22`).

The schema refuses every other header. Never send `x-agentcc-user-id`,
`x-omega-user-id`, `x-resource-id`, `x-fa-end-user`, `x-api-key`, `cookie`,
or any header that carries a vendor token (section 8).

### 4.5 Tool names

- **The gateway's names.** It exposes `<server>__<tool>`
  (`GW:mcp/vserver/names.go:20-21,43-47`), with the characters
  `[A-Za-z0-9_.-]` and at most 64 of them. A longer name keeps its
  first 55 characters, then `_` and 8 hex digits of its sha256.
  Server ids start with a letter or digit, so every prefixed name passes
  EE's rule `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`.
- **EE's names**. EE accepts a server's raw tool names of 1 to
  128 characters, MCP's limit, under the rule above; it stores postures under
  the gateway's names and calls `tools/call` with them. Its models see a
  composite name, EE's own `custom_mcp.<connector>.<tool>`, of at most 64 characters,
  which the compatibility protocol requires EE to shorten deterministically,
  with a hash, when it is longer. The catalog caps its own tool
  names at 64, which is stricter, so every catalog name is valid in EE.
- **128 tools.** EE keeps at most 128 tools per server, and refuses a longer list with
  `tool_manifest_too_large`. One member per virtual server keeps most apps far
  below that.

### 4.6 Results and `_meta`

Every `tools/call` answer, success or refusal, carries
`_meta["io.futureagi/tool_call"] {tool_call_id, executed, billing_mode,
billed_usd, latency_ms}` (`GW:mcp/hook_evidence.go:9-43`; fixture
`results/success.json`).

- **EE can trust it.** The gateway drops any `io.futureagi/*` key an upstream
  put in its result's `_meta` before answering (`GW:mcp/respond.go:103-117`),
  so these keys are always the gateway's. EE's client keeps result fields it
  does not know, so `_meta` reaches EE's broker.
- **`executed`** means "may have executed": it is true once the call was
  dispatched upstream, even if no result came back
  (`GW:mcp/hook_evidence.go:17-30`). EE must not blindly retry a
  non-idempotent call that says true. A refusal before dispatch says false,
  with `billed_usd: 0`.
- **`billing_mode`** is `byok` for every customer-connected server, billed 0. `managed` appears only for FutureAGI-run `fagi.*` servers.
- **`_meta["io.futureagi/budget"]`** `{scope, calls_remaining?,
usd_remaining?}` describes the remaining tool budget.

## 5. Typed results

A refusal on `tools/call` is HTTP 200 with `{content: [{type: "text", text:
"<message> [<code>]"}], isError: true, _meta: {"io.futureagi/error": {type,
code, message, param: null, metadata}, "io.futureagi/tool_call": {...}}}`
(`GW:mcp/denial.go:38-56,103-133`). EE keys on `code`; `type` is
informational.

### 5.1 Typed-result names

`check.sh` compares this complete list in both directions with the reference
names in `typed-results.json`. No separate planning document is needed when
running with `MCP_PLAN=none` (section 10).

<!-- typed-results:begin -->

| Name                          | Kind                              | Gateway shape                                                                                                                                                                          | EE handling                                                                                                                       |
| ----------------------------- | --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `auth_required`               | result code                       | `metadata {server_id, reason, connect_url}`; per-user credentials                                                                                                                      | "Connect yours": in user mode, open `connect_url` for that person; in agent mode, ask an admin to reconnect. The tool did not run |
| `connect_url`                 | field of `auth_required`          | The vendor connect flow's start page with a sealed binding. It grants nothing by itself: it needs a FutureAGI sign-in as the bound user                                                | Open it in the person's browser; never fetch it from the server                                                                   |
| `approval_required`           | result code                       | `metadata {approval_id, status: pending, approve_url, poll_url, expires_at}`; approvals                                                                                                | An Inbox item. The tool did not run                                                                                               |
| `approve_url`                 | field of `approval_required`      | The FutureAGI console page for the approval                                                                                                                                            | Link to it from the Inbox item                                                                                                    |
| `poll_url`                    | field of `approval_required`      | `GET {endpoint}/approvals/{approval_id}`, for the same principal only                                                                                                                  | Poll with the same credential until it is decided or `expires_at` passes                                                          |
| `io.futureagi/approval_id`    | `_meta` key, on the request       | Retry the same call with `params._meta["io.futureagi/approval_id"]`; it runs once, and only with the same arguments                                                                    | Requires per-call metadata; a client that sends only `{name, arguments}` cannot supply the approval id                            |
| `tool_rate_limited`           | result code                       | `metadata {scope, limit, window_s, retry_after_s}`; tool budgets and rate limits                                                                                                       | Retry after `retry_after_s`, not before                                                                                           |
| `retry_after_s`               | field of `tool_rate_limited`      | Seconds; other refusals that clear by themselves carry it too                                                                                                                          | The wait before one retry                                                                                                         |
| `tool_budget_exhausted`       | result code                       | `metadata {limit_source: agentcc_tool_budget, scope: run:<scope>, resets_at: null, kind, remaining}`; tool budgets and rate limits                                                     | End the run as a spend-budget stop (`OmegaSpendBudgetExceededError`)                                                              |
| `agentcc_agent_budget`        | `limit_source`, not a code        | The limit source of the model-call refusal `agent_budget_exhausted` (`GW:models/errors_billing.go:18`), which a tool call can meet when the run's dollar budget also covers tool spend | The same spend-budget stop                                                                                                        |
| `managed_credits_*`           | code family                       | `managed_credits_exhausted` and `managed_credits_insufficient`, for FutureAGI-run `fagi.*` tools only; managed billing                                                                 | A failed tool call; the run goes on                                                                                               |
| `managed_billing_unavailable` | result code                       | `metadata {reason, retry_after_s}`; managed billing                                                                                                                                    | A failed tool call; the run goes on                                                                                               |
| `io.futureagi/tool_call`      | `_meta` key, on the result        | On every result and refusal; tool-call metering                                                                                                                                        | Read it: it is the meter (section 7)                                                                                              |
| `tool_call_id`                | field of `io.futureagi/tool_call` | `tc_<ULID>` (`GW:mcp/hook_meter.go:47-48`)                                                                                                                                             | Join EE's run record to the gateway's                                                                                             |
| `executed`                    | field of `io.futureagi/tool_call` | May have executed                                                                                                                                                                      | Section 4.6                                                                                                                       |
| `billing_mode`                | field of `io.futureagi/tool_call` | `byok` or `managed`                                                                                                                                                                    | Show managed spend only                                                                                                           |
| `billed_usd`                  | field of `io.futureagi/tool_call` | What FutureAGI billed for this call                                                                                                                                                    | Record it; never bill it again                                                                                                    |
| `latency_ms`                  | field of `io.futureagi/tool_call` | Measured by the gateway                                                                                                                                                                | Telemetry                                                                                                                         |
| `io.futureagi/budget`         | `_meta` key, on the result        | `{scope, calls_remaining?, usd_remaining?}`; tool budgets and rate limits                                                                                                              | Show the remaining budget                                                                                                         |

<!-- typed-results:end -->

### 5.2 Other in-band codes EE may see

These additional codes are outside the typed-result list above. EE treats each as a failed
tool call unless its row says otherwise, and never retries a gateway refusal
it cannot clear.

| Code                                                                                                                      | Component                    | EE handling                                                                                                                                                                                                                                                                                                           |
| ------------------------------------------------------------------------------------------------------------------------- | ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tool_denied`                                                                                                             | access rules                 | Final: a gateway rule refused this caller (`metadata.rule`). Show that the organization's gateway policy blocked it. A tool an access rule denies is also left out of `tools/list` (`GW:mcp/hook_list_filter.go:29`), so EE, which calls only tools it discovered, meets this when a rule changed after its discovery |
| `tool_quarantined`                                                                                                        | tool-definition review       | The tool's definition changed and an admin must approve it. Do not retry                                                                                                                                                                                                                                              |
| `invalid_arguments`                                                                                                       | argument validation          | Return the errors to the model                                                                                                                                                                                                                                                                                        |
| `approval_mismatch`                                                                                                       | approvals                    | The arguments changed after the approval; ask again                                                                                                                                                                                                                                                                   |
| `tool_call_depth_exceeded`, `agent_run_stopped`                                                                           | tool budgets and rate limits | Stop the run                                                                                                                                                                                                                                                                                                          |
| `managed_tools_disabled`, `managed_tool_over_cap`, `managed_spend_velocity`, `managed_spend_paused`, `key_limit_exceeded` | managed billing              | A failed tool call                                                                                                                                                                                                                                                                                                    |
| `tool_call_blocked`, `tool_result_blocked`                                                                                | guardrails                   | A guardrail refused the arguments or the result                                                                                                                                                                                                                                                                       |
| `upstream_error`                                                                                                          | upstream transport           | The vendor failed (`metadata.attribution`); `executed` may be true                                                                                                                                                                                                                                                    |
| `policy_unavailable`                                                                                                      | policy lookup                | Retry later                                                                                                                                                                                                                                                                                                           |

### 5.3 Protocol-level answers

| HTTP | Body                                                                                                                                                                                                                                                       | Meaning                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | EE handling                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 401  | The model path's error object `{"error": {message, type: "authentication_error", param: null, code: "invalid_api_key", metadata: {reason}}}`, not JSON-RPC; header `WWW-Authenticate: Bearer realm="agentcc-gateway"`                                      | The gateway's key gate, with request auth on and MCP client sign-in disabled (section 4.2): no key, or one it does not know or that was revoked or has expired (`GW:auth/keystore.go:152-154`) (`GW:middleware/auth.go:69-79`, `GW:models/errors.go:64-66,125-126`). A key the answering replica has not loaded yet is one it does not know: one minted since its last key sync, or any synced key on a replica that has just started and whose startup sync has not loaded keys (section 3.1) | Agent mode, in order (section 4.2): a key that is no longer the workspace's current key, rebuild the client from the current key and retry; while the key's minting window or its 401 window is open (section 3.1), back off and retry; after them, call ensure with the current `agent_key_id`, whose answer decides: 400 `agent_key_invalid`, Refused; 200, no answer or a 5xx, show the gateway as unavailable and keep backing off (after a whole window of 200s, also flag it for an admin); any other error, an admin is needed. This 401 alone never makes a connection Refused                                                                                                                                                                                                                 |
| 401  | JSON-RPC -32001, `id` null, `data.code` one of `missing_credentials`, `invalid_api_key`, `key_expired`, `ip_not_allowed`, `invalid_token`; header `WWW-Authenticate: Bearer realm="agentcc-gateway"`, with `resource_metadata` for OAuth resource metadata | The MCP layer's own check, for a credential the key gate let through (`GW:mcp/principal.go:24-33,258-275`; `GW:mcp/http_auth.go:53`). With MCP enabled and both `mcp.client_oauth_enabled` and `mcp.oauth.enabled` true, the key gate forwards missing or unrecognized credentials to MCP authentication; with sign-in disabled and request auth on, the gate refuses them first (sections 4.2 and 4.3)                                                                                        | User mode: refresh once (section 4.3). Agent mode, with request auth off or the MCP client sign-in handoff enabled as in section 4.2, `invalid_api_key` or `key_expired`: as the key gate's 401 above (section 4.2)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| 403  | The model path's error object `{"error": {message, type: "permission_error", param: null, code: "permission_denied"}}`, not JSON-RPC; no `WWW-Authenticate`                                                                                                | The key gate's IP rules refuse the caller's address: the gateway's `ip_acl`, the org's `ip_acl` (including when the gateway-wide rule is off; section 3.3) or the key's `allowed_ips` (`GW:middleware/auth.go:56-63,81-86`, `GW:plugins/ipacl/ipacl.go:82-101`; section 3.3, rule 3)                                                                                                                                                                                                           | Show that the organization's IP rules block hosted EE, and ask an admin to admit EE's egress addresses. Do not retry                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| 403  | The model path's error object `{"error": {message, type: "permission_error", param: null, code: "key_scope_denied"}}`, not JSON-RPC; no `WWW-Authenticate`                                                                                                 | the agent key on a route outside its scope (section 3.7; fixture `protocol/403-key-scope-denied.json`)                                                                                                                                                                                                                                                                                                                                                                                         | A defect in EE: it sent the agent key where it may not go (section 8, item 12). Do not retry; fix the caller                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| 402  | `gateway_key_budget_error`: the model path's error object in error-402-v1's shape, though not in its code list: `code: "agent_key_budget_exhausted"`, `metadata.limit_source: "agentcc_key_limit"`, `scope: "key:<agent_key_id>"`, `resets_at`             | a model call once the key's budget for the period is spent (section 3.7; fixture `protocol/402-agent-key-budget-exhausted.json`)                                                                                                                                                                                                                                                                                                                                                               | Fail the call, show the workspace's agent budget as spent until `resets_at`, and ask an admin to raise it. Never mint another key to get around it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| 403  | `insufficient_scope`                                                                                                                                                                                                                                       | The token lacks a scope                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | Authorize again with the scope                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| 404  | No body and no content type                                                                                                                                                                                                                                | The session is unknown, has ended (section 4.1: idle past `mcp.session_ttl`, or 24 hours old) or is bound to another principal or virtual server (`GW:mcp/method_initialize.go:92-117`)                                                                                                                                                                                                                                                                                                        | Initialize again on the same `url` with the same credential, then retry the request once. Never call ensure or detach for it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| 404  | JSON-RPC -32600, `virtual server not found`, no `data`                                                                                                                                                                                                     | The replica that answered has no such virtual server for this caller (`GW:mcp/hook_vserver.go:74-77`). It comes on `tools/list` or `tools/call`; `initialize` does not look the virtual server up (`GW:mcp/chain.go:76,105`). Inside a change's window (section 3.1) it can be a replica that has not read the change (a new virtual server, rule 9, or a rotation's new key), and after a rotation, a client still on the old key (section 3.1, step 5)                                       | In order. Agent mode: if the client's key is not the workspace's current key, rebuild the client from the current key and retry. Otherwise call ensure again, in agent mode with the workspace's current `agent_key_id` from EE's store and never the key the client was built with (rule 1), and read its answer as section 4.2 does: 400 `agent_key_invalid`, Refused; no answer or a 5xx, the app is down, so show the gateway as unavailable and keep backing off; 200, initialize again and retry, with backoff, for up to one window (2 minutes by default) from the first such 404, or until the change's window ends if that is later. If ensure keeps answering 200 and the 404 persists, mark the connection as needing an admin to reconnect it, and stop. Never detach on it (section 3.4) |
| 404  | The model path's error object `{"error": {message: "Unknown endpoint: <path>.", type: "not_found", param: null, code: "not_found"}}`, not JSON-RPC                                                                                                         | That gateway does not serve its MCP endpoint: with `mcp.enabled` off, no MCP route is registered and the key gate does not cover the path, so the router answers whatever the key (`GW:server/routes_mcp.go:73-77`, `GW:server/router.go:165`; fixture `protocol/404-mcp-not-served.json`). MCP availability depends on the deployment configuration                                                                                                                                           | Fail the call and show that the gateway's MCP endpoint is not available. Never call ensure or detach for it, and do not retry                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| 503  | JSON-RPC -32603, `session store unavailable`                                                                                                                                                                                                               | The gateway could not read or write its session store (`GW:mcp/method_initialize.go:162-167`)                                                                                                                                                                                                                                                                                                                                                                                                  | Retry the same request on the same session after a short wait                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |

**Telling the three 404s apart.** Only the body does: a JSON-RPC body with
-32600 is the virtual server's, an empty body is the session's, and a
model-path JSON body with `code: "not_found"` is the router's (fixtures
`protocol/404-virtual-server-not-found.json`,
`protocol/404-unknown-session.json` and `protocol/404-mcp-not-served.json`;
`check.sh` checks that each validates as its own shape and as no other). A
lost session is routine (an idle timeout, the 24-hour cap, a key rotation),
and handling it as a lost virtual server would make EE provision again for
nothing. A legacy client that drops the body of non-2xx answers and throws only
`MCP HTTP request failed with status 404.` loses this distinction. A broker
that keeps one client per connection needs both status and body; for an empty
404 it must reset and initialize that client again, then retry once.

## 6. Posture and approvals

- **EE decides posture.** Auto runs; Ask parks the run in EE's Inbox before
  any call; Deny never calls (Ask is the default).
- **A gateway refusal always wins.** EE never overrides or works around
  `tool_denied`, a guardrail block or a budget refusal.
- **Gateway approvals are rare.** The provisioning API provisions `approval.require: none`, so
  `approval_required` comes only from a rule an org admin adds at the gateway,
  which a later ensure keeps (section 3.3, rule 1), or from a tool policy
  (section 5.1). The retry requires per-call approval metadata.

## 7. Metering

The gateway's `mcp-tool-call-v1` record is the one meter. EE reads
`billed_usd` and `billing_mode` from `_meta["io.futureagi/tool_call"]` and
never bills a tool call separately on hosted. Customer-connected servers are
`byok` and billed 0.

## 8. What EE must never do

1. **Forward a vendor token**, in `authorization` or any other header
   (fixtures `forbidden/tools-call.vendor-token-*.json`). In broker = gateway
   mode the gateway holds vendor tokens and EE holds none. The gateway would
   not forward an inbound token upstream anyway.
2. **Assert a user in a header or in `_meta`**: no `x-agentcc-user-id`,
   `x-omega-user-id`, `x-resource-id`, `x-fa-end-user` or
   `io.futureagi/agent.user_id` (fixtures `forbidden/tools-call.user-id-header.json`,
   `end-user-header.json` and `agent-meta-user.json`). In user mode the user
   is the OAuth token's `sub`.
   Never assert a user in request headers or \_meta; any user identity carried
   by a gateway key comes from trusted key issuance, not those inputs.
3. **Provision with the gateway admin token or the org's `system`
   `OrgApiKey`** (section 3.1; fixtures `forbidden/ensure.request-*.json`), or
   use one person's key or token for another person's runs.
4. **Call a virtual server provisioned for another connection or workspace**,
   or build a `vs_id` or URL itself. Use the `url` that ensure returned.
5. **Retry** a non-idempotent call whose `executed` is true, or a final gateway
   refusal.
6. **Accept an ensure or detach response** that carries a credential, or an
   ensure whose `url` is not public HTTPS (fixtures
   `forbidden/ensure.response-*.json`). Only the agent-key mint returns one
   (section 3.7).
7. **Detach to recover from an error** (section 3.4), or provision again
   because a session was lost.
8. **Share a session** between two principals (two keys, or two people) or
   two virtual servers (section 4.1), or use one agent key for two workspaces
   (section 3.3, rule 3). A refreshed OAuth token is the same principal and
   keeps its session.
9. **Send an older agent key in an ensure**, or keep calling with a client
   built from a key that is no longer the workspace's current one. Every
   ensure carries the current `agent_key_id` from EE's store, because rule 1
   would move the virtual server back to an older key, and a client is rebuilt
   when the current key changes (section 3.1).
10. **Mark a connection Refused on the key gate's 401 alone.** Only ensure's
    400 `agent_key_invalid` for the current key does, once the key's windows
    have ended (section 4.2). Nor read a virtual server's 404 as a reason to
    detach (section 5.3).
11. **Mint a new agent key on its own after a revocation**, or bind a key the provisioning API
    did not mint for that workspace (sections 3.3, rule 3, and 4.2).
12. **Send an agent key anywhere but the virtual servers and the three model
    endpoints** (section 3.7): every other route answers 403
    `key_scope_denied`, which is a defect in EE, not a state to retry.
13. **Mint a new agent key to get around a spent budget or the cap**
    (section 3.7). A rotation mints one new key and revokes the old one.
14. **Mint without an Idempotency-Key, or retry an uncertain mint with a new
    one** (section 3.7). After a 409 `agent_key_mint_replayed`, never
    keep the replayed key unrevoked: revoke it, then mint with a new
    Idempotency-Key.

## 9. Self-hosted EE and OSS

Nothing changes for them. Self-hosted EE keeps its built-in broker, with
operator OAuth apps from its environment and settings file; broker = gateway
is an operator switch that is off unless set. OSS Omega may point an ordinary
MCP server entry at a virtual-server URL with a FutureAGI key.

## 10. Fixtures and the check

The directory `agentcc-gateway/scripts/scenarios/fixtures/omega-ee-attach/`
holds:

| Path                                                                                   | What                                                                                                                                                           |
| -------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `schema.json`                                                                          | The JSON Schema (2020-12) for every shape, one `$defs` entry per kind of fixture                                                                               |
| `fixtures.json`                                                                        | Each fixture, its `$defs` entry and the checks it must pass, and, for `forbidden/`, the reason it must fail                                                    |
| `provision/`                                                                           | Ensure (agent, user and a repeat), detach, the agent key's mint (with its Idempotency-Key), its replay, its find and revoke, and each error                    |
| `mcp/`                                                                                 | Compatibility request shapes for both modes, their tool lists, and approval retry and agent context requests with per-call metadata and `mcp-protocol-version` |
| `results/`                                                                             | A success and each typed result                                                                                                                                |
| `protocol/`                                                                            | The key gate's 401 and 403, the MCP layer's 401, and the three 404s (the virtual server's, the session's and the router's)                                     |
| `tokens/`                                                                              | OAuth access-token claims and token response, and the EE host identity the claims must match (section 4.3, step 5)                                             |
| `forbidden/`                                                                           | Shapes EE must never send or accept; each must be refused for its stated reason                                                                                |
| `typed-results.json`                                                                   | The pinned reference names, their source text and the extraction rule                                                                                          |
| `SHA256SUMS`                                                                           | The sha256 of every file above                                                                                                                                 |
| `check.sh`, `validate.py`, `schema_subset.py`, `fixture_checks.py`, `source_checks.py` | The check                                                                                                                                                      |

Run `MCP_PLAN=none bash agentcc-gateway/scripts/scenarios/fixtures/omega-ee-attach/check.sh`.
It:

- validates every fixture against the schema;
- runs the cross-fixture checks, for example that requests use the provisioned
  `url`, that the token's audience is that `url`, that every `tools/call`
  names a tool a `tools/list` fixture lists, that the three 404s cannot be
  mistaken for one another, that the key the provisioning API mints is the one ensure binds
  and the agent-mode requests send, that the token's `sub` and `org_id` are
  EE's bound host identity, and that only the per-call metadata requests carry
  `mcp-protocol-version`;
- checks that the gateway and Django code the fixtures copy still says the
  same thing: the `_meta` keys, the field names, the name rule, the 401 and
  404 shapes, the key and tool-call id formats, how keys reach the replicas (a
  new key is not broadcast, a revocation is, and a starting replica is not
  ready before its startup sync has loaded keys), how Django's key service
  mints and revokes, and the error envelope;
- compares section 5.1 with the pinned reference names;
- runs the repository's `secrets-lint.sh` over the fixtures and this page.

Credentials in fixtures are always `{{PLACEHOLDER}}` tokens. `MCP_PLAN` optionally names an external reference document;
a missing reference is a failure, never a skipped comparison. For a checkout
with only public files, including CI, run with `MCP_PLAN=none`: the check
extracts the names from the reference text vendored in `typed-results.json`,
verifies its pinned sha256 and reports that source in its output.
`OMEGA_EE_ATTACH_JSONSCHEMA_PYTHON` names a Python with the `jsonschema`
package, which must then agree with the bundled validator on every fixture.

**Pinning.** Consumers pin the public contract by version and set digest.

<!-- prettier-ignore -->
Contract: `omega-ee-attach/v1`. Set digest: `sha256:22f51018609e39eefa84eaf1a7e8852160827bd3148e107242f385e6332f6ea3`
(the sha256 of `SHA256SUMS`).

The protocol fixtures match the gateway error helpers. The key gate uses
`ErrUnauthorizedKeyFor(r.URL.Path, rawKey)` through `writeGateError` for its
missing-key and unknown-key refusal branch. On the MCP endpoint that branch
returns the model-path JSON envelope and the Bearer challenge. With MCP
enabled and both `mcp.client_oauth_enabled` and `mcp.oauth.enabled` true,
the gate instead hands missing or unrecognized credentials to MCP
authentication (section 4.2); not every request takes the refusal branch.
`protocol/401-invalid-api-key.json` represents an unrecognized gateway-format
key, with the helper's exact message and `metadata.reason: "unknown_api_key"`.
Missing keys have the distinct `missing_api_key` reason. IP refusals use the
same writer and retain their 403 envelope without a challenge. The router's
`unknownEndpointMessage` adds a final period to the MCP-not-served 404.
`source_checks.py` pins the call sites, helper behavior and complete fixture
envelopes, including status, headers, code, type, param, message and metadata;
none of these pins is a wildcard for the old response.

## 11. Changing this contract

An additive change keeps `v1`: a new optional field, or a new code that EE
may treat as a failed tool call. Run `validate.py --write-sums`, update the set
digest above, and provide consumers with the new digest. Anything that
removes or renames a field, changes a meaning, or tightens what EE must send
is `v2`, in a new directory beside this one.

That rule applies once a consumer has pinned a digest. The initial `v1`
compatibility requirements include the session's 404, the key gate's 403,
rules 1, 3 and 9, strict URL and key-id patterns, the two key windows and
rotation order (section 3.1), and Refused decided by ensure's
`agent_key_invalid` (section 4.2). They also include service credentials,
agent-key endpoints, exact redirect URIs, workspace access, subject binding,
tool-name limits and per-call protocol metadata.

The first additive change after a consumer pinned `v1` (2026-10-21): the 503 code `gateway_url_not_public` (section 3.5),
which EE treats as a failure ("gateway unavailable, an operator must act"), as
it treats any 5xx. It moved the set digest from `sha256:2091a2b7…7d739` to
`sha256:de712f7b…8be22`.

The second additive change (2026-10-21): the
agent key is scoped. Two Django codes (403 `key_scope_denied`, 409
`agent_key_cap_reached`), two gateway answers (`gateway_key_scope_error`,
`gateway_key_budget_error`), the optional `expires_at` in the mint's answer
and `agent_key_expires_at` in ensure's, each a code EE may treat as a failure
or a field it may ignore (section 3.7). It moved the set digest from
`sha256:de712f7b…8be22` to `sha256:2ddbacd0…d718d`.

The third change (2026-10-22): idempotent agent-key minting
(section 3.7). Two answers EE may treat as a failed mint, 409
`agent_key_mint_replayed` (its own shape, `provision_agent_key_mint_replayed_response`,
with the replayed key's fields) and 422 `idempotency_key_reused`; the 400
`idempotency_key_invalid`; `mint_idempotency_key` in the workspace's key list
and its `?mint_idempotency_key=` filter (`provision_agent_keys_find_*`), which
EE's service credential may now read. The required `Idempotency-Key` header
tightens what EE must send. Its inclusion in `v1` requires the only consumer
to adopt idempotent minting before making calls under this gateway contract.
Compatible EE and gateway builds must use the same mint protocol: deploy them together.
An EE build without the required header gets 400 on every mint. This change
moves the set digest from `sha256:2ddbacd0…d718d` to the one above.

## 12. Integration requirements and constraints

The following requirements apply to the compatibility protocol:

- **Service credential.** Provisioning uses a dedicated EE service credential
  per org, scoped to provisioning and audited as EE; never the `system`
  `OrgApiKey` (sections 3.1 and 3.7).
- **Consent and redirects.** EE is a pre-approved first-party OAuth client.
  Each person consents at the vendor. Redirect URIs are exact: the Console
  OAuth callback page, plus loopback (section 4.3).
- **Workspace access.** User-mode authorization requires live FutureAGI
  membership; a synced `access.users` list is a fail-closed cache only
  (section 3.3, rule 6).
- **Subject binding.** The OAuth token's `sub` is the FutureAGI user id bound
  to EE's host identity. EE refuses a mismatched `sub`, org or audience
  (section 4.3, step 5).
- **Tool names.** Raw tool names are 1 to 128 characters; the composite name
  models see is at most 64, shortened deterministically (section 4.5).
- **Protocol revision.** Requests without the protocol header use the
  gateway's legacy `2025-03-26` behavior; per-call metadata requests include
  the negotiated revision (sections 4.1 and 4.4).
- **Session recovery.** A bodiless 404 requires initialize and one retry,
  never ensure or detach. Only a JSON-RPC -32600 body means the virtual
  server (section 5.3).

The contract also has these integration constraints:

- **Timeouts.** EE's 30 s client timeout equals the members' default
  `timeout_ms`; a slow upstream can time the client out before the gateway
  returns `upstream_error` (section 4.1).
- **Approval metadata.** An approval retry must send
  `params._meta["io.futureagi/approval_id"]`. Whether supplied through
  `callTool` in `@omega/core` or a per-call adapter, it must preserve the
  same principal and arguments (section 5.1).
- **Catalog membership.** Ensure requires each server to exist in the org's
  MCP config; it returns `server_not_found` for a missing member (section 3.2).
- **Partner servers.** This `v1` contract does not specify `vendor_end_user`
  partner-server membership in user mode. It does not authorize a user-id
  header as an alternative to the OAuth token's `sub` (section 8).
- **OAuth verification.** User mode requires the key gate to pass OAuth
  access tokens to the MCP verifier. A gate that accepts only virtual keys
  cannot serve this mode (section 4.3).
- **Discovery state.** During synchronization, EE may show a connection as
  "connecting" or wait before probing. It must not report the key gate's
  401 as a refused sign-in before ensure answers 400 `agent_key_invalid`
  (sections 3.1 and 4.2).
