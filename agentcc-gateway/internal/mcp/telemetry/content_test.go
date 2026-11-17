package telemetry

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp/meter"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

func TestOpaqueCredentialFieldsAreScrubbed(t *testing.T) {
	for _, field := range []string{"x-api-key", "api.key", "access_key", "private_key", "credentials", "Authorization", "refreshToken"} {
		raw, _ := json.Marshal(map[string]any{field: "synthetic-opaque-value", "safe": "retained"})
		got := Content(raw, meter.ModeFull, nil)
		if strings.Contains(got, "synthetic-opaque-value") || !strings.Contains(got, "retained") {
			t.Fatal("opaque credential field not scrubbed")
		}
	}
}

func TestContentScrubsBeforeBounding(t *testing.T) {
	r := privacy.New(privacy.ModePatterns, []privacy.PatternConfig{{Name: "pii", Pattern: `bob@corp\.example`}})
	raw, _ := json.Marshal(map[string]any{"a": strings.Repeat("Authorization: Bearer synthetic-credential ", 2000), "password": []any{"synthetic-password"}, "nested": map[string]any{"apiKey": "synthetic-api-value", "email": "bob@corp.example"}, "_meta": map[string]any{"unapproved": "synthetic-private-meta"}})
	got := Content(raw, meter.ModeFull, r)
	if !json.Valid([]byte(got)) || len(got) > meter.MaxContentBytes || !strings.Contains(got, "redacted") {
		t.Fatal("content not bounded redacted JSON")
	}
	for _, canary := range []string{"synthetic-credential", "synthetic-password", "synthetic-api-value", "bob@corp.example", "synthetic-private-meta"} {
		if strings.Contains(got, canary) {
			t.Fatal("canary retained")
		}
	}
	if Content(raw, meter.ModeMetadata, r) != "" || Content(raw, meter.ModeNone, r) != "" || Content(json.RawMessage(`invalid`), meter.ModeFull, r) != "" {
		t.Fatal("closed content policy opened")
	}
}
