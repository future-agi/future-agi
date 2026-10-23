package server

import (
	"context"
	"net/http"
	"testing"

	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
)

// A deployment that does not opt into row admission keeps the pre-budget
// behaviour, including repeated exports larger than the proposed 20,000 cap.
// These call the real handlers; storage itself is covered by the live fixtures.
func TestDefaultAdmissionPreservesUnboundedExports(t *testing.T) {
	for _, protocol := range []string{"json", "protobuf", "grpc"} {
		t.Run(protocol, func(t *testing.T) {
			s := New(Config{}, nil, nil, nil, nil)
			if limits := s.Limits(); limits.MaxPendingRows != 0 || limits.BatchMaxRows != 5000 {
				t.Fatalf("default changed admission or batching: %+v", limits)
			}
			for i := 0; i < 2; i++ {
				req := ptraceotlp.NewExportRequestFromTraces(spansTraces(25000))
				if protocol == "grpc" {
					resp, err := (&otlpHandler{s: s}).Export(context.Background(), req)
					if err != nil || resp.PartialSuccess().RejectedSpans() != 0 {
						t.Fatalf("export %d refused: %v", i, err)
					}
				} else if resp := exportHTTP(s, req, protocol); resp.Code != http.StatusOK {
					t.Fatalf("export %d refused: %d", i, resp.Code)
				}
			}
			if a := s.Admission(); a.BufferedRows != 50000 || a.PendingRows != 50000 || a.Refusals != (Refusals{}) {
				t.Fatalf("unbounded admission: %+v", a)
			}
		})
	}
}
