package routing

import (
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// DefaultCBStatusCodes are HTTP status codes that count as circuit breaker failures.
var DefaultCBStatusCodes = []int{429, 500, 502, 503, 504}

// CircuitState represents the state of a circuit breaker.
type CircuitState int

const (
	StateClosed   CircuitState = iota // Healthy — requests flow normally
	StateOpen                         // Failing — requests rejected
	StateHalfOpen                     // Testing — limited requests allowed
)

func (s CircuitState) String() string {
	switch s {
	case StateClosed:
		return "closed"
	case StateOpen:
		return "open"
	case StateHalfOpen:
		return "half-open"
	default:
		return "unknown"
	}
}

// CircuitBreaker implements the circuit breaker pattern for one breaker key:
// a provider, or one model or endpoint of a provider (BreakerKey).
type CircuitBreaker struct {
	mu            sync.Mutex
	providerID    string
	key           string // BreakerKey; providerID for a provider-level breaker
	state         CircuitState
	failures      int
	successes     int
	lastFailure   time.Time
	cfg           config.CircuitBreakerConfig
	codes         map[int]bool
	onStateChange func(string, bool) // providerID, healthy

	// models are the provider's per-model and per-endpoint breakers, which a
	// provider-level breaker's State aggregates. Nil for a per-key breaker.
	models func() []*CircuitBreaker
}

// NewCircuitBreaker creates a circuit breaker for a provider.
func NewCircuitBreaker(providerID string, cfg config.CircuitBreakerConfig, onStateChange func(string, bool)) *CircuitBreaker {
	if cfg.FailureThreshold <= 0 {
		cfg.FailureThreshold = 5
	}
	if cfg.SuccessThreshold <= 0 {
		cfg.SuccessThreshold = 2
	}
	if cfg.Cooldown <= 0 {
		cfg.Cooldown = 30 * time.Second
	}
	if len(cfg.OnStatusCodes) == 0 {
		cfg.OnStatusCodes = DefaultCBStatusCodes
	}

	codes := make(map[int]bool, len(cfg.OnStatusCodes))
	for _, c := range cfg.OnStatusCodes {
		codes[c] = true
	}

	return &CircuitBreaker{
		providerID:    providerID,
		key:           providerID,
		state:         StateClosed,
		cfg:           cfg,
		codes:         codes,
		onStateChange: onStateChange,
	}
}

// Allow returns true if the circuit allows a request through.
func (cb *CircuitBreaker) Allow() bool {
	cb.mu.Lock()
	defer cb.mu.Unlock()

	switch cb.state {
	case StateClosed:
		return true
	case StateOpen:
		// Check if cooldown has elapsed.
		if time.Since(cb.lastFailure) >= cb.cfg.Cooldown {
			cb.state = StateHalfOpen
			cb.successes = 0
			slog.Debug("circuit breaker half-open",
				"provider", cb.providerID,
				"breaker", cb.key,
			)
			return true
		}
		return false
	case StateHalfOpen:
		return true
	default:
		return true
	}
}

// RecordSuccess records a successful call.
func (cb *CircuitBreaker) RecordSuccess() {
	cb.mu.Lock()
	defer cb.mu.Unlock()

	switch cb.state {
	case StateClosed:
		cb.failures = 0
	case StateHalfOpen:
		cb.successes++
		if cb.successes >= cb.cfg.SuccessThreshold {
			cb.state = StateClosed
			cb.failures = 0
			cb.successes = 0
			slog.Info("circuit breaker closed (recovered)",
				"provider", cb.providerID,
				"breaker", cb.key,
			)
			if cb.onStateChange != nil {
				cb.onStateChange(cb.providerID, true)
			}
		}
	}
}

// RecordFailure records a failed call.
func (cb *CircuitBreaker) RecordFailure(err error) {
	if !cb.isFailure(err) {
		return
	}

	cb.mu.Lock()
	defer cb.mu.Unlock()

	cb.lastFailure = time.Now()

	switch cb.state {
	case StateClosed:
		cb.failures++
		if cb.failures >= cb.cfg.FailureThreshold {
			cb.state = StateOpen
			slog.Warn("circuit breaker opened",
				"provider", cb.providerID,
				"breaker", cb.key,
				"failures", cb.failures,
			)
			if cb.onStateChange != nil {
				cb.onStateChange(cb.providerID, false)
				cb.readmitAfterCooldown()
			}
		}
	case StateHalfOpen:
		// Any failure in half-open → re-open.
		cb.state = StateOpen
		cb.successes = 0
		slog.Warn("circuit breaker re-opened",
			"provider", cb.providerID,
			"breaker", cb.key,
		)
		if cb.onStateChange != nil {
			cb.onStateChange(cb.providerID, false)
			cb.readmitAfterCooldown()
		}
	}
}

// State returns the current circuit state. A provider-level breaker reports
// its provider's view: open when it is open itself or every one of the
// provider's per-model and per-endpoint circuits is, half-open when some of
// them are open or half-open (traffic still flows to the others), else its
// own state.
func (cb *CircuitBreaker) State() CircuitState {
	cb.mu.Lock()
	own := cb.state
	models := cb.models
	cb.mu.Unlock()
	if models == nil || own == StateOpen {
		return own
	}
	return aggregateState(own, models())
}

func aggregateState(own CircuitState, models []*CircuitBreaker) CircuitState {
	if len(models) == 0 {
		return own
	}
	open, degraded := 0, false
	for _, m := range models {
		switch m.State() {
		case StateOpen:
			open++
			degraded = true
		case StateHalfOpen:
			degraded = true
		}
	}
	switch {
	case open == len(models):
		return StateOpen
	case degraded || own == StateHalfOpen:
		return StateHalfOpen
	default:
		return own
	}
}

// Refusing reports whether the breaker refuses calls now: its own state is
// open and its cooldown has not elapsed. Unlike Allow it changes nothing, and
// unlike a provider-level State it reads only this breaker: an open circuit
// past its cooldown is not refusing, so the target is offered and the next
// Allow lets its probe through.
func (cb *CircuitBreaker) Refusing() bool {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	return cb.state == StateOpen && time.Since(cb.lastFailure) < cb.cfg.Cooldown
}

// readmitAfterCooldown puts the provider back into routing once the cooldown has passed. While
// the circuit is open the router skips the provider, so no request would otherwise reach Allow
// to move the circuit to half-open; Allow and the half-open probes decide from there.
func (cb *CircuitBreaker) readmitAfterCooldown() {
	time.AfterFunc(cb.cfg.Cooldown, func() { cb.onStateChange(cb.providerID, true) })
}

// isFailure checks if an error counts as a circuit breaker failure.
func (cb *CircuitBreaker) isFailure(err error) bool {
	if err == nil {
		return false
	}
	// A provider timeout is always a failure, as a non-API timeout is:
	// breakers have no on_timeout switch, and the openai, azure and bedrock
	// adapters answer their HTTP client's timeout as 504 gateway_timeout,
	// which a list naming 502 but not 504 still counts.
	if isProviderTimeout(err) {
		return true
	}
	var apiErr *models.APIError
	if errors.As(err, &apiErr) {
		// Its status or its provider's own status.
		return matchesStatus(cb.codes, apiErr)
	}
	// Non-API errors (timeouts, network) are always failures.
	return true
}

// CircuitBreakerRegistry manages circuit breakers by BreakerKey: one per
// model or endpoint of a provider, so one failing model does not refuse the
// provider's others, plus a provider-level breaker per provider for callers
// that key by provider id (the health views, whose State aggregates the
// provider's per-key circuits).
//
// onChange (Router.SetHealthy for the global registry) hears only
// provider-level transitions: a per-model circuit must never mark the whole
// provider unhealthy. An org's registry is built with none.
type CircuitBreakerRegistry struct {
	mu         sync.RWMutex
	breakers   map[string]*CircuitBreaker
	byProvider map[string][]*CircuitBreaker // per-key breakers of each provider
	cfg        config.CircuitBreakerConfig
	onChange   func(string, bool)
}

// NewCircuitBreakerRegistry creates a registry of circuit breakers.
func NewCircuitBreakerRegistry(cfg config.CircuitBreakerConfig, onChange func(string, bool)) *CircuitBreakerRegistry {
	return &CircuitBreakerRegistry{
		breakers:   make(map[string]*CircuitBreaker),
		byProvider: make(map[string][]*CircuitBreaker),
		cfg:        cfg,
		onChange:   onChange,
	}
}

// Get returns the circuit breaker for key, a BreakerKey or a provider id,
// creating one if needed.
func (r *CircuitBreakerRegistry) Get(key string) *CircuitBreaker {
	r.mu.RLock()
	if cb, ok := r.breakers[key]; ok {
		r.mu.RUnlock()
		return cb
	}
	r.mu.RUnlock()

	r.mu.Lock()
	defer r.mu.Unlock()
	// Double-check after acquiring write lock.
	if cb, ok := r.breakers[key]; ok {
		return cb
	}
	var cb *CircuitBreaker
	if provider, _, perKey := strings.Cut(key, breakerKeySep); perKey {
		cb = NewCircuitBreaker(provider, r.cfg, nil)
		cb.key = key
		r.byProvider[provider] = append(r.byProvider[provider], cb)
	} else {
		cb = NewCircuitBreaker(key, r.cfg, r.onChange)
		cb.models = func() []*CircuitBreaker { return r.providerBreakers(key) }
	}
	r.breakers[key] = cb
	return cb
}

// providerBreakers returns a copy of providerID's per-key breakers.
func (r *CircuitBreakerRegistry) providerBreakers(providerID string) []*CircuitBreaker {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return append([]*CircuitBreaker(nil), r.byProvider[providerID]...)
}

// IsEnabled returns true if circuit breaking is enabled.
func (r *CircuitBreakerRegistry) IsEnabled() bool {
	return r != nil && r.cfg.Enabled
}

// Refuses reports whether a call to model at providerID would be refused now:
// by the model's own circuit, or by the provider-level breaker's own state
// (which a caller recording by provider id feeds). It creates no breaker.
// It reads the model's key with no endpoint, the key failover.go checks and
// records; a caller that pins an endpoint id there must pass it here
// too, or a refusing endpoint circuit goes unseen.
func (r *CircuitBreakerRegistry) Refuses(providerID, model string) bool {
	if !r.IsEnabled() {
		return false
	}
	r.mu.RLock()
	perModel := r.breakers[BreakerKey(providerID, model, "")]
	provider := r.breakers[providerID]
	r.mu.RUnlock()
	return (perModel != nil && perModel.Refusing()) || (provider != nil && provider != perModel && provider.Refusing())
}

// SkipOpenCircuits returns s restricted to the targets whose circuit in reg
// is not refusing, so a run never spends an attempt on a target its own
// check would skip. When every target's circuit refuses, it returns a 503
// all_providers_unavailable, and the failover over it ends with that error
// rather than with no result. With circuit breaking off it returns s.
func SkipOpenCircuits(s Strategy, reg *CircuitBreakerRegistry) Strategy {
	if !reg.IsEnabled() {
		return s
	}
	return &circuitAwareStrategy{inner: s, reg: reg}
}

type circuitAwareStrategy struct {
	inner Strategy
	reg   *CircuitBreakerRegistry
}

func (c *circuitAwareStrategy) Name() string { return c.inner.Name() }

// Select checks each target's circuit for its own model override; without
// one, only the provider-level breaker (ModelStrategy).
func (c *circuitAwareStrategy) Select(targets []RoutingTarget, tracker *LatencyTracker) (int, error) {
	return c.SelectModel("", targets, tracker)
}

func (c *circuitAwareStrategy) SelectModel(model string, targets []RoutingTarget, tracker *LatencyTracker) (int, error) {
	var keepBuf [16]int
	keep := keepBuf[:0]
	for i, t := range targets {
		if !c.reg.Refuses(t.ProviderID, t.ModelFor(model)) {
			keep = append(keep, i)
		}
	}
	switch len(keep) {
	case len(targets):
		return c.selectInner(model, targets, tracker)
	case 0:
		return 0, models.ErrServiceUnavailable(fmt.Sprintf("every provider for model %q has an open circuit", targetModel(model, targets)))
	}
	var openBuf [16]RoutingTarget
	open := openBuf[:0]
	for _, i := range keep {
		open = append(open, targets[i])
	}
	idx, err := c.selectInner(model, open, tracker)
	if err != nil {
		return 0, err
	}
	return keep[idx], nil
}

func (c *circuitAwareStrategy) selectInner(model string, targets []RoutingTarget, tracker *LatencyTracker) (int, error) {
	if ms, ok := c.inner.(ModelStrategy); ok {
		return ms.SelectModel(model, targets, tracker)
	}
	return c.inner.Select(targets, tracker)
}

// targetModel names the model a selection is for, for an error message.
func targetModel(model string, targets []RoutingTarget) string {
	if model == "" && len(targets) > 0 {
		return targets[0].ModelFor(model)
	}
	return model
}
