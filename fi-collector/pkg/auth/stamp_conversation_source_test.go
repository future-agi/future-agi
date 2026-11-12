package auth

import (
	"context"
	"errors"
	"fmt"
	"testing"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

// batchSpan is one span of a test batch: whether it has a parent, and its
// attributes (set by edit).
type batchSpan struct {
	child bool
	edit  func(pcommon.Map)
}

func spanStr(key, value string) func(pcommon.Map) {
	return func(m pcommon.Map) { m.PutStr(key, value) }
}

func spanEdits(edits ...func(pcommon.Map)) func(pcommon.Map) {
	return func(m pcommon.Map) {
		for _, e := range edits {
			e(m)
		}
	}
}

// addSpansResource appends one ResourceSpans named project holding spans,
// in order, in one ScopeSpans.
func addSpansResource(td ptrace.Traces, project string, spans ...batchSpan) {
	rs := td.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("project_name", project)
	ss := rs.ScopeSpans().AppendEmpty().Spans()
	for _, s := range spans {
		span := ss.AppendEmpty()
		span.SetName("s")
		span.SetSpanID(pcommon.SpanID{1, byte(ss.Len())})
		if s.child {
			span.SetParentSpanID(pcommon.SpanID{9, 9})
		}
		if s.edit != nil {
			s.edit(span.Attributes())
		}
	}
}

func rootSpan(edit func(pcommon.Map)) batchSpan  { return batchSpan{edit: edit} }
func childSpan(edit func(pcommon.Map)) batchSpan { return batchSpan{child: true, edit: edit} }

// OBS-SW-69: a project's first root span decides whether it is a voice
// project, with the span kind read the way the exporter stores
// observation_type (converter.go resolveObservationType).
func TestConversationRootProjectsReadsFirstRootSpanKind(t *testing.T) {
	for _, tc := range []struct {
		name  string
		spans []batchSpan
		want  bool
	}{
		{"fi.span.kind uppercase", []batchSpan{rootSpan(spanStr("fi.span.kind", "CONVERSATION"))}, true},
		{"fi.span.kind lowercase with spaces", []batchSpan{rootSpan(spanStr("fi.span.kind", " conversation "))}, true},
		{"gen_ai.span.kind", []batchSpan{rootSpan(spanStr("gen_ai.span.kind", "conversation"))}, true},
		{"llm.request.type", []batchSpan{rootSpan(spanStr("llm.request.type", "Conversation"))}, true},
		{"openinference.span.kind", []batchSpan{rootSpan(spanStr("openinference.span.kind", "CONVERSATION"))}, true},
		{"operation name when no kind", []batchSpan{rootSpan(spanStr("gen_ai.operation.name", "conversation"))}, true},
		{"empty kind is skipped", []batchSpan{rootSpan(spanEdits(spanStr("fi.span.kind", ""), spanStr("gen_ai.span.kind", "CONVERSATION")))}, true},
		{"non-string kind is skipped", []batchSpan{rootSpan(spanEdits(
			func(m pcommon.Map) { m.PutInt("fi.span.kind", 7) },
			spanStr("openinference.span.kind", "CONVERSATION"),
		))}, true},
		{"first non-empty kind wins", []batchSpan{rootSpan(spanEdits(spanStr("fi.span.kind", "LLM"), spanStr("openinference.span.kind", "CONVERSATION")))}, false},
		{"kind wins over operation name", []batchSpan{rootSpan(spanEdits(spanStr("fi.span.kind", "AGENT"), spanStr("gen_ai.operation.name", "conversation")))}, false},
		{"llm root", []batchSpan{rootSpan(spanStr("fi.span.kind", "LLM"))}, false},
		{"root without kind", []batchSpan{rootSpan(nil)}, false},
		{"conversation child only", []batchSpan{childSpan(spanStr("fi.span.kind", "CONVERSATION"))}, false},
		{"first root llm, later root conversation", []batchSpan{rootSpan(spanStr("fi.span.kind", "LLM")), rootSpan(spanStr("fi.span.kind", "CONVERSATION"))}, false},
		{"first root conversation, later root llm", []batchSpan{rootSpan(spanStr("fi.span.kind", "CONVERSATION")), rootSpan(spanStr("fi.span.kind", "LLM"))}, true},
		{"conversation child before llm root", []batchSpan{childSpan(spanStr("fi.span.kind", "CONVERSATION")), rootSpan(spanStr("fi.span.kind", "LLM"))}, false},
		{"llm child before conversation root", []batchSpan{childSpan(spanStr("fi.span.kind", "LLM")), rootSpan(spanStr("fi.span.kind", "CONVERSATION"))}, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			td := ptrace.NewTraces()
			addSpansResource(td, "p", tc.spans...)
			_, got := conversationRootProjects(td.ResourceSpans())["p"]
			if got != tc.want {
				t.Errorf("marked = %v, want %v", got, tc.want)
			}
		})
	}
}

