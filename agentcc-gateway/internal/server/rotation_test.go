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
// .../rotate/promote, changes the key the gateway sends upstream.

const (
	rotAdmin    = "rotation-admin-token-0123456789abcdef0123456789abcdef01"
	rotInternal = "sk-rotation-internal"
	rotOrgKey   = "sk-rotation-org-byok"
	rotOrg      = "org-rotation"
	rotOld      = "sk-OLD-aaaa1111-operator"
	rotNew      = "sk-NEW-bbbb2222-operator"
	rotBad      = "sk-BAD-cccc3333-operator"
	rotOrgOwn   = "sk-ORG-dddd4444-own"
)

// rotUpstream is an OpenAI-format upstream: GET /v1/models answers 401 for
// rotBad and 200 otherwise; every chat request's Authorization is recorded.
// A chat request whose last message is "hold" waits for release.
type rotUpstream struct {
	*httptest.Server
	mu      sync.Mutex
	chats   []string
	lists   []string
	held    chan struct{}
	release chan struct{}
}

func startRotUpstream(t *testing.T) *rotUpstream {
	t.Helper()
	u := &rotUpstream{held: make(chan struct{}, 1), release: make(chan struct{})}
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
			if authz == "Bearer "+rotBad {
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

func (u *rotUpstream) lastChat() string {
	u.mu.Lock()
	defer u.mu.Unlock()
	if len(u.chats) == 0 {
		return ""
	}
	return u.chats[len(u.chats)-1]
}

func (u *rotUpstream) listAuths() []string {
	u.mu.Lock()
	defer u.mu.Unlock()
	return append([]string(nil), u.lists...)
}

// rotServer: operator provider "openai" (rotation on, drain as given) on
// upstream, an internal key that uses it, and org rotOrg whose own "openai"
// provider (its own key) a BYOK key uses.
func rotServer(t *testing.T, upstream string, drain time.Duration, mutate func(*config.Config)) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Admin.Token = rotAdmin
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "internal", Key: rotInternal, Owner: "t", KeyType: "internal"},
		{Name: "org-byok", Key: rotOrgKey, Owner: "t", KeyType: "byok", Metadata: map[string]string{"org_id": rotOrg}},
	}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL: upstream, APIKey: rotOld, APIFormat: "openai", Models: []string{"gpt-4o"},
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
	tenants.Set(rotOrg, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {APIKey: rotOrgOwn, Enabled: true, Models: []string{"gpt-4o"}},
	}})
	keyStore := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true)), keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	return srv
}

