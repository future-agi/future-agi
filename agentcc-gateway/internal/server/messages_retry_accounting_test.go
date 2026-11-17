package server

import (
	"context"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/translation"
)

// sendManagedConfiguredRetry exercises the shared attempt executor with an
// explicitly configured operator retry, while keeping the real org ledger,
// pricing, credits plugin and synthetic upstream. These accounting-unit tests
// must not rely on Messages silently retrying every customer's stream: the
// public route's default is now the same as chat's (no configured retry, none).
func sendManagedConfiguredRetry(t *testing.T, g *managedTestGateway, model string) int {
	t.Helper()
	h := g.srv.handlers
	tr, ok := translation.InboundFor("anthropic")
	if !ok {
		t.Fatal("missing Messages translator")
	}
	req, _, err := tr.RequestToCanonical([]byte(managedMessagesBody(model, true, "")))
	if err != nil {
		t.Fatal(err)
	}
	req.Stream = true
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model, rc.Request, rc.IsStream = model, req, true
	rc.EndpointType, rc.RequestID = "anthropic_messages", "configured-retry-accounting"
	rc.Metadata["org_id"] = "org-m"
	org := g.tenants.Get("org-m")
	p, upstream, ok := h.resolveManaged(rc, org, model)
	if !ok {
		t.Fatal("managed fixture model did not resolve")
	}
	req.Model = upstream
	// Operator scope permits the configured node target; this unit fixture
	// does not alter a key or the public customer-routing policy.
	rc.Metadata["key_type"] = "internal"
	if v := h.checkEligibility(rc, org, p.ID(), upstream); v != nil {
		t.Fatalf("fixture route refused: %+v", v)
	}
	// The gateway's provider-failover router is selected only for models with
	// multiple targets. Round-robin starts at the managed target; MaxAttempts
	// below is one, so its configured retry runs but the spare is never tried.
	if err := h.registry.ReloadRouter(config.RoutingConfig{Targets: map[string][]config.RoutingTargetConfig{
		model: {{Provider: p.ID(), Weight: 1}, {Provider: "operator", Weight: 1}},
	}}); err != nil {
		t.Fatal(err)
	}
	retry := routing.NewRetryer(config.RetryConfig{Enabled: true, MaxRetries: 1, InitialDelay: time.Millisecond,
		MaxDelay: time.Millisecond, Multiplier: 1, OnStatusCodes: []int{502, 504}})
	h.failover.Store(routing.NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 1, OnStatusCodes: []int{502, 504}}, h.registry.Router(), retry, nil))
	w := httptest.NewRecorder()
	h.handleAnthropicStreamViaCanonical(context.Background(), w, rc, p, tr, req, org)
	return w.Code
}
