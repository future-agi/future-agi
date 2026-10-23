package async

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"log/slog"
	"net/http"
	"strconv"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// DefaultResultTTL is the default time-to-live for completed job results.
const DefaultResultTTL = 3600 * time.Second

// DefaultMaxWorkers is the default number of concurrent async workers.
const DefaultMaxWorkers = 10

// DefaultMaxJobTimeout is the default for the longest a job may run.
const DefaultMaxJobTimeout = 120 * time.Second

// QueueCapacity is how many jobs may wait to run, across all orgs. One org
// may have half of them queued.
const QueueCapacity = 1000

// DefaultCleanupInterval is how often expired jobs are garbage collected.
const DefaultCleanupInterval = 5 * time.Minute

// WorkerConfig bounds the worker pool. All orgs' jobs share it, so these
// limits keep one org from holding it. A zero field takes its default.
type WorkerConfig struct {
	// MaxWorkers is how many jobs run at once, across all orgs.
	// Default DefaultMaxWorkers.
	MaxWorkers int
	// MaxWorkersPerOrg is how many jobs one org may have running at once.
	// Default half of MaxWorkers (at least 1); never more than MaxWorkers.
	MaxWorkersPerOrg int
	// MaxJobTimeout is the longest one job may run. The timeout its request
	// resolved (timeout_ms) can shorten it, never lengthen it. Default
	// DefaultMaxJobTimeout.
	MaxJobTimeout time.Duration
}

func (c WorkerConfig) withDefaults() WorkerConfig {
	if c.MaxWorkers <= 0 {
		c.MaxWorkers = DefaultMaxWorkers
	}
	if c.MaxWorkersPerOrg <= 0 {
		c.MaxWorkersPerOrg = max(1, c.MaxWorkers/2)
	}
	c.MaxWorkersPerOrg = min(c.MaxWorkersPerOrg, c.MaxWorkers)
	if c.MaxJobTimeout <= 0 {
		c.MaxJobTimeout = DefaultMaxJobTimeout
	}
	return c
}

// Worker processes async jobs from a queue. It runs each job with the
// executor the job carries; it has no provider of its own.
type Worker struct {
	store         *Store
	queue         *fairQueue
	maxWorkers    int
	maxJobTimeout time.Duration
	stopCh        chan struct{}
}

// NewWorker creates a new async worker.
func NewWorker(store *Store, cfg WorkerConfig) *Worker {
	cfg = cfg.withDefaults()
	return &Worker{
		store:         store,
		queue:         newFairQueue(QueueCapacity, QueueCapacity/2, cfg.MaxWorkersPerOrg),
		maxWorkers:    cfg.MaxWorkers,
		maxJobTimeout: cfg.MaxJobTimeout,
		stopCh:        make(chan struct{}),
	}
}

// Start begins processing jobs.
func (w *Worker) Start() {
	// Start worker goroutines.
	for i := 0; i < w.maxWorkers; i++ {
		go w.processLoop()
	}

	// Start cleanup goroutine.
	go w.cleanupLoop()

	slog.Info("async worker started",
		"max_workers", w.maxWorkers,
		"max_workers_per_org", w.queue.perOrgRunning,
		"max_job_timeout", w.maxJobTimeout,
	)
}

// Stop signals workers to stop. Jobs still queued never run.
func (w *Worker) Stop() {
	close(w.stopCh)
	w.queue.close()
}

// Submit stores a job and queues it. When the queue refuses the job it
// stores nothing and returns the reason as an API error: 429 when the job's
// org already has its share of the queue queued, 503 when the queue is full
// or the worker is stopped.
func (w *Worker) Submit(job *Job) *models.APIError {
	// Store first: a worker may take the job the moment it is queued.
	w.store.Put(job)
	err := w.queue.push(job.OrgID, job.ID)
	if err == nil {
		return nil
	}
	w.store.Delete(job.ID)
	slog.Warn("async job refused by the queue", "job_id", job.ID, "org_id", job.OrgID, "reason", err.Error())
	if errors.Is(err, errOrgQueueFull) {
		return &models.APIError{
			Status:  http.StatusTooManyRequests,
			Type:    models.ErrTypeRateLimit,
			Code:    "async_org_queue_full",
			Message: err.Error(),
		}
	}
	return &models.APIError{
		Status:  http.StatusServiceUnavailable,
		Type:    models.ErrTypeServer,
		Code:    "async_queue_full",
		Message: err.Error(),
	}
}

