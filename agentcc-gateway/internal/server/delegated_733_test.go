package server

// These wire builders are intentionally independent of auth's implementation.
// Spec-derived vectors until L1b publishes the shared three-section corpus.
import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/batch"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const delegated733Secret = "test-only-delegated-admin-secret-not-a-deployment-secret"
const delegated733Org = "11111111-1111-4111-8111-111111111111"
const delegated733User = "22222222-2222-4222-8222-222222222222"
const delegated733Route = "/-/delegated/v1/chat/completions"
const delegated733Body = `{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hello"}]}`

func wire733(fields []string, secret string) string {
	canonical := []byte(strings.Join(fields, "\n"))
	extract := hmac.New(sha256.New, []byte("fe1"))
	extract.Write([]byte(secret))
	expand := hmac.New(sha256.New, extract.Sum(nil))
	expand.Write(append([]byte(fields[3]+">"+fields[4]+"|"+fields[2]), 1))
	mac := hmac.New(sha256.New, expand.Sum(nil))
	mac.Write(canonical)
	return "fe1." + base64.RawURLEncoding.EncodeToString(canonical) + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}
func nonce733() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b[:])
}
func digest733(s string) string { return fmt.Sprintf("%x", sha256.Sum256([]byte(s))) }
func signed733(route, body, purpose string, at int64) (string, string, []string) {
	id := nonce733()
	grant := wire733([]string{"fe1", "grant", "k1", "fa", "fa-eval-grant", "-", "-", fmt.Sprint(at), fmt.Sprint(at + 600), id, purpose, delegated733Org, "-", delegated733User, "person", "chat:33333333-3333-4333-8333-333333333333", "eval", "-", "-"}, "test-only-fa-secret-unavailable-to-gateway")
	if purpose == "batch" {
		grant = wire733([]string{"fe1", "grant", "k1", "fa", "fa-eval-grant", "-", "-", fmt.Sprint(at), fmt.Sprint(at + 600), id, purpose, delegated733Org, "-", delegated733User, "person", "batch:33333333-3333-4333-8333-333333333333", "eval", "-", "-"}, "test-only-fa-secret-unavailable-to-gateway")
	}
	fields := []string{"fe1", "request", "k1", "fa", "gw-delegated", "POST", route, fmt.Sprint(at), fmt.Sprint(at + 60), nonce733(), "-", delegated733Org, "-", delegated733User, "grant:" + id, "-", purpose, digest733(body), digest733(grant)}
	return wire733(fields, delegated733Secret), grant, fields
}
func newDelegated733Server(t *testing.T, configure ...func(*config.Config)) *Server {
	t.Helper()
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
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
	cfg := config.DefaultConfig()
	cfg.Admin.Token = delegated733Secret
	cfg.Auth.Enabled = true
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL + "/v1", APIFormat: "openai", Models: []string{"gpt-4o-mini"}}
	for _, apply := range configure {
		apply(cfg)
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keys := auth.NewKeyStore(cfg.Auth)
	orgs := tenant.NewStore()
	orgs.Set(delegated733Org, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {Enabled: true, APIKey: "test-only-org-provider", Models: []string{"gpt-4o-mini"}},
	}})
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true)), keys, nil, nil, nil, testModelDBPtr(), orgs, nil)
	srv.ready.Store(true)
	t.Cleanup(srv.batchProcessor.Stop)
	return srv
}

