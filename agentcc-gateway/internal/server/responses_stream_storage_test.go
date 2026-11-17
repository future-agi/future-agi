package server

import (
	"fmt"
	"net/http"
	"strings"
	"testing"
)

const storedStreamResponse = `{"id":"resp_stream_stored","object":"response","status":"completed","model":"gpt-4o","output":[{"type":"message","id":"msg_stored","status":"completed","role":"assistant","content":[{"type":"output_text","text":"hi","annotations":[]}]}],"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2},"metadata":{"fixture":"stream-storage"}}`

// Use the existing authenticated BYOK gateway fixture, with native Responses
// events rather than a chat stream. The terminal response must be stored intact,
// not the chat-shaped response used internally for pricing and output checks.
func newStoredStreamGateway(t *testing.T, event, response string, block bool) *postBlockGateway {
	t.Helper()
	setup := postBlockSetup{upstream: func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/responses" {
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, "event: response.created\ndata: {\"type\":\"response.created\",\"response\":{\"id\":\"resp_stream_stored\",\"status\":\"in_progress\"}}\n\n")
		w.(http.Flusher).Flush()
		if event != "" {
			terminal := fmt.Sprintf("event: %s\ndata: {\"type\":%q,\"response\":%s}\n\n", event, event, response)
			// The retained response must survive reads that split a data line.
			mid := len(terminal) / 2
			fmt.Fprint(w, terminal[:mid])
			w.(http.Flusher).Flush()
			fmt.Fprint(w, terminal[mid:])
			w.(http.Flusher).Flush()
		}
	}}
	if block {
		setup.staticRule = answerBlockRule
	}
	return newPostBlockGateway(t, setup)
}

func TestResponsesStream_StoredResponseLifecycle(t *testing.T) {
	for _, tc := range []struct {
		name, store string
	}{
		{"default storage", ""},
		{"explicit storage", `,"store":true`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := newStoredStreamGateway(t, "response.completed", storedStreamResponse, false)
			body := `{"model":"gpt-4o","input":"hi","stream":true` + tc.store + `}`
			created := objCall(t, g.srv, http.MethodPost, "/v1/responses", postBlockKey, body,
				map[string]string{"x-api-key": postBlockOpsKey})
			if created.Code != http.StatusOK || !strings.Contains(created.Body.String(), "response.completed") {
				t.Fatalf("stream did not complete: %d %s", created.Code, created.Body.String())
			}
			path := "/v1/responses/resp_stream_stored"
			for _, method := range []string{http.MethodGet, http.MethodDelete} {
				other := objCall(t, g.srv, method, path, postBlockOpsKey, "", nil)
				if other.Code != http.StatusNotFound {
					t.Errorf("%s by another tenant: got %d, want 404", method, other.Code)
				}
			}
			got := objCall(t, g.srv, http.MethodGet, path, postBlockKey, "", nil)
			if got.Code != http.StatusOK || got.Body.String() != storedStreamResponse {
				t.Fatalf("GET completed response: %d %s; want the complete native response object", got.Code, got.Body.String())
			}
			store := g.srv.handlers.responsesStore
			stored := store.Get("resp_stream_stored", postBlockOrg)
			if stored == nil || stored.ContinuationBinding == "" || !store.CanContinue(stored.ID, postBlockOrg, stored.ContinuationBinding) ||
				store.CanContinue(stored.ID, "", stored.ContinuationBinding) || store.CanContinue(stored.ID, postBlockOrg, "different-binding") {
				t.Fatal("stream storage lost the exact tenant and continuation binding")
			}
			deleted := objCall(t, g.srv, http.MethodDelete, path, postBlockKey, "", nil)
			if deleted.Code != http.StatusOK || mediaDecode(t, deleted)["deleted"] != true {
				t.Fatalf("DELETE completed response: %d %s", deleted.Code, deleted.Body.String())
			}
			if store.CanContinue(stored.ID, postBlockOrg, stored.ContinuationBinding) {
				t.Error("deleted streamed response still authorizes continuation")
			}
			for _, method := range []string{http.MethodGet, http.MethodDelete} {
				got := objCall(t, g.srv, method, path, postBlockKey, "", nil)
				if got.Code != http.StatusNotFound {
					t.Errorf("%s after deletion: got %d, want 404", method, got.Code)
				}
			}
		})
	}
}

