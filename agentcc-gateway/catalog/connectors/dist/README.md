# Generated connector catalog files

Written by `go run ./cmd/agentcc-catalog build` (run from agentcc-gateway/) from the records in `apps/` and `platforms/`. Do not edit them: `agentcc-catalog check` fails when any of them differs from what build writes.

This generation is bundle version 2026.10.28.0, sha256 `72dcd1cbc1dac9c8756eb011687158d5e451d765829915f751e548aa5243d136`, built from commit `03c88facad06485616e0d4539c229cee5b5ee472`.

| File | Schema | What it is |
| --- | --- | --- |
| `catalog.json` | `connector-catalog-bundle/v1` | Every app, provider and platform record. Pin it by version and sha256. |
| `emit/gateway-servers.json` | `connector-gateway-servers/v1` | For each http provider, the org server entry fields the catalog decides, for adding the server from the catalog. No credential value. |
| `emit/ee-apps.json` | `connector-ee-apps/v1` | Omega EE's IntegrationAppDefinition for every app EE can use. |
| `emit/ee-settings.json` | `connector-ee-settings/v1` | A file for EE's OMEGA_INTEGRATIONS_APPS_FILE: at most 64 OAuth apps with a fixed vendor MCP URL. Each still needs its client id and secret in EE's environment. |
| `emit/oss-registry.json` | `connector-oss-registry/v1` | OSS Omega's McpCatalogServer entries. |
| `emit/server-json/<app-id>.json` | MCP Registry server.json, 2025-12-11 | One per rest provider a registry client can sign in to: a server agentcc-connectors serves. None today (see below). |
| `emit/docs/` | Markdown | A card per app and platform, and an index. |

Each JSON file under `emit/`, except the server.json files, names the bundle version and sha256 it was written from.

## The bundle's sha256

The bundle holds each record as its file states it, as JSON, with every object's keys sorted; a rest provider's `tools.generated.json` tools follow its curated tools in `tools.items`. Its `sha256` is the SHA-256, in lowercase hex, of the JSON Canonicalization Scheme (RFC 8785) form of `{"apps": [...], "platforms": [...], "providers": [...]}`. In JavaScript:

```js
const sort = (v) => Array.isArray(v) ? v.map(sort)
  : v && typeof v === "object" ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, sort(v[k])])) : v;
const text = JSON.stringify(sort({ apps: b.apps, providers: b.providers, platforms: b.platforms }));
// sha256(text, utf8) === b.sha256
```

`version` is YYYY.MM.DD.N: unchanged records keep it, and changed ones take the next one for the build date.

## Servers left out of `emit/oss-registry.json`

`emit/oss-registry.json` leaves out each server OSS Omega cannot sign in to, by the rule named for it. `catalog.json` and the other files still carry it, and it keeps its OSS id: no other server may take that id. Public client is what the record's `x-omega.publicClient` says: yes, no, or not stated.

- `oauth_client_self_registration_only`: its OAuth authorization server accepts only clients that register themselves (auth.oauth2.registration lists dcr or cimd, not preregistered), while omega mcp login signs in with a client id registered beforehand; it is listed again once OSS Omega can register a client itself.
- `oauth_public_client_not_proven`: its record does not prove a public client for its OAuth sign-in (x-omega.publicClient.supported is not true), while omega mcp login sends a client id and a PKCE verifier and no client secret, so it signs in only where the vendor lets a person register a client without one; it is listed again once the record proves such a client from the vendor's own pages.

