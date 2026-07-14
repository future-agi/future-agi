package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/scheduled"
)

func newScheduledHandlers(t *testing.T) (*Handlers, scheduled.Store) {
	t.Helper()
	store := scheduled.NewMemoryStore(100)
	return &Handlers{scheduledStore: store, scheduledRetryAttempts: 1}, store
}

// A submitted job must carry the caller's credential, or the pipeline rejects it
// when it later runs and its cost is attributed to nobody.
func TestSubmitScheduledCapturesSubmitterCredential(t *testing.T) {
	h, store := newScheduledHandlers(t)

	body := `{"delay":"1h","request":{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}}`
	r := httptest.NewRequest("POST", "/v1/scheduled", strings.NewReader(body))
	r.Header.Set("Authorization", "Bearer sk-agentcc-caller")
	w := httptest.NewRecorder()

	h.SubmitScheduled(w, r)

	if w.Code != http.StatusCreated {
		t.Fatalf("status = %d, body = %s", w.Code, w.Body.String())
	}
	if strings.Contains(w.Body.String(), "sk-agentcc-caller") {
		t.Errorf("the submit response echoed the caller's API key: %s", w.Body.String())
	}

	jobs, err := store.ListByOrg("", "", 10)
	if err != nil || len(jobs) != 1 {
		t.Fatalf("expected 1 stored job, got %d (err=%v)", len(jobs), err)
	}
	if jobs[0].Authorization != "Bearer sk-agentcc-caller" {
		t.Errorf("Authorization = %q, want the submitter's credential", jobs[0].Authorization)
	}
}

// The job must record the submitter's IP so that, when it runs in the
// background, IP ACLs are evaluated against the submitter and not the worker's
// blank address — otherwise an IP-restricted key's jobs always fail to execute.
func TestSubmitScheduledCapturesClientIP(t *testing.T) {
	h, store := newScheduledHandlers(t)

	body := `{"delay":"1h","request":{"model":"gpt-4o"}}`
	r := httptest.NewRequest("POST", "/v1/scheduled", strings.NewReader(body))
	r.Header.Set("Authorization", "Bearer sk-agentcc-caller")
	r.Header.Set("X-Forwarded-For", "203.0.113.7, 10.0.0.1")
	w := httptest.NewRecorder()

	h.SubmitScheduled(w, r)

	jobs, _ := store.ListByOrg("", "", 10)
	if len(jobs) != 1 {
		t.Fatalf("expected 1 stored job, got %d", len(jobs))
	}
	if jobs[0].ClientIP != "203.0.113.7" {
		t.Errorf("ClientIP = %q, want the submitter's forwarded address", jobs[0].ClientIP)
	}
}

// x-api-key is the other accepted spelling; it must be normalized the same way
// the synchronous path normalizes it.
func TestSubmitScheduledAcceptsXAPIKey(t *testing.T) {
	h, store := newScheduledHandlers(t)

	body := `{"delay":"1h","request":{"model":"gpt-4o"}}`
	r := httptest.NewRequest("POST", "/v1/scheduled", strings.NewReader(body))
	r.Header.Set("x-api-key", "sk-agentcc-caller")
	w := httptest.NewRecorder()

	h.SubmitScheduled(w, r)

	jobs, _ := store.ListByOrg("", "", 10)
	if len(jobs) != 1 {
		t.Fatalf("expected 1 stored job, got %d", len(jobs))
	}
	if jobs[0].Authorization != "Bearer sk-agentcc-caller" {
		t.Errorf("Authorization = %q, want the x-api-key normalized to a Bearer credential", jobs[0].Authorization)
	}
}

// A job belonging to another org must be indistinguishable from one that does not
// exist — otherwise job ids can be probed, and their responses read.
func TestGetScheduledJobHidesOtherOrgsJobs(t *testing.T) {
	h, store := newScheduledHandlers(t)

	if err := store.Create(&scheduled.ScheduledJob{
		ID:          "sched-other",
		OrgID:       "org-b",
		Status:      scheduled.StatusCompleted,
		ScheduledAt: time.Now(),
		Request:     json.RawMessage(`{"model":"gpt-4o"}`),
		Response:    json.RawMessage(`{"secret":"org-b's answer"}`),
	}); err != nil {
		t.Fatalf("Create: %v", err)
	}

	// The caller resolves to org "" (no key store wired), so it does not own org-b's job.
	r := httptest.NewRequest("GET", "/v1/scheduled/sched-other", nil)
	w := httptest.NewRecorder()

	h.GetScheduledJob(w, r)

	if w.Code != http.StatusNotFound {
		t.Errorf("status = %d, want 404; body = %s", w.Code, w.Body.String())
	}
	if strings.Contains(w.Body.String(), "org-b's answer") {
		t.Errorf("another org's response leaked: %s", w.Body.String())
	}
}

func TestCancelScheduledJobHidesOtherOrgsJobs(t *testing.T) {
	h, store := newScheduledHandlers(t)

	if err := store.Create(&scheduled.ScheduledJob{
		ID:          "sched-other",
		OrgID:       "org-b",
		Status:      scheduled.StatusPending,
		ScheduledAt: time.Now().Add(time.Hour),
		Request:     json.RawMessage(`{"model":"gpt-4o"}`),
	}); err != nil {
		t.Fatalf("Create: %v", err)
	}

	r := httptest.NewRequest("DELETE", "/v1/scheduled/sched-other", nil)
	w := httptest.NewRecorder()

	h.CancelScheduledJob(w, r)

	if w.Code != http.StatusNotFound {
		t.Errorf("status = %d, want 404", w.Code)
	}
	job, err := store.Get("sched-other")
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if job.Status != scheduled.StatusPending {
		t.Errorf("status = %q: another org cancelled the job", job.Status)
	}
}

// A caller sees its own jobs, and only its own.
func TestListScheduledJobsIsScopedToCaller(t *testing.T) {
	h, store := newScheduledHandlers(t)

	for _, j := range []*scheduled.ScheduledJob{
		{ID: "sched-mine", OrgID: "", Status: scheduled.StatusPending, ScheduledAt: time.Now()},
		{ID: "sched-theirs", OrgID: "org-b", Status: scheduled.StatusPending, ScheduledAt: time.Now()},
	} {
		if err := store.Create(j); err != nil {
			t.Fatalf("Create: %v", err)
		}
	}

	r := httptest.NewRequest("GET", "/v1/scheduled", nil)
	w := httptest.NewRecorder()

	h.ListScheduledJobs(w, r)

	var got struct {
		Data []struct {
			ID string `json:"id"`
		} `json:"data"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
		t.Fatalf("Unmarshal: %v", err)
	}
	for _, j := range got.Data {
		if j.ID == "sched-theirs" {
			t.Errorf("listed another org's job: %s", w.Body.String())
		}
	}
}
