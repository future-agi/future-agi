package server

import (
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// effectiveOrgConfig is call-order step 4: the org config this request runs
// under. It stamps rc.Evidence with the policy that decides the request: the
// config's "v<meta.version>" (or "unversioned") and the stable variant. With a
// staged rollout (routing.rollout) it buckets the request on its sticky key
// (tenant.RolloutConfig.StickyKey: the session, run, key or request id) and,
// when the bucket falls in the candidate share, returns the candidate config
// stamped with the rollout's version and the candidate variant. Keep
// observeHook as its first call.
func (h *Handlers) effectiveOrgConfig(rc *models.RequestContext, orgCfg *tenant.OrgConfig) *tenant.OrgConfig {
	h.observeHook(hookEffectiveOrgConfig, rc)
	rc.Evidence.PolicyVariant = tenant.PolicyVariantStable
	rc.Evidence.PolicyVersion = orgCfg.PolicyVersion()
	if rc.Evidence.PolicyVersion == "" {
		rc.Evidence.PolicyVersion = tenant.PolicyUnversioned
	}
	rollout := orgCfg.Rollout()
	if rollout == nil || rollout.Percent <= 0 {
		return orgCfg
	}
	// Below 100 percent the request is bucketed; at 100 every request is a
	// candidate, so the sticky ids (and the key lookup) are skipped.
	if rollout.Percent < 100 {
		orgID := rc.Metadata["org_id"]
		if orgID == "" {
			orgID = rc.Metadata[tenant.MetadataKeyOrgID]
		}
		if !rollout.InCandidate(orgID, rollout.StickyKey(h.stickyIDs(rc, rollout))) {
			return orgCfg
		}
	}
	rc.Evidence.PolicyVariant = tenant.PolicyVariantCandidate
	rc.Evidence.PolicyVersion = rollout.Version
	if rc.Evidence.PolicyVersion == "" {
		rc.Evidence.PolicyVersion = tenant.PolicyUnversioned
	}
	return orgCfg.Candidate()
}

// stickyIDs gathers the identifiers the rollout's sticky chain can reach. The
// auth plugin has not run at step 4, so the key id comes from a read-only
// lookup of the presented key, made only when the chain reaches the key.
func (h *Handlers) stickyIDs(rc *models.RequestContext, rollout *tenant.RolloutConfig) tenant.StickyIDs {
	ids := tenant.StickyIDs{SessionID: rc.Agent.SessionID, RunID: rc.Agent.RunID, RequestID: rc.RequestID}
	if !rollout.UsesKeyID(ids) {
		return ids
	}
	if key := h.lookupRequestKey(rc); key != nil {
		ids.KeyID = key.ID
	}
	return ids
}
