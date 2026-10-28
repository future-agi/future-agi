package server

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	cachepkg "github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/blocklist"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	cacheplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cache"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

func TestReflexionDefaultCORSExposesDiagnostics(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.CORS = config.CORSConfig{Enabled: true, AllowedOrigins: []string{"https://allowed.example"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	s := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	for _, origin := range []string{"https://allowed.example", "https://denied.example"} {
		r := httptest.NewRequest("GET", "/health", nil)
		r.Header.Set("Origin", origin)
		w := httptest.NewRecorder()
		s.httpServer.Handler.ServeHTTP(w, r)
		exposed := strings.ToLower(w.Header().Get("Access-Control-Expose-Headers"))
		for _, header := range []string{"x-agentcc-request-id", "x-agentcc-reflexion-attempts", "x-agentcc-provider-attempts", "x-agentcc-usage-incomplete"} {
			if strings.Contains(exposed, header) != (origin == "https://allowed.example") {
				t.Fatalf("origin=%s missing or unauthorized exposure of %s: %v", origin, header, w.Header())
			}
		}
	}
}

func TestCollectCandidateSettlesTransportFailureUsage(t *testing.T) {
	text := "private candidate"
	first := &models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}}
	for i := 0; i < 64; i++ {
		chunks := make(chan models.StreamChunk, 1)
		chunks <- models.StreamChunk{Model: "gpt-4o", Usage: &models.Usage{PromptTokens: 10, CompletionTokens: 20, TotalTokens: 30}}
		errs := make(chan error, 1)
		errs <- io.ErrUnexpectedEOF
		candidate, err := collectCandidate(context.Background(), chunks, errs, first)
		if !errors.Is(err, io.ErrUnexpectedEOF) || candidate.model != "gpt-4o" || candidate.usage == nil || candidate.usage.TotalTokens != 30 || len(chunks) != 0 {
			t.Fatalf("lost queued accounting: candidate=%+v error=%v queued=%d", candidate, err, len(chunks))
		}
		rc := models.AcquireRequestContext()
		rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
		rc.Response = candidate.response()
		rc.ResolvedModel = rc.Response.Model
		rc.Attempts = &models.AttemptLedger{Open: true, Started: 1}
		pipeline.NewEngine(reflectionCost()).EndProviderAttempt(context.Background(), rc)
		if rc.Metadata["cost"] != "0.300000" || rc.Attempts.Attempts[0].UsageMissing {
			t.Fatalf("failed paid attempt not settled: %v %+v", rc.Metadata, rc.Attempts)
		}
		rc.Release()
	}
}

type queuedUsageFailureProvider struct{ failedStartProvider }

func (p *queuedUsageFailureProvider) StreamChatCompletion(context.Context, *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	chunks := make(chan models.StreamChunk, 1)
	chunks <- models.StreamChunk{Model: "gpt-4o", Usage: &models.Usage{PromptTokens: 10, CompletionTokens: 20, TotalTokens: 30}}
	errs := make(chan error, 1)
	errs <- io.ErrUnexpectedEOF
	close(chunks)
	close(errs)
	return chunks, errs
}

