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
	"bytes"
	"compress/gzip"
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	chexp "github.com/future-agi/future-agi/fi-collector/exporter/clickhouse25exporter"
	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/curatedwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
	"github.com/future-agi/future-agi/fi-collector/pkg/traceavailable"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/genproto/googleapis/rpc/errdetails"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	// Registers the gzip compressor: without it a gRPC exporter configured with
	// compression=gzip is refused as Unimplemented. The decompressed size is
	// still bounded by MaxRecvMsgSize.
	_ "google.golang.org/grpc/encoding/gzip"
	"google.golang.org/grpc/stats"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/durationpb"
)

// Config is what main() passes us. Public fields = YAML wire format.
type Config struct {
	GRPCAddr       string        `yaml:"grpc_addr"`         // :4317 default
	HTTPAddr       string        `yaml:"http_addr"`         // :4318 default; empty disables
	BatchMaxRows   int           `yaml:"batch_max_rows"`    // flush after N rows
	BatchMaxAge    time.Duration `yaml:"batch_max_age"`     // flush after X time
	MaxPendingRows int           `yaml:"max_pending_rows"`  // pending + in-flight; whole requests rejected when full
	GRPCMaxRecvMiB int           `yaml:"grpc_max_recv_mib"` // max gRPC message size in MiB; default + rationale in New()
}

// Server owns the gRPC + HTTP OTLP listeners and the batch flusher goroutine.
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

	// Batching: the receiver handler pushes converted rows onto `pending` and
	// signals via `pendCh`. A single flusher goroutine drains it on either
	// the row-count or age trigger. One channel/one goroutine keeps lock
	// contention minimal at 100K spans/sec.
	//
	// `pendCurated` accumulates the CURATED dimension identities for ALL
	// payloads received since the last flush into ONE drain-scoped batch (it
	// dedups across merges). So each drain emits at most one end_users + one
	// trace_sessions best-effort insert — bounding the curated latency and
	// avoiding many tiny RMT parts. It rides the same lock + flush cycle as
	// `pend` so the curated dual-write flushes with the span batch.
	pendMu       sync.Mutex
	bufferedRows int // includes the batch currently being flushed
	pend         []map[string]any
	pendCurated  *curatedwriter.Batch
	pendProperty []observedcatalog.ScopedSpan
	pendRoots    []traceavailable.Root
	pendCh       chan struct{}

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
	if cfg.MaxPendingRows <= 0 {
		cfg.MaxPendingRows = 20000
	}
	if cfg.GRPCMaxRecvMiB <= 0 {
		// Go gRPC's default 4 MiB rejects large single-span exports (e.g. an
		// ended voice call with full transcript) with RESOURCE_EXHAUSTED.
		// Match the OTLP/HTTP body cap so both transports accept the same spans.
		cfg.GRPCMaxRecvMiB = maxOTLPHTTPBodyBytes >> 20
	}
	if cfg.GRPCMaxRecvMiB > 1024 {
		cfg.GRPCMaxRecvMiB = 1024 // keep the <<20 shift well under proto's 2 GiB ceiling
	}

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
		curated: curatedwriter.New(writer),
		pendCh:  make(chan struct{}, 1),
		stopCh:  make(chan struct{}),
	}
	return s
}

// Limits are the admission limits in force after defaults, reported by the
// admin endpoint so a benchmark can check the target's budget instead of assuming
// it.
type Limits struct {
	MaxPendingRows   int   `json:"max_pending_rows"`
	BatchMaxRows     int   `json:"batch_max_rows"`
	BatchMaxAgeMS    int64 `json:"batch_max_age_ms"`
	GRPCMaxRecvMiB   int   `json:"grpc_max_recv_mib"`
	MaxHTTPBodyBytes int   `json:"max_http_body_bytes"`
}

// Limits returns the effective limits of this server.
func (s *Server) Limits() Limits {
	return Limits{
		MaxPendingRows:   s.cfg.MaxPendingRows,
		BatchMaxRows:     s.cfg.BatchMaxRows,
		BatchMaxAgeMS:    s.cfg.BatchMaxAge.Milliseconds(),
		GRPCMaxRecvMiB:   s.cfg.GRPCMaxRecvMiB,
		MaxHTTPBodyBytes: maxOTLPHTTPBodyBytes,
	}
}

