package main

import (
	"context"

	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

// otlpSender ships fabricated traces to a running collector over OTLP/gRPC.
// It is the wire-mode counterpart to the in-process Convert+Insert path:
// the collector performs the conversion server-side.
type otlpSender struct {
	conn   *grpc.ClientConn
	client ptraceotlp.GRPCClient
}

// newOTLPSender dials endpoint (host:4317) with an insecure transport.
func newOTLPSender(endpoint string) (*otlpSender, error) {
	conn, err := grpc.NewClient(endpoint, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, err
	}
	return &otlpSender{conn: conn, client: ptraceotlp.NewGRPCClient(conn)}, nil
}

func (s *otlpSender) Send(ctx context.Context, td ptrace.Traces) error {
	_, err := s.client.Export(ctx, ptraceotlp.NewExportRequestFromTraces(td))
	return err
}

func (s *otlpSender) Close() error { return s.conn.Close() }
