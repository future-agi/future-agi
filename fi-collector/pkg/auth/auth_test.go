package auth

import (
	"context"
	"encoding/base64"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

// ---------------------------------------------------------------------------
// CacheKey
// ---------------------------------------------------------------------------

func TestCacheKeyIncludesSecret(t *testing.T) {
	k1 := CacheKey("api-key-1", "secret-A")
	k2 := CacheKey("api-key-1", "secret-B")
	if k1 == k2 {
		t.Fatalf("cache keys must differ for different secrets: got %q for both", k1)
	}

	k3 := CacheKey("api-key-1", "secret-A")
	if k1 != k3 {
		t.Fatalf("same inputs must produce the same key: %q vs %q", k1, k3)
	}
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

func TestConfigDefaults(t *testing.T) {
	t.Run("ZeroValueFields", func(t *testing.T) {
		cfg := Config{PGWrite: "postgres://localhost/db"}
		cfg.defaults()

		if cfg.CacheTTL != 5*time.Minute {
			t.Errorf("CacheTTL = %v, want 5m", cfg.CacheTTL)
		}
		if cfg.WarmTTL != 1*time.Hour {
			t.Errorf("WarmTTL = %v, want 1h", cfg.WarmTTL)
		}
		if cfg.PGPoolRead != 5 {
			t.Errorf("PGPoolRead = %d, want 5", cfg.PGPoolRead)
		}
		if cfg.PGPoolWrite != 2 {
			t.Errorf("PGPoolWrite = %d, want 2", cfg.PGPoolWrite)
		}
		if cfg.PGRead != cfg.PGWrite {
			t.Errorf("PGRead = %q, want %q (should fall back to PGWrite)", cfg.PGRead, cfg.PGWrite)
		}
	})

	t.Run("ExplicitPGReadNotOverwritten", func(t *testing.T) {
		cfg := Config{PGWrite: "postgres://write", PGRead: "postgres://read"}
		cfg.defaults()

		if cfg.PGRead != "postgres://read" {
			t.Errorf("PGRead = %q, want postgres://read (explicit value must not be overwritten)", cfg.PGRead)
		}
	})

	t.Run("ExplicitValuesNotOverwritten", func(t *testing.T) {
		cfg := Config{
			PGWrite:     "postgres://localhost/db",
			CacheTTL:    10 * time.Second,
			WarmTTL:     30 * time.Second,
			PGPoolRead:  20,
			PGPoolWrite: 10,
		}
		cfg.defaults()

		if cfg.CacheTTL != 10*time.Second {
			t.Errorf("CacheTTL = %v, want 10s", cfg.CacheTTL)
		}
		if cfg.WarmTTL != 30*time.Second {
			t.Errorf("WarmTTL = %v, want 30s", cfg.WarmTTL)
		}
		if cfg.PGPoolRead != 20 {
			t.Errorf("PGPoolRead = %d, want 20", cfg.PGPoolRead)
		}
		if cfg.PGPoolWrite != 10 {
			t.Errorf("PGPoolWrite = %d, want 10", cfg.PGPoolWrite)
		}
	})
}

func TestConfigIsEnabled(t *testing.T) {
	t.Run("EmptyPGWrite", func(t *testing.T) {
		cfg := Config{}
		if cfg.IsEnabled() {
			t.Fatal("expected disabled when PGWrite is empty")
		}
	})

	t.Run("NonEmptyPGWrite", func(t *testing.T) {
		cfg := Config{PGWrite: "postgres://localhost/db"}
		if !cfg.IsEnabled() {
			t.Fatal("expected enabled when PGWrite is set")
		}
	})
}

// ---------------------------------------------------------------------------
// Cache
// ---------------------------------------------------------------------------

func TestCacheOnlyStoresValidKeys(t *testing.T) {
	c := newCache(5*time.Minute, 1*time.Hour)

	ck := CacheKey("api-key-1", "good-secret")
	result := &ResolveResult{OrgID: "org-1", Projects: map[string]string{}}
	c.putPositive(ck, result)

	entry, st := c.get(ck)
	if st != "fresh" || entry == nil {
		t.Fatalf("expected fresh entry, got status=%q", st)
	}

	ckBad := CacheKey("api-key-1", "bad-secret")
	_, st2 := c.get(ckBad)
	if st2 != "miss" {
		t.Fatalf("unknown key must be miss, got %q", st2)
	}
}

func TestCacheWarmStatus(t *testing.T) {
	c := newCache(1*time.Millisecond, 1*time.Hour)

	ck := CacheKey("key", "secret")
	c.putPositive(ck, &ResolveResult{OrgID: "org-1", Projects: map[string]string{}})

	_, st := c.get(ck)
	if st != "fresh" {
		t.Fatalf("expected fresh, got %q", st)
	}

	time.Sleep(5 * time.Millisecond)

	_, st = c.get(ck)
	if st != "warm" {
		t.Fatalf("expected warm after TTL expiry, got %q", st)
	}
}

func TestCacheExpiryPastWarmTTL(t *testing.T) {
	c := newCache(1*time.Millisecond, 2*time.Millisecond)

	ck := CacheKey("expire-key", "expire-secret")
	c.putPositive(ck, &ResolveResult{OrgID: "org-1", Projects: map[string]string{}})

	time.Sleep(10 * time.Millisecond)

	_, st := c.get(ck)
	if st != "miss" {
		t.Fatalf("expected miss after warm TTL expiry, got %q", st)
	}

	var count int
	c.m.Range(func(_, _ any) bool { count++; return true })
	if count != 0 {
		t.Fatalf("expected cache size 0 after expiry, got %d", count)
	}
}

func TestCacheAddProjectsMerges(t *testing.T) {
	c := newCache(5*time.Minute, 1*time.Hour)

	ck := "merge-key"
	result := &ResolveResult{
		OrgID:    "org-1",
		Projects: map[string]string{"existing": "id-existing"},
	}
	c.putPositive(ck, result)

	c.addProjects(ck, map[string]string{"new-proj": "id-new"})

	entry, st := c.get(ck)
	if st != "fresh" || entry == nil {
		t.Fatalf("expected fresh entry, got %q", st)
	}

	id, ok := entry.result.GetProject("existing")
	if !ok || id != "id-existing" {
		t.Errorf("existing project lost after merge: got (%q, %v)", id, ok)
	}

	id, ok = entry.result.GetProject("new-proj")
	if !ok || id != "id-new" {
		t.Errorf("new project not merged: got (%q, %v)", id, ok)
	}
}

func TestCacheAddProjectsNoOpOnMissingKey(t *testing.T) {
	c := newCache(5*time.Minute, 1*time.Hour)
	c.addProjects("nonexistent", map[string]string{"proj": "id"})
}

func TestCachePutPositiveOverwrites(t *testing.T) {
	c := newCache(5*time.Minute, 1*time.Hour)

	ck := "overwrite-key"
	resultA := &ResolveResult{OrgID: "org-A", Projects: map[string]string{}}
	resultB := &ResolveResult{OrgID: "org-B", Projects: map[string]string{}}

	c.putPositive(ck, resultA)
	time.Sleep(1 * time.Millisecond)
	c.putPositive(ck, resultB)

	entry, st := c.get(ck)
	if st != "fresh" || entry == nil {
		t.Fatalf("expected fresh entry, got %q", st)
	}
	if entry.result.OrgID != "org-B" {
		t.Errorf("expected overwritten result org-B, got %q", entry.result.OrgID)
	}
}

func TestCacheSize(t *testing.T) {
	c := newCache(5*time.Minute, 1*time.Hour)

	var count int
	c.m.Range(func(_, _ any) bool { count++; return true })
	if count != 0 {
		t.Fatalf("empty cache size = %d, want 0", count)
	}

	c.putPositive("k1", &ResolveResult{OrgID: "o1", Projects: map[string]string{}})
	c.putPositive("k2", &ResolveResult{OrgID: "o2", Projects: map[string]string{}})

	count = 0
	c.m.Range(func(_, _ any) bool { count++; return true })
	if count != 2 {
		t.Fatalf("cache size = %d, want 2", count)
	}
}

// ---------------------------------------------------------------------------
// Authenticator nil
// ---------------------------------------------------------------------------

func TestAuthenticateNilAuthenticator(t *testing.T) {
	var a *Authenticator
	result, err := a.Authenticate(context.Background(), "any-key", "any-secret")
	if err != nil {
		t.Fatalf("nil authenticator must not error, got: %v", err)
	}
	if result != nil {
		t.Fatalf("nil authenticator must return nil result, got: %v", result)
	}
}

func TestResolveProjectsForKeyNilAuthenticator(t *testing.T) {
	var a *Authenticator
	err := a.ResolveProjectsForKey(context.Background(), "ck", &ResolveResult{
		OrgID:    "org-1",
		Projects: map[string]string{},
	}, []string{"proj"})
	if err != nil {
		t.Fatalf("nil authenticator must not error, got: %v", err)
	}
}

func TestResolveProjectsForKeyNilResult(t *testing.T) {
	a := &Authenticator{
		cache: newCache(5*time.Minute, 1*time.Hour),
		log:   slog.Default(),
	}
	err := a.ResolveProjectsForKey(context.Background(), "ck", nil, []string{"proj"})
	if err != nil {
		t.Fatalf("nil result must not error, got: %v", err)
	}
}

func TestResolveProjectsCached(t *testing.T) {
	a := &Authenticator{
		cache: newCache(5*time.Minute, 1*time.Hour),
		log:   slog.Default(),
	}

	result := &ResolveResult{
		OrgID:    "org-1",
		Projects: map[string]string{"proj-a": "id-a", "proj-b": "id-b"},
	}

	ck := CacheKey("api-key-1", "secret-1")
	a.cache.putPositive(ck, result)

	err := a.ResolveProjectsForKey(context.Background(), ck, result, []string{"proj-a", "proj-b"})
	if err != nil {
		t.Fatalf("expected no error when all projects cached, got: %v", err)
	}

	id, ok := result.GetProject("proj-a")
	if !ok || id != "id-a" {
		t.Errorf("proj-a: got (%q, %v), want (id-a, true)", id, ok)
	}
	id, ok = result.GetProject("proj-b")
	if !ok || id != "id-b" {
		t.Errorf("proj-b: got (%q, %v), want (id-b, true)", id, ok)
	}
}

func TestCloseNilAuthenticator(t *testing.T) {
	var a *Authenticator
	a.Close()
}

// ---------------------------------------------------------------------------
// HTTP Middleware
// ---------------------------------------------------------------------------

func TestHTTPMiddlewareMissingHeaders(t *testing.T) {
	a := &Authenticator{
		cache: newCache(5*time.Minute, 1*time.Hour),
		log:   slog.Default(),
	}

	handler := a.HTTPMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Fatal("handler should not be called for missing headers")
	}))

	t.Run("BothMissing", func(t *testing.T) {
		req := httptest.NewRequest("POST", "/v1/traces", nil)
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)

		if rec.Code != http.StatusUnauthorized {
			t.Errorf("status = %d, want 401", rec.Code)
		}
	})

	t.Run("OnlyApiKey", func(t *testing.T) {
		req := httptest.NewRequest("POST", "/v1/traces", nil)
		req.Header.Set("X-Api-Key", "some-key")
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)

		if rec.Code != http.StatusUnauthorized {
			t.Errorf("status = %d, want 401", rec.Code)
		}
	})

	t.Run("OnlySecretKey", func(t *testing.T) {
		req := httptest.NewRequest("POST", "/v1/traces", nil)
		req.Header.Set("X-Secret-Key", "some-secret")
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)

		if rec.Code != http.StatusUnauthorized {
			t.Errorf("status = %d, want 401", rec.Code)
		}
	})
}

