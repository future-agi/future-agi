package contractwatch

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

// fakeAPI is an httptest server that checks a credential header and answers
// each request with handle; it records every request URL.
type fakeAPI struct {
	*httptest.Server
	mu   sync.Mutex
	urls []string
}

func newFakeAPI(t *testing.T, header, want string, handle func(w http.ResponseWriter, r *http.Request)) *fakeAPI {
	t.Helper()
	f := &fakeAPI{}
	f.Server = httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		f.urls = append(f.urls, r.URL.RequestURI())
		f.mu.Unlock()
		if r.Method != http.MethodGet {
			http.Error(w, "GET only", http.StatusMethodNotAllowed)
			return
		}
		if header != "" && r.Header.Get(header) != want {
			w.WriteHeader(http.StatusUnauthorized)
			_, _ = fmt.Fprintf(w, `{"error":{"message":"Incorrect API key provided: %s"}}`, r.Header.Get(header))
			return
		}
		w.Header().Set("Content-Type", "application/json")
		handle(w, r)
	}))
	t.Cleanup(f.Close)
	return f
}

func (f *fakeAPI) requests() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.urls...)
}

func ids(ms []Model) string {
	var out []string
	for _, m := range ms {
		out = append(out, m.ID)
	}
	return strings.Join(out, ",")
}

func byID(ms []Model) map[string]Model {
	out := map[string]Model{}
	for _, m := range ms {
		out[m.ID] = m
	}
	return out
}

const testKey = "sk-test-0123456789abcdef"

func TestOpenAILister(t *testing.T) {
	f := newFakeAPI(t, "Authorization", "Bearer "+testKey, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/models" {
			http.NotFound(w, r)
			return
		}
		_, _ = w.Write([]byte(`{"object":"list","data":[
			{"id":"gpt-6.1-sol","object":"model","created":1790000000,"owned_by":"openai"},
			{"id":"gpt-4o","object":"model","created":1715367049,"owned_by":"system","shutdown_date":"2027-01-14"}]}`))
	})
	ms, err := OpenAILister{BaseURL: f.URL, Path: "/v1/models", APIKey: testKey, Client: f.Client()}.List(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	m := byID(ms)
	if ids(ms) != "gpt-6.1-sol,gpt-4o" || m["gpt-6.1-sol"].Created == nil || m["gpt-6.1-sol"].OwnedBy != "openai" {
		t.Errorf("got %+v", ms)
	}
	if m["gpt-4o"].AutoIgnore == "" || m["gpt-4o"].Retires != "2027-01-14" {
		t.Errorf("a shutdown date must auto-ignore: %+v", m["gpt-4o"])
	}

	// A wrong key: the error carries the status, and the key never.
	_, err = OpenAILister{BaseURL: f.URL, Path: "/v1/models", APIKey: "sk-wrong-key-123456", Client: f.Client()}.List(context.Background())
	if err == nil || !strings.Contains(err.Error(), "HTTP 401") {
		t.Fatalf("want an HTTP 401 error, got %v", err)
	}
	if strings.Contains(err.Error(), "sk-wrong-key-123456") {
		t.Errorf("the error leaks the key: %v", err)
	}
}

func TestGroqLister(t *testing.T) {
	f := newFakeAPI(t, "Authorization", "Bearer "+testKey, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/openai/v1/models" {
			http.NotFound(w, r)
			return
		}
		_, _ = w.Write([]byte(`{"object":"list","data":[
			{"id":"openai/gpt-oss-20b","object":"model","created":1754407957,"owned_by":"OpenAI","active":true,"context_window":131072,"max_completion_tokens":65536},
			{"id":"llama3-70b-8192","object":"model","created":1693721698,"owned_by":"Meta","active":false,"context_window":8192}]}`))
	})
	ms, err := OpenAILister{BaseURL: f.URL, Path: "/openai/v1/models", APIKey: testKey, Client: f.Client()}.List(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	m := byID(ms)
	if m["openai/gpt-oss-20b"].ContextWindow != 131072 || m["openai/gpt-oss-20b"].MaxOutput != 65536 || m["openai/gpt-oss-20b"].AutoIgnore != "" {
		t.Errorf("groq metadata: %+v", m["openai/gpt-oss-20b"])
	}
	if m["llama3-70b-8192"].AutoIgnore == "" {
		t.Error("active: false must auto-ignore")
	}
}

