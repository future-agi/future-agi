package a2a

import (
	"encoding/json"
	"net/http"
	"sync/atomic"
)

// CardGenerator builds and caches Agentcc's agent card.
type CardGenerator struct {
	cached atomic.Pointer[[]byte] // pre-serialized JSON
}

// CardConfig holds settings for generating the agent card.
type CardConfig struct {
	Name        string
	Description string
	Version     string
	URL         string
	Skills      []Skill
}

// NewCardGenerator creates a generator and builds the initial card.
func NewCardGenerator(cfg CardConfig) *CardGenerator {
	g := &CardGenerator{}
	g.Regenerate(cfg)
	return g
}

// Regenerate rebuilds the cached card (called on config reload).
func (g *CardGenerator) Regenerate(cfg CardConfig) {
	card := AgentCard{
		Name:        cfg.Name,
		Description: cfg.Description,
		URL:         cfg.URL,
		Version:     cfg.Version,
		Capabilities: AgentCapabilities{
			Streaming:         true,
			PushNotifications: false,
		},
		Skills: cfg.Skills,
		SecuritySchemes: []SecurityScheme{
			{Type: "http", Scheme: "bearer"},
		},
		DefaultInputModes:  []string{"text"},
		DefaultOutputModes: []string{"text"},
	}

	data, _ := json.Marshal(card)
	g.cached.Store(&data)
}

// HandleAgentCard serves GET /.well-known/agent.json.
func (g *CardGenerator) HandleAgentCard(w http.ResponseWriter, r *http.Request) {
	data := g.cached.Load()
	if data == nil {
		http.Error(w, "agent card not available", http.StatusServiceUnavailable)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "public, max-age=3600")
	w.Write(*data)
}
