package server

import (
	"encoding/json"
	"errors"
	"math"
	"net/http"
	"sort"
	"strconv"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// maxBlocksBody bounds a PUT /-/catalog/blocks body.
const maxBlocksBody = 1 << 20

// catalogModelView is one catalog model as the console lists it. Endpoints
// and benchmarks have their own routes; the two empty fields shadow the
// model's so the list stays small.
type catalogModelView struct {
	*catalog.Model
	Endpoints        *struct{} `json:"endpoints,omitempty"`
	Benchmarks       *struct{} `json:"benchmarks,omitempty"`
	EndpointsCount   int       `json:"endpoints_count"`
	Provider         string    `json:"provider"`
	Vendor           string    `json:"vendor"`
	InputPerMTok     *float64  `json:"input_per_mtok,omitempty"`
	OutputPerMTok    *float64  `json:"output_per_mtok,omitempty"`
	CacheReadPerMTok *float64  `json:"cache_read_per_mtok,omitempty"`
	// Billed prices include the managed markup.
	BilledInputPerMTok  *float64 `json:"billed_input_per_mtok,omitempty"`
	BilledOutputPerMTok *float64 `json:"billed_output_per_mtok,omitempty"`
}

// catalogFreshness is the version and freshness every catalog read reports.
type catalogFreshness struct {
	SnapshotID string  `json:"snapshot_id"`
	Stale      bool    `json:"stale"`
	AgeSeconds float64 `json:"age_seconds"`
}

func freshness(store *catalog.Store) catalogFreshness {
	return catalogFreshness{SnapshotID: store.Load().ID(), Stale: store.Stale(), AgeSeconds: math.Round(store.Age().Seconds())}
}

// handleCatalog serves GET /-/catalog: every managed provider's models, with
// their state, endpoint count, and upstream and billed prices.
func (s *Server) handleCatalog(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) {
		return
	}
	cat := s.managed.Catalog.Load()
	factor := 1 + s.managed.MarkupPercent/100
	type providerView struct {
		Provider  string    `json:"provider"`
		Source    string    `json:"source"`
		FetchedAt time.Time `json:"fetched_at"`
		Models    int       `json:"models"`
		Endpoints int       `json:"endpoints"`
	}
	resp := struct {
		catalogFreshness
		MarkupPercent float64            `json:"markup_percent"`
		Providers     []providerView     `json:"providers"`
		Models        []catalogModelView `json:"models"`
	}{catalogFreshness: freshness(s.managed.Catalog), MarkupPercent: s.managed.MarkupPercent,
		Providers: []providerView{}, Models: []catalogModelView{}}
	for _, snap := range cat.Snapshots() {
		pv := providerView{Provider: snap.ProviderID, Source: snap.Source, FetchedAt: snap.FetchedAt, Models: len(snap.Models)}
		for _, m := range snap.Sorted() {
			pv.Endpoints += len(m.Endpoints)
			v := catalogModelView{Model: m, EndpointsCount: len(m.Endpoints), Provider: snap.ProviderID, Vendor: m.Vendor()}
			if !m.DynamicPrice {
				v.InputPerMTok = ptr(roundPrice(m.InputPerToken * 1e6))
				v.OutputPerMTok = ptr(roundPrice(m.OutputPerToken * 1e6))
				if m.CacheReadPerToken > 0 {
					v.CacheReadPerMTok = ptr(roundPrice(m.CacheReadPerToken * 1e6))
				}
				v.BilledInputPerMTok = ptr(roundPrice(m.InputPerToken * 1e6 * factor))
				v.BilledOutputPerMTok = ptr(roundPrice(m.OutputPerToken * 1e6 * factor))
			}
			resp.Models = append(resp.Models, v)
		}
		resp.Providers = append(resp.Providers, pv)
	}
	sort.SliceStable(resp.Models, func(i, j int) bool { return resp.Models[i].ID < resp.Models[j].ID })
	writeJSON(w, http.StatusOK, resp)
}

