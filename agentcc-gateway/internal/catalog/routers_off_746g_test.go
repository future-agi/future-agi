package catalog

import (
	"fmt"
	"sort"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routerbilling"
)

func TestRoutersOff746GInventory(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		t.Run(fmt.Sprint(enabled), func(t *testing.T) {
			cfg := cat2Config(t, "{}")
			if enabled {
				cfg = cat2Config(t, "managed:\n  catalogue_families: [cat2]\n")
			}
			s := cat2Store(t, cfg)
			var routers []string
			listed, billable, family := 0, 0, 0
			for _, snap := range s.Load().Snapshots() {
				for id, m := range snap.Models {
					listed++
					if s.Load().ServesManaged(snap.ProviderID, id) {
						billable++
					}
					if cat2FamilyMember(id) {
						family++
					}
					if !routerbilling.FamilyMember(id) {
						continue
					}
					routers = append(routers, id)
					if m.ManagedBillable(snap.CatalogSource) || s.Load().ServesManaged(snap.ProviderID, id) || m.InclusivePrice(snap.CatalogSource) != nil || cat2FamilyMember(id) {
						t.Errorf("router remains credit-qualified: %s", id)
					}
				}
			}
			sort.Strings(routers)
			if len(routers) != 6 {
				t.Fatalf("authoritative router inventory=%v", routers)
			}
			t.Logf("inventory CAT2=%t listed=%d billable=%d family=%d routers=%v", enabled, listed, billable, family, routers)
			if !s.Load().ServesManaged("openrouter", "openai/o1-pro") {
				t.Fatal("fixed model lost")
			}
			if s.Load().ServesManaged("openrouter", "google/lyria-3-pro-preview") != enabled {
				t.Fatal("music activation changed")
			}
		})
	}
}

func TestRoutersOff746GStaleQualification(t *testing.T) {
	all, _, err := BundledOpenRouter()
	if err != nil {
		t.Fatal(err)
	}
	for id := range all {
		if !routerbilling.FamilyMember(id) {
			continue
		}
		for _, contract := range []string{RouterInclusiveContract, InclusiveFamilyContract} {
			// Even an old positive, complete, in-process card cannot restore admission.
			m := &Model{ID: id, InclusiveEnabled: true, Prices: Prices{Prompt: 1e-6, Completion: 2e-6}, Inclusive: &models.InclusivePrice{Model: id, Contract: contract, RouterUniverse: "[]", ContextLimit: 100, OutputLimit: 64, Prompt: "0.000001", Completion: "0.000002"}}
			if m.InclusivePrice(SourceOpenRouter) != nil || m.ManagedBillable(SourceOpenRouter) || m.RouterCreditRefusal() == "" {
				t.Errorf("stale %s card re-enabled %s", contract, id)
			}
			s := NewStore()
			s.Put(&Snapshot{ProviderID: "openrouter", CatalogSource: SourceOpenRouter, Models: map[string]*Model{id: m}})
			if s.Load().ServesManaged("openrouter", id) {
				t.Errorf("refresh restored %s", id)
			}
		}
	}
}
