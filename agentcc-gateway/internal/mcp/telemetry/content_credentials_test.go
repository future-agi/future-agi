package telemetry

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp/meter"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

func TestCredentialFieldClasses(t *testing.T) {
	fields := []string{
		"Authorization", "Proxy-Authorization", "Cookie", "Set-Cookie",
		"X-Access-Key", "Ocp-Apim-Subscription-Key", "X-API-Key", "x_api.key",
		"X-Amz-Security-Token", "X-CSRF-Token", "refreshToken", "Client-Secret",
		"X-Password", "X-Hub-Signature-256", "X-Amz-Signature", "X-Session-ID",
		"Session", "X-Credential", "X-Auth", "Authentication", "X-Private-Key",
		"X-Public-Key", "signingKey", "subscriptionKey", "sEt_CoOkIe", "set.cookie",
		"Set Cookie", "aPi.KeY", "X-Access_Key-Id",
	}
	for _, field := range fields {
		t.Run(field, func(t *testing.T) {
			for _, mode := range []string{meter.ModeFull, meter.ModeMetadata, meter.ModeNone, "", "unknown"} {
				t.Run(mode, func(t *testing.T) {
					const canary = "synthetic-opaque-credential-value"
					raw, err := json.Marshal(map[string]any{
						"headers": map[string]any{field: canary, "Content-Type": "application/json"},
						"items":   []any{map[string]any{field: canary, "message": "retained"}},
					})
					if err != nil {
						t.Fatal("fixture did not encode")
					}
					// Mandatory scrubbing is independent of the organization's optional redactor.
					for _, r := range []*privacy.Redactor{nil, privacy.New(privacy.ModeNone, nil)} {
						got := Content(raw, mode, r)
						if strings.Contains(got, canary) {
							t.Fatal("credential field value retained")
						}
						if mode != meter.ModeFull {
							if got != "" {
								t.Fatal("closed content mode exported content")
							}
							continue
						}
						var decoded struct {
							Headers map[string]string   `json:"headers"`
							Items   []map[string]string `json:"items"`
						}
						if json.Unmarshal([]byte(got), &decoded) != nil || len(decoded.Items) != 1 {
							t.Fatal("content did not retain its structure")
						}
						if decoded.Headers[field] != "[redacted]" || decoded.Items[0][field] != "[redacted]" {
							t.Fatal("credential field not replaced in full")
						}
						if decoded.Headers["Content-Type"] != "application/json" || decoded.Items[0]["message"] != "retained" {
							t.Fatal("noncredential content changed")
						}
					}
				})
			}
		})
	}
}

func TestCredentialFieldReplacesEntireValueBeforeBounding(t *testing.T) {
	for _, value := range []any{
		"synthetic-opaque-value", []any{"synthetic-first-value", "synthetic-second-value"},
		map[string]any{"value": "synthetic-nested-value"}, 12345, true, nil,
		strings.Repeat("synthetic-long-value", meter.MaxContentBytes),
	} {
		raw, err := json.Marshal(map[string]any{"Set-Cookie": value, "safe": "retained"})
		if err != nil {
			t.Fatal("fixture did not encode")
		}
		// Removing the whole credential before serialization means a large secret
		// neither consumes the byte budget nor affects original_bytes metadata.
		if Content(raw, meter.ModeFull, nil) != `{"Set-Cookie":"[redacted]","safe":"retained"}` {
			t.Fatal("credential value was not replaced before bounding")
		}
	}
}

func TestCredentialFieldsAndOrgRedactionBeforeTruncation(t *testing.T) {
	r := privacy.New(privacy.ModePatterns, []privacy.PatternConfig{{Name: "pii", Pattern: `bob@corp\.example`}})
	raw, err := json.Marshal(map[string]any{
		"a": map[string]any{
			"Set-Cookie":   "session=synthetic-cookie-value; HttpOnly; Secure",
			"X-Access-Key": "synthetic-access-value", "Ocp-Apim-Subscription-Key": "synthetic-subscription-value",
			"email": "bob@corp.example", "bob@corp.example": "retained", "Content-Type": "application/json",
		},
		"z": strings.Repeat("padding", meter.MaxContentBytes),
	})
	if err != nil {
		t.Fatal("fixture did not encode")
	}
	got := Content(raw, meter.ModeFull, r)
	var bounded struct {
		Truncated bool   `json:"truncated"`
		Prefix    string `json:"prefix"`
	}
	if len(got) > meter.MaxContentBytes || json.Unmarshal([]byte(got), &bounded) != nil || !bounded.Truncated {
		t.Fatal("content did not preserve bounded JSON contract")
	}
	for _, canary := range []string{"synthetic-cookie-value", "synthetic-access-value", "synthetic-subscription-value", "bob@corp.example"} {
		if strings.Contains(got, canary) {
			t.Fatal("private value survived redaction before truncation")
		}
	}
	if !strings.Contains(bounded.Prefix, "application/json") || !strings.Contains(bounded.Prefix, "retained") || !strings.Contains(bounded.Prefix, "[redacted]") {
		t.Fatal("truncation lost safe content or redaction marker")
	}
}

func TestNonCredentialFieldsRetainValues(t *testing.T) {
	fields := []string{"Content-Type", "Content-Length", "Accept", "User-Agent", "X-Request-Id", "traceparent", "status", "request_id", "name", "message", "count", "enabled"}
	for _, field := range fields {
		t.Run(field, func(t *testing.T) {
			raw, err := json.Marshal(map[string]any{field: "retained"})
			if err != nil {
				t.Fatal("fixture did not encode")
			}
			if Content(raw, meter.ModeFull, nil) != string(raw) {
				t.Fatal("noncredential field changed")
			}
		})
	}
}
