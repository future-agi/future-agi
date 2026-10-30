package ingestbench

import (
	"bytes"
	"compress/gzip"
	"context"
	"io"
	"net"
	"net/http"
	"sync"
	"testing"
	"time"

	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/stats"
)

// spanCountingTraceServer is an OTLP/gRPC trace service that records the spans
// of every request it decodes.
type spanCountingTraceServer struct {
	ptraceotlp.UnimplementedGRPCServer
	mu    sync.Mutex
	spans []int
}

func (s *spanCountingTraceServer) Export(_ context.Context, req ptraceotlp.ExportRequest) (ptraceotlp.ExportResponse, error) {
	s.mu.Lock()
	s.spans = append(s.spans, req.Traces().SpanCount())
	s.mu.Unlock()
	return ptraceotlp.NewExportResponse(), nil
}

// inPayloadRecorder records the size of every message a gRPC server received.
type inPayloadRecorder struct {
	mu       sync.Mutex
	payloads []stats.InPayload
}

func (r *inPayloadRecorder) TagRPC(ctx context.Context, _ *stats.RPCTagInfo) context.Context {
	return ctx
}

func (r *inPayloadRecorder) HandleRPC(_ context.Context, s stats.RPCStats) {
	if in, ok := s.(*stats.InPayload); ok && !in.Client {
		r.mu.Lock()
		r.payloads = append(r.payloads, stats.InPayload{Length: in.Length, CompressedLength: in.CompressedLength, WireLength: in.WireLength})
		r.mu.Unlock()
	}
}

func (r *inPayloadRecorder) TagConn(ctx context.Context, _ *stats.ConnTagInfo) context.Context {
	return ctx
}

func (r *inPayloadRecorder) HandleConn(context.Context, stats.ConnStats) {}

func gzipLevel(t *testing.T, raw []byte, level int) []byte {
	t.Helper()
	var buf bytes.Buffer
	zw, err := gzip.NewWriterLevel(&buf, level)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := zw.Write(raw); err != nil {
		t.Fatal(err)
	}
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

// F2: a gRPC request is encoded and compressed by the build workers, like an
// HTTP body, and the gRPC exporter sends exactly those bytes. Marshalling or
// compressing inside the call would put generator CPU time into the timed send
// (and into a concurrency slot) on this transport only.
func TestGRPCExportSendsThePrebuiltMessageBytes(t *testing.T) {
	const spans = 200
	recorder := &inPayloadRecorder{}
	traces := &spanCountingTraceServer{}
	srv := grpc.NewServer(grpc.StatsHandler(recorder), grpc.RPCDecompressor(grpc.NewGZIPDecompressor()))
	ptraceotlp.RegisterGRPCServer(srv, traces)
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	go func() { _ = srv.Serve(lis) }()
	defer srv.Stop()

	for _, compress := range []bool{false, true} {
		c := testConfig()
		c.Encoding, c.Gzip, c.BatchSize = EncodingGRPC, compress, spans
		c.Endpoint = "http://" + lis.Addr().String()
		built, _, err := c.buildRequest(0, spans, time.Unix(1700000000, 0))
		if err != nil {
			t.Fatal(err)
		}
		raw := built.body
		if compress {
			zr, err := gzip.NewReader(bytes.NewReader(built.body))
			if err != nil {
				t.Fatalf("a gRPC+gzip request is not gzip-compressed by the build: %v", err)
			}
			if raw, err = io.ReadAll(zr); err != nil {
				t.Fatal(err)
			}
		}
		decoded := ptraceotlp.NewExportRequest()
		if len(raw) == 0 || len(raw) != built.uncompressed || decoded.UnmarshalProto(raw) != nil || decoded.Traces().SpanCount() != spans {
			t.Fatalf("gzip=%t: the built gRPC request is not the encoded %d-span message (%d of %d bytes)", compress, spans, len(raw), built.uncompressed)
		}
		send := built
		if compress {
			// A level grpc-go's own gzip never uses: if the exporter re-encoded
			// or re-compressed the message, the received size would differ.
			send.body = gzipLevel(t, raw, gzip.BestSpeed)
			if len(send.body) == len(gzipLevel(t, raw, gzip.DefaultCompression)) {
				t.Fatal("test cannot tell the compression levels apart")
			}
		}
		exp, err := newGRPCExporter(c, http.Header{})
		if err != nil {
			t.Fatal(err)
		}
		o, wire := exp.export(context.Background(), send, spans)
		exp.close()
		if o.class != outcomeAcknowledged || o.statusLabel != "grpc:OK" {
			t.Fatalf("gzip=%t: outcome %+v", compress, o)
		}
		recorder.mu.Lock()
		got := recorder.payloads[len(recorder.payloads)-1]
		recorder.mu.Unlock()
		if got.CompressedLength != len(send.body) || got.WireLength != len(send.body)+5 || got.Length != len(raw) || wire != len(send.body)+5 {
			t.Fatalf("gzip=%t: server received %+v, client counted %d wire bytes; want exactly the %d pre-built bytes (+5 framing) decoding to %d", compress, got, wire, len(send.body), len(raw))
		}
		traces.mu.Lock()
		received := traces.spans[len(traces.spans)-1]
		traces.mu.Unlock()
		if received != spans {
			t.Fatalf("gzip=%t: server decoded %d spans, want %d", compress, received, spans)
		}
	}
}
