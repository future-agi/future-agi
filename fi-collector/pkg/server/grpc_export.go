package server

import (
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"sync"
	"sync/atomic"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"golang.org/x/sync/semaphore"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/experimental"
	"google.golang.org/grpc/keepalive"
	"google.golang.org/grpc/mem"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/stats"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/tap"
)

// OTLP/gRPC trace export, registered by hand so that nothing of a call's
// message is read before the call is authenticated, and no more than its
// length prefix before it has its wire lease.
//
// grpc-go reads and decodes a unary call's whole message before any
// interceptor or handler runs. Export is therefore registered as a stream
// method: grpc-go hands the handler the call before reading its message, and
// the handler authenticates, checks the quota, reads the message's length
// prefix, leases the wire pool for that length (recvGRPCMessage) and only
// then reads the one message, as raw bytes, to scan and price it before
// pdata decodes it. On the wire a unary call is a stream of one
// message each way, so stock OTLP exporters are unaffected.
//
// Before a call is even accepted, a tap handle refuses calls that carry no
// credentials and takes an in-flight slot: from grpcStreams when the call's
// key is in the auth cache, from the smaller grpcPending otherwise, moving to
// grpcStreams once the handler has authenticated it. Each stream's
// flow-control window (grpcStreamWindow) bounds what the transport buffers
// for it before the handler reads, and frameGuardConn bounds what that
// costs per byte.

// exportMethod is the full name of the one method this server serves.
const exportMethod = "/opentelemetry.proto.collector.trace.v1.TraceService/Export"

var traceServiceDesc = grpc.ServiceDesc{
	ServiceName: "opentelemetry.proto.collector.trace.v1.TraceService",
	HandlerType: (*any)(nil),
	Streams: []grpc.StreamDesc{{
		StreamName:    "Export",
		Handler:       exportStreamHandler,
		ClientStreams: true,
	}},
	Metadata: "opentelemetry/proto/collector/trace/v1/trace_service.proto",
}

func exportStreamHandler(srv any, stream grpc.ServerStream) error {
	return srv.(*otlpHandler).exportStream(stream)
}

// grpcServerOptions are the options of the OTLP/gRPC server.
func (s *Server) grpcServerOptions() []grpc.ServerOption {
	return []grpc.ServerOption{
		// grpc-go refuses a message over the cap from its length prefix,
		// before reading it; exportStream answers that refusal itself.
		grpc.MaxRecvMsgSize(int(s.limits.maxRecv)),
		grpc.StatsHandler(&grpcErrLogger{log: s.log}),
		grpc.StatsHandler(grpcConnTracker{s: s}),
		// Raw bytes for the export, which the handler scans and prices
		// before decoding; the nesting limit for any trace request decoded
		// by the codec itself.
		grpc.ForceServerCodecV2(newNestingCheckedCodec()),
		grpc.InTapHandle(s.grpcTap),
		grpc.MaxConcurrentStreams(grpcMaxStreamsPerConn),
		// A fixed stream window (which also turns off grpc-go's BDP window
		// growth, up to 16 MiB per stream) bounds what a stream buffers
		// before its handler reads it.
		grpc.InitialWindowSize(grpcStreamWindow),
		grpc.InitialConnWindowSize(grpcConnWindow),
		grpc.MaxHeaderListSize(maxHeaderBytes),
		// Frame buffers of exactly the frame's size (exactBufferPool).
		experimental.BufferPool(exactBufferPool{}),
		// Release each connection's write buffer once it is flushed: idle
		// connections then hold no write buffer.
		grpc.SharedWriteBuffer(true),
		grpc.ConnectionTimeout(s.grpcHandshake),
		grpc.KeepaliveParams(keepalive.ServerParameters{
			MaxConnectionIdle:     s.grpcIdle,
			MaxConnectionAge:      s.grpcAge,
			MaxConnectionAgeGrace: grpcConnAgeGrace,
		}),
		grpc.KeepaliveEnforcementPolicy(keepalive.EnforcementPolicy{
			MinTime:             grpcMinPingInterval,
			PermitWithoutStream: true,
		}),
	}
}

