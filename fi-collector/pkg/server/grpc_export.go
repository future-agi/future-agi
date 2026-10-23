package server

import (
	"context"
	"errors"
	"io"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"golang.org/x/sync/semaphore"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/tap"
)

// OTLP/gRPC trace export, registered by hand so that nothing of a call's
// message is read before the call is authenticated and has its wire lease.
//
// grpc-go reads and decodes a unary call's whole message before any
// interceptor or handler runs. Export is therefore registered as a stream
// method: grpc-go hands the handler the call before reading its message, and
// the handler authenticates, checks the quota, leases the wire pool and only
// then reads the one message, as raw bytes (rawExport), to scan and price it
// before pdata decodes it. On the wire a unary call is a stream of one
// message each way, so stock OTLP exporters are unaffected.
//
// Before a call is even accepted, a tap handle refuses calls that carry no
// credentials and calls past the in-flight limit (grpcMaxStreams); each
// stream's flow-control window (grpcStreamWindow) bounds what the transport
// buffers for it before the handler reads.

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
		grpc.MaxRecvMsgSize(int(s.limits.maxRecv)),
		grpc.StatsHandler(&grpcErrLogger{log: s.log}),
		// Raw bytes for the export, which the handler scans and prices
		// before decoding; the nesting limit for any trace request decoded
		// by the codec itself.
		grpc.ForceServerCodecV2(newNestingCheckedCodec()),
		grpc.InTapHandle(s.grpcTap),
		grpc.MaxConcurrentStreams(grpcMaxStreamsPerConn),
		// Fixed windows (which also turns off grpc-go's BDP window growth,
		// up to 16 MiB per stream) bound what a stream buffers before its
		// handler reads it.
		grpc.InitialWindowSize(grpcStreamWindow),
		grpc.InitialConnWindowSize(grpcConnWindow),
		grpc.MaxHeaderListSize(maxHeaderBytes),
	}
}

// grpcTap runs as a call arrives, before it is accepted. It must not block:
// it only looks at the metadata and takes an in-flight slot, given back when
// the call ends.
func (s *Server) grpcTap(ctx context.Context, info *tap.Info) (context.Context, error) {
	if s.requireCreds && !auth.HasGRPCCredentials(info.Header) {
		s.countRejection("grpc", codes.Unauthenticated.String(), &rejection{reason: "no_credentials"})
		return nil, status.Error(codes.Unauthenticated, "missing X-Api-Key or X-Secret-Key")
	}
	if !s.grpcStreams.TryAcquire(1) {
		return nil, s.rejectGRPC(rejectStreams)
	}
	context.AfterFunc(ctx, func() { s.grpcStreams.Release(1) })
	return ctx, nil
}

func newStreamLimit() *semaphore.Weighted { return semaphore.NewWeighted(grpcMaxStreams) }

// rawExport is an ExportTraceServiceRequest as received: the codec copies the
// message into it without decoding.
type rawExport struct{ b []byte }

// exportStream serves one Export call.
func (h *otlpHandler) exportStream(stream grpc.ServerStream) error {
	s := h.s
	ctx, err := s.grpcAuth(stream.Context())
	if err != nil {
		return err
	}
	if check, ok := s.checkUsage(ctx); !ok {
		return status.Errorf(codes.ResourceExhausted, "quota exceeded: %s", check.Reason)
	}
	if s.heap.overLimit(time.Now()) {
		return s.rejectGRPC(rejectHeap)
	}
	// The frames of the message and the copy the codec joins them into are
	// live together, so the wire lease is twice the receive cap until the
	// message is in.
	wire, err := s.wire.acquire(ctx, 2*s.limits.maxRecv)
	if err != nil {
		return s.rejectGRPC(rejectWireBusy)
	}
	raw, err := recvExport(stream, wire, s.readTimeout)
	switch {
	case errors.Is(err, errRecvTimeout):
		return s.rejectGRPC(rejectReadTimeout)
	case errors.Is(err, io.EOF):
		return s.rejectGRPC(&rejection{reason: "malformed", msg: "no request message"})
	case status.Code(err) == codes.ResourceExhausted:
		// Over the receive cap: grpc-go refused it, grpcErrLogger logs it.
		s.countRejection("grpc", codes.ResourceExhausted.String(), &rejection{reason: "body_size"})
		return err
	case err != nil:
		return err
	}
	defer wire.release()
	wire.shrink(int64(len(raw)))

	work, rows, rej := s.leaseWork(ctx, raw, false)
	if rej != nil {
		return s.rejectGRPC(rej)
	}
	defer work.release()
	req := ptraceotlp.NewExportRequest()
	if err := req.UnmarshalProto(raw); err != nil {
		rows.release()
		return s.rejectGRPC(&rejection{reason: "malformed", msg: "decode protobuf: " + err.Error()})
	}
	resp, enqueued, err := h.export(ctx, req)
	s.settleRows(rows, enqueued)
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

// recvExport reads the call's one message within timeout. On an error the
// wire lease is released: on a timeout only once the read has stopped, which
// it does when the call ends (the handler returning ends it).
func recvExport(stream grpc.ServerStream, wire *lease, timeout time.Duration) ([]byte, error) {
	var msg rawExport
	done := make(chan error, 1)
	go func() { done <- stream.RecvMsg(&msg) }()
	timer := time.NewTimer(timeout)
	defer timer.Stop()
	select {
	case err := <-done:
		if err != nil {
			wire.release()
			return nil, err
		}
		return msg.b, nil
	case <-timer.C:
		go func() { <-done; wire.release() }()
		return nil, errRecvTimeout
	}
}
