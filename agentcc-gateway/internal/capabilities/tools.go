package capabilities

import "sync/atomic"

// Tool capability endpoint ids: the REST capabilities the capability
// core serves. search is also a provider adapter endpoint.
const (
	EndpointFetch           = "fetch"
	EndpointDocumentsParse  = "documents.parse"
	EndpointGuardrails      = "guardrails"
	EndpointEvals           = "evals"
	EndpointBrowserSessions = "browser.sessions"
)

// toolEndpoints are the tool capability rows and their tiers, in published
// order. Their state comes from the operator config and the capability
// core's adapter registry, through the tool source; new vendors are added
// there, never as rows here.
var toolEndpoints = []struct{ id, tier string }{
	{EndpointSearch, TierProviderDependent},
	{EndpointFetch, TierBeta},
	{EndpointDocumentsParse, TierProviderDependent},
	{EndpointGuardrails, TierBeta},
	{EndpointEvals, TierBeta},
	{EndpointBrowserSessions, TierBeta},
}

// IsToolEndpoint reports whether id is a tool capability row.
func IsToolEndpoint(id string) bool {
	for _, e := range toolEndpoints {
		if e.id == id {
			return true
		}
	}
	return false
}

// Price is a tool vendor's published managed price for one billed unit
// (capabilities-v1 price): usd_per_unit_billed includes the markup, which
// markup_percent states.
type Price struct {
	SKU              string  `json:"sku"`
	USDPerUnitBilled float64 `json:"usd_per_unit_billed"`
	MarkupPercent    float64 `json:"markup_percent"`
}

// ToolCell is one tool vendor's cell on one tool endpoint.
type ToolCell struct {
	Endpoint string
	Vendor   string
	Cell     Cell
}

// ToolSource lists the tool cells the gateway serves now.
type ToolSource func() []ToolCell

var toolSource atomic.Pointer[ToolSource]

// SetToolSource installs the capability core's cells: the wiring calls it
// once when tools are enabled. With none installed the matrix has no tool
// rows beyond the search row. It returns a function that restores the
// previous source (for tests).
func SetToolSource(src ToolSource) (restore func()) {
	var next *ToolSource
	if src != nil {
		next = &src
	}
	prev := toolSource.Swap(next)
	return func() { toolSource.Store(prev) }
}

// applyToolRows adds the tool rows and their vendors' cells to m when a tool
// source is installed: a vendor's cell replaces a provider cell of the same
// id only where that provider does not serve the endpoint, and a row's tier
// is its published tier once any cell serves it.
func applyToolRows(m *Matrix) {
	src := toolSource.Load()
	if src == nil || *src == nil {
		return
	}
	cells := (*src)()
	for _, e := range toolEndpoints {
		row := rowFor(m, e.id)
		for _, tc := range cells {
			if tc.Endpoint != e.id {
				continue
			}
			if old, ok := row.Providers[tc.Vendor]; ok && old.Status != StatusUnsupported {
				continue
			}
			row.Providers[tc.Vendor] = tc.Cell
			if tc.Cell.Status != StatusUnsupported {
				row.LaunchTier = e.tier
			}
		}
	}
}

// rowFor is m's row for endpoint, appended (unsupported, no cells) when m
// has none.
func rowFor(m *Matrix, endpoint string) *EndpointRow {
	for i := range m.Endpoints {
		if m.Endpoints[i].Endpoint == endpoint {
			return &m.Endpoints[i]
		}
	}
	m.Endpoints = append(m.Endpoints, EndpointRow{Endpoint: endpoint, LaunchTier: TierUnsupported, Providers: map[string]Cell{}})
	return &m.Endpoints[len(m.Endpoints)-1]
}
