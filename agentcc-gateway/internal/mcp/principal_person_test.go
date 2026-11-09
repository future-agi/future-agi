package mcp

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Synthetic UUIDs in the exact form str(User.pk) emits (User.id is a UUIDField).
const (
	mcpPersonU = "01234567-89ab-4cde-8fab-0123456789ab"
	mcpPersonV = "fedcba98-7654-4321-8abc-fedcba987654"
)

func personKeyStore(user string) (*auth.KeyStore, string) {
	const raw = "mcp-person-principal-fixture"
	ks := auth.NewKeyStore(config.AuthConfig{}, true)
	ks.LoadFromHashes([]auth.SyncedKey{{ID: "person-key", KeyHash: auth.HashKey(raw),
		Metadata: map[string]string{"org_id": m8Org, "user_id": user}}})
	return ks, raw
}

func TestKeyPersonReservedIdentity(t *testing.T) {
	for _, tc := range []struct {
		name, owner, want string
		metadata          map[string]string
	}{
		{"sync", mcpPersonV, mcpPersonU, map[string]string{"user_id": mcpPersonU}},
		{"owner-label", mcpPersonU, "", nil},
		{"caller-metadata", mcpPersonU, "", map[string]string{"user": mcpPersonU, "owner_id": mcpPersonU, "mcp_user_id": mcpPersonU}},
		{"legacy-marker", mcpPersonU, "", map[string]string{"mcp_personal": "true"}},
		{"reserved-beats-labels", mcpPersonV, mcpPersonU, map[string]string{"user_id": mcpPersonU, "user": mcpPersonV}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ks := auth.NewKeyStore(config.AuthConfig{})
			metadata := map[string]string{"org_id": m8Org}
			for k, v := range tc.metadata {
				metadata[k] = v
			}
			const raw = "mcp-reserved-fixture"
			ks.LoadFromHashes([]auth.SyncedKey{{ID: "reserved-key", Owner: tc.owner, KeyHash: auth.HashKey(raw), Metadata: metadata}})
			s := newTestServer(t)
			defer s.Close()
			s.SetIdentity(Identity{Keys: ks})
			headers := withHeaders(bearer(raw), "x-agentcc-user-id", mcpPersonV, "x-user-id", mcpPersonV, "x-agentcc-org-id", "org-foreign",
				"x-agentcc-metadata", `{"user_id":"`+mcpPersonV+`","org_id":"org-foreign"}`)
			session := initializeSessionWith(t, s, headers)
			p, w := principalAt(t, s, withHeaders(headers, "MCP-Session-Id", session))
			if w.Code != http.StatusOK || p.Kind != PrincipalKey || p.KeyID != "reserved-key" || p.OrgID != m8Org || p.UserID != tc.want {
				t.Fatalf("status=%d principal=%+v; want user=%q in original org", w.Code, p, tc.want)
			}
		})
	}
}

func TestKeyPersonConfigNeverConfersIdentity(t *testing.T) {
	for _, controlPlane := range []bool{false, true} {
		t.Run(map[bool]string{false: "standalone", true: "control-plane"}[controlPlane], func(t *testing.T) {
			const raw = "mcp-config-person-fixture"
			ks := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{Key: raw, KeyType: "internal", Owner: mcpPersonU,
				Metadata: map[string]string{"org_id": m8Org, "user_id": mcpPersonU}}}}, controlPlane)
			s := newTestServer(t)
			defer s.Close()
			s.SetIdentity(Identity{Keys: ks})
			if controlPlane {
				if ks.Lookup(raw) != nil {
					t.Fatal("F1: config seed carrying a person must not load")
				}
				requireAuthError(t, postMCP(t, s, toolsListMsg(), bearer(raw)), AuthInvalidAPIKey, "config seed")
				return
			}
			session := initializeSessionWith(t, s, bearer(raw))
			p, w := principalAt(t, s, withHeaders(bearer(raw), "MCP-Session-Id", session))
			if w.Code != http.StatusOK || p.Kind != PrincipalKey || p.UserID != "" {
				t.Fatalf("standalone seed acquired person identity: %d %+v", w.Code, p)
			}
		})
	}
}

func TestKeyPersonRejectsNonCanonicalUserID(t *testing.T) {
	for _, value := range []string{"", " ", "\t\n", "user-7", "42", "-", " " + mcpPersonU, mcpPersonU + "\n",
		strings.ToUpper(mcpPersonU), strings.ReplaceAll(mcpPersonU, "-", ""), "{" + mcpPersonU + "}", "urn:uuid:" + mcpPersonU,
		"01234567-89ab-4cde-8fab-0123456789ag"} {
		t.Run(value, func(t *testing.T) {
			ks, raw := personKeyStore(value)
			ia := identityAuth{keys: ks}
			key := ks.Authenticate(raw)
			if key == nil {
				t.Fatal("fixture did not load")
			}
			if p := ia.keyPrincipal(key); p.UserID != "" || p.KeyID != key.ID || p.OrgID != m8Org {
				t.Fatalf("noncanonical user acquired identity: %+v", p)
			}
		})
	}
}

