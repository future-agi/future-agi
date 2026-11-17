package server

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

// wireFe1c is wire733 for fe1c, a console-run grant's wire version:
// "fe1c." + base64url(canonical) + "." + base64url(mac), the canonical being
// fields joined by "\n" as given, the MAC fe1's HKDF (salt "fe1"). It is
// independent of auth's implementation.
func wireFe1c(fields []string, secret string) string {
	canonical := []byte(strings.Join(fields, "\n"))
	extract := hmac.New(sha256.New, []byte("fe1"))
	extract.Write([]byte(secret))
	expand := hmac.New(sha256.New, extract.Sum(nil))
	expand.Write(append([]byte(fields[3]+">"+fields[4]+"|"+fields[2]), 1))
	mac := hmac.New(sha256.New, expand.Sum(nil))
	mac.Write(canonical)
	return "fe1c." + base64.RawURLEncoding.EncodeToString(canonical) + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

// signedConsoleRun733 is signed733 for a Console run: an fe1c console-run
// grant naming models (field 20, comma-joined as given; callers pass them in
// ascending byte order) and an fe1 request envelope for route bound to it.
func signedConsoleRun733(route, body string, models []string, at int64) (string, string, []string) {
	id := nonce733()
	grant := wireFe1c([]string{"fe1c", "grant", "k1", "fa", "fa-eval-grant", "-", "-", fmt.Sprint(at), fmt.Sprint(at + 600), id, "console-run", delegated733Org, "-", delegated733User, "person", "chat:33333333-3333-4333-8333-333333333333", "eval", "-", "-", strings.Join(models, ",")}, "test-only-fa-secret-unavailable-to-gateway")
	fields := []string{"fe1", "request", "k1", "fa", "gw-delegated", "POST", route, fmt.Sprint(at), fmt.Sprint(at + 60), nonce733(), "-", delegated733Org, "-", delegated733User, "grant:" + id, "-", "console-run", digest733(body), digest733(grant)}
	return wire733(fields, delegated733Secret), grant, fields
}

// consoleRunCapServer is newDelegated733Server with an upstream that counts
// the model calls it is sent, and a ready verifier.
func consoleRunCapServer(t *testing.T) (*Server, *atomic.Int64) {
	t.Helper()
	var calls atomic.Int64
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/chat/completions") {
			calls.Add(1)
		}
		var body struct {
			Stream bool `json:"stream"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		if body.Stream {
			w.Header().Set("Content-Type", "text/event-stream")
			fmt.Fprint(w, "data: {\"id\":\"chat-test\",\"object\":\"chat.completion.chunk\",\"model\":\"gpt-4o-mini\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"streamed\"}}]}\n\n")
			w.(http.Flusher).Flush()
			fmt.Fprint(w, "data: [DONE]\n\n")
			return
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"id":"chat-test","object":"chat.completion","model":"gpt-4o-mini","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}`)
	}))
	t.Cleanup(up.Close)
	srv := newDelegated733Server(t, func(cfg *config.Config) {
		cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL + "/v1", APIFormat: "openai", Models: []string{"gpt-4o-mini"}}
	})
	srv.delegated = auth.NewEnvelopeVerifier(delegated733Secret, time.Now().Add(-time.Minute))
	return srv, &calls
}

