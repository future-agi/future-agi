package config

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

// TestToolsKeysDefaults: the tools and capabilities keys take their defaults,
// and capabilities.<cap> blocks parse beside the existing
// capabilities.qualification_file.
func TestToolsKeysDefaults(t *testing.T) {
	c := DefaultConfig()
	if c.Tools != (ToolsConfig{Enabled: false, CatalogDir: "", ReserveDefaultUSD: 0.10, CacheTTLSeconds: 0}) {
		t.Errorf("tools defaults = %+v", c.Tools)
	}
	if c.Capabilities.QualificationFile != "" || len(c.Capabilities.PerCapability) != 0 {
		t.Errorf("capabilities defaults = %+v", c.Capabilities)
	}
	if DefaultCapabilityTimeout != 30*time.Second {
		t.Errorf("DefaultCapabilityTimeout = %s, want 30s", DefaultCapabilityTimeout)
	}
	if got := c.Capabilities.Timeout("search"); got != DefaultCapabilityTimeout {
		t.Errorf("an unset capability timeout = %s, want %s", got, DefaultCapabilityTimeout)
	}

	c, err := loadYAML(t, `
tools:
  enabled: true
  catalog_dir: /etc/agentcc/tools
  reserve_default_usd: 0.25
  cache_ttl_seconds: 600
capabilities:
  qualification_file: /var/agentcc/qualification.json
  search:
    timeout_ms: 5000
    defaults: {depth: standard, max_results: 10}
  documents.parse:
    timeout_ms: 120000
`)
	if err != nil {
		t.Fatal(err)
	}
	if c.Tools != (ToolsConfig{Enabled: true, CatalogDir: "/etc/agentcc/tools", ReserveDefaultUSD: 0.25, CacheTTLSeconds: 600}) {
		t.Errorf("tools = %+v", c.Tools)
	}
	if c.Capabilities.QualificationFile != "/var/agentcc/qualification.json" {
		t.Errorf("qualification_file = %q: the per-capability blocks displaced it", c.Capabilities.QualificationFile)
	}
	if got := c.Capabilities.Timeout("search"); got != 5*time.Second {
		t.Errorf("search timeout = %s", got)
	}
	if got := c.Capabilities.Timeout("documents.parse"); got != 2*time.Minute {
		t.Errorf("documents.parse timeout = %s", got)
	}
	if got := c.Capabilities.Timeout("fetch"); got != DefaultCapabilityTimeout {
		t.Errorf("fetch (unset) timeout = %s", got)
	}
	if d := c.Capabilities.PerCapability["search"].Defaults; d["depth"] != "standard" || d["max_results"] != 10 {
		t.Errorf("search defaults = %v", d)
	}
	if _, ok := c.Capabilities.PerCapability["qualification_file"]; ok {
		t.Error("qualification_file was read as a capability")
	}
	raw, err := json.Marshal(c.Capabilities)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), `"qualification_file"`) || !strings.Contains(string(raw), `"search"`) {
		t.Errorf("capabilities JSON = %s", raw)
	}

	for name, body := range map[string]string{
		"negative timeout":     "capabilities:\n  search:\n    timeout_ms: -1\n",
		"negative reserve":     "tools:\n  reserve_default_usd: -0.1\n",
		"negative cache ttl":   "tools:\n  cache_ttl_seconds: -5\n",
		"capability not a map": "capabilities:\n  search: fast\n",
	} {
		if _, err := loadYAML(t, body); err == nil {
			t.Errorf("%s: Load accepted it", name)
		}
	}
}