// exactBufferPool gives grpc-go buffers of exactly the size it asks for.
// grpc-go's default pool rounds a buffer up to its next tier (256 B, 4 KiB,
// 16 KiB, ...), so a DATA frame just over a tier could cost up to four times
// its size. Full-size frames (16 KiB, what gRPC clients send) are pooled.
type exactBufferPool struct{}

const http2MaxFrame = 16 << 10

var frameBuffers = sync.Pool{New: func() any { b := make([]byte, http2MaxFrame); return &b }}

func (exactBufferPool) Get(n int) *[]byte {
	if n == http2MaxFrame {
		b := frameBuffers.Get().(*[]byte)
		*b = (*b)[:n]
		return b
	}
	b := make([]byte, n)
	return &b
}

func (exactBufferPool) Put(b *[]byte) {
	if cap(*b) == http2MaxFrame {
		frameBuffers.Put(b)
	}
}

// grpcSlot is a call's in-flight slot, in grpcPending until the call is
// authenticated, then in grpcStreams.
type grpcSlot struct {
	mu   sync.Mutex
	pool *semaphore.Weighted // nil once released
}

type grpcSlotKey struct{}
type grpcBusyKey struct{}

func (sl *grpcSlot) release() {
	sl.mu.Lock()
	defer sl.mu.Unlock()
	if sl.pool != nil {
		sl.pool.Release(1)
		sl.pool = nil
	}
}

// moveTo moves the slot to pool, if it is not there already and pool has
// room. It reports whether the slot is in pool.
func (sl *grpcSlot) moveTo(pool *semaphore.Weighted) bool {
	sl.mu.Lock()
	defer sl.mu.Unlock()
	switch {
	case sl.pool == pool:
		return true
	case sl.pool == nil || !pool.TryAcquire(1):
		return false
	}
	sl.pool.Release(1)
	sl.pool = pool
	return true
}

// grpcTap runs as a call arrives, before it is accepted. It must not block:
// it only looks at the metadata and the auth cache, and takes an in-flight
// slot, given back when the call ends.
func (s *Server) grpcTap(ctx context.Context, info *tap.Info) (context.Context, error) {
	if s.requireCreds && !auth.HasGRPCCredentials(info.Header) {
		s.tapRefused(ctx)
		r := &rejection{reason: "no_credentials"}
		if ok, held := s.countRejection("grpc", codes.Unauthenticated.String(), r); ok {
			s.logRejection("grpc", codes.Unauthenticated.String(), r, held)
		}
		return nil, status.Error(codes.Unauthenticated, "missing X-Api-Key or X-Secret-Key")
	}
	pool, rej := s.grpcStreams, rejectStreams
	if s.requireCreds && !s.grpcKnown(info.Header) {
		pool, rej = s.grpcPending, rejectUnverified
	}
	if !pool.TryAcquire(1) {
		if pool == s.grpcStreams {
			// Known clients receive a handler-level refusal. The handler
			// returns without reading the body and completes this call's
			// lifecycle without retiring a busy client's connection.
			return context.WithValue(ctx, grpcBusyKey{}, rej), nil
		}
		s.tapRefused(ctx)
		return nil, s.rejectGRPC(rej)
	}
	slot := &grpcSlot{pool: pool}
	context.AfterFunc(ctx, slot.release)
	return context.WithValue(ctx, grpcSlotKey{}, slot), nil
}

// rawExport is an ExportTraceServiceRequest as received: the codec copies the
// message into it without decoding.
type rawExport struct{ b []byte }