// Admission is the admission state at one instant, reported by the admin
// endpoint so a benchmark can follow the backlog over time. BufferedRows is what
// the budget bounds: rows parked for the next flush plus rows of the flush in
// progress (including its retries). PendingRows is the parked part only.
// Acknowledged rows in BufferedRows are not in ClickHouse yet.
type Admission struct {
	BufferedRows   int `json:"buffered_rows"`
	PendingRows    int `json:"pending_rows"`
	MaxPendingRows int `json:"max_pending_rows"`
}

// Admission returns the current admission state.
func (s *Server) Admission() Admission {
	s.pendMu.Lock()
	defer s.pendMu.Unlock()
	return Admission{BufferedRows: s.bufferedRows, PendingRows: len(s.pend), MaxPendingRows: s.cfg.MaxPendingRows}
}

// Run blocks until ctx is cancelled or a serve error occurs. On shutdown
// we attempt to drain pending rows once before returning. A hard process crash
// can still lose all acknowledged rows that have not reached ClickHouse.
func (s *Server) Run(ctx context.Context) error {
	if s.cfg.GRPCAddr == "" && s.cfg.HTTPAddr == "" {
		return fmt.Errorf("at least one of GRPCAddr / HTTPAddr must be set")
	}

	// One error channel sized for both listeners — first error wins, the
	// other listener is shut down by the select-case below.
	serveErr := make(chan error, 2)

	if s.cfg.GRPCAddr != "" {
		lis, err := net.Listen("tcp", s.cfg.GRPCAddr)
		if err != nil {
			return fmt.Errorf("listen grpc %s: %w", s.cfg.GRPCAddr, err)
		}
		s.log.Info("grpc listener", "addr", s.cfg.GRPCAddr, "max_recv_mib", s.cfg.GRPCMaxRecvMiB)
		grpcOpts := []grpc.ServerOption{
			grpc.MaxRecvMsgSize(s.cfg.GRPCMaxRecvMiB << 20),
			grpc.StatsHandler(&grpcErrLogger{log: s.log}),
		}
		if s.auth != nil {
			grpcOpts = append(grpcOpts, grpc.UnaryInterceptor(s.auth.GRPCInterceptor()))
		}
		s.grpc = grpc.NewServer(grpcOpts...)
		ptraceotlp.RegisterGRPCServer(s.grpc, &otlpHandler{s: s})
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
			handler = s.auth.HTTPMiddleware(mux)
		}
		s.httpd = &http.Server{
			Addr:              s.cfg.HTTPAddr,
			Handler:           handler,
			ReadHeaderTimeout: 10 * time.Second,
		}
		lis, err := net.Listen("tcp", s.cfg.HTTPAddr)
		if err != nil {
			if s.grpc != nil {
				s.grpc.GracefulStop()
			}
			return fmt.Errorf("listen http %s: %w", s.cfg.HTTPAddr, err)
		}
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
	close(s.stopCh)
	s.wg.Wait()
	s.drainNow(context.Background())
}

