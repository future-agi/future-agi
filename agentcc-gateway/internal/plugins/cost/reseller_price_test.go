package cost

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
)

// tFeb is a month past the switch, when the dated rows may be gone.
var tFeb = time.Date(2027, 2, 1, 0, 0, 0, 0, time.UTC)

func ptr[T any](v T) *T { return &v }

// resellerCells pins each reseller's own entry for each model as today's
// table lists it, "" for none. An entry under "<reseller>/google/" is what
// the reseller's own "google/" name resolves to without the rule.
var resellerCells = []struct{ provider, version, own string }{
	{"github_copilot", "3.6", ""},
	{"github_copilot", "3.7", "github_copilot/gemini-3.7-flash"},
	{"github_copilot", "3.8", "github_copilot/gemini-3.8-flash"},
	{"aihubmix", "3.6", ""},
	{"aihubmix", "3.7", "aihubmix/gemini-3.7-flash"},
	{"aihubmix", "3.8", ""},
	{"deepinfra", "3.6", ""},
	{"deepinfra", "3.7", "deepinfra/google/gemini-3.7-flash"},
	{"deepinfra", "3.8", ""},
	{"openrouter", "3.6", "openrouter/google/gemini-3.6-flash"},
	{"openrouter", "3.7", "openrouter/google/gemini-3.7-flash"},
	{"openrouter", "3.8", "openrouter/google/gemini-3.8-flash"},
	{"perplexity", "3.6", "perplexity/google/gemini-3.6-flash"},
	{"perplexity", "3.7", "perplexity/google/gemini-3.7-flash"},
	{"perplexity", "3.8", "perplexity/google/gemini-3.8-flash"},
}

// nameForms are the names an upstream reports these models by.
var nameForms = []string{"gemini-%s-flash", "google/gemini-%s-flash", "gemini/gemini-%s-flash", "vertex_ai/gemini-%s-flash"}

// todaysFlash is today's input and output price per token for Google's nine
// keys and the resellers' own entries of these models.
var todaysFlash = map[string][2]float64{
	"gemini-3.6-flash": {7.5e-7, 3.75e-6}, "gemini-3.7-flash": {7.5e-7, 3.75e-6}, "gemini-3.8-flash": {7.5e-7, 3.75e-6},
	"gemini/gemini-3.6-flash": {7.5e-7, 3.75e-6}, "gemini/gemini-3.7-flash": {7.5e-7, 3.75e-6}, "gemini/gemini-3.8-flash": {7.5e-7, 3.75e-6},
	"vertex_ai/gemini-3.6-flash": {7.5e-7, 3.75e-6}, "vertex_ai/gemini-3.7-flash": {7.5e-7, 3.75e-6}, "vertex_ai/gemini-3.8-flash": {7.5e-7, 3.75e-6},
	"github_copilot/gemini-3.7-flash": {7.5e-7, 3.75e-6}, "github_copilot/gemini-3.8-flash": {7.5e-7, 3.75e-6},
	"aihubmix/gemini-3.7-flash": {7.5e-7, 3.75e-6}, "deepinfra/google/gemini-3.7-flash": {7.5e-7, 3.75e-6},
	"openrouter/google/gemini-3.6-flash": {7.5e-7, 3.75e-6}, "openrouter/google/gemini-3.7-flash": {7.5e-7, 3.75e-6}, "openrouter/google/gemini-3.8-flash": {7.5e-7, 3.75e-6},
	"perplexity/google/gemini-3.6-flash": {1.5e-6, 7.5e-6}, "perplexity/google/gemini-3.7-flash": {7.5e-7, 3.75e-6}, "perplexity/google/gemini-3.8-flash": {7.5e-7, 3.75e-6},
}

