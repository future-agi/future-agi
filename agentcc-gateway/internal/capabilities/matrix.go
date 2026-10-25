// Package capabilities is the gateway's capability registry: for every
// published endpoint and every provider, whether the gateway serves it, with
// which features, whether managed traffic may use it, and whether a live
// qualification run proved it (capabilities-v1).
package capabilities

import (
	"sort"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
)

// Cell statuses.
const (
	StatusSupported         = "supported"
	StatusProviderDependent = "provider_dependent"
	StatusUnsupported       = "unsupported"
)

// Sources: what decided a cell, most specific evidence first.
const (
	SourceQualified = "qualified" // an imported live qualification run
	SourceCatalog   = "catalog"   // the managed catalog's models narrowed the features
	SourceConfig    = "config"    // the gateway's config: a gate, or the configured models
	SourceInterface = "interface" // the adapter's interfaces and api_format alone
)

// Managed resale terms: informational, never blocking. Native
// and byok_only describe tool vendors.
const (
	ResaleCleared     = "cleared"
	ResaleUnconfirmed = "unconfirmed"
	ResaleNative      = "native"
	ResaleBYOKOnly    = "byok_only"
)

// PricedFunc reports whether model can be priced on endpoint
// (cost.Priced, injected through Handlers.deps.Priced).
type PricedFunc func(endpoint, model string) bool

// Audience is who the matrix is for: the endpoints a caller can reach
// depend on how it authenticated.
type Audience int

const (
	// AudienceAdmin is the admin token: every route, every provider.
	AudienceAdmin Audience = iota
	// AudienceGlobalKey is a key that may use the global providers (an
	// internal key, a licence, or a gateway without keys).
	AudienceGlobalKey
	// AudienceOrgKey is a key limited to its org's providers and, when the
	// org opted in, the managed ones.
	AudienceOrgKey
)

// Inputs are what Build reads, while it runs; nothing is kept after.
type Inputs struct {
	Providers []Provider
	// Catalog is the managed catalog (nil: none).
	Catalog *catalog.Catalog
	// ModelDB describes configured models (nil: none).
	ModelDB *modeldb.ModelDB
	// OrgView, when set, is the caller's org's view of a model served by
	// providerID: base (the model database's or catalog's entry, nil when
	// neither knows it) with the org's Model Database limits and capability
	// switches on top. nil: the entries as they are.
	OrgView func(providerID, model string, base *modeldb.ModelInfo) *modeldb.ModelInfo
	// Priced decides managed_allowed (nil: nothing is priced).
	Priced PricedFunc
	// ManagedServes reports whether the gateway serves managed traffic on an
	// endpoint (nil: every endpoint).
	ManagedServes func(endpoint string) bool
	// Qualification is the imported live run (nil: no evidence).
	Qualification    *Qualification
	Audience         Audience
	ScheduledEnabled bool
	Now              time.Time
}

// Matrix is the capabilities-v1 document.
type Matrix struct {
	GeneratedAt time.Time     `json:"generated_at"`
	Endpoints   []EndpointRow `json:"endpoints"`
}

// EndpointRow is one endpoint and its cell per provider.
type EndpointRow struct {
	Endpoint   string          `json:"endpoint"`
	LaunchTier string          `json:"launch_tier"`
	Providers  map[string]Cell `json:"providers"`
}

// Cell is one provider on one endpoint.
type Cell struct {
	Status         string   `json:"status"`
	CredentialType string   `json:"credential_type"`
	Features       Features `json:"features"`
	ManagedAllowed bool     `json:"managed_allowed"`
	// ManagedResaleTerms is admin-only: /v1/capabilities
	// clears it, so customers never see internal legal status.
	ManagedResaleTerms string `json:"managed_resale_terms,omitempty"`
	// Price is a tool vendor's published managed price, markup included
	// (shown to customers too). Nil on every other cell.
	Price  *Price `json:"price,omitempty"`
	Source string `json:"source"`
	// Qualified is true only when a live run passed every check of the pair;
	// with no evidence it is false and QualifiedAt is null.
	Qualified   bool       `json:"qualified"`
	QualifiedAt *time.Time `json:"qualified_at"`
}