func TestAnthropicListerPaginates(t *testing.T) {
	pages := map[string]string{
		"": `{"data":[
			{"type":"model","id":"claude-opus-5-6","display_name":"Claude Opus 5.6","created_at":"2026-10-20T00:00:00Z","lifecycle":"active","line":"opus",
			 "max_input_tokens":1000000,"max_tokens":128000,"retires_at":null,"deprecated_at":null,
			 "capabilities":{"thinking":{"supported":true,"types":{}},"batch":{"supported":true},"pdf_input":{"supported":false}}},
			{"type":"model","id":"claude-opus-5-5","display_name":"Claude Opus 5.5","created_at":"2026-06-01T00:00:00Z","lifecycle":"active","line":"opus","capabilities":null}],
			"has_more":true,"first_id":"claude-opus-5-6","last_id":"claude-opus-5-5"}`,
		"claude-opus-5-5": `{"data":[
			{"type":"model","id":"claude-sonnet-4-5-20250929","display_name":"Claude Sonnet 4.5","created_at":"2025-09-29T00:00:00Z","lifecycle":"deprecated","line":"sonnet",
			 "retires_at":"2026-11-30T00:00:00Z","capabilities":{"thinking":{"supported":true,"types":{}}}}],
			"has_more":false,"first_id":"claude-sonnet-4-5-20250929","last_id":"claude-sonnet-4-5-20250929"}`,
	}
	f := newFakeAPI(t, "X-Api-Key", testKey, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/models" || r.Header.Get("Anthropic-Version") != AnthropicVersion || r.URL.Query().Get("limit") != "2" {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		body, ok := pages[r.URL.Query().Get("after_id")]
		if !ok {
			http.NotFound(w, r)
			return
		}
		_, _ = w.Write([]byte(body))
	})
	ms, err := AnthropicLister{BaseURL: f.URL, APIKey: testKey, Client: f.Client(), PageSize: 2}.List(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if ids(ms) != "claude-opus-5-6,claude-opus-5-5,claude-sonnet-4-5-20250929" {
		t.Fatalf("got %s", ids(ms))
	}
	if got := f.requests(); len(got) != 2 || !strings.Contains(got[1], "after_id=claude-opus-5-5") {
		t.Errorf("requests %v", got)
	}
	m := byID(ms)
	o := m["claude-opus-5-6"]
	if o.Thinking == nil || !*o.Thinking || o.Line != "opus" || o.ContextWindow != 1000000 || o.MaxOutput != 128000 ||
		strings.Join(o.Capabilities, ",") != "batch,thinking" || o.Created == nil || o.AutoIgnore != "" {
		t.Errorf("opus 5.6 metadata: %+v", o)
	}
	if m["claude-opus-5-5"].Thinking != nil {
		t.Error("null capabilities must leave thinking unknown")
	}
	if s := m["claude-sonnet-4-5-20250929"]; s.AutoIgnore == "" || s.Retires == "" || s.Lifecycle != "deprecated" {
		t.Errorf("a deprecated model must auto-ignore: %+v", s)
	}
}

