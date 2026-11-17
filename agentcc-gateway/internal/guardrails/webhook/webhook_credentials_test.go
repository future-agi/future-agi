package webhook

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The check sends its endpoint no caller credential even when its
// input's metadata holds one (the guardrail plugin's copy never does).
func TestWebhook_SendsNoCredentialFromItsInput(t *testing.T) {
	const key = "sk-agentcc-webhook-sink-0123456789abcdef"
	var got string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		got = string(b)
		_, _ = w.Write([]byte(`{"pass":true,"score":0}`))
	}))
	defer srv.Close()

	g := newGuard("sink", map[string]interface{}{"url": srv.URL})
	res := g.Check(context.Background(), &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{Model: "gpt-4o",
			Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hello"`)}}},
		Metadata: map[string]string{
			"authorization": "Bearer " + key,
			"api_token":     "planted-api-token",
			"auth_key_id":   "key_1",
			"client_ip":     "203.0.113.7",
		},
	})

	if !res.Pass {
		t.Fatalf("the check failed: %+v", res)
	}
	if strings.Contains(got, key) || strings.Contains(got, "planted-api-token") {
		t.Errorf("the endpoint got a credential: %s", got)
	}
	if !strings.Contains(got, `"auth_key_id":"key_1"`) || !strings.Contains(got, "203.0.113.7") {
		t.Errorf("the endpoint lost what is not a credential: %s", got)
	}
}
