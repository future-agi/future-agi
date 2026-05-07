package rotation

import (
	"context"
	"fmt"
	"sync"
	"time"
)

// Status constants for key rotation state.
const (
	StatusIdle     = "idle"     // no rotation in progress
	StatusPending  = "pending"  // new key set, awaiting promotion
	StatusDraining = "draining" // old key still active for in-flight requests
)

// KeyState represents the rotation state of a provider's API key.
type KeyState struct {
	ProviderID string     `json:"provider_id"`
	Primary    string     `json:"primary"`               // current active key (masked in JSON)
	Status     string     `json:"status"`                // idle, pending, draining
	PendingKey string     `json:"pending_key,omitempty"` // new key awaiting promotion (masked)
	OldKey     string     `json:"old_key,omitempty"`     // previous key during drain (masked)
	DrainUntil *time.Time `json:"drain_until,omitempty"` // when drain period ends
	UpdatedAt  time.Time  `json:"updated_at"`
}

// MaskedState returns a copy with keys masked for API responses.
func (s *KeyState) MaskedState() KeyState {
	out := *s
	out.Primary = maskKey(s.Primary)
	out.PendingKey = maskKey(s.PendingKey)
	out.OldKey = maskKey(s.OldKey)
	return out
}

// Manager handles zero-downtime key rotation for providers.
type Manager struct {
	mu          sync.RWMutex
	states      map[string]*KeyState
	drainPeriod time.Duration
	onRotate    func(providerID, newKey string) // callback to update provider with new key
}

// NewManager creates a key rotation manager.
// onRotate is called when a key is promoted — it should update the provider's active key.
func NewManager(drainPeriod time.Duration, onRotate func(providerID, newKey string)) *Manager {
	return &Manager{
		states:      make(map[string]*KeyState),
		drainPeriod: drainPeriod,
		onRotate:    onRotate,
	}
}

// RegisterProvider registers a provider with its current key.
func (m *Manager) RegisterProvider(providerID, currentKey string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.states[providerID] = &KeyState{
		ProviderID: providerID,
		Primary:    currentKey,
		Status:     StatusIdle,
		UpdatedAt:  time.Now(),
	}
}

// StartRotation sets a new pending key for a provider.
func (m *Manager) StartRotation(providerID, newKey string) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	state, ok := m.states[providerID]
	if !ok {
		return fmt.Errorf("provider %q not registered", providerID)
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
	return nil
}

// Validate tests the pending key using the provided validation function.
func (m *Manager) Validate(ctx context.Context, providerID string, validateFn func(ctx context.Context, key string) error) error {
	m.mu.RLock()
	state, ok := m.states[providerID]
	if !ok {
		m.mu.RUnlock()
		return fmt.Errorf("provider %q not registered", providerID)
	}
	if state.Status != StatusPending {
		m.mu.RUnlock()
		return fmt.Errorf("provider %q has no pending key (status: %s)", providerID, state.Status)
	}
	pendingKey := state.PendingKey
	m.mu.RUnlock()

	return validateFn(ctx, pendingKey)
}

// Promote promotes the pending key to primary.
// The old primary becomes the draining key for the configured drain period.
func (m *Manager) Promote(providerID string) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	state, ok := m.states[providerID]
	if !ok {
		return fmt.Errorf("provider %q not registered", providerID)
	}

	if state.Status != StatusPending {
		return fmt.Errorf("provider %q has no pending key (status: %s)", providerID, state.Status)
	}

	oldKey := state.Primary
	state.Primary = state.PendingKey
	state.PendingKey = ""
	state.OldKey = oldKey
	state.Status = StatusDraining
	drainUntil := time.Now().Add(m.drainPeriod)
	state.DrainUntil = &drainUntil
	state.UpdatedAt = time.Now()

	// Notify provider to use the new key.
	if m.onRotate != nil {
		m.onRotate(providerID, state.Primary)
	}

	// Schedule drain completion.
	go func() {
		time.Sleep(m.drainPeriod)
		m.completeDrain(providerID)
	}()

	return nil
}

// Rollback cancels the pending rotation and keeps the current primary key.
func (m *Manager) Rollback(providerID string) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	state, ok := m.states[providerID]
	if !ok {
		return fmt.Errorf("provider %q not registered", providerID)
	}

	if state.Status == StatusIdle {
		return fmt.Errorf("provider %q has no active rotation", providerID)
	}

	state.PendingKey = ""
	state.OldKey = ""
	state.DrainUntil = nil
	state.Status = StatusIdle
	state.UpdatedAt = time.Now()
	return nil
}

// GetStatus returns the rotation state for a provider.
func (m *Manager) GetStatus(providerID string) (*KeyState, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	state, ok := m.states[providerID]
	if !ok {
		return nil, fmt.Errorf("provider %q not registered", providerID)
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

func (m *Manager) completeDrain(providerID string) {
	m.mu.Lock()
	defer m.mu.Unlock()

	state, ok := m.states[providerID]
	if !ok || state.Status != StatusDraining {
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
