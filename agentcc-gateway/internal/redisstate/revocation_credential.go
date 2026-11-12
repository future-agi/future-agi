package redisstate

import (
	"context"
	"log/slog"

	"github.com/redis/go-redis/v9"
)

// PublishCredentialWithError persists the credential's SHA-256 identity before
// delivery. The ID is for diagnostics only; it is never a durable marker or a
// known-credential delivery payload. Raw credentials never enter this API.
func (b *KeyRevocationBroadcaster) PublishCredentialWithError(ctx context.Context, keyID, keyHash string) error {
	if !validRevocationHash(keyHash) {
		// An absent local ID cannot be safely turned into a permanent identity.
		// Preserve the legacy broadcast attempt, but never claim durable success.
		if err := b.PublishWithError(ctx, keyID); err != nil {
			return err
		}
		return credentialPublishFailure(keyID)
	}
	if b != nil && b.client != nil && ctx.Err() == nil {
		err := b.client.Do(func(rdb redis.UniversalClient) error {
			// No expiry: a disconnected/restarted reader must not reopen a key.
			if err := rdb.SAdd(ctx, keyRevocationTombstones, keyHash).Err(); err != nil {
				return err
			}
			return rdb.Publish(ctx, keyRevocationChannel, "sha256:"+keyHash).Err()
		})
		if err == nil && ctx.Err() == nil {
			return nil
		}
	}
	return credentialPublishFailure(keyID)
}

func credentialPublishFailure(keyID string) error {
	failure := &RevocationPublishError{}
	slog.Warn("key revocation not broadcast", "key_id", keyID, "error", failure)
	return failure
}

func validRevocationHash(hash string) bool {
	if len(hash) != 64 {
		return false
	}
	for _, c := range hash {
		if !(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'f') {
			return false
		}
	}
	return true
}
