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

// Publish broadcasts a key revocation to all replicas.
// Non-blocking: logs and returns on failure (the periodic sync is the fallback).
func (b *KeyRevocationBroadcaster) Publish(ctx context.Context, keyID string) {
	err := b.client.Do(func(rdb redis.UniversalClient) error {
		return rdb.Publish(ctx, keyRevocationChannel, keyID).Err()
	})
	if err != nil {
		slog.Warn("failed to publish key revocation, periodic sync will handle it",
			"key_id", keyID,
			"error", err,
		)
	}
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
