package server

import (
	"math"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pricesfeed"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// modelPricesHandler captures only the operator publication switch/allowlist.
// Account entitlements, org providers/prices, key metadata, logs, billing and
// credentials are deliberately not dependencies of the projection. The global
// immutable catalogue and startup-effective markup are its only runtime data.
func (s *Server) modelPricesHandler() http.HandlerFunc {
	publication := s.publicPriceFeed
	return func(w http.ResponseWriter, r *http.Request) {
		if !publication.Enabled {
			http.NotFound(w, r)
			return
		}
		store := s.handlers.catalog
		if store == nil {
			http.Error(w, "price feed unavailable", http.StatusServiceUnavailable)
			return
		}
		now := store.Now()
		inputs, nextExpiry := publicPriceInputs(store.Load(), publication.Sources, now)
		feed, err := pricesfeed.Build(inputs, int(math.Round(s.handlers.managedMarkupPercent*100)), pricesfeed.SchemaVersion)
		if err != nil {
			http.Error(w, "price feed unavailable", http.StatusServiceUnavailable)
			return
		}
		maxAge := 300
		if !nextExpiry.IsZero() {
			maxAge = min(maxAge, max(0, int(nextExpiry.Sub(now)/time.Second)))
		}
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("ETag", feed.ETag)
		w.Header().Set("Cache-Control", "public, max-age="+strconv.Itoa(maxAge)+", must-revalidate")
		if priceETagMatches(r.Header.Get("If-None-Match"), feed.ETag) {
			w.WriteHeader(http.StatusNotModified)
			return
		}
		w.Write(feed.Body)
	}
}

// startupPricePublication copies the operator's publication switch and
// allowlist once, in New, so that neither the constructor's caller nor a
// routing reload (which replaces s.cfg) changes what the feed serves or what
// GET /-/config reports.
func startupPricePublication(p config.PublicPriceFeedConfig) config.PublicPriceFeedConfig {
	p.Sources = append([]config.PublicPriceSource(nil), p.Sources...)
	return p
}

// pricePublicationReport is GET /-/config's public_price_feed: the startup
// switch and the exact provider_id/catalog_source pairs, nothing else. An
// empty allowlist is [], not null.
func (s *Server) pricePublicationReport() map[string]interface{} {
	sources := make([]map[string]string, 0, len(s.publicPriceFeed.Sources))
	for _, source := range s.publicPriceFeed.Sources {
		sources = append(sources, map[string]string{"provider_id": source.ProviderID, "catalog_source": source.CatalogSource})
	}
	return map[string]interface{}{"enabled": s.publicPriceFeed.Enabled, "sources": sources}
}

// publicPriceInputs reuses the exact serving helpers without selecting a
// cheapest endpoint, canonicalizing an exact variant, reading an account or
// causing catalogue refresh. A fixed hypothetical global policy includes free
// opt-in; publication is catalogue eligibility, not an entitlement grant.
func publicPriceInputs(c *catalog.Catalog, allow []config.PublicPriceSource, now time.Time) ([]pricesfeed.Input, time.Time) {
	var out []pricesfeed.Input
	var next time.Time
	allowFree := true
	policy := &tenant.OrgConfig{ManagedModels: &tenant.ManagedModelsConfig{Enabled: true, AllowFree: &allowFree}}
	for _, snap := range c.Snapshots() {
		allowed := false
		for _, source := range allow {
			if source.ProviderID != "" && source.CatalogSource != "" && source.ProviderID == snap.ProviderID && source.CatalogSource == snap.CatalogSource {
				allowed = true
				break
			}
		}
		if !allowed {
			continue
		}
		for _, m := range snap.Sorted() {
			// Non-text output surfaces require their own dimensional contracts.
			if !m.Produces("text") {
				continue
			}
			l := catalog.Listing{ProviderID: snap.ProviderID, Source: snap.CatalogSource, Model: m}
			v, ok := managedOffer(l, policy, now, true)
			if !ok {
				continue
			}
			expires := ""
			if exp, err := time.Parse(time.DateOnly, m.ExpirationDate); err == nil {
				expires = m.ExpirationDate
				if next.IsZero() || exp.Before(next) {
					next = exp
				}
			}
			for _, e := range offerEndpoints(l, &v) {
				card := e.Prices.Card()
				card.ExplicitFree = m.ExplicitlyFree(snap.CatalogSource) && e.Prices == (catalog.Prices{})
				out = append(out, pricesfeed.Input{ProviderID: snap.ProviderID, CatalogSource: snap.CatalogSource, ModelID: m.ID,
					EndpointID: e.ID, UpstreamProvider: e.UpstreamProvider, EndpointStatus: e.Status, ExpiresAt: expires, Card: card})
			}
		}
	}
	return out, next
}

// GET uses weak comparison for If-None-Match, including lists and wildcard;
// the representation itself always advertises a strong content ETag.
func priceETagMatches(header, etag string) bool {
	for _, candidate := range strings.Split(header, ",") {
		candidate = strings.TrimSpace(candidate)
		if candidate == "*" || strings.TrimPrefix(candidate, "W/") == etag {
			return true
		}
	}
	return false
}
