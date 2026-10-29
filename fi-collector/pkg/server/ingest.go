package server

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"sort"
	"strconv"
	"sync"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

// The ingest pipeline shared by OTLP/HTTP and OTLP/gRPC, in order:
//
//  1. refusals that need no body: auth (HTTP middleware; gRPC handler, after
//     the tap handle refuses calls with no credentials), quota, and the
//     live-heap backstop (heapGuard);
//  2. a wire lease, then the body is read (readOTLPHTTPBody, recvExport);
//  3. the scan (otlp_scan.go), its limits, and a work lease for its price
//     (leaseWork);
//  4. decode, project header, auth stamp, conversion, enqueue: the rows'
//     part of the work lease goes to the pending batch with the rows
//     (adoptRows) and is released when the drain that stores them ends.
//
// Every refusal is one of three kinds:
//
//   - permanent: sending the same request again cannot succeed. HTTP 413 (a
//     size or count limit) or 400 (malformed, too deep), gRPC
//     INVALID_ARGUMENT. OTLP exporters drop the batch.
//   - busy: retry later. HTTP 503 with Retry-After, gRPC UNAVAILABLE; a quota
//     refusal is HTTP 429 without Retry-After, gRPC RESOURCE_EXHAUSTED.
//   - read timeout: the body or message did not arrive within
//     otlpReadTimeout. HTTP 503 with Retry-After, gRPC DEADLINE_EXCEEDED.
//
// OTLP exporters retry 503/UNAVAILABLE and read timeouts within their retry
// and deadline policy. Quota RESOURCE_EXHAUSTED has no RetryInfo: the Go SDK
// and Collector otlp exporter do not retry it; other SDKs may. Auth refusals (HTTP
// 401, gRPC UNAUTHENTICATED), when credentials cannot be checked (HTTP 503,
// gRPC UNAVAILABLE), and unsupported methods, media types or codings (HTTP
// 405, 415) are answered as before. Refusals are counted in /healthz
// (ingest.rejected, by transport, reason and code; HTTP auth refusals as
// http/auth/401) and logged at most once a second per reason; auth logs its
// own refusals.

type rejectKind uint8

const (
	rejectPermanent rejectKind = iota
	rejectBusy
	rejectTimeout
)

// rejection is a request the pipeline refuses.
type rejection struct {
	reason   string // metrics label
	kind     rejectKind
	tooLarge bool // a permanent refusal for size (413), not a bad request (400)
	msg      string
	// status and code, when set, replace the status the kind maps to.
	status     int
	code       codes.Code
	retryAfter string // optional override for existing simulation-routing refusals
	logged     bool   // the simulation router owns its own severity/rate-limited log
}

func (r *rejection) Error() string { return r.msg }

func (r *rejection) httpStatus() int {
	if r.status != 0 {
		return r.status
	}
	switch r.kind {
	case rejectBusy, rejectTimeout:
		return http.StatusServiceUnavailable
	}
	if r.tooLarge {
		return http.StatusRequestEntityTooLarge
	}
	return http.StatusBadRequest
}

func (r *rejection) grpcCode() codes.Code {
	if r.code != codes.OK {
		return r.code
	}
	switch r.kind {
	case rejectBusy:
		return codes.Unavailable
	case rejectTimeout:
		return codes.DeadlineExceeded
	}
	return codes.InvalidArgument
}

var (
	rejectProjectUnavailable = &rejection{reason: "project_unavailable", kind: rejectBusy, msg: "project resolution temporarily unavailable, retry later"}
	rejectHeap               = &rejection{reason: "heap", kind: rejectBusy,
		msg: "server busy (memory), retry later"}
	rejectWireBusy = &rejection{reason: "wire_busy", kind: rejectBusy,
		msg: "server busy receiving other requests, retry later"}
	rejectWorkBusy = &rejection{reason: "work_busy", kind: rejectBusy,
		msg: "server busy processing other requests, retry later"}
	rejectStreams = &rejection{reason: "grpc_streams", kind: rejectBusy,
		msg: "server busy: too many concurrent requests, retry later"}
	rejectUnverified = &rejection{reason: "grpc_unverified", kind: rejectBusy,
		msg: "server busy: too many requests being authenticated, retry later"}
	rejectReadTimeout = &rejection{reason: "read_timeout", kind: rejectTimeout,
		msg: "request body not received in time, retry"}
	// rejectNewProjects: the request names more projects new to its key than
	// one request may add (auth.MaxNewProjectsPerRequest). The first of them
	// were tried, so a retry makes progress (auth.ErrTooManyNewProjects).
	rejectNewProjects = &rejection{reason: "new_projects", kind: rejectBusy,
		msg: fmt.Sprintf("more than %d new project names in one request; up to %d are added per request, retry", auth.MaxNewProjectsPerRequest, auth.MaxNewProjectsPerRequest)}
)

