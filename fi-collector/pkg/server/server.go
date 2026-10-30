// Package server hosts a minimal OTLP/gRPC receiver. Why minimal vs the
// full OTel collector framework:
//
//   - The collector framework brings ≈300 MB of transitive deps and a
//     plugin / factory wiring story that's overkill when we only need ONE
//     receiver, ONE processor pipeline and ONE exporter.
//   - The OTLP wire spec is small (~150 LOC to handle ExportTraceServiceRequest)
//     and stable; we get bit-for-bit OTLP compliance without the framework.
//   - Building light keeps cold-start under 100 ms — important for
//     local-dev `docker compose up` iteration.
//
// If we ever need multi-pipeline routing, sampling, or tail-based sampling,
// we should reach for the OTel collector framework at that point. For now,
// less is more.
package server

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	chexp "github.com/future-agi/future-agi/fi-collector/exporter/clickhouse25exporter"
	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/curatedwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
	"github.com/future-agi/future-agi/fi-collector/pkg/traceavailable"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"golang.org/x/sync/semaphore"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/stats"
	"google.golang.org/grpc/status"
)

// Config is what main() passes us. Public fields = YAML wire format.
type Config struct {
	// ObsoleteMaxPendingRows is decoded only to reject stale YAML at startup.
	ObsoleteMaxPendingRows *int          `yaml:"max_pending_rows"`
	GRPCAddr               string        `yaml:"grpc_addr"`         // :4317 default
	HTTPAddr               string        `yaml:"http_addr"`         // :4318 default; empty disables
	BatchMaxRows           int           `yaml:"batch_max_rows"`    // flush after N rows
	BatchMaxAge            time.Duration `yaml:"batch_max_age"`     // flush after X time
	GRPCMaxRecvMiB         int           `yaml:"grpc_max_recv_mib"` // max gRPC message size in MiB; 0 = the body cap
	// IngestBudgetMiB bounds the heap all OTLP requests hold at once, from
	// their bodies to their rows' insert (see ingest_limits.go). 0 = derived
	// from the Go memory limit.
	IngestBudgetMiB int `yaml:"ingest_budget_mib"`
	// MaxBodyMiB caps an OTLP/HTTP body, as sent and decompressed. 0 = 16,
	// lowered when the budget cannot hold one.
	MaxBodyMiB int `yaml:"otlp_max_body_mib"`
	// MaxConnections caps the open connections of each listener; a
	// connection past it is closed at once. 0 = no limit.
	MaxConnections int `yaml:"otlp_max_connections"`
	// IngestMaxWait is the longest one request waits, wire and work pools
	// together, for its share of the ingest budget; then it is refused as
	// busy (503 + Retry-After: 1, UNAVAILABLE). 0 = 2s.
	IngestMaxWait time.Duration `yaml:"ingest_max_wait"`
	// InsertConcurrency is how many ClickHouse inserts of pending rows run at
	// once. 0 = 3 from a 512 MiB Go memory limit up (and with none), 2 from
	// 384 MiB, else 1.
	InsertConcurrency int `yaml:"insert_concurrency"`
}

// Server owns the gRPC + HTTP OTLP listeners and the batch flusher goroutines.
//
// gRPC and HTTP both decode an OTLP ExportTraceServiceRequest, run the same
// converter, and push rows onto the same `pending` buffer. The wire layer is
// the only difference: gRPC uses the generated stub; HTTP accepts
// `application/x-protobuf` and `application/json` per the OTLP/HTTP spec.
type Server struct {
	traceNotifier   TraceNotifier
	cfg             Config
	writer          *chwriter.Writer
	curated         *curatedwriter.Writer // CH-derived dimensions dual-write (P3b step2 HALF 2)
	propertyCatalog PropertyCatalogWriter // observed keys + values spool; default nil
	auth            *auth.Authenticator
	router          auth.SimulationRouter // nil: requests of sandbox keys are dropped
	refusalLog      auth.LogLimiter
	usage           UsageEmitter
	metering        Metering
	log             *slog.Logger
	pricer          chexp.Pricer
	grpc            *grpc.Server
	httpd           *http.Server

	// The ingest budget (ingest_limits.go): its two pools, the live-heap
	// backstop, the gRPC in-flight limits, and what was refused.
	limits      ingestLimits
	wire, work  *memPool
	heap        *heapGuard
	grpcStreams *semaphore.Weighted // authenticated calls (grpcMaxStreams)
	grpcPending *semaphore.Weighted // calls being authenticated (grpcMaxUnverified)
	stats       ingestStats
	readTimeout time.Duration // for one body or gRPC message, from its wire lease
	// grpcAuth authenticates an Export call from its metadata, before its
	// message is read; grpcKnown reports, from the auth cache alone, whether
	// a call's key is known to be valid; requireCreds makes the tap handle
	// refuse calls with no credentials. All follow auth; tests replace them.
	grpcAuth     func(context.Context) (context.Context, error)
	grpcKnown    func(metadata.MD) bool
	requireCreds bool
	// recvWithRecvMsg makes Export read its message with RecvMsg, as when
	// grpc-go's transport stream does not offer transportMsgReader. Tests.
	recvWithRecvMsg bool
	// grpcIdle, grpcAge, grpcHandshake: keepalive.ServerParameters'
	// MaxConnectionIdle and MaxConnectionAge, and grpc.ConnectionTimeout
	// (grpcMaxConnIdle, grpcMaxConnAge, grpcConnTimeout); tests shorten them.
	grpcIdle, grpcAge, grpcHandshake time.Duration
	// Listener counters, for /healthz: connections closed past the cap, and
	// gRPC connections closed for small DATA frames (frameGuardConn).
	grpcConnsRefused, httpConnsRefused atomic.Int64
	smallFrameConns                    atomic.Int64
	tapRefusalConns                    atomic.Int64 // closed at grpcMaxTapRefusals
	draining                           atomic.Int32 // drains under way (the work pool's busy)
	// grpcConns holds the open OTLP/gRPC connections by remote address, for
	// grpcConnTracker.
	grpcConns sync.Map

	// Batching: the receiver handler pushes converted rows onto `pending` and
	// signals via `pendCh`. Flusher goroutines (limits.inserts of them) drain
	// it on either the row-count or age trigger: each wake-up goes to one idle
	// flusher, which swaps the pending batch out and inserts it, so up to
	// limits.inserts inserts run at once while new rows fill the next batch.
	//
	// `pendCurated` accumulates the CURATED dimension identities for ALL
	// payloads received since the last flush into ONE drain-scoped batch (it
	// dedups across merges). So each drain emits at most one end_users + one
	// trace_sessions best-effort insert — bounding the curated latency and
	// avoiding many tiny RMT parts. It rides the same lock + flush cycle as
	// `pend` so the curated dual-write flushes with the span batch.
	bufferedRows    int  // pending and in-flight rows, through all synchronous sidecars
	admissionClosed bool // guarded by pendMu
	refusals        Refusals
	pendMu          sync.Mutex
	pend            []map[string]any
	pendCurated     *curatedwriter.Batch
	pendProperty    []observedcatalog.ScopedSpan
	pendRoots       []traceavailable.Root
	// Input positions, not span IDs: duplicate IDs can have different chunk
	// outcomes. These indices are appended atomically with their canonical rows.
	pendRootRows, pendPropertyRows []int
	// pendLeases are the work-pool leases of the rows in the pending batch;
	// the drain that swaps them out releases them once it has ended.
	pendLeases []*lease
	pendLeased int64
	pendCh     chan struct{}
	// drainSeq numbers the drains in the order they swap the pending batch
	// out (under pendMu). A drain's leases are released only once every
	// earlier drain has ended too (releaseDrained): a request may park its
	// rows lease just after the swap that took its rows, so the lease rides
	// with the next drain, which may end first when inserts run at once.
	drainSeq     uint64
	curatedStamp time.Time // the last drain's curated version stamp (under pendMu)
	releaseMu    sync.Mutex
	releaseNext  uint64              // the oldest drain whose leases are not released yet
	drained      map[uint64][]*lease // leases of drains that ended out of order

	stopCh chan struct{}
	wg     sync.WaitGroup
}