| OSS id | Provider | Registration | Public client | Rule |
| --- | --- | --- | --- | --- |
| `asana` | `asana/mcp@1.0.1` | preregistered | no | `oauth_public_client_not_proven` |
| `atlassian` | `atlassian/rovo-mcp@1.0.0` | cimd, dcr | not stated | `oauth_client_self_registration_only` |
| `clickup` | `clickup/mcp@1.0.0` | dcr | not stated | `oauth_client_self_registration_only` |
| `close` | `close/mcp@1.0.1` | preregistered, dcr | no | `oauth_public_client_not_proven` |
| `github-remote` | `github/remote@1.0.1` | preregistered | no | `oauth_public_client_not_proven` |
| `google-bigquery` | `google-bigquery/mcp@1.0.2` | preregistered | no | `oauth_public_client_not_proven` |
| `hubspot` | `hubspot/mcp@1.0.1` | preregistered | no | `oauth_public_client_not_proven` |
| `linear` | `linear/mcp@1.0.0` | cimd, dcr | not stated | `oauth_client_self_registration_only` |
| `neon` | `neon/mcp@1.0.0` | none declared | not stated | `oauth_public_client_not_proven` |
| `netlify` | `netlify/mcp@1.0.0` | dcr | not stated | `oauth_client_self_registration_only` |
| `notion` | `notion/mcp@1.0.0` | cimd, dcr | not stated | `oauth_client_self_registration_only` |
| `prisma` | `prisma/mcp@1.0.0` | dcr | not stated | `oauth_client_self_registration_only` |
| `sentry-remote` | `sentry/mcp@1.0.0` | cimd, dcr | not stated | `oauth_client_self_registration_only` |
| `slack` | `slack/mcp@1.0.1` | preregistered | no | `oauth_public_client_not_proven` |
| `supabase` | `supabase/mcp@1.0.1` | preregistered, dcr | no | `oauth_public_client_not_proven` |
| `thoughtspot` | `thoughtspot/mcp@1.0.1` | none declared | no | `oauth_public_client_not_proven` |
| `trello` | `trello/mcp@1.0.0` | cimd, dcr | not stated | `oauth_client_self_registration_only` |
| `wix` | `wix/mcp@1.0.0` | none declared | not stated | `oauth_public_client_not_proven` |

## Servers left out of `emit/server-json`

`emit/server-json/` is the set OA-DIR would publish to the MCP Registry. It leaves out each rest provider an MCP registry client could not sign in to, by the rule named for it. `catalog.json` and the other files still carry it, and the build still checks the server.json it would write.

- `runtime_serves_no_protected_resource_metadata`: agentcc-connectors serves no OAuth protected-resource metadata (RFC 9728) and its 401 names none, so an MCP registry client cannot learn how to get the vendor token this server needs; it is published once the runtime serves that metadata (and OA-DIR's registration is done).

| Registry name | Provider | Rule |
| --- | --- | --- |
| `com.futureagi/gmail` | `gmail/futureagi-rest@1.0.0` | `runtime_serves_no_protected_resource_metadata` |
| `com.futureagi/google-calendar` | `google-calendar/futureagi-rest@1.0.0` | `runtime_serves_no_protected_resource_metadata` |
| `com.futureagi/google-docs` | `google-docs/futureagi-rest@1.0.0` | `runtime_serves_no_protected_resource_metadata` |
| `com.futureagi/google-drive` | `google-drive/futureagi-rest@1.0.0` | `runtime_serves_no_protected_resource_metadata` |
| `com.futureagi/google-meet` | `google-meet/futureagi-rest@1.0.0` | `runtime_serves_no_protected_resource_metadata` |
| `com.futureagi/google-sheets` | `google-sheets/futureagi-rest@1.0.0` | `runtime_serves_no_protected_resource_metadata` |
| `com.futureagi/google-slides` | `google-slides/futureagi-rest@1.0.0` | `runtime_serves_no_protected_resource_metadata` |
| `com.futureagi/microsoft-excel` | `microsoft-excel/futureagi-rest@1.0.0` | `runtime_serves_no_protected_resource_metadata` |
| `com.futureagi/microsoft-onedrive` | `microsoft-onedrive/futureagi-rest@1.0.0` | `runtime_serves_no_protected_resource_metadata` |
| `com.futureagi/microsoft-outlook` | `microsoft-outlook/futureagi-rest@1.0.0` | `runtime_serves_no_protected_resource_metadata` |
| `com.futureagi/microsoft-sharepoint` | `microsoft-sharepoint/futureagi-rest@1.0.0` | `runtime_serves_no_protected_resource_metadata` |
| `com.futureagi/microsoft-teams` | `microsoft-teams/futureagi-rest@1.0.0` | `runtime_serves_no_protected_resource_metadata` |
