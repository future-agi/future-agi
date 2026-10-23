package server

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// An async chat completion (x-agentcc-async: true) must run exactly as the
// same synchronous request from the same key runs: on the provider that
// routing resolved when the request was admitted, under the same pipeline
// (auth, credits). It must not fall back to the platform's own provider, and
// DELETE /v1/async/{id} must stop a call that is already running.

const (
	asyncExecOrg        = "org-async-exec"
	asyncExecKeyOrg     = "sk-agentcc-async-exec-org"
	asyncExecKeyManaged = "sk-agentcc-async-exec-managed"
	asyncExecKeyOps     = "sk-agentcc-async-exec-ops"

	asyncPlatformCred = "Bearer platform-openai-key"
	asyncOrgCred      = "Bearer org-openai-key-" + asyncExecOrg
)

// newAsyncExecServer builds the whole gateway, with the auth and credits
// plugins in the pipeline, against upstreamURL. The platform's openai provider
// serves gpt-4o and gpt-4o-mini with the platform's key; the org's own openai
// provider serves only gpt-4o, with the org's key. The org key and the managed
// key (no credits) belong to the org; the ops key is an internal key.
func newAsyncExecServer(t *testing.T, upstreamURL string) *Server {
	t.Helper()

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "org", Key: asyncExecKeyOrg, Owner: "o", KeyType: "byok", Metadata: map[string]string{"org_id": asyncExecOrg}},
		{Name: "managed", Key: asyncExecKeyManaged, Owner: "m", KeyType: "managed", Metadata: map[string]string{"org_id": asyncExecOrg}},
		{Name: "ops", Key: asyncExecKeyOps, Owner: "ops", KeyType: "internal"},
	}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   upstreamURL,
		APIKey:    "platform-openai-key",
		APIFormat: "openai",
		Models:    []string{"gpt-4o", "gpt-4o-mini"},
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}

	tenantStore := tenant.NewStore()
	tenantStore.Set(asyncExecOrg, &tenant.OrgConfig{
		Providers: map[string]*tenant.ProviderConfig{
			"openai": {Enabled: true, APIKey: "org-openai-key-" + asyncExecOrg, Models: []string{"gpt-4o"}},
		},
	})

	keyStore := auth.NewKeyStore(cfg.Auth)
	engine := pipeline.NewEngine(authplugin.New(keyStore, true), credits.New(true, keyStore))

	srv := New(cfg, "", registry, engine, keyStore, nil, nil, nil, testModelDBPtr(), tenantStore, nil)
	srv.ready.Store(true)
	t.Cleanup(srv.asyncWorker.Stop)
	return srv
}

// credentialRecorder is an OpenAI-compatible upstream that records the
// Authorization header of every chat completion it serves.
type credentialRecorder struct {
	*httptest.Server
	mu   sync.Mutex
	seen []string
}