func TestAnthropicListerStuckCursor(t *testing.T) {
	f := newFakeAPI(t, "X-Api-Key", testKey, func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"data":[{"id":"claude-x"}],"has_more":true,"last_id":"claude-x"}`))
	})
	_, err := AnthropicLister{BaseURL: f.URL, APIKey: testKey, Client: f.Client()}.List(context.Background())
	if err == nil || !strings.Contains(err.Error(), "does not advance") {
		t.Fatalf("want a stuck-cursor error, got %v", err)
	}
	if n := len(f.requests()); n != 2 {
		t.Errorf("made %d requests, want 2", n)
	}
}

func TestGeminiListerPaginates(t *testing.T) {
	f := newFakeAPI(t, "X-Goog-Api-Key", testKey, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1beta/models" || r.URL.Query().Get("key") != "" {
			http.Error(w, "the key goes in a header", http.StatusBadRequest)
			return
		}
		switch r.URL.Query().Get("pageToken") {
		case "":
			_, _ = w.Write([]byte(`{"models":[
				{"name":"models/gemini-4.1-argon","version":"4.1","displayName":"Gemini 4.1 Argon","inputTokenLimit":1048576,"outputTokenLimit":65536,
				 "supportedGenerationMethods":["generateContent","countTokens","createCachedContent"],"thinking":true}],
				"nextPageToken":"p2"}`))
		case "p2":
			_, _ = w.Write([]byte(`{"models":[
				{"name":"models/gemini-embedding-2","supportedGenerationMethods":["embedContent","countTextTokens"]},
				{"name":"models/imagen-4.0-generate-001","supportedGenerationMethods":["predict"]},
				{"name":"models/gemini-3.8-flash","supportedGenerationMethods":["generateContent"],"thinking":false}]}`))
		default:
			http.NotFound(w, r)
		}
	})
	ms, err := GeminiLister{BaseURL: f.URL, APIKey: testKey, Client: f.Client()}.List(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if ids(ms) != "gemini-4.1-argon,gemini-embedding-2,imagen-4.0-generate-001,gemini-3.8-flash" {
		t.Fatalf("got %s (the models/ prefix must be stripped)", ids(ms))
	}
	m := byID(ms)
	a := m["gemini-4.1-argon"]
	if a.Thinking == nil || !*a.Thinking || a.ContextWindow != 1048576 || !contains(a.Capabilities, "generateContent") || a.AutoIgnore != "" {
		t.Errorf("argon metadata: %+v", a)
	}
	// The adapter parses embedContent (embeddings) as well as
	// generateContent, so an embedding model is a contract target.
	if e := m["gemini-embedding-2"]; e.AutoIgnore != "" {
		t.Errorf("an embedContent model must not auto-ignore: %+v", e)
	}
	if m["imagen-4.0-generate-001"].AutoIgnore == "" {
		t.Error("a model with neither generateContent nor embedContent must auto-ignore")
	}
	if f := m["gemini-3.8-flash"]; f.Thinking == nil || *f.Thinking {
		t.Error("thinking false must be kept as a stated false")
	}
}

func TestGeminiListerRepeatingToken(t *testing.T) {
	f := newFakeAPI(t, "X-Goog-Api-Key", testKey, func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"models":[],"nextPageToken":"same"}`))
	})
	if _, err := (GeminiLister{BaseURL: f.URL, APIKey: testKey, Client: f.Client()}).List(context.Background()); err == nil {
		t.Fatal("a repeating page token must be an error, not a loop")
	}
}

