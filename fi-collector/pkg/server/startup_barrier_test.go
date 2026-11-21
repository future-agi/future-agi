package server

import (
	"context"
	"errors"
	"net/http"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc/codes"
)

func TestStartupBarrierPrecedesDecodeOnBothExportPaths(t *testing.T) {
	ready := make(chan struct{})
	s := New(Config{}, nil, nil, NoopUsageEmitter{}, NoopMetering{}, WithStartupBarrier(ready))
	decoded := 0
	decode := func() (ptraceotlp.ExportRequest, error) {
		decoded++
		return ptraceotlp.NewExportRequest(), errors.New("fixture")
	}
	_, rej := s.prepareExport(context.Background(), decode, auth.ProjectNameValues{}, nil)
	if decoded != 0 || rej == nil || rej.httpStatus() != http.StatusServiceUnavailable || rej.grpcCode() != codes.Unavailable {
		t.Fatal("closed barrier must refuse before export")
	}
	close(ready)
	s.prepareExport(context.Background(), decode, auth.ProjectNameValues{}, nil)
	if decoded != 1 {
		t.Fatal("installed schema should admit exports")
	}
}
