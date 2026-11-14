package routing

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func mustAcquireProbe(t *testing.T, cb *CircuitBreaker) *ProbeLease {
	t.Helper()
	lease, allowed := cb.Acquire()
	if !allowed {
		t.Fatal("fixture could not admit probe")
	}
	return lease
}

func recordAdmittedFailure(t *testing.T, cb *CircuitBreaker, err error) {
	t.Helper()
	mustAcquireProbe(t, cb).Complete(context.Background(), err)
}

func probeLeaseConfig(limit int) config.CircuitBreakerConfig {
	return config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, SuccessThreshold: 3, HalfOpenMaxProbes: limit, Cooldown: time.Hour}
}

func TestProbeLeaseLimitsAcrossRegistries(t *testing.T) {
	for _, scope := range []string{"shared", "org"} {
		for _, limit := range []int{-1, 0, 1, 2} {
			t.Run(fmt.Sprintf("%s/%d", scope, limit), func(t *testing.T) {
				reg := NewCircuitBreakerRegistry(probeLeaseConfig(limit), nil)
				cb := reg.Get("p")
				if scope == "org" {
					cb = reg.GetScoped("org", "p")
				}
				want := limit
				if want <= 0 {
					want = 1
				}
				for cycle := 0; cycle < 2; cycle++ {
					if cycle == 0 {
						recordAdmittedFailure(t, cb, serverErr())
					}
					expireProbeCooldown(cb)
					var wg sync.WaitGroup
					leases := make(chan *ProbeLease, 12)
					for i := 0; i < 12; i++ {
						wg.Add(1)
						go func() {
							defer wg.Done()
							if l, ok := cb.Acquire(); ok {
								leases <- l
							}
						}()
					}
					wg.Wait()
					close(leases)
					var admitted []*ProbeLease
					for l := range leases {
						admitted = append(admitted, l)
					}
					if len(admitted) != want {
						t.Fatalf("current-generation admissions=%d want=%d", len(admitted), want)
					}
					admitted[0].Abandon()
					replacement := mustAcquireProbe(t, cb)
					replacement.Complete(context.Background(), serverErr())
					for _, l := range admitted {
						l.Complete(context.Background(), nil)
					}
					if cb.State() != StateOpen {
						t.Fatal("old probes changed reopened state")
					}
				}
			})
		}
	}
}

func TestProbeLeaseExactlyOnceNeutral(t *testing.T) {
	for _, ending := range []string{"abandon", "400", "cancel", "success", "concurrent"} {
		t.Run(ending, func(t *testing.T) {
			cb := NewCircuitBreaker("p", probeLeaseConfig(2), nil)
			recordAdmittedFailure(t, cb, serverErr())
			expireProbeCooldown(cb)
			// Accumulated recovery success must survive a neutral release.
			mustAcquireProbe(t, cb).Complete(context.Background(), nil)
			a, b := mustAcquireProbe(t, cb), mustAcquireProbe(t, cb)
			before := cb.lastFailure
			finish := func() {
				switch ending {
				case "400":
					a.Complete(context.Background(), clientErr())
				case "cancel":
					ctx, cancel := context.WithCancel(context.Background())
					cancel()
					a.Complete(ctx, serverErr())
				case "success":
					a.Complete(context.Background(), nil)
				default:
					a.Abandon()
				}
			}
			if ending == "concurrent" {
				var wg sync.WaitGroup
				for i := 0; i < 20; i++ {
					wg.Add(1)
					go func() { defer wg.Done(); finish() }()
				}
				wg.Wait()
			} else {
				finish()
				finish()
			}
			c := mustAcquireProbe(t, cb)
			a.Abandon()
			a.Complete(context.Background(), serverErr())
			if _, ok := cb.Acquire(); ok {
				t.Fatal("duplicate completion released another live probe")
			}
			want := 1
			if ending == "success" {
				want = 2
			}
			if cb.successes != want || cb.lastFailure != before {
				t.Fatalf("neutral accounting successes=%d want=%d failure_time_changed=%t", cb.successes, want, cb.lastFailure != before)
			}
			b.Abandon()
			c.Abandon()
		})
	}
}

