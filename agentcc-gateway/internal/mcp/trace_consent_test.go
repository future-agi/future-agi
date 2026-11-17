package mcp

import (
	"encoding/json"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"testing"
)

func TestTraceCaptureExplicitOrgConsent(t *testing.T) {
	for _, raw := range []string{`{}`, `{"privacy":{}}`, `{"privacy":{"content_mode":"full"}}`, `{"privacy":{"trace_content":false,"content_mode":"full"}}`} {
		e := &traceCapture{}
		h := tracedHarness(t, e)
		h.onHook(func(hook string, cc *CallContext) {
			attribute("full")(hook, cc)
			if hook == hookEvidenceMeta {
				var cfg tenant.OrgConfig
				if json.Unmarshal([]byte(raw), &cfg) != nil {
					t.Fatal("fixture decode failed")
				}
				cc.OrgConfig = &cfg
			}
		})
		h.call(t, "up_ok", map[string]any{"safe": "control"})
		for _, s := range e.spans {
			if _, ok := s.Attributes["gen_ai.tool.call.arguments"]; ok {
				t.Error("organization without explicit consent exported tool inputs")
			}
			if _, ok := s.Attributes["gen_ai.tool.call.result"]; ok {
				t.Error("organization without explicit consent exported tool outputs")
			}
		}
	}
}