// Each name is decided on its own, by its first root span across the whole
// batch: a later ResourceSpans of a decided name does not change it, and one
// with only child spans leaves the name undecided.
func TestConversationRootProjectsDecidesEachNameAcrossResources(t *testing.T) {
	td := ptrace.NewTraces()
	addSpansResource(td, "voice", childSpan(spanStr("fi.span.kind", "LLM")))
	addSpansResource(td, "text", rootSpan(spanStr("fi.span.kind", "LLM")))
	addSpansResource(td, "voice", rootSpan(spanStr("fi.span.kind", "CONVERSATION")), childSpan(spanStr("fi.span.kind", "LLM")))
	addSpansResource(td, "text", rootSpan(spanStr("fi.span.kind", "CONVERSATION")))
	addSpansResource(td, "late", childSpan(spanStr("fi.span.kind", "CONVERSATION")))

	got := conversationRootProjects(td.ResourceSpans())
	if len(got) != 1 {
		t.Fatalf("marked = %v, want only voice", got)
	}
	if _, ok := got["voice"]; !ok {
		t.Fatalf("marked = %v, want voice", got)
	}
}

func TestAutoCreateSourceFollowsConversationRoots(t *testing.T) {
	marked := withConversationRoots(context.Background(), map[string]struct{}{"voice": {}})
	for _, tc := range []struct {
		name string
		ctx  context.Context
		want string
	}{
		{"voice", marked, "simulator"},
		{"text", marked, "prototype"},
		{"voice", context.Background(), "prototype"},
		{"voice", withConversationRoots(context.Background(), nil), "prototype"},
	} {
		if got := autoCreateSource(tc.ctx, tc.name); got != tc.want {
			t.Errorf("autoCreateSource(%s) = %q, want %q", tc.name, got, tc.want)
		}
	}
}

// sourceRecordingResolver knows some projects and records, for each project it
// is asked to create, the source PGResolver would insert it with.
type sourceRecordingResolver struct {
	known   map[string]string
	sources map[string]string
}

func (r *sourceRecordingResolver) ResolveProjects(_ context.Context, _, _ string, names []string) (map[string]string, error) {
	out := make(map[string]string)
	for _, n := range names {
		if id, ok := r.known[n]; ok {
			out[n] = id
		}
	}
	return out, nil
}

func (r *sourceRecordingResolver) GetOrCreateProject(ctx context.Context, _, _, name, _ string) (string, error) {
	r.sources[name] = autoCreateSource(ctx, name)
	return "created-" + name, nil
}

// OBS-SW-69: the stamp's mark reaches project auto-create through
// ResolveProjectsForKey, so a new project whose first root span is a
// conversation is created as a voice project and every other one as before.
// A project that already exists is not created at all.
func TestStampAutoCreatesConversationRootProjectsAsSimulator(t *testing.T) {
	resolver := &sourceRecordingResolver{known: map[string]string{"known-voice": "id-known"}, sources: map[string]string{}}
	a, result, ck := newTypedAuth(resolver)

	td := ptrace.NewTraces()
	addSpansResource(td, "voice", rootSpan(spanStr("fi.span.kind", "CONVERSATION")), childSpan(spanStr("fi.span.kind", "LLM")))
	addSpansResource(td, "text", rootSpan(spanStr("fi.span.kind", "LLM")))
	addSpansResource(td, "late-root", childSpan(spanStr("fi.span.kind", "CONVERSATION")))
	addSpansResource(td, "known-voice", rootSpan(spanStr("fi.span.kind", "CONVERSATION")))

	dropped, err := StampResourceAttrs(context.Background(), a, ck, td, result)
	if err != nil || dropped != 0 {
		t.Fatalf("stamp: dropped=%d err=%v", dropped, err)
	}
	want := map[string]string{"voice": "simulator", "text": "prototype", "late-root": "prototype"}
	if len(resolver.sources) != len(want) {
		t.Fatalf("created = %v, want %v", resolver.sources, want)
	}
	for name, source := range want {
		if resolver.sources[name] != source {
			t.Errorf("%s created with source %q, want %q", name, resolver.sources[name], source)
		}
	}
	if id, _ := result.GetProjectInWorkspace(result.WorkspaceID, "voice"); id != "created-voice" {
		t.Errorf("voice resolved to %q, want created-voice", id)
	}
}

// The bounded path that creates only the first MaxNewProjectsPerRequest new
// names carries the mark too.
func TestStampBoundedAutoCreateKeepsConversationRoots(t *testing.T) {
	resolver := &sourceRecordingResolver{sources: map[string]string{}}
	a, result, ck := newTypedAuth(resolver)

	td := ptrace.NewTraces()
	addSpansResource(td, "a-voice", rootSpan(spanStr("fi.span.kind", "CONVERSATION")))
	for i := 0; i < MaxNewProjectsPerRequest; i++ {
		addSpansResource(td, fmt.Sprintf("p-%02d", i), rootSpan(spanStr("fi.span.kind", "LLM")))
	}

	if _, err := StampResourceAttrs(context.Background(), a, ck, td, result); !errors.Is(err, ErrTooManyNewProjects) {
		t.Fatalf("stamp: %v, want ErrTooManyNewProjects", err)
	}
	if len(resolver.sources) != MaxNewProjectsPerRequest {
		t.Fatalf("%d projects created, want %d", len(resolver.sources), MaxNewProjectsPerRequest)
	}
	if got := resolver.sources["a-voice"]; got != "simulator" {
		t.Errorf("a-voice created with source %q, want simulator", got)
	}
	if got := resolver.sources["p-00"]; got != "prototype" {
		t.Errorf("p-00 created with source %q, want prototype", got)
	}
}
