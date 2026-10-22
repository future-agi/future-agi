package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	agentcca2a "github.com/futureagi/agentcc-gateway/internal/a2a"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/futureagi/agentcc-gateway/internal/video"
)

// A check on the request reads the input of every API that sends one to a
// provider: chat, /v1/embeddings, /v1/images/generations, the
// audio routes, /v1/rerank, /v1/search, /v1/ocr, /v1beta embedContent,
// /v1/videos, the Assistants API and vector store search.
//
// The probe is pii-detection in block mode, as an organization's
// check and as a rule in the gateway's config file at the pre stage, on a
// request whose text carries a social security number. The upstream is a
// loopback server that answers each API and records every call: a refused
// request must never reach it.

const (
	otherAPIsEmbedding = "text-embedding-3-small"
	otherAPIsImage     = "gpt-image-2"
	otherAPIsSpeech    = "tts-1"
	otherAPIsWhisper   = "whisper-1"
	otherAPIsOCR       = "mistral-ocr-latest"
	otherAPIsRerank    = "rerank-v3.5"
	otherAPIsSearch    = "fake-search"
	otherAPIsVideo     = "fake-video/clip-1"
)

// otherAPIsUpstream answers every API the non-chat routes call, after recording
// the call: embeddings, images, speech, transcriptions, translations, OCR,
// Cohere's rerank, Gemini's embedContent, and the Assistants and Vector
// Stores APIs. A run created with stream true is answered as a stream.
func otherAPIsUpstream(calls *upstreamCalls) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			return
		}
		calls.record(w, r)
		body, _ := io.ReadAll(r.Body)
		path := r.URL.Path
		w.Header().Set("Content-Type", "application/json")
		switch {
		case path == "/v1/embeddings":
			fmt.Fprintf(w, `{"object":"list","data":[{"object":"embedding","index":0,"embedding":[0.1,0.2]}],"model":%q,"usage":{"prompt_tokens":3,"total_tokens":3}}`, otherAPIsEmbedding)
		case path == "/v1/images/generations":
			fmt.Fprint(w, `{"created":1760000000,"data":[{"url":"https://images.example.invalid/1.png"}]}`)
		case path == "/v1/audio/speech":
			w.Header().Set("Content-Type", "audio/mpeg")
			_, _ = w.Write([]byte("ID3-fake-audio"))
		case path == "/v1/audio/transcriptions", path == "/v1/audio/translations":
			fmt.Fprint(w, `{"text":"hello"}`)
		case path == "/v1/ocr":
			fmt.Fprintf(w, `{"pages":[{"index":0,"markdown":"page one"}],"model":%q,"usage_info":{"pages_processed":1}}`, otherAPIsOCR)
		case path == "/v2/rerank":
			fmt.Fprint(w, `{"id":"rr-1","results":[{"index":0,"relevance_score":0.9}],"meta":{"billed_units":{"search_units":1}}}`)
		case strings.HasSuffix(path, ":embedContent"):
			fmt.Fprint(w, `{"embedding":{"values":[0.1,0.2]}}`)
		case strings.HasPrefix(path, "/v1/assistants"), strings.HasPrefix(path, "/v1/threads"), strings.HasPrefix(path, "/v1/vector_stores"):
			if bytes.Contains(body, []byte(`"stream":true`)) {
				w.Header().Set("Content-Type", "text/event-stream")
				fmt.Fprint(w, "event: thread.run.created\ndata: {\"id\":\"run_1\",\"object\":\"thread.run\",\"status\":\"queued\"}\n\nevent: done\ndata: [DONE]\n\n")
				return
			}
			fmt.Fprint(w, `{"id":"obj_1","object":"thread.run","status":"queued"}`)
		}
	}
}

// fakeSearchProvider serves /v1/search (no built-in provider does) and
// records the queries it gets.
type fakeSearchProvider struct {
	fakeVideoProvider
	mu      sync.Mutex
	queries []string
}

func (*fakeSearchProvider) ID() string { return otherAPIsSearch }

func (p *fakeSearchProvider) Search(_ context.Context, req *models.SearchRequest) (*models.SearchResponse, error) {
	p.mu.Lock()
	p.queries = append(p.queries, string(req.QueryRaw))
	p.mu.Unlock()
	return &models.SearchResponse{Object: "search", SearchProvider: otherAPIsSearch, Query: req.QueryRaw,
		Results: []models.SearchResult{{Title: "t", URL: "https://example.invalid", Snippet: "s"}}}, nil
}

func (p *fakeSearchProvider) count() int {
	p.mu.Lock()
	defer p.mu.Unlock()
	return len(p.queries)
}

var _ providers.SearchProvider = (*fakeSearchProvider)(nil)

// otherAPIsGateway is newPostBlockGateway with the models and providers the
// non-chat routes need on the loopback upstream, a fake search provider and a
// fake video provider, and its upstream calls recorded.
type otherAPIsGateway struct {
	*postBlockGateway
	calls  *upstreamCalls
	search *fakeSearchProvider
}