// consoleRunCapRefusal asserts w is the model-cap refusal for model: 403,
// permission_error, model_not_permitted, and no stream.
func consoleRunCapRefusal(t *testing.T, w *httptest.ResponseRecorder, model string) {
	t.Helper()
	if w.Code != http.StatusForbidden {
		t.Fatalf("status %d, want 403: %s", w.Code, w.Body.String())
	}
	if ct := w.Header().Get("Content-Type"); strings.Contains(ct, "text/event-stream") {
		t.Fatalf("a refused call answered Content-Type %q", ct)
	}
	if strings.Contains(w.Body.String(), "data:") {
		t.Fatalf("a refused call carried SSE: %s", w.Body.String())
	}
	var got struct {
		Error struct {
			Message string `json:"message"`
			Type    string `json:"type"`
			Code    string `json:"code"`
		} `json:"error"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
		t.Fatalf("refusal body %q: %v", w.Body.String(), err)
	}
	want := fmt.Sprintf("model %q is not one this Console run may call", model)
	if got.Error.Code != "model_not_permitted" || got.Error.Type != "permission_error" || got.Error.Message != want {
		t.Fatalf("refusal = %+v, want code model_not_permitted, type permission_error, message %q", got.Error, want)
	}
}

// A console-run grant names the models the Console run may call. A model in
// it is served on the org's key (byok); any other model is refused 403
// model_not_permitted before the upstream is called or a stream begins, and
// an fe1 console-run grant is refused 401.
func TestDelegatedConsoleRunModelCap(t *testing.T) {
	srv, calls := consoleRunCapServer(t)
	call := func(body string, models []string) *httptest.ResponseRecorder {
		env, grant, _ := signedConsoleRun733(delegated733Route, body, models, time.Now().Unix())
		return request733(srv, delegated733Route, body, env, grant)
	}
	want := int64(0)
	upstream := func(row string) {
		t.Helper()
		if n := calls.Load(); n != want {
			t.Fatalf("%s: the upstream was sent %d model calls, want %d", row, n, want)
		}
	}

	// In the cap: served on the org's key.
	w := call(delegated733Body, []string{"gpt-4o-mini"})
	if w.Code != http.StatusOK {
		t.Fatalf("a model in the cap answered %d: %s", w.Code, w.Body.String())
	}
	if got := w.Header().Get("x-agentcc-billing-mode"); got != "byok" {
		t.Fatalf("x-agentcc-billing-mode = %q, want byok", got)
	}
	want++
	upstream("in the cap")

	// One of several ids in the cap (a body of its own, so no response cache
	// can answer it).
	if w := call(`{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hello again"}]}`, []string{"anthropic/claude-sonnet-5.5", "gpt-4o-mini"}); w.Code != http.StatusOK {
		t.Fatalf("a model among the cap's ids answered %d: %s", w.Code, w.Body.String())
	}
	want++
	upstream("among the cap's ids")

	// Not in the cap: the same served model, refused before the upstream.
	consoleRunCapRefusal(t, call(delegated733Body, []string{"gpt-4o"}), "gpt-4o-mini")
	upstream("not in the cap")

	// Case-sensitive: an id differing only in case is not the model.
	consoleRunCapRefusal(t, call(`{"model":"GPT-4o-mini","messages":[{"role":"user","content":"hello"}]}`, []string{"gpt-4o-mini"}), "GPT-4o-mini")
	upstream("case differs")

	// The model the chat handler reads (the last case-insensitive "model"
	// key) is the one checked.
	consoleRunCapRefusal(t, call(`{"model":"gpt-4o","Model":"gpt-4o-mini","messages":[{"role":"user","content":"hello"}]}`, []string{"gpt-4o"}), "gpt-4o-mini")
	upstream("a second model key")

	// A stream asking for a model not in the cap is refused before any SSE;
	// the same stream in the cap streams.
	stream := `{"model":"gpt-4o-mini","stream":true,"messages":[{"role":"user","content":"hello"}]}`
	consoleRunCapRefusal(t, call(stream, []string{"gpt-4o"}), "gpt-4o-mini")
	upstream("a stream not in the cap")
	if w := call(strings.Replace(stream, "hello", "hello, streamed", 1), []string{"gpt-4o-mini"}); w.Code != http.StatusOK || !strings.Contains(w.Body.String(), "data:") {
		t.Fatalf("a stream in the cap answered %d: %s", w.Code, w.Body.String())
	}
	want++
	upstream("a stream in the cap")

	// No string model: refused with model "".
	consoleRunCapRefusal(t, call(`{"messages":[{"role":"user","content":"hello"}]}`, []string{"gpt-4o-mini"}), "")
	consoleRunCapRefusal(t, call(`{"model":7,"messages":[{"role":"user","content":"hello"}]}`, []string{"gpt-4o-mini"}), "")
	consoleRunCapRefusal(t, call(`{"model":null,"messages":[{"role":"user","content":"hello"}]}`, []string{"gpt-4o-mini"}), "")
	consoleRunCapRefusal(t, call(`not json`, []string{"gpt-4o-mini"}), "")
	upstream("no string model")

	// An fe1 console-run grant (no models) is refused as every bad envelope is.
	env, grant, _ := signed733(delegated733Route, delegated733Body, "console-run", time.Now().Unix())
	if w := request733(srv, delegated733Route, delegated733Body, env, grant); w.Code != http.StatusUnauthorized {
		t.Fatalf("an fe1 console-run grant answered %d, want 401: %s", w.Code, w.Body.String())
	}
	upstream("an fe1 console-run grant")
}

// The cap is a console-run grant's alone: a playground grant (fe1, no
// models) still calls any model the org serves.
func TestDelegatedModelCapOnlyForConsoleRun(t *testing.T) {
	srv, calls := consoleRunCapServer(t)
	env, grant, _ := signed733(delegated733Route, delegated733Body, "playground", time.Now().Unix())
	if w := request733(srv, delegated733Route, delegated733Body, env, grant); w.Code != http.StatusOK {
		t.Fatalf("a playground call answered %d: %s", w.Code, w.Body.String())
	}
	if n := calls.Load(); n != 1 {
		t.Fatalf("the upstream was sent %d model calls, want 1", n)
	}
}

// A Console run calls only its own model: a body that names fallback models
// (models[], fallbacks, provider options) or an x-agentcc-config header is
// refused 403 model_not_permitted before the upstream is called, even when
// its model is in the cap, so no failure can carry the run to another model.
func TestDelegatedConsoleRunRefusesItsOwnFallbacks(t *testing.T) {
	srv, calls := consoleRunCapServer(t)
	model := "gpt-4o-mini"
	for _, row := range []struct {
		name, body, config string
	}{
		{"models[]", `{"model":"gpt-4o-mini","models":["gpt-4o"],"messages":[{"role":"user","content":"hi"}]}`, ""},
		{"Models[] in another case", `{"model":"gpt-4o-mini","Models":["gpt-4o"],"messages":[{"role":"user","content":"hi"}]}`, ""},
		{"fallbacks", `{"model":"gpt-4o-mini","fallbacks":["gpt-4o"],"messages":[{"role":"user","content":"hi"}]}`, ""},
		{"provider options", `{"model":"gpt-4o-mini","providerOptions":{"gateway":{"models":["gpt-4o"]}},"messages":[{"role":"user","content":"hi"}]}`, ""},
		{"x-agentcc-config", `{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}`, `{"fallback":{"models":["gpt-4o"]}}`},
	} {
		t.Run(row.name, func(t *testing.T) {
			before := calls.Load()
			env, grant, _ := signedConsoleRun733(delegated733Route, row.body, []string{model}, time.Now().Unix())
			r := httptest.NewRequest("POST", delegated733Route, strings.NewReader(row.body))
			r.Header.Set("Content-Type", "application/json")
			r.Header.Set("X-FA-Envelope", env)
			r.Header.Set("X-FA-Grant", grant)
			if row.config != "" {
				r.Header.Set("X-Agentcc-Config", row.config)
			}
			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, r)
			if w.Code != http.StatusForbidden || !strings.Contains(w.Body.String(), `"model_not_permitted"`) {
				t.Fatalf("status %d, want 403 model_not_permitted: %s", w.Code, w.Body.String())
			}
			if n := calls.Load() - before; n != 0 {
				t.Fatalf("the upstream was sent %d model calls for a refused call", n)
			}
		})
	}
	// An empty models[] names no other model and is served.
	body := `{"model":"gpt-4o-mini","models":[],"messages":[{"role":"user","content":"hi"}]}`
	env, grant, _ := signedConsoleRun733(delegated733Route, body, []string{model}, time.Now().Unix())
	if w := request733(srv, delegated733Route, body, env, grant); w.Code != http.StatusOK {
		t.Fatalf("an empty models[] answered %d: %s", w.Code, w.Body.String())
	}
}
