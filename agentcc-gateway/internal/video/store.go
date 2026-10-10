package video

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"math"
	"sort"
	"strings"
	"time"

	"github.com/oklog/ulid/v2"
)

var (
	ErrVideoJobNotFound     = errors.New("video job not found")
	ErrReservationUncertain = errors.New("video reservation outcome uncertain; retained for reconciliation")
	// Foreign IDs deliberately have the same observable error as missing IDs.
	ErrVideoJobForbidden   = ErrVideoJobNotFound
	ErrVideoJobExists      = errors.New("video job already exists")
	ErrIdempotencyConflict = errors.New("idempotency_conflict")
	ErrDeletedJob          = errors.New("deleted_job")
	ErrLeaseHeld           = errors.New("video lease held")
	ErrLeaseLost           = errors.New("video lease lost")
	ErrStoreUnavailable    = errors.New("video_store_unavailable")
	ErrActiveLimit         = errors.New("video active job limit exceeded")
	ErrBudgetExceeded      = errors.New("budget_exceeded")
	ErrCompensationFailed  = errors.New("video reservation compensation failed")
)

type VideoListFilters struct {
	Status, Model string
	Limit, Offset int
	Order         string
}
type Store interface {
	Create(*VideoJob) error
	Get(string) (*VideoJob, error)
	GetForOrg(string, string) (*VideoJob, error)
	Update(*VideoJob) error
	Delete(string) error
	ListByOrg(string, VideoListFilters) ([]*VideoJob, int, error)
	GetActiveJobs() ([]*VideoJob, error)
	GarbageCollect() (int, error)
	Accept(context.Context, AcceptRequest) (AcceptResult, error)
	GetAccounting(context.Context, string) (*VideoJob, error)
	ListAccounting(context.Context) ([]*VideoJob, error)
	Lease(context.Context, string, string, time.Duration) (Lease, error)
	Renew(context.Context, Lease, time.Duration) error
	Release(context.Context, Lease) error
	Save(context.Context, *VideoJob, Lease) error
	Tombstone(context.Context, string, string, Lease) error
	ClaimSubmit(context.Context, string, time.Duration, int) ([]Claim, error)
	ClaimDue(context.Context, string, time.Duration, int) ([]Claim, error)
	ClaimReconcile(context.Context, string, time.Duration, int) ([]Claim, error)
}

// BudgetRecorder is implemented by the EXISTING redisstate.BudgetStore. Video
// stores no independent spend counter. Scope selection belongs to the lifecycle.
type BudgetRecorder interface {
	CheckAndRecordSpend(org, level, key, period, model string, cost, limit, modelLimit float64) (total float64, allowed, ok bool)
	RecordSpend(org, level, key, period, model string, cost float64) (float64, bool)
}
type BudgetReservation struct {
	Level, Key, Period string
	Limit, ModelLimit  float64
}
type AcceptRequest struct {
	OrgMaxActive, AccountMaxActive int
	Job                            *VideoJob
	Operation, IdempotencyKey      string
	Reservations                   []BudgetReservation
}
type AcceptResult struct {
	Job    *VideoJob
	Replay bool
}
type Lease struct {
	Takeover     bool
	JobID, Owner string
	Fence        int64
	ExpiresAt    time.Time
}
type Claim struct {
	Job   *VideoJob
	Lease Lease
}
type StoreOptions struct {
	Prefix         string
	Budget         BudgetRecorder
	IdempotencyTTL time.Duration
	Now            func() time.Time
}

