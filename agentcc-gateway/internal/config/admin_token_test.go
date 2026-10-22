package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// strongToken is 64 hex characters, the shape `openssl rand -hex 32` prints.
const strongToken = "9f2c4e7a1b8d3f6e0a5c2b9d4e7f1a3c6b8d0e2f4a6c8e1b3d5f7a9c2e4b6d8f"

// adminTokenEnv lists every variable these tests read; each test starts with
// all of them unset, so a value exported in the developer's shell never applies.
var adminTokenEnv = []string{
	"AGENTCC_ADMIN_TOKEN",
	"AGENTCC_CONTROL_PLANE_URL",
	"AGENTCC_CONTROL_PLANE_TOKEN",
	"AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN",
	"AGENTCC_TEST_UNSET_ADMIN_TOKEN",
}

// unsetEnv unsets each variable for the rest of the test and restores it
// afterwards (t.Setenv registers the restore, Unsetenv then removes it).
func unsetEnv(t *testing.T, names ...string) {
	t.Helper()
	for _, name := range names {
		t.Setenv(name, "")
		if err := os.Unsetenv(name); err != nil {
			t.Fatalf("unsetting %s: %v", name, err)
		}
	}
}

func writeConfig(t *testing.T, content string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

// requireInsecureTokenRefused asserts the error a weak token gives: it names
// the setting, tells the operator how to fix it, and never echoes the token.
func requireInsecureTokenRefused(t *testing.T, err error, field, token string) {
	t.Helper()
	if err == nil {
		t.Fatalf("Load accepted an insecure %s; want a startup error", field)
	}
	msg := err.Error()
	for _, want := range []string{field, "AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN", "32"} {
		if !strings.Contains(msg, want) {
			t.Errorf("error %q does not mention %q", msg, want)
		}
	}
	if len(strings.TrimSpace(token)) >= 8 && strings.Contains(msg, strings.TrimSpace(token)) {
		t.Errorf("error %q echoes the token", msg)
	}
}

// The admin token guards the key, org-config, credit and cluster admin APIs.
// A weak token (a published default, a placeholder, a short one)
// or an admin block whose ${VAR} expanded to nothing must stop the gateway at
// start-up, unless the operator opts in for local development.
func TestLoad_RefusesInsecureAdminToken(t *testing.T) {
	cases := []struct {
		name  string
		token string
	}{
		{"a published config default", "agentcc-admin-secret"},
		{"a published default, upper case and padded", "  AGENTCC-ADMIN-SECRET  "},
		{"the compose default", "local-dev-only-admin-token-replace-me"},
		{"the commented config.example.yaml value", "your-secret-admin-token"},
		{"the installer placeholder", "CHANGEME-set-by-bin-install-000000000000"},
		{"a long placeholder", "please-replace-me-with-a-real-admin-token-0001"},
		{"changeme", "changeme"},
		{"admin", "admin"},
		{"secret", "secret"},
		{"test", "test"},
		{"31 random characters", "q7Vd9kLm2Xp4Rt8Zw1Ny6Bc3Hf5Js0G"},
		{"one character repeated", strings.Repeat("a", 64)},
		// A published default padded out to the length rule, the fix an
		// operator reaches for after the length error, is still refused.
		{"a published default padded to 32 characters", "agentcc-admin-secret000000000000"},
		{"a published default twice", "agentcc-admin-secret-agentcc-admin-secret"},
		{"the compose default with a suffix", "local-dev-only-admin-token-replace-me-2026-10-16"},
		{"a published default inside a longer token", "prod-test-admin-secret-7f3a9c2e1b4d6f8a0c2e"},
	}
	if n := len(cases[10].token); n != 31 {
		t.Fatalf("the short-token case is %d characters, want 31", n)
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			unsetEnv(t, adminTokenEnv...)
			path := writeConfig(t, "admin:\n  token: \""+tc.token+"\"\n")

			_, err := Load(path)
			requireInsecureTokenRefused(t, err, "admin.token", tc.token)

			t.Setenv("AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN", "1")
			cfg, err := Load(path)
			if err != nil {
				t.Fatalf("with AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN=1, Load error: %v", err)
			}
			if cfg.Admin.Token != tc.token {
				t.Errorf("admin token = %q, want %q", cfg.Admin.Token, tc.token)
			}
		})
	}
}

