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
// lookup tables, connection buffers and the flushers' insert buffers (one
// pair per concurrent insert). The other half is headroom for the garbage
// the GC lets build up between cycles. The budget is never raised to fit a
// request.
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
// beyond twice the insert buffer for each concurrent insert.
const fixedAllowance = 32 << 20

// Concurrent ClickHouse inserts of pending rows (Config.InsertConcurrency,
// FI_INSERT_CONCURRENCY). One insert at a time stored about 500-800
// attribute-heavy spans a second on Standalone with CPU to spare on both
// sides (R1, #3598). Each concurrent insert takes two insert buffers
// from the budget's allowance, so the default follows the Go memory limit: 3
// from 512 MiB (Standalone) up and with no limit, 2 from 384 MiB, else 1.
const (
	defaultInsertConcurrency = 3
	maxInsertConcurrency     = 16
)

// insertConcurrency is the default number of concurrent inserts for a
// memory limit.
func insertConcurrency(memLimit int64) int {
	switch {
	case memLimit <= 0 || memLimit >= 512<<20:
		return defaultInsertConcurrency
	case memLimit >= 384<<20:
		return 2
	}
	return 1
}

// maxEncodedPerBody is how many JSON bytes a request may write to ClickHouse
// per byte of the body cap. Rows repeat their resource's attributes, and
// escaping can grow a string up to six times; ordinary requests write two to
// three times their size.
const maxEncodedPerBody = 8

// Defaults for the listeners.
const (
	maxHeaderBytes  = 16 << 10         // HTTP headers and gRPC metadata
	otlpReadTimeout = 30 * time.Second // to read one request body or gRPC message
	httpIdleTimeout = 2 * time.Minute

	grpcStreamWindow = 256 << 10 // per-stream flow-control window (turns BDP sizing off)
	// grpcConnWindow is the connection's window. Only streams whose handler
	// is reading a message under its wire lease are given more than
	// grpcStreamWindow, so this bounds nothing the leases do not; it is large
	// enough that a message at the receive cap arrives in one round trip.
	grpcConnWindow        = 16 << 20
	grpcMaxStreamsPerConn = 32
	grpcMaxStreams        = 64 // authenticated Export calls in flight, across all connections
	// grpcMaxUnverified is how many calls whose key is not yet known to be
	// valid may be in flight at once, across all connections, while they
	// are authenticated. Calls whose key is in the auth cache never wait on
	// it.
	grpcMaxUnverified = 16
	// A gRPC connection with no call in flight is closed (GOAWAY) after
	// grpcMaxConnIdle, and any connection after grpcMaxConnAge plus
	// grpcConnAgeGrace for the calls in flight to finish; clients reconnect.
	// A connection that has not finished its HTTP/2 handshake within
	// grpcConnTimeout is closed.
	grpcMaxConnIdle  = 2 * time.Minute
	grpcMaxConnAge   = 30 * time.Minute
	grpcConnAgeGrace = 2 * time.Minute
	grpcConnTimeout  = 10 * time.Second
	// Clients may ping as often as every grpcMinPingInterval, with or
	// without calls in flight (grpc-go's default refuses pings more often
	// than every 5 minutes). Pings do not keep a connection from being idle.
	grpcMinPingInterval = 10 * time.Second
)

// grpcWireLease is what an Export call leases from the wire pool for a
// message of n bytes, from its length prefix, before the message is read:
// grpc-go reads the message's DATA frames into buffers of their own size
// (exactBufferPool), with per-frame overhead (at most about 15% for frames of
// 1 KiB or more; frameGuardConn limits smaller ones), and they are joined into
// one copy, so both are live together.
func grpcWireLease(n int64) int64 { return max(n*grpcLeasePer4/4, n+grpcLeaseFixed) }

const (
	grpcLeasePer4  = 9        // grpcWireLease's ratio, in quarters
	grpcLeaseFixed = 16 << 10 // the frame list and a few small frames
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
	maxBody    int64         // HTTP body cap, as sent and decompressed
	maxRecv    int64         // gRPC receive cap
	maxEncoded int64         // JSON bytes one request may write to ClickHouse
	insertMax  int64         // a flusher's insert buffer: rows are sent in inserts of about this size
	inserts    int           // concurrent inserts (flushers), each with its insert buffers
	maxWait    time.Duration // longest wait of one request for its leases, both pools together
	maxConns   int           // per listener; 0: no limit
	notes      []string      // what was lowered and why, for the startup log
}

// goMemoryLimit is the Go memory limit, or 0 when there is none.
func goMemoryLimit() int64 {
	lim := debug.SetMemoryLimit(-1)
	if lim <= 0 || lim == math.MaxInt64 {
		return 0
	}
	return lim
}

// insertBufferBytes is a flusher's insert size for a memory limit.
func insertBufferBytes(memLimit int64) int64 {
	if memLimit <= 0 {
		return 8 << 20
	}
	return min(max(memLimit/64, 2<<20), 16<<20)
}

func resolveIngestLimits(cfg Config, memLimit int64) ingestLimits {
	l := ingestLimits{memLimit: memLimit, insertMax: insertBufferBytes(memLimit),
		inserts: insertConcurrency(memLimit), maxWait: defaultLeaseMaxWait}
	if cfg.InsertConcurrency > 0 {
		l.inserts = min(cfg.InsertConcurrency, maxInsertConcurrency)
	}
	if cfg.IngestMaxWait > 0 {
		l.maxWait = min(cfg.IngestMaxWait, maxLeaseMaxWait)
	}
	budgetMiB := cfg.IngestBudgetMiB
	switch {
	case budgetMiB > 0:
		l.budget, l.source = int64(min(budgetMiB, 1<<20))<<20, "FI_INGEST_BUDGET_MIB"
	case memLimit > 0:
		fixed := int64(fixedAllowance) + 2*l.insertMax*int64(l.inserts)
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
	// A body at the cap and its decompressed or joined copy (and a chunk of
	// slack for each) fit the wire pool.
	if fit := l.wire/2 - readChunk; l.maxBody > fit {
		l.notes = append(l.notes, fmt.Sprintf("OTLP body cap lowered from %d to %d MiB to fit the ingest budget (%d MiB); raise FI_INGEST_BUDGET_MIB or GOMEMLIMIT for a higher cap",
			l.maxBody>>20, fit>>20, l.budget>>20))
		l.maxBody = fit
	}
	l.maxRecv = l.maxBody
	if cfg.GRPCMaxRecvMiB > 0 {
		l.maxRecv = int64(min(cfg.GRPCMaxRecvMiB, 1024)) << 20
	}
	// A gRPC message at the cap leases grpcWireLease(maxRecv).
	if grpcWireLease(l.maxRecv) > l.wire {
		fit := l.wire * 4 / grpcLeasePer4
		l.notes = append(l.notes, fmt.Sprintf("gRPC receive cap lowered from %d to %d MiB to fit the ingest budget (%d MiB); raise FI_INGEST_BUDGET_MIB or GOMEMLIMIT for a higher cap",
			l.maxRecv>>20, fit>>20, l.budget>>20))
		l.maxRecv = fit
	}
	l.maxEncoded = maxEncodedPerBody * l.maxBody
	l.maxConns = max(cfg.MaxConnections, 0)
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
