package server

import (
	"context"
	"log/slog"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

// Tests for OTLP/gRPC Export (grpc_export.go): the call is authenticated,
// checked and leased before its message is read, and its message is scanned
// and priced before it is decoded.

// startGRPCServer runs a gRPC-only server on a stub ClickHouse; prepare can
// change it before it serves.
func startGRPCServer(t *testing.T, cfg Config, prepare func(*Server)) (*Server, *grpc.ClientConn, *chBodies) {
	t.Helper()
	ch, w := newCHBodies(t)
	addr := testTCPAddr(t)
	cfg.GRPCAddr = addr
	if cfg.BatchMaxRows == 0 {
		cfg.BatchMaxRows = 1 << 30
	}
	if cfg.BatchMaxAge == 0 {
		cfg.BatchMaxAge = time.Hour
	}
	s := New(cfg, w, nil, nil, nil)
	s.cfg.HTTPAddr = ""
	if prepare != nil {
		prepare(s)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { _ = s.Run(ctx); close(done) }()
	t.Cleanup(func() { cancel(); <-done })
	if !waitPort(addr, 2*time.Second) {
		t.Fatalf("server didn't listen on %s", addr)
	}
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithDefaultCallOptions(grpc.MaxCallSendMsgSize(64<<20)))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	return s, conn, ch
}

func exportRaw(conn *grpc.ClientConn, ctx context.Context, body []byte) error {
	var resp []byte
	return conn.Invoke(ctx, exportMethod, &body, &resp, grpc.ForceCodec(rawProtoCodec{}))
}

func withKeys(ctx context.Context) context.Context {
	return metadata.AppendToOutgoingContext(ctx, "x-api-key", "k", "x-secret-key", "s")
}

// The call is authenticated before its message is read: while auth is still
// deciding, nothing of the call is leased, and a refused call never leases or
// reads its message. (Without this, the auth interceptor ran only after
// grpc-go had read and decoded the whole message.)
func TestGRPCAuthRunsBeforeTheMessageIsRead(t *testing.T) {
	deciding := make(chan struct{})
	decide := make(chan struct{})
	var wireAtAuth atomic.Int64
	s, conn, _ := startGRPCServer(t, Config{}, func(s *Server) {
		s.requireCreds = true
		s.grpcAuth = func(ctx context.Context) (context.Context, error) {
			wireAtAuth.Store(s.wire.inUse.Load() + 1)
			close(deciding)
			<-decide
			return nil, status.Error(codes.Unauthenticated, "authentication failed")
		}
	})
	body := emptySpans(t, 4<<20) // 8 MiB of empty spans
	errc := make(chan error, 1)
	go func() { errc <- exportRaw(conn, withKeys(context.Background()), body) }()
	<-deciding
	time.Sleep(100 * time.Millisecond) // let the client send what flow control allows
	if w := s.wire.inUse.Load(); w != 0 {
		t.Errorf("%d wire bytes leased while auth decides, want 0", w)
	}
	close(decide)
	if err := <-errc; status.Code(err) != codes.Unauthenticated {
		t.Fatalf("Export: %v, want UNAUTHENTICATED", err)
	}
	if got := wireAtAuth.Load() - 1; got != 0 {
		t.Errorf("%d wire bytes leased when auth ran, want 0", got)
	}
	if w, k := s.wire.peak.Load(), s.work.peak.Load(); w != 0 || k != 0 {
		t.Errorf("a refused call leased wire %d and work %d bytes, want none", w, k)
	}
}

// The server's own authenticator is the one that runs before the message is
// read: a call whose key cannot be checked (Postgres refuses connections) is
// UNAVAILABLE, and nothing of it was leased or read.
func TestGRPCServerAuthenticatorRunsBeforeTheMessageIsRead(t *testing.T) {
	pg := "postgres://u:p@127.0.0.1:1/x?connect_timeout=1"
	authn, err := auth.New(context.Background(), auth.Config{PGWrite: pg, PGRead: pg}, nil, slog.Default())
	if err != nil || authn == nil {
		t.Fatalf("auth.New: %v", err)
	}
	ch, w := newCHBodies(t)
	addr := testTCPAddr(t)
	s := New(Config{GRPCAddr: addr, BatchMaxRows: 1 << 30, BatchMaxAge: time.Hour}, w, authn, nil, nil)
	s.cfg.HTTPAddr = ""
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { _ = s.Run(ctx); close(done) }()
	t.Cleanup(func() { cancel(); <-done })
	if !waitPort(addr, 2*time.Second) {
		t.Fatalf("server didn't listen on %s", addr)
	}
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithDefaultCallOptions(grpc.MaxCallSendMsgSize(64<<20)))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })

	err = exportRaw(conn, withKeys(context.Background()), emptySpans(t, 1<<20))
	if status.Code(err) != codes.Unavailable {
		t.Fatalf("Export with a key that cannot be checked: %v, want UNAVAILABLE", err)
	}
	if err := exportRaw(conn, context.Background(), emptySpans(t, 10)); status.Code(err) != codes.Unauthenticated {
		t.Fatalf("Export without keys: %v, want UNAUTHENTICATED", err)
	}
	if wp, kp := s.wire.peak.Load(), s.work.peak.Load(); wp != 0 || kp != 0 {
		t.Errorf("refused calls leased wire %d and work %d bytes, want none", wp, kp)
	}
	if n, _ := ch.lines("spans"); n != 0 {
		t.Errorf("refused calls stored %d bytes", n)
	}
}

