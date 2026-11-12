package vendors

import (
	"encoding/json"
	"slices"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// ClientAdmission is the only K2 admission policy. It is a pure function of the
// request and of the catalog record as it is at the call: no verdict and no
// copy of a record is kept, so nothing decided earlier authorises a later use.
// Tenancy asks it at entry construction, at every credential lookup and before
// every send. The catalog is the build's embedded one, not a caller argument or
// an operator-directory override.
type ClientAdmission struct {
	runtime string
	catalog *manifest.Catalog
}

// NewClientAdmission makes no client and reads no credentials. Empty runtime is
// intentionally valid: every K2 entry then records a Coming soon refusal.
func NewClientAdmission(cat *manifest.Catalog, runtime string) (*ClientAdmission, error) {
	if runtime != "" {
		var err error
		if runtime, err = runtimeBase(runtime); err != nil {
			return nil, err
		}
	}
	return &ClientAdmission{runtime: runtime, catalog: cat}, nil
}

// provider is the record a reference names, read from the catalog now: the
// exact app, provider and version, else nil.
func (a *ClientAdmission) provider(ref string) *manifest.Provider {
	if a.catalog == nil {
		return nil
	}
	app, rest, _ := strings.Cut(ref, "/")
	id, version, _ := strings.Cut(rest, "@")
	if id != k2Provider || version == "" {
		return nil
	}
	for _, candidate := range a.catalog.Apps {
		if candidate == nil || candidate.ID != app {
			continue
		}
		if p := candidate.Provider(k2Provider); p != nil && p.App == app && p.Version == version {
			return p
		}
	}
	return nil
}

// connectionEnabled requires the catalog owner's declaration: schema version 1,
// enabled, and exactly the connect, list_tools and invoke operations.
func connectionEnabled(p *manifest.Provider) bool {
	if p == nil || p.XGateway == nil || p.XGateway.ConnectionEnablement == nil {
		return false
	}
	e := p.XGateway.ConnectionEnablement
	return e.SchemaVersion == 1 && e.Enabled && len(e.Operations) == 3 &&
		slices.Contains(e.Operations, "connect") && slices.Contains(e.Operations, "list_tools") && slices.Contains(e.Operations, "invoke")
}

// inventory is the provider's tool list as the catalog declares it, so a
// refused server still shows its tools as Coming soon without discovery.
func inventory(p *manifest.Provider) []mcp.Tool {
	var tools []mcp.Tool
	for _, tool := range p.AllTools() {
		raw, err := json.Marshal(tool)
		if err != nil {
			continue
		}
		var definition mcp.Tool
		if json.Unmarshal(raw, &definition) == nil {
			tools = append(tools, definition)
		}
	}
	return tools
}

// AdmitClient is the single K2 decision, evaluated from the current catalog
// record (auth.oauth2.clientSources, connectionEnablement), the current binding
// (catalog_ref, URL, posture) and the actual source. A server bound to K2 now,
// or that was when its client was built, is governed here; any other server is
// untouched. A client whose binding (catalog_ref or URL) has changed since it
// was built is refused, whatever else the new binding would allow.
func (a *ClientAdmission) AdmitClient(request mcp.ClientAdmissionRequest) mcp.ClientAdmission {
	app, k2 := k2App(request.CatalogRef)
	boundK2 := false
	if request.Bound != nil {
		_, boundK2 = k2App(request.Bound.CatalogRef)
	}
	if !k2 && !boundK2 {
		return mcp.ClientAdmission{}
	}
	p := a.provider(request.CatalogRef)
	var out mcp.ClientAdmission
	if request.Inventory && p != nil {
		out.Tools = inventory(p)
	}
	reason := ""
	switch {
	case a.runtime == "":
		reason = "k2_runtime_unavailable"
	case !k2:
		reason = "k2_binding_changed"
	case !providerClientSource(p, k2ClientSource):
		reason = "k2_client_source_unavailable"
	case !connectionEnabled(p):
		reason = "k2_connection_disabled"
	case !googleProvider(p) && !microsoftProvider(p):
		reason = "k2_provider_unavailable"
	case request.OrgID == "" || request.CredentialMode != "per_user" || request.SharedOAuth || request.Managed || request.Builtin || request.InlineAuth:
		reason = "k2_posture_unavailable"
	case request.URL != a.runtime+"/"+app+"/mcp":
		reason = "k2_endpoint_unavailable"
	case request.Bound != nil && (request.Bound.CatalogRef != request.CatalogRef || request.Bound.URL != request.URL):
		reason = "k2_binding_changed"
	}
	if reason != "" {
		out.Denial = k2Refusal(reason)
	}
	return out
}

func k2Refusal(reason string) *mcp.Denial {
	d := mcp.CredentialUnavailable()
	d.Message = "Coming soon: this connection is not available in this gateway yet; no credential was read and the call was not made."
	d.Metadata = map[string]any{"state": "coming_soon", "reason": reason, "client_source": k2ClientSource}
	return d
}

// resolveConstructed asks the pool's gate, which evaluates the one policy now,
// and only then delegates the credential operation. It has no runtime, catalog
// or posture policy of its own and keeps no answer. A synthetic/foreign pool
// without the gate fails closed.
func resolveConstructed(base mcp.CredentialResolver, cc *mcp.CallContext) (*mcp.AuthOverride, *mcp.Denial) {
	if cc == nil {
		return nil, mcp.CredentialUnavailable()
	}
	pool := mcp.TenantPoolOf(cc)
	if admitted, ok := pool.(mcp.ClientAdmissionPool); ok {
		if d := admitted.ClientAdmission(cc.Upstream.ServerID); d != nil {
			return nil, d
		}
	} else if _, k2 := k2Reference(cc); k2 {
		return nil, k2Refusal("k2_construction_unavailable")
	}
	if base == nil {
		return nil, nil
	}
	return base.Resolve(cc)
}
