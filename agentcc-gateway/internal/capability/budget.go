package capability

import (
	"context"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Budget is the per-run and per-call limit a capability call passes before
// it is sent (max_cost_usd_per_call, run tool budgets).
// Late-bound: the core runs with NoBudget until one is installed.
type Budget interface {
	// Admit refuses a call whose worst case (micros, with the markup) does
	// not fit, with the refusal to return.
	Admit(ctx context.Context, rc *models.RequestContext, capability string, worstCaseMicros int64) *models.APIError
	// Record counts what the call was billed.
	Record(rc *models.RequestContext, capability string, billedMicros int64)
}

// NoBudget admits every call and records nothing.
type NoBudget struct{}

// Admit admits.
func (NoBudget) Admit(context.Context, *models.RequestContext, string, int64) *models.APIError {
	return nil
}

// Record does nothing.
func (NoBudget) Record(*models.RequestContext, string, int64) {}
