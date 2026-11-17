# Connector generators

## REST to tools (`rest/`)

Turns a vendor's REST API description into the tools of one of our own MCP connectors (a K2, `kind: rest` provider in `agentcc-gateway/catalog/connectors`). It reads the provider's pinned spec and a curated overlay, `tools.yaml`, and writes `tools.generated.json` (`connector-manifest/v1#tools-generated`), which the catalog bundles and the `agentcc-connectors` runtime serves.

It reads four formats:

| `upstream.spec.format` | Example                                    | Operations are named by       |
| ---------------------- | ------------------------------------------ | ----------------------------- |
| `openapi-3`            | Microsoft Graph v1.0 (OpenAPI 3.0 and 3.1) | `operationId`, or method+path |
| `swagger-2`            | Slack Web API                              | `operationId`, or method+path |
| `postman-2.1`          | Adyen Transfers collection                 | the request's `name`          |
| `google-discovery`     | Gmail, Google Calendar                     | the method `id`               |

### Run it

From the repository root, with Python 3.12 or later and PyYAML:

```
python3 scripts/connector-gen/rest/generate.py [--check] [--app <app-id>]... [--catalog <dir>]
```

- Without `--check` it writes every rest provider's `tools.generated.json`, or the named apps'.
- With `--check` it writes nothing and compares each output, as data, with what its overlay and spec generate now. A JSON formatter's layout is not drift; a changed value is.
- Exit codes: **0** every output written or up to date; **1** `--check` found a stale or missing output; **2** an overlay, provider record or spec has a problem. Every problem is printed, each naming its file and where in it, and a provider with a problem gets no output written.

It never touches the network: it reads the spec stored beside the overlay.

CI: `ci/connector-gen.yml` is the job that runs the tests and `generate.py --check` (Python 3.12 and Go; with `CONNECTOR_GEN_REQUIRE_GO` set, the Go cross-check fails instead of skipping when Go is missing). GitHub runs it from `.github/workflows/connector-gen.yml`, a byte-identical copy (`rest/tests/test_ci_job.py` fails when the two differ, so edit both). On the repository catalog `--check` compares the four rest providers' committed outputs (gmail and google-calendar from CN14c, microsoft-outlook and microsoft-teams from CN15c) and prints one `ok` line for each.

The tests: `python3 -m pytest scripts/connector-gen/rest/tests -q` (pytest and PyYAML; Go for the catalog cross-check). The test specs are pinned in `rest/tests/fixtures/specs/SOURCES.json`, and `rest/tests/fixtures/catalog` holds six example apps, one or more per format, with their reviewed outputs. After an intended change, regenerate those with `python3 scripts/connector-gen/rest/tests/support.py --write-goldens` and review the diff.

### Adding a rest provider

All under `agentcc-gateway/catalog/connectors/apps/<app-id>/`:

1. `app.yaml`. Its `vendor.domains` must cover the API host: the generator refuses a server outside them, because the runtime sends each user's token there.
2. `providers/<provider-id>.yaml` with `kind: rest`, `tools: {source: operations, defaultPosture: ..., generated: tools.generated.json}` and the pin:

   ```yaml
   upstream:
     spec:
       url: https://raw.githubusercontent.com/<org>/<repo>/<commit>/<path> # serves the same bytes every time
       format: google-discovery
       sha256: <sha256 of the bytes that URL serves>
       licence: <the spec's licence>
   ```

3. The spec at `providers/<provider-id>/fixtures/spec/<file>`, stored with `gzip -9n` (`<file>` ends in `.gz`). The pin is the sha256 of the decompressed bytes, so the commit hook, which formats JSON and YAML, cannot change it. The catalog loader accepts only `fixtures/**` and `evals/**` beside `tools.yaml`, so the spec lives under `fixtures/`. At most 16 MB stored and 64 MB decompressed; Microsoft Graph's full v1.0 description is 44 MB, or 2.1 MB stored.
4. `providers/<provider-id>/tools.yaml`, the overlay below.
5. `python3 scripts/connector-gen/rest/generate.py --app <app-id>`, then `cd agentcc-gateway && go run ./cmd/agentcc-catalog check --sources-only`.

