package capabilities_test

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/capabilities"
	"github.com/futureagi/agentcc-gateway/internal/capability"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
)

// toolCard is a priced tool card with permissive terms.
func toolCard(cap, vendor, resale string, usd float64, edit ...func(*capability.Card)) *capability.Card {
	c := &capability.Card{Vendor: vendor, Capability: cap,
		SKUs:         []capability.SKU{{SKU: cap + ".request", Unit: "request", USDPerUnit: usd, FreeOnFailure: true}},
		ResaleStatus: resale, TermsURL: "https://example.invalid/terms", TermsCheckedAt: "2026-10-16", TrainsOnInput: "no",
		ResultsRetention: "full", Cacheable: "none", EvalExport: "allowed"}
	for _, e := range edit {
		e(c)
	}
	return c
}

// toolCore is a capability core serving search (exa managed and cleared,
// tavily byok_only) and fetch (FutureAGI's native fetcher), with the
// operator's managed credentials for exa and futureagi and a 5% tool markup.
// Its cells are installed on the matrix until the test ends.
func toolCore(t *testing.T) *capability.Core {
	t.Helper()
	reg := capability.NewRegistry()
	noop := capability.AdapterFunc(nil)
	for capName, vendors := range map[string][]string{"search": {"exa", "tavily"}, "fetch": {"futureagi"}, "echo": {"echo-a"}} {
		if err := reg.RegisterCapability(capName, capability.Spec{Class: capability.ClassStatelessCrossVendor, EndpointType: "search"}); err != nil {
			t.Fatal(err)
		}
		for _, v := range vendors {
			if err := reg.Register(capName, v, noop); err != nil {
				t.Fatal(err)
			}
		}
	}
	cards := capability.NewStaticCards(
		toolCard("search", "exa", capability.ResaleManagedPartner, 0.01, func(c *capability.Card) { c.ResaleTermsRef = "legal/exa" }),
		toolCard("search", "tavily", capability.ResaleBYOKOnly, 0.008),
		toolCard("fetch", "futureagi", capability.ResaleNative, 0.001),
		toolCard("echo", "echo-a", capability.ResaleManagedPartner, 0.001),
	)
	operator := map[string]config.ProviderConfig{
		"exa":       {Managed: true, APIKey: "fagi-test-exa"},
		"tavily":    {Managed: true, APIKey: "fagi-test-tavily"}, // refused by its card, whatever the operator holds
		"futureagi": {Managed: true, APIKey: "fagi-test-native"},
	}
	core := capability.NewCore(capability.Options{Registry: reg, Cards: cards,
		Credentials: capability.ConfigCredentials{Operator: operator}, MarkupBps: 500})
	t.Cleanup(capabilities.SetToolSource(core.ToolCells))
	return core
}

func build(t *testing.T) *capabilities.Matrix {
	t.Helper()
	return capabilities.Build(capabilities.Inputs{Audience: capabilities.AudienceAdmin, Now: time.Now()})
}

func cell(t *testing.T, m *capabilities.Matrix, endpoint, vendor string) capabilities.Cell {
	t.Helper()
	c, ok := m.Lookup(endpoint, vendor)
	if !ok {
		t.Fatalf("no %s cell on %s", vendor, endpoint)
	}
	return c
}

// A native card shows native, a byok_only card byok_only (and never managed),
// a managed partner with terms cleared.
func TestResaleTermsNativeAndByokOnly(t *testing.T) {
	toolCore(t)
	m := build(t)
	if c := cell(t, m, "search", "tavily"); c.ManagedResaleTerms != capabilities.ResaleBYOKOnly || c.ManagedAllowed || c.Price != nil ||
		c.CredentialType != capabilities.CredentialBYOK || c.Status != capabilities.StatusProviderDependent {
		t.Errorf("tavily = %+v, want byok_only and no managed price", c)
	}
	if c := cell(t, m, "fetch", "futureagi"); c.ManagedResaleTerms != capabilities.ResaleNative || !c.ManagedAllowed {
		t.Errorf("futureagi fetch = %+v, want native and managed allowed", c)
	}
	if c := cell(t, m, "search", "exa"); c.ManagedResaleTerms != capabilities.ResaleCleared {
		t.Errorf("exa = %+v, want cleared", c)
	}
}

