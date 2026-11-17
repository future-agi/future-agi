package batch

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"sort"
	"strings"
	"time"
)

// ErrNotFound is the answer for a batch that does not exist and for another
// org's batch alike, so neither reveals the other.
var ErrNotFound = errors.New("batch not found")

// ErrStateUnavailable means the shared batch state could not be read or
// written: a submission it refuses ran nothing, and a batch it hides is not
// known to be gone.
var ErrStateUnavailable = errors.New("batch state unavailable")

// OwnerLost is the error code of a batch whose owner stopped holding its
// lease before the batch's outcome was stored. The batch reads as failed,
// stays failed, and no replica runs the items it had not started.
const OwnerLost = "owner_lost"

// SharedStore keeps batches where every replica of a cluster reads them (the
// gateway's Redis, redisstate.BatchStore). A batch has a record (identity,
// state and counts, never a credential), one result per item that ran, the
// lease of the replica that runs it, and a cancel request. Every write keeps
// the batch for ttl after it.
type SharedStore interface {
	// Create writes a new batch's record, its owner's lease (for lease) and
	// its listing entries (its org's and every org's), in one transaction.
	Create(ctx context.Context, id, orgID, owner string, record []byte, lease, ttl time.Duration) error
	// Save writes w while w.Owner holds the batch's lease; a final write also
	// ends the lease and any cancel request. Once the lease is gone, the
	// outcome every replica already reads stands: a processing record is
	// written as w.Lost, an ended one only when its status is w.Status, and
	// a batch that has expired is not written at all. kept is false when the
	// stored status is not w.Status.
	Save(ctx context.Context, w Write) (kept bool, err error)
	// Renew extends owner's lease on the batch: held is false once the lease
	// is gone. cancel reports a cancel request from any replica.
	Renew(ctx context.Context, id, orgID, owner string, lease, ttl time.Duration) (held, cancel bool, err error)
	// Load reads one batch with its results; found is false when there is
	// none.
	Load(ctx context.Context, id string) (stored StoredBatch, found bool, err error)
	// RequestCancel asks the batch's owner to cancel it.
	RequestCancel(ctx context.Context, id string, ttl time.Duration) error
	// List reads every batch of orgID, without results.
	List(ctx context.Context, orgID string) ([]StoredBatch, error)
	// ListAll reads every org's batches, without results.
	ListAll(ctx context.Context) ([]StoredBatch, error)
}

// Write is one write of a batch to a SharedStore by the replica that runs it.
type Write struct {
	ID, OrgID, Owner string
	// Status is Record's status. Lost is Record as failed with OwnerLost,
	// what a processing batch becomes once its owner's lease is gone.
	Status       string
	Record, Lost []byte
	// Results are item results by index.
	Results map[int][]byte
	// Final ends the owner's lease and any cancel request.
	Final bool
	TTL   time.Duration
}

// StoredBatch is one batch as a SharedStore holds it.
type StoredBatch struct {
	Record []byte
	// Results are the item results by index (Load only).
	Results map[int][]byte
	// LeaseHeld reports that the owner still holds its lease.
	LeaseHeld bool
}

const (
	defaultOwnerLease = 10 * time.Second
	defaultLeaseRenew = time.Second
	// sharedStoreTimeout bounds one read or write of the shared store.
	sharedStoreTimeout = 2 * time.Second
)

// WithSharedStore keeps every batch in store, so any replica of a cluster
// gets, lists and cancels a batch another one runs; the replica that took a
// batch runs all of it. owner names this replica in the batches it runs (a
// random id when empty).
func WithSharedStore(store SharedStore, owner string) Option {
	return func(p *Processor) {
		if owner == "" {
			owner = randomOwner()
		}
		p.shared, p.owner = store, owner
	}
}

// WithOwnerLease sets how long a batch owner's lease lasts and how often the
// owner renews it and reads cancel requests: by default 10 s and 1 s. renew
// must be at most a third of lease, or the defaults stay. A cancel sent to
// another replica waits up to three renewals for the owner's answer.
func WithOwnerLease(lease, renew time.Duration) Option {
	return func(p *Processor) {
		if renew > 0 && lease >= 3*renew {
			p.lease, p.renewEvery = lease, renew
		}
	}
}

// Shared reports whether the processor keeps its batches in a shared store.
func (p *Processor) Shared() bool { return p != nil && p.shared != nil }

