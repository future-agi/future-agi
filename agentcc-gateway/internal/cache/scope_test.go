package cache

import (
	"regexp"
	"testing"
	"time"
)

var scopeToken = regexp.MustCompile(`^o[0-9a-f]{32}$`)

func TestOrgScope_NoOrgIsItsOwnToken(t *testing.T) {
	if got := OrgScope(""); got != NoOrgScope {
		t.Fatalf("OrgScope(\"\") = %q, want %q", got, NoOrgScope)
	}
	for _, org := range []string{"noorg", "NoOrg", " ", "0", "o", "11111111-1111-1111-1111-111111111111"} {
		if got := OrgScope(org); got == NoOrgScope {
			t.Errorf("OrgScope(%q) collides with the no-org partition", org)
		}
	}
}

func TestOrgScope_TokensAreDistinctFixedAndAlphanumeric(t *testing.T) {
	orgs := []string{
		"11111111-1111-1111-1111-111111111111",
		"22222222-2222-2222-2222-222222222222",
		"a-b", "b-a", "A-B", "a b", "a|b", "ab",
	}
	seen := map[string]string{}
	for _, org := range orgs {
		tok := OrgScope(org)
		if !scopeToken.MatchString(tok) {
			t.Errorf("OrgScope(%q) = %q, want o + 32 lowercase hex", org, tok)
		}
		if prev, dup := seen[tok]; dup {
			t.Errorf("OrgScope(%q) == OrgScope(%q) == %q", org, prev, tok)
		}
		seen[tok] = org
		if OrgScope(org) != tok {
			t.Errorf("OrgScope(%q) is not deterministic", org)
		}
	}
}

func TestSemanticStore_SearchNeverCrossesScope(t *testing.T) {
	s := NewSemanticStore(0.8, 256, 100)
	vec := Vectorize("what is the capital of France", 256)
	a, b := OrgScope("org-a"), OrgScope("org-b")

	s.Set(a, "k1", vec, "gpt-4o", testResponse("a"), time.Minute)

	if r := s.Search(b, vec, "gpt-4o"); r != nil {
		t.Fatalf("scope b found scope a's entry %q", r.Response.ID)
	}
	if r := s.Search(NoOrgScope, vec, "gpt-4o"); r != nil {
		t.Fatalf("no-org scope found scope a's entry %q", r.Response.ID)
	}
	if r := s.Search(a, vec, "gpt-4o"); r == nil || r.Response.ID != "resp-a" {
		t.Fatalf("scope a lost its own entry: %+v", r)
	}
}

// Dedup is by key within a scope: the same key written under two scopes is
// two entries, and neither overwrites the other.
func TestSemanticStore_DedupStaysInsideScope(t *testing.T) {
	s := NewSemanticStore(0.8, 256, 100)
	vec := Vectorize("hello there", 256)
	a, b := OrgScope("org-a"), OrgScope("org-b")

	s.Set(a, "same-key", vec, "gpt-4o", testResponse("a"), time.Minute)
	s.Set(b, "same-key", vec, "gpt-4o", testResponse("b"), time.Minute)

	if s.Len() != 2 {
		t.Fatalf("Len = %d, want 2 (one entry per scope)", s.Len())
	}
	if r := s.Search(a, vec, "gpt-4o"); r == nil || r.Response.ID != "resp-a" {
		t.Fatalf("scope a: %+v, want its own entry", r)
	}
	if r := s.Search(b, vec, "gpt-4o"); r == nil || r.Response.ID != "resp-b" {
		t.Fatalf("scope b: %+v, want its own entry", r)
	}
}
