package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/routerbilling"
)

func cat2AdminRead(t *testing.T, g *managedTestGateway, path string) map[string]any {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, path, nil)
	req.Header.Set("Authorization", "Bearer "+adminToken)
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("%s status=%d", path, w.Code)
	}
	return decode(t, w)
}

// Check the serialized boundary, not a typed view which could hide a leaked
// embedded field. Reading the API must never erase persisted source decimals.
func TestCAT2AdminPrices(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		t.Run(fmt.Sprintf("on=%t", enabled), func(t *testing.T) {
			g, _, ids := cat2OperatorGateway(t, enabled)
			cat := g.srv.handlers.catalog.Load()
			before, err := json.Marshal(cat)
			if err != nil {
				t.Fatal(err)
			}
			family := map[string]bool{}
			for _, id := range ids {
				family[id] = true
			}
			listed, available := 0, 0
			for _, raw := range cat2AdminRead(t, g, "/-/catalog")["models"].([]any) {
				row := raw.(map[string]any)
				id := row["id"].(string)
				if row["state"] == catalog.StateBlocked {
					for _, field := range []string{"inclusive_price", "prices", "price_tiers", "input_per_token", "output_per_token", "cache_read_per_token",
						"input_per_mtok", "output_per_mtok", "cache_read_per_mtok", "billed_input_per_mtok", "billed_output_per_mtok"} {
						if _, exists := row[field]; exists {
							t.Errorf("blocked %s serializes %s", id, field)
						}
					}
				}
				if !family[id] {
					continue
				}
				listed++
				if !enabled || routerbilling.FamilyMember(id) || id == "google/gemini-2.5-flash-image" {
					if row["state"] != catalog.StateBlocked {
						t.Errorf("unavailable %s advertised as %v", id, row["state"])
					}
					if id == "google/gemini-2.5-flash-image" && !strings.Contains(fmt.Sprint(row["state_reason"]), "2026-10-02") {
						t.Errorf("shutdown reason missing for %s", id)
					}
					continue
				}
				available++
				m, _ := cat.Get("openrouter", id)
				want := m.InclusivePrice(catalog.SourceOpenRouter)
				if row["state"] == catalog.StateBlocked || want == nil || row["inclusive_price"] == nil {
					t.Errorf("available %s lost its exact inclusive card", id)
					continue
				}
				// Compare serialized cards. A router's universe fields are
				// runtime-only (json:"-") and never part of any API response.
				got, err := json.Marshal(row["inclusive_price"])
				if err != nil {
					t.Fatal(err)
				}
				wantJSON, _ := json.Marshal(want)
				var normalized any
				_ = json.Unmarshal(wantJSON, &normalized)
				wantJSON, _ = json.Marshal(normalized)
				if !bytes.Equal(got, wantJSON) {
					t.Errorf("available %s lost its exact inclusive card", id)
				}
			}
			wantAvailable := 0
			if enabled {
				wantAvailable = 28
			}
			if listed != len(family) || available != wantAvailable {
				t.Fatalf("listed=%d want=%d available=%d want=%d", listed, len(family), available, wantAvailable)
			}
			after, err := json.Marshal(cat)
			if err != nil || !bytes.Equal(before, after) {
				t.Fatal("admin listing mutated the billing catalogue")
			}
		})
	}
}
