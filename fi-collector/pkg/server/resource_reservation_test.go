package server

import (
	"bytes"
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"google.golang.org/grpc/metadata"
)

// Fill the rest of the pool after the first lease. Predictable auth/header
// additions must already be reserved, rather than wait while holding that lease.
func TestPredictableStampsNeedNoWorkPoolTopUp(t *testing.T) {
	for _, transport := range []string{"proto", "json", "grpc"} {
		for _, resourceName := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/resource-name=%t", transport, resourceName), func(t *testing.T) {
				a := realTestAuth(t, &ingestResolver{}, time.Minute)
				usage := &recordingUsage{}
				var blocker *lease
				configure := func(s *Server) {
					s.router = restartRouter(func(ctx context.Context, _ string, _ ptrace.Traces, _ *auth.ResolveResult) (auth.RouteOutcome, error) {
						var err error
						blocker, err = s.work.acquire(ctx, s.work.size-s.work.inUse.Load())
						return auth.RouteOutcome{}, err
					})
				}
				defer func() { blocker.release() }()
				td := calTraces(1, 1, 4, nil)
				attrs := td.ResourceSpans().At(0).Resource().Attributes()
				attrs.Clear()
				if resourceName {
					attrs.PutStr("project_name", "resource-wins")
				}
				header := strings.Repeat("a", 255)
				if resourceName {
					header = "ignored,invalid,header"
				} else if transport != "grpc" {
					header = strings.Repeat("\U0001f680", 255)
				}
				var s *Server
				if transport == "grpc" {
					server, conn, _ := startGRPCServer(t, Config{IngestMaxWait: 30 * time.Millisecond}, func(s *Server) {
						attachRealAuth(s, a)
						s.usage = usage
						configure(s)
					})
					s = server
					ctx := metadata.AppendToOutgoingContext(withKeys(context.Background()), "x-project-name", header)
					if err := exportRaw(conn, ctx, calBody(t, td, false)); err != nil {
						t.Fatalf("predictable stamps required a second reservation: %v", err)
					}
				} else {
					s = New(Config{IngestMaxWait: 30 * time.Millisecond}, nil, a, usage, NoopMetering{})
					configure(s)
					isJSON := transport == "json"
					req := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(calBody(t, td, isJSON)))
					req.Header.Set("Content-Type", "application/x-protobuf")
					if isJSON {
						req.Header.Set("Content-Type", "application/json")
					}
					req.Header.Set("X-Api-Key", "k")
					req.Header.Set("X-Secret-Key", "s")
					req.Header.Set(auth.ProjectNameHeader, header)
					rec := httptest.NewRecorder()
					s.auth.HTTPMiddleware(http.HandlerFunc(s.handleHTTPTraces)).ServeHTTP(rec, req)
					if rec.Code != http.StatusOK {
						t.Fatalf("predictable stamps required a second reservation: status=%d", rec.Code)
					}
				}
				if s.work.waits.Load() != 0 {
					t.Fatalf("predictable stamps made %d nested work-pool waits", s.work.waits.Load())
				}
				s.pendMu.Lock()
				n := len(s.pend)
				s.pendMu.Unlock()
				if n != 4 || usage.calls.Load() != 1 {
					t.Fatalf("successful preparation changed enqueue/billing cardinality: rows=%d usage=%d", n, usage.calls.Load())
				}
			})
		}
	}
}
