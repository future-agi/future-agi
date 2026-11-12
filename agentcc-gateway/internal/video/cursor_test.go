package video

import (
	"testing"
	"time"
)

// The after cursor is positioned by the cursor job itself, not by the filtered
// page, so a job that left the filter since the last page still positions the
// next one; unknown and foreign cursors are rejected.
func TestListCursorSurvivesFilterChanges(t *testing.T) {
	base := time.Unix(1793000000, 0)
	mk := func(id, org, status string, s int) *VideoJob {
		return &VideoJob{ID: id, OrgID: org, Status: status, CreatedAt: base.Add(time.Duration(s) * time.Second)}
	}
	all := []*VideoJob{mk("a", "o1", StatusCompleted, 4), mk("b", "o1", StatusRunning, 3), mk("c", "o1", StatusRunning, 2), mk("d", "o1", StatusRunning, 1), mk("x", "o2", StatusRunning, 5)}
	// "a" was running when page 1 returned it and has completed since.
	page, n, err := filterJobs(all, "o1", VideoListFilters{Status: StatusRunning, Limit: 2, Order: "desc", After: "a"})
	if err != nil || n != 3 || len(page) != 2 || page[0].ID != "b" || page[1].ID != "c" {
		t.Fatalf("desc %v %d %v", ids(page), n, err)
	}
	page, n, err = filterJobs(all, "o1", VideoListFilters{Limit: 10, Order: "asc", After: "c"})
	if err != nil || n != 2 || ids(page) != "ba" {
		t.Fatalf("asc %v %d %v", ids(page), n, err)
	}
	gone := time.Now()
	all = append(all, &VideoJob{ID: "z", OrgID: "o1", Status: StatusRunning, CreatedAt: base, DeletedAt: &gone})
	for _, cursor := range []string{"missing", "x", "z"} {
		if _, _, err = filterJobs(all, "o1", VideoListFilters{Limit: 10, Order: "desc", After: cursor}); err == nil {
			t.Fatalf("cursor %q accepted", cursor)
		}
	}
}
func ids(js []*VideoJob) string {
	s := ""
	for _, j := range js {
		s += j.ID
	}
	return s
}