func newOtherAPIsGateway(t *testing.T, setup postBlockSetup) *otherAPIsGateway {
	t.Helper()
	calls := &upstreamCalls{}
	search := &fakeSearchProvider{}
	setup.upstream = otherAPIsUpstream(calls)
	setup.openaiModels = []string{otherAPIsEmbedding, otherAPIsImage, otherAPIsSpeech, otherAPIsWhisper, otherAPIsOCR}
	setup.extraProviders = map[string]config.ProviderConfig{
		"cohere": {APIFormat: "cohere", Models: []string{otherAPIsRerank}},
	}
	setup.register = func(r *providers.Registry) {
		r.RegisterProvider(otherAPIsSearch, search, []string{otherAPIsSearch})
		r.RegisterProvider("fake-video", fakeVideoProvider{}, []string{"clip-1"})
	}
	return &otherAPIsGateway{postBlockGateway: newPostBlockGateway(t, setup), calls: calls, search: search}
}

// providerCalls is how many times a provider got the request: the upstream's
// calls and the fake search provider's.
func (g *otherAPIsGateway) providerCalls() int { return g.calls.count() + g.search.count() }

// videoJobs is how many video jobs the gateway stored.
func (g *otherAPIsGateway) videoJobs(t *testing.T) int {
	t.Helper()
	_, n, err := g.srv.handlers.videoStore.ListByOrg(postBlockOrg, videoListAll())
	if err != nil {
		t.Fatal(err)
	}
	return n
}

func videoListAll() video.VideoListFilters { return video.VideoListFilters{Limit: 100} }

// otherAPISurface is one non-chat route, with a body that carries text. A
// multipart route sends text as its prompt field, with an audio file.
type otherAPISurface struct {
	name      string
	path      string
	body      func(text string) string
	multipart bool
	// logged: the route goes through the pipeline, so its request log
	// records the refusal. The Assistants API, vector store search and
	// /v1/videos have no request log entry.
	logged bool
	// ok is the status of a request that goes on.
	ok int
	// unread: the route's input holds what the checks cannot read (an audio
	// file, a document), so a check that would block or rewrite it refuses
	// it unread (request_unavailable) even when its text passes.
	unread bool
	format wireFormat
	// key is the key the request is sent with: the org's key, or, where only
	// the gateway's own provider serves the route (the fake search
	// provider), the org's internal key, whose requests are not logged.
	key string
}

func jsonBody(format string) func(string) string {
	return func(text string) string {
		quoted, _ := json.Marshal(text)
		return fmt.Sprintf(format, quoted)
	}
}

// otherAPISurfaces are the non-chat routes whose text a check reads.
var otherAPISurfaces = []otherAPISurface{
	{name: "embeddings, a string", path: "/v1/embeddings", body: jsonBody(`{"model":"` + otherAPIsEmbedding + `","input":%s}`), logged: true, ok: 200},
	{name: "embeddings, a list", path: "/v1/embeddings", body: jsonBody(`{"model":"` + otherAPIsEmbedding + `","input":["hello",%s]}`), logged: true, ok: 200},
	{name: "images", path: "/v1/images/generations", body: jsonBody(`{"model":"` + otherAPIsImage + `","prompt":%s}`), logged: true, ok: 200},
	{name: "speech", path: "/v1/audio/speech", body: jsonBody(`{"model":"` + otherAPIsSpeech + `","voice":"alloy","input":%s}`), logged: true, ok: 200},
	{name: "speech stream", path: "/v1/audio/speech/stream", body: jsonBody(`{"model":"` + otherAPIsSpeech + `","voice":"alloy","input":%s}`), logged: true, ok: 200},
	{name: "rerank, the query", path: "/v1/rerank", body: jsonBody(`{"model":"` + otherAPIsRerank + `","query":%s,"documents":["a","b"]}`), logged: true, ok: 200},
	{name: "rerank, a document", path: "/v1/rerank", body: jsonBody(`{"model":"` + otherAPIsRerank + `","query":"which one?","documents":["a",%s]}`), logged: true, ok: 200},
	{name: "search, a string", path: "/v1/search", body: jsonBody(`{"search_provider":"` + otherAPIsSearch + `","query":%s}`), ok: 200, key: postBlockInternalOrgKey},
	{name: "search, a list", path: "/v1/search", body: jsonBody(`{"search_provider":"` + otherAPIsSearch + `","query":["weather",%s]}`), ok: 200, key: postBlockInternalOrgKey},
	{name: "embedContent", path: "/v1beta/models/" + postBlockGemini + ":embedContent", body: jsonBody(`{"content":{"parts":[{"text":%s}]}}`), logged: true, ok: 200, format: genaiWire},
	{name: "embedContent, the title", path: "/v1beta/models/" + postBlockGemini + ":embedContent", body: jsonBody(`{"content":{"parts":[{"text":"a page"}]},"taskType":"RETRIEVAL_DOCUMENT","title":%s}`), logged: true, ok: 200, format: genaiWire},
	{name: "transcription, the prompt", path: "/v1/audio/transcriptions", body: func(text string) string { return text }, multipart: true, logged: true, ok: 200, unread: true},
	{name: "translation, the prompt", path: "/v1/audio/translations", body: func(text string) string { return text }, multipart: true, logged: true, ok: 200, unread: true},
	{name: "assistants create", path: "/v1/assistants", body: jsonBody(`{"model":"gpt-4o","instructions":%s}`), ok: 200},
	{name: "assistants update", path: "/v1/assistants/asst_1", body: jsonBody(`{"instructions":%s}`), ok: 200},
	{name: "threads create", path: "/v1/threads", body: jsonBody(`{"messages":[{"role":"user","content":%s}]}`), ok: 200},
	{name: "threads create, text parts", path: "/v1/threads", body: jsonBody(`{"messages":[{"role":"user","content":[{"type":"text","text":"hi"},{"type":"text","text":%s}]}]}`), ok: 200},
	{name: "messages create", path: "/v1/threads/thread_1/messages", body: jsonBody(`{"role":"user","content":%s}`), ok: 200},
	{name: "runs create, instructions", path: "/v1/threads/thread_1/runs", body: jsonBody(`{"assistant_id":"asst_1","instructions":%s}`), ok: 200},
	{name: "runs create, additional instructions", path: "/v1/threads/thread_1/runs", body: jsonBody(`{"assistant_id":"asst_1","additional_instructions":%s}`), ok: 200},
	{name: "runs create, additional messages", path: "/v1/threads/thread_1/runs", body: jsonBody(`{"assistant_id":"asst_1","additional_messages":[{"role":"user","content":%s}]}`), ok: 200},
	{name: "runs create, streamed", path: "/v1/threads/thread_1/runs", body: jsonBody(`{"assistant_id":"asst_1","stream":true,"additional_messages":[{"role":"user","content":%s}]}`), ok: 200},
	{name: "thread and run", path: "/v1/threads/runs", body: jsonBody(`{"assistant_id":"asst_1","thread":{"messages":[{"role":"user","content":%s}]}}`), ok: 200},
	{name: "thread and run, instructions", path: "/v1/threads/runs", body: jsonBody(`{"assistant_id":"asst_1","instructions":%s}`), ok: 200},
	{name: "submit tool outputs", path: "/v1/threads/thread_1/runs/run_1/submit_tool_outputs", body: jsonBody(`{"tool_outputs":[{"tool_call_id":"call_1","output":%s}]}`), ok: 200},
	{name: "vector store search", path: "/v1/vector_stores/vs_1/search", body: jsonBody(`{"query":%s}`), ok: 200},
	{name: "vector store search, a list", path: "/v1/vector_stores/vs_1/search", body: jsonBody(`{"query":["refunds",%s]}`), ok: 200},
	{name: "videos", path: "/v1/videos", body: jsonBody(`{"model":"` + otherAPIsVideo + `","prompt":%s}`), ok: 202},
}

