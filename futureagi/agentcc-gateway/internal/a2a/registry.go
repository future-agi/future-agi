package a2a

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"sync"
	"sync/atomic"
	"time"
)

// Agent represents a configured external A2A agent.
type Agent struct {
	Name        string     `json:"name"`
	URL         string     `json:"url"`
	Auth        A2AAuth    `json:"-"`
	Card        *AgentCard `json:"card,omitempty"`
	Description string     `json:"description,omitempty"`
	Skills      []Skill    `json:"skills,omitempty"`
	healthy     atomic.Bool
}

// Healthy returns whether this agent is reachable.
func (a *Agent) Healthy() bool {
	return a.healthy.Load()
}

// Registry manages configured external A2A agents.
type Registry struct {
	mu     sync.RWMutex
	agents map[string]*Agent
}

// NewRegistry creates an agent registry from config.
func NewRegistry(agents map[string]AgentConfig) *Registry {
	r := &Registry{
		agents: make(map[string]*Agent, len(agents)),
	}
	for name, cfg := range agents {
		a := &Agent{
			Name:        name,
			URL:         cfg.URL,
			Auth:        cfg.Auth,
			Description: cfg.Description,
			Skills:      cfg.Skills,
		}
		a.healthy.Store(true) // assume healthy until proven otherwise
		r.agents[name] = a
	}
	return r
}

// AgentConfig holds configuration for a single external agent.
type AgentConfig struct {
	URL         string  `yaml:"url" json:"url"`
	Auth        A2AAuth `yaml:"auth" json:"auth"`
	Description string  `yaml:"description" json:"description"`
	Skills      []Skill `yaml:"skills" json:"skills"`
}

// Get retrieves an agent by name.
func (r *Registry) Get(name string) (*Agent, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	a, ok := r.agents[name]
	return a, ok
}

// List returns all registered agents.
func (r *Registry) List() []*Agent {
	r.mu.RLock()
	defer r.mu.RUnlock()
	result := make([]*Agent, 0, len(r.agents))
	for _, a := range r.agents {
		result = append(result, a)
	}
	return result
}

// Names returns all agent names.
func (r *Registry) Names() []string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	names := make([]string, 0, len(r.agents))
	for name := range r.agents {
		names = append(names, name)
	}
	return names
}

// Count returns the number of registered agents.
func (r *Registry) Count() int {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return len(r.agents)
}

// FetchCards fetches agent cards from all agents in the background.
// Non-blocking — logs warnings on failures.
func (r *Registry) FetchCards(ctx context.Context) {
	r.mu.RLock()
	agents := make([]*Agent, 0, len(r.agents))
	for _, a := range r.agents {
		agents = append(agents, a)
	}
	r.mu.RUnlock()

	client := &http.Client{Timeout: 10 * time.Second}

	for _, agent := range agents {
		go func(a *Agent) {
			cardURL := a.URL + "/.well-known/agent.json"
			req, err := http.NewRequestWithContext(ctx, "GET", cardURL, nil)
			if err != nil {
				slog.Warn("a2a: failed to create card request", "agent", a.Name, "error", err)
				return
			}

			applyAuth(req, a.Auth)

			resp, err := client.Do(req)
			if err != nil {
				slog.Warn("a2a: failed to fetch agent card", "agent", a.Name, "url", cardURL, "error", err)
				a.healthy.Store(false)
				return
			}
			defer resp.Body.Close()

			if resp.StatusCode != http.StatusOK {
				slog.Warn("a2a: agent card non-200", "agent", a.Name, "status", resp.StatusCode)
				return
			}

			var card AgentCard
			if err := json.NewDecoder(resp.Body).Decode(&card); err != nil {
				slog.Warn("a2a: invalid agent card", "agent", a.Name, "error", err)
				return
			}

			a.Card = &card
			a.healthy.Store(true)
			slog.Info("a2a: fetched agent card", "agent", a.Name, "skills", len(card.Skills))
		}(agent)
	}
}

func applyAuth(req *http.Request, auth A2AAuth) {
	switch auth.Type {
	case "bearer":
		req.Header.Set("Authorization", "Bearer "+auth.Token)
	case "api_key":
		if auth.Header != "" {
			req.Header.Set(auth.Header, auth.Key)
		}
	}
}
