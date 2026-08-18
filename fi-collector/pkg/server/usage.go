package server

import (
	"context"
	"crypto/sha256"
	"encoding/hex"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

type usageRecord struct {
	orgID        string
	numTraces    int
	numSpans     int
	payloadBytes int64
	dedupKey     string
}

func usageFromContext(ctx context.Context, traces ptrace.Traces, payload []byte) *usageRecord {
	result := auth.FromContext(ctx)
	if result == nil {
		return nil
	}
	// Single-trace exports use the trace id. Multi-trace batches use the wire
	// payload hash so a timeout/retry after durable acceptance is billed once.
	ids := distinctTraceIDs(traces)
	dedupKey := usageDedupKey(ids, payload)
	return &usageRecord{
		orgID:        result.OrgID,
		numTraces:    len(ids),
		numSpans:     traces.SpanCount(),
		payloadBytes: int64(len(payload)),
		dedupKey:     dedupKey,
	}
}

func usageDedupKey(ids [][16]byte, payload []byte) string {
	if len(ids) == 1 {
		return hex.EncodeToString(ids[0][:])
	} else if len(payload) > 0 {
		sum := sha256.Sum256(payload)
		return hex.EncodeToString(sum[:])
	}
	return ""
}

func (s *Server) emitUsage(record *usageRecord) {
	if record == nil {
		return
	}
	s.usage.EmitIngestion(
		record.orgID,
		record.numTraces,
		record.numSpans,
		record.payloadBytes,
		record.dedupKey,
	)
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
