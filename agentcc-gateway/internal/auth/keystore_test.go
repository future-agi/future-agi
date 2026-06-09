package auth

import (
	"math"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// helper to build a minimal AuthConfig with the given key configs.
func authCfg(keys ...config.AuthKeyConfig) config.AuthConfig {
	return config.AuthConfig{Enabled: true, Keys: keys}
}

// ---------- NewKeyStore ----------

func TestNewKeyStore_LoadsFromConfig(t *testing.T) {
	cfg := authCfg(
		config.AuthKeyConfig{Name: "key-a", Key: "raw-key-a", Owner: "alice"},
		config.AuthKeyConfig{Name: "key-b", Key: "raw-key-b", Owner: "bob"},
	)
	ks := NewKeyStore(cfg)

	if ks.Count() != 2 {
		t.Fatalf("expected 2 keys, got %d", ks.Count())
	}

	// Both keys should be retrievable by their assigned IDs.
	a := ks.Get("key_1")
	b := ks.Get("key_2")
	if a == nil || b == nil {
		t.Fatal("expected both keys to be retrievable by ID")
	}
	if a.Name != "key-a" {
		t.Errorf("expected name key-a, got %s", a.Name)
	}
	if b.Owner != "bob" {
		t.Errorf("expected owner bob, got %s", b.Owner)
	}
}

func TestNewKeyStore_SkipsEmptyKey(t *testing.T) {
	cfg := authCfg(
		config.AuthKeyConfig{Name: "valid", Key: "some-key", Owner: "alice"},
		config.AuthKeyConfig{Name: "empty", Key: "", Owner: "bob"},
	)
	ks := NewKeyStore(cfg)

	if ks.Count() != 1 {
		t.Fatalf("expected 1 key (empty should be skipped), got %d", ks.Count())
	}
}

// ---------- Authenticate ----------

func TestAuthenticate_ValidKey(t *testing.T) {
	cfg := authCfg(config.AuthKeyConfig{Name: "test", Key: "my-secret", Owner: "alice"})
	ks := NewKeyStore(cfg)

	k := ks.Authenticate("my-secret")
	if k == nil {
		t.Fatal("expected valid key, got nil")
	}
	if k.Name != "test" {
		t.Errorf("expected name test, got %s", k.Name)
	}
	if k.Owner != "alice" {
		t.Errorf("expected owner alice, got %s", k.Owner)
	}
}

func TestAuthenticate_InvalidKey(t *testing.T) {
	cfg := authCfg(config.AuthKeyConfig{Name: "test", Key: "my-secret", Owner: "alice"})
	ks := NewKeyStore(cfg)

	if got := ks.Authenticate("wrong-key"); got != nil {
		t.Errorf("expected nil for invalid key, got %+v", got)
	}
}

func TestAuthenticate_SetsLastUsedAt(t *testing.T) {
	cfg := authCfg(config.AuthKeyConfig{Name: "test", Key: "my-secret", Owner: "alice"})
	ks := NewKeyStore(cfg)

	k := ks.Get("key_1")
	if k.GetLastUsedAt() != nil {
		t.Fatal("expected LastUsedAt to be nil before authentication")
	}

	before := time.Now()
	authed := ks.Authenticate("my-secret")
	after := time.Now()

	lastUsed := authed.GetLastUsedAt()
	if lastUsed == nil {
		t.Fatal("expected LastUsedAt to be set after authentication")
	}
	if lastUsed.Before(before) || lastUsed.After(after) {
		t.Errorf("LastUsedAt %v not in expected range [%v, %v]", lastUsed, before, after)
	}
}

// ---------- CanAccessModel ----------

func TestCanAccessModel_NoRestrictions(t *testing.T) {
	k := &APIKey{AllowedModels: nil}
	if !k.CanAccessModel("gpt-4") {
		t.Error("expected unrestricted key to allow any model")
	}

	k2 := &APIKey{AllowedModels: []string{}}
	if !k2.CanAccessModel("gpt-4") {
		t.Error("expected empty AllowedModels to allow any model")
	}
}

func TestCanAccessModel_Allowed(t *testing.T) {
	k := &APIKey{AllowedModels: []string{"gpt-4", "gpt-3.5-turbo"}}
	if !k.CanAccessModel("gpt-4") {
		t.Error("expected gpt-4 to be allowed")
	}
}

func TestCanAccessModel_Denied(t *testing.T) {
	k := &APIKey{AllowedModels: []string{"gpt-4"}}
	if k.CanAccessModel("claude-3") {
		t.Error("expected claude-3 to be denied")
	}
}

// ---------- CanAccessProvider ----------

func TestCanAccessProvider_NoRestrictions(t *testing.T) {
	k := &APIKey{AllowedProviders: nil}
	if !k.CanAccessProvider("openai") {
		t.Error("expected unrestricted key to allow any provider")
	}

	k2 := &APIKey{AllowedProviders: []string{}}
	if !k2.CanAccessProvider("openai") {
		t.Error("expected empty AllowedProviders to allow any provider")
	}
}

func TestCanAccessProvider_Allowed(t *testing.T) {
	k := &APIKey{AllowedProviders: []string{"openai", "anthropic"}}
	if !k.CanAccessProvider("anthropic") {
		t.Error("expected anthropic to be allowed")
	}
}

func TestCanAccessProvider_Denied(t *testing.T) {
	k := &APIKey{AllowedProviders: []string{"openai"}}
	if k.CanAccessProvider("anthropic") {
		t.Error("expected anthropic to be denied")
	}
}

// ---------- IsExpired ----------

func TestIsExpired_NoExpiry(t *testing.T) {
	k := &APIKey{ExpiresAt: nil}
	if k.IsExpired() {
		t.Error("expected nil ExpiresAt to mean not expired")
	}
}

func TestIsExpired_NotExpired(t *testing.T) {
	future := time.Now().Add(24 * time.Hour)
	k := &APIKey{ExpiresAt: &future}
	if k.IsExpired() {
		t.Error("expected future ExpiresAt to mean not expired")
	}
}

func TestIsExpired_Expired(t *testing.T) {
	past := time.Now().Add(-24 * time.Hour)
	k := &APIKey{ExpiresAt: &past}
	if !k.IsExpired() {
		t.Error("expected past ExpiresAt to mean expired")
	}
}

// ---------- IsActive ----------

func TestIsActive_Active(t *testing.T) {
	k := &APIKey{Status: "active", ExpiresAt: nil}
	if !k.IsActive() {
		t.Error("expected active key with no expiry to be active")
	}
}

func TestIsActive_Revoked(t *testing.T) {
	k := &APIKey{Status: "revoked", ExpiresAt: nil}
	if k.IsActive() {
		t.Error("expected revoked key to not be active")
	}
}

func TestIsActive_Expired(t *testing.T) {
	past := time.Now().Add(-24 * time.Hour)
	k := &APIKey{Status: "active", ExpiresAt: &past}
	if k.IsActive() {
		t.Error("expected active but expired key to not be active")
	}
}

// ---------- Create ----------

func TestCreate(t *testing.T) {
	ks := NewKeyStore(authCfg())

	key, rawKey := ks.Create("new-key", "owner1",
		[]string{"gpt-4"}, []string{"openai"},
		map[string]string{"env": "prod"})

	if !strings.HasPrefix(rawKey, "sk-agentcc-") {
		t.Errorf("expected raw key to start with sk-agentcc-, got %s", rawKey)
	}
	if key.Name != "new-key" {
		t.Errorf("expected name new-key, got %s", key.Name)
	}
	if key.Owner != "owner1" {
		t.Errorf("expected owner owner1, got %s", key.Owner)
	}
	if key.Status != "active" {
		t.Errorf("expected status active, got %s", key.Status)
	}
	if ks.Count() != 1 {
		t.Errorf("expected count 1, got %d", ks.Count())
	}

	// The returned raw key should authenticate successfully.
	authed := ks.Authenticate(rawKey)
	if authed == nil {
		t.Fatal("expected to authenticate with returned raw key")
	}
	if authed.ID != key.ID {
		t.Errorf("expected authenticated key ID %s, got %s", key.ID, authed.ID)
	}
}

// ---------- Revoke ----------

func TestRevoke(t *testing.T) {
	ks := NewKeyStore(authCfg())
	key, _ := ks.Create("revokeMe", "owner", nil, nil, nil)

	if !ks.Revoke(key.ID) {
		t.Fatal("expected Revoke to return true for existing key")
	}

	got := ks.Get(key.ID)
	if got.Status != "revoked" {
		t.Errorf("expected status revoked, got %s", got.Status)
	}
}

func TestRevoke_NotFound(t *testing.T) {
	ks := NewKeyStore(authCfg())
	if ks.Revoke("nonexistent") {
		t.Error("expected Revoke to return false for unknown ID")
	}
}

// ---------- Update ----------

func TestUpdate(t *testing.T) {
	ks := NewKeyStore(authCfg())
	key, _ := ks.Create("original", "alice", nil, nil, nil)

	newName := "updated"
	newOwner := "bob"
	newModels := []string{"gpt-4", "claude-3"}

	updated, ok := ks.Update(key.ID, &newName, &newOwner, newModels, nil, map[string]string{"team": "infra"})
	if !ok {
		t.Fatal("expected Update to return true")
	}
	if updated.Name != "updated" {
		t.Errorf("expected name updated, got %s", updated.Name)
	}
	if updated.Owner != "bob" {
		t.Errorf("expected owner bob, got %s", updated.Owner)
	}
	if len(updated.AllowedModels) != 2 {
		t.Errorf("expected 2 allowed models, got %d", len(updated.AllowedModels))
	}
	if updated.Metadata["team"] != "infra" {
		t.Errorf("expected metadata team=infra, got %s", updated.Metadata["team"])
	}
	if !updated.UpdatedAt.After(key.CreatedAt) || updated.UpdatedAt.Equal(key.CreatedAt) {
		// UpdatedAt should be at or after CreatedAt (they may be equal at nanosecond level)
	}
}

func TestUpdate_NotFound(t *testing.T) {
	ks := NewKeyStore(authCfg())
	name := "x"
	_, ok := ks.Update("nonexistent", &name, nil, nil, nil, nil)
	if ok {
		t.Error("expected Update to return false for unknown ID")
	}
}

// ---------- List ----------

func TestList(t *testing.T) {
	ks := NewKeyStore(authCfg())
	ks.Create("a", "o1", nil, nil, nil)
	ks.Create("b", "o2", nil, nil, nil)
	ks.Create("c", "o3", nil, nil, nil)

	keys := ks.List()
	if len(keys) != 3 {
		t.Fatalf("expected 3 keys, got %d", len(keys))
	}

	names := make(map[string]bool)
	for _, k := range keys {
		names[k.Name] = true
	}
	for _, n := range []string{"a", "b", "c"} {
		if !names[n] {
			t.Errorf("expected key %s in list", n)
		}
	}
}

// ---------- HashKey ----------

func TestHashKey(t *testing.T) {
	h1 := HashKey("test-key")
	h2 := HashKey("test-key")
	if h1 != h2 {
		t.Error("expected same input to produce same hash")
	}

	h3 := HashKey("different-key")
	if h1 == h3 {
		t.Error("expected different inputs to produce different hashes")
	}

	// Should be a valid hex string of SHA-256 length (64 hex chars).
	if len(h1) != 64 {
		t.Errorf("expected hash length 64, got %d", len(h1))
	}
}

// ---------- KeyPrefix ----------

func TestKeyPrefix(t *testing.T) {
	// Short key: len <= 12, prefix is first half + "..."
	short := keyPrefix("abcd")
	if short != "ab..." {
		t.Errorf("expected short key prefix 'ab...', got '%s'", short)
	}

	// Exactly 12 chars: len <= 12, prefix is first 6 + "..."
	exact12 := keyPrefix("123456789012")
	if exact12 != "123456..." {
		t.Errorf("expected exact12 prefix '123456...', got '%s'", exact12)
	}

	// Long key: len > 12, prefix is first 12 + "..."
	long := keyPrefix("sk-agentcc-abcdef1234567890")
	if long != "sk-agentcc-abc..." {
		t.Errorf("expected long key prefix 'sk-agentcc-abc...', got '%s'", long)
	}
}

// ---------- ExpiresAt from config ----------

func TestExpiresAt_FromConfig(t *testing.T) {
	expiresStr := "2030-06-15T12:00:00Z"
	cfg := authCfg(config.AuthKeyConfig{
		Name:      "expiring",
		Key:       "exp-key",
		Owner:     "alice",
		ExpiresAt: expiresStr,
	})
	ks := NewKeyStore(cfg)

	k := ks.Get("key_1")
	if k == nil {
		t.Fatal("expected key to exist")
	}
	if k.ExpiresAt == nil {
		t.Fatal("expected ExpiresAt to be set")
	}

	expected, _ := time.Parse(time.RFC3339, expiresStr)
	if !k.ExpiresAt.Equal(expected) {
		t.Errorf("expected ExpiresAt %v, got %v", expected, *k.ExpiresAt)
	}
}

// ========== Managed Keys & Credits Tests ==========

// ---------- USDToMicros ----------

func TestUSDToMicros(t *testing.T) {
	tests := []struct {
		usd    float64
		micros int64
	}{
		{1.0, 1_000_000},
		{0.005, 5_000},
		{0.000001, 1},
		{0.0, 0},
		{100.50, 100_500_000},
	}
	for _, tc := range tests {
		got := USDToMicros(tc.usd)
		if got != tc.micros {
			t.Errorf("USDToMicros(%f) = %d, want %d", tc.usd, got, tc.micros)
		}
	}
}

// ---------- MicrosToUSD ----------

func TestMicrosToUSD(t *testing.T) {
	tests := []struct {
		micros int64
		usd    float64
	}{
		{1_000_000, 1.0},
		{5_000, 0.005},
		{1, 0.000001},
		{0, 0.0},
		{100_500_000, 100.50},
	}
	for _, tc := range tests {
		got := MicrosToUSD(tc.micros)
		if math.Abs(got-tc.usd) > 1e-9 {
			t.Errorf("MicrosToUSD(%d) = %f, want %f", tc.micros, got, tc.usd)
		}
	}
}

// ---------- APIKey.BalanceUSD ----------

func TestAPIKey_BalanceUSD(t *testing.T) {
	cfg := authCfg(config.AuthKeyConfig{
		Name:          "managed",
		Key:           "sk-agentcc-managed-balance",
		KeyType:       "managed",
		CreditBalance: 5.25,
	})
	ks := NewKeyStore(cfg)
	key := ks.Get("key_1")
	if key == nil {
		t.Fatal("expected key to exist")
	}
	bal := key.BalanceUSD()
	if math.Abs(bal-5.25) > 1e-9 {
		t.Errorf("expected BalanceUSD 5.25, got %f", bal)
	}
}

// ---------- APIKey.DeductMicros ----------

func TestAPIKey_DeductMicros(t *testing.T) {
	cfg := authCfg(config.AuthKeyConfig{
		Name:          "managed",
		Key:           "sk-agentcc-managed-deduct",
		KeyType:       "managed",
		CreditBalance: 10.0,
	})
	ks := NewKeyStore(cfg)
	key := ks.Get("key_1")
	if key == nil {
		t.Fatal("expected key to exist")
	}

	// Deduct $3.50 (3_500_000 micros).
	newBal := key.DeductMicros(3_500_000)
	if math.Abs(newBal-6.5) > 1e-9 {
		t.Errorf("expected 6.5 after deduction, got %f", newBal)
	}
	if math.Abs(key.BalanceUSD()-6.5) > 1e-9 {
		t.Errorf("expected BalanceUSD() 6.5, got %f", key.BalanceUSD())
	}
}

// ---------- APIKey.AddMicros ----------

func TestAPIKey_AddMicros(t *testing.T) {
	cfg := authCfg(config.AuthKeyConfig{
		Name:          "managed",
		Key:           "sk-agentcc-managed-add",
		KeyType:       "managed",
		CreditBalance: 2.0,
	})
	ks := NewKeyStore(cfg)
	key := ks.Get("key_1")
	if key == nil {
		t.Fatal("expected key to exist")
	}

	// Add $5.00 (5_000_000 micros).
	newBal := key.AddMicros(5_000_000)
	if math.Abs(newBal-7.0) > 1e-9 {
		t.Errorf("expected 7.0 after addition, got %f", newBal)
	}
	if math.Abs(key.BalanceUSD()-7.0) > 1e-9 {
		t.Errorf("expected BalanceUSD() 7.0, got %f", key.BalanceUSD())
	}
}

// ---------- APIKey SetBalanceMicros ----------

func TestAPIKey_SetBalanceMicros(t *testing.T) {
	cfg := authCfg(config.AuthKeyConfig{
		Name:          "managed",
		Key:           "sk-agentcc-managed-setbal",
		KeyType:       "managed",
		CreditBalance: 10.0,
	})
	ks := NewKeyStore(cfg)
	key := ks.Get("key_1")
	if key == nil {
		t.Fatal("expected key to exist")
	}

	// Set balance to $3.50 exactly.
	key.SetBalanceMicros(3_500_000)
	if math.Abs(key.BalanceUSD()-3.5) > 1e-9 {
		t.Errorf("expected 3.5 after SetBalanceMicros, got %f", key.BalanceUSD())
	}

	// Set to negative (simulating Redis reporting negative after over-deduct).
	key.SetBalanceMicros(-1_000_000)
	if math.Abs(key.BalanceUSD()-(-1.0)) > 1e-9 {
		t.Errorf("expected -1.0 after SetBalanceMicros, got %f", key.BalanceUSD())
	}
}

// ---------- APIKey concurrent deductions ----------

func TestAPIKey_ConcurrentDeductions(t *testing.T) {
	cfg := authCfg(config.AuthKeyConfig{
		Name:          "managed",
		Key:           "sk-agentcc-managed-concurrent",
		KeyType:       "managed",
		CreditBalance: 1.0, // 1_000_000 micros
	})
	ks := NewKeyStore(cfg)
	key := ks.Get("key_1")
	if key == nil {
		t.Fatal("expected key to exist")
	}

	// 100 goroutines each deducting 1 micro.
	var wg sync.WaitGroup
	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			key.DeductMicros(1)
		}()
	}
	wg.Wait()

	// Initial = 1_000_000, deducted = 100, remaining = 999_900.
	expectedMicros := int64(1_000_000 - 100)
	expectedUSD := MicrosToUSD(expectedMicros)
	bal := key.BalanceUSD()
	if math.Abs(bal-expectedUSD) > 1e-9 {
		t.Errorf("expected balance %f after 100 concurrent deductions, got %f", expectedUSD, bal)
	}
}

