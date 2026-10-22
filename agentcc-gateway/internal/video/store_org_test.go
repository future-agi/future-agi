package video

import (
	"errors"
	"testing"
	"time"
)

// TestMemoryStoreOrgMatchIsExact: GetForOrg and ListByOrg match the org
// exactly. A job with no org belongs to the no-org tenant alone, and an
// empty org id lists only that tenant's jobs.
func TestMemoryStoreOrgMatchIsExact(t *testing.T) {
	s := NewMemoryStore()
	now := time.Now()
	for i, org := range []string{"org-a", "org-b", ""} {
		job := &VideoJob{ID: "job-" + org, OrgID: org, Status: StatusQueued, CreatedAt: now.Add(time.Duration(i) * time.Second)}
		if err := s.Create(job); err != nil {
			t.Fatal(err)
		}
	}

	for _, tc := range []struct{ id, org string }{
		{"job-org-b", "org-a"},
		{"job-", "org-a"},
		{"job-org-a", ""},
	} {
		if _, err := s.GetForOrg(tc.id, tc.org); !errors.Is(err, ErrVideoJobForbidden) {
			t.Errorf("GetForOrg(%q, %q) = %v, want ErrVideoJobForbidden", tc.id, tc.org, err)
		}
	}
	if _, err := s.GetForOrg("job-missing", "org-a"); !errors.Is(err, ErrVideoJobNotFound) {
		t.Errorf("GetForOrg on a missing id = %v, want ErrVideoJobNotFound", err)
	}
	for _, org := range []string{"org-a", ""} {
		if job, err := s.GetForOrg("job-"+org, org); err != nil || job.OrgID != org {
			t.Errorf("GetForOrg on org %q's own job = %v, %v", org, job, err)
		}
	}

	for _, org := range []string{"org-a", "org-b", ""} {
		jobs, total, err := s.ListByOrg(org, VideoListFilters{Limit: 10})
		if err != nil || total != 1 || len(jobs) != 1 || jobs[0].ID != "job-"+org {
			t.Errorf("ListByOrg(%q) = %d jobs (total %d), err %v; want only job-%s", org, len(jobs), total, err, org)
		}
	}
}
