package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/rotation"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// POST /-/admin/providers/{id}/rotate, then
// .../rotate/promote, really changes the key the gateway sends upstream.

const (
	r7RotAdmin    = "r7-ops-rotation-admin-token-0123456789abcdef0123456789ab"
	r7RotInternal = "sk-r7-ops-rotation-internal"
	r7RotOrgKey   = "sk-r7-ops-rotation-org-byok"
	r7RotOrg      = "org-r7-rotation"
	r7RotOld      = "sk-OLD-aaaa1111-operator"
	r7RotNew      = "sk-NEW-bbbb2222-operator"
	r7RotBad      = "sk-BAD-cccc3333-operator"
	r7RotOrgOwn   = "sk-ORG-dddd4444-own"
)

// r7RotUpstream is an OpenAI-format upstream: GET /v1/models answers 401 for
// r7RotBad and 200 otherwise; every chat request's Authorization is recorded.
// A chat request whose last message is "hold" waits for release.
type r7RotUpstream struct {
	*httptest.Server
	mu      sync.Mutex
	chats   []string
	lists   []string
	held    chan struct{}
	release chan struct{}
}

func startR7RotUpstream(t *testing.T) *r7RotUpstream {
	t.Helper()
	u := &r7RotUpstream{held: make(chan struct{}, 1), release: make(chan struct{})}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		authz := r.Header.Get("Authorization")
		body, _ := io.ReadAll(r.Body)
		w.Header().Set("Content-Type", "application/json")
		if r.Method == http.MethodGet {
			// A loopback provider gets an unauthenticated connectivity
			// check at registry start; only keyed list calls are recorded.
			if authz != "" {
				u.mu.Lock()
				u.lists = append(u.lists, authz)
				u.mu.Unlock()
			}
			if authz == "Bearer "+r7RotBad {
				w.WriteHeader(http.StatusUnauthorized)
				io.WriteString(w, `{"error":{"message":"Incorrect API key provided","type":"invalid_request_error","code":"invalid_api_key"}}`)
				return
			}
			io.WriteString(w, `{"object":"list","data":[]}`)
			return
		}
		u.mu.Lock()
		u.chats = append(u.chats, authz)
		u.mu.Unlock()
		if bytes.Contains(body, []byte(`"hold"`)) {
			u.held <- struct{}{}
			<-u.release
		}
		io.WriteString(w, `{"id":"x","object":"chat.completion","created":1,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
	}))
	t.Cleanup(u.Close)
	return u
}

func (u *r7RotUpstream) lastChat() string {
	u.mu.Lock()
	defer u.mu.Unlock()
	if len(u.chats) == 0 {
		return ""
	}
	return u.chats[len(u.chats)-1]
}

func (u *r7RotUpstream) listAuths() []string {
	u.mu.Lock()
	defer u.mu.Unlock()
	return append([]string(nil), u.lists...)
}

// r7RotServer: operator provider "openai" (rotation on, drain as given) on
// upstream, an internal key that uses it, and org r7RotOrg whose own "openai"
// provider (its own key) a BYOK key uses.
func r7RotServer(t *testing.T, upstream string, drain time.Duration, mutate func(*config.Config)) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Admin.Token = r7RotAdmin
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "internal", Key: r7RotInternal, Owner: "t", KeyType: "internal"},
		{Name: "org-byok", Key: r7RotOrgKey, Owner: "t", KeyType: "byok", Metadata: map[string]string{"org_id": r7RotOrg}},
	}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL: upstream, APIKey: r7RotOld, APIFormat: "openai", Models: []string{"gpt-4o"},
		Rotation: config.RotationConfig{Enabled: true, DrainPeriod: drain},
	}
	if mutate != nil {
		mutate(cfg)
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	tenants.Set(r7RotOrg, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {APIKey: r7RotOrgOwn, Enabled: true, Models: []string{"gpt-4o"}},
	}})
	keyStore := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true)), keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	return srv
}

func r7RotDo(srv *Server, method, path, body, key string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

func r7RotChat(t *testing.T, srv *Server, key, content string) {
	t.Helper()
	w := r7RotDo(srv, http.MethodPost, "/v1/chat/completions", `{"model":"gpt-4o","messages":[{"role":"user","content":"`+content+`"}]}`, key)
	if w.Code != http.StatusOK {
		t.Fatalf("chat as %s -> %d %s", key, w.Code, w.Body.String())
	}
}

type r7RotState struct {
	Status        string `json:"status"`
	Primary       string `json:"primary"`
	DrainPeriodMS int64  `json:"drain_period_ms"`
	ValidatedBy   string `json:"validated_by"`
	Source        string `json:"source"`
	AppliesUntil  string `json:"applies_until"`
	Note          string `json:"note"`
	Error         string `json:"error"`
}

func r7RotDecode(t *testing.T, w *httptest.ResponseRecorder) r7RotState {
	t.Helper()
	var s r7RotState
	if err := json.Unmarshal(w.Body.Bytes(), &s); err != nil {
		t.Fatalf("rotation answer %d %q: %v", w.Code, w.Body.String(), err)
	}
	return s
}

// r7RotPromote starts and promotes a rotation to key.
func r7RotPromote(t *testing.T, srv *Server, key string) *httptest.ResponseRecorder {
	t.Helper()
	if w := r7RotDo(srv, http.MethodPost, "/-/admin/providers/openai/rotate", `{"new_key":"`+key+`"}`, r7RotAdmin); w.Code != http.StatusOK {
		t.Fatalf("rotate -> %d %s", w.Code, w.Body.String())
	}
	return r7RotDo(srv, http.MethodPost, "/-/admin/providers/openai/rotate/promote", "", r7RotAdmin)
}

// TestR7RotationReachesUpstream: before the rotation the upstream gets the
// old key; from the promote on, every new call sends the new key, right
// after the promote and once the drain period is over; the promote validated
// the new key with one list-models call that sent the new key; the answer and
// the status state that the rotation lives until restart.
func TestR7RotationReachesUpstream(t *testing.T) {
	up := startR7RotUpstream(t)
	srv := r7RotServer(t, up.URL, 20*time.Millisecond, nil)

	r7RotChat(t, srv, r7RotInternal, "before")
	if got := up.lastChat(); got != "Bearer "+r7RotOld {
		t.Fatalf("before rotation upstream got %q", got)
	}

	w := r7RotPromote(t, srv, r7RotNew)
	st := r7RotDecode(t, w)
	if w.Code != http.StatusOK || st.Status != "draining" || st.ValidatedBy != "list models" || st.Source != "admin_api" {
		t.Fatalf("promote -> %d %s; want 200 draining, validated_by list models, source admin_api", w.Code, w.Body.String())
	}
	if st.Note != RotationRestartNote || st.AppliesUntil != "restart" {
		t.Errorf("promote answer note %q applies_until %q; want %q, restart", st.Note, st.AppliesUntil, RotationRestartNote)
	}
	if lists := up.listAuths(); len(lists) != 1 || lists[0] != "Bearer "+r7RotNew {
		t.Errorf("validation calls %v; want exactly one GET /v1/models with the new key", lists)
	}

	r7RotChat(t, srv, r7RotInternal, "right after promote")
	if got := up.lastChat(); got != "Bearer "+r7RotNew {
		t.Errorf("right after promote upstream got %q, want the new key", got)
	}

	time.Sleep(150 * time.Millisecond) // drain_period is 20 ms
	w = r7RotDo(srv, http.MethodGet, "/-/admin/providers/openai/rotation", "", r7RotAdmin)
	st = r7RotDecode(t, w)
	if st.Status != "idle" || st.Note != RotationRestartNote {
		t.Errorf("status 150 ms after promote (drain 20 ms) = %s", w.Body.String())
	}
	r7RotChat(t, srv, r7RotInternal, "after drain")
	if got := up.lastChat(); got != "Bearer "+r7RotNew {
		t.Errorf("after drain_period upstream got %q, want the new key", got)
	}
}

// TestR7RotationValidationRefusesBadKey: a new key the provider refuses is
// not promoted (422), the rotation stays pending, and calls keep the old key.
func TestR7RotationValidationRefusesBadKey(t *testing.T) {
	up := startR7RotUpstream(t)
	srv := r7RotServer(t, up.URL, 20*time.Millisecond, nil)

	w := r7RotPromote(t, srv, r7RotBad)
	if w.Code != http.StatusUnprocessableEntity || strings.Contains(w.Body.String(), r7RotBad) {
		t.Fatalf("promote of a key the provider refuses -> %d %s; want 422 without the key", w.Code, w.Body.String())
	}
	st := r7RotDecode(t, r7RotDo(srv, http.MethodGet, "/-/admin/providers/openai/rotation", "", r7RotAdmin))
	if st.Status != "pending" {
		t.Errorf("after a refused promote status = %q, want pending", st.Status)
	}
	r7RotChat(t, srv, r7RotInternal, "after refused promote")
	if got := up.lastChat(); got != "Bearer "+r7RotOld {
		t.Errorf("after a refused promote upstream got %q, want the old key", got)
	}
	// The pending key can be rolled back; the old key stays.
	if w := r7RotDo(srv, http.MethodPost, "/-/admin/providers/openai/rotate/rollback", "", r7RotAdmin); w.Code != http.StatusOK {
		t.Errorf("rollback of the pending key -> %d %s", w.Code, w.Body.String())
	}
}

// TestR7RotationDrainPeriodPerProvider: the provider's own drain_period
// governs its drain (the manager was built with the 30 s default before the
// provider's value was read).
func TestR7RotationDrainPeriodPerProvider(t *testing.T) {
	up := startR7RotUpstream(t)
	srv := r7RotServer(t, up.URL, 400*time.Millisecond, nil)
	w := r7RotPromote(t, srv, r7RotNew)
	if st := r7RotDecode(t, w); st.DrainPeriodMS != 400 {
		t.Errorf("drain_period_ms = %d, want 400 (providers.openai.rotation.drain_period)", st.DrainPeriodMS)
	}
	time.Sleep(100 * time.Millisecond)
	if st := r7RotDecode(t, r7RotDo(srv, http.MethodGet, "/-/admin/providers/openai/rotation", "", r7RotAdmin)); st.Status != "draining" {
		t.Errorf("100 ms into a 400 ms drain status = %q, want draining", st.Status)
	}
	time.Sleep(600 * time.Millisecond)
	if st := r7RotDecode(t, r7RotDo(srv, http.MethodGet, "/-/admin/providers/openai/rotation", "", r7RotAdmin)); st.Status != "idle" {
		t.Errorf("700 ms after the promote of a 400 ms drain status = %q, want idle", st.Status)
	}
}

// TestR7RotationInFlightFinishesOnOldKey: a call in flight at the promote
// finishes (on the instance it holds, which sent the old key) while new calls
// send the new key; the old instance is closed only after the drain.
func TestR7RotationInFlightFinishesOnOldKey(t *testing.T) {
	up := startR7RotUpstream(t)
	srv := r7RotServer(t, up.URL, 50*time.Millisecond, nil)

	done := make(chan *httptest.ResponseRecorder, 1)
	go func() {
		done <- r7RotDo(srv, http.MethodPost, "/v1/chat/completions", `{"model":"gpt-4o","messages":[{"role":"user","content":"hold"}]}`, r7RotInternal)
	}()
	<-up.held
	if w := r7RotPromote(t, srv, r7RotNew); w.Code != http.StatusOK {
		t.Fatalf("promote -> %d %s", w.Code, w.Body.String())
	}
	r7RotChat(t, srv, r7RotInternal, "new call during drain")
	if got := up.lastChat(); got != "Bearer "+r7RotNew {
		t.Errorf("a new call during the drain sent %q, want the new key", got)
	}
	time.Sleep(150 * time.Millisecond) // past the drain: the old instance is closed
	close(up.release)
	w := <-done
	if w.Code != http.StatusOK {
		t.Errorf("the call in flight at the promote -> %d %s; want 200", w.Code, w.Body.String())
	}
	up.mu.Lock()
	first := up.chats[0]
	up.mu.Unlock()
	if first != "Bearer "+r7RotOld {
		t.Errorf("the in-flight call was sent with %q, want the old key", first)
	}
}

// TestR7RotationOrgProviderKeepsOrgKey: an org's own provider instance never
// carried the operator key, so a rotation of the operator key leaves it
// sending the org's key.
func TestR7RotationOrgProviderKeepsOrgKey(t *testing.T) {
	up := startR7RotUpstream(t)
	srv := r7RotServer(t, up.URL, 20*time.Millisecond, nil)
	r7RotChat(t, srv, r7RotOrgKey, "org before")
	if got := up.lastChat(); got != "Bearer "+r7RotOrgOwn {
		t.Fatalf("org call before rotation sent %q, want the org's key", got)
	}
	if w := r7RotPromote(t, srv, r7RotNew); w.Code != http.StatusOK {
		t.Fatalf("promote -> %d %s", w.Code, w.Body.String())
	}
	r7RotChat(t, srv, r7RotOrgKey, "org after")
	if got := up.lastChat(); got != "Bearer "+r7RotOrgOwn {
		t.Errorf("org call after rotating the operator key sent %q, want the org's key", got)
	}
	r7RotChat(t, srv, r7RotInternal, "internal after")
	if got := up.lastChat(); got != "Bearer "+r7RotNew {
		t.Errorf("internal call after the rotation sent %q, want the new operator key", got)
	}
}

// TestR7RotationRollbackRules: a pending rotation rolls back to the old key;
// a promoted one answers 409 (the new key already serves every call).
func TestR7RotationRollbackRules(t *testing.T) {
	up := startR7RotUpstream(t)
	srv := r7RotServer(t, up.URL, time.Minute, nil)
	if w := r7RotPromote(t, srv, r7RotNew); w.Code != http.StatusOK {
		t.Fatalf("promote -> %d %s", w.Code, w.Body.String())
	}
	w := r7RotDo(srv, http.MethodPost, "/-/admin/providers/openai/rotate/rollback", "", r7RotAdmin)
	if w.Code != http.StatusConflict {
		t.Errorf("rollback while draining -> %d %s; want 409", w.Code, w.Body.String())
	}
	r7RotChat(t, srv, r7RotInternal, "after refused rollback")
	if got := up.lastChat(); got != "Bearer "+r7RotNew {
		t.Errorf("after a refused rollback upstream got %q, want the promoted key", got)
	}
	if w := r7RotDo(srv, http.MethodPost, "/-/admin/providers/nope/rotate", `{"new_key":"k-unknown-provider"}`, r7RotAdmin); w.Code != http.StatusNotFound {
		t.Errorf("rotate of a provider without rotation -> %d %s; want 404", w.Code, w.Body.String())
	}
}

// TestR7RotationNeverLogsKey: no log line of a rotation (start, refused
// promote, promote, drain, rollback) carries a key or its first 8 characters.
func TestR7RotationNeverLogsKey(t *testing.T) {
	var buf bytes.Buffer
	var mu sync.Mutex
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&lockedWriter{w: &buf, mu: &mu}, &slog.HandlerOptions{Level: slog.LevelDebug})))
	defer slog.SetDefault(prev)

	up := startR7RotUpstream(t)
	srv := r7RotServer(t, up.URL, 20*time.Millisecond, nil)
	r7RotPromote(t, srv, r7RotBad)
	r7RotDo(srv, http.MethodPost, "/-/admin/providers/openai/rotate/rollback", "", r7RotAdmin)
	if w := r7RotPromote(t, srv, r7RotNew); w.Code != http.StatusOK {
		t.Fatalf("promote -> %d %s", w.Code, w.Body.String())
	}
	time.Sleep(100 * time.Millisecond)

	mu.Lock()
	logs := buf.String()
	mu.Unlock()
	if !strings.Contains(logs, "provider key rotation promoted") {
		t.Fatalf("no audit line for the promote in the log:\n%s", logs)
	}
	for _, key := range []string{r7RotOld, r7RotNew, r7RotBad} {
		if strings.Contains(logs, key[:8]) {
			t.Errorf("the log carries %q (part of a key):\n%s", key[:8], logs)
		}
	}
}

type lockedWriter struct {
	w  io.Writer
	mu *sync.Mutex
}

func (l *lockedWriter) Write(p []byte) (int, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.w.Write(p)
}

// TestR7RotationAnthropicValidatesWithOneTokenChat: a provider whose format
// lists no models upstream (anthropic) validates the new key with a one-token
// chat completion to its first model; a refused key is not promoted.
func TestR7RotationAnthropicValidatesWithOneTokenChat(t *testing.T) {
	var mu sync.Mutex
	var seen []string
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if r.Method == http.MethodPost { // not the registry's connectivity check
			mu.Lock()
			seen = append(seen, r.Header.Get("x-api-key")+" "+string(body))
			mu.Unlock()
		}
		w.Header().Set("Content-Type", "application/json")
		if r.Header.Get("x-api-key") == r7RotBad {
			w.WriteHeader(http.StatusUnauthorized)
			io.WriteString(w, `{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}`)
			return
		}
		io.WriteString(w, `{"id":"msg_1","type":"message","role":"assistant","model":"claude-r7","content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1}}`)
	}))
	defer up.Close()
	srv := r7RotServer(t, "http://127.0.0.1:9", 20*time.Millisecond, func(c *config.Config) {
		c.Providers["anthropic"] = config.ProviderConfig{
			BaseURL: up.URL, APIKey: r7RotOld, APIFormat: "anthropic", Models: []string{"claude-r7"},
			Rotation: config.RotationConfig{Enabled: true, DrainPeriod: 20 * time.Millisecond},
		}
	})
	if w := r7RotDo(srv, http.MethodPost, "/-/admin/providers/anthropic/rotate", `{"new_key":"`+r7RotBad+`"}`, r7RotAdmin); w.Code != http.StatusOK {
		t.Fatalf("rotate -> %d %s", w.Code, w.Body.String())
	}
	if w := r7RotDo(srv, http.MethodPost, "/-/admin/providers/anthropic/rotate/promote", "", r7RotAdmin); w.Code != http.StatusUnprocessableEntity {
		t.Errorf("promote of a refused anthropic key -> %d %s; want 422", w.Code, w.Body.String())
	}
	r7RotDo(srv, http.MethodPost, "/-/admin/providers/anthropic/rotate/rollback", "", r7RotAdmin)
	r7RotDo(srv, http.MethodPost, "/-/admin/providers/anthropic/rotate", `{"new_key":"`+r7RotNew+`"}`, r7RotAdmin)
	w := r7RotDo(srv, http.MethodPost, "/-/admin/providers/anthropic/rotate/promote", "", r7RotAdmin)
	st := r7RotDecode(t, w)
	if w.Code != http.StatusOK || st.ValidatedBy != "chat completion, max_tokens 1, model claude-r7" {
		t.Errorf("promote -> %d %s; want 200 validated by a one-token chat to claude-r7", w.Code, w.Body.String())
	}
	mu.Lock()
	defer mu.Unlock()
	if len(seen) != 2 || !strings.HasPrefix(seen[1], r7RotNew+" ") || !strings.Contains(seen[1], `"max_tokens":1`) {
		t.Errorf("validation calls = %q; want the refused key's then one max_tokens 1 call with the new key", seen)
	}
}

