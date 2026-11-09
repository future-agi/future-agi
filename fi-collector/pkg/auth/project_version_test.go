package auth

import (
	"bytes"
	"context"
	"errors"
	"log/slog"
	"strings"
	"testing"
	"time"

	chexp "github.com/future-agi/future-agi/fi-collector/exporter/clickhouse25exporter"
	"go.opentelemetry.io/collector/pdata/pcommon"
)

const testVersionID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

type versionRecordingResolver struct {
	*typedRecordingResolver
	id     string
	err    error
	calls  [][5]string
	during func()
}

func (r *versionRecordingResolver) GetOrCreateProjectVersion(_ context.Context, org, workspace, project, id, name string) (string, error) {
	r.calls = append(r.calls, [5]string{org, workspace, project, id, name})
	if r.during != nil {
		r.during()
	}
	return r.id, r.err
}

func versionAuth(projectType string) (*Authenticator, *ResolveResult, string, *versionRecordingResolver) {
	r := &versionRecordingResolver{
		typedRecordingResolver: &typedRecordingResolver{known: map[string]ResolvedProject{
			"versioned": {ID: routeProdID, TraceType: projectType},
		}},
		id: testVersionID,
	}
	a, result, key := newTypedAuth(r)
	return a, result, key, r
}

// This exercises the production stamp and converter together, not a copy of
// their row mappings. On the base neither spans nor root traces carry a version.
func TestProjectVersionStampReachesSpansAndRootTrace(t *testing.T) {
	a, result, key, r := versionAuth("experiment")
	traces := routeTraces("versioned")
	rs := traces.ResourceSpans().At(0)
	attrs := rs.Resource().Attributes()
	attrs.PutStr("project_version_id", strings.ToUpper(testVersionID))
	attrs.PutStr("project_version_name", "candidate")
	attrs.PutStr("fi.project_version_id", routeHiddenID)
	spans := rs.ScopeSpans().At(0).Spans()
	for i := 0; i < spans.Len(); i++ {
		spans.At(i).SetStartTimestamp(pcommon.NewTimestampFromTime(time.Unix(1700000000, 0)))
		spans.At(i).SetEndTimestamp(pcommon.NewTimestampFromTime(time.Unix(1700000001, 0)))
	}
	spans.At(1).SetParentSpanID(spans.At(0).SpanID())
	if dropped, err := StampResourceAttrs(context.Background(), a, key, traces, result); err != nil || dropped != 0 {
		t.Fatalf("stamp: dropped=%d err=%v", dropped, err)
	}
	wantCall := [5]string{result.OrgID, result.WorkspaceID, routeProdID, strings.ToUpper(testVersionID), "candidate"}
	if len(r.calls) != 1 || r.calls[0] != wantCall {
		t.Fatalf("resolver calls = %v, want %v", r.calls, wantCall)
	}
	if got := getStrAttr(attrs, "fi.project_version_id"); got != testVersionID {
		t.Fatalf("resolved stamp = %q", got)
	}
	rows, batch, err := chexp.ConvertWithIdentities(context.Background(), traces, nil)
	if err != nil || len(rows) != 2 {
		t.Fatalf("convert: rows=%d err=%v", len(rows), err)
	}
	for _, row := range rows {
		if row["project_version_id"] != testVersionID {
			t.Errorf("span version = %v, want %s", row["project_version_id"], testVersionID)
		}
	}
	if got := batch.Traces(); len(got) != 1 || got[0].ProjectVersionID != testVersionID {
		t.Fatalf("trace version = %+v", got)
	}
}

