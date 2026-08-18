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
	"io"
	"log/slog"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"

	chexp "github.com/future-agi/future-agi/fi-collector/exporter/clickhouse25exporter"
	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/curatedwriter"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/genproto/googleapis/rpc/errdetails"
	statuspb "google.golang.org/genproto/googleapis/rpc/status"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/stats"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/tap"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/durationpb"
)

// Config is what main() passes us. Public fields = YAML wire format.
type Config struct {
	GRPCAddr              string        `yaml:"grpc_addr"`               // :4317 default
	HTTPAddr              string        `yaml:"http_addr"`               // :4318 default; empty disables
	BatchMaxRows          int           `yaml:"batch_max_rows"`          // flush after N rows
	BatchMaxAge           time.Duration `yaml:"batch_max_age"`           // flush after X time
	GRPCMaxRecvMiB        int           `yaml:"grpc_max_recv_mib"`       // max gRPC message size in MiB; default + rationale in New()
	MaxPendingRequests    int           `yaml:"max_pending_requests"`    // queued + canonical-writer in-flight requests
	MaxPendingRows        int           `yaml:"max_pending_rows"`        // queued + canonical-writer in-flight rows
	MaxPendingMiB         int           `yaml:"max_pending_mib"`         // queued + canonical-writer in-flight payload MiB
	MaxConcurrentRequests int           `yaml:"max_concurrent_requests"` // shared HTTP + gRPC handler limit
}

var (
	errQueueFull       = errors.New("collector queue is full")
	errRequestTooLarge = errors.New("request exceeds collector queue capacity")
	errShuttingDown    = errors.New("collector is shutting down")
)

const overloadRetryDelay = time.Second
const otlpTraceExportMethod = "/opentelemetry.proto.collector.trace.v1.TraceService/Export"
const usageWorkerCount = 16

type pendingRequest struct {
	rows  int
	bytes int64
	usage *usageRecord
	done  chan chwriter.InsertOutcome
}

type admissionReservation struct {
	rows  int
	bytes int64
}

type receiverTicket struct {
	server *Server
	once   sync.Once
}

type receiverTicketKey struct{}

// QueueStats is a consistent snapshot of bounded admission state.
type QueueStats struct {
	Accepting            bool   `json:"accepting"`
	PendingRequests      int    `json:"pending_requests"`
	PendingRows          int    `json:"pending_rows"`
	PendingBytes         int64  `json:"pending_bytes"`
	ReservedRequests     int    `json:"reserved_requests"`
	ReservedRows         int    `json:"reserved_rows"`
	ReservedBytes        int64  `json:"reserved_bytes"`
	InFlightRequests     int    `json:"in_flight_requests"`
	InFlightRows         int    `json:"in_flight_rows"`
	InFlightBytes        int64  `json:"in_flight_bytes"`
	MaxPendingRequests   int    `json:"max_pending_requests"`
	MaxPendingRows       int    `json:"max_pending_rows"`
	MaxPendingBytes      int64  `json:"max_pending_bytes"`
	RejectedQueueFull    uint64 `json:"rejected_queue_full"`
	RejectedTooLarge     uint64 `json:"rejected_too_large"`
	RejectedShuttingDown uint64 `json:"rejected_shutting_down"`
	UsageEventsDropped   uint64 `json:"usage_events_dropped"`
}