// TestR7RotationBroadcastReachesReplicas: two replicas on one Redis (the test
// Redis, TEST_REDIS_ADDR/TEST_REDIS_DB); a promote on one makes the other send
// the new key too, without a promote there.
func TestR7RotationBroadcastReachesReplicas(t *testing.T) {
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		addr = "localhost:6379"
	}
	db, _ := strconv.Atoi(os.Getenv("TEST_REDIS_DB"))
	client, err := redisstate.NewClient(redisstate.Config{Address: addr, DB: db, Timeout: time.Second})
	if err != nil {
		t.Skipf("skipping: Redis not available at %s: %v", addr, err)
	}
	defer client.Close()
	// A channel of this test's own: pub/sub stores nothing, so nothing to
	// clean up.
	prefix := fmt.Sprintf("r7ops-%d:", time.Now().UnixNano())

	upA, upB := startR7RotUpstream(t), startR7RotUpstream(t)
	srvA := r7RotServer(t, upA.URL, 20*time.Millisecond, nil)
	srvB := r7RotServer(t, upB.URL, 20*time.Millisecond, nil)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	for _, srv := range []*Server{srvA, srvB} {
		b, err := rotation.NewBroadcaster(client.Redis(), r7RotAdmin, prefix)
		if err != nil {
			t.Fatal(err)
		}
		srv.SetRotationBroadcaster(b)
		ready := make(chan error, 1)
		go b.SubscribeReady(ctx, srv.ApplyRemoteRotation, ready)
		if err := <-ready; err != nil {
			t.Skipf("skipping: Redis subscribe failed: %v", err)
		}
	}

	r7RotChat(t, srvB, r7RotInternal, "replica before")
	if got := upB.lastChat(); got != "Bearer "+r7RotOld {
		t.Fatalf("replica B before the rotation sent %q", got)
	}
	if w := r7RotPromote(t, srvA, r7RotNew); w.Code != http.StatusOK {
		t.Fatalf("promote on replica A -> %d %s", w.Code, w.Body.String())
	}
	deadline := time.Now().Add(5 * time.Second)
	for {
		st := r7RotDecode(t, r7RotDo(srvB, http.MethodGet, "/-/admin/providers/openai/rotation", "", r7RotAdmin))
		if st.Source == "replica" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("replica B never applied replica A's promote: %+v", st)
		}
		time.Sleep(10 * time.Millisecond)
	}
	r7RotChat(t, srvB, r7RotInternal, "replica after")
	if got := upB.lastChat(); got != "Bearer "+r7RotNew {
		t.Errorf("replica B after replica A's promote sent %q, want the new key", got)
	}
	if lists := upB.listAuths(); len(lists) != 0 {
		t.Errorf("replica B validated the broadcast key again: %v", lists)
	}
}

