package redisstate

import (
	"context"
	"fmt"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"log/slog"
	"math"
	"strconv"
	"strings"
	"time"

	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/redis/go-redis/v9"
)

// BudgetStore provides Redis-backed budget counter operations.
// All monetary values are stored as integer microdollars (1 USD = 1,000,000)
// to avoid floating-point drift from HINCRBYFLOAT.
type BudgetStore struct {
	client               *Client
	prefix               string
	operation            string
	at                   time.Time
	guardKey, guardToken string
	receiptTTL           time.Duration
}

const microUnit = 1_000_000 // 1 USD = 1,000,000 microdollars

// DefaultReceiptTTL is the least lifetime of an operation receipt when the
// store is not configured: the default 720h video job retention plus the 24h
// window in which a terminal job may still settle.
const DefaultReceiptTTL = 744 * time.Hour

// NewBudgetStore creates a Redis-backed budget store.
func NewBudgetStore(client *Client, prefix string) *BudgetStore {
	if prefix == "" {
		prefix = "agentcc:budget:"
	}
	return &BudgetStore{client: client, prefix: prefix}
}

// budgetKey encodes the period start into the key so period resets happen
// naturally when the date changes. Old keys auto-expire via TTL.
func (s *BudgetStore) budgetKey(org, level, key, period string) string {
	now := time.Now().UTC()
	if !s.at.IsZero() {
		now = s.at.UTC()
	}
	start := budgetpkg.PeriodStart(period, now)
	startStr := start.Format("2006-01-02")
	if key == "" {
		return fmt.Sprintf("%s%s:%s:%s", s.prefix, org, level, startStr)
	}
	return fmt.Sprintf("%s%s:%s:%s:%s", s.prefix, org, level, key, startStr)
}

// budgetTTL returns the TTL for a budget key, generously exceeding the period
// so stale keys self-expire without a cron job.
func budgetTTL(period string) time.Duration {
	switch period {
	case "daily":
		return 48 * time.Hour
	case "weekly":
		return 8 * 24 * time.Hour
	case "monthly":
		return 35 * 24 * time.Hour
	default: // "total"
		return 0 // no expiry
	}
}

func usdToMicros(usd float64) int64 {
	return int64(math.Round(usd * microUnit))
}

func microsToUSD(micros int64) float64 {
	return float64(micros) / microUnit
}

// recordSpendScript atomically increments total and per-model spend counters,
// setting TTL on first write. Values are integer microdollars.
//
// KEYS[1] = budget hash key
// KEYS[2] = operation receipt key (present only when ARGV[4] > 0)
// ARGV[1] = cost in microdollars (int)
// ARGV[2] = model field name ("model:<name>") or "" for no model
// ARGV[3] = TTL in seconds (0 = no expiry)
// ARGV[4] = receipt TTL in seconds (0 = not an operation, no receipt)
//
// Returns the new total spend in microdollars. A replayed operation returns
// the current total without applying its delta again.
var recordSpendScript = redis.NewScript(`
local key = KEYS[1]
local model_field = ARGV[2]
local ttl = tonumber(ARGV[3])
local receipt_ttl = tonumber(ARGV[4] or '0')
if receipt_ttl > 0 and redis.call('HEXISTS', KEYS[2], key) == 1 then
    return redis.call('HGET', key, 'total') or '0'
end

-- ARGV[1] is passed as sent: tonumber() makes it a double, and MaxInt64 (a
-- saturated cost) would come back as 2^63, which HINCRBY refuses.
redis.call('HINCRBY', key, 'total', ARGV[1])
if model_field ~= '' then
    redis.call('HINCRBY', key, model_field, ARGV[1])
end
if ttl > 0 then
    local current_ttl = redis.call('TTL', key)
    if current_ttl < 0 then
        redis.call('EXPIRE', key, ttl)
    end
end
if receipt_ttl > 0 then
    redis.call('HSET', KEYS[2], key, ARGV[1])
    if redis.call('TTL', KEYS[2]) < receipt_ttl then
        redis.call('EXPIRE', KEYS[2], receipt_ttl)
    end
end
-- The stored integer as a string: a Lua number above 2^53 is not exact.
return redis.call('HGET', key, 'total')
`)

