package ingestbench

import (
	"bytes"
	"compress/gzip"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"strconv"
	"time"

	"github.com/google/uuid"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
)

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

// namespaceBounds are the lowest and highest stored trace IDs of this run's
// namespace (trace ordinal 0 and the maximum ordinal), inclusive.
func (c Config) namespaceBounds() (string, string) {
	var lo, hi [16]byte
	prefix, _ := hex.DecodeString(c.RunID)
	copy(lo[:8], prefix)
	copy(hi[:8], prefix)
	for i := 8; i < 16; i++ {
		hi[i] = 0xff
	}
	return uuid.UUID(lo).String(), uuid.UUID(hi).String()
}

func spanID(seq int64) string { return fmt.Sprintf("%016x", seq+1) }

func sessionName(runID string, session int64) string {
	return fmt.Sprintf("bench-%s-%d", runID, session)
}

// batch builds the spans [start, start+n). The structure, sizes and text of
// each span depend only on the config (seed included) and the sequence number;
// timestamps derive from now.
func (c Config) batch(start int64, n int, now time.Time) (ptrace.Traces, []spanPlan) {
	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	ra := rs.Resource().Attributes()
	ra.PutStr("service.name", "synthetic-otlp-benchmark")
	ra.PutStr("project_name", c.ProjectName)
	ra.PutStr("fi.project_id", c.ProjectID)
	ra.PutStr("fi.project_type", "OBSERVE")
	ss := rs.ScopeSpans().AppendEmpty()
	ss.Scope().SetName("fi.otlpbench")
	ss.Scope().SetVersion(SchemaVersion)
	spans := ss.Spans()
	spans.EnsureCapacity(n)
	plans := make([]spanPlan, 0, n)
	for i := 0; i < n; i++ {
		seq := start + int64(i)
		p := c.plan(seq)
		plans = append(plans, p)
		traceID, sid := c.ids(seq)
		sp := spans.AppendEmpty()
		sp.SetTraceID(traceID)
		sp.SetSpanID(sid)
		if p.parent >= 0 {
			_, parent := c.ids(seq - p.position + p.parent)
			sp.SetParentSpanID(parent)
		}
		sp.SetKind(ptrace.SpanKindInternal)
		sp.SetStartTimestamp(pcommon.NewTimestampFromTime(now.Add(-p.duration)))
		sp.SetEndTimestamp(pcommon.NewTimestampFromTime(now))
		a := sp.Attributes()
		a.PutStr("benchmark.run_id", c.RunID)
		a.PutStr("benchmark.seq", strconv.FormatInt(seq, 10))
		a.PutStr("benchmark.cohort", p.cohort)
		a.PutStr("benchmark.payload_class", p.class)
		a.PutStr("session.id", sessionName(c.RunID, p.session))
		a.PutStr("user.id", "synthetic-user-"+strconv.FormatInt(p.user, 10))
		a.PutStr("openinference.span.kind", p.kind)
		switch p.kind {
		case "AGENT":
			sp.SetName("synthetic.agent.run")
		case "CHAIN":
			sp.SetName("synthetic.chain.step")
		case "TOOL":
			sp.SetName("synthetic.tool.call")
			a.PutStr("tool.name", "synthetic_tool_"+strconv.FormatInt(seq%7, 10))
		case "RETRIEVER":
			sp.SetName("synthetic.retrieval")
		default:
			sp.SetName("synthetic.generation")
			a.PutStr("gen_ai.system", "synthetic")
			a.PutStr("gen_ai.request.model", p.model)
			a.PutStr("gen_ai.operation.name", "chat")
			a.PutInt("gen_ai.usage.input_tokens", p.inputTokens)
			a.PutInt("gen_ai.usage.output_tokens", p.outputTokens)
		}
		a.PutStr("input.value", c.text(seq, 1, p.inputBytes))
		a.PutStr("output.value", c.text(seq, 2, p.outputBytes))
		if p.isError {
			sp.Status().SetCode(ptrace.StatusCodeError)
			sp.Status().SetMessage(p.errorMessage)
		} else {
			sp.Status().SetCode(ptrace.StatusCodeOk)
		}
	}
	return traces, plans
}

// encodedBatch is one export request ready to send.
type encodedBatch struct {
	// body is the encoded message as sent, compressed when gzip: the HTTP body,
	// or the gRPC message payload (protobuf).
	body         []byte
	uncompressed int // serialised size before compression
}

// buildRequest generates and encodes spans [start, start+n) in the configured
// encoding, including compression, so the timed send of every transport only
// transmits: a gRPC message is protobuf exactly like an OTLP/HTTP protobuf
// body, and the gRPC exporter sends these bytes without re-encoding them.
func (c Config) buildRequest(start int64, n int, now time.Time) (encodedBatch, []spanPlan, error) {
	traces, plans := c.batch(start, n, now)
	req := ptraceotlp.NewExportRequestFromTraces(traces)
	var raw []byte
	var err error
	switch c.Encoding {
	case EncodingJSON:
		raw, err = req.MarshalJSON()
	case EncodingProtobuf, EncodingGRPC:
		raw, err = req.MarshalProto()
	default:
		return encodedBatch{}, nil, fmt.Errorf("unknown encoding %q", c.Encoding)
	}
	if err != nil {
		return encodedBatch{}, nil, err
	}
	out := encodedBatch{body: raw, uncompressed: len(raw)}
	if c.Gzip {
		var buf bytes.Buffer
		buf.Grow(len(raw)/3 + 64)
		zw := gzip.NewWriter(&buf)
		if _, err := zw.Write(raw); err != nil {
			return encodedBatch{}, nil, err
		}
		if err := zw.Close(); err != nil {
			return encodedBatch{}, nil, err
		}
		out.body = buf.Bytes()
	}
	return out, plans, nil
}
