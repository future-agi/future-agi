package rotation

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"time"
)

// Status constants for key rotation state.
const (
	StatusIdle     = "idle"     // no rotation in progress
	StatusPending  = "pending"  // new key set, awaiting promotion
	StatusDraining = "draining" // the new key serves every new call; the old one's connections close at DrainUntil
)

// DefaultDrainPeriod is a provider's drain period when its rotation config
// sets none.
const DefaultDrainPeriod = 30 * time.Second

// ErrValidation wraps the error of a promote whose new key failed its
// validation call: the key was not applied and the rotation stays pending.
var ErrValidation = errors.New("new key failed validation")

// ErrNotRegistered is returned for a provider with no rotation registered
// (providers.<id>.rotation.enabled is false or the provider does not exist).
var ErrNotRegistered = errors.New("provider has no key rotation")

// KeyState represents the rotation state of a provider's API key.
type KeyState struct {
	ProviderID string     `json:"provider_id"`
	Primary    string     `json:"primary"`               // current active key (masked in JSON)
	Status     string     `json:"status"`                // idle, pending, draining
	PendingKey string     `json:"pending_key,omitempty"` // new key awaiting promotion (masked)
	OldKey     string     `json:"old_key,omitempty"`     // previous key during drain (masked)
	DrainUntil *time.Time `json:"drain_until,omitempty"` // when drain period ends
	UpdatedAt  time.Time  `json:"updated_at"`

	// DrainPeriodMS is this provider's drain period
	// (providers.<id>.rotation.drain_period).
	DrainPeriodMS int64 `json:"drain_period_ms"`
	// ValidatedBy names the upstream call that validated the key now
	// primary, when a promote on this replica did; empty otherwise.
	ValidatedBy string `json:"validated_by,omitempty"`
	// Source is where the current primary came from: "config" (the
	// deployment config at start), "admin_api" (a promote on this replica)
	// or "replica" (a promote another replica broadcast).
	Source string `json:"source"`

	drainPeriod time.Duration
	generation  uint64 // advances on every applied key, so a stale drain timer ends nothing
}

// MaskedState returns a copy with keys masked for API responses.
func (s *KeyState) MaskedState() KeyState {
	out := *s
	out.Primary = maskKey(s.Primary)
	out.PendingKey = maskKey(s.PendingKey)
	out.OldKey = maskKey(s.OldKey)
	return out
}

// Hooks connect a Manager to the provider it rotates. Every hook is optional.
type Hooks struct {
	// Validate makes one upstream call to providerID with key, and returns
	// a description of that call. A promote applies a key only when
	// Validate returns nil.
	Validate func(ctx context.Context, providerID, key string) (method string, err error)
	// Apply makes key the provider's key for every new upstream call. Calls
	// already in flight finish on what they hold. retire, when non-nil,
	// releases what the previous key used; the Manager calls it once the
	// provider's drain period has passed.
	Apply func(providerID, key string) (retire func(), err error)
	// Promoted is called after a promote on this replica has applied key
	// (to tell the other replicas). Not called for ApplyRemote.
	Promoted func(providerID, key string)
}

// Manager handles zero-downtime key rotation for providers.
type Manager struct {
	mu          sync.RWMutex
	states      map[string]*KeyState
	drainPeriod time.Duration // for providers registered without one
	hooks       Hooks
}

// NewManager creates a key rotation manager. drainPeriod applies to every
// provider registered without one of its own. onRotate, when non-nil, is
// called with each key a promote applies (SetHooks replaces it).
func NewManager(drainPeriod time.Duration, onRotate func(providerID, newKey string)) *Manager {
	if drainPeriod <= 0 {
		drainPeriod = DefaultDrainPeriod
	}
	m := &Manager{
		states:      make(map[string]*KeyState),
		drainPeriod: drainPeriod,
	}
	if onRotate != nil {
		m.hooks.Apply = func(providerID, key string) (func(), error) {
			onRotate(providerID, key)
			return nil, nil
		}
	}
	return m
}

// SetHooks connects the manager to the providers it rotates. Call before
// serving.
func (m *Manager) SetHooks(h Hooks) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.hooks = h
}

// RegisterProvider registers a provider with its current key and the
// manager's drain period.
func (m *Manager) RegisterProvider(providerID, currentKey string) {
	m.RegisterProviderWithDrain(providerID, currentKey, 0)
}

