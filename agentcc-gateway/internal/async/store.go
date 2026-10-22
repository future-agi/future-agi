package async

import (
	"context"
	"encoding/json"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// JobStatus represents the state of an async job.
type JobStatus string

const (
	StatusQueued     JobStatus = "queued"
	StatusProcessing JobStatus = "processing"
	StatusCompleted  JobStatus = "completed"
	StatusFailed     JobStatus = "failed"
	StatusCancelled  JobStatus = "cancelled"
)

// Job represents an async inference job.
type Job struct {
	ID           string                         `json:"id"`
	Status       JobStatus                      `json:"status"`
	OrgID        string                         `json:"org_id,omitempty"`
	EndpointType string                         `json:"endpoint_type"` // "chat", "embedding", "image", etc.
	Request      *models.ChatCompletionRequest  `json:"request,omitempty"`
	Response     *models.ChatCompletionResponse `json:"response,omitempty"`
	Error        *models.APIError               `json:"error,omitempty"`
	Metadata     map[string]string              `json:"metadata,omitempty"`
	Provider     string                         `json:"provider,omitempty"` // the provider that ran the job, set when it finishes
	CreatedAt    time.Time                      `json:"created_at"`
	StartedAt    *time.Time                     `json:"started_at,omitempty"`
	CompletedAt  *time.Time                     `json:"completed_at,omitempty"`
	TTL          time.Duration                  `json:"-"`
	ExpiresAt    time.Time                      `json:"expires_at"`

	// CredentialSource is whose credentials the provider call that ran the
	// job used: "org" (the org's own provider config) or "platform" (the
	// gateway's). Set with Provider; "" when no provider was called.
	CredentialSource string `json:"credential_source,omitempty"`

	// Exec runs the job. The handler that admitted the request builds it, so
	// the job runs with that request's routing and pipeline. A job without
	// one fails: the worker resolves nothing itself.
	Exec Executor `json:"-"`

	// cancel stops the running Exec. The worker sets it when the job starts;
	// DeleteForOrg calls it.
	cancel context.CancelFunc
}

// Executor runs a job's request and reports what it produced. The context
// carries the job's timeout and is cancelled when the job is deleted.
type Executor func(ctx context.Context) (*Execution, error)

// Execution is what running a job produced: the response (nil on failure),
// the provider that ran it, and the request metadata the pipeline left.
// CredentialSource is whose credentials that provider used: "org" or
// "platform", "" when no provider was called.
type Execution struct {
	Response         *models.ChatCompletionResponse
	Provider         string
	CredentialSource string
	Metadata         map[string]string
}

// JobResponse is the serialized form returned to clients.
type JobResponse struct {
	ID           string           `json:"id"`
	Status       JobStatus        `json:"status"`
	EndpointType string           `json:"endpoint_type"`
	CreatedAt    time.Time        `json:"created_at"`
	StartedAt    *time.Time       `json:"started_at,omitempty"`
	CompletedAt  *time.Time       `json:"completed_at,omitempty"`
	ExpiresAt    time.Time        `json:"expires_at"`
	Provider     string           `json:"provider,omitempty"`
	Response     json.RawMessage  `json:"response,omitempty"`
	Error        *models.APIError `json:"error,omitempty"`

	// CredentialSource is "org" or "platform"; see Job.CredentialSource.
	CredentialSource string `json:"credential_source,omitempty"`
}

// ToResponse converts a Job to a client-facing JobResponse.
func (j *Job) ToResponse() *JobResponse {
	jr := &JobResponse{
		ID:           j.ID,
		Status:       j.Status,
		EndpointType: j.EndpointType,
		CreatedAt:    j.CreatedAt,
		StartedAt:    j.StartedAt,
		CompletedAt:  j.CompletedAt,
		ExpiresAt:    j.ExpiresAt,
		Provider:     j.Provider,
		Error:        j.Error,

		CredentialSource: j.CredentialSource,
	}

	if j.Response != nil {
		data, _ := json.Marshal(j.Response)
		jr.Response = data
	}

	return jr
}

// Store is a thread-safe in-memory store for async jobs.
type Store struct {
	mu   sync.RWMutex
	jobs map[string]*Job
}

// NewStore creates a new async job store.
func NewStore() *Store {
	return &Store{
		jobs: make(map[string]*Job),
	}
}

// Put stores a job.
func (s *Store) Put(job *Job) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.jobs[job.ID] = job
}

