package main

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// upstreamCall is one request the fake upstream received.
type upstreamCall struct {
	Method, Path, Query string
	Header              http.Header
	Body                []byte
}

// fakeUpstream answers every provider API the endpoints reach with a small
// plausible body: audio for speech, text for a text transcription, SSE for a
// stream, JSON otherwise. respond, when set, answers first.
type fakeUpstream struct {
	*httptest.Server
	respond func(w http.ResponseWriter, r *http.Request, body []byte) bool

	mu    sync.Mutex
	calls []upstreamCall
}

// speechBytes is not UTF-8, so a text round trip would change it.
var speechBytes = []byte{0xff, 0xfb, 0x90, 0x44, 0x00, 0x00, 0x0f, 0xf0, 'I', 'D', '3', 0x80}

func newFakeUpstream(t *testing.T) *fakeUpstream {
	f := &fakeUpstream{}
	f.Server = httptest.NewTLSServer(http.HandlerFunc(f.serve))
	t.Cleanup(f.Close)
	return f
}

func (f *fakeUpstream) received() []upstreamCall {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]upstreamCall(nil), f.calls...)
}

// find returns the one call to "METHOD path", failing t otherwise.
func (f *fakeUpstream) find(t *testing.T, method, path string) upstreamCall {
	t.Helper()
	var got []upstreamCall
	for _, c := range f.received() {
		if c.Method == method && c.Path == path {
			got = append(got, c)
		}
	}
	if len(got) != 1 {
		t.Fatalf("upstream got %d %s %s calls, want 1", len(got), method, path)
	}
	return got[0]
}

