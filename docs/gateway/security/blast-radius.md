# Gateway blast radius: what each credential and component exposes, and how to revoke it

For each part of the gateway stack, this page says what an attacker who holds
it can read, spend or call, how to revoke it, and how long the revocation
takes to reach every gateway replica. It describes the code as built at
`2786e6c7ff` (the W2c base). File references are to that commit. Two figures
are not known yet and are marked **pending P3, P4 (W4c)**: runtime-token and
delegated-grant propagation, which those packages build and test.

The scripted part of the drill is
`agentcc-gateway/scripts/release/blast-radius-drill.sh` (see [The
drill](#the-drill)). Supply-chain controls are in
[supply-chain.md](supply-chain.md).

## How changes reach the replicas

Three paths carry a change to the gateway. Every row below uses one of them.

| Path                               | What it carries                                                                                                             | Reaches                                                                                                                                                                                                                                           |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Redis pub/sub `agentcc:key:revoke` | A virtual key revoked through `DELETE /-/keys/{id}` (`server/admin_keys.go:198-222`, `redisstate/revocation.go`)            | The replica that took the call at once; every other replica on the same Redis as soon as it reads the message. The drill measured 53 to 118 ms over four local runs with two replicas (this includes the revoke call itself, polled every 20 ms). |
| Push from Django                   | Org config, including provider credentials, managed opt-in, privacy and policy (`services/config_push.py:1163-1188`)        | One replica: Django calls `AGENTCC_GATEWAY_INTERNAL_URL` (`services/gateway_client.py:26-43`), which a load balancer sends to one of them.                                                                                                        |
| Control-plane sync                 | Every org config and every active key, pulled from Django (`tenant/startup_sync.go:129-142`, `tenant/spend_sync.go:91-105`) | Every replica, once per `control_plane.sync_interval`: 60 s in `docker-compose.yml` and Helm (`AGENTCC_SYNC_INTERVAL`), and never at the gateway's own default of 0.                                                                              |

So an org config change, such as a removed provider key, takes effect at once
on one replica and within one sync interval on the rest. With a control-plane
URL, `sync_interval` is bounded to (0, 60 s]; zero, negative and larger values
are clamped to 60 s with a startup warning. Managed
credit balances are the exception: they live in the shared Redis, so a change
there reaches every replica at once.

A revocation holds through a key sync that read the key list before it: the
sync keeps a revoked key revoked (`auth/keystore.go` `SyncFromHashes`,
`TestSyncFromHashes_KeepsARevokedKeyRevoked`). A replica on another Redis,
or with none, gets no revoke message. It drops the key at its first
control-plane sync after Django has stored the key as revoked. A key created
on that replica stays until a sync that starts at least
`control_plane.key_sync_grace` (30 s by default) after the key was created
(the key sync's grace for just-created keys, `auth/keystore.go`
`syncFromList`).

## Components and credentials

### Gateway admin token (`admin.token`, `AGENTCC_ADMIN_TOKEN`)

One shared secret. Django and the gateway check the same value in both
directions (`config/config.go:1656-1660`).

- **Read.** Every org's config through Django's `GET /agentcc/org-configs/bulk/`
  (`views/org_config_bulk.py`, `IsAdminToken`). That payload is the push
  payload (`_build_payload`), so it holds every org's provider credentials,
  managed upstream keys and MCP secrets in clear. Also every active key's hash
  (`/agentcc/api-keys/bulk/`), and the gateway's `/-/config`.
- **Spend and call.** Every `/-/` admin route on every replica: create keys
  for any org (`/-/keys`), replace any org's config (`/-/orgs/{id}/config`),
  and grant managed credits (`/-/orgs/{id}/credits/granted`). With the
  credentials above, it can also call every upstream directly, outside the
  gateway.
- **Revoke.** Set a new token in Django and on every gateway, then restart
  every replica. `POST /-/reload` is not enough: it switches `/-/config` and
  the other `checkAdminAuth` routes to the new token, but `/-/keys`,
  `/-/orgs`, rotation and cluster routes keep the token they read at startup
  (`server/server.go:595, 607, 638, 649`). This was checked by a probe on
  2026-10-17: after a reload, `/-/keys` took the old token and refused the new
  one. Then rotate every credential the bulk endpoint returned.
- **Reaches every replica.** When the last replica has restarted.

### Data-plane process (the gateway binary)

- **Read.** Its memory holds the admin token, the webhook secret, the MCP
  state keys, and the config of every org it has loaded, with their provider
  credentials and managed upstream keys. Virtual keys from the control plane
  reach it only as SHA-256 hashes (`auth/keystore.go:682`); keys written into
  its YAML config (`auth.keys`) are in that file, and in memory, in clear.
- **Spend and call.** Everything the admin token can, and every upstream with
  any loaded credential.
- **Revoke.** Redeploy from an image whose signature you checked
  ([supply-chain.md](supply-chain.md#checking-a-release-yourself)). Then
  rotate the admin token (above), every managed upstream key
  (`manage.py agentcc_managed_upstream_keys <org_id> --rotate`, once for each
  managed org), the webhook secret and
  `AGENTCC_MCP_STATE_KEYS`. Each customer must also rotate the provider keys
  they gave the gateway, at their provider.
- **Reaches every replica.** New credentials reach the replica Django pushes
  to at once, and the others within one sync interval. The admin token and
  the state keys take a restart of every replica.

### Redis

The gateway keeps no provider credential and no raw virtual key in Redis
(`internal/redisstate/`, `internal/cache/backend_redis.go`, and the rate-limit,
quota, credits and budget plugins).

- **Read.** Managed credit state per org (`mc:{<org>}`), the settlement
  outbox, rate-limit and budget counters (`agentcc:rl:`, `agentcc:budget:`),
  per-key credits (`agentcc:credits:`), license runtime state
  (`license_grant:`, `license_session:` and the rest), agent routing signals,
  and cached responses when the response cache uses the Redis backend. Cached
  responses are request content.
- **Spend.** It can raise an org's managed balance in Redis, so the org
  spends past what it paid, or delete outbox entries, so settled spend never
  reaches the ledger. The ledger in Django stays the system of record
  (decision 4). The reconciler that reports the drift is pending (D8, W4);
  until it ships, compare the ledger with Redis by hand. A versioned granted
  push from the ledger (E1) restores the balance.
- **Call.** Publishing on `agentcc:key:revoke` revokes any key id on every
  replica, which is a denial of service. The channel ignores `redis.prefix`,
  and Redis pub/sub ignores the database number, so every gateway on that
  Redis server hears it.
- **Revoke.** Change the Redis password or ACL, put it in every gateway's
  config, restart every replica, then re-push granted balances from the
  ledger.
- **Reaches every replica.** When the last replica has restarted.

### Django (the control plane)

- **Read.** Everything: provider credentials (Fernet-encrypted, with the key
  in its environment), managed upstream keys, the admin token, users, request
  logs, and the managed credit ledger.
- **Spend and call.** Grant credits through the ledger, push any org config
  to the gateways, and call the gateway admin API with the admin token.
- **Revoke.** Rotate the admin token, the Fernet key, every provider
  credential, every `OrgApiKey` and every session. Check the ledger against a
  backup (and, once D8 ships, the reconciler's report).
- **Reaches every replica.** Pushed config: one replica at once, the rest
  within one sync interval. The admin token: when the last replica has
  restarted.

### Browser console

The console calls Django as the signed-in user, so a stolen session or an XSS
in the console has that user's role. For an Owner or Admin that is the next
row. Secrets the console saves never come back in clear: provider credentials
are returned masked (`agentcc/serializers/provider_credential.py:46-57`), and
managed upstream keys and MCP secrets are write-only (decisions 19 and 30).

- **Revoke.** Log out, or reset the password. Logout deactivates the session
  token and deletes its cache entry (`accounts/views/signup.py:325-347`), and a
  password reset does the same for every session of the user
  (`signup.py:619-629`), so both act at once. A session token is cached for 2
  days, renewed on each use (`AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES`). The
  cached path does not reread the token's `is_active`
  (`accounts/authentication.py:886-901`), so a path that deactivates a token
  without deleting its cache entry does not end the session.
- **Reaches every replica.** The gateway never sees console sessions.

### Org admin key (an Owner or Admin)

An org's `OrgApiKey` (`X-Api-Key` and `X-Secret-Key`; the `system` key acts as
the org's first user, `accounts/authentication.py:203-212`), or an Owner's or
Admin's console session.

- **Read.** The org's request logs, under the org's content mode (decision
  21), and its config. Not its stored provider credentials, which come back
  masked.
- **Spend and call.** Create virtual keys, and so spend through them. Change
  what the gateway spends, routes or enforces for the org: managed opt-in,
  budgets, provider credentials, routing, privacy, guardrails and MCP servers
  (D2's `IsOrgGatewayAdmin`, `agentcc/permissions.py:45-52`). It cannot grant
  managed credits, which is an operator action (E1's management command), or
  call the gateway's admin routes.
- **Revoke.** Disable or delete the `OrgApiKey`, or remove the user's Owner or
  Admin role. Both are read from the database on every request, so they act
  at once. Keys it created stay valid: revoke them as virtual keys (next
  row), and review config changes in the org-config version history.
- **Reaches every replica.** The gateway never sees this credential. Config
  it pushed stays until a new version is pushed (one replica at once, the
  rest within one sync interval).

### Virtual key

- **Read.** Its own calls' responses, and its own org's stored files, stored
  Responses and async jobs. Each of these belongs to the org of the key that
  KeyAuth authenticated when it was created (`callerOrgID` in
  `server/handlers.go`). Another org's key, or a key with no org, cannot list
  them, and gets the same 404 as for a missing object when it reads,
  downloads or deletes one (`server/stored_objects_org_scope_test.go`). On
  this build an org key cannot queue an async job at all: it gets 400
  `deferred_run_unavailable`. The response cache is partitioned the same
  way: every exact and semantic cache key, and every vector-store filter,
  starts from the org of the authenticated key, so a key is never served a
  response cached under another org or under the no-org tenant
  (`server/cache_org_scope_test.go`, `plugins/cache/org_scope_test.go`).
- **Spend and call.** Every model and provider its ACL allows, as its org. Its
  org is server-owned key metadata, which the caller cannot change
  (`auth/keystore.go:325-344`). It spends the org's own provider keys (billed
  to the customer by the provider) and, if the org opted in, the org's managed
  credits, within the key's own limits. It is not an admin credential: the
  drill checks that it gets 401 on `/-/keys`.
- **Revoke.** Revoke or delete it in the console. Django calls
  `DELETE /-/keys/{id}` on one replica (`services/auth_bridge.py:215-238`).
  If that call fails, Django marks the key revoked and the next sync revokes
  it on the gateway.
- **Reaches every replica.** At once on the replica that took the call, then
  through Redis pub/sub to the others: 53 to 118 ms in the drill. If Redis is
  down at that moment, the others keep the key until their next control-plane
  sync (at most a 60 s interval in control-plane mode). Negative `retired`
  entries are applied even when no active key remains. A complete snapshot
  with `issued_ever > 0` is authoritative even when empty; unlisted keys made
  through the admin API then expire out of the short create/sync grace window.
  Older responses without a snapshot, incomplete snapshots, and a database
  reporting `issued_ever = 0` retain the non-destructive empty-list guard.

Gateway keys are person-owned, require live organization membership at sync,
and do not claim role-derived scopes. A config seed in control-plane mode must
be `internal`, with neither `org_id` nor `user_id`; it returns retriable 503
`key_list_not_loaded` until the first seed-conflict answer. A hash that collides
with any stored personal key (including deleted/revoked rows) is refused on
every restart. Both sides of a config/synced hash collision are dropped.
Standalone gateways retain their operator-owned static config credentials;
they have no control-plane org/person model.

### Runtime token

A signed, short-lived (at most 15 minutes) token from the Future AGI managed
runtime, used in place of a virtual key (decision 22). At `2786e6c7ff` the
gateway does not accept one: `runtimeTokenMiddleware` is a no-op stub
(`server/hook_runtime_identity.go`). G33 (W4) builds it.

- **Read, spend and call.** As designed: the claimed org's models, within the
  run's budget, for at most the token's lifetime.
- **Revoke.** By expiry, and by revoking the runtime's grant.
- **Reaches every replica.** **Pending P3, P4 (W4c).** The delegated-grant
  revocation figure is pending P3, P4 (W4c) too.

## The drill

The drill is a checklist: the script runs the items the local stack can run,
and prints the rest for a person to do.

```bash
SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<your db> \
  bash agentcc-gateway/scripts/release/blast-radius-drill.sh
```

It builds the gateway and the mock upstream, starts two gateway replicas on
one Redis, and checks:

1. A key pushed the way Django pushes it (`POST /-/keys/sync`) works on both
   replicas.
2. A virtual key gets 401 on an admin route, and so does a wrong admin token.
3. Revoked on replica A (`DELETE /-/keys/{id}`), the key gets 401 on A at once
   and on B within `DRILL_MAX_MS` (2000 ms by default). It prints the measured
   time, and checks that another key still works on B.

It prints these as manual items, which no script checks:

- Revoke a key with Redis stopped, and confirm the other replicas drop it at
  their next sync.
- Rotate the admin token by restarting every replica.
- Rotate the Django secrets: the admin token, the Fernet key, the provider
  credentials.
- End a console session by logout or password reset.
- Runtime-token and delegated-grant propagation: pending P3, P4 (W4c).
