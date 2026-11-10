package server

import (
	"fmt"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type providerLockPolicy interface {
	IsEnabled() bool
	ExtractLock(*models.RequestContext, http.Header) string
	Validate(string) error
}

// orgProviderLockResolver shares hint extraction with the gateway resolver,
// but applies org patterns to request-local copies of the allow and deny lists.
type orgProviderLockResolver struct {
	*routing.ProviderLockResolver
	allowed, denied []string
}

func (r *orgProviderLockResolver) Validate(target string) error {
	if routing.MatchesLockPattern(r.denied, target) {
		return fmt.Errorf("provider %q is in the deny list and cannot be locked to", target)
	}
	if len(r.allowed) > 0 && !routing.MatchesLockPattern(r.allowed, target) {
		return fmt.Errorf("provider %q is not in the allowed providers list", target)
	}
	return nil
}

// orgFailoverAdmission snapshots the validated lock before any attempt changes
// rc.Provider. Only an explicit, enabled org policy governs failover hops;
// omitted and disabled policies preserve the gateway's existing behavior.
func (h *Handlers) orgFailoverAdmission(rc *models.RequestContext, orgCfg *tenant.OrgConfig) func(string) error {
	if orgCfg == nil || orgCfg.Routing == nil || orgCfg.Routing.ProviderLock == nil || !orgCfg.Routing.ProviderLock.Enabled {
		return func(string) error { return nil }
	}
	policy := h.effectiveProviderLock(orgCfg)
	lockedTarget := ""
	if rc.Metadata["provider_locked"] == "true" {
		lockedTarget = rc.Metadata["provider_lock_target"]
	}
	return func(providerID string) error {
		if (lockedTarget != "" && providerID != lockedTarget) || policy.Validate(providerID) != nil {
			return routing.ErrFailoverTargetDenied
		}
		return nil
	}
}