// send sends sf's body with text with key (sf's own key when it names
// one), with headers.
func (g *otherAPIsGateway) send(t *testing.T, key string, sf otherAPISurface, text string, headers map[string]string) (int, string) {
	t.Helper()
	if sf.key != "" {
		key = sf.key
	}
	if !sf.multipart {
		return g.doAs(t, key, sf.path, sf.body(text), headers)
	}
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	fw, _ := mw.CreateFormFile("file", "speech.mp3")
	_, _ = fw.Write([]byte("ID3-fake-audio"))
	_ = mw.WriteField("model", otherAPIsWhisper)
	_ = mw.WriteField("prompt", sf.body(text))
	_ = mw.Close()
	req := httptest.NewRequest(http.MethodPost, sf.path, &buf)
	req.Header.Set("Content-Type", mw.FormDataContentType())
	req.Header.Set("Authorization", "Bearer "+key)
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	ctx, cancel := context.WithTimeout(req.Context(), 5*time.Second)
	defer cancel()
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req.WithContext(ctx))
	b, _ := io.ReadAll(w.Result().Body)
	return w.Code, string(b)
}

// sendWithHeaders is send, with the response's headers too.
func (g *otherAPIsGateway) sendWithHeaders(t *testing.T, key string, sf otherAPISurface, text string) (int, string, http.Header) {
	t.Helper()
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	fw, _ := mw.CreateFormFile("file", "speech.mp3")
	_, _ = fw.Write([]byte("ID3-fake-audio"))
	_ = mw.WriteField("model", otherAPIsWhisper)
	_ = mw.WriteField("prompt", sf.body(text))
	_ = mw.Close()
	req := httptest.NewRequest(http.MethodPost, sf.path, &buf)
	req.Header.Set("Content-Type", mw.FormDataContentType())
	req.Header.Set("Authorization", "Bearer "+key)
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	b, _ := io.ReadAll(w.Result().Body)
	return w.Code, string(b), w.Result().Header
}

// assertOtherAPIRefused: the client got the guardrail's 403 naming the
// check (content_blocked, or request_unavailable on a route whose input the
// checks cannot all read), no provider got the request, no video job was
// stored, and, on a route the request log records, the log records 403 and
// the block.
func assertOtherAPIRefused(t *testing.T, g *otherAPIsGateway, sf otherAPISurface, code int, body string) {
	t.Helper()
	if code != http.StatusForbidden {
		t.Errorf("status %d, want the guardrail's 403; body %s", code, body)
	}
	if n := g.providerCalls(); n != 0 {
		t.Errorf("a provider got the request %d times (%v), want 0: the request went out", n, g.calls.paths)
	}
	if strings.HasPrefix(sf.path, "/v1/videos") {
		if n := g.videoJobs(t); n != 0 {
			t.Errorf("%d video jobs stored, want 0", n)
		}
	}
	if sf.unread {
		assertUnavailableError(t, sf.format, strings.TrimSpace(body))
	} else {
		assertWireError(t, sf.format, strings.TrimSpace(body), requestCheck)
	}
	if sf.logged {
		e := g.entry(t)
		assertBlockLogged(t, e, requestCheck)
		meta, _ := e["metadata"].(map[string]any)
		if sf.unread && meta["request_unavailable"] == nil {
			t.Errorf("log metadata %v, want request_unavailable", meta)
		}
	}
}

