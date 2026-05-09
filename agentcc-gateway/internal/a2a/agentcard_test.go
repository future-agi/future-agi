package a2a

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestCardGeneratorBasic(t *testing.T) {
	g := NewCardGenerator(CardConfig{
		Name:        "Test Agent",
		Description: "A test",
		Version:     "2.0",
		URL:         "https://example.com/a2a",
		Skills: []Skill{
			{ID: "chat", Name: "Chat"},
		},
	})

	req := httptest.NewRequest("GET", "/.well-known/agent.json", nil)
	w := httptest.NewRecorder()
	g.HandleAgentCard(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	if w.Header().Get("Content-Type") != "application/json" {
		t.Fatal("expected application/json")
	}
	if w.Header().Get("Cache-Control") != "public, max-age=3600" {
		t.Fatal("expected cache-control header")
	}

	var card AgentCard
	if err := json.NewDecoder(w.Body).Decode(&card); err != nil {
		t.Fatal(err)
	}
	if card.Name != "Test Agent" {
		t.Fatalf("expected 'Test Agent', got %s", card.Name)
	}
	if card.Version != "2.0" {
		t.Fatalf("expected version 2.0, got %s", card.Version)
	}
	if len(card.Skills) != 1 {
		t.Fatalf("expected 1 skill, got %d", len(card.Skills))
	}
}

func TestCardGeneratorRegenerate(t *testing.T) {
	g := NewCardGenerator(CardConfig{Name: "v1"})

	req := httptest.NewRequest("GET", "/", nil)
	w := httptest.NewRecorder()
	g.HandleAgentCard(w, req)

	var card1 AgentCard
	json.NewDecoder(w.Body).Decode(&card1)
	if card1.Name != "v1" {
		t.Fatalf("expected v1, got %s", card1.Name)
	}

	// Regenerate.
	g.Regenerate(CardConfig{Name: "v2", Version: "3.0"})

	w = httptest.NewRecorder()
	g.HandleAgentCard(w, req)

	var card2 AgentCard
	json.NewDecoder(w.Body).Decode(&card2)
	if card2.Name != "v2" {
		t.Fatalf("expected v2, got %s", card2.Name)
	}
	if card2.Version != "3.0" {
		t.Fatalf("expected 3.0, got %s", card2.Version)
	}
}

func TestCardSecurityScheme(t *testing.T) {
	g := NewCardGenerator(CardConfig{Name: "test"})

	req := httptest.NewRequest("GET", "/", nil)
	w := httptest.NewRecorder()
	g.HandleAgentCard(w, req)

	var card AgentCard
	json.NewDecoder(w.Body).Decode(&card)

	if len(card.SecuritySchemes) != 1 {
		t.Fatalf("expected 1 security scheme, got %d", len(card.SecuritySchemes))
	}
	if card.SecuritySchemes[0].Scheme != "bearer" {
		t.Fatalf("expected bearer, got %s", card.SecuritySchemes[0].Scheme)
	}
}

func TestCardDefaultModes(t *testing.T) {
	g := NewCardGenerator(CardConfig{Name: "test"})

	req := httptest.NewRequest("GET", "/", nil)
	w := httptest.NewRecorder()
	g.HandleAgentCard(w, req)

	var card AgentCard
	json.NewDecoder(w.Body).Decode(&card)

	if len(card.DefaultInputModes) != 1 || card.DefaultInputModes[0] != "text" {
		t.Fatal("expected default input mode 'text'")
	}
	if len(card.DefaultOutputModes) != 1 || card.DefaultOutputModes[0] != "text" {
		t.Fatal("expected default output mode 'text'")
	}
}
