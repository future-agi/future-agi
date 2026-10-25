package server

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
)

// keyAuthStatus is the status KeyAuth gives a /v1 request made with rawKey.
func keyAuthStatus(srv *Server, rawKey string) int {
	req := httptest.NewRequest(http.MethodGet, "/v1/models", nil)
	req.Header.Set("Authorization", "Bearer "+rawKey)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w.Code
}

// Deleting a key in Django calls DELETE /-/keys/{id}. The key must stop
// working on the next request, not at the next key sync, and a periodic sync
// that read the key set before the delete must not bring it back.
func TestRevokeKey_StopsTheKeyAtOnceAndAStaleSyncKeepsItStopped(t *testing.T) {
	srv, ids, raws := newOrgBindingServer(t)
	a, b := bindingOrgA, bindingOrgB
	if got := keyAuthStatus(srv, raws[a]); got != http.StatusOK {
		t.Fatalf("before: /v1/models with key A = %d, want 200", got)
	}
	// The key set as the gateway's periodic pull read it before the delete.
	var stale []auth.SyncedKey
	for _, org := range []string{a, b} {
		key := srv.keyStore.Get(ids[org])
		stale = append(stale, auth.SyncedKey{
			ID: key.ID, Name: key.Name, KeyHash: key.KeyHash, KeyPrefix: key.KeyPrefix, Metadata: key.Metadata,
		})
	}

	w := bindingCall(t, srv, http.MethodDelete, "/-/keys/"+ids[a], "")
	if w.Code != http.StatusOK {
		t.Fatalf("DELETE /-/keys/{id}: %d %s", w.Code, w.Body.String())
	}

	if got := keyAuthStatus(srv, raws[a]); got != http.StatusUnauthorized {
		t.Errorf("right after the revoke: /v1/models with key A = %d, want 401", got)
	}
	srv.keyStore.SyncFromHashes(stale)
	if got := keyAuthStatus(srv, raws[a]); got != http.StatusUnauthorized {
		t.Errorf("after a stale key sync: /v1/models with key A = %d, want 401", got)
	}
	if got := keyAuthStatus(srv, raws[b]); got != http.StatusOK {
		t.Errorf("key B = %d, want 200: only the revoked key may stop", got)
	}

	// Django treats 404 as "nothing to revoke here" when it deletes a key.
	if w := bindingCall(t, srv, http.MethodDelete, "/-/keys/key_never_issued", ""); w.Code != http.StatusNotFound {
		t.Errorf("DELETE of an unknown key: %d, want 404", w.Code)
	}
}