// A config that reads `token: "${AGENTCC_ADMIN_TOKEN}"` with the variable unset gives an admin block with an empty token, and Load refuses it at start-up unless the dev-only opt-in is set.
func TestLoad_RefusesEmptyConfiguredAdminToken(t *testing.T) {
	t.Run("admin.token from an unset variable", func(t *testing.T) {
		unsetEnv(t, adminTokenEnv...)
		path := writeConfig(t, "admin:\n  token: \"${AGENTCC_TEST_UNSET_ADMIN_TOKEN}\"\n")
		_, err := Load(path)
		requireInsecureTokenRefused(t, err, "admin.token", "")
		if err != nil && !strings.Contains(err.Error(), "empty") {
			t.Errorf("error %q does not say the token is empty", err)
		}
	})
	t.Run("AGENTCC_ADMIN_TOKEN set but empty", func(t *testing.T) {
		unsetEnv(t, adminTokenEnv...)
		t.Setenv("AGENTCC_ADMIN_TOKEN", "")
		_, err := Load("")
		requireInsecureTokenRefused(t, err, "admin.token", "")
	})
	t.Run("the dev flag allows it", func(t *testing.T) {
		unsetEnv(t, adminTokenEnv...)
		t.Setenv("AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN", "true")
		path := writeConfig(t, "admin:\n  token: \"${AGENTCC_TEST_UNSET_ADMIN_TOKEN}\"\n")
		if _, err := Load(path); err != nil {
			t.Fatalf("Load error: %v", err)
		}
	})
}

// The control-plane token is held to the same policy as the admin token.
func TestLoad_RefusesInsecureControlPlaneToken(t *testing.T) {
	// A config with both tokens as literals: AGENTCC_ADMIN_TOKEN overrides admin.token,
	// but control_plane.admin_token keeps its literal unless
	// AGENTCC_CONTROL_PLANE_TOKEN is set too.
	t.Run("literal tokens with only AGENTCC_ADMIN_TOKEN overridden", func(t *testing.T) {
		unsetEnv(t, adminTokenEnv...)
		t.Setenv("AGENTCC_ADMIN_TOKEN", strongToken)
		path := writeConfig(t, `
admin:
  token: "agentcc-admin-secret"
control_plane:
  url: "http://backend:80"
  admin_token: "agentcc-admin-secret"
`)
		_, err := Load(path)
		requireInsecureTokenRefused(t, err, "control_plane.admin_token", "agentcc-admin-secret")
	})
	t.Run("weak AGENTCC_CONTROL_PLANE_TOKEN", func(t *testing.T) {
		unsetEnv(t, adminTokenEnv...)
		t.Setenv("AGENTCC_ADMIN_TOKEN", strongToken)
		t.Setenv("AGENTCC_CONTROL_PLANE_URL", "http://backend:8000")
		t.Setenv("AGENTCC_CONTROL_PLANE_TOKEN", "local-dev-only-admin-token-replace-me")
		_, err := Load("")
		requireInsecureTokenRefused(t, err, "control_plane.admin_token", "local-dev-only-admin-token-replace-me")
	})
	t.Run("control plane with no token at all", func(t *testing.T) {
		unsetEnv(t, adminTokenEnv...)
		t.Setenv("AGENTCC_CONTROL_PLANE_URL", "http://backend:8000")
		_, err := Load("")
		requireInsecureTokenRefused(t, err, "admin_token", "")
	})
	t.Run("the dev flag allows the compose wiring", func(t *testing.T) {
		unsetEnv(t, adminTokenEnv...)
		t.Setenv("AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN", "1")
		t.Setenv("AGENTCC_ADMIN_TOKEN", "local-dev-only-admin-token-replace-me")
		t.Setenv("AGENTCC_CONTROL_PLANE_URL", "http://backend")
		t.Setenv("AGENTCC_CONTROL_PLANE_TOKEN", "local-dev-only-admin-token-replace-me")
		cfg, err := Load("")
		if err != nil {
			t.Fatalf("Load error: %v", err)
		}
		if cfg.ControlPlane.AdminToken != "local-dev-only-admin-token-replace-me" {
			t.Errorf("control plane token = %q", cfg.ControlPlane.AdminToken)
		}
	})
}

// A config whose two tokens are read from env vars with no literal. A
// deployment that sets only
// AGENTCC_ADMIN_TOKEN starts, and the control plane falls back to it; one that
// sets nothing is refused at start-up unless the dev-only opt-in is set.
func TestLoad_EnvOnlyTokenConfigShape(t *testing.T) {
	const envOnly = `
admin:
  token: "${AGENTCC_ADMIN_TOKEN}"
control_plane:
  url: "http://backend:80"
  admin_token: "${AGENTCC_CONTROL_PLANE_TOKEN}"
  sync_on_startup: true
`
	t.Run("only AGENTCC_ADMIN_TOKEN set", func(t *testing.T) {
		unsetEnv(t, adminTokenEnv...)
		t.Setenv("AGENTCC_ADMIN_TOKEN", strongToken)
		cfg, err := Load(writeConfig(t, envOnly))
		if err != nil {
			t.Fatalf("Load error: %v", err)
		}
		if cfg.Admin.Token != strongToken || cfg.ControlPlane.AdminToken != strongToken {
			t.Errorf("tokens = (%q, %q), want the env token on both", cfg.Admin.Token, cfg.ControlPlane.AdminToken)
		}
	})
	t.Run("nothing set", func(t *testing.T) {
		unsetEnv(t, adminTokenEnv...)
		_, err := Load(writeConfig(t, envOnly))
		requireInsecureTokenRefused(t, err, "admin.token", "")
		if err != nil && !strings.Contains(err.Error(), "empty") {
			t.Errorf("error %q does not say the token is empty", err)
		}
	})
}

