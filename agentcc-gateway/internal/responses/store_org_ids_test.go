package responses

import (
	"testing"
	"time"
)

// Each org has its own response ids: the same id stores, reads and deletes
// per org, and each org reads and deletes only its own responses.

func TestStore_SameIdIsStoredPerOrg(t *testing.T) {
	s := NewStore(time.Hour)
	if !s.Put("resp_shared", "org-squatter", []byte(`{"owner":"squatter"}`)) {
		t.Fatal("the first org could not store its response")
	}
	if !s.Put("resp_shared", "org-victim", []byte(`{"owner":"victim"}`)) {
		t.Fatal("the second org could not store its own response under an id another org holds")
	}

	if got := s.Get("resp_shared", "org-victim"); got == nil || string(got.Body) != `{"owner":"victim"}` {
		t.Fatalf("the second org reads %+v, want its own response", got)
	}
	if got := s.Get("resp_shared", "org-squatter"); got == nil || string(got.Body) != `{"owner":"squatter"}` {
		t.Fatalf("the first org reads %+v, want its own response, not the second org's", got)
	}
	if s.Get("resp_shared", "") != nil || s.Get("resp_shared", "org-other") != nil {
		t.Error("an org that stored nothing under the id read a response")
	}
}

func TestStore_DeleteIsPerOrgForASharedId(t *testing.T) {
	s := NewStore(time.Hour)
	s.Put("resp_shared", "org-a", []byte(`{"owner":"a"}`))
	s.Put("resp_shared", "org-b", []byte(`{"owner":"b"}`))

	if !s.Delete("resp_shared", "org-a") {
		t.Fatal("org A could not delete its own response")
	}
	if s.Get("resp_shared", "org-a") != nil {
		t.Error("org A's response survived its delete")
	}
	if got := s.Get("resp_shared", "org-b"); got == nil || string(got.Body) != `{"owner":"b"}` {
		t.Fatalf("org A's delete removed org B's response: %+v", got)
	}
	if s.Delete("resp_shared", "org-a") {
		t.Error("a second delete by org A reported a deletion")
	}
}

// Expiry is per org too: one org's expired entry is swept without touching
// another org's live one under the same id.
func TestStore_CleanExpiredIsPerOrgForASharedId(t *testing.T) {
	s := NewStore(time.Hour)
	s.Put("resp_shared", "org-a", []byte(`{"owner":"a"}`))
	s.Put("resp_shared", "org-b", []byte(`{"owner":"b"}`))
	s.mu.Lock()
	for _, r := range s.responses {
		if r.OrgID == "org-a" {
			r.ExpiresAt = time.Now().Add(-time.Second)
		}
	}
	s.mu.Unlock()

	if n := s.CleanExpired(); n != 1 {
		t.Fatalf("CleanExpired removed %d, want 1 (org A's)", n)
	}
	if s.Get("resp_shared", "org-b") == nil {
		t.Error("sweeping org A's expired response removed org B's")
	}
}
