package auth

import (
	"context"
	"fmt"
	"log/slog"
	"os"
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

// checkQuota runs checkQuotaLua by its SHA and sends the script again when
// Redis answers NOSCRIPT: a restarted Redis has lost every loaded script.
var checkQuota = redis.NewScript(checkQuotaLua)

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
	rdb *redis.Client
	pg  *pgxpool.Pool
	log *slog.Logger
	// Only an explicit startup deployment decision may exempt admission.
	// A nil receiver or zero value still fails closed.
	nonbillable bool
}

// NewMetering captures deployment intent once. OSS has no billing pause writer;
// configured EE/cloud billing must retain admission even without its store.
func NewMetering(rdb *redis.Client, pgRead *pgxpool.Pool, log *slog.Logger) *Metering {
	if !billingEnforcementConfigured(os.Getenv) {
		log.Warn("collector is not enforcing billing; this is expected for self-hosted open source. Do NOT set billing signals unless running Future AGI Enterprise or Cloud",
			"admission_mode", "non-billable",
			"enterprise_cloud_only_signals", []string{"EE_LICENSE_KEY", "CLOUD_DEPLOYMENT=US|EU|DEV", "FI_BILLING_ENFORCEMENT_ENABLED=true"})
		return &Metering{nonbillable: true, log: log}
	}
	log.Info("billing enforcement enabled", "admission_mode", "billable")
	if rdb == nil {
		log.Error("billing enforcement enabled without Redis: ingestion refuses with BUDGET_CHECK_UNAVAILABLE")
		return &Metering{pg: pgRead, log: log}
	}

	// Loaded up front so the first check takes one round trip; a check loads
	// it itself when Redis does not have it (down now, or restarted later).
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := checkQuota.Load(ctx, rdb).Err(); err != nil {
		log.Warn("metering lua script load failed, the first quota check loads it", "err", err)
	}

	return &Metering{rdb: rdb, pg: pgRead, log: log}
}

// CheckUsage checks if an org can perform a billable action.
// Billable deployments fail closed, including nil receivers and missing stores.
func (m *Metering) CheckUsage(ctx context.Context, orgID, eventType string, amount int64) CheckResult {
	if m != nil && m.nonbillable {
		return CheckResult{Allowed: true, Dimension: eventTypeToDimension[eventType]}
	}
	// EE writes this scope for org-wide spend caps, independently of the
	// ingestion dimension. Never let unknown events or quota errors bypass it.
	if refusal := m.budgetPauseRefusal(ctx, orgID, "total_spend"); refusal != nil {
		return *refusal
	}

	dimension, ok := eventTypeToDimension[eventType]
	if !ok {
		m.log.Warn("unknown event type in check_usage", "event_type", eventType)
		return CheckResult{Allowed: true}
	}

	if refusal := m.budgetPauseRefusal(ctx, orgID, dimension); refusal != nil {
		return *refusal
	}
	plan := m.getCachedPlan(ctx, orgID)

	if hardCapPlans[plan] {
		allowance, hasAllowance := freeTierAllowances[dimension]
		if hasAllowance && allowance > 0 {
			period := time.Now().UTC().Format("2006-01")
			usageKey := fmt.Sprintf("usage:%s:%s:%s", orgID, dimension, period)

			result, err := checkQuota.Run(ctx, m.rdb, []string{usageKey},
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

	return CheckResult{Allowed: true, Dimension: dimension}
}

func (m *Metering) budgetPauseRefusal(ctx context.Context, orgID, scope string) *CheckResult {
	unavailable := &CheckResult{
		Allowed: false, ErrorCode: "BUDGET_CHECK_UNAVAILABLE",
		Reason: "Budget status is unavailable. Retry when the check is restored.", Dimension: scope,
	}
	if m == nil || m.rdb == nil {
		return unavailable
	}
	paused, err := m.rdb.Get(ctx, fmt.Sprintf("pause:%s:%s", orgID, scope)).Result()
	if err != nil && err != redis.Nil {
		m.log.Warn("budget pause check failed, refusing", "err", err, "org", orgID, "scope", scope)
		return unavailable
	}
	if paused != "" {
		return &CheckResult{
			Allowed: false, ErrorCode: "BUDGET_PAUSED",
			Reason: fmt.Sprintf("Usage paused — budget limit set for %s", scope), Dimension: scope,
		}
	}
	return nil
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
