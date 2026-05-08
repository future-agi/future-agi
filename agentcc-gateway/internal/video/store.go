package video

import (
	"errors"
	"sync"
	"time"
)

// Sentinel errors.
var (
	ErrVideoJobNotFound  = errors.New("video job not found")
	ErrVideoJobForbidden = errors.New("video job belongs to a different organization")
)

// VideoListFilters holds list query filters.
type VideoListFilters struct {
	Status string
	Model  string
	Limit  int
	Offset int
	Order  string // "asc" or "desc"
}

// Store is the video job store interface.
type Store interface {
	Create(job *VideoJob) error
	Get(id string) (*VideoJob, error)
	GetForOrg(id, orgID string) (*VideoJob, error)
	Update(job *VideoJob) error
	Delete(id string) error
	ListByOrg(orgID string, filters VideoListFilters) ([]*VideoJob, int, error)
	GetActiveJobs() ([]*VideoJob, error)
	GarbageCollect() (int, error)
}

// MemoryStore is an in-memory video job store.
type MemoryStore struct {
	jobs sync.Map
}

// NewMemoryStore creates a new in-memory video job store.
func NewMemoryStore() *MemoryStore {
	return &MemoryStore{}
}

func (s *MemoryStore) Create(job *VideoJob) error {
	if _, loaded := s.jobs.LoadOrStore(job.ID, job); loaded {
		return errors.New("video job already exists")
	}
	return nil
}

func (s *MemoryStore) Get(id string) (*VideoJob, error) {
	v, ok := s.jobs.Load(id)
	if !ok {
		return nil, ErrVideoJobNotFound
	}
	return v.(*VideoJob), nil
}

func (s *MemoryStore) GetForOrg(id, orgID string) (*VideoJob, error) {
	job, err := s.Get(id)
	if err != nil {
		return nil, err
	}
	if job.OrgID != "" && job.OrgID != orgID {
		return nil, ErrVideoJobForbidden
	}
	return job, nil
}

func (s *MemoryStore) Update(job *VideoJob) error {
	s.jobs.Store(job.ID, job)
	return nil
}

func (s *MemoryStore) Delete(id string) error {
	s.jobs.Delete(id)
	return nil
}

func (s *MemoryStore) ListByOrg(orgID string, filters VideoListFilters) ([]*VideoJob, int, error) {
	var all []*VideoJob
	s.jobs.Range(func(_, v interface{}) bool {
		job := v.(*VideoJob)
		if orgID != "" && job.OrgID != orgID {
			return true
		}
		if filters.Status != "" && job.Status != filters.Status {
			return true
		}
		if filters.Model != "" && job.Model != filters.Model {
			return true
		}
		all = append(all, job)
		return true
	})

	total := len(all)

	// Sort by created_at (desc by default).
	if filters.Order == "asc" {
		sortJobsAsc(all)
	} else {
		sortJobsDesc(all)
	}

	// Apply pagination.
	if filters.Offset >= len(all) {
		return nil, total, nil
	}
	end := filters.Offset + filters.Limit
	if end > len(all) {
		end = len(all)
	}
	return all[filters.Offset:end], total, nil
}

func (s *MemoryStore) GetActiveJobs() ([]*VideoJob, error) {
	var active []*VideoJob
	s.jobs.Range(func(_, v interface{}) bool {
		job := v.(*VideoJob)
		if !job.IsTerminal() {
			active = append(active, job)
		}
		return true
	})
	return active, nil
}

func (s *MemoryStore) GarbageCollect() (int, error) {
	var count int
	now := time.Now()
	s.jobs.Range(func(k, v interface{}) bool {
		job := v.(*VideoJob)
		if !job.ExpiresAt.IsZero() && now.After(job.ExpiresAt) {
			s.jobs.Delete(k)
			count++
		}
		return true
	})
	return count, nil
}

func sortJobsDesc(jobs []*VideoJob) {
	for i := 1; i < len(jobs); i++ {
		for j := i; j > 0 && jobs[j].CreatedAt.After(jobs[j-1].CreatedAt); j-- {
			jobs[j], jobs[j-1] = jobs[j-1], jobs[j]
		}
	}
}

func sortJobsAsc(jobs []*VideoJob) {
	for i := 1; i < len(jobs); i++ {
		for j := i; j > 0 && jobs[j].CreatedAt.Before(jobs[j-1].CreatedAt); j-- {
			jobs[j], jobs[j-1] = jobs[j-1], jobs[j]
		}
	}
}
