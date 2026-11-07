package clickhouse25exporter

import (
	"testing"

	"go.opentelemetry.io/collector/pdata/pcommon"
)

func TestUnqualifiedNativeProducerKeepsLegacyIdentityAndKind(t *testing.T) {
	for _, scope := range [][2]string{{"", ""}, {"ordinary", "13.3.0"}, {"com.anthropic.sdk.csharp", "13.3.1"}} {
		t.Run(scope[0]+"/"+scope[1], func(t *testing.T) {
			td := contentSpan(func(attrs pcommon.Map) {
				attrs.PutStr("gen_ai.operation.name", "invoke_agent")
				attrs.PutStr("gen_ai.request.model", "claude-test")
				attrs.PutStr("gen_ai.conversation.id", "unqualified-session")
			})
			rs := td.ResourceSpans().At(0)
			rs.Resource().Attributes().PutStr("fi.project_id", orProjectID)
			rs.ScopeSpans().At(0).Scope().SetName(scope[0])
			rs.ScopeSpans().At(0).Scope().SetVersion(scope[1])
			row := convertOne(t, td)
			if row["trace_session_id"] != nil || row["observation_type"] != "unknown" {
				t.Fatalf("unknown producer must retain legacy semantics, session=%v kind=%v", row["trace_session_id"], row["observation_type"])
			}
		})
	}
}