// exportStream serves one Export call.
func (h *otlpHandler) exportStream(stream grpc.ServerStream) error {
	s := h.s
	if rej, ok := stream.Context().Value(grpcBusyKey{}).(*rejection); ok {
		return s.rejectGRPC(rej)
	}
	requestCtx, cancel := ingestContext(stream.Context())
	defer cancel()
	ctx, err := s.grpcAuth(requestCtx)
	if err != nil {
		code := status.Code(err)
		s.countRejection("grpc", code.String(), &rejection{reason: "auth"})
		return err
	}
	// An authenticated call moves to the authenticated calls' slots.
	if sl, ok := ctx.Value(grpcSlotKey{}).(*grpcSlot); ok && !sl.moveTo(s.grpcStreams) {
		return s.rejectGRPC(rejectStreams)
	}
	if check, ok := s.checkUsage(ctx); !ok {
		return s.rejectGRPC(quotaRejection(check.Reason))
	}
	if s.heap.overLimit(time.Now()) {
		return s.rejectGRPC(rejectHeap)
	}
	// One wait budget for both leases (withLeaseBudget).
	ctx = withLeaseBudget(ctx, s.limits.maxWait)
	raw, wire, err := s.recvGRPCMessage(ctx, stream)
	if err != nil {
		return err
	}
	defer wire.release()

	md, _ := metadata.FromIncomingContext(ctx)
	held, rej := s.leaseWork(ctx, raw, false, auth.ProjectNameMetadataValues(md))
	if rej != nil {
		return s.rejectGRPC(rej)
	}
	defer held.work.release()
	resp, enqueued, err := h.exportDecoded(ctx, func() (ptraceotlp.ExportRequest, error) {
		req := ptraceotlp.NewExportRequest()
		if err := req.UnmarshalProto(raw); err != nil {
			return req, fmt.Errorf("decode protobuf: %w", err)
		}
		return req, nil
	}, held)
	s.settleRows(held.rows, enqueued)
	if err != nil {
		return err
	}
	out, err := resp.MarshalProto()
	if err != nil {
		return status.Error(codes.Internal, "encode response: "+err.Error())
	}
	return stream.SendMsg(&rawExport{b: out})
}

var errRecvTimeout = errors.New("request message not received in time")

// transportMsgReader is how grpc-go's server transport stream (the value of
// grpc.ServerTransportStreamFromContext) reads a message: its 5-byte length
// prefix, then its bytes. It is not part of grpc-go's API; when the stream
// does not offer it, recvGRPCMessage leases for a message at the receive cap
// and reads it with RecvMsg instead (TestGRPCLeasesTheMessageItReads fails
// if a grpc-go upgrade loses it).
type transportMsgReader interface {
	ReadHeader(header []byte) error
	Read(n int) (mem.BufferSlice, error)
}

// recvGRPCMessage reads the call's one message within the read timeout,
// under a wire lease it returns, shrunk to the message. It reads the length
// prefix first and leases for that length (grpcWireLease), so a call leases
// what its message needs, not the receive cap; a message over the cap is
// refused from its prefix, before it is read. Errors are the call's status.
func (s *Server) recvGRPCMessage(ctx context.Context, stream grpc.ServerStream) ([]byte, *lease, error) {
	deadline := time.Now().Add(s.readTimeout)
	tr, ok := grpc.ServerTransportStreamFromContext(stream.Context()).(transportMsgReader)
	if !ok || s.recvWithRecvMsg {
		return s.recvGRPCMessageAtCap(ctx, stream, deadline)
	}
	var hdr [5]byte
	if err := readWithin(deadline, func() error { return tr.ReadHeader(hdr[:]) }, nil); err != nil {
		return nil, nil, s.recvError(err)
	}
	n := int64(binary.BigEndian.Uint32(hdr[1:]))
	switch {
	case hdr[0] != 0:
		// No gRPC decompressor is registered: a call with a grpc-encoding
		// is refused before its handler runs, so a compressed message here
		// is malformed.
		return nil, nil, s.rejectGRPC(&rejection{reason: "malformed", msg: "compressed message without a grpc-encoding"})
	case n > s.limits.maxRecv:
		return nil, nil, s.rejectGRPC(s.rejectRecvTooLarge())
	}
	wire, err := s.wire.acquire(ctx, grpcWireLease(n))
	if err != nil {
		return nil, nil, s.rejectGRPC(rejectWireBusy)
	}
	var data mem.BufferSlice
	err = readWithin(deadline, func() (err error) { data, err = tr.Read(int(n)); return err }, wire.release)
	if err != nil {
		if !errors.Is(err, errRecvTimeout) {
			wire.release()
		}
		return nil, nil, s.recvError(err)
	}
	raw := data.Materialize()
	data.Free()
	wire.shrink(int64(len(raw)))
	return raw, wire, nil
}

