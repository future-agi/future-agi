package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// Both real TCP transports reach routing with the same old authentication.
// Invalidation happens only after both arrive; neither may enqueue or bill the
// old attempt. The existing parity matrix separately covers repeated churn.
func TestSharedKeyRotationHTTPAndGRPC(t *testing.T) {
	for _, change := range []string{"replace", "scope-remap", "revoke"} {
		t.Run(change, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			old := restartResult(routeOrg, routeWorkspace, false)
			fresh := restartResult(routeOrg, routeWorkspace, false)
			if change == "scope-remap" {
				fresh = restartResult(calOrg, calWS, false)
				fresh.Projects["customer-prod"] = calProject
			}
			r := &restartResolver{result: func(state int32) *auth.ResolveResult {
				if state == 0 {
					return old
				}
				if change == "revoke" {
					return nil
				}
				return fresh
			}}
			a, err := auth.NewWithResolver(auth.Config{CacheTTL: time.Hour, WarmTTL: time.Hour}, r, nil, slog.New(slog.NewTextHandler(io.Discard, nil)))
			if err != nil {
				t.Fatal(err)
			}
			if _, err := a.Authenticate(ctx, "k", "s"); err != nil {
				t.Fatal(err)
			}
			entered, release := make(chan struct{}, 2), make(chan struct{})
			var once sync.Once
			defer once.Do(func() { close(release) })
			var routes atomic.Int32
			usage := &routeUsage{}
			s, conn, ch := startGRPCServer(t, Config{}, func(s *Server) {
				attachRealAuth(s, a)
				s.usage, s.propertyCatalog = usage, nopCatalog{}
				s.router = restartRouter(func(ctx context.Context, _ string, _ ptrace.Traces, result *auth.ResolveResult) (auth.RouteOutcome, error) {
					routes.Add(1)
					if result == old {
						entered <- struct{}{}
						select {
						case <-release:
						case <-ctx.Done():
							return auth.RouteOutcome{}, ctx.Err()
						}
					}
					return auth.RouteOutcome{}, nil
				})
			})
			httpServer := httptest.NewServer(s.countAuthRefusals(a.HTTPMiddleware(http.HandlerFunc(s.handleHTTPTraces))))
			defer httpServer.Close()
			results := make(chan error, 2)
			var expectedBytes int64
			for transport := 0; transport < 2; transport++ {
				req := routeRequest("customer-prod")
				spans := req.Traces().ResourceSpans().At(0).ScopeSpans().At(0).Spans()
				for i := 0; i < spans.Len(); i++ {
					spans.At(i).SetSpanID(pcommon.SpanID{byte(transport + 1), byte(i + 1)})
				}
				body, err := req.MarshalProto()
				if err != nil {
					t.Fatal(err)
				}
				attrs := req.Traces().ResourceSpans().At(0).Resource().Attributes()
				attrs.PutStr("fi.org_id", fresh.OrgID)
				attrs.PutStr("fi.project_id", fresh.Projects["customer-prod"])
				expectedBytes += int64((&ptrace.ProtoMarshaler{}).TracesSize(req.Traces()))
				go func(transport int, body []byte) {
					if transport == 1 {
						results <- exportRaw(conn, withKeys(ctx), body)
						return
					}
					req, err := http.NewRequestWithContext(ctx, http.MethodPost, httpServer.URL+"/v1/traces", bytes.NewReader(body))
					if err != nil {
						results <- err
						return
					}
					req.Header.Set("Content-Type", "application/x-protobuf")
					req.Header.Set("X-Api-Key", "k")
					req.Header.Set("X-Secret-Key", "s")
					resp, err := httpServer.Client().Do(req)
					if err == nil {
						_, _ = io.Copy(io.Discard, resp.Body)
						resp.Body.Close()
						switch resp.StatusCode {
						case http.StatusOK:
						case http.StatusUnauthorized:
							err = status.Error(codes.Unauthenticated, "rejected")
						default:
							err = fmt.Errorf("HTTP status %d", resp.StatusCode)
						}
					}
					results <- err
				}(transport, body)
			}
			for range 2 {
				select {
				case <-entered:
				case <-ctx.Done():
					t.Fatal("both transports did not reach old-generation barrier")
				}
			}
			r.state.Store(1)
			a.InvalidateKey(auth.CacheKey("k", "s"))
			once.Do(func() { close(release) })
			for range 2 {
				select {
				case err := <-results:
					if change == "revoke" {
						if status.Code(err) != codes.Unauthenticated {
							t.Fatalf("revoked export: %v", err)
						}
					} else if err != nil {
						t.Fatal(err)
					}
				case <-ctx.Done():
					t.Fatal("exports did not settle")
				}
			}
			s.pendMu.Lock()
			rows, sidecars, leased := s.pend, s.pendProperty, s.pendLeased
			s.pendMu.Unlock()
			wantRows, wantCalls := 4, int32(4)
			if change == "revoke" {
				wantRows, wantCalls = 0, 2
			}
			if len(rows) != wantRows || len(sidecars) != wantRows || routes.Load() != wantCalls {
				t.Fatalf("rows=%d sidecars=%d routing_attempts=%d", len(rows), len(sidecars), routes.Load())
			}
			seen := map[string]bool{}
			for _, row := range rows {
				id := row["id"].(string)
				if seen[id] || row["project_id"] != fresh.Projects["customer-prod"] {
					t.Fatal("duplicate row or old project enqueued")
				}
				seen[id] = true
			}
			var billedBytes int64
			for _, event := range usage.snapshot() {
				if event.org != fresh.OrgID || event.spans != 2 {
					t.Fatal("stale or duplicate billing")
				}
				billedBytes += event.payloadBytes
			}
			if change == "revoke" {
				expectedBytes = 0
			}
			if len(usage.snapshot()) != wantRows/2 || billedBytes != expectedBytes {
				t.Fatalf("billing events=%d bytes=%d want=%d", len(usage.snapshot()), billedBytes, expectedBytes)
			}
			for _, sidecar := range sidecars {
				if sidecar.OrganizationID != fresh.OrgID || sidecar.WorkspaceID != fresh.WorkspaceID || sidecar.ScopeError != "" {
					t.Fatal("sidecar retained old scope")
				}
			}
			if s.wire.inUse.Load() != 0 || s.work.inUse.Load() != leased {
				t.Fatal("handler leaked a lease")
			}
			s.drainNow(ctx)
			if s.work.inUse.Load() != 0 {
				t.Fatal("drain leaked a lease")
			}
			ch.mu.Lock()
			defer ch.mu.Unlock()
			stored := map[string]bool{}
			for _, body := range ch.tables["spans"] {
				dec := json.NewDecoder(bytes.NewReader(body))
				for dec.More() {
					var row struct {
						ID string `json:"id"`
					}
					if err := dec.Decode(&row); err != nil {
						t.Fatal(err)
					}
					if stored[row.ID] || !seen[row.ID] {
						t.Fatal("duplicate or unexpected stored span")
					}
					stored[row.ID] = true
				}
			}
			if len(stored) != wantRows {
				t.Fatalf("stored IDs=%d want=%d", len(stored), wantRows)
			}
		})
	}
}
