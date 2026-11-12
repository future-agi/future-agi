package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strconv"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	cacheplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cache"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const completionCacheBody = `{"model":"gpt-4o","prompt":"cached prompt","max_tokens":16,"temperature":0,"n":2,"logprobs":1}`

// Exercise the real native OpenAI adapter and cache/cost plugins through HTTP.
// Only inference POSTs count; the registry can probe GET /v1/models at startup.
func newCompletionCacheGateway(t *testing.T) (*Server, *atomic.Int64) {
	t.Helper()
	calls := new(atomic.Int64)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || (r.URL.Path != "/v1/completions" && r.URL.Path != "/v1/chat/completions") {
			http.NotFound(w, r)
			return
		}
		var request struct {
			Logprobs json.RawMessage `json:"logprobs"`
		}
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			http.Error(w, "invalid fixture request", http.StatusBadRequest)
			return
		}
		requested := len(request.Logprobs) > 0 && string(request.Logprobs) != "null" && string(request.Logprobs) != "false"
		n := calls.Add(1)
		answer := &models.CompletionResponse{
			ID: fmt.Sprintf("cmpl-cache-%d", n), Object: "text_completion", Created: 1700000000,
			Model: "gpt-4o", SystemFingerprint: "fp-cache-test",
			Choices: []models.CompletionChoice{
				{Index: 0, Text: "first cached answer", FinishReason: "stop"},
				{Index: 1, Text: "second cached answer", FinishReason: "length"},
			},
			Usage: &models.Usage{PromptTokens: 5, CompletionTokens: 4, TotalTokens: 9},
		}
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/v1/completions" {
			if requested {
				for i := range answer.Choices {
					answer.Choices[i].Logprobs = completionCacheLogprobsFixture(answer.Choices[i].Text, true)
				}
			}
			_ = json.NewEncoder(w).Encode(answer)
			return
		}
		// Build the chat fixture independently of the production conversion.
		chat := &models.ChatCompletionResponse{
			ID: answer.ID, Object: "chat.completion", Created: answer.Created,
			Model: answer.Model, Usage: answer.Usage, SystemFingerprint: answer.SystemFingerprint,
		}
		for _, choice := range answer.Choices {
			content, _ := json.Marshal(choice.Text)
			var logprobs *json.RawMessage
			if requested {
				logprobs = completionCacheLogprobsFixture(choice.Text, false)
			}
			chat.Choices = append(chat.Choices, models.Choice{
				Index: choice.Index, Message: models.Message{Role: "assistant", Content: content},
				FinishReason: choice.FinishReason, Logprobs: logprobs,
			})
		}
		_ = json.NewEncoder(w).Encode(chat)
	}))
	t.Cleanup(upstream.Close)

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name: "cache-completion", Key: cacheScopeKeyA, KeyType: "internal",
		Metadata: map[string]string{"org_id": cacheScopeOrgA},
	}}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL: upstream.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"},
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	tenants.Set(cacheScopeOrgA, &tenant.OrgConfig{Cache: &tenant.CacheConfig{Enabled: true}})
	keys := auth.NewKeyStore(cfg.Auth)
	mdb := testModelDBPtr()
	engine := pipeline.NewEngine(authplugin.New(keys, true),
		cacheplugin.NewFromStore(cache.NewStore(10), time.Minute),
		costplugin.New(true, mdb.Load, tenants, nil))
	srv := New(cfg, "", registry, engine, keys, nil, nil, nil, mdb, tenants, nil)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	return srv, calls
}

func postCompletionCache(t *testing.T, srv *Server, path, body string, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+cacheScopeKeyA)
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

func assertCompletionCacheHeaders(t *testing.T, w *httptest.ResponseRecorder, status string) {
	t.Helper()
	if w.Code != http.StatusOK {
		t.Fatalf("status=%d cache=%q, want 200/%s: %s", w.Code, w.Header().Get("x-agentcc-cache"), status, w.Body.String())
	}
	if got := w.Header().Get("x-agentcc-cache"); got != status {
		t.Errorf("cache=%q, want %q", got, status)
	}
	if got := w.Header().Get("Content-Type"); got != "application/json" {
		t.Errorf("content type=%q", got)
	}
	if got := w.Header().Get("x-agentcc-provider"); got != "openai" {
		t.Errorf("provider=%q, want openai", got)
	}
	if _, err := strconv.ParseInt(w.Header().Get("x-agentcc-latency-ms"), 10, 64); err != nil {
		t.Errorf("missing/invalid latency header: %v", err)
	}
	if status == "hit_exact" {
		if _, present := w.Header()[http.CanonicalHeaderKey("x-agentcc-cost")]; present {
			t.Errorf("cache hit must omit cost, got %q", w.Header().Get("x-agentcc-cost"))
		}
	} else if amount, err := strconv.ParseFloat(w.Header().Get("x-agentcc-cost"), 64); err != nil || amount <= 0 {
		t.Errorf("upstream call must be priced, cost=%q err=%v", w.Header().Get("x-agentcc-cost"), err)
	}
}

