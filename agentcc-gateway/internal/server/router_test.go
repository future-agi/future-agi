package server

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func dummyHandler(name string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(name))
	}
}

func TestRouterStaticRoutes(t *testing.T) {
	rt := NewRouter()
	rt.Handle("GET", "/healthz", dummyHandler("healthz"))
	rt.Handle("GET", "/v1/models", dummyHandler("models"))
	rt.Handle("POST", "/v1/chat/completions", dummyHandler("chat"))

	tests := []struct {
		method string
		path   string
		code   int
		body   string
	}{
		{"GET", "/healthz", 200, "healthz"},
		{"GET", "/v1/models", 200, "models"},
		{"POST", "/v1/chat/completions", 200, "chat"},
	}

	for _, tt := range tests {
		req := httptest.NewRequest(tt.method, tt.path, nil)
		w := httptest.NewRecorder()
		rt.ServeHTTP(w, req)
		if w.Code != tt.code {
			t.Errorf("%s %s: got %d, want %d", tt.method, tt.path, w.Code, tt.code)
		}
		if tt.body != "" && w.Body.String() != tt.body {
			t.Errorf("%s %s: body got %q, want %q", tt.method, tt.path, w.Body.String(), tt.body)
		}
	}
}

func TestRouterParametricRoutes(t *testing.T) {
	rt := NewRouter()
	rt.Handle("GET", "/v1/models/{model}", dummyHandler("get_model"))
	rt.Handle("GET", "/v1/files/{file_id}", dummyHandler("get_file"))
	rt.Handle("GET", "/v1/files/{file_id}/content", dummyHandler("get_file_content"))
	rt.Handle("DELETE", "/v1/files/{file_id}", dummyHandler("delete_file"))

	tests := []struct {
		method string
		path   string
		code   int
		body   string
		param  string // expected param key
		value  string // expected param value
	}{
		{"GET", "/v1/models/gpt-4", 200, "get_model", "model", "gpt-4"},
		{"GET", "/v1/files/file-abc123", 200, "get_file", "file_id", "file-abc123"},
		{"GET", "/v1/files/file-abc123/content", 200, "get_file_content", "file_id", "file-abc123"},
		{"DELETE", "/v1/files/file-xyz", 200, "delete_file", "file_id", "file-xyz"},
	}

	for _, tt := range tests {
		req := httptest.NewRequest(tt.method, tt.path, nil)
		w := httptest.NewRecorder()
		rt.ServeHTTP(w, req)
		if w.Code != tt.code {
			t.Errorf("%s %s: got %d, want %d", tt.method, tt.path, w.Code, tt.code)
		}
		if tt.body != "" && w.Body.String() != tt.body {
			t.Errorf("%s %s: body got %q, want %q", tt.method, tt.path, w.Body.String(), tt.body)
		}
	}
}

