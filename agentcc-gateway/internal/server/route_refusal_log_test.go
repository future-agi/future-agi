package server

// A request the gateway refuses while choosing its provider (a provider
// lock, an unknown model, a model outside the key's access groups, the key's
// provider list, the cost-cap qualification) is refused before the pipeline,
// whose plugins authenticate the key and write the request log, has run.
// The rule: such a request whose key authenticates gets one request-log row,
// with the answer's status and error code, error_source "gateway" and no
// request or response body. A request whose key does not authenticate is
// answered as the pipeline answers it (401) and gets no row.

import (
	"bytes"
	"context"
	"encoding/json"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	loggingplugin "github.com/futureagi/agentcc-gateway/internal/plugins/logging"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	rrlOrg      = "org-route-refusal-log"
	rrlKey      = "sk-agentcc-rrl-org-key-0123456789abcdef"
	rrlGroupKey = "sk-agentcc-rrl-group-key-0123456789abcd"
	rrlBadKey   = "sk-agentcc-rrl-not-a-key-0123456789abcd"
)

// rrlGateway is a gateway with the auth and logging plugins, its request log
// sent to a recording webhook with bodies kept (content mode full, the
// default). The org holds its own "openai" (m-chat), a disabled org-only
// "acme-off" and an org-only Anthropic-format "acme-claude"; the org
// providers point at the test upstream's private address. rrlGroupKey has
// the access group "small", which holds only other-model. entries flushes the
// log and returns every entry the webhook received.
func rrlGateway(t *testing.T) (srv *Server, up *plUpstream, entries func() []map[string]any) {
	t.Helper()
	up = newPLUpstream(t)
	sink := startR7LogSink(t)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "rrl", Key: rrlKey, Owner: "t", KeyType: "byok", Metadata: map[string]string{"org_id": rrlOrg}},
		{Name: "rrl-groups", Key: rrlGroupKey, Owner: "t", KeyType: "byok", Metadata: map[string]string{"org_id": rrlOrg, "access_groups": "small"}},
	}
	cfg.Routing.AccessGroups = config.AccessGroupsConfig{"small": {Models: []string{"other-model"}}}
	off := false
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "operator-openai-key", APIFormat: "openai", Models: []string{"m-chat"}, AutoDiscover: &off}
	cfg.Providers["other"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "operator-other-key", APIFormat: "openai", Models: []string{"other-model"}, AutoDiscover: &off}
	cfg.OrgProviders.AllowPrivateURLs = true
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	t.Cleanup(func() { registry.Close() })
	tenants := tenant.NewStore()
	tenants.Set(rrlOrg, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai":      {Enabled: true, APIKey: "org-openai-key", Models: []string{"m-chat"}},
		"acme-off":    {Enabled: false, APIKey: "org-off-key", APIFormat: "openai", BaseURL: up.URL, Models: []string{"m-chat"}},
		"acme-claude": {Enabled: true, APIKey: "org-claude-key", APIFormat: "anthropic", BaseURL: up.URL, Models: []string{"c-msg"}},
	}})
	keyStore := auth.NewKeyStore(cfg.Auth)
	flusher := loggingplugin.NewLogFlusher(sink.URL, "", time.Hour, 100)
	logPlugin := loggingplugin.New(config.RequestLoggingConfig{Enabled: true, IncludeBodies: true}, tenants)
	logPlugin.SetFlusher(flusher)
	srv = New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true), logPlugin), keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	entries = func() []map[string]any {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		flusher.Close(ctx)
		sink.mu.Lock()
		defer sink.mu.Unlock()
		return append([]map[string]any(nil), sink.entries...)
	}
	return srv, up, entries
}

// rrlRequest is one request to an endpoint: its path, content type and body.
type rrlRequest struct {
	name, path, contentType string
	body                    []byte
}

func rrlJSON(name, path, body string) rrlRequest {
	return rrlRequest{name: name, path: path, contentType: "application/json", body: []byte(body)}
}

