package routing

import (
	"errors"
	"log/slog"
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

// CircuitBreaker implements the circuit breaker pattern for a single provider.
type CircuitBreaker struct {
	mu            sync.Mutex
	providerID    string
	state         CircuitState
	failures      int
	successes     int
	lastFailure   time.Time
	cfg           config.CircuitBreakerConfig
	codes         map[int]bool
	onStateChange func(string, bool) // providerID, healthy
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
				"failures", cb.failures,
			)
			if cb.onStateChange != nil {
				cb.onStateChange(cb.providerID, false)
			}
		}
	case StateHalfOpen:
		// Any failure in half-open → re-open.
		cb.state = StateOpen
		cb.successes = 0
		slog.Warn("circuit breaker re-opened",
			"provider", cb.providerID,
		)
		if cb.onStateChange != nil {
			cb.onStateChange(cb.providerID, false)
		}
	}
}

// State returns the current circuit state.
func (cb *CircuitBreaker) State() CircuitState {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	return cb.state
}

// isFailure checks if an error counts as a circuit breaker failure.
func (cb *CircuitBreaker) isFailure(err error) bool {
	if err == nil {
		return false
	}
	var apiErr *models.APIError
	if errors.As(err, &apiErr) {
		return cb.codes[apiErr.Status]
	}
	// Non-API errors (timeouts, network) are always failures.
	return true
}

// CircuitBreakerRegistry manages per-provider circuit breakers.
type CircuitBreakerRegistry struct {
	mu       sync.RWMutex
	breakers map[string]*CircuitBreaker
	cfg      config.CircuitBreakerConfig
	onChange func(string, bool)
}

// NewCircuitBreakerRegistry creates a registry of circuit breakers.
func NewCircuitBreakerRegistry(cfg config.CircuitBreakerConfig, onChange func(string, bool)) *CircuitBreakerRegistry {
	return &CircuitBreakerRegistry{
		breakers: make(map[string]*CircuitBreaker),
		cfg:      cfg,
		onChange: onChange,
	}
}

// Get returns the circuit breaker for a provider, creating one if needed.
func (r *CircuitBreakerRegistry) Get(providerID string) *CircuitBreaker {
	r.mu.RLock()
	if cb, ok := r.breakers[providerID]; ok {
		r.mu.RUnlock()
		return cb
	}
	r.mu.RUnlock()

	r.mu.Lock()
	defer r.mu.Unlock()
	// Double-check after acquiring write lock.
	if cb, ok := r.breakers[providerID]; ok {
		return cb
	}
	cb := NewCircuitBreaker(providerID, r.cfg, r.onChange)
	r.breakers[providerID] = cb
	return cb
}

// IsEnabled returns true if circuit breaking is enabled.
func (r *CircuitBreakerRegistry) IsEnabled() bool {
	return r != nil && r.cfg.Enabled
}
