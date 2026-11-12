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
	Source           string            `json:"source"`   // "config", "sync" (control plane), or "delegated" (in-process only)
	AllowedModels    []string          `json:"allowed_models,omitempty"`
	AllowedProviders []string          `json:"allowed_providers,omitempty"`
	AllowedIPs       []string          `json:"allowed_ips,omitempty"`
	AllowedTools     []string          `json:"allowed_tools,omitempty"`
	DeniedTools      []string          `json:"denied_tools,omitempty"`
	MCPAllowedTools  []string          `json:"mcp_allowed_tools,omitempty"` // MCP only (bug 23): read through MCPToolLists
	MCPDeniedTools   []string          `json:"mcp_denied_tools,omitempty"`  // MCP only (bug 23): read through MCPToolLists
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

	// mintedAt is when Create or an additive import first installed this key,
	// until a key sync lists it or it is revoked; zero for other keys.
	// CreatedAt cannot serve: sync rebuilds keys and stamps its own time.
	// Guarded by KeyStore.mu.
	mintedAt time.Time
}

// MCPToolLists returns the allow and deny lists the MCP path applies: the
// key's MCP lists, or, only when both are absent, allowed_tools and
// denied_tools as before, so an older key keeps its MCP filtering
// (NewKeyStore logs that deprecated fallback once per key). An empty
// mcp_allowed_tools list counts as set, which ends the fallback.
func (k *APIKey) MCPToolLists() (allowed, denied []string) {
	if k.mcpListsFallBack() {
		return k.AllowedTools, k.DeniedTools
	}
	return k.MCPAllowedTools, k.MCPDeniedTools
}

func (k *APIKey) mcpListsFallBack() bool {
	return k.MCPAllowedTools == nil && k.MCPDeniedTools == nil && (len(k.AllowedTools) > 0 || len(k.DeniedTools) > 0)
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
	mu         sync.RWMutex
	byHash     map[string]*APIKey // SHA-256 hash → key
	byID       map[string]*APIKey // key ID → key
	counter    int
	delegation delegatedEligibility
	revocation *keyRevocationCheck // guarded by mu; nil only in single-instance mode

	// syncGrace is how long a key Create made survives key syncs whose list
	// lacks it (see SetSyncGrace). Guarded by mu.
	syncGrace time.Duration
	// lastListFetchedAt is when the key list last applied by a sync was
	// fetched; a list fetched before it is older and is ignored. Guarded by mu.
	lastListFetchedAt time.Time
	// now is the clock behind Create and the key sync's grace window; tests
	// replace it before using the store.
	now func() time.Time
	// listLoaded is set once a key list from the control plane has been
	// applied (syncFromList), and never cleared. See KeyListLoaded.
	listLoaded atomic.Bool
	// appHasNoKeyTable is whether the control plane's latest answer to a key
	// sync was 503 migrations_pending. See AppHasNoKeyTable.
	appHasNoKeyTable atomic.Bool

	// Immutable mode; the remaining fields are guarded by mu.
	controlPlane  bool
	configSeeds   map[string]string // original hash -> id, even after refusal
	blockedHashes map[string]bool   // negative knowledge survives empty lists
	configRefused map[string]int
	// latched733 is the 733 latch: set once a 733 answer (one
	// carrying the snapshot, KeySyncState.personKeysOnly) has been applied,
	// and never cleared, even when a later answer lacks the snapshot. From
	// then on, in control-plane mode, a synced key needs user_id on every
	// list and every additive push, every list drops each held synced key
	// without one (dropUnownedSyncedLocked), however non-authoritative it is,
	// and only an answer carrying config_conflicts settles the seeds
	// (conflictsAnsweredLocked). Before it, a pre-733 Django's list and push
	// load its org keys, as before 733. A push before any list needs no
	// owner either: that admits no more than a push already could (the admin
	// token that authorizes one can name any user_id, and a 733 Django never
	// pushes an unowned key), and the first list, if it is a 733 one, drops
	// what it pushed without an owner.
	latched733 bool
	// pre733 is whether, in control-plane mode, a pre-733 answer (no
	// snapshot) was applied before any 733 answer: the control plane is
	// pre-733 until the latch (ControlPlanePre733). Logged once.
	pre733 bool
	// latchHeldLogged is whether an answer without the snapshot after the
	// latch has been logged; once is enough.
	latchHeldLogged bool
}

