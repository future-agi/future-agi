package routing

import (
	"context"
	"errors"
	"reflect"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// TestExecuteObservedReportsEachExclusion: an open circuit excludes the first
// target without a call, and onExclude hears about it at that moment, with
// the attempt the run records, before the second target is called.
func TestExecuteObservedReportsEachExclusion(t *testing.T) {
	router := makeFailoverRouter(t, "gpt-4", "p1", "p2")
	cbReg := NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, Cooldown: time.Hour}, nil)
	cbReg.Get(BreakerKey("p1", "gpt-4", "")).RecordFailure(&models.APIError{Status: 500})
	f := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 3}, router, nil, cbReg)

	var events []string
	var excluded []models.Attempt
	call := func(_ context.Context, providerID, _ string) error {
		events = append(events, "call:"+providerID)
		return nil
	}
	onExclude := func(a models.Attempt) {
		events = append(events, "exclude:"+a.Provider)
		excluded = append(excluded, a)
	}

	result, err := f.ExecuteObserved(context.Background(), "gpt-4", call, onExclude)
	if err != nil {
		t.Fatalf("ExecuteObserved: %v", err)
	}
	if want := []string{"exclude:p1", "call:p2"}; !reflect.DeepEqual(events, want) {
		t.Fatalf("events = %v, want %v: the exclusion is reported before the next target is called", events, want)
	}
	if len(excluded) != 1 {
		t.Fatalf("onExclude called %d times, want once", len(excluded))
	}
	a := excluded[0]
	if a.Outcome != models.OutcomeCircuitOpen || a.ErrorClass != models.ErrClassCircuitOpen || a.Provider != "p1" ||
		a.Model != "gpt-4" || a.Kind != models.AttemptPrimary || a.Index != 1 || a.StartedAt.IsZero() {
		t.Fatalf("excluded attempt = %+v, want p1's primary attempt with outcome circuit_open", a)
	}
	if len(result.AttemptRecords) != 2 || !reflect.DeepEqual(result.AttemptRecords[0], a) {
		t.Fatalf("AttemptRecords = %+v; the callback must get the attempt the run records, %+v", result.AttemptRecords, a)
	}
	if got := result.AttemptRecords[1]; got.Provider != "p2" || got.Kind != models.AttemptFailover || got.Reason != models.ReasonCircuitOpen {
		t.Fatalf("second attempt = %+v, want p2 as failover with reason circuit_open", got)
	}

	t.Run("each exclusion of a run is reported, also when the run fails", func(t *testing.T) {
		router := makeFailoverRouter(t, "gpt-4", "p1", "p2", "p3")
		cbReg := NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, Cooldown: time.Hour}, nil)
		for _, p := range []string{"p1", "p2", "p3"} {
			cbReg.Get(BreakerKey(p, "gpt-4", "")).RecordFailure(&models.APIError{Status: 500})
		}
		f := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 3}, router, nil, cbReg)
		var got []models.Attempt
		calls := &scriptedCalls{}
		result, err := f.ExecuteObserved(context.Background(), "gpt-4", calls.call, func(a models.Attempt) { got = append(got, a) })
		if result != nil || err == nil || len(calls.calls) != 0 {
			t.Fatalf("ExecuteObserved = (%v, %v) after calls %v, want no result and an error", result, err, calls.calls)
		}
		if len(got) != 3 {
			t.Fatalf("onExclude saw %d exclusions, want all 3: %+v", len(got), got)
		}
		seen := map[string]bool{}
		for i, a := range got {
			if a.Outcome != models.OutcomeCircuitOpen || a.Index != i+1 {
				t.Errorf("exclusion %d = %+v, want outcome circuit_open with index %d", i, a, i+1)
			}
			seen[a.Provider] = true
		}
		if len(seen) != 3 {
			t.Errorf("exclusions named providers %v, want p1, p2 and p3 once each", seen)
		}
		if got[1].Reason != models.ReasonCircuitOpen || got[1].Kind != models.AttemptFailover {
			t.Errorf("second exclusion = %+v, want kind failover with reason circuit_open", got[1])
		}
	})
}

// executeOutcome is what one run returns, with the attempt start times and
// latencies zeroed so two runs can be compared.
type executeOutcome struct {
	Result *FailoverResult
	Err    string
	Calls  []string
}

func normalizeOutcome(res *FailoverResult, err error, calls []string) executeOutcome {
	out := executeOutcome{Result: res, Calls: calls}
	if err != nil {
		out.Err = err.Error()
	}
	if res != nil {
		cp := *res
		cp.AttemptRecords = append([]models.Attempt(nil), res.AttemptRecords...)
		for i := range cp.AttemptRecords {
			cp.AttemptRecords[i].StartedAt = time.Time{}
			cp.AttemptRecords[i].LatencyMs = 0
		}
		out.Result = &cp
	}
	return out
}

