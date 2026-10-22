package credentials

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// caseTable is api_contracts/gateway/credential-headers.json, the case table
// Django's request-log ingestion tests against too
// (futureagi/agentcc/tests/test_request_log_credential_headers.py).
type caseTable struct {
	Credential []string `json:"credential"`
	Plain      []string `json:"plain"`
}

func loadCaseTable(t *testing.T) caseTable {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "api_contracts", "gateway", "credential-headers.json"))
	if err != nil {
		t.Fatal(err)
	}
	var cases caseTable
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases.Credential) == 0 || len(cases.Plain) == 0 {
		t.Fatal("the case table names no headers")
	}
	return cases
}

func TestIsHeader_TheSharedCaseTable(t *testing.T) {
	cases := loadCaseTable(t)
	for _, name := range cases.Credential {
		for _, spelled := range []string{name, strings.ToLower(name), strings.ToUpper(name)} {
			if !IsHeader(spelled) {
				t.Errorf("IsHeader(%q) = false, want true", spelled)
			}
		}
	}
	for _, name := range cases.Plain {
		for _, spelled := range []string{name, strings.ToLower(name), strings.ToUpper(name)} {
			if IsHeader(spelled) {
				t.Errorf("IsHeader(%q) = true, want false", spelled)
			}
		}
	}
}

// Every header on the named list is in the case table, so Django's copy of
// the list is tested against all of it.
func TestHeaderNames_AreAllInTheCaseTable(t *testing.T) {
	cases := loadCaseTable(t)
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

// Edge cases: names that hold a credential, and names that only count or
// describe tokens.
func TestIsHeader_EdgeCaseTable(t *testing.T) {
	for _, tt := range []struct {
		name string
		want bool
	}{
		{"X-OpenAI-Key", true},
		{"X-Anthropic-Key", true},
		{"x-key", true},
		{"X-Auth", true},
		{"Helicone-Auth", true},
		{"X-Functions-Key", true},
		{"X-Portkey-Virtual-Key", true},
		{"X-Portkey-Api-Key", true},
		{"X-Portkey-Config", true},
		{"X-Master-Key", true},
		{"X-License-Key", true},

		{"Tokenizer", false},
		{"X-Token-Count", false},
		{"token_budget", false},
		{"X-Agentcc-Cache-Key", false},
		{"Idempotency-Key", false},
	} {
		if got := IsHeader(tt.name); got != tt.want {
			t.Errorf("IsHeader(%q) = %v, want %v", tt.name, got, tt.want)
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
		{"auth.token", true},
		{"client_secret", true},
		{"db_password", true},
		{"openai_api_key", true},
		{"openai_key", true},
		{"private_key", true},
		{"aws_secret_access_key", true},
		{"Bearer", true},
		{"x_portkey_virtual_key", true},
		{"proxy_auth", true},
		{"x-oauth", true},

		// A count of tokens, or a word about tokens, is not a token.
		{"prompt_tokens", false},
		{"completion_tokens", false},
		{"x-ratelimit-remaining-tokens", false},
		{"max_tokens", false},
		{"tokenizer", false},
		{"x-token-count", false},
		{"token_budget", false},
		{"input_token_warning", false},
		// A key that opens nothing, and a bare "key" dimension.
		{"cache_key", false},
		{"x-agentcc-cache-key", false},
		{"x_agentcc_cache_key", false},
		{"idempotency_key", false},
		{"key", false},
		{"key_org_id", false},
		// Identity and plain dimensions.
		{"auth_type", false},
		{"auth_allowed_providers", false},
		{"author", false},
		{"session_id", false},
		{"x-goog-api-client", false},
		{"org_rate_limit_per_key_rpm", false},
	} {
		if got := LooksLikeCredential(tt.name); got != tt.want {
			t.Errorf("LooksLikeCredential(%q) = %v, want %v", tt.name, got, tt.want)
		}
	}
}

// A lower-cased name is checked without allocating: the request log checks
// every header of every request it keeps.
func TestLooksLikeCredential_DoesNotAllocateOnALowerCaseName(t *testing.T) {
	names := []string{
		"auth_key_id", "prompt_tokens", "x-agentcc-cache-key", "session_token", "org_id",
		"x-openai-key", "helicone-auth", "x-portkey-config", "x-token-count", "idempotency-key",
	}
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
		"authorization":   "Bearer sk-agentcc-planted",
		"session_token":   "planted-session-token",
		"key_api_secret":  "planted-label",
		"x-goog-api-key":  "planted-google-key",
		"db_password":     "planted-password",
		"openai_api_key":  "planted-openai-key",
		"Authorization":   "Bearer planted-capitalised",
		"refresh_token":   "planted-refresh-token",
		"private_key_pem": "planted-pem",
		"openai_key":      "planted-openai-label",
		"helicone_auth":   "planted-helicone",

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
		"token_budget":           "5000",
		"key":                    "value",
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