func (f *fakeUpstream) serve(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	f.mu.Lock()
	f.calls = append(f.calls, upstreamCall{r.Method, r.URL.Path, r.URL.RawQuery, r.Header.Clone(), body})
	f.mu.Unlock()
	if f.respond != nil && f.respond(w, r, body) {
		return
	}
	stream := bytes.Contains(body, []byte(`"stream":true`)) || strings.HasSuffix(r.URL.Path, ":streamGenerateContent")
	switch {
	case r.URL.Path == "/v1/audio/speech":
		w.Header().Set("Content-Type", "audio/mpeg")
		_, _ = w.Write(speechBytes)
	case r.URL.Path == "/v1/audio/transcriptions" && bytes.Contains(body, []byte("name=\"response_format\"\r\n\r\ntext")):
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		_, _ = io.WriteString(w, "Hello there.\n")
	case stream:
		w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
		_, _ = io.WriteString(w, "data: {\"n\":1}\n\ndata: {\"n\":2}\n\n")
	case r.URL.Path == "/v1/responses":
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"id":"resp_1","object":"response","status":"completed","model":"gpt-x",`+
			`"output":[{"type":"message","id":"msg_1","role":"assistant","content":[{"type":"output_text","text":"Hi."}]}],`+
			`"usage":{"input_tokens":5,"output_tokens":2,"total_tokens":7}}`)
	case r.URL.Path == "/v1/models":
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"object":"list","data":[{"id":"gpt-x","object":"model","created":1,"owned_by":"openai"}]}`)
	default:
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"text":"Hi.","path":"`+r.URL.Path+`"}`)
	}
}

// recordAgainst runs the recorder for one provider against srv.
func recordAgainst(t *testing.T, srv *httptest.Server, env map[string]string, args ...string) (int, string) {
	t.Helper()
	var stdout, stderr bytes.Buffer
	opts, err := parseFlags(args, &stderr)
	if err != nil {
		t.Fatalf("parseFlags(%q): %v %s", args, err, stderr.String())
	}
	r := &recorder{opts: opts, out: &stdout, env: func(k string) string { return env[k] }, now: fixedNow, client: srv.Client()}
	return r.run(context.Background()), stdout.String()
}

// endpointCase writes one case: request.json, case.json naming endpoint,
// and the extra files given (name -> content); a response file among them is
// the synthetic body the recording replaces.
func (f *fixtureTree) endpointCase(provider, model, name, endpoint, request string, files map[string]string) {
	f.t.Helper()
	dir := provider + "/testdata/contracts/" + contracttest.ModelDir(model) + "/" + name + "/"
	f.write(dir+"request.json", request)
	f.write(dir+"case.json", `{"endpoint": "`+endpoint+`"}`)
	for n, c := range files {
		f.write(dir+n, c)
	}
}

func TestRecordEveryEndpointThroughTheAdapters(t *testing.T) {
	srv := newFakeUpstream(t)
	f := newFixtureTree(t)
	env := map[string]string{
		"OPENAI_API_KEY": "sk-openai-test-0123456789", "ANTHROPIC_API_KEY": testKey,
		"GEMINI_API_KEY": "gemini-test-0123456789", "COHERE_API_KEY": "cohere-test-0123456789",
	}

	f.write("openai/testdata/contracts/gpt-x/meta.json", metaJSON("openai", "gpt-x"))
	for _, c := range []struct{ name, endpoint, request string }{
		{"via_responses", "chat_via_responses", `{"model":"gpt-x","messages":[{"role":"user","content":"Hi"}]}`},
		{"completion", "completions", `{"model":"gpt-x","prompt":"Say hi"}`},
		{"embed", "embeddings", `{"model":"gpt-x","input":"hi"}`},
		{"image", "images", `{"model":"gpt-x","prompt":"a cat"}`},
		{"translate", "translation", `{"model":"gpt-x","file":"speech.wav"}`},
		{"ocr", "ocr", `{"model":"gpt-x","document":{"type":"document_url","document_url":"https://example.com/a.pdf"}}`},
		{"responses", "responses", `{"model":"gpt-x","input":"hi"}`},
		{"stream_responses", "responses", `{"model":"gpt-x","input":"hi","stream":true}`},
		{"vector_store", "vector_stores", `{"method":"POST","path":"/v1/vector_stores","body":{"name":"docs"}}`},
		{"models", "list_models", `{}`},
	} {
		files := map[string]string{"response.json": "synthetic"}
		if c.name == "stream_responses" {
			files = map[string]string{"response.sse": "synthetic"}
		}
		if c.endpoint == "translation" {
			files["speech.wav"] = "RIFF-audio"
		}
		f.endpointCase("openai", "gpt-x", c.name, c.endpoint, c.request, files)
	}
	// A case not recorded yet: the file follows the Content-Type.
	f.endpointCase("openai", "gpt-x", "embed_new", "embeddings", `{"model":"gpt-x","input":"new"}`, nil)
	// Speech: binary, and the content type case.json had is not what came.
	f.endpointCase("openai", "gpt-x", "speech", "speech", `{"model":"gpt-x","input":"hi","voice":"alloy"}`,
		map[string]string{"response.bin": "synthetic"})
	f.write("openai/testdata/contracts/gpt-x/speech/case.json", `{"endpoint": "speech", "content_type": "audio/wav"}`)
	// Transcription as text: response.txt, from a multipart upload.
	f.endpointCase("openai", "gpt-x", "transcribe_text", "transcription",
		`{"model":"gpt-x","file":"audio.wav","response_format":"text"}`,
		map[string]string{"audio.wav": "RIFF-audio", "response.txt": "synthetic"})
	// No "file": the default upload.
	f.endpointCase("openai", "gpt-x", "transcribe_default", "transcription", `{"model":"gpt-x","response_format":"json"}`,
		map[string]string{"response.json": "synthetic"})
	// Assistants: a GET with a query, as a passthrough request.
	f.endpointCase("openai", "gpt-x", "list_assistants", "assistants", `{"method":"GET","path":"/v1/assistants","query":{"limit":["2"]}}`,
		map[string]string{"response.json": "synthetic"})

	f.write("anthropic/testdata/contracts/claude-a/meta.json", metaJSON("anthropic", "claude-a"))
	f.endpointCase("anthropic", "claude-a", "stream_native", "anthropic_messages",
		`{"model":"claude-a","max_tokens":16,"stream":true,"messages":[{"role":"user","content":"Hi"}]}`,
		map[string]string{"response.sse": "synthetic"})
	f.endpointCase("anthropic", "claude-a", "count", "anthropic_count_tokens",
		`{"model":"claude-a","messages":[{"role":"user","content":"Hi"}]}`, map[string]string{"response.json": "synthetic"})

	f.write("gemini/testdata/contracts/g-1/meta.json", metaJSON("gemini", "g-1"))
	f.endpointCase("gemini", "g-1", "generate", "genai_generate", `{"contents":[{"parts":[{"text":"Hi"}]}]}`,
		map[string]string{"response.json": "synthetic"})
	f.endpointCase("gemini", "g-1", "stream_generate", "genai_generate", `{"contents":[{"parts":[{"text":"Hi"}]}]}`, nil)
	f.endpointCase("gemini", "g-1", "count", "genai_count_tokens", `{"contents":[{"parts":[{"text":"Hi"}]}]}`,
		map[string]string{"response.json": "synthetic"})
	f.endpointCase("gemini", "g-1", "embed", "genai_embed", `{"content":{"parts":[{"text":"Hi"}]}}`,
		map[string]string{"response.json": "synthetic"})

	f.write("cohere/testdata/contracts/rerank-x/meta.json", metaJSON("cohere", "rerank-x"))
	f.endpointCase("cohere", "rerank-x", "rerank", "rerank", `{"model":"rerank-x","query":"q","documents":["a","b"]}`,
		map[string]string{"response.json": "synthetic"})

	for _, p := range []string{"openai", "anthropic", "gemini", "cohere"} {
		code, out := recordAgainst(t, srv.Server, env, "-providers-dir", f.root, "-provider", p, "-base-url", srv.URL)
		if code != 0 || strings.Contains(out, "FAILED") || strings.Contains(out, "warning") {
			t.Fatalf("%s: exit %d:\n%s", p, code, out)
		}
	}

	for _, want := range []struct{ method, path, query string }{
		{"POST", "/v1/responses", ""}, // chat_via_responses, responses, stream_responses
		{"POST", "/v1/completions", ""},
		{"POST", "/v1/embeddings", ""},
		{"POST", "/v1/images/generations", ""},
		{"POST", "/v1/audio/speech", ""},
		{"POST", "/v1/audio/transcriptions", ""},
		{"POST", "/v1/audio/translations", ""},
		{"POST", "/v1/ocr", ""},
		{"GET", "/v1/assistants", "limit=2"},
		{"POST", "/v1/vector_stores", ""},
		{"GET", "/v1/models", ""},
		{"POST", "/v1/messages", ""},
		{"POST", "/v1/messages/count_tokens", ""},
		{"POST", "/v1beta/models/g-1:generateContent", ""},
		{"POST", "/v1beta/models/g-1:streamGenerateContent", "alt=sse"},
		{"POST", "/v1beta/models/g-1:countTokens", ""},
		{"POST", "/v1beta/models/g-1:embedContent", ""},
		{"POST", "/v2/rerank", ""},
	} {
		n := 0
		for _, c := range srv.received() {
			if c.Method == want.method && c.Path == want.path && c.Query == want.query {
				n++
			}
		}
		if n == 0 {
			t.Errorf("upstream never got %s %s?%s", want.method, want.path, want.query)
		}
	}
	if c := srv.find(t, "GET", "/v1/assistants"); len(c.Body) != 0 || c.Header.Get("OpenAI-Beta") != "assistants=v2" {
		t.Errorf("assistants GET sent body %q, OpenAI-Beta %q", c.Body, c.Header.Get("OpenAI-Beta"))
	}
	if c := srv.find(t, "POST", "/v1/messages"); c.Header.Get("anthropic-version") == "" || c.Header.Get("X-Api-Key") != testKey {
		t.Errorf("anthropic_messages headers = %v", c.Header)
	}
	var named, silent int
	for _, c := range srv.received() {
		switch {
		case c.Path != "/v1/audio/transcriptions":
		case bytes.Contains(c.Body, []byte(`filename="audio.wav"`)) && bytes.Contains(c.Body, []byte("RIFF-audio")):
			named++
		case bytes.Contains(c.Body, []byte(`filename="speech.wav"`)) && bytes.Contains(c.Body, contracttest.DefaultUpload().Data):
			silent++
		}
	}
	if named != 1 || silent != 1 {
		t.Errorf("transcription uploads: %d of audio.wav, %d of the default; want 1 each", named, silent)
	}

	oa := "openai/testdata/contracts/gpt-x/"
	if got := f.read(oa + "speech/response.bin"); got != string(speechBytes) {
		t.Errorf("response.bin = %q, want the audio byte for byte", got)
	}
	if got := f.read(oa + "speech/case.json"); !strings.Contains(got, `"content_type": "audio/mpeg"`) || !strings.Contains(got, `"endpoint": "speech"`) {
		t.Errorf("speech case.json = %s, want the observed content type and the rest kept", got)
	}
	if got := f.read(oa + "transcribe_text/response.txt"); got != "Hello there.\n" {
		t.Errorf("response.txt = %q", got)
	}
	if got := f.read(oa + "transcribe_text/case.json"); got != `{"endpoint": "transcription"}` {
		t.Errorf("a text/plain .txt needs no case.json change, got %s", got)
	}
	if got := f.read(oa + "embed_new/response.json"); !strings.Contains(got, `"/v1/embeddings"`) {
		t.Errorf("new case response.json = %s", got)
	}
	if got := f.read(oa + "stream_responses/response.sse"); !strings.HasPrefix(got, "data: ") {
		t.Errorf("stream_responses/response.sse = %q", got)
	}
	if got := f.read("gemini/testdata/contracts/g-1/stream_generate/response.sse"); !strings.HasPrefix(got, "data: ") {
		t.Errorf("a new genai stream case = %q, want response.sse", got)
	}

	// Every provider's tree is still what Discover accepts.
	for _, p := range []string{"openai", "anthropic", "gemini", "cohere"} {
		t.Chdir(filepath.Join(f.root, p))
		for _, c := range contracttest.Discover(t, p) {
			if c.Meta.Source != contracttest.SourceRecorded {
				t.Errorf("%s %s: meta source %q, want recorded", p, c.ID(), c.Meta.Source)
			}
			if c.Name == "stream_generate" && !c.IsStream {
				t.Errorf("%s: not a stream", c.ID())
			}
		}
	}
}