**Pin a URL that serves fixed bytes.** Some vendor endpoints serve the same document in a different byte order on every fetch. Google's live Discovery service did on 2026-10-21: three fetches, three sha256 values, the parsed documents equal. Pin a commit of a published mirror instead (for Google, `googleapis/google-api-go-client`; for Microsoft Graph, `microsoftgraph/msgraph-metadata`), so the live smoke's spec check (`scripts/connector-smoke`) can re-verify it.

### `tools.yaml`

The overlay is read strictly: an unknown key, a null or a wrong type is a problem.

```yaml
schema: connector-gen/rest-overlay/v1
spec: fixtures/spec/gmail-v1.discovery.json.gz
server: https://gmail.googleapis.com # optional: one of the spec's servers
serverSource: https://... # only when the spec declares no https server
credentialParams: [token] # optional: parameters that carry the credential
tools: # 8 to 25 entries
  - name: search_threads
    title: Search threads
    description: >-
      What it does, its input formats and an example.
    operation: { id: gmail.users.threads.list }
    params:
      fixed: { userId: me }
      exclude: [includeSpamTrash]
      describe: { q: "Gmail search query, for example `from:alice is:unread`." }
      required: [q]
      rename: { $top: top }
    body:
      fields: [message.raw, message.threadId]
      required: [message.raw]
      description: The draft to create.
    annotations:
      {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      }
    posture: auto
    scopes: [https://www.googleapis.com/auth/gmail.readonly]
    scopeSource: https://...
    response: { unwrap: threads, fields: [id, snippet], maxBytes: 25000 }
```

**Top level**

- `schema` (required): `connector-gen/rest-overlay/v1`.
- `spec` (required): the stored spec, a path under `fixtures/` in the provider directory.
- `server`: the API server, which must be one the spec declares. The default is the spec's first `https` server (OpenAPI `servers` with variable defaults filled in, Swagger `host` and `basePath`, Discovery `rootUrl` plus `servicePath`, Postman's request host after collection variables are filled in). A spec that declares none needs `server` plus `serverSource`, the vendor page naming it.
- `credentialParams`: parameter names that carry the credential, such as Slack's `token`. The runtime sends the credential itself, so these are never path or query inputs (a Swagger form field of that name is not yet removed from a JSON body; see Known gaps). API-key parameters the spec's own security schemes name (OpenAPI and Swagger `apiKey`, Postman `apikey` auth) are added automatically.

**Each tool**

