package server

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

const expectedOrg = "11111111-1111-4111-8111-111111111111"
const expectedWorkspace = "22222222-2222-4222-8222-222222222222"
const expectedProject = "33333333-3333-4333-8333-333333333333"
const otherProject = "44444444-4444-4444-8444-444444444444"

type targetUsage struct{ calls int }

func (u *targetUsage) EmitIngestion(string, int, int, int64, string) { u.calls++ }

func targetResult() *auth.ResolveResult {
	return &auth.ResolveResult{OrgID: expectedOrg, WorkspaceID: expectedWorkspace, Projects: map[string]string{"selected": expectedProject, "other": otherProject}}
}
func targetHeaders() http.Header {
	h := http.Header{}
	h.Set(auth.ExpectedOrganizationHeader, expectedOrg)
	h.Set(auth.ExpectedWorkspaceHeader, expectedWorkspace)
	h.Set(auth.ExpectedProjectHeader, expectedProject)
	return h
}
func targetTraces(names ...string) ptrace.Traces {
	traces := ptrace.NewTraces()
	for i, name := range names {
		rs := traces.ResourceSpans().AppendEmpty()
		if name != "" {
			rs.Resource().Attributes().PutStr("project_name", name)
		}
		// The caller's fi attributes never grant the expected project.
		rs.Resource().Attributes().PutStr("fi.project_id", otherProject)
		rs.Resource().Attributes().PutStr("fi.org_id", "forged-org")
		span := rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
		span.SetTraceID([16]byte{byte(i + 1)})
		span.SetSpanID([8]byte{byte(i + 1)})
		span.SetName("scoped-ingest")
		span.SetStartTimestamp(pcommon.NewTimestampFromTime(time.Now()))
		span.SetEndTimestamp(pcommon.NewTimestampFromTime(time.Now().Add(time.Millisecond)))
	}
	return traces
}

func TestExpectedTargetHTTPRejectsWholeExportBeforeIngestAndBilling(t *testing.T) {
	tests := []struct {
		name    string
		names   []string
		headers func(http.Header)
		result  *auth.ResolveResult
		want    int
	}{
		{"matching", []string{"selected"}, nil, targetResult(), http.StatusOK},
		{"other-org", []string{"selected"}, func(h http.Header) { h.Set(auth.ExpectedOrganizationHeader, "foreign-org") }, targetResult(), http.StatusForbidden},
		{"stale-cached-workspace", []string{"selected"}, func(h http.Header) { h.Set(auth.ExpectedWorkspaceHeader, "new-workspace") }, targetResult(), http.StatusForbidden},
		{"recreated-project", []string{"selected"}, func(h http.Header) { h.Set(auth.ExpectedProjectHeader, otherProject) }, targetResult(), http.StatusForbidden},
		{"missing-project", []string{"unknown"}, nil, targetResult(), http.StatusForbidden},
		{"missing-name", []string{""}, nil, targetResult(), http.StatusForbidden},
		{"mixed-project-batch", []string{"selected", "other"}, nil, targetResult(), http.StatusForbidden},
		{"partial", []string{"selected"}, func(h http.Header) { h.Del(auth.ExpectedProjectHeader) }, targetResult(), http.StatusForbidden},
		{"duplicate", []string{"selected"}, func(h http.Header) { h.Add(auth.ExpectedProjectHeader, expectedProject) }, targetResult(), http.StatusForbidden},
		{"auth-disabled", []string{"selected"}, nil, nil, http.StatusForbidden},
		{"legacy-headers-absent", []string{"selected"}, func(h http.Header) {
			h.Del(auth.ExpectedOrganizationHeader)
			h.Del(auth.ExpectedWorkspaceHeader)
			h.Del(auth.ExpectedProjectHeader)
		}, targetResult(), http.StatusOK},
	}
	for _, ct := range []string{"application/json", "application/x-protobuf"} {
		for _, tc := range tests {
			t.Run(ct+"/"+tc.name, func(t *testing.T) {
				writes := 0
				var stored []byte
				destination := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					writes++
					body, _ := io.ReadAll(r.Body)
					if insertTable(r) == "spans" {
						stored = body
					}
					w.WriteHeader(http.StatusOK)
				}))
				defer destination.Close()
				writer := newSpanTestWriter(t, destination.URL, t.TempDir()+"/deadletter.jsonl")
				defer writer.Close()
				usage := &targetUsage{}
				s := New(Config{}, writer, nil, usage, NoopMetering{})
				traces := ptraceotlp.NewExportRequestFromTraces(targetTraces(tc.names...))
				var body []byte
				var err error
				if ct == "application/json" {
					body, err = traces.MarshalJSON()
				} else {
					body, err = traces.MarshalProto()
				}
				if err != nil {
					t.Fatal(err)
				}
				req := httptest.NewRequest(http.MethodPost, "/tracer/v1/traces", bytes.NewReader(body))
				req.Header = targetHeaders()
				req.Header.Set("Content-Type", ct)
				if tc.headers != nil {
					tc.headers(req.Header)
				}
				if tc.result != nil {
					req = req.WithContext(auth.WithResolvedContext(req.Context(), tc.result, "cached-key"))
				}
				rec := httptest.NewRecorder()
				s.handleHTTPTraces(rec, req)
				s.drainNow(context.Background())
				if rec.Code != tc.want {
					t.Fatalf("status=%d want=%d: %s", rec.Code, tc.want, rec.Body.String())
				}
				if tc.want != http.StatusOK {
					if writes != 0 || len(s.pend) != 0 || usage.calls != 0 {
						t.Fatalf("denied export wrote spans: writes=%d pending=%d billing=%d", writes, len(s.pend), usage.calls)
					}
				} else if writes == 0 || usage.calls != 1 || !bytes.Contains(stored, []byte(expectedProject)) || bytes.Contains(stored, []byte("forged-org")) {
					t.Fatalf("matching export not canonically stored: writes=%d billing=%d body=%s", writes, usage.calls, stored)
				}
			})
		}
	}
}

