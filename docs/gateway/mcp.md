# MCP on the gateway

The gateway serves the Model Context Protocol at one endpoint, `/mcp`. An
agent connects to it with a gateway API key and gets the tools of the MCP
servers its organization has added. The gateway connects to each upstream
server with the organization's own credential, applies the key's tool
filters, and records every tool call.

This page covers the endpoint, adding servers and apps, credentials,
virtual servers, tool-call records, and the settings a self-hosted gateway
needs. What changed in this release is in the
[release notes](release-notes.md) (N13 to N18, C81 to C83, U24 to U27).

## Connect an agent

Point any MCP client at the gateway's `/mcp` with a key of your
organization, sent as `Authorization: Bearer <key>` or `x-api-key: <key>`:

```json
{
  "mcpServers": {
    "futureagi": {
      "url": "https://gateway.example.com/mcp",
      "headers": { "Authorization": "Bearer <your gateway key>" }
    }
  }
}
```

- `tools/list` returns the tools of your organization's servers, each named
  `<server>__<tool>` (for a server with id `github`, its `search_issues`
  tool is `github__search_issues`).
- A key's `allowed_tools` and `denied_tools` narrow what it can list and
  call.
- With request auth on (`auth.enabled`), every method and path under
  `/mcp` needs a valid key. With request auth off, a request with no key
  is served the gateway's config-file servers while `mcp.static.org_id` is
  unset, as before; it gets 401 on a virtual server and once
  `mcp.static.org_id` is set. A key that is sent is checked either way. A
  session belongs to the key that opened it, and any other key gets 404
  for it.
- One endpoint serves clients of MCP versions 2026-07-28 and 2025-06-18.

## Which servers a key reaches

