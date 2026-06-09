package async

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"log/slog"
	"strconv"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// DefaultResultTTL is the default time-to-live for completed job results.
const DefaultResultTTL = 3600 * time.Second

// DefaultMaxWorkers is the default number of concurrent async workers.
const DefaultMaxWorkers = 10

// DefaultCleanupInterval is how often expired jobs are garbage collected.
const DefaultCleanupInterval = 5 * time.Minute

// ProviderFunc is the function that actually calls the provider for a chat completion.
// It receives a context and request, returns the response and metadata.
type ProviderFunc func(ctx context.Context, req *models.ChatCompletionRequest, metadata map[string]string) (*models.ChatCompletionResponse, map[string]string, error)

// Worker processes async jobs from a queue.
type Worker struct {
	store       *Store
	queue       chan string
	providerFn  ProviderFunc
	maxWorkers  int
	stopCh      chan struct{}
}

// NewWorker creates a new async worker.
func NewWorker(store *Store, providerFn ProviderFunc, maxWorkers int) *Worker {
	if maxWorkers <= 0 {
		maxWorkers = DefaultMaxWorkers
	}
	return &Worker{
		store:      store,
		queue:      make(chan string, 1000),
		providerFn: providerFn,
		maxWorkers: maxWorkers,
		stopCh:     make(chan struct{}),
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
	)
}

// Stop signals workers to stop.
func (w *Worker) Stop() {
	close(w.stopCh)
}

// Submit queues a job for processing. Returns the job.
func (w *Worker) Submit(job *Job) {
	w.store.Put(job)

	select {
	case w.queue <- job.ID:
	default:
		// Queue full — mark job as failed via store update.
		w.store.Update(job.ID, func(j *Job) {
			now := time.Now()
			j.Status = StatusFailed
			j.CompletedAt = &now
			j.Error = models.ErrInternal("async job queue is full")
		})
		slog.Warn("async job queue full, rejecting job", "job_id", job.ID)
	}
}

func (w *Worker) processLoop() {
	for {
		select {
		case <-w.stopCh:
			return
		case jobID := <-w.queue:
			w.processJob(jobID)
		}
	}
}

func (w *Worker) processJob(jobID string) {
	// Read initial state via snapshot to check status and extract params.
	snap := w.store.Get(jobID)
	if snap == nil {
		return
	}
	if snap.Status == StatusCancelled {
		return
	}

	// Mark as processing via store (mutates the real pointer).
	w.store.Update(jobID, func(job *Job) {
		now := time.Now()
		job.Status = StatusProcessing
		job.StartedAt = &now
	})

	slog.Debug("processing async job", "job_id", jobID, "model", snap.Request.Model)

	// Create context with timeout from metadata.
	timeoutMs := 120000 // default 2 minutes
	if v, ok := snap.Metadata["timeout_ms"]; ok {
		if parsed, err := strconv.Atoi(v); err == nil && parsed > 0 {
			timeoutMs = parsed
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeoutMs)*time.Millisecond)
	defer cancel()

	w.store.Update(jobID, func(job *Job) {
		job.CancelFn = cancel
	})

	// Call the provider function.
	resp, respMeta, err := w.providerFn(ctx, snap.Request, snap.Metadata)

	completedAt := time.Now()

	w.store.Update(jobID, func(job *Job) {
		job.CompletedAt = &completedAt

		if err != nil {
			job.Status = StatusFailed
			if apiErr, ok := err.(*models.APIError); ok {
				job.Error = apiErr
			} else {
				job.Error = models.ErrInternal(err.Error())
			}
		} else {
			job.Status = StatusCompleted
			job.Response = resp
			if respMeta != nil {
				for k, v := range respMeta {
					if job.Metadata == nil {
						job.Metadata = make(map[string]string)
					}
					job.Metadata[k] = v
				}
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
