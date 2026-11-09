package config

import (
	"crypto/rand"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Uses Load, including expansion and environment overrides; no provider or
// secret resolver is constructed. All credentials are generated per test.
func hostedFixture(t *testing.T) string {
	t.Helper()
	for _, name := range []string{"AGENTCC_ADMIN_TOKEN", "AGENTCC_INTERNAL_API_KEY", "AGENTCC_WEBHOOK_SECRET"} {
		b := make([]byte, 32)
		if _, err := rand.Read(b); err != nil {
			t.Fatal(err)
		}
		t.Setenv(name, hex.EncodeToString(b))
	}
	t.Setenv("AGENTCC_DEPLOYMENT_PROFILE", "hosted")
	t.Setenv("AGENTCC_HOSTED_ONEKEY_ACTIVATION", "")
	t.Setenv("AGENTCC_CONTROL_PLANE_URL", "http://backend:8000")
	t.Setenv("AGENTCC_SYNC_ON_STARTUP", "true")
	t.Setenv("AGENTCC_SYNC_INTERVAL", "60s")
	t.Setenv("AGENTCC_REDIS_ADDRESS", "redis:6379")
	return `auth: {enabled: true}
guardrails: {enabled: true}
server:
  trusted_proxies: ["192.0.2.10/32"]
  max_request_timeout: 300s
redis: {enabled: true, address: "redis:6379", timeout: 1s}
mcp: {enabled: false}
tools: {enabled: false}
`
}

func loadHostedFixture(t *testing.T, text string) error {
	t.Helper()
	// Existing hosted fixtures explicitly opt into a velocity budget. Cap-policy
	// regressions supply their own managed block (including an empty one).
	if !strings.Contains(text, "\nmanaged:") {
		text += "managed: {spend_guard: {max_usd_per_minute: 5}}\n"
	}
	path := filepath.Join(t.TempDir(), "gateway.yaml")
	if err := os.WriteFile(path, []byte(text), 0600); err != nil {
		t.Fatal(err)
	}
	_, err := Load(path)
	return err
}

func TestHostedProfileValidAndRefusals(t *testing.T) {
	cases := []struct{ name, old, replacement, env, value string }{
		{name: "valid"},
		{name: "unknown profile", env: "AGENTCC_DEPLOYMENT_PROFILE", value: "hostde"},
		{name: "test endpoint override", env: "AGENTCC_TEST_ENDPOINT", value: "http://upstream.invalid"},
		{name: "loopback Redis", env: "AGENTCC_REDIS_ADDRESS", value: "127.0.0.1:6379"},
		{name: "invalid state key", env: "AGENTCC_MCP_STATE_KEYS", value: "short"},
		{name: "TLS bypass", old: "tools: {enabled: false}", replacement: "providers: {upstream: {base_url: 'https://upstream.invalid', api_format: openai, skip_tls: true}}"},
		{name: "auth off", env: "AGENTCC_AUTH_ENABLED", value: "false"},
		{name: "guardrails off", old: "guardrails: {enabled: true}", replacement: "guardrails: {enabled: false}"},
		{name: "no proxy", old: "192.0.2.10/32", replacement: ""},
		{name: "all proxies", old: "192.0.2.10/32", replacement: "0.0.0.0/0"},
		{name: "missing admin", env: "AGENTCC_ADMIN_TOKEN"},
		{name: "missing internal", env: "AGENTCC_INTERNAL_API_KEY"},
		{name: "missing webhook", env: "AGENTCC_WEBHOOK_SECRET"},
		{name: "no periodic sync", env: "AGENTCC_SYNC_INTERVAL", value: "0s"},
		{name: "no initial sync", env: "AGENTCC_SYNC_ON_STARTUP", value: "false"},
		{name: "insecure admin override", env: "AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN", value: "true"},
		{name: "private provider override", env: "AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS", value: "true"},
		{name: "development state", old: "mcp: {enabled: false}", replacement: "mcp: {enabled: false, dev_insecure_state: true}"},
		{name: "unapproved MCP", old: "mcp: {enabled: false}", replacement: "mcp: {enabled: true, dev_insecure_state: true}"},
		{name: "tools", old: "tools: {enabled: false}", replacement: "tools: {enabled: true}"},
		{name: "credits provider", old: "tools: {enabled: false}", replacement: "providers: {paid: {base_url: 'https://upstream.invalid', api_format: openai, managed: true}}"},
		{name: "broad private egress", old: "mcp: {enabled: false}", replacement: "mcp: {enabled: false, egress: {private_allow: ['0.0.0.0/0']}}"},
		{name: "unbounded request", old: "300s", replacement: "24h"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			text := hostedFixture(t)
			if tc.old != "" {
				text = strings.Replace(text, tc.old, tc.replacement, 1)
			}
			if tc.env != "" {
				t.Setenv(tc.env, tc.value)
			}
			err := loadHostedFixture(t, text)
			if tc.name == "valid" {
				if err != nil {
					t.Fatal(err)
				}
				return
			}
			if err == nil {
				t.Fatal("hosted configuration accepted an unsafe setting")
			}
			for _, name := range []string{"AGENTCC_ADMIN_TOKEN", "AGENTCC_INTERNAL_API_KEY", "AGENTCC_WEBHOOK_SECRET"} {
				if value := os.Getenv(name); value != "" && strings.Contains(err.Error(), value) {
					t.Fatal("error exposed credential")
				}
			}
		})
	}
}

func TestHostedProfileSelfHostedUnchanged(t *testing.T) {
	t.Setenv("AGENTCC_DEPLOYMENT_PROFILE", "self-hosted")
	if _, err := Load(""); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AGENTCC_DEPLOYMENT_PROFILE", "")
	if _, err := Load(""); err != nil {
		t.Fatal(err)
	}
}

func TestHostedProfileReloadRejectsChangedEnvironment(t *testing.T) {
	text := hostedFixture(t)
	if err := loadHostedFixture(t, text); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN", "true")
	if err := loadHostedFixture(t, text); err == nil {
		t.Fatal("reload accepted development override")
	}
}

func TestHostedProfileAllowsOnlyTheOperatorBackendForSavedEvalRules(t *testing.T) {
	text := hostedFixture(t)
	t.Setenv(EnvMCPEgressPrivateAllow, "backend:8000")
	if err := loadHostedFixture(t, text); err != nil {
		t.Fatal(err)
	}
	t.Setenv(EnvMCPEgressPrivateAllow, "other-backend:8000")
	if err := loadHostedFixture(t, text); err == nil {
		t.Fatal("unrelated private destination accepted")
	}
}
