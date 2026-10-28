package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestTenantGuardrailPlatformDestination(t *testing.T) {
	var hits atomic.Int32
	platform := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		if r.URL.Path != "/sdk/api/v1/eval/" {
			t.Errorf("unexpected eval path: %s", r.URL)
		}
		io.WriteString(w, `{"result":[]}`)
	}))
	defer platform.Close()
	g := newTenantGuardrail("futureagi-eval", map[string]interface{}{
		"provider": "futureagi", "platform": true, "base_url": "http://127.0.0.2:1",
		"eval_id": "safety", "api_key": "tenant", "secret_key": "tenant-secret",
	}, false, platform.URL)
	result := g.Check(context.Background(), &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"hello"`)},
		}},
	})
	if !result.Pass || hits.Load() != 1 {
		t.Fatalf("platform hits=%d, result=%+v", hits.Load(), result)
	}
}

func TestTenantGuardrailEgress(t *testing.T) {
	for _, allowPrivate := range []bool{false, true} {
		if allowPrivate {
			t.Setenv(config.EnvAllowPrivateProviderURLs, "true")
		} else {
			t.Setenv(config.EnvAllowPrivateProviderURLs, "false")
		}
		gatewayCfg, err := config.Load("")
		if err != nil {
			t.Fatal(err)
		}
		for _, host := range []string{"127.0.0.1", "10.1.2.3", "169.254.169.254", "localhost", "[::1]"} {
			for _, kind := range []string{"webhook", "external", "crowdstrike", "futureagi"} {
				t.Run(kind+"/"+host+"/private="+strconv.FormatBool(allowPrivate), func(t *testing.T) {
					url := "http://" + host + ":80"
					var cfg map[string]interface{}
					switch kind {
					case "webhook":
						cfg = map[string]interface{}{"url": url}
					case "external":
						cfg = map[string]interface{}{"provider": "lakera", "api_key": "tenant", "endpoint": url}
					case "crowdstrike":
						cfg = map[string]interface{}{"provider": "crowdstrike", "client_id": "tenant", "client_secret": "tenant-secret", "base_url": url}
					case "futureagi":
						cfg = map[string]interface{}{"provider": "futureagi", "eval_id": "safety", "api_key": "tenant", "secret_key": "tenant-secret", "base_url": url}
					}
					g := newTenantGuardrail("tenant-check", cfg, gatewayCfg.OrgProviders.AllowPrivateURLs, "http://127.0.0.1:8000")
					if g == nil {
						t.Fatal("tenant guardrail was not constructed")
					}
					ctx, cancel := context.WithTimeout(context.Background(), time.Second)
					defer cancel()
					result := g.Check(ctx, &guardrails.CheckInput{
						Request: &models.ChatCompletionRequest{Messages: []models.Message{
							{Role: "user", Content: json.RawMessage(`"hello"`)},
						}},
					})
					if result.Pass {
						t.Fatalf("outbound failure returned a pass: %+v", result)
					}
					wantBlocked := host != "10.1.2.3" || !allowPrivate
					if blocked := strings.Contains(result.Message, "destination address is not allowed"); blocked != wantBlocked {
						t.Fatalf("allowPrivate=%v, result=%+v, want egress refusal=%v", allowPrivate, result, wantBlocked)
					}
				})
			}
		}
	}
}

func TestTenantPlatformURL(t *testing.T) {
	t.Setenv("FI_BASE_URL", "http://backend-service:8000")
	if got := tenantPlatformURL("http://127.0.0.1:8000"); got != "http://127.0.0.1:8000" {
		t.Fatalf("control plane set: got %q", got)
	}
	// Helm with controlPlaneSync=false sets FI_BASE_URL but no control plane.
	if got := tenantPlatformURL(""); got != "http://backend-service:8000" {
		t.Fatalf("pull sync off: got %q, want FI_BASE_URL", got)
	}
	t.Setenv("FI_BASE_URL", "")
	if got := tenantPlatformURL(""); got != "" {
		t.Fatalf("neither set: got %q, want empty", got)
	}
}
