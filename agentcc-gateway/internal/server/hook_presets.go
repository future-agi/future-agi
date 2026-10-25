package server

import (
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// expandPreset is call-order step 8, the step for a named routing preset or
// the step preset of an auto request. It is a no-op: it changes and records
// nothing. Keep observeHook as its first call.
func (h *Handlers) expandPreset(rc *models.RequestContext, orgCfg *tenant.OrgConfig, req *models.ChatCompletionRequest) *models.APIError {
	h.observeHook(hookExpandPreset, rc)
	return nil
}
