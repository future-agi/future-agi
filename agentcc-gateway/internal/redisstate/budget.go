package redisstate

import (
	"context"
	"fmt"
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
	client *Client
	prefix string
}

const microUnit = 1_000_000 // 1 USD = 1,000,000 microdollars

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
// ARGV[1] = cost in microdollars (int)
// ARGV[2] = model field name ("model:<name>") or "" for no model
// ARGV[3] = TTL in seconds (0 = no expiry)
//
// Returns the new total spend in microdollars.
var recordSpendScript = redis.NewScript(`
local key = KEYS[1]
local cost = tonumber(ARGV[1])
local model_field = ARGV[2]
local ttl = tonumber(ARGV[3])

local new_total = redis.call('HINCRBY', key, 'total', cost)
if model_field ~= '' then
    redis.call('HINCRBY', key, model_field, cost)
end
if ttl > 0 then
    local current_ttl = redis.call('TTL', key)
    if current_ttl < 0 then
        redis.call('EXPIRE', key, ttl)
    end
end
return new_total
`)

// checkAndRecordScript atomically checks budget limit and records spend.
// Returns -1 if over budget, otherwise the new total in microdollars.
//
// KEYS[1] = budget hash key
// ARGV[1] = cost in microdollars
// ARGV[2] = limit in microdollars
// ARGV[3] = model field ("model:<name>") or ""
// ARGV[4] = model limit in microdollars (0 = no per-model limit)
// ARGV[5] = TTL in seconds
var checkAndRecordScript = redis.NewScript(`
local key = KEYS[1]
local cost = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local model_field = ARGV[3]
local model_limit = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])

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

local new_total = redis.call('HINCRBY', key, 'total', cost)
if model_field ~= '' then
    redis.call('HINCRBY', key, model_field, cost)
end
if ttl > 0 then
    local current_ttl = redis.call('TTL', key)
    if current_ttl < 0 then
        redis.call('EXPIRE', key, ttl)
    end
end
return new_total
`)

// RecordSpend records cost to a budget counter in Redis.
// Returns the new total spend in USD, or (-1, false) if unavailable.
func (s *BudgetStore) RecordSpend(org, level, key, period, model string, cost float64) (float64, bool) {
	if s.client == nil || !s.client.Available() {
		return -1, false
	}

	redisKey := s.budgetKey(org, level, key, period)
	costMicros := usdToMicros(cost)
	modelField := ""
	if model != "" {
		modelField = "model:" + model
	}
	ttl := budgetTTL(period)
	ttlSec := int64(ttl.Seconds())

	var newTotal int64
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	err := s.client.Do(func(rdb redis.UniversalClient) error {
		res, err := recordSpendScript.Run(ctx, rdb,
			[]string{redisKey},
			costMicros, modelField, ttlSec,
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
