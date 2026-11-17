package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// A Messages stream served by a non-Anthropic provider is translated from
// chat chunks. Its serving attempt counts once in the provider's health, at
// the stream's end, as a chat stream's does: a success when the upstream
// finished it, an error when the upstream ended it with one.
func TestMessagesStreamHealthUsesUpstreamCompletion(t *testing.T) {
	for _, fail := range []bool{false, true} {
		t.Run(fmt.Sprintf("upstream-failure=%t", fail), func(t *testing.T) {
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != "/v1/chat/completions" {
					w.WriteHeader(http.StatusNotFound)
					return
				}
				w.Header().Set("Content-Type", "text/event-stream")
				fmt.Fprint(w, "data: {\"id\":\"synthetic\",\"model\":\"adaptive-sample\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"synthetic response\"}}]}\n\n")
				w.(http.Flusher).Flush()
				if fail {
					fmt.Fprint(w, "data: {\"error\":{\"message\":\"synthetic failure\",\"type\":\"server_error\",\"code\":\"synthetic\"}}\n\n")
				} else {
					fmt.Fprint(w, "data: {\"id\":\"synthetic\",\"model\":\"adaptive-sample\",\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n")
					fmt.Fprint(w, "data: [DONE]\n\n")
				}
			}))
			t.Cleanup(upstream.Close)
			cfg := config.DefaultConfig()
			cfg.Providers = map[string]config.ProviderConfig{"fast": {BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}}}
			cfg.Routing = config.RoutingConfig{DefaultStrategy: "adaptive"}
			srv, gateway := adaptiveServer(t, cfg)

			body, err := json.Marshal(map[string]any{
				"model": "adaptive-sample", "stream": true, "max_tokens": 16,
				"messages": []map[string]string{{"role": "user", "content": "synthetic probe"}},
			})
			if err != nil {
				t.Fatal(err)
			}
			req, err := http.NewRequest(http.MethodPost, gateway.URL+"/v1/messages", bytes.NewReader(body))
			if err != nil {
				t.Fatal(err)
			}
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("anthropic-version", "2023-06-01")
			client := gateway.Client()
			client.Timeout = 5 * time.Second
			resp, err := client.Do(req)
			if err != nil {
				t.Fatal(err)
			}
			data, err := io.ReadAll(resp.Body)
			resp.Body.Close()
			if err != nil {
				t.Fatal(err)
			}
			if resp.StatusCode != http.StatusOK || !strings.Contains(string(data), "synthetic response") {
				t.Fatalf("stream not received: status=%d body=%s", resp.StatusCode, data)
			}

			health := srv.handlers.healthMonitor.GetHealth("fast")
			wantErrors := int64(0)
			if fail {
				wantErrors = 1
			}
			if health.RequestCount != 1 || health.ErrorCount != wantErrors {
				t.Fatalf("stream outcome attribution: samples=%d errors=%d want_errors=%d", health.RequestCount, health.ErrorCount, wantErrors)
			}
		})
	}
}