// quotaRejection is a request over its key's usage quota (HTTP 429, gRPC
// RESOURCE_EXHAUSTED without RetryInfo). Monthly quotas and budget pauses
// have no short recovery time, so do not override exporter backoff with 1s.
func quotaRejection(reason string) *rejection {
	return &rejection{reason: "quota", kind: rejectBusy, status: http.StatusTooManyRequests,
		code: codes.ResourceExhausted, msg: "quota exceeded: " + reason}
}

// rejectRecvTooLarge is a gRPC message over the receive cap.
func (s *Server) rejectRecvTooLarge() *rejection {
	return &rejection{reason: "body_size", tooLarge: true,
		msg: fmt.Sprintf("message too large: over the %d MiB OTLP/gRPC receive cap (FI_GRPC_MAX_RECV_MIB); send smaller batches", s.limits.maxRecv>>20)}
}

func (s *Server) rejectBodyTooLarge() *rejection {
	return &rejection{reason: "body_size", tooLarge: true,
		msg: fmt.Sprintf("payload too large: over the %d MiB OTLP body cap (FI_OTLP_MAX_BODY_MIB)", s.limits.maxBody>>20)}
}

// ingestStats counts what the pipeline refuses, for /healthz and the logs.
type ingestStats struct {
	mu       sync.Mutex
	rejected map[string]int64 // transport/reason/code
	logs     map[string]*logSample
}

func (s *Server) countRejection(transport, code string, r *rejection) (logIt bool, held int64) {
	key := transport + "/" + r.reason + "/" + code
	s.stats.mu.Lock()
	if s.stats.rejected == nil {
		s.stats.rejected = map[string]int64{}
		s.stats.logs = map[string]*logSample{}
	}
	s.stats.rejected[key]++
	ls := s.stats.logs[r.reason]
	if ls == nil {
		ls = &logSample{}
		s.stats.logs[r.reason] = ls
	}
	s.stats.mu.Unlock()
	return ls.allow(time.Now())
}

func (s *Server) logRejection(transport, code string, r *rejection, held int64) {
	level := s.log.Warn
	if r.kind == rejectPermanent {
		level = s.log.Error // the exporter drops the batch
	}
	level("OTLP request refused",
		"transport", transport, "reason", r.reason, "code", code, "err", r.msg,
		"wire_in_use", s.wire.inUse.Load(), "work_in_use", s.work.inUse.Load(),
		"suppressed", held)
}

// rejectHTTP answers an OTLP/HTTP request the pipeline refuses. ct is the
// request's media type (application/x-protobuf or application/json), or ""
// when it is not one of them (writeHTTPRefusal).
func (s *Server) rejectHTTP(w http.ResponseWriter, ct string, r *rejection) {
	st := r.httpStatus()
	if ok, held := s.countRejection("http", strconv.Itoa(st), r); ok && !r.logged {
		s.logRejection("http", strconv.Itoa(st), r, held)
	}
	if r.kind != rejectPermanent && st != http.StatusTooManyRequests {
		retryAfter := r.retryAfter
		if retryAfter == "" {
			retryAfter = "1"
		}
		w.Header().Set("Retry-After", retryAfter)
	}
	// A failed internal reauthentication is already counted above, unlike
	// the middleware's pre-body 401. Do not count it again in the wrapper.
	if counted, ok := w.(*authStatusWriter); ok {
		counted.counted = true
	}
	writeHTTPRefusal(w, ct, st, r.grpcCode(), r.msg)
}

// writeHTTPRefusal answers HTTP status st with a google.rpc.Status (code,
// msg) encoded in the request's media type, as OTLP/HTTP specifies for
// failures: the OpenTelemetry Collector's otlphttp exporter and the SDKs
// show its message, where a text/plain body left them only "413 Request
// Entity Too Large". A request in some other media type (a 415, a 405) gets
// msg as text/plain. code is what OTLP/gRPC answers for the same refusal.
func writeHTTPRefusal(w http.ResponseWriter, ct string, st int, code codes.Code, msg string) {
	var body []byte
	var err error
	sp := status.New(code, msg).Proto()
	switch ct {
	case "application/x-protobuf":
		body, err = proto.Marshal(sp)
	case "application/json":
		body, err = protojson.Marshal(sp)
	default:
		err = errNoOTLPMediaType
	}
	if err != nil {
		http.Error(w, msg, st)
		return
	}
	h := w.Header()
	h.Del("Content-Length")
	h.Set("Content-Type", ct)
	h.Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(st)
	_, _ = w.Write(body)
}

