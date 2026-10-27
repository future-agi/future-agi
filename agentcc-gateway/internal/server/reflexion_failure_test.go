package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestReflexionStreamFailureDoesNotLeakCandidate(t *testing.T) {
	for _, mode := range []string{"deadline", "overflow", "exhaustion"} {
		t.Run(mode, func(t *testing.T) {
			var calls atomic.Int32
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
					w.WriteHeader(404)
					return
				}
				calls.Add(1)
				text := "rejected private stream"
				if mode == "overflow" {
					text += strings.Repeat("x", 100<<10)
				}
				w.Header().Set("Content-Type", "text/event-stream")
				count := 1
				if mode == "overflow" {
					count = 24
				}
				for i := 0; i < count; i++ {
					b, _ := json.Marshal(models.StreamChunk{ID: "private", Model: "gpt-4o", Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}})
					if _, err := fmt.Fprintf(w, "data: %s\n\n", b); err != nil {
						return
					}
					w.(http.Flusher).Flush()
				}
				if mode == "deadline" {
					<-r.Context().Done()
					return
				}
				finish := "stop"
				b, _ := json.Marshal(models.StreamChunk{ID: "private", Model: "gpt-4o", Choices: []models.StreamChoice{{FinishReason: &finish}}, Usage: &models.Usage{PromptTokens: 1, CompletionTokens: 2, TotalTokens: 3}})
				fmt.Fprintf(w, "data: %s\n\ndata: [DONE]\n\n", b)
			}))
			defer upstream.Close()
			observer := &reflectionObserver{}
			s := createTestServerWithPlugins(t, upstream.URL, reflectionPlugin(&reflectionCheck{name: "output"}, reflectionPolicy(), "block", "post"), observer)
			ctx := context.Background()
			var cancel context.CancelFunc
			if mode == "deadline" {
				ctx, cancel = context.WithTimeout(ctx, 100*time.Millisecond)
				defer cancel()
			}
			r := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(`{"model":"gpt-4o","messages":[{"role":"user","content":"original"}],"stream":true}`)).WithContext(ctx)
			w := httptest.NewRecorder()
			s.httpServer.Handler.ServeHTTP(w, r)
			wantCalls := int32(1)
			if mode == "exhaustion" {
				wantCalls = 2
			}
			if w.Code < 400 || calls.Load() != wantCalls || strings.Contains(w.Body.String(), "data:") || strings.Contains(w.Body.String(), "private stream") || observer.snapshot.Response != nil {
				t.Fatalf("status=%d calls=%d response=%v body=%s", w.Code, calls.Load(), observer.snapshot.Response, w.Body.String())
			}
			if observer.count != 1 {
				t.Fatalf("observers=%d", observer.count)
			}
		})
	}
}