// rrlMultipart is an audio upload naming model.
func rrlMultipart(t *testing.T, name, path, model string) rrlRequest {
	t.Helper()
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	fw, err := mw.CreateFormFile("file", "a.wav")
	if err != nil {
		t.Fatal(err)
	}
	_, _ = fw.Write([]byte("RIFF0000WAVEfmt "))
	_ = mw.WriteField("model", model)
	_ = mw.Close()
	return rrlRequest{name: name, path: path, contentType: mw.FormDataContentType(), body: buf.Bytes()}
}

func rrlDo(srv *Server, req rrlRequest, key, lock string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(http.MethodPost, req.path, bytes.NewReader(req.body))
	r.Header.Set("Content-Type", req.contentType)
	r.Header.Set("Authorization", "Bearer "+key)
	if strings.HasPrefix(req.path, "/v1/messages") {
		r.Header.Set("anthropic-version", "2023-06-01")
	}
	if lock != "" {
		r.Header.Set("x-agentcc-provider-lock", lock)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, r)
	return w
}

// rrlOneRefusalRow checks that entries hold exactly one row, for the refused
// request: its status is the answer's, its error code is set (and is the
// answer's, when wantCode is given), its error is the gateway's own, it names
// the key that authenticated, and it carries no request or response body.
func rrlOneRefusalRow(t *testing.T, w *httptest.ResponseRecorder, entries []map[string]any, wantCode string) map[string]any {
	t.Helper()
	if len(entries) != 1 {
		t.Fatalf("%d request-log rows, want 1 for the refused request (answer %d %s)", len(entries), w.Code, w.Body.String())
	}
	e := entries[0]
	if got, _ := e["status_code"].(float64); int(got) != w.Code {
		t.Errorf("row status_code = %v, want the answer's %d", e["status_code"], w.Code)
	}
	code, _ := e["error_code"].(string)
	if code == "" || (wantCode != "" && code != wantCode) {
		t.Errorf("row error_code = %q, want %q", code, wantCode)
	}
	if e["error_source"] != "gateway" {
		t.Errorf("row error_source = %v, want gateway", e["error_source"])
	}
	if e["is_error"] != true {
		t.Errorf("row is_error = %v, want true", e["is_error"])
	}
	if id, _ := e["auth_key_id"].(string); id == "" {
		t.Errorf("row has no auth_key_id; the refused request's key authenticated")
	}
	for _, field := range []string{"request_body", "response_body"} {
		if _, ok := e[field]; ok {
			t.Errorf("row carries %s; a route-stage refusal's row has no body", field)
		}
	}
	return e
}

// A served request's row does carry its body here (content mode full), so
// the no-body checks below are not vacuous.
func TestRouteRefusalLogServedRowCarriesBody(t *testing.T) {
	srv, _, entries := rrlGateway(t)
	w := rrlDo(srv, rrlJSON("chat", "/v1/chat/completions", plChatBody), rrlKey, "")
	if w.Code != http.StatusOK {
		t.Fatalf("served chat -> %d %s", w.Code, w.Body.String())
	}
	rows := entries()
	if len(rows) != 1 {
		t.Fatalf("%d rows, want 1", len(rows))
	}
	if _, ok := rows[0]["request_body"]; !ok {
		t.Fatalf("a served request's row has no request_body; the no-body checks would prove nothing (row %v)", rows[0])
	}
}

