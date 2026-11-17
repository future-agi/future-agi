package capability

import (
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The org's own credential comes first: with BYOK for echo-b and managed
// admitted for echo-a, echo-b serves although the org lists echo-a first,
// and the managed echo-a is only behind it, with fallback_to_managed.
func TestByokFirst(t *testing.T) {
	tc := newTestCore(t, coreSetup{capability: "echo", cards: echoFixtureCards(t), managed: []string{"echo-a", "echo-b"}, markupBps: 500})
	org := setFallbackToManaged(withBYOK(orgConfig("echo", []string{"echo-a", "echo-b"}, true), "echo-b"))

	rc, res, err := tc.call(t, "req-byok-first", "echo", Request{Org: org})
	if err != nil {
		t.Fatal(err)
	}
	if res.Provider != "echo-b" || res.Usage.BillingMode != BillingBYOK || res.Attempts[0].CredentialType != models.CredentialBYOK {
		t.Fatalf("served by %s (%s, %s), want echo-b on the org's own key", res.Provider, res.Usage.BillingMode, res.Attempts[0].CredentialType)
	}
	cands, _, apiErr := tc.selectCandidates(rc, "echo", Request{Org: org})
	if apiErr != nil || len(cands) != 2 || cands[0].vendor != "echo-b" || cands[0].managed() || cands[1].vendor != "echo-a" || !cands[1].managed() {
		t.Fatalf("candidates = %+v, want BYOK echo-b then managed echo-a", cands)
	}
	if n := tc.vendors.count("echo-a"); n != 0 {
		t.Fatalf("echo-a called %d times", n)
	}
}

// A managed candidate needs mcp.managed_tools: enabled, a capability/vendor
// glob allowing it and none denying it.
func TestManagedOnlyWhenManagedToolsAllows(t *testing.T) {
	tc := newTestCore(t, coreSetup{capability: "echo", cards: echoFixtureCards(t), managed: []string{"echo-a"}, markupBps: 500})
	off := false
	for name, tt := range map[string]struct {
		edit   func(*tenant.MCPManagedToolsConfig)
		reason string
	}{
		"disabled":    {func(m *tenant.MCPManagedToolsConfig) { m.Enabled = &off }, ReasonManagedToolsDisabled},
		"not allowed": {func(m *tenant.MCPManagedToolsConfig) { m.Allow = []string{"search/*", "fagi.web/*"} }, ReasonNotEnabled},
		"denied":      {func(m *tenant.MCPManagedToolsConfig) { m.Deny = []string{"echo/echo-*"} }, ReasonNotEnabled},
	} {
		t.Run(name, func(t *testing.T) {
			org := orgConfig("echo", []string{"echo-a"}, true)
			tt.edit(org.MCP.ManagedTools)
			_, _, err := tc.call(t, "req-"+name, "echo", Request{Org: org})
			if got := reasonOf(wantAPIError(t, err, http.StatusServiceUnavailable, CodeManagedCapabilityUnavailable)); got != tt.reason {
				t.Fatalf("reason %q, want %q", got, tt.reason)
			}
		})
	}
	_, _, err := tc.call(t, "req-user-token", "echo", Request{Org: orgConfig("echo", []string{"echo-a"}, true), UserToken: true})
	if got := reasonOf(wantAPIError(t, err, http.StatusServiceUnavailable, CodeManagedCapabilityUnavailable)); got != ReasonNotEnabled {
		t.Fatalf("an OAuth principal without allow_user_tokens: reason %q, want not_enabled", got)
	}
	if n := tc.vendors.count("echo-a"); n != 0 {
		t.Fatalf("a refused managed call reached the vendor %d times", n)
	}
	grant(t, tc.ledger, testOrg, 1_000_000)
	on := true
	tokens := orgConfig("echo", []string{"echo-a"}, true)
	tokens.MCP.ManagedTools.AllowUserTokens = &on
	if _, res, err := tc.call(t, "req-user-token-ok", "echo", Request{Org: tokens, UserToken: true}); err != nil || res.Usage.BillingMode != BillingManaged {
		t.Fatalf("allow_user_tokens: %v %+v", err, res.Usage)
	}
	_, res, err := tc.call(t, "req-allowed", "echo", Request{Org: orgConfig("echo", []string{"echo-a"}, true)})
	if err != nil || res.Usage.BillingMode != BillingManaged {
		t.Fatalf("allowed managed call: %v %+v", err, res.Usage)
	}
}

// A byok_only vendor never resolves to managed, however the org opted in;
// with the org's own key it serves.
func TestByokOnlyNeverManaged(t *testing.T) {
	tavily := goldenCard(t, "tavily")
	tc := newTestCore(t, coreSetup{capability: "search", cards: []*Card{tavily}, managed: []string{"tavily"}, markupBps: 500})
	org := orgConfig("search", []string{"tavily"}, true)
	org.MCP.ManagedTools.Allow = []string{"*"}

	_, _, err := tc.call(t, "req-managed", "search", Request{Org: org})
	if got := reasonOf(wantAPIError(t, err, http.StatusServiceUnavailable, CodeManagedCapabilityUnavailable)); got != ReasonBYOKOnly {
		t.Fatalf("reason %q, want byok_only", got)
	}
	_, res, err := tc.call(t, "req-byok", "search", Request{Org: withBYOK(org, "tavily")})
	if err != nil || res.Usage.BillingMode != BillingBYOK {
		t.Fatalf("BYOK call: %v %+v", err, res.Usage)
	}
}

// A failed or lapsed BYOK credential never moves the call to managed unless
// the org sets fallback_to_managed.
func TestNoByokToManagedWithoutFallbackToManaged(t *testing.T) {
	tc := newTestCore(t, coreSetup{capability: "echo", cards: echoFixtureCards(t), managed: []string{"echo-a", "echo-b"}, markupBps: 500})
	grant(t, tc.ledger, testOrg, 1_000_000)
	tc.vendors.fail("echo-a", http.StatusServiceUnavailable)

	org := withBYOK(orgConfig("echo", []string{"echo-a", "echo-b"}, true), "echo-a")
	_, _, err := tc.call(t, "req-no-flag", "echo", Request{Org: org})
	apiErr := wantAPIError(t, err, http.StatusServiceUnavailable, CodeCapabilityProviderUnavailable)
	if refs := apiErr.Metadata.(map[string]any)["attempts"].([]attemptRef); len(refs) != 1 || refs[0].CredentialType != "byok" {
		t.Fatalf("attempts = %+v, want the one BYOK attempt", refs)
	}
	if tc.vendors.count("echo-b") != 0 {
		t.Fatal("a failed BYOK call fell back to managed without fallback_to_managed")
	}

	lapsed := orgConfig("echo", []string{"echo-a"}, true)
	lapsed.Providers["echo-a"] = &tenant.ProviderConfig{APIKey: "org-key", Enabled: false}
	_, _, err = tc.call(t, "req-lapsed", "echo", Request{Org: lapsed})
	if got := reasonOf(wantAPIError(t, err, http.StatusServiceUnavailable, CodeManagedCapabilityUnavailable)); got != ReasonNotEnabled {
		t.Fatalf("lapsed BYOK reason %q, want not_enabled", got)
	}

	_, res, err := tc.call(t, "req-flag", "echo", Request{Org: setFallbackToManaged(org)})
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Attempts) != 2 || res.Attempts[0].CredentialType != models.CredentialBYOK || res.Attempts[1].CredentialType != models.CredentialManaged ||
		res.Provider != "echo-b" {
		t.Fatalf("attempts = %+v, want BYOK echo-a then managed echo-b", res.Attempts)
	}
}