func TestProbeLeasePointerIsolation(t *testing.T) {
	reg := NewCircuitBreakerRegistry(probeLeaseConfig(1), nil)
	breakers := []*CircuitBreaker{reg.Get("p"), reg.GetScoped("a", "p"), reg.GetScoped("b", "p")}
	if breakers[0] == breakers[1] || breakers[0] == breakers[2] || breakers[1] == breakers[2] {
		t.Fatal("distinct scopes share a breaker pointer")
	}
	var leases []*ProbeLease
	for _, cb := range breakers {
		recordAdmittedFailure(t, cb, serverErr())
		expireProbeCooldown(cb)
		leases = append(leases, mustAcquireProbe(t, cb))
	}
	for _, l := range leases {
		if l.generation != leases[0].generation {
			t.Fatal("fixture epochs differ")
		}
	}
	leases[0].Abandon()
	for _, cb := range breakers[1:] {
		if _, ok := cb.Acquire(); ok {
			t.Fatal("completion released another scope with equal epoch")
		}
	}
	// Evict a held org entry; its old lease must never affect the replacement.
	reg.orgBreakers.maxEntries = 1
	reg.GetScoped("new", "p")
	fresh := reg.GetScoped("a", "p")
	if fresh == breakers[1] {
		t.Fatal("fixture did not evict held entry")
	}
	leases[1].Complete(context.Background(), serverErr())
	if fresh.State() != StateClosed {
		t.Fatal("old lease changed replacement breaker")
	}
	leases[2].Abandon()
}

func TestProbeLeaseCancellationClassification(t *testing.T) {
	for _, mode := range []string{"client", "raw-cancel", "deadline", "deadline-with-canceled-adapter", "http504"} {
		t.Run(mode, func(t *testing.T) {
			cfg := probeLeaseConfig(1)
			cfg.OnStatusCodes = []int{500}
			cb := NewCircuitBreaker("p", cfg, nil)
			recordAdmittedFailure(t, cb, serverErr())
			expireProbeCooldown(cb)
			l := mustAcquireProbe(t, cb)
			ctx := context.Background()
			err := error(context.Canceled)
			switch mode {
			case "client":
				var cancel context.CancelFunc
				ctx, cancel = context.WithCancel(ctx)
				cancel()
				err = serverErr()
			case "deadline", "deadline-with-canceled-adapter":
				var cancel context.CancelFunc
				ctx, cancel = context.WithDeadline(ctx, time.Now().Add(-time.Second))
				defer cancel()
				if mode == "deadline" {
					err = nil
				}
			case "http504":
				err = &models.APIError{Status: 504}
			}
			before := cb.lastFailure
			l.Complete(ctx, err)
			if mode == "deadline" || mode == "deadline-with-canceled-adapter" {
				if cb.State() != StateOpen {
					t.Fatal("actual deadline did not reopen")
				}
			} else if cb.State() != StateHalfOpen || cb.successes != 0 || cb.halfOpenProbes != 0 || cb.lastFailure != before {
				t.Fatal("neutral outcome changed recovery or retained capacity")
			}
		})
	}
}

func TestProbeRecoveryCallbackEpochs(t *testing.T) {
	var healthy atomic.Bool
	cb := NewCircuitBreaker("p", probeLeaseConfig(1), func(_ string, h bool) { healthy.Store(h) })
	recordAdmittedFailure(t, cb, serverErr())
	oldEpoch := cb.generation
	expireProbeCooldown(cb)
	lease := mustAcquireProbe(t, cb)
	if !healthy.Load() {
		t.Fatal("direct half-open entry stranded routing recovery")
	}
	lease.Abandon()
	cb.readmit(oldEpoch)
	if !healthy.Load() {
		t.Fatal("neutral direct probe stranded later routed probes")
	}
	mustAcquireProbe(t, cb).Complete(context.Background(), serverErr())
	cb.readmit(oldEpoch)
	if healthy.Load() {
		t.Fatal("obsolete cooldown callback readmitted newer open cycle")
	}
}

