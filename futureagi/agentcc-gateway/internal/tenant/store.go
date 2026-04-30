package tenant

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"sync"
	"time"
)

// Store is a thread-safe in-memory store for per-org gateway configurations.
// It maps organization IDs to their merged OrgConfig. All methods are safe
// for concurrent use.
type Store struct {
	mu         sync.RWMutex
	configs    map[string]*OrgConfig
	configHash map[string][32]byte // SHA-256 of serialized config per org
	onChange   func(orgID string)  // optional callback fired when an org config changes
}

// NewStore creates a new empty tenant config store.
func NewStore() *Store {
	return &Store{
		configs:    make(map[string]*OrgConfig),
		configHash: make(map[string][32]byte),
	}
}

// SetOnChange registers a callback that is invoked (outside the lock) for
// each org whose config was added, updated, or removed by Set, MergeBulk,
// or Delete. This is used to evict downstream caches (e.g., OrgProviderCache).
func (s *Store) SetOnChange(fn func(orgID string)) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.onChange = fn
}

// Get returns the org config for the given org ID, or nil if not found.
func (s *Store) Get(orgID string) *OrgConfig {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.configs[orgID]
}

// Set upserts the org config for the given org ID.
// Updates the config hash so MergeBulk detects no false change on next sync.
func (s *Store) Set(orgID string, cfg *OrgConfig) {
	s.mu.Lock()
	s.configs[orgID] = cfg
	s.configHash[orgID] = hashConfig(cfg)
	s.mu.Unlock()
}

// Delete removes the org config for the given org ID.
func (s *Store) Delete(orgID string) {
	s.mu.Lock()
	delete(s.configs, orgID)
	delete(s.configHash, orgID)
	s.mu.Unlock()
}

// GetAll returns a copy of all org configs.
func (s *Store) GetAll() map[string]*OrgConfig {
	s.mu.RLock()
	defer s.mu.RUnlock()
	result := make(map[string]*OrgConfig, len(s.configs))
	for k, v := range s.configs {
		result[k] = v
	}
	return result
}

// Count returns the number of org configs in the store.
func (s *Store) Count() int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return len(s.configs)
}

// LoadBulk replaces all configs in the store with the provided map.
// Used for initial sync from the control plane on startup.
func (s *Store) LoadBulk(configs map[string]*OrgConfig) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.configs = make(map[string]*OrgConfig, len(configs))
	for k, v := range configs {
		s.configs[k] = v
	}
}

// MergeBulk merges successfully parsed org configs into the store.
//   - Orgs in parsedConfigs are added or updated.
//   - Orgs present in the control plane response (allOrgIDs) but NOT in
//     parsedConfigs failed to parse — their existing store entries are preserved.
//   - Orgs in the store that are NOT in allOrgIDs were removed from the control
//     plane — they are deleted from the store.
//
// Fires the onChange callback for each org that was added, updated, or removed.
func (s *Store) MergeBulk(parsedConfigs map[string]*OrgConfig, allOrgIDs map[string]struct{}) {
	s.mu.Lock()

	// Track orgs that actually changed so we can fire onChange outside the lock.
	var changed []string

	// Remove orgs no longer present in the control plane response.
	for orgID := range s.configs {
		if _, inResponse := allOrgIDs[orgID]; !inResponse {
			delete(s.configs, orgID)
			delete(s.configHash, orgID)
			changed = append(changed, orgID)
		}
	}

	// Upsert configs. Only mark as changed if the config content differs
	// (compared via SHA-256 of JSON serialization).
	for orgID, cfg := range parsedConfigs {
		newHash := hashConfig(cfg)
		if oldHash, exists := s.configHash[orgID]; !exists || oldHash != newHash {
			changed = append(changed, orgID)
		}
		s.configs[orgID] = cfg
		s.configHash[orgID] = newHash
	}

	cb := s.onChange
	s.mu.Unlock()

	// Fire onChange outside the lock to avoid deadlocks with downstream caches.
	if cb != nil {
		for _, orgID := range changed {
			cb(orgID)
		}
	}
}

// hashConfig returns a SHA-256 hash of the JSON-serialized config.
// Used by MergeBulk to detect actual changes and avoid unnecessary cache evictions.
// On marshal failure, returns a unique hash based on the pointer address to
// ensure the config is treated as changed (safe fallback).
func hashConfig(cfg *OrgConfig) [32]byte {
	data, err := json.Marshal(cfg)
	if err != nil {
		// Marshal failed — return a unique hash so this config is always
		// treated as "changed" and the onChange callback fires.
		return sha256.Sum256([]byte(fmt.Sprintf("unmarshalable-%p-%d", cfg, time.Now().UnixNano())))
	}
	return sha256.Sum256(data)
}

// GetProviderConfig returns the provider config for a specific provider
// from an org's config. Returns nil if the org has no config or the
// provider is not configured.
func (s *Store) GetProviderConfig(orgID, providerName string) *ProviderConfig {
	s.mu.RLock()
	defer s.mu.RUnlock()
	cfg := s.configs[orgID]
	if cfg == nil || cfg.Providers == nil {
		return nil
	}
	return cfg.Providers[providerName]
}
