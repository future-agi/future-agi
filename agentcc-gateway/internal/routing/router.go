package routing

import (
	"fmt"
	"log/slog"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Router coordinates load balancing across multiple providers for the same model.
type Router struct {
	mu              sync.RWMutex
	modelTargets    map[string][]RoutingTarget // model → routing targets
	defaultStrategy Strategy
	latencyTracker  *LatencyTracker
}

// RoutingResult is returned by Select with the chosen target and strategy name.
type RoutingResult struct {
	Target       RoutingTarget
	StrategyName string
}

// NewRouter creates a router from config.
// providerIDs is the set of valid provider IDs for validation.
// modelProviders maps model name → list of provider IDs that serve it.
func NewRouter(cfg config.RoutingConfig, providerIDs map[string]bool, modelProviders map[string][]string) (*Router, error) {
	strategy, err := NewStrategy(cfg.DefaultStrategy)
	if err != nil {
		return nil, fmt.Errorf("creating default strategy: %w", err)
	}

	r := &Router{
		modelTargets:    make(map[string][]RoutingTarget),
		defaultStrategy: strategy,
		latencyTracker:  NewLatencyTracker(0.3),
	}

	// Build targets from explicit config.
	for model, targets := range cfg.Targets {
		for _, tc := range targets {
			if !providerIDs[tc.Provider] {
				return nil, fmt.Errorf("routing target for model %q references unknown provider %q", model, tc.Provider)
			}
			weight := tc.Weight
			if weight <= 0 {
				weight = 1
			}
			r.modelTargets[model] = append(r.modelTargets[model], RoutingTarget{
				ProviderID:    tc.Provider,
				Weight:        weight,
				Priority:      tc.Priority,
				ModelOverride: tc.ModelOverride,
				Healthy:       true,
			})
		}
	}

	// Auto-build targets for models with multiple providers that aren't explicitly configured.
	for model, providers := range modelProviders {
		if _, exists := r.modelTargets[model]; exists {
			continue // explicit config takes precedence
		}
		if len(providers) <= 1 {
			continue // single provider, no routing needed
		}
		for _, pid := range providers {
			r.modelTargets[model] = append(r.modelTargets[model], RoutingTarget{
				ProviderID: pid,
				Weight:     1,
				Healthy:    true,
			})
		}
	}

	slog.Info("router initialized",
		"strategy", strategy.Name(),
		"models_with_routing", len(r.modelTargets),
	)

	return r, nil
}

// HasTargets returns true if the model has routing targets configured.
func (r *Router) HasTargets(model string) bool {
	r.mu.RLock()
	defer r.mu.RUnlock()
	targets := r.modelTargets[model]
	return len(targets) > 1
}

// Select picks a provider for the given model using the configured strategy.
func (r *Router) Select(model string) (*RoutingResult, error) {
	return r.selectMatching(model, nil, false)
}

// SelectExactModel picks a provider for the given model but only from targets
// that preserve the requested model name.
func (r *Router) SelectExactModel(model string) (*RoutingResult, error) {
	return r.selectMatching(model, nil, true)
}

func (r *Router) selectMatching(model string, exclude map[string]bool, requireExactModel bool) (*RoutingResult, error) {
	// Copy healthy targets under read lock to avoid racing with SetHealthy.
	r.mu.RLock()
	targets := r.modelTargets[model]
	healthy := make([]RoutingTarget, 0, len(targets))
	for _, t := range targets {
		if !t.Healthy {
			continue
		}
		if exclude != nil && exclude[t.ProviderID] {
			continue
		}
		if requireExactModel && t.ModelOverride != "" && t.ModelOverride != model {
			continue
		}
		healthy = append(healthy, t)
	}
	r.mu.RUnlock()

	if len(targets) == 0 {
		return nil, fmt.Errorf("no routing targets for model %q", model)
	}
	if len(healthy) == 0 {
		if requireExactModel {
			return nil, fmt.Errorf("no providers for model %q preserve the requested model", model)
		}
		return nil, fmt.Errorf("all providers for model %q are unavailable", model)
	}

	// Single healthy target — no strategy needed.
	if len(healthy) == 1 {
		return &RoutingResult{
			Target:       healthy[0],
			StrategyName: r.defaultStrategy.Name(),
		}, nil
	}

	idx, err := r.defaultStrategy.Select(healthy, r.latencyTracker)
	if err != nil {
		return nil, err
	}
	return &RoutingResult{
		Target:       healthy[idx],
		StrategyName: r.defaultStrategy.Name(),
	}, nil
}

// RecordLatency records a provider's response latency for least-latency routing.
func (r *Router) RecordLatency(providerID string, d time.Duration) {
	r.latencyTracker.Record(providerID, d)
}

// SetHealthy sets the health status of a provider across all models.
func (r *Router) SetHealthy(providerID string, healthy bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for model := range r.modelTargets {
		for i := range r.modelTargets[model] {
			if r.modelTargets[model][i].ProviderID == providerID {
				r.modelTargets[model][i].Healthy = healthy
			}
		}
	}
}

// SelectExcluding picks a provider, excluding the given provider IDs.
func (r *Router) SelectExcluding(model string, exclude map[string]bool) (*RoutingResult, error) {
	return r.selectMatching(model, exclude, false)
}

// TargetCount returns the number of routing targets for a model.
func (r *Router) TargetCount(model string) int {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return len(r.modelTargets[model])
}

// Strategy returns the default strategy name.
func (r *Router) Strategy() string {
	return r.defaultStrategy.Name()
}

// GetLatencyTracker returns the latency tracker for external health monitoring.
func (r *Router) GetLatencyTracker() *LatencyTracker {
	return r.latencyTracker
}
