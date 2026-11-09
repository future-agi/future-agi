package responses

import (
	"testing"
	"time"
)

// T2 (internal security assessment 1, P3 hardening, RF16): the store held one
// response per id across every org, and Put refused an id another org held, so
// an org whose upstream answered with an id another org's response already
// had could not store or read back its own response (the squatter answered
// with that id first). Each org has its own ids now: the same id stores and
// reads per org, and neither org can see, replace or delete the other's.

func TestStore_SameIdIsStoredPerOrg(t *testing.T) {
	s := NewStore(time.Hour)
	if !s.Put("resp_shared", "org-squatter", []byte(`{"owner":"squatter"}`)) {
		t.Fatal("the first org could not store its response")
	}
	if !s.Put("resp_shared", "org-victim", []byte(`{"owner":"victim"}`)) {
		t.Fatal("the victim could not store its own response under an id another org holds")
	}

	if got := s.Get("resp_shared", "org-victim"); got == nil || string(got.Body) != `{"owner":"victim"}` {
		t.Fatalf("the victim reads %+v, want its own response", got)
	}
	if got := s.Get("resp_shared", "org-squatter"); got == nil || string(got.Body) != `{"owner":"squatter"}` {
		t.Fatalf("the first org reads %+v, want its own response, not the victim's", got)
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
