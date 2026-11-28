package server

import (
	"context"
	"encoding/hex"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/trajectory"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

// emitUsage accounts only for accepted traces, after project stamping/drops.
// Storage-mode usage is their canonical OTLP protobuf size (including stamped
// resource attributes), not HTTP wire/JSON bytes or compressed ClickHouse size.
// This keeps gRPC's existing basis; HTTP intentionally changes even on full
// success from decompressed-body length to accepted semantic protobuf bytes.
func (s *Server) emitUsage(ctx context.Context, traces ptrace.Traces) {
	s.emitUsageWithReport(ctx, traces, trajectory.UsageReport{})
}

// pendingUsage captures a request's billing inputs at enqueue time so that when
// Phase 1 trajectory deduplication is active, emission is deferred until
// drainNow knows whether `trajectory_items` committed or fell back to inline.
type pendingUsage struct {
	orgID      string
	traceCount int
	spanCount  int
	rawBytes   int64
	dedupKey   string
	report     trajectory.UsageReport
}

func (s *Server) buildPendingUsage(ctx context.Context, traces ptrace.Traces, report trajectory.UsageReport) *pendingUsage {
	result := auth.FromContext(ctx)
	if result == nil || traces.SpanCount() == 0 {
		return nil
	}
	ids := distinctTraceIDs(traces)
	dedupKey := ""
	if len(ids) == 1 {
		dedupKey = hex.EncodeToString(ids[0][:])
	}
	rawBytes := int64((&ptrace.ProtoMarshaler{}).TracesSize(traces))
	report.RawBytes = rawBytes
	return &pendingUsage{
		orgID:      result.OrgID,
		traceCount: len(ids),
		spanCount:  traces.SpanCount(),
		rawBytes:   rawBytes,
		dedupKey:   dedupKey,
		report:     report,
	}
}

func (s *Server) emitPendingUsage(u *pendingUsage, fallbackInline bool) {
	if u == nil {
		return
	}
	report := u.report
	if fallbackInline {
		// trajectory_items failed and spans were written inline: never bill a
		// deduped byte count when full inline payloads were persisted instead.
		report.DedupApplied = false
		report.OrigExtractedBytes = 0
		report.KeptSpanOverheadBytes = 0
		report.NewItemBytes = 0
		report.ReusedItemBytes = 0
		report.NewItems = 0
		report.ReusedItems = 0
	}
	if report.DedupApplied || fallbackInline || s.trajCfg.CompactSpanAttrs || s.trajCfg.BillDedupedBytes {
		if du, ok := s.usage.(DedupUsageEmitter); ok {
			du.EmitIngestionDedup(
				u.orgID,
				u.traceCount,
				u.spanCount,
				u.rawBytes,
				report.StoredTotalBytes(),
				report.NewItemBytes,
				report.DedupSavedBytes(),
				report.NewItems,
				report.ReusedItems,
				s.trajCfg.BillDedupedBytes,
				u.dedupKey,
			)
			return
		}
	}
	s.usage.EmitIngestion(u.orgID, u.traceCount, u.spanCount, u.rawBytes, u.dedupKey)
}

// emitUsageWithReport emits either legacy EmitIngestion (preserving exact
// byte-identical properties when trajectory features are off) or
// EmitIngestionDedup with raw/stored/saved/item telemetry when dedup,
// compaction, or dedup-adjusted billing is active.
func (s *Server) emitUsageWithReport(ctx context.Context, traces ptrace.Traces, report trajectory.UsageReport) {
	u := s.buildPendingUsage(ctx, traces, report)
	if u == nil {
		return
	}
	s.emitPendingUsage(u, false)
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