// NewKeyStore creates a KeyStore from config.
func NewKeyStore(cfg config.AuthConfig, controlPlane ...bool) *KeyStore {
	ks := &KeyStore{
		byHash:        make(map[string]*APIKey),
		byID:          make(map[string]*APIKey),
		syncGrace:     config.DefaultKeySyncGrace,
		now:           time.Now,
		controlPlane:  len(controlPlane) > 0 && controlPlane[0],
		configSeeds:   make(map[string]string),
		blockedHashes: make(map[string]bool),
		configRefused: make(map[string]int),
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

		if ks.controlPlane {
			_, hasOrg := keyCfg.Metadata["org_id"]
			_, hasUser := keyCfg.Metadata["user_id"]
			if keyType != "internal" || hasOrg || hasUser {
				ks.configRefused["not_deployment_identity"]++
				slog.Error("config key refused", "id", id, "reason", "not_deployment_identity")
				continue
			}
			if len(ks.configSeeds) >= 32 {
				ks.configRefused["seed_limit"]++
				slog.Error("config key refused", "id", id, "reason", "seed_limit")
				continue
			}
			ks.configSeeds[hash] = id
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
			MCPAllowedTools:  keyCfg.MCPAllowedTools,
			MCPDeniedTools:   keyCfg.MCPDeniedTools,
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
		if key.mcpListsFallBack() {
			slog.Warn("api key: allowed_tools and denied_tools also filter its MCP tools; set mcp_allowed_tools or mcp_denied_tools instead (deprecated fallback)",
				"name", key.Name, "id", id)
		}
	}

	return ks
}

// Authenticate looks up a key by its raw value (hashes it first).
// Returns the APIKey if found, nil otherwise.
// Uses direct map lookup on the SHA-256 hash — safe because SHA-256 is a
// one-way function and timing on hash comparisons doesn't leak the raw key.
func (ks *KeyStore) Authenticate(rawKey string) *APIKey {
	key, _ := ks.activeWithRevocation(HashKey(rawKey), true)
	return key
}

// Identity is what a request may rely on about the key it authenticated with:
// a copy taken under the store lock, so a later Update cannot change it or race
// a reader of it.
type Identity struct {
	KeyID  string
	OrgID  string // the key's metadata org_id; "" for a key with no org
	UserID string // canonical person id from trusted issuance only; never an Owner label
}

// Identity returns key's identity, read under the store lock. It does not
// authenticate the key: callers must first apply status, expiry and revocation.
// Admin-token-authenticated Create and hash sync both count as control-plane
// issuance in standalone as well as control-plane mode: that transport may set
// reserved user_id. Config seeds never confer person identity in either mode.
func (ks *KeyStore) Identity(key *APIKey) Identity {
	ks.mu.RLock()
	defer ks.mu.RUnlock()
	id := Identity{KeyID: key.ID, OrgID: key.Metadata["org_id"]}
	// Only the control-plane transport and verified in-process delegation
	// write authority metadata. Config seeds (including standalone ones) and
	// keys of unknown provenance never identify a person. The same canonical
	// UUID syntax as fe1 is exactly str(Django User.pk); do not normalize input.
	if key.Source == "sync" || key.Source == "delegated" {
		if user := key.Metadata["user_id"]; uuidRE.MatchString(user) {
			id.UserID = user
		}
	}
	return id
}

// KeyPeek is what the early key peek reads about a key (the server's
// peekKeyOrgID), copied under the store lock, so an Update in flight never
// races it. Update replaces the metadata map and the allowed lists whole and
// never writes into them, so the copies keep the values they were read with.
type KeyPeek struct {
	KeyType          string
	AllowedModels    []string
	AllowedProviders []string
	Metadata         map[string]string
}

// Peek returns key's KeyPeek, read under the store lock.
func (ks *KeyStore) Peek(key *APIKey) KeyPeek {
	ks.mu.RLock()
	defer ks.mu.RUnlock()
	return KeyPeek{
		KeyType:          key.KeyType,
		AllowedModels:    key.AllowedModels,
		AllowedProviders: key.AllowedProviders,
		Metadata:         key.Metadata,
	}
}

// MetadataOf returns key's metadata map, read under the store lock. Update
// swaps in a new map rather than writing into this one, so the caller may
// read it without the lock afterwards; it must never write to it.
func (ks *KeyStore) MetadataOf(key *APIKey) map[string]string {
	ks.mu.RLock()
	defer ks.mu.RUnlock()
	return key.Metadata
}

// LookupActive finds an active key by its raw value, with its metadata read
// under the store lock, without counting it as used: for a route that does
// not accept keys but must recognise a scoped one (keyscope). nil when the
// key is unknown, revoked or expired.
func (ks *KeyStore) LookupActive(rawKey string) (*APIKey, map[string]string) {
	return ks.activeWithRevocation(HashKey(rawKey), false)
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

// SetSyncGrace sets how long a newly created or additively imported key survives
// a sync whose list may predate its positive control-plane row. A committed
// personal key is PENDING during its hash import, then becomes ACTIVE. Older
// callers create here before committing the control-plane row. Either list can
// miss the new key. A d of 0 or less is ignored; the default is
// config.DefaultKeySyncGrace. Main sets config's EffectiveKeySyncGrace.
func (ks *KeyStore) SetSyncGrace(d time.Duration) {
	if d <= 0 {
		return
	}
	ks.mu.Lock()
	ks.syncGrace = d
	ks.mu.Unlock()
}

// Create adds a new key. Returns the APIKey and the raw key value.
func (ks *KeyStore) Create(name, owner string, models, providers []string, metadata map[string]string) (*APIKey, string) {
	if ks.controlPlane && !personMetadata(metadata) {
		return nil, ""
	}
	// Own the authority map: the caller cannot mutate it after Create.
	ownedMetadata := make(map[string]string, len(metadata))
	for name, value := range metadata {
		ownedMetadata[name] = value
	}
	metadata = ownedMetadata
	rawKey := generateKey()
	hash := HashKey(rawKey)
	prefix := keyPrefix(rawKey)

	ks.mu.Lock()
	defer ks.mu.Unlock()

	id := newKeyID()
	for ks.byID[id] != nil {
		id = newKeyID()
	}

	now := ks.now()
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
		CreatedAt:        now,
		UpdatedAt:        now,
		mintedAt:         now, // a sync that misses it inside the grace window keeps it
	}

	ks.byHash[hash] = key
	ks.byID[id] = key

	return key, rawKey
}

// Revoke marks a key as revoked. It also ends the key's sync grace window,
// so the next sync whose list lacks the key removes it.
func (ks *KeyStore) Revoke(id string) bool {
	revoked, _ := ks.RevokeWithIdentity(id)
	return revoked
}

// ErrKeyNotFound is returned by Update for an unknown key ID.
var ErrKeyNotFound = errors.New("key not found")

// reservedMetadataKeys are the key metadata names a tenant may never set. The
// auth plugin copies a key's metadata into the request as "key_<name>", so a
// name is reserved when the gateway reads key_<name> as authority, or when
// key_<name> would stand in for one of the key's own fields:
//   - authority (authorityMetadataKeys): org_id picks the org whose config,
//     provider credentials, budgets and guardrails the key uses; access_groups
//     grants model access groups; role is the RBAC role; team picks the RBAC
//     team (its role, member roles and model list) and the team budget the
//     key's spend counts against; allow_policy_override lets a request choose
//     its guardrail policy.
//   - the key's own fields: "type" would replace key_type, which decides
//     whether a managed key pays for its requests and whether a request is
//     logged at all; key_type goes with it. id, name and owner would read as
//     the key's identity (key_id, key_name, key_owner) in the request log,
//     next to the real auth_key_id, auth_key_name and auth_key_owner.
//
// The platform sets the authority entries when a key is created and they never
// change afterwards. Django rejects the same list
// (RESERVED_KEY_METADATA in futureagi/agentcc/validators.py); a Django test
// parses this map literal, so keep its shape.
var reservedMetadataKeys = map[string]struct{}{
	"org_id":                {},
	"user_id":               {},
	"access_groups":         {},
	"type":                  {},
	"key_type":              {},
	"role":                  {},
	"allow_policy_override": {},
	"team":                  {},
	"id":                    {},
	"name":                  {},
	"owner":                 {},
	"mcp_personal":          {}, // MCP personal key (reserved); only the MCP credential flow sets it, with a server-owned user id
	// A platform-minted key's scope and spend budget (internal/keyscope):
	// only the control plane sets them, from records no tenant can write.
	"scopes":           {},
	"budget_limit_usd": {},
	"budget_period":    {},
}

// authorityMetadataKeys are the reserved entries the gateway reads as
// authority. The auth plugin writes each to the request as key_<name> from the
// key's own metadata, after the labels, and drops every other reserved name.
var authorityMetadataKeys = []string{"org_id", "user_id", "access_groups", "role", "team", "allow_policy_override"}

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

// platformSetMetadataKeys are the reserved names a key takes from the admin
// API (POST /-/keys) and from the key sync: the platform binds org_id to every
// key it creates or syncs, and sets allow_policy_override on its own
// guardrail playground key. On the keys it mints for Omega EE it also sets
// the key's scope and spend budget (scopes, budget_limit_usd, budget_period;
// see internal/keyscope), derived from records no tenant can write
// (futureagi/agentcc/services/ee_agent_key_policy.py) and sent on the create
// and on every sync. They only narrow a key, and the sync rebuilds each key
// from its payload, so dropping them would leave an EE agent key unrestricted.
// The other reserved names never come from the platform (Django drops them
// before it calls the gateway): access_groups, role and team are authority a
// config-file key may hold, type, key_type, id, name and owner would stand in
// for the key's own fields, and mcp_personal is set only by the per-user connect flow, which this
// build does not have. Kept apart from reservedMetadataKeys, whose literal a
// Django parity test parses.
var platformSetMetadataKeys = map[string]struct{}{
	"org_id":                {},
	"user_id":               {},
	"allow_policy_override": {},
	"scopes":                {},
	"budget_limit_usd":      {},
	"budget_period":         {},
}

// NotPlatformSetMetadata returns the reserved names in metadata that the
// admin API and the key sync never take (every reserved name but org_id,
// allow_policy_override and an EE agent key's scopes, budget_limit_usd and
// budget_period), in order.
func NotPlatformSetMetadata(metadata map[string]string) []string {
	var names []string
	for k := range metadata {
		if _, settable := platformSetMetadataKeys[k]; IsReservedMetadataKey(k) && !settable {
			names = append(names, k)
		}
	}
	sort.Strings(names)
	return names
}

// withoutNotPlatformSetMetadata is metadata without the reserved names the
// key sync never takes: metadata itself when it holds none, else a copy.
func withoutNotPlatformSetMetadata(metadata map[string]string) map[string]string {
	if len(NotPlatformSetMetadata(metadata)) == 0 {
		return metadata
	}
	kept := make(map[string]string, len(metadata))
	for k, v := range metadata {
		if _, settable := platformSetMetadataKeys[k]; !IsReservedMetadataKey(k) || settable {
			kept[k] = v
		}
	}
	return kept
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
		// ks.mu (the auth plugin copies it into the request), and a write into
		// that map aborts the whole process with "concurrent map iteration and
		// map write". Readers keep the map they loaded; it never changes.
		merged := make(map[string]string, len(key.Metadata)+len(metadata))
		for k, v := range key.Metadata {
			merged[k] = v
		}
		for k, v := range metadata {
			merged[k] = v
		}
		key.Metadata = merged
	}
	key.UpdatedAt = ks.now()

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
		KeyHash:   strings.ToLower(k.KeyHash),
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
		Metadata:         withoutNotPlatformSetMetadata(sk.Metadata),
		ExpiresAt:        sk.ExpiresAt,
		CreatedAt:        time.Now(),
		UpdatedAt:        time.Now(),
	}
}

