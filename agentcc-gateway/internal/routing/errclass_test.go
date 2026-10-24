package routing

import (
	"context"
	"errors"
	"fmt"
	"net"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// timeoutNetErr is a net.Error that timed out, as a dial or read deadline does.
type timeoutNetErr struct{ timeout bool }

func (e timeoutNetErr) Error() string   { return "i/o timeout" }
func (e timeoutNetErr) Timeout() bool   { return e.timeout }
func (e timeoutNetErr) Temporary() bool { return false }

var _ net.Error = timeoutNetErr{}

func TestClassifyErrorTable(t *testing.T) {
	apiErr := func(status int, typ, code, msg string) error {
		return &models.APIError{Status: status, Type: typ, Code: code, Message: msg}
	}
	cases := []struct {
		name   string
		err    error
		status int
		class  models.ErrorClass
		origin models.ErrorOrigin
	}{
		{"no error", nil, 0, "", ""},
		{"429 parsed", apiErr(429, "rate_limit_error", "rate_limit_exceeded", "slow down"), 0, models.ErrClassRateLimited, models.OriginUpstream},
		{"5xx mapped to 502", apiErr(502, "server_error", "", "upstream down"), 0, models.ErrClassUpstream5xx, models.OriginUpstream},
		{"unparsed 429 body keeps its status in the code", models.ErrUpstreamProvider(429, "provider error (HTTP 429): busy"), 0, models.ErrClassRateLimited, models.OriginUpstream},
		{"unparsed 503", models.ErrUpstreamProvider(503, "provider error (HTTP 503): <html>"), 0, models.ErrClassUpstream5xx, models.OriginUpstream},
		{"transport failure", models.ErrUpstreamProvider(0, "provider request failed: connection refused"), 0, models.ErrClassNetwork, models.OriginUpstream},
		{"gateway timeout", models.ErrGatewayTimeout("provider request timed out"), 0, models.ErrClassTimeout, models.OriginGateway},
		{"upstream 504 unparsed", models.ErrUpstreamProvider(504, "provider error (HTTP 504): gateway timeout"), 0, models.ErrClassTimeout, models.OriginUpstream},
		{"408", apiErr(408, "timeout_error", "request_timeout", "too slow"), 0, models.ErrClassTimeout, models.OriginUpstream},
		{"401", apiErr(401, "authentication_error", "invalid_api_key", "bad key"), 0, models.ErrClassAuth, models.OriginUpstream},
		{"403", apiErr(403, "permission_error", "", "forbidden"), 0, models.ErrClassAuth, models.OriginUpstream},
		{"402 upstream out of credit", apiErr(402, "", "", "insufficient credits"), 0, models.ErrClassInsufficientCredits, models.OriginUpstream},
		{"400", apiErr(400, "invalid_request_error", "invalid_value", "bad temperature"), 0, models.ErrClassInvalidRequest, models.OriginUpstream},
		{"422", apiErr(422, "invalid_request_error", "", "bad body"), 0, models.ErrClassInvalidRequest, models.OriginUpstream},
		{"404", apiErr(404, "not_found", "model_not_found", "no such model"), 0, models.ErrClassUpstream4xx, models.OriginUpstream},
		{"context length by code", apiErr(400, "invalid_request_error", "context_length_exceeded", "too long"), 0, models.ErrClassContextLength, models.OriginUpstream},
		{"context length by message", apiErr(400, "invalid_request_error", "", "This model's maximum context length is 8192 tokens"), 0, models.ErrClassContextLength, models.OriginUpstream},
		{"anthropic prompt too long", apiErr(400, "invalid_request_error", "", "prompt is too long: 210000 tokens > 200000 maximum"), 0, models.ErrClassContextLength, models.OriginUpstream},
		{"content filter by code", apiErr(400, "invalid_request_error", "content_filter", "blocked"), 0, models.ErrClassContentFilter, models.OriginUpstream},
		{"content policy by code", apiErr(400, "invalid_request_error", "content_policy_violation", "blocked"), 0, models.ErrClassContentFilter, models.OriginUpstream},
		{"context canceled", context.Canceled, 0, models.ErrClassCancelled, models.OriginClient},
		{"wrapped canceled", fmt.Errorf("call: %w", context.Canceled), 0, models.ErrClassCancelled, models.OriginClient},
		{"deadline exceeded", context.DeadlineExceeded, 0, models.ErrClassTimeout, models.OriginGateway},
		{"net timeout", timeoutNetErr{timeout: true}, 0, models.ErrClassTimeout, models.OriginUpstream},
		{"net error", timeoutNetErr{timeout: false}, 0, models.ErrClassNetwork, models.OriginUpstream},
		{"plain error", errors.New(`provider "x" not found`), 0, models.ErrClassUnknown, models.OriginGateway},
		{"plain error with an upstream status", errors.New("bad gateway"), 503, models.ErrClassUpstream5xx, models.OriginUpstream},
		{"status overrides the error's own", apiErr(502, "server_error", "", "x"), 429, models.ErrClassRateLimited, models.OriginUpstream},
		{"skipped for policy", SkipHop(apiErr(502, "server_error", "", "x"), models.ReasonPolicy), 0, models.ErrClassPolicy, models.OriginGateway},
		{"skipped for credits", SkipHop(apiErr(502, "server_error", "", "x"), models.ReasonSkippedInsufficientCredits), 0, models.ErrClassInsufficientCredits, models.OriginGateway},
		{"circuit open", ErrCircuitOpen, 0, models.ErrClassCircuitOpen, models.OriginGateway},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			class, origin := ClassifyError(c.err, c.status)
			if class != c.class || origin != c.origin {
				t.Fatalf("ClassifyError(%v, %d) = (%q, %q), want (%q, %q)", c.err, c.status, class, origin, c.class, c.origin)
			}
		})
	}
}