// Existing batch journey tests use the real new wire while retaining their
// trace, guardrail, cancellation and org-isolation assertions.
func prepareBatch733(t *testing.T, srv *Server) {
	t.Helper()
	srv.delegated = auth.NewEnvelopeVerifier(srv.cfg.Admin.Token, time.Now().Add(-time.Minute))
	if srv.keyStore == nil {
		srv.keyStore = auth.NewKeyStore(srv.cfg.Auth)
		srv.batchProcessor.Stop()
		srv.batchProcessor = batch.NewProcessor(batch.RunAsChatRequest(func() http.Handler { return srv.httpServer.Handler }), batch.WithKeyStore(srv.keyStore, true))
	}
	srv.keyStore.SyncDelegations(&auth.DelegationSnapshot{Complete: true, Members: []auth.DelegatedMember{{Org: delegated733Org, User: delegated733User, Workspace: "-"}}}, time.Now())
}
func signBatch733(srv *Server, r *http.Request, body string) string {
	_, grant, fields := signed733("/-/batches", body, "batch", time.Now().Unix())
	r.Header.Del("Authorization")
	r.Header.Del(batch.KeyHeader)
	r.Header.Set(auth.EnvelopeHeader, wire733(fields, srv.cfg.Admin.Token))
	r.Header.Set(auth.GrantHeader, grant)
	return "dlg_" + strings.TrimPrefix(fields[14], "grant:")
}
func readyEpoch733(srv *Server) {
	srv.delegated = auth.NewEnvelopeVerifier(srv.cfg.Admin.Token, time.Now().Add(-time.Minute))
}

func request733(srv *Server, route, body, envelope, grant string) *httptest.ResponseRecorder {
	r := httptest.NewRequest("POST", route, strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("X-FA-Envelope", envelope)
	r.Header.Set("X-FA-Grant", grant)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, r)
	return w
}
func TestDelegated733RouteAndReplay(t *testing.T) {
	srv := newDelegated733Server(t)
	// Ensure integer iat can be >= the fractional boot epoch + 5 seconds.
	time.Sleep(1100 * time.Millisecond)
	env, grant, fields := signed733(delegated733Route, delegated733Body, "playground", time.Now().Unix()+5)
	w := request733(srv, delegated733Route, delegated733Body, env, grant)
	if w.Code != 200 {
		t.Fatalf("delegated chat: %d %s", w.Code, w.Body.String())
	}
	if len(srv.keyStore.List()) != 0 {
		t.Fatal("delegated chat minted a key")
	}
	if got := request733(srv, delegated733Route, delegated733Body, env, grant); got.Code != 401 {
		t.Fatalf("replay: %d", got.Code)
	}
	fields[9] = nonce733()
	env = wire733(fields, delegated733Secret)
	var wg sync.WaitGroup
	statuses := make(chan int, 8)
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			statuses <- request733(srv, delegated733Route, delegated733Body, env, grant).Code
		}()
	}
	wg.Wait()
	close(statuses)
	successes := 0
	for status := range statuses {
		if status == 200 {
			successes++
		} else if status != 401 {
			t.Errorf("concurrent replay status: %d", status)
		}
	}
	if successes != 1 {
		t.Fatalf("concurrent successes: %d", successes)
	}
	// An envelope is not an API key on the public route.
	fields[9] = nonce733()
	fields[6] = "/v1/chat/completions"
	if got := request733(srv, fields[6], delegated733Body, wire733(fields, delegated733Secret), grant); got.Code != 401 {
		t.Fatalf("public envelope: %d", got.Code)
	}
}
func TestDelegated733RejectsBadBindings(t *testing.T) {
	srv := newDelegated733Server(t)
	time.Sleep(1100 * time.Millisecond)
	for _, field := range []int{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18} {
		t.Run(fmt.Sprint(field), func(t *testing.T) {
			_, grant, fields := signed733(delegated733Route, delegated733Body, "playground", time.Now().Unix()+5)
			fields[field] = "invalid"
			got := request733(srv, delegated733Route, delegated733Body, wire733(fields, delegated733Secret), grant)
			if got.Code != 401 {
				t.Fatalf("field %d: %d %s", field, got.Code, got.Body.String())
			}
		})
	}
	env, grant, _ := signed733(delegated733Route, delegated733Body, "playground", time.Now().Unix()-100)
	if got := request733(srv, delegated733Route, delegated733Body, env, grant); got.Code != 401 {
		t.Fatalf("expired: %d", got.Code)
	}
	r := httptest.NewRequest("POST", delegated733Route, strings.NewReader(delegated733Body))
	r.Header.Set("Authorization", "Bearer "+delegated733Secret)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, r)
	if w.Code != 401 {
		t.Fatalf("raw admin token: %d", w.Code)
	}
}
func TestDelegated733SSEAndClusterRefusal(t *testing.T) {
	srv := newDelegated733Server(t)
	srv.delegated = auth.NewEnvelopeVerifier(delegated733Secret, time.Now().Add(-time.Minute))
	body := strings.TrimSuffix(delegated733Body, "}") + `,"stream":true}`
	env, grant, _ := signed733(delegated733Route, body, "playground", time.Now().Unix())
	w := request733(srv, delegated733Route, body, env, grant)
	if w.Code != 200 || !w.Flushed || !strings.Contains(w.Header().Get("Content-Type"), "text/event-stream") || !strings.Contains(w.Body.String(), "streamed") || !strings.Contains(w.Body.String(), "[DONE]") {
		t.Fatalf("SSE: %d %s", w.Code, w.Body.String())
	}
	srv.cfg.Cluster.Enabled = true
	env, grant, _ = signed733(delegated733Route, body, "playground", time.Now().Unix())
	if got := request733(srv, delegated733Route, body, env, grant); got.Code != 401 {
		t.Fatal("cluster admitted process-local nonce")
	}
}

