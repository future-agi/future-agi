package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing/eligibility"
	"github.com/futureagi/agentcc-gateway/internal/routing/reqopts"
)

// Replays EVERY exported row, including every model's maximizer, through the
// real managed field gate, eligibility, production applyVerdict (each endpoint
// OBJECT, never a catalogue lookup by ID), and the real credit reservation.
// The VM runner supplies the export from this exact source/catalogue variant.
// Spend limits and wallet balance are deliberately not limiting this sizing
// proof: those independent limits may refuse an otherwise admissible shape.
func TestWorstCaseAdmissionParity(t *testing.T) {
	path := os.Getenv("HOLD_CATALOGUE")
	if path == "" {
		t.Skip("set HOLD_CATALOGUE to the same-variant Go export")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var doc struct {
		Variant string `json:"variant"`
		AsOf    string `json:"as_of"`
		Rows    []struct {
			Model        string   `json:"model"`
			Scenario     string   `json:"scenario"`
			Input        int      `json:"input_tokens"`
			Output       int      `json:"output_tokens"`
			N            int      `json:"n"`
			InputImages  int      `json:"input_images"`
			Modalities   []string `json:"modalities"`
			ProviderOnly string   `json:"provider_only"`
			GoMicros     int64    `json:"go_micros"`
			Endpoints    []struct {
				Ref          models.EndpointRef `json:"ref"`
				ExplicitFree bool               `json:"explicit_free"`
				Inclusive    json.RawMessage    `json:"inclusive"`
			} `json:"endpoints"`
		} `json:"rows"`
	}
	if err := json.Unmarshal(data, &doc); err != nil {
		t.Fatal(err)
	}
	if len(doc.Rows) == 0 {
		t.Fatal("empty admission proof")
	}
	cfg := config.DefaultConfig()
	if doc.Variant == "cat2-on" {
		field := reflect.ValueOf(&cfg.Managed).Elem().FieldByName("CatalogueFamilies")
		if !field.IsValid() {
			t.Fatal("CAT-2 export on stock binary")
		}
		field.Set(reflect.ValueOf([]string{"cat2"}))
	}
	now, err := time.Parse(time.DateOnly, doc.AsOf)
	if err != nil {
		t.Fatal(err)
	}
	g := newManagedTestGateway(t, managedOptIn())
	store := g.srv.handlers.catalog
	sources := []catalog.Source{{ProviderID: "openrouter", Type: "openrouter"}}
	opts := catalog.OptionsFromConfig(cfg, sources)
	opts.SnapshotDir, opts.Now = "", func() time.Time { return now }
	if err := store.Configure(opts); err != nil {
		t.Fatal(err)
	}
	catalog.Seed(store, sources)
	cat := store.Load()
	g.srv.cfg.Managed = cfg.Managed
	g.srv.RefreshModelDB()
	answer := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
		card, _ := cat.PriceCard("openrouter", model, "")
		inc := reflect.ValueOf(card).FieldByName("Inclusive")
		if inc.IsValid() && !inc.IsNil() && inc.Elem().FieldByName("RouterUniverse").String() != "" {
			model = "openai/o1-pro"
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"id":"audit-mock","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":0,"completion_tokens":0,"cost":0}}`, model)
	})
	g.managed.responseWith.Store(&answer)
	maxRows := map[string]int{}
	for i, row := range doc.Rows {
		prior, ok := maxRows[row.Model]
		if !ok || row.GoMicros > doc.Rows[prior].GoMicros {
			maxRows[row.Model] = i
		}
	}
	calendar, err := os.ReadFile("../../catalog/deprecations.json")
	if err != nil {
		t.Fatal(err)
	}
	shutdowns, err := modeldb.ParseDeprecations(calendar)
	if err != nil {
		t.Fatal(err)
	}
	g.credits.SetPrices(store.PriceCard)
	g.credits.SetMaxOutput(func(provider, id string) int { m, _ := cat.Get(provider, id); return m.MaxOutputTokens })
	g.grant(t, "org-m", 1_000_000_000_000)
	for i, row := range doc.Rows {
		t.Run(fmt.Sprintf("%s/%s", row.Model, row.Scenario), func(t *testing.T) {
			req := &models.ChatCompletionRequest{Model: row.Model, N: &row.N, Modalities: row.Modalities}
			if row.Scenario != "default-output" {
				req.MaxTokens = &row.Output
			}
			content, _ := json.Marshal(strings.Repeat("x", max(0, row.Input-7)*4))
			if row.InputImages > 0 {
				parts := make([]map[string]any, row.InputImages)
				for j := range parts {
					parts[j] = map[string]any{"type": "image_url", "image_url": map[string]string{"url": "http://127.0.0.1/never-fetched"}}
				}
				content, _ = json.Marshal(parts)
			}
			req.Messages = []models.Message{{Role: "user", Content: content}}
			if row.ProviderOnly != "" {
				pin, _ := json.Marshal(map[string]any{"only": []string{row.ProviderOnly}})
				req.Extra = map[string]json.RawMessage{"provider": pin}
			}
			wire, err := json.Marshal(req)
			if err != nil {
				t.Fatal(err)
			}
			parsed, apiErr := reqopts.Parse(req)
			if apiErr != nil {
				t.Fatal(apiErr)
			}
			if apiErr := reqopts.CheckManaged(req, &parsed.Options, nil); apiErr != nil {
				t.Fatalf("maximizing request rejected by managed gate: %v", apiErr)
			}
			m, ok := cat.Get("openrouter", row.Model)
			if !ok || !cat.ServesManaged("openrouter", row.Model) || !m.Servable(now) {
				t.Fatal("reported model is not managed-servable")
			}
			v := eligibility.Check(&eligibility.Input{Model: row.Model, ProviderID: "openrouter", Managed: true, Aggregator: true,
				Entry: m, Org: managedOptIn(), Request: req, Prefs: parsed.Options.Provider, Now: now, Shutdowns: shutdowns, Host: "openrouter"})
			if !v.OK {
				t.Fatalf("maximizing request ineligible: %+v", v)
			}
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Provider, rc.Model, rc.RequestID, rc.Request = "openrouter", row.Model, fmt.Sprintf("audit-%d", i), req
			rc.Metadata["org_id"], rc.Metadata["billing_mode"] = "org-m", models.BillingModeManaged
			rc.Routing.Provider = parsed.Options.Provider
			// judge attaches the trusted catalogue card after eligibility.Check.
			// Exercise it rather than recreating that production step here.
			v = g.srv.handlers.judge(rc, managedOptIn(), "openrouter", row.Model, row.Model, false)
			if !v.OK {
				t.Fatalf("real managed judgment refused: %+v", v)
			}
			g.srv.handlers.applyVerdict(rc, &v)
			if len(row.Endpoints) != len(rc.Billing.EndpointSet) {
				t.Fatalf("endpoint set length: export=%d actual=%d", len(row.Endpoints), len(rc.Billing.EndpointSet))
			}
			for j, endpoint := range row.Endpoints {
				// json:"-" trusted fields travel only in this test receipt, never
				// in client-supplied production cards. Rehydrate before comparing.
				expected := endpoint.Ref
				expected.Price.ExplicitFree = endpoint.ExplicitFree
				if len(endpoint.Inclusive) > 0 {
					field := reflect.ValueOf(&expected.Price).Elem().FieldByName("Inclusive")
					if !field.IsValid() {
						t.Fatal("inclusive receipt on stock binary")
					}
					if err := json.Unmarshal(endpoint.Inclusive, field.Addr().Interface()); err != nil {
						t.Fatal(err)
					}
				}
				if !reflect.DeepEqual(expected, rc.Billing.EndpointSet[j]) {
					t.Fatalf("exported endpoint object %d differs from production admission: export=%+v actual=%+v", j, expected, rc.Billing.EndpointSet[j])
				}
			}
			if result := g.credits.ProcessRequest(context.Background(), rc); result.Error != nil {
				t.Fatalf("actual credit admission refused: %v", result.Error)
			}
			if !rc.Billing.Reserved || rc.Billing.HoldMicros != row.GoMicros {
				t.Fatalf("actual hold=%d exported=%d reserved=%t", rc.Billing.HoldMicros, row.GoMicros, rc.Billing.Reserved)
			}
			if maxRows[row.Model] == i {
				before := g.managed.calls()
				response := g.chat(t, string(wire), nil)
				if response.Code != http.StatusOK || g.managed.calls() != before+1 {
					t.Fatalf("maximizer HTTP admission: status=%d provider calls=%d want=%d body=%s", response.Code, g.managed.calls(), before+1, response.Body)
				}
			}
		})
	}
	if !t.Failed() {
		t.Logf("ADMITTED %d exported request shapes through managed fields, eligibility, per-object endpoint pricing, and credit reservation; %d maximizing requests reached the real HTTP loopback provider", len(doc.Rows), len(maxRows))
	}
}