func TestCohereListerPaginates(t *testing.T) {
	f := newFakeAPI(t, "Authorization", "Bearer "+testKey, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/models" || r.URL.Query().Get("page_size") != "1000" {
			http.Error(w, "bad", http.StatusBadRequest)
			return
		}
		switch r.URL.Query().Get("page_token") {
		case "":
			_, _ = w.Write([]byte(`{"models":[
				{"name":"command-a-vision-07-2025","endpoints":["chat"],"finetuned":false,"context_length":128000,"features":["tools","vision"]},
				{"name":"embed-v5.0-pro","endpoints":["embed"],"context_length":512},
				{"name":"rerank-v5.0-fast","endpoints":["rerank"]},
				{"name":"classify-v1","endpoints":["classify"]}],"next_page_token":"t2"}`))
		case "t2":
			_, _ = w.Write([]byte(`{"models":[
				{"name":"command-a-reasoning-11-2025","endpoints":["chat"],"features":["tools","reasoning"]},
				{"name":"command-r","endpoints":["chat","generate"],"is_deprecated":true},
				{"name":"acme-ft-1","endpoints":["chat"],"finetuned":true}]}`))
		}
	})
	ms, err := CohereLister{BaseURL: f.URL, APIKey: testKey, Client: f.Client()}.List(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	m := byID(ms)
	if len(ms) != 7 {
		t.Fatalf("got %s", ids(ms))
	}
	if v := m["command-a-vision-07-2025"]; v.AutoIgnore != "" || v.ContextWindow != 128000 || !contains(v.Capabilities, "endpoint:chat") || v.Thinking != nil {
		t.Errorf("vision metadata: %+v", v)
	}
	if r := m["command-a-reasoning-11-2025"]; r.Thinking == nil || !*r.Thinking {
		t.Error("a reasoning feature must mark thinking")
	}
	// The adapter serves chat, embed and rerank: those are contract targets.
	for _, id := range []string{"embed-v5.0-pro", "rerank-v5.0-fast"} {
		if m[id].AutoIgnore != "" {
			t.Errorf("%s must not auto-ignore: %s", id, m[id].AutoIgnore)
		}
	}
	for _, id := range []string{"classify-v1", "command-r", "acme-ft-1"} {
		if m[id].AutoIgnore == "" {
			t.Errorf("%s must auto-ignore", id)
		}
	}
}

func TestStaticLister(t *testing.T) {
	ms, err := StaticLister{Models: []string{"sonar", "sonar-pro"}}.List(context.Background())
	if err != nil || ids(ms) != "sonar,sonar-pro" {
		t.Fatalf("got %v %v", ms, err)
	}
}

func TestFetcherRejectsBadJSON(t *testing.T) {
	f := newFakeAPI(t, "", "", func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte(`<html>`)) })
	_, err := OpenAILister{BaseURL: f.URL, Path: "/v1/models", APIKey: testKey, Client: f.Client()}.List(context.Background())
	if err == nil || !strings.Contains(err.Error(), "decoding") {
		t.Fatalf("want a decode error, got %v", err)
	}
}

func TestRedactURL(t *testing.T) {
	got := redactURL("https://x.test/v1beta/models?key=AIzaSECRET&pageSize=10")
	if strings.Contains(got, "SECRET") || !strings.Contains(got, "pageSize=10") {
		t.Errorf("redactURL = %s", got)
	}
}

// Model metadata survives a JSON round trip (it is copied into the plan).
func TestModelJSON(t *testing.T) {
	yes := true
	m := Model{ID: "x", Thinking: &yes, Capabilities: []string{"a"}}
	raw, _ := json.Marshal(m)
	var back Model
	if err := json.Unmarshal(raw, &back); err != nil || back.Thinking == nil || !*back.Thinking {
		t.Fatalf("round trip: %s %v", raw, err)
	}
}

