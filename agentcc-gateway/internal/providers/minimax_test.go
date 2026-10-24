package providers

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestMiniMaxDefaultsAndExplicitModels(t *testing.T) {
	cfg := config.ProviderConfig{Type: "minimax"}
	applyProviderPreset(&cfg)
	if !reflect.DeepEqual(cfg.Models, []string{"MiniMax-M3.1-Flash", "MiniMax-M3"}) {
		t.Fatalf("defaults = %v", cfg.Models)
	}
	for _, ids := range [][]string{{"MiniMax-M2.7"}, {"custom-model"}, {"speech-2.8-hd"}} {
		cfg := config.ProviderConfig{Type: "minimax", Models: append([]string(nil), ids...)}
		applyProviderPreset(&cfg)
		if !reflect.DeepEqual(cfg.Models, ids) {
			t.Fatalf("explicit model list changed: %v", cfg.Models)
		}
	}
}

func TestMiniMaxDiscoveryPreservesConfiguredAndMedia(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = fmt.Fprint(w, `{"data":[{"id":"MiniMax-M2.7"},{"id":"abab6.5s-chat"},{"id":"MiniMax-M3.1-Flash"},{"id":"MiniMax-M3"},{"id":"speech-2.8-hd"}]}`)
	}))
	defer srv.Close()
	discover := true
	cfg := config.ProviderConfig{Type: "minimax", BaseURL: srv.URL, AutoDiscover: &discover, Models: []string{"MiniMax-M2.7"}}
	got := autoDiscoverModels("minimax", &cfg)
	if !reflect.DeepEqual(got, []string{"MiniMax-M3.1-Flash", "MiniMax-M3", "speech-2.8-hd"}) {
		t.Fatalf("discovered = %v", got)
	}
	if !reflect.DeepEqual(cfg.Models, []string{"MiniMax-M2.7"}) {
		t.Fatal("explicit models changed")
	}
	defaults := config.ProviderConfig{Type: "minimax", BaseURL: srv.URL, AutoDiscover: &discover}
	applyProviderPreset(&defaults)
	if got := autoDiscoverModels("minimax", &defaults); !reflect.DeepEqual(got, []string{"speech-2.8-hd"}) {
		t.Fatalf("defaults duplicated by discovery: %v", got)
	}
	cfg.Type = "custom"
	if got := autoDiscoverModels("custom", &cfg); len(got) != 5 {
		t.Fatalf("unrelated provider filtered: %v", got)
	}
}