func TestKeyPersonRevocationAtMCPEntry(t *testing.T) {
	for _, mode := range []string{"revoke", "retired", "authoritative-drop"} {
		t.Run(mode, func(t *testing.T) {
			ks, raw := personKeyStore(mcpPersonU)
			s := newTestServer(t)
			defer s.Close()
			s.SetIdentity(Identity{Keys: ks})
			session := initializeSessionWith(t, s, bearer(raw))
			if mode == "revoke" {
				if !ks.Revoke("person-key") {
					t.Fatal("revoke failed")
				}
			} else {
				body := map[string]interface{}{"status": true, "result": []interface{}{},
					"snapshot": map[string]interface{}{"complete": true, "issued_ever": 1}, "config_conflicts": []string{}}
				if mode == "retired" {
					body["retired"] = []map[string]string{{"id": "person-key", "key_hash": auth.HashKey(raw)}}
				}
				var reached atomic.Int32
				cp := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					reached.Add(1)
					if r.URL.Path != "/agentcc/api-keys/bulk/" {
						t.Errorf("unexpected sync route %s", r.URL.Path)
					}
					json.NewEncoder(w).Encode(body)
				}))
				defer cp.Close()
				if err := auth.SyncKeysFromControlPlane(context.Background(), cp.URL, "", ks); err != nil {
					t.Fatal(err)
				}
				if reached.Load() != 1 {
					t.Fatalf("real sync did not reach loopback once: %d", reached.Load())
				}
			}
			cred := withHeaders(bearer(raw), "MCP-Session-Id", session, "x-agentcc-user-id", mcpPersonV)
			p, w := principalAt(t, s, cred)
			requireAuthError(t, w, AuthInvalidAPIKey, mode)
			if p.Kind != "" || p.UserID != "" || p.OrgID != "" || p.KeyID != "" {
				t.Fatalf("revoked key reached the chain with a principal: %+v", p)
			}
			for _, method := range []string{http.MethodGet, http.MethodDelete} {
				requireAuthError(t, mcpRequest(s, method, cred), AuthInvalidAPIKey, mode+" "+method)
			}
		})
	}
}

func TestKeyPersonCrossOrgRemainsBound(t *testing.T) {
	ks, raw := personKeyStore(mcpPersonU)
	s := newTestServer(t)
	defer s.Close()
	s.SetIdentity(Identity{Keys: ks})
	s.Registry().RegisterServer("foreign", []Tool{{Name: "secret", InputSchema: json.RawMessage(`{}`)}})
	m8BindStaticOrg(s, "org-foreign")
	cred := withHeaders(bearer(raw), "x-agentcc-org-id", "org-foreign", "x-agentcc-user-id", mcpPersonV)
	session := initializeSessionWith(t, s, cred)
	p, w := principalAt(t, s, withHeaders(cred, "MCP-Session-Id", session))
	if p.UserID != mcpPersonU || p.OrgID != m8Org {
		t.Fatalf("cross-org header changed identity: %+v", p)
	}
	var res ListToolsResult
	if err := json.Unmarshal(decodeResponse(t, w).Result, &res); err != nil || len(res.Tools) != 0 {
		t.Fatalf("foreign static tools visible: %+v (%v)", res, err)
	}
}

func TestKeyPersonDelegatedIssuance(t *testing.T) {
	// DESIGN-r2 sections 1.2/3: FA verifies the live person before issuing the
	// envelope. Its in-process key is ephemeral, not a public MCP bearer route.
	ks := auth.NewKeyStore(config.AuthConfig{})
	ia := identityAuth{keys: ks}
	claims := auth.EnvelopeClaims{ID: "delegated-fixture", Org: m8Org, User: mcpPersonU, Exp: time.Now().Add(time.Minute).Unix()}
	ctx := auth.WithDelegatedPrincipal(context.Background(), claims, "opaque-fixture")
	key := auth.DelegatedPrincipal(ctx)
	p := ia.keyPrincipal(key)
	if p.Kind != PrincipalKey || p.KeyID != "dlg_"+claims.ID || p.OrgID != m8Org || p.UserID != mcpPersonU {
		t.Fatalf("delegated principal lost its verified person: %+v", p)
	}
	if ks.Count() != 0 || ks.Authenticate(key.ID) != nil {
		t.Fatal("delegated key must never be installed in the public keystore")
	}
	s := newTestServer(t)
	defer s.Close()
	s.SetIdentity(Identity{Keys: ks})
	requireAuthError(t, postMCP(t, s, toolsListMsg(), bearer(key.ID)), AuthInvalidAPIKey, "delegated id is not a bearer")
}

func TestKeyPersonConcurrentUpdate(t *testing.T) {
	ks, raw := personKeyStore(mcpPersonU)
	key := ks.Authenticate(raw)
	ia := identityAuth{keys: ks}
	var wg sync.WaitGroup
	start := make(chan struct{})
	wg.Add(1)
	go func() {
		defer wg.Done()
		<-start
		for i := 0; i < 2000; i++ {
			owner := mcpPersonV
			if _, err := ks.Update(key.ID, nil, &owner, nil, nil, map[string]string{"user": mcpPersonV}); err != nil {
				t.Error(err)
				return
			}
		}
	}()
	close(start)
	for i := 0; i < 2000; i++ {
		p := ia.keyPrincipal(key)
		if p.UserID != mcpPersonU || p.OrgID != m8Org || p.KeyID != key.ID {
			t.Errorf("concurrent metadata update changed identity: %+v", p)
			break
		}
	}
	wg.Wait()
}
