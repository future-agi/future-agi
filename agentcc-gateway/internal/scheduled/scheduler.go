package scheduled

import (
	"bytes"
	"encoding/json"
	"log/slog"
	"net/http"
	"sync"
	"time"
)

// ExecuteFunc is called by the scheduler to execute a scheduled job.
// It receives the raw request JSON and returns the raw response JSON.
type ExecuteFunc func(requestJSON json.RawMessage) (json.RawMessage, error)

// Scheduler picks up due jobs and executes them.
type Scheduler struct {
	store        Store
	executeFunc  ExecuteFunc
	resultTTL    time.Duration
	retryBackoff time.Duration
	workerCh     chan *ScheduledJob
	stopCh       chan struct{}
	wg           sync.WaitGroup
}

// NewScheduler creates and starts a scheduler.
func NewScheduler(store Store, executeFunc ExecuteFunc, resultTTL, retryBackoff time.Duration, workerCount int) *Scheduler {
	if resultTTL <= 0 {
		resultTTL = 24 * time.Hour
	}
	if retryBackoff <= 0 {
		retryBackoff = 30 * time.Second
	}
	if workerCount <= 0 {
		workerCount = 4
	}

	s := &Scheduler{
		store:        store,
		executeFunc:  executeFunc,
		resultTTL:    resultTTL,
		retryBackoff: retryBackoff,
		workerCh:     make(chan *ScheduledJob, workerCount*2),
		stopCh:       make(chan struct{}),
	}

	// Start workers.
	for i := 0; i < workerCount; i++ {
		s.wg.Add(1)
		go s.worker()
	}

	// Start ticker.
	s.wg.Add(1)
	go s.run()

	// Start GC.
	s.wg.Add(1)
	go s.gc()

	return s
}

func (s *Scheduler) run() {
	defer s.wg.Done()
	ticker := time.NewTicker(1 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			s.processDueJobs()
		case <-s.stopCh:
			return
		}
	}
}

func (s *Scheduler) processDueJobs() {
	due, err := s.store.GetDueJobs(time.Now(), 10)
	if err != nil {
		slog.Error("scheduler: failed to get due jobs", "error", err)
		return
	}

	for _, job := range due {
		job.Status = StatusRunning
		job.UpdatedAt = time.Now()
		if err := s.store.Update(job); err != nil {
			continue
		}

		// Non-blocking send — if workers are busy, skip this batch.
		select {
		case s.workerCh <- job:
		default:
			// Workers busy, revert status and try next tick.
			job.Status = StatusPending
			_ = s.store.Update(job)
		}
	}
}

func (s *Scheduler) worker() {
	defer s.wg.Done()
	for {
		select {
		case job, ok := <-s.workerCh:
			if !ok {
				return
			}
			s.executeJob(job)
		case <-s.stopCh:
			return
		}
	}
}

func (s *Scheduler) executeJob(job *ScheduledJob) {
	slog.Info("scheduler: executing job", "job_id", job.ID, "model", job.Model, "attempt", job.Attempts+1)

	responseJSON, err := s.executeFunc(job.Request)
	job.Attempts++
	job.UpdatedAt = time.Now()

	if err == nil {
		job.Status = StatusCompleted
		job.Response = responseJSON
		job.ExpiresAt = time.Now().Add(s.resultTTL)
		slog.Info("scheduler: job completed", "job_id", job.ID)
	} else {
		if job.Attempts < job.MaxAttempts {
			job.Status = StatusPending
			job.ScheduledAt = time.Now().Add(s.retryBackoff * time.Duration(job.Attempts))
			slog.Warn("scheduler: job failed, will retry",
				"job_id", job.ID, "attempt", job.Attempts, "error", err)
		} else {
			job.Status = StatusFailed
			job.Error = err.Error()
			job.ExpiresAt = time.Now().Add(s.resultTTL)
			slog.Error("scheduler: job failed permanently",
				"job_id", job.ID, "attempts", job.Attempts, "error", err)
		}
	}

	if updateErr := s.store.Update(job); updateErr != nil {
		slog.Error("scheduler: failed to update job", "job_id", job.ID, "error", updateErr)
	}

	// Fire webhook if configured.
	if job.WebhookURL != "" && job.IsTerminal() {
		go s.fireWebhook(job)
	}
}

func (s *Scheduler) fireWebhook(job *ScheduledJob) {
	payload := map[string]interface{}{
		"job_id":   job.ID,
		"status":   job.Status,
		"metadata": job.Metadata,
	}
	if job.Status == StatusCompleted {
		payload["result"] = json.RawMessage(job.Response)
	} else {
		payload["error"] = job.Error
	}

	body, err := json.Marshal(payload)
	if err != nil {
		return
	}

	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Post(job.WebhookURL, "application/json", bytes.NewReader(body))
	if err != nil {
		slog.Warn("scheduler: webhook delivery failed",
			"job_id", job.ID, "url", job.WebhookURL, "error", err)
		return
	}
	resp.Body.Close()
}

func (s *Scheduler) gc() {
	defer s.wg.Done()
	ticker := time.NewTicker(5 * time.Minute)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			s.store.GarbageCollect(time.Now())
		case <-s.stopCh:
			return
		}
	}
}

// Stop shuts down the scheduler gracefully.
func (s *Scheduler) Stop() {
	close(s.stopCh)
	// Wait with timeout.
	done := make(chan struct{})
	go func() {
		s.wg.Wait()
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(30 * time.Second):
		slog.Warn("scheduler: shutdown timed out")
	}
}