// flashTable lists only these models' keys, at today's input and output
// prices, then moves applied. A key the bundled table lists is a struct copy
// of it, which keeps the rates no bill reads; any other key is added. Every
// input and output price a bill reads is fixed here. Google's dated rows come
// from the package schedule by key, whether or not the entry is a copy.
func flashTable(moves map[string][2]float64) map[string]*modeldb.ModelInfo {
	table := make(map[string]*modeldb.ModelInfo, len(todaysFlash)+len(moves))
	set := func(key string, price [2]float64) {
		m := &modeldb.ModelInfo{ID: key, Mode: modeldb.ModeChat}
		if b, ok := modeldb.BundledModels[key]; ok {
			cpy := *b
			m = &cpy
		}
		m.Pricing.InputPerToken, m.Pricing.OutputPerToken = price[0], price[1]
		table[key] = m
	}
	for k, p := range todaysFlash {
		set(k, p)
	}
	for k, p := range moves {
		set(k, p)
	}
	return table
}

// googleKeys are Google's nine keys of these models, written out here so the
// probe does not depend on the list under test.
var googleKeys = []string{
	"gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash",
	"gemini/gemini-3.6-flash", "gemini/gemini-3.7-flash", "gemini/gemini-3.8-flash",
	"vertex_ai/gemini-3.6-flash", "vertex_ai/gemini-3.7-flash", "vertex_ai/gemini-3.8-flash",
}

// refreshed moves Google's nine keys to the 2027 price, as the January refresh will.
func refreshed() map[string][2]float64 {
	moves := map[string][2]float64{}
	for _, k := range googleKeys {
		moves[k] = [2]float64{1.5e-6, 7.5e-6}
	}
	return moves
}

// billAt is what the plugin bills one request at the instant at.
func billAt(t *testing.T, db *modeldb.ModelDB, at time.Time, provider, table, configuredType, model, resolved string) string {
	t.Helper()
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	p.now = clockAt(at)
	if table != "" {
		p.SetPriceTables(func(_, id string) string {
			if id == provider {
				return table
			}
			return ""
		})
	}
	p.SetProviderTypes(func(_, id string) string {
		if id == provider {
			return configuredType
		}
		return ""
	})
	rc := newRC(model, 1_000_000, 100_000)
	rc.Provider = provider
	rc.ResolvedModel = resolved
	p.ProcessResponse(context.Background(), rc)
	return rc.Metadata["cost"]
}

// direct is what resellerPrice says for a request, asked as ProcessResponse
// asks it.
func direct(db *modeldb.ModelDB, at time.Time, provider, table, configuredType, model string) (string, bool) {
	return resellerPrice(db, priceKey(db, model, provider, table), provider, table, configuredType, at)
}

func checkDirect(t *testing.T, db *modeldb.ModelDB, at time.Time, provider, table, configuredType, model, wantKey string, wantKeep bool) {
	t.Helper()
	gotKey, gotKeep := direct(db, at, provider, table, configuredType, model)
	if gotKey != wantKey || gotKeep != wantKeep {
		t.Errorf("%s %s (table %q, type %q) at %s: (%q, %v), want (%q, %v)",
			provider, model, table, configuredType, at.Format(time.RFC3339Nano), gotKey, gotKeep, wantKey, wantKeep)
	}
}

func checkBill(t *testing.T, db *modeldb.ModelDB, at time.Time, provider, table, configuredType, model, resolved, want string) {
	t.Helper()
	if got := billAt(t, db, at, provider, table, configuredType, model, resolved); got != want {
		t.Errorf("%s %s (resolved %q, table %q, type %q) at %s: cost %q, want %q",
			provider, model, resolved, table, configuredType, at.Format(time.RFC3339Nano), got, want)
	}
}

// matrixReq is one request of the matrix. canon is the reseller it is served
// by ("" for Google's own and for no provider) and own that reseller's own
// entry for the model ("" for none).
type matrixReq struct {
	provider, table, typ, model, resolved, version, canon, own string
}

