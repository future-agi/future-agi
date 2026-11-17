package oauthrs

import (
	"context"
	"errors"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// RedisKey is the one global key the pushed key set lives under (MCP-PLAN
// section 7, mcpoauth:keys).
const RedisKey = "mcpoauth:keys"

// redisTimeout bounds each Redis call.
const redisTimeout = 500 * time.Millisecond

// saveIfNewerScript stores ARGV[2] unless the stored document's version is at
// least ARGV[1], in one step, so two replicas taking pushes at once can never
// leave an older set over a newer one.
var saveIfNewerScript = redis.NewScript(`
local cur = redis.call('GET', KEYS[1])
if cur then
  local ok, doc = pcall(cjson.decode, cur)
  if ok and type(doc) == 'table' and tonumber(doc.version) and tonumber(doc.version) >= tonumber(ARGV[1]) then
    return 0
  end
end
redis.call('SET', KEYS[1], ARGV[2])
return 1
`)

// RedisKeyStore keeps the key set in Redis, shared by every replica.
type RedisKeyStore struct {
	client *redisstate.Client
	key    string
}

// NewRedisKeyStore returns the store on client, with prefix ahead of RedisKey
// ("" keeps the plan's name).
func NewRedisKeyStore(client *redisstate.Client, prefix string) *RedisKeyStore {
	return &RedisKeyStore{client: client, key: prefix + RedisKey}
}

var errNoRedis = errors.New("mcp oauth keys: no Redis client")

// do runs fn with a context detached from the caller's cancellation and
// bounded by redisTimeout, so a client that went away does not count as a
// Redis failure against the breaker every store shares.
func (s *RedisKeyStore) do(ctx context.Context, fn func(ctx context.Context, rdb redis.UniversalClient) error) error {
	if s == nil || s.client == nil {
		return errNoRedis
	}
	ctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), redisTimeout)
	defer cancel()
	return s.client.Do(func(rdb redis.UniversalClient) error { return fn(ctx, rdb) })
}

// Load returns the stored document, nil when there is none.
func (s *RedisKeyStore) Load(ctx context.Context) ([]byte, error) {
	var out []byte
	err := s.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		v, err := rdb.Get(ctx, s.key).Bytes()
		if errors.Is(err, redis.Nil) {
			return nil
		}
		out = v
		return err
	})
	return out, err
}

// SaveIfNewer stores doc unless the stored version is at least version.
func (s *RedisKeyStore) SaveIfNewer(ctx context.Context, version int64, doc []byte) (bool, error) {
	var saved bool
	err := s.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		n, err := saveIfNewerScript.Run(ctx, rdb, []string{s.key}, strconv.FormatInt(version, 10), doc).Int()
		saved = n == 1
		return err
	})
	return saved, err
}
