package server

import (
	"context"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

func (s *Server) emitUsage(ctx context.Context, traces ptrace.Traces, payloadBytes int64) {
	result := auth.FromContext(ctx)
	if result == nil {
		return
	}
	s.usage.EmitIngestion(result.OrgID, countDistinctTraces(traces), traces.SpanCount(), payloadBytes)
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
	seen := make(map[[16]byte]struct{})
	rss := traces.ResourceSpans()
	for i := 0; i < rss.Len(); i++ {
		sss := rss.At(i).ScopeSpans()
		for j := 0; j < sss.Len(); j++ {
			spans := sss.At(j).Spans()
			for k := 0; k < spans.Len(); k++ {
				seen[spans.At(k).TraceID()] = struct{}{}
			}
		}
	}
	return len(seen)
}