// matrix is every reseller cell in each name form and as a fallback, the same
// under another provider ID that is known by its type or by the table it
// states, and Google's own providers and no provider.
func matrix() []matrixReq {
	versions := []string{"3.6", "3.7", "3.8"}
	ownOf := map[string]string{}
	for _, c := range resellerCells {
		ownOf[c.provider+c.version] = c.own
	}
	var out []matrixReq
	add := func(provider, table, typ, canon, version string) {
		own := ownOf[canon+version]
		for _, form := range nameForms {
			out = append(out, matrixReq{provider, table, typ, fmt.Sprintf(form, version), "", version, canon, own})
		}
		model := fmt.Sprintf("gemini-%s-flash", version)
		out = append(out, matrixReq{provider, table, typ, model, model + "-unlisted", version, canon, own})
	}
	for _, c := range resellerCells {
		add(c.provider, "", "", c.provider, c.version)
	}
	for _, v := range versions {
		add("di", "", "deepinfra", "deepinfra", v)
		add("pplx", "", "perplexity", "perplexity", v)
		add("my-or", "openrouter", "", "openrouter", v)
	}
	for _, v := range versions {
		for _, provider := range []string{"vertex_ai", "gemini", ""} {
			out = append(out, matrixReq{provider: provider, model: fmt.Sprintf("gemini-%s-flash", v), version: v})
		}
	}
	return out
}

func checkMatrix(t *testing.T, db *modeldb.ModelDB, at time.Time, want func(matrixReq) string) {
	t.Helper()
	for _, r := range matrix() {
		if got, w := billAt(t, db, at, r.provider, r.table, r.typ, r.model, r.resolved), want(r); got != w {
			t.Errorf("%s %s (resolved %q, table %q, type %q) at %s: cost %q, want %q",
				r.provider, r.model, r.resolved, r.table, r.typ, at.Format(time.RFC3339Nano), got, w)
		}
	}
}

// No bill moves while Google's price has not: every request bills what it
// billed before the rule, Perplexity's own-price lookup for its "google/" name
// included.
func TestResellerPriceNoBillMovesToday(t *testing.T) {
	db := modeldb.New(flashTable(nil), nil)
	checkMatrix(t, db, t26, func(r matrixReq) string {
		if r.provider == "perplexity" && r.model == "google/gemini-3.6-flash" {
			return cost2027
		}
		return cost2026
	})
}

// A refresh that moves Google's nine keys to the 2027 price, probed on the
// first instant of 2027: resellers keep their own price, the rest follow
// Google. Google's own requests, and any request with no own entry, bill the
// 2027 dated row, which equals the refreshed table price; the bills are the
// same with the rows present or deleted.
func TestResellerPriceRefreshProbeOnNewYear(t *testing.T) {
	db := modeldb.New(flashTable(refreshed()), nil)
	checkMatrix(t, db, t27, func(r matrixReq) string {
		switch {
		case r.canon == "":
			return cost2027
		case r.canon == "perplexity" && r.version == "3.6":
			return cost2027
		case r.own == "":
			return cost2027
		}
		return cost2026
	})
}

// The same refreshed table on the last instant of 2026, which needs the dated
// rows: Google's keys still bill the 2026 row there, and a reseller's own
// entry is compared with that row, not with the refreshed table price.
func TestResellerPriceRefreshProbeOnNewYearsEve(t *testing.T) {
	db := modeldb.New(flashTable(refreshed()), nil)
	m, ok := db.Get("gemini-3.7-flash")
	if !ok {
		t.Fatal("the table does not list gemini-3.7-flash")
	}
	if m.PricingAt(t26) == m.Pricing {
		t.Fatal("the dated rows are gone: this test models a refresh that lands before them, and goes with them")
	}
	checkMatrix(t, db, t26, func(r matrixReq) string {
		if r.canon == "perplexity" && r.version == "3.6" {
			// Its own entry is not Google's 2026 price, so the table price is
			// billed, and the refresh has moved that.
			return cost2027
		}
		return cost2026
	})
}

