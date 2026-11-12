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

// An org id of "" is the no-org tenant (single-tenant or ops keys), not a
// wildcard: it reaches only files that have no org, and an org never reaches
// another org's files or the no-org ones.
func TestStore_OrgScopeIsStrict(t *testing.T) {
	s := NewStore()
	ids := map[string]string{}
	for _, org := range []string{"org-a", "org-b", ""} {
		ids[org] = s.Upload(org, org+".txt", "batch", []byte(org)).ID
	}

	for _, org := range []string{"org-a", "org-b", ""} {
		files := s.List(org, "")
		if len(files) != 1 || files[0].ID != ids[org] {
			t.Errorf("List(%q) returned %d files, want only %s", org, len(files), ids[org])
		}
	}

	cases := []struct {
		owner, caller string
		ok            bool
	}{
		{"org-a", "org-a", true},
		{"", "", true},
		{"org-a", "org-b", false},
		{"org-a", "", false},
		{"", "org-a", false},
	}
	for _, tc := range cases {
		id := ids[tc.owner]
		if got := s.Get(id, tc.caller); (got != nil) != tc.ok {
			t.Errorf("Get(%s owned by %q, caller %q) found=%v, want %v", id, tc.owner, tc.caller, got != nil, tc.ok)
		}
		if !tc.ok && s.Delete(id, tc.caller) {
			t.Errorf("Delete(%s owned by %q, caller %q) succeeded, want refused", id, tc.owner, tc.caller)
		}
	}
	if s.Count() != 3 {
		t.Errorf("Count = %d after refused deletes, want 3", s.Count())
	}
	if !s.Delete(ids[""], "") || !s.Delete(ids["org-a"], "org-a") {
		t.Error("owners could not delete their own files")
	}
}