// A native card whose owner has not set the price refuses managed calls.
func TestOwnerPricePendingRefused503(t *testing.T) {
	protect := goldenCard(t, "futureagi")
	tc := newTestCore(t, coreSetup{capability: "guardrails", cards: []*Card{protect}, managed: []string{"futureagi"}, markupBps: 500})
	_, _, err := tc.call(t, "req-pending", "guardrails", Request{Org: orgConfig("guardrails", nil, true)})
	if got := reasonOf(wantAPIError(t, err, http.StatusServiceUnavailable, CodeManagedCapabilityUnavailable)); got != ReasonOwnerPricePending {
		t.Fatalf("reason %q, want owner_price_pending", got)
	}
	if tc.vendors.count("futureagi") != 0 {
		t.Fatal("a price-pending managed call reached the service")
	}
}

// no_divert vendors serve only when the org or the request pins them; they
// never enter an auto pool.
func TestNoDivertOnlyWhenPinned(t *testing.T) {
	tc := newTestCore(t, coreSetup{capability: "search", cards: []*Card{goldenCard(t, "parallel"), goldenCard(t, "exa")}})
	org := withBYOK(orgConfig("search", []string{"parallel", "exa"}, false), "parallel", "exa")

	_, res, err := tc.call(t, "req-auto", "search", Request{Org: org})
	if err != nil || res.Provider != "exa" {
		t.Fatalf("auto pool served %q (%v), want exa: parallel is never in an auto pool", res.Provider, err)
	}
	_, res, err = tc.call(t, "req-pinned", "search", Request{Org: org, Providers: []string{"parallel"}})
	if err != nil || res.Provider != "parallel" {
		t.Fatalf("request pin served %q (%v), want parallel", res.Provider, err)
	}
	pinned := withBYOK(orgConfig("search", []string{"parallel", "exa"}, false), "parallel", "exa")
	pinned.Tools.Capabilities["search"].Pinned = map[string]interface{}{"provider": "parallel"}
	_, res, err = tc.call(t, "req-org-pin", "search", Request{Org: pinned})
	if err != nil || res.Provider != "parallel" {
		t.Fatalf("org pin served %q (%v), want parallel", res.Provider, err)
	}
	only := withBYOK(orgConfig("search", []string{"parallel"}, false), "parallel")
	_, _, err = tc.call(t, "req-unpinned", "search", Request{Org: only})
	if got := reasonOf(wantAPIError(t, err, http.StatusForbidden, CodePolicyTermsViolation)); got != PolicyNoDivertUnpinned {
		t.Fatalf("reason %q, want no_divert_unpinned", got)
	}
}

