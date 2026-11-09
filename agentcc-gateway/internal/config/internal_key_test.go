package config

import (
	"crypto/rand"
	"encoding/hex"
	"math/big"
	"path/filepath"
	"strings"
	"testing"
)

// internalKeyEnv lists every variable these tests read; each test starts with
// all of them unset, so a value exported in the developer's shell cannot leak in.
var internalKeyEnv = append([]string{
	"AGENTCC_INTERNAL_API_KEY",
	"AGENTCC_DEPLOYMENT_PROFILE",
	"AGENTCC_HOSTED_ONEKEY_ACTIVATION",
}, adminTokenEnv...)

// hex32Key is what bin/install (bin/lib/secrets.sh gen_secret) and
// deploy/setup.sh write: `openssl rand -hex 32`, 64 hex characters.
func hex32Key(t *testing.T) string {
	t.Helper()
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		t.Fatal(err)
	}
	return hex.EncodeToString(b)
}

// alnum48Key is what the Helm chart generates (templates/secret.yaml:
// randAlphaNum 48).
func alnum48Key(t *testing.T) string {
	t.Helper()
	const alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
	out := make([]byte, 48)
	for i := range out {
		n, err := rand.Int(rand.Reader, big.NewInt(int64(len(alphabet))))
		if err != nil {
			t.Fatal(err)
		}
		out[i] = alphabet[n.Int64()]
	}
	return string(out)
}

func seededInternalKey(cfg *Config, key string) *AuthKeyConfig {
	for i := range cfg.Auth.Keys {
		if cfg.Auth.Keys[i].Key == key && cfg.Auth.Keys[i].KeyType == "internal" {
			return &cfg.Auth.Keys[i]
		}
	}
	return nil
}

// requireInternalKeyRefused asserts the error an insecure internal key gives:
// it names the variable, says what to set, and never quotes the value.
func requireInternalKeyRefused(t *testing.T, err error, key string) {
	t.Helper()
	if err == nil {
		t.Fatalf("Load accepted AGENTCC_INTERNAL_API_KEY=%q outside hosted; want a startup error", key)
	}
	msg := err.Error()
	for _, want := range []string{"AGENTCC_INTERNAL_API_KEY", "openssl rand -hex 32", ".env", "AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN"} {
		if !strings.Contains(msg, want) {
			t.Errorf("error %q does not mention %q", msg, want)
		}
	}
	// A .env file is read as text: AGENTCC_INTERNAL_API_KEY=$(openssl rand
	// -hex 32) there is that literal string. The error says to run the command
	// and paste its output, and never shows a command substitution to copy.
	for _, want := range []string{"paste", "does not run commands"} {
		if !strings.Contains(msg, want) {
			t.Errorf("error %q does not say to paste the command's output (%q)", msg, want)
		}
	}
	if strings.Contains(msg, "$(") {
		t.Errorf("error %q shows a command substitution, which .env keeps as literal text", msg)
	}
	if trimmed := strings.TrimSpace(key); len(trimmed) >= 8 && strings.Contains(strings.ToLower(msg), strings.ToLower(trimmed)) {
		t.Errorf("error %q quotes the key", msg)
	}
}

// AGENTCC_INTERNAL_API_KEY is seeded as an "internal" key (config.go), which
// may use the gateway's own, operator-credentialed providers. The compose
// files fall back to a default published in this repository, so a gateway
// that starts with it (or another published default, or the installer's
// CHANGEME- placeholder) lets anyone who can reach its port spend the
// operator's provider keys. Outside hosted (which refuses it on its own), the
// gateway must not start with one unless the dev-only opt-in is set.
func TestLoad_RefusesPublishedOrPlaceholderInternalKey(t *testing.T) {
	cases := []struct{ name, key string }{
		{"the compose default", "local-dev-only-shared-secret-replace-me"},
		{"the compose default, upper case and padded", "  LOCAL-DEV-ONLY-SHARED-SECRET-REPLACE-ME \n"},
		{"the compose default with a suffix", "local-dev-only-shared-secret-replace-me-2026-10-31"},
		{"the futureagi/.env.example default", "fi-internal-dev-key-change-in-prod"},
		{"another published default inside a longer key", "prod-agentcc-admin-secret-7f3a9c2e1b4d6f8a0c2e"},
		{"the installer placeholder", "CHANGEME-set-by-bin-install"},
		{"a lower-case CHANGEME placeholder", "changeme-0123456789abcdef"},
		{"blank after trimming", "   "},
		{"only a tab and a newline", "\t\n"},
		// The command, not its output: what a .env holds when someone writes
		// AGENTCC_INTERNAL_API_KEY=$(openssl rand -hex 32) in it, a string
		// anyone who has read an install guide knows.
		{"an unrun command substitution", "$(openssl rand -hex 32)"},
		{"an unrun command substitution, upper case", "$(OPENSSL RAND -HEX 32)"},
		{"an unrun backquoted command", "`openssl rand -base64 48`"},
	}
	for _, published := range publishedAdminTokens {
		cases = append(cases, struct{ name, key string }{"published: " + published, published})
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			unsetEnv(t, internalKeyEnv...)
			t.Setenv("AGENTCC_INTERNAL_API_KEY", tc.key)

			_, err := Load("")
			requireInternalKeyRefused(t, err, tc.key)

			for _, optIn := range []string{"1", "true"} {
				t.Setenv("AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN", optIn)
				cfg, err := Load("")
				if err != nil {
					t.Fatalf("with AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN=%s, Load error: %v", optIn, err)
				}
				if seededInternalKey(cfg, tc.key) == nil {
					t.Errorf("with AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN=%s the key was not seeded", optIn)
				}
			}
		})
	}
}