func decodeCachedCompletion(t *testing.T, w *httptest.ResponseRecorder) models.CompletionResponse {
	t.Helper()
	var response models.CompletionResponse
	if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	if response.Object != "text_completion" || response.Model != "gpt-4o" || response.ID == "" || response.Created != 1700000000 || response.SystemFingerprint != "fp-cache-test" {
		t.Fatalf("invalid legacy response envelope: %s", w.Body.String())
	}
	if len(response.Choices) != 2 || response.Choices[0].Text != "first cached answer" || response.Choices[1].Text != "second cached answer" {
		t.Fatalf("lost choices/text: %s", w.Body.String())
	}
	if response.Usage == nil || response.Usage.PromptTokens != 5 || response.Usage.CompletionTokens != 4 || response.Usage.TotalTokens != 9 {
		t.Fatalf("lost cached usage: %s", w.Body.String())
	}
	return response
}

func TestNativeCompletionCacheHit(t *testing.T) {
	srv, calls := newCompletionCacheGateway(t)
	first := postCompletionCache(t, srv, "/v1/completions", completionCacheBody, nil)
	assertCompletionCacheHeaders(t, first, "miss")
	want := decodeCachedCompletion(t, first)
	for i := 0; i < 2; i++ {
		hit := postCompletionCache(t, srv, "/v1/completions", completionCacheBody, nil)
		assertCompletionCacheHeaders(t, hit, "hit_exact")
		if got := decodeCachedCompletion(t, hit); !reflect.DeepEqual(got, want) {
			t.Errorf("cached response differs from native response: got %+v, want %+v", got, want)
		}
		if got := calls.Load(); got != 1 {
			t.Fatalf("cache hit called upstream: calls=%d", got)
		}
	}
	// Cache controls still reach the provider and get a fresh answer.
	refresh := postCompletionCache(t, srv, "/v1/completions", completionCacheBody,
		map[string]string{"x-agentcc-cache-force-refresh": "true"})
	assertCompletionCacheHeaders(t, refresh, "miss")
	if got := decodeCachedCompletion(t, refresh); got.ID == want.ID || calls.Load() != 2 {
		t.Fatalf("force refresh did not reach upstream: id=%s calls=%d", got.ID, calls.Load())
	}
}

// Cross-route reuse without logprobs is intentional: a native completion
// renders the canonical cached answer in the legacy response shape without
// another upstream call or charge.
func TestNativeCompletionCacheHitFromChat(t *testing.T) {
	srv, calls := newCompletionCacheGateway(t)
	first := postCompletionCache(t, srv, "/v1/chat/completions",
		`{"model":"gpt-4o","messages":[{"role":"user","content":"cached prompt"}],"max_tokens":16,"temperature":0,"n":2}`, nil)
	assertCompletionCacheHeaders(t, first, "miss")
	var chat models.ChatCompletionResponse
	if err := json.Unmarshal(first.Body.Bytes(), &chat); err != nil {
		t.Fatal(err)
	}
	hit := postCompletionCache(t, srv, "/v1/completions", completionCacheRequestBody(true, false), nil)
	assertCompletionCacheHeaders(t, hit, "hit_exact")
	if got, want := decodeCachedCompletion(t, hit), models.CompletionResponseFromChat(&chat); !reflect.DeepEqual(&got, want) {
		t.Errorf("cached chat answer changed in legacy response: got %+v, want %+v", got, want)
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("cross-route cache hit called upstream: calls=%d", got)
	}
}

func TestNativeCompletionCacheRoundTripLogprobs(t *testing.T) {
	logprobs := json.RawMessage(`{"tokens":["answer"],"token_logprobs":[-0.25]}`)
	want := &models.CompletionResponse{
		Object:  "text_completion",
		Choices: []models.CompletionChoice{{Index: 0, Text: "answer", FinishReason: "stop", Logprobs: &logprobs}},
	}
	if got := models.CompletionResponseFromChat(chatAnswerFromCompletion(want)); !reflect.DeepEqual(got, want) {
		t.Fatalf("native-to-cache-to-native conversion lost logprobs: got %+v, want %+v", got.Choices, want.Choices)
	}
}
