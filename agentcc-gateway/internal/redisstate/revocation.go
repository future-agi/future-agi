package redisstate

import (
	"context"
	"log/slog"
	"time"

	"github.com/redis/go-redis/v9"
)

const keyRevocationChannel = "agentcc:key:revoke"

// KeyRevocationBroadcaster uses Redis pub/sub to broadcast key revocations
// across all gateway replicas. When one replica revokes a key via the admin
// API, all other replicas receive the message and revoke it locally — removing
// the 15-second periodic sync window.
type KeyRevocationBroadcaster struct {
	client *Client
}

// NewKeyRevocationBroadcaster creates a new broadcaster backed by the given Redis client.
func NewKeyRevocationBroadcaster(client *Client) *KeyRevocationBroadcaster {
	return &KeyRevocationBroadcaster{client: client}
}

// RevocationPublishError reports a failed broadcast without retaining key,
// connection, or underlying transport error values.
type RevocationPublishError struct{}

func (*RevocationPublishError) Error() string { return "revocation not broadcast" }

// Publish preserves the log-only publisher interface. Call PublishWithError
// when the caller must also report a failed broadcast to its client.
func (b *KeyRevocationBroadcaster) Publish(ctx context.Context, keyID string) {
	_ = b.PublishWithError(ctx, keyID)
}

// PublishWithError preserves legacy ID-only notification and sanitized errors.
// It never persists an ID: configuration slots can be reused. Production admin
// revocation uses PublishCredentialWithError for durable credential delivery.
func (b *KeyRevocationBroadcaster) PublishWithError(ctx context.Context, keyID string) error {
	if b != nil && b.client != nil {
		if err := b.client.Do(func(rdb redis.UniversalClient) error {
			return rdb.Publish(ctx, keyRevocationChannel, keyID).Err()
		}); err == nil {
			return nil
		}
	}
	failure := &RevocationPublishError{}
	slog.Warn("key revocation not broadcast", "key_id", keyID, "error", failure)
	return failure
}

// Subscribe starts a blocking loop that listens for key revocation messages
// and calls revokeFn for each one. Call this in a goroutine.
// Automatically reconnects on connection drops. Exits when ctx is cancelled.
func (b *KeyRevocationBroadcaster) Subscribe(ctx context.Context, revokeFn func(keyID string)) {
	for {
		if ctx.Err() != nil {
			slog.Info("key revocation subscriber stopped")
			return
		}
		b.subscribeOnce(ctx, revokeFn)
		// subscribeOnce returned — channel closed or error. Wait before reconnecting.
		slog.Warn("key revocation subscription lost, reconnecting in 2s")
		select {
		case <-ctx.Done():
			return
		case <-time.After(2 * time.Second):
		}
	}
}

func (b *KeyRevocationBroadcaster) subscribeOnce(ctx context.Context, revokeFn func(keyID string)) {
	rdb := b.client.Redis()
	sub := rdb.Subscribe(ctx, keyRevocationChannel)
	defer sub.Close()

	ch := sub.Channel()
	slog.Info("key revocation subscriber started", "channel", keyRevocationChannel)

	for {
		select {
		case <-ctx.Done():
			return
		case msg, ok := <-ch:
			if !ok {
				return // channel closed, caller will reconnect
			}
			keyID := msg.Payload
			if keyID == "" {
				continue
			}
			revokeFn(keyID)
			slog.Debug("key revoked via pub/sub", "key_id", keyID)
		}
	}
}
