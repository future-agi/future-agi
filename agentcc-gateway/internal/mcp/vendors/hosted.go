package vendors

import (
	"context"
	"errors"
	"net/url"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credentials"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// k2Pilots are FutureAGI's own REST providers the hosted runtime serves to
// each person, by app id, with the routing label of their adapter.
var k2Pilots = map[string]string{
	"gmail":             "google",
	"google-calendar":   "google",
	"microsoft-outlook": "microsoft",
	"microsoft-teams":   "microsoft",
}

const k2Provider = "futureagi-rest"

// OrgConfigs is the authoritative store of the orgs' pushed config
// snapshots (tenant.Store): the only source of a K2 binding.
type OrgConfigs interface {
	Get(orgID string) *tenant.OrgConfig
}

// HostedOptions configure the K2 hosted runtime wiring (G14b).
type HostedOptions struct {
	// Resolver is M25's per-user resolver: it stays the credential path of
	// every server that is not a K2 pilot, and the token source of those that
	// are.
	Resolver *credentials.Resolver
	// Catalog is the operator's loaded connector catalog.
	Catalog *manifest.Catalog
	// Configs is the pushed org config store.
	Configs OrgConfigs
	// RuntimeURL is mcp.vendors.k2_runtime_url: where the gateway reaches the
	// agentcc-connectors runtime.
	RuntimeURL string
}

// Hosted is the K2 hosted runtime's credential step (Deps.CredentialResolver).
// A server added from a K2 pilot provider (catalog_ref <app>/futureagi-rest@v)
// gets a person's token only through the Registry's admission checks, and
// only at <runtime>/<app>/mcp, so a Google or Microsoft token is never sent to
// another host; any other refusal is fail closed and makes no token fetch.
// Every other server goes to the M25 resolver unchanged. Step 13 then binds
// the per-user pool to the credential returned, as for any per_user server.
type Hosted struct {
	base     *credentials.Resolver
	registry *Registry
}

var _ mcp.CredentialResolver = (*Hosted)(nil)
var _ mcp.CredentialPurger = (*Hosted)(nil)

// NewHosted builds the wiring; it registers nothing and makes no request.
func NewHosted(o HostedOptions) (*Hosted, error) {
	runtime, err := runtimeBase(o.RuntimeURL)
	if err != nil {
		return nil, err
	}
	if o.Resolver == nil || o.Catalog == nil || o.Configs == nil {
		return nil, errors.New("mcp vendors: the K2 runtime needs the per-user resolver, the connector catalog and the org config store")
	}
	h := &Hosted{base: o.Resolver}
	h.registry, err = New(Options{Resolver: o.Resolver, Catalog: o.Catalog,
		Bindings: snapshotBindings{configs: o.Configs, runtime: runtime}})
	if err != nil {
		return nil, err
	}
	return h, nil
}

// Resolve is mcp.CredentialResolver.
func (h *Hosted) Resolve(cc *mcp.CallContext) (*mcp.AuthOverride, *mcp.Denial) {
	if platform, ok := k2Platform(cc); ok {
		return h.registry.Resolve(cc, platform)
	}
	return h.base.Resolve(cc)
}

// PurgeCredentials is mcp.CredentialPurger: the local cache, for every server.
func (h *Hosted) PurgeCredentials(ctx context.Context, org, user, server string) (int, error) {
	return h.base.PurgeCredentials(ctx, org, user, server)
}

// k2Platform names the adapter of the call's server when its pushed entry was
// added from a K2 pilot provider, at any version: a version the catalog does
// not hold is then refused by the Registry, never sent down the plain path.
func k2Platform(cc *mcp.CallContext) (string, bool) {
	if cc == nil || cc.OrgConfig == nil || cc.OrgConfig.MCP == nil {
		return "", false
	}
	sc := cc.OrgConfig.MCP.Servers[cc.Upstream.ServerID]
	if sc == nil {
		return "", false
	}
	app, ok := k2App(sc.CatalogRef)
	if !ok {
		return "", false
	}
	return k2Pilots[app], true
}

func k2App(ref string) (string, bool) {
	app, rest, ok := strings.Cut(ref, "/")
	if !ok {
		return "", false
	}
	provider, _, ok := strings.Cut(rest, "@")
	if !ok || provider != k2Provider {
		return "", false
	}
	_, pilot := k2Pilots[app]
	return app, pilot
}

// snapshotBindings derives a K2 server's binding from the org's pushed
// snapshot, never from the request: the server must be at the operator's
// runtime, and a pilot's generated tools are fixed by construction. Whether
// its provider is a pinned native pilot, and Microsoft's delegated-only
// scopes, are the Registry's catalog checks.
type snapshotBindings struct {
	configs OrgConfigs
	runtime string
}

func (s snapshotBindings) Lookup(_ context.Context, org, server string) (Binding, error) {
	cfg := s.configs.Get(org)
	if cfg == nil || cfg.MCP == nil || cfg.MCP.Servers[server] == nil {
		return Binding{}, errors.New("mcp vendors: no pushed server")
	}
	sc := cfg.MCP.Servers[server]
	app, ok := k2App(sc.CatalogRef)
	if !ok || sc.URL != s.runtime+"/"+app+"/mcp" {
		return Binding{}, errors.New("mcp vendors: not a K2 pilot server on this runtime")
	}
	platform := k2Pilots[app]
	return Binding{OrgID: org, ServerID: server, Platform: platform, CatalogRef: sc.CatalogRef, URL: sc.URL,
		AuthKind: manifest.AuthOAuth2, ToolMode: "fixed", Delegated: platform == "microsoft"}, nil
}

func runtimeBase(raw string) (string, error) {
	u, err := url.Parse(raw)
	if err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return "", errors.New("mcp.vendors.k2_runtime_url must be an absolute http(s) URL with no user, query or fragment")
	}
	return strings.TrimRight(raw, "/"), nil
}