func TestMiniMaxHTTPRequests(t *testing.T) {
	for _, region := range []string{"https://api.minimax.io", "https://api.minimaxi.com"} {
		for _, format := range []string{"openai", "anthropic"} {
			for _, model := range []string{"MiniMax-M3.1-Flash", "MiniMax-M3", "MiniMax-M2.7", "custom-model"} {
				for _, stream := range []bool{false, true} {
					for _, effort := range []string{"", "high", "max"} {
						name := fmt.Sprintf("%s/%s/%s/stream=%v/effort=%s", region, format, model, stream, effort)
						t.Run(name, func(t *testing.T) {
							path := "/v1/chat/completions"
							root := region
							if format == "anthropic" {
								root += "/anthropic"
								path = "/anthropic/v1/messages"
							}
							cfg := config.ProviderConfig{Type: "minimax", BaseURL: root, APIFormat: format, APIKey: "fixture-key"}
							applyProviderPreset(&cfg)
							if cfg.BaseURL != root {
								t.Fatalf("regional endpoint changed: %s", cfg.BaseURL)
							}
							var req models.ChatCompletionRequest
							if err := json.Unmarshal([]byte(`{"messages":[{"role":"user","content":[{"type":"text","text":"Describe it"},{"type":"image_url","image_url":{"url":"data:image/png;base64,aW1hZ2U="}}]},{"role":"assistant","reasoning_content":"Inspect the image","thinking_blocks":[{"type":"thinking","thinking":"Inspect the image","signature":"fixture-signature"}],"tool_calls":[{"id":"call_1","type":"function","function":{"name":"inspect","arguments":"{}"}}]},{"role":"tool","tool_call_id":"call_1","content":"result"}],"tools":[{"type":"function","function":{"name":"inspect","parameters":{"type":"object","properties":{}}}}]}`), &req); err != nil {
								t.Fatal(err)
							}
							req.Model = model
							if model == "MiniMax-M2.7" || model == "custom-model" {
								cfg.Models = []string{model}
							}
							if effort != "" {
								req.Extra = map[string]json.RawMessage{"reasoning_effort": json.RawMessage(`"` + effort + `"`), "thinking": json.RawMessage(`{"type":"adaptive"}`)}
							}
							before, _ := json.Marshal(req)
							var called atomic.Bool
							srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
								called.Store(true)
								if r.Method != http.MethodPost || r.URL.Path != path {
									t.Errorf("request = %s %s", r.Method, r.URL.Path)
								}
								var body map[string]json.RawMessage
								if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
									t.Error(err)
								}
								if string(body["model"]) != `"`+model+`"` {
									t.Errorf("model changed: %s", body["model"])
								}
								if _, ok := body["service_tier"]; ok {
									t.Error("unexpected service_tier")
								}
								if _, ok := body["reasoning_split"]; ok {
									t.Error("unexpected reasoning_split")
								}
								messages := string(body["messages"])
								for _, value := range []string{"aW1hZ2U=", "Inspect the image", "call_1", "result", "fixture-signature"} {
									if !strings.Contains(messages, value) {
										t.Errorf("lost message content %s: %s", value, messages)
									}
								}
								if !strings.Contains(string(body["tools"]), "inspect") {
									t.Error("lost tool schema")
								}
								if effort != "" {
									key := "reasoning_effort"
									if format == "anthropic" {
										key = "output_config"
									}
									if !strings.Contains(string(body[key]), effort) || !strings.Contains(string(body["thinking"]), "adaptive") {
										t.Errorf("lost thinking/effort: %s %s", body[key], body["thinking"])
									}
								} else if len(body["thinking"]) > 0 || len(body["reasoning_effort"]) > 0 || len(body["output_config"]) > 0 {
									t.Error("provider defaults were overridden")
								}
								if stream {
									w.Header().Set("Content-Type", "text/event-stream")
									if format == "anthropic" {
										_, _ = fmt.Fprint(w, "event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_1\",\"model\":\"MiniMax-M3.1-Flash\",\"usage\":{\"input_tokens\":1}}}\n\nevent: message_stop\ndata: {\"type\":\"message_stop\"}\n\n")
									} else {
										_, _ = fmt.Fprint(w, "data: {\"id\":\"chat_1\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"ok\"}}]}\n\ndata: [DONE]\n\n")
									}
								} else if format == "anthropic" {
									_, _ = fmt.Fprint(w, `{"id":"msg_1","type":"message","role":"assistant","content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn"}`)
								} else {
									_, _ = fmt.Fprint(w, `{"id":"chat_1","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}`)
								}
							}))
							defer srv.Close()
							// Substitute only the network origin; keep the selected protocol path.
							cfg.BaseURL = srv.URL + strings.TrimPrefix(root, region)
							local := false
							cfg.Local = &local
							registry, err := NewRegistry(&config.Config{Providers: map[string]config.ProviderConfig{"minimax": cfg}})
							if err != nil {
								t.Fatal(err)
							}
							defer func() {
								if err := registry.Close(); err != nil {
									t.Error(err)
								}
							}()
							if model == "MiniMax-M3.1-Flash" || model == "MiniMax-M3" {
								listed := registry.ListAllModels()
								if len(listed) != 2 || listed[0].ID != "MiniMax-M3.1-Flash" || listed[1].ID != "MiniMax-M3" {
									t.Fatalf("ordinary registry choices = %v", listed)
								}
							}
							provider, err := registry.Resolve(req.Model)
							if err != nil {
								t.Fatal(err)
							}
							if stream {
								chunks, errs := provider.StreamChatCompletion(context.Background(), &req)
								for range chunks {
								}
								for err := range errs {
									if err != nil {
										t.Error(err)
									}
								}
							} else if _, err := provider.ChatCompletion(context.Background(), &req); err != nil {
								t.Fatal(err)
							}
							after, _ := json.Marshal(req)
							if !called.Load() || string(before) != string(after) {
								t.Error("transport not called or caller history mutated")
							}
						})
					}
				}
			}
		}
	}
}
