// Package keyscope is the pipeline half of scoped keys (CN22c,
// internal/keyscope): a scoped key reaches the pipeline only through the key
// gate's admission, and a budgeted key makes no model call once its period's
// spend reaches its budget.
//
// The key gate (middleware.KeyAuthScoped) refuses a scoped key on every route
// outside its scope before any handler runs. A few pipeline routes are not
// key-gated (/v1beta's Gemini routes take x-goog-api-key or ?key=), so this
// plugin refuses a scoped key the key gate did not admit for this request
// (403 key_scope_denied). It also holds the per-key spend budget: refused
// before the provider call with 402 agent_key_budget_exhausted once the
// period's spend reaches the budget, and each call's cost (the cost plugin's
// "cost") added after it.
package keyscope

import (
	"context"
	"math"
	"strconv"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	ks "github.com/futureagi/agentcc-gateway/internal/keyscope"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// Priority runs the plugin right after auth (20), which records the key it
// authenticated, and before RBAC (30), the cache (35) and every budget.
const Priority = 21

// PostPriority records the call's cost right after the cost plugin (post 1)
// prices it.
const PostPriority = 2

// Plugin is the scoped-key pipeline plugin.
type Plugin struct {
	keys     *auth.KeyStore
	spend    *ks.Spend
	reporter atomic.Pointer[ks.Reporter]
	now      func() time.Time
}

// New returns the plugin over the gateway's key store and a spend store.
func New(keys *auth.KeyStore, spend *ks.Spend) *Plugin {
	return &Plugin{keys: keys, spend: spend, now: time.Now}
}

// SetReporter sets where refusals are reported (the server's reporter, which
// the key gate also uses). Call before the server starts.
func (p *Plugin) SetReporter(r *ks.Reporter) { p.reporter.Store(r) }

func (p *Plugin) Name() string               { return "keyscope" }
func (p *Plugin) Priority() int              { return Priority }
func (p *Plugin) PostPriority() int          { return PostPriority }
func (p *Plugin) ShouldSkipOnCacheHit() bool { return true } // a cache hit cost nothing

// key is the key the auth plugin authenticated for rc, with its metadata,
// or nil.
func (p *Plugin) key(rc *models.RequestContext) (*auth.APIKey, map[string]string) {
	if p.keys == nil {
		return nil, nil
	}
	keyID := rc.Metadata["auth_key_id"]
	if keyID == "" {
		return nil, nil
	}
	key := p.keys.Get(keyID)
	if key == nil {
		return nil, nil
	}
	return key, p.keys.MetadataOf(key)
}

// ProcessRequest refuses a scoped key the key gate did not admit for this
// request, and a budgeted key whose period's budget is spent.
func (p *Plugin) ProcessRequest(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	key, metadata := p.key(rc)
	if key == nil {
		return pipeline.ResultContinue()
	}
	if sc := ks.Parse(metadata); sc.Restricted() {
		if id, ok := middleware.KeyIdentity(ctx); !ok || id.KeyID != key.ID {
			p.report(key.ID, ks.CodeScopeDenied, rc)
			return pipeline.ResultError(ks.ErrScopeDenied(sc, "the", routeOf(rc)))
		}
	}
	b, ok := ks.ParseBudget(metadata)
	if !ok {
		return pipeline.ResultContinue()
	}
	now := p.now()
	if spent := p.spend.Spent(metadata["org_id"], key.ID, b, now); b.Exhausted(spent) {
		p.report(key.ID, ks.CodeBudgetExhausted, rc)
		return pipeline.ResultError(ks.ErrBudgetExhausted(key.ID, b, spent, now))
	}
	return pipeline.ResultContinue()
}

// ProcessResponse adds the call's cost to a budgeted key's spend, whatever
// the call's outcome: a failed call the cost plugin priced was paid for.
func (p *Plugin) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if rc.Flags.CacheHit {
		return pipeline.ResultContinue()
	}
	key, metadata := p.key(rc)
	if key == nil {
		return pipeline.ResultContinue()
	}
	b, ok := ks.ParseBudget(metadata)
	if !ok {
		return pipeline.ResultContinue()
	}
	cost, err := strconv.ParseFloat(rc.Metadata["cost"], 64)
	if err != nil || !(cost > 0) || math.IsInf(cost, 0) {
		return pipeline.ResultContinue()
	}
	p.spend.Record(metadata["org_id"], key.ID, b, int64(math.Round(cost*1_000_000)), p.now())
	return pipeline.ResultContinue()
}

func (p *Plugin) report(keyID, code string, rc *models.RequestContext) {
	p.reporter.Load().Report(ks.Refusal{KeyID: keyID, Code: code, Method: "PIPELINE", Path: routeOf(rc)})
}

// routeOf names the request's endpoint for a refusal (the pipeline does not
// keep the HTTP route): its endpoint type and model.
func routeOf(rc *models.RequestContext) string {
	endpoint := rc.EndpointType
	if endpoint == "" {
		endpoint = "request"
	}
	return endpoint + " endpoint (model " + rc.Model + ")"
}
