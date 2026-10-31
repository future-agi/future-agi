package webhook

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

func TestTenantWebhookDoesNotReachOperatorPrivateEndpoint(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		io.WriteString(w, `{"pass":true}`)
	}))
	defer srv.Close()
	cfg := map[string]interface{}{"url": srv.URL}
	input := &guardrails.CheckInput{}

	if result := New("operator-check", cfg).Check(context.Background(), input); !result.Pass {
		t.Fatalf("operator static webhook stopped working: %+v", result)
	}
	for _, allowPrivate := range []bool{false, true} {
		result := NewTenant("tenant-check", cfg, allowPrivate).Check(context.Background(), input)
		if result.Pass || !strings.Contains(result.Message, "destination address is not allowed") {
			t.Fatalf("tenant loopback call (allowPrivate=%v) did not fail closed: %+v", allowPrivate, result)
		}
	}
	if hits.Load() != 1 {
		t.Fatalf("private endpoint received %d requests, want the operator request only", hits.Load())
	}
}
