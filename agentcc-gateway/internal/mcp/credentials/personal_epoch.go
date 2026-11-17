package credentials

import (
	"context"

	"github.com/redis/go-redis/v9"
)

// Both epochs are permanent and in the token's Redis slot. The server epoch
// fences all users, including identities not yet present in the cache index.
// A pushed grant has no epoch: revision is its control-plane grant revision,
// fenced by the slot's revoked-through value instead (pushed_lifetime.go).
type personalEpoch struct {
	user, server string
	revision     int64
}

func (c *TokenCache) personalEpochKey(k Key) string {
	return c.prefix + cacheKeyPrefix + "{" + k.OrgID + "}:#user-epoch:" + k.UserID + ":" + k.ServerID
}
func (c *TokenCache) personalServerEpochKey(k Key) string {
	return c.prefix + cacheKeyPrefix + "{" + k.OrgID + "}:#personal-epoch:" + k.ServerID
}
func (c *TokenCache) personalEpoch(ctx context.Context, k Key) (personalEpoch, error) {
	e := personalEpoch{user: "0", server: "0"}
	err := c.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		values, err := rdb.MGet(ctx, c.personalEpochKey(k), c.personalServerEpochKey(k)).Result()
		if err != nil {
			return err
		}
		if values[0] != nil {
			e.user = values[0].(string)
		}
		if values[1] != nil {
			e.server = values[1].(string)
		}
		return nil
	})
	return e, err
}

// A comparison without a write is also the retained handle's dispatch fence.
// Cache value and membership enter Redis atomically with the epoch comparison.
var personalPublish = redis.NewScript(`
if (redis.call('GET', KEYS[1]) or '0') ~= ARGV[1] or
   (redis.call('GET', KEYS[2]) or '0') ~= ARGV[2] then return 0 end
if tonumber(ARGV[4]) > 0 then
 redis.call('SET', KEYS[3], ARGV[3], 'PX', ARGV[4])
 redis.call('SADD', KEYS[4], ARGV[5])
 redis.call('PEXPIRE', KEYS[4], ARGV[6])
end
return 1
`)

func (c *TokenCache) publishPersonal(ctx context.Context, k Key, tok Token, epoch personalEpoch, write bool) (bool, error) {
	var sealed string
	var ttlMillis int64
	if write {
		value, ttl, err := c.seal(k, tok)
		if err != nil {
			return false, err
		}
		sealed, ttlMillis = value, ttl.Milliseconds()
	}
	var accepted int64
	err := c.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		accepted, err = personalPublish.Run(ctx, rdb, []string{
			c.personalEpochKey(k), c.personalServerEpochKey(k), c.RedisKey(k), c.indexKey(k.OrgID, k.ServerID),
		}, epoch.user, epoch.server, sealed, ttlMillis, k.UserID, c.maxTTL.Milliseconds()).Int64()
		return err
	})
	return accepted == 1, err
}

var purgePersonal = redis.NewScript(`
redis.call('INCR', KEYS[1])
local n = redis.call('DEL', KEYS[2])
redis.call('SREM', KEYS[3], ARGV[1])
redis.call('PUBLISH', ARGV[2], KEYS[1])
return n
`)

// Reading the index, advancing epochs and deleting tokens must be atomic: a
// concurrent publication cannot escape the purge's snapshot of indexed users.
// Naming no revision, it revokes every pushed grant admitted so far.
var purgeServer = redis.NewScript(`
redis.call('INCR', KEYS[1])
redis.call('INCR', KEYS[2])
local admitted = redis.call('GET', KEYS[6])
if admitted and tonumber(admitted) > tonumber(redis.call('GET', KEYS[5]) or '0') then
 redis.call('SET', KEYS[5], admitted)
end
local n = redis.call('DEL', KEYS[3])
for _, user in ipairs(redis.call('SMEMBERS', KEYS[4])) do
 n = n + redis.call('DEL', ARGV[1] .. user .. ':' .. ARGV[2])
end
redis.call('DEL', KEYS[4])
redis.call('PUBLISH', ARGV[3], KEYS[1])
return n
`)
