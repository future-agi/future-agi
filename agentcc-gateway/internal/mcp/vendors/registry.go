// Package vendors provides explicitly constructed per-user token adapters.
// It installs nothing: the shipped VendorCredentials hook is still a no-op.
// A later integrator must wire dispatch and supply authoritative bindings.
package vendors

import (
	"context"
	"errors"
	"net/url"
	"slices"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credentials"
)

// Binding is a control-plane admission decision for one org's server. It is
// NOT a request argument, end-user override, OAuth credential, or shipped
// configuration schema. URL and CatalogRef must equal the accepted org
// snapshot; native providers must also exist in the supplied loaded catalog.
// Arcade has no catalog ref: the customer explicitly supplies its gateway.
// ToolMode must be "fixed", attested by the admitting control plane. This
// package does not implement a tools/list recommendation-mode detector.
type Binding struct {
	OrgID, ServerID, Platform string
	CatalogRef, URL, AuthKind string
	ToolMode                  string
	// Delegated must be explicitly assured for Microsoft; app-only credentials
	// are not a substitute for a person's delegated Graph connection.
	Delegated bool
}

// BindingSource reads authoritative org/server admission records. Implementors
// must not derive records from headers, query strings, tool arguments, or a
// key's creator. Resolve looks up only the authenticated org and routed server.
// Changes must be safe for concurrent readers and accompany the org snapshot.
type BindingSource interface {
	Lookup(ctx context.Context, orgID, serverID string) (Binding, error)
}

// BindingFunc adapts a control-plane lookup without a global registry.
type BindingFunc func(context.Context, string, string) (Binding, error)

func (f BindingFunc) Lookup(ctx context.Context, org, server string) (Binding, error) {
	if f == nil {
		return Binding{}, errors.New("mcp vendors: no binding lookup")
	}
	return f(ctx, org, server)
}

// Options reuse the vendor resolver, sealed cache, token channel and connect links.
// Catalog must be the operator's validated catalog (embedded bundle by
// default), not a caller-supplied manifest. New snapshots exact provider
// references whose clientSources explicitly admit the org client path.
type Options struct {
	Resolver *credentials.Resolver
	Bindings BindingSource
	Catalog  *manifest.Catalog
}

// Registry implements the frozen vendor seam and local purge contract. It
// never fetches vendor metadata, registers OAuth clients, or revokes remotely.
type Registry struct {
	resolver *credentials.Resolver
	bindings BindingSource
	native   map[string]string // immutable catalog reference -> routing label
}

var _ mcp.VendorCredentials = (*Registry)(nil)
var _ mcp.CredentialPurger = (*Registry)(nil)

// New constructs an inert, injectable component. No globals, auth defaults,
// config, or server dependency are changed, and no network request is made.
func New(o Options) (*Registry, error) {
	if o.Resolver == nil || o.Bindings == nil || o.Catalog == nil {
		return nil, errors.New("mcp vendors: resolver, binding source and catalog are required")
	}
	r := &Registry{resolver: o.Resolver, bindings: o.Bindings, native: map[string]string{}}
	for _, a := range o.Catalog.Apps {
		if a == nil {
			continue
		}
		p := a.Provider("futureagi-rest")
		if p == nil || p.App != a.ID {
			continue
		}
		var label string
		switch {
		case googleProvider(p):
			label = "google"
		case microsoftProvider(p):
			label = "microsoft"
		default:
			continue
		}
		r.native[p.App+"/"+p.ID+"@"+p.Version] = label
	}
	return r, nil
}

