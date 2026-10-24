package server

import (
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// hedgePlan is call-order step 13: the targets to race the request across,
// or nil for no hedge. targets[0] is the routed target. It plans no hedge:
// it returns nil. Keep observeHook as its first call.
func (h *Handlers) hedgePlan(rc *models.RequestContext, orgCfg *tenant.OrgConfig, targets []routing.RoutingTarget) []routing.RoutingTarget {
	h.observeHook(hookHedgePlan, rc)
	return nil
}
