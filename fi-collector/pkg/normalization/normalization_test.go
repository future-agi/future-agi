package normalization

import (
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

func TestSharedRegistryAndVectors(t *testing.T) {
	path := "../../../futureagi/tracer/utils/normalization/"
	raw, err := os.ReadFile(path + "profiles.json")
	if err != nil {
		t.Fatal(err)
	}
	if string(raw) != profileJSON {
		t.Fatal("run scripts/sync_trace_normalization.py: Go/Python contract drift")
	}
	raw, err = os.ReadFile(path + "vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	var vectors []struct {
		Name                 string
		Scope                struct{ Name, Version string }
		Attributes, Expected map[string]any
	}
	if err = json.Unmarshal(raw, &vectors); err != nil {
		t.Fatal(err)
	}
	for _, v := range vectors {
		t.Run(v.Name, func(t *testing.T) {
			scope := pcommon.NewInstrumentationScope()
			scope.SetName(v.Scope.Name)
			scope.SetVersion(v.Scope.Version)
			attrs := pcommon.NewMap()
			_ = attrs.FromRaw(v.Attributes)
			Normalize(attrs, ProfileFor(scope))
			if !reflect.DeepEqual(attrs.AsRaw(), v.Expected) {
				t.Fatalf("got %#v; want %#v", attrs.AsRaw(), v.Expected)
			}
			Normalize(attrs, ProfileFor(scope))
			if !reflect.DeepEqual(attrs.AsRaw(), v.Expected) {
				t.Fatal("normalization not idempotent")
			}
		})
	}
}

func TestNonAdditiveMovesEveryUsageAliasWithoutFakeCost(t *testing.T) {
	attrs := pcommon.NewMap()
	attrs.PutStr("fi.usage.contribution", "none")
	for _, k := range []string{"gen_ai.usage.input_tokens", "gen_ai.usage.cache_read_tokens", "gen_ai.usage.total_cost", "gen_ai.cost.total", "llm.cost.prompt", "llm.token_count.prompt", "llm.usage.completion_tokens"} {
		attrs.PutInt(k, 15)
	}
	Normalize(attrs, nil)
	for _, k := range []string{"gen_ai.usage.input_tokens", "gen_ai.cost.total", "llm.token_count.prompt"} {
		if _, ok := attrs.Get(k); ok {
			t.Errorf("additive key survived: %s", k)
		}
		if v, ok := attrs.Get("fi.source.summary." + k); !ok || v.Int() != 15 {
			t.Errorf("lost raw %s", k)
		}
	}
	if String(attrs, "fi.cost.kind") != "unknown" {
		t.Fatal("unknown source cost must not become confirmed zero")
	}
}

func TestExplicitSessionAndKindWin(t *testing.T) {
	scope := pcommon.NewInstrumentationScope()
	scope.SetName("gemini-cli")
	scope.SetVersion("v1")
	attrs := pcommon.NewMap()
	attrs.PutStr("gen_ai.operation.name", "llm_call")
	attrs.PutStr("session.id", "explicit")
	attrs.PutStr("gen_ai.conversation.id", "fallback")
	attrs.PutStr("openinference.span.kind", "TOOL")
	Normalize(attrs, ProfileFor(scope))
	if String(attrs, "session.id") != "explicit" || String(attrs, "fi.span.kind") != "" {
		t.Fatal(attrs.AsRaw())
	}
}

func TestDetailsAreBoundedAndIdempotent(t *testing.T) {
	span := ptrace.NewSpan()
	span.Attributes().PutStr("fi.normalization.profile", "test")
	for i := 0; i < 70; i++ {
		ev := span.Events().AppendEmpty()
		ev.SetName("compaction\x01")
		ev.Attributes().PutStr("exception.type", "Error")
		ev.Attributes().PutStr("exception.message", "private-not-persisted")
		ev.Attributes().PutStr("unknown", strings.Repeat("x", 8192))
	}
	for i := 0; i < 35; i++ {
		l := span.Links().AppendEmpty()
		l.SetTraceID(pcommon.TraceID{1})
		l.SetSpanID(pcommon.SpanID{2})
		l.TraceState().FromRaw("vendor=private")
		l.Attributes().PutStr("fi.agent.caused_by.work_id", "opaque")
		l.Attributes().PutStr("unknown", "private")
	}
	StoreDetails(span)
	before := span.Attributes().AsRaw()
	if span.Events().Len() != MaxEvents || span.DroppedEventsCount() != 6 {
		t.Fatal("event cap")
	}
	if span.Events().At(0).Attributes().Len() != 1 || span.Events().At(0).DroppedAttributesCount() != 2 {
		t.Fatal("event allowlist")
	}
	links, _ := span.Attributes().Get("fi.otel.links")
	if links.Slice().Len() != MaxLinks {
		t.Fatal("link cap")
	}
	StoreDetails(span)
	if !reflect.DeepEqual(before, span.Attributes().AsRaw()) {
		t.Fatal("details not idempotent")
	}
	b, _ := json.Marshal(before)
	if strings.Contains(string(b), "private") {
		t.Fatal("unapproved content in links")
	}
}

func TestImmutableContractIsHeld(t *testing.T) {
	for _, k := range registry.AdmissionKeys {
		td := ptrace.NewTraces()
		sp := td.ResourceSpans().AppendEmpty().ScopeSpans().AppendEmpty().Spans().AppendEmpty()
		sp.Attributes().PutStr(k, "") // even malformed/empty identity must not bypass
		if !RequiresAdmission(td) {
			t.Fatalf("not held: %s", k)
		}
	}
	for _, v := range []string{"turn", "request", "attempt", "invalid"} {
		td := ptrace.NewTraces()
		sp := td.ResourceSpans().AppendEmpty().ScopeSpans().AppendEmpty().Spans().AppendEmpty()
		sp.Attributes().PutStr("fi.usage.contribution", v)
		if !RequiresAdmission(td) {
			t.Fatalf("not held: %s", v)
		}
	}
}

func TestLegacyEventNamesAreNotTruncated(t *testing.T) {
	span := ptrace.NewSpan()
	for i := 0; i < 70; i++ {
		ev := span.Events().AppendEmpty()
		ev.SetName(strings.Repeat("x", 5000))
		ev.Attributes().PutStr("exception.type", strings.Repeat("x", 5000))
	}
	StoreDetails(span)
	if span.Events().Len() != 70 || span.Events().At(0).Name() != strings.Repeat("x", 5000) {
		t.Fatal("changed legacy event names")
	}
	if span.Events().At(0).Attributes().Len() != 0 {
		t.Fatal("unbounded newly stored event attribute")
	}
}
