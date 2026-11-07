package contracttest

import (
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

type hintedErr struct{ *models.APIError }

func (hintedErr) RetryAfter() time.Duration { return 3 * time.Second }
func (h hintedErr) Unwrap() error           { return h.APIError }

func TestErrorGolden(t *testing.T) {
	api := &models.APIError{Status: 429, Type: "rate_limit_error", Code: "provider_rate_limit_error", Message: "slow down"}
	if got := ErrorGolden(api); got.Status != 429 || got.Code != "provider_rate_limit_error" || got.GoType != "" || got.RetryAfter != "" {
		t.Errorf("APIError = %+v", got)
	}
	if got := ErrorGolden(hintedErr{api}); got.Status != 429 || got.RetryAfter != "3s" {
		t.Errorf("with a retry hint = %+v", got)
	}
	if got := ErrorGolden(errors.New("boom")); got.GoType != "*errors.errorString" || got.Message != "boom" {
		t.Errorf("plain error = %+v", got)
	}
}

func TestAssertSaneError(t *testing.T) {
	api := func(status int) error { return &models.APIError{Status: status, Type: "x", Code: "x", Message: "m"} }
	tests := []struct {
		name    string
		c       Case
		err     error
		want    []string
		mutant  bool
		gapLive bool
	}{
		{"4xx stays 4xx", Case{Status: 400}, api(400), nil, false, false},
		{"4xx became 502", Case{Status: 402}, api(502), []string{"upstream status 402 became 502"}, false, false},
		{"429 must stay 429", Case{Status: 429}, api(400), []string{"a rate limit must stay a 429"}, false, false},
		{"5xx stays 5xx", Case{Status: 529}, api(502), nil, false, false},
		{"5xx became 4xx", Case{Status: 503}, api(400), []string{"must stay a 5xx"}, false, false},
		{"no error", Case{Status: 500}, nil, []string{"returned no error"}, false, false},
		{"not an APIError", Case{Status: 500}, errors.New("x"), []string{"not a *models.APIError"}, false, false},
		{"retry-after dropped", Case{Status: 429, Headers: map[string]string{"Retry-After": "3"}}, api(429),
			[]string{`retry-after: "3" but the canonical error carries no retry hint`}, false, false},
		{"retry-after kept", Case{Status: 429, Headers: map[string]string{"retry-after": "3"}}, hintedErr{api(429).(*models.APIError)}, nil, false, false},
		{"known gap", Case{Status: 429, Headers: map[string]string{"retry-after": "3"},
			SanityGaps: []SanityGap{{Failure: "no retry hint", Reason: "known gap: test"}}}, api(429), nil, false, false},
		{"stale gap", Case{Status: 429, Headers: map[string]string{"retry-after": "3"},
			SanityGaps: []SanityGap{{Failure: "no retry hint", Reason: "known gap: test"}}}, hintedErr{api(429).(*models.APIError)},
			[]string{`known sanity gap "no retry hint" no longer occurs`}, false, false},
		{"stale gap is fine for a mutant", Case{Status: 429, Headers: map[string]string{"retry-after": "3"},
			SanityGaps: []SanityGap{{Failure: "no retry hint", Reason: "known gap: test"}}}, hintedErr{api(429).(*models.APIError)},
			nil, true, false},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			tc.c.mutant = tc.mutant
			ft := runFake(func(tb *fakeTB) { AssertSaneError(tb, tc.c, tc.err) })
			if len(ft.errors) != len(tc.want) {
				t.Fatalf("got %q, want %q", ft.errors, tc.want)
			}
			for i, w := range tc.want {
				if !strings.Contains(ft.errors[i], w) {
					t.Errorf("failure %d = %q, want it to mention %q", i, ft.errors[i], w)
				}
			}
		})
	}
}
