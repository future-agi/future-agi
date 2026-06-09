package auth

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"log/slog"
	"math"
	"sync"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// USDToMicros converts a USD float to microdollars (millionths of a dollar).
func USDToMicros(usd float64) int64 {
	return int64(math.Round(usd * 1_000_000))
}

// MicrosToUSD converts microdollars to USD float.
func MicrosToUSD(micros int64) float64 {
	return float64(micros) / 1_000_000
}

// APIKey represents a virtual API key.
type APIKey struct {
	ID               string            `json:"id"`
	KeyHash          string            `json:"-"`
	KeyPrefix        string            `json:"key_prefix"`
	Name             string            `json:"name"`
	Owner            string            `json:"owner"`
	Status           string            `json:"status"`   // active, revoked, expired
	KeyType          string            `json:"key_type"` // "byok" or "managed"
	Source           string            `json:"source"`   // "config" (from config.yaml) or "sync" (from control plane)
	AllowedModels    []string          `json:"allowed_models,omitempty"`
	AllowedProviders []string          `json:"allowed_providers,omitempty"`
	AllowedIPs       []string          `json:"allowed_ips,omitempty"`
	AllowedTools     []string          `json:"allowed_tools,omitempty"`
	DeniedTools      []string          `json:"denied_tools,omitempty"`
	RateLimitRPM     int               `json:"rate_limit_rpm,omitempty"`
	RateLimitTPM     int               `json:"rate_limit_tpm,omitempty"`
	Metadata         map[string]string `json:"metadata,omitempty"`
	CreatedAt        time.Time         `json:"created_at"`
	UpdatedAt        time.Time         `json:"updated_at"`
	ExpiresAt        *time.Time        `json:"expires_at,omitempty"`

	// lastUsedAt is updated atomically on every Authenticate call.
	lastUsedAt atomic.Pointer[time.Time] `json:"-"`

	// creditBalance stores microdollars (1 USD = 1,000,000) for managed keys.
	creditBalance atomic.Int64 `json:"-"`
}

// BalanceUSD returns the current credit balance in USD.
func (k *APIKey) BalanceUSD() float64 {
	return MicrosToUSD(k.creditBalance.Load())
}

// DeductMicros atomically deducts microdollars and returns the new balance in USD.
func (k *APIKey) DeductMicros(amount int64) float64 {
	newVal := k.creditBalance.Add(-amount)
	return MicrosToUSD(newVal)
}

// AddMicros atomically adds microdollars and returns the new balance in USD.
func (k *APIKey) AddMicros(amount int64) float64 {
	newVal := k.creditBalance.Add(amount)
	return MicrosToUSD(newVal)
}

// SetBalanceMicros sets the credit balance to an exact value.
// Used to sync local state to an authoritative source (e.g. Redis).
func (k *APIKey) SetBalanceMicros(micros int64) {
	k.creditBalance.Store(micros)
}

// BalanceMicros returns the current credit balance in microdollars.
func (k *APIKey) BalanceMicros() int64 {
	return k.creditBalance.Load()
}

// IsManaged returns true if this is a managed key (credits-based).
func (k *APIKey) IsManaged() bool {
	return k.KeyType == "managed"
}

// CanAccessModel checks if this key is allowed to use the given model.
func (k *APIKey) CanAccessModel(model string) bool {
	if len(k.AllowedModels) == 0 {
		return true
	}
	for _, m := range k.AllowedModels {
		if m == model {
			return true
		}
	}
	return false
}

// CanAccessProvider checks if this key is allowed to use the given provider.
func (k *APIKey) CanAccessProvider(provider string) bool {
	if len(k.AllowedProviders) == 0 {
		return true
	}
	for _, p := range k.AllowedProviders {
		if p == provider {
			return true
		}
	}
	return false
}

// IsExpired checks if the key has expired.
func (k *APIKey) IsExpired() bool {
	if k.ExpiresAt == nil {
		return false
	}
	return time.Now().After(*k.ExpiresAt)
}