func newCredentialRecorder(t *testing.T) *credentialRecorder {
	t.Helper()
	rec := &credentialRecorder{}
	rec.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
			fmt.Fprint(w, `{"object":"list","data":[]}`)
			return
		}
		rec.mu.Lock()
		rec.seen = append(rec.seen, r.Header.Get("Authorization"))
		rec.mu.Unlock()
		fmt.Fprint(w, `{"id":"chatcmpl-async-exec","object":"chat.completion","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
	}))
	t.Cleanup(rec.Close)
	return rec
}

// take returns the credentials seen since the last call and forgets them.
func (rec *credentialRecorder) take() []string {
	rec.mu.Lock()
	defer rec.mu.Unlock()
	seen := rec.seen
	rec.seen = nil
	return seen
}

func chatBody(model string) string {
	return `{"model":"` + model + `","messages":[{"role":"user","content":"hi"}]}`
}

// waitAsyncJob polls GET /v1/async/{id} as key until the job leaves queued and
// processing, and returns its last body.
func waitAsyncJob(t *testing.T, srv *Server, key, id string) map[string]any {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for {
		w := objCall(t, srv, "GET", "/v1/async/"+id, key, "", nil)
		if w.Code != http.StatusOK {
			t.Fatalf("GET /v1/async/%s: status %d body %s", id, w.Code, w.Body.String())
		}
		job := mediaDecode(t, w)
		if s := job["status"]; s != "queued" && s != "processing" {
			return job
		}
		if time.Now().After(deadline) {
			t.Fatalf("async job %s still %v after 5s", id, job["status"])
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func sameCredentials(got []string, want string) bool {
	if want == "" {
		return len(got) == 0
	}
	return len(got) == 1 && got[0] == want
}

func TestAsyncJobs_RunWithTheRoutingOfTheSameSyncRequest(t *testing.T) {
	up := newCredentialRecorder(t)
	srv := newAsyncExecServer(t, up.URL)

	cases := []struct {
		name     string
		key      string
		model    string
		syncCode int    // the synchronous request's status
		admitted bool   // the async request answers 202; otherwise syncCode and no job
		jobError string // the admitted job's error code; "" means it completes
		cred     string // the only credential the upstream may see; "" means none
		source   string // the job's credential_source: whose credential cred is
	}{
		{"org key, model its org serves", asyncExecKeyOrg, "gpt-4o", http.StatusOK, true, "", asyncOrgCred, "org"},
		{"org key, model only the platform serves", asyncExecKeyOrg, "gpt-4o-mini", http.StatusForbidden, false, "", "", ""},
		{"managed org key without credits", asyncExecKeyManaged, "gpt-4o", http.StatusPaymentRequired, true, "payment_required", "", ""},
		{"internal key", asyncExecKeyOps, "gpt-4o", http.StatusOK, true, "", asyncPlatformCred, "platform"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			up.take()

			w := objCall(t, srv, "POST", "/v1/chat/completions", tc.key, chatBody(tc.model), nil)
			if w.Code != tc.syncCode {
				t.Fatalf("sync request: status %d, want %d; body %s", w.Code, tc.syncCode, w.Body.String())
			}
			if got := up.take(); !sameCredentials(got, tc.cred) {
				t.Fatalf("sync request reached the upstream with %q, want only %q", got, tc.cred)
			}

			jobsBefore := srv.handlers.asyncStore.Count()
			w = objCall(t, srv, "POST", "/v1/chat/completions", tc.key, chatBody(tc.model),
				map[string]string{"x-agentcc-async": "true"})
			if !tc.admitted {
				if w.Code != tc.syncCode {
					t.Errorf("async request: status %d, want %d like the sync request; body %s", w.Code, tc.syncCode, w.Body.String())
				}
				if n := srv.handlers.asyncStore.Count(); n != jobsBefore {
					t.Errorf("a refused async request stored a job (%d jobs, was %d)", n, jobsBefore)
				}
				if got := up.take(); len(got) != 0 {
					t.Errorf("a refused async request reached the upstream with %q", got)
				}
				return
			}
			if w.Code != http.StatusAccepted {
				t.Fatalf("async request: status %d, want 202; body %s", w.Code, w.Body.String())
			}
			job := waitAsyncJob(t, srv, tc.key, mediaDecode(t, w)["job_id"].(string))

			if got := up.take(); !sameCredentials(got, tc.cred) {
				t.Errorf("async job reached the upstream with %q, want only %q (what the sync request used)", got, tc.cred)
			}
			if tc.jobError == "" {
				if job["status"] != "completed" {
					t.Fatalf("async job status %v, want completed; job %v", job["status"], job)
				}
				// Both credentials belong to the routing provider openai;
				// credential_source tells them apart.
				if job["provider"] != "openai" {
					t.Errorf("async job records provider %v, want openai", job["provider"])
				}
				if job["credential_source"] != tc.source {
					t.Errorf("async job records credential_source %v, want %s (it ran with %s)", job["credential_source"], tc.source, tc.cred)
				}
				return
			}
			// The managed key is refused before any provider is called, so
			// there is no credential to name.
			if _, ok := job["credential_source"]; ok {
				t.Errorf("a job that never called a provider records credential_source %v", job["credential_source"])
			}
			if job["status"] != "failed" {
				t.Fatalf("async job status %v, want failed with %s; job %v", job["status"], tc.jobError, job)
			}
			if apiErr, _ := job["error"].(map[string]any); apiErr == nil || apiErr["Code"] != tc.jobError {
				t.Errorf("async job error %v, want code %s", job["error"], tc.jobError)
			}
		})
	}
}

// slowUpstream holds every chat completion open until the caller goes away
// (or the test ends), and reports when a call starts and when its caller
// cancelled it.
type slowUpstream struct {
	*httptest.Server
	started   chan struct{}
	cancelled chan struct{}
}

func newSlowUpstream(t *testing.T) *slowUpstream {
	t.Helper()
	release := make(chan struct{})
	up := &slowUpstream{started: make(chan struct{}, 8), cancelled: make(chan struct{}, 8)}
	up.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, `{"object":"list","data":[]}`)
			return
		}
		// The server notices a caller that went away only once the
		// request body has been read.
		io.Copy(io.Discard, r.Body)
		up.started <- struct{}{}
		select {
		case <-r.Context().Done():
			up.cancelled <- struct{}{}
		case <-release:
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, `{"id":"chatcmpl-slow","object":"chat.completion","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"late"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
		}
	}))
	// Cleanups run last-in first-out: release held calls, then close.
	t.Cleanup(up.Close)
	t.Cleanup(func() { close(release) })
	return up
}