func TestHTTPMiddlewareNilAuthenticator(t *testing.T) {
	var a *Authenticator
	var called bool
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called = true
		w.WriteHeader(http.StatusOK)
	})

	handler := a.HTTPMiddleware(next)

	req := httptest.NewRequest("GET", "/", nil)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if !called {
		t.Fatal("nil authenticator middleware must pass through to next handler")
	}
	if rec.Code != http.StatusOK {
		t.Errorf("status = %d, want 200", rec.Code)
	}
}

func TestHTTPMiddlewareContextValues(t *testing.T) {
	a := &Authenticator{
		cache: newCache(5*time.Minute, 1*time.Hour),
		log:   slog.Default(),
	}

	apiKey := "test-api-key"
	secretKey := "test-secret-key"
	ck := CacheKey(apiKey, secretKey)
	result := &ResolveResult{
		OrgID:       "org-ctx",
		WorkspaceID: "ws-ctx",
		Projects:    map[string]string{},
	}
	a.cache.putPositive(ck, result)

	var gotResult *ResolveResult
	var gotCacheKey string

	handler := a.HTTPMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotResult = FromContext(r.Context())
		gotCacheKey = CacheKeyFromContext(r.Context())
		w.WriteHeader(http.StatusOK)
	}))

	req := httptest.NewRequest("POST", "/v1/traces", nil)
	req.Header.Set("X-Api-Key", apiKey)
	req.Header.Set("X-Secret-Key", secretKey)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	if gotResult == nil || gotResult.OrgID != "org-ctx" {
		t.Errorf("FromContext returned %v, want result with OrgID=org-ctx", gotResult)
	}
	if gotCacheKey != ck {
		t.Errorf("CacheKeyFromContext = %q, want %q", gotCacheKey, ck)
	}
}

