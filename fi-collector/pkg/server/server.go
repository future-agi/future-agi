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
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"sync"
	"time"

	chexp "github.com/future-agi/future-agi/fi-collector/exporter/clickhouse25exporter"
	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/curatedwriter"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// Config is what main() passes us. Public fields = YAML wire format.
type Config struct {
	GRPCAddr     string        `yaml:"grpc_addr"`      // :4317 default
	HTTPAddr     string        `yaml:"http_addr"`      // :4318 default; empty disables
	BatchMaxRows int           `yaml:"batch_max_rows"` // flush after N rows
	BatchMaxAge  time.Duration `yaml:"batch_max_age"`  // flush after X time
}

// Server owns the gRPC + HTTP OTLP listeners and the batch flusher goroutine.
//
// gRPC and HTTP both decode an OTLP ExportTraceServiceRequest, run the same
// converter, and push rows onto the same `pending` buffer. The wire layer is
// the only difference: gRPC uses the generated stub; HTTP accepts
// `application/x-protobuf` and `application/json` per the OTLP/HTTP spec.
type Server struct {
	cfg      Config
	writer   *chwriter.Writer
	curated  *curatedwriter.Writer // CH-derived dimensions dual-write (P3b step2 HALF 2)
	auth     *auth.Authenticator
	usage    UsageEmitter
	metering Metering
	log      *slog.Logger
	pricer   chexp.Pricer
	grpc    *grpc.Server
	httpd   *http.Server

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
	pendMu      sync.Mutex
	pend        []map[string]any
	pendCurated *curatedwriter.Batch
	pendCh      chan struct{}

	stopCh chan struct{}
	wg     sync.WaitGroup
}

// Option configures optional Server dependencies.
type Option struct {
	log    *slog.Logger
	pricer chexp.Pricer
}

// WithLogger sets the server's logger.
func WithLogger(l *slog.Logger) Option { return Option{log: l} }

