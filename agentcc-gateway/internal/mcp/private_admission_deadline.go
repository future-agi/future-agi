package mcp

import (
	"context"
	"net/http"
	"sync"
	"time"
)

// One gate per response, including its short-lived event sinks. Only deadline
// changes hold this lock: cancellation never waits behind transport Write/Flush.
// A forced deadline is terminal even if a different sink is still finishing.
type admissionWriteDeadline struct {
	mu     sync.Mutex
	writer http.ResponseWriter
	forced time.Time
}

func (d *admissionWriteDeadline) set(ctx context.Context, deadline time.Time) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	now := time.Now()
	if d.forced.IsZero() && (ctx.Err() != nil || !deadline.IsZero() && !deadline.After(now)) {
		d.forced = now
	}
	if !d.forced.IsZero() {
		deadline = d.forced
	} else if ceiling := now.Add(admissionWriteTimeout); deadline.After(ceiling) {
		deadline = ceiling
	}
	return http.NewResponseController(d.writer).SetWriteDeadline(deadline)
}