// Chat's route stage (resolveRoute and the checks after it): each refusal of
// a request whose key authenticates gets one row.
func TestRouteRefusalChatAuthenticatedGetsOneRow(t *testing.T) {
	chat := rrlJSON("chat", "/v1/chat/completions", plChatBody)
	for _, tc := range []struct {
		name, key, lock string
		req             rrlRequest
		wantStatus      int
		wantCode        string
		wantLockTarget  string
		wantModel       string
	}{
		{"lock to a missing provider", rrlKey, "nope", chat, http.StatusNotFound, "provider_not_found", "nope", "m-chat"},
		{"lock to a provider the key may not use", rrlKey, "acme-off", chat, http.StatusForbidden, "permission_denied", "acme-off", "m-chat"},
		{"unknown model", rrlKey, "", rrlJSON("chat", "/v1/chat/completions", `{"model":"no-such-model","messages":[{"role":"user","content":"hi"}]}`), 0, "", "", "no-such-model"},
		{"model outside the key's access groups", rrlGroupKey, "", chat, http.StatusForbidden, "", "", "m-chat"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv, up, entries := rrlGateway(t)
			w := rrlDo(srv, tc.req, tc.key, tc.lock)
			if w.Code < 400 || (tc.wantStatus != 0 && w.Code != tc.wantStatus) {
				t.Fatalf("-> %d %s, want refusal %d", w.Code, w.Body.String(), tc.wantStatus)
			}
			if tc.wantCode != "" && r7Code(w) != tc.wantCode {
				t.Fatalf("-> %d %s, want code %s", w.Code, r7Code(w), tc.wantCode)
			}
			if calls := up.snapshot(); len(calls) != 0 {
				t.Fatalf("upstream calls = %q, want none", calls)
			}
			row := rrlOneRefusalRow(t, w, entries(), r7Code(w))
			if row["model"] != tc.wantModel {
				t.Errorf("row model = %v, want %q, the model as requested", row["model"], tc.wantModel)
			}
			if tc.wantLockTarget != "" {
				meta, _ := row["metadata"].(map[string]any)
				if meta["provider_lock_target"] != tc.wantLockTarget {
					t.Errorf("row metadata provider_lock_target = %v, want %q (metadata %v)", meta["provider_lock_target"], tc.wantLockTarget, meta)
				}
			}
		})
	}
}

// A request whose key does not authenticate gets the pipeline's answer, 401,
// before any route-stage answer, and no row.
func TestRouteRefusalUnauthenticatedGets401AndNoRow(t *testing.T) {
	for _, tc := range []struct {
		name, lock string
		req        rrlRequest
	}{
		{"lock to a missing provider", "nope", rrlJSON("chat", "/v1/chat/completions", plChatBody)},
		{"lock to a provider the key may not use", "acme-off", rrlJSON("chat", "/v1/chat/completions", plChatBody)},
		{"unknown model", "", rrlJSON("chat", "/v1/chat/completions", `{"model":"no-such-model","messages":[{"role":"user","content":"hi"}]}`)},
		{"unknown model on embeddings", "", rrlJSON("embeddings", "/v1/embeddings", `{"model":"no-such-model","input":"hi"}`)},
		{"unknown model on messages", "", rrlJSON("messages", "/v1/messages", `{"model":"no-such-model","max_tokens":16,"messages":[{"role":"user","content":"hi"}]}`)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv, up, entries := rrlGateway(t)
			w := rrlDo(srv, tc.req, rrlBadKey, tc.lock)
			if w.Code != http.StatusUnauthorized {
				t.Fatalf("-> %d %s, want 401: the key is checked before the route-stage answer", w.Code, w.Body.String())
			}
			if calls := up.snapshot(); len(calls) != 0 {
				t.Fatalf("upstream calls = %q, want none", calls)
			}
			if rows := entries(); len(rows) != 0 {
				t.Fatalf("%d request-log rows for an unauthenticated request, want none: %v", len(rows), rows)
			}
		})
	}
}