// Option configures optional Server dependencies.
type Option struct {
	traceNotifier   TraceNotifier
	log             *slog.Logger
	pricer          chexp.Pricer
	propertyCatalog PropertyCatalogWriter
	router          auth.SimulationRouter
}

// TraceNotifier receives ended roots only after their canonical batch is stored.
type TraceNotifier interface {
	EnqueueRoots([]traceavailable.Root) error
}

func WithTraceNotifier(n TraceNotifier) Option { return Option{traceNotifier: n} }

// WithLogger sets the server's logger.
func WithLogger(l *slog.Logger) Option { return Option{log: l} }

// WithPricer sets the server's token-cost pricer. Nil (the zero value)
// disables token-based cost (see chexp.Pricer).
func WithPricer(p chexp.Pricer) Option { return Option{pricer: p} }

// WithPropertyCatalogWriter installs the default-off observed catalog spool.
func WithPropertyCatalogWriter(w PropertyCatalogWriter) Option {
	return Option{propertyCatalog: w}
}

// WithSimulationRouter replaces the router of sandbox-key requests, which
// defaults to the authenticator. A nil router is ignored. A server that ends
// with no router at all (no authenticator and no option) drops the requests of
// sandbox keys, so they are never stored under the project name they carry.
func WithSimulationRouter(r auth.SimulationRouter) Option { return Option{router: r} }

// New wires up the server but does NOT start serving. Call Run().
//
// Defaults:
//   - GRPCAddr ":4317" (OTLP gRPC). Set to "" to disable.
//   - HTTPAddr ":4318" (OTLP/HTTP). Set to "" to disable.
//   - BatchMaxRows 5000, BatchMaxAge 5s.
//
// At least one of GRPCAddr / HTTPAddr must be non-empty or Run returns an
// error. We default both ON because every supported SDK picks one of them;
// disabling either is an opt-in deployment choice.
func New(cfg Config, writer *chwriter.Writer, authenticator *auth.Authenticator, usage UsageEmitter, metering Metering, opts ...Option) *Server {
	if cfg.GRPCAddr == "" {
		cfg.GRPCAddr = ":4317"
	}
	if cfg.HTTPAddr == "" {
		cfg.HTTPAddr = ":4318"
	}
	if cfg.BatchMaxRows <= 0 {
		cfg.BatchMaxRows = 5000
	}
	if cfg.BatchMaxAge <= 0 {
		cfg.BatchMaxAge = 5 * time.Second
	}
	// The gRPC receive cap defaults to the OTLP/HTTP body cap, so both
	// transports accept the same spans (Go gRPC's own default of 4 MiB
	// refuses large single-span exports, e.g. an ended voice call with its
	// transcript).
	limits := resolveIngestLimits(cfg, goMemoryLimit())

	log := slog.Default()
	var pricer chexp.Pricer
	var propertyCatalogWriter PropertyCatalogWriter
	var traceNotifier TraceNotifier
	var router auth.SimulationRouter
	if authenticator != nil {
		router = authenticator
	}
	for _, o := range opts {
		if o.router != nil {
			router = o.router
		}
		if o.traceNotifier != nil {
			traceNotifier = o.traceNotifier
		}
		if o.log != nil {
			log = o.log
		}
		if o.pricer != nil {
			pricer = o.pricer
		}
		if o.propertyCatalog != nil {
			propertyCatalogWriter = o.propertyCatalog
		}
	}

	s := &Server{
		traceNotifier:   traceNotifier,
		cfg:             cfg,
		writer:          writer,
		auth:            authenticator,
		router:          router,
		usage:           usage,
		metering:        metering,
		log:             log,
		pricer:          pricer,
		propertyCatalog: propertyCatalogWriter,
		// Share the span writer's HTTP client (keep-alive) for the curated RMTs,
		// but the curated path writes BEST-EFFORT (chwriter.InsertBestEffort:
		// single POST, no retry, no dead-letter) so it can't stall the span flush
		// loop or pollute the span dead-letter. Targets end_users /
		// trace_sessions, never the pinned span table.
		curated:       curatedwriter.New(writer),
		pendCh:        make(chan struct{}, 1),
		stopCh:        make(chan struct{}),
		limits:        limits,
		wire:          newMemPool("wire", limits.wire),
		work:          newMemPool("work", limits.work),
		heap:          newHeapGuard(limits.memLimit),
		grpcStreams:   semaphore.NewWeighted(grpcMaxStreams),
		grpcPending:   semaphore.NewWeighted(grpcMaxUnverified),
		readTimeout:   otlpReadTimeout,
		grpcIdle:      grpcMaxConnIdle,
		grpcAge:       grpcMaxConnAge,
		grpcHandshake: grpcConnTimeout,
	}
	s.grpcAuth, s.requireCreds = authenticator.AuthenticateGRPC, authenticator != nil
	s.grpcKnown = authenticator.KnownGRPCCredentials
	s.wire.setMaxWait(limits.maxWait)
	s.work.setMaxWait(limits.maxWait)
	s.work.onBlock = s.kickFlush
	s.work.busy = func() bool { return s.draining.Load() > 0 }
	if writer != nil {
		writer.SetMaxInsertBytes(int(limits.insertMax))
		// limits.inserts span inserts in flight in all, from every drain and
		// within one: a large batch's inserts run at once too.
		writer.SetMaxConcurrentInserts(limits.inserts)
	}
	return s
}

