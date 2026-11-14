package server

import (
	"bytes"
	"context"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/jackc/pgx/v5/pgconn"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"golang.org/x/sync/semaphore"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// Only persistence is deterministic: keys pass through the real Authenticator,
// its middleware, cache, project resolution, stamping and server usage path.
type ingestResolver struct {
	validations, creates atomic.Int64
	transient            atomic.Bool
}

func (r *ingestResolver) ValidateKey(_ context.Context, key, secret string) (*auth.ResolveResult, error) {
	r.validations.Add(1)
	if key != "k" || secret != "s" {
		return nil, nil
	}
	return &auth.ResolveResult{OrgID: calOrg, WorkspaceID: calWS, Projects: map[string]string{}}, nil
}
func (r *ingestResolver) ResolveProjects(context.Context, string, string, []string) (map[string]string, error) {
	return nil, nil
}
func (r *ingestResolver) GetOrCreateProject(_ context.Context, _, _, name, _ string) (string, error) {
	r.creates.Add(1)
	if r.transient.Load() {
		return "", context.DeadlineExceeded
	}
	if strings.HasPrefix(name, "uncreatable") {
		return "", &pgconn.PgError{Code: "23514", Message: "test check constraint"}
	}
	return calProject, nil
}
func realTestAuth(t *testing.T, r *ingestResolver, ttl time.Duration) *auth.Authenticator {
	t.Helper()
	a, err := auth.NewWithResolver(auth.Config{CacheTTL: ttl, WarmTTL: ttl}, r, nil, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil || a == nil {
		t.Fatalf("real authenticator: %v", err)
	}
	t.Cleanup(a.Close)
	return a
}
func attachRealAuth(s *Server, a *auth.Authenticator) {
	s.metering = NoopMetering{}
	s.auth, s.grpcAuth, s.grpcKnown, s.requireCreds = a, a.AuthenticateGRPC, a.KnownGRPCCredentials, true
}
func authenticatedBody(t *testing.T, name string) []byte {
	td := makeTraces("authenticated", calProject)
	td.ResourceSpans().At(0).Resource().Attributes().PutStr("project_name", name)
	return protoBody(t, td)
}
func authPost(s *Server, body []byte) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/x-protobuf")
	req.Header.Set("X-Api-Key", "k")
	req.Header.Set("X-Secret-Key", "s")
	rec := httptest.NewRecorder()
	s.auth.HTTPMiddleware(http.HandlerFunc(s.handleHTTPTraces)).ServeHTTP(rec, req)
	return rec
}

type recordingUsage struct {
	calls          atomic.Int64
	spans          atomic.Int64
	wrongOrg       atomic.Bool
	enter, release chan struct{}
	once           sync.Once
}

func (u *recordingUsage) EmitIngestion(org string, traces, spans int, payload int64, dedup string) {
	if org != calOrg || payload <= 0 {
		u.wrongOrg.Store(true)
	}
	u.calls.Add(1)
	u.spans.Add(int64(spans))
	if u.enter != nil {
		u.once.Do(func() { close(u.enter); <-u.release })
	}
}
func TestAuthenticatedProjectTimeoutRecovery(t *testing.T) {
	for _, transport := range []string{"http", "grpc"} {
		t.Run(transport, func(t *testing.T) {
			r := &ingestResolver{}
			r.transient.Store(true)
			a := realTestAuth(t, r, time.Minute)
			usage := &recordingUsage{}
			var s *Server
			var conn *grpc.ClientConn
			if transport == "grpc" {
				s, conn, _ = startGRPCServer(t, Config{}, func(s *Server) { attachRealAuth(s, a); s.usage = usage })
			} else {
				s = New(Config{}, nil, a, usage, NoopMetering{})
			}
			body := authenticatedBody(t, "new-project")
			attempt := func() error {
				if conn != nil {
					ctx, cancel := context.WithTimeout(withKeys(context.Background()), 5*time.Second)
					defer cancel()
					return exportRaw(conn, ctx, body)
				}
				rec := authPost(s, body)
				if rec.Code == 200 {
					return nil
				}
				if rec.Code == 503 && rec.Header().Get("Retry-After") == "1" {
					return status.Error(codes.Unavailable, "retry")
				}
				return errors.New(rec.Body.String())
			}
			if err := attempt(); status.Code(err) != codes.Unavailable {
				t.Fatalf("timeout must be retryable: %v", err)
			}
			if len(s.pend) != 0 || usage.calls.Load() != 0 {
				t.Fatal("failed resolution enqueued or billed data")
			}
			if r.creates.Load() != 1 {
				t.Fatal("persistence failure was internally retried as an auth change")
			}
			r.transient.Store(false)
			if err := attempt(); err != nil {
				t.Fatalf("recovery: %v", err)
			}
			if usage.calls.Load() != 1 || usage.spans.Load() != 1 || usage.wrongOrg.Load() {
				t.Fatal("successful request did not emit authenticated usage")
			}
			s.pendMu.Lock()
			defer s.pendMu.Unlock()
			if len(s.pend) != 1 || s.pend[0]["project_id"] != calProject {
				t.Fatalf("stamping/enqueue failed: rows=%d", len(s.pend))
			}
			if r.creates.Load() != 2 {
				t.Fatalf("create calls=%d; transient failure cached", r.creates.Load())
			}
		})
	}
}
func TestAuthenticatedBusyRefusalsKeepAcceptedCall(t *testing.T) {
	r := &ingestResolver{}
	a := realTestAuth(t, r, time.Minute)
	usage := &recordingUsage{enter: make(chan struct{}), release: make(chan struct{})}
	s, conn, _ := startGRPCServer(t, Config{}, func(s *Server) { attachRealAuth(s, a); s.grpcStreams = semaphore.NewWeighted(1); s.usage = usage })
	var release sync.Once
	defer release.Do(func() { close(usage.release) })
	ctx, cancel := context.WithTimeout(withKeys(context.Background()), 10*time.Second)
	defer cancel()
	done := make(chan error, 1)
	body := authenticatedBody(t, "accepted")
	go func() { done <- exportRaw(conn, ctx, body) }()
	select {
	case <-usage.enter:
	case <-ctx.Done():
		t.Fatal("accepted call never reached usage")
	}
	for i := 0; i < grpcMaxTapRefusals+32; i++ {
		err := exportRaw(conn, ctx, body)
		if status.Code(err) != codes.Unavailable || !strings.Contains(status.Convert(err).Message(), "too many concurrent requests") {
			t.Fatalf("refusal %d killed transport: %v", i, err)
		}
	}
	if s.tapRefusalConns.Load() != 0 {
		t.Fatal("authenticated capacity refusals closed connection")
	}
	release.Do(func() { close(usage.release) })
	if err := <-done; err != nil {
		t.Fatalf("accepted RPC failed: %v", err)
	}
	if err := exportRaw(conn, ctx, body); err != nil {
		t.Fatalf("same connection after recovery: %v", err)
	}
	if usage.calls.Load() != 2 {
		t.Fatalf("accepted usage=%d", usage.calls.Load())
	}
}
func TestAuthenticatedCacheChurnAndPermanentFailure(t *testing.T) {
	r := &ingestResolver{}
	a := realTestAuth(t, r, 5*time.Millisecond)
	usage := &recordingUsage{}
	s := New(Config{}, nil, a, usage, NoopMetering{})
	for i := 0; i < 4; i++ {
		if rec := authPost(s, authenticatedBody(t, "known")); rec.Code != 200 {
			t.Fatalf("churn request: %d", rec.Code)
		}
		time.Sleep(12 * time.Millisecond)
	}
	if r.validations.Load() < 4 || r.creates.Load() < 4 || usage.calls.Load() != 4 {
		t.Fatal("cold-cache churn did not exercise full auth/stamp/usage path")
	}
	r2 := &ingestResolver{}
	s.auth = realTestAuth(t, r2, time.Minute)
	body := authenticatedBody(t, "uncreatable-project")
	for i := 0; i < 2; i++ {
		rec := authPost(s, body)
		if rec.Code != 200 {
			t.Fatalf("permanent constraint handling: %d", rec.Code)
		}
		response := ptraceotlp.NewExportResponse()
		if err := response.UnmarshalProto(rec.Body.Bytes()); err != nil || response.PartialSuccess().RejectedSpans() != 1 {
			t.Fatalf("missing partial success: %v", err)
		}
	}
	if usage.calls.Load() != 4 {
		t.Fatal("fully dropped requests emitted usage")
	}

	if r2.creates.Load() != 1 {
		t.Fatal("deterministic value failure was not cached")
	}
}
