package responses

import (
	"testing"
	"time"
)

func TestStore_PutGet(t *testing.T) {
	s := NewStore(time.Hour)

	body := []byte(`{"id":"resp_1","object":"response","status":"completed"}`)
	s.Put("resp_1", "org-1", body)

	got := s.Get("resp_1", "org-1")
	if got == nil {
		t.Fatal("expected response, got nil")
	}
	if string(got.Body) != string(body) {
		t.Errorf("Body mismatch")
	}
}

func TestStore_OrgIsolation(t *testing.T) {
	s := NewStore(time.Hour)

	s.Put("resp_1", "org-1", []byte(`{"id":"resp_1"}`))

	// Same org — visible.
	if got := s.Get("resp_1", "org-1"); got == nil {
		t.Error("expected response for matching org")
	}

	// Different org — not visible.
	if got := s.Get("resp_1", "org-2"); got != nil {
		t.Error("expected nil for different org")
	}

	// Empty org is the no-org tenant, not a wildcard — not visible.
	if got := s.Get("resp_1", ""); got != nil {
		t.Error("expected nil for the no-org tenant")
	}
}

func TestStore_Delete(t *testing.T) {
	s := NewStore(time.Hour)

	s.Put("resp_1", "org-1", []byte(`{"id":"resp_1"}`))

	// Different org can't delete.
	if s.Delete("resp_1", "org-2") {
		t.Error("expected delete to fail for different org")
	}

	// Correct org can delete.
	if !s.Delete("resp_1", "org-1") {
		t.Error("expected delete to succeed for matching org")
	}

	if got := s.Get("resp_1", "org-1"); got != nil {
		t.Error("expected nil after delete")
	}
}

func TestStore_Expiry(t *testing.T) {
	// Very short TTL.
	s := NewStore(10 * time.Millisecond)

	s.Put("resp_1", "org-1", []byte(`{"id":"resp_1"}`))

	// Should be visible immediately.
	if got := s.Get("resp_1", "org-1"); got == nil {
		t.Error("expected response immediately after put")
	}

	// Wait for expiry.
	time.Sleep(20 * time.Millisecond)

	// Should be expired.
	if got := s.Get("resp_1", "org-1"); got != nil {
		t.Error("expected nil after expiry")
	}
}

func TestStore_CleanExpired(t *testing.T) {
	s := NewStore(10 * time.Millisecond)

	s.Put("resp_1", "org-1", []byte(`{"id":"resp_1"}`))
	s.Put("resp_2", "org-1", []byte(`{"id":"resp_2"}`))

	time.Sleep(20 * time.Millisecond)

	// Add a fresh one.
	s = NewStore(time.Hour)
	s.Put("resp_3", "org-1", []byte(`{"id":"resp_3"}`))

	// Only resp_3 should survive since it was put in a new store with 1h TTL.
	removed := s.CleanExpired()
	if removed != 0 {
		t.Errorf("CleanExpired removed %d in fresh store, want 0", removed)
	}
}

func TestStore_NotFound(t *testing.T) {
	s := NewStore(time.Hour)

	if got := s.Get("nonexistent", "org-1"); got != nil {
		t.Error("expected nil for nonexistent response")
	}

	if s.Delete("nonexistent", "org-1") {
		t.Error("expected delete to return false for nonexistent")
	}
}

// An org id of "" is the no-org tenant (single-tenant or ops keys), not a
// wildcard: it reaches only responses that have no org, and an org never
// reaches another org's responses or the no-org ones.
func TestStore_OrgScopeIsStrict(t *testing.T) {
	s := NewStore(time.Hour)
	s.Put("resp_a", "org-a", []byte(`{"id":"resp_a"}`))
	s.Put("resp_none", "", []byte(`{"id":"resp_none"}`))

	cases := []struct {
		id, caller string
		ok         bool
	}{
		{"resp_a", "org-a", true},
		{"resp_none", "", true},
		{"resp_a", "org-b", false},
		{"resp_a", "", false},
		{"resp_none", "org-a", false},
	}
	for _, tc := range cases {
		if got := s.Get(tc.id, tc.caller); (got != nil) != tc.ok {
			t.Errorf("Get(%s, %q) found=%v, want %v", tc.id, tc.caller, got != nil, tc.ok)
		}
		if !tc.ok && s.Delete(tc.id, tc.caller) {
			t.Errorf("Delete(%s, %q) succeeded, want refused", tc.id, tc.caller)
		}
	}
	if s.Get("resp_a", "org-a") == nil || s.Get("resp_none", "") == nil {
		t.Fatal("a refused delete removed the response")
	}
	if !s.Delete("resp_a", "org-a") || !s.Delete("resp_none", "") {
		t.Error("owners could not delete their own responses")
	}
}

// The response id comes from the upstream provider, which an org can
// configure, so a Put under another org's unexpired id must not replace it.
func TestStore_PutKeepsAnotherOrgsResponse(t *testing.T) {
	s := NewStore(time.Hour)
	s.Put("resp_x", "org-a", []byte(`{"owner":"a"}`))
	s.Put("resp_x", "org-b", []byte(`{"owner":"b"}`))

	if got := s.Get("resp_x", "org-a"); got == nil || string(got.Body) != `{"owner":"a"}` {
		t.Errorf("org A's response was replaced: %+v", got)
	}
	if s.Get("resp_x", "org-b") != nil {
		t.Error("org B took over org A's response id")
	}

	// The same org may store under its own id again.
	s.Put("resp_x", "org-a", []byte(`{"owner":"a","v":2}`))
	if got := s.Get("resp_x", "org-a"); got == nil || string(got.Body) != `{"owner":"a","v":2}` {
		t.Errorf("org A could not replace its own response: %+v", got)
	}
}

// Once another org's response has expired, its id may be reused.
func TestStore_PutReusesAnExpiredId(t *testing.T) {
	s := NewStore(time.Millisecond)
	s.Put("resp_x", "org-a", []byte(`{"owner":"a"}`))
	time.Sleep(5 * time.Millisecond)
	s.Put("resp_x", "org-b", []byte(`{"owner":"b"}`))
	if got := s.Get("resp_x", "org-b"); got == nil {
		t.Error("org B could not reuse an expired id")
	}
}