func TestHTTPMiddlewareBasicAuth(t *testing.T) {
	apiKey := "basic-api-key"
	secretKey := "basic-secret-key"
	ck := CacheKey(apiKey, secretKey)

	a := &Authenticator{
		cache: newCache(5*time.Minute, 1*time.Hour),
		log:   slog.Default(),
	}
	result := &ResolveResult{OrgID: "org-basic", Projects: map[string]string{}}
	a.cache.putPositive(ck, result)

	var gotResult *ResolveResult
	handler := a.HTTPMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotResult = FromContext(r.Context())
		w.WriteHeader(http.StatusOK)
	}))

	encoded := base64.StdEncoding.EncodeToString([]byte(apiKey + ":" + secretKey))
	req := httptest.NewRequest("POST", "/v1/traces", nil)
	req.Header.Set("Authorization", "Basic "+encoded)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	if gotResult == nil || gotResult.OrgID != "org-basic" {
		t.Errorf("FromContext = %v, want OrgID=org-basic", gotResult)
	}
}

func TestExtractCredentialsNativeHeaders(t *testing.T) {
	req := httptest.NewRequest("POST", "/v1/traces", nil)
	req.Header.Set("X-Api-Key", "k1")
	req.Header.Set("X-Secret-Key", "s1")
	api, sec := extractCredentials(req)
	if api != "k1" || sec != "s1" {
		t.Errorf("got (%q, %q), want (k1, s1)", api, sec)
	}
}

func TestExtractCredentialsBasicAuth(t *testing.T) {
	req := httptest.NewRequest("POST", "/v1/traces", nil)
	encoded := base64.StdEncoding.EncodeToString([]byte("mykey:mysecret"))
	req.Header.Set("Authorization", "Basic "+encoded)
	api, sec := extractCredentials(req)
	if api != "mykey" || sec != "mysecret" {
		t.Errorf("got (%q, %q), want (mykey, mysecret)", api, sec)
	}
}

