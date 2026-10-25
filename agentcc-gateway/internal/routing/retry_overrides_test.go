package routing

import (
	"context"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func retryHeaders(kv ...string) http.Header {
	h := http.Header{}
	for i := 0; i+1 < len(kv); i += 2 {
		h.Set(kv[i], kv[i+1])
	}
	return h
}

// failingTries runs r with overrides o on ctx, every try failing with a
// retryable 503, and returns how many tries it made.
func failingTries(t *testing.T, r *Retryer, o RetryOverrides) int {
	t.Helper()
	tries := 0
	_, err := r.ExecuteObserved(WithRetryOverrides(context.Background(), o), func() error {
		tries++
		return &models.APIError{Status: 503}
	}, nil)
	if err == nil {
		t.Fatal("every try failed, so the run must end with an error")
	}
	return tries
}

// TestRetryOverridesOnlyTighten: the request's retry headers can lower the
// org's tries per target, never raise them, and never turn retries on.
func TestRetryOverridesOnlyTighten(t *testing.T) {
	t.Run("parse", func(t *testing.T) {
		o, apiErr := ParseRetryOverrides(retryHeaders(HeaderMaxAttempts, "2", HeaderRetryDelayMs, "250", HeaderBackoff, "Linear"))
		if apiErr != nil {
			t.Fatalf("ParseRetryOverrides: %v", apiErr)
		}
		if o.MaxAttempts != 2 || !o.DelaySet || o.Delay != 250*time.Millisecond || o.Backoff != BackoffLinear {
			t.Fatalf("overrides = %+v, want 2 attempts, 250ms, linear", o)
		}
		if o, apiErr := ParseRetryOverrides(http.Header{}); apiErr != nil || !o.IsZero() {
			t.Fatalf("no headers = (%+v, %v), want zero overrides", o, apiErr)
		}
		if o, apiErr := ParseRetryOverrides(retryHeaders(HeaderRetryDelayMs, "0")); apiErr != nil || !o.DelaySet || o.Delay != 0 || o.IsZero() {
			t.Fatalf("a delay of 0 = (%+v, %v), want a set delay of 0", o, apiErr)
		}
		for _, bad := range [][2]string{
			{HeaderMaxAttempts, "0"}, {HeaderMaxAttempts, "6"}, {HeaderMaxAttempts, "two"}, {HeaderMaxAttempts, "-1"},
			{HeaderRetryDelayMs, "60001"}, {HeaderRetryDelayMs, "-5"}, {HeaderRetryDelayMs, "1.5"},
			{HeaderRetryDelayMs, "10000000000000"}, // overflows a Duration when scaled to ns
			{HeaderRetryDelayMs, "99999999999999999999"},
			{HeaderBackoff, "fibonacci"}, {HeaderBackoff, ""},
		} {
			h := http.Header{}
			h[http.CanonicalHeaderKey(bad[0])] = []string{bad[1]}
			_, apiErr := ParseRetryOverrides(h)
			if apiErr == nil || apiErr.Status != http.StatusBadRequest || apiErr.Code != ErrCodeInvalidRetryHeader ||
				apiErr.Param == nil || *apiErr.Param != bad[0] {
				t.Errorf("%s: %q = %+v, want 400 %s with param %s", bad[0], bad[1], apiErr, ErrCodeInvalidRetryHeader, bad[0])
			}
		}
		if _, apiErr := ParseRetryOverrides(retryHeaders(HeaderRetryDelayMs, "60000")); apiErr != nil {
			t.Errorf("60000 ms is the bound, and allowed: %v", apiErr)
		}
	})

	t.Run("tries never exceed the org's", func(t *testing.T) {
		org := fastRetryer() // MaxRetries 2: 3 tries per target
		for _, tc := range []struct {
			header, want int
		}{{0, 3}, {1, 1}, {2, 2}, {3, 3}, {5, 3}} {
			if got := failingTries(t, org, RetryOverrides{MaxAttempts: tc.header}); got != tc.want {
				t.Errorf("x-agentcc-max-attempts %d against an org maximum of 3 made %d tries, want %d", tc.header, got, tc.want)
			}
		}
	})

	t.Run("a header never turns retries on", func(t *testing.T) {
		disabled := NewRetryer(config.RetryConfig{Enabled: false, MaxRetries: 4, InitialDelay: time.Microsecond})
		for _, r := range []*Retryer{nil, {}, disabled} {
			if got := failingTries(t, r, RetryOverrides{MaxAttempts: 5}); got != 1 {
				t.Errorf("retries off: x-agentcc-max-attempts 5 made %d tries, want 1", got)
			}
		}
	})

	t.Run("overrides reach the failover loop through the context", func(t *testing.T) {
		router := makeFailoverRouter(t, "gpt-4", "p1", "p2")
		f := NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 2}, router, fastRetryer(), nil)
		calls := &scriptedCalls{script: map[string][]error{
			"p1": {&models.APIError{Status: 503}, &models.APIError{Status: 503}, &models.APIError{Status: 503}},
			"p2": {&models.APIError{Status: 503}, &models.APIError{Status: 503}, &models.APIError{Status: 503}},
		}}
		ctx := WithRetryOverrides(context.Background(), RetryOverrides{MaxAttempts: 1})
		if _, err := f.Execute(ctx, "gpt-4", calls.call); err == nil {
			t.Fatal("every call failed; the run must fail")
		}
		if calls.count("p1") != 1 || calls.count("p2") != 1 {
			t.Fatalf("calls = %v, want one try per target under x-agentcc-max-attempts 1 (the org allows 3)", calls.calls)
		}
	})

	t.Run("delay and backoff", func(t *testing.T) {
		org := NewRetryer(config.RetryConfig{Enabled: true, MaxRetries: 4, InitialDelay: 100 * time.Millisecond, MaxDelay: time.Second, Multiplier: 3})
		for _, tc := range []struct {
			name string
			o    RetryOverrides
			want []time.Duration
		}{
			{"constant", RetryOverrides{Delay: 200 * time.Millisecond, DelaySet: true, Backoff: BackoffConstant},
				[]time.Duration{200 * time.Millisecond, 200 * time.Millisecond, 200 * time.Millisecond}},
			{"linear", RetryOverrides{Delay: 200 * time.Millisecond, DelaySet: true, Backoff: BackoffLinear},
				[]time.Duration{200 * time.Millisecond, 400 * time.Millisecond, 600 * time.Millisecond}},
			{"exponential uses the org multiplier", RetryOverrides{Delay: 200 * time.Millisecond, DelaySet: true, Backoff: BackoffExponential},
				[]time.Duration{200 * time.Millisecond, 600 * time.Millisecond, 1800 * time.Millisecond}},
			{"a delay alone is exponential", RetryOverrides{Delay: 10 * time.Millisecond, DelaySet: true},
				[]time.Duration{10 * time.Millisecond, 30 * time.Millisecond, 90 * time.Millisecond}},
			{"a backoff alone starts at the org delay", RetryOverrides{Backoff: BackoffLinear},
				[]time.Duration{100 * time.Millisecond, 200 * time.Millisecond, 300 * time.Millisecond}},
			{"a zero delay retries at once", RetryOverrides{Delay: 0, DelaySet: true, Backoff: BackoffExponential},
				[]time.Duration{0, 0, 0}},
			{"capped at 60 s, not at the org's 1 s", RetryOverrides{Delay: 40 * time.Second, DelaySet: true, Backoff: BackoffLinear},
				[]time.Duration{40 * time.Second, MaxRetryDelayOverride, MaxRetryDelayOverride}},
		} {
			p := org.planFor(tc.o)
			for i, want := range tc.want {
				if got := p.delay(i); got != want {
					t.Errorf("%s: delay before retry %d = %v, want %v", tc.name, i+1, got, want)
				}
			}
		}
		// Without a delay or backoff header the org's own jittered schedule
		// stays: each delay is below the org's capped exponential bound.
		p := org.planFor(RetryOverrides{MaxAttempts: 2})
		for i, bound := range []time.Duration{100 * time.Millisecond, 300 * time.Millisecond, 900 * time.Millisecond, time.Second} {
			for n := 0; n < 50; n++ {
				if got := p.delay(i); got < 0 || got >= bound {
					t.Fatalf("org schedule: delay before retry %d = %v, want within [0, %v)", i+1, got, bound)
				}
			}
		}
		if p.retries != 1 {
			t.Errorf("x-agentcc-max-attempts 2 planned %d retries, want 1", p.retries)
		}
	})

	t.Run("the planned delay is what the loop waits", func(t *testing.T) {
		org := NewRetryer(config.RetryConfig{Enabled: true, MaxRetries: 2, InitialDelay: time.Hour, MaxDelay: time.Hour})
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		start := time.Now()
		tries := 0
		_, _ = org.ExecuteObserved(WithRetryOverrides(ctx, RetryOverrides{Delay: 0, DelaySet: true, Backoff: BackoffConstant}), func() error {
			tries++
			return &models.APIError{Status: 503}
		}, nil)
		if tries != 3 || time.Since(start) > 2*time.Second {
			t.Fatalf("made %d tries in %v; a 0 ms header delay must replace the org's 1 h delay", tries, time.Since(start))
		}
	})

	t.Run("no overrides on the context is the org's behaviour", func(t *testing.T) {
		if o, ok := RetryOverridesFromContext(context.Background()); ok || !o.IsZero() {
			t.Fatalf("RetryOverridesFromContext(background) = (%+v, %v)", o, ok)
		}
		ctx := context.Background()
		if WithRetryOverrides(ctx, RetryOverrides{}) != ctx {
			t.Error("zero overrides must leave the context as it is")
		}
		if got := failingTries(t, fastRetryer(), RetryOverrides{}); got != 3 {
			t.Errorf("made %d tries, want the org's 3", got)
		}
	})

	t.Run("the no-header path allocates nothing", func(t *testing.T) {
		// Every chat request parses these headers, so a request without
		// them allocates nothing here.
		h := http.Header{"Authorization": {"Bearer k"}, "Content-Type": {"application/json"}}
		ctx := context.Background()
		allocs := testing.AllocsPerRun(100, func() {
			o, apiErr := ParseRetryOverrides(h)
			if apiErr != nil {
				t.Fatal(apiErr)
			}
			if _, ok := RetryOverridesFromContext(WithRetryOverrides(ctx, o)); ok {
				t.Fatal("no headers must put nothing on the context")
			}
		})
		if allocs != 0 {
			t.Fatalf("parsing a request without retry headers made %v allocations, want 0", allocs)
		}
	})

	t.Run("the error names the header", func(t *testing.T) {
		_, apiErr := ParseRetryOverrides(retryHeaders(HeaderMaxAttempts, "9"))
		if apiErr == nil || !strings.Contains(apiErr.Message, HeaderMaxAttempts) {
			t.Fatalf("error = %+v, want a message naming %s", apiErr, HeaderMaxAttempts)
		}
	})
}
