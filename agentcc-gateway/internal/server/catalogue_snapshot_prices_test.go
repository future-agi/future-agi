package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
)

// Kept snapshots are an HTTP view too, not permission to bypass OFF. The
// underlying Catalog bytes must remain lossless for audit and restart.
func TestCAT2SnapshotPrices(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		t.Run(fmt.Sprintf("on=%t", enabled), func(t *testing.T) {
			g, _, _ := cat2OperatorGateway(t, enabled)
			r601CatalogAfterShutdown(t, g)
			store := g.srv.handlers.catalog
			cat := store.Load()
			before, err := json.Marshal(cat)
			if err != nil {
				t.Fatal(err)
			}
			snapshot := cat2AdminRead(t, g, "/-/catalog/snapshots/"+cat.ID())
			seen, priced := 0, 0
			for _, raw := range snapshot["providers"].([]any) {
				provider := raw.(map[string]any)
				for _, model := range provider["models"].([]any) {
					row := model.(map[string]any)
					id := row["id"].(string)
					m, _ := cat.Get(provider["provider"].(string), id)
					if row["state"] == catalog.StateBlocked {
						for _, field := range []string{"inclusive_price", "prices", "price_tiers", "input_per_token", "output_per_token", "cache_read_per_token", "endpoints"} {
							if _, exists := row[field]; exists {
								t.Errorf("blocked snapshot %s serializes %s", id, field)
							}
						}
					}
					if m.Inclusive == nil {
						continue
					}
					seen++
					if !enabled || !m.Servable(store.Now()) || id == "google/gemini-2.5-flash-image" {
						if row["state"] != catalog.StateBlocked {
							t.Errorf("snapshot bypasses availability for %s", id)
						}
						continue
					}
					priced++
					got, _ := json.Marshal(row["inclusive_price"])
					want, _ := json.Marshal(m.InclusivePrice(catalog.SourceOpenRouter))
					// Decode and re-encode both sides to compare key ordering.
					var normalized any
					_ = json.Unmarshal(want, &normalized)
					want, _ = json.Marshal(normalized)
					if !bytes.Equal(got, want) {
						t.Errorf("snapshot changed exact price for %s", id)
					}
				}
			}
			if seen == 0 || (enabled && priced != 28) || (!enabled && priced != 0) {
				t.Fatalf("seen=%d priced=%d", seen, priced)
			}
			after, err := json.Marshal(cat)
			if err != nil || !bytes.Equal(before, after) {
				t.Fatal("snapshot HTTP view mutated persisted prices")
			}
			// Disk-loaded kept versions omit InclusiveEnabled and a router's
			// runtime universe (json:"-"). Their HTTP view must match the
			// in-memory version under the current startup gate, except that a
			// router card, which cannot be rebuilt from disk, fails closed:
			// blocked, with no price of any kind.
			var restored catalog.Catalog
			if err := json.Unmarshal(before, &restored); err != nil {
				t.Fatal(err)
			}
			restoredBefore, _ := json.Marshal(&restored)
			raw, err := json.Marshal(g.srv.catalogSnapshotResponse(&restored))
			if err != nil {
				t.Fatal(err)
			}
			var restoredView map[string]any
			if err := json.Unmarshal(raw, &restoredView); err != nil {
				t.Fatal(err)
			}
			routers := map[string]bool{}
			for _, id := range r602Routers {
				routers[id] = true
			}
			memProviders, diskProviders := snapshot["providers"].([]any), restoredView["providers"].([]any)
			if restoredView["id"] != snapshot["id"] || len(diskProviders) != len(memProviders) {
				t.Fatal("disk-loaded snapshot view differs from in-memory view")
			}
			for i := range memProviders {
				mem, disk := memProviders[i].(map[string]any), diskProviders[i].(map[string]any)
				memModels, diskModels := mem["models"].([]any), disk["models"].([]any)
				if len(diskModels) != len(memModels) {
					t.Fatalf("disk-loaded snapshot view lists %d models, in-memory %d", len(diskModels), len(memModels))
				}
				for j := range memModels {
					a, _ := json.Marshal(memModels[j])
					b, _ := json.Marshal(diskModels[j])
					if bytes.Equal(a, b) {
						continue
					}
					row := diskModels[j].(map[string]any)
					id, _ := row["id"].(string)
					if !routers[id] {
						t.Errorf("disk-loaded snapshot view differs from in-memory view for %s", id)
						continue
					}
					if row["state"] != catalog.StateBlocked {
						t.Errorf("disk-loaded router %s is not blocked", id)
					}
					for _, field := range []string{"inclusive_price", "prices", "price_tiers", "input_per_token", "output_per_token", "cache_read_per_token", "endpoints"} {
						if _, exists := row[field]; exists {
							t.Errorf("disk-loaded router %s serializes %s", id, field)
						}
					}
				}
				memRest, diskRest := map[string]any{}, map[string]any{}
				for k, v := range mem {
					if k != "models" {
						memRest[k] = v
					}
				}
				for k, v := range disk {
					if k != "models" {
						diskRest[k] = v
					}
				}
				a, _ := json.Marshal(memRest)
				b, _ := json.Marshal(diskRest)
				if !bytes.Equal(a, b) {
					t.Fatal("disk-loaded snapshot provider view differs from in-memory view")
				}
			}
			restoredAfter, _ := json.Marshal(&restored)
			if !bytes.Equal(restoredBefore, restoredAfter) {
				t.Fatal("snapshot view mutated disk-loaded source decimals")
			}
		})
	}
}

func TestCAT2BlockedEndpointPrices(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		g, _, _ := cat2OperatorGateway(t, enabled)
		r601CatalogAfterShutdown(t, g)
		for _, id := range []string{"google/gemini-3.5-flash", "google/gemini-2.5-flash-image"} {
			doc := cat2AdminRead(t, g, "/-/catalog/endpoints?model="+id)["data"].(map[string]any)
			eps := doc["endpoints"].([]any)
			if !enabled || id == "google/gemini-2.5-flash-image" {
				if len(eps) != 0 {
					t.Errorf("on=%t blocked %s exposes endpoint prices", enabled, id)
				}
			} else if len(eps) == 0 {
				t.Errorf("available %s lost endpoint prices", id)
			}
		}
	}
}
