package auth

import (
	"context"
	"net/http"
	"testing"
	"time"

	authpkg "github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// pastTime returns a time in the past for testing expiration.
func pastTime() string {
	return time.Now().Add(-24 * time.Hour).Format(time.RFC3339)
}

// newTestKeyStore creates a KeyStore with known test keys.
func newTestKeyStore() *authpkg.KeyStore {
	cfg := config.AuthConfig{
		Enabled: true,
		Keys: []config.AuthKeyConfig{
			{
				Name:      "test-key",
				Key:       "sk-agentcc-test-key-123",
				Owner:     "tester",
				Models:    []string{"gpt-4o"},
				Providers: []string{"openai"},
			},
			{
				Name:  "unrestricted",
				Key:   "sk-agentcc-unrestricted",
				Owner: "admin",
			},
			{
				Name:  "with-metadata",
				Key:   "sk-agentcc-meta-key",
				Owner: "meta-owner",
				Metadata: map[string]string{
					"team":        "platform",
					"environment": "production",
				},
			},
		},
	}
	return authpkg.NewKeyStore(cfg)
}

// newExpiredKeyStore creates a KeyStore with an already-expired key.
func newExpiredKeyStore() *authpkg.KeyStore {
	cfg := config.AuthConfig{
		Enabled: true,
		Keys: []config.AuthKeyConfig{
			{
				Name:      "expired-key",
				Key:       "sk-agentcc-expired",
				Owner:     "tester",
				ExpiresAt: pastTime(),
			},
		},
	}
	return authpkg.NewKeyStore(cfg)
}

func TestPlugin_Name(t *testing.T) {
	ks := newTestKeyStore()
	p := New(ks, true)
	if got := p.Name(); got != "auth" {
		t.Errorf("Name() = %q, want %q", got, "auth")
	}
}

func TestPlugin_Priority(t *testing.T) {
	ks := newTestKeyStore()
	p := New(ks, true)
	if got := p.Priority(); got != 20 {
		t.Errorf("Priority() = %d, want %d", got, 20)
	}
}

func TestPlugin_Disabled(t *testing.T) {
	ks := newTestKeyStore()
	p := New(ks, false) // disabled

	rc := models.AcquireRequestContext()
	defer rc.Release()
	// No auth header at all — should still pass because plugin is disabled.

	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatalf("expected Continue when plugin is disabled, got action=%d", result.Action)
	}
	if result.Error != nil {
		t.Fatalf("expected no error when plugin is disabled, got %v", result.Error)
	}
}

func TestPlugin_MissingAuthHeader(t *testing.T) {
	ks := newTestKeyStore()
	p := New(ks, true)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	// No authorization metadata set.

	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("expected error for missing auth header, got nil")
	}
	if result.Error.Status != http.StatusUnauthorized {
		t.Errorf("expected status 401, got %d", result.Error.Status)
	}
	if result.Action != pipeline.ShortCircuit {
		t.Errorf("expected ShortCircuit action on error, got %d", result.Action)
	}
}

func TestPlugin_InvalidFormat(t *testing.T) {
	ks := newTestKeyStore()
	p := New(ks, true)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Metadata["authorization"] = "Basic dXNlcjpwYXNz"

	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("expected error for invalid auth format, got nil")
	}
	if result.Error.Status != http.StatusUnauthorized {
		t.Errorf("expected status 401, got %d", result.Error.Status)
	}
}

func TestPlugin_InvalidKey(t *testing.T) {
	ks := newTestKeyStore()
	p := New(ks, true)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Metadata["authorization"] = "Bearer sk-agentcc-wrong-key"

	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("expected error for invalid key, got nil")
	}
	if result.Error.Status != http.StatusUnauthorized {
		t.Errorf("expected status 401, got %d", result.Error.Status)
	}
}

