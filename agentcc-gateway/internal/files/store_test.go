package files

import (
	"testing"
)

func TestStore_Upload(t *testing.T) {
	s := NewStore()

	meta := s.Upload("org-1", "test.txt", "batch", []byte("hello world"))

	if meta.ID == "" {
		t.Error("expected non-empty ID")
	}
	if meta.Object != "file" {
		t.Errorf("Object = %q, want %q", meta.Object, "file")
	}
	if meta.Bytes != 11 {
		t.Errorf("Bytes = %d, want 11", meta.Bytes)
	}
	if meta.Filename != "test.txt" {
		t.Errorf("Filename = %q, want %q", meta.Filename, "test.txt")
	}
	if meta.Purpose != "batch" {
		t.Errorf("Purpose = %q, want %q", meta.Purpose, "batch")
	}
	if meta.Status != "uploaded" {
		t.Errorf("Status = %q, want %q", meta.Status, "uploaded")
	}
}

func TestStore_Get(t *testing.T) {
	s := NewStore()

	meta := s.Upload("org-1", "test.txt", "batch", []byte("content"))

	got := s.Get(meta.ID, "org-1")
	if got == nil {
		t.Fatal("expected file, got nil")
	}
	if string(got.Content) != "content" {
		t.Errorf("Content = %q, want %q", string(got.Content), "content")
	}
}

func TestStore_OrgIsolation(t *testing.T) {
	s := NewStore()

	meta := s.Upload("org-1", "secret.txt", "batch", []byte("secret"))

	// Same org — visible.
	if got := s.Get(meta.ID, "org-1"); got == nil {
		t.Error("expected file for matching org")
	}

	// Different org — not visible.
	if got := s.Get(meta.ID, "org-2"); got != nil {
		t.Error("expected nil for different org")
	}
}

func TestStore_List(t *testing.T) {
	s := NewStore()

	s.Upload("org-1", "a.txt", "batch", []byte("a"))
	s.Upload("org-1", "b.txt", "fine-tune", []byte("b"))
	s.Upload("org-2", "c.txt", "batch", []byte("c"))

	// All files for org-1.
	files := s.List("org-1", "")
	if len(files) != 2 {
		t.Errorf("List(org-1, '') = %d files, want 2", len(files))
	}

	// Filter by purpose.
	files = s.List("org-1", "batch")
	if len(files) != 1 {
		t.Errorf("List(org-1, 'batch') = %d files, want 1", len(files))
	}

	// org-2 files.
	files = s.List("org-2", "")
	if len(files) != 1 {
		t.Errorf("List(org-2, '') = %d files, want 1", len(files))
	}
}

func TestStore_Delete(t *testing.T) {
	s := NewStore()

	meta := s.Upload("org-1", "test.txt", "batch", []byte("data"))

	// Different org can't delete.
	if s.Delete(meta.ID, "org-2") {
		t.Error("expected delete to fail for different org")
	}

	// Same org can delete.
	if !s.Delete(meta.ID, "org-1") {
		t.Error("expected delete to succeed")
	}

	if s.Get(meta.ID, "org-1") != nil {
		t.Error("expected nil after delete")
	}
}

func TestStore_Count(t *testing.T) {
	s := NewStore()

	if s.Count() != 0 {
		t.Errorf("empty store Count = %d", s.Count())
	}

	s.Upload("org-1", "a.txt", "batch", []byte("a"))
	s.Upload("org-1", "b.txt", "batch", []byte("b"))

	if s.Count() != 2 {
		t.Errorf("Count = %d, want 2", s.Count())
	}
}
