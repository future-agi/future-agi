package auth

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

var checkQuotaLua = `
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local amount = tonumber(ARGV[2]) or 1
local current = tonumber(redis.call('GET', key) or '0')
if limit <= 0 then return current end
if (current + amount) > limit then return -1 end
return current
`

var freeTierAllowances = map[string]int64{
	"tracing_events": 50_000,
	"storage":        50 * 1024 * 1024 * 1024,
}

var hardCapPlans = map[string]bool{
	"free": true,
}

var eventTypeToDimension = map[string]string{
	"tracing_event": "tracing_events",
	"observe_add":   "storage",
}

// CheckResult holds the outcome of a quota check.
type CheckResult struct {
	Allowed   bool
	Reason    string
	ErrorCode string
	Dimension string
}

// Metering enforces pre-check quota limits before ingestion.
type Metering struct {
	rdb    *redis.Client
	pg     *pgxpool.Pool
	log    *slog.Logger
	luaSHA string
}

// NewMetering creates a metering instance. Returns nil if rdb is nil.
func NewMetering(rdb *redis.Client, pgRead *pgxpool.Pool, log *slog.Logger) *Metering {
	if rdb == nil {
		return nil
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	sha, err := rdb.ScriptLoad(ctx, checkQuotaLua).Result()
	if err != nil {
		log.Warn("metering lua script load failed, quota checks disabled", "err", err)
		return &Metering{rdb: rdb, pg: pgRead, log: log}
	}

	return &Metering{rdb: rdb, pg: pgRead, log: log, luaSHA: sha}
}

// CheckUsage checks if an org can perform a billable action.
// Fail-open: returns Allowed=true on any error.
func (m *Metering) CheckUsage(ctx context.Context, orgID, eventType string, amount int64) CheckResult {
	if m == nil {
		return CheckResult{Allowed: true}
	}

	dimension, ok := eventTypeToDimension[eventType]
	if !ok {
		m.log.Warn("unknown event type in check_usage", "event_type", eventType)
		return CheckResult{Allowed: true}
	}

	plan := m.getCachedPlan(ctx, orgID)

	if hardCapPlans[plan] {
		allowance, hasAllowance := freeTierAllowances[dimension]
		if hasAllowance && allowance > 0 && m.luaSHA != "" {
			period := time.Now().UTC().Format("2006-01")
			usageKey := fmt.Sprintf("usage:%s:%s:%s", orgID, dimension, period)

			result, err := m.rdb.EvalSha(ctx, m.luaSHA, []string{usageKey},
				fmt.Sprintf("%d", allowance),
				fmt.Sprintf("%d", amount),
			).Int64()
			if err != nil {
				m.log.Warn("quota check lua failed, allowing", "err", err, "org", orgID)
				return CheckResult{Allowed: true, Dimension: dimension}
			}
			if result == -1 {
				return CheckResult{
					Allowed:   false,
					ErrorCode: "FREE_TIER_LIMIT",
					Reason:    fmt.Sprintf("Free tier %s limit reached", dimension),
					Dimension: dimension,
				}
			}
		}
	}

	// Check budget pause flag
	pauseKey := fmt.Sprintf("pause:%s:%s", orgID, dimension)
	paused, err := m.rdb.Get(ctx, pauseKey).Result()
	if err == nil && paused != "" {
		return CheckResult{
			Allowed:   false,
			ErrorCode: "BUDGET_PAUSED",
			Reason:    fmt.Sprintf("Usage paused — budget limit set for %s", dimension),
			Dimension: dimension,
		}
	}

	return CheckResult{Allowed: true, Dimension: dimension}
}

// getCachedPlan returns the org's billing plan from Redis cache, falling back to PG.
func (m *Metering) getCachedPlan(ctx context.Context, orgID string) string {
	cacheKey := fmt.Sprintf("plan:%s", orgID)

	cached, err := m.rdb.Get(ctx, cacheKey).Result()
	if err == nil && cached != "" {
		return cached
	}

	if m.pg == nil {
		return "free"
	}

	const q = `SELECT plan FROM usage_organizationsubscription
		WHERE organization_id = $1 AND deleted = false LIMIT 1`

	var plan string
	err = m.pg.QueryRow(ctx, q, orgID).Scan(&plan)
	if err != nil {
		if err != pgx.ErrNoRows {
			m.log.Warn("plan lookup failed", "err", err, "org", orgID)
		}
		plan = "free"
	}

	_ = m.rdb.SetEx(ctx, cacheKey, plan, 5*time.Minute).Err()

	return plan
}
