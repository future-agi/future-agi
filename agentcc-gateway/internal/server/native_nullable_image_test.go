package server

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func TestNativePolicyResponsesImageNullableOutgoing(t *testing.T) {
	g, calls, spy := nativeCompatGateway(t, config.ToolPolicyConfig{Enabled: true, Deny: []string{"drop"}, DefaultAction: "strip"})
	for _, tc := range []struct {
		name, fields string
		refuse       bool
	}{
		{"omitted-file", `,"file_id":"file-local","detail":"auto"`, false},
		{"null-file", `,"image_url":null,"file_id":"file-local","detail":"auto"`, false},
		{"string-file", `,"image_url":"drop","file_id":"file-local","detail":"auto"`, false},
		{"string-url-only", `,"image_url":"drop"`, false},
		{"url-number", `,"image_url":7,"file_id":"file-local"`, true},
		{"url-bool", `,"image_url":true,"file_id":"file-local"`, true},
		{"url-array", `,"image_url":["drop"],"file_id":"file-local"`, true},
		{"url-object", `,"image_url":{"url":"drop"},"file_id":"file-local"`, true},
		{"no-locator", "", true},
		{"null-url-no-file", `,"image_url":null`, true},
		{"null-file-no-url", `,"file_id":null`, true},
		{"all-null", `,"image_url":null,"file_id":null`, true},
		{"file-null", `,"image_url":"drop","file_id":null`, true},
		{"file-number", `,"image_url":"drop","file_id":7`, true},
		{"file-bool", `,"image_url":"drop","file_id":true`, true},
		{"file-array", `,"image_url":"drop","file_id":["file-local"]`, true},
		{"file-object", `,"image_url":"drop","file_id":{"id":"file-local"}`, true},
		{"detail-null", `,"image_url":"drop","file_id":"file-local","detail":null`, true},
		{"detail-number", `,"image_url":"drop","file_id":"file-local","detail":7`, true},
		{"detail-bool", `,"image_url":"drop","file_id":"file-local","detail":true`, true},
		{"detail-array", `,"image_url":"drop","file_id":"file-local","detail":["auto"]`, true},
		{"detail-object", `,"image_url":"drop","file_id":"file-local","detail":{"value":"auto"}`, true},
		{"image-reference", `,"image_url":"drop","file_id":"file-local","tool_call_id":null`, true},
		{"nullable-url-reference", `,"image_url":null,"file_id":"file-local","tool_use_id":null`, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			body := " \n" + `{"model":"gpt-4o","input":[{"role":"user","content":[{"type":"input_image"` + tc.fields + `}]}],"tools":[{"type":"function","name":"keep"},{"type":"function","name":"drop"}],"opaque":{"name":"drop","n":1e+03,"s":"drop"}}` + "\n "
			want := strings.Replace(body, `,{"type":"function","name":"drop"}`, "", 1)
			assertNativeCompatOutgoing(t, g, calls, spy, "/v1/responses", body, want, tc.refuse)
		})
	}
}
