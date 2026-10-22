package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
)

// A key created through the admin API survives a periodic sync that fetched
// its list before the console stored the key (the console commits its row
// about a second after the gateway answers). Revoking that key through the
// admin API still takes effect at once: it stops authenticating immediately,
// and the next sync removes it without waiting out the grace window.
func TestAdminKey_SurvivesRacingSyncAndRevokesAtOnce(t *testing.T) {
	h := newTestHandlers("admin-secret")
	listWithoutNewKey := []auth.SyncedKey{{ID: "key_other", Name: "other", KeyHash: auth.HashKey("sk-agentcc-other")}}

	createReq := httptest.NewRequest(http.MethodPost, "/-/keys", strings.NewReader(`{"name":"console-key"}`))
	setAdminAuth(createReq, "admin-secret")
	createRec := httptest.NewRecorder()
	h.CreateKey(createRec, createReq)
	if createRec.Code != http.StatusCreated {
		t.Fatalf("create: status %d; body: %s", createRec.Code, createRec.Body.String())
	}
	var created struct {
		ID  string `json:"id"`
		Key string `json:"key"`
	}
	if err := json.Unmarshal(createRec.Body.Bytes(), &created); err != nil || created.ID == "" || created.Key == "" {
		t.Fatalf("create response %s: %v", createRec.Body.String(), err)
	}

	h.keyStore.SyncFromHashes(listWithoutNewKey)
	if k := h.keyStore.Authenticate(created.Key); k == nil || k.ID != created.ID {
		t.Fatalf("new key after a sync that missed it = %+v, want it still authenticating", k)
	}

	revokeReq := httptest.NewRequest(http.MethodDelete, "/-/keys/"+created.ID, nil)
	revokeReq.URL.RawQuery = "key_id=" + created.ID
	setAdminAuth(revokeReq, "admin-secret")
	revokeRec := httptest.NewRecorder()
	h.RevokeKey(revokeRec, revokeReq)
	if revokeRec.Code != http.StatusOK {
		t.Fatalf("revoke: status %d; body: %s", revokeRec.Code, revokeRec.Body.String())
	}
	if h.keyStore.Authenticate(created.Key) != nil {
		t.Fatal("revoked key still authenticates")
	}

	h.keyStore.SyncFromHashes(listWithoutNewKey)
	if k := h.keyStore.Get(created.ID); k != nil {
		t.Fatalf("revoked key still in the store after the next sync (status %q): the grace window held it", k.Status)
	}
}

// fanoutPublisher stands in for the Redis revocation channel: every replica's
// subscriber revokes each published key ID in its own store.
type fanoutPublisher struct {
	mu        sync.Mutex
	stores    []*auth.KeyStore
	published []string
}

func (p *fanoutPublisher) Publish(_ context.Context, keyID string) {
	p.mu.Lock()
	p.published = append(p.published, keyID)
	p.mu.Unlock()
	for _, ks := range p.stores {
		ks.Revoke(keyID)
	}
}

// With several replicas, the app's DELETE can reach a replica that has not
// synced a new key yet. That replica broadcasts the revocation, so the
// replica that created the key revokes it at once. The DELETE answers 404:
// this replica did not hold the key.
func TestRevokeKey_UnknownHereStillRevokesOnTheReplicaThatHasIt(t *testing.T) {
	creator := newTestHandlers("admin-secret")
	other := newTestHandlers("admin-secret")
	pub := &fanoutPublisher{stores: []*auth.KeyStore{creator.keyStore, other.keyStore}}
	creator.SetRevocationPublisher(pub)
	other.SetRevocationPublisher(pub)

	created, raw := creator.keyStore.Create("console-key", "", nil, nil, nil)

	revokeReq := httptest.NewRequest(http.MethodDelete, "/-/keys/"+created.ID, nil)
	revokeReq.URL.RawQuery = "key_id=" + created.ID
	setAdminAuth(revokeReq, "admin-secret")
	revokeRec := httptest.NewRecorder()
	other.RevokeKey(revokeRec, revokeReq)

	if revokeRec.Code != http.StatusNotFound {
		t.Fatalf("revoke on a replica without the key: status %d, want 404; body: %s", revokeRec.Code, revokeRec.Body.String())
	}
	if len(pub.published) != 1 || pub.published[0] != created.ID {
		t.Fatalf("published %v, want [%s]", pub.published, created.ID)
	}
	if creator.keyStore.Authenticate(raw) != nil {
		t.Fatal("the replica that created the key still authenticates it after the app revoked it elsewhere")
	}
}
