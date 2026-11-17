package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Both organizations deliberately use the same upstream account. The upstream
// accepts any previous_response_id, so the gateway must enforce ownership
// before dispatch rather than relying on the upstream credential to isolate it.
func newContinuationGateway(t *testing.T, authEnabled bool) (*Server, *atomic.Int64) {
	t.Helper()
	return newContinuationGatewayWithStreamHook(t, authEnabled, nil)
}

func newContinuationGatewayWithStreamHook(t *testing.T, authEnabled bool, afterCreated func(http.ResponseWriter, string)) (*Server, *atomic.Int64) {
	t.Helper()
	calls := &atomic.Int64{}
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Registry connectivity checks have no Responses request body and do
		// not count as dispatches for the continuation authorization tests.
		if r.Method == http.MethodGet && r.URL.Path == "/v1/models" {
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, `{"data":[]}`)
			return
		}
		var req struct {
			Stream     bool `json:"stream"`
			Background bool `json:"background"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Error(err)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		id := fmt.Sprintf("resp_cont_%d", calls.Add(1))
		// Omit store, as compatible providers may do: the request's store:false
		// must still prevent the gateway from authorizing a continuation.
		response := fmt.Sprintf(`{"id":%q,"object":"response","model":"gpt-4o","status":"completed","output":[],"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}`, id)
		if req.Stream {
			w.Header().Set("Content-Type", "text/event-stream")
			fmt.Fprintf(w, "event: response.created\ndata: {\"type\":\"response.created\",\"response\":{\"id\":%q}}\n\n", id)
			w.(http.Flusher).Flush()
			if afterCreated != nil {
				afterCreated(w, id)
			}
			if !req.Background {
				fmt.Fprintf(w, "event: response.completed\ndata: {\"type\":\"response.completed\",\"response\":%s}\n\n", response)
			}
			return
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, response)
	}))
	t.Cleanup(up.Close)
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
		BaseURL: up.URL, APIKey: "shared-upstream-fixture", APIFormat: "openai", Models: []string{"gpt-4o"},
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	for _, org := range []string{objOrgA, objOrgB} {
		tenants.Set(org, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
			"openai": {Enabled: true, APIKey: "shared-upstream-fixture", Models: []string{"gpt-4o"}},
		}})
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenants, nil)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	return srv, calls
}

func continuationBody(id string, stream bool) string {
	return fmt.Sprintf(`{"model":"gpt-4o","input":"repeat the earlier private sentence","previous_response_id":%q,"stream":%t}`, id, stream)
}

func TestResponsesContinuationRejectsForeignContextBeforeDispatch(t *testing.T) {
	for _, tc := range crossOrgCases {
		for _, originalStream := range []bool{false, true} {
			for _, continuationStream := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/original-stream=%t/continuation-stream=%t", tc.name, originalStream, continuationStream), func(t *testing.T) {
					srv, calls := newContinuationGateway(t, true)
					body := fmt.Sprintf(`{"model":"gpt-4o","input":"private sentence belonging only to the owner","store":true,"stream":%t}`, originalStream)
					if w := objCall(t, srv, "POST", "/v1/responses", tc.owner, body, nil); w.Code != http.StatusOK {
						t.Fatalf("create: %d %s", w.Code, w.Body.String())
					}
					if w := objCall(t, srv, "GET", "/v1/responses/resp_cont_1", tc.intruder, "", nil); w.Code != http.StatusNotFound {
						t.Fatalf("foreign GET: %d", w.Code)
					}
					before := calls.Load()
					w := objCall(t, srv, "POST", "/v1/responses", tc.intruder, continuationBody("resp_cont_1", continuationStream), nil)
					if w.Code != http.StatusNotFound || !strings.Contains(w.Body.String(), "response_not_found") {
						t.Errorf("foreign continuation: %d %s; want 404 response_not_found", w.Code, w.Body.String())
					}
					if calls.Load() != before {
						t.Error("foreign continuation reached the shared upstream account")
					}
					w = objCall(t, srv, "POST", "/v1/responses", tc.owner, continuationBody("resp_cont_1", continuationStream), nil)
					if w.Code != http.StatusOK || calls.Load() != before+1 {
						t.Errorf("owner continuation: %d %s; want one upstream call", w.Code, w.Body.String())
					}
				})
			}
		}
	}
}

