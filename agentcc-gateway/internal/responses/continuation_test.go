package responses

import (
	"testing"
	"time"
)

func TestContinuationAuthorityRequiresLiveOrgAndBinding(t *testing.T) {
	s := NewStore(time.Hour)
	s.PutBound("resp", "org-a", "binding-a", []byte(`{"id":"resp"}`))
	for _, tc := range []struct {
		id, org, binding string
		want             bool
	}{
		{"resp", "org-a", "binding-a", true},
		{"resp", "org-b", "binding-a", false},
		{"resp", "", "binding-a", false},
		{"resp", "org-a", "binding-b", false},
		{"resp", "org-a", "", false},
		{"missing", "org-a", "binding-a", false},
	} {
		if got := s.CanContinue(tc.id, tc.org, tc.binding); got != tc.want {
			t.Errorf("CanContinue(%q, %q, %q) = %t; want %t", tc.id, tc.org, tc.binding, got, tc.want)
		}
	}
	// Deterministic expiry without a timing-sensitive sleep.
	s.mu.Lock()
	s.responses[storeKey{orgID: "org-a", id: "resp"}].ExpiresAt = time.Now().Add(-time.Hour)
	s.mu.Unlock()
	if s.CanContinue("resp", "org-a", "binding-a") {
		t.Error("expired record authorized a continuation")
	}
	s.PutBound("resp", "org-a", "binding-a", nil)
	if !s.Delete("resp", "org-a") || s.CanContinue("resp", "org-a", "binding-a") {
		t.Error("deletion did not revoke continuation authority")
	}
	s.Put("resp", "org-a", []byte(`{"id":"resp"}`))
	if s.CanContinue("resp", "org-a", "binding-a") {
		t.Error("legacy unbound record authorized a continuation")
	}
}

func TestStreamingAuthorityDoesNotExposeUncheckedBody(t *testing.T) {
	s := NewStore(time.Hour)
	s.PutBound("resp", "org-a", "binding-a", nil)
	if !s.CanContinue("resp", "org-a", "binding-a") {
		t.Error("created streaming response has no continuation authority")
	}
	if s.Get("resp", "org-a") != nil {
		t.Error("GET exposed an unchecked streaming response")
	}
	s.PutBound("resp", "org-a", "binding-a", []byte(`{"id":"resp"}`))
	if s.Get("resp", "org-a") == nil || !s.CanContinue("resp", "org-a", "binding-a") {
		t.Error("publishing the checked body lost ownership or retrieval")
	}
	// Another provider in another org may legitimately reuse an ID. Neither
	// record may grant access to the other's upstream namespace.
	s.PutBound("resp", "org-b", "binding-b", nil)
	if !s.CanContinue("resp", "org-a", "binding-a") || s.CanContinue("resp", "org-b", "binding-a") {
		t.Error("same-ID response changed another tenant's authority")
	}
}
