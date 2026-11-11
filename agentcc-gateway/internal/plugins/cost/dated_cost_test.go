package cost

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The last instant of 2026 and the first of 2027, both UTC. 1M tokens in and
// 100k out of Gemini 3.x Flash cost 1.125 at the 2026 rates, 2.25 at 2027's.
var (
	t26 = time.Date(2026, 12, 31, 23, 59, 59, 999999999, time.UTC)
	t27 = time.Date(2027, 1, 1, 0, 0, 0, 0, time.UTC)
)

const (
	cost2026 = "1.125000"
	cost2027 = "2.250000"
)

func bundledDB() func() *modeldb.ModelDB {
	db := modeldb.New(modeldb.BundledModels, nil)
	return func() *modeldb.ModelDB { return db }
}

func clockAt(instant time.Time) func() time.Time { return func() time.Time { return instant } }

func TestCostPluginPricesAtTheMomentItCosts(t *testing.T) {
	p := New(true, bundledDB(), nil, nil)

	p.now = clockAt(t26)
	rc := newRC("gemini-3.7-flash", 1_000_000, 100_000)
	p.ProcessResponse(context.Background(), rc)
	if got := rc.Metadata["cost"]; got != cost2026 {
		t.Fatalf("costed on 31 Dec: %q, want %q", got, cost2026)
	}

	// The same plugin and table, past midnight.
	p.now = clockAt(t27)
	rc = newRC("gemini-3.7-flash", 1_000_000, 100_000)
	p.ProcessResponse(context.Background(), rc)
	if got := rc.Metadata["cost"]; got != cost2027 {
		t.Fatalf("costed on 1 Jan: %q, want %q", got, cost2027)
	}
}

func TestResellersAndFallbackNamesFrom2027(t *testing.T) {
	cases := []struct {
		name     string
		provider string
		table    string // the price table name the provider states
		resolved string // the model the upstream reports, when it differs
		model    string
		keeps    bool // priced at its table entry, not the dated row
	}{
		// A reseller with its own entry keeps its price, even under a bare name.
		{"copilot bare 3.7, own entry", "github_copilot", "", "", "gemini-3.7-flash", true},
		{"copilot bare 3.8, own entry", "github_copilot", "", "", "gemini-3.8-flash", true},
		{"aihubmix bare 3.7, own entry", "aihubmix", "", "", "gemini-3.7-flash", true},
		{"openrouter, own entry", "openrouter", "", "", "google/gemini-3.7-flash", true},
		{"perplexity, own entry", "perplexity", "", "", "google/gemini-3.7-flash", true},
		{"deepinfra 3.7, own entry", "deepinfra", "", "", "google/gemini-3.7-flash", true},
		{"copilot prefixed, own entry", "github_copilot", "", "", "github_copilot/gemini-3.7-flash", true},

		// A reseller with no entry of its own already takes Google's price.
		{"copilot 3.6, no own entry", "github_copilot", "", "", "gemini-3.6-flash", false},
		{"deepinfra 3.8, no own entry", "deepinfra", "", "", "google/gemini-3.8-flash", false},
		{"deepinfra 3.6, no own entry", "deepinfra", "", "", "google/gemini-3.6-flash", false},
		{"aihubmix 3.6, no own entry", "aihubmix", "", "", "gemini-3.6-flash", false},
		{"aihubmix 3.8, no own entry", "aihubmix", "", "", "gemini-3.8-flash", false},

		// Google's own providers follow the date, whatever entries exist.
		{"vertex_ai", "vertex_ai", "", "", "gemini-3.7-flash", false},
		{"gemini", "gemini", "", "", "google/gemini-3.7-flash", false},
		{"google by the table name it states", "vertex_ai_global", "vertex_ai", "", "gemini-3.7-flash", false},
		{"no provider", "", "", "", "gemini-3.7-flash", false},

		// The upstream's name is unlisted, so the requested one is priced.
		{"fallback, copilot own entry", "github_copilot", "", "gemini-3.7-flash-unlisted", "gemini-3.7-flash", true},
		{"fallback, deepinfra no own entry", "deepinfra", "", "gemini-3.6-flash-unlisted", "gemini-3.6-flash", false},

		// A reseller under another ID is known by the type its config records.
		{"deepinfra as di, typed", "di", "", "", "google/gemini-3.7-flash", true},
		{"deepinfra as di-untyped, not recognised", "di-untyped", "", "", "google/gemini-3.7-flash", false},
	}

	db := modeldb.New(modeldb.BundledModels, nil)
	for _, tc := range cases {
		// A dated request costs the dated rows' price on each side of
		// midnight. One that keeps the table price costs, on both sides, what
		// the big-table entry the plugin's lookup resolves it to says, read
		// from the table so a refresh that moves that entry does not turn
		// this red. TestBareResellerNameKeepsTodaysPrice pins that price.
		want26, want27 := cost2026, cost2027
		if tc.keeps {
			want26 = tableCost(t, db, tc.model, tc.provider, tc.table)
			want27 = want26
		}
		for _, instant := range []struct {
			at   time.Time
			want string
		}{{t26, want26}, {t27, want27}} {
			p := New(true, bundledDB(), nil, nil)
			p.now = clockAt(instant.at)
			if tc.table != "" {
				table := tc.table
				p.SetPriceTables(func(_, _ string) string { return table })
			}
			p.SetProviderTypes(func(_, id string) string {
				if id == "di" {
					return "deepinfra"
				}
				return ""
			})
			rc := newRC(tc.model, 1_000_000, 100_000)
			rc.Provider = tc.provider
			rc.ResolvedModel = tc.resolved
			p.ProcessResponse(context.Background(), rc)
			if got := rc.Metadata["cost"]; got != instant.want {
				t.Errorf("%s (%s, %s) at %s: cost %q, want %q", tc.name, tc.provider, tc.model, instant.at.Format(time.RFC3339), got, instant.want)
			}
		}
	}
}

