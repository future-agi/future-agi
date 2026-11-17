package vendors

import "github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"

// arcadeBinding is a customer-supplied MCP OAuth gateway, never Arcade's
// API-key/tools.authorize flavor. CIMD/DCR, consent, refresh and verification
// belong to the existing OAuth/control-plane path, not an Arcade REST API.
// Fixed tools must be explicitly assured before admission; this is not a
// production recommendation-only tools/list detector. Project-member access
// and customer User Sources/verifier restrictions still apply.
func arcadeBinding(b Binding) bool {
	return b.AuthKind == manifest.AuthMCPOAuth && b.CatalogRef == "" && b.ToolMode == "fixed"
}