func randomOwner() string {
	b := make([]byte, 6)
	if _, err := rand.Read(b); err != nil {
		panic("crypto/rand.Read failed: " + err.Error())
	}
	return "replica-" + hex.EncodeToString(b)
}

// ownerClock is the time a batch owner's lease loop runs on.
type ownerClock interface {
	Now() time.Time
	NewTicker(every time.Duration) (ticks <-chan time.Time, stop func())
}

type realClock struct{}

func (realClock) Now() time.Time { return time.Now() }

func (realClock) NewTicker(every time.Duration) (<-chan time.Time, func()) {
	t := time.NewTicker(every)
	return t.C, t.Stop
}

// sharedRecord is what a shared store keeps of a batch besides its results:
// identity, state and counts, never a credential.
type sharedRecord struct {
	ID          string     `json:"batch_id"`
	Status      string     `json:"status"`
	ErrorCode   string     `json:"error_code,omitempty"`
	OrgID       string     `json:"org_id"`
	KeyID       string     `json:"key_id,omitempty"`
	Total       int        `json:"total"`
	MaxConc     int        `json:"max_concurrency"`
	CreatedAt   time.Time  `json:"created_at"`
	CompletedAt *time.Time `json:"completed_at,omitempty"`
	Counts      Counts     `json:"counts"`
	Owner       string     `json:"owner"`
}

// decodeRecord reads a stored record; one without an id or with a negative
// total cannot be read.
func decodeRecord(raw []byte) (sharedRecord, error) {
	var rec sharedRecord
	if err := json.Unmarshal(raw, &rec); err != nil {
		return rec, fmt.Errorf("%w: the stored record does not decode: %w", ErrStateUnavailable, err)
	}
	if rec.ID == "" || rec.Total < 0 {
		return rec, fmt.Errorf("%w: the stored record has no id or a negative total", ErrStateUnavailable)
	}
	return rec, nil
}

// observed is the state every replica reads for a stored batch: a
// processing batch whose owner no longer holds its lease has failed
// (OwnerLost). A cancel request alone changes nothing: a batch reads
// cancelled once its owner has stopped it and stored that.
func (rec *sharedRecord) observed(leaseHeld bool) (status, code string) {
	if rec.Status == "processing" && !leaseHeld {
		return "failed", OwnerLost
	}
	return rec.Status, rec.ErrorCode
}

// write is b's record for the shared store, as Status and as Lost.
func (b *Batch) write(owner string, ttl time.Duration) (Write, error) {
	b.mu.Lock()
	rec := sharedRecord{ID: b.ID, Status: b.Status, ErrorCode: b.ErrorCode, OrgID: b.OrgID, KeyID: b.KeyID, Total: b.Total, MaxConc: b.MaxConc, CreatedAt: b.CreatedAt, CompletedAt: b.CompletedAt, Owner: owner}
	b.mu.Unlock()
	rec.Counts = Counts{Completed: int(b.completed.Load()), Failed: int(b.failed.Load()), Cancelled: int(b.cancelled.Load())}
	w := Write{ID: b.ID, OrgID: b.OrgID, Owner: owner, Status: rec.Status, TTL: ttl}
	var err error
	if w.Record, err = json.Marshal(rec); err != nil {
		return Write{}, err
	}
	rec.Status, rec.ErrorCode = "failed", OwnerLost
	if w.Lost, err = json.Marshal(rec); err != nil {
		return Write{}, err
	}
	return w, nil
}

// encodeResults is every result b holds, encoded for the shared store.
func (b *Batch) encodeResults() (map[int][]byte, error) {
	results := b.GetResults()
	out := make(map[int][]byte, len(results))
	for i, r := range results {
		if r == nil {
			continue
		}
		raw, err := json.Marshal(r)
		if err != nil {
			return nil, err
		}
		out[i] = raw
	}
	return out, nil
}

// settled reports that every replica reads b's status as this replica holds
// it: b is processing, or its status is the one last stored. b.mu is held.
func (b *Batch) settled() bool {
	return b.Status == "processing" || b.Status == b.storedStatus
}

// settledView is a copy of b, taken in the same locked read that finds b
// settled; ok is false when it is not. The copy never changes, so an answer
// built from it holds no outcome the shared store has not taken, even when
// b's run ends while the answer is written.
func (b *Batch) settledView() (view *Batch, ok bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if !b.settled() {
		return nil, false
	}
	view = &Batch{ID: b.ID, Status: b.Status, ErrorCode: b.ErrorCode, OrgID: b.OrgID, KeyID: b.KeyID, Total: b.Total, Results: append([]*Result(nil), b.Results...), MaxConc: b.MaxConc, CreatedAt: b.CreatedAt, CompletedAt: b.CompletedAt, cancel: func() {}}
	view.completed.Store(b.completed.Load())
	view.failed.Store(b.failed.Load())
	view.cancelled.Store(b.cancelled.Load())
	return view, true
}