func TestProbeRetryGroupOwnership(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprint(stream), func(t *testing.T) {
			reg := NewCircuitBreakerRegistry(probeLeaseConfig(1), nil)
			cb := reg.Get("p")
			recordAdmittedFailure(t, cb, serverErr())
			expireProbeCooldown(cb)
			router := makeFailoverRouter(t, "m", "p")
			retry := NewRetryer(config.RetryConfig{Enabled: true, MaxRetries: 1, InitialDelay: time.Nanosecond})
			fo := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 1, PerAttemptTimeout: time.Second}, router, retry, reg)
			admissions, calls := 0, 0
			fo = fo.WithAdmission(func(ctx context.Context, id string) (*ProbeLease, error) {
				admissions++
				l, ok := cb.Acquire()
				if !ok {
					return nil, CircuitOpenError()
				}
				return l, nil
			})
			call := func(ctx context.Context, _, _ string) error {
				calls++
				if _, ok := cb.Acquire(); ok {
					t.Error("retry group lost occupied probe")
				}
				if calls == 1 {
					return serverErr()
				}
				return nil
			}
			var result *FailoverResult
			var err error
			var lease *ProbeLease
			if stream {
				result, lease, err = fo.ExecuteStream(context.Background(), "m", call)
			} else {
				result, err = fo.Execute(context.Background(), "m", call)
			}
			if err != nil || calls != 2 || admissions != 1 || result.Attempts != 1 {
				t.Fatalf("retry ownership calls=%d admissions=%d result=%+v err=%v", calls, admissions, result, err)
			}
			if stream {
				if cb.halfOpenProbes != 1 || cb.successes != 0 {
					t.Fatal("startup completed transferred stream lease")
				}
				lease.Complete(context.Background(), nil)
			}
			if cb.successes != 1 || cb.halfOpenProbes != 0 {
				t.Fatal("retry group did not record exactly one terminal success")
			}
		})
	}
}

func TestProbeCancelDuringRetryBackoff(t *testing.T) {
	reg := NewCircuitBreakerRegistry(probeLeaseConfig(1), nil)
	cb := reg.Get("p")
	recordAdmittedFailure(t, cb, serverErr())
	expireProbeCooldown(cb)
	router := makeFailoverRouter(t, "m", "p")
	fo := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 1}, router, NewRetryer(config.RetryConfig{Enabled: true, MaxRetries: 2, InitialDelay: time.Second}), reg)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	calls := 0
	upstream := serverErr()
	before := cb.lastFailure
	_, err := fo.Execute(ctx, "m", func(context.Context, string, string) error { calls++; cancel(); return upstream })
	if !errors.Is(err, upstream) || calls != 1 || cb.halfOpenProbes != 0 || cb.State() != StateHalfOpen || cb.lastFailure != before {
		t.Fatalf("backoff cancellation recorded saved upstream failure: calls=%d state=%v probes=%d err=%v", calls, cb.State(), cb.halfOpenProbes, err)
	}
}

func TestProbeUpstreamCodeIsNotAdmission(t *testing.T) {
	for _, code := range []string{"circuit_open", "all_providers_unavailable"} {
		t.Run(code, func(t *testing.T) {
			router := makeFailoverRouter(t, "m", "p", "other")
			reg := NewCircuitBreakerRegistry(probeLeaseConfig(1), nil)
			fo := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 1}, router, nil, reg)
			calls := 0
			upstream := &models.APIError{Status: 503, Code: code}
			_, err := fo.Execute(context.Background(), "m", func(context.Context, string, string) error { calls++; return upstream })
			if calls != 1 || err != upstream || IsAdmissionRefusal(err) {
				t.Fatalf("upstream code changed admission budget: calls=%d err=%v", calls, err)
			}
		})
	}
}