func TestExtractCredentialsSecretWithColon(t *testing.T) {
	// secret_key may contain colons — SplitN(..., 2) handles this
	req := httptest.NewRequest("POST", "/v1/traces", nil)
	encoded := base64.StdEncoding.EncodeToString([]byte("mykey:sec:ret"))
	req.Header.Set("Authorization", "Basic "+encoded)
	api, sec := extractCredentials(req)
	if api != "mykey" || sec != "sec:ret" {
		t.Errorf("got (%q, %q), want (mykey, sec:ret)", api, sec)
	}
}

func TestExtractCredentialsNoneProvided(t *testing.T) {
	req := httptest.NewRequest("POST", "/v1/traces", nil)
	api, sec := extractCredentials(req)
	if api != "" || sec != "" {
		t.Errorf("got (%q, %q), want empty", api, sec)
	}
}

// ---------------------------------------------------------------------------
// gRPC Interceptor
// ---------------------------------------------------------------------------

func TestGRPCInterceptorMissingMetadata(t *testing.T) {
	a := &Authenticator{
		cache: newCache(5*time.Minute, 1*time.Hour),
		log:   slog.Default(),
	}
	interceptor := a.GRPCInterceptor()

	handler := func(ctx context.Context, req any) (any, error) {
		t.Fatal("handler should not be called for missing metadata")
		return nil, nil
	}

	_, err := interceptor(context.Background(), nil, &grpc.UnaryServerInfo{}, handler)
	if err == nil {
		t.Fatal("expected error for missing metadata")
	}
	if status.Code(err) != codes.Unauthenticated {
		t.Errorf("code = %v, want Unauthenticated", status.Code(err))
	}
}

func TestGRPCInterceptorMissingKeys(t *testing.T) {
	a := &Authenticator{
		cache: newCache(5*time.Minute, 1*time.Hour),
		log:   slog.Default(),
	}
	interceptor := a.GRPCInterceptor()

	handler := func(ctx context.Context, req any) (any, error) {
		t.Fatal("handler should not be called for missing keys")
		return nil, nil
	}

	t.Run("EmptyMetadata", func(t *testing.T) {
		ctx := metadata.NewIncomingContext(context.Background(), metadata.New(map[string]string{}))
		_, err := interceptor(ctx, nil, &grpc.UnaryServerInfo{}, handler)
		if err == nil {
			t.Fatal("expected error for empty metadata")
		}
		if status.Code(err) != codes.Unauthenticated {
			t.Errorf("code = %v, want Unauthenticated", status.Code(err))
		}
	})

	t.Run("OnlyApiKey", func(t *testing.T) {
		ctx := metadata.NewIncomingContext(context.Background(), metadata.New(map[string]string{
			"x-api-key": "some-key",
		}))
		_, err := interceptor(ctx, nil, &grpc.UnaryServerInfo{}, handler)
		if err == nil {
			t.Fatal("expected error for missing secret key")
		}
		if status.Code(err) != codes.Unauthenticated {
			t.Errorf("code = %v, want Unauthenticated", status.Code(err))
		}
	})

	t.Run("OnlySecretKey", func(t *testing.T) {
		ctx := metadata.NewIncomingContext(context.Background(), metadata.New(map[string]string{
			"x-secret-key": "some-secret",
		}))
		_, err := interceptor(ctx, nil, &grpc.UnaryServerInfo{}, handler)
		if err == nil {
			t.Fatal("expected error for missing api key")
		}
		if status.Code(err) != codes.Unauthenticated {
			t.Errorf("code = %v, want Unauthenticated", status.Code(err))
		}
	})
}

func TestGRPCInterceptorNilAuthenticator(t *testing.T) {
	var a *Authenticator
	interceptor := a.GRPCInterceptor()

	var called bool
	handler := func(ctx context.Context, req any) (any, error) {
		called = true
		return "ok", nil
	}

	resp, err := interceptor(context.Background(), nil, &grpc.UnaryServerInfo{}, handler)
	if err != nil {
		t.Fatalf("nil authenticator interceptor must not error, got: %v", err)
	}
	if !called {
		t.Fatal("nil authenticator interceptor must call handler")
	}
	if resp != "ok" {
		t.Errorf("response = %v, want ok", resp)
	}
}

func TestGRPCInterceptorContextValues(t *testing.T) {
	a := &Authenticator{
		cache: newCache(5*time.Minute, 1*time.Hour),
		log:   slog.Default(),
	}

	apiKey := "grpc-api-key"
	secretKey := "grpc-secret-key"
	ck := CacheKey(apiKey, secretKey)
	result := &ResolveResult{
		OrgID:       "org-grpc",
		WorkspaceID: "ws-grpc",
		Projects:    map[string]string{},
	}
	a.cache.putPositive(ck, result)

	interceptor := a.GRPCInterceptor()

	var gotResult *ResolveResult
	var gotCacheKey string

	handler := func(ctx context.Context, req any) (any, error) {
		gotResult = FromContext(ctx)
		gotCacheKey = CacheKeyFromContext(ctx)
		return "ok", nil
	}

	ctx := metadata.NewIncomingContext(context.Background(), metadata.New(map[string]string{
		"x-api-key":    apiKey,
		"x-secret-key": secretKey,
	}))

	_, err := interceptor(ctx, nil, &grpc.UnaryServerInfo{}, handler)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if gotResult == nil || gotResult.OrgID != "org-grpc" {
		t.Errorf("FromContext returned %v, want result with OrgID=org-grpc", gotResult)
	}
	if gotCacheKey != ck {
		t.Errorf("CacheKeyFromContext = %q, want %q", gotCacheKey, ck)
	}
}

