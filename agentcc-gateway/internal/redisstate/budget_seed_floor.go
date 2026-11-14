package redisstate

import (
	"context"
	"math"
	"time"

	"github.com/redis/go-redis/v9"
)

// SeedSpendFloor never adds a second copy of spend already recorded directly
// (notably video settlement), nor lowers a live counter or removes receipts.
// Updating the total, model floors and TTL is atomic with reservations.
func (s *BudgetStore) SeedSpendFloor(org, level, key, period string, total float64, models map[string]float64) bool {
	if s.client == nil || !s.client.Available() {
		return false
	}
	valid := func(n float64) bool {
		return n >= 0 && !math.IsNaN(n) && !math.IsInf(n, 0) && n < float64(math.MaxInt64)/microUnit
	}
	if !valid(total) {
		return false
	}
	args := []any{int64(budgetTTL(period) / time.Second), "total", usdToMicros(total)}
	for model, spend := range models {
		if !valid(spend) {
			return false
		}
		args = append(args, "model:"+model, usdToMicros(spend))
	}
	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()
	return s.client.Do(func(c redis.UniversalClient) error {
		return seedSpendFloor.Run(ctx, c, []string{s.budgetKey(org, level, key, period)}, args...).Err()
	}) == nil
}

var seedSpendFloor = redis.NewScript(`
local typ=redis.call('TYPE',KEYS[1]).ok
if typ ~= 'none' and typ ~= 'hash' then return redis.error_reply('budget key type mismatch') end
for i=2,#ARGV,2 do
 local current=tonumber(redis.call('HGET',KEYS[1],ARGV[i]) or '0')
 if not current then return redis.error_reply('budget value invalid') end
end
for i=2,#ARGV,2 do
 local current=tonumber(redis.call('HGET',KEYS[1],ARGV[i]) or '0')
 if tonumber(ARGV[i+1])>current then redis.call('HSET',KEYS[1],ARGV[i],ARGV[i+1]) end
end
if tonumber(ARGV[1])>0 and redis.call('TTL',KEYS[1])<0 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end
return 1
`)
