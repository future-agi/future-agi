package mcp

import (
	"context"
	"errors"
	"sync/atomic"
	"testing"
)

func TestCredentialLifecycleStreamFenceChecksEveryChunk(t *testing.T) {
	var revoked atomic.Bool
	var delivered atomic.Int32
	ctx := WithUpstreamNotifications(context.Background(), func(*Message) { delivered.Add(1) })
	ctx = GuardUpstreamNotifications(ctx, func() error {
		if revoked.Load() {
			return errors.New("synthetic epoch changed")
		}
		return nil
	})
	sink := ctx.Value(notificationSinkKey{}).(func(*Message))
	sink(&Message{Method: MethodProgress})
	revoked.Store(true)
	for range 8 {
		sink(&Message{Method: MethodProgress})
	}
	if delivered.Load() != 1 {
		t.Fatal("buffered chunks bypassed the completion fence")
	}
}
