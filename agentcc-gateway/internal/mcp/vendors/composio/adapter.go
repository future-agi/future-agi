// Package composio prepares minted session bindings. The runtime adapter stays
// closed until W4 composition adds a guarded, fingerprinted Endpoint override.
package composio

import (
	"net/http"
	"net/url"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/vendors/grantclient"
)

const PlatformOrigin = "https://backend.composio.dev"

type Binding struct {
	Endpoint string
	Header   http.Header
}
type Adapter struct{ Source grantclient.Source }

func Prepare(grant grantclient.Grant, configuredURL string) (Binding, error) {
	u, err := url.Parse(grant.Endpoint)
	configured, configErr := url.Parse(configuredURL)
	if err != nil || configErr != nil || grant.Vendor != "composio" || grant.APIKey == "" || strings.ContainsAny(grant.APIKey, "\r\n\x00") || grant.CredentialVersion < 1 || !platformURL(u) || !platformURL(configured) || u.Scheme != configured.Scheme || u.Host != configured.Host || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || u.Path == "" || u.Path == "/" {
		return Binding{}, grantclient.ErrUnavailable
	}
	return Binding{Endpoint: u.String(), Header: http.Header{"X-Api-Key": {grant.APIKey}}}, nil
}
func platformURL(u *url.URL) bool {
	return u != nil && u.Scheme == "https" && u.Host == "backend.composio.dev" && u.User == nil && u.Fragment == "" && u.RawQuery == ""
}

func (a *Adapter) PrepareCall(cc *mcp.CallContext) (Binding, *mcp.Denial) {
	identity, ok := mcp.EndUserOf(cc)
	if !ok || a.Source == nil || cc.OrgConfig == nil || cc.OrgConfig.MCP == nil {
		return Binding{}, mcp.CredentialUnavailable()
	}
	sc := cc.OrgConfig.MCP.Servers[cc.Upstream.ServerID]
	if sc == nil {
		return Binding{}, mcp.CredentialUnavailable()
	}
	grant, err := a.Source.Fetch(cc.Context(), grantclient.Request{OrgID: cc.Principal.OrgID, ServerID: cc.Upstream.ServerID, Vendor: "composio", EndUser: grantclient.EndUser{Source: identity.Source, ID: identity.Subject}})
	if err != nil {
		return Binding{}, mcp.CredentialUnavailable()
	}
	binding, err := Prepare(grant, sc.URL)
	if err != nil {
		return Binding{}, mcp.CredentialUnavailable()
	}
	return binding, nil
}

// Resolve never discards a minted endpoint and accidentally uses the configured
// shared URL. Stage 2 replaces this refusal with the accepted endpoint guard.
func (*Adapter) Resolve(*mcp.CallContext, string) (*mcp.AuthOverride, *mcp.Denial) {
	return nil, mcp.CredentialUnavailable()
}