// Get retrieves a job by ID. Returns a snapshot copy (nil if not found).
func (s *Store) Get(id string) *Job {
	s.mu.RLock()
	defer s.mu.RUnlock()
	job := s.jobs[id]
	if job == nil {
		return nil
	}
	return job.snapshot()
}

// GetForOrg retrieves a job by ID, verifying org ownership.
// Returns a snapshot copy (nil if not found or if the job's org is not orgID
// exactly: "" is the no-org tenant, not a wildcard).
func (s *Store) GetForOrg(id, orgID string) *Job {
	s.mu.RLock()
	defer s.mu.RUnlock()
	job := s.jobs[id]
	if job == nil {
		return nil
	}
	if job.OrgID != orgID {
		return nil
	}
	return job.snapshot()
}

// snapshot returns a shallow copy of the Job safe for reading outside the lock.
// It leaves out the executor and the cancel function: only the worker runs a
// job, and only DeleteForOrg stops one.
func (j *Job) snapshot() *Job {
	cp := *j
	cp.Exec = nil
	cp.cancel = nil
	return &cp
}

// start moves a queued job to processing and records cancel as the way to
// stop it, in one step, so a delete either comes first (and the job never
// runs) or finds cancel to call. It hands the job's executor to the caller
// and drops it from the job, which may stay stored for its TTL after it
// runs. It returns false when the job is gone or not queued.
func (s *Store) start(id string, cancel context.CancelFunc) (Executor, *Job, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	job := s.jobs[id]
	if job == nil || job.Status != StatusQueued {
		return nil, nil, false
	}
	now := time.Now()
	job.Status = StatusProcessing
	job.StartedAt = &now
	job.cancel = cancel
	exec := job.Exec
	job.Exec = nil
	return exec, job.snapshot(), true
}

// Update applies a mutation function to a job under the write lock.
// Returns false if the job was not found.
func (s *Store) Update(id string, fn func(job *Job)) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	job := s.jobs[id]
	if job == nil {
		return false
	}
	fn(job)
	return true
}

// DeleteForOrg removes a job that belongs to orgID (exactly, as GetForOrg)
// and stops it if it is running: the context its executor runs under is
// cancelled, so the provider call ends. It reports false, and leaves the job
// untouched, when there is no such job in orgID.
func (s *Store) DeleteForOrg(id, orgID string) bool {
	s.mu.Lock()
	job := s.jobs[id]
	if job == nil || job.OrgID != orgID {
		s.mu.Unlock()
		return false
	}
	delete(s.jobs, id)
	cancel := job.cancel
	job.cancel = nil
	s.mu.Unlock()

	if cancel != nil {
		cancel()
	}
	return true
}

// Delete removes a job.
func (s *Store) Delete(id string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.jobs[id]; ok {
		delete(s.jobs, id)
		return true
	}
	return false
}

// CleanExpired removes all expired jobs. Returns the number of jobs removed.
func (s *Store) CleanExpired() int {
	now := time.Now()
	s.mu.Lock()
	defer s.mu.Unlock()

	removed := 0
	for id, job := range s.jobs {
		if !job.ExpiresAt.IsZero() && now.After(job.ExpiresAt) {
			delete(s.jobs, id)
			removed++
		}
	}
	return removed
}

// Count returns the number of jobs in the store.
func (s *Store) Count() int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return len(s.jobs)
}
