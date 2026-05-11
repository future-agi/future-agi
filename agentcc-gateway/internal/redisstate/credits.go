package redisstate

import (
	"context"
	"log/slog"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"
)

// CreditStore provides Redis-backed credit balance operations.
// Balances are stored as integer microdollars (1 USD = 1,000,000).
type CreditStore struct {
	client *Client
	prefix string
}

// NewCreditStore creates a Redis-backed credit store.
func NewCreditStore(client *Client, prefix string) *CreditStore {
	if prefix == "" {
		prefix = "agentcc:credits:"
	}
	return &CreditStore{client: client, prefix: prefix}
}

// deductScript atomically checks balance and deducts.
// Returns the new balance, or -1 if insufficient funds.
//
// KEYS[1] = credit key
// ARGV[1] = amount in microdollars to deduct
var deductScript = redis.NewScript(`
local key = KEYS[1]
local amount = tonumber(ARGV[1])
local balance = tonumber(redis.call('GET', key) or "0")
if balance <= 0 then
    return -1
end
return redis.call('DECRBY', key, amount)
`)

// GetBalance returns the credit balance in microdollars.
// Returns (-1, false) if Redis is unavailable.
func (s *CreditStore) GetBalance(keyID string) (int64, bool) {
	if s.client == nil || !s.client.Available() {
		return -1, false
	}

	var balance int64
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	err := s.client.Do(func(rdb redis.UniversalClient) error {
		val, err := rdb.Get(ctx, s.prefix+keyID).Result()
		if err == redis.Nil {
			balance = 0
			return nil
		}
		if err != nil {
			return err
		}
		balance, err = strconv.ParseInt(val, 10, 64)
		return err
	})

	if err != nil {
		return -1, false
	}
	return balance, true
}

// DeductMicros atomically checks balance and deducts microdollars.
// Returns (newBalance, true) on success, or (-1, false) if unavailable.
// Returns the raw Redis result even if balance goes negative (caller decides).
func (s *CreditStore) DeductMicros(keyID string, amount int64) (int64, bool) {
	if s.client == nil || !s.client.Available() {
		return -1, false
	}

	var result int64
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	err := s.client.Do(func(rdb redis.UniversalClient) error {
		res, err := deductScript.Run(ctx, rdb,
			[]string{s.prefix + keyID},
			amount,
		).Int64()
		if err != nil {
			return err
		}
		result = res
		return nil
	})

	if err != nil {
		slog.Debug("redis credit deduct failed", "key", keyID, "error", err)
		return -1, false
	}
	return result, true
}

// ForceDeductMicros unconditionally deducts microdollars (DECRBY) without
// checking the current balance. Use this in post-response paths where the
// request already completed and spend must be recorded regardless of balance.
// Returns (newBalance, true) on success, or (-1, false) if Redis unavailable.
func (s *CreditStore) ForceDeductMicros(keyID string, amount int64) (int64, bool) {
	if s.client == nil || !s.client.Available() {
		return -1, false
	}

	var result int64
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	err := s.client.Do(func(rdb redis.UniversalClient) error {
		res, err := rdb.DecrBy(ctx, s.prefix+keyID, amount).Result()
		if err != nil {
			return err
		}
		result = res
		return nil
	})

	if err != nil {
		slog.Debug("redis credit force-deduct failed", "key", keyID, "error", err)
		return -1, false
	}
	return result, true
}

// AddMicros atomically adds microdollars and returns the new balance.
func (s *CreditStore) AddMicros(keyID string, amount int64) (int64, bool) {
	if s.client == nil || !s.client.Available() {
		return -1, false
	}

	var result int64
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	err := s.client.Do(func(rdb redis.UniversalClient) error {
		res, err := rdb.IncrBy(ctx, s.prefix+keyID, amount).Result()
		if err != nil {
			return err
		}
		result = res
		return nil
	})

	if err != nil {
		slog.Debug("redis credit add failed", "key", keyID, "error", err)
		return -1, false
	}
	return result, true
}

// SetBalance sets the credit balance for a key (used for seeding).
func (s *CreditStore) SetBalance(keyID string, micros int64) bool {
	if s.client == nil || !s.client.Available() {
		return false
	}

	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	err := s.client.Do(func(rdb redis.UniversalClient) error {
		return rdb.Set(ctx, s.prefix+keyID, micros, 0).Err()
	})
	return err == nil
}

// SeedBalance sets the credit balance only if the key does not already exist
// in Redis (SETNX). Used at startup to populate Redis from Postgres without
// overwriting balances that real traffic may have modified.
// Returns true if the value was set (key was new), false otherwise.
func (s *CreditStore) SeedBalance(keyID string, micros int64) bool {
	if s.client == nil || !s.client.Available() {
		return false
	}

	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	var seeded bool
	err := s.client.Do(func(rdb redis.UniversalClient) error {
		ok, err := rdb.SetNX(ctx, s.prefix+keyID, micros, 0).Result()
		if err != nil {
			return err
		}
		seeded = ok
		return nil
	})

	if err != nil {
		slog.Debug("redis credit seed failed", "key", keyID, "error", err)
		return false
	}
	return seeded
}

// Available returns true if Redis is reachable.
func (s *CreditStore) Available() bool {
	return s.client != nil && s.client.Available()
}
