package server

import (
	"context"
	"encoding/json"
	"math"
	"sort"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/modeldb/orgdb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/routing/eligibility"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// autoDefaultVendors are the vendors whose managed-catalog models make up the
// default auto pool. An org widens or narrows it with routing.auto.candidates.
var autoDefaultVendors = map[string]bool{
	"openai": true, "anthropic": true, "google": true, "x-ai": true, "deepseek": true,
	"qwen": true, "mistralai": true, "meta-llama": true, "moonshotai": true, "z-ai": true,
	"minimax": true, "amazon": true, "cohere": true, "nvidia": true, "microsoft": true,
}

// autoMaxCatalogAge keeps models released over a year before the catalog's
// newest out of the default pool: they are superseded at a lower price.
const autoMaxCatalogAge = 365 * 24 * 60 * 60

// defaultAutoCandidates is every model the key can reach that auto routing
// knows enough about: the org's own providers' models, the managed catalog
// when the org has opted in, and for an internal key the global providers'.
func (h *Handlers) defaultAutoCandidates(ctx context.Context, rc *models.RequestContext, orgCfg *tenant.OrgConfig) []routing.AutoCandidate {
	var pool []routing.AutoCandidate
	if orgCfg != nil {
		for providerID, pc := range orgCfg.Providers {
			if pc == nil || !pc.Enabled || !pc.HasCredentials() {
				continue
			}
			for _, m := range pc.Models {
				pool = append(pool, h.autoCandidateFor(orgCfg, providerID, m))
			}
		}
	}
	if !h.orgProvidersOnly(ctx, rc) {
		for _, m := range h.registry.ListAllModels() {
			if p, err := h.registry.Resolve(m.ID); err == nil {
				pool = append(pool, h.autoCandidateFor(orgCfg, p.ID(), m.ID))
			}
		}
	}
	if orgCfg.ManagedModelsEnabled() || !h.orgProvidersOnly(ctx, rc) {
		cat := h.catalog.Load()
		for _, snap := range cat.Snapshots() {
			if !h.registry.IsManaged(snap.ProviderID) {
				continue
			}
			newest := int64(0)
			for _, m := range snap.Models {
				if m.Created > newest {
					newest = m.Created
				}
			}
			for _, m := range snap.Sorted() {
				if inDefaultAutoPool(m, newest) {
					pool = append(pool, h.autoCandidateFor(orgCfg, snap.ProviderID, m.ID))
				}
			}
		}
	}
	return pool
}

// buildAutoPool is the auto pool of call-order step 11: the org's listed
// candidates, or the default pool when it lists none, less the candidates
// checkAutoCandidate refuses, which come back as rejections with the
// verdict's code as the reason. A model offered by several managed sources
// keeps the one ChooseSource picks (chooseManagedSources). The candidate
// auto chooses is judged again as the route (resolveRoute), which gives it
// its endpoint set and effective data policy.
func (h *Handlers) buildAutoPool(ctx context.Context, rc *models.RequestContext, orgCfg *tenant.OrgConfig, listed []string) ([]routing.AutoCandidate, []routing.AutoRejection) {
	var pool []routing.AutoCandidate
	var sentAs map[int]string
	if len(listed) > 0 {
		pool, sentAs = h.listedAutoPool(ctx, rc, orgCfg, listed)
	} else {
		pool = h.defaultAutoCandidates(ctx, rc, orgCfg)
	}
	var rejected []routing.AutoRejection
	kept := pool[:0]
	for i, c := range pool {
		model := c.Model
		if upstream, ok := sentAs[i]; ok {
			model = upstream
		}
		if v := h.checkAutoCandidate(rc, orgCfg, c.ProviderID, model, c.Model); v != nil {
			rejected = append(rejected, routing.AutoRejection{Model: c.Model, Reason: v.Code})
			continue
		}
		kept = append(kept, c)
	}
	return h.chooseManagedSources(rc, orgCfg, kept), rejected
}

// inDefaultAutoPool keeps the managed catalog's current, fixed-price chat
// models from established vendors; variants such as ":free" (rate limited)
// and ":batch" (slow) and safety classifiers are left out.
func inDefaultAutoPool(m *catalog.Model, newest int64) bool {
	if m.DynamicPrice || strings.Contains(m.ID, ":") || !autoDefaultVendors[m.Vendor()] {
		return false
	}
	if m.InputPerToken <= 0 || !m.Produces("text") {
		return false
	}
	lower := strings.ToLower(m.ID)
	for _, skip := range []string{"guard", "safety", "moderation", "embed", "image"} {
		if strings.Contains(lower, skip) {
			return false
		}
	}
	return newest == 0 || m.Created == 0 || newest-m.Created <= autoMaxCatalogAge
}

// listedAutoCandidates resolves the org's chosen candidates to the providers
// the key would reach them through; a managed model also brings every other
// managed source whose catalog lists it, so the source choice can weigh
// them. One the key cannot reach is left out.
func (h *Handlers) listedAutoCandidates(ctx context.Context, rc *models.RequestContext, orgCfg *tenant.OrgConfig, listed []string) []routing.AutoCandidate {
	pool, _ := h.listedAutoPool(ctx, rc, orgCfg, listed)
	return pool
}

// listedAutoPool is listedAutoCandidates with sentAs, which maps the index
// of a candidate sent upstream under another id (a provider-prefixed managed
// model, "openrouter/x-ai/grok-5" sent as "x-ai/grok-5") to that id, which
// eligibility judges; the candidate keeps the listed id, which auto answers
// with and resolution resolves again.
func (h *Handlers) listedAutoPool(ctx context.Context, rc *models.RequestContext, orgCfg *tenant.OrgConfig, listed []string) (pool []routing.AutoCandidate, sentAs map[int]string) {
	orgID := rc.Metadata["org_id"]
	for _, m := range listed {
		if p, providerID, host, _ := h.orgProviderFor(orgID, orgCfg, m); providerID != "" {
			// The org's own provider is sent the id it registered
			// (orgModelFor), on which the candidate is described (as the org
			// sees it: autoCandidateFor) and judged, as a provider-prefixed
			// managed model is.
			if sent := orgModelFor(p, m, host); p != nil && sent != m {
				c := h.autoCandidateFor(orgCfg, providerID, sent)
				c.Model = m
				if sentAs == nil {
					sentAs = map[int]string{}
				}
				sentAs[len(pool)] = sent
				pool = append(pool, c)
				continue
			}
			pool = append(pool, h.autoCandidateFor(orgCfg, providerID, m))
			continue
		}
		if orgCfg.ManagedModelsEnabled() || !h.orgProvidersOnly(ctx, rc) {
			if p, upstream, ok := h.registry.ResolveManaged(m, h.catalogHas); ok && h.managedServes(p.ID(), upstream) {
				c := h.autoCandidateFor(orgCfg, p.ID(), upstream)
				c.Model = m
				if upstream != m {
					if sentAs == nil {
						sentAs = map[int]string{}
					}
					sentAs[len(pool)] = upstream
				}
				pool = append(pool, c)
				for _, other := range h.managedSourcesOf(m) {
					if other != p.ID() && h.managedServes(other, m) {
						pool = append(pool, h.autoCandidateFor(orgCfg, other, m))
					}
				}
				continue
			}
		}
		if !h.orgProvidersOnly(ctx, rc) {
			if p, err := h.registry.Resolve(m); err == nil {
				pool = append(pool, h.autoCandidateFor(orgCfg, p.ID(), m))
			}
		}
	}
	return pool, sentAs
}

// autoCandidate describes model as served by providerID, from the provider's
// own catalog entry when it has one. Its health is the model's own on that
// provider (ModelHealth: latency, call count, success rate) once the model
// has recent calls, so two models behind one managed provider are told
// apart; a model with none keeps the provider's figures, so having no
// history neither helps nor hurts it. The circuit state is the provider's.
func (h *Handlers) autoCandidate(providerID, model string) routing.AutoCandidate {
	return h.autoCandidateFor(nil, providerID, model)
}

// autoCandidateFor is autoCandidate as orgCfg's org sees the model
// (modelInfoFor): its own prices, limits and capability switches.
func (h *Handlers) autoCandidateFor(orgCfg *tenant.OrgConfig, providerID, model string) routing.AutoCandidate {
	c := routing.AutoCandidate{Model: model, ProviderID: providerID, Info: h.modelInfoFor(orgCfg, providerID, model)}
	snap := h.Deps().ModelHealth.Snapshot(providerID, model)
	if h.healthMonitor == nil && snap.N == 0 {
		return c
	}
	health := routing.ProviderHealth{ProviderID: providerID, Healthy: true}
	if h.healthMonitor != nil {
		health = h.healthMonitor.GetHealth(providerID)
	}
	if snap.N > 0 {
		health.RequestCount = int64(snap.N)
		health.ErrorCount = int64(math.Round(snap.ErrorRate * float64(snap.N)))
		health.SuccessRate = snap.SuccessRate
		health.LatencyEWMAMs = int64(math.Round(snap.P50Ms))
	}
	c.Health = &health
	return c
}

// modelInfoFor is what auto knows about model served by providerID, as
// orgCfg's org sees it: the managed catalog's entry, else the model
// database's (modeldb.ForProvider: the provider's own entry, then the entry
// under its model database prefix, "together_ai/" for a "together" provider,
// then the model's general entry); then the org's view on top (orgdb.Info).
// On the org's own provider (BYOK) that is its Cost Tracking price, else its
// Model Database price merged on top of the database's, plus its token limits
// and capability switches, the precedence the cost plugin prices the call
// with; on a managed provider the limits and switches only, since a managed
// call is priced on FutureAGI's card. A model nothing prices stays unpriced
// and auto sets it aside ("no price data").
func (h *Handlers) modelInfoFor(orgCfg *tenant.OrgConfig, providerID, model string) *modeldb.ModelInfo {
	base := h.baseModelInfo(providerID, model)
	if orgCfg == nil || (orgCfg.ModelDatabase == nil && orgCfg.CostTracking == nil) {
		return base
	}
	byok := orgHasCredentials(orgCfg, providerID) || !h.registry.IsManaged(providerID)
	info, _ := orgdb.Info(orgCfg, providerID, model, base, byok)
	if info != nil && info.Mode == "" && base == nil {
		// An entry made of the org's override alone is the org's chat model.
		info.Mode = modeldb.ModeChat
	}
	return info
}

// baseModelInfo is modelInfoFor before the org's view.
func (h *Handlers) baseModelInfo(providerID, model string) *modeldb.ModelInfo {
	if m, ok := h.catalog.Load().Get(providerID, model); ok {
		if m.DynamicPrice {
			return nil
		}
		return m.ModelInfo(providerID+"/"+model, providerID)
	}
	if info, ok := h.ModelDB().ForProvider(providerID, model); ok {
		return info
	}
	return nil
}

// metaSourceChoice records the managed source choices an auto decision made:
// per model offered by several sources, the source kept, those
// it was chosen over and the landed-cost ratio. It is request-log metadata,
// which the org's request explorer shows; it carries a ratio, never a
// landed cost.
const metaSourceChoice = "source_choice"

// maxSourceChoices bounds the source choices recorded per request.
const maxSourceChoices = 10

// sourceChoice is one entry of metadata.source_choice.
type sourceChoice struct {
	Model       string   `json:"model"`
	Chosen      string   `json:"chosen"`
	Over        []string `json:"over"`
	LandedRatio float64  `json:"landed_ratio"`
}

// managedSourcesOf lists the managed providers whose catalogs list model,
// in provider order.
func (h *Handlers) managedSourcesOf(model string) []string {
	var out []string
	for _, snap := range h.catalog.Load().Snapshots() {
		if _, ok := snap.Models[model]; ok && h.registry.IsManaged(snap.ProviderID) {
			out = append(out, snap.ProviderID)
		}
	}
	return out
}

// chooseManagedSources keeps, of every model the pool offers through more
// than one managed source, the source eligibility.ChooseSource picks: the
// lowest billed price, then within 1% of it the lowest landed cost
// (Endpoint.UpstreamFeeBps), then the model's health on the provider. The
// choices are recorded in metadata.source_choice.
func (h *Handlers) chooseManagedSources(rc *models.RequestContext, orgCfg *tenant.OrgConfig, pool []routing.AutoCandidate) []routing.AutoCandidate {
	byModel := map[string][]int{}
	for i, c := range pool {
		if h.registry.IsManaged(c.ProviderID) && !orgHasCredentials(orgCfg, c.ProviderID) {
			byModel[c.Model] = append(byModel[c.Model], i)
		}
	}
	drop := map[int]bool{}
	var choices []sourceChoice
	for model, idx := range byModel {
		if len(idx) < 2 {
			continue
		}
		sources := make([]eligibility.Source, len(idx))
		for j, i := range idx {
			sources[j] = h.managedSource(pool[i].ProviderID, model)
		}
		best, ratio := eligibility.ChooseSource(sources)
		choice := sourceChoice{Model: model, Chosen: sources[best].ProviderID, LandedRatio: math.Round(ratio*1e4) / 1e4}
		for j, i := range idx {
			if j != best {
				drop[i] = true
				choice.Over = append(choice.Over, sources[j].ProviderID)
			}
		}
		choices = append(choices, choice)
	}
	if len(drop) == 0 {
		return pool
	}
	kept := pool[:0]
	for i, c := range pool {
		if !drop[i] {
			kept = append(kept, c)
		}
	}
	sort.Slice(choices, func(i, j int) bool { return choices[i].Model < choices[j].Model })
	if len(choices) > maxSourceChoices {
		choices = choices[:maxSourceChoices]
	}
	if b, err := json.Marshal(choices); err == nil {
		rc.SetMetadata(metaSourceChoice, string(b))
	}
	return kept
}

// managedSource describes model's managed source on providerID: the
// catalog's blended price (3 prompt to 1 completion), the source's fee from
// its endpoints (unknown when it lists none), and the model's health there.
func (h *Handlers) managedSource(providerID, model string) eligibility.Source {
	s := eligibility.Source{ProviderID: providerID}
	if m := h.catalogEntry(providerID, model); m != nil {
		s.Price = (3*m.Prices.Prompt + m.Prices.Completion) / 4
		for _, e := range m.Endpoints {
			if e.Provider == "" || e.Provider == providerID {
				s.UpstreamFeeBps, s.FeeKnown = e.UpstreamFeeBps, true
				break
			}
		}
	}
	if snap := h.Deps().ModelHealth.Snapshot(providerID, model); snap.N > 0 {
		s.SuccessRate, s.HealthKnown = snap.SuccessRate, true
	}
	return s
}