var errNoOTLPMediaType = errors.New("not an OTLP media type")

// rejectGRPC is the status for an OTLP/gRPC call the pipeline refuses.
func (s *Server) rejectGRPC(r *rejection) error {
	code := r.grpcCode()
	if ok, held := s.countRejection("grpc", code.String(), r); ok && !r.logged {
		s.logRejection("grpc", code.String(), r, held)
	}
	return status.Error(code, r.msg)
}

// countAuthRefusals counts the OTLP/HTTP auth middleware's 401s in
// ingest.rejected (as http/auth/401). The middleware logs them itself.
func (s *Server) countAuthRefusals(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		next.ServeHTTP(&authStatusWriter{ResponseWriter: w, s: s}, r)
	})
}

type authStatusWriter struct {
	http.ResponseWriter
	s       *Server
	counted bool
}

func (w *authStatusWriter) WriteHeader(code int) {
	if code == http.StatusUnauthorized && !w.counted {
		w.s.countRejection("http", "401", &rejection{reason: "auth"})
	}
	w.ResponseWriter.WriteHeader(code)
}

// Unwrap lets http.ResponseController reach the connection (the body read
// deadline).
func (w *authStatusWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }

// exportWork owns both lifetimes while preparation may add authenticated
// resource fields. A successful preparation reprices the final pdata shape
// before conversion; retries retain the high-water reservation, never a partly
// stamped request or a new wait budget.
type exportWork struct {
	work, rows *lease
}

// leaseWork scans and leases before decoding. rows goes to the pending batch;
// work covers the handler. Final authenticated additions are checked before
// conversion by pricePreparedExport, under these same leases and wait budget.
func (s *Server) leaseWork(ctx context.Context, body []byte, jsonBody bool, headers ...auth.ProjectNameValues) (*exportWork, *rejection) {
	var values auth.ProjectNameValues
	if len(headers) != 0 {
		values = headers[0]
	}
	stamps := resourceStamps(ctx, values)
	var shape otlpShape
	var err error
	if jsonBody {
		shape, err = scanOTLPJSON(body, stamps)
	} else {
		shape, err = scanOTLPProto(body, stamps)
	}
	if err != nil {
		var se *scanError
		if errors.As(err, &se) {
			return nil, &rejection{reason: se.reason, tooLarge: se.tooLarge, msg: "decode: " + se.msg}
		}
		return nil, &rejection{reason: "malformed", msg: "decode: " + err.Error()}
	}
	if shape.rowJSONBytes > s.limits.maxEncoded {
		return nil, &rejection{reason: "stored_size", tooLarge: true,
			msg: fmt.Sprintf("payload too large: its row JSON before storage compression is about %d MiB, over the %d MiB limit for one request (%dx the OTLP body cap); send smaller batches",
				shape.rowJSONBytes>>20, s.limits.maxEncoded>>20, maxEncodedPerBody)}
	}
	p := shape.price()
	work, err := s.work.acquire(ctx, p.total())
	switch {
	case errors.Is(err, errLeaseTooLarge):
		return nil, &rejection{reason: "budget", tooLarge: true,
			msg: fmt.Sprintf("payload too large: processing it needs about %d MiB, over the %d MiB this server can give one request; send smaller batches (OpenTelemetry Collector: lower send_batch_max_size), or raise FI_INGEST_BUDGET_MIB (or GOMEMLIMIT)",
				p.total()>>20, s.work.size>>20)}
	case err != nil:
		return nil, rejectWorkBusy
	}
	return &exportWork{work: work, rows: work.split(p.rows)}, nil
}

// settleRows hands a request's rows lease to the pending batch when its rows
// may have been enqueued, and releases it otherwise.
func (s *Server) settleRows(rows *lease, enqueued bool) {
	if !enqueued {
		rows.release()
		return
	}
	s.adoptRows(rows)
}

// adoptRows parks a rows lease with the pending batch. The drain that next
// swaps the batch releases it when it ends. A lease parked just after a swap
// whose batch already held its rows is released one drain late, never early:
// drains release their leases in swap order (releaseDrained), even when
// several run at once.
func (s *Server) adoptRows(l *lease) {
	if l == nil {
		return
	}
	n := l.size()
	s.pendMu.Lock()
	s.pendLeases = append(s.pendLeases, l)
	s.pendLeased += n
	kick := s.pendLeased > s.work.size/2
	s.pendMu.Unlock()
	if kick {
		s.kickFlush()
	}
}

