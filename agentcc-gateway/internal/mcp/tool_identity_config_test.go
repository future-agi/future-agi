package mcp

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

// Connection-level sources only, retrieved 2026-10-27:
// https://pipedream.com/docs/connect/mcp/developers.md (app / x-pd-app-slug)
// https://docs.composio.dev/docs/sessions-via-mcp.md (configured toolkits)
// https://docs.composio.dev/reference/sdk-reference/python/mcp.md (toolkits)
// Tool names/shapes come from documentedToolFixtures, not invented metadata.
// The toolkits field records the operator's configured list, not a vendor claim.
func TestServedReadServerConfigurationGrouping(t *testing.T) {
	cases := []struct {
		name, server, tool, platform, slug string
	}{
		{"pipedream-query", `{"url":"https://remote.mcp.pipedream.net/v3?app=google_sheets"}`, "google_sheets-add-single-row", "pipedream", "google_sheets"},
		{"pipedream-all-tools", `{"url":"https://remote.mcp.pipedream.net/v3?app=google_sheets"}`, "resend-retrieve-email", "pipedream", "google_sheets"},
		{"pipedream-upper-tool", `{"url":"https://remote.mcp.pipedream.net/v3?app=google_sheets"}`, "GOOGLE_SHEETS-ADD-SINGLE-ROW", "pipedream", "google_sheets"},
		{"pipedream-header", `{"url":"https://remote.mcp.pipedream.net/v3","auth":{"type":"api_key","header":"X-Pd-App-Slug","key":"linear_app"}}`, "linear_app-create-issue", "other", ""},
		{"pipedream-vault-header", `{"url":"https://remote.mcp.pipedream.net/v3","credential":{"mode":"shared","secret":{"type":"api_key","in":"header","name":"x-pd-app-slug","header":"x-pd-app-slug","value":"google_sheets"}}}`, "GOOGLE_SHEETS-ADD-SINGLE-ROW", "other", ""},
		{"pipedream-header-part", `{"url":"https://remote.mcp.pipedream.net/v3","credential":{"mode":"shared","secret":{"type":"bearer","header":"Authorization","value":"synthetic-private-credential","parts":[{"in":"header","name":"x-pd-app-slug","value":"google_sheets"}]}}}`, "google_sheets-add-single-row", "other", ""},
		{"pipedream-multi", `{"url":"https://remote.mcp.pipedream.net/v3?app=google_sheets,resend"}`, "google_sheets-add-single-row", "other", ""},
		{"pipedream-multi-header", `{"url":"https://remote.mcp.pipedream.net/v3","auth":{"type":"api_key","header":"x-pd-app-slug","key":"google_sheets,resend"}}`, "resend-retrieve-email", "other", ""},
		{"pipedream-missing", `{"url":"https://remote.mcp.pipedream.net/v3"}`, "google_sheets-add-single-row", "other", ""},
		{"pipedream-vault-query", `{"url":"https://remote.mcp.pipedream.net/v3","credential":{"mode":"shared","secret":{"type":"api_key","in":"query","name":"app","value":"google_sheets"}}}`, "google_sheets-add-single-row", "other", ""},
		{"pipedream-query-part-conflict", `{"url":"https://remote.mcp.pipedream.net/v3?app=google_sheets","credential":{"mode":"shared","secret":{"type":"bearer","header":"Authorization","value":"synthetic-private-credential","parts":[{"in":"query","name":"app","value":"resend"}]}}}`, "google_sheets-add-single-row", "other", ""},
		{"pipedream-repeated-app", `{"url":"https://remote.mcp.pipedream.net/v3?app=google_sheets&app=resend"}`, "google_sheets-add-single-row", "other", ""},
		{"pipedream-conflict", `{"url":"https://remote.mcp.pipedream.net/v3?app=google_sheets","auth":{"type":"api_key","header":"x-pd-app-slug","key":"resend"}}`, "google_sheets-add-single-row", "other", ""},
		{"pipedream-invalid-slug", `{"url":"https://remote.mcp.pipedream.net/v3?app=google_sheets%0Asecret"}`, "google_sheets-add-single-row", "other", ""},
		{"pipedream-forwarded-app", `{"url":"https://remote.mcp.pipedream.net/v3?app=google_sheets","forward_headers":["x-pd-app-slug"]}`, "google_sheets-add-single-row", "other", ""},
		{"pipedream-other-secret", `{"url":"https://remote.mcp.pipedream.net/v3","auth":{"type":"api_key","header":"x-api-key","key":"google_sheets"}}`, "google_sheets-add-single-row", "other", ""},
		{"foreign-host", `{"url":"https://fixture.invalid/v3?app=google_sheets","toolkits":["gmail"]}`, "GMAIL_FETCH_EMAILS", "other", ""},
		{"suffix-host", `{"url":"https://remote.mcp.pipedream.net.fixture.invalid/v3?app=google_sheets"}`, "google_sheets-add-single-row", "other", ""},
		{"userinfo-host", `{"url":"https://remote.mcp.pipedream.net@fixture.invalid/v3?app=google_sheets"}`, "google_sheets-add-single-row", "other", ""},
		{"composio-configured", `{"url":"https://backend.composio.dev/v3/mcp/fixture","toolkits":["gmail"]}`, "GMAIL_FETCH_EMAILS", "composio", "gmail"},
		{"composio-longest", `{"url":"https://backend.composio.dev/v3/mcp/fixture","toolkits":["share","share_point"]}`, "SHARE_POINT_SEARCH_QUERY", "composio", "share_point"},
		{"composio-reversed", `{"url":"https://backend.composio.dev/v3/mcp/fixture","toolkits":["share_point","share"]}`, "SHARE_POINT_SEARCH_QUERY", "composio", "share_point"},
		{"composio-search-toolkit", `{"url":"https://backend.composio.dev/v3/mcp/fixture","toolkits":["composio_search"]}`, "COMPOSIO_SEARCH_DUCK_DUCK_GO_SEARCH", "composio", "composio_search"},
		{"composio-meta", `{"url":"https://backend.composio.dev/v3/mcp/fixture","toolkits":["composio_search"]}`, "COMPOSIO_SEARCH_TOOLS", "other", ""},
		{"composio-local", `{"url":"https://backend.composio.dev/v3/mcp/fixture","toolkits":["local_user_management"]}`, "LOCAL_USER_MANAGEMENT_ASSIGN_ROLE", "other", ""},
		{"composio-custom", `{"url":"https://backend.composio.dev/v3/mcp/fixture","toolkits":["custom_acme"]}`, "CUSTOM_ACME_FETCH", "other", ""},
		{"composio-boundary", `{"url":"https://backend.composio.dev/v3/mcp/fixture","toolkits":["share_point"]}`, "SHARE_POINTLESS_FETCH", "other", ""},
		{"composio-no-match", `{"url":"https://backend.composio.dev/v3/mcp/fixture","toolkits":["gmail"]}`, "SHARE_POINT_SEARCH_QUERY", "other", ""},
		{"composio-no-config", `{"url":"https://backend.composio.dev/v3/mcp/fixture"}`, "GMAIL_FETCH_EMAILS", "other", ""},
		{"no-config", `{}`, "GMAIL_FETCH_EMAILS", "other", ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, reader, ten, _ := servedFixture(t)
			ten.snapshots["org-a"] = orgConfig(t, `{"mcp":{"enabled":true,"access":{"default":"allow"},"servers":{"github":`+tc.server+`},"virtual_servers":{"eng":{"id":"vs_eng","members":["github"],"overrides":{"github/`+tc.tool+`":{"name":"display_alias"}}}}}}`)
			ten.pools["org-a"].servers[0].Tools = []Tool{pinTool(t, `{"name":"`+tc.tool+`","description":"Synthetic parameters; documented tool name","inputSchema":{"type":"object"}}`)}
			for _, vs := range []string{"", "vs_eng"} {
				got, err := reader.AdminServedTools(context.Background(), "org-a", vs, "")
				if err != nil || len(got.Tools) != 1 {
					t.Fatalf("served read failed: %v", err)
				}
				row := got.Tools[0]
				if row.UpstreamName != tc.tool || row.ServerID != "github" {
					t.Fatal("display grouping changed the runtime identity")
				}
				raw, _ := json.Marshal(row.Identity)
				var id map[string]any
				_ = json.Unmarshal(raw, &id)
				if id["platform"] != tc.platform || id["toolkit_slug"] != tc.slug || id["toolkit_name"] != tc.slug {
					t.Fatalf("configuration grouping mismatch: got %s; want %s/%s", raw, tc.platform, tc.slug)
				}
				if tc.platform != "other" && id["source"] != "from server configuration" {
					t.Fatal("configuration-derived group is not labelled with its source")
				}
				if tc.platform == "other" && id["source"] != nil {
					t.Fatal("unknown group claimed a configuration source")
				}
				wire, _ := json.Marshal(got)
				if strings.Contains(string(wire), "synthetic-private-credential") || strings.Contains(string(wire), "https://") {
					t.Fatal("configuration or credentials escaped the bounded projection")
				}
			}
		})
	}
}