// Every non-chat route refuses a request whose text the check blocks, for both
// shapes of the check, before any provider gets it. A route whose input the
// checks cannot all read (the audio of a transcription or translation) is
// refused unread.
func TestRequestCheck_OtherAPIsRefuseABlockedRequest(t *testing.T) {
	for _, shape := range requestCheckShapes {
		for _, sf := range otherAPISurfaces {
			t.Run(sf.name+" / "+shape.name, func(t *testing.T) {
				g := newOtherAPIsGateway(t, shape.setup)
				code, body := g.send(t, postBlockKey, sf, requestCheckPrompt, nil)
				assertOtherAPIRefused(t, g, sf, code, body)
			})
		}
	}
}

// Control: with the check in block mode, a request whose text it passes
// goes on, on every non-chat route whose input the checks can all read, and a
// provider gets it once (a video job is stored: no video provider runs one
// here). Without a check, every route serves its request.
func TestRequestCheck_OtherAPIsSendAPassingRequest(t *testing.T) {
	for _, shape := range append(requestCheckShapes, struct {
		name  string
		setup postBlockSetup
	}{"no check", postBlockSetup{}}) {
		for _, sf := range otherAPISurfaces {
			if sf.unread && shape.name != "no check" {
				continue
			}
			t.Run(sf.name+" / "+shape.name, func(t *testing.T) {
				g := newOtherAPIsGateway(t, shape.setup)
				code, body := g.send(t, postBlockKey, sf, postBlockPrompt, nil)
				if code != sf.ok {
					t.Fatalf("status %d, want %d; body %s", code, sf.ok, body)
				}
				want := 1
				if strings.HasPrefix(sf.path, "/v1/videos") {
					want = 0
					if n := g.videoJobs(t); n != 1 {
						t.Errorf("%d video jobs stored, want 1", n)
					}
				}
				if n := g.providerCalls(); n != want {
					t.Errorf("a provider got the request %d times, want %d", n, want)
				}
			})
		}
	}
}

// otherAPIUnreadable are non-chat requests whose input the checks cannot read:
// embeddings given as token ids, an OCR document, and the audio of a
// transcription or translation without a prompt. A check that would block
// or rewrite such a request refuses it before any provider gets it, with
// 403 request_unavailable naming the check.
var otherAPIUnreadable = []struct {
	name, path, body string
	multipart        bool
}{
	{"embeddings, token ids", "/v1/embeddings", `{"model":"` + otherAPIsEmbedding + `","input":[1,2,3]}`, false},
	{"embeddings, lists of token ids", "/v1/embeddings", `{"model":"` + otherAPIsEmbedding + `","input":[[1,2],[3]]}`, false},
	{"OCR, a document URL", "/v1/ocr", `{"model":"` + otherAPIsOCR + `","document":{"type":"document_url","document_url":"https://docs.example.invalid/a.pdf"}}`, false},
	{"OCR, an image", "/v1/ocr", `{"model":"` + otherAPIsOCR + `","document":{"type":"image_url","image_url":"data:image/png;base64,iVBORw0KGgo="}}`, false},
	{"transcription, no prompt", "/v1/audio/transcriptions", "", true},
	{"translation, no prompt", "/v1/audio/translations", "", true},
}

func TestRequestCheck_OtherAPIsUnreadableInputIsRefused(t *testing.T) {
	shapes := append([]struct {
		name  string
		setup postBlockSetup
	}{}, requestCheckShapes...)
	for _, rem := range remediations {
		for _, s := range remediationShapes(rem.mode) {
			shapes = append(shapes, struct {
				name  string
				setup postBlockSetup
			}{rem.mode + ", " + s.name, s.setup})
		}
	}
	for _, shape := range shapes {
		for _, tc := range otherAPIUnreadable {
			t.Run(tc.name+" / "+shape.name, func(t *testing.T) {
				g := newOtherAPIsGateway(t, shape.setup)
				sf := otherAPISurface{path: tc.path, body: func(string) string { return tc.body }, multipart: tc.multipart, logged: true, unread: true}
				code, body := g.send(t, postBlockKey, sf, "", nil)
				assertOtherAPIRefused(t, g, sf, code, body)
			})
		}
	}
}