// checkAndRecordScript atomically checks budget limit and records spend.
// Returns -1 if over budget, otherwise the new total in microdollars.
//
// KEYS[1] = budget hash key
// KEYS[2] = operation receipt key (present only when ARGV[6] > 0)
// KEYS[3] = reservation fence (optional; only with a receipt key)
// ARGV[1] = cost in microdollars
// ARGV[2] = limit in microdollars
// ARGV[3] = model field ("model:<name>") or ""
// ARGV[4] = model limit in microdollars (0 = no per-model limit)
// ARGV[5] = TTL in seconds
// ARGV[6] = receipt TTL in seconds (0 = not an operation, no receipt)
// ARGV[7] = reservation fence token
var checkAndRecordScript = redis.NewScript(`
local key = KEYS[1]
local cost = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local model_field = ARGV[3]
local model_limit = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])
local receipt_ttl = tonumber(ARGV[6] or '0')
if #KEYS > 2 and redis.call('GET', KEYS[3]) ~= ARGV[7] then
    return redis.error_reply('reservation fence lost')
end
if receipt_ttl > 0 and redis.call('HEXISTS', KEYS[2], key) == 1 then
    return redis.call('HGET', key, 'total') or '0'
end

local spent = tonumber(redis.call('HGET', key, 'total') or "0")
if limit > 0 and spent + cost > limit then
    return -1
end

if model_field ~= '' and model_limit > 0 then
    local model_spent = tonumber(redis.call('HGET', key, model_field) or "0")
    if model_spent + cost > model_limit then
        return -2
    end
end

-- ARGV[1] is passed as sent: tonumber() makes it a double, and MaxInt64 (a
-- saturated cost) would come back as 2^63, which HINCRBY refuses.
redis.call('HINCRBY', key, 'total', ARGV[1])
if model_field ~= '' then
    redis.call('HINCRBY', key, model_field, ARGV[1])
end
if ttl > 0 then
    local current_ttl = redis.call('TTL', key)
    if current_ttl < 0 then
        redis.call('EXPIRE', key, ttl)
    end
end
if receipt_ttl > 0 then
    redis.call('HSET', KEYS[2], key, ARGV[1])
    if redis.call('TTL', KEYS[2]) < receipt_ttl then
        redis.call('EXPIRE', KEYS[2], receipt_ttl)
    end
end
-- The stored integer as a string: a Lua number above 2^53 is not exact.
return redis.call('HGET', key, 'total')
`)

// RecordSpend records cost to a budget counter in Redis.
// Returns the new total spend in USD, or (-1, false) if unavailable.
func (s *BudgetStore) RecordSpend(org, level, key, period, model string, cost float64) (float64, bool) {
	if s.client == nil || !s.client.Available() {
		return -1, false
	}

	redisKey := s.budgetKey(org, level, key, period)
	costMicros := models.SpendMicros(cost)
	modelField := ""
	if model != "" {
		modelField = "model:" + model
	}
	ttl := budgetTTL(period)
	ttlSec := int64(ttl.Seconds())
	keys, receiptSec := s.receiptKeys(redisKey, period)

	var newTotal int64
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	err := s.client.Do(func(rdb redis.UniversalClient) error {
		res, err := recordSpendScript.Run(ctx, rdb,
			keys,
			costMicros, modelField, ttlSec, receiptSec,
		).Int64()
		if err != nil {
			return err
		}
		newTotal = res
		return nil
	})

	if err != nil {
		slog.Debug("redis budget record failed", "org", org, "level", level, "error", err)
		return -1, false
	}

	return microsToUSD(newTotal), true
}

// GetSpend retrieves the current spend from Redis.
// Returns (totalSpend, perModelSpend, ok).
func (s *BudgetStore) GetSpend(org, level, key, period string) (float64, map[string]float64, bool) {
	if s.client == nil || !s.client.Available() {
		return 0, nil, false
	}

	redisKey := s.budgetKey(org, level, key, period)

	var fields map[string]string
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	err := s.client.Do(func(rdb redis.UniversalClient) error {
		var err error
		fields, err = rdb.HGetAll(ctx, redisKey).Result()
		return err
	})

	if err != nil {
		return 0, nil, false
	}

	if len(fields) == 0 {
		return 0, nil, true // key doesn't exist yet
	}

	var totalSpend float64
	modelSpend := make(map[string]float64)

	for field, value := range fields {
		micros, _ := strconv.ParseInt(value, 10, 64)
		if field == "total" {
			totalSpend = microsToUSD(micros)
		} else if strings.HasPrefix(field, "model:") {
			modelSpend[field[6:]] = microsToUSD(micros)
		}
	}

	return totalSpend, modelSpend, true
}

// SeedSpend sets initial spend values. Used at startup to seed from PostgreSQL.
// Skips seeding if the key already exists (another replica may have seeded and
// real traffic may have incremented the counters since then).
func (s *BudgetStore) SeedSpend(org, level, key, period string, totalSpend float64, modelSpend map[string]float64) bool {
	if s.client == nil || !s.client.Available() {
		return false
	}

	redisKey := s.budgetKey(org, level, key, period)
	ttl := budgetTTL(period)

	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()

	err := s.client.Do(func(rdb redis.UniversalClient) error {
		// Check if key already exists — don't overwrite counters that may
		// have been incremented by real traffic since another replica seeded.
		exists, err := rdb.Exists(ctx, redisKey).Result()
		if err != nil {
			return err
		}
		if exists > 0 {
			return nil // already seeded
		}

		pipe := rdb.Pipeline()
		pipe.HSet(ctx, redisKey, "total", usdToMicros(totalSpend))
		for model, spend := range modelSpend {
			pipe.HSet(ctx, redisKey, "model:"+model, usdToMicros(spend))
		}
		if ttl > 0 {
			pipe.Expire(ctx, redisKey, ttl)
		}
		_, err = pipe.Exec(ctx)
		return err
	})

	if err != nil {
		slog.Warn("redis budget seed failed", "org", org, "level", level, "error", err)
		return false
	}
	return true
}

