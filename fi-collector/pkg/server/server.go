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
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/stats"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/durationpb"
)

var (
	errOversized = errors.New("request size exceeds total queue capacity")
	errOverload  = errors.New("queue capacity exhausted")
	errClosing   = errors.New("server is shutting down")
)

// Config is what main() passes us. Public fields = YAML wire format.
type Config struct {
	GRPCAddr       string        `yaml:"grpc_addr"`         // :4317 default
	HTTPAddr       string        `yaml:"http_addr"`         // :4318 default; empty disables
	BatchMaxRows   int           `yaml:"batch_max_rows"`    // flush after N rows
	BatchMaxAge    time.Duration `yaml:"batch_max_age"`     // flush after X time
	GRPCMaxRecvMiB int           `yaml:"grpc_max_recv_mib"` // max gRPC message size in MiB; default + rationale in New()

	QueueMaxRequests int   `yaml:"queue_max_requests"` // max pending + in-flight OTLP requests
	QueueMaxRows     int   `yaml:"queue_max_rows"`     // max pending + in-flight rows
	QueueMaxBytes    int64 `yaml:"queue_max_bytes"`    // max pending + in-flight bytes
}

type requestItem struct {
	rows     []map[string]any
	ids      *curatedwriter.Batch
	numRows  int
	numBytes int64
	done     chan error
}

// QueueStats snapshot for admin/health endpoints.
type QueueStats struct {
	PendingRequests  int   `json:"pending_requests"`
	PendingRows      int   `json:"pending_rows"`
	PendingBytes     int64 `json:"pending_bytes"`
	InFlightRequests int   `json:"inflight_requests"`
	InFlightRows     int   `json:"inflight_rows"`
	InFlightBytes    int64 `json:"inflight_bytes"`
	QueueMaxRequests int   `json:"queue_max_requests"`
	QueueMaxRows     int   `json:"queue_max_rows"`
	QueueMaxBytes    int64 `json:"queue_max_bytes"`
	RejectedOverload uint64 `json:"rejected_overload"`
	RejectedOversized uint64 `json:"rejected_oversized"`
}

// Server owns the gRPC + HTTP OTLP listeners and the batch flusher goroutine.
type Server struct {
	cfg      Config
	writer   *chwriter.Writer
	curated  *curatedwriter.Writer // CH-derived dimensions dual-write (P3b step2 HALF 2)
	auth     *auth.Authenticator
	usage    UsageEmitter
	metering Metering
	log      *slog.Logger
	pricer   chexp.Pricer
	grpc     *grpc.Server
	httpd    *http.Server

	pendMu            sync.Mutex
	pendItems         []requestItem
	pendCurated       *curatedwriter.Batch
	pendCh            chan struct{}
	curRequests       int
	curRows           int
	curBytes          int64
	inFlightRequests  int
	inFlightRows      int
	inFlightBytes     int64
	rejectedOverload  uint64
	rejectedOversized uint64
	closing           bool

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
		cfg.GRPCMaxRecvMiB = maxOTLPHTTPBodyBytes >> 20
	}
	if cfg.GRPCMaxRecvMiB > 1024 {
		cfg.GRPCMaxRecvMiB = 1024
	}

	if cfg.QueueMaxRequests <= 0 {
		cfg.QueueMaxRequests = 1000
	}
	if cfg.QueueMaxRows <= 0 {
		cfg.QueueMaxRows = 50000
	}
	if cfg.QueueMaxBytes <= 0 {
		cfg.QueueMaxBytes = 128 << 20 // 128 MiB default
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
		curated:  curatedwriter.New(writer),
		pendCh:   make(chan struct{}, 1),
		stopCh:   make(chan struct{}),
	}
	return s
}

// Run blocks until ctx is cancelled or a serve error occurs. On shutdown
// we drain pending rows once before returning so a SIGTERM doesn't lose
// in-flight batches.
func (s *Server) Run(ctx context.Context) error {
	if s.cfg.GRPCAddr == "" && s.cfg.HTTPAddr == "" {
		return fmt.Errorf("at least one of GRPCAddr / HTTPAddr must be set")
	}

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
		return err
	}
}