func rotDo(srv *Server, method, path, body, key string) *httptest.ResponseRecorder {
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

func rotChat(t *testing.T, srv *Server, key, content string) {
	t.Helper()
	w := rotDo(srv, http.MethodPost, "/v1/chat/completions", `{"model":"gpt-4o","messages":[{"role":"user","content":"`+content+`"}]}`, key)
	if w.Code != http.StatusOK {
		t.Fatalf("chat as %s -> %d %s", key, w.Code, w.Body.String())
	}
}

type rotState struct {
	Status        string `json:"status"`
	Primary       string `json:"primary"`
	DrainPeriodMS int64  `json:"drain_period_ms"`
	ValidatedBy   string `json:"validated_by"`
	Source        string `json:"source"`
	AppliesUntil  string `json:"applies_until"`
	Note          string `json:"note"`
	Error         string `json:"error"`
}

func rotDecode(t *testing.T, w *httptest.ResponseRecorder) rotState {
	t.Helper()
	var s rotState
	if err := json.Unmarshal(w.Body.Bytes(), &s); err != nil {
		t.Fatalf("rotation answer %d %q: %v", w.Code, w.Body.String(), err)
	}
	return s
}

// rotPromote starts and promotes a rotation to key.
func rotPromote(t *testing.T, srv *Server, key string) *httptest.ResponseRecorder {
	t.Helper()
	if w := rotDo(srv, http.MethodPost, "/-/admin/providers/openai/rotate", `{"new_key":"`+key+`"}`, rotAdmin); w.Code != http.StatusOK {
		t.Fatalf("rotate -> %d %s", w.Code, w.Body.String())
	}
	return rotDo(srv, http.MethodPost, "/-/admin/providers/openai/rotate/promote", "", rotAdmin)
}

// TestRotationReachesUpstream: before the rotation the upstream gets the
// old key; from the promote on, every new call sends the new key, right
// after the promote and once the drain period is over; the promote validated
// the new key with one list-models call that sent the new key; the answer and
// the status state that the rotation lives until restart.
func TestRotationReachesUpstream(t *testing.T) {
	up := startRotUpstream(t)
	srv := rotServer(t, up.URL, 20*time.Millisecond, nil)

	rotChat(t, srv, rotInternal, "before")
	if got := up.lastChat(); got != "Bearer "+rotOld {
		t.Fatalf("before rotation upstream got %q", got)
	}

	w := rotPromote(t, srv, rotNew)
	st := rotDecode(t, w)
	if w.Code != http.StatusOK || st.Status != "draining" || st.ValidatedBy != "list models" || st.Source != "admin_api" {
		t.Fatalf("promote -> %d %s; want 200 draining, validated_by list models, source admin_api", w.Code, w.Body.String())
	}
	if st.Note != RotationRestartNote || st.AppliesUntil != "restart" {
		t.Errorf("promote answer note %q applies_until %q; want %q, restart", st.Note, st.AppliesUntil, RotationRestartNote)
	}
	if lists := up.listAuths(); len(lists) != 1 || lists[0] != "Bearer "+rotNew {
		t.Errorf("validation calls %v; want exactly one GET /v1/models with the new key", lists)
	}

	rotChat(t, srv, rotInternal, "right after promote")
	if got := up.lastChat(); got != "Bearer "+rotNew {
		t.Errorf("right after promote upstream got %q, want the new key", got)
	}

	time.Sleep(150 * time.Millisecond) // drain_period is 20 ms
	w = rotDo(srv, http.MethodGet, "/-/admin/providers/openai/rotation", "", rotAdmin)
	st = rotDecode(t, w)
	if st.Status != "idle" || st.Note != RotationRestartNote {
		t.Errorf("status 150 ms after promote (drain 20 ms) = %s", w.Body.String())
	}
	rotChat(t, srv, rotInternal, "after drain")
	if got := up.lastChat(); got != "Bearer "+rotNew {
		t.Errorf("after drain_period upstream got %q, want the new key", got)
	}
}

// TestRotationValidationRefusesBadKey: a new key the provider refuses is
// not promoted (422), the rotation stays pending, and calls keep the old key.
func TestRotationValidationRefusesBadKey(t *testing.T) {
	up := startRotUpstream(t)
	srv := rotServer(t, up.URL, 20*time.Millisecond, nil)

	w := rotPromote(t, srv, rotBad)
	if w.Code != http.StatusUnprocessableEntity || strings.Contains(w.Body.String(), rotBad) {
		t.Fatalf("promote of a key the provider refuses -> %d %s; want 422 without the key", w.Code, w.Body.String())
	}
	st := rotDecode(t, rotDo(srv, http.MethodGet, "/-/admin/providers/openai/rotation", "", rotAdmin))
	if st.Status != "pending" {
		t.Errorf("after a refused promote status = %q, want pending", st.Status)
	}
	rotChat(t, srv, rotInternal, "after refused promote")
	if got := up.lastChat(); got != "Bearer "+rotOld {
		t.Errorf("after a refused promote upstream got %q, want the old key", got)
	}
	// The pending key can be rolled back; the old key stays.
	if w := rotDo(srv, http.MethodPost, "/-/admin/providers/openai/rotate/rollback", "", rotAdmin); w.Code != http.StatusOK {
		t.Errorf("rollback of the pending key -> %d %s", w.Code, w.Body.String())
	}
}

// TestRotationDrainPeriodPerProvider: the provider's own drain_period
// governs its drain.
func TestRotationDrainPeriodPerProvider(t *testing.T) {
	up := startRotUpstream(t)
	srv := rotServer(t, up.URL, 400*time.Millisecond, nil)
	w := rotPromote(t, srv, rotNew)
	if st := rotDecode(t, w); st.DrainPeriodMS != 400 {
		t.Errorf("drain_period_ms = %d, want 400 (providers.openai.rotation.drain_period)", st.DrainPeriodMS)
	}
	time.Sleep(100 * time.Millisecond)
	if st := rotDecode(t, rotDo(srv, http.MethodGet, "/-/admin/providers/openai/rotation", "", rotAdmin)); st.Status != "draining" {
		t.Errorf("100 ms into a 400 ms drain status = %q, want draining", st.Status)
	}
	time.Sleep(600 * time.Millisecond)
	if st := rotDecode(t, rotDo(srv, http.MethodGet, "/-/admin/providers/openai/rotation", "", rotAdmin)); st.Status != "idle" {
		t.Errorf("700 ms after the promote of a 400 ms drain status = %q, want idle", st.Status)
	}
}

// TestRotationInFlightFinishesOnOldKey: a call in flight at the promote
// finishes (on the instance it holds, which sent the old key) while new calls
// send the new key; the old instance is closed only after the drain.
func TestRotationInFlightFinishesOnOldKey(t *testing.T) {
	up := startRotUpstream(t)
	srv := rotServer(t, up.URL, 50*time.Millisecond, nil)

	done := make(chan *httptest.ResponseRecorder, 1)
	go func() {
		done <- rotDo(srv, http.MethodPost, "/v1/chat/completions", `{"model":"gpt-4o","messages":[{"role":"user","content":"hold"}]}`, rotInternal)
	}()
	<-up.held
	if w := rotPromote(t, srv, rotNew); w.Code != http.StatusOK {
		t.Fatalf("promote -> %d %s", w.Code, w.Body.String())
	}
	rotChat(t, srv, rotInternal, "new call during drain")
	if got := up.lastChat(); got != "Bearer "+rotNew {
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
	if first != "Bearer "+rotOld {
		t.Errorf("the in-flight call was sent with %q, want the old key", first)
	}
}

// TestRotationOrgProviderKeepsOrgKey: an org's own provider instance never
// carries the operator key, so a rotation of the operator key leaves it
// sending the org's key.
func TestRotationOrgProviderKeepsOrgKey(t *testing.T) {
	up := startRotUpstream(t)
	srv := rotServer(t, up.URL, 20*time.Millisecond, nil)
	rotChat(t, srv, rotOrgKey, "org before")
	if got := up.lastChat(); got != "Bearer "+rotOrgOwn {
		t.Fatalf("org call before rotation sent %q, want the org's key", got)
	}
	if w := rotPromote(t, srv, rotNew); w.Code != http.StatusOK {
		t.Fatalf("promote -> %d %s", w.Code, w.Body.String())
	}
	rotChat(t, srv, rotOrgKey, "org after")
	if got := up.lastChat(); got != "Bearer "+rotOrgOwn {
		t.Errorf("org call after rotating the operator key sent %q, want the org's key", got)
	}
	rotChat(t, srv, rotInternal, "internal after")
	if got := up.lastChat(); got != "Bearer "+rotNew {
		t.Errorf("internal call after the rotation sent %q, want the new operator key", got)
	}
}

// TestRotationRollbackRules: a pending rotation rolls back to the old key;
// a promoted one answers 409 (the new key already serves every call).
func TestRotationRollbackRules(t *testing.T) {
	up := startRotUpstream(t)
	srv := rotServer(t, up.URL, time.Minute, nil)
	if w := rotPromote(t, srv, rotNew); w.Code != http.StatusOK {
		t.Fatalf("promote -> %d %s", w.Code, w.Body.String())
	}
	w := rotDo(srv, http.MethodPost, "/-/admin/providers/openai/rotate/rollback", "", rotAdmin)
	if w.Code != http.StatusConflict {
		t.Errorf("rollback while draining -> %d %s; want 409", w.Code, w.Body.String())
	}
	rotChat(t, srv, rotInternal, "after refused rollback")
	if got := up.lastChat(); got != "Bearer "+rotNew {
		t.Errorf("after a refused rollback upstream got %q, want the promoted key", got)
	}
	if w := rotDo(srv, http.MethodPost, "/-/admin/providers/nope/rotate", `{"new_key":"k-unknown-provider"}`, rotAdmin); w.Code != http.StatusNotFound {
		t.Errorf("rotate of a provider without rotation -> %d %s; want 404", w.Code, w.Body.String())
	}
}

// TestRotationNeverLogsKey: no log line of a rotation (start, refused
// promote, promote, drain, rollback) carries a key or its first 8 characters.
func TestRotationNeverLogsKey(t *testing.T) {
	var buf bytes.Buffer
	var mu sync.Mutex
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&lockedWriter{w: &buf, mu: &mu}, &slog.HandlerOptions{Level: slog.LevelDebug})))
	defer slog.SetDefault(prev)

	up := startRotUpstream(t)
	srv := rotServer(t, up.URL, 20*time.Millisecond, nil)
	rotPromote(t, srv, rotBad)
	rotDo(srv, http.MethodPost, "/-/admin/providers/openai/rotate/rollback", "", rotAdmin)
	if w := rotPromote(t, srv, rotNew); w.Code != http.StatusOK {
		t.Fatalf("promote -> %d %s", w.Code, w.Body.String())
	}
	time.Sleep(100 * time.Millisecond)

	mu.Lock()
	logs := buf.String()
	mu.Unlock()
	if !strings.Contains(logs, "provider key rotation promoted") {
		t.Fatalf("no audit line for the promote in the log:\n%s", logs)
	}
	for _, key := range []string{rotOld, rotNew, rotBad} {
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

// TestRotationAnthropicValidatesWithOneTokenChat: a provider whose format
// lists no models upstream (anthropic) validates the new key with a one-token
// chat completion to its first model; a refused key is not promoted.
func TestRotationAnthropicValidatesWithOneTokenChat(t *testing.T) {
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
		if r.Header.Get("x-api-key") == rotBad {
			w.WriteHeader(http.StatusUnauthorized)
			io.WriteString(w, `{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}`)
			return
		}
		io.WriteString(w, `{"id":"msg_1","type":"message","role":"assistant","model":"claude-rot","content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1}}`)
	}))
	defer up.Close()
	srv := rotServer(t, "http://127.0.0.1:9", 20*time.Millisecond, func(c *config.Config) {
		c.Providers["anthropic"] = config.ProviderConfig{
			BaseURL: up.URL, APIKey: rotOld, APIFormat: "anthropic", Models: []string{"claude-rot"},
			Rotation: config.RotationConfig{Enabled: true, DrainPeriod: 20 * time.Millisecond},
		}
	})
	if w := rotDo(srv, http.MethodPost, "/-/admin/providers/anthropic/rotate", `{"new_key":"`+rotBad+`"}`, rotAdmin); w.Code != http.StatusOK {
		t.Fatalf("rotate -> %d %s", w.Code, w.Body.String())
	}
	if w := rotDo(srv, http.MethodPost, "/-/admin/providers/anthropic/rotate/promote", "", rotAdmin); w.Code != http.StatusUnprocessableEntity {
		t.Errorf("promote of a refused anthropic key -> %d %s; want 422", w.Code, w.Body.String())
	}
	rotDo(srv, http.MethodPost, "/-/admin/providers/anthropic/rotate/rollback", "", rotAdmin)
	rotDo(srv, http.MethodPost, "/-/admin/providers/anthropic/rotate", `{"new_key":"`+rotNew+`"}`, rotAdmin)
	w := rotDo(srv, http.MethodPost, "/-/admin/providers/anthropic/rotate/promote", "", rotAdmin)
	st := rotDecode(t, w)
	if w.Code != http.StatusOK || st.ValidatedBy != "chat completion, max_tokens 1, model claude-rot" {
		t.Errorf("promote -> %d %s; want 200 validated by a one-token chat to claude-rot", w.Code, w.Body.String())
	}
	mu.Lock()
	defer mu.Unlock()
	if len(seen) != 2 || !strings.HasPrefix(seen[1], rotNew+" ") || !strings.Contains(seen[1], `"max_tokens":1`) {
		t.Errorf("validation calls = %q; want the refused key's then one max_tokens 1 call with the new key", seen)
	}
}