// A call with no credentials is refused before it is accepted, by the tap
// handle: its handler never runs.
func TestGRPCCallWithoutCredentialsIsRefusedBeforeItIsAccepted(t *testing.T) {
	var handled atomic.Int32
	s, conn, _ := startGRPCServer(t, Config{}, func(s *Server) {
		s.requireCreds = true
		s.grpcAuth = func(ctx context.Context) (context.Context, error) {
			handled.Add(1)
			return ctx, nil
		}
	})
	err := exportRaw(conn, context.Background(), emptySpans(t, 10))
	if status.Code(err) != codes.Unauthenticated {
		t.Fatalf("Export without keys: %v, want UNAUTHENTICATED", err)
	}
	if handled.Load() != 0 {
		t.Error("the handler ran for a call with no credentials")
	}
	if got := s.IngestStats().Rejected["grpc/no_credentials/Unauthenticated"]; got != 1 {
		t.Errorf("no_credentials refusals counted %d, want 1", got)
	}
	if err := exportRaw(conn, withKeys(context.Background()), emptySpans(t, 10)); err != nil {
		t.Errorf("Export with keys: %v", err)
	}
}

// Calls past the in-flight limit are refused as UNAVAILABLE, which exporters
// retry, until a slot frees up.
func TestGRPCInFlightLimitIsUnavailable(t *testing.T) {
	s, conn, _ := startGRPCServer(t, Config{}, nil)
	if !s.grpcStreams.TryAcquire(grpcMaxStreams) {
		t.Fatal("could not take every in-flight slot")
	}
	err := exportRaw(conn, context.Background(), emptySpans(t, 10))
	if status.Code(err) != codes.Unavailable {
		t.Fatalf("Export past the in-flight limit: %v, want UNAVAILABLE", err)
	}
	s.grpcStreams.Release(grpcMaxStreams)
	if err := exportRaw(conn, context.Background(), emptySpans(t, 10)); err != nil {
		t.Fatalf("Export after the slots freed up: %v", err)
	}
	// Every slot is given back when its call ends.
	if !s.grpcStreams.TryAcquire(grpcMaxStreams) {
		t.Error("a finished call kept its in-flight slot")
	} else {
		s.grpcStreams.Release(grpcMaxStreams)
	}
}

// With auth off, every limit still applies: a call priced over the work pool
// is refused permanently before it is decoded, and a busy wire pool is
// UNAVAILABLE.
func TestGRPCLimitsApplyWithAuthOff(t *testing.T) {
	s, conn, _ := startGRPCServer(t, Config{IngestBudgetMiB: 96}, func(s *Server) { s.wire.wait = 100 * time.Millisecond })
	err := exportRaw(conn, context.Background(), emptySpans(t, 60000))
	if status.Code(err) != codes.InvalidArgument || !strings.Contains(err.Error(), "FI_INGEST_BUDGET_MIB") {
		t.Fatalf("over-priced Export: %v, want INVALID_ARGUMENT naming FI_INGEST_BUDGET_MIB", err)
	}
	if p := s.work.peak.Load(); p != 0 {
		t.Errorf("leased %d work bytes for a refused call", p)
	}
	release := holdPool(t, s.wire, s.wire.size)
	err = exportRaw(conn, context.Background(), emptySpans(t, 10))
	release()
	if status.Code(err) != codes.Unavailable {
		t.Fatalf("Export while the wire pool is full: %v, want UNAVAILABLE", err)
	}
	if err := exportRaw(conn, context.Background(), emptySpans(t, 10)); err != nil {
		t.Fatalf("Export after: %v", err)
	}
	if w := s.wire.inUse.Load(); w != 0 {
		t.Errorf("%d wire bytes leased after every call returned", w)
	}
}

