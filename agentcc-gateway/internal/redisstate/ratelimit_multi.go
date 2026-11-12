package redisstate

import (
	"context"
	"log/slog"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"
)

// LimitCheck is one counter a request must fit under.
// Key, Limit and Cost are inputs; Remaining and ResetSeconds are written by
// AllowN.
type LimitCheck struct {
	// Key names the counter, without the limiter's prefix or window. The
	// per-key RPM counter is the key id, as Allow keys it, so the two paths
	// share one count.
	Key string
	// Limit is the most the sliding minute may hold.
	Limit int64
	// Cost is what an admitted request adds: 1 for a request limit, the
	// request's token estimate for a token limit.
	Cost int64

	// Remaining is what is left under Limit once the request is counted (or,
	// for the check that refused, before it), never below 0.
	Remaining int64
	// ResetSeconds is when the counter's window rolls, at least 1.
	ResetSeconds int
}

// LimitDecision is what AllowN decided.
type LimitDecision struct {
	// Allowed is true when every check fitted and each Cost was added.
	Allowed bool
	// Refused is the index of the first check that did not fit (-1 when
	// allowed). A refused request adds to no counter.
	Refused int
	// Window identifies where the costs were added; Settle takes it back.
	Window int64
	// Local is true when the local fallback decided, so Settle goes there.
	Local bool
}

// MultiLimiter admits a request only when it fits under every check, and
// counts it against all of them, in one step. Both the in-memory limiter and
// the Redis-backed RateLimiter implement it.
type MultiLimiter interface {
	AllowN(checks []LimitCheck) LimitDecision
	// Settle adds delta (negative to give back) to the counters keys in the
	// window AllowN reported, never taking one below 0. A window that has
	// already expired is left alone.
	Settle(keys []string, window int64, delta int64, local bool)
}

// multiWindowScript is slidingWindowScript over several counters at once:
// it admits the request only when every counter's weighted total is under its
// limit, and only then adds each counter's cost.
//
// KEYS[2i-1], KEYS[2i] = counter i's current and previous window keys
// ARGV[1] = window seconds, ARGV[2] = now (unix seconds)
// ARGV[1+2i], ARGV[2+2i] = counter i's limit and cost
//
// Returns {0, i, total_i} when counter i refused, else
// {1, 0, total_1 + cost_1, ..., total_n + cost_n}.
var multiWindowScript = redis.NewScript(`
local window = tonumber(ARGV[1])
local now    = tonumber(ARGV[2])
local weight = (window - (now % window)) / window
local n      = #KEYS / 2

local totals = {}
for i = 1, n do
    local curr  = tonumber(redis.call('GET', KEYS[2*i-1]) or "0")
    local prev  = tonumber(redis.call('GET', KEYS[2*i]) or "0")
    local total = math.floor(prev * weight) + curr
    if total >= tonumber(ARGV[1 + 2*i]) then
        return {0, i, total}
    end
    totals[i] = total
end

-- Every counter is created, a cost of 0 included (a token hold with no
-- estimate): the request's settle adds its usage to this key.
local out = {1, 0}
for i = 1, n do
    local cost = tonumber(ARGV[2 + 2*i])
    local key  = KEYS[2*i-1]
    redis.call('INCRBY', key, cost)
    if redis.call('TTL', key) < 0 then
        redis.call('EXPIRE', key, window * 2)
    end
    out[#out + 1] = totals[i] + cost
end
return out
`)

// rateSettleScript adds ARGV[1] to each existing key, never below 0, keeping its
// expiry. A key that has expired is not recreated.
var rateSettleScript = redis.NewScript(`
for i = 1, #KEYS do
    if redis.call('EXISTS', KEYS[i]) == 1 then
        local v = redis.call('INCRBY', KEYS[i], ARGV[1])
        if v < 0 then
            redis.call('INCRBY', KEYS[i], -v)
        end
    end
end
return 1
`)