// Every other endpoint that resolves its provider before the pipeline: an
// unknown model refused for a key that authenticates gets one row; for a key
// that does not, 401 and no row. Search is the one exception for the row:
// its requests name no model and the request log keeps none of them, served
// or refused, so an unknown search provider gets the same 401 rule and no
// row either way.
func TestRouteRefusalEveryRoutedEndpoint(t *testing.T) {
	const m = "no-such-model"
	reqs := []rrlRequest{
		rrlJSON("completions", "/v1/completions", `{"model":"`+m+`","prompt":"hi"}`),
		rrlJSON("embeddings", "/v1/embeddings", `{"model":"`+m+`","input":"hi"}`),
		rrlJSON("images", "/v1/images/generations", `{"model":"`+m+`","prompt":"a cat"}`),
		rrlJSON("speech", "/v1/audio/speech", `{"model":"`+m+`","input":"hi","voice":"alloy"}`),
		rrlJSON("speech stream", "/v1/audio/speech/stream", `{"model":"`+m+`","input":"hi","voice":"alloy"}`),
		rrlMultipart(t, "transcriptions", "/v1/audio/transcriptions", m),
		rrlMultipart(t, "translations", "/v1/audio/translations", m),
		rrlJSON("rerank", "/v1/rerank", `{"model":"`+m+`","query":"q","documents":["a","b"]}`),
		rrlJSON("responses", "/v1/responses", `{"model":"`+m+`","input":"hi"}`),
		rrlJSON("messages", "/v1/messages", `{"model":"`+m+`","max_tokens":16,"messages":[{"role":"user","content":"hi"}]}`),
		rrlJSON("count tokens", "/v1/messages/count_tokens", `{"model":"`+m+`","messages":[{"role":"user","content":"hi"}]}`),
		rrlJSON("genai", "/v1beta/models/"+m+":generateContent", `{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}`),
		rrlJSON("ocr", "/v1/ocr", `{"model":"`+m+`","document":{"type":"document_url","document_url":"https://example.com/a.pdf"}}`),
	}
	search := rrlJSON("search", "/v1/search", `{"search_provider":"no-such-search","query":"q"}`)
	t.Run("search/authenticated", func(t *testing.T) {
		srv, _, entries := rrlGateway(t)
		w := rrlDo(srv, search, rrlKey, "")
		if w.Code < 400 || w.Code == http.StatusUnauthorized {
			t.Fatalf("-> %d %s, want the route-stage refusal", w.Code, w.Body.String())
		}
		if rows := entries(); len(rows) != 0 {
			t.Fatalf("%d rows for a search request, want none (search is not request-logged): %v", len(rows), rows)
		}
	})
	t.Run("search/unauthenticated", func(t *testing.T) {
		srv, _, _ := rrlGateway(t)
		if w := rrlDo(srv, search, rrlBadKey, ""); w.Code != http.StatusUnauthorized {
			t.Fatalf("-> %d %s, want 401", w.Code, w.Body.String())
		}
	})
	for _, req := range reqs {
		t.Run(req.name+"/authenticated", func(t *testing.T) {
			srv, up, entries := rrlGateway(t)
			w := rrlDo(srv, req, rrlKey, "")
			if w.Code < 400 || w.Code == http.StatusUnauthorized {
				t.Fatalf("-> %d %s, want the route-stage refusal", w.Code, w.Body.String())
			}
			if calls := up.snapshot(); len(calls) != 0 {
				t.Fatalf("upstream calls = %q, want none", calls)
			}
			wantCode := ""
			if !strings.HasPrefix(req.path, "/v1/messages") && !strings.HasPrefix(req.path, "/v1beta/") {
				wantCode = r7Code(w)
			}
			rrlOneRefusalRow(t, w, entries(), wantCode)
		})
		t.Run(req.name+"/unauthenticated", func(t *testing.T) {
			srv, _, entries := rrlGateway(t)
			w := rrlDo(srv, req, rrlBadKey, "")
			if w.Code != http.StatusUnauthorized {
				t.Fatalf("-> %d %s, want 401", w.Code, w.Body.String())
			}
			if rows := entries(); len(rows) != 0 {
				t.Fatalf("%d request-log rows for an unauthenticated request, want none: %v", len(rows), rows)
			}
		})
	}
}

// A request served through a provider lock is logged as before, with its
// body: the body is withheld only from a route-stage refusal's row.
func TestRouteRefusalLogLeavesLockedServedRowsAlone(t *testing.T) {
	srv, _, entries := rrlGateway(t)
	w := rrlDo(srv, rrlJSON("chat", "/v1/chat/completions", plChatBody), rrlKey, "openai")
	if w.Code != http.StatusOK {
		t.Fatalf("lock to the org's own openai -> %d %s, want 200", w.Code, w.Body.String())
	}
	rows := entries()
	if len(rows) != 1 {
		t.Fatalf("%d rows, want 1", len(rows))
	}
	var body map[string]any
	if raw, ok := rows[0]["request_body"]; !ok {
		t.Fatalf("served row has no request_body")
	} else if b, err := json.Marshal(raw); err != nil || json.Unmarshal(b, &body) != nil {
		t.Fatalf("served row request_body unreadable: %v", raw)
	}
}
