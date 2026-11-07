package external

import (
	"context"
	"io"
	"net"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestTenantExternalEnvironmentIsLiteral(t *testing.T) {
	t.Setenv("GUARDRAIL_TEST_KEY", "operator-secret")
	t.Setenv("GUARDRAIL_TEST_ENDPOINT", "https://operator.example/guard")
	cfg := map[string]interface{}{
		"provider": "lakera", "api_key": "${GUARDRAIL_TEST_KEY}", "endpoint": "https://tenant.example/guard",
	}
	for _, tenant := range []bool{false, true} {
		var g *ExternalGuardrail
		want := "Bearer operator-secret"
		if tenant {
			g = NewTenant("check", cfg, false)
			want = "Bearer ${GUARDRAIL_TEST_KEY}"
		} else {
			g = New("check", cfg)
		}
		var sent bool
		g.client.Transport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
			sent = true
			if got := r.Header.Get("Authorization"); got != want {
				t.Errorf("tenant=%v sent authorization %q, want %q", tenant, got, want)
			}
			return &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(`{"flagged":false}`)), Header: make(http.Header)}, nil
		})
		result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
		if !sent || !result.Pass {
			t.Fatalf("tenant=%v request sent=%v, result=%+v", tenant, sent, result)
		}
	}
	if cfg["api_key"] != "${GUARDRAIL_TEST_KEY}" {
		t.Fatal("constructors mutated the shared tenant configuration")
	}
	cfg["endpoint"] = "${GUARDRAIL_TEST_ENDPOINT}"
	if got := NewTenant("check", cfg, false).adapter.(*lakeraAdapter).endpoint; got != "${GUARDRAIL_TEST_ENDPOINT}" {
		t.Fatalf("tenant endpoint expanded to %q", got)
	}
	if got := New("check", cfg).adapter.(*lakeraAdapter).endpoint; got != "https://operator.example/guard" {
		t.Fatalf("operator endpoint did not expand: %q", got)
	}
}

func TestTenantExternalDialTimeout(t *testing.T) {
	// Hold DNS in memory rather than relying on a slow/unreachable network.
	originalResolver := net.DefaultResolver
	net.DefaultResolver = &net.Resolver{PreferGo: true, Dial: func(ctx context.Context, _, _ string) (net.Conn, error) {
		<-ctx.Done()
		return nil, ctx.Err()
	}}
	t.Cleanup(func() { net.DefaultResolver = originalResolver })

	for _, tc := range []struct {
		name       string
		provider   string
		timeout    string
		token      bool
		parentEnds bool
	}{
		{"long-check", "lakera", "20s", false, true},
		{"short-check", "lakera", "50ms", false, false},
		{"oauth", "crowdstrike", "50ms", true, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := NewTenant("check", map[string]interface{}{"provider": tc.provider, "timeout": tc.timeout}, false)
			client := g.client
			if tc.token {
				client = g.adapter.(*crowdstrikeAdapter).client
				if client.Timeout != 10*time.Second {
					t.Fatalf("token client timeout = %s, want 10s", client.Timeout)
				}
			}
			// A configured 20s check and 10s token dial must survive past the old
			// fixed 5s dial timeout. A 50ms check must end before this parent.
			parentTimeout := 6 * time.Second
			if !tc.parentEnds {
				parentTimeout = time.Second
			}
			ctx, cancel := context.WithTimeout(context.Background(), parentTimeout)
			defer cancel()
			conn, err := client.Transport.(*http.Transport).DialContext(ctx, "tcp", "tenant-timeout.invalid:443")
			if conn != nil {
				conn.Close()
			}
			if err == nil || (ctx.Err() != nil) != tc.parentEnds {
				t.Fatalf("dial error=%v, parent error=%v, want parent ended=%v (client timeout=%s)", err, ctx.Err(), tc.parentEnds, client.Timeout)
			}
		})
	}
}

func TestTenantCrowdstrikeOAuthUsesGuardedClient(t *testing.T) {
	g := NewTenant("check", map[string]interface{}{
		"provider": "crowdstrike", "client_id": "tenant", "client_secret": "tenant-secret",
		"base_url": "http://127.0.0.1:80",
	}, false)
	a := g.adapter.(*crowdstrikeAdapter)
	if a.client.Transport == nil || a.client.Timeout != 10*time.Second {
		t.Fatal("OAuth client must have a guarded transport and its own 10s timeout")
	}
	_, err := a.getToken(context.Background())
	if err == nil || !strings.Contains(err.Error(), "destination address is not allowed") {
		t.Fatalf("OAuth request returned %v, want egress refusal", err)
	}
}
