package credits

import (
	"context"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// ---------- helpers ----------

// newKeyStore builds a KeyStore with one managed key ($10 balance) and one BYOK key.
func newKeyStore() *auth.KeyStore {
	cfg := config.AuthConfig{
		Enabled: true,
		Keys: []config.AuthKeyConfig{
			{
				Name:          "test-managed",
				Key:           "sk-agentcc-managed-test",
				KeyType:       "managed",
				CreditBalance: 10.0,
			},
			{
				Name:    "test-byok",
				Key:     "sk-agentcc-byok-test",
				KeyType: "byok",
			},
		},
	}
	return auth.NewKeyStore(cfg)
}

// findKey returns the first key that matches the given key type.
func findKey(ks *auth.KeyStore, keyType string) *auth.APIKey {
	for _, k := range ks.List() {
		if k.KeyType == keyType {
			return k
		}
	}
	return nil
}

// makeRC builds a RequestContext with the given auth_key_id and key_type metadata.
func makeRC(keyID, keyType string) *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.Metadata["auth_key_id"] = keyID
	rc.Metadata["key_type"] = keyType
	return rc
}

// ---------- tests ----------

func TestCreditsPlugin_ByokKey_PassThrough(t *testing.T) {
	ks := newKeyStore()
	p := New(true, ks)

	byok := findKey(ks, "byok")
	if byok == nil {
		t.Fatal("expected to find byok key")
	}

	rc := makeRC(byok.ID, "byok")
	defer rc.Release()

	// ProcessRequest should pass through.
	res := p.ProcessRequest(context.Background(), rc)
	if res.Action != pipeline.Continue {
		t.Errorf("expected Continue for BYOK ProcessRequest, got %d", res.Action)
	}

	// Simulate a cost being set by a previous plugin (e.g., cost tracker).
	rc.Metadata["cost"] = "0.005000"

	// ProcessResponse should also pass through -- no credits metadata set.
	res = p.ProcessResponse(context.Background(), rc)
	if res.Action != pipeline.Continue {
		t.Errorf("expected Continue for BYOK ProcessResponse, got %d", res.Action)
	}
	if _, ok := rc.Metadata["credits_used"]; ok {
		t.Error("expected no credits_used metadata for BYOK key")
	}
	if _, ok := rc.Metadata["credits_remaining"]; ok {
		t.Error("expected no credits_remaining metadata for BYOK key")
	}
}

func TestCreditsPlugin_ManagedKey_SufficientBalance(t *testing.T) {
	ks := newKeyStore()
	p := New(true, ks)

	managed := findKey(ks, "managed")
	if managed == nil {
		t.Fatal("expected to find managed key")
	}

	rc := makeRC(managed.ID, "managed")
	defer rc.Release()

	// Pre-request: should pass (balance = $10).
	res := p.ProcessRequest(context.Background(), rc)
	if res.Action != pipeline.Continue {
		t.Fatalf("expected Continue for managed key with balance, got %d", res.Action)
	}
	if res.Error != nil {
		t.Fatalf("expected no error, got %v", res.Error)
	}

	// Simulate cost tracker setting cost after provider call.
	rc.Metadata["cost"] = "0.005000"

	// Post-response: should deduct and set credits metadata.
	res = p.ProcessResponse(context.Background(), rc)
	if res.Action != pipeline.Continue {
		t.Fatalf("expected Continue for ProcessResponse, got %d", res.Action)
	}

	creditsUsed, ok := rc.Metadata["credits_used"]
	if !ok {
		t.Fatal("expected credits_used to be set")
	}
	if creditsUsed != "0.005000" {
		t.Errorf("expected credits_used=0.005000, got %s", creditsUsed)
	}

	creditsRemaining, ok := rc.Metadata["credits_remaining"]
	if !ok {
		t.Fatal("expected credits_remaining to be set")
	}
	if creditsRemaining != "9.995000" {
		t.Errorf("expected credits_remaining=9.995000, got %s", creditsRemaining)
	}

	// Verify the key's actual balance matches.
	bal := managed.BalanceUSD()
	if bal != 9.995 {
		t.Errorf("expected key balance 9.995, got %f", bal)
	}
}