func TestResponsesContinuationRejectsUnownedOrUnstoredContext(t *testing.T) {
	for _, originalStream := range []bool{false, true} {
		for _, reason := range []string{"unknown", "deleted", "store-false", "unbound", "store-unavailable"} {
			t.Run(fmt.Sprintf("%s/stream=%t", reason, originalStream), func(t *testing.T) {
				srv, calls := newContinuationGateway(t, true)
				if reason != "unknown" {
					body := fmt.Sprintf(`{"model":"gpt-4o","input":"private","stream":%t,"store":%t}`, originalStream, reason != "store-false")
					if w := objCall(t, srv, "POST", "/v1/responses", objKeyA, body, nil); w.Code != http.StatusOK {
						t.Fatalf("create: %d %s", w.Code, w.Body.String())
					}
				}
				switch reason {
				case "deleted":
					objCall(t, srv, "DELETE", "/v1/responses/resp_cont_1", objKeyA, "", nil)
				case "unbound":
					srv.handlers.responsesStore.Put("resp_cont_1", objOrgA, []byte(`{"id":"resp_cont_1"}`))
				case "store-unavailable":
					srv.handlers.responsesStore = nil
				}
				for _, stream := range []bool{false, true} {
					before := calls.Load()
					w := objCall(t, srv, "POST", "/v1/responses", objKeyA, continuationBody("resp_cont_1", stream), nil)
					if w.Code != http.StatusNotFound || calls.Load() != before {
						t.Errorf("%s continuation: %d %s, upstream calls %d; want 404 without dispatch", reason, w.Code, w.Body.String(), calls.Load()-before)
					}
				}
			})
		}
	}
}

func TestResponsesContinuationBindsTheActualProviderCredential(t *testing.T) {
	srv, calls := newContinuationGateway(t, true)
	createResponseAs(t, srv, objKeyA)
	srv.TenantStore.Set(objOrgA, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {Enabled: true, APIKey: "different-upstream-fixture", Models: []string{"gpt-4o"}},
	}})
	srv.OrgProviderCache.Evict(objOrgA)
	for _, stream := range []bool{false, true} {
		before := calls.Load()
		w := objCall(t, srv, "POST", "/v1/responses", objKeyA, continuationBody("resp_cont_1", stream), nil)
		if w.Code != http.StatusNotFound || calls.Load() != before {
			t.Errorf("credential changed under same provider id: %d %s; want 404 without dispatch", w.Code, w.Body.String())
		}
	}
}

func TestResponsesContinuationReadsExactWireField(t *testing.T) {
	srv, calls := newContinuationGateway(t, true)
	createResponseAs(t, srv, objKeyA)
	// encoding/json's struct matcher accepts the uppercase alias, whereas
	// the upstream resolves the exact lowercase JSON field. The alias must
	// not hide the foreign reference actually forwarded to the provider.
	body := `{"model":"gpt-4o","input":"private","previous_response_id":"resp_cont_1","PREVIOUS_RESPONSE_ID":""}`
	before := calls.Load()
	w := objCall(t, srv, "POST", "/v1/responses", objKeyB, body, nil)
	if w.Code != http.StatusNotFound || calls.Load() != before {
		t.Errorf("wire-field continuation: %d %s; want 404 without dispatch", w.Code, w.Body.String())
	}
}

func TestResponsesContinuationTracksCreatedStreamBeforeCompletion(t *testing.T) {
	srv, calls := newContinuationGateway(t, true)
	w := objCall(t, srv, "POST", "/v1/responses", objKeyA, `{"model":"gpt-4o","input":"private","stream":true,"background":true}`, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("background stream: %d %s", w.Code, w.Body.String())
	}
	// There is no completed event, but the admitted stored ID is already
	// visible to the caller and must have its ownership binding recorded.
	for _, key := range []string{objKeyB, objKeyA} {
		before := calls.Load()
		w = objCall(t, srv, "POST", "/v1/responses", key, continuationBody("resp_cont_1", false), nil)
		if key == objKeyB {
			if w.Code != http.StatusNotFound || calls.Load() != before {
				t.Error("foreign background continuation was not rejected before dispatch")
			}
		} else if w.Code != http.StatusOK || calls.Load() != before+1 {
			t.Errorf("owner background continuation: %d %s", w.Code, w.Body.String())
		}
	}
}

func TestResponsesContinuationKeepsAuthDisabledOwner(t *testing.T) {
	srv, calls := newContinuationGateway(t, false)
	createResponseAs(t, srv, "")
	before := calls.Load()
	w := objCall(t, srv, "POST", "/v1/responses", "", continuationBody("resp_cont_1", false), nil)
	if w.Code != http.StatusOK || calls.Load() != before+1 {
		t.Errorf("single-tenant continuation: %d %s", w.Code, w.Body.String())
	}
}
