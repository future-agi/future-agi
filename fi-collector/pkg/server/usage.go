package server

import (
	"context"
	"encoding/hex"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

func (s *Server) emitUsage(ctx context.Context, traces ptrace.Traces, payloadBytes int64) {
	result := auth.FromContext(ctx)
	if result == nil {
		return
	}
	// Single-trace export = a provider-pull call (deterministic trace id, re-polled):
	// key billing on it so re-polls bill once. Multi-trace SDK batch → random id.
	ids := distinctTraceIDs(traces)
	dedupKey := ""
	if len(ids) == 1 {
		dedupKey = hex.EncodeToString(ids[0][:])
	}
	s.usage.EmitIngestion(result.OrgID, len(ids), traces.SpanCount(), payloadBytes, dedupKey)
}

func (s *Server) checkUsage(ctx context.Context) (auth.CheckResult, bool) {
	result := auth.FromContext(ctx)
	if result == nil {
		return auth.CheckResult{Allowed: true}, true
	}
	check := s.metering.CheckUsage(ctx, result.OrgID, "tracing_event", 1)
	return check, check.Allowed
}

func countDistinctTraces(traces ptrace.Traces) int {
	return len(distinctTraceIDs(traces))
}

// distinctTraceIDs returns the unique trace ids in the batch, in first-seen order.
func distinctTraceIDs(traces ptrace.Traces) [][16]byte {
	seen := make(map[[16]byte]struct{})
	var ids [][16]byte
	rss := traces.ResourceSpans()
	for i := 0; i < rss.Len(); i++ {
		sss := rss.At(i).ScopeSpans()
		for j := 0; j < sss.Len(); j++ {
			spans := sss.At(j).Spans()
			for k := 0; k < spans.Len(); k++ {
				id := spans.At(k).TraceID()
				if _, ok := seen[id]; !ok {
					seen[id] = struct{}{}
					ids = append(ids, id)
				}
			}
		}
	}
	return ids
}
