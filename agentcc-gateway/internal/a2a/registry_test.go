package a2a

import (
	"testing"
)

func TestRegistryGet(t *testing.T) {
	agents := map[string]AgentConfig{
		"travel": {URL: "http://travel.local", Description: "Travel agent"},
		"code":   {URL: "http://code.local"},
	}
	r := NewRegistry(agents)

	a, ok := r.Get("travel")
	if !ok {
		t.Fatal("expected to find travel agent")
	}
	if a.URL != "http://travel.local" {
		t.Fatalf("expected URL, got %s", a.URL)
	}
	if a.Description != "Travel agent" {
		t.Fatalf("expected description, got %s", a.Description)
	}

	_, ok = r.Get("nonexistent")
	if ok {
		t.Fatal("expected not found")
	}
}

func TestRegistryList(t *testing.T) {
	agents := map[string]AgentConfig{
		"a": {URL: "http://a.local"},
		"b": {URL: "http://b.local"},
	}
	r := NewRegistry(agents)

	list := r.List()
	if len(list) != 2 {
		t.Fatalf("expected 2 agents, got %d", len(list))
	}
}

func TestRegistryNames(t *testing.T) {
	agents := map[string]AgentConfig{
		"alpha": {URL: "http://a.local"},
		"beta":  {URL: "http://b.local"},
	}
	r := NewRegistry(agents)

	names := r.Names()
	if len(names) != 2 {
		t.Fatalf("expected 2 names, got %d", len(names))
	}
}

func TestRegistryCount(t *testing.T) {
	r := NewRegistry(nil)
	if r.Count() != 0 {
		t.Fatal("expected 0 for nil config")
	}

	r2 := NewRegistry(map[string]AgentConfig{
		"a": {URL: "http://a.local"},
	})
	if r2.Count() != 1 {
		t.Fatal("expected 1")
	}
}

func TestAgentHealthy(t *testing.T) {
	agents := map[string]AgentConfig{
		"test": {URL: "http://test.local"},
	}
	r := NewRegistry(agents)

	a, _ := r.Get("test")
	if !a.Healthy() {
		t.Fatal("expected healthy by default")
	}

	a.healthy.Store(false)
	if a.Healthy() {
		t.Fatal("expected unhealthy")
	}
}

func TestRegistryWithSkills(t *testing.T) {
	agents := map[string]AgentConfig{
		"travel": {
			URL: "http://travel.local",
			Skills: []Skill{
				{ID: "book", Name: "Book Flight", Description: "Book flights"},
			},
		},
	}
	r := NewRegistry(agents)

	a, _ := r.Get("travel")
	if len(a.Skills) != 1 {
		t.Fatalf("expected 1 skill, got %d", len(a.Skills))
	}
	if a.Skills[0].ID != "book" {
		t.Fatalf("expected skill id 'book', got %s", a.Skills[0].ID)
	}
}