// TestRotationBroadcastReachesReplicas: two replicas on one Redis (the test
// Redis, TEST_REDIS_ADDR/TEST_REDIS_DB); a promote on one makes the other send
// the new key too, without a promote there.
func TestRotationBroadcastReachesReplicas(t *testing.T) {
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
	prefix := fmt.Sprintf("rotation-%d:", time.Now().UnixNano())

	upA, upB := startRotUpstream(t), startRotUpstream(t)
	srvA := rotServer(t, upA.URL, 20*time.Millisecond, nil)
	srvB := rotServer(t, upB.URL, 20*time.Millisecond, nil)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	for _, srv := range []*Server{srvA, srvB} {
		b, err := rotation.NewBroadcaster(client.Redis(), rotAdmin, prefix)
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

	rotChat(t, srvB, rotInternal, "replica before")
	if got := upB.lastChat(); got != "Bearer "+rotOld {
		t.Fatalf("replica B before the rotation sent %q", got)
	}
	if w := rotPromote(t, srvA, rotNew); w.Code != http.StatusOK {
		t.Fatalf("promote on replica A -> %d %s", w.Code, w.Body.String())
	}
	deadline := time.Now().Add(5 * time.Second)
	for {
		st := rotDecode(t, rotDo(srvB, http.MethodGet, "/-/admin/providers/openai/rotation", "", rotAdmin))
		if st.Source == "replica" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("replica B never applied replica A's promote: %+v", st)
		}
		time.Sleep(10 * time.Millisecond)
	}
	rotChat(t, srvB, rotInternal, "replica after")
	if got := upB.lastChat(); got != "Bearer "+rotNew {
		t.Errorf("replica B after replica A's promote sent %q, want the new key", got)
	}
	if lists := upB.listAuths(); len(lists) != 0 {
		t.Errorf("replica B validated the broadcast key again: %v", lists)
	}
}

