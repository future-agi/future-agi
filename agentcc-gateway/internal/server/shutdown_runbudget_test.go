package server

import (
	"context"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// countingRunBudget passes every call and counts how often it was asked.
type countingRunBudget struct{ calls atomic.Int32 }

func (c *countingRunBudget) CallCap(*models.RequestContext, *tenant.OrgConfig) *models.APIError {
	c.calls.Add(1)
	return nil
}

func shutdownForTest(t *testing.T, s *Server) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := s.Shutdown(ctx); err != nil {
		t.Fatalf("shutdown: %v", err)
	}
}

// Batch items reach the handlers directly, so one can start after Shutdown
// has released the server's run budget. On a gateway that had a run budget,
// such a call is refused rather than served without the budget's checks.
func TestShutdownKeepsAReleasedRunBudgetClosed(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))
	rb := &countingRunBudget{}
	g.srv.handlers.SetRunBudget(rb)

	if w := g.chat(t, chatBody("anthropic/claude-sonnet-5.5"), nil); w.Code != http.StatusOK || rb.calls.Load() != 1 {
		t.Fatalf("before Shutdown: status %d, run budget asked %d times; body %s", w.Code, rb.calls.Load(), w.Body)
	}
	served := g.managed.calls()

	shutdownForTest(t, g.srv)
	w := g.chat(t, chatBody("anthropic/claude-sonnet-5.5"), nil)

	if w.Code != http.StatusServiceUnavailable || !strings.Contains(w.Body.String(), "budget_state_unavailable") {
		t.Fatalf("after Shutdown: status %d, want 503 budget_state_unavailable; body %s", w.Code, w.Body)
	}
	if got := g.managed.calls(); got != served {
		t.Errorf("after Shutdown the call reached the upstream (%d calls, want %d)", got, served)
	}
}

// A gateway that never had a run budget is not refused by this check after
// Shutdown.
func TestShutdownWithoutARunBudgetAddsNoRunBudgetRefusal(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))

	shutdownForTest(t, g.srv)
	w := g.chat(t, chatBody("anthropic/claude-sonnet-5.5"), nil)

	if strings.Contains(w.Body.String(), "budget_state_unavailable") {
		t.Fatalf("a gateway with no run budget was refused for its run budget after Shutdown: status %d; body %s", w.Code, w.Body)
	}
}
