package lifecycle

import (
	"context"
	"errors"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/video"
)

type Worker struct {
	s      *Service
	owner  string
	mu     sync.Mutex
	cancel context.CancelFunc
	done   chan struct{}
}

func (s *Service) NewWorker() *Worker { return &Worker{s: s, owner: video.NewID()} }
func (w *Worker) Start() {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.cancel != nil {
		return
	}
	ctx, cancel := context.WithCancel(context.Background())
	w.cancel = cancel
	w.done = make(chan struct{})
	go func() {
		defer close(w.done)
		t := time.NewTicker(250 * time.Millisecond)
		defer t.Stop()
		for {
			if e := w.Tick(ctx); errors.Is(e, ErrCrash) {
				return
			}
			select {
			case <-ctx.Done():
				return
			case <-t.C:
			}
		}
	}()
}
func (w *Worker) Stop(ctx context.Context) error {
	w.mu.Lock()
	cancel, done := w.cancel, w.done
	w.mu.Unlock()
	if cancel == nil {
		return nil
	}
	cancel()
	select {
	case <-done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

// Tick is a bounded pass and is also the deterministic clock/crash test entry.
func (w *Worker) Tick(ctx context.Context) error {
	s := w.s
	queues := []func(context.Context, string, time.Duration, int) ([]video.Claim, error){s.store.ClaimSubmit, s.store.ClaimDue, s.store.ClaimReconcile}
	var result error
	for _, claim := range queues {
		jobs, err := claim(ctx, w.owner, s.cfg.Submit.LeaseTTL, s.cfg.Poll.MaxConcurrentTotal)
		if err != nil {
			return err
		}
		errs := make(chan error, len(jobs))
		var wg sync.WaitGroup
		for _, job := range jobs {
			wg.Add(1)
			go func(c video.Claim) { defer wg.Done(); errs <- w.process(ctx, c) }(job)
		}
		wg.Wait()
		close(errs)
		for err := range errs {
			result = errors.Join(result, err)
		}
		if result != nil {
			return result
		}
	}
	if s.sweeper != nil {
		if _, err := s.sweeper.Sweep(ctx, 32, s.expireArtifact); err != nil {
			return err
		}
	}
	_, err := s.store.GarbageCollect()
	if err != nil {
		return err
	}
	return s.RefreshMetrics(ctx)
}
func (w *Worker) process(parent context.Context, c video.Claim) (err error) {
	s := w.s
	ctx, cancel := context.WithCancel(parent)
	done := make(chan struct{})
	renewed := make(chan struct{})
	go func() {
		defer close(renewed)
		t := time.NewTicker(max(s.cfg.Submit.LeaseTTL/3, time.Millisecond))
		defer t.Stop()
		for {
			select {
			case <-done:
				return
			case <-ctx.Done():
				return
			case <-t.C:
				if s.store.Renew(ctx, c.Lease, s.cfg.Submit.LeaseTTL) != nil {
					cancel()
					return
				}
			}
		}
	}()
	defer func() {
		close(done)
		cancel()
		<-renewed
		if !errors.Is(err, ErrCrash) {
			cleanup, stop := context.WithTimeout(context.Background(), time.Second)
			defer stop()
			_ = s.store.Release(cleanup, c.Lease)
		}
	}()
	s.claimMetrics(c)
	j := c.Job
	switch {
	case j.Status == video.StatusSubmitting:
		return s.submit(ctx, j, c.Lease)
	case j.Status == video.StatusSubmissionUnknown || j.ReconcileState == video.ReconcilePending || j.ReconcileState == video.ReconcileUnresolved:
		return s.reconcile(ctx, j, c.Lease)
	case j.BillingKnown && j.IsTerminal():
		return s.settle(ctx, j, c.Lease)
	case j.Observation != nil && j.Observation.Normalized == "completed":
		return s.copyResult(ctx, j, c.Lease)
	case j.IsTerminal():
		j.NextPollAt = s.clock.Now().Add(5 * time.Minute)
		return s.save(ctx, j, c.Lease)
	default:
		return s.poll(ctx, j, c.Lease)
	}
}
func (s *Service) saveLater(ctx context.Context, j *video.VideoJob, l video.Lease, d time.Duration) error {
	j.NextPollAt = s.clock.Now().Add(max(d, time.Second))
	return s.save(ctx, j, l)
}
func (s *Service) fail(ctx context.Context, j *video.VideoJob, l video.Lease, code string, release bool) error {
	if !j.IsTerminal() {
		j.Status = video.StatusFailed
		now := s.clock.Now()
		j.CompletedAt = &now
		j.Error = &video.VideoError{Code: code, Message: stringsSafe(code)}
	}
	if release {
		j.BillingKnown = true
		j.BillingMicros = 0
		j.UpstreamMayContinue = false
		j.ReconcileState = video.ReconcileNone
	}
	if e := s.save(ctx, j, l); e != nil {
		return e
	}
	if release {
		return s.settle(ctx, j, l)
	}
	return nil
}