func TestAttemptReasonFromPreviousError(t *testing.T) {
	cases := []struct {
		prev error
		want string
	}{
		{nil, models.ReasonNone},
		{&models.APIError{Status: 429}, models.ReasonStatus429},
		{&models.APIError{Status: 502}, models.ReasonStatus5xx},
		{models.ErrGatewayTimeout("provider request timed out"), models.ReasonTimeout},
		{models.ErrUpstreamProvider(0, "provider request failed: refused"), models.ReasonNetwork},
		{&models.APIError{Status: 400, Code: "context_length_exceeded"}, models.ReasonContextLength},
		{&models.APIError{Status: 400, Code: "content_filter"}, models.ReasonContentFilter},
		{ErrCircuitOpen, models.ReasonCircuitOpen},
		{&models.APIError{Status: 404}, models.ReasonNone},
		// After a skipped hop the next attempt was triggered by the provider
		// error before the skip, not by the skip.
		{SkipHop(&models.APIError{Status: 503}, models.ReasonPolicy), models.ReasonStatus5xx},
	}
	for _, c := range cases {
		if got := AttemptReason(c.prev); got != c.want {
			t.Errorf("AttemptReason(%v) = %q, want %q", c.prev, got, c.want)
		}
	}
}

func TestAttemptOutcomeFromError(t *testing.T) {
	cases := []struct {
		err  error
		want models.AttemptOutcome
	}{
		{nil, models.OutcomeSuccess},
		{&models.APIError{Status: 500}, models.OutcomeError},
		{models.ErrGatewayTimeout("timed out"), models.OutcomeTimeout},
		{context.Canceled, models.OutcomeCancelled},
		{SkipHop(nil, models.ReasonPolicy), models.OutcomeSkipped},
		{ErrCircuitOpen, models.OutcomeCircuitOpen},
	}
	for _, c := range cases {
		if got, _, _ := AttemptResult(context.Background(), c.err); got != c.want {
			t.Errorf("AttemptResult(%v) outcome = %q, want %q", c.err, got, c.want)
		}
	}
}

