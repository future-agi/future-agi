package server

// A /-/batches batch runs as the org whose API key the
// submitter names in x-agentcc-batch-key. These tests drive the real gateway
// handler chain (New, its router and middleware, the auth and logging
// plugins) the way Django's console proxy does: the admin token, plus the
// batch key Django mints for the caller's org.

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	loggingplugin "github.com/futureagi/agentcc-gateway/internal/plugins/logging"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	r7BatchAdminToken  = "r7-batch-admin-token-3c9e1f7a5b2d8e406a1c"
	r7BatchOperatorKey = "r7-batch-node-operator-key"
	r7BatchOrgA        = delegated733Org
	r7BatchOrgB        = "org-r7-batch-b"
	r7BatchOrgAUpKey   = "r7-batch-org-a-provider-key"
	r7BatchKeyHeader   = "x-agentcc-batch-key"
)

// r7BatchUpstream is an OpenAI-format upstream that records the Authorization
// of every chat call. While hold is open, each chat call waits on it.
type r7BatchUpstream struct {
	*httptest.Server
	mu    sync.Mutex
	calls []string
	hold  chan struct{}
}

func startR7BatchUpstream(t *testing.T, hold bool) *r7BatchUpstream {
	t.Helper()
	u := &r7BatchUpstream{}
	if hold {
		u.hold = make(chan struct{})
	}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		w.Header().Set("Content-Type", "application/json")
		if r.Method != http.MethodPost || !strings.HasSuffix(r.URL.Path, "/chat/completions") {
			_, _ = io.WriteString(w, `{"object":"list","data":[]}`)
			return
		}
		u.mu.Lock()
		u.calls = append(u.calls, r.Header.Get("Authorization"))
		hold := u.hold // read under the lock: release() sets it to nil
		u.mu.Unlock()
		if hold != nil {
			select {
			case <-hold:
			case <-r.Context().Done():
				return
			}
		}
		_, _ = io.WriteString(w, `{"id":"chatcmpl-r7-batch","object":"chat.completion","created":1,"model":"gpt-4o-mini","choices":[{"index":0,"message":{"role":"assistant","content":"hello"},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":1,"total_tokens":6}}`)
	}))
	t.Cleanup(func() {
		u.release()
		u.Close()
	})
	return u
}

func (u *r7BatchUpstream) release() {
	u.mu.Lock()
	defer u.mu.Unlock()
	if u.hold != nil {
		close(u.hold)
		u.hold = nil
	}
}

func (u *r7BatchUpstream) seen() []string {
	u.mu.Lock()
	defer u.mu.Unlock()
	return append([]string(nil), u.calls...)
}

// r7BatchLogEntry is what the platform's request-log webhook receives for a
// request, as far as these tests read it.
type r7BatchLogEntry struct {
	RequestID  string            `json:"request_id"`
	AuthKeyID  string            `json:"auth_key_id"`
	StatusCode int               `json:"status_code"`
	Metadata   map[string]string `json:"metadata"`
}

type r7BatchLog struct {
	srv     *httptest.Server
	mu      sync.Mutex
	entries []r7BatchLogEntry
}