func TestExpectedTargetPreflightIsReadOnlyVersionedAndFailsClosed(t *testing.T) {
	s := New(Config{}, nil, nil, NoopUsageEmitter{}, NoopMetering{})
	cases := []struct {
		name, query string
		headers     http.Header
		result      *auth.ResolveResult
		want        int
	}{
		{"match", "?project_name=selected", targetHeaders(), targetResult(), 200},
		{"unknown", "?project_name=unknown", targetHeaders(), targetResult(), 403},
		{"wrong-project", "?project_name=other", targetHeaders(), targetResult(), 403},
		{"no-auth", "?project_name=selected", targetHeaders(), nil, 403},
		{"no-headers", "?project_name=selected", http.Header{}, targetResult(), 403},
		{"no-project", "", targetHeaders(), targetResult(), 400},
		{"repeated-project", "?project_name=selected&project_name=other", targetHeaders(), targetResult(), 400},
		{"stale-key", "?project_name=selected", targetHeaders(), &auth.ResolveResult{OrgID: expectedOrg, WorkspaceID: "previous-workspace", Projects: map[string]string{"selected": expectedProject}}, 403},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "/tracer/v1/traces/target"+tc.query, nil)
			req.Header = tc.headers
			if tc.result != nil {
				req = req.WithContext(auth.WithResolvedContext(req.Context(), tc.result, "cached-key"))
			}
			rec := httptest.NewRecorder()
			s.handleHTTPTraceTarget(rec, req)
			if rec.Code != tc.want {
				t.Fatalf("status=%d want=%d: %s", rec.Code, tc.want, rec.Body.String())
			}
			if rec.Header().Get("Cache-Control") != "no-store" {
				t.Fatal("preflight must not be cached")
			}
			if rec.Code == 200 {
				var result map[string]string
				if err := json.Unmarshal(rec.Body.Bytes(), &result); err != nil {
					t.Fatal(err)
				}
				if len(result) != 4 || result["protocol"] != "fi-expected-target/v1" || result["organization_id"] != expectedOrg || result["workspace_id"] != expectedWorkspace || result["project_id"] != expectedProject {
					t.Fatalf("bad preflight: %v", result)
				}
			}
			if len(s.pend) != 0 || len(s.pendProperty) != 0 || len(s.pendRoots) != 0 {
				t.Fatal("preflight queued ingestion")
			}
		})
	}
}

func TestExpectedTargetGRPCMetadataCannotBypassProjectFence(t *testing.T) {
	s := New(Config{}, nil, nil, NoopUsageEmitter{}, NoopMetering{})
	md := metadata.MD{}
	for name, values := range targetHeaders() {
		md.Set(strings.ToLower(name), values...)
	}
	ctx := metadata.NewIncomingContext(auth.WithResolvedContext(context.Background(), targetResult(), "cached-key"), md)
	_, err := (&otlpHandler{s: s}).Export(ctx, ptraceotlp.NewExportRequestFromTraces(targetTraces("selected", "other")))
	if status.Code(err) != codes.PermissionDenied || len(s.pend) != 0 {
		t.Fatalf("gRPC project fence failed: %v pending=%d", err, len(s.pend))
	}
}
