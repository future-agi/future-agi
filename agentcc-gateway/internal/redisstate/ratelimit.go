package redisstate

import (
	"context"
	"log/slog"
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
// Returns {allowed (0/1), total_count, ttl_seconds}.
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
    return {0, total, ttl}
end

-- Under limit — increment current window.
curr = redis.call('INCR', curr_key)
if curr == 1 then
    redis.call('EXPIRE', curr_key, window * 2)
end

total = math.floor(prev * weight) + curr
local ttl = redis.call('TTL', curr_key)
if ttl < 0 then ttl = window end

return {1, total, ttl}
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

	if len(result) < 3 {
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

	return allowed, remaining, ttl
}

// Stop stops the local fallback limiter.
func (r *RateLimiter) Stop() {
	r.local.Stop()
}