// IsActive checks if the key is in a usable state.
func (k *APIKey) IsActive() bool {
	return k.Status == "active" && !k.IsExpired()
}

// GetLastUsedAt returns the last-used timestamp (may be nil if never used).
func (k *APIKey) GetLastUsedAt() *time.Time {
	return k.lastUsedAt.Load()
}

// KeyStore manages API keys.
type KeyStore struct {
	mu      sync.RWMutex
	byHash  map[string]*APIKey // SHA-256 hash → key
	byID    map[string]*APIKey // key ID → key
	counter int
}

// NewKeyStore creates a KeyStore from config.
func NewKeyStore(cfg config.AuthConfig) *KeyStore {
	ks := &KeyStore{
		byHash: make(map[string]*APIKey),
		byID:   make(map[string]*APIKey),
	}

	for _, keyCfg := range cfg.Keys {
		if keyCfg.Key == "" {
			continue
		}

		hash := HashKey(keyCfg.Key)
		prefix := keyPrefix(keyCfg.Key)

		ks.counter++
		id := fmt.Sprintf("key_%d", ks.counter)

		keyType := keyCfg.KeyType
		if keyType == "" {
			keyType = "byok"
		}

		key := &APIKey{
			ID:               id,
			KeyHash:          hash,
			KeyPrefix:        prefix,
			Name:             keyCfg.Name,
			Owner:            keyCfg.Owner,
			Status:           "active",
			KeyType:          keyType,
			Source:           "config",
			AllowedModels:    keyCfg.Models,
			AllowedProviders: keyCfg.Providers,
			AllowedIPs:       keyCfg.AllowedIPs,
			AllowedTools:     keyCfg.AllowedTools,
			DeniedTools:      keyCfg.DeniedTools,
			RateLimitRPM:     keyCfg.RateLimitRPM,
			RateLimitTPM:     keyCfg.RateLimitTPM,
			Metadata:         keyCfg.Metadata,
			CreatedAt:        time.Now(),
			UpdatedAt:        time.Now(),
		}
		if keyType == "managed" && keyCfg.CreditBalance > 0 {
			key.creditBalance.Store(USDToMicros(keyCfg.CreditBalance))
		}

		if keyCfg.ExpiresAt != "" {
			if t, err := time.Parse(time.RFC3339, keyCfg.ExpiresAt); err == nil {
				key.ExpiresAt = &t
			}
		}

		ks.byHash[hash] = key
		ks.byID[id] = key

		slog.Info("api key loaded", "name", key.Name, "prefix", key.KeyPrefix, "id", id)
	}

	return ks
}

// Authenticate looks up a key by its raw value (hashes it first).
// Returns the APIKey if found, nil otherwise.
// Uses direct map lookup on the SHA-256 hash — safe because SHA-256 is a
// one-way function and timing on hash comparisons doesn't leak the raw key.
func (ks *KeyStore) Authenticate(rawKey string) *APIKey {
	hash := HashKey(rawKey)

	ks.mu.RLock()
	key, ok := ks.byHash[hash]
	ks.mu.RUnlock()

	if !ok {
		return nil
	}
	// Best-effort last-used timestamp — atomic pointer swap, no write lock needed.
	now := time.Now()
	key.lastUsedAt.Store(&now)
	return key
}

// Lookup finds a key by its raw value without updating last-used metadata.
// Use this for read-only peeks (e.g., extracting org_id before full auth).
func (ks *KeyStore) Lookup(rawKey string) *APIKey {
	hash := HashKey(rawKey)

	ks.mu.RLock()
	key := ks.byHash[hash]
	ks.mu.RUnlock()

	return key
}

// Get returns a key by ID.
func (ks *KeyStore) Get(id string) *APIKey {
	ks.mu.RLock()
	defer ks.mu.RUnlock()
	return ks.byID[id]
}