// The refusal is about values anyone could know, not about strength: the keys
// bin/install and Helm generate, and keys operators chose themselves (however
// short), keep working, and a gateway without an internal key is unchanged.
func TestLoad_AcceptsGeneratedOperatorOrAbsentInternalKey(t *testing.T) {
	t.Run("unset: no internal key is seeded", func(t *testing.T) {
		unsetEnv(t, internalKeyEnv...)
		cfg, err := Load("")
		if err != nil {
			t.Fatalf("Load error: %v", err)
		}
		for _, k := range cfg.Auth.Keys {
			if k.KeyType == "internal" {
				t.Errorf("seeded internal key %q without AGENTCC_INTERNAL_API_KEY", k.Name)
			}
		}
	})
	generated := map[string]func(*testing.T) string{
		"openssl rand -hex 32 (bin/install, deploy/setup.sh)": hex32Key,
		"randAlphaNum 48 (Helm)":                              alnum48Key,
	}
	for name, gen := range generated {
		t.Run(name, func(t *testing.T) {
			for i := 0; i < 50; i++ {
				unsetEnv(t, internalKeyEnv...)
				key := gen(t)
				t.Setenv("AGENTCC_INTERNAL_API_KEY", key)
				cfg, err := Load("")
				if err != nil {
					t.Fatalf("Load error for a generated key: %v", err)
				}
				if seededInternalKey(cfg, key) == nil {
					t.Fatal("generated key was not seeded as the internal key")
				}
			}
		})
	}
	for _, key := range []string{
		"sk-agentcc-internal-test",   // internal/server tests
		"test-internal-key",          // config_test.go
		"k3y",                        // short: no length rule for this key
		strings.Repeat("a", 64),      // no distinct-character rule either
		"9f2c4e7a1b8d3f6e0a5c2b9d4e", // shorter than an admin token may be
	} {
		t.Run("operator key "+key, func(t *testing.T) {
			unsetEnv(t, internalKeyEnv...)
			t.Setenv("AGENTCC_INTERNAL_API_KEY", key)
			cfg, err := Load("")
			if err != nil {
				t.Fatalf("Load error: %v", err)
			}
			if seededInternalKey(cfg, key) == nil {
				t.Fatal("operator key was not seeded as the internal key")
			}
		})
	}
}

func requireNoInternalKey(t *testing.T, cfg *Config) {
	t.Helper()
	for _, k := range cfg.Auth.Keys {
		if k.KeyType == "internal" {
			t.Errorf("seeded internal key %q from an empty AGENTCC_INTERNAL_API_KEY", k.Name)
		}
	}
}

