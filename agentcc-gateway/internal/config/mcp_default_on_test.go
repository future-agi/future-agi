package config

import (
	"bytes"
	"encoding/json"
	"log/slog"
	"reflect"
	"strings"
	"testing"
)

func TestLoadMCPKeyAwareDefault(t *testing.T) {
	const current = "synthetic-current-state-key-0123456789"
	const previous = "synthetic-previous-state-key-9876543210"
	const warning = "MCP disabled: set AGENTCC_MCP_STATE_KEYS to enable MCP by default"
	cases := []struct {
		name, body, env, profile string
		enabled, warn            bool
		keys                     []string
		wantErr                  string
	}{
		{name: "no file", warn: true},
		{name: "self hosted no file", profile: "self-hosted", warn: true},
		{name: "minimal YAML", body: "server: {port: 8081}\n", warn: true},
		{name: "empty MCP block", body: "mcp: {}\n", warn: true},
		{name: "empty YAML list", body: "mcp: {state_keys: []}\n", warn: true},
		{name: "empty YAML key", body: "mcp: {state_keys: ['']}\n", warn: true},
		{name: "whitespace YAML key", body: "mcp: {state_keys: ['" + strings.Repeat(" ", 32) + "']}\n", warn: true},
		{name: "empty expanded YAML key", body: "mcp: {state_keys: ['${MCP_DEFAULT_EMPTY_KEY}']}\n", warn: true},
		{name: "mixed YAML keys preserve bytes", body: "mcp: {state_keys: ['', ' " + current + " ', '   ', " + previous + "]}\n", enabled: true, keys: []string{" " + current + " ", previous}},
		{name: "true blank YAML key", body: "mcp: {enabled: true, state_keys: ['']}\n", wantErr: "mcp.enabled needs a state key"},
		{name: "true whitespace YAML key", body: "mcp: {enabled: true, state_keys: ['" + strings.Repeat(" ", 32) + "']}\n", wantErr: "mcp.enabled needs a state key"},
		{name: "hosted blank YAML behavior unchanged", profile: "hosted", body: "mcp: {state_keys: ['']}\n", wantErr: "mcp.state_keys[0]"},
		{name: "blank parsed list", env: " , \t, ", warn: true},
		{name: "env key", env: current, enabled: true, keys: []string{current}},
		{name: "self hosted env key", profile: "self-hosted", env: current, enabled: true, keys: []string{current}},
		{name: "minimal YAML env key", body: "server: {port: 8081}\n", env: current, enabled: true, keys: []string{current}},
		{name: "YAML key", body: "mcp: {state_keys: [" + current + "]}\n", enabled: true, keys: []string{current}},
		{name: "expanded YAML key", body: "mcp: {state_keys: ['${MCP_DEFAULT_STATE_KEY}']}\n", enabled: true, keys: []string{current}},
		{name: "env rotation", env: " " + current + ", , " + previous + " ", enabled: true, keys: []string{current, previous}},
		{name: "YAML rotation", body: "mcp: {state_keys: [" + current + ", " + previous + "]}\n", enabled: true, keys: []string{current, previous}},
		{name: "env replaces YAML", body: "mcp: {state_keys: [" + previous + "]}\n", env: current, enabled: true, keys: []string{current}},
		{name: "blank env replaces YAML", body: "mcp: {state_keys: [" + current + "]}\n", env: " , ", warn: true},
		{name: "false without key", body: "mcp: {enabled: false}\n"},
		{name: "false with env key", body: "mcp: {enabled: false}\n", env: current, keys: []string{current}},
		{name: "false with YAML key", body: "mcp: {enabled: false, state_keys: [" + current + "]}\n", keys: []string{current}},
		{name: "expanded false", body: "mcp: {enabled: ${MCP_DEFAULT_ENABLED}}\n", env: current, keys: []string{current}},
		{name: "merged false", body: "mcp: {<<: {enabled: false}, state_keys: [" + current + "]}\n", keys: []string{current}},
		{name: "true without key", body: "mcp: {enabled: true}\n", wantErr: "mcp.enabled needs a state key"},
		{name: "true blank parsed list", body: "mcp: {enabled: true}\n", env: " , ", wantErr: "mcp.enabled needs a state key"},
		{name: "true env key", body: "mcp: {enabled: true}\n", env: current, enabled: true, keys: []string{current}},
		{name: "true YAML key", body: "mcp: {enabled: true, state_keys: [" + current + "]}\n", enabled: true, keys: []string{current}},
		{name: "short env key", env: "synthetic-short", wantErr: "mcp.state_keys[0]"},
		{name: "short YAML key", body: "mcp: {state_keys: [synthetic-short]}\n", wantErr: "mcp.state_keys[0]"},
		{name: "short rotation key", env: current + ",synthetic-short", wantErr: "mcp.state_keys[1]"},
		{name: "false still rejects short key", body: "mcp: {enabled: false}\n", env: "synthetic-short", wantErr: "mcp.state_keys[0]"},
		{name: "invalid enabled retains loader error", body: "mcp: {enabled: invalid}\n", wantErr: "loading config file:"},
		{name: "hosted no key", profile: "hosted"},
		{name: "hosted env key", profile: "hosted", env: current, keys: []string{current}},
		{name: "hosted YAML key", profile: "hosted", body: "mcp: {state_keys: [" + current + "]}\n", keys: []string{current}},
		{name: "hosted false key", profile: "hosted", body: "mcp: {enabled: false}\n", env: current, keys: []string{current}},
		{name: "hosted true key refused", profile: "hosted", body: "mcp: {enabled: true}\n", env: current, wantErr: "MCP disabled until the paired release is qualified"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			unsetEnv(t, adminTokenEnv...)
			unsetEnv(t, EnvMCPStateKeys, EnvMCPDjangoSecret, EnvMCPEgressPrivateAllow,
				EnvDeploymentProfile, EnvHostedOneKeyActivation, EnvInternalAPIKey,
				"AGENTCC_AUTH_ENABLED", "AGENTCC_WEBHOOK_SECRET", "AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS",
				"AGENTCC_SYNC_ON_STARTUP", "AGENTCC_SYNC_INTERVAL", "AGENTCC_KEY_SYNC_GRACE",
				"AGENTCC_PORT", "AGENTCC_HOST", "AGENTCC_LOG_LEVEL", "AGENTCC_DEFAULT_REQUEST_TIMEOUT",
				"AGENTCC_MAX_REQUEST_TIMEOUT", "AGENTCC_SHUTDOWN_TIMEOUT", "AGENTCC_REDIS_ADDRESS",
				"AGENTCC_REDIS_PASSWORD", "AGENTCC_REDIS_DB", "AGENTCC_MANAGED_USER_HASH_KEY",
				"AGENTCC_VIDEO_ENABLED", "AGENTCC_VIDEO_KILL_SWITCH", "AGENTCC_TEST_ENDPOINT",
				"AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED", "AGENTCC_MCP_CLIENT_OAUTH_ENABLED",
				"AGENTCC_MCP_K2_APPS_ENABLED", "AGENTCC_MCP_CATALOG_ENABLED", "AGENTCC_MCP_VIRTUAL_SERVERS_ENABLED")
			t.Setenv(EnvDeploymentProfile, tc.profile)
			t.Setenv(EnvMCPStateKeys, tc.env)
			t.Setenv("MCP_DEFAULT_STATE_KEY", current)
			t.Setenv("MCP_DEFAULT_EMPTY_KEY", "")
			t.Setenv("MCP_DEFAULT_ENABLED", "false")
			body := tc.body
			if tc.profile == "hosted" {
				body = strings.Replace(hostedFixture(t), "mcp: {enabled: false}\n", "", 1) + body
				body += "managed: {spend_guard: {max_usd_per_minute: 5}}\n"
			}
			var logs bytes.Buffer
			old := slog.Default()
			slog.SetDefault(slog.New(slog.NewJSONHandler(&logs, &slog.HandlerOptions{Level: slog.LevelWarn})))
			t.Cleanup(func() { slog.SetDefault(old) })
			path := ""
			if body != "" {
				path = writeConfig(t, body)
			}
			cfg, err := Load(path)
			if tc.wantErr != "" {
				if err == nil || !strings.Contains(err.Error(), tc.wantErr) {
					t.Fatalf("Load error = %v, want %q", err, tc.wantErr)
				}
			} else {
				if err != nil {
					t.Fatal(err)
				}
				if cfg.MCP.Enabled != tc.enabled || cfg.MCP.DevInsecureState {
					t.Errorf("enabled=%v insecure=%v, want enabled=%v insecure=false", cfg.MCP.Enabled, cfg.MCP.DevInsecureState, tc.enabled)
				}
				if !reflect.DeepEqual(cfg.MCP.StateKeys, tc.keys) && !(len(cfg.MCP.StateKeys) == 0 && len(tc.keys) == 0) {
					t.Error("effective state keys or rotation order changed")
				}
				if cfg.IsHosted() != (tc.profile == "hosted") {
					t.Error("deployment profile changed")
				}
			}
			wantWarnings := 0
			if tc.warn {
				wantWarnings = 1
			}
			lines := strings.Split(strings.TrimSpace(logs.String()), "\n")
			if logs.Len() == 0 {
				lines = nil
			}
			if len(lines) != wantWarnings {
				t.Errorf("warning count = %d, want %d", len(lines), wantWarnings)
			}
			for _, line := range lines {
				var record map[string]any
				if err := json.Unmarshal([]byte(line), &record); err != nil {
					t.Fatal(err)
				}
				if record["level"] != "WARN" || record["msg"] != warning || len(record) != 3 {
					t.Error("warning must have only timestamp, level, and the constant setup message")
				}
			}
			for _, secret := range []string{current, previous, "synthetic-short"} {
				if strings.Contains(logs.String(), secret) || (err != nil && strings.Contains(err.Error(), secret)) {
					t.Error("load exposed a synthetic state key")
				}
			}
		})
	}
}

func TestMCPKeyAwareDefaultLeavesDefaultConfigUnchanged(t *testing.T) {
	m := DefaultConfig().MCP
	if m.Enabled || m.DevInsecureState || len(m.StateKeys) != 0 {
		t.Fatal("DefaultConfig must remain disabled without state keys or insecure state")
	}
}