// List returns all keys.
func (ks *KeyStore) List() []*APIKey {
	ks.mu.RLock()
	defer ks.mu.RUnlock()

	keys := make([]*APIKey, 0, len(ks.byID))
	for _, k := range ks.byID {
		keys = append(keys, k)
	}
	return keys
}

// Create adds a new key. Returns the APIKey and the raw key value.
func (ks *KeyStore) Create(name, owner string, models, providers []string, metadata map[string]string) (*APIKey, string) {
	rawKey := generateKey()
	hash := HashKey(rawKey)
	prefix := keyPrefix(rawKey)

	ks.mu.Lock()
	defer ks.mu.Unlock()

	ks.counter++
	id := fmt.Sprintf("key_%d", ks.counter)

	key := &APIKey{
		ID:               id,
		KeyHash:          hash,
		KeyPrefix:        prefix,
		Name:             name,
		Owner:            owner,
		Status:           "active",
		Source:           "sync", // mark as sync-sourced so periodic SyncFromHashes can manage lifecycle
		AllowedModels:    models,
		AllowedProviders: providers,
		Metadata:         metadata,
		CreatedAt:        time.Now(),
		UpdatedAt:        time.Now(),
	}

	ks.byHash[hash] = key
	ks.byID[id] = key

	return key, rawKey
}

// Revoke marks a key as revoked.
func (ks *KeyStore) Revoke(id string) bool {
	ks.mu.Lock()
	defer ks.mu.Unlock()

	key, ok := ks.byID[id]
	if !ok {
		return false
	}

	key.Status = "revoked"
	key.UpdatedAt = time.Now()
	return true
}

// Update modifies key metadata.
func (ks *KeyStore) Update(id string, name, owner *string, models, providers []string, metadata map[string]string) (*APIKey, bool) {
	ks.mu.Lock()
	defer ks.mu.Unlock()

	key, ok := ks.byID[id]
	if !ok {
		return nil, false
	}

	if name != nil {
		key.Name = *name
	}
	if owner != nil {
		key.Owner = *owner
	}
	if models != nil {
		key.AllowedModels = models
	}
	if providers != nil {
		key.AllowedProviders = providers
	}
	if metadata != nil {
		if key.Metadata == nil {
			key.Metadata = make(map[string]string)
		}
		for k, v := range metadata {
			key.Metadata[k] = v
		}
	}
	key.UpdatedAt = time.Now()

	return key, true
}

// AddCredits adds USD credits to a managed key. Returns the new balance and true, or 0 and false if not found.
func (ks *KeyStore) AddCredits(id string, amountUSD float64) (float64, bool) {
	ks.mu.RLock()
	key, ok := ks.byID[id]
	ks.mu.RUnlock()
	if !ok {
		return 0, false
	}
	if key.KeyType != "managed" {
		return 0, false
	}
	newBalance := key.AddMicros(USDToMicros(amountUSD))
	return newBalance, true
}

// Count returns the number of keys.
func (ks *KeyStore) Count() int {
	ks.mu.RLock()
	defer ks.mu.RUnlock()
	return len(ks.byID)
}

// SyncedKey represents a key received from the Django control plane during
// startup sync. It contains the pre-computed SHA-256 hash (the raw key is
// never sent over the wire).
type SyncedKey struct {
	ID        string            `json:"id"`
	Name      string            `json:"name"`
	Owner     string            `json:"owner"`
	KeyHash   string            `json:"key_hash"`
	Models    []string          `json:"models"`
	Providers []string          `json:"providers"`
	Metadata  map[string]string `json:"metadata"`
}