// Server owns the gRPC + HTTP OTLP listeners and the batch flusher goroutine.
//
// gRPC and HTTP both decode an OTLP ExportTraceServiceRequest, run the same
// converter, and push rows onto the same `pending` buffer. The wire layer is
// the only difference: gRPC uses the generated stub; HTTP accepts
// `application/x-protobuf` and `application/json` per the OTLP/HTTP spec.
type Server struct {
	cfg         Config
	writer      *chwriter.Writer
	curated     *curatedwriter.Writer // CH-derived dimensions dual-write (P3b step2 HALF 2)
	auth        *auth.Authenticator
	usage       UsageEmitter
	metering    Metering
	log         *slog.Logger
	pricer      chexp.Pricer
	grpc        *grpc.Server
	httpd       *http.Server
	receiverSem chan struct{}
	receiverWG  sync.WaitGroup

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
	pendMu           sync.Mutex
	drainMu          sync.Mutex
	accepting        bool
	pend             []map[string]any
	pendCurated      *curatedwriter.Batch
	pendRequests     []pendingRequest
	pendingRows      int
	pendingBytes     int64
	reservedRequests int
	reservedRows     int
	reservedBytes    int64
	inFlightRequests int
	inFlightRows     int
	inFlightBytes    int64
	rejectedFull     uint64
	rejectedLarge    uint64
	rejectedStopping uint64
	usageDropped     uint64
	pendCh           chan struct{}

	stopCh        chan struct{}
	shutdownOnce  sync.Once
	reservationWG sync.WaitGroup
	usageCh       chan *usageRecord
	wg            sync.WaitGroup
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
//   - MaxPendingRequests 1000, MaxPendingRows 20000, MaxPendingMiB 64.
//   - MaxConcurrentRequests 32.
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
	if cfg.GRPCMaxRecvMiB <= 0 {
		// Go gRPC's default 4 MiB rejects large single-span exports (e.g. an
		// ended voice call with full transcript) with RESOURCE_EXHAUSTED.
		// Match the OTLP/HTTP body cap so both transports accept the same spans.
		cfg.GRPCMaxRecvMiB = maxOTLPHTTPBodyBytes >> 20
	}
	if cfg.GRPCMaxRecvMiB > 1024 {
		cfg.GRPCMaxRecvMiB = 1024 // keep the <<20 shift well under proto's 2 GiB ceiling
	}
	if cfg.MaxPendingRequests <= 0 {
		cfg.MaxPendingRequests = 1000
	}
	if cfg.MaxPendingRows <= 0 {
		cfg.MaxPendingRows = 20000
	}
	if cfg.MaxPendingMiB <= 0 {
		cfg.MaxPendingMiB = 64
	}
	if cfg.MaxPendingMiB > 4096 {
		cfg.MaxPendingMiB = 4096
	}
	if cfg.MaxConcurrentRequests <= 0 {
		cfg.MaxConcurrentRequests = 32
	}
	if cfg.MaxConcurrentRequests > cfg.MaxPendingRequests {
		cfg.MaxConcurrentRequests = cfg.MaxPendingRequests
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
		curated:     curatedwriter.New(writer),
		accepting:   true,
		receiverSem: make(chan struct{}, cfg.MaxConcurrentRequests),
		usageCh:     make(chan *usageRecord, cfg.MaxPendingRequests),
		pendCh:      make(chan struct{}, 1),
		stopCh:      make(chan struct{}),
	}
	return s
}

// Run blocks until ctx is cancelled or a serve error occurs. On shutdown it
// stops admission first, lets active handlers finish, then drains any accepted
// batch before returning.
func (s *Server) Run(ctx context.Context) error {
	if s.cfg.GRPCAddr == "" && s.cfg.HTTPAddr == "" {
		return fmt.Errorf("at least one of GRPCAddr / HTTPAddr must be set")
	}

	// Bind every configured listener before serving any traffic. Otherwise a
	// successful gRPC bind followed by an HTTP bind failure can accept exports
	// before the flusher exists and then strand them during startup rollback.
	serveErr := make(chan error, 2)
	var grpcLis, httpLis net.Listener

	if s.cfg.GRPCAddr != "" {
		lis, err := net.Listen("tcp", s.cfg.GRPCAddr)
		if err != nil {
			return fmt.Errorf("listen grpc %s: %w", s.cfg.GRPCAddr, err)
		}
		grpcLis = lis
	}
	if s.cfg.HTTPAddr != "" {
		lis, err := net.Listen("tcp", s.cfg.HTTPAddr)
		if err != nil {
			if grpcLis != nil {
				_ = grpcLis.Close()
			}
			return fmt.Errorf("listen http %s: %w", s.cfg.HTTPAddr, err)
		}
		httpLis = lis
	}

	if grpcLis != nil {
		s.log.Info("grpc listener", "addr", s.cfg.GRPCAddr, "max_recv_mib", s.cfg.GRPCMaxRecvMiB)
		grpcOpts := []grpc.ServerOption{
			grpc.MaxRecvMsgSize(s.cfg.GRPCMaxRecvMiB << 20),
			grpc.StatsHandler(&grpcErrLogger{log: s.log}),
			grpc.InTapHandle(s.receiverTap),
		}
		interceptors := []grpc.UnaryServerInterceptor{s.receiverInterceptor()}
		if s.auth != nil {
			interceptors = append(interceptors, s.auth.GRPCInterceptor())
		}
		grpcOpts = append(grpcOpts, grpc.ChainUnaryInterceptor(interceptors...))
		s.grpc = grpc.NewServer(grpcOpts...)
		ptraceotlp.RegisterGRPCServer(s.grpc, &otlpHandler{s: s})
	}

	if httpLis != nil {
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
			ReadTimeout:       30 * time.Second,
		}
	}

	s.wg.Add(1 + usageWorkerCount)
	go s.flushLoop()
	for i := 0; i < usageWorkerCount; i++ {
		go s.usageLoop()
	}
	if s.grpc != nil {
		go func() { serveErr <- s.grpc.Serve(grpcLis) }()
	}
	if s.httpd != nil {
		go func() { serveErr <- s.httpd.Serve(httpLis) }()
	}

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