// ---------------------------------------------------------------------------
// Context helpers with empty context
// ---------------------------------------------------------------------------

func TestFromContextEmpty(t *testing.T) {
	if r := FromContext(context.Background()); r != nil {
		t.Errorf("FromContext on empty ctx = %v, want nil", r)
	}
}

func TestCacheKeyFromContextEmpty(t *testing.T) {
	if k := CacheKeyFromContext(context.Background()); k != "" {
		t.Errorf("CacheKeyFromContext on empty ctx = %q, want empty", k)
	}
}

// ---------------------------------------------------------------------------
// StampResourceAttrs
// ---------------------------------------------------------------------------

func TestStampResourceAttrsNilResult(t *testing.T) {
	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("project_name", "proj")
	rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty().SetName("span-1")

	dropped, err := StampResourceAttrs(context.Background(), nil, "ck", traces, nil)
	if err != nil {
		t.Fatalf("nil result must not error, got: %v", err)
	}
	if dropped != 0 {
		t.Errorf("dropped = %d, want 0", dropped)
	}

	raw := traces.ResourceSpans().At(0).Resource().Attributes().AsRaw()
	if _, ok := raw["fi.project_id"]; ok {
		t.Error("fi.project_id should not be set when result is nil")
	}
}

func TestStampResourceAttrsAlreadyHasProjectID(t *testing.T) {
	// A span with only fi.project_id and no project_name must be rejected;
	// client-supplied fi.project_id is not trusted.
	a := &Authenticator{
		cache: newCache(5*time.Minute, 1*time.Hour),
		log:   slog.Default(),
	}

	result := &ResolveResult{
		OrgID:    "org-1",
		Projects: map[string]string{},
	}
	ck := "stamp-key"
	a.cache.putPositive(ck, result)

	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("fi.project_id", "pre-existing-id")
	rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty().SetName("span-1")

	_, err := StampResourceAttrs(context.Background(), a, ck, traces, result)
	if err == nil {
		t.Fatal("expected error: span has fi.project_id but no project_name")
	}
}

func TestStampResourceAttrsNoProjectNameNoProjectID(t *testing.T) {
	a := &Authenticator{
		cache: newCache(5*time.Minute, 1*time.Hour),
		log:   slog.Default(),
	}

	result := &ResolveResult{
		OrgID:    "org-1",
		Projects: map[string]string{},
	}
	ck := "stamp-key"
	a.cache.putPositive(ck, result)

	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty().SetName("span-1")

	_, err := StampResourceAttrs(context.Background(), a, ck, traces, result)
	if err == nil {
		t.Fatal("expected error when ResourceSpan has neither project_name nor fi.project_id")
	}
}

func TestStampResourceAttrsValidProjectName(t *testing.T) {
	a := &Authenticator{
		cache: newCache(5*time.Minute, 1*time.Hour),
		log:   slog.Default(),
	}

	result := &ResolveResult{
		OrgID:    "org-stamp",
		Projects: map[string]string{"my-project": "proj-id-123"},
	}
	ck := "stamp-valid-key"
	a.cache.putPositive(ck, result)

	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("project_name", "my-project")
	rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty().SetName("span-1")

	dropped, err := StampResourceAttrs(context.Background(), a, ck, traces, result)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if dropped != 0 {
		t.Errorf("dropped = %d, want 0", dropped)
	}

	raw := traces.ResourceSpans().At(0).Resource().Attributes().AsRaw()
	if raw["fi.project_id"] != "proj-id-123" {
		t.Errorf("fi.project_id = %v, want proj-id-123", raw["fi.project_id"])
	}
	if raw["fi.org_id"] != "org-stamp" {
		t.Errorf("fi.org_id = %v, want org-stamp", raw["fi.org_id"])
	}
}

func TestStampResourceAttrsMixedSpans(t *testing.T) {
	// Two ResourceSpans: one with a resolvable project, one with an unresolvable
	// project — the unresolvable one should be dropped.
	a := &Authenticator{
		cache: newCache(5*time.Minute, 1*time.Hour),
		log:   slog.Default(),
	}

	// Both projects are known to the auth result — "projB" has an empty ID
	// which simulates a project that resolved to nothing (will be dropped).
	result := &ResolveResult{
		OrgID:    "org-mixed",
		Projects: map[string]string{"projA": "id-A", "projB": ""},
	}
	ck := "stamp-mixed-key"
	a.cache.putPositive(ck, result)

	traces := ptrace.NewTraces()

	rs0 := traces.ResourceSpans().AppendEmpty()
	rs0.Resource().Attributes().PutStr("project_name", "projA")
	rs0.ScopeSpans().AppendEmpty().Spans().AppendEmpty().SetName("span-0")

	rs1 := traces.ResourceSpans().AppendEmpty()
	rs1.Resource().Attributes().PutStr("project_name", "projB")
	rs1.ScopeSpans().AppendEmpty().Spans().AppendEmpty().SetName("span-1")

	dropped, err := StampResourceAttrs(context.Background(), a, ck, traces, result)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if dropped != 1 {
		t.Errorf("dropped = %d, want 1 (projB unresolvable)", dropped)
	}

	raw0 := traces.ResourceSpans().At(0).Resource().Attributes().AsRaw()
	if raw0["fi.project_id"] != "id-A" {
		t.Errorf("rs0 fi.project_id = %v, want id-A", raw0["fi.project_id"])
	}
	if raw0["fi.org_id"] != "org-mixed" {
		t.Errorf("rs0 fi.org_id = %v, want org-mixed", raw0["fi.org_id"])
	}
}

