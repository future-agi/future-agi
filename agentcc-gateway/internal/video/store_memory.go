package video

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"
)

type MemoryStore struct {
	mu     sync.Mutex
	jobs   map[string]*VideoJob
	idem   map[string]string
	leases map[string]Lease
	opts   StoreOptions
}

func NewMemoryStore() *MemoryStore { return NewMemoryStoreWithOptions(StoreOptions{}) }
func NewMemoryStoreWithOptions(opts StoreOptions) *MemoryStore {
	return &MemoryStore{jobs: map[string]*VideoJob{}, idem: map[string]string{}, leases: map[string]Lease{}, opts: defaults(opts)}
}
func idemScope(j *VideoJob) string { return j.OrgID + "\x00" + j.IdemOp + "\x00" + j.IdemDigest }
func (s *MemoryStore) Create(j *VideoJob) error {
	if err := validateJob(j); err != nil {
		return err
	}
	copy, err := cloneJob(j)
	if err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.jobs[j.ID]; ok {
		return ErrVideoJobExists
	}
	s.jobs[j.ID] = copy
	return nil
}
func (s *MemoryStore) getLocked(id string, deleted bool) (*VideoJob, error) {
	j, ok := s.jobs[id]
	if !ok || j.OrgID == "" || !deleted && j.DeletedAt != nil {
		return nil, ErrVideoJobNotFound
	}
	return cloneJob(j)
}
func (s *MemoryStore) Get(id string) (*VideoJob, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.getLocked(id, false)
}
func (s *MemoryStore) GetAccounting(ctx context.Context, id string) (*VideoJob, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.getLocked(id, true)
}
func (s *MemoryStore) GetForOrg(id, org string) (*VideoJob, error) {
	j, err := s.Get(id)
	if err != nil {
		return nil, err
	}
	if org == "" || j.OrgID != org {
		return nil, ErrVideoJobNotFound
	}
	return j, nil
}
func (s *MemoryStore) Accept(ctx context.Context, req AcceptRequest) (AcceptResult, error) {
	if err := ctx.Err(); err != nil {
		return AcceptResult{}, err
	}
	j, err := prepareAccept(req, s.opts)
	if err != nil {
		return AcceptResult{}, err
	}
	s.mu.Lock()
	id := s.idempotentIDLocked(j)
	if id != "" {
		old, err := s.getLocked(id, true)
		s.mu.Unlock()
		if err != nil {
			return AcceptResult{}, err
		}
		return replay(old, j.Fingerprint)
	}
	s.mu.Unlock()
	compensate, err := reserve(s.opts, j)
	if err != nil {
		return AcceptResult{}, err
	}
	s.mu.Lock()
	if id := s.idempotentIDLocked(j); id != "" {
		old, e := s.getLocked(id, true)
		s.mu.Unlock()
		if err := compensate(); err != nil {
			return AcceptResult{}, err
		}
		if e != nil {
			return AcceptResult{}, e
		}
		return replay(old, j.Fingerprint)
	}
	if err := ctx.Err(); err != nil {
		s.mu.Unlock()
		return AcceptResult{}, errors.Join(err, compensate())
	}
	if _, ok := s.jobs[j.ID]; ok {
		s.mu.Unlock()
		return AcceptResult{}, errors.Join(ErrVideoJobExists, compensate())
	}
	orgCount, accountCount := 0, 0
	for _, other := range s.jobs {
		if other.OrgID == "" || other.IsTerminal() && !other.UpstreamMayContinue {
			continue
		}
		if other.OrgID == j.OrgID {
			orgCount++
		}
		if other.Service == j.Service && other.AccountRef == j.AccountRef && other.ModelID == j.ModelID {
			accountCount++
		}
	}
	if req.OrgMaxActive > 0 && orgCount >= req.OrgMaxActive || req.AccountMaxActive > 0 && accountCount >= req.AccountMaxActive {
		s.mu.Unlock()
		return AcceptResult{}, errors.Join(ErrActiveLimit, compensate())
	}
	j.LeaseFence = 1
	s.jobs[j.ID] = j
	s.idem[idemScope(j)] = j.ID
	copy, e := cloneJob(j)
	s.mu.Unlock()
	return AcceptResult{Job: copy}, e
}
func (s *MemoryStore) Lease(ctx context.Context, id, owner string, ttl time.Duration) (Lease, error) {
	if err := ctx.Err(); err != nil {
		return Lease{}, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.leaseLocked(id, owner, ttl)
}
func (s *MemoryStore) leaseLocked(id, owner string, ttl time.Duration) (Lease, error) {
	if owner == "" || ttl < time.Millisecond {
		return Lease{}, fmt.Errorf("invalid lease")
	}
	j, ok := s.jobs[id]
	if !ok || j.OrgID == "" {
		return Lease{}, ErrVideoJobNotFound
	}
	now := s.opts.Now()
	if l, ok := s.leases[id]; ok && now.Before(l.ExpiresAt) {
		return Lease{}, ErrLeaseHeld
	}
	j.LeaseFence++
	j.LeaseOwner = owner
	_, takeover := s.leases[id]
	l := Lease{Takeover: takeover, JobID: id, Owner: owner, Fence: j.LeaseFence, ExpiresAt: now.Add(ttl)}
	s.leases[id] = l
	return l, nil
}
func (s *MemoryStore) fenced(l Lease) bool {
	current, ok := s.leases[l.JobID]
	j := s.jobs[l.JobID]
	return ok && j != nil && j.OrgID != "" && current.Owner == l.Owner && current.Fence == l.Fence && j.LeaseFence == l.Fence && s.opts.Now().Before(current.ExpiresAt)
}
func (s *MemoryStore) Renew(ctx context.Context, l Lease, ttl time.Duration) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.fenced(l) {
		return ErrLeaseLost
	}
	if ttl < time.Millisecond {
		return fmt.Errorf("invalid lease TTL")
	}
	l.ExpiresAt = s.opts.Now().Add(ttl)
	s.leases[l.JobID] = l
	return nil
}
func (s *MemoryStore) Release(ctx context.Context, l Lease) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.fenced(l) {
		return ErrLeaseLost
	}
	delete(s.leases, l.JobID)
	return nil
}
func (s *MemoryStore) Save(ctx context.Context, j *VideoJob, l Lease) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if j == nil || j.ID != l.JobID {
		return ErrLeaseLost
	}
	copy, err := cloneJob(j)
	if err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.fenced(l) {
		return ErrLeaseLost
	}
	old := s.jobs[j.ID]
	if err := validateImmutable(old, copy); err != nil {
		return err
	}
	if err := ValidateTransition(old.Status, old.Phase, copy.Status, copy.Phase); err != nil && !authorizedResubmit(old, copy) {
		return err
	}
	if old.DeletedAt != nil {
		copy.DeletedAt = old.DeletedAt
	}
	stripDeleted(copy)
	if err := validateCompletion(old, copy); err != nil {
		return err
	}
	if err := validateJob(copy); err != nil {
		return err
	}
	copy.LeaseFence = l.Fence
	copy.LeaseOwner = l.Owner
	copy.UpdatedAt = s.opts.Now()
	s.jobs[j.ID] = copy
	return nil
}
func (s *MemoryStore) Update(j *VideoJob) error {
	ctx := context.Background()
	l, err := s.Lease(ctx, j.ID, NewID(), time.Minute)
	if err != nil {
		return err
	}
	defer s.Release(ctx, l)
	return s.Save(ctx, j, l)
}
func (s *MemoryStore) Tombstone(ctx context.Context, id, org string, l Lease) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	j := s.jobs[id]
	if j == nil || j.OrgID == "" || j.OrgID != org {
		return ErrVideoJobNotFound
	}
	if id != l.JobID || !s.fenced(l) {
		return ErrLeaseLost
	}
	if j.DeletedAt == nil {
		now := s.opts.Now()
		j.DeletedAt = &now
	}
	stripDeleted(j)
	return nil
}
func (s *MemoryStore) Delete(id string) error {
	ctx := context.Background()
	j, err := s.GetAccounting(ctx, id)
	if err != nil {
		return err
	}
	l, err := s.Lease(ctx, id, NewID(), time.Minute)
	if err != nil {
		return err
	}
	defer s.Release(ctx, l)
	return s.Tombstone(ctx, id, j.OrgID, l)
}
func (s *MemoryStore) ListByOrg(org string, f VideoListFilters) ([]*VideoJob, int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	var all []*VideoJob
	for _, j := range s.jobs {
		copy, err := cloneJob(j)
		if err != nil {
			return nil, 0, err
		}
		all = append(all, copy)
	}
	return filterJobs(all, org, f)
}
func (s *MemoryStore) GetActiveJobs() ([]*VideoJob, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := []*VideoJob{}
	for _, j := range s.jobs {
		if j.OrgID != "" && !j.IsTerminal() && j.DeletedAt == nil {
			copy, err := cloneJob(j)
			if err != nil {
				return nil, err
			}
			out = append(out, copy)
		}
	}
	return out, nil
}
func (s *MemoryStore) GarbageCollect() (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	n := 0
	now := s.opts.Now()
	for id, j := range s.jobs {
		if j.ExpiresAt.IsZero() || now.Before(j.ExpiresAt) || !j.IdempotencyExpiresAt.IsZero() && now.Before(j.IdempotencyExpiresAt) || protected(j) {
			continue
		}
		if l, ok := s.leases[id]; ok && now.Before(l.ExpiresAt) {
			continue
		}
		delete(s.jobs, id)
		if s.idem[idemScope(j)] == j.ID {
			delete(s.idem, idemScope(j))
		}
		delete(s.leases, id)
		n++
	}
	return n, nil
}
func (s *MemoryStore) claim(ctx context.Context, queue, owner string, ttl time.Duration, limit int) ([]Claim, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if limit < 1 || ttl < time.Millisecond || owner == "" {
		return nil, fmt.Errorf("invalid claim")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	out := []Claim{}
	for _, j := range s.jobs {
		if j.OrgID == "" || !inQueue(j, queue) || !j.NextPollAt.IsZero() && s.opts.Now().Before(j.NextPollAt) {
			continue
		}
		l, err := s.leaseLocked(j.ID, owner, ttl)
		if errors.Is(err, ErrLeaseHeld) {
			continue
		}
		if err != nil {
			return nil, err
		}
		copy, err := cloneJob(j)
		if err != nil {
			return nil, err
		}
		out = append(out, Claim{Job: copy, Lease: l})
		if len(out) == limit {
			break
		}
	}
	return out, nil
}
func inQueue(j *VideoJob, q string) bool {
	if j.DeletedAt != nil && !protected(j) {
		return false
	}
	switch q {
	case "submit":
		return j.Status == StatusSubmitting && j.DeletedAt == nil
	case "reconcile":
		return j.Status == StatusSubmissionUnknown || j.ReconcileState == ReconcilePending || j.ReconcileState == ReconcileUnresolved
	case "due":
		return j.Status == StatusQueued || j.Status == StatusRunning || j.IsTerminal() && (j.SettlementState == SettlementReserved || j.SettlementState == SettlementUnsettled) && j.ReconcileState != ReconcilePending && j.ReconcileState != ReconcileUnresolved
	}
	return false
}
func (s *MemoryStore) ClaimSubmit(ctx context.Context, o string, ttl time.Duration, n int) ([]Claim, error) {
	return s.claim(ctx, "submit", o, ttl, n)
}
func (s *MemoryStore) ClaimDue(ctx context.Context, o string, ttl time.Duration, n int) ([]Claim, error) {
	return s.claim(ctx, "due", o, ttl, n)
}
func (s *MemoryStore) ClaimReconcile(ctx context.Context, o string, ttl time.Duration, n int) ([]Claim, error) {
	return s.claim(ctx, "reconcile", o, ttl, n)
}

func (s *MemoryStore) idempotentIDLocked(j *VideoJob) string {
	scope := idemScope(j)
	id := s.idem[scope]
	if old := s.jobs[id]; old != nil && !protected(old) && !old.IdempotencyExpiresAt.IsZero() && !s.opts.Now().Before(old.IdempotencyExpiresAt) {
		delete(s.idem, scope)
		return ""
	}
	return id
}

func (s *MemoryStore) ListAccounting(ctx context.Context) ([]*VideoJob, error) {
	if e := ctx.Err(); e != nil {
		return nil, e
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	out := []*VideoJob{}
	for _, j := range s.jobs {
		if j.OrgID != "" {
			c, e := cloneJob(j)
			if e != nil {
				return nil, e
			}
			out = append(out, c)
		}
	}
	return out, nil
}