// Available returns true if Redis is reachable.
func (s *BudgetStore) Available() bool {
	return s.client != nil && s.client.Available()
}

// CheckAndRecordSpend reserves spend atomically with the existing limit-checking
// script. allowed=false, ok=true means a budget rejection; ok=false means Redis
// could not confirm the operation. Callers must fail closed on the latter.
func (s *BudgetStore) CheckAndRecordSpend(org, level, key, period, model string, cost, limit, modelLimit float64) (total float64, allowed, ok bool) {
	if s.client == nil || !s.client.Available() || cost < 0 || limit < 0 || modelLimit < 0 || math.IsNaN(cost) || math.IsNaN(limit) || math.IsNaN(modelLimit) || math.IsInf(cost, 0) || math.IsInf(limit, 0) || math.IsInf(modelLimit, 0) || cost >= float64(math.MaxInt64)/microUnit || limit >= float64(math.MaxInt64)/microUnit || modelLimit >= float64(math.MaxInt64)/microUnit {
		return -1, false, false
	}
	modelField := ""
	if model != "" {
		modelField = "model:" + model
	}
	// A reservation fence is only meaningful with the receipt that dedups it.
	if s.guardKey != "" && s.operation == "" {
		return -1, false, false
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	var result int64
	keys, receiptSec := s.receiptKeys(s.budgetKey(org, level, key, period), period)
	if s.guardKey != "" {
		keys = append(keys, s.guardKey)
	}
	err := s.client.Do(func(rdb redis.UniversalClient) error {
		var err error
		result, err = checkAndRecordScript.Run(ctx, rdb, keys, models.SpendMicros(cost), usdToMicros(limit), modelField, usdToMicros(modelLimit), int64(budgetTTL(period).Seconds()), receiptSec, s.guardToken).Int64()
		return err
	})
	if err != nil {
		return -1, false, false
	}
	if result < 0 {
		return -1, false, true
	}
	return microsToUSD(result), true, true
}

// WithOperation pins a signed RecordSpend delta to its reservation period and
// atomically deduplicates it with a receipt kept outside the counter hash.
// Copies are immutable and safe across concurrent jobs; the ordinary plugin
// path is unchanged.
func (s *BudgetStore) WithOperation(id string, at time.Time) *BudgetStore {
	copy := *s
	copy.operation = id
	copy.at = at
	return &copy
}

// WithReceiptTTL sets the least lifetime of operation receipts written by the
// returned copy. A receipt never expires before the counter hash it protects.
func (s *BudgetStore) WithReceiptTTL(ttl time.Duration) *BudgetStore {
	copy := *s
	copy.receiptTTL = ttl
	return &copy
}

// receiptKey holds one operation's receipts, one field per counter hash it
// changed. Receipts stay out of the counter hashes so ordinary budget reads
// (HGETALL on every chat request) do not grow with video volume, and the
// counters keep their period TTL.
func (s *BudgetStore) receiptKey() string { return s.prefix + "receipt:" + s.operation }

// receiptKeys returns the script keys for a counter hash and the receipt TTL
// in seconds, which is zero for ordinary (non-operation) spend.
func (s *BudgetStore) receiptKeys(counter, period string) ([]string, int64) {
	if s.operation == "" {
		return []string{counter}, 0
	}
	ttl := s.receiptTTL
	if ttl <= 0 {
		ttl = DefaultReceiptTTL
	}
	ttl = max(ttl, budgetTTL(period))
	return []string{counter, s.receiptKey()}, max(int64(ttl/time.Second), 1)
}

// WithReservation fences acceptance against orphan recovery. The existing
// CheckAndRecordSpend script checks this guard atomically with the budget debit.
func (s *BudgetStore) WithReservation(id string, at time.Time, guardKey, token string) *BudgetStore {
	copy := s.WithOperation(id, at)
	copy.guardKey, copy.guardToken = guardKey, token
	return copy
}

// OperationApplied distinguishes a lost reservation reply from an uncharged
// scope. The receipt is written atomically with the counter change.
func (s *BudgetStore) OperationApplied(ctx context.Context, org, level, key, period string) (bool, error) {
	if s.operation == "" {
		return false, fmt.Errorf("budget operation id required")
	}
	var applied bool
	err := s.client.Do(func(rdb redis.UniversalClient) error {
		var err error
		applied, err = rdb.HExists(ctx, s.receiptKey(), s.budgetKey(org, level, key, period)).Result()
		return err
	})
	return applied, err
}
