package tenancy

import (
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The admission rule of this package: no recorded "admitted" outcome ever
// authorises a credential lookup or a send. The one policy, Deps.ClientAdmission,
// is asked at entry construction (Pool.apply), at every credential lookup
// (Pool.ClientAdmission) and before every send (entry.authorize). Each time it
// is asked about the server's CURRENT binding in the store, never about the
// pool's last-known-good copy of it, so a config the pool could not load (an
// invalid sibling) cannot keep a refused or replaced binding serving. A refusal
// retires the entry; nothing admitted is stored.

// settingsRequest is the only place a server's settings become an admission
// request, so construction and every later use derive the binding identically.
func settingsRequest(s tenant.MCPServerSettings) mcp.ClientAdmissionRequest {
	shared, err := sharedOAuthOf(s)
	return mcp.ClientAdmissionRequest{
		ServerID: s.ID, CatalogRef: s.CatalogRef, URL: s.URL, CredentialMode: s.CredentialMode,
		// A credential kind the gateway cannot place is no posture to admit.
		SharedOAuth: shared || err != nil,
		InlineAuth:  s.Credential.Secret != nil || s.Auth != nil,
	}
}

// request is the admission request for the binding this spec's clients are
// built for.
func (sp spec) request(org string) mcp.ClientAdmissionRequest {
	req := sp.admit
	req.OrgID, req.ServerID = org, sp.id
	req.Managed, req.Builtin = sp.source == SourceManaged, sp.builtin != ""
	if sp.source != SourceOrg {
		req.URL = sp.client.URL
	}
	return req
}

// admitSpec is the construction-time evaluation of sp, which would replace
// old when it exists: the same policy, with the old entry's binding as the one
// its clients were built for, and with the offline inventory asked for.
func (p *Pool) admitSpec(sp spec, old *entry) mcp.ClientAdmission {
	policy := p.m.deps().ClientAdmission
	if policy == nil {
		return mcp.ClientAdmission{}
	}
	req := sp.request(p.org)
	req.Inventory = true
	if old != nil {
		bound := old.spec().request(p.org)
		req.Bound = &mcp.ClientBinding{CatalogRef: bound.CatalogRef, URL: bound.URL}
	}
	return policy.AdmitClient(req)
}

// storeConfig is the org's config in the store now. known is false for a
// manager with no store, which has no binding newer than a spec's own.
func (p *Pool) storeConfig() (cfg *tenant.OrgConfig, known bool) {
	if p.m.opts.Store == nil {
		return nil, false
	}
	return p.m.opts.Store.Get(p.org), true
}

// refusalFor asks the policy now whether this entry may be used: the server's
// binding in cfg (the store's current config, nil when the org has none) is
// what is evaluated, and the binding the entry's clients were built for rides
// along so a policy can refuse a client whose binding has changed. A server
// the config no longer holds binds nothing. A refusal retires the entry and is
// returned; no admission is returned or kept. It never reads the store, so a
// store write can call it from its own callback.
func (e *entry) refusalFor(cfg *tenant.OrgConfig, known bool) *mcp.Denial {
	policy := e.pool.m.deps().ClientAdmission
	if policy == nil {
		return nil
	}
	sp := e.spec()
	bound := sp.request(e.pool.org)
	current := bound
	if known && sp.source == SourceOrg {
		current = mcp.ClientAdmissionRequest{OrgID: e.pool.org, ServerID: e.id}
		if cfg != nil && cfg.MCP != nil && cfg.MCP.Enabled {
			if s, ok := cfg.MCPServer(e.id); ok && s.Enabled {
				current = settingsRequest(s)
				current.OrgID = e.pool.org
			}
		}
	}
	current.Bound = &mcp.ClientBinding{CatalogRef: bound.CatalogRef, URL: bound.URL}
	if refusal := policy.AdmitClient(current).Denial; refusal != nil {
		e.retire()
		return refusal
	}
	return nil
}