// TestR7RotationValidationErrorCarriesNoKey: an upstream may repeat the key
// it refused (OpenAI's 401 shows its first and last characters; others echo
// it whole, in a JSON message or a raw body). The refused promote's log line
// and its 422 answer carry the upstream's status, type and code, never its
// message, so no part of the key reaches either.
func TestR7RotationValidationErrorCarriesNoKey(t *testing.T) {
	masked := r7RotBad[:8] + strings.Repeat("*", len(r7RotBad)-12) + r7RotBad[len(r7RotBad)-4:]
	for _, tc := range []struct{ name, body string }{
		{"json message, masked like openai", `{"type":"error","error":{"type":"authentication_error","message":"Incorrect API key provided: ` + masked + `"}}`},
		{"json message, whole key", `{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key ` + r7RotBad + `"}}`},
		{"raw body, whole key", `key ` + r7RotBad + ` refused`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var buf bytes.Buffer
			var mu sync.Mutex
			prev := slog.Default()
			slog.SetDefault(slog.New(slog.NewTextHandler(&lockedWriter{w: &buf, mu: &mu}, &slog.HandlerOptions{Level: slog.LevelDebug})))
			defer slog.SetDefault(prev)

			up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				io.Copy(io.Discard, r.Body)
				if r.Header.Get("x-api-key") == r7RotBad {
					w.WriteHeader(http.StatusUnauthorized)
					io.WriteString(w, tc.body)
					return
				}
				w.Header().Set("Content-Type", "application/json")
				io.WriteString(w, `{"id":"msg_1","type":"message","role":"assistant","model":"claude-r7","content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1}}`)
			}))
			defer up.Close()
			srv := r7RotServer(t, "http://127.0.0.1:9", 20*time.Millisecond, func(c *config.Config) {
				c.Providers["anthropic"] = config.ProviderConfig{
					BaseURL: up.URL, APIKey: r7RotOld, APIFormat: "anthropic", Models: []string{"claude-r7"},
					Rotation: config.RotationConfig{Enabled: true, DrainPeriod: 20 * time.Millisecond},
				}
			})
			if w := r7RotDo(srv, http.MethodPost, "/-/admin/providers/anthropic/rotate", `{"new_key":"`+r7RotBad+`"}`, r7RotAdmin); w.Code != http.StatusOK {
				t.Fatalf("rotate -> %d %s", w.Code, w.Body.String())
			}
			w := r7RotDo(srv, http.MethodPost, "/-/admin/providers/anthropic/rotate/promote", "", r7RotAdmin)
			if w.Code != http.StatusUnprocessableEntity {
				t.Fatalf("promote of a refused key -> %d %s; want 422", w.Code, w.Body.String())
			}
			if !strings.Contains(w.Body.String(), "401") {
				t.Errorf("422 answer %s does not name the upstream's 401", w.Body.String())
			}
			mu.Lock()
			logs := buf.String()
			mu.Unlock()
			if !strings.Contains(logs, "new key failed validation") {
				t.Fatalf("no log line for the refused promote:\n%s", logs)
			}
			for where, text := range map[string]string{"log": logs, "422 answer": w.Body.String()} {
				if strings.Contains(text, r7RotBad[:8]) || strings.Contains(text, r7RotBad[len(r7RotBad)-12:]) {
					t.Errorf("the %s carries part of the refused key:\n%s", where, text)
				}
			}
		})
	}
}

