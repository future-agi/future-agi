package credentials

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The case table Django's request-log ingestion tests against too
// (futureagi/agentcc/tests/test_request_log_credential_headers.py).
func TestIsHeader_TheSharedCaseTable(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "api_contracts", "gateway", "credential-headers.json"))
	if err != nil {
		t.Fatal(err)
	}
	var cases struct {
		Credential []string `json:"credential"`
		Plain      []string `json:"plain"`
	}
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases.Credential) == 0 || len(cases.Plain) == 0 {
		t.Fatal("the case table names no headers")
	}
	for _, name := range cases.Credential {
		for _, spelled := range []string{name, strings.ToLower(name), strings.ToUpper(name)} {
			if !IsHeader(spelled) {
				t.Errorf("IsHeader(%q) = false, want true", spelled)
			}
		}
	}
	for _, name := range cases.Plain {
		if IsHeader(name) {
			t.Errorf("IsHeader(%q) = true, want false", name)
		}
	}
}

// Every header on the named list is in the case table, so Django's copy of
// the list is tested against all of it.
func TestHeaderNames_AreAllInTheCaseTable(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "api_contracts", "gateway", "credential-headers.json"))
	if err != nil {
		t.Fatal(err)
	}
	var cases struct {
		Credential []string `json:"credential"`
	}
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	inTable := map[string]bool{}
	for _, name := range cases.Credential {
		inTable[strings.ToLower(name)] = true
	}
	for name := range headerNames {
		if !inTable[name] {
			t.Errorf("%s is on the named list but not in the case table", name)
		}
	}
}

func TestLooksLikeCredential(t *testing.T) {
	for _, tt := range []struct {
		name string
		want bool
	}{
		{"x-access-token", true},
		{"session_token", true},
		{"X-CSRFToken", true},
		{"token", true},
		{"x-token", true},
		{"client_secret", true},
		{"db_password", true},
		{"openai_api_key", true},
		{"private_key", true},
		{"aws_secret_access_key", true},
		{"Bearer", true},

		// A count of tokens is not a token.
		{"prompt_tokens", false},
		{"completion_tokens", false},
		{"x-ratelimit-remaining-tokens", false},
		{"max_tokens", false},
		// Identity and plain dimensions.
		{"auth_type", false},
		{"auth_allowed_providers", false},
		{"key_org_id", false},
		{"cache_key", false},
		{"x-agentcc-cache-key", false},
		{"author", false},
		{"session_id", false},
		{"x-goog-api-client", false},

		// Names a caller sends a key in, and names of counts and lookup keys that
		// carry none.
		{"x-openai-key", true},
		{"X-Anthropic-Key", true},
		{"x-key", true},
		{"X-Auth", true},
		{"Helicone-Auth", true},
		{"X-Functions-Key", true},
		{"X-Portkey-Virtual-Key", true},
		{"x-portkey-config", true},
		{"x_portkey_api_key", true},
		{"x_portkey-trace-id", true},
		{"X-Master-Key", true},
		{"X-License-Key", true},
		{"tenant_key", true},

		{"Tokenizer", false},
		{"X-Token-Count", false},
		{"token_budget", false},
		{"Idempotency-Key", false},
		{"x-idempotency-key", false},
		{"cache_key", false},
		{"key", false},
		{"auth", true},
		{"oauth_scope", false},
		{"x-portkey", false},

		// "token" names a token in any word, not only in the last one. Only a
		// count of tokens (token followed by a count word, a word ending in
		// "tokens") and a tokenizer are left out.
		{"X-Access-Token-V2", true},
		{"access_token_2", true},
		{"slack_token_prod", true},
		{"github_token_1", true},
		{"X-Refresh-Token-Encrypted", true},
		{"service_token_b64", true},
		{"id_token_hint", true},
		{"X-Session-Token-Id", true},
		{"my_auth_token_2", true},
		{"x-token-value", true},
		{"X-TokenValue", true},
		{"x-tokenid", true},
		{"csrftoken", true},
		{"total_token", true},
		{"token_type", true},
		{"x-tokenizer-token", true},

		{"token_count", false},
		{"token-budget", false},
		{"X-Token-Limit", false},
		{"token_usage", false},
		{"token-used", false},
		{"X-Token-Remaining", false},
		{"token_total", false},
		{"token-max", false},
		{"token_min", false},
		{"token-limit-remaining", false},
		{"x-tokenizer", false},
		{"tokenize", false},
		{"detokenization_mode", false},
		{"tokens_used", false},
	} {
		if got := LooksLikeCredential(tt.name); got != tt.want {
			t.Errorf("LooksLikeCredential(%q) = %v, want %v", tt.name, got, tt.want)
		}
	}
}

// A lower-cased name is checked without allocating: the guardrail plugin
// checks every request-metadata key on each stage that runs a check.
func TestLooksLikeCredential_DoesNotAllocateOnALowerCaseName(t *testing.T) {
	names := []string{"auth_key_id", "prompt_tokens", "x-agentcc-cache-key", "session_token", "org_id",
		"x-openai-key", "helicone-auth", "x-portkey-config", "x-token-count", "idempotency-key", "key",
		"x-access-token-v2", "x-tokenvalue", "token_budget", "tokenizer", "x_token_limit_remaining"}
	allocs := testing.AllocsPerRun(100, func() {
		for _, n := range names {
			_ = IsHeader(n)
		}
	})
	if allocs != 0 {
		t.Errorf("%v allocations per run, want 0", allocs)
	}
}

func TestWithoutCredentials(t *testing.T) {
	in := map[string]string{
		// Credentials: the caller's Authorization header, a caller's own
		// dimension and a key label that name one.
		"authorization":    "Bearer sk-agentcc-planted",
		"session_token":    "planted-session-token",
		"key_api_secret":   "planted-label",
		"x-goog-api-key":   "planted-google-key",
		"db_password":      "planted-password",
		"openai_api_key":   "planted-openai-key",
		"Authorization":    "Bearer planted-capitalised",
		"refresh_token":    "planted-refresh-token",
		"private_key_pem":  "planted-pem",
		"access_token_2":   "planted-access-token-2",
		"slack_token_prod": "planted-slack-token",

		// Not credentials: identity, usage and the request's dimensions.
		"auth_key_id":            "key_1",
		"auth_key_name":          "client",
		"auth_key_owner":         "owner@example.test",
		"auth_type":              "license",
		"auth_allowed_providers": "openai",
		"key_org_id":             "org_1",
		"key_access_groups":      "default",
		"org_id":                 "org_1",
		"client_ip":              "203.0.113.7",
		"prompt_tokens":          "12",
		"completion_tokens":      "3",
		"input_token_warning":    "approaching model input limit",
		"cache_key":              "abc",
		"team":                   "research",
		"token_budget":           "2000",
		"token_usage":            "42",
	}
	got := WithoutCredentials(in)
	for k := range in {
		_, kept := got[k]
		if want := !strings.Contains(in[k], "planted"); kept != want {
			t.Errorf("%s kept = %v, want %v", k, kept, want)
		}
	}
	if in["authorization"] == "" {
		t.Error("the input map was changed")
	}
	if WithoutCredentials(nil) != nil {
		t.Error("nil should stay nil")
	}
}
