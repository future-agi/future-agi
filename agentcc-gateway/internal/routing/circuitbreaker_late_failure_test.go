package routing

import (
	"testing"
	"time"
)

// Failed recovery probes start new cooldowns, and late in-flight failures must
// extend each one without suppressing the existing recovery notification.
func TestCircuitBreaker_LateFailureAcrossRepeatedFailedProbes(t *testing.T) {
	cfg := newTestCBConfig()
	cfg.FailureThreshold = 1
	cfg.Cooldown = 100 * time.Millisecond
	type change struct {
		healthy bool
		at      time.Time
	}
	changes := make(chan change, 16)
	cb := NewCircuitBreaker("provider-1", cfg, func(_ string, healthy bool) {
		changes <- change{healthy, time.Now()}
	})
	await := func(healthy bool) change {
		t.Helper()
		select {
		case got := <-changes:
			if got.healthy != healthy {
				t.Fatalf("callback healthy=%v, want %v", got.healthy, healthy)
			}
			return got
		case <-time.After(2 * time.Second):
			t.Fatal("missing state callback")
			return change{}
		}
	}

	cb.RecordFailure(serverErr())
	for cycle := 0; cycle < 3; cycle++ {
		await(false)
		time.Sleep(60 * time.Millisecond)
		late := time.Now()
		cb.RecordFailure(serverErr())
		readmitted := await(true)
		if elapsed := readmitted.at.Sub(late); elapsed < cfg.Cooldown {
			t.Fatalf("cycle %d: readmitted %s after late failure, want at least %s", cycle, elapsed, cfg.Cooldown)
		}
		if !cb.Allow() || cb.State() != StateHalfOpen {
			t.Fatalf("cycle %d: readmission did not allow a half-open probe", cycle)
		}
		if cycle < 2 {
			cb.RecordFailure(serverErr())
		}
	}
	for i := 0; i < cfg.SuccessThreshold; i++ {
		cb.RecordSuccess()
	}
	await(true) // Preserve dev's distinct closed/recovered callback.
	if cb.State() != StateClosed {
		t.Fatal("successful probes did not close the breaker")
	}
	select {
	case extra := <-changes:
		t.Fatalf("unexpected extra callback: %+v", extra)
	default:
	}
}
