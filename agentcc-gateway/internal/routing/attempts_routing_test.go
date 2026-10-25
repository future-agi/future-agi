package routing

import (
	"context"
	"errors"
	"go/ast"
	"go/parser"
	"go/token"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// scriptedCalls answers each provider's calls in turn from its script; a
// provider past the end of its script succeeds.
type scriptedCalls struct {
	script map[string][]error
	calls  []string
}

func (s *scriptedCalls) call(_ context.Context, providerID, _ string) error {
	s.calls = append(s.calls, providerID)
	n := 0
	for _, p := range s.calls[:len(s.calls)-1] {
		if p == providerID {
			n++
		}
	}
	if n < len(s.script[providerID]) {
		return s.script[providerID][n]
	}
	return nil
}

func (s *scriptedCalls) count(providerID string) int {
	n := 0
	for _, p := range s.calls {
		if p == providerID {
			n++
		}
	}
	return n
}

func fastRetryer() *Retryer {
	return NewRetryer(config.RetryConfig{Enabled: true, MaxRetries: 2, InitialDelay: time.Microsecond, MaxDelay: time.Microsecond})
}

type attemptWant struct {
	kind     models.AttemptKind
	provider string
	outcome  models.AttemptOutcome
	class    models.ErrorClass
	reason   string
	status   int
}

func checkAttempts(t *testing.T, got []models.Attempt, want []attemptWant) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("recorded %d attempts, want %d: %+v", len(got), len(want), got)
	}
	for i, w := range want {
		a := got[i]
		if a.Index != i+1 || a.Kind != w.kind || a.Provider != w.provider || a.Outcome != w.outcome ||
			a.ErrorClass != w.class || a.Reason != w.reason || a.StatusCode != w.status {
			t.Errorf("attempt %d = {index %d, kind %s, provider %s, outcome %s, class %q, reason %q, status %d}, want {kind %s, provider %s, outcome %s, class %q, reason %q, status %d}",
				i+1, a.Index, a.Kind, a.Provider, a.Outcome, a.ErrorClass, a.Reason, a.StatusCode,
				w.kind, w.provider, w.outcome, w.class, w.reason, w.status)
		}
		if a.Model != "gpt-4" || a.StartedAt.IsZero() {
			t.Errorf("attempt %d model %q started %v, want gpt-4 and a start time", i+1, a.Model, a.StartedAt)
		}
		if w.outcome == models.OutcomeError && a.ErrorOrigin != models.OriginUpstream {
			t.Errorf("attempt %d error origin = %q, want upstream", i+1, a.ErrorOrigin)
		}
	}
}

// The first provider answers 429, 429, then 500 (its primary call and both
// retries), so the run fails over to the second, which succeeds. Every one of
// the four calls is kept, not just the last error.
func TestFailoverRecordsEveryAttempt(t *testing.T) {
	router := makeFailoverRouter(t, "gpt-4", "p1", "p2")
	f := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 3}, router, fastRetryer(), nil)
	calls := &scriptedCalls{script: map[string][]error{"p1": {
		&models.APIError{Status: 429, Message: "slow down"},
		&models.APIError{Status: 429, Message: "slow down"},
		&models.APIError{Status: 500, Message: "upstream down"},
	}}}

	result, err := f.Execute(context.Background(), "gpt-4", calls.call)
	if err != nil {
		t.Fatalf("Execute: %v", err)
	}
	if result.ProviderID != "p2" || result.Attempts != 2 || !result.FallbackUsed {
		t.Fatalf("result = %+v, want p2 after 2 targets", result)
	}
	checkAttempts(t, result.AttemptRecords, []attemptWant{
		{models.AttemptPrimary, "p1", models.OutcomeError, models.ErrClassRateLimited, "", 429},
		{models.AttemptRetry, "p1", models.OutcomeError, models.ErrClassRateLimited, models.ReasonStatus429, 429},
		{models.AttemptRetry, "p1", models.OutcomeError, models.ErrClassUpstream5xx, models.ReasonStatus429, 500},
		{models.AttemptFailover, "p2", models.OutcomeSuccess, "", models.ReasonStatus5xx, 0},
	})
}

// An open circuit excludes its target without a call; the exclusion is kept
// as an attempt of outcome circuit_open.
func TestFailoverRecordsCircuitOpenExclusion(t *testing.T) {
	router := makeFailoverRouter(t, "gpt-4", "p1", "p2")
	cbReg := NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, Cooldown: time.Hour}, nil)
	cbReg.Get(BreakerKey("p1", "gpt-4", "")).RecordFailure(&models.APIError{Status: 500})
	f := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 3}, router, nil, cbReg)
	calls := &scriptedCalls{}

	result, err := f.Execute(context.Background(), "gpt-4", calls.call)
	if err != nil || calls.count("p1") != 0 {
		t.Fatalf("Execute = %v after calls %v; the open circuit must keep p1 uncalled", err, calls.calls)
	}
	got := result.AttemptRecords
	if len(got) != 2 || got[0].Outcome != models.OutcomeCircuitOpen || got[0].ErrorClass != models.ErrClassCircuitOpen ||
		got[0].Provider != "p1" || got[1].Outcome != models.OutcomeSuccess || got[1].Reason != models.ReasonCircuitOpen {
		t.Fatalf("attempts = %+v, want p1 circuit_open then p2 success with reason circuit_open", got)
	}
}