// settledRow is b's listing row if keep accepts its org and b is settled,
// both checked in the same locked read as the row's fields.
func (b *Batch) settledRow(keep func(orgID string) bool) (ListRow, bool) {
	return b.rowIf(func() bool { return keep(b.OrgID) && b.settled() })
}

// stored records that the shared store took b with status.
func (b *Batch) stored(status string) {
	b.mu.Lock()
	b.storedStatus = status
	b.mu.Unlock()
}

// lose ends b as failed (OwnerLost), whatever this replica had decided, once
// its lease is gone and the store keeps it so; its workers stop.
func (b *Batch) lose() {
	b.mu.Lock()
	b.Status, b.ErrorCode, b.storedStatus = "failed", OwnerLost, "failed"
	b.mu.Unlock()
	b.cancel()
}

// sharedView reads a stored batch, if it is orgID's, from the org check on:
// another org's batch is ErrNotFound before anything else of it is read.
// The view is a copy whose state is derived on read (observed) and never
// written back.
func sharedView(stored StoredBatch, orgID string) (*Batch, error) {
	rec, err := decodeRecord(stored.Record)
	if err != nil {
		return nil, err
	}
	if rec.OrgID != orgID {
		return nil, ErrNotFound
	}
	b := &Batch{ID: rec.ID, OrgID: rec.OrgID, KeyID: rec.KeyID, Total: rec.Total, MaxConc: rec.MaxConc, CreatedAt: rec.CreatedAt, CompletedAt: rec.CompletedAt, Results: make([]*Result, rec.Total), cancel: func() {}}
	b.Status, b.ErrorCode = rec.observed(stored.LeaseHeld)
	for i := range b.Results {
		b.Results[i] = &Result{Index: i, Status: "pending"}
		raw, ok := stored.Results[i]
		if !ok {
			continue
		}
		var r Result
		if err := json.Unmarshal(raw, &r); err != nil {
			return nil, fmt.Errorf("%w: a stored result does not decode: %w", ErrStateUnavailable, err)
		}
		r.Index = i
		b.Results[i] = &r
	}
	b.completed.Store(int32(rec.Counts.Completed))
	b.failed.Store(int32(rec.Counts.Failed))
	b.cancelled.Store(int32(rec.Counts.Cancelled))
	return b, nil
}

// listRowOf is a stored batch's listing row, if keep accepts its org, read
// from its record alone: a listing never builds item results.
func listRowOf(stored StoredBatch, keep func(orgID string) bool) (ListRow, bool, error) {
	rec, err := decodeRecord(stored.Record)
	if err != nil || !keep(rec.OrgID) {
		return ListRow{}, false, err
	}
	row := ListRow{ID: rec.ID, OrgID: rec.OrgID, KeyID: rec.KeyID, Total: rec.Total, Counts: rec.Counts, CreatedAt: rec.CreatedAt, CompletedAt: rec.CompletedAt}
	row.Status, row.ErrorCode = rec.observed(stored.LeaseHeld)
	return row, true, nil
}

// issuedBatchID reports whether id has the shape of an id this gateway
// issues: "batch-" and twelve lowercase hex digits. With a shared store an id
// names Redis keys, so an id of any other shape is not found without a
// lookup: a path cannot name another key of the store's.
func issuedBatchID(id string) bool {
	digits, ok := strings.CutPrefix(id, "batch-")
	if !ok || len(digits) != 12 {
		return false
	}
	for _, c := range digits {
		if (c < '0' || c > '9') && (c < 'a' || c > 'f') {
			return false
		}
	}
	return true
}