// Control: such a request goes on when no check would block or rewrite it
// (no check, or pii-detection in log mode with no remediation), and the
// request log says why the checks did not read it.
func TestRequestCheck_OtherAPIsUnreadableInputGoesOnWithoutABlockingCheck(t *testing.T) {
	for _, shape := range []struct {
		name  string
		setup postBlockSetup
	}{
		{"no check", postBlockSetup{}},
		{"org check, log", postBlockSetup{orgRequestCheck: requestCheck, requestAction: "log"}},
		{"config rule, log", postBlockSetup{requestRule: requestCheck, requestAction: "log"}},
	} {
		for _, tc := range otherAPIUnreadable {
			t.Run(tc.name+" / "+shape.name, func(t *testing.T) {
				g := newOtherAPIsGateway(t, shape.setup)
				sf := otherAPISurface{path: tc.path, body: func(string) string { return tc.body }, multipart: tc.multipart}
				code, body := g.send(t, postBlockKey, sf, "", nil)
				if code != http.StatusOK || g.providerCalls() != 1 {
					t.Fatalf("status %d, provider calls %d; want 200 and 1; body %s", code, g.providerCalls(), body)
				}
				meta, _ := g.entry(t)["metadata"].(map[string]any)
				if shape.name != "no check" && meta["request_unavailable"] == nil {
					t.Errorf("log metadata %v, want request_unavailable", meta)
				}
			})
		}
	}
}

// A check in log or warn mode reads the prompt of a transcription or
// translation: the request goes on, the response says a guardrail matched,
// and the log records the match as logged or warned.
func TestRequestCheck_AudioPromptIsReadInLogMode(t *testing.T) {
	for _, sf := range otherAPISurfaces {
		if !sf.multipart {
			continue
		}
		for _, action := range []string{"log", "warn"} {
			t.Run(sf.name+" / "+action, func(t *testing.T) {
				g := newOtherAPIsGateway(t, postBlockSetup{orgRequestCheck: requestCheck, requestAction: action})
				code, body, header := g.sendWithHeaders(t, postBlockKey, sf, requestCheckPrompt)
				if code != http.StatusOK || g.providerCalls() != 1 {
					t.Fatalf("status %d, provider calls %d; want 200 and 1; body %s", code, g.providerCalls(), body)
				}
				e := g.entry(t)
				if e["guardrail_triggered"] != true {
					t.Errorf("log guardrail_triggered %v, want true: the check read the prompt", e["guardrail_triggered"])
				}
				if header.Get("x-agentcc-guardrail-triggered") != "true" {
					t.Errorf("x-agentcc-guardrail-triggered %q, want true", header.Get("x-agentcc-guardrail-triggered"))
				}
				want := map[string]string{"log": "logged", "warn": "warned"}[action]
				if meta, _ := e["metadata"].(map[string]any); meta["guardrail_action"] != want {
					t.Errorf("log metadata.guardrail_action %v, want %s", meta["guardrail_action"], want)
				}
			})
		}
	}
}

// A check that rewrites the request (pii-detection's mask or redact) has its
// rewrite reach the provider on every non-chat route whose input it can read
// (the video job stores the rewritten prompt).
func TestRequestCheck_OtherAPIsSendTheRewrite(t *testing.T) {
	for _, rem := range remediations {
		for _, shape := range remediationShapes(rem.mode) {
			for _, sf := range otherAPISurfaces {
				if sf.unread {
					continue
				}
				t.Run(rem.mode+" / "+sf.name+" / "+shape.name, func(t *testing.T) {
					g := newOtherAPIsGateway(t, shape.setup)
					code, resp := g.send(t, postBlockKey, sf, requestCheckPrompt, nil)
					if code != sf.ok {
						t.Fatalf("status %d, want %d; body %s", code, sf.ok, resp)
					}
					var sent string
					switch {
					case strings.HasPrefix(sf.path, "/v1/videos"):
						jobs, _, err := g.srv.handlers.videoStore.ListByOrg(postBlockOrg, videoListAll())
						if err != nil || len(jobs) != 1 {
							t.Fatalf("jobs %v (%v), want 1", jobs, err)
						}
						sent = jobs[0].Prompt
					case strings.HasPrefix(sf.path, "/v1/search"):
						if g.search.count() != 1 {
							t.Fatalf("search provider calls %d, want 1", g.search.count())
						}
						sent = g.search.queries[0]
					default:
						if g.calls.count() != 1 {
							t.Fatalf("provider calls %d, want 1", g.calls.count())
						}
						sent = g.calls.bodies[0]
					}
					if strings.Contains(sent, requestCheckSSN) || strings.Contains(decodedJSON(t, sent), requestCheckSSN) {
						t.Errorf("the provider got the social security number: %s", sent)
					}
					if n := strings.Count(sent, rem.token); n != 1 {
						t.Errorf("the provider got %q %d times, want 1: %s", rem.token, n, sent)
					}
				})
			}
		}
	}
}

// The routes the gateway does not serve answer 404: POST
// /v1/moderations, /v1/images/edits and /v1/images/variations answer 404,
// and no provider gets the request, with or without a check.
func TestRequestCheck_AbsentRoutesReachNoProvider(t *testing.T) {
	for _, path := range []string{"/v1/moderations", "/v1/images/edits", "/v1/images/variations"} {
		for _, shape := range append(requestCheckShapes, struct {
			name  string
			setup postBlockSetup
		}{"no check", postBlockSetup{}}) {
			t.Run(path+" / "+shape.name, func(t *testing.T) {
				g := newOtherAPIsGateway(t, shape.setup)
				code, body := g.do(t, path, `{"model":"gpt-4o","input":`+fmt.Sprintf("%q", requestCheckPrompt)+`,"prompt":`+fmt.Sprintf("%q", requestCheckPrompt)+`}`)
				if code != http.StatusNotFound {
					t.Errorf("status %d, want 404; body %s", code, body)
				}
				if n := g.providerCalls(); n != 0 {
					t.Errorf("a provider got the request %d times, want 0", n)
				}
			})
		}
	}
}

