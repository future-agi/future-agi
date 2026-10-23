package server

import (
	"bytes"
	"fmt"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/async"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Files, stored Responses and async jobs belong to the org of the API key
// that created them, the same rule as video and scheduled jobs
// (media_jobs_org_scope_test.go). Another org's key must not list, read,
// download or delete them, and get, content and delete answer 404 so an id
// from another org reveals nothing. A key with no org only reaches objects that
// also have no org.

const (
	objOrgA     = "org-objects-a"
	objOrgB     = "org-objects-b"
	objKeyA     = "sk-agentcc-objects-org-a"
	objKeyB     = "sk-agentcc-objects-org-b"
	objKeyNoOrg = "sk-agentcc-objects-no-org"
)

// newStoredObjectsServer builds the whole gateway (key auth middleware, router,
// handlers) the way main.go does, with a tenant store. With auth on, a non
// internal key may only use its own org's providers, so orgs A and B each get
// an OpenAI provider (inheriting the mock's base URL), and the no-org key is an
// internal (ops) key: a key with no org that can reach a provider at all is
// one. With authEnabled false it is the OSS single-tenant setup: no key store,
// every request has no org.
func newStoredObjectsServer(t *testing.T, authEnabled bool) *Server {
	t.Helper()

	var seq atomic.Int64
	mock := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/v1/responses":
			fmt.Fprintf(w, `{"id":"resp_scope_%d","object":"response","status":"completed","model":"gpt-4o","output":[],"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}`, seq.Add(1))
		case r.Method == http.MethodPost && r.URL.Path == "/v1/chat/completions":
			fmt.Fprint(w, `{"id":"chatcmpl-scope","object":"chat.completion","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
		default:
			fmt.Fprint(w, `{"object":"list","data":[]}`)
		}
	}))
	t.Cleanup(mock.Close)

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = authEnabled
	if authEnabled {
		cfg.Auth.Keys = []config.AuthKeyConfig{
			{Name: "org-a", Key: objKeyA, Owner: "a", KeyType: "byok", Metadata: map[string]string{"org_id": objOrgA}},
			{Name: "org-b", Key: objKeyB, Owner: "b", KeyType: "byok", Metadata: map[string]string{"org_id": objOrgB}},
			{Name: "no-org", Key: objKeyNoOrg, Owner: "ops", KeyType: "internal"},
		}
	}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   mock.URL,
		APIKey:    "platform-openai-key",
		APIFormat: "openai",
		Models:    []string{"gpt-4o"},
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}

	tenantStore := tenant.NewStore()
	for _, org := range []string{objOrgA, objOrgB} {
		tenantStore.Set(org, &tenant.OrgConfig{
			Providers: map[string]*tenant.ProviderConfig{
				"openai": {Enabled: true, APIKey: "org-openai-key-" + org, Models: []string{"gpt-4o"}},
			},
		})
	}

	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenantStore, nil)
	srv.ready.Store(true)
	t.Cleanup(srv.asyncWorker.Stop)
	return srv
}

// objCall sends a request with the key as a Bearer token, plus any extra
// headers.
func objCall(t *testing.T, srv *Server, method, path, key, body string, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, path, bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// uploadFileWith uploads a file, authenticating with the given headers.
func uploadFileWith(t *testing.T, srv *Server, headers map[string]string, content string) string {
	t.Helper()
	var body bytes.Buffer
	mw := multipart.NewWriter(&body)
	if err := mw.WriteField("purpose", "batch"); err != nil {
		t.Fatal(err)
	}
	part, err := mw.CreateFormFile("file", "data.jsonl")
	if err != nil {
		t.Fatal(err)
	}
	part.Write([]byte(content))
	mw.Close()

	req := httptest.NewRequest("POST", "/v1/files", &body)
	req.Header.Set("Content-Type", mw.FormDataContentType())
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("upload file: status %d body %s", w.Code, w.Body.String())
	}
	return mediaDecode(t, w)["id"].(string)
}

func uploadFileAs(t *testing.T, srv *Server, key string) string {
	t.Helper()
	headers := map[string]string{}
	if key != "" {
		headers["Authorization"] = "Bearer " + key
	}
	return uploadFileWith(t, srv, headers, "content of "+key)
}

func createResponseAs(t *testing.T, srv *Server, key string) string {
	t.Helper()
	w := objCall(t, srv, "POST", "/v1/responses", key, `{"model":"gpt-4o","input":"hi"}`, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("create response: status %d body %s", w.Code, w.Body.String())
	}
	return mediaDecode(t, w)["id"].(string)
}

func submitAsyncAs(t *testing.T, srv *Server, key string) string {
	t.Helper()
	w := objCall(t, srv, "POST", "/v1/chat/completions", key,
		`{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`,
		map[string]string{"x-agentcc-async": "true"})
	if w.Code != http.StatusAccepted {
		t.Fatalf("submit async job: status %d body %s", w.Code, w.Body.String())
	}
	return mediaDecode(t, w)["job_id"].(string)
}

// orgCase is an owner key that creates an object and an intruder key from
// another tenant that must not reach it.
type orgCase struct {
	name     string
	owner    string
	intruder string
}

var crossOrgCases = []orgCase{
	{"other org", objKeyA, objKeyB},
	{"no-org key on an org's object", objKeyA, objKeyNoOrg},
	{"org key on a no-org object", objKeyNoOrg, objKeyA},
}

var keyOrgs = map[string]string{objKeyA: objOrgA, objKeyB: objOrgB, objKeyNoOrg: ""}

// --- Files ---

func TestFiles_UploadRecordsCallerOrg(t *testing.T) {
	srv := newStoredObjectsServer(t, true)
	for key, wantOrg := range keyOrgs {
		id := uploadFileAs(t, srv, key)
		stored := srv.handlers.fileStore.Get(id, wantOrg)
		if stored == nil || stored.OrgID != wantOrg {
			got := "<not found>"
			if stored != nil {
				got = stored.OrgID
			}
			t.Errorf("file uploaded with key %q has org %q, want %q", key, got, wantOrg)
		}
	}
}

func TestFiles_ListReturnsOnlyCallerOrg(t *testing.T) {
	srv := newStoredObjectsServer(t, true)
	own := map[string]string{}
	for key := range keyOrgs {
		own[key] = uploadFileAs(t, srv, key)
	}
	for key, id := range own {
		ids := listedIDs(t, objCall(t, srv, "GET", "/v1/files", key, "", nil))
		if len(ids) != 1 || !ids[id] {
			t.Errorf("key %q listed files %v, want only its own %s", key, ids, id)
		}
	}
}

func TestFiles_OtherOrgGetsNotFound(t *testing.T) {
	for _, tc := range crossOrgCases {
		t.Run(tc.name, func(t *testing.T) {
			srv := newStoredObjectsServer(t, true)
			id := uploadFileAs(t, srv, tc.owner)
			for _, req := range []struct{ method, path string }{
				{"GET", "/v1/files/" + id},
				{"GET", "/v1/files/" + id + "/content"},
				{"DELETE", "/v1/files/" + id},
			} {
				w := objCall(t, srv, req.method, req.path, tc.intruder, "", nil)
				if w.Code != http.StatusNotFound {
					t.Errorf("%s %s by another tenant: status %d, want 404; body %s", req.method, req.path, w.Code, w.Body.String())
				}
			}
			if w := objCall(t, srv, "GET", "/v1/files/"+id, tc.owner, "", nil); w.Code != http.StatusOK {
				t.Errorf("owner GET after the intruder's delete: status %d, want 200 (file must survive)", w.Code)
			}
		})
	}
}

func TestFiles_OwnerCanGetContentAndDelete(t *testing.T) {
	for _, key := range []string{objKeyA, objKeyNoOrg} {
		srv := newStoredObjectsServer(t, true)
		id := uploadFileAs(t, srv, key)
		if w := objCall(t, srv, "GET", "/v1/files/"+id, key, "", nil); w.Code != http.StatusOK {
			t.Errorf("key %q GET own file: status %d", key, w.Code)
		}
		w := objCall(t, srv, "GET", "/v1/files/"+id+"/content", key, "", nil)
		if w.Code != http.StatusOK || w.Body.String() != "content of "+key {
			t.Errorf("key %q GET own file content: status %d body %q", key, w.Code, w.Body.String())
		}
		if w := objCall(t, srv, "DELETE", "/v1/files/"+id, key, "", nil); w.Code != http.StatusOK {
			t.Errorf("key %q DELETE own file: status %d", key, w.Code)
		}
		if w := objCall(t, srv, "GET", "/v1/files/"+id, key, "", nil); w.Code != http.StatusNotFound {
			t.Errorf("key %q GET deleted file: status %d, want 404", key, w.Code)
		}
	}
}

// --- Responses ---

func TestResponses_CreateRecordsCallerOrg(t *testing.T) {
	srv := newStoredObjectsServer(t, true)
	for key, wantOrg := range keyOrgs {
		id := createResponseAs(t, srv, key)
		stored := srv.handlers.responsesStore.Get(id, wantOrg)
		if stored == nil || stored.OrgID != wantOrg {
			t.Errorf("response created with key %q is not stored under org %q", key, wantOrg)
		}
	}
}

func TestResponses_OtherOrgGetsNotFound(t *testing.T) {
	for _, tc := range crossOrgCases {
		t.Run(tc.name, func(t *testing.T) {
			srv := newStoredObjectsServer(t, true)
			id := createResponseAs(t, srv, tc.owner)
			for _, method := range []string{"GET", "DELETE"} {
				w := objCall(t, srv, method, "/v1/responses/"+id, tc.intruder, "", nil)
				if w.Code != http.StatusNotFound {
					t.Errorf("%s /v1/responses/%s by another tenant: status %d, want 404; body %s", method, id, w.Code, w.Body.String())
				}
			}
			if w := objCall(t, srv, "GET", "/v1/responses/"+id, tc.owner, "", nil); w.Code != http.StatusOK {
				t.Errorf("owner GET after the intruder's delete: status %d, want 200 (response must survive)", w.Code)
			}
		})
	}
}

func TestResponses_OwnerCanGetAndDelete(t *testing.T) {
	for _, key := range []string{objKeyA, objKeyNoOrg} {
		srv := newStoredObjectsServer(t, true)
		id := createResponseAs(t, srv, key)
		w := objCall(t, srv, "GET", "/v1/responses/"+id, key, "", nil)
		if w.Code != http.StatusOK || mediaDecode(t, w)["id"] != id {
			t.Errorf("key %q GET own response: status %d body %s", key, w.Code, w.Body.String())
		}
		if w := objCall(t, srv, "DELETE", "/v1/responses/"+id, key, "", nil); w.Code != http.StatusOK {
			t.Errorf("key %q DELETE own response: status %d", key, w.Code)
		}
		if w := objCall(t, srv, "GET", "/v1/responses/"+id, key, "", nil); w.Code != http.StatusNotFound {
			t.Errorf("key %q GET deleted response: status %d, want 404", key, w.Code)
		}
	}
}

// --- Async jobs ---

func TestAsyncJobs_CreateRecordsCallerOrg(t *testing.T) {
	srv := newStoredObjectsServer(t, true)
	for key, wantOrg := range keyOrgs {
		id := submitAsyncAs(t, srv, key)
		job := srv.handlers.asyncStore.Get(id)
		if job == nil || job.OrgID != wantOrg {
			t.Errorf("async job submitted with key %q is not stored under org %q", key, wantOrg)
		}
	}
}

func TestAsyncJobs_OtherOrgGetsNotFound(t *testing.T) {
	for _, tc := range crossOrgCases {
		t.Run(tc.name, func(t *testing.T) {
			srv := newStoredObjectsServer(t, true)
			id := submitAsyncAs(t, srv, tc.owner)
			for _, method := range []string{"GET", "DELETE"} {
				w := objCall(t, srv, method, "/v1/async/"+id, tc.intruder, "", nil)
				if w.Code != http.StatusNotFound {
					t.Errorf("%s /v1/async/%s by another tenant: status %d, want 404; body %s", method, id, w.Code, w.Body.String())
				}
			}
			if srv.handlers.asyncStore.Get(id) == nil {
				t.Error("the intruder's DELETE removed another tenant's async job")
			}
			if w := objCall(t, srv, "GET", "/v1/async/"+id, tc.owner, "", nil); w.Code != http.StatusOK {
				t.Errorf("owner GET after the intruder's delete: status %d, want 200", w.Code)
			}
		})
	}
}

func TestAsyncJobs_OwnerCanGetAndDelete(t *testing.T) {
	for _, key := range []string{objKeyA, objKeyNoOrg} {
		srv := newStoredObjectsServer(t, true)
		id := submitAsyncAs(t, srv, key)
		w := objCall(t, srv, "GET", "/v1/async/"+id, key, "", nil)
		if w.Code != http.StatusOK || mediaDecode(t, w)["id"] != id {
			t.Errorf("key %q GET own async job: status %d body %s", key, w.Code, w.Body.String())
		}
		if w := objCall(t, srv, "DELETE", "/v1/async/"+id, key, "", nil); w.Code != http.StatusOK {
			t.Errorf("key %q DELETE own async job: status %d", key, w.Code)
		}
		if srv.handlers.asyncStore.Get(id) != nil {
			t.Errorf("key %q DELETE own async job left it in the store", key)
		}
	}
}

// --- Both ---

// The org comes from the same key KeyAuth authenticated: a Bearer
// Authorization header first, otherwise x-api-key; an Authorization header of
// any other scheme is ignored.
//
// Creating a response or async job needs provider routing, which reads
// any Authorization header first, so with a Basic header it finds no org and
// refuses the request (asserted below); those objects are seeded in the store
// here instead. Uploads need no provider.
func TestStoredObjects_CallerOrgFollowsKeyAuthHeaderPrecedence(t *testing.T) {
	cases := []struct {
		name          string
		headers       map[string]string
		wantOrg       string
		createRefused bool
	}{
		{"basic authorization with org key in x-api-key", map[string]string{"Authorization": "Basic dXNlcjpwYXNz", "x-api-key": objKeyA}, objOrgA, true},
		{"org key in x-api-key only", map[string]string{"x-api-key": objKeyA}, objOrgA, false},
		{"bearer key beats x-api-key", map[string]string{"Authorization": "Bearer " + objKeyB, "x-api-key": objKeyA}, objOrgB, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := newStoredObjectsServer(t, true)
			h := srv.handlers

			if tc.createRefused {
				w := objCall(t, srv, "POST", "/v1/responses", "", `{"model":"gpt-4o","input":"hi"}`, tc.headers)
				if w.Code < 400 {
					t.Errorf("POST /v1/responses with %v: status %d, want it refused", tc.headers, w.Code)
				}
				asyncHeaders := map[string]string{"x-agentcc-async": "true"}
				for k, v := range tc.headers {
					asyncHeaders[k] = v
				}
				w = objCall(t, srv, "POST", "/v1/chat/completions", "",
					`{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`, asyncHeaders)
				if w.Code < 400 {
					t.Errorf("async POST /v1/chat/completions with %v: status %d, want it refused", tc.headers, w.Code)
				}
				if n := h.asyncStore.Count(); n != 0 {
					t.Errorf("a refused async submit stored %d jobs", n)
				}
			} else {
				w := objCall(t, srv, "POST", "/v1/responses", "", `{"model":"gpt-4o","input":"hi"}`, tc.headers)
				if w.Code != http.StatusOK {
					t.Fatalf("POST /v1/responses with %v: status %d body %s", tc.headers, w.Code, w.Body.String())
				}
				respID := mediaDecode(t, w)["id"].(string)
				if stored := h.responsesStore.Get(respID, tc.wantOrg); stored == nil {
					t.Errorf("response created with %v is not stored under org %q", tc.headers, tc.wantOrg)
				}

				asyncHeaders := map[string]string{"x-agentcc-async": "true"}
				for k, v := range tc.headers {
					asyncHeaders[k] = v
				}
				w = objCall(t, srv, "POST", "/v1/chat/completions", "",
					`{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`, asyncHeaders)
				if w.Code != http.StatusAccepted {
					t.Fatalf("async POST /v1/chat/completions with %v: status %d body %s", tc.headers, w.Code, w.Body.String())
				}
				jobID := mediaDecode(t, w)["job_id"].(string)
				if job := h.asyncStore.Get(jobID); job == nil || job.OrgID != tc.wantOrg {
					t.Errorf("async job submitted with %v is not stored under org %q", tc.headers, tc.wantOrg)
				}
			}

			id := uploadFileWith(t, srv, tc.headers, "precedence")
			if stored := h.fileStore.Get(id, tc.wantOrg); stored == nil || stored.OrgID != tc.wantOrg {
				t.Errorf("file uploaded with %v is not stored under org %q", tc.headers, tc.wantOrg)
			}

			noOrgFile := h.fileStore.Upload("", "ops.jsonl", "batch", []byte("ops"))
			h.responsesStore.Put("resp_no_org", "", []byte(`{"id":"resp_no_org"}`))
			h.responsesStore.Put("resp_own", tc.wantOrg, []byte(`{"id":"resp_own"}`))
			expires := time.Now().Add(time.Hour)
			h.asyncStore.Put(&async.Job{ID: "async-no-org", Status: async.StatusCompleted, ExpiresAt: expires})
			h.asyncStore.Put(&async.Job{ID: "async-own", OrgID: tc.wantOrg, Status: async.StatusCompleted, ExpiresAt: expires})

			for path, want := range map[string]int{
				"/v1/files/" + noOrgFile.ID:              http.StatusNotFound,
				"/v1/files/" + noOrgFile.ID + "/content": http.StatusNotFound,
				"/v1/responses/resp_no_org":              http.StatusNotFound,
				"/v1/async/async-no-org":                 http.StatusNotFound,
				"/v1/files/" + id:                        http.StatusOK,
				"/v1/responses/resp_own":                 http.StatusOK,
				"/v1/async/async-own":                    http.StatusOK,
			} {
				w := objCall(t, srv, "GET", path, "", "", tc.headers)
				if w.Code != want {
					t.Errorf("GET %s with %v: status %d, want %d", path, tc.headers, w.Code, want)
				}
			}
		})
	}
}

// With auth off (OSS, single tenant) every request has no org, so every
// object is reachable.
func TestStoredObjects_AuthDisabledIsSingleTenant(t *testing.T) {
	srv := newStoredObjectsServer(t, false)

	fileID := uploadFileAs(t, srv, "")
	if ids := listedIDs(t, objCall(t, srv, "GET", "/v1/files", "", "", nil)); !ids[fileID] {
		t.Errorf("auth off: file list %v does not include %s", ids, fileID)
	}
	for _, req := range []struct{ method, path string }{
		{"GET", "/v1/files/" + fileID},
		{"GET", "/v1/files/" + fileID + "/content"},
		{"DELETE", "/v1/files/" + fileID},
	} {
		if w := objCall(t, srv, req.method, req.path, "", "", nil); w.Code != http.StatusOK {
			t.Errorf("auth off: %s %s status %d, want 200", req.method, req.path, w.Code)
		}
	}

	respID := createResponseAs(t, srv, "")
	for _, method := range []string{"GET", "DELETE"} {
		if w := objCall(t, srv, method, "/v1/responses/"+respID, "", "", nil); w.Code != http.StatusOK {
			t.Errorf("auth off: %s response status %d, want 200", method, w.Code)
		}
	}

	jobID := submitAsyncAs(t, srv, "")
	for _, method := range []string{"GET", "DELETE"} {
		if w := objCall(t, srv, method, "/v1/async/"+jobID, "", "", nil); w.Code != http.StatusOK {
			t.Errorf("auth off: %s async job status %d, want 200", method, w.Code)
		}
	}
}
