package ingestbench

import (
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"math/rand"
	"strconv"
	"time"

	"github.com/google/uuid"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
)

// Payload sizes are synthetic input+output UTF-8 bytes, not measured customer
// distributions or full wire sizes. A fixed seed avoids unrealistically uniform
// compression while making every run reproducible without storing prompts.
var payloads = makePayloads()

func makePayloads() map[string]string {
	rng := rand.New(rand.NewSource(8687))
	alphabet := "abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:-_"
	out := map[string]string{}
	for _, size := range []struct {
		name string
		n    int
	}{{"small", 512}, {"medium", 4096}, {"large", 32768}} {
		b := make([]byte, size.n)
		for i := range b {
			b[i] = alphabet[rng.Intn(len(alphabet))]
		}
		out[size.name] = string(b)
	}
	return out
}

func (c Config) ids(seq int64) (pcommon.TraceID, pcommon.SpanID) {
	var trace pcommon.TraceID
	var span pcommon.SpanID
	prefix, _ := hex.DecodeString(c.RunID)
	copy(trace[:8], prefix)
	binary.BigEndian.PutUint64(trace[8:], uint64(seq/c.SpansPerTrace+1))
	binary.BigEndian.PutUint64(span[:], uint64(seq+1))
	return trace, span
}

func (c Config) traceString(seq int64) string {
	trace, _ := c.ids(seq)
	return uuid.UUID(trace).String()
}

func spanID(seq int64) string { return fmt.Sprintf("%016x", seq+1) }

func (c Config) payload(seq int64) string {
	name := c.Payload
	if name == "mixed" {
		// 70% small, 25% medium, 5% large, repeatable in every 20 spans.
		switch seq % 20 {
		case 14, 15, 16, 17, 18:
			name = "medium"
		case 19:
			name = "large"
		default:
			name = "small"
		}
	}
	return payloads[name]
}

func (c Config) batch(start int64, n int, now time.Time) ([]byte, error) {
	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	ra := rs.Resource().Attributes()
	ra.PutStr("service.name", "synthetic-otlp-benchmark")
	ra.PutStr("project_name", c.ProjectName)
	ra.PutStr("fi.project_id", c.ProjectID)
	ra.PutStr("fi.project_type", "OBSERVE")
	ss := rs.ScopeSpans().AppendEmpty()
	ss.Scope().SetName("fi.otlpbench.v1")
	spans := ss.Spans()
	spans.EnsureCapacity(n)
	for i := 0; i < n; i++ {
		seq := start + int64(i)
		traceID, sid := c.ids(seq)
		sp := spans.AppendEmpty()
		sp.SetTraceID(traceID)
		sp.SetSpanID(sid)
		sp.SetKind(ptrace.SpanKindInternal)
		sp.SetStartTimestamp(pcommon.NewTimestampFromTime(now.Add(-200 * time.Millisecond)))
		sp.SetEndTimestamp(pcommon.NewTimestampFromTime(now))
		a := sp.Attributes()
		a.PutStr("benchmark.run_id", c.RunID)
		a.PutStr("benchmark.seq", strconv.FormatInt(seq, 10))
		a.PutStr("session.id", fmt.Sprintf("bench-%s-%d", c.RunID, seq/(c.SpansPerTrace*c.TracesPerSession)))
		a.PutStr("user.id", fmt.Sprintf("synthetic-user-%d", seq/(c.SpansPerTrace*c.TracesPerSession)%1000))
		a.PutStr("benchmark.cohort", []string{"control", "variant"}[seq%2])
		if seq%c.SpansPerTrace == 0 {
			sp.SetName("synthetic.agent")
			a.PutStr("openinference.span.kind", "AGENT")
		} else {
			_, parent := c.ids(seq - seq%c.SpansPerTrace)
			sp.SetParentSpanID(parent)
			sp.SetName("synthetic.generation")
			a.PutStr("openinference.span.kind", "LLM")
		}
		a.PutStr("gen_ai.system", "synthetic")
		a.PutStr("gen_ai.request.model", "benchmark-model")
		a.PutStr("gen_ai.operation.name", "chat")
		a.PutInt("gen_ai.usage.input_tokens", 256+seq%512)
		a.PutInt("gen_ai.usage.output_tokens", 64+seq%128)
		payload := c.payload(seq)
		a.PutStr("input.value", payload[:len(payload)*3/4])
		a.PutStr("output.value", payload[len(payload)*3/4:])
		if seq%100 == 99 {
			sp.Status().SetCode(ptrace.StatusCodeError)
			sp.Status().SetMessage("synthetic tool timeout")
		} else {
			sp.Status().SetCode(ptrace.StatusCodeOk)
		}
	}
	return ptraceotlp.NewExportRequestFromTraces(traces).MarshalJSON()
}