// handleCatalogEndpoints serves GET /-/catalog/endpoints?model=[&provider=]:
// the model's endpoints (catalog-endpoints-v1, with each endpoint's upstream
// fee), re-read from the upstream first when they are over 30 minutes old.
func (s *Server) handleCatalogEndpoints(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) {
		return
	}
	model := r.URL.Query().Get("model")
	if model == "" {
		models.WriteError(w, models.ErrBadRequest("missing_model", "the model query parameter is required"))
		return
	}
	_, m, ok := s.managed.Catalog.ModelEndpoints(r.Context(), r.URL.Query().Get("provider"), model)
	if !ok {
		models.WriteError(w, models.ErrNotFound("model_not_found", "no managed catalog lists model '"+model+"'"))
		return
	}
	writeJSON(w, http.StatusOK, m.EndpointsDocument(true))
}

// handleCatalogStats serves GET /-/catalog/stats: the current version's
// counts, per source.
func (s *Server) handleCatalogStats(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) {
		return
	}
	writeJSON(w, http.StatusOK, struct {
		catalogFreshness
		catalog.Stats
	}{freshness(s.managed.Catalog), s.managed.Catalog.Stats()})
}

// handleCatalogHistory serves GET /-/catalog/history?limit=: the catalog
// versions, newest first, each with how it changed.
func (s *Server) handleCatalogHistory(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) {
		return
	}
	limit := 0
	if raw := r.URL.Query().Get("limit"); raw != "" {
		n, err := strconv.Atoi(raw)
		if err != nil || n < 1 {
			models.WriteError(w, models.ErrBadRequest("invalid_limit", "limit must be a positive integer"))
			return
		}
		limit = n
	}
	writeJSON(w, http.StatusOK, map[string]any{"data": s.managed.Catalog.History(limit)})
}

// handleCatalogSnapshot serves GET /-/catalog/snapshots/{id}: a kept catalog
// version's full content.
func (s *Server) handleCatalogSnapshot(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) {
		return
	}
	id := r.URL.Query().Get("id")
	snap, ok := s.managed.Catalog.Snapshot(id)
	if !ok {
		models.WriteError(w, models.ErrNotFound("snapshot_not_found", "no kept catalog snapshot '"+id+"'"))
		return
	}
	writeJSON(w, http.StatusOK, snap)
}

// handleCatalogRefresh serves POST /-/catalog/refresh[?provider=]: it starts
// a model-list refresh of the provider (every managed provider when none is
// named), at most once a minute per provider, and answers 202 with the
// snapshot id the refresh starts from. GET /-/catalog/history shows the
// version it produces.
func (s *Server) handleCatalogRefresh(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) {
		return
	}
	var providers []string
	if p := r.URL.Query().Get("provider"); p != "" {
		providers = []string{p}
	}
	store := s.managed.Catalog
	from := store.Load().ID()
	started, retryAfter, err := store.RequestRefresh(providers)
	switch {
	case errors.Is(err, catalog.ErrUnknownProvider):
		models.WriteError(w, models.ErrNotFound("unknown_provider", "no managed provider '"+providers[0]+"'"))
		return
	case err != nil:
		models.WriteError(w, &models.APIError{Status: http.StatusServiceUnavailable, Type: models.ErrTypeServer,
			Code: "catalog_refresh_unavailable", Message: "the catalog refresher is not running"})
		return
	case len(started) == 0:
		models.WriteError(w, &models.APIError{Status: http.StatusTooManyRequests, Type: models.ErrTypeRateLimit,
			Code: "catalog_refresh_rate_limited", Message: "each provider's catalog refreshes at most once a minute",
			RetryAfterSec: int(math.Ceil(retryAfter.Seconds()))})
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"snapshot_id": from, "providers": started})
}

// handleCatalogBlocks serves PUT /-/catalog/blocks {models, reason}: it
// replaces the runtime blocks (model ids, or globs with "*") and returns
// every block in force. Blocks persist in catalog.snapshot_dir.
func (s *Server) handleCatalogBlocks(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) {
		return
	}
	var req struct {
		Models *[]string `json:"models"`
		Reason string    `json:"reason"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxBlocksBody)).Decode(&req); err != nil || req.Models == nil {
		models.WriteError(w, models.ErrBadRequest("invalid_blocks", `the body must be {"models": [...], "reason": "..."}`))
		return
	}
	blocks, err := s.managed.Catalog.SetBlocks(*req.Models, req.Reason)
	if err != nil {
		models.WriteError(w, &models.APIError{Status: http.StatusInternalServerError, Type: models.ErrTypeServer,
			Code: "catalog_blocks_not_saved", Message: "the blocks could not be saved: " + err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"blocks": blocks})
}