func defaults(opts StoreOptions) StoreOptions {
	if opts.Prefix == "" {
		opts.Prefix = "video:v1:"
	}
	if opts.IdempotencyTTL <= 0 {
		opts.IdempotencyTTL = 720 * time.Hour
	}
	if opts.Now == nil {
		opts.Now = time.Now
	}
	return opts
}
func NewID() string          { return "video_" + ulid.Make().String() }
func digest(s string) string { h := sha256.Sum256([]byte(s)); return hex.EncodeToString(h[:]) }
func prepareAccept(req AcceptRequest, opts StoreOptions) (*VideoJob, error) {
	if req.Job == nil || req.Job.OrgID == "" {
		return nil, fmt.Errorf("missing_org")
	}
	if req.Operation == "" || strings.ContainsAny(req.Operation, ":{}\x00") {
		return nil, fmt.Errorf("invalid idempotency operation")
	}
	if len(req.IdempotencyKey) < 1 || len(req.IdempotencyKey) > 255 {
		return nil, fmt.Errorf("missing_idempotency_key")
	}
	for _, ch := range req.IdempotencyKey {
		if ch < 32 || ch > 126 {
			return nil, fmt.Errorf("invalid idempotency key")
		}
	}
	j, err := cloneJob(req.Job)
	if err != nil {
		return nil, err
	}
	if j.ID == "" {
		j.ID = NewID()
	}
	if j.Fingerprint == "" {
		return nil, fmt.Errorf("fingerprint required")
	}
	if j.Status != "" && j.Status != StatusSubmitting || j.Phase != "" && j.Phase != PhasePrepared {
		return nil, fmt.Errorf("accept requires prepared submission")
	}
	j.Status = StatusSubmitting
	j.Phase = PhasePrepared
	j.IdemDigest = digest(req.IdempotencyKey)
	j.IdemOp = req.Operation
	j.OrgMaxActive = req.OrgMaxActive
	j.AccountMaxActive = req.AccountMaxActive
	j.Reservations = append([]BudgetReservation(nil), req.Reservations...)
	now := opts.Now().UTC()
	if j.CreatedAt.IsZero() {
		j.CreatedAt = now
	}
	j.UpdatedAt = now
	if j.ExpiresAt.IsZero() {
		j.ExpiresAt = now.Add(720 * time.Hour)
	}
	j.IdempotencyExpiresAt = now.Add(opts.IdempotencyTTL)
	if j.ReservedMicros > 0 {
		j.SettlementState = SettlementReserved
	}
	if err := validateJob(j); err != nil {
		return nil, err
	}
	seen := map[string]bool{}
	for _, scope := range req.Reservations {
		key := scope.Level + "\x00" + scope.Key
		if seen[key] {
			return nil, fmt.Errorf("duplicate budget scope")
		}
		seen[key] = true
		if scope.Level == "" || scope.Period == "" || scope.Limit < 0 || scope.ModelLimit < 0 || math.IsNaN(scope.Limit) || math.IsNaN(scope.ModelLimit) || math.IsInf(scope.Limit, 0) || math.IsInf(scope.ModelLimit, 0) {
			return nil, fmt.Errorf("invalid budget scope")
		}
	}
	return j, nil
}
func reserve(opts StoreOptions, j *VideoJob) (func() error, error) {
	rollback := func() error { return nil }
	if j.ReservedMicros == 0 || len(j.Reservations) == 0 {
		return rollback, nil
	}
	if opts.Budget == nil {
		return nil, ErrStoreUnavailable
	}
	var recorded []BudgetReservation
	cost := float64(j.ReservedMicros) / 1e6
	rollback = func() error {
		var result error
		for i := len(recorded) - 1; i >= 0; i-- {
			s := recorded[i]
			if _, ok := opts.Budget.RecordSpend(j.OrgID, s.Level, s.Key, s.Period, j.Model, -cost); !ok {
				result = errors.Join(result, ErrCompensationFailed)
			}
		}
		return result
	}
	for _, s := range j.Reservations {
		_, allowed, ok := opts.Budget.CheckAndRecordSpend(j.OrgID, s.Level, s.Key, s.Period, j.Model, cost, s.Limit, s.ModelLimit)
		if !ok {
			return nil, errors.Join(ErrStoreUnavailable, rollback())
		}
		if !allowed {
			return nil, errors.Join(ErrBudgetExceeded, rollback())
		}
		recorded = append(recorded, s)
	}
	return rollback, nil
}
func replay(j *VideoJob, fp string) (AcceptResult, error) {
	r := AcceptResult{Job: j, Replay: true}
	if j.Fingerprint != fp {
		return r, ErrIdempotencyConflict
	}
	if j.DeletedAt != nil {
		return r, ErrDeletedJob
	}
	return r, nil
}
func validateFilters(f VideoListFilters) error {
	if f.Limit < 1 || f.Limit > 100 || f.Offset < 0 || f.Order != "" && f.Order != "asc" && f.Order != "desc" || f.Status != "" && !validState(f.Status) {
		return fmt.Errorf("invalid_filter")
	}
	return nil
}
func filterJobs(all []*VideoJob, org string, f VideoListFilters) ([]*VideoJob, int, error) {
	if err := validateFilters(f); err != nil {
		return nil, 0, err
	}
	out := make([]*VideoJob, 0)
	for _, j := range all {
		if org == "" || j.OrgID != org || j.OrgID == "" || j.DeletedAt != nil || f.Status != "" && j.Status != f.Status || f.Model != "" && j.Model != f.Model {
			continue
		}
		out = append(out, j)
	}
	sort.Slice(out, func(i, j int) bool {
		a, b := out[i], out[j]
		less := a.CreatedAt.Before(b.CreatedAt) || a.CreatedAt.Equal(b.CreatedAt) && a.ID < b.ID
		if f.Order == "asc" {
			return less
		}
		return b.CreatedAt.Before(a.CreatedAt) || a.CreatedAt.Equal(b.CreatedAt) && b.ID < a.ID
	})
	n := len(out)
	if f.Offset >= n {
		return []*VideoJob{}, n, nil
	}
	end := f.Offset + f.Limit
	if end > n {
		end = n
	}
	return out[f.Offset:end], n, nil
}
func validateImmutable(old, next *VideoJob) error {
	if old.KeyID != next.KeyID || old.Creator != next.Creator || old.TariffRevision != next.TariffRevision || old.ReservedMicros != next.ReservedMicros || !old.CreatedAt.Equal(next.CreatedAt) || !old.SubmitBy.Equal(next.SubmitBy) || !old.RunBy.IsZero() && !old.RunBy.Equal(next.RunBy) || old.ProviderJobID != "" && old.ProviderJobID != next.ProviderJobID || old.ID != next.ID || old.OrgID != next.OrgID || old.Service != next.Service || old.ModelID != next.ModelID || old.Region != next.Region || old.AccountRef != next.AccountRef || old.Model != next.Model || old.CapabilityRevision != next.CapabilityRevision || old.Fingerprint != next.Fingerprint || old.IdemDigest != next.IdemDigest || old.IdemOp != next.IdemOp {
		return fmt.Errorf("video pinned identity is immutable")
	}
	return nil
}
