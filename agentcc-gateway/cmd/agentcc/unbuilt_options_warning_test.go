package main

import (
	"bytes"
	"log/slog"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// TestUnbuiltOptionsWarning: a configured file or webhook audit sink and
// cluster.redis_url are named in one startup warning, never with their
// values; a config without them logs nothing.
func TestUnbuiltOptionsWarning(t *testing.T) {
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	defer slog.SetDefault(prev)

	cfg := config.DefaultConfig()
	cfg.Audit.Sinks = []config.AuditSinkConfig{
		{Type: "stdout"},
		{Type: "file", Path: "/var/log/agentcc/unbuilt-secret-path.jsonl"},
		{Type: "webhook", URL: "https://siem.example.com/ingest?token=unbuilt-secret-token"},
	}
	cfg.Cluster.RedisURL = "redis://:unbuilt-secret-password@redis:6379/0"
	warnUnbuiltOptions(cfg)
	out := buf.String()
	if strings.Count(out, "configured options not used by this build") != 1 {
		t.Fatalf("want one startup warning, got:\n%s", out)
	}
	for _, want := range []string{"audit.sinks[1] (type file", "audit.sinks[2] (type webhook", "cluster.redis_url"} {
		if !strings.Contains(out, want) {
			t.Errorf("the warning does not name %q:\n%s", want, out)
		}
	}
	if strings.Contains(out, "audit.sinks[0]") || strings.Contains(out, "unbuilt-secret") {
		t.Errorf("the warning names the stdout sink or carries a configured value:\n%s", out)
	}

	buf.Reset()
	warnUnbuiltOptions(config.DefaultConfig())
	if buf.Len() != 0 {
		t.Errorf("a default config logged: %s", buf.String())
	}
}