// TestMistralLister: Mistral's GET /v1/models carries capabilities,
// deprecation and type. Chat and OCR models are contract targets; the rest,
// fine-tunes and announced deprecations are ignored from that metadata.
func TestMistralLister(t *testing.T) {
	f := newFakeAPI(t, "Authorization", "Bearer "+testKey, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/models" {
			http.NotFound(w, r)
			return
		}
		_, _ = w.Write([]byte(`{"object":"list","data":[
			{"id":"mistral-large-2512","object":"model","created":1765000000,"owned_by":"mistralai","max_context_length":262144,
			 "capabilities":{"completion_chat":true,"function_calling":true,"vision":true,"ocr":false},"aliases":["mistral-large-latest"],"deprecation":null,"type":"base"},
			{"id":"mistral-ocr-2512","object":"model","owned_by":"mistralai","capabilities":{"completion_chat":false,"ocr":true},"type":"base"},
			{"id":"mistral-embed","object":"model","owned_by":"mistralai","capabilities":{"completion_chat":false,"ocr":false},"type":"base"},
			{"id":"open-mistral-nemo","object":"model","owned_by":"mistralai","capabilities":{"completion_chat":true},"deprecation":"2026-12-31T12:00:00Z","type":"base"},
			{"id":"ft:open-mistral-7b:acme:abc","object":"model","owned_by":"acme","capabilities":{"completion_chat":true},"type":"fine-tuned"}]}`))
	})
	ms, err := Specs["mistral"].NewLister(envOf(map[string]string{"MISTRAL_API_KEY": testKey}), f.Client()).(OpenAILister).withBase(f.URL).List(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	m := byID(ms)
	if l := m["mistral-large-2512"]; l.AutoIgnore != "" || !contains(l.Capabilities, "completion_chat") || !contains(l.Capabilities, "type:base") || contains(l.Capabilities, "ocr") {
		t.Errorf("chat model: %+v", l)
	}
	if o := m["mistral-ocr-2512"]; o.AutoIgnore != "" || ModelKind(o.ID, &o) != KindOCR {
		t.Errorf("an OCR model is a contract target of kind ocr: %+v", o)
	}
	for id, want := range map[string]string{
		"mistral-embed":               "neither completion_chat nor ocr",
		"open-mistral-nemo":           "shutdown announced for 2026-12-31",
		"ft:open-mistral-7b:acme:abc": "fine-tuned",
	} {
		if !strings.Contains(m[id].AutoIgnore, want) {
			t.Errorf("%s: auto_ignore %q, want it to say %q", id, m[id].AutoIgnore, want)
		}
	}
}

// TestOpenAIListerBareArray: Together answers GET /v1/models with a bare
// array, and OpenRouter announces retirements as expiration_date.
func TestOpenAIListerBareArray(t *testing.T) {
	f := newFakeAPI(t, "Authorization", "Bearer "+testKey, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/v1/models":
			_, _ = w.Write([]byte(` [{"id":"meta-llama/Llama-4-Scout-17B-16E-Instruct","object":"model","created":1743878400,"type":"chat","context_length":1048576},
				{"id":"BAAI/bge-large-en-v1.5","object":"model","type":"embedding","context_length":512}]`))
		case "/api/v1/models":
			_, _ = w.Write([]byte(`{"data":[{"id":"anthropic/claude-sonnet-4.5","created":1759161676,"context_length":1000000,"expiration_date":null},
				{"id":"openai/gpt-4o-2024-05-13","context_length":128000,"expiration_date":"2026-11-30"}]}`))
		default:
			http.NotFound(w, r)
		}
	})
	together, err := OpenAILister{BaseURL: f.URL, Path: "/v1/models", APIKey: testKey, Client: f.Client()}.List(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	m := byID(together)
	if s := m["meta-llama/Llama-4-Scout-17B-16E-Instruct"]; s.ContextWindow != 1048576 || !contains(s.Capabilities, "type:chat") || s.Created == nil {
		t.Errorf("together metadata: %+v", s)
	}
	if ModelKind("BAAI/bge-large-en-v1.5", nil) != KindChat || len(together) != 2 {
		t.Errorf("got %s", ids(together))
	}
	or, err := OpenAILister{BaseURL: f.URL, Path: "/api/v1/models", APIKey: testKey, Client: f.Client()}.List(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	m = byID(or)
	if c := m["anthropic/claude-sonnet-4.5"]; c.AutoIgnore != "" || c.ContextWindow != 1000000 {
		t.Errorf("openrouter metadata: %+v", c)
	}
	if g := m["openai/gpt-4o-2024-05-13"]; g.Retires != "2026-11-30" || g.AutoIgnore == "" {
		t.Errorf("an expiration_date must auto-ignore: %+v", g)
	}
	// A body that is neither shape is an error, not an empty listing.
	bad := newFakeAPI(t, "", "", func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte(`"models"`)) })
	if _, err := (OpenAILister{BaseURL: bad.URL, Path: "/v1/models", APIKey: testKey, Client: bad.Client()}).List(context.Background()); err == nil {
		t.Error("a string body must be an error")
	}
}

// withBase points a lister built by Specs at a test server, keeping its path
// and key.
func (l OpenAILister) withBase(base string) OpenAILister {
	l.BaseURL = base
	return l
}
