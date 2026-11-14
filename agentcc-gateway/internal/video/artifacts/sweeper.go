package artifacts

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"strconv"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/redis/go-redis/v9"
)

// Sweeper uses a separate artifact expire zset, since the job store's expire
// zset governs metadata retention. Batch 3 schedules keys after copy and supplies
// an idempotent, fenced callback to mark the owning job's artifact expired.
// Keys must not be reused for new artifacts. Track does not extend retention.
type Sweeper struct {
	client *redisstate.Client
	store  Store
	prefix string
}

func NewSweeper(c *redisstate.Client, s Store, prefix string) *Sweeper {
	if prefix == "" {
		prefix = "video:v1:artifacts:"
	}
	return &Sweeper{client: c, store: s, prefix: prefix}
}
func (s *Sweeper) do(fn func(redis.UniversalClient) error) error {
	if s.client == nil {
		return errors.New("artifact expiry store unavailable")
	}
	return s.client.Do(fn)
}
func (s *Sweeper) Track(ctx context.Context, key string, expiry time.Time) error {
	if !validKey(key) {
		return ErrKey
	}
	if expiry.IsZero() {
		return fmt.Errorf("artifact expiry required")
	}
	return s.do(func(c redis.UniversalClient) error {
		return c.ZAddNX(ctx, s.prefix+"expire", redis.Z{Score: float64(expiry.UnixMilli()), Member: key}).Err()
	})
}

const sweepRemove = `if redis.call('GET',KEYS[1]) ~= ARGV[1] then return 0 end
if redis.call('ZSCORE',KEYS[2],ARGV[2]) == ARGV[3] then return redis.call('ZREM',KEYS[2],ARGV[2]) end
return 0`
const sweepUnlock = `if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('DEL',KEYS[1]) end return 0`

// Sweep is one bounded pass, not a background worker. Callback must honor ctx
// and succeed only after persisting the expired state. Failed passes stay due.
func (s *Sweeper) Sweep(ctx context.Context, limit int, markExpired func(context.Context, string) error) (int, error) {
	if limit <= 0 || markExpired == nil {
		return 0, fmt.Errorf("positive sweep limit and expiry callback required")
	}
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	var nonce [16]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return 0, err
	}
	token := hex.EncodeToString(nonce[:])
	lease := s.prefix + "lease"
	index := s.prefix + "expire"
	var held bool
	if err := s.do(func(c redis.UniversalClient) error {
		var e error
		held, e = c.SetNX(ctx, lease, token, 30*time.Second).Result()
		return e
	}); err != nil {
		return 0, err
	}
	if !held {
		return 0, nil
	}
	defer func() {
		cleanup, stop := context.WithTimeout(context.Background(), time.Second)
		defer stop()
		_ = s.do(func(c redis.UniversalClient) error {
			return redis.NewScript(sweepUnlock).Run(cleanup, c, []string{lease}, token).Err()
		})
	}()
	var due []redis.Z
	if err := s.do(func(c redis.UniversalClient) error {
		var e error
		due, e = c.ZRangeByScoreWithScores(ctx, index, &redis.ZRangeBy{Min: "-inf", Max: strconv.FormatInt(time.Now().UnixMilli(), 10), Count: int64(limit)}).Result()
		return e
	}); err != nil {
		return 0, err
	}
	count := 0
	for _, entry := range due {
		if err := ctx.Err(); err != nil {
			return count, err
		}
		key, ok := entry.Member.(string)
		if !ok || !validKey(key) {
			return count, ErrKey
		}
		if err := s.store.Delete(ctx, key); err != nil {
			return count, err
		}
		if err := markExpired(ctx, key); err != nil {
			return count, err
		}
		var removed int64
		if err := s.do(func(c redis.UniversalClient) error {
			var e error
			removed, e = redis.NewScript(sweepRemove).Run(ctx, c, []string{lease, index}, token, key, strconv.FormatInt(int64(entry.Score), 10)).Int64()
			return e
		}); err != nil {
			return count, err
		}
		if removed != 1 {
			return count, fmt.Errorf("artifact sweep lease lost")
		}
		count++
	}
	return count, nil
}
