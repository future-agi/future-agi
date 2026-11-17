package vendors

import (
	"errors"
	"net/url"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const k2Provider = "futureagi-rest"

type OrgConfigs interface {
	Get(orgID string) *tenant.OrgConfig
}

// HostedOptions preserve the existing adapter API. Runtime/source/posture
// decisions belong exclusively to ClientAdmission, which tenancy evaluates at
// construction, at every credential lookup and before every send.
type HostedOptions struct {
	Resolver   mcp.CredentialResolver
	Catalog    *manifest.Catalog
	Configs    OrgConfigs
	RuntimeURL string
}

// Hosted asks the pool's admission gate before delegating the complete token
// override, including lifetime/revision fences. It never makes a second K2
// decision and keeps none.
type Hosted struct{ *UnavailableRuntime }

var _ mcp.CredentialResolver = (*Hosted)(nil)
var _ mcp.CredentialPurger = (*Hosted)(nil)
var _ mcp.PushedGrantRevoker = (*Hosted)(nil)

func NewHosted(o HostedOptions) (*Hosted, error) {
	if _, err := runtimeBase(o.RuntimeURL); err != nil {
		return nil, err
	}
	if o.Resolver == nil || o.Catalog == nil || o.Configs == nil {
		return nil, errors.New("mcp vendors: the K2 runtime needs the per-user resolver, the connector catalog and the org config store")
	}
	return &Hosted{UnavailableRuntime: NewUnavailableRuntime(o.Resolver)}, nil
}

// Recognition is broader than admission, so stale/incomplete references cannot
// fall through to ordinary credential resolution in a foreign pool.
func k2Reference(cc *mcp.CallContext) (string, bool) {
	if cc == nil || cc.OrgConfig == nil || cc.OrgConfig.MCP == nil {
		return "", false
	}
	sc := cc.OrgConfig.MCP.Servers[cc.Upstream.ServerID]
	if sc == nil {
		return "", false
	}
	_, ok := k2App(sc.CatalogRef)
	return sc.CatalogRef, ok
}
func k2App(ref string) (string, bool) {
	app, rest, ok := strings.Cut(ref, "/")
	if !ok {
		return "", false
	}
	provider, _, _ := strings.Cut(rest, "@")
	return app, provider == k2Provider
}
func runtimeBase(raw string) (string, error) {
	u, err := url.Parse(raw)
	if err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return "", errors.New("mcp.vendors.k2_runtime_url must be an absolute http(s) URL with no user, query or fragment")
	}
	return strings.TrimRight(raw, "/"), nil
}
