package secrets

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// --- IsSecretURI tests ---

func TestIsSecretURI(t *testing.T) {
	tests := []struct {
		value string
		want  bool
	}{
		{"vault://secret/data/agentcc/openai#api_key", true},
		{"aws-sm://agentcc/openai-key", true},
		{"gcp-sm://projects/p/secrets/s/versions/latest", true},
		{"azure-kv://my-vault/secrets/my-key", true},
		{"sk-agentcc-1234", false},
		{"", false},
		{"vault://", true}, // has scheme, but Resolve will reject empty path
		{"http://example.com", false},
	}

	for _, tt := range tests {
		got := IsSecretURI(tt.value)
		if got != tt.want {
			t.Errorf("IsSecretURI(%q) = %v, want %v", tt.value, got, tt.want)
		}
	}
}

// --- ParseURI tests ---

func TestParseURI(t *testing.T) {
	tests := []struct {
		uri      string
		scheme   string
		path     string
		fragment string
		wantErr  bool
	}{
		{"vault://secret/data/agentcc/openai#api_key", "vault", "secret/data/agentcc/openai", "api_key", false},
		{"vault://secret/data/agentcc/openai", "vault", "secret/data/agentcc/openai", "", false},
		{"aws-sm://my-secret", "aws-sm", "my-secret", "", false},
		{"aws-sm://my-secret#password", "aws-sm", "my-secret", "password", false},
		{"gcp-sm://projects/p/secrets/s/versions/latest", "gcp-sm", "projects/p/secrets/s/versions/latest", "", false},
		{"azure-kv://vault-name/secrets/key-name", "azure-kv", "vault-name/secrets/key-name", "", false},
		{"bad-scheme://foo", "", "", "", true},
		{"not-a-uri", "", "", "", true},
		{"vault://", "", "", "", true}, // empty path
	}

	for _, tt := range tests {
		scheme, path, fragment, err := ParseURI(tt.uri)
		if tt.wantErr {
			if err == nil {
				t.Errorf("ParseURI(%q): expected error", tt.uri)
			}
			continue
		}
		if err != nil {
			t.Errorf("ParseURI(%q): unexpected error: %v", tt.uri, err)
			continue
		}
		if scheme != tt.scheme || path != tt.path || fragment != tt.fragment {
			t.Errorf("ParseURI(%q) = (%q, %q, %q), want (%q, %q, %q)",
				tt.uri, scheme, path, fragment, tt.scheme, tt.path, tt.fragment)
		}
	}
}

// --- Resolver tests ---

type mockBackend struct {
	values map[string]string
}

func (m *mockBackend) Resolve(uri string) (string, error) {
	_, path, _, _ := ParseURI(uri)
	if val, ok := m.values[path]; ok {
		return val, nil
	}
	return "", fmt.Errorf("not found: %s", path)
}

func TestResolver_Resolve(t *testing.T) {
	r := NewResolver(map[string]Backend{
		"vault": &mockBackend{values: map[string]string{
			"secret/data/openai": "sk-openai-123",
		}},
		"aws-sm": &mockBackend{values: map[string]string{
			"my-secret": "aws-secret-value",
		}},
	})

	val, err := r.Resolve("vault://secret/data/openai")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if val != "sk-openai-123" {
		t.Fatalf("expected sk-openai-123, got %s", val)
	}

	val2, err := r.Resolve("aws-sm://my-secret")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if val2 != "aws-secret-value" {
		t.Fatalf("expected aws-secret-value, got %s", val2)
	}
}

func TestResolver_UnsupportedScheme(t *testing.T) {
	r := NewResolver(map[string]Backend{})
	_, err := r.Resolve("vault://secret/data/openai")
	if err == nil {
		t.Fatal("expected error for unsupported scheme")
	}
}

func TestResolver_NotAURI(t *testing.T) {
	r := NewResolver(map[string]Backend{})
	_, err := r.Resolve("sk-plain-key")
	if err == nil {
		t.Fatal("expected error for non-URI")
	}
}

// --- Vault Backend tests (with httptest) ---

