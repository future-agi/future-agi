package pricing

import (
	"context"
	"errors"
	"log/slog"
	"sync"
	"time"

	"github.com/jackc/pgx/v5"
)

// rowQuerier is the subset of *pgxpool.Pool we need. Local interface keeps
// the package testable without a live Postgres.
type rowQuerier interface {
	QueryRow(ctx context.Context, sql string, args ...any) pgx.Row
}

// Matches Django's CustomAIModel.objects.get(organization_id=…, user_model_id=…)
// (model_hub/models/custom_models.py). deleted=false mirrors Django's
// soft-delete default manager (BaseModelManager.get_queryset() in
// tfc/utils/base_model.py filters deleted=False), not a uniqueness constraint.
const customPricingQuery = `
SELECT input_token_cost, output_token_cost
FROM model_hub_customaimodel
WHERE organization_id = $1 AND user_model_id = $2 AND deleted = false
LIMIT 1`

type customEntry struct {
	inPer1K, outPer1K float64
	found             bool
	expires           time.Time
}

// errTTL bounds how long a transient DB failure is cached, distinct from the
// (much longer) positive/negative-result TTL. Django never cached errors —
// every unknown-model span retried the query — but that's a blocking DB call
// on the hot ingestion path, so during a PG outage it would multiply into a
// query storm. We deviate: cache the failure briefly so at most one query
// (and one Warn) per (org,model) fires every 45s. Worst case, custom-priced
// spans read cost 0 for up to 45s after PG recovers.
const errTTL = 45 * time.Second

// CustomPricing resolves per-org model prices from PG with a TTL cache.
// Mirrors Django's Redis cache custom_model_pricing:{org}:{model} (24h,
// negative results included).
type CustomPricing struct {
	db  rowQuerier
	ttl time.Duration
	log *slog.Logger

	mu    sync.RWMutex
	cache map[string]customEntry
}

func NewCustomPricing(db rowQuerier, ttl time.Duration, log *slog.Logger) *CustomPricing {
	return &CustomPricing{db: db, ttl: ttl, log: log, cache: map[string]customEntry{}}
}

// Cost prices with per-1K-token rates: prompt*(in/1000) + completion*(out/1000)
// — Django otel.py:1771-1780. ok=false when the org has no such model.
func (c *CustomPricing) Cost(
	ctx context.Context, orgID, model string, promptTokens, completionTokens int32,
) (float64, bool) {
	key := orgID + "\x00" + model

	c.mu.RLock()
	e, hit := c.cache[key]
	c.mu.RUnlock()

	if !hit || time.Now().After(e.expires) {
		var in, out float64
		err := c.db.QueryRow(ctx, customPricingQuery, orgID, model).Scan(&in, &out)
		switch {
		case err == nil:
			e = customEntry{inPer1K: in, outPer1K: out, found: true, expires: time.Now().Add(c.ttl)}
		case errors.Is(err, pgx.ErrNoRows):
			e = customEntry{found: false, expires: time.Now().Add(c.ttl)}
		default:
			// Transient DB failure: log and cache briefly (errTTL) so the
			// hot path doesn't re-query — and re-Warn — for every span until
			// PG recovers. See errTTL comment for the tradeoff.
			c.log.Warn("custom pricing lookup failed", "org", orgID, "model", model, "err", err)
			e = customEntry{found: false, expires: time.Now().Add(errTTL)}
		}
		c.mu.Lock()
		c.cache[key] = e
		c.mu.Unlock()
	}

	if !e.found {
		return 0, false
	}
	return float64(promptTokens)*(e.inPer1K/1000) +
		float64(completionTokens)*(e.outPer1K/1000), true
}
