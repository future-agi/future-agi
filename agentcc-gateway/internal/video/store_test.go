package video

import (
	"testing"
	"time"
)

// An org id of "" is the no-org tenant (single-tenant or ops keys), not a
// wildcard: it reaches only jobs that have no org, and an org never reaches
// another org's jobs or the no-org ones.

func orgScopeStore(t *testing.T) *MemoryStore {
	t.Helper()
	s := NewMemoryStore()
	now := time.Now()
	for i, j := range []*VideoJob{
		{ID: "a1", OrgID: "org-a"},
		{ID: "a2", OrgID: "org-a"},
		{ID: "b1", OrgID: "org-b"},
		{ID: "none1", OrgID: ""},
	} {
		j.Status = StatusQueued
		j.CreatedAt = now.Add(time.Duration(i) * time.Second)
		if err := s.Create(j); err != nil {
			t.Fatal(err)
		}
	}
	return s
}

func TestMemoryStore_ListByOrgIsStrict(t *testing.T) {
	s := orgScopeStore(t)
	for orgID, want := range map[string][]string{
		"org-a": {"a2", "a1"},
		"org-b": {"b1"},
		"":      {"none1"},
		"org-c": nil,
	} {
		jobs, total, err := s.ListByOrg(orgID, VideoListFilters{Limit: 10})
		if err != nil {
			t.Fatal(err)
		}
		var got []string
		for _, j := range jobs {
			got = append(got, j.ID)
		}
		if total != len(want) || len(got) != len(want) {
			t.Errorf("ListByOrg(%q) = %v (total %d), want %v", orgID, got, total, want)
			continue
		}
		for i := range want {
			if got[i] != want[i] {
				t.Errorf("ListByOrg(%q) = %v, want %v", orgID, got, want)
				break
			}
		}
	}
}

func TestMemoryStore_GetForOrgIsStrict(t *testing.T) {
	s := orgScopeStore(t)
	cases := []struct {
		id, orgID string
		ok        bool
	}{
		{"a1", "org-a", true},
		{"none1", "", true},
		{"a1", "org-b", false},
		{"a1", "", false},
		{"none1", "org-a", false},
		{"missing", "org-a", false},
	}
	for _, tc := range cases {
		job, err := s.GetForOrg(tc.id, tc.orgID)
		if tc.ok && (err != nil || job == nil || job.ID != tc.id) {
			t.Errorf("GetForOrg(%q, %q) = %v, %v; want the job", tc.id, tc.orgID, job, err)
		}
		if !tc.ok && (err == nil || job != nil) {
			t.Errorf("GetForOrg(%q, %q) = %v, %v; want an error and no job", tc.id, tc.orgID, job, err)
		}
	}
}