func startR7BatchLog(t *testing.T, tenants *tenant.Store) (*r7BatchLog, *loggingplugin.Plugin) {
	t.Helper()
	l := &r7BatchLog{}
	l.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Logs []json.RawMessage `json:"logs"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		for _, raw := range body.Logs {
			var kind struct {
				Kind string `json:"kind"`
			}
			if json.Unmarshal(raw, &kind) == nil && kind.Kind != "" {
				continue
			}
			var e r7BatchLogEntry
			if json.Unmarshal(raw, &e) == nil {
				l.mu.Lock()
				l.entries = append(l.entries, e)
				l.mu.Unlock()
			}
		}
	}))
	t.Cleanup(l.srv.Close)
	flusher := loggingplugin.NewLogFlusher(l.srv.URL, "", time.Hour, 1)
	ctx, cancel := context.WithCancel(context.Background())
	go flusher.Run(ctx)
	logging := loggingplugin.New(config.RequestLoggingConfig{Enabled: true, Workers: 1}, tenants)
	logging.SetFlusher(flusher)
	t.Cleanup(func() {
		cancel()
		logging.Close()
	})
	return l, logging
}

// waitFor returns the entries once there are n of them, or after 5 s.
func (l *r7BatchLog) waitFor(n int) []r7BatchLogEntry {
	for deadline := time.Now().Add(5 * time.Second); ; time.Sleep(10 * time.Millisecond) {
		l.mu.Lock()
		got := append([]r7BatchLogEntry(nil), l.entries...)
		l.mu.Unlock()
		if len(got) >= n || time.Now().After(deadline) {
			return got
		}
	}
}

type r7BatchKeys struct {
	orgA, orgB, revoked, expired         string // raw keys
	orgAID, orgBID, revokedID, expiredID string // key ids
}

type r7BatchRig struct {
	srv  *Server
	up   *r7BatchUpstream
	log  *r7BatchLog
	runs *runIDProbe // every pipeline run's request and trace id
	keys r7BatchKeys
}

// newR7BatchRig builds the gateway with the auth and logging plugins. The
// node's own openai provider holds the operator's key; org A has its own
// openai provider with its own key (the org provider a batch item must use).
func newR7BatchRig(t *testing.T, authEnabled, holdUpstream bool) *r7BatchRig {
	t.Helper()
	up := startR7BatchUpstream(t, holdUpstream)
	cfg := config.DefaultConfig()
	cfg.Admin.Token = r7BatchAdminToken
	cfg.Auth.Enabled = authEnabled
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL + "/v1", APIKey: r7BatchOperatorKey, APIFormat: "openai", Models: []string{"gpt-4o-mini"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	tenants.Set(r7BatchOrgA, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {Enabled: true, APIKey: r7BatchOrgAUpKey, Models: []string{"gpt-4o-mini"}},
	}})

	keyStore := auth.NewKeyStore(cfg.Auth)
	var k r7BatchKeys
	var key *auth.APIKey
	key, k.orgA = keyStore.Create("console-batch-a", "owner-a", nil, nil, map[string]string{"org_id": r7BatchOrgA})
	k.orgAID = key.ID
	key, k.orgB = keyStore.Create("console-batch-b", "owner-b", nil, nil, map[string]string{"org_id": r7BatchOrgB})
	k.orgBID = key.ID
	key, k.revoked = keyStore.Create("console-batch-revoked", "owner-a", nil, nil, map[string]string{"org_id": r7BatchOrgA})
	k.revokedID = key.ID
	keyStore.Revoke(key.ID)
	key, k.expired = keyStore.Create("console-batch-expired", "owner-a", nil, nil, map[string]string{"org_id": r7BatchOrgA})
	k.expiredID = key.ID
	past := time.Now().Add(-time.Minute)
	key.ExpiresAt = &past

	logs, logging := startR7BatchLog(t, tenants)
	probe := &runIDProbe{}
	engine := pipeline.NewEngine(authplugin.New(keyStore, authEnabled), logging, probe)
	srv := New(cfg, "", registry, engine, keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	return &r7BatchRig{srv: srv, up: up, log: logs, runs: probe, keys: k}
}

// admin calls an admin route with the admin token and, when batchKey is not
// empty, the batch key header.
func (rig *r7BatchRig) admin(t *testing.T, method, path, body, batchKey string) (int, http.Header, map[string]any, string) {
	t.Helper()
	req := httptest.NewRequest(method, path, bytes.NewReader([]byte(body)))
	req.Header.Set("Authorization", "Bearer "+r7BatchAdminToken)
	req.Header.Set("Content-Type", "application/json")
	if batchKey != "" {
		req.Header.Set(r7BatchKeyHeader, batchKey)
	}
	// The org-A happy path now represents FA's verified session delegation,
	// not a key accepted by the gateway. Other keys remain negative cases.
	if method == http.MethodPost && path == "/-/batches" && batchKey != "" && batchKey == rig.keys.orgA {
		prepareBatch733(t, rig.srv)
		rig.keys.orgAID = signBatch733(rig.srv, req, body)
	}
	w := httptest.NewRecorder()
	rig.srv.httpServer.Handler.ServeHTTP(w, req)
	raw := w.Body.String()
	var out map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &out)
	return w.Code, w.Header(), out, raw
}

const r7BatchBody = `{"requests":[{"model":"gpt-4o-mini","messages":[{"role":"user","content":"a"}]},{"model":"gpt-4o-mini","messages":[{"role":"user","content":"b"}]}],"max_concurrency":2}`

// errorCode reads error.code from an OpenAI-shaped error body.
func r7BatchErrorCode(out map[string]any) string {
	e, _ := out["error"].(map[string]any)
	code, _ := e["code"].(string)
	return code
}

// waitBatch polls GET /-/batches/{id}?org_id=orgID until the batch is
// completed or cancelled.
func (rig *r7BatchRig) waitBatch(t *testing.T, id, orgID string) map[string]any {
	t.Helper()
	path := fmt.Sprintf("/-/batches/%s?org_id=%s", id, orgID)
	for deadline := time.Now().Add(10 * time.Second); ; time.Sleep(10 * time.Millisecond) {
		code, _, out, raw := rig.admin(t, http.MethodGet, path, "", "")
		if code != http.StatusOK {
			t.Fatalf("GET %s: status %d body %s", path, code, raw)
		}
		if s := out["status"]; s == "completed" || s == "cancelled" {
			return out
		}
		if time.Now().After(deadline) {
			t.Fatalf("batch %s still %v after 10 s", id, out["status"])
		}
	}
}

// Every shipped config turns auth on. A batch submitted without naming the
// key its items run as would run with no org and no key, and every item would
// end invalid_api_key: the submit is refused instead, and no batch starts.
func TestR7BatchSubmitUnderAuthNeedsTheBatchKey(t *testing.T) {
	rig := newR7BatchRig(t, true, false)
	code, _, out, raw := rig.admin(t, http.MethodPost, "/-/batches", r7BatchBody, "")
	t.Logf("POST /-/batches without %s -> %d %s", r7BatchKeyHeader, code, strings.TrimSpace(raw))
	if code != http.StatusUnauthorized || r7BatchErrorCode(out) != "service_envelope_rejected" {
		t.Fatalf("submit without a batch key: status %d code %q; want 400 batch_key_required", code, r7BatchErrorCode(out))
	}
	time.Sleep(50 * time.Millisecond)
	if calls := rig.up.seen(); len(calls) != 0 {
		t.Fatalf("the upstream was called %d times for a refused batch", len(calls))
	}
}

// An unknown, revoked or expired batch key is refused at submit, as the same
// key would be on /v1: 401 invalid_api_key, and nothing runs.
func TestR7BatchSubmitRefusesUnknownRevokedOrExpiredKeys(t *testing.T) {
	rig := newR7BatchRig(t, true, false)
	for name, key := range map[string]string{
		"unknown": "sk-agentcc-not-a-key-of-this-gateway",
		"revoked": rig.keys.revoked,
		"expired": rig.keys.expired,
	} {
		code, hdr, out, raw := rig.admin(t, http.MethodPost, "/-/batches", r7BatchBody, key)
		t.Logf("%s key -> %d %s", name, code, strings.TrimSpace(raw))
		if code != http.StatusUnauthorized || r7BatchErrorCode(out) != "service_envelope_rejected" {
			t.Errorf("%s batch key: status %d code %q; want 401 invalid_api_key", name, code, r7BatchErrorCode(out))
		}
		if hdr.Get("WWW-Authenticate") == "" {
			t.Errorf("%s batch key: 401 without a WWW-Authenticate challenge", name)
		}
	}
	time.Sleep(50 * time.Millisecond)
	if calls := rig.up.seen(); len(calls) != 0 {
		t.Fatalf("the upstream was called %d times for refused batches", len(calls))
	}
}

// Each item runs as a request authenticated by the batch key: the org's own
// provider is called with the org's provider key (never the operator's); each
// item's run has its own request id and the batch request's trace id; and the
// request log holds each item under its request id, with the batch key's id
// and the key's org. The batch carries its org and key id, and the raw key is
// never in a response.
func TestR7BatchItemsRunAsTheOrgsKey(t *testing.T) {
	rig := newR7BatchRig(t, true, false)
	code, hdr, sub, raw := rig.admin(t, http.MethodPost, "/-/batches", r7BatchBody, rig.keys.orgA)
	t.Logf("POST /-/batches with org A's batch key -> %d %s", code, strings.TrimSpace(raw))
	if code != http.StatusAccepted {
		t.Fatalf("submit: status %d body %s", code, raw)
	}
	if strings.Contains(raw, rig.keys.orgA) {
		t.Fatalf("the submit response holds the raw batch key: %s", raw)
	}
	if sub["org_id"] != r7BatchOrgA || sub["key_id"] != rig.keys.orgAID {
		t.Fatalf("submit response org_id %v key_id %v; want %s and %s", sub["org_id"], sub["key_id"], r7BatchOrgA, rig.keys.orgAID)
	}
	batchTrace := hdr.Get("x-agentcc-trace-id")
	id, _ := sub["batch_id"].(string)

	got := rig.waitBatch(t, id, r7BatchOrgA)
	gotRaw, _ := json.Marshal(got)
	t.Logf("GET /-/batches/%s?org_id=%s -> %s", id, r7BatchOrgA, gotRaw)
	if strings.Contains(string(gotRaw), rig.keys.orgA) {
		t.Fatalf("the batch response holds the raw batch key")
	}
	if got["org_id"] != r7BatchOrgA {
		t.Errorf("batch org_id %v; want %s", got["org_id"], r7BatchOrgA)
	}
	results, _ := got["results"].([]any)
	if len(results) != 2 {
		t.Fatalf("results %v; want 2", got["results"])
	}
	itemIDs := map[string]bool{}
	for _, r := range results {
		item, _ := r.(map[string]any)
		if item["status"] != "success" {
			t.Errorf("item %v did not succeed", item)
		}
		rid, _ := item["request_id"].(string)
		if rid == "" || itemIDs[rid] {
			t.Errorf("item %v: request id missing or shared", item)
		}
		itemIDs[rid] = true
	}

	calls := rig.up.seen()
	t.Logf("upstream Authorization headers: %q", calls)
	if len(calls) != 2 {
		t.Fatalf("the upstream got %d chat calls for 2 items", len(calls))
	}
	for _, c := range calls {
		if c != "Bearer "+r7BatchOrgAUpKey {
			t.Errorf("an item called the upstream with %q; want org A's own provider key", c)
		}
	}

	runs := rig.runs.waitRuns(2)
	t.Logf("runs (request id, trace id): %q; the batch request's trace id %s", runs, batchTrace)
	if len(runs) != 2 {
		t.Fatalf("the pipeline ran %d times for 2 items", len(runs))
	}
	assertOwnRequestIDs(t, "batch item", runs)
	for _, r := range runs {
		if !itemIDs[r.requestID] {
			t.Errorf("a run with request id %q, which no item names (%v)", r.requestID, itemIDs)
		}
		if batchTrace == "" || r.traceID != batchTrace {
			t.Errorf("run %s: trace id %q; want the batch request's %q", r.requestID, r.traceID, batchTrace)
		}
	}

	entries := rig.log.waitFor(2)
	t.Logf("request log entries: %+v", entries)
	if len(entries) != 2 {
		t.Fatalf("the request log holds %d entries for 2 items", len(entries))
	}
	for _, e := range entries {
		if !itemIDs[e.RequestID] {
			t.Errorf("log entry under request id %q, which no item ran with (%v)", e.RequestID, itemIDs)
		}
		if e.AuthKeyID != rig.keys.orgAID {
			t.Errorf("log entry %s: auth_key_id %q; want the batch key %s", e.RequestID, e.AuthKeyID, rig.keys.orgAID)
		}
		if e.Metadata["org_id"] != r7BatchOrgA {
			t.Errorf("log entry %s: org_id %q; want %s", e.RequestID, e.Metadata["org_id"], r7BatchOrgA)
		}
		if e.StatusCode != http.StatusOK {
			t.Errorf("log entry %s: status %d", e.RequestID, e.StatusCode)
		}
	}
}

// GET and cancel name the org: another org (or none) gets 404 and cannot see
// or stop the batch; the batch's own org can.
func TestR7BatchGetAndCancelAreScopedToTheBatchOrg(t *testing.T) {
	rig := newR7BatchRig(t, true, true)
	code, _, sub, raw := rig.admin(t, http.MethodPost, "/-/batches", r7BatchBody, rig.keys.orgA)
	if code != http.StatusAccepted {
		t.Fatalf("submit: status %d body %s", code, raw)
	}
	id, _ := sub["batch_id"].(string)

	for _, path := range []string{
		fmt.Sprintf("/-/batches/%s?org_id=%s", id, r7BatchOrgB),
		fmt.Sprintf("/-/batches/%s", id),
	} {
		code, _, _, raw := rig.admin(t, http.MethodGet, path, "", "")
		t.Logf("GET %s -> %d %s", path, code, strings.TrimSpace(raw))
		if code != http.StatusNotFound {
			t.Errorf("GET %s: status %d; want 404", path, code)
		}
		if strings.Contains(raw, "processing") {
			t.Errorf("GET %s leaked the batch: %s", path, raw)
		}
	}
	for _, path := range []string{
		fmt.Sprintf("/-/batches/%s/cancel?org_id=%s", id, r7BatchOrgB),
		fmt.Sprintf("/-/batches/%s/cancel", id),
	} {
		code, _, _, raw := rig.admin(t, http.MethodPost, path, "", "")
		t.Logf("POST %s -> %d %s", path, code, strings.TrimSpace(raw))
		if code != http.StatusNotFound {
			t.Errorf("POST %s: status %d; want 404", path, code)
		}
	}
	code, _, got, raw := rig.admin(t, http.MethodGet, fmt.Sprintf("/-/batches/%s?org_id=%s", id, r7BatchOrgA), "", "")
	if code != http.StatusOK || got["status"] != "processing" || got["org_id"] != r7BatchOrgA {
		t.Fatalf("org A's GET after another org's cancel: %d %s; want 200, still processing, org_id %s", code, raw, r7BatchOrgA)
	}

	code, _, cancelled, raw := rig.admin(t, http.MethodPost, fmt.Sprintf("/-/batches/%s/cancel?org_id=%s", id, r7BatchOrgA), "", "")
	t.Logf("org A's cancel -> %d %s", code, strings.TrimSpace(raw))
	if code != http.StatusOK || cancelled["status"] != "cancelled" || cancelled["org_id"] != r7BatchOrgA {
		t.Fatalf("org A's cancel: %d %s; want 200 cancelled with org_id %s", code, raw, r7BatchOrgA)
	}
	rig.up.release()
	if final := rig.waitBatch(t, id, r7BatchOrgA); final["status"] != "cancelled" {
		t.Fatalf("final status %v; want cancelled", final["status"])
	}
}

// With auth off (no shipped config), a submit without a batch key still runs
// as before, and a batch key, when sent, is still checked and scopes the
// batch's GET and cancel to the key's org, so Django's org-scoped GET works on
// such a gateway too.
func TestR7BatchAuthOffKeyStillScopesTheBatch(t *testing.T) {
	rig := newR7BatchRig(t, false, false)

	code, _, sub, raw := rig.admin(t, http.MethodPost, "/-/batches", r7BatchBody, "")
	if code != http.StatusUnauthorized {
		t.Fatalf("keyless submit with auth off: status %d body %s; want 401", code, raw)
	}

	code, _, out, raw := rig.admin(t, http.MethodPost, "/-/batches", r7BatchBody, "sk-agentcc-not-a-key-of-this-gateway")
	if code != http.StatusUnauthorized || r7BatchErrorCode(out) != "service_envelope_rejected" {
		t.Fatalf("unknown batch key with auth off: %d %s; want 401 invalid_api_key", code, raw)
	}

	code, _, sub, raw = rig.admin(t, http.MethodPost, "/-/batches", r7BatchBody, rig.keys.orgA)
	if code != http.StatusAccepted || sub["org_id"] != r7BatchOrgA {
		t.Fatalf("keyed submit with auth off: %d %s; want 202 with org_id %s", code, raw, r7BatchOrgA)
	}
	id, _ := sub["batch_id"].(string)
	if got := rig.waitBatch(t, id, r7BatchOrgA); got["status"] != "completed" {
		t.Fatalf("keyed batch: %v", got)
	}
	if code, _, _, _ := rig.admin(t, http.MethodGet, fmt.Sprintf("/-/batches/%s?org_id=%s", id, r7BatchOrgB), "", ""); code != http.StatusNotFound {
		t.Fatalf("another org's GET with auth off: status %d; want 404", code)
	}
}

// An item answers what a /v1/chat/completions call with the batch key answers.
// A managed org key (auth and credits plugins in the pipeline) asking for a
// model that is not managed for it is refused before any provider is called,
// with the same error, on /v1 and for every batch item alike; the operator's
// provider is never called on the org's behalf.
func TestR7BatchItemsAreRefusedAsAChatCallWithTheKeyIs(t *testing.T) {
	const managedKey = "sk-agentcc-r7-batch-managed-no-credits"
	up := startR7BatchUpstream(t, false)
	cfg := config.DefaultConfig()
	cfg.Admin.Token = r7BatchAdminToken
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "managed", Key: managedKey, Owner: "m", KeyType: "managed", Metadata: map[string]string{"org_id": r7BatchOrgA}},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL + "/v1", APIKey: r7BatchOperatorKey, APIFormat: "openai", Models: []string{"gpt-4o-mini"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	keyStore := auth.NewKeyStore(cfg.Auth)
	engine := pipeline.NewEngine(authplugin.New(keyStore, true), credits.New(true, keyStore))
	srv := New(cfg, "", registry, engine, keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	rig := &r7BatchRig{srv: srv, up: up}

	chat := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(`{"model":"gpt-4o-mini","messages":[{"role":"user","content":"a"}]}`))
	chat.Header.Set("Authorization", "Bearer "+managedKey)
	chat.Header.Set("Content-Type", "application/json")
	cw := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(cw, chat)
	var direct map[string]any
	_ = json.Unmarshal(cw.Body.Bytes(), &direct)
	want := r7BatchErrorCode(direct)
	t.Logf("POST /v1/chat/completions with the managed key -> %d %s", cw.Code, strings.TrimSpace(cw.Body.String()))
	if cw.Code < 400 || want == "" {
		t.Fatalf("the direct call was not refused: %d %s", cw.Code, cw.Body.String())
	}

	// Managed personal keys no longer authorize batch submissions at all.
	// Delegated items retain Create's default type, never caller-selected type.
	code, _, sub, raw := rig.admin(t, http.MethodPost, "/-/batches", r7BatchBody, managedKey)
	if code != http.StatusUnauthorized || r7BatchErrorCode(sub) != "service_envelope_rejected" {
		t.Fatalf("legacy managed-key submit: status %d body %s", code, raw)
	}
	if calls := up.seen(); len(calls) != 0 {
		t.Fatalf("the upstream was called %d times", len(calls))
	}
}