func TestProjectVersionStampSkipsObserveAndMissingInput(t *testing.T) {
	for _, tc := range []struct{ name, projectType, input string }{
		{"observe despite sender type", "observe", testVersionID},
		{"no version input", "experiment", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			a, result, key, r := versionAuth(tc.projectType)
			attrs := stampOne(t, a, key, result, "versioned", func(attrs pcommon.Map) {
				attrs.PutStr("project_type", "experiment")
				attrs.PutStr("fi.project_version_id", testVersionID)
				if tc.input != "" {
					attrs.PutStr("project_version_id", tc.input)
				}
			})
			if _, present := attrs.Get("fi.project_version_id"); present || len(r.calls) != 0 {
				t.Fatalf("unexpected version stamp or lookup: attrs=%v calls=%v", attrs.AsRaw(), r.calls)
			}
		})
	}
}

func TestProjectVersionNameOnlyAndUnknownTypeUseScopedResolver(t *testing.T) {
	a, result, key, r := versionAuth("")
	attrs := stampOne(t, a, key, result, "versioned", func(attrs pcommon.Map) {
		attrs.PutStr("project_version_name", "same name")
	})
	if getStrAttr(attrs, "fi.project_version_id") != testVersionID || len(r.calls) != 1 || r.calls[0][3] != "" || r.calls[0][4] != "same name" {
		t.Fatalf("name-only resolution: attrs=%v calls=%v", attrs.AsRaw(), r.calls)
	}
}

func TestProjectVersionIgnoredHasValueFreeLogAndNoStamp(t *testing.T) {
	a, result, key, r := versionAuth("experiment")
	r.id, r.err = "", ErrProjectVersionIgnored
	var logs bytes.Buffer
	a.log = slog.New(slog.NewJSONHandler(&logs, nil))
	attrs := stampOne(t, a, key, result, "versioned", func(attrs pcommon.Map) {
		attrs.PutStr("project_version_id", testVersionID)
		attrs.PutStr("project_version_name", "private-version-name")
		attrs.PutStr("fi.project_version_id", routeHiddenID)
	})
	if _, present := attrs.Get("fi.project_version_id"); present {
		t.Fatal("ignored version was stamped")
	}
	if !strings.Contains(logs.String(), "project version ignored") {
		t.Fatal("missing value-free ignored-version diagnostic")
	}
	for _, value := range []string{testVersionID, "private-version-name", result.OrgID, result.WorkspaceID, routeProdID} {
		if strings.Contains(logs.String(), value) {
			t.Fatalf("version diagnostic contains request or tenant value")
		}
	}
}

func TestProjectVersionNumberMatchesLegacy(t *testing.T) {
	for _, tc := range []struct{ previous, want string }{
		{"", "v1"}, {"v1", "v2"}, {"v9", "v10"}, {"custom", "custom_v2"}, {"custom_v2", "custom_v3"},
	} {
		if got, err := nextProjectVersionNumber(tc.previous); err != nil || got != tc.want {
			t.Errorf("next(%q) = %q, %v; want %q", tc.previous, got, err, tc.want)
		}
	}
	for _, previous := range []string{"v999999999", "custom_vx"} {
		if _, err := nextProjectVersionNumber(previous); !errors.Is(err, ErrProjectVersionIgnored) {
			t.Errorf("invalid/oversized next(%q): %v", previous, err)
		}
	}
}

func TestProjectVersionLookupFailureAndAuthReplacementAreRetryable(t *testing.T) {
	for _, replacement := range []bool{false, true} {
		a, result, key, r := versionAuth("experiment")
		if replacement {
			r.during = func() { a.InvalidateKey(key) }
		} else {
			r.err = errors.New("database unavailable")
		}
		traces := routeTraces("versioned")
		attrs := traces.ResourceSpans().At(0).Resource().Attributes()
		attrs.PutStr("project_version_id", testVersionID)
		attrs.PutStr("fi.project_version_id", routeHiddenID)
		_, err := StampResourceAttrs(context.Background(), a, key, traces, result)
		if !errors.Is(err, ErrProjectResolutionUnavailable) {
			t.Fatalf("replacement=%v: expected retryable failure, got %v", replacement, err)
		}
		if _, present := attrs.Get("fi.project_version_id"); present {
			t.Fatal("failed lookup published a version stamp")
		}
	}
}
