package config

import "testing"

func TestW07DeploymentSwitches(t *testing.T) {
	for _, name := range []string{"AGENTCC_MCP_CATALOG_ENABLED", "AGENTCC_MCP_VIRTUAL_SERVERS_ENABLED"} {
		t.Run(name, func(t *testing.T) {
			for _, value := range []string{"", "false", "invalid", "true"} {
				t.Setenv("AGENTCC_MCP_CATALOG_ENABLED", "false")
				t.Setenv("AGENTCC_MCP_VIRTUAL_SERVERS_ENABLED", "false")
				t.Setenv(name, value)
				cfg := DefaultConfig()
				if !cfg.MCP.CatalogEnabled || !cfg.MCP.VirtualServersEnabled {
					t.Fatal("the MCP catalog and virtual servers must default ON; explicit environment values may disable them")
				}
				cfg.MCP.CatalogEnabled, cfg.MCP.VirtualServersEnabled = true, true
				loadFromEnv(cfg)
				if cfg.MCP.CatalogEnabled != (name == "AGENTCC_MCP_CATALOG_ENABLED" && value == "true") || cfg.MCP.VirtualServersEnabled != (name == "AGENTCC_MCP_VIRTUAL_SERVERS_ENABLED" && value == "true") {
					t.Fatal("independent fail-closed environment binding failed")
				}
			}
		})
	}
}