func TestStampResourceAttrsEmptyTraces(t *testing.T) {
	result := &ResolveResult{
		OrgID:    "org-1",
		Projects: map[string]string{},
	}
	traces := ptrace.NewTraces()

	dropped, err := StampResourceAttrs(context.Background(), nil, "ck", traces, result)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if dropped != 0 {
		t.Errorf("dropped = %d, want 0", dropped)
	}
}

// ---------------------------------------------------------------------------
// ResolveResult thread safety
// ---------------------------------------------------------------------------

func TestResolveResultConcurrentAccess(t *testing.T) {
	r := &ResolveResult{
		OrgID:    "org-concurrent",
		Projects: map[string]string{},
	}

	const goroutines = 50
	const iterations = 100

	var wg sync.WaitGroup
	wg.Add(goroutines * 2)

	for i := 0; i < goroutines; i++ {
		go func(id int) {
			defer wg.Done()
			for j := 0; j < iterations; j++ {
				name := "proj-" + string(rune('A'+id%26))
				r.SetProject(name, "id-"+name)
			}
		}(i)

		go func(id int) {
			defer wg.Done()
			for j := 0; j < iterations; j++ {
				name := "proj-" + string(rune('A'+id%26))
				r.GetProject(name)
			}
		}(i)
	}

	wg.Wait()
}

func TestResolveResultConcurrentSetProjects(t *testing.T) {
	r := &ResolveResult{
		OrgID:    "org-concurrent",
		Projects: map[string]string{},
	}

	const goroutines = 20

	var wg sync.WaitGroup
	wg.Add(goroutines)

	for i := 0; i < goroutines; i++ {
		go func(id int) {
			defer wg.Done()
			m := map[string]string{
				"batch-a": "id-a",
				"batch-b": "id-b",
			}
			r.SetProjects(m)
		}(i)
	}

	wg.Wait()

	idA, okA := r.GetProject("batch-a")
	idB, okB := r.GetProject("batch-b")
	if !okA || idA != "id-a" {
		t.Errorf("batch-a: got (%q, %v), want (id-a, true)", idA, okA)
	}
	if !okB || idB != "id-b" {
		t.Errorf("batch-b: got (%q, %v), want (id-b, true)", idB, okB)
	}
}

func TestResolveResultMissingProjects(t *testing.T) {
	r := &ResolveResult{
		OrgID:    "org-1",
		Projects: map[string]string{"known": "id-known"},
	}

	missing := r.MissingProjects([]string{"known", "unknown1", "unknown2"})
	if len(missing) != 2 {
		t.Fatalf("missing = %v, want [unknown1, unknown2]", missing)
	}

	found := map[string]bool{}
	for _, name := range missing {
		found[name] = true
	}
	if !found["unknown1"] || !found["unknown2"] {
		t.Errorf("missing = %v, want unknown1 and unknown2", missing)
	}
}

// ---------------------------------------------------------------------------
// UsageEmitter
// ---------------------------------------------------------------------------

func TestNewUsageEmitterNilRedis(t *testing.T) {
	u := NewUsageEmitter(nil, nil, slog.Default())
	if u != nil {
		t.Fatal("NewUsageEmitter with nil redis must return nil")
	}
}

func TestUsageEmitterEmitIngestionNil(t *testing.T) {
	var u *UsageEmitter
	u.EmitIngestion("org-1", 10, 50, 1024, "")
}

func TestBillingEventIDDeterministic(t *testing.T) {
	a := billingEventID("trace-abc")
	if a != billingEventID("trace-abc") {
		t.Error("same dedupKey must yield the same event_id (re-poll must dedup, even across a mode flip)")
	}
	if billingEventID("trace-xyz") == a {
		t.Error("different dedupKey must yield distinct ids")
	}
}

func TestBillingEventIDEmptyKeyIsRandom(t *testing.T) {
	if billingEventID("") == billingEventID("") {
		t.Error("empty dedupKey must fall back to a random event_id (SDK batches)")
	}
}

