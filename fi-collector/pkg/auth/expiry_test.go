package auth

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

func timePtr(t time.Time) *time.Time { return &t }

func TestResolveResultExpired(t *testing.T) {
	now := time.Date(2026, 10, 7, 12, 0, 0, 0, time.UTC)
	cases := []struct {
		name   string
		result *ResolveResult
		want   bool
	}{
		{"nil result", nil, false},
		{"no expiry", &ResolveResult{}, false},
		{"future expiry", &ResolveResult{ExpiresAt: timePtr(now.Add(time.Second))}, false},
		{"expires now", &ResolveResult{ExpiresAt: timePtr(now)}, true},
		{"past expiry", &ResolveResult{ExpiresAt: timePtr(now.Add(-time.Hour))}, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := tc.result.Expired(now); got != tc.want {
				t.Errorf("Expired() = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestErrKeyExpiredIsUnauthenticated(t *testing.T) {
	if !errors.Is(ErrKeyExpired, ErrUnauthenticated) {
		t.Fatal("ErrKeyExpired must wrap ErrUnauthenticated so generic callers still refuse it")
	}
}

func newExpiryTestAuthenticator(apiKey, secretKey string, expiresAt *time.Time) *Authenticator {
	a := &Authenticator{
		cache: newCache(5*time.Minute, time.Hour),
		log:   slog.Default(),
	}
	a.cache.putPositive(CacheKey(apiKey, secretKey), &ResolveResult{
		OrgID:       "org-exp",
		WorkspaceID: "ws-exp",
		ExpiresAt:   expiresAt,
		Projects:    map[string]string{},
	})
	return a
}

func TestAuthenticateRejectsCachedKeyAfterExpiry(t *testing.T) {
	a := newExpiryTestAuthenticator("k", "s", timePtr(time.Now().Add(-time.Minute)))

	result, err := a.Authenticate(context.Background(), "k", "s")
	if !errors.Is(err, ErrKeyExpired) {
		t.Fatalf("err = %v, want ErrKeyExpired", err)
	}
	if result != nil {
		t.Errorf("result = %v, want nil", result)
	}
	if _, ok := a.cache.m.Load(CacheKey("k", "s")); ok {
		t.Error("expired key must be evicted from the cache")
	}
}

func TestAuthenticateAcceptsCachedKeyBeforeExpiry(t *testing.T) {
	a := newExpiryTestAuthenticator("k", "s", timePtr(time.Now().Add(time.Hour)))

	result, err := a.Authenticate(context.Background(), "k", "s")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result == nil || result.OrgID != "org-exp" {
		t.Errorf("result = %v, want OrgID=org-exp", result)
	}
}

func TestHTTPMiddlewareExpiredKey(t *testing.T) {
	a := newExpiryTestAuthenticator("k", "s", timePtr(time.Now().Add(-time.Minute)))
	handler := a.HTTPMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Fatal("handler must not run for an expired key")
	}))

	req := httptest.NewRequest("POST", "/v1/traces", nil)
	req.Header.Set("X-Api-Key", "k")
	req.Header.Set("X-Secret-Key", "s")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", rec.Code)
	}
	if body := rec.Body.String(); !strings.Contains(body, `"code":"api_key_expired"`) ||
		!strings.Contains(body, ExpiredKeyMessage) {
		t.Errorf("body = %q, want the api_key_expired code and message", body)
	}
}

func TestGRPCInterceptorExpiredKey(t *testing.T) {
	a := newExpiryTestAuthenticator("k", "s", timePtr(time.Now().Add(-time.Minute)))
	handler := func(ctx context.Context, req any) (any, error) {
		t.Fatal("handler must not run for an expired key")
		return nil, nil
	}
	ctx := metadata.NewIncomingContext(context.Background(), metadata.New(map[string]string{
		"x-api-key":    "k",
		"x-secret-key": "s",
	}))

	_, err := a.GRPCInterceptor()(ctx, nil, &grpc.UnaryServerInfo{}, handler)

	st, _ := status.FromError(err)
	if st.Code() != codes.Unauthenticated || st.Message() != ExpiredKeyMessage {
		t.Errorf("status = (%v, %q), want (Unauthenticated, %q)", st.Code(), st.Message(), ExpiredKeyMessage)
	}
}

type fakeKeyValidator struct {
	result *ResolveResult
	err    error
	calls  int
}

func (f *fakeKeyValidator) ValidateKey(context.Context, string, string) (*ResolveResult, error) {
	f.calls++
	return f.result, f.err
}

func TestAuthenticateLookupRejectsExpiredKeyWithoutCaching(t *testing.T) {
	keys := &fakeKeyValidator{err: ErrKeyExpired}
	a := &Authenticator{
		cache: newCache(5*time.Minute, time.Hour),
		keys:  keys,
		log:   slog.Default(),
	}

	for i := 0; i < 2; i++ {
		if _, err := a.Authenticate(context.Background(), "k", "s"); !errors.Is(err, ErrKeyExpired) {
			t.Fatalf("attempt %d: err = %v, want ErrKeyExpired", i, err)
		}
	}
	if keys.calls != 2 {
		t.Errorf("lookups = %d, want 2 (expired keys are never cached)", keys.calls)
	}
}

func TestAuthenticateLookupStillRejectsUnknownKey(t *testing.T) {
	a := &Authenticator{
		cache: newCache(5*time.Minute, time.Hour),
		keys:  &fakeKeyValidator{},
		log:   slog.Default(),
	}

	_, err := a.Authenticate(context.Background(), "k", "s")
	if !errors.Is(err, ErrUnauthenticated) || errors.Is(err, ErrKeyExpired) {
		t.Fatalf("err = %v, want plain ErrUnauthenticated", err)
	}
}

func TestRefreshKeyEvictsKeyThatExpired(t *testing.T) {
	a := newExpiryTestAuthenticator("k", "s", nil)
	a.keys = &fakeKeyValidator{err: ErrKeyExpired}

	a.refreshKey(context.Background(), "k", "s")

	if _, ok := a.cache.m.Load(CacheKey("k", "s")); ok {
		t.Error("a key that expired since it was cached must be evicted on refresh")
	}
}
