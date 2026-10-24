package async

import (
	"testing"
	"time"
)

func TestStore_PutGet(t *testing.T) {
	s := NewStore()

	job := &Job{
		ID:        "job-1",
		Status:    StatusQueued,
		OrgID:     "org-1",
		CreatedAt: time.Now(),
		ExpiresAt: time.Now().Add(time.Hour),
	}
	s.Put(job)

	got := s.Get("job-1")
	if got == nil {
		t.Fatal("expected job, got nil")
	}
	if got.ID != "job-1" {
		t.Errorf("ID = %q, want %q", got.ID, "job-1")
	}
}

func TestStore_GetForOrg(t *testing.T) {
	s := NewStore()

	job := &Job{
		ID:        "job-1",
		Status:    StatusQueued,
		OrgID:     "org-1",
		CreatedAt: time.Now(),
		ExpiresAt: time.Now().Add(time.Hour),
	}
	s.Put(job)

	// Same org — should find it.
	got := s.GetForOrg("job-1", "org-1")
	if got == nil {
		t.Fatal("expected job for matching org, got nil")
	}

	// Different org — should not find it.
	got = s.GetForOrg("job-1", "org-2")
	if got != nil {
		t.Error("expected nil for different org, got job")
	}

	// Empty caller scope must not read an owned job.
	got = s.GetForOrg("job-1", "")
	if got != nil {
		t.Error("expected nil for empty caller scope, got job")
	}
}

func TestStore_GetForOrgEmptyOwner(t *testing.T) {
	s := NewStore()
	s.Put(&Job{
		ID:        "job-empty",
		Status:    StatusQueued,
		OrgID:     "",
		CreatedAt: time.Now(),
		ExpiresAt: time.Now().Add(time.Hour),
	})

	if got := s.GetForOrg("job-empty", "org-1"); got != nil {
		t.Error("empty stored owner must not match a caller scope")
	}
	if got := s.GetForOrg("job-empty", ""); got != nil {
		t.Error("empty stored owner must not match an empty caller scope")
	}
}

func TestStore_Delete(t *testing.T) {
	s := NewStore()

	job := &Job{
		ID:        "job-1",
		Status:    StatusQueued,
		CreatedAt: time.Now(),
		ExpiresAt: time.Now().Add(time.Hour),
	}
	s.Put(job)

	if !s.Delete("job-1") {
		t.Error("expected delete to return true")
	}
	if s.Get("job-1") != nil {
		t.Error("expected nil after delete")
	}
	if s.Delete("job-1") {
		t.Error("expected second delete to return false")
	}
}

func TestStore_CleanExpired(t *testing.T) {
	s := NewStore()

	// Add an expired job.
	s.Put(&Job{
		ID:        "expired",
		Status:    StatusCompleted,
		CreatedAt: time.Now().Add(-2 * time.Hour),
		ExpiresAt: time.Now().Add(-time.Hour),
	})

	// Add a non-expired job.
	s.Put(&Job{
		ID:        "active",
		Status:    StatusQueued,
		CreatedAt: time.Now(),
		ExpiresAt: time.Now().Add(time.Hour),
	})

	removed := s.CleanExpired()
	if removed != 1 {
		t.Errorf("CleanExpired removed %d, want 1", removed)
	}
	if s.Count() != 1 {
		t.Errorf("Count = %d, want 1", s.Count())
	}
	if s.Get("active") == nil {
		t.Error("active job should still exist")
	}
}

func TestStore_CancelForOrgDoesNotTouchOtherOrg(t *testing.T) {
	s := NewStore()
	called := false
	s.Put(&Job{
		ID:     "job-1",
		Status: StatusProcessing,
		OrgID:  "org-1",
		CancelFn: func() {
			called = true
		},
	})
	if s.CancelForOrg("job-1", "org-2") {
		t.Fatal("other org must not cancel")
	}
	if called {
		t.Fatal("other org invoked CancelFn")
	}
	got := s.Get("job-1")
	if got == nil || got.Status != StatusProcessing {
		t.Fatalf("job changed: %+v", got)
	}
	if !s.CancelForOrg("job-1", "org-1") {
		t.Fatal("owner cancel failed")
	}
	if !called {
		t.Fatal("owner cancel did not invoke live CancelFn")
	}
	if s.Get("job-1") != nil {
		t.Fatal("job still present")
	}
}

func TestStore_Count(t *testing.T) {
	s := NewStore()

	if s.Count() != 0 {
		t.Errorf("empty store count = %d, want 0", s.Count())
	}

	s.Put(&Job{ID: "a", ExpiresAt: time.Now().Add(time.Hour)})
	s.Put(&Job{ID: "b", ExpiresAt: time.Now().Add(time.Hour)})

	if s.Count() != 2 {
		t.Errorf("Count = %d, want 2", s.Count())
	}
}