// Limits are the admission limits in force after defaults, reported by the
// admin endpoint so a benchmark can check the target's budget instead of assuming
// it.
type Limits struct {
	MaxPendingRows    int     `json:"max_pending_rows"`
	BatchMaxRows      int     `json:"batch_max_rows"`
	BatchMaxAgeMS     int64   `json:"batch_max_age_ms"`
	GRPCMaxRecvMiB    float64 `json:"grpc_max_recv_mib"`
	GRPCMaxRecvBytes  int64   `json:"grpc_max_recv_bytes"`
	AdmissionModel    string  `json:"admission_model"`
	IngestBudgetBytes int64   `json:"ingest_budget_bytes"`
	WirePoolBytes     int64   `json:"wire_pool_bytes"`
	WorkPoolBytes     int64   `json:"work_pool_bytes"`
	MaxWaitMS         int64   `json:"max_wait_ms"`
	InsertConcurrency int     `json:"insert_concurrency"`
	GRPCGzip          bool    `json:"grpc_gzip"`
	MaxHTTPBodyBytes  int     `json:"max_http_body_bytes"`
}

// Limits returns the effective limits of this server.
func (s *Server) Limits() Limits {
	return Limits{
		AdmissionModel: "byte-leases-v1", IngestBudgetBytes: s.limits.budget, WirePoolBytes: s.limits.wire, WorkPoolBytes: s.limits.work, MaxWaitMS: s.limits.maxWait.Milliseconds(), InsertConcurrency: s.limits.inserts, GRPCMaxRecvBytes: s.limits.maxRecv,
		MaxPendingRows:   0, // legacy field; byte leases govern admission
		BatchMaxRows:     s.cfg.BatchMaxRows,
		BatchMaxAgeMS:    s.cfg.BatchMaxAge.Milliseconds(),
		GRPCMaxRecvMiB:   float64(s.limits.maxRecv) / (1 << 20),
		MaxHTTPBodyBytes: int(s.limits.maxBody),
	}
}

// Admission is an observational row backlog, not the byte admission budget.
// BufferedRows includes pending and in-flight rows until synchronous sidecars end;
// some rows may already be visible in ClickHouse. It is not a loss detector.
type Admission struct {
	BufferedRows   int      `json:"buffered_rows"`
	PendingRows    int      `json:"pending_rows"`
	MaxPendingRows int      `json:"max_pending_rows"` // legacy receipt field; always zero, no row cap
	Refusals       Refusals `json:"refusals"`
}
type Refusals struct {
	ShuttingDownRequests int64 `json:"shutting_down_requests"`
}

func (s *Server) Admission() Admission {
	s.pendMu.Lock()
	defer s.pendMu.Unlock()
	return Admission{BufferedRows: s.bufferedRows, PendingRows: len(s.pend), Refusals: s.refusals}
}