// X-Guardrail-Policy applies on every non-chat route, as on chat: a key
// without allow_policy_override that sends it is refused before any provider
// gets the request; a permitted key's disabled or log-only lets a request the
// rule blocks go on, strict makes a rule in log mode block, and a value that
// is not a policy is refused 400. Without the header the rule runs as the
// config file sets it. The probe is pii-detection as a rule in the config
// file at the pre stage, which the policy governs.
func TestGuardrailPolicyOverride_OtherAPIs(t *testing.T) {
	rule := func(action string) postBlockSetup {
		return postBlockSetup{requestRule: requestCheck, requestAction: action}
	}
	for _, sf := range otherAPISurfaces {
		if sf.key != "" {
			continue // the internal key's routes: covered by the org key's
		}
		want := 1
		if strings.HasPrefix(sf.path, "/v1/videos") {
			want = 0 // the job is stored; no provider runs it
		}
		t.Run(sf.name+" / no permission", func(t *testing.T) {
			g := newOtherAPIsGateway(t, rule("block"))
			code, resp := g.send(t, postBlockKey, sf, postBlockPrompt, map[string]string{"X-Guardrail-Policy": "disabled"})
			if code != http.StatusForbidden || !strings.Contains(resp, "does not allow guardrail policy overrides") {
				t.Errorf("status %d, want 403 saying the key does not allow overrides; body %s", code, resp)
			}
			if n := g.providerCalls(); n != 0 {
				t.Errorf("a provider got the request %d times, want 0", n)
			}
		})
		for _, tc := range []struct {
			policy, action string
			code, calls    int
		}{
			{"disabled", "block", sf.ok, want},
			{"log-only", "block", sf.ok, want},
			{"strict", "log", http.StatusForbidden, 0},
			{"not-a-policy", "block", http.StatusBadRequest, 0},
			{"", "block", http.StatusForbidden, 0},
		} {
			if sf.unread && tc.policy == "strict" {
				continue // refused unread whatever the text
			}
			t.Run(sf.name+" / "+tc.policy+" over "+tc.action, func(t *testing.T) {
				g := newOtherAPIsGateway(t, rule(tc.action))
				headers := map[string]string{}
				if tc.policy != "" {
					headers["X-Guardrail-Policy"] = tc.policy
				}
				code, resp := g.send(t, postBlockOverrideKey, sf, requestCheckPrompt, headers)
				if code != tc.code || g.providerCalls() != tc.calls {
					t.Errorf("status %d, provider calls %d; want %d and %d; body %s", code, g.providerCalls(), tc.code, tc.calls, resp)
				}
			})
		}
	}
}

// An organization's Tool Permissions check reads the tools an assistant, a
// run or a thread-and-run offers, by name, as it reads a chat request's: a
// function it does not allow is refused before the provider gets the
// request, and one it allows goes on.
func TestRequestCheck_ToolPermissionsReadAssistantsTools(t *testing.T) {
	for _, tc := range []struct{ name, path, body string }{
		{"assistants create", "/v1/assistants", `{"model":"gpt-4o","tools":[{"type":"function","function":{"name":%q}}]}`},
		{"runs create", "/v1/threads/thread_1/runs", `{"assistant_id":"asst_1","tools":[{"type":"function","function":{"name":%q}}]}`},
		{"thread and run", "/v1/threads/runs", `{"assistant_id":"asst_1","tools":[{"type":"function","function":{"name":%q}}]}`},
	} {
		for _, fn := range []struct {
			name string
			code int
		}{{"delete_everything", http.StatusForbidden}, {"lookup", http.StatusOK}} {
			t.Run(tc.name+" / "+fn.name, func(t *testing.T) {
				g := newOtherAPIsGateway(t, toolPermissionsSetup())
				code, resp := g.do(t, tc.path, fmt.Sprintf(tc.body, fn.name))
				want := 0
				if fn.code == http.StatusOK {
					want = 1
				}
				if code != fn.code || g.providerCalls() != want {
					t.Errorf("status %d, provider calls %d; want %d and %d; body %s", code, g.providerCalls(), fn.code, want, resp)
				}
			})
		}
	}
}

