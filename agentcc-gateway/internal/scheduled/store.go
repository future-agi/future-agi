package scheduled

import (
	"errors"
	"sort"
	"sync"
	"time"
)

var (
	// ErrJobNotFound is returned when a scheduled job is not found.
	ErrJobNotFound = errors.New("scheduled job not found")
	// ErrOrgLimitExceeded is returned when the org has too many pending jobs.
	ErrOrgLimitExceeded = errors.New("maximum pending jobs exceeded for this organization")
)

// Store manages scheduled job persistence.
type Store interface {
	Create(job *ScheduledJob) error
	Get(id string) (*ScheduledJob, error)
	Update(job *ScheduledJob) error
	Delete(id string) error
	ListByOrg(orgID string, status string, limit int) ([]*ScheduledJob, error)
	GetDueJobs(now time.Time, limit int) ([]*ScheduledJob, error)
	CountPendingByOrg(orgID string) int
	GarbageCollect(now time.Time)
}

// MemoryStore is an in-memory implementation of Store.
type MemoryStore struct {
	mu             sync.RWMutex
	jobs           map[string]*ScheduledJob
	maxPendingJobs int
}

// NewMemoryStore creates an in-memory store.
func NewMemoryStore(maxPendingJobs int) *MemoryStore {
	if maxPendingJobs <= 0 {
		maxPendingJobs = 1000
	}
	return &MemoryStore{
		jobs:           make(map[string]*ScheduledJob),
		maxPendingJobs: maxPendingJobs,
	}
}

func (s *MemoryStore) Create(job *ScheduledJob) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	// Check org limit.
	if s.maxPendingJobs > 0 {
		count := 0
		for _, j := range s.jobs {
			if j.OrgID == job.OrgID && j.Status == StatusPending {
				count++
			}
		}
		if count >= s.maxPendingJobs {
			return ErrOrgLimitExceeded
		}
	}

	s.jobs[job.ID] = job
	return nil
}

func (s *MemoryStore) Get(id string) (*ScheduledJob, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	j, ok := s.jobs[id]
	if !ok {
		return nil, ErrJobNotFound
	}
	return j, nil
}

func (s *MemoryStore) Update(job *ScheduledJob) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.jobs[job.ID]; !ok {
		return ErrJobNotFound
	}
	job.UpdatedAt = time.Now()
	s.jobs[job.ID] = job
	return nil
}

func (s *MemoryStore) Delete(id string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.jobs[id]; !ok {
		return ErrJobNotFound
	}
	delete(s.jobs, id)
	return nil
}

func (s *MemoryStore) ListByOrg(orgID string, status string, limit int) ([]*ScheduledJob, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	var results []*ScheduledJob
	for _, j := range s.jobs {
		if j.OrgID != orgID {
			continue
		}
		if status != "" && j.Status != status {
			continue
		}
		results = append(results, j)
	}

	// Sort by created_at descending.
	sort.Slice(results, func(i, k int) bool {
		return results[i].CreatedAt.After(results[k].CreatedAt)
	})

	if limit > 0 && len(results) > limit {
		results = results[:limit]
	}
	return results, nil
}

func (s *MemoryStore) GetDueJobs(now time.Time, limit int) ([]*ScheduledJob, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	var due []*ScheduledJob
	for _, j := range s.jobs {
		if j.Status == StatusPending && !j.ScheduledAt.After(now) {
			due = append(due, j)
		}
	}

	// Sort by scheduled_at ascending (oldest first).
	sort.Slice(due, func(i, k int) bool {
		return due[i].ScheduledAt.Before(due[k].ScheduledAt)
	})

	if limit > 0 && len(due) > limit {
		due = due[:limit]
	}
	return due, nil
}

func (s *MemoryStore) CountPendingByOrg(orgID string) int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	count := 0
	for _, j := range s.jobs {
		if j.OrgID == orgID && j.Status == StatusPending {
			count++
		}
	}
	return count
}

func (s *MemoryStore) GarbageCollect(now time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for id, j := range s.jobs {
		if j.IsTerminal() && !j.ExpiresAt.IsZero() && now.After(j.ExpiresAt) {
			delete(s.jobs, id)
		}
	}
}