// Run blocks until ctx is cancelled or a serve error occurs. On shutdown
// we attempt a final drain. A hard crash can lose acknowledged in-memory rows;
// no time or loss bound is guaranteed by the flush-age trigger.
func (s *Server) Run(ctx context.Context) error {
	if s.cfg.GRPCAddr == "" && s.cfg.HTTPAddr == "" {
		return fmt.Errorf("at least one of GRPCAddr / HTTPAddr must be set")
	}
	s.logLimits()

	// One error channel sized for both listeners — first error wins, the
	// other listener is shut down by the select-case below.
	serveErr := make(chan error, 2)

	if s.cfg.GRPCAddr != "" {
		lis, err := net.Listen("tcp", s.cfg.GRPCAddr)
		if err != nil {
			return fmt.Errorf("listen grpc %s: %w", s.cfg.GRPCAddr, err)
		}
		// Past FI_OTLP_MAX_CONNECTIONS open connections (when set), new ones
		// are closed at once. Every connection's frames are watched for
		// small DATA frames (frameGuardConn).
		lis = frameGuardListener{Listener: newConnLimitListener(lis, s.limits.maxConns, &s.grpcConnsRefused), closed: &s.smallFrameConns, conns: &s.grpcConns}
		s.log.Info("grpc listener", "addr", s.cfg.GRPCAddr, "max_recv_mib", float64(s.limits.maxRecv)/(1<<20))
		// No gzip decompressor is registered, on purpose: gRPC stays
		// uncompressed-only. A grpc-go decompressor is handed only the
		// compressed bytes, not the call, so it cannot wait on the call for a
		// share of the wire pool or hold that share until the handler
		// returns, as OTLP/HTTP gzip requests do. A gzip call gets
		// UNIMPLEMENTED before its message is read.
		// TestGRPCGzipDecompressorIsNotRegistered (cmd/fi-collector) fails if
		// any import registers one.
		// Export authenticates in its handler, before its message is read
		// (grpc_export.go), not in a unary interceptor, which would run only
		// after grpc-go had read and decoded the message.
		s.grpc = grpc.NewServer(s.grpcServerOptions()...)
		s.grpc.RegisterService(&traceServiceDesc, &otlpHandler{s: s})
		go func() { serveErr <- s.grpc.Serve(lis) }()
	}

	if s.cfg.HTTPAddr != "" {
		mux := http.NewServeMux()
		// OTLP/HTTP wire spec: a single endpoint per signal. `/v1/traces` is
		// the trace signal — POST only, body is a serialised
		// ExportTraceServiceRequest in one of two media types:
		//   application/x-protobuf  (preferred — every server-side SDK)
		//   application/json        (browser SDKs, lightweight clients)
		// Any other method or content-type is rejected with 415 / 405 per
		// the spec.
		mux.HandleFunc("/v1/traces", s.handleHTTPTraces)
		mux.HandleFunc("/tracer/v1/traces", s.handleHTTPTraces)
		var handler http.Handler = mux
		if s.auth != nil {
			handler = s.countAuthRefusals(s.auth.HTTPMiddleware(mux))
		}
		s.log.Info("http listener", "addr", s.cfg.HTTPAddr, "max_body_mib", float64(s.limits.maxBody)/(1<<20))
		// Each body also has otlpReadTimeout to arrive once its wire lease
		// is granted (readOTLPHTTPBody).
		s.httpd = &http.Server{
			Addr:              s.cfg.HTTPAddr,
			Handler:           handler,
			ReadHeaderTimeout: 10 * time.Second,
			IdleTimeout:       httpIdleTimeout,
			MaxHeaderBytes:    maxHeaderBytes,
		}
		lis, err := net.Listen("tcp", s.cfg.HTTPAddr)
		if err != nil {
			if s.grpc != nil {
				s.grpc.GracefulStop()
			}
			return fmt.Errorf("listen http %s: %w", s.cfg.HTTPAddr, err)
		}
		lis = newConnLimitListener(lis, s.limits.maxConns, &s.httpConnsRefused)
		go func() { serveErr <- s.httpd.Serve(lis) }()
	}

	s.wg.Add(1)
	go s.flushLoop()

	select {
	case <-ctx.Done():
		s.shutdown()
		return ctx.Err()
	case err := <-serveErr:
		s.shutdown()
		// http.ErrServerClosed is the expected return when we call Shutdown,
		// not a real failure — but here we got the error BEFORE shutdown so
		// it's a genuine listener crash.
		return err
	}
}

// shutdown stops both listeners, waits for the flusher to exit, drains the
// in-flight batch. Called once from Run on either ctx cancel or a serve
// error. Safe to call when one of grpc/httpd is nil.
func (s *Server) shutdown() {
	if s.grpc != nil {
		s.grpc.GracefulStop()
	}
	if s.httpd != nil {
		shCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = s.httpd.Shutdown(shCtx)
	}
	s.closeAdmission()
	close(s.stopCh)
	s.wg.Wait()
	s.drainNow(context.Background())
}

func (s *Server) closeAdmission() {
	s.pendMu.Lock()
	s.admissionClosed = true
	s.pendMu.Unlock()
}

// grpcErrLogger logs fallback transport-level receive-cap failures. The
// Export handler normally translates these to INVALID_ARGUMENT and counts
// them itself; this narrow hook covers other transport rejection paths.
type grpcErrLogger struct {
	log *slog.Logger
}

type grpcMethodKey struct{}

// grpc-go's MaxRecvMsgSize rejection message. Both recv variants
// ("received message larger than max" and the gzip "received message after
// decompression larger than max") share these two substrings; matching both
// excludes the send-side "trying to send message larger than max", which
// carries the same ResourceExhausted code but is the wrong story for a
// "request rejected" log.
const (
	grpcMsgRecv     = "received message"
	grpcMsgTooLarge = "larger than max"
)

func (h *grpcErrLogger) TagRPC(ctx context.Context, info *stats.RPCTagInfo) context.Context {
	return context.WithValue(ctx, grpcMethodKey{}, info.FullMethodName)
}

func (h *grpcErrLogger) HandleRPC(ctx context.Context, s stats.RPCStats) {
	end, ok := s.(*stats.End)
	if !ok || end.Error == nil {
		return
	}
	st, _ := status.FromError(end.Error)
	if st.Code() != codes.ResourceExhausted ||
		!strings.Contains(st.Message(), grpcMsgRecv) ||
		!strings.Contains(st.Message(), grpcMsgTooLarge) {
		return
	}
	method, _ := ctx.Value(grpcMethodKey{}).(string)
	h.log.Error("grpc message over size cap, request rejected",
		"method", method,
		"code", st.Code().String(),
		"err", st.Message(),
	)
}

func (h *grpcErrLogger) TagConn(ctx context.Context, _ *stats.ConnTagInfo) context.Context {
	return ctx
}

func (h *grpcErrLogger) HandleConn(context.Context, stats.ConnStats) {}

// otlpHandler implements ptraceotlp.GRPCServer. Stateless per call.
type otlpHandler struct {
	ptraceotlp.UnimplementedGRPCServer
	s *Server
}

func (h *otlpHandler) Export(ctx context.Context, req ptraceotlp.ExportRequest) (ptraceotlp.ExportResponse, error) {
	resp, _, err := h.export(ctx, req)
	return resp, err
}

// export serves a decoded Export request; enqueued reports whether any of its
// rows went to the pending batch.
func (h *otlpHandler) export(ctx context.Context, req ptraceotlp.ExportRequest) (resp ptraceotlp.ExportResponse, enqueued bool, err error) {
	// The in-process API has no wire body. Preserve the caller's input; the
	// production stream uses exportDecoded and decodes its leased bytes instead.
	return h.exportDecoded(ctx, func() (ptraceotlp.ExportRequest, error) {
		copy := ptraceotlp.NewExportRequest()
		req.Traces().CopyTo(copy.Traces())
		return copy, nil
	}, nil)
}

