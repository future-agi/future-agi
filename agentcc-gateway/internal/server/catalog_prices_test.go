package server

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
)

// Exercise the JSON boundary, not catalogModelView: decoding into that type
// would hide accidentally serialized prices behind its optional view fields.
func TestCatalogBlockedPrices(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	all := billabilityModels(t)
	card := catalog.Prices{Prompt: 2e-6, Completion: 10e-6, CacheRead: 1e-6,
		CacheWrite: 3e-6, Request: .04, WebSearch: .005, Reasoning: 4e-6}
	all["acme/cached"] = &catalog.Model{ID: "acme/cached", Prices: card}
	all["acme/runtime-blocked"] = &catalog.Model{ID: "acme/runtime-blocked", Prices: card}
	all["acme/expired"] = &catalog.Model{ID: "acme/expired", Prices: card, ExpirationDate: "2000-01-01"}
	all["acme/tiered"] = &catalog.Model{ID: "acme/tiered", Prices: card,
		PriceTiers: []catalog.PriceTier{{MinPromptTokens: 100000, Prices: card}}}
	all["acme/image"] = &catalog.Model{ID: "acme/image", Prices: catalog.Prices{Image: .01}}
	installBillabilityModels(all)(t, g)
	store := g.srv.handlers.catalog
	if _, err := store.SetBlocks([]string{"acme/runtime-blocked"}, "operator block"); err != nil {
		t.Fatal(err)
	}
	before, err := json.Marshal(store.Load())
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodGet, "/-/catalog", nil)
	req.Header.Set("Authorization", "Bearer "+adminToken)
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("catalog status=%d body=%s", w.Code, w.Body)
	}
	// The cloud contract gate feeds this same handler response through Django,
	// proving the pass-through rather than an independently sanitized fixture.
	if path := os.Getenv("CATALOG_PRICE_CONTRACT_OUTPUT"); path != "" {
		if err := os.WriteFile(path, w.Body.Bytes(), 0600); err != nil {
			t.Fatal(err)
		}
	}
	data := decode(t, w)
	if data["markup_percent"] != 5.0 {
		t.Fatalf("markup changed: %v", data["markup_percent"])
	}
	blocked := map[string]bool{}
	for _, id := range refusedBillabilityIDs {
		if all[id] != nil {
			blocked[id] = true
		}
	}
	for _, id := range []string{"acme/runtime-blocked", "acme/expired", "acme/tiered", "acme/image"} {
		blocked[id] = true
	}
	rows := data["models"].([]any)
	if len(rows) != len(all) {
		t.Fatalf("rows=%d want %d", len(rows), len(all))
	}
	for _, raw := range rows {
		row := raw.(map[string]any)
		id := row["id"].(string)
		t.Run(id, func(t *testing.T) {
			if blocked[id] {
				if row["state"] != catalog.StateBlocked || row["state_reason"] == "" || row["state_reason"] == nil {
					t.Fatalf("expected a blocked row with a reason: %v", row)
				}
				for _, field := range []string{"prices", "price_tiers", "input_per_token", "output_per_token", "cache_read_per_token",
					"input_per_mtok", "output_per_mtok", "cache_read_per_mtok", "billed_input_per_mtok", "billed_output_per_mtok"} {
					if row[field] != nil {
						t.Errorf("blocked model serializes %s=%v; want null or absent", field, row[field])
					}
				}
				return
			}
			if row["state"] == catalog.StateBlocked {
				t.Fatalf("available model became blocked: %v", row)
			}
			m, ok := store.Load().Get("openrouter", id)
			if !ok {
				t.Fatal("missing stored model")
			}
			encoded, err := json.Marshal(m)
			if err != nil {
				t.Fatal(err)
			}
			var original map[string]any
			if err := json.Unmarshal(encoded, &original); err != nil {
				t.Fatal(err)
			}
			for _, field := range []string{"prices", "price_tiers", "input_per_token", "output_per_token", "cache_read_per_token"} {
				if !reflect.DeepEqual(row[field], original[field]) {
					t.Errorf("available %s=%v want %v", field, row[field], original[field])
				}
			}
			for field, want := range map[string]float64{
				"input_per_mtok": roundPrice(m.InputPerToken * 1e6), "output_per_mtok": roundPrice(m.OutputPerToken * 1e6),
				"billed_input_per_mtok": roundPrice(m.InputPerToken * 1e6 * 1.05), "billed_output_per_mtok": roundPrice(m.OutputPerToken * 1e6 * 1.05),
			} {
				if row[field] != want {
					t.Errorf("available %s=%v want %v", field, row[field], want)
				}
			}
			if m.CacheReadPerToken > 0 && row["cache_read_per_mtok"] != roundPrice(m.CacheReadPerToken*1e6) {
				t.Errorf("cache rate changed: %v", row)
			}
		})
	}
	after, err := json.Marshal(store.Load())
	if err != nil || !bytes.Equal(before, after) {
		t.Fatalf("listing mutated the stored billing catalogue: err=%v", err)
	}
}
