package main

import (
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credentials"
)

// Refuse K2 before the credential resolver can read a cached token. This is
// independent of upstream OAuth: ordinary per-user MCP servers keep working.
func k2CallHidden(cc *mcp.CallContext) bool {
	if cc == nil || cc.OrgConfig == nil || cc.OrgConfig.MCP == nil {
		return false
	}
	server := cc.OrgConfig.MCP.Servers[cc.Upstream.ServerID]
	if server == nil {
		return false
	}
	app, provider, _ := strings.Cut(server.CatalogRef, "/")
	providerID, _, _ := strings.Cut(provider, "@")
	if providerID == "futureagi-rest" {
		return true // all catalog K2 providers, including unavailable/unknown ones
	}
	// Preserve the existing legacy visibility guard for non-REST entries;
	// this list only hides entries and is never an admission authority.
	switch app {
	case "gmail", "google-calendar", "google-bigquery", "microsoft-outlook", "microsoft-teams", "arcade":
		return true
	}
	return strings.HasPrefix(provider, "arcade@")
}

type k2DisabledResolver struct{ *credentials.Resolver }

func (r k2DisabledResolver) Resolve(cc *mcp.CallContext) (*mcp.AuthOverride, *mcp.Denial) {
	if k2CallHidden(cc) {
		return nil, &mcp.Denial{Code: "not_found", Type: "not_found", Message: "Not found.", HTTPStatus: 404}
	}
	return r.Resolver.Resolve(cc)
}