// The tool rows come from the core's registry and the operator config, in
// their published tiers; without a tool source the matrix has only the
// model endpoints' rows.
func TestCapabilityRowsFromRegistry(t *testing.T) {
	before := build(t)
	toolCore(t)
	m := build(t)

	if len(m.Endpoints) != len(before.Endpoints)+5 {
		t.Fatalf("%d rows with the tool source, %d without; want five more", len(m.Endpoints), len(before.Endpoints))
	}
	tiers := map[string]string{}
	var ids []string
	for _, row := range m.Endpoints {
		tiers[row.Endpoint] = row.LaunchTier
		ids = append(ids, row.Endpoint)
	}
	if got := strings.Join(ids[len(ids)-5:], ","); got != "fetch,documents.parse,guardrails,evals,browser.sessions" {
		t.Errorf("tool rows = %s", got)
	}
	for ep, want := range map[string]string{"search": "provider_dependent", "fetch": "beta", "documents.parse": "unsupported",
		"guardrails": "unsupported", "evals": "unsupported", "browser.sessions": "unsupported"} {
		if tiers[ep] != want {
			t.Errorf("%s launch_tier %s, want %s", ep, tiers[ep], want)
		}
	}
	if c := cell(t, m, "search", "exa"); c.Status != capabilities.StatusSupported || c.Source != capabilities.SourceConfig {
		t.Errorf("exa = %+v, want supported from config", c)
	}
	if _, ok := m.Lookup("search", "echo-a"); ok {
		t.Error("the test capability echo leaked into the published rows")
	}
	if errs := schematest.Validate(t, "capabilities-v1.schema.json", m); len(errs) > 0 {
		t.Fatalf("matrix fails capabilities-v1: %v", errs)
	}

	// A vendor with no operator credential is the org's to bring.
	capabilities.SetToolSource(func() []capabilities.ToolCell {
		return []capabilities.ToolCell{{Endpoint: "documents.parse", Vendor: "mistral-ocr",
			Cell: capabilities.Cell{Status: capabilities.StatusProviderDependent, CredentialType: capabilities.CredentialBYOK, Source: capabilities.SourceConfig}}}
	})
	if tier := rowTier(build(t), "documents.parse"); tier != "provider_dependent" {
		t.Errorf("documents.parse tier %s with a BYOK vendor, want provider_dependent", tier)
	}
}

func rowTier(m *capabilities.Matrix, endpoint string) string {
	for _, row := range m.Endpoints {
		if row.Endpoint == endpoint {
			return row.LaunchTier
		}
	}
	return ""
}

// videos is unsupported until a provider is configured for it, even for an
// adapter that implements video; scheduled is unsupported while
// routing.scheduled is refused at config load, so no scheduled store exists.
func TestScheduledAndVideosUnsupported(t *testing.T) {
	video := capabilities.Provider{ID: "veo", Interfaces: capabilities.InterfaceSet{capabilities.IfaceChat: true, capabilities.IfaceVideo: true},
		APIFormat: "openai", CredentialType: capabilities.CredentialBYOK}
	m := capabilities.Build(capabilities.Inputs{Providers: []capabilities.Provider{video}, Audience: capabilities.AudienceGlobalKey, Now: time.Now()})
	for _, ep := range []string{capabilities.EndpointVideos, capabilities.EndpointScheduled} {
		c := cell(t, m, ep, "veo")
		if c.Status != capabilities.StatusUnsupported || c.Source != capabilities.SourceConfig || rowTier(m, ep) != capabilities.TierUnsupported {
			t.Errorf("%s = %+v (tier %s), want unsupported by config", ep, c, rowTier(m, ep))
		}
	}
	if c := cell(t, m, capabilities.EndpointChat, "veo"); c.Status != capabilities.StatusSupported {
		t.Errorf("the same provider's chat cell = %+v, want supported", c)
	}
}

// price is additive: the published managed price, markup included, on a
// managed-allowed tool cell only; no other cell carries a price.
func TestCapabilityPriceFieldAdditive(t *testing.T) {
	chat := capabilities.Provider{ID: "openai", Interfaces: capabilities.InterfaceSet{capabilities.IfaceChat: true}, APIFormat: "openai",
		CredentialType: capabilities.CredentialBYOK}
	toolCore(t)
	m := capabilities.Build(capabilities.Inputs{Providers: []capabilities.Provider{chat}, Audience: capabilities.AudienceAdmin, Now: time.Now()})

	p := cell(t, m, "search", "exa").Price
	if p == nil || p.SKU != "search.request" || p.USDPerUnitBilled != 0.0105 || p.MarkupPercent != 5 {
		t.Fatalf("exa price = %+v, want search.request at $0.0105 with 5%%", p)
	}
	raw, _ := json.Marshal(cell(t, m, capabilities.EndpointChat, "openai"))
	if strings.Contains(string(raw), "price") {
		t.Fatalf("a model provider's cell carries a price: %s", raw)
	}
	if errs := schematest.Validate(t, "capabilities-v1.schema.json", m); len(errs) > 0 {
		t.Fatalf("matrix fails capabilities-v1: %v", errs)
	}
}
