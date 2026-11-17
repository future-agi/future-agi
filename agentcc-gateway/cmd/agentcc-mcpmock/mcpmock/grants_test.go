package mcpmock

import (
	"net/url"
	"testing"
)

func TestRegisteredGrantTypes(t *testing.T) {
	for _, tc := range []struct {
		name    string
		grants  []string
		refresh bool
	}{
		{"default code only", nil, false},
		{"explicit code only", []string{"authorization_code"}, false},
		{"code and refresh", []string{"authorization_code", "refresh_token"}, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			m, srv := testMock(t, "2026", "oauth")
			client := registerClient(t, srv.URL, "none", tc.grants...).ClientID
			verifier, challenge := pkcePair()
			code := authorizeRedirect(t, srv.URL, authQuery(client, m.resourceURI(), challenge, "S256")).Get("code")
			status, out := tokenCall(t, srv.URL, url.Values{"grant_type": {"authorization_code"}, "client_id": {client},
				"code": {code}, "redirect_uri": {testRedirect}, "code_verifier": {verifier}})
			if status != 200 || out["access_token"] == nil {
				t.Fatalf("code exchange = %d, error %v", status, out["error"])
			}
			_, issuedRefresh := out["refresh_token"]
			if issuedRefresh != tc.refresh {
				t.Errorf("refresh issued = %t, want %t", issuedRefresh, tc.refresh)
			}
			m.oauth.mu.Lock()
			stored := len(m.oauth.refresh)
			m.oauth.mu.Unlock()
			if !tc.refresh && stored != 0 {
				t.Error("code-only exchange stored a refresh token")
			}
			// Seed a known token even for code-only clients: redemption must check
			// the registration, not merely assume every stored token is eligible.
			rt := m.oauth.mintRefresh(client, m.resourceURI(), "mcp", defaultSubject, "")
			status, out = tokenCall(t, srv.URL, url.Values{"grant_type": {"refresh_token"}, "client_id": {client}, "refresh_token": {rt}})
			if tc.refresh {
				if status != 200 || out["access_token"] == nil || out["refresh_token"] == nil || out["refresh_token"] == rt {
					t.Errorf("registered refresh = %d, error %v; want rotated tokens", status, out["error"])
				}
			} else {
				if status != 400 || out["error"] != "unauthorized_client" || out["access_token"] != nil {
					t.Errorf("unregistered refresh = %d, error %v; want unauthorized_client", status, out["error"])
				}
				m.oauth.mu.Lock()
				used := m.oauth.refresh[rt].used
				m.oauth.mu.Unlock()
				if used {
					t.Error("unauthorized refresh consumed the token")
				}
			}
		})
	}
}

func TestRefreshOnlyClientRefusesCodeGrant(t *testing.T) {
	m, srv := testMock(t, "2026", "oauth")
	client := registerClient(t, srv.URL, "none", "refresh_token").ClientID
	_, challenge := pkcePair()
	got := authorizeRedirect(t, srv.URL, authQuery(client, m.resourceURI(), challenge, "S256"))
	if got.Get("code") != "" || got.Get("error") != "unauthorized_client" {
		t.Error("refresh-only client authorized a code grant")
	}
	status, out := tokenCall(t, srv.URL, url.Values{"grant_type": {"authorization_code"}, "client_id": {client}, "code": {"unknown-fixture"}})
	if status != 400 || out["error"] != "unauthorized_client" {
		t.Errorf("unregistered code grant = %d, error %v; want unauthorized_client", status, out["error"])
	}
	status, out = tokenCall(t, srv.URL, url.Values{"grant_type": {"password"}, "client_id": {client}})
	if status != 400 || out["error"] != "unsupported_grant_type" {
		t.Errorf("unsupported grant = %d, error %v", status, out["error"])
	}
}