// shutdown stops both listeners, waits for the flusher to exit, drains the
// in-flight batch.
func (s *Server) shutdown() {
	s.pendMu.Lock()
	s.closing = true
	s.pendMu.Unlock()

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

// grpcErrLogger surfaces transport-level message-size rejections.
type grpcErrLogger struct {
	log *slog.Logger
}

type grpcMethodKey struct{}

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

	payloadBytes, _ := req.MarshalProto()
	numBytes := int64(len(payloadBytes))

	doneCh, err := h.s.enqueue(ctx, rows, ids, numBytes)
	if err != nil {
		if errors.Is(err, errOversized) {
			return ptraceotlp.NewExportResponse(), status.Errorf(codes.ResourceExhausted, "request size (%d rows, %d bytes) exceeds queue capacity limits (%d rows, %d bytes)", len(rows), numBytes, h.s.cfg.QueueMaxRows, h.s.cfg.QueueMaxBytes)
		}
		if errors.Is(err, errClosing) {
			return ptraceotlp.NewExportResponse(), status.Errorf(codes.Unavailable, "server is shutting down")
		}
		// Overload retryable backpressure with RetryInfo
		st := status.New(codes.Unavailable, "queue capacity exhausted")
		if stWithDetails, dErr := st.WithDetails(&errdetails.RetryInfo{
			RetryDelay: durationpb.New(1 * time.Second),
		}); dErr == nil {
			return ptraceotlp.NewExportResponse(), stWithDetails.Err()
		}
		return ptraceotlp.NewExportResponse(), st.Err()
	}

	if doneCh != nil {
		select {
		case <-ctx.Done():
			return ptraceotlp.NewExportResponse(), ctx.Err()
		case writeErr := <-doneCh:
			if writeErr != nil {
				return ptraceotlp.NewExportResponse(), status.Errorf(codes.Unavailable, "durable write failed: %v", writeErr)
			}
		}
	}

	h.s.emitUsage(ctx, req.Traces(), numBytes)
	return ptraceotlp.NewExportResponse(), nil
}

const maxOTLPHTTPBodyBytes = 16 << 20

func (s *Server) handleHTTPTraces(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	ct := r.Header.Get("Content-Type")
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
		w.Header().Set("Accept", "application/x-protobuf, application/json")
		http.Error(w, "unsupported content type: "+ct, http.StatusUnsupportedMediaType)
		return
	}

	if check, ok := s.checkUsage(r.Context()); !ok {
		http.Error(w, check.Reason, http.StatusTooManyRequests)
		return
	}

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
		http.Error(w, "convert: "+err.Error(), http.StatusBadRequest)
		return
	}

	numBytes := int64(len(body))
	doneCh, enqueueErr := s.enqueue(r.Context(), rows, ids, numBytes)
	if enqueueErr != nil {
		if errors.Is(enqueueErr, errOversized) {
			http.Error(w, "request payload exceeds total capacity limit", http.StatusRequestEntityTooLarge)
			return
		}
		if errors.Is(enqueueErr, errClosing) {
			w.Header().Set("Retry-After", "1")
			http.Error(w, "server is shutting down", http.StatusServiceUnavailable)
			return
		}
		w.Header().Set("Retry-After", "1")
		http.Error(w, "server queue capacity exhausted", http.StatusServiceUnavailable)
		return
	}

	if doneCh != nil {
		select {
		case <-r.Context().Done():
			http.Error(w, r.Context().Err().Error(), http.StatusServiceUnavailable)
			return
		case writeErr := <-doneCh:
			if writeErr != nil {
				w.Header().Set("Retry-After", "1")
				http.Error(w, "durable write failed: "+writeErr.Error(), http.StatusServiceUnavailable)
				return
			}
		}
	}

	s.emitUsage(r.Context(), req.Traces(), numBytes)

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

