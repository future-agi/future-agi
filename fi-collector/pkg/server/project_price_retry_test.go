package server

import (
	"context"
	"io"
	"log/slog"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc/codes"
)

// Final-shape growth is a new wait inside preparation. Force invalidation
// during that wait, not merely during routing, and prove both transports
// discard every old stamp, recheck fresh quota, and emit usage at most once.
func TestProjectAuthRestartAfterFinalPriceWait(t *testing.T) {
	for _, transport := range []string{"http-proto", "grpc"} {
		for _, change := range []string{"tenant", "quota", "revoked"} {
			t.Run(transport+"/"+change, func(t *testing.T) {
				r := &restartResolver{}
				r.result = func(state int32) *auth.ResolveResult {
					if state == 0 {
						return restartResult(routeOrg, routeWorkspace, false)
					}
					if change == "revoked" {
						return nil
					}
					result := restartResult(calOrg, calWS, false)
					result.Projects["header-project"] = calProject
					return result
				}
				a, err := auth.NewWithResolver(auth.Config{CacheTTL: time.Hour, WarmTTL: time.Hour}, r, nil, slog.New(slog.NewTextHandler(io.Discard, nil)))
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(a.Close)
				usage, meter := &routeUsage{}, &restartMeter{}
				if change == "quota" {
					meter.deny = calOrg
				}
				blocked := make(chan struct{})
				keyReady := make(chan string, 1)
				var blockOnce sync.Once
				var blocker *lease
				var calls atomic.Int32
				var originalBudget *leaseBudget
				s, conn, _ := startGRPCServer(t, Config{}, func(s *Server) {
					attachRealAuth(s, a)
					s.usage = usage
					s.metering = meter
					s.propertyCatalog = nopCatalog{}
					s.work.onBlock = func() { blockOnce.Do(func() { close(blocked) }) }
					s.router = restartRouter(func(ctx context.Context, key string, td ptrace.Traces, result *auth.ResolveResult) (auth.RouteOutcome, error) {
						if calls.Add(1) == 1 {
							// Production's four scalar stamps are reserved up front.
							// Force the fallback final-shape guard with an additional
							// test-only router field, then prove it cannot survive retry.
							td.ResourceSpans().At(0).Resource().Attributes().PutStr("unpriced_test_shape", strings.Repeat("x", 2048))
							originalBudget = leaseBudgetFrom(ctx)
							var err error
							blocker, err = s.work.acquire(ctx, s.work.size-s.work.inUse.Load())
							if err != nil {
								return auth.RouteOutcome{}, err
							}
							keyReady <- key
						} else if originalBudget != leaseBudgetFrom(ctx) {
							t.Error("final-shape retry replaced the shared wait budget")
						}
						return auth.RouteOutcome{}, nil
					})
				})
				req := ptraceotlp.NewExportRequestFromTraces(calTraces(1, 1, 1, nil))
				req.Traces().ResourceSpans().At(0).Resource().Attributes().Clear()
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				result := make(chan codes.Code, 1)
				go func() { code, _ := sendRestartRequest(t, s, conn, ctx, transport, req); result <- code }()
				var key string
				select {
				case key = <-keyReady:
				case <-ctx.Done():
					t.Fatal("request did not reach routing")
				}
				defer blocker.release()
				select {
				case <-blocked:
				case <-ctx.Done():
					t.Fatal("final shape did not need a reservation growth")
				}
				r.state.Store(1)
				a.InvalidateKey(key)
				blocker.release()
				var got codes.Code
				select {
				case got = <-result:
				case <-ctx.Done():
					t.Fatal("request did not finish")
				}
				want := codes.OK
				if change == "quota" {
					want = codes.ResourceExhausted
				}
				if change == "revoked" {
					want = codes.Unauthenticated
				}
				if got != want {
					t.Fatalf("lost-generation result=%s want=%s", got, want)
				}
				s.pendMu.Lock()
				pending, properties := s.pend, s.pendProperty
				s.pendMu.Unlock()
				events := usage.snapshot()
				if want != codes.OK {
					if len(pending) != 0 || len(properties) != 0 || len(events) != 0 {
						t.Fatal("old-generation rows or usage escaped on refusal")
					}
					return
				}
				if len(pending) != 1 || len(properties) != 1 || len(events) != 1 {
					t.Fatalf("accepted retry rows=%d sidecars=%d usage=%d", len(pending), len(properties), len(events))
				}
				if pending[0]["org_id"] != calOrg || pending[0]["project_id"] != calProject || properties[0].WorkspaceID != calWS || properties[0].OrganizationID != calOrg {
					t.Fatal("old tenant stamps survived final-shape wait")
				}
				if _, stale := pending[0]["resource_attrs"].(map[string]any)["unpriced_test_shape"]; stale {
					t.Fatal("partially mutated old request survived repricing retry")
				}
				if calls.Load() != 2 || r.validations.Load() != 2 {
					t.Fatalf("expected one restart, routing=%d validations=%d", calls.Load(), r.validations.Load())
				}
			})
		}
	}
}