func TestRetryAttemptsKept(t *testing.T) {
	r := fastRetryer()
	results := []error{&models.APIError{Status: 429}, &models.APIError{Status: 503}, nil}
	type try struct {
		n   int
		err error
	}
	var tries []try
	calls := 0
	retries, err := r.ExecuteObserved(context.Background(), func() error {
		calls++
		return results[calls-1]
	}, func(n int, err error) { tries = append(tries, try{n, err}) })
	if err != nil || retries != 2 || calls != 3 {
		t.Fatalf("ExecuteObserved = (%d, %v) after %d calls, want (2, nil) after 3", retries, err, calls)
	}
	if len(tries) != 3 {
		t.Fatalf("onTry saw %d tries, want every one of 3: %+v", len(tries), tries)
	}
	for i, tr := range tries {
		if tr.n != i || tr.err != results[i] {
			t.Errorf("try %d = (%d, %v), want (%d, %v)", i, tr.n, tr.err, i, results[i])
		}
	}

	// Disabled retry still reports its one try; Execute keeps its contract.
	var once []int
	_, _ = (&Retryer{}).ExecuteObserved(context.Background(), func() error { return nil }, func(n int, _ error) { once = append(once, n) })
	if len(once) != 1 || once[0] != 0 {
		t.Fatalf("a disabled retryer reported tries %v, want [0]", once)
	}
	if n, err := r.Execute(context.Background(), func() error { return nil }); n != 0 || err != nil {
		t.Fatalf("Execute = (%d, %v), want (0, nil)", n, err)
	}
}

func TestModelFallbackAttemptKind(t *testing.T) {
	if got := FallbackAttemptKind(false); got != models.AttemptModelFallback {
		t.Errorf("a configured model fallback is kind %q, want model_fallback", got)
	}
	if got := FallbackAttemptKind(true); got != models.AttemptAutoFallback {
		t.Errorf("an auto-routed request's runner-up is kind %q, want auto_fallback", got)
	}
}

// TestBreakerRecordSiteUsesBreakerKey reads failover.go: every
// cbReg.Get call, the check before a call and the record after it, is
// keyed by BreakerKey(...), so both sites name the same circuit for a
// provider's model or endpoint.
func TestBreakerRecordSiteUsesBreakerKey(t *testing.T) {
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, "failover.go", nil, 0)
	if err != nil {
		t.Fatalf("parsing failover.go: %v", err)
	}
	gets := 0
	ast.Inspect(file, func(n ast.Node) bool {
		call, ok := n.(*ast.CallExpr)
		if !ok {
			return true
		}
		sel, ok := call.Fun.(*ast.SelectorExpr)
		if !ok || sel.Sel.Name != "Get" {
			return true
		}
		recv, ok := sel.X.(*ast.SelectorExpr)
		if !ok || recv.Sel.Name != "cbReg" {
			return true
		}
		gets++
		if len(call.Args) != 1 || !isBreakerKeyCall(call.Args[0]) {
			t.Errorf("%s: cbReg.Get is not passed a BreakerKey(...) value", fset.Position(call.Pos()))
		}
		return true
	})
	if gets < 2 {
		t.Fatalf("found %d cbReg.Get calls in failover.go, want the check and the record site", gets)
	}
}

// isBreakerKeyCall reports whether e is BreakerKey(...), or a variable
// assigned from it is not accepted: the key is computed at each site.
func isBreakerKeyCall(e ast.Expr) bool {
	call, ok := e.(*ast.CallExpr)
	if !ok {
		return false
	}
	id, ok := call.Fun.(*ast.Ident)
	return ok && id.Name == "BreakerKey"
}

// callSequence answers the n-th call of a run with results[n], whichever
// provider it goes to, and succeeds past the end. It records the providers.
type callSequence struct {
	results []error
	calls   []string
}

func (c *callSequence) call(_ context.Context, providerID, _ string) error {
	c.calls = append(c.calls, providerID)
	if n := len(c.calls) - 1; n < len(c.results) {
		return c.results[n]
	}
	return nil
}

func (c *callSequence) count(providerID string) int {
	n := 0
	for _, p := range c.calls {
		if p == providerID {
			n++
		}
	}
	return n
}

// The skip tests refuse the second target as the server's hop admission
// does: its call returns the hop-skipped sentinel wrapping the previous
// attempt's error, without reaching an upstream.

