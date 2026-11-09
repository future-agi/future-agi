package credentials

import (
	"context"
	"errors"

	"github.com/redis/go-redis/v9"
)

// The epoch lives in the same Redis slot as the shared token. It deliberately
// has no TTL: an arbitrarily delayed fetch must never see its epoch reused.
func (c *TokenCache) sharedEpochKey(k Key) string {
	return c.prefix + cacheKeyPrefix + "{" + k.OrgID + "}:#shared-epoch:" + k.ServerID
}

func (c *TokenCache) sharedEpoch(ctx context.Context, k Key) (string, error) {
	epoch := "0"
	err := c.do(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		v, err := rdb.Get(ctx, c.sharedEpochKey(k)).Result()
		if errors.Is(err, redis.Nil) {
			return nil
		}
		if err == nil {
			epoch = v
		}
		return err
	})
	return epoch, err
}

// Checking the epoch and publishing the sealed value are one Redis operation,
// ordered against Purge's increment-and-delete even on another gateway process.
var sharedPublish = redis.NewScript(`
if (redis.call('GET', KEYS[1]) or '0') ~= ARGV[1] then return 0 end
if tonumber(ARGV[3]) > 0 then
 redis.call('SET', KEYS[2], ARGV[2], 'PX', ARGV[3])
end
return 1
`)

func (c *TokenCache) publishShared(ctx context.Context, k Key, tok Token, epoch string, write bool) (bool, error) {
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
		accepted, err = sharedPublish.Run(ctx, rdb, []string{c.sharedEpochKey(k), c.RedisKey(k)}, epoch, sealed, ttlMillis).Int64()
		return err
	})
	return accepted == 1, err
}

// token resolves shared grants only. Both flight identity and publication carry
// a local generation plus the distributed epoch. A post-purge call cannot join
// a pre-purge flight; its late response can neither overwrite Redis nor escape
// to a caller. No lock spans the authority fetch (which may itself evict).
func (r *Resolver) token(ctx context.Context, k Key) (Token, error) {
	u := r.users.get(k)
	u.mu.Lock()
	generation := u.generation
	u.mu.Unlock()
	epoch := "0"
	if r.cache != nil {
		var err error
		epoch, err = r.cache.sharedEpoch(ctx, k)
		if err != nil {
			return Token{}, err
		}
	}
	flightKey := tokenFlightKey{key: k, generation: generation, epoch: epoch}
	tok, err := r.flights.do(flightKey, func() (Token, error) {
		fctx := context.WithoutCancel(ctx)
		if r.cache != nil {
			tok, ok, err := r.cache.Get(fctx, k)
			if err != nil {
				return Token{}, err
			}
			if ok {
				return r.publishSharedToken(fctx, k, tok, u, generation, epoch, false)
			}
		}
		tok, err := r.tokens.Fetch(fctx, k)
		if err != nil {
			return Token{}, err
		}
		return r.publishSharedToken(fctx, k, tok, u, generation, epoch, true)
	})
	if err != nil {
		return Token{}, err
	}
	// A waiting caller must check again: eviction may have completed since the
	// flight published its result, before this waiter resumed.
	return r.publishSharedToken(ctx, k, tok, u, generation, epoch, false)
}

func (r *Resolver) publishSharedToken(ctx context.Context, k Key, tok Token, u *userLifetime, generation uint64, epoch string, write bool) (Token, error) {
	u.mu.Lock()
	stale := u.generation != generation
	u.mu.Unlock()
	if stale {
		return Token{}, &Refusal{Reason: RefusalNeedsReauth}
	}
	if !tok.ExpiresAt.After(r.now()) {
		return Token{}, &Refusal{Reason: RefusalExpired}
	}
	if tok.AccessToken == "" {
		return Token{}, ErrTokenChannel
	}
	if r.cache != nil {
		ok, err := r.cache.publishShared(ctx, k, tok, epoch, write)
		if err != nil {
			return Token{}, err
		}
		if !ok {
			return Token{}, &Refusal{Reason: RefusalNeedsReauth}
		}
	}
	// Redis publication is atomic against epoch advancement. Local fallback
	// invalidation can also have happened during I/O; it cannot revive a grant.
	u.mu.Lock()
	stale = u.generation != generation
	u.mu.Unlock()
	if stale {
		return Token{}, &Refusal{Reason: RefusalNeedsReauth}
	}
	if !tok.ExpiresAt.After(r.now()) {
		return Token{}, &Refusal{Reason: RefusalExpired}
	}
	return tok, nil
}