- `name` (required): matches `^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$`, and `<app-id>__<name>` is at most 64 characters.
- `description` (required): what the tool does, with formats and an example. The spec's summary is never used, because some vendors' are wrong (Microsoft Graph's `GET /me/messages` says "Get open extension"). Optional `title`: 1-100 characters.
- `operation` (required): exactly one operation in the spec, named by `id` (Discovery), `operationId` (OpenAPI, Swagger), `name` (Postman), or `method` and `path` as the spec writes them. Several keys must all match. An operation the spec does not have, or a matcher that fits more than one, is refused.
- `params`:
  - `fixed`: give a path or query parameter a constant. A path value is written into the path (`userId: me` gives `/gmail/v1/users/me/threads`); a query value becomes a `const` input the runtime always sends.
  - `exclude`: leave out optional parameters. A required one cannot be excluded.
  - `describe`: a description where the spec has none or a poor one. An input with no description anywhere is refused.
  - `required`: make an optional parameter required.
  - `rename`: the input name a parameter gets. A name outside `^[A-Za-z0-9_.-]{1,64}$`, such as Microsoft Graph's `$top`, is refused until it is renamed, because not every model provider accepts it in a tool schema. A renamed path parameter renames its `{placeholder}`; a renamed query parameter keeps its name on the wire in `x-wireName`.
  - Header and cookie parameters cannot be inputs; exclude them (an optional `If-Match`) or pick another operation.
- `body`: the JSON request body, as the input named `body`.
  - `fields`: keep only these fields, as dotted paths into the body (`Message.subject`). Every object on the way keeps only the listed children and refuses others. A field the body requires must be kept.
  - `required`: make fields required, as dotted paths.
  - `description`: required when the spec gives the body none.
  - A body that is not JSON (an upload, `application/octet-stream`, a form without JSON) is refused. Swagger form fields become a JSON body when the operation accepts JSON. Discovery and Postman bodies count as required. Postman bodies carry no types, so the schema is inferred from the saved example body. OData annotations such as `@odata.type` and read-only properties are dropped from inputs.
- `annotations`: the reviewer's MCP hints. The generator derives what HTTP decides and refuses contradictions:
  - a `GET` is `readOnlyHint: true`; a `POST` writes unless the reviewer says `readOnlyHint: true` (a `POST` that only reads, such as free/busy); a `PUT`, `PATCH` or `DELETE` always writes, and `readOnlyHint: true` on one is refused;
  - a write must state `destructiveHint`; a `DELETE` is destructive (`destructiveHint: false` on one is refused); a read-only tool is not;
  - `idempotentHint` defaults to true for a read, `PUT` and `DELETE`, else false;
  - `openWorldHint` is always stated by the reviewer.
- `posture`: `auto`, `ask` or `deny`, overriding the provider's `defaultPosture`. A destructive tool must be `deny`; a write is never `auto`.
- `scopes`: for an `oauth2` provider, the minimum scopes the tool needs (`[]` when none); for any other provider, none. Each must be in `auth.oauth2.scopes`. Where the spec lists the scopes an operation accepts (Discovery, Swagger, OpenAPI security requirements), a scope outside that list needs `scopeSource`, the vendor page that says so; where the spec lists none (Microsoft Graph), every scope needs `scopeSource`. The generator does not yet refuse `scopes: []` where the spec lists the scopes an operation accepts (see Known gaps), so the reviewer checks that.
- `response`: shaping the runtime applies to a successful result: `unwrap`, a dotted path to the part to return; `fields`, up to 64 dotted paths to keep (arrays are stepped through); `maxBytes`, 1 to 1,048,576. Paths are checked against the spec's success response. A dotted path cannot name `@odata.nextLink`, so a Microsoft Graph list tool keeps pagination by not projecting `fields` and lets the caller use `select` instead.
- `generic` and `genericArguments`: mark a tool whose real operation is one of its arguments (CN1's AM-C12), naming those inputs.

**Checks across the provider**, as the catalog's L1 runs them: the scopes the tools need, together, equal `auth.oauth2.scopes` apart from `openid`, `offline_access`, `profile` and `email`; no tool name repeats `tools.items`; at most 128 tools; `auth.verify.tool` is one of them; every input schema is plain JSON Schema 2020-12; and `tools/list` stays within 20,000 estimated tokens.

### One HTTP request per tool

A generated tool makes exactly one HTTP request, because the catalog's frozen `operation` is one `{method, path}`. The overlay consolidates within that: one tool per task instead of one per endpoint, constants for parameters a model should not choose (`userId: me`), bodies projected to the fields a task needs, results projected to what a model should read, and readable input names.

A flow that needs two requests, such as replying in a Gmail thread after reading the parent's `Message-ID`, or posting to a Slack username after looking up its channel, is not overlay data, and this generator builds none: there is no overlay key for a second request. Such a flow goes to a small code module in CN10's runtime, behind the same manifest (scaling.md §4.2, item 5; IR-CN4-3). That module is not built: the runtime on gw/CN10 serves one request per tool. Until it is, such a flow is two tools the model calls in turn, or it is left out.

### Request contract

What a generated tool means, for the runtime that executes it (`agentcc-connectors`). CN10's runtime (`agentcc-gateway/internal/connectorcatalog/restmcp`) is built to it. It re-checks the rules marked _at load_ when it loads a bundle: a catalog with any tool it refuses is not served at all, and it writes one log record per refused tool (naming the app, the provider and the tool), then exits.

- **URL.** The request goes to `inputSchema["x-origin"]` (the server's scheme and authority, for example `https://graph.microsoft.com`) followed by `operation.path`, which already includes the server's base path (`/v1.0/me/messages`). The origin must be under the app's `vendor.domains`; the next section says where that is checked. _At load_, `operation.path` starts with "/" (without it the path would name the host).
- **Path inputs.** Each `{name}` in `operation.path` is the input of that name, percent-encoded as one path segment. An empty value, `.` or `..` is refused: percent-encoding leaves the dots as they are, and a URL resolver reads them as this or the parent path, so the call would reach another resource.
- **The `body` input** is the request body, sent as `application/json`.
- **Every other input is a query parameter.** Its name on the wire is its `x-wireName` when present, else the input's name. An array is sent as one parameter per element (`labelIds=INBOX&labelIds=UNREAD`); a vendor array serialized as one delimited value is already a string input, whose description names the delimiter (see Known gaps for two OpenAPI styles). A `const` input is always sent with its value, whether or not the caller gave it. _At load_ and at generation, no query input may go out (under its name or its `x-wireName`) under the name of a credential part or constant the provider places in the query, compared without case: the vendor would get a model-chosen value beside the user's key under the key's name. Leave such a parameter out, or list it in `credentialParams` when it is the credential.
- **The credential** is never an input: the runtime sends the user's token (or, for an API-key provider, the parts `auth.credentialParts` places), and only to the origin above.
- **The result** is shaped by `response`: first `unwrap`, then `fields` (keep only the listed members, through arrays), then the `maxBytes` cap.
- **Annotations for the runtime.** `x-origin` and `x-wireName` are JSON Schema annotations from the generator to the runtime. The runtime removes every `x-` keyword before it lists the tools: annotation keywords only, so values (`const`, `enum`, `default` and `examples`) are listed as they are, even where they hold an `x-` member.
- **Tool annotations.** _At load_, as at generation, `readOnlyHint` is never true on a PUT, PATCH or DELETE (each writes) and `destructiveHint` is never false on a DELETE (it always destroys); a POST may be read-only when a reviewer states it.

### Where the origin rule is enforced

The runtime sends each user's vendor token to a tool's `x-origin`, so that origin must be one of the app's `vendor.domains` or under one. Three checks hold the rule (orchestrator ruling on the CN4 review, 2026-10-21); the repository catalog's rest providers (gmail, google-calendar, microsoft-outlook, microsoft-teams) pass all three.

- **At generation.** `generate.py` refuses a server whose host is not under the app's `vendor.domains` (`restgen/catalog.py`, `_vendor_host`) and writes nothing for that provider. `x-origin` is then that server's scheme and authority (`restgen/model.py`, `origin`).
- **In CI.** `generate.py --check` compares every committed `tools.generated.json` with what its `tools.yaml` and pinned spec generate, as data. A hand-edited `x-origin` is drift (exit 1), and an output with no `tools.yaml` beside it is a problem (exit 2). `agentcc-catalog check` does not look at `x-origin`, so this is the check that catches a hand edit. The job is `ci/connector-gen.yml`, which GitHub runs from its byte-identical copy `.github/workflows/connector-gen.yml` (`rest/tests/test_ci_job.py` holds the two equal).
- **At load (after CN10b).** The K2 runtime, `agentcc-connectors`, re-checks every rest tool's `x-origin` against the app's `vendor.domains` when it loads the bundle. It also refuses an origin that is not https, carries user info or a path, or names an IP address, and it describes a refused origin without echoing it. A catalog with any refused tool is not served at all: the runtime logs one record per refused tool, naming the app, the provider and the tool, and exits 1 before it listens (CN10 and CN10b, merged into this line in the CW2 integration).

### Known gaps

Review findings (CN4 review, P3) that are not fixed yet. The sentences above that they touch say so.

- **Credential form fields.** A Swagger form field named in `credentialParams` or by an `apiKey` scheme, in an operation that accepts JSON, stays a field of the `body` input (`restgen/swagger2.py`, `_form_body`). No example hits it: Slack's form operations accept only form encoding, so they are refused.
- **`scopes: []`.** It is accepted on an operation whose spec lists the scopes it accepts (`restgen/annotate.py`, `check_scopes`), so a tool's minimum scopes can be under-declared while the scope union still passes.
- **Server user info.** A declared server with user info (`https://user:pass@host`) keeps it in `x-origin` (`restgen/model.py`, `origin`), and declared servers are not held to the overlay's strict https pattern. The runtime on gw/CN10 refuses such an origin at load.
- **Postman request host.** A Postman request whose own host keeps an unfilled `{{variable}}`, or is not https, is generated against the provider's server instead of being refused (`restgen/postman.py`, `operation`).
- **Delimited arrays.** An OpenAPI 3 query array with `explode: false` and `style: spaceDelimited` or `pipeDelimited` is described as "Comma-separated values." (`restgen/openapi3.py`).