// ---------- KeyStore.AddCredits ----------

func TestKeyStore_AddCredits(t *testing.T) {
	cfg := authCfg(config.AuthKeyConfig{
		Name:          "managed",
		Key:           "sk-agentcc-managed-addcredits",
		KeyType:       "managed",
		CreditBalance: 5.0,
	})
	ks := NewKeyStore(cfg)
	key := ks.Get("key_1")
	if key == nil {
		t.Fatal("expected key to exist")
	}

	newBal, ok := ks.AddCredits(key.ID, 3.0)
	if !ok {
		t.Fatal("expected AddCredits to return true for managed key")
	}
	if math.Abs(newBal-8.0) > 1e-9 {
		t.Errorf("expected new balance 8.0, got %f", newBal)
	}
	if math.Abs(key.BalanceUSD()-8.0) > 1e-9 {
		t.Errorf("expected BalanceUSD() 8.0, got %f", key.BalanceUSD())
	}
}

func TestKeyStore_AddCredits_ByokKey(t *testing.T) {
	cfg := authCfg(config.AuthKeyConfig{
		Name:    "byok",
		Key:     "sk-agentcc-byok-addcredits",
		KeyType: "byok",
	})
	ks := NewKeyStore(cfg)
	key := ks.Get("key_1")
	if key == nil {
		t.Fatal("expected key to exist")
	}

	_, ok := ks.AddCredits(key.ID, 10.0)
	if ok {
		t.Error("expected AddCredits to return false for BYOK key")
	}
}

func TestKeyStore_AddCredits_NotFound(t *testing.T) {
	ks := NewKeyStore(authCfg())

	_, ok := ks.AddCredits("nonexistent", 10.0)
	if ok {
		t.Error("expected AddCredits to return false for non-existent key")
	}
}

// ---------- APIKey.IsManaged ----------

func TestAPIKey_IsManaged(t *testing.T) {
	cfg := authCfg(
		config.AuthKeyConfig{
			Name:          "managed",
			Key:           "sk-agentcc-managed-ismanaged",
			KeyType:       "managed",
			CreditBalance: 1.0,
		},
		config.AuthKeyConfig{
			Name:    "byok",
			Key:     "sk-agentcc-byok-ismanaged",
			KeyType: "byok",
		},
	)
	ks := NewKeyStore(cfg)

	managed := ks.Get("key_1")
	byok := ks.Get("key_2")
	if managed == nil || byok == nil {
		t.Fatal("expected both keys to exist")
	}

	if !managed.IsManaged() {
		t.Error("expected managed key to return true for IsManaged()")
	}
	if byok.IsManaged() {
		t.Error("expected byok key to return false for IsManaged()")
	}
}
