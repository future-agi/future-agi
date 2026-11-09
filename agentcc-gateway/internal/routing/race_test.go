package routing

import (
	"context"
	"errors"
	"testing"
	"testing/synctest"
	"time"
)

// Hide the parent's cancelCtx value so WithCancel must use its propagation
// goroutine. This makes a forgotten child cancellation observable even when all
// providers return immediately and the caller keeps its context alive.
type raceOpaqueContext struct{ context.Context }

func (raceOpaqueContext) Value(any) any { return nil }

func TestRaceExecutorCleanup(t *testing.T) {
	providerErr := errors.New("provider failed")
	targets := []RoutingTarget{{ProviderID: "a", ModelOverride: "model-a"}, {ProviderID: "b", ModelOverride: "model-b"}}
	for _, tc := range []struct {
		name           string
		targets        []RoutingTarget
		excluded       []string
		limit          int
		mode           string
		wantCalls      int
		wantErr        error
		noEligible     bool
		parentCanceled bool
		wantElapsed    time.Duration
	}{
		{name: "no targets", noEligible: true},
		{name: "all excluded", targets: targets, excluded: []string{"a", "b"}, noEligible: true},
		{name: "single success", targets: targets[:1], mode: "success", wantCalls: 1},
		{name: "single error", targets: targets[:1], mode: "failure", wantCalls: 1, wantErr: providerErr},
		{name: "limited single", targets: targets, limit: 1, mode: "success", wantCalls: 1},
		{name: "excluded single", targets: targets, excluded: []string{"b"}, mode: "success", wantCalls: 1},
		{name: "all succeed before delay", targets: targets, mode: "success", wantCalls: 2},
		{name: "all fail", targets: targets, mode: "failure", wantCalls: 2, wantErr: providerErr},
		{name: "success with failure", targets: targets, mode: "mixed", wantCalls: 2},
		{name: "winner cancels waiting loser", targets: targets, mode: "waiting loser", wantCalls: 2, wantElapsed: time.Hour},
		{name: "parent cancellation", targets: targets, mode: "cancel parent", wantCalls: 2, wantErr: context.Canceled, parentCanceled: true},
		{name: "already canceled parent", targets: targets, mode: "already canceled", wantCalls: 2, wantErr: context.Canceled, parentCanceled: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) {
				parent, cancelParent := context.WithCancel(context.Background())
				defer cancelParent()
				ctx := raceOpaqueContext{parent}
				synctest.Wait()
				before := raceBubbleGoroutines(t)
				seen := make(chan context.Context, len(targets))
				started := make(chan struct{}, len(targets))
				if tc.mode == "already canceled" {
					cancelParent()
				}
				if tc.mode == "cancel parent" {
					go func() {
						for i := 0; i < tc.wantCalls; i++ {
							<-started
						}
						cancelParent()
					}()
				}

				// Fake time keeps the grace-delay path deterministic and instant;
				// an hour also exposes an unnecessary sleeping cleanup goroutine.
				executor := NewRaceExecutor(tc.limit, time.Hour, tc.excluded)
				start := time.Now()
				outcome, err := executor.Execute(ctx, tc.targets, func(callCtx context.Context, id, model string) (interface{}, error) {
					seen <- callCtx
					started <- struct{}{}
					if model != "model-"+id {
						t.Errorf("model override = %q for provider %q", model, id)
					}
					switch tc.mode {
					case "failure":
						return nil, providerErr
					case "mixed":
						if id == "a" {
							return nil, providerErr
						}
					case "waiting loser":
						if id == "b" {
							<-callCtx.Done()
							return nil, callCtx.Err()
						}
					case "cancel parent", "already canceled":
						<-callCtx.Done()
						return nil, callCtx.Err()
					}
					return id, nil
				})
				if elapsed := time.Since(start); elapsed != tc.wantElapsed {
					t.Errorf("elapsed = %v, want %v", elapsed, tc.wantElapsed)
				}
				if tc.noEligible {
					if err == nil || outcome != nil {
						t.Fatal("expected no-eligible-provider error without an outcome")
					}
				} else if !errors.Is(err, tc.wantErr) {
					t.Fatalf("error = %v, want %v", err, tc.wantErr)
				} else if tc.wantErr != nil {
					if outcome != nil {
						t.Fatal("failed race returned an outcome")
					}
				} else {
					if outcome == nil || outcome.ProviderCount != tc.wantCalls || outcome.Response != outcome.WinnerProvider || len(outcome.Losers) != tc.wantCalls-1 {
						t.Fatalf("unexpected successful outcome: %+v", outcome)
					}
					if tc.mode == "mixed" && outcome.WinnerProvider != "b" {
						t.Fatal("failed provider won the race")
					}
					if tc.mode == "waiting loser" && (outcome.WinnerProvider != "a" || !errors.Is(outcome.Losers[0].Err, context.Canceled)) {
						t.Fatal("waiting loser was not canceled after the grace delay")
					}
				}
				if len(seen) != tc.wantCalls {
					t.Fatalf("calls = %d, want %d", len(seen), tc.wantCalls)
				}
				for i := 0; i < tc.wantCalls; i++ {
					callCtx := <-seen
					if tc.wantCalls == 1 {
						if callCtx != ctx {
							t.Error("single provider did not receive the caller context")
						}
					} else if callCtx.Err() != context.Canceled {
						t.Error("race context is still live after Execute returned")
					}
				}
				if (parent.Err() != nil) != tc.parentCanceled {
					t.Error("Execute changed caller cancellation state")
				}

				// Drain runnable bubble work. The per-call cancellation assertions
				// above are the cleanup oracle: process-wide NumGoroutine also
				// counts unrelated runtime/race goroutines outside this bubble and
				// can change even when every owned child has been cancelled.
				synctest.Wait()
				if after := raceBubbleGoroutines(t); after > before {
					t.Errorf("Execute left %d extra goroutines after completion", after-before)
				}
			})
		})
	}
}
