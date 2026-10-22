package auth

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"log/slog"
	"math"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
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

	// Status is checked under the lock: Revoke writes it under the same lock.
	ks.mu.RLock()
	key, ok := ks.byHash[hash]
	active := ok && key.IsActive()
	ks.mu.RUnlock()

	if !active {
		return nil
	}
	// Best-effort last-used timestamp — atomic pointer swap, no write lock needed.
	now := time.Now()
	key.lastUsedAt.Store(&now)
	return key
}

// Identity is what a request may rely on about the key it authenticated with:
// a copy taken under the store lock, so a later Update cannot change it or race
// a reader of it.
type Identity struct {
	KeyID string
	OrgID string // the key's metadata org_id; "" for a key with no org
}

// Identity returns key's identity, read under the store lock.
func (ks *KeyStore) Identity(key *APIKey) Identity {
	ks.mu.RLock()
	defer ks.mu.RUnlock()
	return Identity{KeyID: key.ID, OrgID: key.Metadata["org_id"]}
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

	id := newKeyID()
	for ks.byID[id] != nil {
		id = newKeyID()
	}

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

// ErrKeyNotFound is returned by Update for an unknown key ID.
var ErrKeyNotFound = errors.New("key not found")

// reservedMetadataKeys are the key metadata names only the platform sets: the
// authority entries (authorityMetadataKeys) and the key's own fields (type,
// key_type, id, name and owner). The auth plugin never copies a reserved name
// as a label; it writes the authority entries from the key itself.
//
// The platform sets the authority entries when a key is created and they never
// change afterwards. Django rejects the same list
// (RESERVED_KEY_METADATA in futureagi/agentcc/validators.py); a Django test
// parses this map literal, so keep its shape.
var reservedMetadataKeys = map[string]struct{}{
	"org_id":                {},
	"access_groups":         {},
	"type":                  {},
	"key_type":              {},
	"role":                  {},
	"allow_policy_override": {},
	"team":                  {},
	"id":                    {},
	"name":                  {},
	"owner":                 {},
}

// authorityMetadataKeys are the reserved entries the gateway reads as
// authority. The auth plugin writes each to the request as key_<name> from the
// key's own metadata, after the labels, and drops every other reserved name.
var authorityMetadataKeys = []string{"org_id", "access_groups", "role", "team", "allow_policy_override"}

// IsReservedMetadataKey reports whether name is a reserved key metadata name:
// one a tenant may not set, and that the auth plugin never copies as a label.
func IsReservedMetadataKey(name string) bool {
	_, reserved := reservedMetadataKeys[name]
	return reserved
}

// ReservedMetadataKeys returns every reserved key metadata name, sorted.
func ReservedMetadataKeys() []string {
	names := make([]string, 0, len(reservedMetadataKeys))
	for name := range reservedMetadataKeys {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

// AuthorityMetadataKeys returns the reserved metadata entries the gateway reads
// as authority, in a new slice.
func AuthorityMetadataKeys() []string {
	return append([]string(nil), authorityMetadataKeys...)
}

// ReservedMetadataError is returned by Update for a metadata change to a
// reserved entry. Keys lists them in order.
type ReservedMetadataError struct {
	Keys []string
}

func (e *ReservedMetadataError) Error() string {
	return fmt.Sprintf("metadata %s cannot be changed on an existing key: it is set when the key is created",
		strings.Join(e.Keys, ", "))
}

// reservedChanges returns the reserved entries metadata would change on key,
// in order. Setting one to the value it already has is not a change.
func reservedChanges(key *APIKey, metadata map[string]string) []string {
	var changed []string
	for k, v := range metadata {
		if !IsReservedMetadataKey(k) {
			continue
		}
		if current, set := key.Metadata[k]; !set || current != v {
			changed = append(changed, k)
		}
	}
	sort.Strings(changed)
	return changed
}

// Update modifies a key. Metadata is merged into a copy of the key's own, which
// then replaces it; a change to a reserved entry fails with a
// *ReservedMetadataError and changes nothing, so an update can never move a key
// to another org.
func (ks *KeyStore) Update(id string, name, owner *string, models, providers []string, metadata map[string]string) (*APIKey, error) {
	ks.mu.Lock()
	defer ks.mu.Unlock()

	key, ok := ks.byID[id]
	if !ok {
		return nil, ErrKeyNotFound
	}
	if changed := reservedChanges(key, metadata); len(changed) > 0 {
		return nil, &ReservedMetadataError{Keys: changed}
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
		// Copy on write: in-flight requests range over key.Metadata without
		// ks.mu (the auth plugin copies it into the request), so Update builds a
		// new map and swaps it in. Readers keep the map they loaded; it never
		// changes.
		merged := make(map[string]string, len(key.Metadata)+len(metadata))
		for k, v := range key.Metadata {
			merged[k] = v
		}
		for k, v := range metadata {
			merged[k] = v
		}
		key.Metadata = merged
	}
	key.UpdatedAt = time.Now()

	return key, nil
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

// SyncedKey is a key the Django control plane holds, as the store loads it.
// It carries the pre-computed SHA-256 hash (the raw key is never sent over the
// wire). SyncedKeyFromContract builds it from the wire shape.
type SyncedKey struct {
	ID        string
	Name      string
	Owner     string
	KeyHash   string
	KeyPrefix string
	Models    []string
	Providers []string
	Metadata  map[string]string
	ExpiresAt *time.Time
}

// SyncedKeyFromContract checks and converts a key as the control plane sends
// it, both when the gateway pulls the key set and when Django pushes keys to
// POST /-/keys/sync.
func SyncedKeyFromContract(k *gatewayadmin.SyncedKey) (SyncedKey, error) {
	if k == nil || k.ID == "" || !isSHA256Hex(k.KeyHash) {
		return SyncedKey{}, errors.New("id and a hex SHA-256 key_hash are required")
	}
	key := SyncedKey{
		ID:        k.ID,
		KeyHash:   k.KeyHash,
		Models:    k.Models,
		Providers: k.Providers,
		Metadata:  k.Metadata,
	}
	if k.Name != nil {
		key.Name = *k.Name
	}
	if k.Owner != nil {
		key.Owner = *k.Owner
	}
	if k.KeyPrefix != nil {
		key.KeyPrefix = *k.KeyPrefix
	}
	if k.ExpiresAt != nil {
		t, err := time.Parse(time.RFC3339, *k.ExpiresAt)
		if err != nil {
			return SyncedKey{}, fmt.Errorf("expires_at: %w", err)
		}
		key.ExpiresAt = &t
	}
	return key, nil
}

func isSHA256Hex(s string) bool {
	if len(s) != 64 {
		return false
	}
	_, err := hex.DecodeString(s)
	return err == nil
}

// syncedKeyToAPIKey is the single construction site for both sync paths, so a
// field can't be wired into one and missed in the other.
func syncedKeyToAPIKey(sk SyncedKey, id string) *APIKey {
	return &APIKey{
		ID:               id,
		KeyHash:          sk.KeyHash,
		KeyPrefix:        sk.KeyPrefix,
		Name:             sk.Name,
		Owner:            sk.Owner,
		Status:           "active",
		KeyType:          "byok",
		Source:           "sync",
		AllowedModels:    sk.Models,
		AllowedProviders: sk.Providers,
		Metadata:         sk.Metadata,
		ExpiresAt:        sk.ExpiresAt,
		CreatedAt:        time.Now(),
		UpdatedAt:        time.Now(),
	}
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

		id := sk.ID
		if id == "" {
			id = newKeyID()
		}
		if idTaken(ks.byID, id, sk) {
			continue
		}

		key := syncedKeyToAPIKey(sk, id)

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
// A key this gateway revoked stays revoked when its hash comes back: the
// control plane never re-activates a key, so a set that still lists it was
// read before the revoke reached it (Django revokes a key here before it
// drops the key from the set). Returns the number of keys loaded from the
// incoming set.
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

	// Remove every synced key (not from config), remembering which
	// of them were revoked.
	revoked := make(map[string]bool)
	for hash, key := range ks.byHash {
		if key.Source == "sync" {
			if key.Status == "revoked" {
				revoked[hash] = true
			}
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

		id := sk.ID
		if id == "" {
			id = newKeyID()
		}
		if idTaken(ks.byID, id, sk) {
			continue
		}

		key := syncedKeyToAPIKey(sk, id)
		if revoked[sk.KeyHash] {
			key.Status = "revoked"
		}

		ks.byHash[sk.KeyHash] = key
		ks.byID[id] = key
		loaded++
	}

	return loaded
}

// idTaken reports whether id already belongs to a different key, and logs it.
// Such a synced key is skipped rather than loaded over the other one: the
// displaced key would stay usable by hash but drop out of byID, and revoking
// the synced ID would then revoke the wrong key.
func idTaken(byID map[string]*APIKey, id string, sk SyncedKey) bool {
	existing, ok := byID[id]
	if !ok || existing.KeyHash == sk.KeyHash {
		return false
	}
	slog.Warn("key sync: skipping key whose id belongs to another key",
		"id", id, "name", sk.Name, "existing_name", existing.Name, "existing_source", existing.Source)
	return true
}

// --- Helpers ---

// newKeyID returns the ID for a key the gateway mints. It is random, not
// counted: the control plane stores keys by ID and outlives this process, so a
// counter that restarts at key_1 would reissue IDs it already holds.
func newKeyID() string {
	b := make([]byte, 12)
	if _, err := rand.Read(b); err != nil {
		panic(fmt.Sprintf("crypto/rand.Read failed: %v", err))
	}
	return "key_" + hex.EncodeToString(b)
}

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
