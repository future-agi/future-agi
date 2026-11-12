package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers/openai"
)

// Real asynchronous adapter: opening channels is not a successful handshake.
func TestDXCompletionStartupHTTPRefusal(t *testing.T) {
	for _, status := range []int{401, 403} {
		for _, raw := range []bool{false, true} {
			t.Run(fmt.Sprintf("%d/raw=%v", status, raw), func(t *testing.T) {
				body := `{"error":{"message":"synthetic refusal","type":"authentication_error","code":"invalid_api_key"}}`
				message, typ, code := "synthetic refusal", "authentication_error", "invalid_api_key"
				if raw {
					body = "synthetic refusal"
					message = fmt.Sprintf("provider error (HTTP %d): synthetic refusal", status)
					typ = "upstream_error"
					code = fmt.Sprintf("provider_%d", status)
				}
				up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(status); fmt.Fprint(w, body) }))
				defer up.Close()
				p, err := openai.New("fixture", config.ProviderConfig{BaseURL: up.URL, APIKey: "synthetic-platform-credential"})
				if err != nil {
					t.Fatal(err)
				}
				defer p.Close()
				rc := models.AcquireRequestContext()
				defer rc.Release()
				rc.Provider, rc.Model, rc.IsStream = "fixture", "probe", true
				rc.Request = &models.ChatCompletionRequest{Model: "probe", Stream: true}
				post := &admissionPostObserver{}
				h := &Handlers{engine: pipeline.NewEngine(post)}
				w := httptest.NewRecorder()
				h.handleCompletionStream(context.Background(), w, rc, p)
				want := map[string]any{"error": map[string]any{"message": fmt.Sprintf(`Provider "fixture" rejected the credential this gateway is configured with for it (HTTP %d). This is not a problem with your API key; the gateway's operator must update that provider's key. The provider said: %s`, status, message), "type": "upstream_error", "code": "provider_auth_failed", "param": nil, "metadata": map[string]any{"provider": "fixture", "provider_status": float64(status), "provider_message": message, "provider_type": typ, "provider_code": code}}}
				var got map[string]any
				if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
					t.Fatal(err, w.Body.String())
				}
				if w.Code != 502 || !reflect.DeepEqual(got, want) || w.Header().Get("Content-Type") != "application/json" || w.Flushed || w.Header().Get("WWW-Authenticate") != "" {
					t.Fatalf("HTTP %d headers=%v body=%s; want %#v", w.Code, w.Header(), w.Body.String(), want)
				}
				if post.calls != 1 || post.usage != nil {
					t.Fatalf("posts=%d usage=%+v", post.calls, post.usage)
				}
			})
		}
	}
}

func TestDXCompletionStartupExactHealthyStream(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Provider, rc.Model, rc.IsStream = "fixture", "probe", true
	rc.Request = &models.ChatCompletionRequest{Model: "probe", Stream: true}
	post := &admissionPostObserver{}
	h := &Handlers{engine: pipeline.NewEngine(post)}
	w := httptest.NewRecorder()
	h.handleCompletionStream(context.Background(), w, rc, admissionStreamProvider{})
	want := "data: {\"id\":\"first\",\"object\":\"text_completion\",\"created\":0,\"model\":\"resolved\",\"choices\":[{\"index\":0,\"text\":\"first-and-only\",\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":3,\"completion_tokens\":5,\"total_tokens\":8}}\n\ndata: [DONE]\n\n"
	if w.Code != 200 || w.Header().Get("Content-Type") != "text/event-stream" || w.Body.String() != want {
		t.Fatalf("HTTP %d stream=%q; want %q", w.Code, w.Body.String(), want)
	}
	if post.calls != 1 || post.usage == nil || post.usage.PromptTokens != 3 || post.usage.CompletionTokens != 5 {
		t.Fatalf("posts=%d usage=%+v", post.calls, post.usage)
	}
}