// GetFor is GetForOrg that tells a batch that does not exist (ErrNotFound,
// also for another org's batch and for an id this gateway never issues) from
// shared state it could not read (ErrStateUnavailable). With a shared store,
// a batch this replica runs is answered from a copy of it taken while every
// replica reads it so (settledView); otherwise, and for another replica's
// batch, from the shared store.
func (p *Processor) GetFor(batchID, orgID string) (*Batch, error) {
	if p.shared != nil && !issuedBatchID(batchID) {
		return nil, ErrNotFound
	}
	if b, ok := p.Get(batchID); ok {
		if b.OrgID != orgID {
			return nil, ErrNotFound
		}
		if p.shared == nil {
			return b, nil
		}
		if view, ok := b.settledView(); ok {
			return view, nil
		}
	} else if p.shared == nil {
		return nil, ErrNotFound
	}
	ctx, cancel := context.WithTimeout(context.Background(), sharedStoreTimeout)
	defer cancel()
	stored, found, err := p.shared.Load(ctx, batchID)
	if err != nil {
		return nil, fmt.Errorf("%w: %w", ErrStateUnavailable, err)
	}
	if !found {
		return nil, ErrNotFound
	}
	return sharedView(stored, orgID)
}

// CancelFor is CancelForOrg with GetFor's errors. A batch this replica runs
// stops here. A batch another replica runs gets a cancel request in the
// shared store, which its owner reads at its next lease renewal; the answer
// is the batch once its owner has stopped it, or still processing when the
// owner has not answered within three renewals. Another org's batch is not
// found, and keeps running with no request left.
func (p *Processor) CancelFor(batchID, orgID string) (*Batch, error) {
	if p.shared != nil && !issuedBatchID(batchID) {
		return nil, ErrNotFound
	}
	if b, ok := p.Get(batchID); ok {
		if b.OrgID != orgID {
			return nil, ErrNotFound
		}
		p.stop(b, "cancelled", "")
		return p.GetFor(batchID, orgID)
	}
	b, err := p.GetFor(batchID, orgID)
	if err != nil || b.GetStatus() != "processing" {
		return b, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), sharedStoreTimeout)
	err = p.shared.RequestCancel(ctx, batchID, p.retention)
	cancel()
	if err != nil {
		return nil, fmt.Errorf("%w: %w", ErrStateUnavailable, err)
	}
	return p.awaitOwner(b, orgID)
}

// awaitOwner reads b, which another replica runs and was asked to cancel,
// until it is no longer processing or three lease renewals have passed, and
// returns it as last read.
func (p *Processor) awaitOwner(b *Batch, orgID string) (*Batch, error) {
	deadline := time.NewTimer(3 * p.renewEvery)
	defer deadline.Stop()
	poll := time.NewTicker(max(p.renewEvery/4, time.Millisecond))
	defer poll.Stop()
	for {
		select {
		case <-deadline.C:
			return b, nil
		case <-poll.C:
		}
		read, err := p.GetFor(b.ID, orgID)
		if err != nil {
			return nil, err
		}
		if b = read; b.GetStatus() != "processing" {
			return b, nil
		}
	}
}

// ListFor is OrgInventory with GetFor's errors: with a shared store, the
// org's batches on every replica, sorted by id.
func (p *Processor) ListFor(orgID string) ([]ListRow, error) {
	if orgID == "" {
		return []ListRow{}, nil
	}
	return p.listShared(func(org string) bool { return org == orgID }, func(ctx context.Context) ([]StoredBatch, error) {
		return p.shared.List(ctx, orgID)
	})
}

// ClusterInventory is Inventory with GetFor's errors: with a shared store,
// every org's batches on every replica, sorted by id.
func (p *Processor) ClusterInventory() ([]ListRow, error) {
	return p.listShared(func(string) bool { return true }, func(ctx context.Context) ([]StoredBatch, error) {
		return p.shared.ListAll(ctx)
	})
}

// listShared lists the batches of the orgs keep accepts: this replica's
// alone without a shared store, else those read lists from the store, each
// as every replica reads it. A batch this replica runs is read from memory
// while it is settled (settledRow).
func (p *Processor) listShared(keep func(orgID string) bool, read func(context.Context) ([]StoredBatch, error)) ([]ListRow, error) {
	if p.shared == nil {
		return p.listRows(keep), nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), sharedStoreTimeout)
	defer cancel()
	stored, err := read(ctx)
	if err != nil {
		return nil, fmt.Errorf("%w: %w", ErrStateUnavailable, err)
	}
	byID := make(map[string]ListRow, len(stored))
	for _, s := range stored {
		row, ok, err := listRowOf(s, keep)
		if err != nil {
			return nil, err
		}
		if ok {
			byID[row.ID] = row
		}
	}
	p.batches.Range(func(_, value any) bool {
		if row, ok := value.(*Batch).settledRow(keep); ok {
			byID[row.ID] = row
		}
		return true
	})
	rows := make([]ListRow, 0, len(byID))
	for _, row := range byID {
		rows = append(rows, row)
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].ID < rows[j].ID })
	return rows, nil
}