// A batch's items (POST /-/batches, admin token) are chat requests, which a
// check on the request reads: an item a config-file rule blocks fails with
// the guardrail's error and never reaches the provider, and a rule's rewrite
// reaches it. The gateway runs without key auth here and the submit names no
// batch key, so the items run with no organization and only the config
// file's rules apply, as in TestPostBlock_BatchItemFailsWithTheGuardrailError.
func TestRequestCheck_BatchItemsAreChecked(t *testing.T) {
	for _, tc := range []struct {
		name      string
		rule      config.GuardrailRuleConfig
		ruleCfg   map[string]any
		wantError bool
		token     string
	}{
		{"block", config.GuardrailRuleConfig{Name: requestCheck, Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.5}, nil, true, ""},
		{"mask", config.GuardrailRuleConfig{Name: requestCheck, Stage: "pre", Mode: "sync", Action: "log", Threshold: 0.5}, map[string]any{"remediation": "mask"}, false, "***"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			calls := &upstreamCalls{}
			upstream := startLeakUpstreamWith(t, calls.record)
			cfg := config.DefaultConfig()
			cfg.Admin.Token = "other-apis-admin"
			cfg.Guardrails = config.GuardrailsConfig{Enabled: true, DefaultTimeout: 2 * time.Second, Rules: []config.GuardrailRuleConfig{tc.rule}}
			cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatal(err)
			}
			grRegistry := map[string]guardrails.Guardrail{requestCheck: pii.New(tc.ruleCfg)}
			grEngine := guardrails.NewEngine(cfg.Guardrails, grRegistry)
			policies := policy.NewStore()
			engine := pipeline.NewEngine(guardrails.NewPlugin(grEngine, grRegistry, nil, policies, nil))
			srv := New(cfg, "", registry, engine, nil, grEngine, policies, nil, testModelDBPtr(), tenant.NewStore(), nil)
			srv.ready.Store(true)
			admin := func(method, path, body string) map[string]any {
				t.Helper()
				req := httptest.NewRequest(method, path, strings.NewReader(body))
				req.Header.Set("Content-Type", "application/json")
				req.Header.Set("Authorization", "Bearer "+cfg.Admin.Token)
				w := httptest.NewRecorder()
				srv.httpServer.Handler.ServeHTTP(w, req)
				var out map[string]any
				_ = json.Unmarshal(w.Body.Bytes(), &out)
				return out
			}
			submitted := admin(http.MethodPost, "/-/batches", fmt.Sprintf(
				`{"requests":[{"model":"gpt-4o","messages":[{"role":"user","content":%q}]}]}`, requestCheckPrompt))
			id, _ := submitted["batch_id"].(string)
			var batch map[string]any
			for deadline := time.Now().Add(5 * time.Second); ; {
				if batch = admin(http.MethodGet, "/-/batches/"+id, ""); batch["status"] == "completed" {
					break
				}
				if time.Now().After(deadline) {
					t.Fatalf("batch %v still %v after 5s", submitted, batch["status"])
				}
				time.Sleep(10 * time.Millisecond)
			}
			results, _ := batch["results"].([]any)
			if len(results) != 1 {
				t.Fatalf("results %v, want one", batch["results"])
			}
			item, _ := results[0].(map[string]any)
			if tc.wantError {
				if item["status"] != "error" || !strings.Contains(fmt.Sprint(item["error"]), requestCheck) || calls.count() != 0 {
					t.Errorf("batch item %v, provider calls %d; want an error naming %s and no call", item, calls.count(), requestCheck)
				}
				return
			}
			if calls.count() != 1 || strings.Contains(calls.bodies[0], requestCheckSSN) || !strings.Contains(calls.bodies[0], tc.token) {
				t.Errorf("provider calls %d (%v), want one with %q and no SSN", calls.count(), calls.bodies, tc.token)
			}
		})
	}
}

// An async chat request (x-agentcc-async) and an A2A task are chat requests,
// which a check on the request reads: a request a check blocks never
// reaches the provider. An async job is queued by an internal key, which has
// no organization here, so the probe is the config file's rule; an A2A task
// is sent with the org's key, under both shapes of the check.
func TestRequestCheck_AsyncAndA2AAreChecked(t *testing.T) {
	t.Run("async", func(t *testing.T) {
		g := newOtherAPIsGateway(t, postBlockSetup{requestRule: requestCheck})
		code, resp := g.doAs(t, postBlockOpsKey, "/v1/chat/completions", chatWith(false)(requestCheckPrompt),
			map[string]string{"x-agentcc-async": "true"})
		if code == http.StatusAccepted {
			var accepted struct {
				JobID string `json:"job_id"`
			}
			_ = json.Unmarshal([]byte(resp), &accepted)
			job := waitAsyncJob(t, g.srv, postBlockOpsKey, accepted.JobID)
			if job["status"] != "failed" || !strings.Contains(fmt.Sprint(job["error"]), requestCheck) {
				t.Errorf("job %v, want failed with the guardrail's error naming %s", job, requestCheck)
			}
		} else if code != http.StatusForbidden || !strings.Contains(resp, requestCheck) {
			t.Errorf("status %d, want 202 then a failed job, or 403 naming %s; body %s", code, requestCheck, resp)
		}
		if n := g.providerCalls(); n != 0 {
			t.Errorf("the provider got the request %d times, want 0", n)
		}
	})
	for _, shape := range requestCheckShapes {
		for _, path := range []string{"/v1/a2a", "/a2a"} {
			t.Run(path+" / "+shape.name, func(t *testing.T) {
				setup := shape.setup
				setup.a2a = true
				g := newOtherAPIsGateway(t, setup)
				params, _ := json.Marshal(agentcca2a.MessageSendParams{
					Message:  agentcca2a.Message{Role: "user", Parts: []agentcca2a.MessagePart{{Type: "text", Text: requestCheckPrompt}}},
					Metadata: json.RawMessage(`{"model":"gpt-4o"}`),
				})
				body, _ := json.Marshal(mcp.Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: agentcca2a.MethodMessageSend, Params: params})
				code, resp := g.do(t, path, string(body))
				if code != http.StatusOK || !strings.Contains(resp, "failed") || !strings.Contains(resp, requestCheck) {
					t.Errorf("status %d, want the task failed with the guardrail's error naming %s; body %s", code, requestCheck, resp)
				}
				if n := g.providerCalls(); n != 0 {
					t.Errorf("the provider got the request %d times, want 0", n)
				}
			})
		}
	}
}