// kickFlush wakes the flusher without blocking.
func (s *Server) kickFlush() {
	select {
	case s.pendCh <- struct{}{}:
	default:
	}
}

// IngestStats is the ingest section of /healthz.
type IngestStats struct {
	BudgetBytes       int64            `json:"budget_bytes"`
	BudgetSource      string           `json:"budget_source"`
	MemoryLimitBytes  int64            `json:"memory_limit_bytes"`
	MaxBodyBytes      int64            `json:"max_body_bytes"`
	MaxGRPCRecvBytes  int64            `json:"max_grpc_recv_bytes"`
	MaxWaitMillis     int64            `json:"max_wait_ms"`        // longest wait of a request for its leases
	InsertConcurrency int              `json:"insert_concurrency"` // ClickHouse inserts that may run at once
	InsertsInFlight   int32            `json:"inserts_in_flight"`  // drains under way now
	Wire              PoolStats        `json:"wire"`
	Work              PoolStats        `json:"work"`
	PendingLeased     int64            `json:"pending_leased_bytes"`
	LiveHeapBytes     int64            `json:"live_heap_bytes"`
	Rejected          map[string]int64 `json:"rejected"` // by transport/reason/code
	// ConnectionsClosed counts connections the listeners closed: past the
	// connection cap (grpc_over_cap, http_over_cap), and gRPC connections
	// that sent too many small DATA frames (grpc_small_frames) or had too
	// many calls refused before they were accepted (grpc_refused_calls).
	ConnectionsClosed map[string]int64 `json:"connections_closed"`
}

// PoolStats describes one memPool.
type PoolStats struct {
	SizeBytes  int64 `json:"size_bytes"`
	InUseBytes int64 `json:"in_use_bytes"`
	PeakBytes  int64 `json:"peak_bytes"`
	Waits      int64 `json:"waits"`
	WaitMillis int64 `json:"wait_ms"`
	Timeouts   int64 `json:"timeouts"`
}

func poolStats(p *memPool) PoolStats {
	return PoolStats{SizeBytes: p.size, InUseBytes: p.inUse.Load(), PeakBytes: p.peak.Load(),
		Waits: p.waits.Load(), WaitMillis: p.waitNanos.Load() / int64(time.Millisecond), Timeouts: p.timeouts.Load()}
}

// IngestStats reports the ingest budget, its use, and what was refused.
func (s *Server) IngestStats() IngestStats {
	st := IngestStats{
		BudgetBytes: s.limits.budget, BudgetSource: s.limits.source, MemoryLimitBytes: s.limits.memLimit,
		MaxBodyBytes: s.limits.maxBody, MaxGRPCRecvBytes: s.limits.maxRecv,
		MaxWaitMillis: s.limits.maxWait.Milliseconds(), InsertConcurrency: s.limits.inserts,
		InsertsInFlight: s.draining.Load(),
		Wire:            poolStats(s.wire), Work: poolStats(s.work), LiveHeapBytes: s.heap.live.Load(),
		Rejected: map[string]int64{},
		ConnectionsClosed: map[string]int64{
			"grpc_over_cap": s.grpcConnsRefused.Load(), "http_over_cap": s.httpConnsRefused.Load(),
			"grpc_small_frames": s.smallFrameConns.Load(), "grpc_refused_calls": s.tapRefusalConns.Load(),
		},
	}
	s.pendMu.Lock()
	st.PendingLeased = s.pendLeased
	s.pendMu.Unlock()
	s.stats.mu.Lock()
	for k, v := range s.stats.rejected {
		st.Rejected[k] = v
	}
	s.stats.mu.Unlock()
	return st
}

// logLimits logs the resolved limits once, at startup.
func (s *Server) logLimits() {
	l := s.limits
	s.log.Info("ingest limits",
		"budget_mib", l.budget>>20, "source", l.source, "memory_limit_mib", l.memLimit>>20,
		"wire_mib", l.wire>>20, "work_mib", l.work>>20,
		"max_body_mib", float64(l.maxBody)/(1<<20), "grpc_max_recv_mib", float64(l.maxRecv)/(1<<20),
		"insert_mib", float64(l.insertMax)/(1<<20), "insert_concurrency", l.inserts,
		"max_wait", l.maxWait.String(), "max_connections", l.maxConns)
	notes := append([]string(nil), l.notes...)
	sort.Strings(notes)
	for _, n := range notes {
		s.log.Warn("ingest limits: " + n)
	}
}
