package server

import (
	"bytes"
	"context"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// Tests for how refusals are answered and counted (ingest.go).

type denyAll struct{}

func (denyAll) CheckUsage(context.Context, string, string, int64) auth.CheckResult {
	return auth.CheckResult{Allowed: false, Reason: "monthly tracing events used up"}
}

// A quota refusal is counted before reading the body: HTTP 429 without
// Retry-After, gRPC RESOURCE_EXHAUSTED without RetryInfo. No short retry is promised.
func TestQuotaRefusalIsCountedWithoutShortRetryHint(t *testing.T) {
	result := &auth.ResolveResult{OrgID: "org"}
	t.Run("http", func(t *testing.T) {
		s := New(Config{}, nil, nil, nil, denyAll{})
		req := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(emptySpans(t, 1)))
		req.Header.Set("Content-Type", "application/x-protobuf")
		req = req.WithContext(auth.WithResolvedContext(req.Context(), result, "ck"))
		rec := httptest.NewRecorder()
		s.handleHTTPTraces(rec, req)
		if rec.Code != http.StatusTooManyRequests || rec.Header().Get("Retry-After") != "" {
			t.Errorf("status %d, Retry-After %q; want 429 without Retry-After", rec.Code, rec.Header().Get("Retry-After"))
		}
		if got := s.IngestStats().Rejected["http/quota/429"]; got != 1 {
			t.Errorf("quota refusals counted %d, want 1", got)
		}
		if p := s.wire.peak.Load(); p != 0 {
			t.Errorf("leased %d wire bytes for a refused request", p)
		}
	})
	t.Run("grpc", func(t *testing.T) {
		s, conn, _ := startGRPCServer(t, Config{}, func(s *Server) {
			s.metering = denyAll{}
			s.grpcAuth = func(ctx context.Context) (context.Context, error) {
				return auth.WithResolvedContext(ctx, result, "ck"), nil
			}
		})
		err := exportRaw(conn, context.Background(), emptySpans(t, 1))
		if status.Code(err) != codes.ResourceExhausted || len(status.Convert(err).Details()) != 0 {
			t.Errorf("Export over quota: %v, want RESOURCE_EXHAUSTED", err)
		}
		if got := s.IngestStats().Rejected["grpc/quota/ResourceExhausted"]; got != 1 {
			t.Errorf("quota refusals counted %d, want 1", got)
		}
	})
}

// The OTLP/HTTP auth middleware's 401s are counted with the other refusals.
func TestOTLPHTTPAuthRefusalsAreCounted(t *testing.T) {
	pg := "postgres://u:p@127.0.0.1:1/x?connect_timeout=1"
	authn, err := auth.New(context.Background(), auth.Config{PGWrite: pg, PGRead: pg}, nil, slog.Default())
	if err != nil || authn == nil {
		t.Fatalf("auth.New: %v", err)
	}
	s := New(Config{}, nil, authn, nil, nil)
	h := s.countAuthRefusals(authn.HTTPMiddleware(http.HandlerFunc(s.handleHTTPTraces)))
	req := httptest.NewRequest(http.MethodPost, "/v1/traces", bytes.NewReader(emptySpans(t, 1)))
	req.Header.Set("Content-Type", "application/x-protobuf")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("status %d, want 401", rec.Code)
	}
	if got := s.IngestStats().Rejected["http/auth/401"]; got != 1 {
		t.Errorf("auth refusals counted %d, want 1 (%v)", got, s.IngestStats().Rejected)
	}
}

// A gRPC call with no credentials is refused by the tap handle, counted, and
// logged (sampled).
func TestGRPCNoCredentialsRefusalIsLogged(t *testing.T) {
	var logs bytes.Buffer
	var mu syncWriter
	mu.w = &logs
	s, conn, _ := startGRPCServer(t, Config{}, func(s *Server) {
		s.requireCreds = true
		s.log = slog.New(slog.NewTextHandler(&mu, nil))
	})
	for i := 0; i < 3; i++ {
		if err := exportRaw(conn, context.Background(), emptySpans(t, 1)); status.Code(err) != codes.Unauthenticated {
			t.Fatalf("Export without keys: %v, want UNAUTHENTICATED", err)
		}
	}
	if got := s.IngestStats().Rejected["grpc/no_credentials/Unauthenticated"]; got != 3 {
		t.Errorf("counted %d, want 3", got)
	}
	deadline := time.Now().Add(time.Second)
	for !bytes.Contains(mu.bytes(), []byte("reason=no_credentials")) && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if n := bytes.Count(mu.bytes(), []byte("reason=no_credentials")); n != 1 {
		t.Errorf("logged %d lines for 3 refusals within a second, want 1 (sampled)", n)
	}
}

// syncWriter is an io.Writer safe for concurrent log lines.
type syncWriter struct {
	mu sync.Mutex
	w  *bytes.Buffer
}

func (s *syncWriter) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.w.Write(p)
}

func (s *syncWriter) bytes() []byte {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]byte(nil), s.w.Bytes()...)
}
