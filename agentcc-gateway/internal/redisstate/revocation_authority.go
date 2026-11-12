package redisstate

import (
	"context"
	"github.com/redis/go-redis/v9"
)

// This shares the existing revocation channel's deployment/Redis DB scope.
// Tombstones contain credential SHA-256 hashes, never slot IDs or raw keys.
// The versioned namespace never interprets legacy ID-only markers as hashes.
const keyRevocationTombstones = "agentcc:key:revoked:sha256:v1"

// RevocationFreshnessError intentionally retains no Redis or key values.
type RevocationFreshnessError struct{}

func (*RevocationFreshnessError) Error() string { return "revocation freshness unavailable" }

// CheckKeyRevocation reads the authoritative tombstone for each cached active
// authentication. Pub/sub remains the immediate local invalidation path, but
// a disconnect, transparent reconnect or lost message cannot provide freshness.
// Callers must refuse errors and expired results, not reuse cached active state.
// Redis persistence/no-eviction is required; this is not a data-loss recovery
// protocol. Read from the authoritative primary, never an eventually stale replica.
func (b *KeyRevocationBroadcaster) CheckKeyRevocation(ctx context.Context, keyHash string) (bool, error) {
	if b == nil || b.client == nil || !validRevocationHash(keyHash) || ctx.Err() != nil {
		return false, &RevocationFreshnessError{}
	}
	var revoked bool
	err := b.client.Do(func(rdb redis.UniversalClient) error {
		var err error
		revoked, err = rdb.SIsMember(ctx, keyRevocationTombstones, keyHash).Result()
		return err
	})
	if err != nil || ctx.Err() != nil {
		return false, &RevocationFreshnessError{}
	}
	return revoked, nil
}
