package async

import (
	"context"
	"errors"
	"net/http"
	"strconv"
	"sync/atomic"
	"testing"
	"time"
)

// nextWithin returns the queue's next job, or fails the test if next is
// still blocked after d.
func nextWithin(t *testing.T, q *fairQueue, d time.Duration) (org, id string) {
	t.Helper()
	type pick struct{ org, id string }
	got := make(chan pick, 1)
	go func() {
		o, i, ok := q.next()
		if ok {
			got <- pick{o, i}
		}
	}()
	select {
	case p := <-got:
		return p.org, p.id
	case <-time.After(d):
		t.Fatalf("next still blocked after %s", d)
		return "", ""
	}
}

// nextAsync calls next in the background and delivers the ID it returns.
func nextAsync(q *fairQueue) <-chan string {
	got := make(chan string, 1)
	go func() {
		if _, id, ok := q.next(); ok {
			got <- id
		}
	}()
	return got
}

func TestFairQueue_TakesOrgsInTurn(t *testing.T) {
	q := newFairQueue(100, 100, 100)
	t.Cleanup(q.close)
	for _, j := range []struct{ org, id string }{{"a", "a1"}, {"a", "a2"}, {"a", "a3"}, {"b", "b1"}, {"", "n1"}} {
		if err := q.push(j.org, j.id); err != nil {
			t.Fatalf("push %s: %v", j.id, err)
		}
	}
	var order []string
	for range 5 {
		_, id := nextWithin(t, q, time.Second)
		order = append(order, id)
	}
	// One job per org per turn: a's backlog does not hold b or the no-org
	// tenant back.
	want := []string{"a1", "b1", "n1", "a2", "a3"}
	for i := range want {
		if order[i] != want[i] {
			t.Fatalf("jobs taken in order %v, want %v", order, want)
		}
	}
}

func TestFairQueue_AnOrgRunsAtMostItsShare(t *testing.T) {
	q := newFairQueue(100, 100, 2)
	t.Cleanup(q.close)
	for i := range 4 {
		if err := q.push("a", "a"+strconv.Itoa(i)); err != nil {
			t.Fatal(err)
		}
	}
	nextWithin(t, q, time.Second)
	nextWithin(t, q, time.Second)
	waiting := nextAsync(q)
	select {
	case id := <-waiting:
		t.Fatalf("an org with 2 jobs running got a third worker (job %s); its share is 2", id)
	case <-time.After(50 * time.Millisecond):
	}
	// The waiting worker takes another org's job.
	if err := q.push("b", "b1"); err != nil {
		t.Fatal(err)
	}
	select {
	case id := <-waiting:
		if id != "b1" {
			t.Fatalf("the waiting worker took %s, want b1", id)
		}
	case <-time.After(time.Second):
		t.Fatal("org b's job waited behind org a's share")
	}
	// One of a's jobs ends: its turn frees for a's next job.
	q.done("a")
	if org, _ := nextWithin(t, q, time.Second); org != "a" {
		t.Fatalf("after one of a's jobs ended, next took %q's job, want a's", org)
	}
}

func TestFairQueue_AnOrgQueuesAtMostItsShare(t *testing.T) {
	q := newFairQueue(4, 2, 1)
	t.Cleanup(q.close)
	for _, j := range []struct {
		org  string
		want error
	}{{"a", nil}, {"a", nil}, {"a", errOrgQueueFull}, {"b", nil}, {"b", nil}, {"c", errQueueFull}} {
		if err := q.push(j.org, "x"); !errors.Is(err, j.want) && err != j.want {
			t.Fatalf("push for %s: %v, want %v", j.org, err, j.want)
		}
	}
	// A job taken from the queue frees its place.
	nextWithin(t, q, time.Second)
	if err := q.push("c", "c1"); err != nil {
		t.Errorf("push after a job was taken: %v", err)
	}
}

func TestFairQueue_CloseReleasesWaitingWorkers(t *testing.T) {
	q := newFairQueue(10, 10, 1)
	done := make(chan bool, 1)
	go func() {
		_, _, ok := q.next()
		done <- ok
	}()
	time.Sleep(20 * time.Millisecond)
	q.close()
	select {
	case ok := <-done:
		if ok {
			t.Error("next returned a job from an empty, closed queue")
		}
	case <-time.After(time.Second):
		t.Fatal("close left a worker waiting")
	}
	if err := q.push("a", "a1"); !errors.Is(err, errQueueClosed) {
		t.Errorf("push on a closed queue: %v, want errQueueClosed", err)
	}
}

