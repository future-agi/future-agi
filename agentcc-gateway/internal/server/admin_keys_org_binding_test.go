package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	bindingAdminToken = "binding-admin-secret"
	bindingOrgA       = "org-a-0000"
	bindingOrgB       = "org-b-0000"
)

// newOrgBindingServer runs the whole server (admin routes, key store and
// tenant store) with one key per org, created through POST /-/keys the way
// Django provisions them.
func newOrgBindingServer(t *testing.T) (*Server, map[string]string, map[string]string) {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Admin.Token = bindingAdminToken
	cfg.Auth.Enabled = true
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	tenants := tenant.NewStore()
	tenants.Set(bindingOrgA, &tenant.OrgConfig{})
	tenants.Set(bindingOrgB, &tenant.OrgConfig{})
	ks := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(), ks, nil, nil, nil, testModelDBPtr(), tenants, nil)

	ids, raws := map[string]string{}, map[string]string{}
	for _, org := range []string{bindingOrgA, bindingOrgB} {
		w := bindingCall(t, srv, http.MethodPost, "/-/keys",
			`{"name":"key-`+org+`","metadata":{"org_id":"`+org+`","env":"prod"}}`)
		if w.Code != http.StatusCreated {
			t.Fatalf("create key for %s: %d %s", org, w.Code, w.Body.String())
		}
		var created map[string]any
		if err := json.Unmarshal(w.Body.Bytes(), &created); err != nil {
			t.Fatalf("decode create: %v", err)
		}
		ids[org] = created["id"].(string)
		raws[org] = created["key"].(string)
	}
	return srv, ids, raws
}

func bindingCall(t *testing.T, srv *Server, method, path, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+bindingAdminToken)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// resolvedOrg is the org whose config a request with rawKey resolves, as the
// handlers resolve it before the pipeline runs.
func resolvedOrg(srv *Server, rawKey string) string {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Metadata["authorization"] = "Bearer " + rawKey
	srv.handlers.peekKeyOrgID(rc)
	orgID, cfg := srv.handlers.resolveOrgConfig(rc)
	if cfg == nil {
		return ""
	}
	return orgID
}

func TestUpdateKey_CannotRebindKeyToAnotherOrg(t *testing.T) {
	srv, ids, raws := newOrgBindingServer(t)
	if got := resolvedOrg(srv, raws[bindingOrgA]); got != bindingOrgA {
		t.Fatalf("before: key A resolves org %q, want %q", got, bindingOrgA)
	}

	w := bindingCall(t, srv, http.MethodPut, "/-/keys/"+ids[bindingOrgA],
		`{"name":"renamed","metadata":{"org_id":"`+bindingOrgB+`"}}`)

	if w.Code != http.StatusConflict {
		t.Fatalf("rebind: status %d, want 409; body %s", w.Code, w.Body.String())
	}
	if body := w.Body.String(); !strings.Contains(body, "org_id") {
		t.Errorf("rebind error should name org_id: %s", body)
	}
	if got := resolvedOrg(srv, raws[bindingOrgA]); got != bindingOrgA {
		t.Fatalf("after: key A resolves org %q, want %q", got, bindingOrgA)
	}
	key := srv.keyStore.Get(ids[bindingOrgA])
	if key.Metadata["org_id"] != bindingOrgA || key.Name != "key-"+bindingOrgA {
		t.Errorf("a refused update must change nothing: name=%q metadata=%v", key.Name, key.Metadata)
	}
}

// reservedNamesButOrgID is every reserved metadata name but org_id, which
// TestUpdateKey_CannotRebindKeyToAnotherOrg covers.
// TestReservedNamesButOrgIDMatchTheKeyStore keeps it equal to the key store's
// list.
var reservedNamesButOrgID = []string{
	"access_groups", "allow_policy_override", "id", "key_type", "name", "owner", "role", "team", "type",
}

func TestUpdateKey_CannotSetAuthorityMetadata(t *testing.T) {
	for _, field := range reservedNamesButOrgID {
		t.Run(field, func(t *testing.T) {
			srv, ids, _ := newOrgBindingServer(t)

			w := bindingCall(t, srv, http.MethodPut, "/-/keys/"+ids[bindingOrgA],
				`{"metadata":{"`+field+`":"internal"}}`)

			if w.Code != http.StatusConflict {
				t.Fatalf("status %d, want 409; body %s", w.Code, w.Body.String())
			}
			if want := "metadata " + field + " cannot be changed"; !strings.Contains(w.Body.String(), want) {
				t.Errorf("error should say %q: %s", want, w.Body.String())
			}
			if _, set := srv.keyStore.Get(ids[bindingOrgA]).Metadata[field]; set {
				t.Errorf("metadata.%s was set", field)
			}
		})
	}
}

func TestUpdateKey_KeepsItsOwnOrgAndLabels(t *testing.T) {
	srv, ids, raws := newOrgBindingServer(t)

	// What Django's update_key sends: the labels plus the key's own org_id.
	w := bindingCall(t, srv, http.MethodPut, "/-/keys/"+ids[bindingOrgA],
		`{"name":"renamed","metadata":{"org_id":"`+bindingOrgA+`","env":"staging","service":"infra"}}`)

	if w.Code != http.StatusOK {
		t.Fatalf("status %d, want 200; body %s", w.Code, w.Body.String())
	}
	key := srv.keyStore.Get(ids[bindingOrgA])
	if key.Name != "renamed" || key.Metadata["env"] != "staging" || key.Metadata["service"] != "infra" {
		t.Errorf("update not applied: name=%q metadata=%v", key.Name, key.Metadata)
	}
	if got := resolvedOrg(srv, raws[bindingOrgA]); got != bindingOrgA {
		t.Errorf("key A resolves org %q, want %q", got, bindingOrgA)
	}
}
