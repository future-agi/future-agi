package redisstate

import (
	"context"

	"github.com/redis/go-redis/v9"
)

// Retained indefinitely. An unresolved historical slot cannot safely be
// translated to its current occupant, nor may cutover silently allow that ID.
const legacyKeyRevocationTombstones = "agentcc:key:revoked"

// CheckCredentialRevocation dual-reads historical IDs and credential hashes.
// It protects admission before, during and after the one-time data migration,
// even when publication was lost or the migration stopped after a partial write.
func (b *KeyRevocationBroadcaster) CheckCredentialRevocation(ctx context.Context, keyID, keyHash string) (bool, error) {
	if b == nil || b.client == nil || keyID == "" || !validRevocationHash(keyHash) || ctx.Err() != nil {
		return false, &RevocationFreshnessError{}
	}
	var revoked bool
	err := b.client.Do(func(rdb redis.UniversalClient) error {
		var err error
		revoked, err = rdb.SIsMember(ctx, legacyKeyRevocationTombstones, keyID).Result()
		if err != nil || revoked {
			return err
		}
		revoked, err = rdb.SIsMember(ctx, keyRevocationTombstones, keyHash).Result()
		return err
	})
	if err != nil || ctx.Err() != nil {
		return false, &RevocationFreshnessError{}
	}
	return revoked, nil
}