// blockingExec runs until its context ends, after reporting that it started.
func blockingExec(started chan<- string, id string) Executor {
	return func(ctx context.Context) (*Execution, error) {
		started <- id
		<-ctx.Done()
		return &Execution{Provider: "p"}, ctx.Err()
	}
}

// Jobs of every org share the workers. One org whose jobs never end must
// leave workers for other orgs.
func TestWorker_OneOrgCannotHoldEveryWorker(t *testing.T) {
	s := NewStore()
	w := NewWorker(s, WorkerConfig{MaxWorkers: 4, MaxJobTimeout: time.Minute})
	w.Start()
	t.Cleanup(w.Stop)

	started := make(chan string, 16)
	for i := range 6 {
		id := "a" + strconv.Itoa(i)
		if err := w.Submit(newTestJob(id, "org-a", blockingExec(started, id))); err != nil {
			t.Fatalf("submit %s: %v", id, err)
		}
	}
	// Half the workers (the default share) take org a's jobs.
	for range 2 {
		select {
		case <-started:
		case <-time.After(2 * time.Second):
			t.Fatal("org a's jobs did not start")
		}
	}

	if err := w.Submit(newTestJob("b1", "org-b", okExec("p"))); err != nil {
		t.Fatalf("submit b1: %v", err)
	}
	if job := waitDone(t, s, "b1"); job.Status != StatusCompleted {
		t.Fatalf("org b's job: status %s, want completed", job.Status)
	}
	select {
	case id := <-started:
		t.Errorf("org a's job %s started while 2 of its jobs ran; its share of 4 workers is 2", id)
	case <-time.After(50 * time.Millisecond):
	}
}

func TestWorker_JobTimeoutIsTheOperatorsCap(t *testing.T) {
	cases := []struct {
		name      string
		timeoutMs string
		want      time.Duration
	}{
		{"request asks for far longer than the cap", strconv.Itoa(int(time.Hour.Milliseconds())), 200 * time.Millisecond},
		{"request asks for less than the cap", "50", 50 * time.Millisecond},
		{"no request timeout", "", 200 * time.Millisecond},
		{"unparseable request timeout", "1000h", 200 * time.Millisecond},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s := NewStore()
			w := NewWorker(s, WorkerConfig{MaxWorkers: 1, MaxJobTimeout: 200 * time.Millisecond})
			w.Start()
			t.Cleanup(w.Stop)

			var budget atomic.Int64
			job := newTestJob("job-1", "org-a", func(ctx context.Context) (*Execution, error) {
				deadline, ok := ctx.Deadline()
				if ok {
					budget.Store(int64(time.Until(deadline)))
				}
				<-ctx.Done()
				return &Execution{Provider: "p"}, ctx.Err()
			})
			if tc.timeoutMs != "" {
				job.Metadata = map[string]string{"timeout_ms": tc.timeoutMs}
			}
			if err := w.Submit(job); err != nil {
				t.Fatal(err)
			}
			done := waitDone(t, s, "job-1")
			if done.Status != StatusFailed {
				t.Fatalf("status %s, want failed at its timeout", done.Status)
			}
			got := time.Duration(budget.Load())
			if got <= 0 || got > tc.want || got < tc.want-100*time.Millisecond {
				t.Errorf("job ran with %s left on its deadline, want about %s", got, tc.want)
			}
		})
	}
}

// One org may queue half the queue. Its next job is refused at once, and is
// not stored; another org's job is still queued.
func TestWorker_SubmitRefusesAnOrgPastItsShareOfTheQueue(t *testing.T) {
	s := NewStore()
	w := NewWorker(s, WorkerConfig{MaxWorkers: 1})
	t.Cleanup(w.Stop) // never started: every job stays queued

	for i := range QueueCapacity / 2 {
		if err := w.Submit(newTestJob("a"+strconv.Itoa(i), "org-a", okExec("p"))); err != nil {
			t.Fatalf("job %d of org a refused: %v", i, err)
		}
	}
	err := w.Submit(newTestJob("a-over", "org-a", okExec("p")))
	if err == nil || err.Status != http.StatusTooManyRequests || err.Code != "async_org_queue_full" {
		t.Fatalf("org a's job past its share: %v, want 429 async_org_queue_full", err)
	}
	if s.Get("a-over") != nil {
		t.Error("a refused job was stored")
	}
	if err := w.Submit(newTestJob("b1", "org-b", okExec("p"))); err != nil {
		t.Errorf("org b's job refused while org a has its share queued: %v", err)
	}
}
