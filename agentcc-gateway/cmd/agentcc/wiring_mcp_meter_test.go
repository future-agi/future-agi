package main

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/meter"
)

// TestMCPMeterWiring: the meter step hands deps.Events the log webhook's
// sink as main sets it after the inits (not the default it saw at init), and
// installs a meter that reads a key's log_content by id and by raw key and
// redacts with the global privacy redactor.
func TestMCPMeterWiring(t *testing.T) {
	before := meter.Default()
	t.Cleanup(func() { meter.Install(before) })

	cfg := &config.Config{}
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "quiet", Key: "scn-quiet-key", Metadata: map[string]string{"log_content": "none"}}}
	cfg.Privacy.Enabled = true
	cfg.Privacy.Mode = "patterns"
	cfg.Privacy.Patterns = []config.RedactPatternConfig{{Name: "email", Pattern: `[a-z]+@[a-z]+\.com`}}
	keys := auth.NewKeyStore(cfg.Auth)
	quiet := keys.Lookup("scn-quiet-key")
	if quiet == nil {
		t.Fatal("the config key was not loaded")
	}

	d := &wiringDeps{cfg: cfg, keyStore: keys}
	d.seedInjected(nil)
	deps := &mcp.Deps{}
	if err := initMCPMeter(d, deps); err != nil {
		t.Fatalf("initMCPMeter: %v", err)
	}
	if deps.Events != nil {
		t.Fatal("the meter step set Events before the server was built")
	}
	var got []string
	d.events = func(kind string, _ any) { got = append(got, kind) } // what main sets
	if err := d.startServerWiring(); err != nil {
		t.Fatalf("afterServer: %v", err)
	}
	if deps.Events == nil {
		t.Fatal("deps.Events not set")
	}
	deps.Events(mcp.EventKindToolCall, mcp.ToolCallRecord{})
	if len(got) != 1 || got[0] != mcp.EventKindToolCall {
		t.Fatalf("events reached %v, want the flusher's sink", got)
	}

	m := meter.Default()
	if m == before {
		t.Fatal("no meter installed")
	}
	if v := m.KeyLogContent(quiet.ID, ""); v != "none" {
		t.Fatalf("by key id: %q", v)
	}
	if v := m.KeyLogContent("", "scn-quiet-key"); v != "none" {
		t.Fatalf("by raw key: %q", v)
	}
	if v := m.KeyLogContent("no-such-id", "scn-quiet-key"); v != "" {
		t.Fatalf("an unknown key id fell back to the raw key: %q", v)
	}
	r := m.Redactor("org-without-config")
	if r == nil || r.Redact("mail bob@corp.com") != "mail [REDACTED:email]" {
		t.Fatalf("global redactor not used: %v", r)
	}

	// Without privacy and a key store the meter has neither source.
	d2 := &wiringDeps{cfg: &config.Config{}}
	d2.seedInjected(nil)
	if err := initMCPMeter(d2, &mcp.Deps{}); err != nil || d2.startServerWiring() != nil {
		t.Fatal("initMCPMeter failed without privacy")
	}
	if meter.Default().Redactor("org") != nil || meter.Default().KeyLogContent("k", "raw") != "" {
		t.Fatal("sources present without privacy or keys")
	}
}