// enqueue parks rows on the pending queue, enforcing bounded limits atomically.
// Returns a done channel that receives the final durable write outcome, or an error
// if admission is rejected.
func (s *Server) enqueue(ctx context.Context, rows []map[string]any, ids *curatedwriter.Batch, numBytes int64) (<-chan error, error) {
	if len(rows) == 0 {
		return nil, nil
	}

	numRows := len(rows)

	s.pendMu.Lock()
	if s.closing {
		s.pendMu.Unlock()
		return nil, errClosing
	}

	// 1. Single request larger than total capacity is non-retryable.
	if 1 > s.cfg.QueueMaxRequests || numRows > s.cfg.QueueMaxRows || numBytes > s.cfg.QueueMaxBytes {
		s.rejectedOversized++
		s.pendMu.Unlock()
		return nil, errOversized
	}

	// 2. Total pending + in-flight capacity check.
	totalReqs := s.curRequests + s.inFlightRequests + 1
	totalRows := s.curRows + s.inFlightRows + numRows
	totalBytes := s.curBytes + s.inFlightBytes + numBytes

	if totalReqs > s.cfg.QueueMaxRequests || totalRows > s.cfg.QueueMaxRows || totalBytes > s.cfg.QueueMaxBytes {
		s.rejectedOverload++
		s.pendMu.Unlock()
		return nil, errOverload
	}

	done := make(chan error, 1)
	item := requestItem{
		rows:     rows,
		ids:      ids,
		numRows:  numRows,
		numBytes: numBytes,
		done:     done,
	}

	s.pendItems = append(s.pendItems, item)
	s.curRequests++
	s.curRows += numRows
	s.curBytes += numBytes

	if ids != nil && !ids.Empty() {
		if s.pendCurated == nil {
			s.pendCurated = curatedwriter.NewBatch()
		}
		s.pendCurated.Merge(ids)
	}

	shouldKick := s.curRows >= s.cfg.BatchMaxRows || s.curRequests >= s.cfg.QueueMaxRequests
	s.pendMu.Unlock()

	if shouldKick {
		select {
		case s.pendCh <- struct{}{}:
		default:
		}
	}

	return done, nil
}

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

// drainNow swaps the pending buffer, moves pending accounting to in-flight,
// performs the canonical write, releases capacity, and resolves completions.
func (s *Server) drainNow(ctx context.Context) {
	s.pendMu.Lock()
	items := s.pendItems
	curated := s.pendCurated

	s.pendItems = nil
	s.pendCurated = nil

	// Transition pending items to in-flight
	itemRequests := len(items)
	var itemRows int
	var itemBytes int64
	for _, it := range items {
		itemRows += it.numRows
		itemBytes += it.numBytes
	}

	s.curRequests -= itemRequests
	s.curRows -= itemRows
	s.curBytes -= itemBytes

	s.inFlightRequests += itemRequests
	s.inFlightRows += itemRows
	s.inFlightBytes += itemBytes
	s.pendMu.Unlock()

	if len(items) == 0 {
		return
	}

	var batch []map[string]any
	for _, it := range items {
		batch = append(batch, it.rows...)
	}

	writeErr := s.writer.Insert(ctx, batch)
	var finalErr error
	if writeErr != nil && !chwriter.IsDeadLettered(writeErr) {
		finalErr = writeErr
	}

	s.pendMu.Lock()
	s.inFlightRequests -= itemRequests
	s.inFlightRows -= itemRows
	s.inFlightBytes -= itemBytes
	s.pendMu.Unlock()

	for _, it := range items {
		it.done <- finalErr
	}

	if curated != nil && !curated.Empty() {
		_ = s.curated.Write(ctx, curated, time.Now().UTC())
	}
}

// QueueStats returns current queue depth, limits, and rejection stats.
func (s *Server) QueueStats() QueueStats {
	s.pendMu.Lock()
	defer s.pendMu.Unlock()
	return QueueStats{
		PendingRequests:   s.curRequests,
		PendingRows:       s.curRows,
		PendingBytes:      s.curBytes,
		InFlightRequests:  s.inFlightRequests,
		InFlightRows:      s.inFlightRows,
		InFlightBytes:     s.inFlightBytes,
		QueueMaxRequests:  s.cfg.QueueMaxRequests,
		QueueMaxRows:      s.cfg.QueueMaxRows,
		QueueMaxBytes:     s.cfg.QueueMaxBytes,
		RejectedOverload:  s.rejectedOverload,
		RejectedOversized: s.rejectedOversized,
	}
}