// Build evaluates every published endpoint against every provider.
func Build(in Inputs) *Matrix {
	knowns := make(map[string]knownModels, len(in.Providers))
	for _, p := range in.Providers {
		knowns[p.ID] = in.knownModelsOf(p)
	}
	m := &Matrix{GeneratedAt: in.Now.UTC(), Endpoints: make([]EndpointRow, 0, len(endpointSpecs))}
	for _, spec := range endpointSpecs {
		row := EndpointRow{Endpoint: spec.id, LaunchTier: TierUnsupported, Providers: make(map[string]Cell, len(in.Providers))}
		for _, p := range in.Providers {
			c := in.cell(spec, p, knowns[p.ID])
			row.Providers[p.ID] = c
			if c.Status != StatusUnsupported {
				row.LaunchTier = spec.tier
			}
		}
		m.Endpoints = append(m.Endpoints, row)
	}
	applyToolRows(m)
	return m
}

// cell evaluates spec for p.
func (in Inputs) cell(spec endpointSpec, p Provider, known knownModels) Cell {
	status, source := in.status(spec, p)
	c := Cell{Status: status, CredentialType: p.CredentialType, Source: source, ManagedResaleTerms: ResaleUnconfirmed}
	if p.ResaleTermsRef != "" {
		c.ManagedResaleTerms = ResaleCleared
	}
	if status != StatusUnsupported {
		c.Features = adapterFeatures(spec, p)
		if spec.generates() && len(known.infos) > 0 {
			c.Features = c.Features.and(anyModelFeatures(known.infos))
			if c.Source == SourceInterface {
				c.Source = known.source
			}
		}
		c.ManagedAllowed = in.managedAllowed(spec, p, known.ids)
	}
	if v, ok := in.Qualification.Verdict(p.ID, spec.id); ok {
		c.Qualified = v.Qualified
		if !v.At.IsZero() {
			at := v.At.UTC()
			c.QualifiedAt = &at
		}
		// A live pass proves an upstream serves what its adapter can only
		// forward; it cannot make the gateway serve what it does not.
		if c.Status != StatusUnsupported {
			c.Source = SourceQualified
		}
		if v.Qualified && c.Status == StatusProviderDependent {
			c.Status = StatusSupported
		}
	}
	return c
}

// status decides whether the gateway serves spec for p, and on what evidence.
func (in Inputs) status(spec endpointSpec, p Provider) (string, string) {
	switch spec.gate {
	case gateUnconfigured:
		return StatusUnsupported, SourceConfig
	case gateHosted:
		return StatusSupported, SourceConfig
	case gateAdmin:
		if in.Audience != AudienceAdmin {
			return StatusUnsupported, SourceConfig
		}
	case gateScheduled:
		if !in.ScheduledEnabled {
			return StatusUnsupported, SourceConfig
		}
		fallthrough
	case gateGlobal:
		if in.Audience == AudienceOrgKey {
			return StatusUnsupported, SourceConfig
		}
	}
	if p.Interfaces[spec.native] {
		if canonicalFormat(p.APIFormat) == "openai" && spec.openAIUpstreams != nil && !contains(spec.openAIUpstreams, p.Upstream) {
			return StatusProviderDependent, SourceInterface
		}
		return StatusSupported, SourceInterface
	}
	if spec.translated {
		return StatusSupported, SourceInterface
	}
	return StatusUnsupported, SourceInterface
}

// managedAllowed: a managed provider, a launch endpoint the gateway serves
// managed traffic on, and at least one of the provider's models priced there.
func (in Inputs) managedAllowed(spec endpointSpec, p Provider, modelIDs []string) bool {
	if !p.Managed || spec.tier != TierLaunch || in.Priced == nil {
		return false
	}
	if in.ManagedServes != nil && !in.ManagedServes(spec.id) {
		return false
	}
	for _, id := range modelIDs {
		if pricedFor(in.Priced, spec.id, p.ID, id) {
			return true
		}
	}
	return false
}

// pricedFor tries model as named, then filed under the provider.
func pricedFor(priced PricedFunc, endpoint, providerID, model string) bool {
	return priced(endpoint, model) || priced(endpoint, providerID+"/"+model)
}

// Lookup returns provider's cell on endpoint.
func (m *Matrix) Lookup(endpoint, provider string) (Cell, bool) {
	if m == nil {
		return Cell{}, false
	}
	for _, row := range m.Endpoints {
		if row.Endpoint == endpoint {
			c, ok := row.Providers[provider]
			return c, ok
		}
	}
	return Cell{}, false
}

// SupportedProviders are the providers that serve endpoint, supported or
// provider_dependent, sorted.
func (m *Matrix) SupportedProviders(endpoint string) []string {
	out := []string{}
	if m == nil {
		return out
	}
	for _, row := range m.Endpoints {
		if row.Endpoint != endpoint {
			continue
		}
		for id, c := range row.Providers {
			if c.Status != StatusUnsupported {
				out = append(out, id)
			}
		}
	}
	sort.Strings(out)
	return out
}