// AllowN checks every counter on Redis in one round trip, or on the local
// limiter when Redis is unavailable or fails.
func (r *RateLimiter) AllowN(checks []LimitCheck) LimitDecision {
	if r.client == nil || !r.client.Available() {
		return r.localAllowN(checks)
	}

	now := time.Now().Unix()
	windowStart := now - (now % windowSeconds)
	curr := ":" + strconv.FormatInt(windowStart, 10)
	prev := ":" + strconv.FormatInt(windowStart-windowSeconds, 10)
	keys := make([]string, 0, 2*len(checks))
	args := make([]interface{}, 0, 2+2*len(checks))
	args = append(args, windowSeconds, now)
	for _, c := range checks {
		keys = append(keys, r.prefix+c.Key+curr, r.prefix+c.Key+prev)
		args = append(args, c.Limit, c.Cost)
	}

	var result []int64
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	err := r.client.Do(func(rdb redis.UniversalClient) error {
		res, err := multiWindowScript.Run(ctx, rdb, keys, args...).Int64Slice()
		if err != nil {
			return err
		}
		result = res
		return nil
	})
	if err != nil {
		slog.Debug("redis rate limit fallback to local", "checks", len(checks), "error", err)
		return r.localAllowN(checks)
	}

	reset := int(windowSeconds - (now % windowSeconds))
	if reset < 1 {
		reset = 1
	}
	switch {
	case len(result) == 3 && result[0] == 0 && result[1] >= 1 && int(result[1]) <= len(checks):
		i := int(result[1]) - 1
		checks[i].Remaining = nonNegative(checks[i].Limit - result[2])
		checks[i].ResetSeconds = reset
		return LimitDecision{Refused: i, Window: windowStart}
	case len(result) == 2+len(checks) && result[0] == 1:
		for i := range checks {
			checks[i].Remaining = nonNegative(checks[i].Limit - result[2+i])
			checks[i].ResetSeconds = reset
		}
		return LimitDecision{Allowed: true, Refused: -1, Window: windowStart}
	}
	slog.Debug("redis rate limit: unexpected script reply, fallback to local", "reply", result)
	return r.localAllowN(checks)
}

// Settle adjusts the counters a reservation was added to, on the store that
// took it: Redis for a decision AllowN made there, the local limiter for one
// it made locally.
func (r *RateLimiter) Settle(keys []string, window int64, delta int64, local bool) {
	if delta == 0 || len(keys) == 0 {
		return
	}
	if local {
		if m, ok := r.local.(MultiLimiter); ok {
			m.Settle(keys, window, delta, true)
		}
		return
	}
	if r.client == nil {
		return
	}
	suffix := ":" + strconv.FormatInt(window, 10)
	full := make([]string, len(keys))
	for i, k := range keys {
		full[i] = r.prefix + k + suffix
	}
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	err := r.client.Do(func(rdb redis.UniversalClient) error {
		return rateSettleScript.Run(ctx, rdb, full, delta).Err()
	})
	if err != nil {
		// The reservation stays counted until its window expires.
		slog.Debug("redis rate limit settle failed", "keys", len(keys), "delta", delta, "error", err)
	}
}

// localAllowN is AllowN on the local fallback. A local limiter that cannot
// check several counters at once is asked one counter at a time.
func (r *RateLimiter) localAllowN(checks []LimitCheck) LimitDecision {
	if m, ok := r.local.(MultiLimiter); ok {
		d := m.AllowN(checks)
		d.Local = true
		return d
	}
	for i := range checks {
		allowed, remaining, reset := r.local.Allow(checks[i].Key, int(checks[i].Limit))
		checks[i].Remaining, checks[i].ResetSeconds = int64(remaining), reset
		if !allowed {
			return LimitDecision{Refused: i, Local: true}
		}
	}
	return LimitDecision{Allowed: true, Refused: -1, Local: true}
}

func nonNegative(n int64) int64 {
	if n < 0 {
		return 0
	}
	return n
}
