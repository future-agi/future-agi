package redisstate

import (
	"context"
	"log/slog"
	"math"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"
)

// slidingWindowScript implements a sliding-window rate limiter in a single
// atomic Lua evaluation.  It uses two keys (previous + current window) and
// a weighted formula to approximate a true sliding window, avoiding the
// 2x burst problem of fixed-window counters.
//
// KEYS[1] = current window key
// KEYS[2] = previous window key
// ARGV[1] = limit (int)
// ARGV[2] = window size in seconds (int)
// ARGV[3] = current unix timestamp (int)
//
// Returns {allowed (0/1), total_count, ttl_seconds, prev_count, curr_count}.
// The two counts let a refusal say when the window reopens; the TTL is the
// counter key's expiry (twice the window), not that.
var slidingWindowScript = redis.NewScript(`
local curr_key  = KEYS[1]
local prev_key  = KEYS[2]
local limit     = tonumber(ARGV[1])
local window    = tonumber(ARGV[2])
local now       = tonumber(ARGV[3])

local elapsed   = now % window
local weight    = (window - elapsed) / window

local prev = tonumber(redis.call('GET', prev_key) or "0")
local curr = tonumber(redis.call('GET', curr_key) or "0")

-- Weighted total approximates a sliding window.
local total = math.floor(prev * weight) + curr

if total >= limit then
    -- Over limit — do NOT increment.
    local ttl = redis.call('TTL', curr_key)
    if ttl < 0 then ttl = window end
    return {0, total, ttl, prev, curr}
end

-- Under limit — increment current window.
curr = redis.call('INCR', curr_key)
if curr == 1 then
    redis.call('EXPIRE', curr_key, window * 2)
end

total = math.floor(prev * weight) + curr
local ttl = redis.call('TTL', curr_key)
if ttl < 0 then ttl = window end

return {1, total, ttl, prev, curr}
`)

const windowSeconds = 60 // 60-second windows for RPM

// RateLimiter implements rate limiting backed by Redis with automatic
// fallback to a local in-memory limiter when Redis is unavailable.
type RateLimiter struct {
	client *Client
	local  LocalLimiter
	prefix string
}

// LocalLimiter is satisfied by the in-memory ratelimit.RateLimiter.
type LocalLimiter interface {
	Allow(key string, limit int) (allowed bool, remaining int, resetSeconds int)
	AllowTokens(key string, limit int) (allowed bool, remaining int, resetSeconds int)
	AddTokens(key string, tokens int)
	Stop()
}

// NewRateLimiter creates a Redis-backed rate limiter.
// If client is nil, all calls go directly to the local fallback.
func NewRateLimiter(client *Client, local LocalLimiter, prefix string) *RateLimiter {
	if prefix == "" {
		prefix = "agentcc:rl:"
	}
	return &RateLimiter{client: client, local: local, prefix: prefix}
}

// Allow checks the rate limit. Returns (allowed, remaining, resetSeconds).
// On Redis failure, falls back to the local limiter.
func (r *RateLimiter) Allow(key string, limit int) (bool, int, int) {
	if r.client == nil || !r.client.Available() {
		return r.local.Allow(key, limit)
	}

	now := time.Now().Unix()
	windowStart := now - (now % windowSeconds)
	currKey := r.prefix + key + ":" + strconv.FormatInt(windowStart, 10)
	prevKey := r.prefix + key + ":" + strconv.FormatInt(windowStart-windowSeconds, 10)

	var result []int64
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	err := r.client.Do(func(rdb redis.UniversalClient) error {
		res, err := slidingWindowScript.Run(ctx, rdb,
			[]string{currKey, prevKey},
			limit, windowSeconds, now,
		).Int64Slice()
		if err != nil {
			return err
		}
		result = res
		return nil
	})

	if err != nil {
		slog.Debug("redis rate limit fallback to local", "key", key, "error", err)
		return r.local.Allow(key, limit)
	}

	if len(result) < 5 {
		return r.local.Allow(key, limit)
	}

	allowed := result[0] == 1
	total := int(result[1])
	ttl := int(result[2])

	remaining := limit - total
	if remaining < 0 {
		remaining = 0
	}
	if ttl < 1 {
		ttl = 1
	}

	// Write-through: also update local limiter so it stays warm for fallback.
	// We skip this for now — local will catch up on its own.

	if !allowed {
		// A refusal's reset is sent as Retry-After. The key's TTL is twice the
		// window from its first request, so it told callers to wait up to
		// 120 s for a window that reopens within about 60.
		return false, remaining, secondsUntilAdmitted(result[3], result[4], limit, now)
	}
	return allowed, remaining, ttl
}

// slidingTotal is the script's weighted count at a second elapsed into a
// window, computed with the same float arithmetic so the two agree exactly.
func slidingTotal(prev, curr, elapsed int64) int64 {
	weight := float64(windowSeconds-elapsed) / float64(windowSeconds)
	return int64(math.Floor(float64(prev)*weight)) + curr
}

// secondsUntilAdmitted is how long a request refused at unix second now must
// wait, with no other traffic, before the script admits it: later in this
// window as the previous window's weighted share decays, or in the next one,
// where this window's count becomes the previous one. A caller that waits
// exactly this long, as Retry-After tells it to, is admitted; one second less
// is refused.
func secondsUntilAdmitted(prev, curr int64, limit int, now int64) int {
	window := now / windowSeconds
	for wait := int64(1); wait < 3*windowSeconds; wait++ {
		at := now + wait
		elapsed := at % windowSeconds
		var total int64
		switch at / windowSeconds {
		case window:
			total = slidingTotal(prev, curr, elapsed)
		case window + 1:
			total = slidingTotal(curr, 0, elapsed)
		}
		if total < int64(limit) {
			return int(wait)
		}
	}
	return 2 * windowSeconds
}

// AllowTokens and AddTokens hold the token budget in the local limiter, so a
// token limit counts per pod rather than across the cluster. The sliding
// window above is a Lua script this package cannot run in tests without a
// Redis; a second one, counting a number that only the answering pod knows,
// would ship untested. Per-pod matches how providers.max_concurrent already
// behaves: with N replicas the effective limit is N times the configured one,
// which the deployment docs state.
func (r *RateLimiter) AllowTokens(key string, limit int) (bool, int, int) {
	return r.local.AllowTokens(key, limit)
}

func (r *RateLimiter) AddTokens(key string, tokens int) {
	r.local.AddTokens(key, tokens)
}

// Stop stops the local fallback limiter.
func (r *RateLimiter) Stop() {
	r.local.Stop()
}
