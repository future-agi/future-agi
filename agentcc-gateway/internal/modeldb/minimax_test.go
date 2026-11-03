package modeldb

import (
	"reflect"
	"testing"
)

func TestMiniMaxCurrentCatalogPreservesExplicitModels(t *testing.T) {
	db := New(BundledModels, nil)
	var ids []string
	for _, m := range db.ListByProvider("minimax") {
		if m.Mode == ModeChat {
			ids = append(ids, m.ID)
		}
	}
	want := []string{"minimax/MiniMax-M3.1-Flash", "minimax/MiniMax-M3"}
	if !reflect.DeepEqual(ids, want) {
		t.Fatalf("ordinary MiniMax chat choices = %v, want %v", ids, want)
	}
	for _, list := range [][]*ModelInfo{db.List(), db.ListByMode(ModeChat)} {
		for _, m := range list {
			if m.Provider == "minimax" && m.Mode == ModeChat && m.ID != want[0] && m.ID != want[1] {
				t.Fatalf("legacy choice leaked: %s", m.ID)
			}
		}
	}
	listed := make(map[string]bool)
	for _, m := range db.List() {
		listed[m.ID] = true
	}
	for id, original := range BundledModels {
		if _, ok := db.Get(id); !ok {
			t.Errorf("explicit lookup lost %s", id)
		}
		if original.Provider != "minimax" || original.Mode != ModeChat {
			if !listed[id] {
				t.Errorf("unrelated catalog entry lost %s", id)
			}
		}
	}
	configured := New(BundledModels, map[string]ModelOverride{
		"minimax/MiniMax-M2.7": {},
		"minimax/custom":       {Provider: "minimax", Mode: "chat"},
	})
	for _, id := range []string{"minimax/MiniMax-M2.7", "minimax/custom"} {
		found := false
		for _, m := range configured.ListByProvider("minimax") {
			found = found || m.ID == id
		}
		if !found {
			t.Errorf("explicit choice lost %s", id)
		}
	}
}

func TestMiniMaxFlashMetadata(t *testing.T) {
	db := New(BundledModels, nil)
	m, ok := db.Get("minimax/MiniMax-M3.1-Flash")
	if !ok {
		t.Fatal("Flash missing")
	}
	if bare, ok := db.Get("MiniMax-M3.1-Flash"); !ok || bare != m {
		t.Fatal("bare request model does not resolve to bundled metadata")
	}
	if _, ok := db.CalculateCost("MiniMax-M3.1-Flash", 1000, 500, CostOptions{}); !ok {
		t.Fatal("bare request model has no cost metadata")
	}
	if m.MaxInputTokens != 1_000_000 || m.MaxOutputTokens != 524_288 {
		t.Fatalf("wrong limits: %+v", m)
	}
	if m.Pricing.InputPerToken != 0.15e-6 || m.Pricing.OutputPerToken != 0.60e-6 || m.Pricing.CachedInputPerToken != 0.015e-6 {
		t.Fatalf("wrong prices: %+v", m.Pricing)
	}
	if !m.Capabilities.FunctionCalling || !m.Capabilities.Vision || !m.Capabilities.Reasoning || !m.Capabilities.Streaming || !m.Capabilities.PromptCaching {
		t.Fatalf("missing capabilities: %+v", m.Capabilities)
	}
}