// Resolve validates admission and individual identity before delegating to
// the vendor resolver. Google/Microsoft are package routing labels, NOT vendor_end_user enum
// additions. All three adapters require the existing per_user posture.
func (r *Registry) Resolve(cc *mcp.CallContext, platform string) (*mcp.AuthOverride, *mcp.Denial) {
	if r == nil || cc == nil || r.resolver == nil || r.bindings == nil {
		return nil, mcp.CredentialUnavailable()
	}
	switch platform {
	case "google", "microsoft", "arcade":
	default:
		return nil, mcp.CredentialUnavailable()
	}
	pool := mcp.TenantPoolOf(cc)
	if pool == nil {
		return nil, mcp.CredentialUnavailable()
	}
	caller, ok := credentials.CallerOf(cc.Principal)
	if !ok || caller.OrgID != pool.OrgID() {
		return nil, mcp.AuthRequired(cc.Upstream.ServerID, mcp.AuthReasonNoUserIdentity, "")
	}
	ps, ok := pool.Server(cc.Upstream.ServerID)
	if !ok || ps.ID != cc.Upstream.ServerID || !ps.PerUser || ps.SharedOAuth || ps.Managed || ps.Builtin || cc.Upstream.Managed {
		return nil, mcp.CredentialUnavailable()
	}
	if cc.OrgConfig == nil || cc.OrgConfig.MCP == nil {
		return nil, mcp.CredentialUnavailable()
	}
	sc := cc.OrgConfig.MCP.Servers[ps.ID]
	if sc == nil || sc.Credential == nil || sc.Credential.Mode != "per_user" || sc.Credential.Secret != nil || sc.Auth != nil {
		return nil, mcp.CredentialUnavailable()
	}
	b, err := r.bindings.Lookup(cc.Context(), caller.OrgID, ps.ID)
	if err != nil || b.OrgID != caller.OrgID || b.ServerID != ps.ID || b.Platform != platform || b.URL != sc.URL || b.CatalogRef != sc.CatalogRef || b.ToolMode != "fixed" || !absoluteEndpoint(b.URL) {
		return nil, mcp.CredentialUnavailable()
	}
	switch platform {
	case "google":
		ok = r.native[b.CatalogRef] == "google" && b.AuthKind == manifest.AuthOAuth2
	case "microsoft":
		ok = r.native[b.CatalogRef] == "microsoft" && b.AuthKind == manifest.AuthOAuth2 && b.Delegated
	case "arcade":
		ok = arcadeBinding(b)
	}
	if !ok {
		return nil, mcp.CredentialUnavailable()
	}
	auth, d := r.resolver.Resolve(cc)
	if d != nil {
		return nil, d
	} // preserve the typed refusal and sealed link
	if auth == nil || auth.Header.Get("Authorization") == "" {
		return nil, mcp.CredentialUnavailable()
	}
	return &mcp.AuthOverride{Header: auth.Header.Clone()}, nil
}

// PurgeCredentials delegates local invalidation even when admission changed
// or remote cleanup failed. Revocation state/cleanup belong to the control
// plane; this neither performs remote revocation nor broadcasts to replicas.
func (r *Registry) PurgeCredentials(ctx context.Context, org, user, server string) (int, error) {
	if r == nil || r.resolver == nil {
		return 0, errors.New("mcp vendors: no credential resolver")
	}
	return r.resolver.PurgeCredentials(ctx, org, user, server)
}

func absoluteEndpoint(raw string) bool {
	u, err := url.Parse(raw)
	return err == nil && (u.Scheme == "https" || u.Scheme == "http") && u.Host != "" && u.User == nil && u.RawQuery == "" && u.Fragment == ""
}

// nativeProvider: FutureAGI's own per-person REST provider with a fixed tool
// list. A catalog directory keeps generated tools in GeneratedTools, while the
// embedded bundle (the gateway's default catalog) folds them into
// Tools.Items, so the fixed list is AllTools in either form.
func nativeProvider(p *manifest.Provider, group string) bool {
	return providerClientSource(p, k2ClientSource) && p.ID == "futureagi-rest" && p.Kind == manifest.KindREST && p.Publisher == "futureagi" &&
		p.Auth.Kind == manifest.AuthOAuth2 && p.Auth.OAuth2 != nil && p.Auth.OAuth2.ClientGroup == group &&
		p.Auth.OAuth2.PKCE && slices.Contains(p.AccountModes, "user") &&
		p.Tools.Source == manifest.ToolsOperations && len(p.AllTools()) > 0
}
