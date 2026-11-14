package async

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestCancelForPrincipalDoesNotMutateOtherJobs(t *testing.T) {
	for _, status := range []JobStatus{StatusQueued, StatusProcessing, StatusCompleted, StatusFailed, StatusCancelled} {
		t.Run(string(status), func(t *testing.T) {
			s := NewStore()
			called := 0
			s.Put(&Job{ID: "job", Principal: "org:owner", Status: status, CancelFn: func() { called++; s.Count() }})
			for _, principal := range []string{"", "org:other", "key:owner"} {
				if s.GetForPrincipal("job", principal) != nil || s.CancelForPrincipal("job", principal) {
					t.Fatalf("non-owner %q admitted", principal)
				}
				if got := s.Get("job"); got == nil || got.Status != status || called != 0 {
					t.Fatalf("non-owner mutated job: %+v, callbacks=%d", got, called)
				}
			}
			if got := s.GetForPrincipal("job", "org:owner"); got == nil || got.CancelFn != nil {
				t.Fatal("owner snapshot absent or exposes cancel callback")
			}
			if !s.CancelForPrincipal("job", "org:owner") || called != 1 || s.Count() != 0 {
				t.Fatalf("owner cancel: callbacks=%d count=%d", called, s.Count())
			}
			if s.CancelForPrincipal("job", "org:owner") || called != 1 {
				t.Fatal("cancel was not idempotent")
			}
		})
	}
}

func TestStartCannotDispatchRemovedSnapshot(t *testing.T) {
	s := NewStore()
	s.Put(&Job{ID: "job", Status: StatusQueued, Principal: "org:owner"})
	if s.Get("job") == nil {
		t.Fatal("missing initial worker snapshot")
	}
	if !s.CancelForPrincipal("job", "org:owner") {
		t.Fatal("cancel failed")
	}
	if s.start("job", func() {}) {
		t.Fatal("removed job can still start")
	}
}

func TestCancelRacesWithStartAndCompletion(t *testing.T) {
	for i := 0; i < 1000; i++ {
		s := NewStore()
		s.Put(&Job{ID: "job", Status: StatusQueued, Principal: "org:owner"})
		var called atomic.Int32
		var started bool
		var wg sync.WaitGroup
		wg.Add(2)
		go func() {
			defer wg.Done()
			started = s.start("job", func() { called.Add(1); s.Count() })
			if started {
				s.Update("job", func(job *Job) { job.Status = StatusCompleted })
			}
		}()
		go func() {
			defer wg.Done()
			if !s.CancelForPrincipal("job", "org:owner") {
				t.Error("owner cancel failed")
			}
		}()
		wg.Wait()
		want := int32(0)
		if started {
			want = 1
		}
		if called.Load() != want || s.Count() != 0 {
			t.Fatalf("iteration=%d started=%v calls=%d count=%d", i, started, called.Load(), s.Count())
		}
	}
}

func TestWorkerCancellationStopsProviderAndDoesNotResurrect(t *testing.T) {
	s := NewStore()
	entered, stopped := make(chan struct{}), make(chan struct{})
	w := NewWorker(s, func(ctx context.Context, _ *models.ChatCompletionRequest, _ map[string]string) (*models.ChatCompletionResponse, map[string]string, error) {
		close(entered)
		<-ctx.Done()
		close(stopped)
		return nil, nil, ctx.Err()
	}, 1)
	w.Start()
	defer w.Stop()
	w.Submit(&Job{ID: "job", Principal: "org:owner", Status: StatusQueued, Request: &models.ChatCompletionRequest{Model: "test"}})
	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		t.Fatal("provider did not start")
	}
	if !s.CancelForPrincipal("job", "org:owner") {
		t.Fatal("cancel failed")
	}
	select {
	case <-stopped:
	case <-time.After(5 * time.Second):
		t.Fatal("provider did not observe cancellation")
	}
	if s.Get("job") != nil {
		t.Fatal("cancelled job resurrected")
	}
}
