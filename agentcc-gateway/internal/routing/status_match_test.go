package routing

// Retry, failover and breaker status lists match the upstream's own status
// or the gateway's, and the on_timeout switches act on a provider timeout.

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// upstreamErrors are the errors a provider adapter builds for an upstream
// that answered 503 (or another status), each as the adapter shapes it.
func upstreamErrors() map[string]struct {
	err      error
	upstream int
} {
	return map[string]struct {
		err      error
		upstream int
	}{
		"unparsed body (ErrUpstreamProvider)": {models.ErrUpstreamProvider(503, "provider error (HTTP 503): <html>"), 503},
		"parsed body, status kept on the error": {&models.APIError{Status: http.StatusBadGateway, Type: "server_error", Code: "unavailable",
			Message: "upstream unavailable", UpstreamStatus: 503}, 503},
		"parsed body, status in the code (cohere, bedrock)": {&models.APIError{Status: http.StatusBadGateway, Type: models.ErrTypeServer,
			Code: "provider_503", Message: "x"}, 503},
		"gemini UNAVAILABLE":         {&models.APIError{Status: http.StatusBadGateway, Type: models.ErrTypeServer, Code: "provider_UNAVAILABLE", Message: "x"}, 503},
		"anthropic api_error":        {&models.APIError{Status: http.StatusBadGateway, Type: models.ErrTypeServer, Code: "provider_api_error", Message: "x"}, 500},
		"anthropic overloaded_error": {&models.APIError{Status: http.StatusBadGateway, Type: models.ErrTypeServer, Code: "provider_overloaded_error", Message: "x"}, 529},
		"wrapped":                    {fmt.Errorf("attempt: %w", models.ErrUpstreamProvider(503, "x")), 503},
	}
}

func TestUpstreamStatusMatchRetryer(t *testing.T) {
	for name, tc := range upstreamErrors() {
		own := NewRetryer(config.RetryConfig{Enabled: true, OnStatusCodes: []int{tc.upstream}})
		if !own.ShouldRetry(tc.err) {
			t.Errorf("%s: a retry list [%d] does not match the upstream's %d", name, tc.upstream, tc.upstream)
		}
		gw := NewRetryer(config.RetryConfig{Enabled: true, OnStatusCodes: []int{502}})
		if !gw.ShouldRetry(tc.err) {
			t.Errorf("%s: a retry list [502] does not match the gateway's 502", name)
		}
		other := NewRetryer(config.RetryConfig{Enabled: true, OnStatusCodes: []int{429}})
		if other.ShouldRetry(tc.err) {
			t.Errorf("%s: a retry list [429] matches an upstream %d", name, tc.upstream)
		}
		def := NewRetryer(config.RetryConfig{Enabled: true})
		if !def.ShouldRetry(tc.err) {
			t.Errorf("%s: the default retry list does not match", name)
		}
	}
	// A client error is matched by its own status.
	bad := &models.APIError{Status: http.StatusBadRequest, Code: "invalid_request_error", UpstreamStatus: 400}
	if NewRetryer(config.RetryConfig{Enabled: true, OnStatusCodes: []int{503}}).ShouldRetry(bad) {
		t.Error("a retry list [503] matches an upstream 400")
	}
}

func TestUpstreamStatusMatchFailover(t *testing.T) {
	for name, tc := range upstreamErrors() {
		if !NewFailover(config.FailoverConfig{Enabled: true, OnStatusCodes: []int{tc.upstream}}, nil, nil, nil).ShouldFailover(tc.err) {
			t.Errorf("%s: a failover list [%d] does not match the upstream's %d", name, tc.upstream, tc.upstream)
		}
		if !NewFailover(config.FailoverConfig{Enabled: true, OnStatusCodes: []int{502}}, nil, nil, nil).ShouldFailover(tc.err) {
			t.Errorf("%s: a failover list [502] does not match the gateway's 502", name)
		}
		if NewFailover(config.FailoverConfig{Enabled: true, OnStatusCodes: []int{429}}, nil, nil, nil).ShouldFailover(tc.err) {
			t.Errorf("%s: a failover list [429] matches an upstream %d", name, tc.upstream)
		}
	}
}

