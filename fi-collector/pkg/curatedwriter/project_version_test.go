package curatedwriter

import (
	"testing"
	"time"
)

func TestTraceRowCarriesResolvedProjectVersion(t *testing.T) {
	const versionID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
	for _, version := range []string{versionID, ""} {
		row := traceRow(Trace{ID: "trace", ProjectID: "project", ProjectVersionID: version}, time.Unix(1700000000, 0))
		got, present := row["project_version_id"]
		if version == "" {
			if present {
				t.Fatalf("unset project version must be omitted, got %v", got)
			}
		} else if !present || got != version {
			t.Fatalf("trace project_version_id = %v, want %s", got, version)
		}
	}
}
