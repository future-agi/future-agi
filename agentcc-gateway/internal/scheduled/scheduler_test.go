package scheduled

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

// The executor authenticates and bills as the submitter, so the scheduler has to
// hand it the whole job. Passing only the request body is what left scheduled
// executions unauthenticated and their spend attributed to no org.
func TestExecuteJobPassesSubmitterIdentityToExecutor(t *testing.T) {
	store := NewMemoryStore(10)
	job := &ScheduledJob{
		ID:            "sched-1",
		OrgID:         "org-a",
		Authorization: "Bearer sk-agentcc-caller",
		Status:        StatusPending,
		ScheduledAt:   time.Now(),
		Request:       json.RawMessage(`{"model":"gpt-4o"}`),
		Model:         "gpt-4o",
		MaxAttempts:   1,
	}
	if err := store.Create(job); err != nil {
		t.Fatalf("Create: %v", err)
	}

	var got *ScheduledJob
	s := NewScheduler(store, func(j *ScheduledJob) (json.RawMessage, error) {
		got = j
		return json.RawMessage(`{"id":"chatcmpl-1"}`), nil
	}, time.Hour, time.Second, 1)

	s.executeJob(job)

	if got == nil {
		t.Fatal("executor was never called")
	}
	if got.Authorization != "Bearer sk-agentcc-caller" {
		t.Errorf("Authorization = %q, want the submitter's credential", got.Authorization)
	}
	if got.OrgID != "org-a" {
		t.Errorf("OrgID = %q, want org-a", got.OrgID)
	}
}

// The job is serialized straight back to the client by GET /v1/scheduled/{id}.
// Authorization holds a raw API key and must never appear in that response.
func TestScheduledJobNeverSerializesAuthorization(t *testing.T) {
	data, err := json.Marshal(&ScheduledJob{
		ID:            "sched-1",
		OrgID:         "org-a",
		Authorization: "Bearer sk-agentcc-supersecret",
		Status:        StatusPending,
	})
	if err != nil {
		t.Fatalf("Marshal: %v", err)
	}

	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatalf("Unmarshal: %v", err)
	}
	for _, k := range []string{"authorization", "Authorization"} {
		if _, present := raw[k]; present {
			t.Errorf("job serialized an %q key: %s", k, data)
		}
	}
	if strings.Contains(string(data), "sk-agentcc-supersecret") {
		t.Errorf("the caller's API key leaked into the job JSON: %s", data)
	}
}
