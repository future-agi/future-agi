package toolcatalog

import (
	"encoding/json"
	"errors"
	"os"
	"path"
	"strconv"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func reserveServer(capability, vendor, sku string) config.ManagedMCPServerConfig {
	return config.ManagedMCPServerConfig{
		Kind: config.ManagedServerKindRemote, Billing: config.ManagedServerBillingReserve, URL: "https://tools.example/mcp",
		PriceCard: config.ToolPriceCardRef{Capability: capability, Vendor: vendor, SKU: sku},
	}
}

func orgConfig(t *testing.T, raw string) *tenant.OrgConfig {
	t.Helper()
	var oc tenant.OrgConfig
	if err := json.Unmarshal([]byte(raw), &oc); err != nil {
		t.Fatal(err)
	}
	return &oc
}

// A managed credential for a vendor whose card is byok_only is
// refused, in the operator config (a managed provider, or a managed server
// that names that card, reserve or delegated) and in an org config (a
// managed_tools allow glob that names the vendor, literally or through an
// escape or a bracket class). Other statuses, BYOK use and vendor wildcards
// (* and ?) are left alone.
func TestByokOnlyManagedRejected(t *testing.T) {
	cat := seedCatalog(t)
	brave, ok := cat.Card("search", "brave")
	if !ok || brave.ResaleStatus != ResaleByokOnly {
		t.Fatalf("the seed brave card is %+v; this test needs it byok_only", brave)
	}

	cfg := &config.Config{Providers: map[string]config.ProviderConfig{"brave": {Managed: true, APIKey: "x"}}}
	if err := cat.CheckOperatorConfig(cfg); !errors.Is(err, ErrManagedNotAllowed) || !strings.Contains(err.Error(), "brave") {
		t.Errorf("managed provider brave: got %v, want ErrManagedNotAllowed naming brave", err)
	}
	for name, ok := range map[string]config.ProviderConfig{
		"brave, not managed": {APIKey: "x"},
		"exa (unconfirmed)":  {Managed: true, APIKey: "x"},
		"openrouter":         {Managed: true, Type: "openrouter"},
	} {
		id := strings.Fields(name)[0]
		id = strings.TrimSuffix(id, ",")
		if err := cat.CheckOperatorConfig(&config.Config{Providers: map[string]config.ProviderConfig{id: ok}}); err != nil {
			t.Errorf("%s: refused: %v", name, err)
		}
	}

	braveRef := config.ToolPriceCardRef{Capability: "search", Vendor: "brave", SKU: "brave.search.web"}
	for name, server := range map[string]config.ManagedMCPServerConfig{
		"reserve": reserveServer("search", "brave", "brave.search.web"),
		"delegated builtin": {Kind: config.ManagedServerKindBuiltin, Billing: config.ManagedServerBillingDelegated,
			PriceCard: braveRef},
		"delegated remote with FutureAGI's credential": {Kind: config.ManagedServerKindRemote,
			Billing: config.ManagedServerBillingDelegated, URL: "https://brave.example/mcp",
			CredentialEnv: "FAGI_BRAVE", PriceCard: braveRef},
	} {
		_, err := cat.ResolveManagedServers(map[string]config.ManagedMCPServerConfig{"web": server})
		if !errors.Is(err, ErrManagedNotAllowed) || !strings.Contains(err.Error(), "fagi.web") {
			t.Errorf("a %s server naming brave's card: got %v, want ErrManagedNotAllowed naming fagi.web", name, err)
		}
	}
	cfg = &config.Config{}
	cfg.MCP.ManagedServers = map[string]config.ManagedMCPServerConfig{"web": {Kind: config.ManagedServerKindRemote,
		Billing: config.ManagedServerBillingDelegated, URL: "https://brave.example/mcp", CredentialEnv: "FAGI_BRAVE",
		PriceCard: braveRef}}
	if _, _, err := LoadForConfig(cfg); !errors.Is(err, ErrManagedNotAllowed) {
		t.Errorf("LoadForConfig with a delegated server at brave's card: got %v, want ErrManagedNotAllowed", err)
	}

	allow := func(globs ...string) *tenant.OrgConfig {
		raw, err := json.Marshal(map[string]any{"mcp": map[string]any{"managed_tools": map[string]any{
			"enabled": true, "allow": globs, "deny": []string{"search/brave"}}}})
		if err != nil {
			t.Fatal(err)
		}
		return orgConfig(t, string(raw))
	}
	for _, glob := range []string{"search/brave", "*/brave", "s*/brave", `search/br\ave`, "search/brav[e]",
		"*/[b]rave", "[s]earch/brave"} {
		if err := cat.CheckOrgConfig(allow(glob)); !errors.Is(err, ErrManagedNotAllowed) || !strings.Contains(err.Error(), strconv.Quote(glob)) {
			t.Errorf("allow %q: got %v, want ErrManagedNotAllowed naming the glob", glob, err)
		}
	}
	for _, glob := range []string{"search/*", "search/exa", "fetch/brave", "fagi.web/*", "*/*", "search/brav?",
		"search/b*", "search/[x]rave"} {
		if err := cat.CheckOrgConfig(allow(glob)); err != nil {
			t.Errorf("allow %q: refused: %v", glob, err)
		}
	}
	if err := cat.CheckOrgConfig(nil); err != nil {
		t.Errorf("no org config: %v", err)
	}
}

// A reserve server's price_card resolves to its card and SKU, keyed by
// the server's fagi.<id>.
func TestManagedServerPriceCardResolves(t *testing.T) {
	cat := seedCatalog(t)
	card, sku, err := cat.Lookup("search", "exa", "exa.search.auto")
	if err != nil {
		t.Fatalf("Lookup: %v", err)
	}
	got, err := cat.ResolveManagedServers(map[string]config.ManagedMCPServerConfig{
		"web":       reserveServer("search", "exa", "exa.search.auto"),
		"fagi.docs": reserveServer("documents", "mistral_ocr", "mistral_ocr.page"),
	})
	if err != nil {
		t.Fatalf("ResolveManagedServers: %v", err)
	}
	web, ok := got["fagi.web"]
	if !ok || web.Card == nil || web.SKU == nil {
		t.Fatalf("fagi.web not resolved to a card: %+v", got)
	}
	if web.Card.Vendor != card.Vendor || web.SKU.ID != sku.ID || web.SKU.USDPerUnit != sku.USDPerUnit || web.Billing != config.ManagedServerBillingReserve {
		t.Errorf("fagi.web resolved to %+v %+v, want %s %s", web.Card, web.SKU, card.Vendor, sku.ID)
	}
	if sku.USDPerUnit <= 0 || sku.Unit != UnitRequest {
		t.Errorf("exa.search.auto is %v per %s; the seed prices it per request", sku.USDPerUnit, sku.Unit)
	}
	if docs := got["fagi.docs"]; docs.SKU == nil || docs.SKU.Unit != UnitPage || docs.ServerID != "fagi.docs" {
		t.Errorf("fagi.docs resolved to %+v", docs)
	}
}

// A reference to a card or SKU that does not exist is a startup error
// that names the server and the reference, from ResolveManagedServers and
// from LoadForConfig alike.
func TestDanglingPriceCardIsStartupError(t *testing.T) {
	cat := seedCatalog(t)
	for name, ref := range map[string][3]string{
		"missing sku":        {"search", "exa", "exa.search.nope"},
		"missing vendor":     {"search", "nobody", "x"},
		"missing capability": {"teleport", "exa", "exa.search.auto"},
	} {
		_, err := cat.ResolveManagedServers(map[string]config.ManagedMCPServerConfig{"web": reserveServer(ref[0], ref[1], ref[2])})
		if !errors.Is(err, ErrDanglingPriceCard) || !strings.Contains(err.Error(), "fagi.web") || !strings.Contains(err.Error(), ref[2]) {
			t.Errorf("%s: got %v, want ErrDanglingPriceCard naming fagi.web and %s", name, err, ref[2])
		}
	}
	cfg := &config.Config{}
	cfg.MCP.ManagedServers = map[string]config.ManagedMCPServerConfig{
		"web":  reserveServer("search", "exa", "exa.search.auto"),
		"docs": reserveServer("documents", "mistral_ocr", "gone"),
	}
	if _, _, err := LoadForConfig(cfg); !errors.Is(err, ErrDanglingPriceCard) || !strings.Contains(err.Error(), "fagi.docs") {
		t.Errorf("LoadForConfig: got %v, want ErrDanglingPriceCard naming fagi.docs", err)
	}
	cfg.MCP.ManagedServers["docs"] = reserveServer("documents", "mistral_ocr", "mistral_ocr.page")
	cat2, prices, err := LoadForConfig(cfg)
	if err != nil || cat2 == nil || len(prices) != 2 {
		t.Fatalf("LoadForConfig with both cards present: %v, %d prices", err, len(prices))
	}
	if cat, prices, err := LoadForConfig(nil); err != nil || cat == nil || len(prices) != 0 {
		t.Errorf("LoadForConfig(nil): %v, %v", err, prices)
	}
	cfg.Tools.CatalogDir = writeCards(t, validCard())
	if _, _, err := LoadForConfig(cfg); !errors.Is(err, ErrDanglingPriceCard) {
		t.Errorf("tools.catalog_dir without exa's card: got %v, want ErrDanglingPriceCard", err)
	}
}

// A delegated server may carry no price_card; it is
// resolved with no card, priced per call by the capability core, never a
// dangling reference. A delegated server that does name a card must name one
// that exists.
func TestDelegatedServerWithoutCardResolves(t *testing.T) {
	cat := seedCatalog(t)
	got, err := cat.ResolveManagedServers(map[string]config.ManagedMCPServerConfig{
		"fagi.web": {Kind: config.ManagedServerKindBuiltin, Billing: config.ManagedServerBillingDelegated},
	})
	if err != nil {
		t.Fatalf("a card-less delegated server: %v", err)
	}
	web := got["fagi.web"]
	if web.Billing != config.ManagedServerBillingDelegated || web.Card != nil || web.SKU != nil || !web.PricedPerCall() {
		t.Errorf("fagi.web resolved to %+v, want delegated with no card", web)
	}
	_, err = cat.ResolveManagedServers(map[string]config.ManagedMCPServerConfig{
		"web": {Kind: config.ManagedServerKindBuiltin, Billing: config.ManagedServerBillingDelegated,
			PriceCard: config.ToolPriceCardRef{Capability: "search", Vendor: "exa", SKU: "nope"}},
	})
	if !errors.Is(err, ErrDanglingPriceCard) {
		t.Errorf("a delegated server naming a missing card: got %v, want ErrDanglingPriceCard", err)
	}
	if _, err := cat.ResolveManagedServers(map[string]config.ManagedMCPServerConfig{
		"web": {Kind: config.ManagedServerKindRemote, URL: "https://x.example/mcp"},
	}); !errors.Is(err, ErrDanglingPriceCard) {
		t.Errorf("a reserve server (billing defaulted) with no card: got %v, want ErrDanglingPriceCard", err)
	}
}

// "web" and "fagi.web" are one server; ManagedServerID is the normalizer
// every managed-server lookup keys by.
func TestManagedServerIDNormalized(t *testing.T) {
	for in, want := range map[string]string{"web": "fagi.web", "fagi.web": "fagi.web", "fagi.fagi.x": "fagi.fagi.x", "a-b_1": "fagi.a-b_1"} {
		if got := ManagedServerID(in); got != want {
			t.Errorf("ManagedServerID(%q) = %q, want %q", in, got, want)
		}
	}
	got, err := seedCatalog(t).ResolveManagedServers(map[string]config.ManagedMCPServerConfig{
		"fagi.docs": reserveServer("documents", "mistral_ocr", "mistral_ocr.page"),
		"web":       {Kind: config.ManagedServerKindBuiltin, Billing: config.ManagedServerBillingDelegated},
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := got["fagi.docs"]; !ok {
		t.Errorf("fagi.docs missing from %v", got)
	}
	if _, ok := got["fagi.web"]; !ok {
		t.Errorf("web is not keyed as fagi.web: %v", got)
	}
	if len(got) != 2 {
		t.Errorf("%d resolutions, want 2", len(got))
	}
}

// The glob table Django's matcher is tested against (testdata/glob_cases.json)
// is path.Match's behaviour, so CheckOrgConfig and validate_mcp refuse the
// same managed_tools globs.
func TestGlobCasesArePathMatch(t *testing.T) {
	raw, err := os.ReadFile("testdata/glob_cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var table struct {
		Cases [][3]any `json:"cases"`
	}
	if err := json.Unmarshal(raw, &table); err != nil {
		t.Fatal(err)
	}
	if len(table.Cases) < 20 {
		t.Fatalf("%d glob cases", len(table.Cases))
	}
	for _, c := range table.Cases {
		pattern, name, want := c[0].(string), c[1].(string), c[2].(bool)
		if got, _ := path.Match(pattern, name); got != want {
			t.Errorf("path.Match(%q, %q) = %v, the table says %v", pattern, name, got, want)
		}
	}
}