// recvGRPCMessageAtCap is recvGRPCMessage for a transport stream that does
// not offer transportMsgReader: grpc-go does not report a message's size
// before RecvMsg reads it, so the lease is for a message at the receive cap.
func (s *Server) recvGRPCMessageAtCap(ctx context.Context, stream grpc.ServerStream, deadline time.Time) ([]byte, *lease, error) {
	wire, err := s.wire.acquire(ctx, grpcWireLease(s.limits.maxRecv))
	if err != nil {
		return nil, nil, s.rejectGRPC(rejectWireBusy)
	}
	var msg rawExport
	err = readWithin(deadline, func() error { return stream.RecvMsg(&msg) }, wire.release)
	if err != nil {
		if !errors.Is(err, errRecvTimeout) {
			wire.release()
		}
		if status.Code(err) == codes.ResourceExhausted {
			// Over the receive cap: grpc-go refused it from the length
			// prefix. Some exporters (Python, Java) retry RESOURCE_EXHAUSTED
			// without RetryInfo; answer this permanent refusal as INVALID_ARGUMENT.
			return nil, nil, s.rejectGRPC(s.rejectRecvTooLarge())
		}
		return nil, nil, s.recvError(err)
	}
	wire.shrink(int64(len(msg.b))) // the frames were freed when the codec copied them
	return msg.b, wire, nil
}

// recvError is the status for a failed message read.
func (s *Server) recvError(err error) error {
	switch {
	case errors.Is(err, errRecvTimeout):
		return s.rejectGRPC(rejectReadTimeout)
	case errors.Is(err, io.EOF), errors.Is(err, io.ErrUnexpectedEOF):
		return s.rejectGRPC(&rejection{reason: "malformed", msg: "no complete request message"})
	}
	if _, ok := status.FromError(err); ok {
		return err
	}
	return status.Error(codes.Internal, err.Error())
}

// readWithin runs read, which blocks on the call's stream, and waits for it
// until deadline. On a timeout it returns errRecvTimeout at once and runs
// after (when set) once read has returned: the read stops when the call
// ends, which the handler returning does.
func readWithin(deadline time.Time, read func() error, after func()) error {
	done := make(chan error, 1)
	go func() { done <- read() }()
	timer := time.NewTimer(time.Until(deadline))
	defer timer.Stop()
	select {
	case err := <-done:
		return err
	case <-timer.C:
		if after != nil {
			go func() { <-done; after() }()
		}
		return errRecvTimeout
	}
}

// grpcConnTracker gives each connection a context cancelled when it ends.
// It retires connections after grpcMaxTapRefusals unverified/missing-credential
// tap refusals. Known clients' capacity and quota refusals end in the handler
// instead, without closing their connections or interrupting accepted calls.
const grpcMaxTapRefusals = 256

// grpcConn is a connection's state, in the context of its calls.
type grpcConn struct {
	conn    net.Conn // nil when the listener did not register it
	refused atomic.Int64
	cancel  context.CancelFunc
}

type grpcConnKey struct{}

// grpcConnTracker is a stats.Handler for the connection events only.
type grpcConnTracker struct{ s *Server }

func (h grpcConnTracker) TagConn(ctx context.Context, info *stats.ConnTagInfo) context.Context {
	gc := &grpcConn{}
	if info.RemoteAddr != nil {
		if c, ok := h.s.grpcConns.Load(info.RemoteAddr.String()); ok {
			gc.conn = c.(net.Conn)
		}
	}
	ctx, gc.cancel = context.WithCancel(ctx)
	return context.WithValue(ctx, grpcConnKey{}, gc)
}

func (h grpcConnTracker) HandleConn(ctx context.Context, cs stats.ConnStats) {
	if _, ok := cs.(*stats.ConnEnd); ok {
		if gc, ok := ctx.Value(grpcConnKey{}).(*grpcConn); ok {
			gc.cancel()
		}
	}
}

func (grpcConnTracker) TagRPC(ctx context.Context, _ *stats.RPCTagInfo) context.Context { return ctx }
func (grpcConnTracker) HandleRPC(context.Context, stats.RPCStats)                       {}

// tapRefused counts a call the tap handle refused against its connection,
// and closes the connection at grpcMaxTapRefusals.
func (s *Server) tapRefused(ctx context.Context) {
	gc, ok := ctx.Value(grpcConnKey{}).(*grpcConn)
	if !ok || gc.refused.Add(1) != grpcMaxTapRefusals || gc.conn == nil {
		return
	}
	s.tapRefusalConns.Add(1)
	_ = gc.conn.Close()
}
