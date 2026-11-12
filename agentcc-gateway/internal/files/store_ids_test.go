package files

import (
	"regexp"
	"strconv"
	"strings"
	"testing"
)

// File ids were
// "file-<unix seconds>-<n>" from one counter for the whole process, so any org
// could read from its own upload's id how many files every org had uploaded
// before it, and guess the ids of other orgs' files (Get, List and Delete
// still compare the org exactly, so the guess read nothing). Ids are 96 random
// bits now, as async job ids are.

var fileIDShape = regexp.MustCompile(`^file-[0-9a-f]{24}$`)

func TestUpload_IdDoesNotCountOtherOrgsUploads(t *testing.T) {
	s := NewStore()
	const before = 7
	for i := 0; i < before; i++ {
		s.Upload("org-a", "a.jsonl", "batch", []byte("x"))
	}
	id := s.Upload("org-b", "b.jsonl", "batch", []byte("y")).ID

	if strings.HasSuffix(id, "-"+strconv.Itoa(before+1)) {
		t.Fatalf("org B's file id %q counts the %d files org A uploaded before it", id, before)
	}
	if !fileIDShape.MatchString(id) {
		t.Fatalf("file id %q, want file-<24 hex digits> (96 random bits)", id)
	}
}

func TestUpload_IdsAreRandomAndUnique(t *testing.T) {
	s := NewStore()
	seen := make(map[string]bool, 1000)
	for i := 0; i < 1000; i++ {
		id := s.Upload("org-a", "a.jsonl", "batch", []byte("x")).ID
		if !fileIDShape.MatchString(id) {
			t.Fatalf("file id %q, want file-<24 hex digits>", id)
		}
		if seen[id] {
			t.Fatalf("file id %q issued twice", id)
		}
		seen[id] = true
	}
}