// TestRotationValidationErrorCarriesNoKey: an upstream may repeat the key
// it refused (OpenAI's 401 shows its first and last characters; others echo
// it whole, in a JSON message or a raw body). The refused promote's log line
// and its 422 answer carry the upstream's status, type and code, never its
// message, so no part of the key reaches either.
func TestRotationValidationErrorCarriesNoKey(t *testing.T) {
	masked := rotBad[:8] + strings.Repeat("*", len(rotBad)-12) + rotBad[len(rotBad)-4:]
	for _, tc := range []struct{ name, body string }{
		{"json message, masked like openai", `{"type":"error","error":{"type":"authentication_error","message":"Incorrect API key provided: ` + masked + `"}}`},
		{"json message, whole key", `{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key ` + rotBad + `"}}`},
		{"raw body, whole key", `key ` + rotBad + ` refused`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var buf bytes.Buffer
			var mu sync.Mutex
			prev := slog.Default()
			slog.SetDefault(slog.New(slog.NewTextHandler(&lockedWriter{w: &buf, mu: &mu}, &slog.HandlerOptions{Level: slog.LevelDebug})))
			defer slog.SetDefault(prev)

			up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				io.Copy(io.Discard, r.Body)
				if r.Header.Get("x-api-key") == rotBad {
					w.WriteHeader(http.StatusUnauthorized)
					io.WriteString(w, tc.body)
					return
				}
				w.Header().Set("Content-Type", "application/json")
				io.WriteString(w, `{"id":"msg_1","type":"message","role":"assistant","model":"claude-rot","content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1}}`)
			}))
			defer up.Close()
			srv := rotServer(t, "http://127.0.0.1:9", 20*time.Millisecond, func(c *config.Config) {
				c.Providers["anthropic"] = config.ProviderConfig{
					BaseURL: up.URL, APIKey: rotOld, APIFormat: "anthropic", Models: []string{"claude-rot"},
					Rotation: config.RotationConfig{Enabled: true, DrainPeriod: 20 * time.Millisecond},
				}
			})
			if w := rotDo(srv, http.MethodPost, "/-/admin/providers/anthropic/rotate", `{"new_key":"`+rotBad+`"}`, rotAdmin); w.Code != http.StatusOK {
				t.Fatalf("rotate -> %d %s", w.Code, w.Body.String())
			}
			w := rotDo(srv, http.MethodPost, "/-/admin/providers/anthropic/rotate/promote", "", rotAdmin)
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
				if strings.Contains(text, rotBad[:8]) || strings.Contains(text, rotBad[len(rotBad)-12:]) {
					t.Errorf("the %s carries part of the refused key:\n%s", where, text)
				}
			}
		})
	}
}