func TestCreditsPlugin_ManagedKey_ZeroBalance(t *testing.T) {
	ks := newKeyStore()
	p := New(true, ks)

	managed := findKey(ks, "managed")
	if managed == nil {
		t.Fatal("expected to find managed key")
	}

	// Drain all credits.
	managed.DeductMicros(auth.USDToMicros(10.0))
	if managed.BalanceUSD() != 0 {
		t.Fatalf("expected 0 balance after drain, got %f", managed.BalanceUSD())
	}

	rc := makeRC(managed.ID, "managed")
	defer rc.Release()

	res := p.ProcessRequest(context.Background(), rc)
	if res.Action != pipeline.ShortCircuit {
		t.Fatalf("expected ShortCircuit for zero-balance managed key, got %d", res.Action)
	}
	if res.Error == nil {
		t.Fatal("expected error result for zero balance")
	}
	if res.Error.Status != http.StatusPaymentRequired {
		t.Errorf("expected 402 status, got %d", res.Error.Status)
	}
	if res.Error.Code != "payment_required" {
		t.Errorf("expected code payment_required, got %s", res.Error.Code)
	}
}

func TestCreditsPlugin_ManagedKey_NegativeBalance(t *testing.T) {
	ks := newKeyStore()
	p := New(true, ks)

	managed := findKey(ks, "managed")
	if managed == nil {
		t.Fatal("expected to find managed key")
	}

	// Push balance negative (deduct more than available).
	managed.DeductMicros(auth.USDToMicros(15.0))
	if managed.BalanceUSD() >= 0 {
		t.Fatalf("expected negative balance, got %f", managed.BalanceUSD())
	}

	rc := makeRC(managed.ID, "managed")
	defer rc.Release()

	res := p.ProcessRequest(context.Background(), rc)
	if res.Action != pipeline.ShortCircuit {
		t.Fatalf("expected ShortCircuit for negative-balance managed key, got %d", res.Action)
	}
	if res.Error == nil {
		t.Fatal("expected error result for negative balance")
	}
	if res.Error.Status != http.StatusPaymentRequired {
		t.Errorf("expected 402 status, got %d", res.Error.Status)
	}
}

func TestCreditsPlugin_ManagedKey_NoCost(t *testing.T) {
	ks := newKeyStore()
	p := New(true, ks)

	managed := findKey(ks, "managed")
	if managed == nil {
		t.Fatal("expected to find managed key")
	}

	initialBalance := managed.BalanceUSD()

	rc := makeRC(managed.ID, "managed")
	defer rc.Release()
	// No "cost" metadata set -- simulates a response without cost info.

	res := p.ProcessResponse(context.Background(), rc)
	if res.Action != pipeline.Continue {
		t.Fatalf("expected Continue when no cost, got %d", res.Action)
	}
	if _, ok := rc.Metadata["credits_used"]; ok {
		t.Error("expected no credits_used metadata when cost is absent")
	}
	if _, ok := rc.Metadata["credits_remaining"]; ok {
		t.Error("expected no credits_remaining metadata when cost is absent")
	}
	if managed.BalanceUSD() != initialBalance {
		t.Errorf("expected balance unchanged (%f), got %f", initialBalance, managed.BalanceUSD())
	}
}

func TestCreditsPlugin_Disabled(t *testing.T) {
	ks := newKeyStore()
	p := New(false, ks) // disabled

	managed := findKey(ks, "managed")
	if managed == nil {
		t.Fatal("expected to find managed key")
	}

	// Drain balance so it would normally block.
	managed.DeductMicros(auth.USDToMicros(10.0))

	rc := makeRC(managed.ID, "managed")
	defer rc.Release()

	res := p.ProcessRequest(context.Background(), rc)
	if res.Action != pipeline.Continue {
		t.Errorf("expected Continue when plugin disabled, got %d (ProcessRequest)", res.Action)
	}

	rc.Metadata["cost"] = "1.000000"

	res = p.ProcessResponse(context.Background(), rc)
	if res.Action != pipeline.Continue {
		t.Errorf("expected Continue when plugin disabled, got %d (ProcessResponse)", res.Action)
	}
}

func TestCreditsPlugin_NilKeyStore(t *testing.T) {
	p := New(true, nil) // nil keyStore

	rc := makeRC("key_1", "managed")
	defer rc.Release()

	res := p.ProcessRequest(context.Background(), rc)
	if res.Action != pipeline.Continue {
		t.Errorf("expected Continue when keyStore nil, got %d (ProcessRequest)", res.Action)
	}

	rc.Metadata["cost"] = "1.000000"

	res = p.ProcessResponse(context.Background(), rc)
	if res.Action != pipeline.Continue {
		t.Errorf("expected Continue when keyStore nil, got %d (ProcessResponse)", res.Action)
	}
}
