package ratelimit_test

// R7-LIMITS (NG-13) route by route: which requests the org's rate_limiting
// section counts and limits. The console's Org Config > Rate Limiting tab
// (RateLimitingConfigTab.jsx) and the C35 release note say exactly this:
//
//   - counted: chat completions, completions, Responses, Anthropic Messages
//     (its count_tokens too, when an Anthropic-format provider serves it)
//     and Gemini (/v1beta), the routes whose handler stamps the org's limits
//     (applyOrgRateLimitOverrides) and runs the pipeline;
//   - not counted or limited: embeddings (/v1/embeddings), images, audio,
//     rerank, search, OCR, Assistants, video, realtime and scheduled
//     requests, whose handlers do not stamp them (Assistants stamps them
//     but never runs the pipeline);
//   - per_user_rpm's user: on chat completions x-agentcc-user-id (or
//     metadata.agent.user_id), else the body's user; on completions and
//     Responses the body's user; on Messages and Gemini none;
//   - a cached answer is neither counted nor refused (the cache plugin, at
//     priority 35, answers before the rate limit plugin, at 80).
//
// IF ONE OF THESE TESTS FAILS because a route now counts (or stops counting)
// against the org's limits, the copy is out of date: update the tab's scope
// note and help texts (frontend/src/sections/gateway/settings/
// RateLimitingConfigTab.jsx and its R7Limits test) and the C35 release note
// together with this file.

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// post sends body to handler as a request to path with key, and hdr set.
func (g *r7Gateway) post(handler http.HandlerFunc, path, key, body string, hdr map[string]string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("Content-Type", "application/json")
	for k, v := range hdr {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	handler(w, req)
	return w
}

// r7Route is one route: how to send a request on it with a key, extra body
// fields (a leading comma, or "") and headers.
type r7Route struct {
	name string
	send func(g *r7Gateway, key, extra string, hdr map[string]string) *httptest.ResponseRecorder
	// openAIErrors: a refusal is the OpenAI error object (code, param) with
	// Retry-After; otherwise it is the route's own API's error, of type
	// nativeError, whose message names the field and when to retry.
	openAIErrors bool
	nativeError  string
}

var r7CountedRoutes = []r7Route{
	{name: "chat completions", openAIErrors: true, send: func(g *r7Gateway, key, extra string, hdr map[string]string) *httptest.ResponseRecorder {
		return g.chat(key, extra, hdr)
	}},
	{name: "completions", openAIErrors: true, send: func(g *r7Gateway, key, extra string, hdr map[string]string) *httptest.ResponseRecorder {
		return g.post(g.h.TextCompletion, "/v1/completions", key, `{"model":"`+r7Model+`","prompt":"Say hello."`+extra+`}`, hdr)
	}},
	{name: "Responses", openAIErrors: true, send: func(g *r7Gateway, key, extra string, hdr map[string]string) *httptest.ResponseRecorder {
		// The Responses handler reads the key KeyAuth authenticated (the
		// org a stored response belongs to), as the server's router runs it.
		gated := middleware.KeyAuth(g.ks, true, nil, "/v1/responses")(http.HandlerFunc(g.h.CreateResponse))
		return g.post(gated.ServeHTTP, "/v1/responses", key, `{"model":"`+r7Model+`","input":"Say hello."`+extra+`}`, hdr)
	}},
	{name: "Anthropic Messages", nativeError: "rate_limit_error", send: func(g *r7Gateway, key, extra string, hdr map[string]string) *httptest.ResponseRecorder {
		return g.post(g.h.AnthropicMessages, "/v1/messages", key, `{"model":"`+r7Model+`","max_tokens":16,"messages":[{"role":"user","content":"Say hello."}]`+extra+`}`, hdr)
	}},
	{name: "Gemini generateContent", nativeError: "RESOURCE_EXHAUSTED", send: func(g *r7Gateway, key, extra string, hdr map[string]string) *httptest.ResponseRecorder {
		action := r7Model + ":generateContent"
		h := map[string]string{"x-goog-api-key": key}
		for k, v := range hdr {
			h[k] = v
		}
		req := httptest.NewRequest(http.MethodPost, "/v1beta/models/"+action+"?model_action="+action,
			strings.NewReader(`{"contents":[{"role":"user","parts":[{"text":"Say hello."}]}]`+extra+`}`))
		req.Header.Set("Content-Type", "application/json")
		for k, v := range h {
			req.Header.Set(k, v)
		}
		w := httptest.NewRecorder()
		g.h.GenAIHandler(w, req)
		return w
	}},
}

var r7UncountedRoutes = []r7Route{
	{name: "embeddings", send: func(g *r7Gateway, key, _ string, hdr map[string]string) *httptest.ResponseRecorder {
		return g.post(g.h.CreateEmbedding, "/v1/embeddings", key, `{"model":"`+r7Model+`","input":"hello there, embed me"}`, hdr)
	}},
	{name: "images", send: func(g *r7Gateway, key, _ string, hdr map[string]string) *httptest.ResponseRecorder {
		return g.post(g.h.CreateImage, "/v1/images/generations", key, `{"model":"`+r7Model+`","prompt":"a red square","response_format":"b64_json"}`, hdr)
	}},
	{name: "audio speech", send: func(g *r7Gateway, key, _ string, hdr map[string]string) *httptest.ResponseRecorder {
		return g.post(g.h.CreateSpeech, "/v1/audio/speech", key, `{"model":"`+r7Model+`","input":"hello","voice":"alloy"}`, hdr)
	}},
	{name: "OCR", send: func(g *r7Gateway, key, _ string, hdr map[string]string) *httptest.ResponseRecorder {
		return g.post(g.h.OCR, "/v1/ocr", key, `{"model":"`+r7Model+`","document":{"type":"image_url","image_url":"data:image/png;base64,aGVsbG8="}}`, hdr)
	}},
	{name: "Assistants", send: func(g *r7Gateway, key, _ string, hdr map[string]string) *httptest.ResponseRecorder {
		return g.post(g.h.CreateAssistant, "/v1/assistants", key, `{"model":"`+r7Model+`","name":"r7"}`, hdr)
	}},
}

// r7Refused checks a 429 from field on route.
func r7Refused(t *testing.T, rt r7Route, what string, w *httptest.ResponseRecorder, field string) {
	t.Helper()
	if rt.openAIErrors {
		r7Want(t, what, w, http.StatusTooManyRequests, field)
		return
	}
	if w.Code != http.StatusTooManyRequests {
		t.Fatalf("%s: %d %s, want 429 naming %s", what, w.Code, strings.TrimSpace(w.Body.String()), field)
	}
	if b := w.Body.String(); !strings.Contains(b, `"`+rt.nativeError+`"`) || !strings.Contains(b, "("+field+" = ") || !strings.Contains(b, "Please retry after") {
		t.Errorf("%s: 429 body %s, want a %s error whose message names %s and when to retry", what, strings.TrimSpace(b), rt.nativeError, field)
	}
}

func r7OK(t *testing.T, what string, w *httptest.ResponseRecorder) {
	t.Helper()
	if w.Code != http.StatusOK {
		t.Fatalf("%s: %d %s, want 200", what, w.Code, strings.TrimSpace(w.Body.String()))
	}
}

// Each counted route: global_rpm=1 counts the org's requests from every key
// together, and refuses the second.
func TestR7RoutesOrgRPMCounted(t *testing.T) {
	for _, rt := range r7CountedRoutes {
		t.Run(rt.name, func(t *testing.T) {
			g := newR7Gateway(t, &tenant.RateLimitConfig{Enabled: true, GlobalRPM: 1}, 2)
			r7OK(t, rt.name+" org-a key1", rt.send(g, g.keys[0], "", nil))
			r7Refused(t, rt, rt.name+" org-a key2", rt.send(g, g.keys[1], "", nil), "global_rpm")
			r7OK(t, rt.name+" org-b", rt.send(g, g.keys[2], "", nil))
		})
	}
}

// /v1/messages/count_tokens, served by an Anthropic-format provider, runs
// the pipeline as /v1/messages does: global_rpm counts it.
func TestR7RoutesAnthropicCountTokensCounted(t *testing.T) {
	g := newR7Gateway(t, &tenant.RateLimitConfig{Enabled: true, GlobalRPM: 1}, 2, r7WithAnthropic())
	rt := r7Route{name: "Anthropic count_tokens", nativeError: "rate_limit_error"}
	body := `{"model":"` + r7AnthropicModel + `","messages":[{"role":"user","content":"Say hello."}]}`
	r7OK(t, "count_tokens org-a key1", g.post(g.h.AnthropicCountTokens, "/v1/messages/count_tokens", g.keys[0], body, nil))
	r7Refused(t, rt, "count_tokens org-a key2", g.post(g.h.AnthropicCountTokens, "/v1/messages/count_tokens", g.keys[1], body, nil), "global_rpm")
}

// Each counted route: global_tpm=1000 counts the 2000 tokens the upstream
// reports for the first call, and refuses the next.
func TestR7RoutesOrgTPMCounted(t *testing.T) {
	for _, rt := range r7CountedRoutes {
		t.Run(rt.name, func(t *testing.T) {
			g := newR7Gateway(t, &tenant.RateLimitConfig{Enabled: true, GlobalTPM: 1000}, 2)
			r7OK(t, rt.name+" org-a key1", rt.send(g, g.keys[0], "", nil))
			r7Refused(t, rt, rt.name+" org-a key2", rt.send(g, g.keys[1], "", nil), "global_tpm")
		})
	}
}

// Each route the section does not cover: its requests consume nothing of the
// org's global_rpm, per_key_rpm or per_user_rpm (a chat request of the same
// key and user after them is let through), and are not refused once the org
// is over its limits.
func TestR7RoutesNotCounted(t *testing.T) {
	user := map[string]string{"x-agentcc-user-id": "u1"}
	for _, rt := range r7UncountedRoutes {
		t.Run(rt.name, func(t *testing.T) {
			g := newR7Gateway(t, &tenant.RateLimitConfig{Enabled: true, GlobalRPM: 1, PerKeyRPM: 1, PerUserRPM: 1}, 1)
			for i := 0; i < 3; i++ {
				r7OK(t, rt.name+" before the chat request", rt.send(g, g.keys[0], "", user))
			}
			r7OK(t, "chat of the same key and user after the "+rt.name+" requests", g.chat(g.keys[0], "", user))
			if w := g.chat(g.keys[0], "", user); w.Code != http.StatusTooManyRequests {
				t.Fatalf("second chat: %d %s, want 429 (the limits are live)", w.Code, strings.TrimSpace(w.Body.String()))
			}
			r7OK(t, rt.name+" with the org over its limits", rt.send(g, g.keys[0], "", user))
		})
	}
}

// per_user_rpm on each counted route: which user it counts, and the routes
// on which no request names one.
func TestR7RoutesPerUserRPM(t *testing.T) {
	byName := map[string]r7Route{}
	for _, rt := range r7CountedRoutes {
		byName[rt.name] = rt
	}
	header := map[string]string{"x-agentcc-user-id": "u1"}

	// Completions and Responses: the body's user; the header is not read.
	for _, name := range []string{"completions", "Responses"} {
		rt := byName[name]
		t.Run(name, func(t *testing.T) {
			g := newR7Gateway(t, &tenant.RateLimitConfig{Enabled: true, PerUserRPM: 1}, 2)
			r7OK(t, name+" body user u1 via key1", rt.send(g, g.keys[0], `,"user":"u1"`, nil))
			r7Refused(t, rt, name+" body user u1 via key2", rt.send(g, g.keys[1], `,"user":"u1"`, nil), "per_user_rpm")
			r7OK(t, name+" body user u2", rt.send(g, g.keys[0], `,"user":"u2"`, nil))
			for i := 0; i < 3; i++ {
				r7OK(t, name+" x-agentcc-user-id only", rt.send(g, g.keys[0], "", header))
			}
		})
	}

	// Messages and Gemini: no user is read, so the field limits nothing.
	for _, c := range []struct{ name, extra string }{
		{"Anthropic Messages", `,"metadata":{"user_id":"u1"}`},
		{"Gemini generateContent", ""},
	} {
		rt := byName[c.name]
		t.Run(c.name, func(t *testing.T) {
			g := newR7Gateway(t, &tenant.RateLimitConfig{Enabled: true, PerUserRPM: 1}, 1)
			for i := 0; i < 3; i++ {
				r7OK(t, c.name+" naming u1", rt.send(g, g.keys[0], c.extra, header))
			}
		})
	}
}

// A cached answer is neither counted nor refused: with global_rpm=2, two
// hits of a cached prompt leave the org's second request free, and a hit is
// served once the org is over its limit.
func TestR7RoutesCacheHitNotCountedOrRefused(t *testing.T) {
	g := newR7Gateway(t, &tenant.RateLimitConfig{Enabled: true, GlobalRPM: 2}, 1, r7WithCache())
	cached := `,"temperature":0`
	r7OK(t, "prompt A (miss, counted)", g.chat(g.keys[0], cached, nil))
	r7OK(t, "prompt A again (hit)", g.chat(g.keys[0], cached, nil))
	r7OK(t, "prompt A again (hit)", g.chat(g.keys[0], cached, nil))
	g.up.mu.Lock()
	calls := g.up.calls
	g.up.mu.Unlock()
	if calls != 1 {
		t.Fatalf("upstream chat calls after one miss and two hits: %d, want 1 (the cache did not answer)", calls)
	}
	r7OK(t, "prompt B (miss, the org's second counted request)", g.chat(g.keys[0], `,"temperature":0.5`, nil))
	r7Want(t, "prompt C (the org's third)", g.chat(g.keys[0], `,"temperature":0.7`, nil), http.StatusTooManyRequests, "global_rpm")
	r7OK(t, "prompt A with the org over its limit (hit)", g.chat(g.keys[0], cached, nil))
}
