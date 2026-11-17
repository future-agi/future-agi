package config

import "testing"

func TestMCPSubfeatureEnvironmentIsIndependentAndFailClosed(t *testing.T) {
	names := []string{"AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED", "AGENTCC_MCP_CLIENT_OAUTH_ENABLED", "AGENTCC_MCP_K2_APPS_ENABLED"}
	for i, name := range names {
		t.Run(name, func(t *testing.T) {
			for _, other := range names {
				t.Setenv(other, "false")
			}
			t.Setenv(name, "true")
			cfg := DefaultConfig()
			loadFromEnv(cfg)
			actual := []bool{cfg.MCP.UpstreamOAuthEnabled, cfg.MCP.ClientOAuthEnabled, cfg.MCP.K2AppsEnabled}
			for j, got := range actual {
				if got != (j == i) {
					t.Fatalf("%s affected flag %s: %v", name, names[j], actual)
				}
			}
			for _, invalid := range []string{"", "tru", "yes"} {
				cfg.MCP.UpstreamOAuthEnabled, cfg.MCP.ClientOAuthEnabled, cfg.MCP.K2AppsEnabled = true, true, true
				t.Setenv(name, invalid)
				loadFromEnv(cfg)
				actual = []bool{cfg.MCP.UpstreamOAuthEnabled, cfg.MCP.ClientOAuthEnabled, cfg.MCP.K2AppsEnabled}
				if actual[i] {
					t.Fatalf("%s=%q retained an enabled YAML value", name, invalid)
				}
			}
		})
	}
}

func TestMCPSubfeaturesDefaultOff(t *testing.T) {
	cfg := DefaultConfig()
	if cfg.MCP.UpstreamOAuthEnabled || cfg.MCP.ClientOAuthEnabled || cfg.MCP.K2AppsEnabled {
		t.Fatal("MCP sub-features must default OFF")
	}
}