func TestSkippedHopNotRecordedAsFailure(t *testing.T) {
	router := makeFailoverRouter(t, "gpt-4", "p1", "p2", "p3")
	cbReg := NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, Cooldown: time.Hour}, nil)
	f := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 3}, router, nil, cbReg)
	down := &models.APIError{Status: 502, Message: "p1 down"}
	calls := &callSequence{results: []error{down, SkipHop(down, models.ReasonSkippedInsufficientCredits)}}

	result, err := f.Execute(context.Background(), "gpt-4", calls.call)
	if err != nil || len(calls.calls) != 3 || calls.calls[0] != "p1" {
		t.Fatalf("Execute = %v after calls %v, want p1, a refused hop, then a success", err, calls.calls)
	}
	skipped, served := calls.calls[1], calls.calls[2]
	if cbReg.Get(BreakerKey("p1", "gpt-4", "")).State() != StateOpen {
		t.Fatal("p1's real failure did not reach its breaker; the test proves nothing")
	}
	if state := cbReg.Get(BreakerKey(skipped, "gpt-4", "")).State(); state != StateClosed {
		t.Fatalf("%s's breaker is %v after a hop the gateway refused; a refusal is not a provider failure", skipped, state)
	}
	checkAttempts(t, result.AttemptRecords, []attemptWant{
		{models.AttemptPrimary, "p1", models.OutcomeError, models.ErrClassUpstream5xx, "", 502},
		{models.AttemptFailover, skipped, models.OutcomeSkipped, models.ErrClassInsufficientCredits, models.ReasonSkippedInsufficientCredits, 0},
		{models.AttemptFailover, served, models.OutcomeSuccess, "", models.ReasonStatus5xx, 0},
	})
	if o := result.AttemptRecords[1].ErrorOrigin; o != models.OriginGateway {
		t.Fatalf("skipped hop origin = %q, want gateway", o)
	}
}

func TestSkippedHopNotRetried(t *testing.T) {
	router := makeFailoverRouter(t, "gpt-4", "p1", "p2", "p3")
	f := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 3}, router, fastRetryer(), nil)
	down := &models.APIError{Status: 503, Message: "p1 down"}
	calls := &callSequence{results: []error{down, down, down, SkipHop(down, models.ReasonPolicy)}}

	result, err := f.Execute(context.Background(), "gpt-4", calls.call)
	if err != nil || len(calls.calls) != 5 {
		t.Fatalf("Execute = %v after calls %v, want p1 three times, one refused hop, one success", err, calls.calls)
	}
	skipped := calls.calls[3]
	if n := calls.count(skipped); n != 1 {
		t.Fatalf("the refused hop to %s was tried %d times, want once: a retried hop runs its admission (and its hold) again", skipped, n)
	}
	if n := calls.count("p1"); n != 3 {
		t.Fatalf("p1 was called %d times, want 3 (its retries are unaffected)", n)
	}
	if got := result.AttemptRecords[3]; got.Outcome != models.OutcomeSkipped || got.Reason != models.ReasonPolicy || got.ErrorClass != models.ErrClassPolicy {
		t.Fatalf("the refused hop's attempt = %+v, want skipped for policy", got)
	}
}

func TestSkippedHopKeepsPreviousError(t *testing.T) {
	router := makeFailoverRouter(t, "gpt-4", "p1", "p2")
	f := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 3}, router, nil, nil)
	down := &models.APIError{Status: 502, Message: "p1 down"}
	calls := &callSequence{results: []error{down, SkipHop(down, models.ReasonPolicy)}}

	_, err := f.Execute(context.Background(), "gpt-4", calls.call)
	if err != down {
		t.Fatalf("Execute error = %#v, want p1's own error, not the skip", err)
	}
	if errors.Is(err, ErrHopSkipped) {
		t.Fatal("the skip sentinel leaked out of Execute")
	}
	if len(calls.calls) != 2 {
		t.Fatalf("calls = %v, want p1 and the refused hop", calls.calls)
	}
}

func TestSkipHopWrapsThePreviousError(t *testing.T) {
	down := &models.APIError{Status: 503}
	skip := SkipHop(down, models.ReasonPolicy)
	if !errors.Is(skip, ErrHopSkipped) {
		t.Fatal("SkipHop's error is not ErrHopSkipped")
	}
	var apiErr *models.APIError
	if !errors.As(skip, &apiErr) || apiErr != down {
		t.Fatal("SkipHop does not unwrap to the previous error")
	}
	reason, prev, ok := SkippedHop(SkipHop(skip, models.ReasonSkippedInsufficientCredits))
	if !ok || reason != models.ReasonSkippedInsufficientCredits || prev != down {
		t.Fatalf("a skip after a skip = (%q, %v, %v), want the latest reason over the original provider error", reason, prev, ok)
	}
	if _, _, ok := SkippedHop(down); ok {
		t.Fatal("a provider error reads as a skip")
	}
	if NewRetryer(config.RetryConfig{Enabled: true}).ShouldRetry(skip) {
		t.Fatal("a skipped hop is retryable")
	}
}
