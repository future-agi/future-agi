package futureagi

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestTenantFutureAGIPlatformDestination(t *testing.T) {
	t.Setenv("FI_API_KEY", "operator-key")
	t.Setenv("FI_SECRET_KEY", "operator-secret")
	var tenantHits, platformHits atomic.Int32
	tenant := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		tenantHits.Add(1)
		io.WriteString(w, `{"result":[]}`)
	}))
	defer tenant.Close()
	t.Setenv("FI_BASE_URL", tenant.URL)
	platform := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		platformHits.Add(1)
		if r.URL.Path != "/sdk/api/v1/eval/" || r.Method != http.MethodPost {
			t.Errorf("unexpected platform request: %s %s", r.Method, r.URL)
		}
		if r.Header.Get("X-Api-Key") != "tenant-key" || r.Header.Get("X-Secret-Key") != "tenant-secret" {
			t.Error("platform request did not use tenant credentials")
		}
		io.WriteString(w, `{"result":[]}`)
	}))
	defer platform.Close()

	g := NewTenant("check", map[string]interface{}{
		"platform": true, "base_url": tenant.URL, "eval_id": "safety",
		"api_key": "tenant-key", "secret_key": "tenant-secret", "timeout": "2s",
	}, false, platform.URL)
	defer g.client.CloseIdleConnections()
	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
	if !result.Pass || platformHits.Load() != 1 || tenantHits.Load() != 0 {
		t.Fatalf("result=%+v, platform hits=%d, tenant hits=%d", result, platformHits.Load(), tenantHits.Load())
	}
	if g.client.Timeout != 2*time.Second {
		t.Fatalf("platform timeout=%s, want 2s", g.client.Timeout)
	}
}

func TestTenantFutureAGIPlatformRedirects(t *testing.T) {
	for _, sameOrigin := range []bool{false, true} {
		t.Run(fmt.Sprintf("same_origin=%v", sameOrigin), func(t *testing.T) {
			var foreignHits, resultHits atomic.Int32
			foreign := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				foreignHits.Add(1)
				io.WriteString(w, `{"result":[]}`)
			}))
			defer foreign.Close()
			platform := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/sdk/api/v1/eval/" {
					destination := foreign.URL + "/result"
					if sameOrigin {
						destination = "/result"
					}
					http.Redirect(w, r, destination, http.StatusTemporaryRedirect)
					return
				}
				resultHits.Add(1)
				if r.Header.Get("X-Api-Key") != "tenant-key" || r.Header.Get("X-Secret-Key") != "tenant-secret" {
					t.Error("same-origin redirect lost tenant credentials")
				}
				io.WriteString(w, `{"result":[]}`)
			}))
			defer platform.Close()
			g := NewTenant("check", map[string]interface{}{
				"platform": true, "base_url": platform.URL, "eval_id": "safety",
				"api_key": "tenant-key", "secret_key": "tenant-secret",
			}, false, platform.URL)
			defer g.client.CloseIdleConnections()
			result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
			if result.Pass != sameOrigin || foreignHits.Load() != 0 {
				t.Fatalf("result=%+v, foreign hits=%d", result, foreignHits.Load())
			}
			if sameOrigin && resultHits.Load() != 1 {
				t.Fatalf("same-origin result hits=%d, want 1", resultHits.Load())
			}
			if !sameOrigin && !strings.Contains(result.Message, "control-plane origin") {
				t.Fatalf("want cross-origin redirect refusal, got %+v", result)
			}
		})
	}
}

func TestTenantFutureAGIPlatformRedirectOriginBoundaries(t *testing.T) {
	for _, tc := range []struct{ origin, destination string }{
		{"http://api.example", "http://apİ.example/result"},
		{"http://127.0.0.1:8000", "http://127.0.0.2:8000/result"},
		{"https://api.example", "http://api.example/result"},
	} {
		t.Run(tc.destination, func(t *testing.T) {
			g := NewTenant("check", map[string]interface{}{
				"platform": true, "base_url": tc.origin, "eval_id": "safety",
				"api_key": "tenant-key", "secret_key": "tenant-secret",
			}, true, tc.origin)
			calls := 0
			g.client.Transport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
				calls++
				if calls == 1 {
					return &http.Response{StatusCode: http.StatusTemporaryRedirect, Header: http.Header{"Location": {tc.destination}}, Body: io.NopCloser(strings.NewReader(""))}, nil
				}
				return &http.Response{StatusCode: http.StatusOK, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(`{"result":[]}`))}, nil
			})
			result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
			if result.Pass || calls != 1 || !strings.Contains(result.Message, "control-plane origin") {
				t.Fatalf("result=%+v, transport calls=%d", result, calls)
			}
		})
	}
}

func TestTenantFutureAGIGuardedDestinations(t *testing.T) {
	for _, allowPrivate := range []bool{false, true} {
		for _, platform := range []bool{false, true} {
			for _, host := range []string{"127.0.0.1", "10.1.2.3", "169.254.169.254"} {
				t.Run(fmt.Sprintf("%s/private=%v/platform=%v", host, allowPrivate, platform), func(t *testing.T) {
					// No operator URL: even platform:true must use the tenant guard.
					g := NewTenant("check", map[string]interface{}{
						"platform": platform, "base_url": "http://" + host + ":80", "eval_id": "safety",
						"api_key": "tenant-key", "secret_key": "tenant-secret", "timeout": "100ms",
					}, allowPrivate, "")
					defer g.client.CloseIdleConnections()
					result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
					wantBlocked := host != "10.1.2.3" || !allowPrivate
					if result.Pass || strings.Contains(result.Message, "destination address is not allowed") != wantBlocked {
						t.Fatalf("want blocked=%v, got %+v", wantBlocked, result)
					}
					transport, ok := g.client.Transport.(*http.Transport)
					if !ok || transport.Proxy != nil || g.client.Timeout != 100*time.Millisecond {
						t.Fatal("tenant path must use the direct guarded transport and configured timeout")
					}
				})
			}
		}
	}
}