// A no_divert vendor is never a fallback source or target: its failure is
// 503 capability_provider_unavailable, and a failing vendor before it never
// moves to it.
func TestNoDivertNeverFallsBack(t *testing.T) {
	tc := newTestCore(t, coreSetup{capability: "search", cards: []*Card{goldenCard(t, "parallel"), goldenCard(t, "exa")}})
	org := withBYOK(orgConfig("search", []string{"parallel", "exa"}, false), "parallel", "exa")

	tc.vendors.fail("parallel", http.StatusServiceUnavailable)
	_, _, err := tc.call(t, "req-source", "search", Request{Org: org, Providers: []string{"parallel", "exa"}})
	apiErr := wantAPIError(t, err, http.StatusServiceUnavailable, CodeCapabilityProviderUnavailable)
	if refs := apiErr.Metadata.(map[string]any)["attempts"].([]attemptRef); len(refs) != 1 || refs[0].Provider != "parallel" {
		t.Fatalf("attempts = %+v, want only parallel", refs)
	}
	if tc.vendors.count("exa") != 0 {
		t.Fatal("a failed no_divert vendor fell back to exa")
	}

	tc.vendors.fail("parallel", 0)
	tc.vendors.fail("exa", http.StatusServiceUnavailable)
	_, _, err = tc.call(t, "req-target", "search", Request{Org: org, Providers: []string{"exa", "parallel"}})
	wantAPIError(t, err, http.StatusServiceUnavailable, CodeCapabilityProviderUnavailable)
	if n := tc.vendors.count("parallel"); n != 1 {
		t.Fatalf("parallel called %d times, want 1 (only the pinned call): a no_divert vendor is never a fallback target", n)
	}
}

// zdr_required keeps only zdr_capable vendors.
func TestZdrFilter(t *testing.T) {
	tc := newTestCore(t, coreSetup{capability: "search", cards: []*Card{goldenCard(t, "parallel"), goldenCard(t, "tavily"), goldenCard(t, "exa")}})
	on := true
	org := withBYOK(orgConfig("search", []string{"tavily", "exa"}, false), "tavily", "exa")
	org.Tools.Policy = &tenant.ToolsPolicyConfig{ZDRRequired: &on}
	_, res, err := tc.call(t, "req-zdr", "search", Request{Org: org})
	if err != nil || res.Provider != "exa" {
		t.Fatalf("served %q (%v), want exa, the only zdr_capable vendor", res.Provider, err)
	}
	org.Tools.Capabilities["search"].Providers = []string{"tavily"}
	_, _, err = tc.call(t, "req-zdr-none", "search", Request{Org: org})
	if got := reasonOf(wantAPIError(t, err, http.StatusForbidden, CodePolicyTermsViolation)); got != PolicyZDRRequired {
		t.Fatalf("reason %q, want zdr_required", got)
	}
}

