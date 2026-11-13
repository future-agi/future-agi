package routing

import (
	"context"
	"runtime"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A slow or saturated mirror target must not let shadow traffic pile up one
// goroutine (holding a request copy for up to 30s) per mirrored request. With
// no max_concurrent configured, in-flight mirrors are capped at the default and
// the excess is dropped.
func TestMirrorBurstAgainstSlowTargetIsBounded(t *testing.T) {
	var inFlight, peak, calls atomic.Int64
	release := make(chan struct{})
	slow := &mockMirrorProvider{fn: func(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
		calls.Add(1)
		n := inFlight.Add(1)
		for {
			p := peak.Load()
			if n <= p || peak.CompareAndSwap(p, n) {
				break
			}
		}
		defer inFlight.Add(-1)
		select {
		case <-release:
		case <-ctx.Done():
		}
		return &models.ChatCompletionResponse{ID: "shadow"}, nil
	}}
	m := NewMirror(config.MirrorConfig{
		Enabled: true,
		Rules:   []config.MirrorRule{{SourceModel: "gpt-4o", TargetProvider: "target", SampleRate: 1.0}},
	}, func(id string) (MirrorProvider, bool) { return slow, id == "target" })

	before := runtime.NumGoroutine()
	req := &models.ChatCompletionRequest{Model: "gpt-4o"}
	const burst = 1000
	for i := 0; i < burst; i++ {
		m.ExecuteAsync(req, "openai", "gpt-4o", nil)
	}
	time.Sleep(300 * time.Millisecond)
	grown := runtime.NumGoroutine() - before
	t.Logf("burst=%d provider calls=%d peak in-flight=%d goroutines grown=%d", burst, calls.Load(), peak.Load(), grown)

	if got := peak.Load(); got > 64 {
		t.Errorf("peak in-flight mirrors = %d, want <= 64 (default cap)", got)
	}
	if grown > 64+16 {
		t.Errorf("goroutines grew by %d for a burst of %d mirrors, want bounded by the cap", grown, burst)
	}
	if calls.Load() == 0 {
		t.Fatal("no mirror ran at all; the cap must still let shadow traffic through")
	}

	close(release)
	deadline := time.Now().Add(2 * time.Second)
	for inFlight.Load() > 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}

	// Positive control: once the burst drains, mirroring works again.
	before2 := calls.Load()
	m.ExecuteAsync(req, "openai", "gpt-4o", nil)
	deadline = time.Now().Add(2 * time.Second)
	for calls.Load() == before2 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if calls.Load() == before2 {
		t.Error("mirror did not run after the in-flight slots were released")
	}
}