// A call that opens and never sends its message holds its wire lease only
// for the read timeout: then it is DEADLINE_EXCEEDED and the lease is back.
func TestGRPCMessageNotSentInTimeIsDeadlineExceeded(t *testing.T) {
	s, conn, _ := startGRPCServer(t, Config{}, func(s *Server) { s.readTimeout = 300 * time.Millisecond })
	stream, err := conn.NewStream(context.Background(), &grpc.StreamDesc{ClientStreams: true}, exportMethod,
		grpc.ForceCodec(rawProtoCodec{}))
	if err != nil {
		t.Fatal(err)
	}
	var resp []byte
	start := time.Now()
	err = stream.RecvMsg(&resp) // the client never sends its message
	if status.Code(err) != codes.DeadlineExceeded {
		t.Fatalf("stalled call: %v, want DEADLINE_EXCEEDED", err)
	}
	if waited := time.Since(start); waited > 2*time.Second {
		t.Errorf("answered after %v, want about the %v read timeout", waited, s.readTimeout)
	}
	for deadline := time.Now().Add(2 * time.Second); s.wire.inUse.Load() != 0 && time.Now().Before(deadline); {
		time.Sleep(5 * time.Millisecond)
	}
	if w := s.wire.inUse.Load(); w != 0 {
		t.Errorf("%d wire bytes leased after the stalled call ended", w)
	}
}

// The rows' share of a call's price stays leased with the pending rows until
// the drain that stores them ends; the rest is back when the call returns.
func TestGRPCRowsKeepTheirLeaseUntilTheDrain(t *testing.T) {
	s, conn, ch := startGRPCServer(t, Config{}, nil)
	traces := makeTraces("lease", "51515151-5151-4151-8151-515151515151")
	body := protoBody(t, traces)
	shape, err := scanOTLPProto(body)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := ptraceotlp.NewGRPCClient(conn).Export(context.Background(), ptraceotlp.NewExportRequestFromTraces(traces)); err != nil {
		t.Fatalf("stock client Export: %v", err)
	}
	if w := s.wire.inUse.Load(); w != 0 {
		t.Errorf("%d wire bytes leased after the call", w)
	}
	if k := s.work.inUse.Load(); k != shape.price().rows {
		t.Errorf("%d work bytes leased with the rows pending, want their share %d", k, shape.price().rows)
	}
	s.drainNow(context.Background())
	if n, _ := ch.lines("spans"); n == 0 {
		t.Fatal("nothing stored")
	}
	if k := s.work.inUse.Load(); k != 0 {
		t.Errorf("%d work bytes leased after the drain", k)
	}
}

// Export is served at the method path stock OTLP exporters call, and nothing
// else is.
func TestGRPCServesOnlyTheExportMethod(t *testing.T) {
	_, conn, _ := startGRPCServer(t, Config{}, nil)
	var resp []byte
	body := emptySpans(t, 1)
	err := conn.Invoke(context.Background(), "/opentelemetry.proto.collector.trace.v1.TraceService/Export2", &body, &resp,
		grpc.ForceCodec(rawProtoCodec{}))
	if status.Code(err) != codes.Unimplemented {
		t.Errorf("another method: %v, want UNIMPLEMENTED", err)
	}
	td := ptrace.NewTraces()
	td.ResourceSpans().AppendEmpty().ScopeSpans().AppendEmpty().Spans().AppendEmpty().SetName("x")
	if _, err := ptraceotlp.NewGRPCClient(conn).Export(context.Background(), ptraceotlp.NewExportRequestFromTraces(td)); err != nil {
		t.Errorf("stock client Export at %s: %v", exportMethod, err)
	}
}

