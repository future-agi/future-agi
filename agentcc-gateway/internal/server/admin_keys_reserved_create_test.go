package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

// POST /-/keys takes only the reserved key-metadata names the platform
// sets. A key's authority is set once, when it is created, by the
// platform: Django binds org_id to every key it creates, and its guardrail
// playground key also carries allow_policy_override. Those two are settable
// on create. access_groups, role and team come only from config-file keys,
// and type, key_type, id, name and owner are the key's own fields. A create
// that sends any of them is refused with 400 reserved_metadata and no key
// is made.

func createKey(t *testing.T, h *KeyHandlers, payload string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/-/keys", strings.NewReader(payload))
	req.Header.Set("Content-Type", "application/json")
	setAdminAuth(req, "admin-secret")
	rec := httptest.NewRecorder()
	h.CreateKey(rec, req)
	return rec
}

func TestCreateKey_RefusesReservedMetadataThePlatformNeverSets(t *testing.T) {
	for _, name := range []string{"access_groups", "role", "team", "type", "key_type", "id", "name", "owner"} {
		t.Run(name, func(t *testing.T) {
			h := newTestHandlers("admin-secret")
			rec := createKey(t, h, `{"name":"k","metadata":{"org_id":"org-a","`+name+`":"forged"}}`)
			if rec.Code != http.StatusBadRequest {
				t.Fatalf("status %d, want 400; body %s", rec.Code, rec.Body.String())
			}
			var body struct {
				Error struct {
					Code    string `json:"code"`
					Message string `json:"message"`
				} `json:"error"`
			}
			if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
				t.Fatalf("decode: %v; body %s", err, rec.Body.String())
			}
			if body.Error.Code != "reserved_metadata" || !strings.Contains(body.Error.Message, name) {
				t.Errorf("error %+v, want code reserved_metadata naming %q", body.Error, name)
			}
			if n := len(h.keyStore.List()); n != 0 {
				t.Errorf("a refused create made %d key(s), want none", n)
			}
		})
	}
}

// A create carrying several reserved names is refused whole, and nothing
// is stored.
func TestCreateKey_ManyReservedNamesAreRefusedWhole(t *testing.T) {
	h := newTestHandlers("admin-secret")
	rec := createKey(t, h, `{"name":"attacker-key","metadata":{"org_id":"victim-org-123","access_groups":"internal",`+
		`"role":"admin","team":"platform","key_type":"internal","allow_policy_override":"true"}}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status %d, want 400; body %s", rec.Code, rec.Body.String())
	}
	for _, name := range []string{"access_groups", "key_type", "role", "team"} {
		if !strings.Contains(rec.Body.String(), name) {
			t.Errorf("the refusal does not name %s: %s", name, rec.Body.String())
		}
	}
	if n := len(h.keyStore.List()); n != 0 {
		t.Errorf("a refused create made %d key(s), want none", n)
	}
}

// The platform's creates are accepted: Django's provision_key (org_id plus
// the tenant's own labels, reserved names already dropped) and its guardrail
// playground key (org_id, purpose, allow_policy_override).
func TestCreateKey_AcceptsThePlatformsCreates(t *testing.T) {
	for _, tc := range []struct {
		name, metadata string
		want           map[string]string
	}{
		{"provision_key", `{"org_id":"org-a","env":"prod","tenant":"t1"}`,
			map[string]string{"org_id": "org-a", "env": "prod", "tenant": "t1"}},
		{"playground", `{"org_id":"org-a","purpose":"playground-testing","allow_policy_override":"true"}`,
			map[string]string{"org_id": "org-a", "purpose": "playground-testing", "allow_policy_override": "true"}},
		{"no metadata", `{}`, map[string]string{}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			h := newTestHandlers("admin-secret")
			rec := createKey(t, h, `{"name":"k","metadata":`+tc.metadata+`}`)
			if rec.Code != http.StatusCreated {
				t.Fatalf("status %d, want 201; body %s", rec.Code, rec.Body.String())
			}
			keys := h.keyStore.List()
			if len(keys) != 1 {
				t.Fatalf("%d keys, want 1", len(keys))
			}
			got := keys[0].Metadata
			if len(got) != len(tc.want) {
				t.Errorf("metadata %v, want %v", got, tc.want)
			}
			for k, v := range tc.want {
				if got[k] != v {
					t.Errorf("metadata[%s] = %q, want %q", k, got[k], v)
				}
			}
		})
	}
}

// The key sync takes the same two reserved names and drops the other eight,
// on both paths (Django's push to POST /-/keys/sync and the gateway's own
// pull, SyncFromHashes): Django's key payload binds org_id and drops every
// other reserved name before it sends a key, and one key carrying a
// reserved name never fails the whole sync.
func TestImportKeys_DropsReservedMetadataThePlatformNeverSets(t *testing.T) {
	h := newTestHandlers("admin-secret")
	body := `{"keys":[{"id":"key_synced","name":"synced","key_hash":"` + auth.HashKey("sk-agentcc-synced") + `",` +
		`"metadata":{"org_id":"org-a","allow_policy_override":"true","env":"prod","access_groups":"internal",` +
		`"role":"admin","team":"platform","type":"internal","key_type":"internal","id":"x","name":"x","owner":"x"}}]}`
	req := httptest.NewRequest(http.MethodPost, "/-/keys/sync", strings.NewReader(body))
	setAdminAuth(req, "admin-secret")
	rec := httptest.NewRecorder()
	h.ImportKeys(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d, want 200; body %s", rec.Code, rec.Body.String())
	}
	k := h.keyStore.Authenticate("sk-agentcc-synced")
	if k == nil {
		t.Fatal("the synced key was not loaded")
	}
	want := map[string]string{"org_id": "org-a", "allow_policy_override": "true", "env": "prod"}
	if len(k.Metadata) != len(want) {
		t.Errorf("metadata %v, want %v", k.Metadata, want)
	}
	for name, v := range want {
		if k.Metadata[name] != v {
			t.Errorf("metadata[%s] = %q, want %q", name, k.Metadata[name], v)
		}
	}
}

func TestSyncFromHashes_DropsReservedMetadataThePlatformNeverSets(t *testing.T) {
	ks := auth.NewKeyStore(config.AuthConfig{Enabled: true})
	ks.SyncFromHashes([]auth.SyncedKey{{ID: "key_pulled", KeyHash: auth.HashKey("sk-agentcc-pulled"),
		Metadata: map[string]string{"org_id": "org-a", "team": "platform", "role": "admin", "env": "prod"}}})
	k := ks.Authenticate("sk-agentcc-pulled")
	if k == nil {
		t.Fatal("the pulled key was not loaded")
	}
	if len(k.Metadata) != 2 || k.Metadata["org_id"] != "org-a" || k.Metadata["env"] != "prod" {
		t.Errorf("metadata %v, want org_id and env only", k.Metadata)
	}
}
