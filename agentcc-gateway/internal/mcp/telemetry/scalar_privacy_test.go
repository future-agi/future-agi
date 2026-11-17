package telemetry

import (
	"encoding/base64"
	"strings"
	"testing"
)

func TestScalarPrivacyShape(t *testing.T) {
	for _, value := range []string{"tool_call_01", "researcher", "run_R1", "up", "tools/call", "2025-11-25", "-32603", "agentcc-gateway", "alpha.beta:read", "42"} {
		if SanitizeText(value, nil) != value {
			t.Error("ordinary scalar identity changed")
		}
	}
	opaque := base64.RawURLEncoding.EncodeToString([]byte(strings.Repeat("synthetic-scalar-fixture-", 2)))
	for _, value := range []string{
		`{"message":"` + opaque + `"}`, "Authorization: Basic\r\n " + opaque,
		"hello\n" + opaque, strings.Repeat("a", 257), "name with spaces",
		"https://" + "sk-" + opaque[:32] + ".invalid", "password=" + opaque,
		base64.StdEncoding.EncodeToString([]byte("Authorization: Basic " + opaque)),
	} {
		if got := SanitizeText(value, nil); got != "[redacted]" {
			t.Error("unapproved scalar not replaced by fixed placeholder")
		}
	}
}
