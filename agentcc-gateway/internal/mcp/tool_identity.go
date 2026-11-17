package mcp

import (
	"net/url"
	"strings"

	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const configurationIdentitySource = "from server configuration"

// toolIdentity is a display hint, never a routing or authorization identity.
// Neither vendor documents per-tool identity in tools/list. Only the selected
// org's stored connection configuration is consulted; no metadata or vendor
// calls. The original name (before virtual-server aliases) is used for Composio.
// Sources, retrieved 2026-10-27:
// https://pipedream.com/docs/connect/mcp/developers.md
// https://docs.composio.dev/docs/sessions-via-mcp.md
// https://docs.composio.dev/reference/sdk-reference/python/mcp.md
func toolIdentity(tool Tool, cfg *tenant.MCPServerOrgConfig) *gatewayadmin.MCPToolIdentity {
	other := &gatewayadmin.MCPToolIdentity{Platform: "other"}
	if cfg == nil {
		return other
	}
	u, err := url.Parse(cfg.URL)
	if err != nil || u.Scheme != "https" || u.User != nil || (u.Port() != "" && u.Port() != "443") {
		return other
	}
	var platform, slug string
	switch strings.ToLower(u.Hostname()) {
	case "remote.mcp.pipedream.net":
		platform, slug = "pipedream", pipedreamConfiguredApp(cfg, u)
	case "backend.composio.dev", "connect.composio.dev", "mcp.composio.dev":
		platform, slug = "composio", composioConfiguredToolkit(tool.Name, cfg.Toolkits)
	}
	if slug == "" {
		return other
	}
	source := configurationIdentitySource
	return &gatewayadmin.MCPToolIdentity{
		Platform: platform, ToolkitSlug: slug, ToolkitName: slug, Source: &source,
	}
}

// A bounded slug is the entire display label. Never project credentials, URLs,
// descriptions, labels or arbitrary upstream fields into the response.
func configuredSlug(value string) string {
	if len(value) == 0 || len(value) > 64 {
		return ""
	}
	for i, c := range value {
		if (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || (i > 0 && (c == '_' || c == '-')) {
			continue
		}
		return ""
	}
	return value
}

// Pipedream identifies an app per connection, not per tool. The app comes
// only from the stored URL's single app query parameter. Credential values
// (legacy auth, the pushed secret and its parts) are push-only and never
// read here, so none can reach a display projection. A multi-app, malformed
// or repeated selector stays Other, and so does a connection whose selector
// may also come from elsewhere: then the effective app is not known from
// stored configuration.
func pipedreamConfiguredApp(cfg *tenant.MCPServerOrgConfig, u *url.URL) string {
	query, err := url.ParseQuery(u.RawQuery)
	if err != nil || len(query["app"]) != 1 || pipedreamSelectorElsewhere(cfg) {
		return ""
	}
	return configuredSlug(query["app"][0])
}

// pipedreamSelectorElsewhere reports whether any stored name could place an
// app selector (the x-pd-app-slug header or the app query parameter) besides
// the URL: a forwarded header, the end-user identity binding, or any
// credential placement. It reads names only, never values, and deliberately
// over-approximates the gateway's placement rules (credtemplate): a false
// Other is acceptable for a display hint; a missed conflict is not.
func pipedreamSelectorElsewhere(cfg *tenant.MCPServerOrgConfig) bool {
	names := func(values ...string) bool {
		for _, v := range values {
			v = strings.TrimSpace(v)
			if strings.EqualFold(v, "x-pd-app-slug") || strings.EqualFold(v, "app") {
				return true
			}
		}
		return false
	}
	if names(cfg.ForwardHeaders...) {
		return true
	}
	if auth := cfg.Auth; auth != nil && names(auth.Header) {
		return true
	}
	credential := cfg.Credential
	if credential == nil {
		return false
	}
	if names(credential.Header) {
		return true
	}
	if binding := credential.IdentityBinding; binding != nil && names(binding.Name) {
		return true
	}
	if secret := credential.Secret; secret != nil {
		if names(secret.Header, secret.Name) {
			return true
		}
		for _, part := range secret.Parts {
			if names(part.Name) {
				return true
			}
		}
	}
	return false
}

// The glossary's TOOLKIT_ACTION convention is a hint only. Match the longest
// configured slug plus '_' (share_point, not share); never split at the first
// underscore. Meta tools are not toolkit tools: COMPOSIO_SEARCH_TOOLS overlaps
// the real composio_search toolkit's COMPOSIO_SEARCH_DUCK_DUCK_GO_SEARCH.
func composioConfiguredToolkit(name string, toolkits []string) string {
	if len(toolkits) > 256 {
		return ""
	}
	name = strings.ToUpper(name)
	if strings.HasPrefix(name, "LOCAL_") || strings.HasPrefix(name, "CUSTOM_") {
		return ""
	}
	switch name {
	case "COMPOSIO_SEARCH_TOOLS", "COMPOSIO_GET_TOOL_SCHEMAS", "COMPOSIO_MULTI_EXECUTE_TOOL",
		"COMPOSIO_MANAGE_CONNECTIONS", "COMPOSIO_WAIT_FOR_CONNECTIONS", "COMPOSIO_REMOTE_WORKBENCH", "COMPOSIO_REMOTE_BASH_TOOL":
		return ""
	}
	var longest string
	for _, configured := range toolkits {
		slug := configuredSlug(configured)
		prefix := strings.ToUpper(slug) + "_"
		if slug != "" && len(slug) > len(longest) && len(name) > len(prefix) && strings.HasPrefix(name, prefix) {
			longest = slug
		}
	}
	return longest
}