func TestUpstreamStatusMatchCircuitBreaker(t *testing.T) {
	for name, tc := range upstreamErrors() {
		own := NewCircuitBreaker("p", config.CircuitBreakerConfig{Enabled: true, OnStatusCodes: []int{tc.upstream}}, nil)
		if !own.isFailure(tc.err) {
			t.Errorf("%s: a breaker list [%d] does not count the upstream's %d", name, tc.upstream, tc.upstream)
		}
		gw := NewCircuitBreaker("p", config.CircuitBreakerConfig{Enabled: true, OnStatusCodes: []int{502}}, nil)
		if !gw.isFailure(tc.err) {
			t.Errorf("%s: a breaker list [502] does not count the gateway's 502", name)
		}
		other := NewCircuitBreaker("p", config.CircuitBreakerConfig{Enabled: true, OnStatusCodes: []int{429}}, nil)
		if other.isFailure(tc.err) {
			t.Errorf("%s: a breaker list [429] counts an upstream %d", name, tc.upstream)
		}
	}
	// One threshold-1 breaker with [503] opens on an upstream 503.
	cb := NewCircuitBreaker("p", config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, OnStatusCodes: []int{503}}, nil)
	cb.RecordFailure(models.ErrUpstreamProvider(503, "x"))
	if cb.State() != StateOpen {
		t.Errorf("breaker [503] after an upstream 503: state %v, want open", cb.State())
	}
}

// providerTimeouts are the errors a provider adapter returns when the
// provider did not answer in time.
var providerTimeouts = map[string]error{
	"gateway timeout (the adapters' timeout)": models.ErrGatewayTimeout("provider request timed out"),
	"upstream 504 (unparsed)":                 models.ErrUpstreamProvider(504, "provider error (HTTP 504): gateway timeout"),
	"upstream 408":                            &models.APIError{Status: http.StatusRequestTimeout, Type: models.ErrTypeTimeout, Code: "request_timeout", UpstreamStatus: 408},
	"deadline":                                context.DeadlineExceeded,
}

func TestRetryOnTimeoutRetryer(t *testing.T) {
	for name, err := range providerTimeouts {
		on := NewRetryer(config.RetryConfig{Enabled: true, OnStatusCodes: []int{500}, OnTimeout: true})
		if !on.ShouldRetry(err) {
			t.Errorf("%s: Retry on Timeout with list [500] does not retry it", name)
		}
		off := NewRetryer(config.RetryConfig{Enabled: true, OnStatusCodes: []int{500}})
		if off.ShouldRetry(err) {
			t.Errorf("%s: retried with Retry on Timeout off and list [500]", name)
		}
	}
	// 504 keeps matching a list that names it.
	if !NewRetryer(config.RetryConfig{Enabled: true, OnStatusCodes: []int{504}}).ShouldRetry(models.ErrGatewayTimeout("x")) {
		t.Error("a retry list [504] does not match a gateway timeout")
	}
	// A non-timeout error is not a timeout.
	if NewRetryer(config.RetryConfig{Enabled: true, OnStatusCodes: []int{500}, OnTimeout: true}).ShouldRetry(models.ErrUpstreamProvider(503, "x")) {
		t.Error("Retry on Timeout retries an upstream 503")
	}
	if NewRetryer(config.RetryConfig{Enabled: true, OnStatusCodes: []int{500}, OnTimeout: true}).ShouldRetry(errors.New("boom")) {
		t.Error("Retry on Timeout retries a plain error")
	}
}

func TestRetryOnTimeoutFailover(t *testing.T) {
	for name, err := range providerTimeouts {
		on := NewFailover(config.FailoverConfig{Enabled: true, OnStatusCodes: []int{503}, OnTimeout: true}, nil, nil, nil)
		if !on.ShouldFailover(err) {
			t.Errorf("%s: failover on_timeout with list [503] does not fail over", name)
		}
		off := NewFailover(config.FailoverConfig{Enabled: true, OnStatusCodes: []int{503}}, nil, nil, nil)
		if off.ShouldFailover(err) {
			t.Errorf("%s: failed over with on_timeout off and list [503]", name)
		}
	}
}