func TestResellerPriceEachSideOfNewYear(t *testing.T) {
	if !ownPriceFrom.Equal(t27) || !t26.Before(ownPriceFrom) {
		t.Fatalf("ownPriceFrom is %s, want %s, and the last instant of 2026 before it", ownPriceFrom, t27)
	}

	// Perplexity's own 3.6 price is above Google's 2026 price: it is billed
	// from the switch and not before.
	db := modeldb.New(flashTable(nil), nil)
	for _, c := range []struct{ provider, typ, model, resolved string }{
		{"perplexity", "", "gemini-3.6-flash", ""},
		{"perplexity", "", "gemini/gemini-3.6-flash", ""},
		{"perplexity", "", "vertex_ai/gemini-3.6-flash", ""},
		{"perplexity", "", "gemini-3.6-flash", "gemini-3.6-flash-unlisted"},
		{"pplx", "perplexity", "google/gemini-3.6-flash", ""},
	} {
		checkBill(t, db, t26, c.provider, "", c.typ, c.model, c.resolved, cost2026)
		checkBill(t, db, t27, c.provider, "", c.typ, c.model, c.resolved, cost2027)
	}
	checkDirect(t, db, t26, "perplexity", "", "", "gemini-3.6-flash", "", true)
	checkDirect(t, db, t27, "perplexity", "", "", "gemini-3.6-flash", "perplexity/google/gemini-3.6-flash", false)

	// OpenRouter's own 3.7 price moved to 0.80/4.00: billed from the switch.
	db = modeldb.New(flashTable(map[string][2]float64{"openrouter/google/gemini-3.7-flash": {8e-7, 4e-6}}), nil)
	for _, c := range []struct{ model, resolved string }{
		{"gemini-3.7-flash", ""},
		{"gemini/gemini-3.7-flash", ""},
		{"vertex_ai/gemini-3.7-flash", ""},
		{"gemini-3.7-flash", "gemini-3.7-flash-unlisted"},
	} {
		checkBill(t, db, t26, "openrouter", "", "", c.model, c.resolved, cost2026)
		checkBill(t, db, t27, "openrouter", "", "", c.model, c.resolved, "1.200000")
	}
	// Its "google/" name already lands on the own entry.
	checkBill(t, db, t26, "openrouter", "", "", "google/gemini-3.7-flash", "", "1.200000")
	checkBill(t, db, t27, "openrouter", "", "", "google/gemini-3.7-flash", "", "1.200000")
}

// The dated rows cannot be removed from package cost: they sit in a
// package-level schedule found by the entry's key. So this test pins the rule
// from the switch on with every Google key priced at the 2027 row, which
// makes the result the same with the rows present or deleted. Once the rows
// are deleted it really runs without them, and must stay then.
func TestResellerPriceAfterDatedRowsAreGone(t *testing.T) {
	google := modeldb.PricingInfo{InputPerToken: 1.5e-6, OutputPerToken: 7.5e-6}
	db := modeldb.New(map[string]*modeldb.ModelInfo{
		"gemini-3.6-flash":                {ID: "gemini-3.6-flash", Mode: modeldb.ModeChat, Pricing: google},
		"gemini-3.7-flash":                {ID: "gemini-3.7-flash", Mode: modeldb.ModeChat, Pricing: google},
		"github_copilot/gemini-3.7-flash": {ID: "github_copilot/gemini-3.7-flash", Mode: modeldb.ModeChat, Pricing: modeldb.PricingInfo{InputPerToken: 7.5e-7, OutputPerToken: 3.75e-6}},
	}, nil)
	for _, at := range []time.Time{tFeb, t27} {
		checkBill(t, db, at, "github_copilot", "", "", "gemini-3.7-flash", "", cost2026)
		checkDirect(t, db, at, "github_copilot", "", "", "gemini-3.7-flash", "github_copilot/gemini-3.7-flash", false)
	}
	checkBill(t, db, tFeb, "github_copilot", "", "", "gemini-3.6-flash", "", cost2027)
}

