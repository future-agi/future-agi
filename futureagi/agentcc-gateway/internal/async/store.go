package async

import (
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
	CreatedAt    time.Time                      `json:"created_at"`
	StartedAt    *time.Time                     `json:"started_at,omitempty"`
	CompletedAt  *time.Time                     `json:"completed_at,omitempty"`
	TTL          time.Duration                  `json:"-"`
	ExpiresAt    time.Time                      `json:"expires_at"`

	// CancelFn is set when the job is submitted for processing.
	CancelFn func() `json:"-"`
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
	Response     json.RawMessage  `json:"response,omitempty"`
	Error        *models.APIError `json:"error,omitempty"`
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
		Error:        j.Error,
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
// Returns a snapshot copy (nil if not found or if the org doesn't match).
func (s *Store) GetForOrg(id, orgID string) *Job {
	s.mu.RLock()
	defer s.mu.RUnlock()
	job := s.jobs[id]
	if job == nil {
		return nil
	}
	// If orgID is set on the job, verify it matches.
	if job.OrgID != "" && orgID != "" && job.OrgID != orgID {
		return nil
	}
	return job.snapshot()
}

// snapshot returns a shallow copy of the Job safe for reading outside the lock.
func (j *Job) snapshot() *Job {
	cp := *j
	cp.CancelFn = nil // don't leak cancel function
	return &cp
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