// TestRotationValidatesWithChatWhenNoModelsList: an OpenAI-compatible
// provider without a models endpoint (GET /v1/models answers 404) cannot
// tell a good key from a bad one by listing models, so the promote validates
// the key with a one-token chat completion to its first model instead; a key
// that chat refuses is not promoted.
func TestRotationValidatesWithChatWhenNoModelsList(t *testing.T) {
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
		if r.Header.Get("Authorization") == "Bearer "+rotBad {
			w.WriteHeader(http.StatusUnauthorized)
			io.WriteString(w, `{"error":{"message":"invalid key","type":"invalid_request_error","code":"invalid_api_key"}}`)
			return
		}
		io.WriteString(w, `{"id":"x","object":"chat.completion","created":1,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
	}))
	defer up.Close()
	srv := rotServer(t, up.URL, 20*time.Millisecond, nil)

	if w := rotPromote(t, srv, rotBad); w.Code != http.StatusUnprocessableEntity {
		t.Errorf("promote of a key chat refuses -> %d %s; want 422", w.Code, w.Body.String())
	}
	rotDo(srv, http.MethodPost, "/-/admin/providers/openai/rotate/rollback", "", rotAdmin)
	w := rotPromote(t, srv, rotNew)
	st := rotDecode(t, w)
	if w.Code != http.StatusOK || st.ValidatedBy != "chat completion, max_tokens 1, model gpt-4o (no models list)" {
		t.Fatalf("promote with no models endpoint -> %d %s; want 200 validated by a one-token chat to gpt-4o", w.Code, w.Body.String())
	}
	mu.Lock()
	defer mu.Unlock()
	if n := len(chats); n != 2 || !strings.HasPrefix(chats[1], "Bearer "+rotNew+" ") || !strings.Contains(chats[1], `"max_tokens":1`) {
		t.Errorf("validation chats = %q; want the refused key's, then one max_tokens 1 call with the new key", chats)
	}
}
