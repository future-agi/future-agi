package models

import (
	"context"
	"sync/atomic"
)

type streamAcceptanceKey struct{}

// ObserveStreamAcceptance tracks a successful upstream HTTP response separately
// from its first model chunk. The adapter only marks it after accepting status
// headers; a failed connection, refusal, or undispatched call never marks it.
func ObserveStreamAcceptance(ctx context.Context) (context.Context, *atomic.Bool) {
	accepted := &atomic.Bool{}
	return context.WithValue(ctx, streamAcceptanceKey{}, accepted), accepted
}

// MarkStreamAccepted is called by a stream adapter after successful headers.
func MarkStreamAccepted(ctx context.Context) {
	if accepted, ok := ctx.Value(streamAcceptanceKey{}).(*atomic.Bool); ok {
		accepted.Store(true)
	}
}