// tableCost is the plugin's cost for 1M tokens in and 100k out at the
// big-table price of the entry the plugin's lookup resolves model to.
func tableCost(t *testing.T, db *modeldb.ModelDB, model, provider, table string) string {
	t.Helper()
	key := priceKey(db, model, provider, table)
	m, ok := db.Get(key)
	if !ok {
		t.Fatalf("the table does not list %q (%s, %s)", key, provider, model)
	}
	p := m.Pricing
	return fmt.Sprintf("%.6f", p.Calculate(1_000_000, 100_000, modeldb.CostOptions{}))
}

// A reseller whose upstream reports a bare model name is priced at the big
// table's bare entry, which is Google's, not at the reseller's own entry. Its
// price is right only while that bare entry holds the dated file's 2026 price,
// the price these resellers charge. This test fails on the refresh that moves
// the bare entry, which is when that stops holding.
func TestBareResellerNameKeepsTodaysPrice(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	for _, bare := range []string{"gemini-3.7-flash", "gemini-3.8-flash"} {
		m, ok := db.Get(bare)
		if !ok || m.ID != bare {
			t.Fatalf("the table does not list the bare entry %q", bare)
		}
		table, dated2026 := m.Pricing, m.PricingAt(t26)
		if table.InputPerToken != dated2026.InputPerToken || table.OutputPerToken != dated2026.OutputPerToken ||
			table.CachedInputPerToken != dated2026.CachedInputPerToken {
			t.Fatalf("The big table's bare Gemini Flash price has moved: %s is now input %g, output %g, cached %g per token, "+
				"no longer the dated file's 2026 price (input %g, output %g, cached %g). "+
				"Copilot and AIHubMix requests that report the bare name are priced at this entry, not at their own, "+
				"so this refresh would change what those resellers' customers pay. "+
				"Fix the gateway's reseller lookup order first, so a provider's own entry is found before the bare name; "+
				"this refresh must not merge until then, and this test must not be edited to pass.",
				bare, table.InputPerToken, table.OutputPerToken, table.CachedInputPerToken,
				dated2026.InputPerToken, dated2026.OutputPerToken, dated2026.CachedInputPerToken)
		}
	}

	for _, tc := range []struct{ provider, model string }{
		{"github_copilot", "gemini-3.7-flash"},
		{"github_copilot", "gemini-3.8-flash"},
		{"aihubmix", "gemini-3.7-flash"},
	} {
		p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
		p.now = clockAt(t27)
		rc := newRC(tc.model, 1_000_000, 100_000)
		rc.Provider = tc.provider
		p.ProcessResponse(context.Background(), rc)
		if got := rc.Metadata["cost"]; got != cost2026 {
			t.Errorf("%s reporting the bare name %s was billed %q on 1 Jan 2027, want %q: "+
				"a reseller with its own entry must keep its own price after the dated rows switch.",
				tc.provider, tc.model, got, cost2026)
		}
	}
}

// The configured type is asked for the org whose request is costed, since an
// org's own instance of a provider can record a different type.
func TestProviderTypeIsAskedForTheRequestsOrg(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	p.now = clockAt(t27)
	var askedOrg string
	p.SetProviderTypes(func(orgID, id string) string {
		askedOrg = orgID
		if orgID == "org-1" && id == "di" {
			return "deepinfra"
		}
		return ""
	})
	rc := newRC("google/gemini-3.7-flash", 1_000_000, 100_000)
	rc.Provider = "di"
	rc.Metadata[tenant.MetadataKeyOrgID] = "org-1"
	p.ProcessResponse(context.Background(), rc)
	if askedOrg != "org-1" {
		t.Errorf("the provider type was asked for org %q, want the request's org-1", askedOrg)
	}
	if want := tableCost(t, db, "google/gemini-3.7-flash", "di", ""); rc.Metadata["cost"] != want {
		t.Errorf("org-1's di, typed deepinfra, on 1 Jan: cost %q, want its table price %q", rc.Metadata["cost"], want)
	}
}

func TestOrgCustomPriceBeatsTheDatedPrice(t *testing.T) {
	store := tenant.NewStore()
	store.Set("org-1", &tenant.OrgConfig{CostTracking: &tenant.CostTrackingConfig{
		Enabled:       true,
		CustomPricing: map[string]*tenant.CustomPricing{"gemini-3.7-flash": {InputPerMTok: 1.0, OutputPerMTok: 1.0}},
	}})
	p := New(true, bundledDB(), store, nil)
	p.now = clockAt(t27)
	rc := newRC("gemini-3.7-flash", 1_000_000, 100_000)
	rc.Metadata[tenant.MetadataKeyOrgID] = "org-1"
	p.ProcessResponse(context.Background(), rc)
	if rc.Metadata["cost"] != "1.100000" || rc.Metadata["cost_source"] != "org_custom" {
		t.Fatalf("cost = %q source = %q, want 1.100000 from org_custom", rc.Metadata["cost"], rc.Metadata["cost_source"])
	}
}