func TestResellerPriceNeedsBothPrices(t *testing.T) {
	// An own entry with no output price is never billed.
	db := modeldb.New(flashTable(map[string][2]float64{"github_copilot/gemini-3.7-flash": {7.5e-7, 0}}), nil)
	checkDirect(t, db, t27, "github_copilot", "", "", "gemini-3.7-flash", "", true)
	checkBill(t, db, t27, "github_copilot", "", "", "gemini-3.7-flash", "", cost2026)

	// An own entry an override gives an input price only is not priced.
	db = modeldb.New(flashTable(nil), map[string]modeldb.ModelOverride{
		"github_copilot/gemini-3.6-flash": {Pricing: &modeldb.PricingOverride{InputPerToken: ptr(7.5e-7)}},
	})
	checkDirect(t, db, t27, "github_copilot", "", "", "gemini-3.6-flash", "", true)
	checkBill(t, db, t27, "github_copilot", "", "", "gemini-3.6-flash", "", cost2026)

	// An override on a listed own entry is part of its price.
	db = modeldb.New(flashTable(nil), map[string]modeldb.ModelOverride{
		"github_copilot/gemini-3.7-flash": {Pricing: &modeldb.PricingOverride{InputPerToken: ptr(1e-6)}},
	})
	checkBill(t, db, t26, "github_copilot", "", "", "gemini-3.7-flash", "", cost2026)
	checkBill(t, db, t27, "github_copilot", "", "", "gemini-3.7-flash", "", "1.375000")

	// An output price alone leaves the request unpriced; the table price bills.
	db = modeldb.New(flashTable(nil), map[string]modeldb.ModelOverride{
		"github_copilot/gemini-3.6-flash": {Pricing: &modeldb.PricingOverride{OutputPerToken: ptr(3.75e-6)}},
	})
	checkDirect(t, db, t27, "github_copilot", "", "", "gemini-3.6-flash", "", true)
	checkBill(t, db, t27, "github_copilot", "", "", "gemini-3.6-flash", "", cost2026)
}

func TestResellerPriceBareKeyOverride(t *testing.T) {
	db := modeldb.New(flashTable(nil), map[string]modeldb.ModelOverride{
		"gemini-3.7-flash": {Pricing: &modeldb.PricingOverride{InputPerToken: ptr(2e-6)}},
	})
	// Until the switch the reseller is billed the table price, override
	// included; from it, its own price, which the override no longer reaches.
	checkBill(t, db, t26, "github_copilot", "", "", "gemini-3.7-flash", "", "2.375000")
	checkBill(t, db, t27, "github_copilot", "", "", "gemini-3.7-flash", "", cost2026)
}

func TestResellerPriceMatchOrder(t *testing.T) {
	const model = "gemini-3.7-flash"
	copilot := "github_copilot/gemini-3.7-flash"
	copilotGoogle := "github_copilot/google/gemini-3.7-flash"
	for _, c := range []struct {
		name                 string
		moves                map[string][2]float64
		provider, table, typ string
		at                   time.Time
		wantKey              string
		wantKeep             bool
	}{
		{"plain spelling before google/", map[string][2]float64{copilotGoogle: {1e-6, 5e-6}}, "github_copilot", "", "", t27, copilot, false},
		{"an unpriced entry is skipped", map[string][2]float64{copilot: {7.5e-7, 0}, copilotGoogle: {7.5e-7, 3.75e-6}}, "github_copilot", "", "", t27, copilotGoogle, false},
		{"known by its type", map[string][2]float64{"deepinfra/gemini-3.7-flash": {1e-6, 5e-6}}, "di", "", "deepinfra", t27, "deepinfra/gemini-3.7-flash", false},
		{"first listed decides, before the switch", map[string][2]float64{copilot: {1e-6, 5e-6}, copilotGoogle: {7.5e-7, 3.75e-6}}, "github_copilot", "", "", t26, "", true},
		{"first listed decides, from the switch", map[string][2]float64{copilot: {1e-6, 5e-6}, copilotGoogle: {7.5e-7, 3.75e-6}}, "github_copilot", "", "", t27, copilot, false},
		{"the table it states before its type", nil, "x", "openrouter", "perplexity", t27, "openrouter/google/gemini-3.7-flash", false},
		{"the table's spelling of its ID", map[string][2]float64{"fireworks_ai/gemini-3.7-flash": {1e-6, 5e-6}}, "fireworks", "", "perplexity", t27, "fireworks_ai/gemini-3.7-flash", false},
		{"its ID before the table it states", nil, "openrouter", "perplexity", "", t27, "openrouter/google/gemini-3.7-flash", false},
	} {
		db := modeldb.New(flashTable(c.moves), nil)
		gotKey, gotKeep := resellerPrice(db, model, c.provider, c.table, c.typ, c.at)
		if gotKey != c.wantKey || gotKeep != c.wantKeep {
			t.Errorf("%s: (%q, %v), want (%q, %v)", c.name, gotKey, gotKeep, c.wantKey, c.wantKeep)
		}
	}

	// Equal input but a different output is a different price.
	db := modeldb.New(flashTable(map[string][2]float64{copilot: {7.5e-7, 4e-6}}), nil)
	if gotKey, gotKeep := resellerPrice(db, model, "github_copilot", "", "", t26); gotKey != "" || !gotKeep {
		t.Errorf("before the switch: (%q, %v), want (\"\", true)", gotKey, gotKeep)
	}
	checkBill(t, db, t26, "github_copilot", "", "", model, "", cost2026)
	if gotKey, gotKeep := resellerPrice(db, model, "github_copilot", "", "", t27); gotKey != copilot || gotKeep {
		t.Errorf("from the switch: (%q, %v), want (%q, false)", gotKey, gotKeep, copilot)
	}
	checkBill(t, db, t27, "github_copilot", "", "", model, "", "1.150000")
}