func TestVaultBackend_Resolve(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/secret/data/agentcc/openai" {
			http.NotFound(w, r)
			return
		}
		if r.Header.Get("X-Vault-Token") != "test-token" {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"data": map[string]interface{}{
				"data": map[string]interface{}{
					"api_key": "sk-openai-from-vault",
					"extra":   "ignored",
				},
			},
		})
	}))
	defer ts.Close()

	backend, err := NewVaultBackend(VaultConfig{
		Address: ts.URL,
		Token:   "test-token",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	val, err := backend.Resolve("vault://secret/data/agentcc/openai#api_key")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if val != "sk-openai-from-vault" {
		t.Fatalf("expected sk-openai-from-vault, got %s", val)
	}
}

func TestVaultBackend_SingleField_NoFragment(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"data": map[string]interface{}{
				"data": map[string]interface{}{
					"value": "single-value",
				},
			},
		})
	}))
	defer ts.Close()

	backend, err := NewVaultBackend(VaultConfig{Address: ts.URL, Token: "t"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	val, err := backend.Resolve("vault://secret/data/single")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if val != "single-value" {
		t.Fatalf("expected single-value, got %s", val)
	}
}

func TestVaultBackend_MultipleFields_RequiresFragment(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"data": map[string]interface{}{
				"data": map[string]interface{}{
					"key1": "val1",
					"key2": "val2",
				},
			},
		})
	}))
	defer ts.Close()

	backend, err := NewVaultBackend(VaultConfig{Address: ts.URL, Token: "t"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	_, err = backend.Resolve("vault://secret/data/multi")
	if err == nil {
		t.Fatal("expected error when multiple fields and no fragment")
	}
}

func TestVaultBackend_NotFound(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.NotFound(w, r)
	}))
	defer ts.Close()

	backend, err := NewVaultBackend(VaultConfig{Address: ts.URL, Token: "t"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	_, err = backend.Resolve("vault://secret/data/missing")
	if err == nil {
		t.Fatal("expected error for not found")
	}
}

func TestVaultBackend_MissingAddress(t *testing.T) {
	_, err := NewVaultBackend(VaultConfig{Token: "t"})
	if err == nil {
		t.Fatal("expected error for missing address")
	}
}

func TestVaultBackend_MissingToken(t *testing.T) {
	_, err := NewVaultBackend(VaultConfig{Address: "http://localhost:8200"})
	if err == nil {
		t.Fatal("expected error for missing token")
	}
}

func TestVaultBackend_AppRoleLogin(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/auth/approle/login" {
			json.NewEncoder(w).Encode(map[string]interface{}{
				"auth": map[string]interface{}{
					"client_token": "approle-token",
				},
			})
			return
		}
		// Verify the token from AppRole login is used.
		if r.Header.Get("X-Vault-Token") != "approle-token" {
			http.Error(w, "wrong token", http.StatusForbidden)
			return
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"data": map[string]interface{}{
				"data": map[string]interface{}{
					"key": "from-approle",
				},
			},
		})
	}))
	defer ts.Close()

	backend, err := NewVaultBackend(VaultConfig{
		Address:       ts.URL,
		AppRoleID:     "role-id",
		AppRoleSecret: "secret-id",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	val, err := backend.Resolve("vault://secret/data/test#key")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if val != "from-approle" {
		t.Fatalf("expected from-approle, got %s", val)
	}
}

// --- ResolveProviderSecrets tests ---

func TestResolveProviderSecrets_NoSecrets(t *testing.T) {
	cfg := &config.Config{
		Providers: map[string]config.ProviderConfig{
			"openai": {BaseURL: "https://api.openai.com", APIKey: "sk-plain-key", APIFormat: "openai"},
		},
	}

	err := ResolveProviderSecrets(cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if cfg.Providers["openai"].APIKey != "sk-plain-key" {
		t.Fatal("plain key should not be modified")
	}
}

func TestResolveProviderSecrets_WithVault(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"data": map[string]interface{}{
				"data": map[string]interface{}{
					"api_key": "resolved-from-vault",
				},
			},
		})
	}))
	defer ts.Close()

	cfg := &config.Config{
		Providers: map[string]config.ProviderConfig{
			"openai": {BaseURL: "https://api.openai.com", APIKey: "vault://secret/data/openai#api_key", APIFormat: "openai"},
			"anthropic": {BaseURL: "https://api.anthropic.com", APIKey: "sk-plain", APIFormat: "anthropic"},
		},
		Secrets: config.SecretsConfig{
			Vault: config.VaultSecretsConfig{
				Address: ts.URL,
				Token:   "test-token",
			},
		},
	}

	err := ResolveProviderSecrets(cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if cfg.Providers["openai"].APIKey != "resolved-from-vault" {
		t.Fatalf("expected resolved-from-vault, got %s", cfg.Providers["openai"].APIKey)
	}
	if cfg.Providers["anthropic"].APIKey != "sk-plain" {
		t.Fatal("plain key should not be modified")
	}
}