func TestResponsesStream_DoesNotStoreUnretainedAnswers(t *testing.T) {
	for _, tc := range []struct {
		name, requestStore, event, response string
		block                               bool
	}{
		{"request opts out even without echoed store", `,"store":false`, "response.completed", storedStreamResponse, false},
		{"response opts out", `,"store":true`, "response.completed", strings.Replace(storedStreamResponse, `"object":"response"`, `"object":"response","store":false`, 1), false},
		{"no terminal event", "", "", "", false},
		{"failed response", "", "response.failed", strings.Replace(storedStreamResponse, `"status":"completed"`, `"status":"failed"`, 1), false},
		{"incomplete response", "", "response.incomplete", strings.Replace(storedStreamResponse, `"status":"completed"`, `"status":"incomplete"`, 1), false},
		{"missing response", "", "response.completed", "null", false},
		{"malformed response", "", "response.completed", "{", false},
		{"output blocked", "", "response.completed", storedStreamResponse, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := newStoredStreamGateway(t, tc.event, tc.response, tc.block)
			body := `{"model":"gpt-4o","input":"hi","stream":true` + tc.requestStore + `}`
			created := objCall(t, g.srv, http.MethodPost, "/v1/responses", postBlockKey, body, nil)
			if created.Code != http.StatusOK {
				t.Fatalf("create stream: %d %s", created.Code, created.Body.String())
			}
			if tc.block && (!strings.Contains(created.Body.String(), "content_blocked") || strings.Contains(created.Body.String(), "response.completed")) {
				t.Fatalf("output policy did not withhold the completed response: %s", created.Body.String())
			}
			path := "/v1/responses/resp_stream_stored"
			if got := objCall(t, g.srv, http.MethodGet, path, postBlockKey, "", nil); got.Code != http.StatusNotFound {
				t.Errorf("GET unretained response: %d %s, want 404", got.Code, got.Body.String())
			}
			// L01 records the created ID's continuation authority without a body.
			// Preserve its deletion semantics: DELETE revokes that authority even
			// when GET has no answer. Request store:false records nothing at all.
			wantDelete := http.StatusOK
			if tc.requestStore == `,"store":false` {
				wantDelete = http.StatusNotFound
			}
			if got := objCall(t, g.srv, http.MethodDelete, path, postBlockKey, "", nil); got.Code != wantDelete {
				t.Errorf("DELETE lifecycle record: %d %s, want %d", got.Code, got.Body.String(), wantDelete)
			}
			if got := objCall(t, g.srv, http.MethodPost, "/v1/responses", postBlockKey, continuationBody("resp_stream_stored", true), nil); got.Code != http.StatusNotFound {
				t.Errorf("continuation after deletion: %d %s, want 404", got.Code, got.Body.String())
			}
		})
	}
}

func TestResponsesStream_DoesNotStoreAfterSkippedTerminalEvent(t *testing.T) {
	g := newPostBlockGateway(t, postBlockSetup{upstream: func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/responses" {
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprintf(w, "event: response.completed\ndata: {\"type\":\"response.completed\",\"response\":%s}\n\n", storedStreamResponse)
		w.(http.Flusher).Flush()
		// A skipped final event must not publish an earlier, stale answer.
		fmt.Fprintf(w, "event: response.completed\ndata: {\"type\":\"response.completed\",\"response\":{\"id\":\"resp_stream_stored\",\"padding\":%q}}\n\n", strings.Repeat("x", maxResponsesUsageLineBytes))
	}})
	created := objCall(t, g.srv, http.MethodPost, "/v1/responses", postBlockKey, `{"model":"gpt-4o","input":"hi","stream":true}`, nil)
	if created.Code != http.StatusOK {
		t.Fatalf("create stream: %d", created.Code)
	}
	if got := objCall(t, g.srv, http.MethodGet, "/v1/responses/resp_stream_stored", postBlockKey, "", nil); got.Code != http.StatusNotFound {
		t.Errorf("GET after skipped terminal event: %d %s, want 404", got.Code, got.Body.String())
	}
}
