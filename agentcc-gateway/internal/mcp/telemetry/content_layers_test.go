package telemetry

import (
	"encoding/base64"
	"encoding/json"
	"net/url"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp/meter"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

func TestSpanContentSafeRepresentations(t *testing.T) {
	for _, value := range []any{
		"Content-Type: application/json\nAccept: text/plain",
		"https://service.invalid/path?message=retained&count=1#section",
		"see https://service.invalid/path?count=1 for details",
		"http://[::1]",
		"https://service.invalid/path?message=[retained]",
		"version 1.2.3 and docs.service.invalid",
		map[string]any{"headers": []any{[]any{"Content-Type", "application/json"}, map[string]any{"name": "Accept", "value": "text/plain"}}},
		map[string]any{"nested": []any{"safe", 123, true, nil}, "count": 1},
	} {
		raw, _ := json.Marshal(value)
		if Content(raw, meter.ModeFull, nil) != string(raw) {
			t.Error("safe content changed")
		}
	}
}

func TestSpanContentURLSemantics(t *testing.T) {
	canary := base64.RawURLEncoding.EncodeToString([]byte(strings.Repeat("fixture-", 4)))
	value := "https://user:" + canary + "@service.invalid/path?api%5Fkey=" + canary + "&api%5Fkey=" + canary + "&safe=a%2Bb%26c#section"
	got := scrubText(value, 0)
	u, err := url.Parse(got)
	if err != nil || u.User != nil || strings.Contains(got, canary) {
		t.Fatal("URL retained credentials or became invalid")
	}
	q, err := url.ParseQuery(u.RawQuery)
	if err != nil || len(q["api_key"]) != 2 || q["api_key"][0] != "[redacted]" || q["api_key"][1] != "[redacted]" || q.Get("safe") != "a+b&c" || u.Fragment != "section" {
		t.Fatal("URL query redaction or safe-value round trip failed")
	}
}

func TestSpanContentBoundsFailClosed(t *testing.T) {
	canary := base64.RawURLEncoding.EncodeToString([]byte(strings.Repeat("fixture-", 4)))
	var nested any = map[string]any{"Cookie": canary}
	for i := 0; i < 64; i++ {
		nested = []any{nested}
	}
	encoded := `{"X-Cookie":"` + canary + `"}`
	for i := 0; i < 10; i++ {
		raw, _ := json.Marshal(encoded)
		encoded = string(raw)
	}
	for _, value := range []any{
		nested, encoded,
		strings.Repeat("safe padding ", 100000) + "Cookie: " + canary,
		strings.Repeat("{} ", 300) + "Cookie: " + canary,
		map[string]any{"a": "Cookie: " + canary, "z": strings.Repeat("padding ", meter.MaxContentBytes)},
	} {
		raw, _ := json.Marshal(value)
		got := Content(raw, meter.ModeFull, nil)
		if strings.Contains(got, canary) || len(got) > meter.MaxContentBytes || !json.Valid([]byte(got)) {
			t.Fatal("bounded sanitizer retained credential material or invalid JSON")
		}
	}
	if Content(json.RawMessage(`{} {"safe":1}`), meter.ModeFull, nil) != "" {
		t.Fatal("invalid multiple-root content accepted")
	}
}

func TestSpanContentOrgRedactionAfterLayers(t *testing.T) {
	r := privacy.New(privacy.ModePatterns, []privacy.PatternConfig{{Name: "pii", Pattern: `bob@corp\.example`}})
	raw, _ := json.Marshal(map[string]any{"a": `result: {"email":"bob@corp.example","safe":"retained"}`, "z": strings.Repeat("padding ", meter.MaxContentBytes)})
	got := Content(raw, meter.ModeFull, r)
	if strings.Contains(got, "bob@corp.example") || !strings.Contains(got, "retained") || !strings.Contains(got, "truncated") {
		t.Fatal("organization redaction or safe content lost before bounding")
	}
}
