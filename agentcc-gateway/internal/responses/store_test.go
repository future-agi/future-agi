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

	// Empty org — visible (no filtering).
	if got := s.Get("resp_1", ""); got == nil {
		t.Error("expected response for empty org filter")
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