// RegisterProviderWithDrain registers a provider with its current key and its
// own drain period (0: the manager's).
func (m *Manager) RegisterProviderWithDrain(providerID, currentKey string, drainPeriod time.Duration) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if drainPeriod <= 0 {
		drainPeriod = m.drainPeriod
	}
	m.states[providerID] = &KeyState{
		ProviderID:    providerID,
		Primary:       currentKey,
		Status:        StatusIdle,
		UpdatedAt:     time.Now(),
		DrainPeriodMS: drainPeriod.Milliseconds(),
		Source:        "config",
		drainPeriod:   drainPeriod,
	}
}

// StartRotation sets a new pending key for a provider.
func (m *Manager) StartRotation(providerID, newKey string) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	state, ok := m.states[providerID]
	if !ok {
		return fmt.Errorf("provider %q: %w", providerID, ErrNotRegistered)
	}

	if state.Status == StatusDraining && state.DrainUntil != nil {
		return fmt.Errorf("provider %q is draining until %s; a new rotation can start after that", providerID, drainUntilText(state.DrainUntil))
	}
	if state.Status != StatusIdle {
		return fmt.Errorf("provider %q already has rotation in status %q", providerID, state.Status)
	}

	if newKey == "" {
		return fmt.Errorf("new key cannot be empty")
	}

	state.PendingKey = newKey
	state.Status = StatusPending
	state.UpdatedAt = time.Now()
	slog.Info("provider key rotation started", "provider", providerID)
	return nil
}

// Validate tests the pending key using the provided validation function.
func (m *Manager) Validate(ctx context.Context, providerID string, validateFn func(ctx context.Context, key string) error) error {
	m.mu.RLock()
	state, ok := m.states[providerID]
	if !ok {
		m.mu.RUnlock()
		return fmt.Errorf("provider %q: %w", providerID, ErrNotRegistered)
	}
	if state.Status != StatusPending {
		m.mu.RUnlock()
		return fmt.Errorf("provider %q has no pending key (status: %s)", providerID, state.Status)
	}
	pendingKey := state.PendingKey
	m.mu.RUnlock()

	return validateFn(ctx, pendingKey)
}

// Promote validates the pending key with one upstream call (Hooks.Validate),
// then applies it (Hooks.Apply): every new upstream call uses it at once,
// calls in flight finish on the old one, and the old key's resources are
// released once the provider's drain period has passed. A key that fails
// validation is not applied and the rotation stays pending (ErrValidation).
// It returns how the key was validated ("" with no Validate hook).
func (m *Manager) Promote(ctx context.Context, providerID string) (string, error) {
	m.mu.RLock()
	state, ok := m.states[providerID]
	if !ok {
		m.mu.RUnlock()
		return "", fmt.Errorf("provider %q: %w", providerID, ErrNotRegistered)
	}
	if state.Status != StatusPending {
		m.mu.RUnlock()
		return "", fmt.Errorf("provider %q has no pending key (status: %s)", providerID, state.Status)
	}
	key, gen, validate := state.PendingKey, state.generation, m.hooks.Validate
	m.mu.RUnlock()

	// The validation call goes upstream: never under the lock.
	method := ""
	if validate != nil {
		var err error
		if method, err = validate(ctx, providerID, key); err != nil {
			slog.Warn("provider key rotation: new key failed validation, not promoted",
				"provider", providerID, "validated_by", method, "error", err)
			return method, fmt.Errorf("%w: %v", ErrValidation, err)
		}
	}

	m.mu.Lock()
	state = m.states[providerID]
	if state == nil || state.Status != StatusPending || state.PendingKey != key || state.generation != gen {
		m.mu.Unlock()
		return method, fmt.Errorf("provider %q: the pending rotation changed while its key was validated; promote again", providerID)
	}
	retire, err := m.applyLocked(state, key, "admin_api", method)
	promoted := m.hooks.Promoted
	m.mu.Unlock()
	if err != nil {
		return method, err
	}
	if promoted != nil {
		promoted(providerID, key)
	}
	m.scheduleDrain(providerID, retire)
	return method, nil
}

// ApplyRemote applies a key another replica promoted (it validated it). A
// key that is already primary changes nothing. A pending rotation on this
// replica is replaced.
func (m *Manager) ApplyRemote(providerID, key string) (bool, error) {
	if key == "" {
		return false, fmt.Errorf("empty key")
	}
	m.mu.Lock()
	state, ok := m.states[providerID]
	if !ok {
		m.mu.Unlock()
		return false, fmt.Errorf("provider %q: %w", providerID, ErrNotRegistered)
	}
	if state.Primary == key {
		m.mu.Unlock()
		return false, nil
	}
	retire, err := m.applyLocked(state, key, "replica", "")
	m.mu.Unlock()
	if err != nil {
		return false, err
	}
	m.scheduleDrain(providerID, retire)
	return true, nil
}

