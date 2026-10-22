package credits

import (
	"context"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// Plugin is the credits pipeline plugin (pre 510, post after cost).
//
// A managed request (billing_mode managed) is held on its org's managed
// credit before it is dispatched: RESERVE at the request's worst-case cost
// (reserve.go), refused with error-402-v1 or failing closed with 503. After
// the call it is settled at what the cost plugin billed, or voided when nothing was
// billed, and a request finalizer voids a hold nothing settled
// (settle.go). The org's hot-path ledger is OrgLedger.
//
// A managed key (key_type managed) keeps its own prepaid balance, checked
// and debited per request (keycredits.go).
type Plugin struct {
	keyStore    *auth.KeyStore
	enabled     bool
	redisCredit RedisCreditBackend // nil = local-only

	// orgLedger holds managed requests; requireOrgCredits off admits them
	// without a hold (their spend is still settled).
	orgLedger         *OrgLedger
	requireOrgCredits bool

	settings Settings
	// prices, maxOutput and orgMaxTokens are read at call time; nil leaves a
	// model unpriced, its max output unknown and the org's
	// default_max_tokens_estimate unset. Set them before the gateway serves.
	prices       models.PriceLookup
	maxOutput    func(providerID, model string) int
	orgMaxTokens func(orgID string) int
}

// New creates a new credits plugin.
func New(enabled bool, keyStore *auth.KeyStore) *Plugin {
	return &Plugin{
		keyStore: keyStore,
		enabled:  enabled,
		settings: DefaultSettings(),
	}
}

// SetRedisCredit attaches a Redis-backed credit store for managed keys'
// balances, for multi-replica support.
func (p *Plugin) SetRedisCredit(rc RedisCreditBackend) {
	p.redisCredit = rc
}

// SetOrgLedger holds managed requests on their org's ledger. With
// requireCredits false they are admitted without a hold and only settled.
func (p *Plugin) SetOrgLedger(l *OrgLedger, requireCredits bool) {
	p.orgLedger = l
	p.requireOrgCredits = requireCredits
}

// SetSettings sets the managed.* values the hot path uses.
func (p *Plugin) SetSettings(s Settings) { p.settings = s }

// SetPrices sets the price lookup worst-case estimates read, at call time
// (the gateway's injected Handlers.Deps().Prices).
func (p *Plugin) SetPrices(prices models.PriceLookup) { p.prices = prices }

// SetMaxOutput sets the lookup of a model's max output tokens; 0 is unknown.
func (p *Plugin) SetMaxOutput(fn func(providerID, model string) int) { p.maxOutput = fn }

// SetOrgMaxTokens sets the lookup of an org's
// managed_models.default_max_tokens_estimate; 0 is unset.
func (p *Plugin) SetOrgMaxTokens(fn func(orgID string) int) { p.orgMaxTokens = fn }

// OrgLedger returns the managed-credit ledger the plugin holds requests on.
func (p *Plugin) OrgLedger() *OrgLedger { return p.orgLedger }

func (p *Plugin) Name() string  { return "credits" }
func (p *Plugin) Priority() int { return 510 } // After the cost plugin (500), before the run budget (515).

// ShouldSkipOnCacheHit is false: a request reserved before a cache answered
// it must still be voided.
func (p *Plugin) ShouldSkipOnCacheHit() bool { return false }

// orgIDOf is the org a request bills to.
func orgIDOf(rc *models.RequestContext) string {
	if id := rc.Metadata["org_id"]; id != "" {
		return id
	}
	return rc.Metadata["key_org_id"]
}

// isManaged reports whether rc is billed to its org's managed credit.
func isManaged(rc *models.RequestContext) bool {
	return rc.Metadata["billing_mode"] == models.BillingModeManaged && orgIDOf(rc) != ""
}

// ProcessRequest holds a managed request on its org's managed credit, and
// checks a managed key's own balance.
func (p *Plugin) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if !p.enabled {
		return pipeline.ResultContinue()
	}
	if p.orgLedger != nil && isManaged(rc) {
		if apiErr := p.reserve(rc); apiErr != nil {
			return pipeline.ResultError(apiErr)
		}
	}
	return p.checkKeyBalance(rc)
}

// ProcessResponse settles or voids a managed request's hold; a request it
// settled to the org is never also charged to a managed key. Otherwise it
// debits a managed key's own balance.
func (p *Plugin) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if !p.enabled {
		return pipeline.ResultContinue()
	}
	if p.orgLedger != nil && p.finishManaged(rc) {
		return pipeline.ResultContinue()
	}
	return p.chargeKey(rc)
}
