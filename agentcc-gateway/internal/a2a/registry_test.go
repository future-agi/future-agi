package a2a

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
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

func TestAgentCardNilByDefault(t *testing.T) {
	r := NewRegistry(map[string]AgentConfig{
		"test": {URL: "http://test.local"},
	})
	a, _ := r.Get("test")
	if a.Card() != nil {
		t.Fatal("expected nil card before fetch")
	}
}

func TestFetchCardsStoresCard(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/.well-known/agent.json" {
			http.NotFound(w, r)
			return
		}
		json.NewEncoder(w).Encode(AgentCard{
			Name:    "travel",
			URL:     "http://travel.local",
			Version: "1.0",
			Skills:  []Skill{{ID: "book", Name: "Book"}},
		})
	}))
	defer srv.Close()

	reg := NewRegistry(map[string]AgentConfig{
		"travel": {URL: srv.URL},
	})
	reg.FetchCards(context.Background())

	deadline := time.Now().Add(2 * time.Second)
	for {
		a, ok := reg.Get("travel")
		if !ok {
			t.Fatal("agent missing")
		}
		card := a.Card()
		if card != nil {
			if card.Name != "travel" {
				t.Fatalf("expected name travel, got %s", card.Name)
			}
			if len(card.Skills) != 1 || card.Skills[0].ID != "book" {
				t.Fatalf("unexpected skills: %+v", card.Skills)
			}
			if !a.Healthy() {
				t.Fatal("expected healthy after successful card fetch")
			}
			return
		}
		if time.Now().After(deadline) {
			t.Fatal("timed out waiting for agent card")
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func TestFetchCardsAndListRace(t *testing.T) {
	// Keep readers live until a background Store is observed so -race can
	// see concurrent Card() loads vs FetchCards stores.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(AgentCard{Name: "race", Version: "1.0"})
	}))
	defer srv.Close()

	reg := NewRegistry(map[string]AgentConfig{
		"race": {URL: srv.URL},
	})
	ctx := context.Background()

	stop := make(chan struct{})
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				select {
				case <-stop:
					return
				default:
					for _, a := range reg.List() {
						_ = a.Card()
						_ = a.Healthy()
					}
				}
			}
		}()
	}

	for i := 0; i < 20; i++ {
		reg.FetchCards(ctx)
	}

	deadline := time.Now().Add(2 * time.Second)
	for {
		a, ok := reg.Get("race")
		if !ok {
			close(stop)
			wg.Wait()
			t.Fatal("agent missing")
		}
		if a.Card() != nil {
			break
		}
		if time.Now().After(deadline) {
			close(stop)
			wg.Wait()
			t.Fatal("timed out waiting for agent card during race test")
		}
		time.Sleep(5 * time.Millisecond)
	}

	close(stop)
	wg.Wait()
}