// applyLocked applies key through Hooks.Apply and records it as primary,
// with the old one draining. m.mu must be held. It returns the retire
// function for the drain timer, which carries the generation it ends.
func (m *Manager) applyLocked(state *KeyState, key, source, method string) (drainEnd, error) {
	var retire func()
	if m.hooks.Apply != nil {
		var err error
		if retire, err = m.hooks.Apply(state.ProviderID, key); err != nil {
			return drainEnd{}, fmt.Errorf("provider %q: applying the new key: %w", state.ProviderID, err)
		}
	}
	now := time.Now()
	drainUntil := now.Add(state.drainPeriod)
	state.OldKey = state.Primary
	state.Primary = key
	state.PendingKey = ""
	state.Status = StatusDraining
	state.DrainUntil = &drainUntil
	state.UpdatedAt = now
	state.ValidatedBy = method
	state.Source = source
	state.generation++
	// The audit line: provider, source and timing only, never a key or a
	// part of one.
	slog.Info("provider key rotation promoted",
		"provider", state.ProviderID,
		"source", source,
		"validated_by", method,
		"drain_period", state.drainPeriod.String(),
		"applies_until", "restart (the deployment config wins on restart)",
	)
	return drainEnd{generation: state.generation, after: state.drainPeriod, retire: retire}, nil
}

// drainEnd is what a drain timer does when it fires.
type drainEnd struct {
	generation uint64
	after      time.Duration
	retire     func()
}

func (m *Manager) scheduleDrain(providerID string, d drainEnd) {
	time.AfterFunc(d.after, func() {
		m.completeDrain(providerID, d.generation)
		// What the old key used is released whatever happened since: it
		// serves no new call.
		if d.retire != nil {
			d.retire()
		}
	})
}

// Rollback cancels a pending rotation and keeps the current primary key. A
// promoted key is already serving every new call, so a draining rotation is
// not rolled back: after drain_until (a rotation starts only from idle),
// start a new rotation with the previous key instead.
func (m *Manager) Rollback(providerID string) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	state, ok := m.states[providerID]
	if !ok {
		return fmt.Errorf("provider %q: %w", providerID, ErrNotRegistered)
	}

	switch state.Status {
	case StatusIdle:
		return fmt.Errorf("provider %q has no active rotation", providerID)
	case StatusDraining:
		return fmt.Errorf("provider %q: the promoted key already serves every new call; to go back, after drain_until %s, start a new rotation with the previous key", providerID, drainUntilText(state.DrainUntil))
	}

	state.PendingKey = ""
	state.Status = StatusIdle
	state.UpdatedAt = time.Now()
	slog.Info("provider key rotation rolled back", "provider", providerID)
	return nil
}

// drainUntilText is drain_until as the rotation errors name it: UTC,
// RFC 3339 with the fraction, so a client can wait exactly that long.
func drainUntilText(t *time.Time) string {
	if t == nil {
		return "the end of the drain period"
	}
	return t.UTC().Format(time.RFC3339Nano)
}

// GetStatus returns the rotation state for a provider.
func (m *Manager) GetStatus(providerID string) (*KeyState, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	state, ok := m.states[providerID]
	if !ok {
		return nil, fmt.Errorf("provider %q: %w", providerID, ErrNotRegistered)
	}

	cp := *state
	return &cp, nil
}

// GetActiveKey returns the current primary key for a provider.
func (m *Manager) GetActiveKey(providerID string) string {
	m.mu.RLock()
	defer m.mu.RUnlock()

	state, ok := m.states[providerID]
	if !ok {
		return ""
	}
	return state.Primary
}

// completeDrain ends the drain of the key applied as generation gen; a later
// key's drain is left alone.
func (m *Manager) completeDrain(providerID string, gen uint64) {
	m.mu.Lock()
	defer m.mu.Unlock()

	state, ok := m.states[providerID]
	if !ok || state.Status != StatusDraining || state.generation != gen {
		return
	}

	state.OldKey = ""
	state.DrainUntil = nil
	state.Status = StatusIdle
	state.UpdatedAt = time.Now()
}

func maskKey(key string) string {
	if key == "" {
		return ""
	}
	if len(key) <= 8 {
		return "***"
	}
	return key[:4] + "..." + key[len(key)-4:]
}
