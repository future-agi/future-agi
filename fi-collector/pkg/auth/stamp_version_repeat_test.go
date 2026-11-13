package auth

import (
	"testing"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

func countAttrKey(attrs pcommon.Map, key string) int {
	n := 0
	attrs.Range(func(k string, _ pcommon.Value) bool {
		if k == key {
			n++
		}
		return true
	})
	return n
}

// A producer can repeat a resource attribute key on the wire. Every copy of
// the internal version stamp must be cleared, not just the first, before the
// scoped resolver decides whether to stamp a version.
func TestClearInternalVersionStampRemovesRepeatedEntries(t *testing.T) {
	raw := `{"resourceSpans":[{"resource":{"attributes":[` +
		`{"key":"fi.project_version_id","value":{"stringValue":"11111111-1111-1111-1111-111111111111"}},` +
		`{"key":"project_name","value":{"stringValue":"demo"}},` +
		`{"key":"fi.project_version_id","value":{"stringValue":"22222222-2222-2222-2222-222222222222"}},` +
		`{"key":"fi.project_version_id","value":{"stringValue":"33333333-3333-3333-3333-333333333333"}}` +
		`]},"scopeSpans":[]}]}`
	td, err := (&ptrace.JSONUnmarshaler{}).UnmarshalTraces([]byte(raw))
	if err != nil {
		t.Fatal(err)
	}
	attrs := td.ResourceSpans().At(0).Resource().Attributes()
	if got := countAttrKey(attrs, "fi.project_version_id"); got != 3 {
		t.Fatalf("decoder kept %d copies of the repeated key, want 3 (test premise)", got)
	}

	clearInternalVersionStamp(attrs)

	if got := countAttrKey(attrs, "fi.project_version_id"); got != 0 {
		t.Fatalf("%d producer-supplied version stamps survived, want 0", got)
	}
	if v, ok := attrs.Get("project_name"); !ok || v.Str() != "demo" {
		t.Fatalf("unrelated attribute changed: %v %v", v, ok)
	}
}