// TestEmitIngestionEventIDDedupViaRedis is an end-to-end check of the emit path
// against a real (in-memory) Redis stream: a re-poll of the same call must land
// the SAME event_id (so the consumer dedups), a different call a different id,
// and an SDK batch (empty dedupKey) a random id.
func TestEmitIngestionEventIDDedupViaRedis(t *testing.T) {
	mr, err := miniredis.Run()
	if err != nil {
		t.Fatal(err)
	}
	defer mr.Close()
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	u := NewUsageEmitter(rdb, nil, slog.Default())
	setTracingMode(t, rdb, "org-1", "events") // events mode → tracing_event is emitted

	// Order: same call twice (re-poll), a different call, then an SDK batch twice.
	u.EmitIngestion("org-1", 1, 1, 100, "trace-A")
	u.EmitIngestion("org-1", 1, 1, 100, "trace-A")
	u.EmitIngestion("org-1", 1, 1, 100, "trace-B")
	u.EmitIngestion("org-1", 2, 5, 200, "")
	u.EmitIngestion("org-1", 2, 5, 200, "")

	msgs, err := rdb.XRange(context.Background(), usageStreamKey, "-", "+").Result()
	if err != nil {
		t.Fatal(err)
	}
	var ids []string
	for _, m := range msgs {
		if m.Values["event_type"] == "tracing_event" {
			ids = append(ids, m.Values["event_id"].(string))
		}
	}
	if len(ids) != 5 {
		t.Fatalf("want 5 tracing_event entries, got %d", len(ids))
	}
	if ids[0] != ids[1] {
		t.Errorf("re-poll of the same call must reuse event_id: %s vs %s", ids[0], ids[1])
	}
	if ids[0] == ids[2] {
		t.Error("a different call must get a different event_id")
	}
	if ids[3] == ids[4] {
		t.Error("SDK batch (empty dedupKey) must get random event_ids")
	}
}

// setTracingMode seeds the org's billing-mode cache key so EmitIngestion
// resolves a known mode without a Postgres pool.
func setTracingMode(t *testing.T, rdb *redis.Client, orgID, mode string) {
	t.Helper()
	if err := rdb.Set(context.Background(), "tracing_billing_mode:"+orgID, mode, 0).Err(); err != nil {
		t.Fatal(err)
	}
}

// usageStreamEvents returns every event currently on the usage stream.
func usageStreamEvents(t *testing.T, rdb *redis.Client) []map[string]any {
	t.Helper()
	msgs, err := rdb.XRange(context.Background(), usageStreamKey, "-", "+").Result()
	if err != nil {
		t.Fatal(err)
	}
	out := make([]map[string]any, 0, len(msgs))
	for _, m := range msgs {
		out = append(out, m.Values)
	}
	return out
}

// storage mode → one observe_add (amount = payloadBytes), no tracing_event.
func TestEmitIngestionStorageModeEmitsOnlyStorage(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	u := NewUsageEmitter(rdb, nil, slog.Default())
	setTracingMode(t, rdb, "org-s", "storage")

	u.EmitIngestion("org-s", 3, 7, 500, "trace-1")

	evs := usageStreamEvents(t, rdb)
	if len(evs) != 1 {
		t.Fatalf("storage mode must emit exactly one event, got %d: %v", len(evs), evs)
	}
	if evs[0]["event_type"] != "observe_add" {
		t.Errorf("storage mode must emit observe_add, got %v", evs[0]["event_type"])
	}
	if evs[0]["amount"] != "500" {
		t.Errorf("observe_add amount must be payloadBytes=500, got %v", evs[0]["amount"])
	}
}

// events mode → one tracing_event (amount = traces+spans), no observe_add.
func TestEmitIngestionEventsModeEmitsOnlyTracing(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	u := NewUsageEmitter(rdb, nil, slog.Default())
	setTracingMode(t, rdb, "org-e", "events")

	u.EmitIngestion("org-e", 3, 7, 500, "trace-1")

	evs := usageStreamEvents(t, rdb)
	if len(evs) != 1 {
		t.Fatalf("events mode must emit exactly one event, got %d: %v", len(evs), evs)
	}
	if evs[0]["event_type"] != "tracing_event" {
		t.Errorf("events mode must emit tracing_event, got %v", evs[0]["event_type"])
	}
	if evs[0]["amount"] != "10" {
		t.Errorf("tracing_event amount must be traces+spans=10, got %v", evs[0]["amount"])
	}
}

// Unknown mode (no cache key, no PG) defaults to storage — matches Python.
func TestEmitIngestionDefaultsToStorageWhenModeUnknown(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	u := NewUsageEmitter(rdb, nil, slog.Default())

	u.EmitIngestion("org-x", 3, 7, 500, "")

	evs := usageStreamEvents(t, rdb)
	if len(evs) != 1 || evs[0]["event_type"] != "observe_add" {
		t.Fatalf("unknown mode must default to storage (observe_add only), got %v", evs)
	}
}

// events-mode tracing amount is traces+spans, and payloadBytes is ignored.
func TestEmitIngestionEventsModeAmountIncludesSpans(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	u := NewUsageEmitter(rdb, nil, slog.Default())
	setTracingMode(t, rdb, "org-b", "events")

	u.EmitIngestion("org-b", 2, 5, 999, "trace-1")

	evs := usageStreamEvents(t, rdb)
	if len(evs) != 1 {
		t.Fatalf("events mode must emit exactly one event (bytes ignored), got %d: %v", len(evs), evs)
	}
	if evs[0]["event_type"] != "tracing_event" || evs[0]["amount"] != "7" {
		t.Errorf("want tracing_event amount=7 (2 traces + 5 spans), got type=%v amount=%v",
			evs[0]["event_type"], evs[0]["amount"])
	}
}

