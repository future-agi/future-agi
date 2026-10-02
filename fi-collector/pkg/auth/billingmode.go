package auth

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
)

// tracingBillingModeTTL matches the Python emitter's cache (ee.usage) so both
// services share the same tracing_billing_mode:{org} key.
const tracingBillingModeTTL = 5 * time.Minute

// tracingBillingMode resolves the org's tracing billing mode ("storage" or
// "events"): Redis cache first, Postgres fallback, "storage" default.
func (u *UsageEmitter) tracingBillingMode(ctx context.Context, orgID string) string {
	cacheKey := "tracing_billing_mode:" + orgID

	if u.rdb != nil {
		if cached, err := u.rdb.Get(ctx, cacheKey).Result(); err == nil && cached != "" {
			return cached
		}
	}

	if u.pg == nil {
		return "storage"
	}

	const q = `SELECT tracing_billing_mode FROM usage_organizationsubscription
		WHERE organization_id = $1 AND deleted = false LIMIT 1`

	mode := "storage"
	cacheable := true
	var got string
	if err := u.pg.QueryRow(ctx, q, orgID).Scan(&got); err != nil {
		if err != pgx.ErrNoRows {
			// Transient PG error: don't pin the storage fallback in the shared cache.
			u.log.Warn("tracing_billing_mode lookup failed", "err", err, "org", orgID)
			cacheable = false
		}
	} else if got != "" {
		mode = got
	}

	if u.rdb != nil && cacheable {
		_ = u.rdb.SetEx(ctx, cacheKey, mode, tracingBillingModeTTL).Err()
	}

	return mode
}