func TestReflexionStreamOpenerSettlesQueuedFailureUsage(t *testing.T) {
	for i := 0; i < 64; i++ {
		observer := &reflectionObserver{}
		h := &Handlers{engine: pipeline.NewEngine(reflectionPlugin(&reflectionCheck{name: "output"}, reflectionPolicy(), "block", "post"), reflectionCost(), observer)}
		rc := models.AcquireRequestContext()
		rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"original"`)}}}
		w := httptest.NewRecorder()
		h.handleStream(context.Background(), w, rc, &queuedUsageFailureProvider{}, nil)
		if w.Code < 400 || observer.count != 1 || observer.usage == nil || observer.usage.TotalTokens != 30 || observer.metadata["cost"] != "0.300000" || observer.metadata["usage_incomplete"] != "" || observer.snapshot.Response != nil {
			t.Fatalf("opener lost paid usage: status=%d usage=%+v metadata=%v", w.Code, observer.usage, observer.metadata)
		}
		rc.Release()
	}
}

func TestReflexionStreamFrameLimit(t *testing.T) {
	for _, frames := range []int{maxReflexionStreamChunks - 1, maxReflexionStreamChunks, maxReflexionStreamChunks + 1} {
		t.Run(fmt.Sprint(frames), func(t *testing.T) {
			chunks := make(chan models.StreamChunk, frames)
			text, finish := "valid candidate", "stop"
			chunks <- models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}, FinishReason: &finish}}}
			for i := 1; i < frames; i++ {
				chunks <- models.StreamChunk{}
			}
			close(chunks)
			candidate, err := collectCandidate(context.Background(), chunks, nil, nil)
			if frames <= maxReflexionStreamChunks {
				if err != nil || len(candidate.frames) != frames {
					t.Fatalf("allowed boundary rejected: frames=%d error=%v", len(candidate.frames), err)
				}
				return
			}
			var apiErr *models.APIError
			if !errors.As(err, &apiErr) || apiErr.Code != "reflection_buffer_exceeded" {
				t.Fatalf("overflow must fail for the frame limit, not EOF: %v", err)
			}
		})
	}
}

func TestReflexionToolAssemblyAllocationsAreLinear(t *testing.T) {
	const fragments = 3000
	part := strings.Repeat("x", 256)
	var response *models.ChatCompletionResponse
	result := testing.Benchmark(func(b *testing.B) {
		for n := 0; n < b.N; n++ {
			candidate := newBufferedCandidate()
			for i := 0; i < fragments; i++ {
				name := ""
				if i == 0 {
					name = "look"
				} else if i == 1 {
					name = "up"
				}
				chunk := models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{ToolCalls: []models.ToolCallDelta{{Index: 0, ID: "call-1", Type: "function", Function: &models.FunctionCall{Name: name, Arguments: part}}}}}}}
				if err := candidate.add(chunk); err != nil {
					b.Fatal(err)
				}
			}
			response = candidate.response()
		}
	})
	tool := response.Choices[0].Message.ToolCalls[0]
	if tool.ID != "call-1" || tool.Type != "function" || tool.Function.Name != "lookup" || tool.Function.Arguments != strings.Repeat(part, fragments) {
		t.Fatalf("fragmented tool was not assembled correctly: name=%q bytes=%d", tool.Function.Name, len(tool.Function.Arguments))
	}
	// Includes frame ownership/JSON overhead; repeated immutable concatenation
	// alone allocates over 1 GiB for this sub-2-MiB candidate.
	if allocated := result.AllocedBytesPerOp(); allocated > 64<<20 {
		t.Fatalf("quadratic tool assembly: %d bytes allocated per candidate", allocated)
	}
}

func TestReflexionNonstreamMalformedCandidatesFailClosed(t *testing.T) {
	for _, failure := range []string{"envelope", "empty", "finish"} {
		for _, attempt := range []string{"first", "repair", "ordinary"} {
			t.Run(failure+"/"+attempt, func(t *testing.T) {
				u := &reflectionUpstream{responseFailure: failure}
				if attempt == "repair" {
					u.responseFailure, u.repairResponseFailure, u.blocked = "", failure, 1
				}
				upstream := httptest.NewServer(http.HandlerFunc(u.serve))
				defer upstream.Close()
				logger, records := reflectionLogCapture(t)
				observer := &reflectionObserver{}
				plugins := []pipeline.Plugin{reflectionCost(), observer, logger}
				if attempt != "ordinary" {
					cfg := reflectionPolicy()
					cfg["words"] = []interface{}{"rejected"}
					check := blocklist.New(cfg)
					registry := map[string]guardrails.Guardrail{check.Name(): check}
					ge := guardrails.NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: []config.GuardrailRuleConfig{{Name: check.Name(), Stage: "post", Action: "block", Config: cfg}}}, registry)
					plugins = append(plugins, guardrails.NewPlugin(ge, registry, nil, nil, nil))
				}
				s := createTestServerWithPlugins(t, upstream.URL, plugins...)
				w := callReflectionServer(s, false)
				logger.Close()
				if attempt == "ordinary" {
					if w.Code != 200 || !strings.Contains(w.Body.String(), "NONSTREAM_PRIVATE_SENTINEL") {
						t.Fatalf("ordinary first-attempt behavior changed: %d %s", w.Code, w.Body.String())
					}
					return
				}
				calls := 1
				if attempt == "repair" {
					calls = 2
				}
				if w.Code != 502 || len(u.calls()) != calls || observer.snapshot.Response != nil || observer.usage == nil || observer.usage.TotalTokens != 30*calls || observer.metadata["cost"] != fmt.Sprintf("%.6f", .3*float64(calls)) {
					t.Fatalf("invalid candidate accepted or unmetered: status=%d calls=%d usage=%+v metadata=%v body=%s", w.Code, len(u.calls()), observer.usage, observer.metadata, w.Body.String())
				}
				var record string
				select {
				case record = <-records:
				default:
					t.Fatal("request log missing")
				}
				for _, sink := range []string{w.Body.String(), record, fmt.Sprint(observer.snapshot.Errors)} {
					for _, private := range []string{"NONSTREAM_PRIVATE_SENTINEL", "rejected secret answer", "The preceding answer failed"} {
						if strings.Contains(sink, private) {
							t.Fatalf("invalid candidate leaked through a final sink: %s", sink)
						}
					}
				}
				if attempt == "repair" && !strings.Contains(w.Body.String(), "Provider request failed during response repair") {
					t.Fatalf("repair failure bypassed sanitization: %s", w.Body.String())
				}
			})
		}
	}
}

func TestReflexionCachedRejectionRequiresExplicitRegeneration(t *testing.T) {
	u := &reflectionUpstream{blocked: 2}
	upstream := httptest.NewServer(http.HandlerFunc(u.serve))
	defer upstream.Close()
	store := cachepkg.NewStore(10)
	request := &models.ChatCompletionRequest{Model: "gpt-4o", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"original prompt"`)}}}
	key := cacheplugin.BuildCacheKey("default", request)
	store.Set(key, &models.ChatCompletionResponse{Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: json.RawMessage(`"rejected old cached answer"`)}, FinishReason: "stop"}}}, time.Minute)
	observer := &reflectionObserver{initial: map[string]string{"org_cache_disabled": "false"}}
	s := createTestServerWithPlugins(t, upstream.URL, reflectionPlugin(&reflectionCheck{name: "output"}, reflectionPolicy(), "block", "post"), reflectionCost(), cacheplugin.NewFromStore(store, time.Minute), observer)
	w := callReflectionServer(s, false)
	if w.Code != 403 || !strings.Contains(w.Body.String(), `"code":"cached_response_blocked"`) || strings.Contains(w.Body.String(), "rejected") || len(u.calls()) != 0 || observer.snapshot.Response != nil || observer.metadata["cost"] != "" {
		t.Fatalf("cached failure must be explicit, private and free: status=%d calls=%d metadata=%v body=%s", w.Code, len(u.calls()), observer.metadata, w.Body.String())
	}
	r := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(`{"model":"gpt-4o","messages":[{"role":"user","content":"original prompt"}]}`))
	r.Header.Set("Cache-Control", "no-cache")
	// One rejected generation followed by one passing repair.
	u.blocked = 1
	w = httptest.NewRecorder()
	s.httpServer.Handler.ServeHTTP(w, r)
	if w.Code != 200 || len(u.calls()) != 2 || !strings.Contains(w.Body.String(), "accepted answer") {
		t.Fatalf("explicit cache bypass did not repair: status=%d calls=%d body=%s", w.Code, len(u.calls()), w.Body.String())
	}
	cached, ok := store.Get(key)
	if !ok || len(cached.Choices) != 1 || string(cached.Choices[0].Message.Content) != `"accepted answer"` {
		t.Fatalf("passing answer did not replace stale cache entry: %+v", cached)
	}
}
