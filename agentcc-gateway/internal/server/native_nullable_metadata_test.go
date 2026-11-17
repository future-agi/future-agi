package server

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func nativeNullableMessagesBody(block, selection string) string {
	return " \n" + `{"model":"` + postBlockClaude + `","max_tokens":64,"messages":[{"role":"user","content":[` + block + `]}],"tools":[{"name":"keep","input_schema":{}},{"name":"drop","input_schema":{}}],"opaque":{"name":"drop","n":1e+03,"s":"drop"}` + selection + `}` + "\n "
}

func TestNativePolicyDocumentNullableMetadataOutgoing(t *testing.T) {
	g, calls, spy := nativeCompatGateway(t, config.ToolPolicyConfig{Enabled: true, Deny: []string{"drop"}, DefaultAction: "strip"})
	values := []struct{ name, raw string }{
		{"omitted", ""}, {"null", "null"}, {"string", `"drop"`},
	}
	for _, title := range values {
		for _, contextValue := range values {
			t.Run("title-"+title.name+"/context-"+contextValue.name, func(t *testing.T) {
				metadata := ""
				if title.raw != "" {
					metadata += `,"title":` + title.raw
				}
				if contextValue.raw != "" {
					metadata += `,"context":` + contextValue.raw
				}
				block := `{"type":"document"` + metadata + `,"source":{"type":"text","media_type":"text/plain","data":"hello"}}`
				body := nativeNullableMessagesBody(block, "")
				want := strings.Replace(body, `,{"name":"drop","input_schema":{}}`, "", 1)
				assertNativeCompatOutgoing(t, g, calls, spy, "/v1/messages", body, want, false)
			})
		}
	}
}

func TestNativePolicyDocumentMetadataInvalidOutgoing(t *testing.T) {
	g, calls, spy := nativeCompatGateway(t, config.ToolPolicyConfig{Enabled: true, Deny: []string{"drop"}, DefaultAction: "strip"})
	for _, field := range []string{"title", "context"} {
		for _, value := range []struct{ name, raw string }{
			{"number", "7"}, {"bool", "true"}, {"array", `["drop"]`}, {"object", `{"text":"drop"}`},
		} {
			t.Run(field+"/"+value.name, func(t *testing.T) {
				title, contextValue := `"title"`, `"context"`
				if field == "title" {
					title = value.raw
				} else {
					contextValue = value.raw
				}
				block := `{"type":"document","title":` + title + `,"context":` + contextValue + `,"source":{"type":"text","media_type":"text/plain","data":"hello"}}`
				assertNativeCompatOutgoing(t, g, calls, spy, "/v1/messages", nativeNullableMessagesBody(block, ""), "", true)
			})
		}
	}
}

func TestNativePolicyDocumentRequiredFieldsClosedOutgoing(t *testing.T) {
	g, calls, spy := nativeCompatGateway(t, config.ToolPolicyConfig{Enabled: true, Deny: []string{"drop"}, DefaultAction: "strip"})
	for _, tc := range []struct{ name, source, extra, selection string }{
		{"source-null", `null`, "", ""},
		{"text-data-null", `{"type":"text","media_type":"text/plain","data":null}`, "", ""},
		{"document-reference-null", `{"type":"text","media_type":"text/plain","data":"hello"}`, `,"tool_use_id":null`, ""},
		{"selector-name-null", `{"type":"text","media_type":"text/plain","data":"hello"}`, "", `,"tool_choice":{"type":"tool","name":null}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Valid string metadata keeps each refusal independent of nullable metadata.
			block := `{"type":"document","title":"title","context":"context","source":` + tc.source + tc.extra + `}`
			assertNativeCompatOutgoing(t, g, calls, spy, "/v1/messages", nativeNullableMessagesBody(block, tc.selection), "", true)
		})
	}
}
