package scheduled

import (
	"errors"
	"testing"
	"time"
)

// An org id of "" is the no-org tenant (single-tenant or ops keys), not a
// wildcard: it reaches only jobs that have no org, and an org never reaches
// another org's jobs or the no-org ones.

func TestMemoryStore_OrgScopeIsStrict(t *testing.T) {
	s := NewMemoryStore(0)
	now := time.Now()
	for i, j := range []*ScheduledJob{
		{ID: "a1", OrgID: "org-a"},
		{ID: "b1", OrgID: "org-b"},
		{ID: "none1", OrgID: ""},
	} {
		j.Status = StatusPending
		j.CreatedAt = now.Add(time.Duration(i) * time.Second)
		if err := s.Create(j); err != nil {
			t.Fatal(err)
		}
	}

	for orgID, want := range map[string]string{"org-a": "a1", "org-b": "b1", "": "none1"} {
		jobs, err := s.ListByOrg(orgID, "", 10)
		if err != nil {
			t.Fatal(err)
		}
		if len(jobs) != 1 || jobs[0].ID != want {
			t.Errorf("ListByOrg(%q) returned %d jobs, want only %s", orgID, len(jobs), want)
		}
	}

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
		if !tc.ok && (job != nil || !errors.Is(err, ErrJobNotFound)) {
			t.Errorf("GetForOrg(%q, %q) = %v, %v; want ErrJobNotFound and no job", tc.id, tc.orgID, job, err)
		}
	}
}