// grpcErrLogger surfaces transport-level message-size rejections. A message
// larger than MaxRecvMsgSize is rejected with RESOURCE_EXHAUSTED before the
// handler or interceptor runs, so a stats.Handler is the only server-side
// hook that sees it — without this, the client's span is dropped with no
// trace in the collector's own logs. Deliberately narrow: auth failures are
// logged by the interceptor, quota rejections are silent by design (same
// code, would be indistinguishable spam), and client cancels are benign.
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
	if check, ok := h.s.checkUsage(ctx); !ok {
		return ptraceotlp.NewExportResponse(), status.Errorf(codes.ResourceExhausted, "quota exceeded: %s", check.Reason)
	}

	// Keep the authenticated workspace out-of-band. StampResourceAttrs retains
	// the existing org/project behavior but deliberately does not add a
	// workspace attribute to the canonical span payload.
	receivedSpans := req.Traces().SpanCount()
	var propertyOrganizationID, propertyWorkspaceID string
	var propertyProjectIDs, hiddenProjectIDs map[string]struct{}
	var routed auth.RouteOutcome
	if result := auth.FromContext(ctx); result != nil {
		propertyOrganizationID, propertyWorkspaceID = result.OrgID, result.WorkspaceID
		ck := auth.CacheKeyFromContext(ctx)
		var err error
		routed, err = h.s.routeSimulation(ctx, result, req.Traces())
		if err != nil {
			return ptraceotlp.NewExportResponse(), status.Error(codes.Unavailable, "simulation routing temporarily unavailable")
		}
		if routed.DroppedSpans() > 0 && req.Traces().ResourceSpans().Len() == 0 {
			// Nothing of the request is stored, so nothing of it is metered.
			return partialSuccessResponse(routed), nil
		}
		dropped, err := auth.StampResourceAttrs(ctx, h.s.auth, ck, req.Traces(), result)
		if err != nil {
			return ptraceotlp.NewExportResponse(), status.Errorf(codes.InvalidArgument, "auth stamp: %v", err)
		}
		if dropped > 0 {
			h.s.log.Warn("dropped ResourceSpans with unresolvable project", "dropped", dropped)
		}
		var scoped bool
		propertyProjectIDs, scoped = result.ProjectIDsInWorkspace(propertyWorkspaceID)
		hiddenProjectIDs, _ = result.HiddenProjectIDsInWorkspace(propertyWorkspaceID)
		if !scoped {
			return ptraceotlp.NewExportResponse(), status.Error(codes.InvalidArgument, "auth project/workspace scope mismatch")
		}
	}

	rows, ids, err := chexp.ConvertWithIdentities(ctx, req.Traces(), h.s.pricer)
	if err != nil {
		return ptraceotlp.NewExportResponse(), err
	}
	if outcome := h.s.enqueueRouted(rows, ids, routed, propertyOrganizationID, propertyWorkspaceID, propertyProjectIDs, hiddenProjectIDs); outcome != enqueueAccepted {
		return ptraceotlp.NewExportResponse(), h.s.grpcRefusal(outcome, len(rows))
	}

	payloadBytes, _ := req.MarshalProto()
	h.s.emitUsage(ctx, req.Traces(), int64(len(payloadBytes)))

	return exportResponse(receivedSpans-req.Traces().SpanCount(), routed), nil
}

// Cap the body size we will read from an OTLP/HTTP request. 16 MiB matches
// the conservative default in the upstream OTel collector receiver and
// covers a 5000-span batch carrying ~3 KiB of attrs each. Larger bodies
// almost certainly indicate a misconfigured exporter (no batching) and
// would let a single client consume memory unboundedly.
const maxOTLPHTTPBodyBytes = 16 << 20

// readOTLPBody returns the decoded body of an OTLP/HTTP request. The only
// supported content codings are identity and gzip, the one OTLP exporters use.
// The size cap applies to the bytes read from the wire AND to the decoded bytes,
// so a few KiB of compressed zeros cannot expand into unbounded memory. On error
// the returned status is the one to answer with.
func readOTLPBody(r *http.Request) ([]byte, int, error) {
	encoding := strings.ToLower(strings.TrimSpace(r.Header.Get("Content-Encoding")))
	if encoding != "" && encoding != "identity" && encoding != "gzip" {
		return nil, http.StatusUnsupportedMediaType, fmt.Errorf("unsupported content encoding %q; use gzip or identity", encoding)
	}
	raw, err := io.ReadAll(io.LimitReader(r.Body, maxOTLPHTTPBodyBytes+1))
	if err != nil {
		return nil, http.StatusBadRequest, fmt.Errorf("read body: %w", err)
	}
	if len(raw) > maxOTLPHTTPBodyBytes {
		return nil, http.StatusRequestEntityTooLarge, errors.New("payload too large")
	}
	if encoding != "gzip" {
		return raw, http.StatusOK, nil
	}
	zr, err := gzip.NewReader(bytes.NewReader(raw))
	if err != nil {
		return nil, http.StatusBadRequest, errors.New("invalid gzip body")
	}
	defer zr.Close()
	body, err := io.ReadAll(io.LimitReader(zr, maxOTLPHTTPBodyBytes+1))
	if err != nil {
		return nil, http.StatusBadRequest, errors.New("invalid gzip body")
	}
	if len(body) > maxOTLPHTTPBodyBytes {
		return nil, http.StatusRequestEntityTooLarge, errors.New("payload too large after decompression")
	}
	return body, http.StatusOK, nil
}

