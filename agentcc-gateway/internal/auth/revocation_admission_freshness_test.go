package auth

import (
	"context"
	"testing"
	"time"
)

// A local sync may hold mu after the Redis read. Admission must use the read's
// original deadline, not treat a result as fresh forever while waiting for mu.
func TestRevocationFreshnessAtAdmission(t *testing.T) {
	ks := revocationCheckStore(t)
	entered := make(chan time.Time, 1)
	release, returning := make(chan struct{}), make(chan struct{})
	bindRevocationCheck(t, ks, func(ctx context.Context, _ string) (bool, error) {
		deadline, _ := ctx.Deadline()
		entered <- deadline
		<-release
		close(returning)
		return false, nil
	})
	done := make(chan *APIKey, 1)
	go func() { done <- ks.Authenticate("synthetic-target-credential") }()
	var deadline time.Time
	select {
	case deadline = <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("authority was not consulted")
	}
	ks.mu.Lock()
	close(release)
	<-returning
	time.Sleep(time.Until(deadline) + 50*time.Millisecond)
	ks.mu.Unlock()
	select {
	case key := <-done:
		if key != nil {
			t.Error("stale authority result admitted after waiting for key-store lock")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("authentication did not finish")
	}
}