func TestPlugin_ValidKey(t *testing.T) {
	ks := newTestKeyStore()
	p := New(ks, true)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "gpt-4o"
	rc.Provider = "openai"
	rc.Metadata["authorization"] = "Bearer sk-agentcc-test-key-123"

	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatalf("expected Continue, got action=%d, err=%v", result.Action, result.Error)
	}
	if result.Error != nil {
		t.Fatalf("expected no error, got %v", result.Error)
	}

	// Check that auth metadata was set.
	if v := rc.Metadata["auth_key_name"]; v != "test-key" {
		t.Errorf("auth_key_name = %q, want %q", v, "test-key")
	}
	if v := rc.Metadata["auth_key_owner"]; v != "tester" {
		t.Errorf("auth_key_owner = %q, want %q", v, "tester")
	}
	if v := rc.Metadata["auth_key_id"]; v == "" {
		t.Error("auth_key_id should be set, got empty string")
	}
}

func TestPlugin_RevokedKey(t *testing.T) {
	ks := newTestKeyStore()
	p := New(ks, true)

	// First, find the key ID for the test key so we can revoke it.
	keys := ks.List()
	var testKeyID string
	for _, k := range keys {
		if k.Name == "test-key" {
			testKeyID = k.ID
			break
		}
	}
	if testKeyID == "" {
		t.Fatal("could not find test-key in keystore")
	}

	// Revoke the key.
	if !ks.Revoke(testKeyID) {
		t.Fatal("failed to revoke key")
	}

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "gpt-4o"
	rc.Provider = "openai"
	rc.Metadata["authorization"] = "Bearer sk-agentcc-test-key-123"

	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("expected error for revoked key, got nil")
	}
	if result.Error.Status != http.StatusUnauthorized {
		t.Errorf("expected status 401, got %d", result.Error.Status)
	}
}

func TestPlugin_ExpiredKey(t *testing.T) {
	ks := newExpiredKeyStore()
	p := New(ks, true)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Metadata["authorization"] = "Bearer sk-agentcc-expired"

	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("expected error for expired key, got nil")
	}
	if result.Error.Status != http.StatusUnauthorized {
		t.Errorf("expected status 401, got %d", result.Error.Status)
	}
}

func TestPlugin_ModelAccessDenied(t *testing.T) {
	ks := newTestKeyStore()
	p := New(ks, true)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "claude-3-opus"           // test-key only allows gpt-4o
	rc.Provider = "openai"
	rc.Metadata["authorization"] = "Bearer sk-agentcc-test-key-123"

	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("expected error for model access denied, got nil")
	}
	if result.Error.Status != http.StatusForbidden {
		t.Errorf("expected status 403, got %d", result.Error.Status)
	}
}

func TestPlugin_ModelAccessAllowed(t *testing.T) {
	ks := newTestKeyStore()
	p := New(ks, true)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "gpt-4o"
	rc.Provider = "openai"
	rc.Metadata["authorization"] = "Bearer sk-agentcc-test-key-123"

	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatalf("expected Continue for allowed model, got action=%d, err=%v", result.Action, result.Error)
	}
}

func TestPlugin_UnrestrictedKey(t *testing.T) {
	ks := newTestKeyStore()
	p := New(ks, true)

	// unrestricted key has no model/provider restrictions.
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "claude-3-opus"
	rc.Provider = "anthropic"
	rc.Metadata["authorization"] = "Bearer sk-agentcc-unrestricted"

	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatalf("expected Continue for unrestricted key, got action=%d, err=%v", result.Action, result.Error)
	}
	if result.Error != nil {
		t.Fatalf("expected no error, got %v", result.Error)
	}
}

func TestPlugin_SetsUserID(t *testing.T) {
	ks := newTestKeyStore()
	p := New(ks, true)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "gpt-4o"
	rc.Provider = "openai"
	rc.Metadata["authorization"] = "Bearer sk-agentcc-test-key-123"
	// UserID is empty initially.

	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatalf("expected Continue, got action=%d, err=%v", result.Action, result.Error)
	}

	if rc.UserID != "tester" {
		t.Errorf("UserID = %q, want %q", rc.UserID, "tester")
	}
}

func TestPlugin_PreservesExistingUserID(t *testing.T) {
	ks := newTestKeyStore()
	p := New(ks, true)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "gpt-4o"
	rc.Provider = "openai"
	rc.UserID = "existing-user"
	rc.Metadata["authorization"] = "Bearer sk-agentcc-test-key-123"

	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatalf("expected Continue, got action=%d, err=%v", result.Action, result.Error)
	}

	// UserID should NOT be overridden.
	if rc.UserID != "existing-user" {
		t.Errorf("UserID = %q, want %q (should preserve existing)", rc.UserID, "existing-user")
	}
}

