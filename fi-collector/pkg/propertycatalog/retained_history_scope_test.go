package propertycatalog

import (
	"testing"
	"time"
)

func TestRetainedHistoryKeepsExactExistingSourceScopeContract(t *testing.T) {
	since := time.Date(2021, 2, 3, 4, 5, 6, 654321000, time.UTC)
	until := time.Date(2026, 9, 7, 12, 0, 0, 0, time.UTC)
	lower, upper := uint64(since.UnixMicro()), uint64(until.UnixMicro())
	projects := []string{testProject}
	if err := validateRevisionSourceScope(projects, lower, upper); err != nil {
		t.Fatal(err)
	}
	if err := validateRevisionSourceObservation(projects, lower, upper, testProject, since, since); err != nil {
		t.Fatal(err)
	}
	if err := validateRevisionSourceObservation(projects, lower, upper, testProject, since.Add(-time.Microsecond), since); err == nil {
		t.Fatal("an event outside the exact lower bound was accepted")
	}
	if err := validateRevisionSourceObservation(projects, lower, upper, testProject, until, until); err == nil {
		t.Fatal("the source upper bound is no longer half-open")
	}
	if err := validateRevisionSourceObservation(projects, lower, upper, testProjectTwo, since, since); err == nil {
		t.Fatal("another project's event was accepted")
	}
	fence := testRevisionFence(17, "building")
	fence.ProjectIDs, fence.SpanSinceUS, fence.SpanUntilUS = projects, lower, upper
	original := RevisionFenceSHA256(fence)
	fence.SpanSinceUS++
	if RevisionFenceSHA256(fence) == original {
		t.Fatal("source lower bound is no longer bound by the existing fence digest")
	}
	fence.SpanSinceUS--
	fence.SpanUntilUS++
	if RevisionFenceSHA256(fence) == original {
		t.Fatal("source upper bound is no longer bound by the existing fence digest")
	}
}