// LoadFromHashes merges control-plane keys (identified by pre-computed hashes)
// into the store. Keys whose hash already exists are skipped (config.yaml wins).
// Returns the number of new keys added.
func (ks *KeyStore) LoadFromHashes(keys []SyncedKey) int {
	ks.mu.Lock()
	defer ks.mu.Unlock()

	added := 0
	for _, sk := range keys {
		if sk.KeyHash == "" {
			continue
		}
		// Skip if hash already in store (config.yaml seed keys take precedence).
		if _, exists := ks.byHash[sk.KeyHash]; exists {
			continue
		}

		ks.counter++
		id := sk.ID
		if id == "" {
			id = fmt.Sprintf("key_%d", ks.counter)
		}

		key := &APIKey{
			ID:               id,
			KeyHash:          sk.KeyHash,
			Name:             sk.Name,
			Owner:            sk.Owner,
			Status:           "active",
			KeyType:          "byok",
			Source:           "sync",
			AllowedModels:    sk.Models,
			AllowedProviders: sk.Providers,
			Metadata:         sk.Metadata,
			CreatedAt:        time.Now(),
			UpdatedAt:        time.Now(),
		}

		ks.byHash[sk.KeyHash] = key
		ks.byID[id] = key
		added++
	}

	return added
}

// SyncFromHashes replaces all control-plane-sourced keys with the given set.
// Keys loaded from config.yaml (Source="config") are preserved. Keys from a
// previous sync that are no longer present in the incoming set are removed,
// ensuring revoked keys in Django don't persist in the Go gateway forever.
// Returns the number of keys loaded from the incoming set.
func (ks *KeyStore) SyncFromHashes(keys []SyncedKey) int {
	// Guard: refuse to wipe all keys when the control plane returns an
	// empty set. This protects against transient errors (DB timeout,
	// partial response) that would otherwise cause a full blackout.
	if len(keys) == 0 {
		ks.mu.RLock()
		hasSynced := false
		for _, key := range ks.byHash {
			if key.Source == "sync" {
				hasSynced = true
				break
			}
		}
		ks.mu.RUnlock()
		if hasSynced {
			slog.Warn("key sync: received empty set but store has synced keys, skipping destructive sync")
			return 0
		}
	}

	ks.mu.Lock()
	defer ks.mu.Unlock()

	// Remove all previously synced keys (not from config).
	for hash, key := range ks.byHash {
		if key.Source == "sync" {
			delete(ks.byHash, hash)
			delete(ks.byID, key.ID)
		}
	}

	// Load the new set from the control plane.
	loaded := 0
	for _, sk := range keys {
		if sk.KeyHash == "" {
			continue
		}
		// Skip if hash belongs to a config key (config.yaml wins).
		if existing, exists := ks.byHash[sk.KeyHash]; exists && existing.Source == "config" {
			continue
		}

		ks.counter++
		id := sk.ID
		if id == "" {
			id = fmt.Sprintf("key_%d", ks.counter)
		}

		key := &APIKey{
			ID:               id,
			KeyHash:          sk.KeyHash,
			Name:             sk.Name,
			Owner:            sk.Owner,
			Status:           "active",
			KeyType:          "byok",
			Source:           "sync",
			AllowedModels:    sk.Models,
			AllowedProviders: sk.Providers,
			Metadata:         sk.Metadata,
			CreatedAt:        time.Now(),
			UpdatedAt:        time.Now(),
		}

		ks.byHash[sk.KeyHash] = key
		ks.byID[id] = key
		loaded++
	}

	return loaded
}

// --- Helpers ---

// HashKey returns the SHA-256 hex hash of a key.
func HashKey(key string) string {
	h := sha256.Sum256([]byte(key))
	return hex.EncodeToString(h[:])
}

func keyPrefix(key string) string {
	if len(key) <= 12 {
		return key[:len(key)/2] + "..."
	}
	return key[:12] + "..."
}

func generateKey() string {
	b := make([]byte, 24)
	if _, err := rand.Read(b); err != nil {
		panic(fmt.Sprintf("crypto/rand.Read failed: %v", err))
	}
	return "sk-agentcc-" + hex.EncodeToString(b)
}
