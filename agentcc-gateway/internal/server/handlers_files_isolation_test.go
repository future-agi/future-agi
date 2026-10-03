package server

import (
	"net/http/httptest"
	"strings"
	"testing"

	authpkg "github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/files"
)

func twoOrgHandlers() (*Handlers, *files.Store) {
	ks := authpkg.NewKeyStore(config.AuthConfig{
		Enabled: true,
		Keys: []config.AuthKeyConfig{
			{Name: "a", Key: "sk-key-a", Metadata: map[string]string{"org_id": "org-a"}},
			{Name: "b", Key: "sk-key-b", Metadata: map[string]string{"org_id": "org-b"}},
		},
	})
	store := files.NewStore()
	return &Handlers{keyStore: ks, fileStore: store}, store
}

// extractOrgID must return the org that owns the presented key. When it returns
// "" for a real tenant key, every org-scoped resource collapses into one shared
// bucket — the root cause of the /v1/files cross-tenant leak.
func TestExtractOrgIDReturnsTheKeysOrg(t *testing.T) {
	h, _ := twoOrgHandlers()

	r := httptest.NewRequest("GET", "/v1/files", nil)
	r.Header.Set("Authorization", "Bearer sk-key-a")

	if got := extractOrgID(r, h); got != "org-a" {
		t.Errorf("extractOrgID = %q, want %q", got, "org-a")
	}
}

// A file uploaded by one org must be invisible to another: not listed, not
// readable by id, not deletable. This is the leak that shipped when the store
// treated an empty org as a wildcard and extractOrgID always returned "".
func TestFilesAreIsolatedBetweenOrgs(t *testing.T) {
	h, store := twoOrgHandlers()

	// Org A uploads, attributed to whatever extractOrgID resolves for its key.
	rA := httptest.NewRequest("POST", "/v1/files", nil)
	rA.Header.Set("Authorization", "Bearer sk-key-a")
	orgA := extractOrgID(rA, h)
	meta := store.Upload(orgA, "org-a-secrets.jsonl", "batch", []byte("org A's private data"))

	rB := httptest.NewRequest("GET", "/v1/files", nil)
	rB.Header.Set("Authorization", "Bearer sk-key-b")
	orgB := extractOrgID(rB, h)

	// List must not surface it to org B.
	w := httptest.NewRecorder()
	h.ListFiles(w, rB)
	if strings.Contains(w.Body.String(), "org-a-secrets.jsonl") {
		t.Errorf("org B's GET /v1/files returned org A's file: %s", w.Body.String())
	}

	// Direct id lookups must not serve it to org B.
	if f := store.Get(meta.ID, orgB); f != nil {
		t.Errorf("org B read org A's file content: %q", string(f.Content))
	}
	if store.Delete(meta.ID, orgB) {
		t.Errorf("org B deleted org A's file")
	}

	// Sanity: org A still sees its own file — isolation must not over-block.
	wA := httptest.NewRecorder()
	h.ListFiles(wA, rA)
	if !strings.Contains(wA.Body.String(), "org-a-secrets.jsonl") {
		t.Errorf("org A cannot see its own file: %s", wA.Body.String())
	}
}
