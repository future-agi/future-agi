package overlay

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"testing/fstest"

	"github.com/futureagi/agentcc-gateway/catalog/connectors/schema"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// No overlay document is committed: each test writes its own, with made-up
// prices, into a temporary operator directory.

const byokDoc = `schema: connector-price-overlay/v1
key: gmail/relay
estimate:
  - {unit: tool_call, usdPerUnit: 0.000317, source: https://relay.example/pricing, checkedAt: 2026-10-19}
billingMode: byok
`

const managedDoc = `{"schema": "connector-price-overlay/v1", "key": "platform/relay",
 "estimate": [{"unit": "auth_event", "usdPerUnit": 2, "source": "https://relay.example/pricing", "checkedAt": "2026-10-19"}],
 "billingMode": "managed", "priceCardRef": {"capability": "connector.relay", "vendor": "relay", "sku": "auth"}}`

func operatorDir(t *testing.T, files map[string]string) string {
	t.Helper()
	dir := t.TempDir()
	for name, body := range files {
		p := filepath.Join(dir, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

func TestLoadDirIsStrict(t *testing.T) {
	entries, err := LoadDir(os.DirFS(operatorDir(t, map[string]string{
		"gmail-relay.yaml": byokDoc, "nested/relay.json": managedDoc, ".DS_Store": "x",
	})))
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 2 {
		t.Fatalf("%d entries", len(entries))
	}
	if e := entries["gmail/relay"]; e.BillingMode != "byok" || e.Estimate[0].USDPerUnit != 0.000317 || e.PriceCardRef != nil {
		t.Errorf("gmail/relay: %+v", e)
	}
	if e := entries["platform/relay"]; e.PriceCardRef == nil || e.PriceCardRef.SKU != "auth" || e.Estimate[0].USDPerUnit != 2 {
		t.Errorf("platform/relay: %+v", e)
	}

	replace := func(doc, old, new string) string {
		if !strings.Contains(doc, old) {
			t.Fatalf("%q is not in the document", old)
		}
		return strings.Replace(doc, old, new, 1)
	}
	for name, files := range map[string]map[string]string{
		"an unknown member":          {"a.yaml": byokDoc + "discount: 0.1\n"},
		"an unknown estimate member": {"a.yaml": replace(byokDoc, "checkedAt: 2026-10-19}", "checkedAt: 2026-10-19, tier: gold}")},
		"another schema":             {"a.yaml": replace(byokDoc, "connector-price-overlay/v1", "tool-price-card-v1")},
		"a null":                     {"a.yaml": replace(byokDoc, "billingMode: byok", "billingMode: null")},
		"no estimate":                {"a.yaml": replace(byokDoc, "estimate:\n  - {unit: tool_call, usdPerUnit: 0.000317, source: https://relay.example/pricing, checkedAt: 2026-10-19}\n", "estimate: []\n")},
		"a negative price":           {"a.yaml": replace(byokDoc, "0.000317", "-1")},
		"a price as text":            {"a.yaml": replace(byokDoc, "0.000317", `"0.000317"`)},
		"an http source":             {"a.yaml": replace(byokDoc, "https://relay", "http://relay")},
		"a date that does not exist": {"a.yaml": replace(byokDoc, "2026-10-19", "2026-02-30")},
		"a key that is not a key":    {"a.yaml": replace(byokDoc, "gmail/relay", "Gmail/Relay")},
		"managed without a card":     {"a.json": replace(managedDoc, `, "priceCardRef": {"capability": "connector.relay", "vendor": "relay", "sku": "auth"}`, "")},
		"byok with a card":           {"a.yaml": byokDoc + "priceCardRef: {capability: c, vendor: v, sku: s}\n"},
		"a card with an unknown key": {"a.json": replace(managedDoc, `"sku": "auth"`, `"sku": "auth", "usd": 1`)},
		"a key twice":                {"a.yaml": byokDoc, "b.yaml": byokDoc},
		"a file that is not overlay": {"a.yaml": byokDoc, "README.md": "notes"},
		"a duplicate member":         {"a.json": replace(managedDoc, `"billingMode": "managed"`, `"billingMode": "managed", "billingMode": "byok"`)},
		"two YAML documents in one":  {"a.yaml": byokDoc + "---\n" + byokDoc},
	} {
		_, err := LoadDir(os.DirFS(operatorDir(t, files)))
		if err == nil {
			t.Errorf("%s: accepted", name)
			continue
		}
		if strings.Contains(err.Error(), "0.000317") {
			t.Errorf("%s: the message quotes a price: %v", name, err)
		}
	}
}

func TestCheckRefusesKeysOutsideTheCatalog(t *testing.T) {
	cat, err := manifest.LoadDir(os.DirFS("../manifest/testdata/valid"))
	if err != nil {
		t.Fatal(err)
	}
	entries, err := LoadDir(os.DirFS(operatorDir(t, map[string]string{"a.yaml": byokDoc, "b.json": managedDoc})))
	if err != nil {
		t.Fatal(err)
	}
	if err := Check(entries, cat); err != nil {
		t.Fatalf("gmail/relay and platform/relay are in the catalog: %v", err)
	}
	stray, err := LoadDir(os.DirFS(operatorDir(t, map[string]string{
		"a.yaml": strings.Replace(byokDoc, "gmail/relay", "gmail/composio", 1),
		"b.json": strings.Replace(managedDoc, "platform/relay", "platform/arcade", 1),
	})))
	if err != nil {
		t.Fatal(err)
	}
	err = Check(stray, cat)
	if err == nil || !strings.Contains(err.Error(), "gmail/composio") || !strings.Contains(err.Error(), "platform/arcade") {
		t.Fatalf("got %v", err)
	}
}

func TestFindDocuments(t *testing.T) {
	published, err := schema.Files.ReadFile(schema.Overlay)
	if err != nil {
		t.Fatal(err)
	}
	fsys := fstest.MapFS{
		"apps/gmail/providers/relay/fixtures/price.json": {Data: []byte(managedDoc)},
		"pricing/gmail.yaml":                             {Data: []byte(byokDoc)},
		"notes/two.yml":                                  {Data: []byte("schema: other\n---\n" + byokDoc)},
		"schema/connector-price-overlay.v1.json":         {Data: published},
		"README.md":                                      {Data: []byte("The overlay, connector-price-overlay/v1, lives elsewhere.\n")},
		"apps/gmail/app.yaml":                            {Data: []byte("schema: \"connector-manifest/v1#app\"\nid: gmail\n")},
		"dist/catalog.json":                              {Data: []byte(`{"schema": "connector-catalog-bundle/v1"}`)},
	}
	found, err := FindDocuments(fsys)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"apps/gmail/providers/relay/fixtures/price.json", "notes/two.yml", "pricing/gmail.yaml"}
	if !slices.Equal(found, want) {
		t.Fatalf("found %v, want %v", found, want)
	}
}

// The loader's rules equal the published schema's.
func TestOverlaySchemaParity(t *testing.T) {
	data, err := schema.Files.ReadFile(schema.Overlay)
	if err != nil {
		t.Fatal(err)
	}
	var doc struct {
		Required   []string `json:"required"`
		Properties struct {
			Schema struct {
				Const string `json:"const"`
			} `json:"schema"`
			Key      struct{ Pattern string } `json:"key"`
			Estimate struct {
				MinItems, MaxItems int
				Items              struct {
					Required   []string `json:"required"`
					Properties struct {
						Unit       struct{ Pattern string }  `json:"unit"`
						USDPerUnit struct{ Minimum float64 } `json:"usdPerUnit"`
						Source     struct {
							MaxLength int    `json:"maxLength"`
							Pattern   string `json:"pattern"`
						} `json:"source"`
						CheckedAt struct{ Pattern string } `json:"checkedAt"`
					} `json:"properties"`
				} `json:"items"`
			} `json:"estimate"`
			BillingMode struct {
				Enum []string `json:"enum"`
			} `json:"billingMode"`
			PriceCardRef struct {
				Required   []string `json:"required"`
				Properties struct {
					Capability struct{ Pattern string } `json:"capability"`
					Vendor     struct{ Pattern string } `json:"vendor"`
					SKU        struct {
						MinLength int `json:"minLength"`
						MaxLength int `json:"maxLength"`
					} `json:"sku"`
				} `json:"properties"`
			} `json:"priceCardRef"`
		} `json:"properties"`
	}
	if err := json.Unmarshal(data, &doc); err != nil {
		t.Fatal(err)
	}
	p := doc.Properties
	for name, pair := range map[string][2]string{
		"schema":     {p.Schema.Const, Schema},
		"key":        {p.Key.Pattern, keyPattern.String()},
		"unit":       {p.Estimate.Items.Properties.Unit.Pattern, unitPattern.String()},
		"source":     {p.Estimate.Items.Properties.Source.Pattern, sourcePattern.String()},
		"checkedAt":  {p.Estimate.Items.Properties.CheckedAt.Pattern, datePattern.String()},
		"capability": {p.PriceCardRef.Properties.Capability.Pattern, capabilityPattern.String()},
		"vendor":     {p.PriceCardRef.Properties.Vendor.Pattern, vendorPattern.String()},
	} {
		if pair[0] != pair[1] {
			t.Errorf("%s: the schema says %q, the loader %q", name, pair[0], pair[1])
		}
	}
	if !slices.Equal(doc.Required, []string{"schema", "key", "estimate", "billingMode"}) ||
		!slices.Equal(p.Estimate.Items.Required, []string{"unit", "usdPerUnit", "source", "checkedAt"}) ||
		!slices.Equal(p.PriceCardRef.Required, []string{"capability", "vendor", "sku"}) ||
		!slices.Equal(p.BillingMode.Enum, []string{"byok", "managed"}) {
		t.Errorf("required members or the billing modes differ from the schema")
	}
	if p.Estimate.MinItems != 1 || p.Estimate.MaxItems != maxEstimates || p.Estimate.Items.Properties.Source.MaxLength != maxSource ||
		p.Estimate.Items.Properties.USDPerUnit.Minimum != 0 || p.PriceCardRef.Properties.SKU.MinLength != 1 || p.PriceCardRef.Properties.SKU.MaxLength != maxSKU {
		t.Errorf("a bound differs from the schema: %+v", p.Estimate)
	}
}