func (h *otlpHandler) exportDecoded(ctx context.Context, decode exportDecoder, held *exportWork) (ptraceotlp.ExportResponse, bool, error) {
	ctx, cancel := ingestContext(ctx)
	defer cancel()
	md, _ := metadata.FromIncomingContext(ctx)
	prepared, rej := h.s.prepareExport(ctx, decode, auth.ProjectNameMetadataValues(md), held)
	if rej != nil {
		return ptraceotlp.NewExportResponse(), false, h.s.rejectGRPC(rej)
	}
	ctx, req := prepared.ctx, prepared.req
	if prepared.routed.DroppedSpans() > 0 && req.Traces().ResourceSpans().Len() == 0 {
		return partialSuccessResponse(prepared.routed), false, nil
	}
	rows, ids, err := chexp.ConvertWithIdentities(ctx, req.Traces(), h.s.pricer)
	if err != nil {
		return ptraceotlp.NewExportResponse(), false, err
	}
	if ctx.Err() != nil {
		return ptraceotlp.NewExportResponse(), false, h.s.rejectGRPC(rejectProjectUnavailable)
	}
	if h.s.enqueueRouted(rows, ids, prepared.routed, prepared.organization, prepared.workspace, prepared.projects, prepared.hidden) != enqueueAccepted {
		return ptraceotlp.NewExportResponse(), false, h.s.rejectGRPC(rejectShutdown)
	}
	h.s.emitUsage(ctx, req.Traces())

	resp := ptraceotlp.NewExportResponse()
	setProjectPartialSuccess(resp, prepared.rejectedSpans)
	return resp, len(rows) > 0, nil
}

// handleHTTPTraces implements POST /v1/traces per the OTLP/HTTP wire spec
// (https://opentelemetry.io/docs/specs/otlp/#otlphttp). Accepts both
// `application/x-protobuf` and `application/json`, either uncompressed or
// with `Content-Encoding: gzip` (the spec requires servers to support gzip).
// Any other method, content type or content coding is rejected with the
// canonical status code.
//
// The project may also come from the X-Project-Name header (alias
// FI-Project-Name), for exporters that can set headers but not resource
// attributes; a resource attribute `project_name` still wins (see
// auth.ProjectNameHeader).
//
// Success is HTTP 200 + an empty (or near-empty) ExportTraceServiceResponse
// in the response media type that matches the request — the spec requires
// echoing the content-type so client SDKs can decode the partial-success
// field. We always return the fully-successful response since our pipeline
// is at-least-once + dead-letter for failed inserts.
func (s *Server) handleHTTPTraces(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := ingestContext(r.Context())
	defer cancel()
	r = r.WithContext(ctx)
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		s.rejectHTTP(w, "", &rejection{reason: "method", status: http.StatusMethodNotAllowed, msg: "method not allowed"})
		return
	}

	ct := r.Header.Get("Content-Type")
	// Strip any `;charset=...` suffix. The spec only mentions the two base
	// types but charset is allowed and common (esp. from JSON clients).
	if i := indexByte(ct, ';'); i >= 0 {
		ct = ct[:i]
	}
	ct = trimSpace(ct)
	if ct != "application/x-protobuf" && ct != "application/json" {
		// The spec is explicit: unsupported media types return 415.
		w.Header().Set("Accept", "application/x-protobuf, application/json")
		s.rejectHTTP(w, "", &rejection{reason: "content_type", status: http.StatusUnsupportedMediaType, msg: "unsupported content type: " + ct})
		return
	}

	gzipped, ok := otlpContentCoding(r.Header)
	if !ok {
		// RFC 7694: a 415 caused by the content coding lists the codings
		// the server does accept.
		w.Header().Set("Accept-Encoding", "gzip, identity")
		s.rejectHTTP(w, ct, &rejection{reason: "content_encoding", status: http.StatusUnsupportedMediaType,
			msg: "unsupported content encoding: " + strings.Join(r.Header.Values("Content-Encoding"), ", ")})
		return
	}

	// Refusals that need none of the body come before any of it is read.
	if check, ok := s.checkUsage(r.Context()); !ok {
		s.rejectHTTP(w, ct, quotaRejection(check.Reason))
		return
	}
	if s.heap.overLimit(time.Now()) {
		s.rejectHTTP(w, ct, rejectHeap)
		return
	}
	// One wait budget for both leases (withLeaseBudget).
	r = r.WithContext(withLeaseBudget(r.Context(), s.limits.maxWait))

	// The body is read under a wire lease and capped (the cap applies to
	// the body as sent and, for gzip, again after decompression). It is then
	// scanned, and its price leased from the work pool, before it is
	// decoded. The rows' share of that lease goes to the pending batch with
	// the rows, if the request gets as far as enqueueing them.
	body, wire, err := s.readOTLPHTTPBody(w, r, gzipped)
	defer wire.release()
	if err != nil {
		s.rejectHTTP(w, ct, s.bodyRejection(err))
		return
	}
	held, rej := s.leaseWork(r.Context(), body, ct == "application/json", auth.ProjectNameHTTPValues(r.Header))
	if rej != nil {
		s.rejectHTTP(w, ct, rej)
		return
	}
	defer held.work.release()
	enqueued := false
	defer func() { s.settleRows(held.rows, enqueued) }()

	prepared, rej := s.prepareExport(r.Context(), func() (ptraceotlp.ExportRequest, error) {
		req := ptraceotlp.NewExportRequest()
		if ct == "application/json" {
			if err := req.UnmarshalJSON(body); err != nil {
				return req, fmt.Errorf("decode json: %w", err)
			}
		} else if err := req.UnmarshalProto(body); err != nil {
			return req, fmt.Errorf("decode protobuf: %w", err)
		}
		return req, nil
	}, auth.ProjectNameHTTPValues(r.Header), held)
	if rej != nil {
		s.rejectHTTP(w, ct, rej)
		return
	}
	ctx, req := prepared.ctx, prepared.req
	if prepared.routed.DroppedSpans() > 0 && req.Traces().ResourceSpans().Len() == 0 {
		writeExportResponse(w, ct, partialSuccessResponse(prepared.routed))
		return
	}
	rows, ids, err := chexp.ConvertWithIdentities(ctx, req.Traces(), s.pricer)
	if err != nil {
		// 4xx — the SDK shouldn't retry a malformed conversion.
		s.rejectHTTP(w, ct, &rejection{reason: "convert", msg: "convert: " + err.Error()})
		return
	}
	if ctx.Err() != nil {
		s.rejectHTTP(w, ct, rejectProjectUnavailable)
		return
	}
	if s.enqueueRouted(rows, ids, prepared.routed, prepared.organization, prepared.workspace, prepared.projects, prepared.hidden) != enqueueAccepted {
		s.rejectHTTP(w, ct, rejectShutdown)
		return
	}
	enqueued = len(rows) > 0

	s.emitUsage(ctx, req.Traces())

	// ExportTraceServiceResponse — same wire shape, encoded to match the
	// request's content-type. The spec requires the response media type to
	// match the request.
	resp := ptraceotlp.NewExportResponse()
	setProjectPartialSuccess(resp, prepared.rejectedSpans)
	writeExportResponse(w, ct, resp)
}

