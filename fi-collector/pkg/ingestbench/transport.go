package ingestbench

import (
	"bytes"
	"context"
	"crypto/tls"
	"fmt"
	"mime"
	"net"
	"net/http"
	"net/url"
	"strings"
	"sync/atomic"

	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/encoding/gzip"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/stats"
)

// exporter sends one export request and classifies the response. It never
// retries: a retry would make a duplicate indistinguishable from a writer
// duplicate and would hide the collector's admission behaviour.
type exporter interface {
	export(ctx context.Context, b encodedBatch, n int) (outcome exportOutcome, wireBytes int)
	close()
}

func (c Config) newExporter(client *http.Client, headers http.Header) (exporter, error) {
	if c.Encoding == EncodingGRPC {
		return newGRPCExporter(c, headers)
	}
	return &httpExporter{cfg: c, client: client, headers: headers}, nil
}

type httpExporter struct {
	cfg     Config
	client  *http.Client
	headers http.Header
}

func (e *httpExporter) close() {}

func (e *httpExporter) mediaType() string {
	if e.cfg.Encoding == EncodingJSON {
		return "application/json"
	}
	return "application/x-protobuf"
}

func (e *httpExporter) export(ctx context.Context, b encodedBatch, n int) (exportOutcome, int) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, e.cfg.Endpoint, bytes.NewReader(b.body))
	if err != nil {
		return exportOutcome{class: outcomeAmbiguous, statusLabel: "transport-error"}, 0
	}
	req.Header = e.headers.Clone()
	if req.Header == nil {
		req.Header = http.Header{}
	}
	req.Header.Set("Content-Type", e.mediaType())
	if e.cfg.Gzip {
		req.Header.Set("Content-Encoding", "gzip")
	}
	resp, err := e.client.Do(req)
	if err != nil {
		return exportOutcome{class: outcomeAmbiguous, statusLabel: "transport-error"}, len(b.body)
	}
	body, readErr := readBounded(resp.Body, 1<<20)
	resp.Body.Close()
	decodeOK, rejected, message := false, int64(0), false
	if resp.StatusCode == http.StatusOK && readErr == nil {
		got, _, _ := mime.ParseMediaType(resp.Header.Get("Content-Type"))
		// OTLP/HTTP: the response uses the request's media type.
		if got == e.mediaType() {
			otlp := ptraceotlp.NewExportResponse()
			if got == "application/json" {
				err = otlp.UnmarshalJSON(body)
			} else {
				err = otlp.UnmarshalProto(body)
			}
			if err == nil {
				decodeOK = true
				rejected = otlp.PartialSuccess().RejectedSpans()
				message = otlp.PartialSuccess().ErrorMessage() != ""
			}
		}
	}
	return classifyHTTP(resp.StatusCode, resp.Header, decodeOK, rejected, message, n), len(b.body)
}

// grpcExporter speaks OTLP/gRPC, the transport most production SDK exporters
// default to. Wire bytes come from the gRPC stats handler (after compression).
type grpcExporter struct {
	cfg    Config
	conn   *grpc.ClientConn
	client ptraceotlp.GRPCClient
	md     metadata.MD
}

type wireCounterKey struct{}

// wireStats records the compressed on-the-wire size of each outgoing message.
type wireStats struct{}

func (wireStats) TagRPC(ctx context.Context, _ *stats.RPCTagInfo) context.Context { return ctx }
func (wireStats) HandleRPC(ctx context.Context, s stats.RPCStats) {
	if out, ok := s.(*stats.OutPayload); ok {
		if counter, ok := ctx.Value(wireCounterKey{}).(*atomic.Int64); ok {
			counter.Add(int64(out.WireLength))
		}
	}
}
func (wireStats) TagConn(ctx context.Context, _ *stats.ConnTagInfo) context.Context { return ctx }
func (wireStats) HandleConn(context.Context, stats.ConnStats)                       {}

// grpcTarget maps an http(s) endpoint URL to a gRPC target. http means
// plaintext (only for loopback or an approved isolated network) and https TLS.
func grpcTarget(endpoint string) (target string, tlsServerName string, err error) {
	u, err := url.Parse(endpoint)
	if err != nil || (u.Path != "" && u.Path != "/") || u.RawQuery != "" {
		return "", "", fmt.Errorf("a gRPC endpoint is scheme://host:port with no path or query")
	}
	port := u.Port()
	if port == "" {
		return "", "", fmt.Errorf("a gRPC endpoint needs an explicit port")
	}
	if u.Scheme == "https" {
		tlsServerName = u.Hostname()
	}
	return net.JoinHostPort(u.Hostname(), port), tlsServerName, nil
}

func newGRPCExporter(c Config, headers http.Header) (*grpcExporter, error) {
	target, serverName, err := grpcTarget(c.Endpoint)
	if err != nil {
		return nil, err
	}
	creds := insecure.NewCredentials()
	if serverName != "" {
		creds = credentials.NewTLS(&tls.Config{ServerName: serverName, MinVersion: tls.VersionTLS12})
	}
	dialer := &net.Dialer{Timeout: c.Timeout}
	conn, err := grpc.NewClient("passthrough:///"+target,
		grpc.WithTransportCredentials(creds),
		// No proxy from the environment and no service-config retries: load and
		// credentials go only to the approved address, exactly once.
		grpc.WithNoProxy(),
		grpc.WithDisableRetry(),
		grpc.WithStatsHandler(wireStats{}),
		grpc.WithContextDialer(func(ctx context.Context, addr string) (net.Conn, error) {
			host, port, err := net.SplitHostPort(addr)
			if err != nil {
				return nil, fmt.Errorf("invalid target address")
			}
			if strings.EqualFold(host, "localhost") {
				addr = net.JoinHostPort("127.0.0.1", port)
			}
			return dialer.DialContext(ctx, "tcp", addr)
		}),
	)
	if err != nil {
		return nil, fmt.Errorf("create gRPC client: %w", err)
	}
	md := metadata.MD{}
	for k, values := range headers {
		md.Set(strings.ToLower(k), values...)
	}
	return &grpcExporter{cfg: c, conn: conn, client: ptraceotlp.NewGRPCClient(conn), md: md}, nil
}

func (e *grpcExporter) close() { _ = e.conn.Close() }

func (e *grpcExporter) export(ctx context.Context, b encodedBatch, n int) (exportOutcome, int) {
	var wire atomic.Int64
	ctx, cancel := context.WithTimeout(ctx, e.cfg.Timeout)
	defer cancel()
	ctx = context.WithValue(metadata.NewOutgoingContext(ctx, e.md), wireCounterKey{}, &wire)
	var opts []grpc.CallOption
	if e.cfg.Gzip {
		opts = append(opts, grpc.UseCompressor(gzip.Name))
	}
	resp, err := e.client.Export(ctx, b.request, opts...)
	var rejected int64
	message := false
	if err == nil {
		rejected = resp.PartialSuccess().RejectedSpans()
		message = resp.PartialSuccess().ErrorMessage() != ""
	}
	return classifyGRPC(err, rejected, message, n), int(wire.Load())
}