// events mode with spans=0 pins the other boundary of the traces+spans sum.
func TestEmitIngestionEventsModeTracesOnly(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	u := NewUsageEmitter(rdb, nil, slog.Default())
	setTracingMode(t, rdb, "org-t", "events")

	u.EmitIngestion("org-t", 2, 0, 999, "trace-1")

	evs := usageStreamEvents(t, rdb)
	if len(evs) != 1 {
		t.Fatalf("events mode must emit exactly one event, got %d: %v", len(evs), evs)
	}
	if evs[0]["event_type"] != "tracing_event" || evs[0]["amount"] != "2" {
		t.Errorf("want tracing_event amount=2 (2 traces + 0 spans), got type=%v amount=%v",
			evs[0]["event_type"], evs[0]["amount"])
	}
}

// ---------------------------------------------------------------------------
// formatIndices
// ---------------------------------------------------------------------------

func TestFormatIndicesShort(t *testing.T) {
	got := formatIndices([]int{0, 3, 7})
	want := "[0 3 7]"
	if got != want {
		t.Errorf("formatIndices = %q, want %q", got, want)
	}
}

// ---------------------------------------------------------------------------
// WatchRevocations
// ---------------------------------------------------------------------------

func TestWatchRevocationsNilAuthenticator(t *testing.T) {
	// Must not panic.
	var a *Authenticator
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	a.WatchRevocations(ctx) // returns immediately
}

func TestWatchRevocationsNilRdb(t *testing.T) {
	a := &Authenticator{
		cache: newCache(5*time.Minute, 1*time.Hour),
		log:   slog.Default(),
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	a.WatchRevocations(ctx) // returns immediately — no rdb
}

func TestWatchRevocationsEvictsOnPublish(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	t.Cleanup(func() { rdb.Close() })

	apiKey, secretKey := "evict-api-key", "evict-secret-key"
	ck := CacheKey(apiKey, secretKey)

	a := &Authenticator{
		cache: newCache(5*time.Minute, 1*time.Hour),
		rdb:   rdb,
		log:   slog.Default(),
	}
	a.cache.putPositive(ck, &ResolveResult{OrgID: "org-evict", Projects: map[string]string{}})

	if _, status := a.cache.get(ck); status != "fresh" {
		t.Fatal("expected fresh cache entry before revocation")
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Start watcher first, then wait for the subscription to register before publishing.
	go a.WatchRevocations(ctx)
	time.Sleep(100 * time.Millisecond)

	if err := rdb.Publish(ctx, revocationChannel, ck).Err(); err != nil {
		t.Fatalf("publish failed: %v", err)
	}

	// Poll for eviction.
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if _, status := a.cache.get(ck); status == "miss" {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("cache entry was not evicted after revocation publish")
}

func TestWatchRevocationsExitsOnContextCancel(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	t.Cleanup(func() { rdb.Close() })

	a := &Authenticator{
		cache: newCache(5*time.Minute, 1*time.Hour),
		rdb:   rdb,
		log:   slog.Default(),
	}

	ctx, cancel := context.WithCancel(context.Background())

	done := make(chan struct{})
	go func() {
		a.WatchRevocations(ctx)
		close(done)
	}()

	cancel()

	select {
	case <-done:
		// WatchRevocations exited cleanly.
	case <-time.After(2 * time.Second):
		t.Fatal("WatchRevocations did not exit after context cancellation")
	}
}

func TestWatchRevocationsIgnoresUnrelatedKeys(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	t.Cleanup(func() { rdb.Close() })

	ck := CacheKey("key-a", "secret-a")
	otherCK := CacheKey("key-b", "secret-b")

	a := &Authenticator{
		cache: newCache(5*time.Minute, 1*time.Hour),
		rdb:   rdb,
		log:   slog.Default(),
	}
	a.cache.putPositive(ck, &ResolveResult{OrgID: "org-a", Projects: map[string]string{}})
	a.cache.putPositive(otherCK, &ResolveResult{OrgID: "org-b", Projects: map[string]string{}})

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	go a.WatchRevocations(ctx)
	time.Sleep(100 * time.Millisecond)

	// Revoke only key-a.
	if err := rdb.Publish(ctx, revocationChannel, ck).Err(); err != nil {
		t.Fatalf("publish failed: %v", err)
	}

	time.Sleep(100 * time.Millisecond)

	// key-a should be evicted.
	if _, status := a.cache.get(ck); status != "miss" {
		t.Error("key-a should have been evicted")
	}
	// key-b must remain.
	if _, status := a.cache.get(otherCK); status != "fresh" {
		t.Error("key-b should still be fresh")
	}
}

func TestFormatIndicesLong(t *testing.T) {
	got := formatIndices([]int{0, 1, 2, 3, 4, 5, 6, 7})
	if got == "" {
		t.Fatal("formatIndices returned empty string")
	}
	if len(got) < 10 {
		t.Errorf("formatIndices should include truncation marker, got %q", got)
	}
}