// Strong tokens, and a gateway with no admin API at all, start.
func TestLoad_AcceptsSecureOrAbsentAdminToken(t *testing.T) {
	t.Run("no admin block, no control plane", func(t *testing.T) {
		unsetEnv(t, adminTokenEnv...)
		cfg, err := Load(writeConfig(t, "server:\n  port: 8080\n"))
		if err != nil {
			t.Fatalf("Load error: %v", err)
		}
		if cfg.Admin.Token != "" {
			t.Errorf("admin token = %q, want empty", cfg.Admin.Token)
		}
	})
	t.Run("strong token in the file", func(t *testing.T) {
		unsetEnv(t, adminTokenEnv...)
		if _, err := Load(writeConfig(t, "admin:\n  token: \""+strongToken+"\"\n")); err != nil {
			t.Fatalf("Load error: %v", err)
		}
	})
	t.Run("strong env token overrides the literal on both settings", func(t *testing.T) {
		unsetEnv(t, adminTokenEnv...)
		t.Setenv("AGENTCC_ADMIN_TOKEN", strongToken)
		t.Setenv("AGENTCC_CONTROL_PLANE_TOKEN", strongToken)
		cfg, err := Load(writeConfig(t, `
admin:
  token: "agentcc-admin-secret"
control_plane:
  url: "http://backend:80"
  admin_token: "agentcc-admin-secret"
`))
		if err != nil {
			t.Fatalf("Load error: %v", err)
		}
		if cfg.Admin.Token != strongToken || cfg.ControlPlane.AdminToken != strongToken {
			t.Errorf("tokens = (%q, %q), want the env token on both", cfg.Admin.Token, cfg.ControlPlane.AdminToken)
		}
	})
	t.Run("control plane falls back to a strong admin token", func(t *testing.T) {
		unsetEnv(t, adminTokenEnv...)
		t.Setenv("AGENTCC_ADMIN_TOKEN", strongToken)
		t.Setenv("AGENTCC_CONTROL_PLANE_URL", "http://backend:8000")
		cfg, err := Load("")
		if err != nil {
			t.Fatalf("Load error: %v", err)
		}
		if cfg.ControlPlane.AdminToken != strongToken {
			t.Errorf("control plane token = %q, want the admin token", cfg.ControlPlane.AdminToken)
		}
	})
}

// The repo's own configs start the way the compose files and the e2e
// stack run them: the published compose default is refused on its own and
// starts with the dev flag; a generated token starts without it.
func TestLoad_RepoConfigsStartUnderComposeWiring(t *testing.T) {
	root := filepath.Join("..", "..", "..")
	paths := []string{
		filepath.Join(root, "agentcc-gateway", "config.example.yaml"),
		filepath.Join(root, "agentcc-gateway", "config.hosted-local.yaml"),
		filepath.Join(root, "agentcc-gateway", "config.error-feed-vertex.example.yaml"),
		filepath.Join(root, "e2e", "stack", "gateway.e2e.yaml"),
	}
	const composeDefault = "local-dev-only-admin-token-replace-me"
	compose := func(t *testing.T, token string) {
		unsetEnv(t, adminTokenEnv...)
		t.Setenv("AGENTCC_ADMIN_TOKEN", token)
		t.Setenv("AGENTCC_CONTROL_PLANE_URL", "http://backend")
		t.Setenv("AGENTCC_CONTROL_PLANE_TOKEN", token)
	}
	for _, path := range paths {
		t.Run(filepath.Base(path), func(t *testing.T) {
			if _, err := os.Stat(path); err != nil {
				t.Fatalf("repo config missing: %v", err)
			}
			compose(t, composeDefault)
			if _, err := Load(path); err == nil {
				t.Errorf("the published compose default started without the dev flag")
			}
			t.Setenv("AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN", "1")
			if _, err := Load(path); err != nil {
				t.Errorf("with the dev flag, Load error: %v", err)
			}
			compose(t, strongToken)
			if _, err := Load(path); err != nil {
				t.Errorf("with a generated token, Load error: %v", err)
			}
		})
	}
}

// testdata/admin_token_cases.json is shared with Django
// (futureagi/agentcc/tests/test_admin_token_parity.py), whose admin endpoints
// accept the same credential: both sides must refuse and accept the same
// tokens.
func TestAdminTokenProblem_SharedCases(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("testdata", "admin_token_cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	var cases struct {
		Refused  []string `json:"refused"`
		Accepted []string `json:"accepted"`
	}
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases.Refused) == 0 || len(cases.Accepted) == 0 {
		t.Fatalf("read %d refused and %d accepted cases; want some of each", len(cases.Refused), len(cases.Accepted))
	}
	for _, token := range cases.Refused {
		if adminTokenProblem(token) == "" {
			t.Errorf("accepted %q; the shared cases say refuse it", token)
		}
	}
	for _, token := range cases.Accepted {
		if p := adminTokenProblem(token); p != "" {
			t.Errorf("refused %q (%s); the shared cases say accept it", token, p)
		}
	}
}