// LoadFromHashes merges control-plane keys (identified by pre-computed hashes)
// into the store. Keys whose hash already exists are skipped (config.yaml wins).
// Newly added hashes receive the same sync grace as Create. Duplicates do not
// renew that window, and retirement/revocation facts always override it.
// Returns the number of new keys added.
func (ks *KeyStore) LoadFromHashes(keys []SyncedKey) int {
	ks.mu.Lock()
	defer ks.mu.Unlock()

	added := 0
	for _, sk := range keys {
		if sk.KeyHash == "" {
			continue
		}
		if ks.refuseSyncedHash(sk, ks.latched733) || ks.blockedHashes[sk.KeyHash] {
			continue
		}
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
		// Additive imports include committed personal-key creation. A pull
		// fetched before this import (or during local PENDING) can omit it.
		// Reuse Create's bounded grace; explicit negative facts still clear it.
		key.mintedAt = ks.now()

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
// The one exception is a key created or additively imported within the sync
// grace window (see SetSyncGrace) measured at fetch start, taken here as now:
// Django may not list it as ACTIVE yet. A revoked key stays revoked when its hash
// comes back: the control plane never re-activates a key, so a set that still
// lists it was read before the revoke reached it (Django revokes a key here
// before it drops the key from the set). A set fetched before the last one
// applied is older than what the store holds and is ignored. Returns the
// number of keys loaded from the incoming set.
func (ks *KeyStore) SyncFromHashes(keys []SyncedKey) int {
	return ks.syncFromList(keys, ks.now())
}

// syncFromList is SyncFromHashes for a set fetched at fetchedAt.
func (ks *KeyStore) syncFromList(keys []SyncedKey, fetchedAt time.Time, states ...KeySyncState) int {
	ks.mu.Lock()
	defer ks.mu.Unlock()
	var state KeySyncState
	if len(states) > 0 {
		state = states[0]
	}

	// Syncs can overlap (at boot the startup sync retries next to the periodic
	// one). A set whose fetch began before the last applied one's is older: it
	// could drop a key the newer set listed, which no longer has a grace
	// window, or bring back a key revoked since.
	if fetchedAt.Before(ks.lastListFetchedAt) {
		slog.Info("key sync: ignoring a key list fetched before the one already applied",
			"fetched_earlier_by", ks.lastListFetchedAt.Sub(fetchedAt).String())
		return 0
	}
	ks.lastListFetchedAt = fetchedAt
	// Only an answer this sync applies latches: an older one ignored above
	// never does.
	ks.noteAnswerGenerationLocked(state)

	// Negative facts precede every positive list, including legacy empty lists.
	ks.applyNegativeSync(state)
	if !ks.controlPlane || len(ks.configSeeds) == 0 || ks.conflictsAnsweredLocked(state) {
		defer ks.listLoaded.Store(true)
	}
	// Under the latch no list, not even an empty non-authoritative one that
	// keeps the keys held, keeps an unowned synced key.
	if ks.controlPlane && ks.latched733 {
		ks.dropUnownedSyncedLocked()
	}
	if len(keys) == 0 && !state.authoritative() {
		// Today's guard: only a store holding synced keys has anything to
		// keep, so an app with no keys yet is not a WARN on every sync.
		if ks.holdsSyncedKeyLocked() {
			slog.Warn("key sync: non-authoritative empty list; keeping unlisted keys")
		}
		return 0
	}

	// A key Create made is kept only while the set lists neither its hash nor
	// its ID: a listed key goes through the load below, as every synced key.
	listedHash := make(map[string]bool, len(keys))
	listedID := make(map[string]bool, len(keys))
	for _, sk := range keys {
		if sk.KeyHash != "" {
			listedHash[sk.KeyHash] = true
			listedID[sk.ID] = true
		}
	}

	// Remove all previously synced keys (not from config), except keys made
	// here that the set may predate, remembering which of them were revoked.
	// Revoke ends a key's grace window, so a kept key is never a revoked one.
	revoked := make(map[string]bool)
	for hash, key := range ks.byHash {
		if key.Source != "sync" {
			continue
		}
		if !key.mintedAt.IsZero() && !listedHash[hash] && !listedID[key.ID] {
			age := fetchedAt.Sub(key.mintedAt)
			if age < ks.syncGrace {
				slog.Info("key sync: keeping a key created here that the control plane does not list yet",
					"id", key.ID, "name", key.Name, "age", age.Round(time.Second).String(),
					"grace", ks.syncGrace.String())
				continue
			}
			slog.Warn("key sync: removing a key created here that the control plane never listed",
				"id", key.ID, "name", key.Name, "age", age.Round(time.Second).String(),
				"grace", ks.syncGrace.String())
		}
		if key.Status == "revoked" {
			revoked[hash] = true
		}
		delete(ks.byHash, hash)
		delete(ks.byID, key.ID)
	}

	// Load the new set from the control plane.
	requireOwner := ks.latched733
	loaded := 0
	for _, sk := range keys {
		if sk.KeyHash == "" {
			continue
		}
		if ks.refuseSyncedHash(sk, requireOwner) {
			continue
		}
		// Standalone config keys retain their operator-owned precedence.
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
		if revoked[sk.KeyHash] || ks.blockedHashes[sk.KeyHash] {
			key.Status = "revoked"
		}

		ks.byHash[sk.KeyHash] = key
		ks.byID[id] = key
		loaded++
	}

	return loaded
}

// KeyListLoaded reports whether a key list from the control plane has been
// applied since the store was made: until then the store holds only its
// config keys, the keys made here and keys pushed to it one by one, so a key
// made in the app is not known yet. A gateway whose control plane syncs keys
// is not ready before this (Server's /readyz). An empty first list counts:
// the app lists no key until one is made.
func (ks *KeyStore) KeyListLoaded() bool {
	return ks.listLoaded.Load()
}

// AppHasNoKeyTable reports whether the control plane answered the latest key
// sync with 503 migrations_pending (ErrAppNotMigrated): its database has no
// key table yet, as on a new install before its bootstrap job has migrated it,
// so no key has been made in the app. The store then knows every key the app
// has, as after an empty first list, but holds no list: any other answer, a
// failure or a list, clears it, and keys already loaded stay. Server's /readyz
// counts it in place of a loaded list, so that `helm install --wait`, which
// runs the chart's post-install bootstrap job only once the gateway is ready,
// can finish an install with bundled datastores.
func (ks *KeyStore) AppHasNoKeyTable() bool {
	return ks.appHasNoKeyTable.Load()
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
