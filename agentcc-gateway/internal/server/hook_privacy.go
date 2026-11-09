package server

import (
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// resolveContentMode is call-order step 5: it sets rc.Privacy to the most
// restrictive content mode of the org, the key, the request header and the
// global default (privacy.ResolveContentMode). The org is the effective
// config, so a staged rollout's candidate decides for its traffic. Keep
// observeHook as its first call.
func (h *Handlers) resolveContentMode(rc *models.RequestContext, orgCfg *tenant.OrgConfig) {
	h.observeHook(hookResolveContentMode, rc)
	rc.Privacy = privacy.ResolveContentMode(privacy.ContentModeSources{
		Org:     orgCfg.ContentMode(),
		Key:     h.keyLogContent(rc),
		Request: privacy.RequestContentMode(rc.RequestHeaders),
		Default: privacy.DefaultContentMode(),
	})
}

// keyLogContent is the presented key's log_content. The auth plugin has not
// run at step 5, so it comes from the key_log_content the early key peek
// copied, when it did, and otherwise from a read-only lookup of the key.
// Callers cannot set key_* metadata (isBlockedMetadataKey). An empty copy
// only says the key has none, so it is removed rather than logged; the auth
// plugin copies a set value again under the same name.
func (h *Handlers) keyLogContent(rc *models.RequestContext) string {
	if v, ok := rc.Metadata[privacy.KeyLogContentRequestMetadata]; ok {
		if v == "" {
			delete(rc.Metadata, privacy.KeyLogContentRequestMetadata)
		}
		return v
	}
	if key := h.lookupRequestKey(rc); key != nil {
		return key.Metadata[privacy.KeyLogContentMetadata]
	}
	return ""
}
