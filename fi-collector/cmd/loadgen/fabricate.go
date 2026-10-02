package main

import (
	"fmt"
	"math/rand"
	"time"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

// must match converter.go resAttrProjectTy
const resAttrProjectType = "project_type"

// Span shapes. `mixed` picks one of the others per-trace.
const (
	shapeLLM       = "llm"
	shapeVoice     = "voice"
	shapeAgentDeep = "agent-deep"
	shapeFatAttrs  = "fat-attrs"
	shapeMixed     = "mixed"
)

// voice transcript sizing and routing. The key carries an overflow prefix
// (adapter.overflowKeyPrefixes "llm.messages") so the converter routes the
// payload to attributes_extra rather than a typed Map.
const (
	attrVoiceTranscript = "llm.messages.transcript"
	voiceTranscriptSize = 1258291 // 1.2 MiB, > 1<<20
	fatAttrsCount       = 80
)

// Span-attribute keys the converter reads
const (
	attrSessionID    = "session.id"
	attrSpanKind     = "fi.span.kind"
	attrInputValue   = "input.value"
	attrOutputValue  = "output.value"
	attrModelName    = "llm.model_name"
	attrInputTokens  = "gen_ai.usage.input_tokens"
	attrOutputTokens = "gen_ai.usage.output_tokens"
)

type FabricateConfig struct {
	ProjectID, OrgID, Shape         string
	Traces, SpansPerTrace, Sessions int
	Start, End                      time.Time
}

func fabricateBatch(cfg FabricateConfig, r *rand.Rand) ptrace.Traces {
	td := ptrace.NewTraces()
	rs := td.ResourceSpans().AppendEmpty()
	ra := rs.Resource().Attributes()
	ra.PutStr("fi.project_id", cfg.ProjectID)
	ra.PutStr("fi.org_id", cfg.OrgID)
	ra.PutStr("service.name", "loadgen")
	ra.PutStr("fi.semconv", "fi_native")
	ra.PutStr(resAttrProjectType, "observe")
	ss := rs.ScopeSpans().AppendEmpty().Spans()

	window := cfg.End.Sub(cfg.Start)
	for t := 0; t < cfg.Traces; t++ {
		traceID := randTraceID(r)
		rootID := randSpanID(r)
		rootStart := cfg.Start.Add(time.Duration(r.Int63n(int64(window))))
		sessionID := fmt.Sprintf("session-%08d", r.Intn(cfg.Sessions))

		shape := cfg.Shape
		if shape == shapeMixed {
			shape = pickMixedShape(r)
		}
		spans := cfg.SpansPerTrace
		if shape == shapeAgentDeep {
			spans = 50 + r.Intn(151) // [50,200]
		}

		appendSpan(ss, r, spanSpec{
			traceID: traceID, spanID: rootID, name: "root",
			start: rootStart, sessionID: sessionID, shape: shape, root: true,
		})
		for c := 1; c < spans; c++ {
			start := rootStart.Add(time.Duration(c) * time.Second)
			if shape == shapeAgentDeep {
				switch c {
				case 1:
					start = rootStart.Add(-time.Second) // child precedes root
				case 2:
					start = rootStart // child ties root
				}
			}
			appendSpan(ss, r, spanSpec{
				traceID: traceID, spanID: randSpanID(r), parentID: rootID,
				name:      fmt.Sprintf("child-%d", c),
				start:     start,
				sessionID: sessionID, shape: shape, llm: r.Intn(5) == 0,
			})
		}
	}
	return td
}

// pickMixedShape draws a per-trace shape for the `mixed` profile: mostly the
// cheap llm shape, with the three stress shapes at 10% each.
func pickMixedShape(r *rand.Rand) string {
	switch n := r.Intn(100); {
	case n < 70:
		return shapeLLM
	case n < 80:
		return shapeVoice
	case n < 90:
		return shapeAgentDeep
	default:
		return shapeFatAttrs
	}
}

// voiceTranscript builds a ~1.2 MiB lowercase-letter payload from r only.
func voiceTranscript(r *rand.Rand) string {
	b := make([]byte, voiceTranscriptSize)
	r.Read(b)
	for i := range b {
		b[i] = 'a' + b[i]%26
	}
	return string(b)
}

// spanSpec is the fabrication input for a single span: identifiers, placement,
// the session it belongs to, and whether it carries LLM attributes.
type spanSpec struct {
	traceID   pcommon.TraceID
	spanID    pcommon.SpanID
	parentID  pcommon.SpanID
	name      string
	start     time.Time
	sessionID string
	shape     string
	root      bool
	llm       bool
}

// appendSpan materialises one span onto ss.
func appendSpan(ss ptrace.SpanSlice, r *rand.Rand, spec spanSpec) {
	s := ss.AppendEmpty()
	s.SetTraceID(spec.traceID)
	s.SetSpanID(spec.spanID)
	if !spec.parentID.IsEmpty() {
		s.SetParentSpanID(spec.parentID)
	}
	s.SetName(spec.name)
	s.SetStartTimestamp(pcommon.NewTimestampFromTime(spec.start))
	latency := time.Duration(r.Intn(2000)+1) * time.Millisecond
	s.SetEndTimestamp(pcommon.NewTimestampFromTime(spec.start.Add(latency)))

	a := s.Attributes()
	a.PutStr(attrSessionID, spec.sessionID)
	a.PutStr(attrInputValue, "prompt for "+spec.name)
	a.PutStr(attrOutputValue, "response for "+spec.name)
	if spec.llm {
		a.PutStr(attrSpanKind, "LLM")
		a.PutStr(attrModelName, "gpt-4o-mini")
		a.PutInt(attrInputTokens, int64(r.Intn(500)+1))
		a.PutInt(attrOutputTokens, int64(r.Intn(500)+1))
	}

	switch spec.shape {
	case shapeVoice:
		if spec.root {
			a.PutStr(attrVoiceTranscript, voiceTranscript(r))
		}
	case shapeFatAttrs:
		for k := 0; k < fatAttrsCount; k++ {
			a.PutInt(fmt.Sprintf("attr.k%02d", k), r.Int63())
		}
	}
}

// randTraceID fills an OTel TraceID from r only (seeded PRNG).
func randTraceID(r *rand.Rand) pcommon.TraceID {
	var b [16]byte
	r.Read(b[:])
	return pcommon.TraceID(b)
}

func randSpanID(r *rand.Rand) pcommon.SpanID {
	var b [8]byte
	r.Read(b[:])
	return pcommon.SpanID(b)
}

func mustTime(s string) time.Time {
	t, err := time.Parse(time.RFC3339, s)
	if err != nil {
		panic(err)
	}
	return t
}
