package clickhouse25exporter

import (
	"context"
	"strings"
	"testing"
)

func TestProjectVersionColumnUsesResolvedResourceStamp(t *testing.T) {
	const versionID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
	for _, tc := range []struct {
		name, stamp string
		want        any
	}{
		{"resolved", strings.ToUpper(versionID), versionID},
		{"raw input is not a resolved version", "", nil},
		{"invalid stamp", "not-a-uuid", nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			traces := buildOTLPSpan()
			attrs := traces.ResourceSpans().At(0).Resource().Attributes()
			attrs.PutStr("project_version_id", versionID)
			if tc.stamp != "" {
				attrs.PutStr("fi.project_version_id", tc.stamp)
			}
			rows, _, err := ConvertWithIdentities(context.Background(), traces, nil)
			if err != nil || len(rows) != 1 {
				t.Fatalf("convert: rows=%d err=%v", len(rows), err)
			}
			if got := rows[0]["project_version_id"]; got != tc.want {
				t.Fatalf("project_version_id = %v, want %v", got, tc.want)
			}
			resource := rows[0]["resource_attrs"].(map[string]any)
			if resource["project_version_id"] != versionID {
				t.Fatal("raw resource input was not retained")
			}
		})
	}
}