// writeExportResponse answers 200 with resp in the request's media type.
func writeExportResponse(w http.ResponseWriter, ct string, resp ptraceotlp.ExportResponse) {
	var out []byte
	var err error
	switch ct {
	case "application/json":
		out, err = resp.MarshalJSON()
	default:
		out, err = resp.MarshalProto()
	}
	if err != nil {
		http.Error(w, "encode response: "+err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", ct)
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(out)
}

// Permanent project-value failures may discard part of a batch. Report the
// actual rejected span count instead of acknowledging it as fully stored.
func setProjectPartialSuccess(resp ptraceotlp.ExportResponse, rejected int64) {
	if rejected > 0 {
		resp.PartialSuccess().SetRejectedSpans(rejected)
		resp.PartialSuccess().SetErrorMessage("spans rejected: project could not be resolved after a permanent value constraint failure")
	}
}

// bodyRejection maps an error from reading an OTLP/HTTP body.
func (s *Server) bodyRejection(err error) *rejection {
	var tooLarge *http.MaxBytesError
	switch {
	case errors.As(err, &tooLarge) || errors.Is(err, errInflatedBodyTooLarge) || errors.Is(err, errLeaseTooLarge):
		return s.rejectBodyTooLarge()
	case errors.Is(err, errPoolBusy):
		return rejectWireBusy
	case errors.Is(err, os.ErrDeadlineExceeded):
		return rejectReadTimeout
	}
	return &rejection{reason: "bad_body", msg: err.Error()}
}

// dropNoRouter is the reason given for a sandbox-key request that arrives
// where nothing can route it.
const dropNoRouter = "no_router"

// routeSimulation sends the spans of a sandbox key to the hidden project of
// its agent. An error means the request must be refused as retryable; nothing
// of it has been stored or changed.
func (s *Server) routeSimulation(ctx context.Context, result *auth.ResolveResult, traces ptrace.Traces) (auth.RouteOutcome, error) {
	if s.router == nil {
		if result.KeyType != auth.KeyTypeIngest {
			return auth.RouteOutcome{}, nil
		}
		// Storing the spans under their own project name would put a
		// sandbox run in a production project.
		spans := traces.SpanCount()
		traces.ResourceSpans().RemoveIf(func(ptrace.ResourceSpans) bool { return true })
		var out auth.RouteOutcome
		if spans > 0 {
			out.Dropped = map[string]int{dropNoRouter: spans}
			if s.refusalLog.Allow(dropNoRouter, time.Now()) {
				s.log.Error("simulation span dropped: the server has no router for sandbox keys",
					"reason", dropNoRouter, "spans", spans)
			}
		}
		return out, nil
	}
	outcome, err := s.router.RouteSimulation(ctx, auth.CacheKeyFromContext(ctx), traces, result)
	switch {
	case err == nil:
	case errors.Is(err, auth.ErrRoutingNotReady):
		// Expected until the first column check, or for the first request
		// after a cached key is read again.
		s.log.Debug("simulation routing is not ready; request refused", "err", err)
	case s.refusalLog.Allow("refused", time.Now()):
		s.log.Warn("simulation routing refused a request as retryable", "err", err)
	}
	return outcome, err
}

// partialSuccessResponse is the empty success response, carrying the count
// and reasons of the spans routing dropped when there were any.
func partialSuccessResponse(routed auth.RouteOutcome) ptraceotlp.ExportResponse {
	resp := ptraceotlp.NewExportResponse()
	if n := routed.DroppedSpans(); n > 0 {
		partial := resp.PartialSuccess()
		partial.SetRejectedSpans(int64(n))
		partial.SetErrorMessage("simulation routing dropped " + strconv.Itoa(n) + " spans: " + routed.Summary())
	}
	return resp
}

// enqueueRouted parks the rows of a request. Rows of a hidden project, whether
// routing put the whole request there or another key's request names one,
// neither the observed catalog nor the stored-root notifier may see, so they
// are parked without a tenant scope: both then skip them, and the catalog does
// not record a scope failure for every drain that carries them.
//
// The request is admitted or refused as ONE unit, even when its rows are
// parked in two parcels: a client that is told to retry a refused request
// would otherwise store the parcel that fitted a second time.
func (s *Server) enqueueRouted(
	rows []map[string]any,
	ids *curatedwriter.Batch,
	routed auth.RouteOutcome,
	organizationID string,
	workspaceID string,
	workspaceProjectIDs map[string]struct{},
	hiddenProjectIDs map[string]struct{},
) enqueueOutcome {
	if routed.Hidden != "" {
		return s.enqueueParcels(parcel{rows: rows, ids: ids})
	}
	if len(hiddenProjectIDs) > 0 {
		var visible, hidden []map[string]any
		for _, row := range rows {
			if projectID, _ := row["project_id"].(string); projectID != "" {
				if _, isHidden := hiddenProjectIDs[projectID]; isHidden {
					hidden = append(hidden, row)
					continue
				}
			}
			visible = append(visible, row)
		}
		if len(hidden) > 0 {
			// The payload's identities go with the visible rows, or with the
			// hidden ones when nothing else is left.
			if len(visible) == 0 {
				return s.enqueueParcels(parcel{rows: hidden, ids: ids})
			}
			return s.enqueueParcels(
				parcel{rows: hidden},
				parcel{rows: visible, ids: ids, organizationID: organizationID, workspaceID: workspaceID, projectIDs: workspaceProjectIDs},
			)
		}
	}
	return s.enqueueScoped(rows, ids, organizationID, workspaceID, workspaceProjectIDs)
}

// indexByte and trimSpace are lifted here so the file doesn't grow a
// strings import just for content-type parsing. Inline 5-line helpers are
// cheaper than a stdlib pull when we already share package boundaries.
func indexByte(s string, c byte) int {
	for i := 0; i < len(s); i++ {
		if s[i] == c {
			return i
		}
	}
	return -1
}

func trimSpace(s string) string {
	for len(s) > 0 && (s[0] == ' ' || s[0] == '\t') {
		s = s[1:]
	}
	for len(s) > 0 && (s[len(s)-1] == ' ' || s[len(s)-1] == '\t') {
		s = s[:len(s)-1]
	}
	return s
}

// enqueueOutcome is the admission decision for one request.
type enqueueOutcome int

const (
	// enqueueAccepted: every row of the request is parked.
	enqueueAccepted enqueueOutcome = iota
	// enqueueClosed: the server is shutting down and has closed admission for
	// its final drain. Nothing is parked; a client should retry (elsewhere or
	// after the restart).
	enqueueClosed
)

// parcel is a group of rows parked together under one tenant scope.
type parcel struct {
	rows           []map[string]any
	ids            *curatedwriter.Batch
	organizationID string
	workspaceID    string
	projectIDs     map[string]struct{}
}

// enqueue parks rows on the pending buffer and signals the flusher.
// We choose non-blocking signalling: if the channel already holds a tick
// the flusher will already wake up and see this batch.
//
// `ids` are the CURATED dimension identities collected for this same payload;
// they ride alongside `rows` so the curated dual-write flushes with the span
// batch. A nil / empty Batch is skipped (the common no-user/no-session case).
func (s *Server) enqueue(rows []map[string]any, ids *curatedwriter.Batch) enqueueOutcome {
	return s.enqueueScoped(rows, ids, "", "", nil)
}

// enqueueScoped keeps the authenticated workspace in a drain-local sidecar.
// The sidecar is only allocated when the default-off observed writer is
// installed, and no field is added to the canonical span row.
func (s *Server) enqueueScoped(
	rows []map[string]any,
	ids *curatedwriter.Batch,
	organizationID string,
	workspaceID string,
	workspaceProjectIDs map[string]struct{},
) enqueueOutcome {
	return s.enqueueParcels(parcel{
		rows:           rows,
		ids:            ids,
		organizationID: organizationID,
		workspaceID:    workspaceID,
		projectIDs:     workspaceProjectIDs,
	})
}

// enqueueParcels parks one already byte-admitted request atomically, or refuses
// all of it after shutdown. It does not introduce a second row-count budget.
func (s *Server) enqueueParcels(parcels ...parcel) enqueueOutcome {
	total := 0
	for _, p := range parcels {
		total += len(p.rows)
	}
	if total == 0 {
		return enqueueAccepted
	}
	s.pendMu.Lock()
	if s.admissionClosed {
		s.refusals.ShuttingDownRequests++
		s.pendMu.Unlock()
		return enqueueClosed
	}
	s.bufferedRows += total
	for _, p := range parcels {
		s.appendLocked(p)
	}
	shouldKick := len(s.pend) >= s.cfg.BatchMaxRows
	s.pendMu.Unlock()
	if shouldKick {
		select {
		case s.pendCh <- struct{}{}:
		default:
		}
	}
	return enqueueAccepted
}

// appendLocked parks a parcel whose rows are already reserved. s.pendMu must
// be held.
func (s *Server) appendLocked(p parcel) {
	rows := p.rows
	if len(rows) == 0 {
		return
	}
	base := len(s.pend)
	s.pend = append(s.pend, rows...)
	if s.traceNotifier != nil {
		for i := range rows {
			for _, root := range traceavailable.ExtractRoots(rows[i:i+1], p.organizationID, p.workspaceID, p.projectIDs) {
				s.pendRoots = append(s.pendRoots, root)
				s.pendRootRows = append(s.pendRootRows, base+i)
			}
		}
	}
	if s.propertyCatalog != nil && p.organizationID != "" && p.workspaceID != "" {
		for i, row := range rows {
			s.pendPropertyRows = append(s.pendPropertyRows, base+i)
			projectID, _ := row["project_id"].(string)
			scopeError := ""
			if p.projectIDs == nil {
				scopeError = "missing_project_workspace_proof"
			} else if _, allowed := p.projectIDs[projectID]; !allowed {
				scopeError = "project_workspace_mismatch"
			}
			s.pendProperty = append(s.pendProperty, observedcatalog.ScopedSpan{
				OrganizationID: p.organizationID,
				WorkspaceID:    p.workspaceID,
				ScopeError:     scopeError,
				Row:            row,
			})
		}
	}
	if p.ids != nil && !p.ids.Empty() {
		if s.pendCurated == nil {
			s.pendCurated = curatedwriter.NewBatch()
		}
		s.pendCurated.Merge(p.ids)
	}
}

// flushLoop runs limits.inserts flushers until stopCh closes, and returns
// when every one of them has finished the drain it was running. Each wakes
// on either an explicit kick (row-count threshold, budget pressure) or the
// shared age ticker; one wake-up wakes one idle flusher.
func (s *Server) flushLoop() {
	defer s.wg.Done()
	t := time.NewTicker(s.cfg.BatchMaxAge)
	defer t.Stop()
	var flushers sync.WaitGroup
	for range max(s.limits.inserts, 1) {
		flushers.Add(1)
		go func() {
			defer flushers.Done()
			s.flusher(t.C)
		}()
	}
	flushers.Wait()
}

func (s *Server) flusher(tick <-chan time.Time) {
	for {
		select {
		case <-s.stopCh:
			return
		case <-tick:
			s.drainNow(context.Background())
		case <-s.pendCh:
			s.drainNow(context.Background())
		}
	}
}

// drainNow swaps the pending buffer and flushes it. Uses a fresh slice so
// the next request can immediately start filling without contending. When it
// returns, the rows it took are no longer held, and their work-pool leases
// are released, or will be as soon as every earlier drain has ended
// (releaseDrained). Drains may run at once (flushLoop).
func (s *Server) drainNow(ctx context.Context) {
	s.pendMu.Lock()
	batch := s.pend
	curated := s.pendCurated
	property := s.pendProperty
	roots := s.pendRoots
	rootRows, propertyRows := s.pendRootRows, s.pendPropertyRows
	leases := s.pendLeases
	s.pend = nil
	s.pendCurated = nil
	s.pendProperty = nil
	s.pendRoots = nil
	s.pendRootRows, s.pendPropertyRows = nil, nil
	s.pendLeases, s.pendLeased = nil, 0
	seq := s.drainSeq
	s.drainSeq++
	// The curated rows' RMT version (latest wins) is stamped at the swap, in
	// swap order, not when the write happens: drains that run at once may
	// write in any order, and an earlier batch must not out-version a later
	// one.
	stamp := time.Now().UTC()
	if !stamp.After(s.curatedStamp) {
		stamp = s.curatedStamp.Add(time.Microsecond) // DateTime64(6)
	}
	s.curatedStamp = stamp
	s.draining.Add(1) // under pendMu: busy before the swapped rows' leases can be missed
	s.pendMu.Unlock()
	defer s.draining.Add(-1)
	// The rows' leases cover everything below: the span insert, the
	// stored-root notice, the catalog handoff and the curated write.
	defer func() {
		s.releaseDrained(seq, leases)
		// While a request waits for the work pool, drain again at once if
		// rows (and their leases) arrived during this drain: they would
		// otherwise wait for the next tick.
		if s.work.waiting.Load() > 0 {
			s.pendMu.Lock()
			more := len(s.pendLeases) > 0
			s.pendMu.Unlock()
			if more {
				s.kickFlush()
			}
		}
	}()
	if len(batch) == 0 {
		return
	}
	// Observation only: retain the count through canonical and sidecar ownership.
	defer func() { s.pendMu.Lock(); s.bufferedRows -= len(batch); s.pendMu.Unlock() }()
	confirmed, _ := s.writer.InsertConfirmed(ctx, batch)
	roots = confirmedSidecars(roots, rootRows, confirmed)
	property = confirmedSidecars(property, propertyRows, confirmed)
	if s.traceNotifier != nil && len(roots) > 0 {
		if err := s.traceNotifier.EnqueueRoots(roots); err != nil {
			s.log.Warn("error feed stored-root notification gap", "error", err)
		}
	}
	// Insert returns an error on dead-letter; the writer already persisted
	// the rows + bumped stats. We swallow here because the flusher's job
	// is to make progress, not propagate per-batch failures. /healthz
	// surfaces the writer's failure counter.

	// Transfer observation ownership to the fsync-backed catalog spool only
	// after confirmed canonical success. Backfill repairs the preceding crash
	// gap. Catalog errors never change canonical writer health or dead-letter.
	if s.propertyCatalog != nil && len(property) > 0 {
		if err := s.propertyCatalog.EnqueueCanonicalSpans(property); err != nil {
			observedcatalog.LogHandoffGap(s.log, property, err)
		}
	}

	// CH-derived dimensions (P3b step2 HALF 2): BEST-EFFORT mirror the
	// drain-scoped curated end_users / trace_sessions identities AFTER the span
	// insert. curated.Write uses chwriter.InsertBestEffort (single POST, no
	// retry, no dead-letter — see its doc), so even on a CH outage this adds at
	// most two bounded requests and can NEVER stall span draining or pollute the
	// span dead-letter. The result is swallowed — the span insert above already
	// completed and Django's backfill reconciles any curated gap. One `stamp`
	// (taken at the swap, above) stamps version/first_seen for every curated
	// row in this drain.
	if curated != nil && !curated.Empty() {
		_ = s.curated.Write(ctx, curated, stamp)
	}
}

// confirmedSidecars filters in place, preserving enqueue order and exact row
// association even for repeated span IDs or interleaved hidden/visible routes.
// The drain keeps all its leases until these handoffs and curated writes end.
func confirmedSidecars[T any](sidecars []T, indices []int, confirmed []bool) []T {
	out := sidecars[:0]
	for i, sidecar := range sidecars {
		if i < len(indices) && indices[i] >= 0 && indices[i] < len(confirmed) && confirmed[indices[i]] {
			out = append(out, sidecar)
		}
	}
	clear(sidecars[len(out):])
	return out
}

// releaseDrained releases the leases of drain seq once it and every earlier
// drain have ended, in drain order, without waiting: the drain that ends
// last releases what the others left. So a lease is never released before
// the drain holding its rows has ended, even when the lease was parked one
// swap late (adoptRows) and its own drain ends first.
func (s *Server) releaseDrained(seq uint64, leases []*lease) {
	s.releaseMu.Lock()
	defer s.releaseMu.Unlock()
	if seq != s.releaseNext {
		if s.drained == nil {
			s.drained = map[uint64][]*lease{}
		}
		s.drained[seq] = leases
		return
	}
	for {
		for _, l := range leases {
			l.release()
		}
		s.releaseNext++
		next, ok := s.drained[s.releaseNext]
		if !ok {
			return
		}
		delete(s.drained, s.releaseNext)
		leases = next
	}
}