func (w *Worker) processLoop() {
	for {
		org, jobID, ok := w.queue.next()
		if !ok {
			return
		}
		w.runTurn(org, jobID)
	}
}

// runTurn processes one job taken from the queue, and gives its org's turn
// back however the job ends.
func (w *Worker) runTurn(org, jobID string) {
	defer w.queue.done(org)
	w.processJob(jobID)
}

// jobTimeout is how long a job may run: the timeout its request resolved
// (timeoutMs, from the request's timeout header, its org's or the operator's
// model timeout, or the server default) when that is shorter than the
// operator's cap, and the cap otherwise. Nothing a caller or an org sets can
// make a job hold a shared worker longer than the cap.
func (w *Worker) jobTimeout(timeoutMs string) time.Duration {
	ms, err := strconv.ParseInt(timeoutMs, 10, 64)
	if err == nil && ms > 0 && ms < w.maxJobTimeout.Milliseconds() {
		return time.Duration(ms) * time.Millisecond
	}
	return w.maxJobTimeout
}

func (w *Worker) processJob(jobID string) {
	// Read the timeout from a snapshot; a job deleted since it was queued is
	// gone, and start below refuses it.
	snap := w.store.Get(jobID)
	if snap == nil {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), w.jobTimeout(snap.Metadata["timeout_ms"]))
	defer cancel()

	// Mark the job processing and register cancel in one step, so a delete
	// either stops the job before it runs or cancels ctx while it runs.
	exec, job, ok := w.store.start(jobID, cancel)
	if !ok {
		return
	}

	model := ""
	if job.Request != nil {
		model = job.Request.Model
	}
	slog.Debug("processing async job", "job_id", jobID, "model", model)

	var result *Execution
	var err error
	if exec == nil {
		err = models.ErrInternal("async job has no executor")
	} else {
		result, err = exec(ctx)
	}
	if err == nil && (result == nil || result.Response == nil) {
		err = models.ErrInternal("no response from provider")
	}

	completedAt := time.Now()

	w.store.Update(jobID, func(job *Job) {
		job.cancel = nil
		if job.Status != StatusProcessing {
			return
		}
		job.CompletedAt = &completedAt
		if result != nil {
			job.Provider = result.Provider
			job.CredentialSource = result.CredentialSource
		}

		if err != nil {
			job.Status = StatusFailed
			if apiErr, ok := err.(*models.APIError); ok {
				job.Error = apiErr
			} else {
				job.Error = models.ErrInternal(err.Error())
			}
		} else {
			job.Status = StatusCompleted
			job.Response = result.Response
			for k, v := range result.Metadata {
				if job.Metadata == nil {
					job.Metadata = make(map[string]string)
				}
				job.Metadata[k] = v
			}
		}

		// Set expiry based on TTL.
		ttl := job.TTL
		if ttl <= 0 {
			ttl = DefaultResultTTL
		}
		job.ExpiresAt = completedAt.Add(ttl)
	})

	if err != nil {
		slog.Debug("async job failed", "job_id", jobID, "error", err)
	} else {
		slog.Debug("async job completed", "job_id", jobID)
	}
}

func (w *Worker) cleanupLoop() {
	ticker := time.NewTicker(DefaultCleanupInterval)
	defer ticker.Stop()

	for {
		select {
		case <-w.stopCh:
			return
		case <-ticker.C:
			removed := w.store.CleanExpired()
			if removed > 0 {
				slog.Debug("cleaned expired async jobs",
					"removed", removed,
					"remaining", w.store.Count(),
				)
			}
		}
	}
}

// GenerateJobID creates a unique job ID using crypto/rand.
func GenerateJobID() string {
	b := make([]byte, 12)
	if _, err := rand.Read(b); err != nil {
		panic("crypto/rand.Read failed: " + err.Error())
	}
	return "async-" + hex.EncodeToString(b)
}