// The provider adapters turn an expired or cancelled request context into a
// gateway timeout, so the attempt's own context says which it was.
func TestAttemptResultReadsTheAttemptContext(t *testing.T) {
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	outcome, class, origin := AttemptResult(cancelled, models.ErrGatewayTimeout("provider request timed out"))
	if outcome != models.OutcomeCancelled || class != models.ErrClassCancelled || origin != models.OriginClient {
		t.Fatalf("cancelled attempt = (%q, %q, %q), want cancelled by the client", outcome, class, origin)
	}
	expired, stop := context.WithTimeout(context.Background(), 0)
	defer stop()
	<-expired.Done()
	outcome, class, origin = AttemptResult(expired, models.ErrGatewayTimeout("provider request timed out"))
	if outcome != models.OutcomeTimeout || class != models.ErrClassTimeout || origin != models.OriginGateway {
		t.Fatalf("expired attempt = (%q, %q, %q), want a gateway timeout", outcome, class, origin)
	}
	// An upstream answer that arrived before the context ended keeps its class.
	outcome, class, _ = AttemptResult(cancelled, &models.APIError{Status: 500})
	if outcome != models.OutcomeError || class != models.ErrClassUpstream5xx {
		t.Fatalf("an upstream 500 became (%q, %q) because the context ended later", outcome, class)
	}
}

func TestAttemptErrorMessageScrubbedAndCapped(t *testing.T) {
	secrets := []string{
		"sk-proj-abcdefghijklmnopqrstuvwx",
		"sk-or-v1-0123456789abcdef0123456789abcdef",
		"sk-ant-api03-ABCDEFGHIJKLMNOP",
		"AIzaSyA1234567890abcdefghijklmnopqrstu",
		"AKIAIOSFODNN7EXAMPLE",
		"hunter2secretvalue",
		"eyJhbGciOiJIUzI1NiJ9.payload.sig",
	}
	msg := "Incorrect API key provided: sk-proj-abcdefghijklmnopqrstuvwx (also sk-or-v1-0123456789abcdef0123456789abcdef, " +
		"sk-ant-api03-ABCDEFGHIJKLMNOP); GET https://x/v1?key=AIzaSyA1234567890abcdefghijklmnopqrstu failed; " +
		"aws AKIAIOSFODNN7EXAMPLE; api_key=hunter2secretvalue; Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig"
	got := AttemptErrorMessage(&models.APIError{Status: 401, Message: msg})
	for _, s := range secrets {
		if strings.Contains(got, s) {
			t.Errorf("message still carries %q: %s", s, got)
		}
	}
	if !strings.Contains(got, "Incorrect API key provided") {
		t.Errorf("scrubbing removed the explanation: %s", got)
	}

	long := strings.Repeat("é", 600)
	if got := AttemptErrorMessage(errors.New(long)); len([]rune(got)) != MaxAttemptErrorMessage {
		t.Errorf("a %d-character message became %d characters, want %d", 600, len([]rune(got)), MaxAttemptErrorMessage)
	}
	if got := AttemptErrorMessage(nil); got != "" {
		t.Errorf("no error gave message %q", got)
	}
}

// An attempt's status is the upstream's own when the adapter kept it in the
// error code because the body did not parse: an unparsed 429 or 503 is not a
// 502, and agrees with its error class.
func TestAttemptStatusPrefersUpstreamStatus(t *testing.T) {
	cases := []struct {
		err   error
		want  int
		class models.ErrorClass
	}{
		{&models.APIError{Status: 502, Code: "provider_429"}, 429, models.ErrClassRateLimited},
		{&models.APIError{Status: 502, Code: "provider_503"}, 503, models.ErrClassUpstream5xx},
		{&models.APIError{Status: 502, Code: "provider_0"}, 502, models.ErrClassNetwork},
		{&models.APIError{Status: 502}, 502, models.ErrClassUpstream5xx},
		{&models.APIError{Status: 400, Code: "invalid_value"}, 400, models.ErrClassInvalidRequest},
		{SkipHop(&models.APIError{Status: 502}, models.ReasonPolicy), 0, models.ErrClassPolicy},
		{errors.New("no status"), 0, models.ErrClassUnknown},
	}
	for _, c := range cases {
		if got := AttemptStatus(c.err); got != c.want {
			t.Errorf("AttemptStatus(%v) = %d, want %d", c.err, got, c.want)
		}
		if class, _ := ClassifyError(c.err, 0); class != c.class {
			t.Errorf("ClassifyError(%v) = %q, want %q", c.err, class, c.class)
		}
	}
}
