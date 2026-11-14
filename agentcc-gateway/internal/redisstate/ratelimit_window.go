package redisstate

import (
	"context"
	"errors"
	"github.com/redis/go-redis/v9"
	"time"
)

var rollingWindowScript = redis.NewScript(`
local t=redis.call('TIME')
local now=tonumber(t[1])*1000000+tonumber(t[2])
local window=tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',now-window)
if redis.call('ZCARD',KEYS[1]) >= tonumber(ARGV[1]) then
 local first=redis.call('ZRANGE',KEYS[1],0,0,'WITHSCORES')
 return {0,math.max(1,tonumber(first[2])+window-now)}
end
local seq=redis.call('INCR',KEYS[2])
redis.call('ZADD',KEYS[1],now,seq)
redis.call('PEXPIRE',KEYS[1],math.ceil(window/1000)+1000)
redis.call('PEXPIRE',KEYS[2],math.ceil(window/1000)+1000)
return {1,0}
`)

// AllowWindow is fail-closed and uses Redis time. Video account QPS and list
// limits must not fall back to process-local counters during a Redis outage.
func (r *RateLimiter) AllowWindow(ctx context.Context, key string, limit int, window time.Duration) (bool, time.Duration, error) {
	if r.client == nil || limit < 1 || window <= 0 {
		return false, 0, errors.New("rate limiter unavailable")
	}
	var out []int64
	err := r.client.Do(func(c redis.UniversalClient) error {
		var e error
		out, e = rollingWindowScript.Run(ctx, c, []string{r.prefix + key, r.prefix + key + ":seq"}, limit, window.Microseconds()).Int64Slice()
		return e
	})
	if err != nil {
		return false, 0, err
	}
	if len(out) != 2 {
		return false, 0, errors.New("invalid rate limit reply")
	}
	return out[0] == 1, time.Duration(out[1]) * time.Microsecond, nil
}