func TestResellerPriceLeavesOtherModels(t *testing.T) {
	chat := func(in, out float64) *modeldb.ModelInfo {
		return &modeldb.ModelInfo{Mode: modeldb.ModeChat, Pricing: modeldb.PricingInfo{InputPerToken: in, OutputPerToken: out}}
	}
	db := modeldb.New(map[string]*modeldb.ModelInfo{
		"gpt-4-turbo":                    chat(1e-5, 3e-5),
		"azure/gpt-4-turbo":              chat(1.1e-5, 3.3e-5),
		"deepseek-v4-flash":              chat(3e-7, 1.2e-6),
		"fireworks_ai/deepseek-v4-flash": chat(1.4e-7, 2.8e-7),
	}, nil)
	for _, at := range []time.Time{t26, t27} {
		checkBill(t, db, at, "azure", "", "", "gpt-4-turbo", "", "13.000000")
		checkDirect(t, db, at, "azure", "", "", "gpt-4-turbo", "", true)
		checkBill(t, db, at, "fireworks", "", "", "deepseek-v4-flash", "", "0.420000")
		checkDirect(t, db, at, "fireworks", "", "", "deepseek-v4-flash", "", true)
	}

	// A model the table does not list is not one of the nine, whatever it
	// starts like. (The exact-key check is covered by the cross-reseller keys
	// in TestResellerPriceIgnoresResolvedOwnKeys.)
	checkDirect(t, modeldb.New(flashTable(nil), nil), t27, "github_copilot", "", "", "gemini-3.8-flash-cyber", "", false)
}

func TestResellerPriceIgnoresResolvedOwnKeys(t *testing.T) {
	db := modeldb.New(flashTable(nil), nil)
	for _, at := range []time.Time{t26, t27} {
		checkBill(t, db, at, "perplexity", "", "", "openrouter/google/gemini-3.6-flash", "", cost2026)
		checkDirect(t, db, at, "perplexity", "", "", "openrouter/google/gemini-3.6-flash", "", true)
		checkBill(t, db, at, "openrouter", "", "", "perplexity/google/gemini-3.6-flash", "", cost2027)
		checkBill(t, db, at, "perplexity", "", "", "google/gemini-3.6-flash", "", cost2027)
		checkBill(t, db, at, "github_copilot", "", "", "github_copilot/gemini-3.7-flash", "", cost2026)
	}
}

func TestResellerPriceLeavesGoogleAlone(t *testing.T) {
	db := modeldb.New(flashTable(nil), nil)
	for _, c := range []struct{ provider, table, typ string }{
		{"vertex_ai", "", ""},
		{"gemini", "", ""},
		{"vertex_ai_global", "vertex_ai", ""},
		{"google", "gemini", ""},
		{"openrouter", "", "gemini"},
		{"openrouter", "", "vertex_ai"},
		{"", "", ""},
	} {
		for _, at := range []time.Time{t26, t27} {
			checkDirect(t, db, at, c.provider, c.table, c.typ, "gemini-3.7-flash", "", false)
		}
	}

	// A reseller configured as Google's own follows Google's price.
	checkBill(t, modeldb.New(flashTable(refreshed()), nil), t27, "openrouter", "", "gemini", "gemini-3.7-flash", "", cost2027)
}