// shutdown stops admission, drains accepted work, and is safe to call when one
// of grpc/httpd is nil. shutdownOnce also makes it safe for repeated callers.
func (s *Server) shutdown() {
	s.shutdownOnce.Do(func() {
		s.pendMu.Lock()
		s.accepting = false
		s.pendMu.Unlock()
		s.kickFlusher()

		var listenerWG sync.WaitGroup
		if s.grpc != nil {
			listenerWG.Add(1)
			go func() {
				defer listenerWG.Done()
				s.grpc.GracefulStop()
			}()
		}
		if s.httpd != nil {
			listenerWG.Add(1)
			go func() {
				defer listenerWG.Done()
				shCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				if err := s.httpd.Shutdown(shCtx); err != nil {
					_ = s.httpd.Close()
				}
			}()
		}
		listenerWG.Wait()
		s.receiverWG.Wait()
		s.reservationWG.Wait()
		s.kickFlusher()
		close(s.stopCh)
		s.drainNow(context.Background())
		close(s.usageCh)
		s.wg.Wait()
	})
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
	if !ok {
		return
	}
	releaseReceiverFromContext(ctx)
	if end.Error == nil {
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

	payloadBytes, _ := req.MarshalProto()
	reservation, err := h.s.reserve(req.Traces().SpanCount(), int64(len(payloadBytes)))
	if err != nil {
		return ptraceotlp.NewExportResponse(), grpcAdmissionError(err)
	}
	rows, ids, err := chexp.ConvertWithIdentities(ctx, req.Traces(), h.s.pricer)
	if err != nil {
		h.s.releaseReservation(reservation)
		return ptraceotlp.NewExportResponse(), status.Errorf(codes.InvalidArgument, "convert: %v", err)
	}

	usage := usageFromContext(ctx, req.Traces(), payloadBytes)
	done, err := h.s.commitReservation(reservation, rows, ids, usage)
	if err != nil {
		return ptraceotlp.NewExportResponse(), grpcAdmissionError(err)
	}
	releaseReceiverFromContext(ctx)
	select {
	case outcome := <-done:
		if !outcome.Durable {
			return ptraceotlp.NewExportResponse(), grpcRetryableError("collector could not durably accept batch")
		}
	case <-ctx.Done():
		return ptraceotlp.NewExportResponse(), status.FromContextError(ctx.Err()).Err()
	}

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
	ticket, err := s.tryAcquireReceiver()
	if err != nil {
		s.writeHTTPAdmissionError(w, ct, err)
		return
	}
	defer ticket.release()

	body, err := io.ReadAll(io.LimitReader(r.Body, maxOTLPHTTPBodyBytes+1))
	if err != nil {
		http.Error(w, "read body: "+err.Error(), http.StatusBadRequest)
		return
	}
	if len(body) > maxOTLPHTTPBodyBytes {
		// Mirror the gRPC-side over-cap log so an HTTP exporter's drop is
		// equally visible server-side.
		s.log.Error("http body over size cap, request rejected",
			"path", r.URL.Path, "max_bytes", maxOTLPHTTPBodyBytes)
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

	reservation, err := s.reserve(req.Traces().SpanCount(), int64(len(body)))
	if err != nil {
		s.writeHTTPAdmissionError(w, ct, err)
		return
	}
	rows, ids, err := chexp.ConvertWithIdentities(r.Context(), req.Traces(), s.pricer)
	if err != nil {
		s.releaseReservation(reservation)
		// 4xx — the SDK shouldn't retry a malformed conversion.
		http.Error(w, "convert: "+err.Error(), http.StatusBadRequest)
		return
	}
	usage := usageFromContext(r.Context(), req.Traces(), body)
	done, err := s.commitReservation(reservation, rows, ids, usage)
	if err != nil {
		s.writeHTTPAdmissionError(w, ct, err)
		return
	}
	ticket.release()
	select {
	case outcome := <-done:
		if !outcome.Durable {
			w.Header().Set("Retry-After", "1")
			writeOTLPHTTPError(w, ct, http.StatusServiceUnavailable, codes.Unavailable, "collector could not durably accept batch")
			return
		}
	case <-r.Context().Done():
		return
	}

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

func (s *Server) tryAcquireReceiver() (*receiverTicket, error) {
	s.pendMu.Lock()
	if !s.accepting {
		s.rejectedStopping++
		s.pendMu.Unlock()
		return nil, errShuttingDown
	}
	select {
	case s.receiverSem <- struct{}{}:
		s.receiverWG.Add(1)
		s.pendMu.Unlock()
		return &receiverTicket{server: s}, nil
	default:
		s.rejectedFull++
		s.pendMu.Unlock()
		return nil, errQueueFull
	}
}

func (t *receiverTicket) release() {
	if t == nil {
		return
	}
	t.once.Do(func() {
		<-t.server.receiverSem
		t.server.receiverWG.Done()
	})
}

func (s *Server) receiverTap(ctx context.Context, info *tap.Info) (context.Context, error) {
	if info.FullMethodName != otlpTraceExportMethod {
		return ctx, nil
	}
	ticket, err := s.tryAcquireReceiver()
	if err != nil {
		return ctx, grpcAdmissionError(err)
	}
	return context.WithValue(ctx, receiverTicketKey{}, ticket), nil
}

func (s *Server) receiverInterceptor() grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req any, _ *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
		ticket, _ := ctx.Value(receiverTicketKey{}).(*receiverTicket)
		defer ticket.release()
		return handler(ctx, req)
	}
}

func releaseReceiverFromContext(ctx context.Context) {
	ticket, _ := ctx.Value(receiverTicketKey{}).(*receiverTicket)
	ticket.release()
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

// enqueue is the white-box convenience path for already-converted rows. OTLP
// handlers reserve capacity before conversion and call commitReservation.
//
// `ids` are the CURATED dimension identities collected for this same payload;
// they ride alongside `rows` so the curated dual-write flushes with the span
// batch. A nil / empty Batch is skipped (the common no-user/no-session case).
func (s *Server) enqueue(rows []map[string]any, ids *curatedwriter.Batch, payloadBytes int64, usage *usageRecord) (<-chan chwriter.InsertOutcome, error) {
	reservation, err := s.reserve(len(rows), payloadBytes)
	if err != nil {
		return nil, err
	}
	return s.commitReservation(reservation, rows, ids, usage)
}

// reserve atomically claims queue capacity before conversion expands pdata
// into row maps. This bounds the admitted conversion work as well as queued
// and canonical-writer in-flight batches.
func (s *Server) reserve(rows int, payloadBytes int64) (*admissionReservation, error) {
	s.pendMu.Lock()
	if !s.accepting {
		s.rejectedStopping++
		s.pendMu.Unlock()
		return nil, errShuttingDown
	}
	if rows == 0 {
		s.pendMu.Unlock()
		return &admissionReservation{}, nil
	}
	maxBytes := int64(s.cfg.MaxPendingMiB) << 20
	if rows > s.cfg.MaxPendingRows || payloadBytes > maxBytes {
		s.rejectedLarge++
		s.pendMu.Unlock()
		return nil, errRequestTooLarge
	}
	activeRequests := len(s.pendRequests) + s.reservedRequests + s.inFlightRequests
	activeRows := s.pendingRows + s.reservedRows + s.inFlightRows
	activeBytes := s.pendingBytes + s.reservedBytes + s.inFlightBytes
	if activeRequests >= s.cfg.MaxPendingRequests ||
		activeRows > s.cfg.MaxPendingRows-rows ||
		activeBytes > maxBytes-payloadBytes {
		s.rejectedFull++
		s.pendMu.Unlock()
		s.kickFlusher()
		return nil, errQueueFull
	}
	s.reservedRequests++
	s.reservedRows += rows
	s.reservedBytes += payloadBytes
	s.reservationWG.Add(1)
	s.pendMu.Unlock()
	return &admissionReservation{rows: rows, bytes: payloadBytes}, nil
}

func (s *Server) releaseReservation(reservation *admissionReservation) {
	if reservation == nil || reservation.rows == 0 {
		return
	}
	s.pendMu.Lock()
	s.reservedRequests--
	s.reservedRows -= reservation.rows
	s.reservedBytes -= reservation.bytes
	s.pendMu.Unlock()
	s.reservationWG.Done()
}

func (s *Server) commitReservation(reservation *admissionReservation, rows []map[string]any, ids *curatedwriter.Batch, usage *usageRecord) (<-chan chwriter.InsertOutcome, error) {
	done := make(chan chwriter.InsertOutcome, 1)
	if len(rows) == 0 {
		s.releaseReservation(reservation)
		done <- chwriter.InsertOutcome{Durable: true}
		return done, nil
	}
	s.pendMu.Lock()
	if reservation == nil || reservation.rows == 0 {
		s.pendMu.Unlock()
		return nil, errors.New("missing queue reservation")
	}
	if len(rows) > reservation.rows {
		// Converter output is expected to be at most one row per input span.
		// Fail closed rather than let a changed converter violate admission.
		s.reservedRequests--
		s.reservedRows -= reservation.rows
		s.reservedBytes -= reservation.bytes
		s.rejectedLarge++
		s.pendMu.Unlock()
		s.reservationWG.Done()
		return nil, errRequestTooLarge
	}
	s.reservedRequests--
	s.reservedRows -= reservation.rows
	s.reservedBytes -= reservation.bytes
	s.pend = append(s.pend, rows...)
	s.pendRequests = append(s.pendRequests, pendingRequest{
		rows: len(rows), bytes: reservation.bytes, usage: usage, done: done,
	})
	s.pendingRows += len(rows)
	s.pendingBytes += reservation.bytes
	if ids != nil && !ids.Empty() {
		if s.pendCurated == nil {
			s.pendCurated = curatedwriter.NewBatch()
		}
		s.pendCurated.Merge(ids)
	}
	shouldKick := len(s.pend) >= s.cfg.BatchMaxRows || !s.accepting
	s.pendMu.Unlock()
	s.reservationWG.Done()
	if shouldKick {
		s.kickFlusher()
	}
	return done, nil
}

func (s *Server) kickFlusher() {
	select {
	case s.pendCh <- struct{}{}:
	default:
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
	s.drainMu.Lock()
	defer s.drainMu.Unlock()

	s.pendMu.Lock()
	batch := s.pend
	curated := s.pendCurated
	requests := s.pendRequests
	batchRows := s.pendingRows
	batchBytes := s.pendingBytes
	s.pend = nil
	s.pendCurated = nil
	s.pendRequests = nil
	s.pendingRows = 0
	s.pendingBytes = 0
	s.inFlightRequests += len(requests)
	s.inFlightRows += batchRows
	s.inFlightBytes += batchBytes
	s.pendMu.Unlock()
	if len(batch) == 0 {
		return
	}
	outcome := s.writer.InsertWithOutcome(ctx, batch)
	if outcome.Err != nil {
		level := slog.LevelError
		if outcome.Durable {
			level = slog.LevelWarn
		}
		s.log.Log(ctx, level, "canonical span batch write failed",
			"durable", outcome.Durable, "dead_lettered", outcome.DeadLettered, "err", outcome.Err)
	}

	s.pendMu.Lock()
	s.inFlightRequests -= len(requests)
	s.inFlightRows -= batchRows
	s.inFlightBytes -= batchBytes
	s.pendMu.Unlock()
	for _, request := range requests {
		request.done <- outcome
	}
	if outcome.Durable {
		// Billing is deliberately outside the canonical flusher and request
		// lifecycle. A client cancellation after durable acceptance must neither
		// block ingestion nor skip usage; payload-based IDs deduplicate retries.
		for _, request := range requests {
			if request.usage == nil {
				continue
			}
			select {
			case s.usageCh <- request.usage:
			default:
				s.pendMu.Lock()
				s.usageDropped++
				s.pendMu.Unlock()
				s.log.Warn("usage queue full; ingestion usage event dropped", "org_id", request.usage.orgID)
			}
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
	s.pendMu.Lock()
	accepting := s.accepting
	s.pendMu.Unlock()
	if accepting && curated != nil && !curated.Empty() {
		_ = s.curated.Write(ctx, curated, time.Now().UTC())
	}
}

func (s *Server) usageLoop() {
	defer s.wg.Done()
	for record := range s.usageCh {
		s.emitUsage(record)
	}
}

// QueueSnapshot returns bounded admission state for health and diagnostics.
func (s *Server) QueueSnapshot() QueueStats {
	s.pendMu.Lock()
	defer s.pendMu.Unlock()
	return QueueStats{
		Accepting:            s.accepting,
		PendingRequests:      len(s.pendRequests),
		PendingRows:          s.pendingRows,
		PendingBytes:         s.pendingBytes,
		ReservedRequests:     s.reservedRequests,
		ReservedRows:         s.reservedRows,
		ReservedBytes:        s.reservedBytes,
		InFlightRequests:     s.inFlightRequests,
		InFlightRows:         s.inFlightRows,
		InFlightBytes:        s.inFlightBytes,
		MaxPendingRequests:   s.cfg.MaxPendingRequests,
		MaxPendingRows:       s.cfg.MaxPendingRows,
		MaxPendingBytes:      int64(s.cfg.MaxPendingMiB) << 20,
		RejectedQueueFull:    s.rejectedFull,
		RejectedTooLarge:     s.rejectedLarge,
		RejectedShuttingDown: s.rejectedStopping,
		UsageEventsDropped:   s.usageDropped,
	}
}

func grpcAdmissionError(err error) error {
	switch {
	case errors.Is(err, errRequestTooLarge):
		return status.Error(codes.ResourceExhausted, err.Error())
	case errors.Is(err, errQueueFull), errors.Is(err, errShuttingDown):
		return grpcRetryableError(err.Error())
	default:
		return status.Error(codes.Internal, err.Error())
	}
}

func grpcRetryableError(message string) error {
	st := status.New(codes.Unavailable, message)
	withDetails, err := st.WithDetails(&errdetails.RetryInfo{RetryDelay: durationpb.New(overloadRetryDelay)})
	if err != nil {
		return st.Err()
	}
	return withDetails.Err()
}

func (s *Server) writeHTTPAdmissionError(w http.ResponseWriter, contentType string, err error) {
	switch {
	case errors.Is(err, errRequestTooLarge):
		writeOTLPHTTPError(w, contentType, http.StatusRequestEntityTooLarge, codes.ResourceExhausted, err.Error())
	case errors.Is(err, errQueueFull), errors.Is(err, errShuttingDown):
		w.Header().Set("Retry-After", "1")
		writeOTLPHTTPError(w, contentType, http.StatusServiceUnavailable, codes.Unavailable, err.Error())
	default:
		writeOTLPHTTPError(w, contentType, http.StatusInternalServerError, codes.Internal, err.Error())
	}
}

func writeOTLPHTTPError(w http.ResponseWriter, contentType string, httpStatus int, code codes.Code, message string) {
	body := &statuspb.Status{Code: int32(code), Message: message}
	var (
		encoded []byte
		err     error
	)
	if contentType == "application/json" {
		encoded, err = protojson.Marshal(body)
	} else {
		contentType = "application/x-protobuf"
		encoded, err = proto.Marshal(body)
	}
	if err != nil {
		http.Error(w, message, httpStatus)
		return
	}
	w.Header().Set("Content-Type", contentType)
	w.WriteHeader(httpStatus)
	_, _ = w.Write(encoded)
}
