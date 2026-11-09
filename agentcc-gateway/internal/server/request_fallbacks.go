package server

import (
	"errors"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing/reqopts"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The body's models[] (G26), OpenRouter's ordered fallback list: when the
// request's model fails, the gateway tries each listed model in turn, on
// BYOK and Future AGI traffic alike. Each hop is resolved, judged and held
// as a configured model fallback's is (resolveFallbackProvider,
// admitFallbackHop): a managed hop passes the managed allowlist, takes its
// own credit hold and is billed at its upstream's charge plus the markup;
// a hop the key may not use is skipped. The list, less the request's own
// model, replaces the org's and the gateway's configured chain for this
// request even when nothing is left of it (effectiveFallbackPlan), and is
// never forwarded upstream (reqopts.Parse took it out of the body).

func init() { reqopts.MarkSupported(reqopts.FieldModels) }

// requestFallbacksEnabled preserves the historical unset-switch default.
func requestFallbacksEnabled(orgCfg *tenant.OrgConfig) bool {
	return orgCfg == nil || !orgCfg.Routing.FallbackSet() || orgCfg.Routing.FallbackOn()
}

// requestFallbackErr reports whether err moves a request that sent models[]
// to its next model: a rate limit or an upstream failure, as OpenRouter
// falls back, whatever failover the org configured.
// An explicit false on the effective org disables caller chains as well.
func requestFallbackErr(rc *models.RequestContext, orgCfg *tenant.OrgConfig, err error) bool {
	if !declaresFallbackPlan(rc) || !requestFallbacksEnabled(orgCfg) {
		return false
	}
	var apiErr *models.APIError
	return errors.As(err, &apiErr) && (apiErr.Status == http.StatusTooManyRequests || apiErr.Status >= 500)
}