// A call leases what its message needs, from the message's length prefix,
// and not a message at the receive cap; when grpc-go's transport stream does
// not offer the two-step read, it leases for the cap. This test fails if a
// grpc-go upgrade loses the two-step read. (Before, every call leased twice
// the receive cap while its message arrived, whatever its size.)
func TestGRPCLeasesTheMessageItReads(t *testing.T) {
	body := tracesOfSize(t, "lease", 1<<20)
	for name, atCap := range map[string]bool{"from the length prefix": false, "with RecvMsg": true} {
		t.Run(name, func(t *testing.T) {
			s, conn, _ := startGRPCServer(t, Config{}, func(s *Server) { s.recvWithRecvMsg = atCap })
			if err := exportRaw(conn, context.Background(), body); err != nil {
				t.Fatalf("Export: %v", err)
			}
			want := grpcWireLease(int64(len(body)))
			if atCap {
				want = grpcWireLease(s.limits.maxRecv)
			}
			if p := s.wire.peak.Load(); p != want {
				t.Errorf("wire peak %d for a %d-byte message, want %d", p, len(body), want)
			}
			if w := s.wire.inUse.Load(); w != 0 {
				t.Errorf("%d wire bytes leased after the call", w)
			}
		})
	}
}

// A message flagged as compressed, on a call with no grpc-encoding, is
// refused permanently before it is read or leased.
func TestGRPCCompressedFlagWithoutEncodingIsInvalid(t *testing.T) {
	s, _, _ := startGRPCServer(t, Config{}, nil)
	h := dialH2Call(t, s.cfg.GRPCAddr, nil)
	_ = h.send(emptySpans(t, 10), true, 16<<10)
	_ = h.ended(2 * time.Second)
	if got := s.IngestStats().Rejected["grpc/malformed/InvalidArgument"]; got != 1 {
		t.Errorf("malformed refusals %d, want 1 (%v)", got, s.IngestStats().Rejected)
	}
	if p := s.wire.peak.Load(); p != 0 {
		t.Errorf("leased %d wire bytes for a refused message", p)
	}
}

// Calls whose key the auth cache knows as valid take their in-flight slot
// from the authenticated calls' limit, and calls still being authenticated
// from a separate, smaller one, so calls being authenticated cannot take
// every slot.
func TestGRPCCallsBeingAuthenticatedCannotHoldEverySlot(t *testing.T) {
	key := func(ctx context.Context) string {
		md, _ := metadata.FromIncomingContext(ctx)
		if v := md.Get("x-api-key"); len(v) > 0 {
			return v[0]
		}
		return ""
	}
	s, conn, _ := startGRPCServer(t, Config{}, func(s *Server) {
		s.requireCreds = true
		s.grpcKnown = func(md metadata.MD) bool { v := md.Get("x-api-key"); return len(v) > 0 && v[0] == "good" }
		s.grpcAuth = func(ctx context.Context) (context.Context, error) {
			if key(ctx) == "good" {
				return ctx, nil
			}
			time.Sleep(200 * time.Millisecond) // a key that takes long to refuse
			return nil, status.Error(codes.Unauthenticated, "authentication failed")
		}
	})
	junk := func(ctx context.Context) context.Context {
		return metadata.AppendToOutgoingContext(ctx, "x-api-key", "junk", "x-secret-key", "junk")
	}
	good := func(ctx context.Context) context.Context {
		return metadata.AppendToOutgoingContext(ctx, "x-api-key", "good", "x-secret-key", "good")
	}
	body := emptySpans(t, 10)
	ctx, cancel := context.WithCancel(context.Background())
	var wg sync.WaitGroup
	t.Cleanup(func() { cancel(); wg.Wait() })
	for c := 0; c < 4; c++ {
		jc := readyConn(t, s.cfg.GRPCAddr)
		for i := 0; i < grpcMaxStreamsPerConn; i++ { // more callers than there are slots
			wg.Add(1)
			go func() {
				defer wg.Done()
				for ctx.Err() == nil {
					_ = exportRaw(jc, junk(ctx), body)
				}
			}()
		}
	}
	time.Sleep(300 * time.Millisecond)
	for i := 0; i < 20; i++ {
		if err := exportRaw(conn, good(context.Background()), body); err != nil {
			t.Fatalf("call %d with a known key: %v", i, err)
		}
	}
	if got := s.IngestStats().Rejected["grpc/grpc_unverified/Unavailable"]; got == 0 {
		t.Error("no call being authenticated was refused past its limit")
	}
}