// createShared writes b's record and this replica's lease on it, before any
// of its items runs.
func (p *Processor) createShared(b *Batch) error {
	w, err := b.write(p.owner, p.retention)
	if err != nil {
		return fmt.Errorf("%w: %w", ErrStateUnavailable, err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), sharedStoreTimeout)
	defer cancel()
	if err := p.shared.Create(ctx, b.ID, b.OrgID, p.owner, w.Record, p.lease, p.retention); err != nil {
		return fmt.Errorf("%w: %w", ErrStateUnavailable, err)
	}
	b.stored(w.Status)
	return nil
}

// publish writes b's record and results to the shared store, one write at a
// time per batch, and nothing once its final write has been stored; a final
// write takes every result and ends this replica's lease. When the store
// keeps another outcome because the lease is gone, b takes it: failed
// (OwnerLost).
func (p *Processor) publish(b *Batch, results map[int][]byte, final bool) error {
	b.publishMu.Lock()
	defer b.publishMu.Unlock()
	if b.finalStored {
		return nil
	}
	w, err := b.write(p.owner, p.retention)
	if err != nil {
		return err
	}
	w.Results, w.Final = results, final
	if final {
		if w.Results, err = b.encodeResults(); err != nil {
			return err
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), sharedStoreTimeout)
	defer cancel()
	kept, err := p.shared.Save(ctx, w)
	if err != nil {
		return err
	}
	if kept {
		b.stored(w.Status)
	} else {
		b.lose()
	}
	b.finalStored = final
	return nil
}

// publishProgress writes b's record after a change, with result when an
// item ran. A write that fails is logged: the final write carries
// everything.
func (p *Processor) publishProgress(b *Batch, result *Result) {
	if b.finished == nil {
		return
	}
	var results map[int][]byte
	if result != nil {
		raw, err := json.Marshal(result)
		if err != nil {
			slog.Warn("batch item result not encoded for the shared store", "batch_id", b.ID, "index", result.Index, "error", err)
			return
		}
		results = map[int][]byte{result.Index: raw}
	}
	if err := p.publish(b, results, false); err != nil {
		slog.Warn("batch progress not stored", "batch_id", b.ID, "error", err)
	}
}

// storeFinal writes b's final record and ends this replica's lease on it;
// false when the store did not take it.
func (p *Processor) storeFinal(b *Batch) bool {
	if err := p.publish(b, nil, true); err != nil {
		slog.Warn("batch final state not stored", "batch_id", b.ID, "error", err)
		return false
	}
	return true
}

// ownBatch holds this replica's lease on b while b runs, from leased (when
// Create sent it), and writes b's final record once b has finished, trying
// again at each renewal until the store takes it. Each renewal also reads
// cancel requests from other replicas. The owner stops b (failed,
// OwnerLost) when the lease is gone, and when it has not renewed it for all
// but two renewals of the lease, so b stops starting items before any
// replica can read it as lost. When the processor stops, b ends the same way
// at once.
func (p *Processor) ownBatch(b *Batch, leased time.Time) {
	ticks, stopTicks := p.clock.NewTicker(p.renewEvery)
	defer stopTicks()
	renewed, finished := leased, b.finished
	for {
		select {
		case <-p.stopCh:
			p.stop(b, "failed", OwnerLost)
			p.storeFinal(b)
			return
		case <-finished:
			finished = nil
			if p.storeFinal(b) {
				return
			}
		case <-ticks:
			if finished == nil && p.storeFinal(b) {
				return
			}
			if p.clock.Now().Sub(renewed) >= p.lease-2*p.renewEvery {
				p.stop(b, "failed", OwnerLost)
			}
			sent := p.clock.Now()
			held, cancelRequested, err := p.renew(b)
			switch {
			case err != nil:
				slog.Warn("batch lease not renewed", "batch_id", b.ID, "error", err)
			case !held:
				p.stop(b, "failed", OwnerLost)
			default:
				renewed = sent
				if cancelRequested {
					p.stop(b, "cancelled", "")
				}
			}
		}
	}
}

func (p *Processor) renew(b *Batch) (held, cancelRequested bool, err error) {
	ctx, cancel := context.WithTimeout(context.Background(), p.renewEvery)
	defer cancel()
	return p.shared.Renew(ctx, b.ID, b.OrgID, p.owner, p.lease, p.retention)
}