func TestDelegated733BatchSubmitAndReadback(t *testing.T) {
	srv := newDelegated733Server(t)
	srv.delegated = auth.NewEnvelopeVerifier(delegated733Secret, time.Now().Add(-time.Minute))
	srv.keyStore.SyncDelegations(&auth.DelegationSnapshot{Complete: true, Members: []auth.DelegatedMember{{Org: delegated733Org, User: delegated733User, Workspace: "-"}}}, time.Now())
	body := `{"requests":[` + delegated733Body + `],"max_concurrency":1}`
	env, grant, _ := signed733("/-/batches", body, "batch", time.Now().Unix())
	w := request733(srv, "/-/batches", body, env, grant)
	if w.Code != 202 {
		t.Fatalf("batch submit: %d %s", w.Code, w.Body.String())
	}
	var submitted struct {
		ID string `json:"batch_id"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &submitted); err != nil {
		t.Fatal(err)
	}
	b, ok := srv.batchProcessor.GetForOrg(submitted.ID, delegated733Org)
	if !ok {
		t.Fatal("batch not scoped to principal org")
	}
	for deadline := time.Now().Add(5 * time.Second); b.GetCompletedAt() == nil; time.Sleep(time.Millisecond) {
		if time.Now().After(deadline) {
			t.Fatal("batch did not finish")
		}
	}
	if results := b.GetResults(); len(results) != 1 || results[0].Status != "success" {
		t.Fatalf("batch item: %+v", results)
	}
	for i := 0; i < 2; i++ {
		r := httptest.NewRequest("POST", "/-/batches/"+b.ID+"/cancel?org_id="+delegated733Org, nil)
		r.Header.Set("Authorization", "Bearer "+delegated733Secret)
		got := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(got, r)
		if got.Code != 200 {
			t.Fatalf("cancel readback: %d %s", got.Code, got.Body.String())
		}
	}
	r := httptest.NewRequest("GET", "/-/batches/"+b.ID+"?org_id="+delegated733Org, nil)
	r.Header.Set("Authorization", "Bearer "+delegated733Secret)
	got := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(got, r)
	if got.Code != 200 || strings.Contains(got.Body.String(), grant) || strings.Contains(got.Body.String(), "fe1.") || len(srv.keyStore.List()) != 0 {
		t.Fatalf("batch readback: %d %s", got.Code, got.Body.String())
	}
}

func TestDelegated733BootReadinessAndBatchRequiresEnvelope(t *testing.T) {
	srv := newDelegated733Server(t)
	w := httptest.NewRecorder()
	srv.readyHandler(w, httptest.NewRequest("GET", "/readyz", nil))
	if w.Code != 503 {
		t.Fatalf("boot skew readiness: %d", w.Code)
	}
	r := httptest.NewRequest("POST", "/-/batches", strings.NewReader(`{"requests":[`+delegated733Body+`]}`))
	r.Header.Set("Authorization", "Bearer "+delegated733Secret)
	w = httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, r)
	if w.Code != 401 {
		t.Fatalf("batch without envelope: %d %s", w.Code, w.Body.String())
	}
}