// handleHTTPTraces implements POST /v1/traces per the OTLP/HTTP wire spec
// (https://opentelemetry.io/docs/specs/otlp/#otlphttp). Accepts both
// `application/x-protobuf` and `application/json`. Any other method or
// content type is rejected with the canonical status code.
//
// Success is HTTP 200 + an empty (or near-empty) ExportTraceServiceResponse
// in the response media type that matches the request — the spec requires
// echoing the content-type so client SDKs can decode the partial-success
// field. Success acknowledges enqueue, not durable storage; project-resolution
// drops are reported as partial success. A full row budget is retryable (503 with
// Retry-After); a request with more rows than the whole budget can never fit and
// gets 413, which OTLP clients do not retry.
func (s *Server) handleHTTPTraces(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		writeOTLPError(w, "", http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	ct := r.Header.Get("Content-Type")
	// Strip any `;charset=...` suffix. The spec only mentions the two base
	// types but charset is allowed and common (esp. from JSON clients).
	if i := indexByte(ct, ';'); i >= 0 {
		ct = ct[:i]
	}
	ct = trimSpace(ct)

	body, code, err := readOTLPBody(r)
	if err != nil {
		if code == http.StatusRequestEntityTooLarge {
			// Mirror the gRPC-side over-cap log so an HTTP exporter's drop is
			// equally visible server-side.
			s.log.Error("http body over size cap, request rejected",
				"path", r.URL.Path, "max_bytes", maxOTLPHTTPBodyBytes)
		}
		if code == http.StatusUnsupportedMediaType {
			w.Header().Set("Accept-Encoding", "gzip, identity")
		}
		writeOTLPError(w, ct, code, err.Error())
		return
	}

	req := ptraceotlp.NewExportRequest()
	switch ct {
	case "application/x-protobuf":
		if err := req.UnmarshalProto(body); err != nil {
			writeOTLPError(w, ct, http.StatusBadRequest, "decode protobuf: "+err.Error())
			return
		}
	case "application/json":
		if err := req.UnmarshalJSON(body); err != nil {
			writeOTLPError(w, ct, http.StatusBadRequest, "decode json: "+err.Error())
			return
		}
	default:
		// The spec is explicit: unsupported media types return 415.
		w.Header().Set("Accept", "application/x-protobuf, application/json")
		writeOTLPError(w, "", http.StatusUnsupportedMediaType, "unsupported content type: "+ct)
		return
	}

	if check, ok := s.checkUsage(r.Context()); !ok {
		writeOTLPError(w, ct, http.StatusTooManyRequests, check.Reason)
		return
	}

	// Keep the authenticated workspace out-of-band; see the gRPC path above.
	receivedSpans := req.Traces().SpanCount()
	var propertyOrganizationID, propertyWorkspaceID string
	var propertyProjectIDs, hiddenProjectIDs map[string]struct{}
	var routed auth.RouteOutcome
	if result := auth.FromContext(r.Context()); result != nil {
		propertyOrganizationID, propertyWorkspaceID = result.OrgID, result.WorkspaceID
		ck := auth.CacheKeyFromContext(r.Context())
		var err error
		routed, err = s.routeSimulation(r.Context(), result, req.Traces())
		if err != nil {
			w.Header().Set("Retry-After", "5")
			writeOTLPError(w, ct, http.StatusServiceUnavailable, "simulation routing temporarily unavailable")
			return
		}
		if routed.DroppedSpans() > 0 && req.Traces().ResourceSpans().Len() == 0 {
			// Nothing of the request is stored, so nothing of it is metered.
			writeExportResponse(w, ct, partialSuccessResponse(routed))
			return
		}
		dropped, err := auth.StampResourceAttrs(r.Context(), s.auth, ck, req.Traces(), result)
		if err != nil {
			writeOTLPError(w, ct, http.StatusBadRequest, "auth stamp: "+err.Error())
			return
		}
		if dropped > 0 {
			s.log.Warn("dropped ResourceSpans with unresolvable project", "dropped", dropped)
		}
		var scoped bool
		propertyProjectIDs, scoped = result.ProjectIDsInWorkspace(propertyWorkspaceID)
		hiddenProjectIDs, _ = result.HiddenProjectIDsInWorkspace(propertyWorkspaceID)
		if !scoped {
			writeOTLPError(w, ct, http.StatusBadRequest, "auth project/workspace scope mismatch")
			return
		}
	}

	rows, ids, err := chexp.ConvertWithIdentities(r.Context(), req.Traces(), s.pricer)
	if err != nil {
		// 4xx — the SDK shouldn't retry a malformed conversion.
		writeOTLPError(w, ct, http.StatusBadRequest, "convert: "+err.Error())
		return
	}
	if outcome := s.enqueueRouted(rows, ids, routed, propertyOrganizationID, propertyWorkspaceID, propertyProjectIDs, hiddenProjectIDs); outcome != enqueueAccepted {
		s.writeRefusal(w, ct, outcome, len(rows))
		return
	}

	s.emitUsage(r.Context(), req.Traces(), int64(len(body)))

	// ExportTraceServiceResponse, including any rejected spans, encoded to
	// match the request's content-type. The spec requires the response media
	// type to match the request.
	writeExportResponse(w, ct, exportResponse(receivedSpans-req.Traces().SpanCount(), routed))
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
		writeOTLPError(w, ct, http.StatusInternalServerError, "encode response: "+err.Error())
		return
	}
	w.Header().Set("Content-Type", ct)
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(out)
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

// exportResponse is the success response of a request of which `rejected`
// spans were discarded before storage. OTLP counts rejected spans, not
// ResourceSpans containers: routing or project resolution can discard one
// resource that holds many spans even when the rest of the request is queued.
func exportResponse(rejected int, routed auth.RouteOutcome) ptraceotlp.ExportResponse {
	if rejected <= 0 {
		return ptraceotlp.NewExportResponse()
	}
	routedDrops := routed.DroppedSpans()
	if routedDrops == rejected {
		return partialSuccessResponse(routed)
	}
	resp := ptraceotlp.NewExportResponse()
	partial := resp.PartialSuccess()
	partial.SetRejectedSpans(int64(rejected))
	message := "spans with unresolvable project were rejected"
	if routedDrops > 0 {
		message = "simulation routing dropped " + strconv.Itoa(routedDrops) + " spans: " + routed.Summary() + "; " + message
	}
	partial.SetErrorMessage(message)
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
	// enqueueFull: the pending and in-flight budget cannot hold the request
	// now. Nothing is parked; the same request can fit once the writer frees
	// budget, so it is the one outcome a client should retry.
	enqueueFull
	// enqueueOversize: the request alone is larger than the whole budget, so
	// it can never be admitted however long a client waits. Nothing is
	// parked; retrying the same request is pointless.
	enqueueOversize
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

// enqueueParcels admits every parcel or none of them. The budget is checked
// against the rows of all parcels together, under the same lock that reserves
// them, so a concurrent request can never slip between the check and the
// reservation.
func (s *Server) enqueueParcels(parcels ...parcel) enqueueOutcome {
	total := 0
	for _, p := range parcels {
		total += len(p.rows)
	}
	if total == 0 {
		return enqueueAccepted
	}
	if total > s.cfg.MaxPendingRows {
		return enqueueOversize
	}
	s.pendMu.Lock()
	if total > s.cfg.MaxPendingRows-s.bufferedRows {
		s.pendMu.Unlock()
		return enqueueFull
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
	s.pend = append(s.pend, rows...)
	if s.traceNotifier != nil {
		s.pendRoots = append(s.pendRoots, traceavailable.ExtractRoots(rows, p.organizationID, p.workspaceID, p.projectIDs)...)
	}
	if s.propertyCatalog != nil && p.organizationID != "" && p.workspaceID != "" {
		for _, row := range rows {
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

// grpcRefusal is the gRPC status of a request that was not admitted.
//
// A request that merely met a full buffer is RESOURCE_EXHAUSTED with RetryInfo,
// the one form OTLP clients retry. A request larger than the whole budget is
// INVALID_ARGUMENT, which no OTLP client retries and which names the limit, so
// the sender can split the export instead of looping on it.
func (s *Server) grpcRefusal(outcome enqueueOutcome, rows int) error {
	if outcome == enqueueOversize {
		s.logOversize(rows)
		return status.Error(codes.InvalidArgument, oversizeMessage(rows, s.cfg.MaxPendingRows))
	}
	retry, err := status.New(codes.ResourceExhausted, "collector pending row budget exhausted; retry with backoff").WithDetails(&errdetails.RetryInfo{RetryDelay: durationpb.New(time.Second)})
	if err != nil {
		return status.Error(codes.Unavailable, "collector pending row budget exhausted")
	}
	return retry.Err()
}

// writeRefusal answers an OTLP/HTTP request that was not admitted: 503 with
// Retry-After for a full buffer (retryable per the OTLP spec), 413 for a
// request that can never fit (not retryable per the OTLP spec).
func (s *Server) writeRefusal(w http.ResponseWriter, contentType string, outcome enqueueOutcome, rows int) {
	if outcome == enqueueOversize {
		s.logOversize(rows)
		writeOTLPError(w, contentType, http.StatusRequestEntityTooLarge, oversizeMessage(rows, s.cfg.MaxPendingRows))
		return
	}
	w.Header().Set("Retry-After", "1")
	writeOTLPError(w, contentType, http.StatusServiceUnavailable, "collector pending row budget exhausted; retry with backoff")
}

func oversizeMessage(rows, limit int) string {
	return "request carries " + strconv.Itoa(rows) + " spans, more than the collector's max_pending_rows (" +
		strconv.Itoa(limit) + "), so it can never be admitted; split the export into batches of at most " +
		strconv.Itoa(limit) + " spans or raise max_pending_rows"
}

func (s *Server) logOversize(rows int) {
	if s.refusalLog.Allow("oversize", time.Now()) {
		s.log.Error("request over the pending row budget refused as not retryable",
			"rows", rows, "max_pending_rows", s.cfg.MaxPendingRows)
	}
}

// flushLoop runs until stopCh closes. Wakes on either an explicit kick
// (row-count threshold) or the time-based ticker.
func (s *Server) flushLoop() {
	defer s.wg.Done()
	t := time.NewTicker(s.cfg.BatchMaxAge)
	defer t.Stop()
	for {
		select {
		case <-s.stopCh:
			return
		case <-t.C:
			s.drainNow(context.Background())
		case <-s.pendCh:
			s.drainNow(context.Background())
		}
	}
}

// drainNow swaps the pending buffer and flushes it. Uses a fresh slice so
// the next request can immediately start filling without contending.
func (s *Server) drainNow(ctx context.Context) {
	s.pendMu.Lock()
	batch := s.pend
	curated := s.pendCurated
	property := s.pendProperty
	roots := s.pendRoots
	s.pend = nil
	s.pendCurated = nil
	s.pendProperty = nil
	s.pendRoots = nil
	s.pendMu.Unlock()
	if len(batch) == 0 {
		return
	}
	// Hold the reservation through the in-flight insert (including retries and
	// sidecars). Releasing it at buffer-swap would hide a stalled writer.
	defer func() {
		s.pendMu.Lock()
		s.bufferedRows -= len(batch)
		s.pendMu.Unlock()
	}()
	spanErr := s.writer.Insert(ctx, batch)
	if spanErr == nil && s.traceNotifier != nil && len(roots) > 0 {
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
	if spanErr == nil && s.propertyCatalog != nil && len(property) > 0 {
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
	// completed and Django's backfill reconciles any curated gap. One `now`
	// stamps version/first_seen for every curated row in this drain.
	if curated != nil && !curated.Empty() {
		_ = s.curated.Write(ctx, curated, time.Now().UTC())
	}
}