func TestRouterParamExtraction(t *testing.T) {
	rt := NewRouter()

	var capturedParams map[string]string
	rt.Handle("GET", "/v1/threads/{thread_id}/runs/{run_id}/steps/{step_id}", func(w http.ResponseWriter, r *http.Request) {
		capturedParams = map[string]string{
			"thread_id": r.URL.Query().Get("thread_id"),
			"run_id":    r.URL.Query().Get("run_id"),
			"step_id":   r.URL.Query().Get("step_id"),
		}
		w.WriteHeader(200)
	})

	req := httptest.NewRequest("GET", "/v1/threads/t-1/runs/r-2/steps/s-3", nil)
	w := httptest.NewRecorder()
	rt.ServeHTTP(w, req)

	if w.Code != 200 {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	if capturedParams["thread_id"] != "t-1" {
		t.Errorf("thread_id = %q, want %q", capturedParams["thread_id"], "t-1")
	}
	if capturedParams["run_id"] != "r-2" {
		t.Errorf("run_id = %q, want %q", capturedParams["run_id"], "r-2")
	}
	if capturedParams["step_id"] != "s-3" {
		t.Errorf("step_id = %q, want %q", capturedParams["step_id"], "s-3")
	}
}

func TestRouterNotFound(t *testing.T) {
	rt := NewRouter()
	rt.Handle("GET", "/healthz", dummyHandler("ok"))

	req := httptest.NewRequest("GET", "/nonexistent", nil)
	w := httptest.NewRecorder()
	rt.ServeHTTP(w, req)

	if w.Code != 404 {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

func TestRouterMethodNotAllowed(t *testing.T) {
	rt := NewRouter()
	rt.Handle("GET", "/v1/models", dummyHandler("list"))
	rt.Handle("POST", "/v1/chat/completions", dummyHandler("chat"))

	// GET to a POST-only endpoint.
	req := httptest.NewRequest("GET", "/v1/chat/completions", nil)
	w := httptest.NewRecorder()
	rt.ServeHTTP(w, req)

	if w.Code != 405 {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestRouterMethodNotAllowedParametric(t *testing.T) {
	rt := NewRouter()
	rt.Handle("GET", "/v1/models/{model}", dummyHandler("get"))
	rt.Handle("DELETE", "/v1/models/{model}", dummyHandler("delete"))

	// POST to a GET/DELETE-only parametric endpoint.
	req := httptest.NewRequest("POST", "/v1/models/gpt-4", nil)
	w := httptest.NewRecorder()
	rt.ServeHTTP(w, req)

	if w.Code != 405 {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestRouterPriorityStaticOverParametric(t *testing.T) {
	rt := NewRouter()
	// Static route should win over parametric.
	rt.Handle("POST", "/v1/threads/runs", dummyHandler("threads_runs"))
	rt.Handle("GET", "/v1/threads/{thread_id}", dummyHandler("get_thread"))

	req := httptest.NewRequest("POST", "/v1/threads/runs", nil)
	w := httptest.NewRecorder()
	rt.ServeHTTP(w, req)

	if w.Code != 200 || w.Body.String() != "threads_runs" {
		t.Errorf("expected static route to win: code=%d body=%q", w.Code, w.Body.String())
	}
}

func TestRouterSpecificBeforeGeneric(t *testing.T) {
	rt := NewRouter()
	// More specific parametric route registered first.
	rt.Handle("POST", "/v1/threads/{thread_id}/runs/{run_id}/cancel", dummyHandler("cancel"))
	rt.Handle("GET", "/v1/threads/{thread_id}/runs/{run_id}", dummyHandler("get_run"))

	req := httptest.NewRequest("POST", "/v1/threads/t1/runs/r1/cancel", nil)
	w := httptest.NewRecorder()
	rt.ServeHTTP(w, req)

	if w.Body.String() != "cancel" {
		t.Errorf("expected cancel handler, got %q", w.Body.String())
	}
}

// TestRouterFullRouteTable tests with a route table similar to the real gateway.
func TestRouterFullRouteTable(t *testing.T) {
	rt := NewRouter()

	// Register all real gateway routes.
	rt.Handle("POST", "/v1/chat/completions", dummyHandler("chat"))
	rt.Handle("POST", "/v1/completions", dummyHandler("text"))
	rt.Handle("POST", "/v1/embeddings", dummyHandler("embed"))
	rt.Handle("POST", "/v1/images/generations", dummyHandler("image"))
	rt.Handle("POST", "/v1/audio/speech", dummyHandler("speech"))
	rt.Handle("POST", "/v1/audio/transcriptions", dummyHandler("transcription"))
	rt.Handle("POST", "/v1/audio/translations", dummyHandler("translation"))
	rt.Handle("POST", "/v1/rerank", dummyHandler("rerank"))
	rt.Handle("GET", "/v1/.well-known/agent.json", dummyHandler("agent_card"))
	rt.Handle("POST", "/v1/a2a", dummyHandler("a2a"))
	rt.Handle("POST", "/v1/responses", dummyHandler("create_response"))
	rt.Handle("GET", "/v1/responses/{id}", dummyHandler("get_response"))
	rt.Handle("DELETE", "/v1/responses/{id}", dummyHandler("delete_response"))
	rt.Handle("POST", "/v1/files", dummyHandler("upload_file"))
	rt.Handle("GET", "/v1/files", dummyHandler("list_files"))
	rt.Handle("GET", "/v1/files/{file_id}", dummyHandler("get_file"))
	rt.Handle("DELETE", "/v1/files/{file_id}", dummyHandler("delete_file"))
	rt.Handle("GET", "/v1/files/{file_id}/content", dummyHandler("get_file_content"))
	rt.Handle("POST", "/v1/messages/count_tokens", dummyHandler("count_tokens_anthropic"))
	rt.Handle("POST", "/v1/messages", dummyHandler("messages"))
	rt.Handle("POST", "/v1/count_tokens", dummyHandler("count_tokens"))
	rt.Handle("GET", "/v1/models", dummyHandler("list_models"))
	rt.Handle("GET", "/v1/models/{model}", dummyHandler("get_model"))
	rt.Handle("POST", "/v1/assistants", dummyHandler("create_assistant"))
	rt.Handle("GET", "/v1/assistants", dummyHandler("list_assistants"))
	rt.Handle("GET", "/v1/assistants/{assistant_id}", dummyHandler("get_assistant"))
	rt.Handle("POST", "/v1/assistants/{assistant_id}", dummyHandler("update_assistant"))
	rt.Handle("DELETE", "/v1/assistants/{assistant_id}", dummyHandler("delete_assistant"))
	rt.Handle("POST", "/v1/threads", dummyHandler("create_thread"))
	rt.Handle("POST", "/v1/threads/runs", dummyHandler("create_thread_and_run"))
	rt.Handle("GET", "/v1/threads/{thread_id}", dummyHandler("get_thread"))
	rt.Handle("POST", "/v1/threads/{thread_id}", dummyHandler("update_thread"))
	rt.Handle("DELETE", "/v1/threads/{thread_id}", dummyHandler("delete_thread"))
	rt.Handle("POST", "/v1/threads/{thread_id}/messages", dummyHandler("create_message"))
	rt.Handle("GET", "/v1/threads/{thread_id}/messages", dummyHandler("list_messages"))
	rt.Handle("GET", "/v1/threads/{thread_id}/messages/{message_id}", dummyHandler("get_message"))
	rt.Handle("POST", "/v1/threads/{thread_id}/runs", dummyHandler("create_run"))
	rt.Handle("GET", "/v1/threads/{thread_id}/runs", dummyHandler("list_runs"))
	rt.Handle("POST", "/v1/threads/{thread_id}/runs/{run_id}/cancel", dummyHandler("cancel_run"))
	rt.Handle("POST", "/v1/threads/{thread_id}/runs/{run_id}/submit_tool_outputs", dummyHandler("submit_tool_outputs"))
	rt.Handle("GET", "/v1/threads/{thread_id}/runs/{run_id}", dummyHandler("get_run"))
	rt.Handle("GET", "/v1/threads/{thread_id}/runs/{run_id}/steps", dummyHandler("list_steps"))
	rt.Handle("GET", "/v1/threads/{thread_id}/runs/{run_id}/steps/{step_id}", dummyHandler("get_step"))
	rt.Handle("POST", "/v1/vector_stores", dummyHandler("create_vector_store"))
	rt.Handle("GET", "/v1/vector_stores", dummyHandler("list_vector_stores"))
	rt.Handle("POST", "/v1/search", dummyHandler("search"))
	rt.Handle("POST", "/v1/ocr", dummyHandler("ocr"))
	rt.Handle("GET", "/healthz", dummyHandler("health"))
	rt.Handle("GET", "/readyz", dummyHandler("ready"))
	rt.Handle("GET", "/livez", dummyHandler("live"))
	rt.Handle("POST", "/-/reload", dummyHandler("reload"))
	rt.Handle("GET", "/-/config", dummyHandler("config"))
	rt.Handle("GET", "/-/health/providers", dummyHandler("provider_health"))
	rt.Handle("GET", "/-/metrics", dummyHandler("metrics"))

	tests := []struct {
		method string
		path   string
		body   string
	}{
		// Static routes.
		{"POST", "/v1/chat/completions", "chat"},
		{"GET", "/v1/models", "list_models"},
		{"GET", "/healthz", "health"},
		{"POST", "/-/reload", "reload"},
		{"POST", "/v1/search", "search"},
		{"GET", "/v1/.well-known/agent.json", "agent_card"},
		{"POST", "/v1/a2a", "a2a"},
		// Parametric routes.
		{"GET", "/v1/models/gpt-4o", "get_model"},
		{"GET", "/v1/files/f-123/content", "get_file_content"},
		{"GET", "/v1/threads/t-1/runs/r-2/steps/s-3", "get_step"},
		{"POST", "/v1/threads/t-1/runs/r-2/cancel", "cancel_run"},
		// Static should win over parametric.
		{"POST", "/v1/threads/runs", "create_thread_and_run"},
	}

	for _, tt := range tests {
		req := httptest.NewRequest(tt.method, tt.path, nil)
		w := httptest.NewRecorder()
		rt.ServeHTTP(w, req)

		if w.Code != 200 {
			t.Errorf("%s %s: got %d, want 200", tt.method, tt.path, w.Code)
		}
		if w.Body.String() != tt.body {
			t.Errorf("%s %s: body got %q, want %q", tt.method, tt.path, w.Body.String(), tt.body)
		}
	}
}

// Benchmarks — compare the fast router against different route types.

func setupBenchRouter() *Router {
	rt := NewRouter()
	// Replicate real gateway route table.
	rt.Handle("POST", "/v1/chat/completions", dummyHandler("chat"))
	rt.Handle("POST", "/v1/completions", dummyHandler("text"))
	rt.Handle("POST", "/v1/embeddings", dummyHandler("embed"))
	rt.Handle("POST", "/v1/images/generations", dummyHandler("image"))
	rt.Handle("POST", "/v1/audio/speech", dummyHandler("speech"))
	rt.Handle("POST", "/v1/audio/transcriptions", dummyHandler("transcription"))
	rt.Handle("POST", "/v1/audio/translations", dummyHandler("translation"))
	rt.Handle("POST", "/v1/rerank", dummyHandler("rerank"))
	rt.Handle("GET", "/v1/.well-known/agent.json", dummyHandler("agent_card"))
	rt.Handle("POST", "/v1/a2a", dummyHandler("a2a"))
	rt.Handle("POST", "/v1/responses", dummyHandler("response"))
	rt.Handle("GET", "/v1/responses/{id}", dummyHandler("get_response"))
	rt.Handle("DELETE", "/v1/responses/{id}", dummyHandler("delete_response"))
	rt.Handle("POST", "/v1/files", dummyHandler("upload"))
	rt.Handle("GET", "/v1/files", dummyHandler("list_files"))
	rt.Handle("GET", "/v1/files/{file_id}", dummyHandler("get_file"))
	rt.Handle("DELETE", "/v1/files/{file_id}", dummyHandler("delete_file"))
	rt.Handle("GET", "/v1/files/{file_id}/content", dummyHandler("content"))
	rt.Handle("POST", "/v1/messages/count_tokens", dummyHandler("count"))
	rt.Handle("POST", "/v1/messages", dummyHandler("messages"))
	rt.Handle("POST", "/v1/count_tokens", dummyHandler("count"))
	rt.Handle("GET", "/v1/models", dummyHandler("models"))
	rt.Handle("GET", "/v1/models/{model}", dummyHandler("get_model"))
	rt.Handle("POST", "/v1/assistants", dummyHandler("create_assistant"))
	rt.Handle("GET", "/v1/assistants", dummyHandler("list_assistants"))
	rt.Handle("GET", "/v1/assistants/{assistant_id}", dummyHandler("get_assistant"))
	rt.Handle("POST", "/v1/threads", dummyHandler("create_thread"))
	rt.Handle("POST", "/v1/threads/runs", dummyHandler("thread_run"))
	rt.Handle("GET", "/v1/threads/{thread_id}", dummyHandler("get_thread"))
	rt.Handle("POST", "/v1/threads/{thread_id}/messages", dummyHandler("create_message"))
	rt.Handle("GET", "/v1/threads/{thread_id}/messages", dummyHandler("list_messages"))
	rt.Handle("GET", "/v1/threads/{thread_id}/messages/{message_id}", dummyHandler("get_message"))
	rt.Handle("POST", "/v1/threads/{thread_id}/runs", dummyHandler("create_run"))
	rt.Handle("GET", "/v1/threads/{thread_id}/runs", dummyHandler("list_runs"))
	rt.Handle("POST", "/v1/threads/{thread_id}/runs/{run_id}/cancel", dummyHandler("cancel"))
	rt.Handle("POST", "/v1/threads/{thread_id}/runs/{run_id}/submit_tool_outputs", dummyHandler("submit"))
	rt.Handle("GET", "/v1/threads/{thread_id}/runs/{run_id}", dummyHandler("get_run"))
	rt.Handle("GET", "/v1/threads/{thread_id}/runs/{run_id}/steps", dummyHandler("list_steps"))
	rt.Handle("GET", "/v1/threads/{thread_id}/runs/{run_id}/steps/{step_id}", dummyHandler("get_step"))
	rt.Handle("POST", "/v1/vector_stores", dummyHandler("vs"))
	rt.Handle("GET", "/v1/vector_stores", dummyHandler("list_vs"))
	rt.Handle("POST", "/v1/vector_stores/{vector_store_id}/search", dummyHandler("search_vs"))
	rt.Handle("GET", "/v1/vector_stores/{vector_store_id}", dummyHandler("get_vs"))
	rt.Handle("POST", "/v1/search", dummyHandler("search"))
	rt.Handle("POST", "/v1/ocr", dummyHandler("ocr"))
	rt.Handle("GET", "/v1/realtime", dummyHandler("realtime"))
	rt.Handle("POST", "/v1/videos", dummyHandler("submit_video"))
	rt.Handle("GET", "/v1/videos", dummyHandler("list_videos"))
	rt.Handle("GET", "/v1/videos/{video_id}", dummyHandler("get_video"))
	rt.Handle("GET", "/v1/async/{job_id}", dummyHandler("get_async"))
	rt.Handle("GET", "/healthz", dummyHandler("health"))
	rt.Handle("GET", "/readyz", dummyHandler("ready"))
	rt.Handle("GET", "/livez", dummyHandler("live"))
	rt.Handle("POST", "/-/reload", dummyHandler("reload"))
	rt.Handle("GET", "/-/config", dummyHandler("config"))
	rt.Handle("GET", "/-/health/providers", dummyHandler("provider_health"))
	rt.Handle("GET", "/-/metrics", dummyHandler("metrics"))
	rt.Handle("POST", "/-/batches", dummyHandler("batch"))
	rt.Handle("GET", "/-/batches/{batch_id}", dummyHandler("get_batch"))
	rt.Handle("GET", "/-/keys", dummyHandler("list_keys"))
	rt.Handle("POST", "/-/keys", dummyHandler("create_key"))
	rt.Handle("GET", "/-/keys/{key_id}", dummyHandler("get_key"))
	rt.Handle("DELETE", "/-/keys/{key_id}", dummyHandler("delete_key"))
	rt.Handle("PUT", "/-/orgs/{org_id}/config", dummyHandler("set_org_config"))
	rt.Handle("GET", "/-/orgs/{org_id}/config", dummyHandler("get_org_config"))
	rt.Handle("GET", "/-/orgs/configs", dummyHandler("list_org_configs"))
	return rt
}

// BenchmarkRouterStaticHot benchmarks the hot path: POST /v1/chat/completions.
func BenchmarkRouterStaticHot(b *testing.B) {
	rt := setupBenchRouter()
	req := httptest.NewRequest("POST", "/v1/chat/completions", nil)
	w := httptest.NewRecorder()

	b.ResetTimer()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		w.Body.Reset()
		rt.ServeHTTP(w, req)
	}
}

// BenchmarkRouterStaticHealth benchmarks GET /healthz.
func BenchmarkRouterStaticHealth(b *testing.B) {
	rt := setupBenchRouter()
	req := httptest.NewRequest("GET", "/healthz", nil)
	w := httptest.NewRecorder()

	b.ResetTimer()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		w.Body.Reset()
		rt.ServeHTTP(w, req)
	}
}

// BenchmarkRouterParametric1 benchmarks a route with 1 param: GET /v1/models/{model}.
func BenchmarkRouterParametric1(b *testing.B) {
	rt := setupBenchRouter()
	req := httptest.NewRequest("GET", "/v1/models/gpt-4o", nil)
	w := httptest.NewRecorder()

	b.ResetTimer()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		w.Body.Reset()
		rt.ServeHTTP(w, req)
	}
}

// BenchmarkRouterParametric3 benchmarks a deeply nested route with 3 params.
func BenchmarkRouterParametric3(b *testing.B) {
	rt := setupBenchRouter()
	req := httptest.NewRequest("GET", "/v1/threads/t-1/runs/r-2/steps/s-3", nil)
	w := httptest.NewRecorder()

	b.ResetTimer()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		w.Body.Reset()
		rt.ServeHTTP(w, req)
	}
}

// BenchmarkRouterNotFound benchmarks a 404 response.
func BenchmarkRouterNotFound(b *testing.B) {
	rt := setupBenchRouter()
	req := httptest.NewRequest("GET", "/v1/nonexistent/path/foo", nil)
	w := httptest.NewRecorder()

	b.ResetTimer()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		w.Body.Reset()
		rt.ServeHTTP(w, req)
	}
}
