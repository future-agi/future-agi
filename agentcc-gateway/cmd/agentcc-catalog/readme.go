package main

import (
	"fmt"
	"slices"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/emit"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// distReadmePath is the generated guide to dist/.
const distReadmePath = "dist/README.md"

// distReadme describes the generated files, for the bundle they belong to,
// and names each server oss-registry.json and server-json leave out.
func distReadme(b *bundle.Bundle, ossLeftOut []emit.OSSLeftOut, serverJSONLeftOut []emit.ServerJSONLeftOut) []byte {
	return append([]byte(fmt.Sprintf(`# Generated connector catalog files

Written by `+"`go run ./cmd/agentcc-catalog build`"+` (run from agentcc-gateway/) from the records in `+"`apps/`"+` and `+"`platforms/`"+`. Do not edit them: `+"`agentcc-catalog check`"+` fails when any of them differs from what build writes.

This generation is bundle version %s, sha256 `+"`%s`"+`, built from commit `+"`%s`"+`.

| File | Schema | What it is |
| --- | --- | --- |
| `+"`catalog.json`"+` | `+"`%s`"+` | Every app, provider and platform record. Pin it by version and sha256. |
| `+"`emit/gateway-servers.json`"+` | `+"`%s`"+` | For each http provider, the org server entry fields the catalog decides, for adding the server from the catalog. No credential value. |
| `+"`emit/ee-apps.json`"+` | `+"`%s`"+` | Omega EE's IntegrationAppDefinition for every app EE can use. |
| `+"`emit/ee-settings.json`"+` | `+"`%s`"+` | A file for EE's OMEGA_INTEGRATIONS_APPS_FILE: at most %d OAuth apps with a fixed vendor MCP URL. Each still needs its client id and secret in EE's environment. |
| `+"`emit/oss-registry.json`"+` | `+"`%s`"+` | OSS Omega's McpCatalogServer entries. |
| `+"`emit/server-json/<app-id>.json`"+` | MCP Registry server.json, 2025-12-11 | One per rest provider a registry client can sign in to: a server agentcc-connectors serves. None today (see below). |
| `+"`emit/docs/`"+` | Markdown | A card per app and platform, and an index. |

Each JSON file under `+"`emit/`"+`, except the server.json files, names the bundle version and sha256 it was written from.

## The bundle's sha256

The bundle holds each record as its file states it, as JSON, with every object's keys sorted; a rest provider's `+"`tools.generated.json`"+` tools follow its curated tools in `+"`tools.items`"+`. Its `+"`sha256`"+` is the SHA-256, in lowercase hex, of the JSON Canonicalization Scheme (RFC 8785) form of `+"`{\"apps\": [...], \"platforms\": [...], \"providers\": [...]}`"+`. In JavaScript:

`+"```js"+`
const sort = (v) => Array.isArray(v) ? v.map(sort)
  : v && typeof v === "object" ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, sort(v[k])])) : v;
const text = JSON.stringify(sort({ apps: b.apps, providers: b.providers, platforms: b.platforms }));
// sha256(text, utf8) === b.sha256
`+"```"+`

`+"`version`"+` is YYYY.MM.DD.N: unchanged records keep it, and changed ones take the next one for the build date.
`, b.Version, b.SHA256, b.SourceCommit,
		bundle.Schema, emit.GatewaySchema, emit.EEAppsSchema, emit.EESettingsSchema, emit.EEMaxSettingsApps, emit.OSSRegistrySchema)),
		append(ossLeftOutSection(ossLeftOut), serverJSONLeftOutSection(serverJSONLeftOut)...)...)
}

// serverJSONLeftOutSection lists the servers emit/server-json leaves out,
// with every rule that can leave one out.
func serverJSONLeftOutSection(left []emit.ServerJSONLeftOut) []byte {
	var sb strings.Builder
	sb.WriteString("\n## Servers left out of `emit/server-json`\n\n")
	sb.WriteString("`emit/server-json/` is the set OA-DIR would publish to the MCP Registry. It leaves out each rest provider " +
		"an MCP registry client could not sign in to, by the rule named for it. `catalog.json` and the other files still " +
		"carry it, and the build still checks the server.json it would write.\n\n")
	rules := make([]string, 0, len(emit.ServerJSONLeftOutReasons))
	for r := range emit.ServerJSONLeftOutReasons {
		rules = append(rules, r)
	}
	slices.Sort(rules)
	for _, r := range rules {
		fmt.Fprintf(&sb, "- `%s`: %s.\n", r, emit.ServerJSONLeftOutReasons[r])
	}
	if len(left) == 0 {
		sb.WriteString("\nNo server is left out.\n")
		return []byte(sb.String())
	}
	sb.WriteString("\n| Registry name | Provider | Rule |\n| --- | --- | --- |\n")
	for _, s := range left {
		fmt.Fprintf(&sb, "| `%s` | `%s` | `%s` |\n", s.Name, s.Ref, s.Reason)
	}
	return []byte(sb.String())
}

// ossLeftOutSection lists the servers oss-registry.json leaves out, with
// every rule that can leave one out.
func ossLeftOutSection(left []emit.OSSLeftOut) []byte {
	var sb strings.Builder
	sb.WriteString("\n## Servers left out of `emit/oss-registry.json`\n\n")
	sb.WriteString("`emit/oss-registry.json` leaves out each server OSS Omega cannot sign in to, by the rule named for it. " +
		"`catalog.json` and the other files still carry it, and it keeps its OSS id: no other server may take that id. " +
		"Public client is what the record's `x-omega.publicClient` says: yes, no, or not stated.\n\n")
	rules := make([]string, 0, len(manifest.OSSLeftOutReasons))
	for r := range manifest.OSSLeftOutReasons {
		rules = append(rules, r)
	}
	slices.Sort(rules)
	for _, r := range rules {
		fmt.Fprintf(&sb, "- `%s`: %s.\n", r, manifest.OSSLeftOutReasons[r])
	}
	if len(left) == 0 {
		sb.WriteString("\nNo server is left out.\n")
		return []byte(sb.String())
	}
	sb.WriteString("\n| OSS id | Provider | Registration | Public client | Rule |\n| --- | --- | --- | --- | --- |\n")
	for _, s := range left {
		reg := strings.Join(s.Registration, ", ")
		if reg == "" {
			reg = "none declared"
		}
		fmt.Fprintf(&sb, "| `%s` | `%s` | %s | %s | `%s` |\n", s.ID, s.Ref, reg, s.PublicClient, s.Reason)
	}
	return []byte(sb.String())
}
