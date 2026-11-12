package server

import (
	"sort"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Snapshot HTTP responses are operator views, not raw persistence dumps. Keep
// the stored Catalog lossless; shadow blocked prices only when serving it.
// Available rows retain the historical version's rates, not today's rates.
type catalogSnapshotModelView struct {
	*catalog.Model
	Prices            *catalog.Prices        `json:"prices,omitempty"`
	PriceTiers        []catalog.PriceTier    `json:"price_tiers,omitempty"`
	InputPerToken     *float64               `json:"input_per_token,omitempty"`
	OutputPerToken    *float64               `json:"output_per_token,omitempty"`
	CacheReadPerToken *float64               `json:"cache_read_per_token,omitempty"`
	Inclusive         *models.InclusivePrice `json:"inclusive_price,omitempty"`
	Endpoints         []catalog.Endpoint     `json:"endpoints,omitempty"`
}

type catalogSnapshotProviderView struct {
	*catalog.Snapshot
	ZDR    []string                   `json:"zdr,omitempty"`
	Models []catalogSnapshotModelView `json:"models"`
}

type catalogSnapshotView struct {
	ID        string                        `json:"id"`
	Providers []catalogSnapshotProviderView `json:"providers"`
}

func (s *Server) catalogCreditAvailable(cat *catalog.Catalog, provider string, m *catalog.Model, now time.Time) bool {
	_, retired := s.handlers.deprecations.ShutDown(s.handlers.deprecationHost(nil, provider), m.ID, now)
	live, exists := cat.Get(provider, m.ID)
	return exists && cat.ServesManaged(provider, m.ID) && live.Servable(now) && m.Servable(now) && !retired
}

func (s *Server) catalogSnapshotResponse(kept *catalog.Catalog) catalogSnapshotView {
	current, now := s.managed.Catalog.Load(), s.managed.Catalog.Now()
	out := catalogSnapshotView{ID: kept.ID(), Providers: []catalogSnapshotProviderView{}}
	for _, snap := range kept.Snapshots() {
		p := catalogSnapshotProviderView{Snapshot: snap, Models: []catalogSnapshotModelView{}}
		for key := range snap.ZDR {
			p.ZDR = append(p.ZDR, key)
		}
		sort.Strings(p.ZDR)
		for _, stored := range snap.Sorted() {
			m := *stored
			live, exists := current.Get(snap.ProviderID, m.ID)
			// InclusiveEnabled is deliberately not persisted. Apply the current
			// startup gate to this view's copy, never to the kept version.
			m.InclusiveEnabled = exists && live.InclusiveEnabled
			available := exists && s.catalogCreditAvailable(current, snap.ProviderID, live, now) &&
				m.Servable(now) && m.ManagedBillable(snap.CatalogSource)
			v := catalogSnapshotModelView{Model: &m}
			if available {
				v.Prices, v.PriceTiers = &m.Prices, m.PriceTiers
				v.InputPerToken, v.OutputPerToken = &m.InputPerToken, &m.OutputPerToken
				if m.CacheReadPerToken != 0 {
					v.CacheReadPerToken = &m.CacheReadPerToken
				}
				v.Inclusive = m.InclusivePrice(snap.CatalogSource)
				v.Endpoints = m.Endpoints
			} else {
				m.State = catalog.StateBlocked
				m.StateReason = "Not available on Future AGI credits; snapshot prices are hidden."
			}
			p.Models = append(p.Models, v)
		}
		out.Providers = append(out.Providers, p)
	}
	return out
}
