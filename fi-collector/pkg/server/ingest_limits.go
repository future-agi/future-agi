package server

import (
	"fmt"
	"math"
	"runtime/debug"
	"runtime/metrics"
	"sync/atomic"
	"time"
)

// Ingest limits. One budget bounds the heap that OTLP requests (plain, gzip
// and gRPC alike) can hold at once: their bodies while they are read and
// decoded, the decoded requests, and the converted rows until ClickHouse has
// stored them. Without it any API-key holder could send bodies that decode to
// many times their size (an empty span is 2 bytes of protobuf and over 4 KiB
// once converted) and exhaust the collector's memory.
//
// The budget is FI_INGEST_BUDGET_MIB, or derived from the Go memory limit
// (GOMEMLIMIT; when unset, main sets it to 90% of the container's memory
// limit): half of what is left after a fixed allowance for the runtime,
// lookup tables, connection buffers and the flusher's insert buffer. The
// other half is headroom for the garbage the GC lets build up between
// cycles. The budget is never raised to fit a request.
//
// A third of the budget is the wire pool, the rest the work pool (see
// memPool). The body cap and the gRPC receive cap are lowered, when needed,
// so that a body at the cap fits the wire pool on its own.

// defaultMaxBodyBytes is the OTLP body cap (FI_OTLP_MAX_BODY_MIB), as sent and,
// for gzip, after decompression: the OpenTelemetry Collector's default too.
const defaultMaxBodyBytes = 16 << 20

// noLimitBudget is the budget when no memory limit is known at all.
const noLimitBudget = 256 << 20

// fixedAllowance is what the budget leaves aside for the rest of the process,
// beyond twice the flusher's insert buffer.
const fixedAllowance = 32 << 20

// maxEncodedPerBody is how many JSON bytes a request may write to ClickHouse
// per byte of the body cap. Rows repeat their resource's attributes, and
// escaping can grow a string up to six times; ordinary requests write two to
// three times their size.
const maxEncodedPerBody = 8

// Defaults for the listeners.
const (
	defaultMaxConnections = 1024             // per listener (FI_OTLP_MAX_CONNECTIONS)
	maxHeaderBytes        = 16 << 10         // HTTP headers and gRPC metadata
	otlpReadTimeout       = 30 * time.Second // to read one request body or gRPC message
	httpIdleTimeout       = 2 * time.Minute
	grpcStreamWindow      = 256 << 10 // per-stream flow-control window (turns BDP sizing off)
	grpcConnWindow        = 1 << 20
	grpcMaxStreamsPerConn = 32
	grpcMaxStreams        = 64 // in flight across all connections
)

// heapGuardShare: a request is refused as busy while the live heap is above
// this share of the memory limit. A backstop for anything the price misses.
const heapGuardShare = 0.85

// ingestLimits are the resolved limits.
type ingestLimits struct {
	memLimit   int64  // the Go memory limit they were derived from, or 0
	source     string // where the budget came from
	budget     int64
	wire, work int64
	maxBody    int64 // HTTP body cap, as sent and decompressed
	maxRecv    int64 // gRPC receive cap
	maxEncoded int64 // JSON bytes one request may write to ClickHouse
	insertMax  int64 // the flusher's insert buffer: rows are sent in inserts of about this size
	maxConns   int
	notes      []string // what was lowered and why, for the startup log
}

// goMemoryLimit is the Go memory limit, or 0 when there is none.
func goMemoryLimit() int64 {
	lim := debug.SetMemoryLimit(-1)
	if lim <= 0 || lim == math.MaxInt64 {
		return 0
	}
	return lim
}

// insertBufferBytes is the flusher's insert size for a memory limit.
func insertBufferBytes(memLimit int64) int64 {
	if memLimit <= 0 {
		return 8 << 20
	}
	return min(max(memLimit/64, 2<<20), 16<<20)
}

