package routing

// A provider timeout is a circuit-breaker failure whatever the breaker's
// status list: the openai, azure and bedrock adapters answer an HTTP-client
// timeout as 504 gateway_timeout, which a list naming 502 but not 504
// still counts. (Breakers have no on_timeout switch: their timeouts are
// always failures, as isFailure's own comment says of non-API timeouts.)

import (
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestCircuitBreakerCountsProviderTimeoutsWhateverTheList(t *testing.T) {
	for _, codes := range [][]int{{500, 502, 503}, {502}, {429}} {
		for name, err := range providerTimeouts {
			cb := NewCircuitBreaker("p", config.CircuitBreakerConfig{Enabled: true, OnStatusCodes: codes}, nil)
			if !cb.isFailure(err) {
				t.Errorf("breaker list %v: %s is not counted as a failure", codes, name)
			}
		}
	}
	// A threshold-1 breaker with the custom list opens on one provider
	// timeout.
	cb := NewCircuitBreaker("p", config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, OnStatusCodes: []int{500, 502, 503}}, nil)
	cb.RecordFailure(providerTimeouts["gateway timeout (the adapters' timeout)"])
	if cb.State() != StateOpen {
		t.Errorf("breaker [500 502 503] after a provider timeout: state %v, want open", cb.State())
	}
	// The list still decides for every other error.
	other := NewCircuitBreaker("p", config.CircuitBreakerConfig{Enabled: true, OnStatusCodes: []int{500, 502, 503}}, nil)
	if other.isFailure(&models.APIError{Status: http.StatusTooManyRequests, Type: models.ErrTypeRateLimit, Code: "rate_limit_exceeded", UpstreamStatus: 429}) {
		t.Errorf("breaker [500 502 503] counts a 429")
	}
	if other.isFailure(models.ErrBadRequest("invalid_request", "bad")) {
		t.Errorf("breaker [500 502 503] counts a 400")
	}
	if !other.isFailure(models.ErrUpstreamProvider(503, "down")) {
		t.Errorf("breaker [500 502 503] does not count an upstream 503")
	}
}