| Key                                                                                      | Servers it reaches on `/mcp`                                                                                                          |
| ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| A key of an organization                                                                 | That organization's servers, and no other organization's                                                                              |
| A key with no organization (such as a key in the gateway's config file with no `org_id`) | The servers in the gateway's config file (`mcp.servers`)                                                                              |
| A key of the organization named by `mcp.static.org_id`                                   | Its organization's servers and the config file's servers; a server the organization saved shadows a config-file server of the same id |

When `mcp.static.org_id` is set, a key with no organization reaches no
config-file server.

An organization's servers are served while MCP is on for that
organization (`mcp.enabled` in its config, which an Owner or Admin turns
on from the MCP tools page). A server saved with `enabled: false` is not
connected.

Two organizations can each add a server with the same id, such as
`github`, pointing at different accounts. Each organization's keys list
and call only their own, and each call reaches only the caller's
organization's upstream.

## Add a server

On **Providers, Tools & Keys > MCP tools > Servers**, an Owner or Admin adds a
server by its URL, with its credential if it needs one. Members see the
servers and their tools.

The gateway connects to upstream servers of MCP versions 2026-07-28 and
2025-06-18 over Streamable HTTP, and 2024-11-05 over HTTP+SSE (`transport:
sse`). It reads every page of a server's tool list and lists again when the
server says its tools changed. A server that does not connect is shown
with its last error and retried, from 1 s apart up to every 5 minutes. The
first request after a server is saved waits up to 3 s for it to connect.

To check a server before you rely on it, `POST /agentcc/mcp/probe/` (Owner
or Admin, 10 a minute per organization) connects to it through the gateway
with its stored credential and reports whether it connected, its MCP
version, how many tools it has and a hash of its tool list.

## Add an app from the catalogue

**Providers, Tools & Keys > Integrations** lists the apps from the gateway's
connector catalogue (`agentcc-gateway/catalog/connectors`) that your
organization can add: apps whose vendor runs a remote MCP server that you
connect to with your own key. An Owner or Admin opens an app, enters its
key, and adds it.

- The key is stored in the MCP credential vault and never shown again.
- The app becomes one of your organization's servers, and gets a virtual
  server of its own at `/mcp/v/<vs_id>`.
- The app's tools come from the vendor's live tool list, read with your
  key. A tool the vendor marks read-only (`readOnlyHint: true`) is allowed
  on the app's virtual server; every other tool is denied there.

To add an MCP server that is not in the catalogue, use **Custom MCP
server**, which opens the Servers tab.

On a deployment that runs with MCP off (`mcp.enabled: false`), the page
says "MCP is turned off for this deployment" and offers no app, since every
app connects through the gateway's MCP. An add sent to the API then answers
404 `gateway_mcp_disabled` and stores nothing.

## Credentials

A server's credential is a bearer token, or an API key sent in a header, a
query parameter or a variable in the server's URL. It is stored encrypted
in the MCP credential vault. The organization's config holds only a
reference to it, and the platform adds the secret to the push it sends the
gateway. The secret is never in a config version, never returned by a
read, and never logged; the gateway's own admin reads show it as `****`.

| Call                                         | Who            | What it does                                                      |
| -------------------------------------------- | -------------- | ----------------------------------------------------------------- |
| `GET /agentcc/mcp/credentials/`              | Any member     | Lists the organization's credentials, without values              |
| `POST /agentcc/mcp/credentials/`             | Owner or Admin | Stores a credential                                               |
| `POST /agentcc/mcp/credentials/{id}/rotate/` | Owner or Admin | Replaces its secret                                               |
| `DELETE /agentcc/mcp/credentials/{id}/`      | Owner or Admin | Revokes it; a revoked credential is not sent to the gateway again |

**MCP tools > Credentials** shows the same list.

Three rules keep a secret where it belongs:

- A config write that puts an MCP secret into the config as a value
  (`auth.token`, `auth.key`, `credential.secret`) is refused 400
  `secret_in_config`. Store it through the vault or the Servers tab.
- Moving a server that keeps a stored credential to another scheme, host
  or port is refused 400 `mcp_credential_origin_changed`. Enter the
  credential again, or remove it, with the move. A change of path or query
  keeps it.
- A stored credential is bound to the origin (scheme, host and port) of the
  URL its secret was entered with, and the push and **Test connection** send
  it only to its server there. Activating an older config version that puts
  the server anywhere else, or names a credential bound to no origin, is
  refused 400 `mcp_credential_origin_changed`, and nothing is pushed. A
  credential is bound to none when it is rotated while no config sends it;
  enter it again with its server.

## Virtual servers

A virtual server is a curated set of your organization's tools at its own
address, `/mcp/v/<vs_id>`. Use one to give an agent only the tools its job
needs. Edit them on **MCP tools > Virtual servers**, or in the
organization's config under `mcp.virtual_servers.<id>`:

| Field                            | What it does                                                                                                      |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `members`                        | The servers whose tools it offers                                                                                 |
| `tools.include`, `tools.exclude` | Globs that narrow the tools                                                                                       |
| `overrides`                      | Rename, retitle, redescribe or hide one tool, keyed `<server_id>/<tool>`                                          |
| `naming`                         | `prefixed` (default): `<member>__<tool>`; `flat`: the tool's own name                                             |
| `access`                         | The keys, agents and users that may use it, and rules that narrow what each may list and call; a deny always wins |

A virtual server is served only to its own organization's keys; another
organization's key gets 404. One that names no key, agent or user in
`access` is open to every key of its organization. Exposed names never
repeat, are at most 64 characters, and are listed in a stable order.

## Tool-call records

Every `tools/call` through `/mcp` or a virtual server is recorded once,
whatever its outcome: `success`, `is_error`, `denied` (with its code, such
as `tool_denied` or `unknown_tool`), `timeout` and the rest. A record names
the key, the server, the virtual server, the tool and its upstream name,
the agent context, the outcome and error code, the latency, the sizes and
the billing mode. It holds the call's arguments and result only under
content mode `full`, and never a key, a bearer token or the session id.
Each result the gateway returns carries the record's id in
`_meta["io.futureagi/tool_call"].tool_call_id`.

Read them on **MCP tools > Tool calls**, or through the API:

```bash
curl -H "X-Api-Key: $FI_API_KEY" -H "X-Secret-Key: $FI_SECRET_KEY" \
  "https://api.example.com/agentcc/mcp/tool-calls/?server_id=github&outcome=denied,timeout&from=2026-10-01T00:00:00Z"
```

The list is newest first, 50 to a page (`limit` up to 100), and filters by
`run_id`, `agent_id`, `api_key_id`, `server_id`, `tool` or `outcome` (each
a comma list), `billing_mode`, `from` and `to`.
`GET /agentcc/mcp/tool-calls/{id}/` returns one call.

## Run it on your own gateway

```yaml
mcp:
  enabled: true
  # Required with MCP on: AGENTCC_MCP_STATE_KEYS, or mcp.state_keys
  # (comma-separated, the current key first, each at least 32 bytes).
  static:
    org_id: "" # an organization that also gets the servers below
  servers:
    search:
      transport: "http"
      url: "https://mcp.example.com"
      auth:
        type: "bearer"
        token: "${MCP_SEARCH_TOKEN}"
  egress:
    private_allow:
      - "10.0.0.0/8" # only for upstreams on your own network
```

- **Organizations' servers** come from the platform: with a control plane
  set (`control_plane.url`), the gateway serves each organization the
  servers in the config the platform pushes for it.
- **Upstream addresses.** An upstream on a loopback, private, link-local or
  cloud-metadata address is refused (`egress_refused`) unless
  `mcp.egress.private_allow` lists it. A cloud-metadata address is refused
  even then.
- **Admin routes.** `GET /-/mcp/status`, `/-/mcp/tools`, `/-/mcp/resources`
  and `/-/mcp/prompts` take the organization as `org_id` in the query, and
  `POST /-/mcp/test` in its body; they need the admin token. Without
  `org_id` they answer 400 `missing_org_id`, and for an organization the
  gateway has no config for, 404 `org_not_found`. To read the config file's
  servers this way, set `mcp.static.org_id` and name that organization.
- **Turn it off.** With `mcp.enabled: false`, `/mcp` answers 404, the
  platform's MCP admin API reports `gateway_mcp_disabled`, and the console
  shows "MCP is turned off for this deployment".

## Omega EE agents

Omega EE attaches to the gateway through the contract in
[omega-ee-attach.md](connectors/omega-ee-attach.md): it provisions
virtual servers over your organization's servers and calls them with a
scoped agent key per workspace, which reaches only `/mcp` and the model
endpoints and spends at most its workspace's budget. The platform needs the gateway's public HTTPS origin in
`AGENTCC_GATEWAY_PUBLIC_URL` to hand out those addresses; the Helm chart
sets it from `gatewayApi.llmGateway.host`.

## Independent deployment sub-features

MCP core is independent of three deployment gates. All three default to `false`
in Django, the gateway, Compose, `.env.example` and Helm. They cannot be enabled
by an organization config push. Enable a gate only after that sub-feature's own
acceptance walk passes; a passing core MCP walk does not enable another feature.

| Environment variable (Django and gateway) | Gateway YAML (`mcp`)     | Helm (`mcpFeatures`) | Hidden while OFF                                                                                                                                                             |
| ----------------------------------------- | ------------------------ | -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED`      | `upstream_oauth_enabled` | `upstreamOAuth`      | Per-person and shared upstream OAuth connect, callbacks, client metadata, token retrieval and OAuth app registrations; Your accounts and OAuth registration/sign-in controls |
| `AGENTCC_MCP_CLIENT_OAUTH_ENABLED`        | `client_oauth_enabled`   | `clientOAuth`        | Gateway client OAuth authorize, DCR, token, consent, revoke, JWKS and authorization-server/protected-resource metadata                                                       |
| `AGENTCC_MCP_K2_APPS_ENABLED`             | `k2_apps_enabled`        | `k2Apps`             | Google/Microsoft K2 OAuth apps and Arcade, including their catalogue entries and runtime endpoints                                                                           |

Use the same values in Django and the gateway. Helm injects the shared values into
both; the K2 runtime also receives its gate. A present empty or malformed env value
is OFF. A restart is required. Configuration and stored credentials are retained;
turning a feature OFF is not a credential deletion or a revocation at a vendor.
Disabled feature APIs return `404` with `not_found`, before authentication wherever
the route alone identifies the feature. Discovery and catalogue responses omit it.
The Console reads runtime capabilities from `mcp-status.features`, fails closed when
those capabilities are missing, and never offers a disabled feature.

Client OAuth still needs `mcp.oauth.enabled`, its issuer and public URL configured.
When client OAuth is OFF the gateway advertises no OAuth resource metadata and
accepts no OAuth bearer grant at `/mcp`; MCP clients use personal gateway API keys.
Static-credential apps, MCP servers, virtual servers, tool calls and the credential
vault remain available. A static-credential app has no OAuth control while upstream
OAuth is OFF. K2 OAuth apps need **both** K2 and upstream OAuth enabled, but neither
switch enables the other. Core platform MCP OAuth is a separate service; these
gates apply to the gateway's `/agentcc/mcp-oauth` authorization server.

### Known issues when a gate ships OFF

- **W08:** Upstream OAuth app connections are not available in this deployment;
  use static bearer/API-key apps or custom MCP servers with static credentials.
- **W09:** OAuth sign-in by MCP clients is not available in this deployment;
  authenticate to gateway MCP endpoints with a personal gateway API key.
- **W10:** Google, Microsoft and Arcade K2 OAuth apps are not available in this
  deployment; MCP core and static-credential apps remain available.