// Set but empty is not unset. futureagi/docker-compose.yml passes
// AGENTCC_INTERNAL_API_KEY=${AGENTCC_INTERNAL_API_KEY:-} to the gateway, so a
// .env without the key hands it "". Nothing is seeded then, so nothing turns
// authentication on: with the shipped config.example.yaml (no auth block) the
// gateway, on its published port, would serve every caller from the
// providers configured with the operator's keys. Only a variable that is not
// in the environment at all means "no internal key".
func TestLoad_RefusesInternalKeySetButEmpty(t *testing.T) {
	t.Run("refused outside hosted", func(t *testing.T) {
		unsetEnv(t, internalKeyEnv...)
		t.Setenv("AGENTCC_INTERNAL_API_KEY", "")
		_, err := Load("")
		requireInternalKeyRefused(t, err, "")
		if err != nil && !strings.Contains(err.Error(), "empty") {
			t.Errorf("error %q does not say the variable is empty", err)
		}
	})
	t.Run("the dev opt-in starts it, seeding nothing", func(t *testing.T) {
		for _, optIn := range []string{"1", "true"} {
			unsetEnv(t, internalKeyEnv...)
			t.Setenv("AGENTCC_INTERNAL_API_KEY", "")
			t.Setenv("AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN", optIn)
			cfg, err := Load("")
			if err != nil {
				t.Fatalf("with AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN=%s, Load error: %v", optIn, err)
			}
			requireNoInternalKey(t, cfg)
		}
	})
	t.Run("unset still loads, seeding nothing", func(t *testing.T) {
		unsetEnv(t, internalKeyEnv...)
		cfg, err := Load("")
		if err != nil {
			t.Fatalf("Load error: %v", err)
		}
		requireNoInternalKey(t, cfg)
	})

	// The stock gateway config, wired as the compose files wire it (a strong
	// admin token, the backend as control plane): an explicitly empty key must
	// not leave its authentication off.
	stock := filepath.Join("..", "..", "..", "agentcc-gateway", "config.example.yaml")
	stockEnv := func(t *testing.T) {
		unsetEnv(t, internalKeyEnv...)
		t.Setenv("AGENTCC_ADMIN_TOKEN", strongToken)
		t.Setenv("AGENTCC_CONTROL_PLANE_URL", "http://backend")
		t.Setenv("AGENTCC_CONTROL_PLANE_TOKEN", strongToken)
		t.Setenv("AGENTCC_INTERNAL_API_KEY", "")
	}
	t.Run("stock config: explicit empty is refused", func(t *testing.T) {
		stockEnv(t)
		cfg, err := Load(stock)
		if err == nil {
			t.Fatalf("Load(config.example.yaml) with AGENTCC_INTERNAL_API_KEY=\"\" started; auth enabled = %v, %d keys",
				cfg.Auth.Enabled, len(cfg.Auth.Keys))
		}
		requireInternalKeyRefused(t, err, "")
	})
	// What the refusal prevents: under the dev opt-in the same gateway starts
	// with authentication off and no internal key.
	t.Run("stock config: what the opt-in starts", func(t *testing.T) {
		stockEnv(t)
		t.Setenv("AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN", "1")
		cfg, err := Load(stock)
		if err != nil {
			t.Fatalf("with the dev opt-in, Load error: %v", err)
		}
		if cfg.Auth.Enabled {
			t.Errorf("stock config with an empty key: auth enabled; this test documents that it is off")
		}
		requireNoInternalKey(t, cfg)
	})
	t.Run("stock config: a generated key turns auth on", func(t *testing.T) {
		stockEnv(t)
		key := hex32Key(t)
		t.Setenv("AGENTCC_INTERNAL_API_KEY", key)
		cfg, err := Load(stock)
		if err != nil {
			t.Fatalf("Load error: %v", err)
		}
		if !cfg.Auth.Enabled || seededInternalKey(cfg, key) == nil {
			t.Errorf("a generated key must seed the internal key and turn auth on (enabled = %v)", cfg.Auth.Enabled)
		}
	})
}

// Hosted keeps its own, stricter rule (validateHosted: the admin-token policy,
// length included) and its own error; the dev opt-in stays refused there.
func TestLoad_HostedInternalKeyRuleUnchanged(t *testing.T) {
	t.Run("valid hosted fixture still loads", func(t *testing.T) {
		unsetEnv(t, internalKeyEnv...)
		if err := loadHostedFixture(t, hostedFixture(t)); err != nil {
			t.Fatal(err)
		}
	})
	t.Run("published default gets the hosted error", func(t *testing.T) {
		unsetEnv(t, internalKeyEnv...)
		text := hostedFixture(t)
		t.Setenv("AGENTCC_INTERNAL_API_KEY", "local-dev-only-shared-secret-replace-me")
		err := loadHostedFixture(t, text)
		if err == nil || !strings.HasSuffix(err.Error(), "hosted profile requires valid AGENTCC_INTERNAL_API_KEY") {
			t.Fatalf("error = %v, want the hosted profile's own refusal", err)
		}
	})
}