func TestPlugin_ProcessResponse_Noop(t *testing.T) {
	ks := newTestKeyStore()
	p := New(ks, true)

	rc := models.AcquireRequestContext()
	defer rc.Release()

	result := p.ProcessResponse(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Errorf("ProcessResponse should return Continue, got action=%d", result.Action)
	}
	if result.Error != nil {
		t.Errorf("ProcessResponse should return no error, got %v", result.Error)
	}
}

func TestPlugin_KeyMetadataPropagated(t *testing.T) {
	ks := newTestKeyStore()
	p := New(ks, true)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Metadata["authorization"] = "Bearer sk-agentcc-meta-key"

	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatalf("expected Continue, got action=%d, err=%v", result.Action, result.Error)
	}

	// Key metadata should be prefixed with "key_".
	if v := rc.Metadata["key_team"]; v != "platform" {
		t.Errorf("key_team = %q, want %q", v, "platform")
	}
	if v := rc.Metadata["key_environment"]; v != "production" {
		t.Errorf("key_environment = %q, want %q", v, "production")
	}

	// Standard auth metadata should also be set.
	if v := rc.Metadata["auth_key_owner"]; v != "meta-owner" {
		t.Errorf("auth_key_owner = %q, want %q", v, "meta-owner")
	}
}

func TestPlugin_ProviderACLStoredInMetadata(t *testing.T) {
	ks := newTestKeyStore()
	p := New(ks, true)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "gpt-4o"
	// Provider is NOT set at auth plugin time (it's set after routing).
	rc.Metadata["authorization"] = "Bearer sk-agentcc-test-key-123"

	result := p.ProcessRequest(context.Background(), rc)
	if result.Error != nil {
		t.Fatalf("unexpected error: %v", result.Error)
	}

	// Auth plugin should store allowed providers for downstream enforcement.
	allowedProviders := rc.Metadata["auth_allowed_providers"]
	if allowedProviders != "openai" {
		t.Errorf("auth_allowed_providers = %q, want %q", allowedProviders, "openai")
	}
}

func TestPlugin_UnrestrictedProviderAccess(t *testing.T) {
	ks := newTestKeyStore()
	p := New(ks, true)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "gpt-4o"
	// Use unrestricted key (no provider restrictions).
	rc.Metadata["authorization"] = "Bearer sk-agentcc-unrestricted"

	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatalf("expected Continue for unrestricted key, got action=%d, err=%v", result.Action, result.Error)
	}
	// Unrestricted key should NOT set auth_allowed_providers.
	if v, ok := rc.Metadata["auth_allowed_providers"]; ok {
		t.Errorf("auth_allowed_providers should not be set for unrestricted key, got %q", v)
	}
}

func TestPlugin_EmptyBearerToken(t *testing.T) {
	ks := newTestKeyStore()
	p := New(ks, true)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Metadata["authorization"] = "Bearer "

	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("expected error for empty bearer token, got nil")
	}
	if result.Error.Status != http.StatusUnauthorized {
		t.Errorf("expected status 401, got %d", result.Error.Status)
	}
}

func TestPlugin_DisabledNoKeyStore(t *testing.T) {
	// Even with a nil keystore, disabled plugin should return Continue.
	p := New(nil, false)

	rc := models.AcquireRequestContext()
	defer rc.Release()

	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatalf("expected Continue when plugin is disabled (nil keystore), got action=%d", result.Action)
	}
}

func TestPlugin_NoModelNoProvider(t *testing.T) {
	// When Model and Provider are empty, model/provider access checks should pass.
	ks := newTestKeyStore()
	p := New(ks, true)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Metadata["authorization"] = "Bearer sk-agentcc-test-key-123"
	// Model and Provider are both empty.

	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Fatalf("expected Continue when no model/provider set, got action=%d, err=%v", result.Action, result.Error)
	}
}
