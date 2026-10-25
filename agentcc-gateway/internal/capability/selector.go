package capability

import (
	"sort"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Reasons a managed candidate is refused (503 managed_capability_unavailable
// metadata.reason).
const (
	ReasonBYOKOnly             = "byok_only"
	ReasonNoManagedCredential  = "no_managed_credential"
	ReasonNotEnabled           = "not_enabled"
	ReasonManagedToolsDisabled = "managed_tools_disabled"
	ReasonOwnerPricePending    = "owner_price_pending"
)

// Reasons the org's policy or a card's terms removed a candidate (403
// policy_terms_violation metadata.reason).
const (
	PolicyCapabilityNotEnabled = "capability_not_enabled"
	PolicyProviderNotAllowed   = "provider_not_allowed"
	PolicyZDRRequired          = "zdr_required"
	PolicyNoTraining           = "no_training"
	PolicyRegion               = "region"
	PolicyNoDivertUnpinned     = "no_divert_unpinned"
)

// candidate is one vendor a call may be sent to, with the credential it would
// use.
type candidate struct {
	vendor  string
	card    *Card
	sku     SKU
	priced  bool // false: the SKU has no list price and the vendor reports the cost
	cred    Credential
	adapter Adapter
}

func (c candidate) managed() bool { return c.cred.Type == models.CredentialManaged }

// refusals records why vendors dropped out, for the error when none is left.
type refusals struct {
	managed string // the first managed refusal reason
	policy  string // the first policy reason
}

// selectCandidates is the selector: the vendors a call
// of capability for req may use, in the order Do tries them. Candidates come
// from the org's tools.capabilities.<cap>.providers order, else the cards'
// default order; the request's provider list narrows them and pins them.
// Each keeps the org's own credential when it has one (BYOK); otherwise it is
// a managed candidate only when mcp.managed_tools admits it. The org's
// policy and each card's terms then filter them, BYOK candidates precede
// managed ones, and at most MaxCandidates remain. A refusal error is returned
// when the org has not enabled the capability, or when the request or the
// org's pins name a vendor the org's list does not hold.
func (c *Core) selectCandidates(rc *models.RequestContext, capability string, req Request) ([]candidate, refusals, *models.APIError) {
	var why refusals
	org := req.Org
	settings := org.ToolsCapability(capability)
	if !settings.Enabled {
		why.policy = PolicyCapabilityNotEnabled
		return nil, why, policyRefusal(capability, PolicyCapabilityNotEnabled)
	}
	vendors, pinned, apiErr := c.vendorOrder(capability, settings, req)
	if apiErr != nil {
		return nil, why, apiErr
	}
	policy := org.ToolsPolicy()
	managedTools := org.MCPManagedTools()

	var byok, managed []candidate
	lapsed := false
	for _, vendor := range vendors {
		adapter, ok := c.reg.Adapter(capability, vendor)
		card := cardOf(c.cards, capability, vendor)
		if !ok || card == nil {
			continue // not served here, or no card: its terms are unknown
		}
		sku, ok := card.SKUFor(req.Tier)
		if !ok {
			continue // the vendor has no SKU for this tier
		}
		cand := candidate{vendor: vendor, card: card, sku: sku, adapter: adapter,
			priced: sku.USDPerUnit > 0 || !sku.CostFromResultMeta}
		if reason := termsRefusal(card, policy, req, pinned); reason != "" {
			if why.policy == "" {
				why.policy = reason
			}
			continue
		}
		cred, state := c.creds.BYOK(org, vendor)
		if state == BYOKUsable {
			cand.cred = cred
			byok = append(byok, cand)
			continue
		}
		lapsed = lapsed || state == BYOKLapsed
		if reason := c.managedRefusal(capability, card, managedTools, req, &cand); reason != "" {
			if why.managed == "" {
				why.managed = reason
			}
			continue
		}
		managed = append(managed, cand)
	}

	// BYOK first; a managed candidate after a usable or lapsed BYOK one only
	// with fallback_to_managed.
	if (len(byok) > 0 || lapsed) && !managedTools.FallbackToManaged {
		if len(managed) > 0 && why.managed == "" {
			why.managed = ReasonNotEnabled
		}
		managed = nil
	}
	if !pinned && len(settings.Providers) == 0 {
		c.rank(capability, byok)
		c.rank(capability, managed)
	} else {
		c.demoteOpen(capability, byok)
		c.demoteOpen(capability, managed)
	}
	out := noDivertAlone(append(byok, managed...))
	if len(out) > MaxCandidates {
		out = out[:MaxCandidates]
	}
	return out, why, nil
}

// vendorOrder is the vendors to consider, in order, and whether they are
// pinned (named by the org's pinned provider or by the request). A request
// may only narrow the org's list: a vendor outside it is refused.
func (c *Core) vendorOrder(capability string, settings tenant.ToolsCapabilitySettings, req Request) ([]string, bool, *models.APIError) {
	base := settings.Providers
	if len(base) == 0 {
		for _, card := range c.cards.Cards(capability) {
			if card != nil {
				base = append(base, card.Vendor)
			}
		}
	}
	pinned := false
	if pins := pinnedProviders(settings.Pinned); len(pins) > 0 {
		narrowed, ok := narrow(base, pins)
		if !ok {
			return nil, false, policyRefusal(capability, PolicyProviderNotAllowed)
		}
		base, pinned = narrowed, true
	}
	if requested := requestedProviders(req.Providers); len(requested) > 0 {
		narrowed, ok := narrow(base, requested)
		if !ok {
			return nil, false, policyRefusal(capability, PolicyProviderNotAllowed)
		}
		base, pinned = narrowed, true
	}
	return base, pinned, nil
}

// narrow is want, in want's order, when every vendor in it is in base.
func narrow(base, want []string) ([]string, bool) {
	for _, v := range want {
		if !contains(base, v) {
			return nil, false
		}
	}
	return want, true
}

// requestedProviders is the request's vendor list, nil for auto.
func requestedProviders(p []string) []string {
	var out []string
	for _, v := range p {
		v = strings.TrimSpace(v)
		if v != "" && v != "auto" && !contains(out, v) {
			out = append(out, v)
		}
	}
	return out
}

// pinnedProviders reads the org's developer-pinned provider option: a vendor
// or a list of vendors under tools.capabilities.<cap>.pinned.provider.
func pinnedProviders(pinned map[string]interface{}) []string {
	switch v := pinned["provider"].(type) {
	case string:
		return requestedProviders([]string{v})
	case []interface{}:
		var names []string
		for _, x := range v {
			if s, ok := x.(string); ok {
				names = append(names, s)
			}
		}
		return requestedProviders(names)
	case []string:
		return requestedProviders(v)
	}
	return nil
}

// termsRefusal is why the org's policy or the card's terms exclude the
// vendor, "" when they do not. A card with no regions fails a region pin.
func termsRefusal(card *Card, policy tenant.ToolsPolicySettings, req Request, pinned bool) string {
	switch {
	case card.NoDivert && !pinned:
		return PolicyNoDivertUnpinned
	case policy.ZDRRequired && !card.ZDRCapable:
		return PolicyZDRRequired
	case policy.NoTrainingVendorsOnly && !trainsOnlyAllowed(card, req):
		return PolicyNoTraining
	case len(policy.Regions) > 0 && !anyShared(card.Regions, policy.Regions):
		return PolicyRegion
	}
	return ""
}

// trainsOnlyAllowed: the vendor does not train on the call's input (no, or
// ai_features_only for a call that uses no AI feature) and holds no
// improvement licence the org has not opted out of.
func trainsOnlyAllowed(card *Card, req Request) bool {
	trains := card.TrainsOnInput == TrainsNo || (card.TrainsOnInput == TrainsAIFeaturesOnly && !req.UsesAIFeatures)
	return trains && (!card.ImprovementLicense || card.ImprovementOptOutRef != "")
}

// managedRefusal is why vendor cannot be a managed candidate, "" when it can,
// in which case cand gets the operator's managed credential: the org opted in
// to managed tools, a <capability>/<vendor> glob allows it (and, for an OAuth
// principal, allow_user_tokens), the card allows managed resale at a price,
// and the operator holds a credential.
func (c *Core) managedRefusal(capability string, card *Card, mt tenant.MCPManagedToolsSettings, req Request, cand *candidate) string {
	if !mt.Enabled {
		return ReasonManagedToolsDisabled
	}
	name := capability + "/" + cand.vendor
	if !anyGlob(mt.Allow, name) || anyGlob(mt.Deny, name) || (req.UserToken && !mt.AllowUserTokens) {
		return ReasonNotEnabled
	}
	if reason := card.managedRefusal(); reason != "" {
		return reason
	}
	cred, ok := c.creds.Managed(cand.vendor)
	if !ok {
		return ReasonNoManagedCredential
	}
	cand.cred = cred
	return ""
}

// noDivertAlone applies no_divert to an ordered list: a no_divert vendor is
// never a fallback source or target, so when it leads it stands alone and
// anywhere else it is dropped.
func noDivertAlone(cands []candidate) []candidate {
	if len(cands) > 0 && cands[0].card.NoDivert {
		return cands[:1]
	}
	out := cands[:0:0]
	for _, cand := range cands {
		if !cand.card.NoDivert {
			out = append(out, cand)
		}
	}
	return out
}

// rank orders an auto pool: closed circuits first, then by p50 latency, then
// by list price. The figures stay inside the gateway (no_perf_disclosure).
func (c *Core) rank(capability string, cands []candidate) {
	sort.SliceStable(cands, func(i, j int) bool {
		a, b := c.health.view(capability, cands[i].vendor), c.health.view(capability, cands[j].vendor)
		if a.open != b.open {
			return !a.open
		}
		if a.p50 != b.p50 {
			return a.p50 < b.p50
		}
		return listPricePico(cands[i]) < listPricePico(cands[j])
	})
}

// demoteOpen keeps an explicit order but moves vendors whose circuit is open
// to the end.
func (c *Core) demoteOpen(capability string, cands []candidate) {
	sort.SliceStable(cands, func(i, j int) bool {
		return !c.health.view(capability, cands[i].vendor).open && c.health.view(capability, cands[j].vendor).open
	})
}

func anyGlob(patterns []string, s string) bool {
	for _, p := range patterns {
		if catalog.MatchGlob(p, s) {
			return true
		}
	}
	return false
}

func anyShared(a, b []string) bool {
	for _, x := range a {
		if contains(b, x) {
			return true
		}
	}
	return false
}

func contains(list []string, v string) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}