// no_training_vendors_only keeps vendors that do not train on the input: no,
// or ai_features_only for a call that uses no AI feature.
func TestNoTrainingFilter(t *testing.T) {
	trainsYes := card("search", "trainer", 0.001, func(c *Card) { c.TrainsOnInput = "yes" })
	tavily := goldenCard(t, "tavily") // ai_features_only, improvement licence with an opt-out
	tc := newTestCore(t, coreSetup{capability: "search", cards: []*Card{trainsYes, tavily}})
	on := true
	org := withBYOK(orgConfig("search", []string{"trainer", "tavily"}, false), "trainer", "tavily")
	org.Tools.Policy = &tenant.ToolsPolicyConfig{NoTrainingVendorsOnly: &on}

	_, res, err := tc.call(t, "req-no-ai", "search", Request{Org: org})
	if err != nil || res.Provider != "tavily" {
		t.Fatalf("served %q (%v), want tavily (ai_features_only, no AI feature used)", res.Provider, err)
	}
	_, _, err = tc.call(t, "req-ai", "search", Request{Org: org, UsesAIFeatures: true})
	if got := reasonOf(wantAPIError(t, err, http.StatusForbidden, CodePolicyTermsViolation)); got != PolicyNoTraining {
		t.Fatalf("reason %q, want no_training", got)
	}
	if tc.vendors.count("trainer") != 0 {
		t.Fatal("a vendor that trains on input was called under no_training_vendors_only")
	}
}

// Under no_training_vendors_only a vendor with an improvement licence is
// excluded unless the org has opted out of it.
func TestImprovementLicenseExcludedForNoTraining(t *testing.T) {
	licensed := card("search", "licensed", 0.001, func(c *Card) { c.ImprovementLicense = true })
	optedOut := card("search", "optedout", 0.001, func(c *Card) {
		c.ImprovementLicense, c.ImprovementOptOutRef = true, "https://example.invalid/opt-out"
	})
	tc := newTestCore(t, coreSetup{capability: "search", cards: []*Card{licensed, optedOut}})
	on := true
	org := withBYOK(orgConfig("search", []string{"licensed", "optedout"}, false), "licensed", "optedout")
	org.Tools.Policy = &tenant.ToolsPolicyConfig{NoTrainingVendorsOnly: &on}
	_, res, err := tc.call(t, "req-licence", "search", Request{Org: org})
	if err != nil || res.Provider != "optedout" {
		t.Fatalf("served %q (%v), want optedout", res.Provider, err)
	}
	if tc.vendors.count("licensed") != 0 {
		t.Fatal("a vendor holding an improvement licence was called under no_training_vendors_only")
	}
	org.Tools.Policy = nil
	_, res, err = tc.call(t, "req-no-policy", "search", Request{Org: org})
	if err != nil || res.Provider != "licensed" {
		t.Fatalf("without the policy served %q (%v), want licensed", res.Provider, err)
	}
}

// A region pin keeps only vendors serving one of the regions; a card with no
// regions, or none of them, fails closed and nothing is called.
func TestRegionFailsClosed(t *testing.T) {
	usOnly := card("search", "usonly", 0.001, func(c *Card) { c.Regions = []string{"us"} })
	none := card("search", "noregion", 0.001, func(c *Card) { c.Regions = nil })
	euToo := card("search", "eutoo", 0.001)
	tc := newTestCore(t, coreSetup{capability: "search", cards: []*Card{usOnly, none, euToo}})
	org := withBYOK(orgConfig("search", []string{"usonly", "noregion", "eutoo"}, false), "usonly", "noregion", "eutoo")
	org.Tools.Policy = &tenant.ToolsPolicyConfig{Regions: []string{"eu"}}

	_, res, err := tc.call(t, "req-eu", "search", Request{Org: org})
	if err != nil || res.Provider != "eutoo" {
		t.Fatalf("served %q (%v), want eutoo", res.Provider, err)
	}
	org.Tools.Capabilities["search"].Providers = []string{"usonly", "noregion"}
	_, _, err = tc.call(t, "req-none", "search", Request{Org: org})
	if got := reasonOf(wantAPIError(t, err, http.StatusForbidden, CodePolicyTermsViolation)); got != PolicyRegion {
		t.Fatalf("reason %q, want region", got)
	}
	if tc.vendors.count("usonly")+tc.vendors.count("noregion") != 0 {
		t.Fatal("an out-of-region vendor was called")
	}
}