// TestR7RotationValidatesWithChatWhenNoModelsList: an OpenAI-compatible
// provider without a models endpoint (GET /v1/models answers 404) cannot
// tell a good key from a bad one by listing models, so the promote validates
// the key with a one-token chat completion to its first model instead; a key
// that chat refuses is still not promoted.
func TestR7RotationValidatesWithChatWhenNoModelsList(t *testing.T) {
	var mu sync.Mutex
	var chats []string
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		w.Header().Set("Content-Type", "application/json")
		if r.Method == http.MethodGet {
			w.WriteHeader(http.StatusNotFound)
			io.WriteString(w, `{"error":"not found"}`)
			return
		}
		mu.Lock()
		chats = append(chats, r.Header.Get("Authorization")+" "+string(body))
		mu.Unlock()
		if r.Header.Get("Authorization") == "Bearer "+r7RotBad {
			w.WriteHeader(http.StatusUnauthorized)
			io.WriteString(w, `{"error":{"message":"invalid key","type":"invalid_request_error","code":"invalid_api_key"}}`)
			return
		}
		io.WriteString(w, `{"id":"x","object":"chat.completion","created":1,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
	}))
	defer up.Close()
	srv := r7RotServer(t, up.URL, 20*time.Millisecond, nil)

	if w := r7RotPromote(t, srv, r7RotBad); w.Code != http.StatusUnprocessableEntity {
		t.Errorf("promote of a key chat refuses -> %d %s; want 422", w.Code, w.Body.String())
	}
	r7RotDo(srv, http.MethodPost, "/-/admin/providers/openai/rotate/rollback", "", r7RotAdmin)
	w := r7RotPromote(t, srv, r7RotNew)
	st := r7RotDecode(t, w)
	if w.Code != http.StatusOK || st.ValidatedBy != "chat completion, max_tokens 1, model gpt-4o (no models list)" {
		t.Fatalf("promote with no models endpoint -> %d %s; want 200 validated by a one-token chat to gpt-4o", w.Code, w.Body.String())
	}
	mu.Lock()
	defer mu.Unlock()
	if n := len(chats); n != 2 || !strings.HasPrefix(chats[1], "Bearer "+r7RotNew+" ") || !strings.Contains(chats[1], `"max_tokens":1`) {
		t.Errorf("validation chats = %q; want the refused key's, then one max_tokens 1 call with the new key", chats)
	}
}
