package server

import (
	"bytes"
	"context"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/audit"
	"github.com/futureagi/agentcc-gateway/internal/video"
)

func TestAdminVideoRequiresExistingAdminAuth(t *testing.T) {
	f := newVideoHTTP(t)
	for _, tc := range []struct{ method, path, body string }{{"GET", "/-/admin/video/capabilities", ""}, {"POST", "/-/admin/video/killswitch", `{"enabled":true}`}, {"POST", "/-/admin/video/jobs/video_missing/attach", `{"provider_job_id":"cgt-1"}`}, {"GET", "/-/admin/video/unsettled", ""}} {
		for _, key := range []string{"", f.keyA, "wrong-admin"} {
			w := f.request(tc.method, tc.path, key, "", tc.body)
			if w.Code != 401 {
				t.Fatal(tc.path, w.Code, w.Body.String())
			}
		}
	}
	w := f.request("GET", "/-/admin/video/capabilities", "test-admin-token", "", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), "dreamina-seedance-2-5-260628") || strings.Contains(w.Body.String(), "api_key") {
		t.Fatal(w.Code, w.Body.String())
	}
	w = f.request("GET", "/admin/video/capabilities", "test-admin-token", "", "")
	if w.Code != 404 {
		t.Fatal("incorrect admin prefix exposed", w.Code)
	}
}
func TestAdminVideoKillSwitchAndAudit(t *testing.T) {
	f := newVideoHTTP(t)
	var events bytes.Buffer
	f.s.videoAudit = audit.NewLoggerWithSinks([]audit.Sink{audit.NewWriterSink(&events)}, nil, audit.SeverityInfo, 32)
	for _, on := range []string{"true", "false"} {
		w := f.request("POST", "/-/admin/video/killswitch", "test-admin-token", "", `{"enabled":`+on+`}`)
		if w.Code != 200 {
			t.Fatal(w.Code, w.Body.String())
		}
		k, e := f.service.Killed(context.Background())
		if e != nil || k != (on == "true") {
			t.Fatal(k, e)
		}
	}
	for _, body := range []string{`{}`, `{"enabled":true,"unknown":1}`, `{"enabled":"true"}`, `{"enabled":true} {}`} {
		w := f.request("POST", "/-/admin/video/killswitch", "test-admin-token", "", body)
		if w.Code != 400 {
			t.Fatal(w.Code, w.Body.String())
		}
	}
	f.s.videoAudit.Close()
	if !strings.Contains(events.String(), `"action":"video.killswitch"`) || strings.Contains(events.String(), "test-admin-token") {
		t.Fatal(events.String())
	}
}
func seedUnresolved(t *testing.T, f *videoHTTPFixture, terminal bool) string {
	t.Helper()
	id := f.submit(t)
	ctx := context.Background()
	l, e := f.store.Lease(ctx, id, "test", time.Minute)
	if e != nil {
		t.Fatal(e)
	}
	defer f.store.Release(ctx, l)
	j, e := f.store.GetAccounting(ctx, id)
	if e != nil {
		t.Fatal(e)
	}
	j.Phase = video.PhaseCalling
	if e = f.store.Save(ctx, j, l); e != nil {
		t.Fatal(e)
	}
	j.Status = video.StatusSubmissionUnknown
	j.ReconcileState = video.ReconcilePending
	j.ReconcileBy = time.Now().Add(time.Hour)
	if e = f.store.Save(ctx, j, l); e != nil {
		t.Fatal(e)
	}
	if terminal {
		j.Status = video.StatusFailed
		j.Error = &video.VideoError{Code: "submission_unresolved", Message: "submission unresolved"}
		j.SettlementState = video.SettlementUnsettled
		j.ReconcileState = video.ReconcileUnresolved
		if e = f.store.Save(ctx, j, l); e != nil {
			t.Fatal(e)
		}
	}
	return id
}
func TestAdminVideoAttachN7AndUnsettled(t *testing.T) {
	f := newVideoHTTP(t)
	var events bytes.Buffer
	f.s.videoAudit = audit.NewLoggerWithSinks([]audit.Sink{audit.NewWriterSink(&events)}, nil, audit.SeverityInfo, 32)
	for _, terminal := range []bool{false, true} {
		id := seedUnresolved(t, f, terminal)
		w := f.request("POST", "/-/admin/video/jobs/"+id+"/attach", "test-admin-token", "", `{"provider_job_id":"cgt-attached"}`)
		if w.Code != 200 {
			t.Fatal(w.Code, w.Body.String())
		}
		j, e := f.store.GetAccounting(context.Background(), id)
		if e != nil {
			t.Fatal(e)
		}
		want := video.StatusQueued
		if terminal {
			want = video.StatusFailed
		}
		if j.Status != want || j.ProviderJobID != "cgt-attached" || len(j.Artifacts) != 0 {
			t.Fatal(j.Status, j.ProviderJobID)
		}
		if !terminal {
			w = f.request("POST", "/-/admin/video/jobs/"+id+"/attach", "test-admin-token", "", `{"provider_job_id":"cgt-second"}`)
			if w.Code != 409 {
				t.Fatal(w.Code)
			}
		}
	}
	w := f.request("GET", "/-/admin/video/unsettled", "test-admin-token", "", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), "reserved_micros") || strings.Contains(w.Body.String(), "private prompt") {
		t.Fatal(w.Code, w.Body.String())
	}
	id := f.submit(t)
	w = f.request("POST", "/-/admin/video/jobs/"+id+"/attach", "test-admin-token", "", `{"provider_job_id":"cgt-not-unresolved"}`)
	if w.Code != 409 {
		t.Fatal(w.Code, w.Body.String())
	}
	f.s.videoAudit.Close()
	if strings.Count(events.String(), `"action":"video.attach"`) != 2 || strings.Contains(events.String(), "test-admin-token") {
		t.Fatal(events.String())
	}
}