// WithPricer sets the server's token-cost pricer. Nil (the zero value)
// disables token-based cost (see chexp.Pricer).
func WithPricer(p chexp.Pricer) Option { return Option{pricer: p} }

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

	log := slog.Default()
	var pricer chexp.Pricer
	for _, o := range opts {
		if o.log != nil {
			log = o.log
		}
		if o.pricer != nil {
			pricer = o.pricer
		}
	}

	s := &Server{
		cfg:      cfg,
		writer:   writer,
		auth:     authenticator,
		usage:    usage,
		metering: metering,
		log:      log,
		pricer:   pricer,
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

// Run blocks until ctx is cancelled or a serve error occurs. On shutdown
// we drain pending rows once before returning so an SIGTERM doesn't lose
// the in-flight batch (DECISIONS: in-flight loss bounded to last 5 s as
// the deliberate at-least-once boundary).
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
		var grpcOpts []grpc.ServerOption
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

// otlpHandler implements ptraceotlp.GRPCServer. Stateless per call.
type otlpHandler struct {
	ptraceotlp.UnimplementedGRPCServer
	s *Server
}

func (h *otlpHandler) Export(ctx context.Context, req ptraceotlp.ExportRequest) (ptraceotlp.ExportResponse, error) {
	if check, ok := h.s.checkUsage(ctx); !ok {
		return ptraceotlp.NewExportResponse(), status.Errorf(codes.ResourceExhausted, "quota exceeded: %s", check.Reason)
	}

	// Stamp auth-resolved org/project IDs onto resource attributes.
	if result := auth.FromContext(ctx); result != nil {
		ck := auth.CacheKeyFromContext(ctx)
		dropped, err := auth.StampResourceAttrs(ctx, h.s.auth, ck, req.Traces(), result)
		if err != nil {
			return ptraceotlp.NewExportResponse(), status.Errorf(codes.InvalidArgument, "auth stamp: %v", err)
		}
		if dropped > 0 {
			h.s.log.Warn("dropped ResourceSpans with unresolvable project", "dropped", dropped)
		}
	}

	rows, ids, err := chexp.ConvertWithIdentities(ctx, req.Traces(), h.s.pricer)
	if err != nil {
		return ptraceotlp.NewExportResponse(), err
	}
	h.s.enqueue(rows, ids)

	payloadBytes, _ := req.MarshalProto()
	h.s.emitUsage(ctx, req.Traces(), int64(len(payloadBytes)))

	return ptraceotlp.NewExportResponse(), nil
}

// Cap the body size we will read from an OTLP/HTTP request. 16 MiB matches
// the conservative default in the upstream OTel collector receiver and
// covers a 5000-span batch carrying ~3 KiB of attrs each. Larger bodies
// almost certainly indicate a misconfigured exporter (no batching) and
// would let a single client consume memory unboundedly.
const maxOTLPHTTPBodyBytes = 16 << 20

// handleHTTPTraces implements POST /v1/traces per the OTLP/HTTP wire spec
// (https://opentelemetry.io/docs/specs/otlp/#otlphttp). Accepts both
// `application/x-protobuf` and `application/json`. Any other method or
// content type is rejected with the canonical status code.
//
// Success is HTTP 200 + an empty (or near-empty) ExportTraceServiceResponse
// in the response media type that matches the request — the spec requires
// echoing the content-type so client SDKs can decode the partial-success
// field. We always return the fully-successful response since our pipeline
// is at-least-once + dead-letter for failed inserts.
func (s *Server) handleHTTPTraces(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	ct := r.Header.Get("Content-Type")
	// Strip any `;charset=...` suffix. The spec only mentions the two base
	// types but charset is allowed and common (esp. from JSON clients).
	if i := indexByte(ct, ';'); i >= 0 {
		ct = ct[:i]
	}
	ct = trimSpace(ct)

	body, err := io.ReadAll(io.LimitReader(r.Body, maxOTLPHTTPBodyBytes+1))
	if err != nil {
		http.Error(w, "read body: "+err.Error(), http.StatusBadRequest)
		return
	}
	if len(body) > maxOTLPHTTPBodyBytes {
		http.Error(w, "payload too large", http.StatusRequestEntityTooLarge)
		return
	}

	req := ptraceotlp.NewExportRequest()
	switch ct {
	case "application/x-protobuf":
		if err := req.UnmarshalProto(body); err != nil {
			http.Error(w, "decode protobuf: "+err.Error(), http.StatusBadRequest)
			return
		}
	case "application/json":
		if err := req.UnmarshalJSON(body); err != nil {
			http.Error(w, "decode json: "+err.Error(), http.StatusBadRequest)
			return
		}
	default:
		// The spec is explicit: unsupported media types return 415.
		w.Header().Set("Accept", "application/x-protobuf, application/json")
		http.Error(w, "unsupported content type: "+ct, http.StatusUnsupportedMediaType)
		return
	}

	if check, ok := s.checkUsage(r.Context()); !ok {
		http.Error(w, check.Reason, http.StatusTooManyRequests)
		return
	}

	// Stamp auth-resolved org/project IDs onto resource attributes.
	if result := auth.FromContext(r.Context()); result != nil {
		ck := auth.CacheKeyFromContext(r.Context())
		dropped, err := auth.StampResourceAttrs(r.Context(), s.auth, ck, req.Traces(), result)
		if err != nil {
			http.Error(w, "auth stamp: "+err.Error(), http.StatusBadRequest)
			return
		}
		if dropped > 0 {
			s.log.Warn("dropped ResourceSpans with unresolvable project", "dropped", dropped)
		}
	}

	rows, ids, err := chexp.ConvertWithIdentities(r.Context(), req.Traces(), s.pricer)
	if err != nil {
		// 4xx — the SDK shouldn't retry a malformed conversion.
		http.Error(w, "convert: "+err.Error(), http.StatusBadRequest)
		return
	}
	s.enqueue(rows, ids)

	s.emitUsage(r.Context(), req.Traces(), int64(len(body)))

	// Empty ExportTraceServiceResponse — same wire shape, encoded to match
	// the request's content-type. The spec requires the response media type
	// to match the request.
	resp := ptraceotlp.NewExportResponse()
	var out []byte
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

// enqueue parks rows on the pending buffer and signals the flusher.
// We choose non-blocking signalling: if the channel already holds a tick
// the flusher will already wake up and see this batch.
//
// `ids` are the CURATED dimension identities collected for this same payload;
// they ride alongside `rows` so the curated dual-write flushes with the span
// batch. A nil / empty Batch is skipped (the common no-user/no-session case).
func (s *Server) enqueue(rows []map[string]any, ids *curatedwriter.Batch) {
	if len(rows) == 0 {
		return
	}
	s.pendMu.Lock()
	s.pend = append(s.pend, rows...)
	if ids != nil && !ids.Empty() {
		if s.pendCurated == nil {
			s.pendCurated = curatedwriter.NewBatch()
		}
		s.pendCurated.Merge(ids)
	}
	shouldKick := len(s.pend) >= s.cfg.BatchMaxRows
	s.pendMu.Unlock()
	if shouldKick {
		select {
		case s.pendCh <- struct{}{}:
		default:
		}
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
	s.pend = nil
	s.pendCurated = nil
	s.pendMu.Unlock()
	if len(batch) == 0 {
		return
	}
	_ = s.writer.Insert(ctx, batch)
	// Insert returns an error on dead-letter; the writer already persisted
	// the rows + bumped stats. We swallow here because the flusher's job
	// is to make progress, not propagate per-batch failures. /healthz
	// surfaces the writer's failure counter.

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