func TestAsyncJobs_DeleteCancelsTheRunningProviderCall(t *testing.T) {
	up := newSlowUpstream(t)
	srv := newAsyncExecServer(t, up.URL)

	w := objCall(t, srv, "POST", "/v1/chat/completions", asyncExecKeyOrg, chatBody("gpt-4o"),
		map[string]string{"x-agentcc-async": "true"})
	if w.Code != http.StatusAccepted {
		t.Fatalf("submit async job: status %d body %s", w.Code, w.Body.String())
	}
	id := mediaDecode(t, w)["job_id"].(string)

	select {
	case <-up.started:
	case <-time.After(5 * time.Second):
		t.Fatal("the async job never called the provider")
	}

	w = objCall(t, srv, "DELETE", "/v1/async/"+id, asyncExecKeyOrg, "", nil)
	if w.Code != http.StatusOK {
		t.Fatalf("DELETE /v1/async/%s: status %d body %s", id, w.Code, w.Body.String())
	}

	select {
	case <-up.cancelled:
	case <-time.After(3 * time.Second):
		t.Fatal("DELETE /v1/async/{id} answered 200 but the provider call kept running")
	}

	if w := objCall(t, srv, "GET", "/v1/async/"+id, asyncExecKeyOrg, "", nil); w.Code != http.StatusNotFound {
		t.Errorf("GET a deleted async job: status %d, want 404", w.Code)
	}
	if srv.handlers.asyncStore.Get(id) != nil {
		t.Error("the deleted job came back after its provider call ended")
	}
}

// signLicenseToken mints a managed-service license token for model, and
// returns it with the PEM public key that verifies it.
func signLicenseToken(t *testing.T, model string) (token, publicPEM string) {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	der, err := x509.MarshalPKIXPublicKey(&key.PublicKey)
	if err != nil {
		t.Fatal(err)
	}
	publicPEM = string(pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: der}))

	now := time.Now().Unix()
	header, _ := json.Marshal(map[string]string{"alg": "RS256", "kid": "default"})
	payload, _ := json.Marshal(map[string]any{
		"typ": "futureagi-managed-service-token", "iss": "https://licenses.futureagi.com",
		"aud": "futureagi-agentcc-gateway", "iat": now, "nbf": now, "exp": now + 3600,
		"jti": "tok_async", "license_id": "lic_async", "instance_id": "inst_async",
		"authorization_version": 1, "scope": "enterprise",
		"services": []string{"turing"}, "models": []string{model},
	})
	signed := base64.RawURLEncoding.EncodeToString(header) + "." + base64.RawURLEncoding.EncodeToString(payload)
	digest := sha256.Sum256([]byte(signed))
	sig, err := rsa.SignPKCS1v15(rand.Reader, key, crypto.SHA256, digest[:])
	if err != nil {
		t.Fatal(err)
	}
	return signed + "." + base64.RawURLEncoding.EncodeToString(sig), publicPEM
}

// A managed-service (license) token may call only POST /v1/chat/completions
// and cannot read job results. Its async request is refused with 400 before
// any provider is called; its sync request still runs.
func TestAsyncJobs_LicenseAuthorizedRequestCannotQueueAJob(t *testing.T) {
	up := newCredentialRecorder(t)
	token, publicPEM := signLicenseToken(t, "turing_small")

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "ops", Key: asyncExecKeyOps, Owner: "ops", KeyType: "internal"}}
	cfg.LicenseAuth = config.LicenseAuthConfig{Enabled: true, PublicKey: publicPEM}
	cfg.Providers["turing"] = config.ProviderConfig{
		BaseURL:   up.URL,
		APIKey:    "platform-turing-key",
		APIFormat: "openai",
		Models:    []string{"turing_small"},
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	engine := pipeline.NewEngine(authplugin.New(keyStore, true), credits.New(true, keyStore))
	srv := New(cfg, "", registry, engine, keyStore, nil, nil, nil, testModelDBPtr(), tenant.NewStore(), nil)
	srv.ready.Store(true)
	t.Cleanup(srv.asyncWorker.Stop)

	const platformCred = "Bearer platform-turing-key"
	w := objCall(t, srv, "POST", "/v1/chat/completions", token, chatBody("turing_small"), nil)
	if w.Code != http.StatusOK {
		t.Fatalf("sync request with a license token: status %d body %s", w.Code, w.Body.String())
	}
	if got := up.take(); !sameCredentials(got, platformCred) {
		t.Fatalf("sync request reached the upstream with %q, want only %q", got, platformCred)
	}

	w = objCall(t, srv, "POST", "/v1/chat/completions", token, chatBody("turing_small"),
		map[string]string{"x-agentcc-async": "true"})
	if w.Code != http.StatusBadRequest {
		t.Fatalf("async request with a license token: status %d body %s, want 400", w.Code, w.Body.String())
	}
	if code := mediaDecode(t, w)["error"].(map[string]any)["code"]; code != "async_not_supported" {
		t.Errorf("async request with a license token: error code %v, want async_not_supported", code)
	}
	if n := srv.handlers.asyncStore.Count(); n != 0 {
		t.Errorf("a refused license async request stored %d jobs", n)
	}
	time.Sleep(50 * time.Millisecond)
	if got := up.take(); len(got) != 0 {
		t.Errorf("a refused license async request reached the upstream with %q", got)
	}
}