// TestExecuteObservedNilCallbackUnchanged: with a nil callback,
// ExecuteObserved returns what Execute returns, scenario by scenario, and
// both entry points meet the expectations below.
func TestExecuteObservedNilCallbackUnchanged(t *testing.T) {
	type scenario struct {
		name      string
		providers []string
		open      []string // providers whose circuit starts open
		script    map[string][]error
		retries   bool
		maxAtt    int
		// want
		provider  string
		attempts  int
		fallback  bool
		kinds     []models.AttemptKind
		outcomes  []models.AttemptOutcome
		errStatus int // 0: no error
		calls     []string
	}
	scenarios := []scenario{
		{name: "first target succeeds", providers: []string{"p1", "p2"}, maxAtt: 3,
			provider: "p1", attempts: 1,
			kinds: []models.AttemptKind{models.AttemptPrimary}, outcomes: []models.AttemptOutcome{models.OutcomeSuccess},
			calls: []string{"p1"}},
		{name: "retries then failover", providers: []string{"p1", "p2"}, maxAtt: 3, retries: true,
			script:   map[string][]error{"p1": {&models.APIError{Status: 429}, &models.APIError{Status: 429}, &models.APIError{Status: 500}}},
			provider: "p2", attempts: 2, fallback: true,
			kinds:    []models.AttemptKind{models.AttemptPrimary, models.AttemptRetry, models.AttemptRetry, models.AttemptFailover},
			outcomes: []models.AttemptOutcome{models.OutcomeError, models.OutcomeError, models.OutcomeError, models.OutcomeSuccess},
			calls:    []string{"p1", "p1", "p1", "p2"}},
		{name: "open circuit then success", providers: []string{"p1", "p2"}, open: []string{"p1"}, maxAtt: 3,
			provider: "p2", attempts: 2, fallback: true,
			kinds:    []models.AttemptKind{models.AttemptPrimary, models.AttemptFailover},
			outcomes: []models.AttemptOutcome{models.OutcomeCircuitOpen, models.OutcomeSuccess},
			calls:    []string{"p2"}},
		{name: "a non-failover error ends the run", providers: []string{"p1", "p2"}, maxAtt: 3,
			script:    map[string][]error{"p1": {&models.APIError{Status: 400}}},
			errStatus: 400, calls: []string{"p1"}},
		{name: "every target fails", providers: []string{"p1", "p2"}, maxAtt: 3,
			script:    map[string][]error{"p1": {&models.APIError{Status: 502}}, "p2": {&models.APIError{Status: 503}}},
			errStatus: 503, calls: []string{"p1", "p2"}},
		{name: "every circuit open", providers: []string{"p1", "p2"}, open: []string{"p1", "p2"}, maxAtt: 2,
			errStatus: 503},
	}

	run := func(t *testing.T, sc scenario, observed bool) executeOutcome {
		t.Helper()
		router := makeFailoverRouter(t, "gpt-4", sc.providers...)
		var cbReg *CircuitBreakerRegistry
		if len(sc.open) > 0 {
			cbReg = NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, Cooldown: time.Hour}, nil)
			for _, p := range sc.open {
				cbReg.Get(BreakerKey(p, "gpt-4", "")).RecordFailure(&models.APIError{Status: 500})
			}
		}
		var retryer *Retryer
		if sc.retries {
			retryer = fastRetryer()
		}
		f := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: sc.maxAtt}, router, retryer, cbReg)
		calls := &scriptedCalls{script: sc.script}
		var res *FailoverResult
		var err error
		if observed {
			res, err = f.ExecuteObserved(context.Background(), "gpt-4", calls.call, nil)
		} else {
			res, err = f.Execute(context.Background(), "gpt-4", calls.call)
		}
		return normalizeOutcome(res, err, calls.calls)
	}

	for _, sc := range scenarios {
		t.Run(sc.name, func(t *testing.T) {
			viaExecute := run(t, sc, false)
			viaObserved := run(t, sc, true)
			if !reflect.DeepEqual(viaExecute, viaObserved) {
				t.Fatalf("ExecuteObserved(nil) = %+v, Execute = %+v", viaObserved, viaExecute)
			}
			for name, got := range map[string]executeOutcome{"Execute": viaExecute, "ExecuteObserved": viaObserved} {
				if !reflect.DeepEqual(got.Calls, sc.calls) {
					t.Errorf("%s calls = %v, want %v", name, got.Calls, sc.calls)
				}
				if sc.errStatus != 0 {
					if got.Result != nil || got.Err == "" {
						t.Errorf("%s = (%+v, %q), want no result and an error", name, got.Result, got.Err)
					}
					continue
				}
				r := got.Result
				if got.Err != "" || r == nil || r.ProviderID != sc.provider || r.Attempts != sc.attempts || r.FallbackUsed != sc.fallback {
					t.Errorf("%s = (%+v, %q), want %s after %d targets (fallback %v)", name, r, got.Err, sc.provider, sc.attempts, sc.fallback)
					continue
				}
				if len(r.AttemptRecords) != len(sc.kinds) {
					t.Errorf("%s recorded %d attempts, want %d: %+v", name, len(r.AttemptRecords), len(sc.kinds), r.AttemptRecords)
					continue
				}
				for i, a := range r.AttemptRecords {
					if a.Kind != sc.kinds[i] || a.Outcome != sc.outcomes[i] {
						t.Errorf("%s attempt %d = {%s %s}, want {%s %s}", name, i+1, a.Kind, a.Outcome, sc.kinds[i], sc.outcomes[i])
					}
				}
			}
		})
	}

	// The error itself is the same value kind: the 503 of an all-open run and
	// the provider error of a failed one.
	t.Run("errors keep their type", func(t *testing.T) {
		router := makeFailoverRouter(t, "gpt-4", "p1")
		f := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 1}, router, nil, nil)
		want := &models.APIError{Status: 502, Code: "bad_gateway"}
		_, err := f.ExecuteObserved(context.Background(), "gpt-4", func(context.Context, string, string) error { return want }, nil)
		var apiErr *models.APIError
		if !errors.As(err, &apiErr) || apiErr != want {
			t.Fatalf("err = %v, want the provider's own error", err)
		}
	})
}
