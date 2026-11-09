package routing

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestRaceReleasesContextAfterAllCallsReturn(t *testing.T) {
	for _, fail := range []bool{false, true} {
		t.Run(map[bool]string{false: "success", true: "failure"}[fail], func(t *testing.T) {
			executor := NewRaceExecutor(2, time.Hour, nil)
			contexts := make(chan context.Context, 2)
			outcome, err := executor.Execute(context.Background(), []RoutingTarget{
				{ProviderID: "one"}, {ProviderID: "two"},
			}, func(ctx context.Context, _, _ string) (interface{}, error) {
				contexts <- ctx
				if fail {
					return nil, errors.New("synthetic provider failure")
				}
				return "synthetic response", nil
			})
			if (err != nil) != fail || (outcome == nil) != fail {
				t.Fatalf("unexpected outcome: fail=%v, outcome=%v, err=%v", fail, outcome, err)
			}
			for range 2 {
				select {
				case <-(<-contexts).Done():
				default:
					t.Fatal("completed race left its context live")
				}
			}
		})
	}
}

func TestRacePreservesInFlightCancellationGrace(t *testing.T) {
	const grace = 30 * time.Millisecond
	executor := NewRaceExecutor(2, grace, nil)
	parent, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	started := make(chan struct{})
	winnerReturning := make(chan time.Time, 1)
	cancelled := make(chan time.Time, 1)
	outcome, err := executor.Execute(parent, []RoutingTarget{
		{ProviderID: "winner"}, {ProviderID: "loser"},
	}, func(ctx context.Context, provider, _ string) (interface{}, error) {
		if provider == "winner" {
			<-started
			winnerReturning <- time.Now()
			return "synthetic response", nil
		}
		close(started)
		<-ctx.Done()
		cancelled <- time.Now()
		return nil, ctx.Err()
	})
	if err != nil || outcome == nil || outcome.WinnerProvider != "winner" {
		t.Fatalf("unexpected race result: %v, %v", outcome, err)
	}
	if elapsed := (<-cancelled).Sub(<-winnerReturning); elapsed < grace {
		t.Fatalf("loser cancelled before cleanup grace: %v < %v", elapsed, grace)
	}
	if parent.Err() != nil {
		t.Fatal("race relied on parent cancellation instead of its own timer")
	}
}