func TestResellerPriceTypedProviderUnderAnotherID(t *testing.T) {
	db := modeldb.New(flashTable(refreshed()), nil)
	const model = "gemini-3.7-flash"
	checkDirect(t, db, t27, "copilot-eu", "", "github_copilot", model, "github_copilot/gemini-3.7-flash", false)
	checkBill(t, db, t27, "copilot-eu", "", "github_copilot", model, "", cost2026)
	// Not recognised without a type: Google's price.
	checkDirect(t, db, t27, "copilot-eu", "", "", model, "", false)
	checkBill(t, db, t27, "copilot-eu", "", "", model, "", cost2027)
	checkDirect(t, db, t27, "di", "", "deepinfra", model, "deepinfra/google/gemini-3.7-flash", false)
	checkBill(t, db, t27, "di", "", "deepinfra", model, "", cost2026)
}

// googleFlashKeys must name exactly the keys whose price the dated rows move,
// or a refresh could move a model the rule does not cover.
func TestGoogleFlashKeysAreTheDatedKeys(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	if len(googleKeys) != len(googleFlashKeys) {
		t.Errorf("googleFlashKeys has %d keys, the probe's list has %d", len(googleFlashKeys), len(googleKeys))
	}
	for _, k := range googleKeys {
		if !googleFlashKeys[k] {
			t.Errorf("%s is not in googleFlashKeys", k)
		}
	}
	moved := 0
	for key := range modeldb.BundledModels {
		m, ok := db.Get(key)
		if !ok || m.ID != key || m.PricingAt(t26) == m.PricingAt(t27) {
			continue
		}
		moved++
		if !googleFlashKeys[key] {
			t.Errorf("%s changes price on 1 Jan 2027 and is not in googleFlashKeys: add it, or leave it out, once it is decided whether that model's resellers keep their own price", key)
		}
	}
	if moved > 0 && moved != len(googleFlashKeys) {
		t.Errorf("%d keys change price on 1 Jan 2027, googleFlashKeys has %d: remove from it, or add to it, once it is decided whether that model's resellers keep their own price", moved, len(googleFlashKeys))
	}
	for k := range googleFlashKeys {
		if m, ok := db.Get(k); !ok || m.ID != k {
			t.Errorf("googleFlashKeys names %q, which the table does not list under that exact key: remove it from the list once it is decided whether that model's resellers keep their own price", k)
		}
	}
}

// A reseller's own price wins from ownPriceFrom, so that instant must be when
// Google's price changes. Where the dated rows still exist, a key whose price
// does not change at the switch but does change within the year means the
// change moved away from it. With no dated change at all, nothing is left to
// compare, and this passes.
func TestOwnPriceSwitchIsGooglesPriceChange(t *testing.T) {
	db := modeldb.New(modeldb.BundledModels, nil)
	mid26 := time.Date(2026, time.June, 1, 0, 0, 0, 0, time.UTC)
	mid27 := time.Date(2027, time.June, 1, 0, 0, 0, 0, time.UTC)
	for key := range googleFlashKeys {
		m, ok := db.Get(key)
		if !ok {
			t.Errorf("%s is in googleFlashKeys and not listed in the table", key)
			continue
		}
		if m.PricingAt(ownPriceFrom.Add(-time.Nanosecond)) != m.PricingAt(ownPriceFrom) {
			continue
		}
		if m.PricingAt(mid26) != m.PricingAt(mid27) {
			t.Errorf("%s changes price between %s and %s but not at ownPriceFrom (%s): Google's price change has moved away from ownPriceFrom, so move ownPriceFrom with it",
				key, mid26.Format(time.DateOnly), mid27.Format(time.DateOnly), ownPriceFrom.Format(time.DateOnly))
		}
	}
}