// Credential values are push-only (MCPCredentialSecret, legacy auth) and are
// never a display hint, wherever the gateway would place them on the wire. A
// credential that names the app selector leaves the connection Other, even
// with a URL app: the effective selector is not known from stored config.
// The markers are slug-shaped so a projection would pass configuredSlug; tool
// names never contain them, so any occurrence in the response is an echo.
func TestServedReadNeverProjectsCredentialValues(t *testing.T) {
	const host = `"url":"https://remote.mcp.pipedream.net/v3`
	cases := []struct {
		name, server, marker, platform, slug string
	}{
		// A bearer goes in Authorization (credtemplate.SecretHeader) whatever its header says.
		{"bearer-app-header", `{` + host + `","credential":{"mode":"shared","secret":{"type":"bearer","header":"x-pd-app-slug","value":"zz_synthetic_bearer_value"}}}`, "zz_synthetic_bearer_value", "other", ""},
		{"bearer-app-header-url-app", `{` + host + `?app=google_sheets","credential":{"mode":"shared","secret":{"type":"bearer","header":"x-pd-app-slug","value":"zz_synthetic_bearer_value"}}}`, "zz_synthetic_bearer_value", "other", ""},
		// An api_key with no header of its own goes in the credential's header.
		{"api-key-reference-header", `{` + host + `","credential":{"mode":"shared","header":"x-pd-app-slug","secret":{"type":"api_key","value":"zz_synthetic_api_key_value"}}}`, "zz_synthetic_api_key_value", "other", ""},
		{"api-key-reference-header-url-app", `{` + host + `?app=google_sheets","credential":{"mode":"shared","header":"x-pd-app-slug","secret":{"type":"api_key","value":"zz_synthetic_api_key_value"}}}`, "zz_synthetic_api_key_value", "other", ""},
		{"legacy-auth-key", `{` + host + `","auth":{"type":"api_key","header":"x-pd-app-slug","key":"zz_synthetic_legacy_value"}}`, "zz_synthetic_legacy_value", "other", ""},
		{"vault-query-name", `{` + host + `","credential":{"mode":"shared","secret":{"type":"api_key","in":"query","name":"app","value":"zz_synthetic_query_value"}}}`, "zz_synthetic_query_value", "other", ""},
		{"vault-header-part", `{` + host + `","credential":{"mode":"shared","secret":{"type":"bearer","header":"Authorization","value":"zz_synthetic_first_value","parts":[{"in":"header","name":"X-PD-App-Slug","value":"zz_synthetic_part_value"}]}}}`, "zz_synthetic_part_value", "other", ""},
		// Control: the URL app is projected; an unrelated credential is not.
		{"url-app-unrelated-secret", `{` + host + `?app=google_sheets","credential":{"mode":"shared","secret":{"type":"api_key","header":"x-api-key","value":"zz_synthetic_unrelated_value"}}}`, "zz_synthetic_unrelated_value", "pipedream", "google_sheets"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, reader, ten, _ := servedFixture(t)
			ten.snapshots["org-a"] = orgConfig(t, `{"mcp":{"enabled":true,"access":{"default":"allow"},"servers":{"github":`+tc.server+`},"virtual_servers":{"eng":{"id":"vs_eng","members":["github"]}}}}`)
			ten.pools["org-a"].servers[0].Tools = []Tool{pinTool(t, `{"name":"google_sheets-add-single-row","description":"Documented tool name","inputSchema":{"type":"object"}}`)}
			for _, vs := range []string{"", "vs_eng"} {
				got, err := reader.AdminServedTools(context.Background(), "org-a", vs, "")
				if err != nil || len(got.Tools) != 1 || got.Tools[0].Identity == nil {
					t.Fatalf("served read failed: %v", err)
				}
				id := got.Tools[0].Identity
				// The got slug is never printed: on failure it may be the marker.
				if id.Platform != tc.platform || id.ToolkitSlug != tc.slug || id.ToolkitName != tc.slug {
					t.Fatalf("vs %q: grouping platform %s, want %s/%q", vs, id.Platform, tc.platform, tc.slug)
				}
				if (id.Source != nil) != (tc.platform != "other") {
					t.Fatalf("vs %q: source label does not match the group", vs)
				}
				wire, _ := json.Marshal(got)
				if strings.Contains(string(wire), tc.marker) {
					t.Fatalf("vs %q: a credential value reached the served-tools projection", vs)
				}
			}
		})
	}
}