// genaiProviderQualified are the /v1beta actions on the provider-qualified
// route (/v1beta/models/{provider}/{model}:{action}), the same handler as
// /v1beta/models/{model}:{action}.
var genaiProviderQualified = []struct{ action, body string }{
	{"generateContent", `{"contents":[{"role":"user","parts":[{"text":%s}]}]}`},
	{"streamGenerateContent", `{"contents":[{"role":"user","parts":[{"text":%s}]}]}`},
	{"countTokens", `{"contents":[{"role":"user","parts":[{"text":%s}]}]}`},
	{"embedContent", `{"content":{"parts":[{"text":%s}]}}`},
}

// A check on the request reads the provider-qualified /v1beta route as it
// reads /v1beta/models/{model}: a request it blocks never reaches the
// provider, on every action.
func TestRequestCheck_ProviderQualifiedGenAIIsChecked(t *testing.T) {
	for _, tc := range genaiProviderQualified {
		for _, shape := range requestCheckShapes {
			t.Run(tc.action+" / "+shape.name, func(t *testing.T) {
				g := newOtherAPIsGateway(t, shape.setup)
				sf := otherAPISurface{path: "/v1beta/models/gemini/" + postBlockGemini + ":" + tc.action, body: jsonBody(tc.body), logged: true, format: genaiWire}
				code, resp := g.send(t, postBlockKey, sf, requestCheckPrompt, nil)
				assertOtherAPIRefused(t, g, sf, code, resp)
			})
		}
	}
}

// The refusal of a transcription a check would block says, in the words the
// release notes quote, which check refused it and why.
func TestRequestCheck_AudioRefusalSaysWhy(t *testing.T) {
	g := newOtherAPIsGateway(t, postBlockSetup{orgRequestCheck: requestCheck})
	sf := otherAPISurface{path: "/v1/audio/transcriptions", body: func(string) string { return "names: Ada" }, multipart: true}
	code, body := g.send(t, postBlockKey, sf, "", nil)
	want := "Request blocked by guardrail: pii-detection — it checks the request, and the gateway cannot read this request: its audio file is not text the checks can read"
	var e wireError
	_ = json.Unmarshal([]byte(body), &e)
	if code != http.StatusForbidden || e.Error.Message != want || e.Error.Code != "request_unavailable" {
		t.Errorf("status %d, error %s; want 403 request_unavailable %q", code, body, want)
	}
}

// The refusal of input the checks cannot read applies to any check on the
// request in block mode, whatever its type: an organization whose only such
// check is Tool Permissions, which could never match audio or a document,
// also gets request_unavailable on a transcription, a translation and OCR,
// naming that check.
func TestRequestCheck_UnreadableInputIsRefusedByAnyBlockingCheck(t *testing.T) {
	for _, tc := range otherAPIUnreadable {
		t.Run(tc.name, func(t *testing.T) {
			g := newOtherAPIsGateway(t, toolPermissionsSetup())
			sf := otherAPISurface{path: tc.path, body: func(string) string { return tc.body }, multipart: tc.multipart}
			code, body := g.send(t, postBlockKey, sf, "", nil)
			var e wireError
			_ = json.Unmarshal([]byte(body), &e)
			if code != http.StatusForbidden || e.Error.Code != "request_unavailable" || !strings.Contains(e.Error.Message, toolPermissions) {
				t.Errorf("status %d, error %s; want 403 request_unavailable naming %s", code, body, toolPermissions)
			}
			if n := g.providerCalls(); n != 0 {
				t.Errorf("a provider got the request %d times, want 0", n)
			}
		})
	}
}

// /v1/search reads X-Guardrail-Policy too: its only provider here is
// the gateway's own fake one, which the org's internal key reaches. That key
// has no allow_policy_override, so the header is refused before the search
// provider is called, and without the header the rule blocks the query.
func TestGuardrailPolicyOverride_SearchReadsTheHeader(t *testing.T) {
	for _, sf := range otherAPISurfaces {
		if !strings.HasPrefix(sf.path, "/v1/search") {
			continue
		}
		t.Run(sf.name, func(t *testing.T) {
			g := newOtherAPIsGateway(t, postBlockSetup{requestRule: requestCheck, requestAction: "block"})
			code, resp := g.send(t, postBlockInternalOrgKey, sf, postBlockPrompt, map[string]string{"X-Guardrail-Policy": "disabled"})
			if code != http.StatusForbidden || !strings.Contains(resp, "does not allow guardrail policy overrides") {
				t.Errorf("status %d, want 403 saying the key does not allow overrides; body %s", code, resp)
			}
			if n := g.providerCalls(); n != 0 {
				t.Errorf("the search provider got the query %d times, want 0", n)
			}
		})
	}
}