func resolveIngestLimits(cfg Config, memLimit int64) ingestLimits {
	l := ingestLimits{memLimit: memLimit, insertMax: insertBufferBytes(memLimit)}
	budgetMiB := cfg.IngestBudgetMiB
	if budgetMiB <= 0 && cfg.GzipInflightMiB > 0 {
		budgetMiB = cfg.GzipInflightMiB
		l.notes = append(l.notes, "gzip_inflight_mib (FI_GZIP_INFLIGHT_MIB) is deprecated; it sets ingest_budget_mib (FI_INGEST_BUDGET_MIB)")
	}
	switch {
	case budgetMiB > 0:
		l.budget, l.source = int64(min(budgetMiB, 1<<20))<<20, "FI_INGEST_BUDGET_MIB"
	case memLimit > 0:
		fixed := int64(fixedAllowance) + 2*l.insertMax
		l.budget, l.source = max((memLimit-fixed)/2, memLimit/8), "GOMEMLIMIT"
	default:
		l.budget, l.source = noLimitBudget, "default"
		l.notes = append(l.notes, "no memory limit is set (GOMEMLIMIT or a container limit); the ingest budget is a fixed 256 MiB")
	}
	l.wire = l.budget / 3
	l.work = l.budget - l.wire

	l.maxBody = defaultMaxBodyBytes
	if cfg.MaxBodyMiB > 0 {
		l.maxBody = int64(min(cfg.MaxBodyMiB, 1024)) << 20
	}
	if l.maxBody > l.wire/2 {
		l.notes = append(l.notes, fmt.Sprintf("OTLP body cap lowered from %d to %d MiB to fit the ingest budget (%d MiB); raise FI_INGEST_BUDGET_MIB or GOMEMLIMIT for a higher cap",
			l.maxBody>>20, (l.wire/2)>>20, l.budget>>20))
		l.maxBody = l.wire / 2
	}
	l.maxRecv = l.maxBody
	if cfg.GRPCMaxRecvMiB > 0 {
		l.maxRecv = int64(min(cfg.GRPCMaxRecvMiB, 1024)) << 20
	}
	// A gRPC call leases twice its receive cap before its message is read.
	if l.maxRecv > l.wire/2 {
		l.notes = append(l.notes, fmt.Sprintf("gRPC receive cap lowered from %d to %d MiB to fit the ingest budget (%d MiB); raise FI_INGEST_BUDGET_MIB or GOMEMLIMIT for a higher cap",
			l.maxRecv>>20, (l.wire/2)>>20, l.budget>>20))
		l.maxRecv = l.wire / 2
	}
	l.maxEncoded = maxEncodedPerBody * l.maxBody
	l.maxConns = defaultMaxConnections
	if cfg.MaxConnections > 0 {
		l.maxConns = cfg.MaxConnections
	}
	return l
}

// heapGuard refuses new requests while the live heap (as of the last GC) is
// above a share of the memory limit. It reads runtime/metrics at most once a
// second and never forces a GC.
type heapGuard struct {
	limit int64 // 0: off
	next  atomic.Int64
	over  atomic.Bool
	live  atomic.Int64
}

func newHeapGuard(memLimit int64) *heapGuard {
	g := &heapGuard{}
	if memLimit > 0 {
		g.limit = int64(float64(memLimit) * heapGuardShare)
	}
	return g
}

const liveHeapMetric = "/gc/heap/live:bytes"

func (g *heapGuard) overLimit(now time.Time) bool {
	if g.limit == 0 {
		return false
	}
	t := now.UnixNano()
	next := g.next.Load()
	if t >= next && g.next.CompareAndSwap(next, t+int64(time.Second)) {
		sample := []metrics.Sample{{Name: liveHeapMetric}}
		metrics.Read(sample)
		if sample[0].Value.Kind() == metrics.KindUint64 {
			live := int64(sample[0].Value.Uint64())
			g.live.Store(live)
			g.over.Store(live > g.limit)
		}
	}
	return g.over.Load()
}
