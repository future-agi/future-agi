package async

import (
	"context"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func newTestJob(id, orgID string, exec Executor) *Job {
	now := time.Now()
	return &Job{
		ID:        id,
		Status:    StatusQueued,
		OrgID:     orgID,
		Request:   &models.ChatCompletionRequest{Model: "gpt-4o"},
		Exec:      exec,
		CreatedAt: now,
		TTL:       time.Hour,
		ExpiresAt: now.Add(2 * time.Hour),
	}
}

func okExec(provider string) Executor {
	return func(ctx context.Context) (*Execution, error) {
		return &Execution{
			Response: &models.ChatCompletionResponse{ID: "chatcmpl-1", Model: "gpt-4o"},
			Provider: provider,
			Metadata: map[string]string{"cost": "0.01"},
		}, nil
	}
}

// waitDone polls until the job's status is neither queued nor processing.
func waitDone(t *testing.T, s *Store, id string) *Job {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for {
		job := s.Get(id)
		if job == nil {
			t.Fatalf("job %s disappeared", id)
		}
		if job.Status != StatusQueued && job.Status != StatusProcessing {
			return job
		}
		if time.Now().After(deadline) {
			t.Fatalf("job %s still %s after 5s", id, job.Status)
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func TestWorker_RunsTheJobsExecutorAndRecordsItsProvider(t *testing.T) {
	s := NewStore()
	w := NewWorker(s, WorkerConfig{MaxWorkers: 1})
	w.Start()
	t.Cleanup(w.Stop)

	w.Submit(newTestJob("job-1", "org-a", okExec("org-provider")))
	job := waitDone(t, s, "job-1")
	if job.Status != StatusCompleted {
		t.Fatalf("status %s, want completed (error %v)", job.Status, job.Error)
	}
	if job.Provider != "org-provider" {
		t.Errorf("job records provider %q, want the executor's %q", job.Provider, "org-provider")
	}
	if got := job.ToResponse().Provider; got != "org-provider" {
		t.Errorf("job response provider %q, want %q", got, "org-provider")
	}
	if job.Response == nil || job.Response.ID != "chatcmpl-1" {
		t.Errorf("job response %+v, want the executor's", job.Response)
	}
	if job.Metadata["cost"] != "0.01" {
		t.Errorf("job metadata %v, want the executor's cost", job.Metadata)
	}
}

// A job carries the routing its request was admitted with. A job without an
// executor must fail, never be run on some provider the worker picks.
func TestWorker_JobWithoutAnExecutorFails(t *testing.T) {
	s := NewStore()
	w := NewWorker(s, WorkerConfig{MaxWorkers: 1})
	w.Start()
	t.Cleanup(w.Stop)

	w.Submit(newTestJob("job-1", "org-a", nil))
	job := waitDone(t, s, "job-1")
	if job.Status != StatusFailed || job.Error == nil {
		t.Fatalf("status %s error %v, want failed with an error", job.Status, job.Error)
	}
	if job.Provider != "" {
		t.Errorf("a job without an executor records provider %q", job.Provider)
	}
}

func TestWorker_FailedExecutionRecordsTheProviderTried(t *testing.T) {
	s := NewStore()
	w := NewWorker(s, WorkerConfig{MaxWorkers: 1})
	w.Start()
	t.Cleanup(w.Stop)

	w.Submit(newTestJob("job-1", "org-a", func(ctx context.Context) (*Execution, error) {
		return &Execution{Provider: "org-provider"}, models.ErrUpstreamProvider(500, "boom")
	}))
	job := waitDone(t, s, "job-1")
	if job.Status != StatusFailed {
		t.Fatalf("status %s, want failed", job.Status)
	}
	if job.Provider != "org-provider" {
		t.Errorf("failed job records provider %q, want %q", job.Provider, "org-provider")
	}
}

func TestWorker_DeleteCancelsARunningJob(t *testing.T) {
	s := NewStore()
	w := NewWorker(s, WorkerConfig{MaxWorkers: 1})
	w.Start()
	t.Cleanup(w.Stop)

	started := make(chan struct{})
	cancelled := make(chan struct{})
	w.Submit(newTestJob("job-1", "org-a", func(ctx context.Context) (*Execution, error) {
		close(started)
		select {
		case <-ctx.Done():
			close(cancelled)
			return &Execution{Provider: "org-provider"}, ctx.Err()
		case <-time.After(5 * time.Second):
			return nil, models.ErrInternal("never cancelled")
		}
	}))

	select {
	case <-started:
	case <-time.After(5 * time.Second):
		t.Fatal("the job never started")
	}
	if !s.DeleteForOrg("job-1", "org-a") {
		t.Fatal("DeleteForOrg did not find the job")
	}
	select {
	case <-cancelled:
	case <-time.After(2 * time.Second):
		t.Fatal("DeleteForOrg left the running job's context live")
	}
	// The worker's last update must not bring the job back.
	time.Sleep(20 * time.Millisecond)
	if s.Get("job-1") != nil {
		t.Error("the deleted job is back in the store")
	}
}

func TestWorker_JobDeletedBeforeItStartsNeverRuns(t *testing.T) {
	s := NewStore()
	w := NewWorker(s, WorkerConfig{MaxWorkers: 1})
	t.Cleanup(w.Stop)

	var ran atomic.Bool
	w.Submit(newTestJob("job-deleted", "org-a", func(ctx context.Context) (*Execution, error) {
		ran.Store(true)
		return okExec("p")(ctx)
	}))
	w.Submit(newTestJob("job-barrier", "org-a", okExec("p")))
	if !s.DeleteForOrg("job-deleted", "org-a") {
		t.Fatal("DeleteForOrg did not find the queued job")
	}

	// One worker takes the queue in order, so once the barrier job is done
	// the deleted one has been dequeued.
	w.Start()
	waitDone(t, s, "job-barrier")
	if ran.Load() {
		t.Error("a job deleted while queued still ran")
	}
}

// DeleteForOrg follows GetForOrg: only the job's own org (exactly; "" is the
// no-org tenant) can delete or cancel it.
func TestStore_DeleteForOrgIsStrict(t *testing.T) {
	cases := []struct {
		name, jobOrg, caller string
		ok                   bool
	}{
		{"own org", "org-a", "org-a", true},
		{"no-org job, no-org caller", "", "", true},
		{"other org", "org-a", "org-b", false},
		{"no-org caller on an org's job", "org-a", "", false},
		{"org caller on a no-org job", "", "org-a", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s := NewStore()
			s.Put(newTestJob("job-1", tc.jobOrg, okExec("p")))
			var cancelled atomic.Bool
			if _, _, ok := s.start("job-1", func() { cancelled.Store(true) }); !ok {
				t.Fatal("start refused a queued job")
			}

			if got := s.DeleteForOrg("job-1", tc.caller); got != tc.ok {
				t.Fatalf("DeleteForOrg = %v, want %v", got, tc.ok)
			}
			if gone := s.Get("job-1") == nil; gone != tc.ok {
				t.Errorf("job removed = %v, want %v", gone, tc.ok)
			}
			if cancelled.Load() != tc.ok {
				t.Errorf("job cancelled = %v, want %v", cancelled.Load(), tc.ok)
			}
		})
	}
	if NewStore().DeleteForOrg("missing", "") {
		t.Error("DeleteForOrg found a missing job")
	}
}

func TestStore_StartRunsAJobOnce(t *testing.T) {
	s := NewStore()
	s.Put(newTestJob("job-1", "org-a", okExec("p")))
	exec, job, ok := s.start("job-1", func() {})
	if !ok || exec == nil || job == nil || job.Status != StatusProcessing || job.StartedAt == nil {
		t.Fatalf("start = (%v, %+v, %v), want the executor and a processing snapshot", exec != nil, job, ok)
	}
	if job.Exec != nil {
		t.Error("the snapshot start returns leaks the executor")
	}
	if _, _, ok := s.start("job-1", func() {}); ok {
		t.Error("start ran a job that is already processing")
	}
	if _, _, ok := s.start("missing", func() {}); ok {
		t.Error("start ran a missing job")
	}
	if snap := s.Get("job-1"); snap.Exec != nil {
		t.Error("Get leaks the executor")
	}
}

// A finished job stays in the store for its TTL. It must not keep its
// executor, which holds the request it ran and the provider it ran on.
func TestWorker_FinishedJobDropsItsExecutor(t *testing.T) {
	s := NewStore()
	w := NewWorker(s, WorkerConfig{MaxWorkers: 1})
	w.Start()
	t.Cleanup(w.Stop)

	w.Submit(newTestJob("job-1", "org-a", okExec("p")))
	waitDone(t, s, "job-1")
	s.mu.RLock()
	defer s.mu.RUnlock()
	if s.jobs["job-1"].Exec != nil {
		t.Error("a finished job still holds its executor")
	}
}
