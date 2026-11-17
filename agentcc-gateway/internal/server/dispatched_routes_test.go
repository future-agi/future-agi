package server

// Every route family, in both directions: an answer the gateway gives after it
// called a provider carries no x-agentcc-dispatched header, whatever the
// provider answered, and a refusal the gateway makes before any provider call
// says "false".

import (
	"bytes"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
)

type dispatchRoute struct{ name, path, body, contentType string }

func dispatchAudioForm() (string, string) {
	var b bytes.Buffer
	mw := multipart.NewWriter(&b)
	_ = mw.WriteField("model", "gpt-4o")
	fw, _ := mw.CreateFormFile("file", "a.wav")
	_, _ = fw.Write([]byte("RIFF0000WAVEfmt "))
	_ = mw.Close()
	return b.String(), mw.FormDataContentType()
}

// dispatchCallingRoutes are the route families that call a provider for this
// request; dispatchRefusingRoutes add the ones that refuse it here before any
// call.
func dispatchCallingRoutes() []dispatchRoute {
	audio, audioType := dispatchAudioForm()
	const js = "application/json"
	return []dispatchRoute{
		{"chat", "/v1/chat/completions", `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`, js},
		{"completions", "/v1/completions", `{"model":"gpt-4o","prompt":"hi"}`, js},
		{"embeddings", "/v1/embeddings", `{"model":"gpt-4o","input":"hi"}`, js},
		{"images", "/v1/images/generations", `{"model":"gpt-4o","prompt":"a cat"}`, js},
		{"speech", "/v1/audio/speech", `{"model":"gpt-4o","input":"hi","voice":"alloy"}`, js},
		{"speech stream", "/v1/audio/speech/stream", `{"model":"gpt-4o","input":"hi","voice":"alloy"}`, js},
		{"transcriptions", "/v1/audio/transcriptions", audio, audioType},
		{"translations", "/v1/audio/translations", audio, audioType},
		{"responses", "/v1/responses", `{"model":"gpt-4o","input":"hi"}`, js},
		{"Anthropic Messages", "/v1/messages", `{"model":"gpt-4o","max_tokens":16,"messages":[{"role":"user","content":"hi"}]}`, js},
		{"ocr", "/v1/ocr", `{"model":"gpt-4o","document":{"type":"document_url","document_url":"https://example.com/a.pdf"}}`, js},
	}
}

func dispatchRefusingRoutes() []dispatchRoute {
	const js = "application/json"
	return append(dispatchCallingRoutes(),
		dispatchRoute{"rerank", "/v1/rerank", `{"model":"gpt-4o","query":"q","documents":["a","b"]}`, js},
		dispatchRoute{"count tokens", "/v1/count_tokens", `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`, js},
		dispatchRoute{"decisions", "/v1/decisions", `{"model":"gpt-4o","input":"hi","options":["a","b"]}`, js},
		dispatchRoute{"systemone", "/v1/systemone", `{"model":"gpt-4o","input":"hi"}`, js},
	)
}

// dispatchGateway is a gateway whose providers answer every call with a 500,
// counting the calls.
func dispatchGateway(t *testing.T) (*managedTestGateway, *atomic.Int32) {
	t.Helper()
	g := newManagedTestGateway(t, dxBYOKOrg())
	calls := new(atomic.Int32)
	fail := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"error":{"message":"upstream down","type":"server_error"}}`))
	})
	g.operator.Server.Config.Handler = fail
	g.managed.Server.Config.Handler = fail
	return g, calls
}

func serveDispatchRoute(g *managedTestGateway, rt dispatchRoute, key string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, rt.path, bytes.NewBufferString(rt.body))
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("Content-Type", rt.contentType)
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

func TestAProviderErrorOnEveryRouteCarriesNoDispatchedHeader(t *testing.T) {
	for _, rt := range dispatchCallingRoutes() {
		t.Run(rt.name, func(t *testing.T) {
			g, calls := dispatchGateway(t)
			w := serveDispatchRoute(g, rt, managedOrgKey)
			if calls.Load() == 0 || w.Code < 400 {
				t.Fatalf("%s: answered %d after %d provider calls, want a provider error: %s", rt.path, w.Code, calls.Load(), w.Body)
			}
			expectNoDispatchedHeader(t, w)
		})
	}
}

func TestAnUnknownKeyOnEveryRouteSaysNotDispatched(t *testing.T) {
	for _, rt := range dispatchRefusingRoutes() {
		t.Run(rt.name, func(t *testing.T) {
			g, calls := dispatchGateway(t)
			w := serveDispatchRoute(g, rt, "sk-agentcc-0000")
			if calls.Load() != 0 {
				t.Fatalf("%s: the provider was called %d times", rt.path, calls.Load())
			}
			expectNotDispatched(t, w, http.StatusUnauthorized)
		})
	}
}

// Video jobs: a refusal before any call says so, and an accepted job (the
// vendor is called later, by the job's worker) carries no header.
func TestVideoDispatchedHeader(t *testing.T) {
	f := newVideoHTTP(t)
	expectNotDispatched(t, f.request("POST", "/v1/videos", "sk-agentcc-0000", "", videoTestBody), http.StatusUnauthorized)
	accepted := f.request("POST", "/v1/videos", f.keyA, "dispatch-1", videoTestBody)
	if accepted.Code >= 400 {
		t.Fatalf("video create: %d %s", accepted.Code, accepted.Body)
	}
	expectNoDispatchedHeader(t, accepted)
}
