package batch

import (
	"context"
	"encoding/json"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// sharedWait bounds every wait for something a test expects to see.
const sharedWait = 5 * time.Second

const sharedOrg = "org-a"

var sharedCaller = Caller{OrgID: sharedOrg, KeyID: "dlg_shared"}

var errStoreDown = errors.New("this replica's store is unavailable")

// storeEvent is one call a replica made to the shared store: a save
// (status is what the record then holds), a renewal, or a cancel request.
type storeEvent struct {
	replica, op  string
	status       string
	final, kept  bool
	held, cancel bool
	err          error
}

// memStore is the store the replicas of a test cluster share. Each replica
// reaches it through its own view (replica), which can be made unavailable
// alone, and every save, renewal and cancel request is reported on events.
// Its saves keep the outcome every replica reads once a lease is gone, as
// SharedStore.Save says.
type memStore struct {
	mu      sync.Mutex
	records map[string][]byte
	orgs    map[string]string
	results map[string]map[int][]byte
	leases  map[string]string
	cancels map[string]bool
	down    map[string]bool
	events  chan storeEvent
}

func newMemStore() *memStore {
	return &memStore{
		records: map[string][]byte{},
		orgs:    map[string]string{},
		results: map[string]map[int][]byte{},
		leases:  map[string]string{},
		cancels: map[string]bool{},
		down:    map[string]bool{},
		events:  make(chan storeEvent, 1024),
	}
}

func (m *memStore) replica(name string) SharedStore { return &memReplica{m: m, name: name} }

// setDown makes replica's store unavailable, or available again.
func (m *memStore) setDown(replica string, down bool) {
	m.mu.Lock()
	m.down[replica] = down
	m.mu.Unlock()
}

// expireLease ends the owner's lease on id, as its running out would.
func (m *memStore) expireLease(id string) {
	m.mu.Lock()
	delete(m.leases, id)
	m.mu.Unlock()
}

// report hands e to the test; the caller holds m.mu.
func (m *memStore) report(e storeEvent) {
	select {
	case m.events <- e:
	default:
		panic("the test reads none of the store's events")
	}
}

// await waits for the first event match accepts, passing over the others.
func (m *memStore) await(t *testing.T, what string, match func(storeEvent) bool) storeEvent {
	t.Helper()
	timeout := time.After(sharedWait)
	for {
		select {
		case e := <-m.events:
			if match(e) {
				return e
			}
		case <-timeout:
			t.Fatalf("%s did not happen within %s", what, sharedWait)
		}
	}
}

func statusOf(record []byte) string {
	var rec struct {
		Status string `json:"status"`
	}
	_ = json.Unmarshal(record, &rec)
	return rec.Status
}

type memReplica struct {
	m    *memStore
	name string
}

func (r *memReplica) unavailable() bool { return r.m.down[r.name] }

func (r *memReplica) Create(_ context.Context, id, orgID, owner string, record []byte, _, _ time.Duration) error {
	m := r.m
	m.mu.Lock()
	defer m.mu.Unlock()
	if r.unavailable() {
		return errStoreDown
	}
	m.records[id], m.orgs[id], m.leases[id], m.results[id] = record, orgID, owner, map[int][]byte{}
	return nil
}

func (r *memReplica) Save(_ context.Context, w Write) (bool, error) {
	m := r.m
	m.mu.Lock()
	defer m.mu.Unlock()
	if r.unavailable() {
		m.report(storeEvent{replica: r.name, op: "save", final: w.Final, err: errStoreDown})
		return false, errStoreDown
	}
	stored, ok := m.records[w.ID]
	if !ok {
		m.report(storeEvent{replica: r.name, op: "save", final: w.Final})
		return false, nil
	}
	record, kept := w.Record, true
	if m.leases[w.ID] != w.Owner {
		switch status := statusOf(stored); {
		case status == "processing":
			record, kept = w.Lost, w.Status == "failed"
		case status != w.Status:
			record, kept = nil, false
		}
	}
	if record != nil {
		m.records[w.ID] = record
	}
	for index, raw := range w.Results {
		m.results[w.ID][index] = raw
	}
	if w.Final {
		delete(m.leases, w.ID)
		delete(m.cancels, w.ID)
	}
	m.report(storeEvent{replica: r.name, op: "save", status: statusOf(m.records[w.ID]), final: w.Final, kept: kept})
	return kept, nil
}

func (r *memReplica) Renew(_ context.Context, id, _, owner string, _, _ time.Duration) (bool, bool, error) {
	m := r.m
	m.mu.Lock()
	defer m.mu.Unlock()
	if r.unavailable() {
		m.report(storeEvent{replica: r.name, op: "renew", err: errStoreDown})
		return false, false, errStoreDown
	}
	_, held := m.leases[id]
	if held {
		m.leases[id] = owner
	}
	m.report(storeEvent{replica: r.name, op: "renew", held: held, cancel: m.cancels[id]})
	return held, m.cancels[id], nil
}

func (r *memReplica) Load(_ context.Context, id string) (StoredBatch, bool, error) {
	m := r.m
	m.mu.Lock()
	defer m.mu.Unlock()
	if r.unavailable() {
		return StoredBatch{}, false, errStoreDown
	}
	record, ok := m.records[id]
	if !ok {
		return StoredBatch{}, false, nil
	}
	results := make(map[int][]byte, len(m.results[id]))
	for index, raw := range m.results[id] {
		results[index] = raw
	}
	_, held := m.leases[id]
	return StoredBatch{Record: record, Results: results, LeaseHeld: held}, true, nil
}

func (r *memReplica) RequestCancel(_ context.Context, id string, _ time.Duration) error {
	m := r.m
	m.mu.Lock()
	defer m.mu.Unlock()
	if r.unavailable() {
		return errStoreDown
	}
	m.cancels[id] = true
	m.report(storeEvent{replica: r.name, op: "cancel"})
	return nil
}

func (r *memReplica) List(_ context.Context, orgID string) ([]StoredBatch, error) {
	return r.list(func(org string) bool { return org == orgID })
}

func (r *memReplica) ListAll(context.Context) ([]StoredBatch, error) {
	return r.list(func(string) bool { return true })
}

func (r *memReplica) list(keep func(orgID string) bool) ([]StoredBatch, error) {
	m := r.m
	m.mu.Lock()
	defer m.mu.Unlock()
	if r.unavailable() {
		return nil, errStoreDown
	}
	var out []StoredBatch
	for id, record := range m.records {
		if keep(m.orgs[id]) {
			_, held := m.leases[id]
			out = append(out, StoredBatch{Record: record, LeaseHeld: held})
		}
	}
	return out, nil
}

// ownerTicks is a batch owner's clock in a test: its time moves, and its
// lease loop gets a renewal tick, only when the test ticks it.
type ownerTicks struct {
	mu    sync.Mutex
	now   time.Time
	ticks chan time.Time
}

func newOwnerTicks() *ownerTicks {
	return &ownerTicks{now: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), ticks: make(chan time.Time)}
}

func (c *ownerTicks) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

func (c *ownerTicks) NewTicker(time.Duration) (<-chan time.Time, func()) { return c.ticks, func() {} }

// tick moves the clock on by d and hands the lease loop one renewal tick,
// returning once the loop has taken it.
func (c *ownerTicks) tick(t *testing.T, d time.Duration) {
	t.Helper()
	c.mu.Lock()
	c.now = c.now.Add(d)
	now := c.now
	c.mu.Unlock()
	select {
	case c.ticks <- now:
	case <-time.After(sharedWait):
		t.Fatalf("the owner's lease loop took no renewal tick within %s", sharedWait)
	}
}

// itemGate runs a batch's items as the test lets them through: arrived
// reports each item that started, and next lets one finish. ran counts the
// items it ran: what its replica charged.
type itemGate struct {
	arrived chan struct{}
	next    chan struct{}
	ran     atomic.Int32
}

func newItemGate() *itemGate {
	return &itemGate{arrived: make(chan struct{}, 16), next: make(chan struct{})}
}

func (g *itemGate) run(ctx context.Context, _ *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error) {
	g.ran.Add(1)
	g.arrived <- struct{}{}
	select {
	case <-g.next:
		return &models.ChatCompletionResponse{ID: "item", Model: "gpt-4o-mini"}, map[string]string{"cost": "0.001", "cost_known": "true", "billable": "true"}, nil
	case <-ctx.Done():
		return nil, nil, ctx.Err()
	}
}

func (g *itemGate) awaitArrival(t *testing.T, what string) {
	t.Helper()
	select {
	case <-g.arrived:
	case <-time.After(sharedWait):
		t.Fatalf("%s did not start within %s", what, sharedWait)
	}
}

func (g *itemGate) letOneThrough(t *testing.T) {
	t.Helper()
	select {
	case g.next <- struct{}{}:
	case <-time.After(sharedWait):
		t.Fatalf("no item was waiting to finish within %s", sharedWait)
	}
}

// newSharedReplica is one replica on store, named name, running its items
// with run. A replica that owns batches in a test passes its clock; one
// that does not passes nil and keeps real time.
func newSharedReplica(t *testing.T, store *memStore, name string, run BatchHandler, clock ownerClock, opts ...Option) *Processor {
	t.Helper()
	opts = append([]Option{WithSharedStore(store.replica(name), name)}, opts...)
	if clock != nil {
		opts = append(opts, func(p *Processor) { p.clock = clock })
	}
	p := NewProcessor(run, opts...)
	t.Cleanup(p.Stop)
	return p
}

func submitShared(t *testing.T, p *Processor, n int) *Batch {
	t.Helper()
	b, err := p.SubmitFor(makeRequests(n), 1, "", sharedCaller)
	if err != nil {
		t.Fatalf("submit: %v", err)
	}
	return b
}

func readShared(t *testing.T, p *Processor, id string) *Batch {
	t.Helper()
	b, err := p.GetFor(id, sharedOrg)
	if err != nil {
		t.Fatalf("get %s: %v", id, err)
	}
	return b
}

func marshalledShared(t *testing.T, v any) string {
	t.Helper()
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

func isSave(replica string, final bool) func(storeEvent) bool {
	return func(e storeEvent) bool { return e.replica == replica && e.op == "save" && e.final == final }
}

// A batch the owner completes reads on another replica as on the owner,
// its progress, results and summary included, and only the owner runs its
// items.
func TestSharedBatchReadsTheSameOnAnotherReplica(t *testing.T) {
	store, clock := newMemStore(), newOwnerTicks()
	gate, idle := newItemGate(), newItemGate()
	owner := newSharedReplica(t, store, "a", gate.run, clock)
	other := newSharedReplica(t, store, "b", idle.run, nil)

	b := submitShared(t, owner, 2)
	gate.awaitArrival(t, "the first item")
	gate.letOneThrough(t)
	gate.awaitArrival(t, "the second item, once the first one's progress is stored")
	if read := readShared(t, other, b.ID); read.GetStatus() != "processing" || read.CompletedCount() != 1 {
		t.Fatalf("another replica reads %s with %d done, want processing with 1", read.GetStatus(), read.CompletedCount())
	}
	if rows, err := other.ListFor(sharedOrg); err != nil || len(rows) != 1 || rows[0].Counts.Completed != 1 {
		t.Fatalf("another replica lists %+v, %v; want the batch with 1 completed", rows, err)
	}

	gate.letOneThrough(t)
	if e := store.await(t, "the owner's final write", isSave("a", true)); e.err != nil || e.status != "completed" || !e.kept {
		t.Fatalf("the owner's final write: %+v", e)
	}
	read := readShared(t, other, b.ID)
	if got, want := marshalledShared(t, read.Snapshot()), marshalledShared(t, b.Snapshot()); got != want {
		t.Fatalf("another replica reads\n%s\nwant the owner's\n%s", got, want)
	}
	if got, want := marshalledShared(t, read.Summary()), marshalledShared(t, b.Summary()); got != want {
		t.Fatalf("another replica's summary %s, want the owner's %s", got, want)
	}
	if ran, idled := gate.ran.Load(), idle.ran.Load(); ran != 2 || idled != 0 {
		t.Fatalf("the owner ran %d items and another replica %d, want 2 and 0", ran, idled)
	}
}

// A cancel sent to another replica is a request until the owner has stopped
// the batch: every replica reads it processing until then, the cancel
// answers cancelled once the owner has stored its stop, and no item starts
// after it.
func TestSharedCancelReadsCancelledOnlyOnceTheOwnerStopped(t *testing.T) {
	store, clock := newMemStore(), newOwnerTicks()
	gate, idle := newItemGate(), newItemGate()
	owner := newSharedReplica(t, store, "a", gate.run, clock)
	other := newSharedReplica(t, store, "b", idle.run, nil)
	b := submitShared(t, owner, 2)
	gate.awaitArrival(t, "the first item")

	type answer struct {
		b   *Batch
		err error
	}
	answered := make(chan answer, 1)
	go func() {
		got, err := other.CancelFor(b.ID, sharedOrg)
		answered <- answer{got, err}
	}()
	store.await(t, "the cancel request", func(e storeEvent) bool { return e.replica == "b" && e.op == "cancel" })
	for name, p := range map[string]*Processor{"the owner": owner, "another replica": other} {
		if s := readShared(t, p, b.ID).GetStatus(); s != "processing" {
			t.Fatalf("%s reads a batch whose owner has not stopped it %s, want processing", name, s)
		}
	}

	clock.tick(t, time.Second)
	if e := store.await(t, "the owner storing its stop", isSave("a", false)); e.err != nil || e.status != "cancelled" {
		t.Fatalf("the owner's write after the cancel request: %+v", e)
	}
	select {
	case got := <-answered:
		if got.err != nil || got.b.GetStatus() != "cancelled" {
			t.Fatalf("the cancel on another replica answered %v, %v; want cancelled", got.b, got.err)
		}
	case <-time.After(sharedWait):
		t.Fatalf("the cancel on another replica did not answer within %s", sharedWait)
	}
	if e := store.await(t, "the owner's final write", isSave("a", true)); e.err != nil || e.status != "cancelled" {
		t.Fatalf("the owner's final write: %+v", e)
	}
	if ran, idled := gate.ran.Load(), idle.ran.Load(); ran != 1 || idled != 0 {
		t.Fatalf("the owner ran %d items and another replica %d, want 1 and 0: no item starts after the stop", ran, idled)
	}
}

// A cancel sent while only the owner's store is unavailable is never read
// as cancelled: it answers processing, the owner stops starting items
// before its lease can run out, and once it has the batch reads failed with
// owner_lost on every replica.
func TestSharedCancelWhileOnlyTheOwnersStoreIsDownIsNeverReadAsCancelled(t *testing.T) {
	store, clock := newMemStore(), newOwnerTicks()
	gate, idle := newItemGate(), newItemGate()
	owner := newSharedReplica(t, store, "a", gate.run, clock)
	other := newSharedReplica(t, store, "b", idle.run, nil, WithOwnerLease(300*time.Millisecond, 100*time.Millisecond))
	b := submitShared(t, owner, 2)
	gate.awaitArrival(t, "the first item")
	store.setDown("a", true)

	got, err := other.CancelFor(b.ID, sharedOrg)
	if err != nil || got.GetStatus() != "processing" {
		t.Fatalf("a cancel the owner cannot read answered %v, %v; want processing", got, err)
	}
	if s := readShared(t, other, b.ID).GetStatus(); s != "processing" {
		t.Fatalf("another replica reads %s, want processing", s)
	}

	clock.tick(t, defaultOwnerLease-2*defaultLeaseRenew)
	store.await(t, "the owner's renewal", func(e storeEvent) bool { return e.replica == "a" && e.op == "renew" })
	if s := b.Snapshot(); s.Status != "failed" || s.ErrorCode != OwnerLost {
		t.Fatalf("the owner, unable to renew, holds the batch %s (%s), want failed (%s)", s.Status, s.ErrorCode, OwnerLost)
	}
	if _, err := owner.GetFor(b.ID, sharedOrg); !errors.Is(err, ErrStateUnavailable) {
		t.Fatalf("the owner answers a stop it could not store with %v, want ErrStateUnavailable", err)
	}

	store.expireLease(b.ID)
	if s := readShared(t, other, b.ID).Snapshot(); s.Status != "failed" || s.ErrorCode != OwnerLost {
		t.Fatalf("another replica reads %s (%s) once the lease ran out, want failed (%s)", s.Status, s.ErrorCode, OwnerLost)
	}
	if rows, err := other.ListFor(sharedOrg); err != nil || len(rows) != 1 || rows[0].Status != "failed" || rows[0].ErrorCode != OwnerLost {
		t.Fatalf("another replica lists %+v, %v; want the batch failed (%s)", rows, err, OwnerLost)
	}
	if idled := idle.ran.Load(); idled != 0 {
		t.Fatalf("another replica ran %d items, want 0", idled)
	}
}

// A batch that finishes while only its owner's store is unavailable is not
// answered completed by its owner, and once its lease has run out it stays
// failed with owner_lost on every replica, even after the owner's final
// write gets through: that write fills in the results and counts only.
func TestSharedBatchFinishedWhileItsOwnerIsCutOffStaysOwnerLost(t *testing.T) {
	store, clock := newMemStore(), newOwnerTicks()
	gate, idle := newItemGate(), newItemGate()
	owner := newSharedReplica(t, store, "a", gate.run, clock)
	other := newSharedReplica(t, store, "b", idle.run, nil)
	b := submitShared(t, owner, 1)
	gate.awaitArrival(t, "the item")
	store.setDown("a", true)

	gate.letOneThrough(t)
	if e := store.await(t, "the owner's final write failing", isSave("a", true)); e.err == nil {
		t.Fatalf("the owner's final write while its store is down: %+v", e)
	}
	if s := b.GetStatus(); s != "completed" {
		t.Fatalf("the owner's run ended %s, want completed", s)
	}
	if _, err := owner.GetFor(b.ID, sharedOrg); !errors.Is(err, ErrStateUnavailable) {
		t.Fatalf("the owner answers an outcome it could not store with %v, want ErrStateUnavailable", err)
	}

	store.expireLease(b.ID)
	if s := readShared(t, other, b.ID).Snapshot(); s.Status != "failed" || s.ErrorCode != OwnerLost {
		t.Fatalf("another replica reads %s (%s) once the lease ran out, want failed (%s)", s.Status, s.ErrorCode, OwnerLost)
	}
	store.setDown("a", false)
	clock.tick(t, defaultLeaseRenew)
	if e := store.await(t, "the owner's late final write", isSave("a", true)); e.err != nil || e.status != "failed" || e.kept {
		t.Fatalf("the owner's late final write: %+v, want the batch kept failed", e)
	}

	for name, p := range map[string]*Processor{"the owner": owner, "another replica": other} {
		read := readShared(t, p, b.ID)
		s := read.Snapshot()
		if s.Status != "failed" || s.ErrorCode != OwnerLost || s.CompletedAt == nil {
			t.Fatalf("%s reads %s (%s), ended %v; want failed (%s) and ended", name, s.Status, s.ErrorCode, s.CompletedAt, OwnerLost)
		}
		if len(s.Results) != 1 || s.Results[0].Status != "success" || read.CompletedCount() != 1 {
			t.Fatalf("%s reads results %+v and %d completed, want the item's result", name, s.Results, read.CompletedCount())
		}
	}
}

// An answer the owner gives while its batch runs shows the batch as every
// replica reads it at that moment: when the run then completes but its
// outcome cannot be stored, that answer, serialized afterwards, still shows
// the batch processing with its item pending and nothing completed.
func TestSharedOwnersAnswerWhileItsBatchRunsNeverShowsAnOutcomeItCouldNotStore(t *testing.T) {
	store, clock := newMemStore(), newOwnerTicks()
	gate := newItemGate()
	owner := newSharedReplica(t, store, "a", gate.run, clock)
	b := submitShared(t, owner, 1)
	gate.awaitArrival(t, "the item")
	read := readShared(t, owner, b.ID)

	store.setDown("a", true)
	gate.letOneThrough(t)
	if e := store.await(t, "the owner's final write failing", isSave("a", true)); e.err == nil {
		t.Fatalf("the owner's final write while its store is down: %+v", e)
	}
	if s := b.GetStatus(); s != "completed" {
		t.Fatalf("the owner's run ended %s, want completed", s)
	}

	var answer struct {
		Status      string     `json:"status"`
		CompletedAt *time.Time `json:"completed_at"`
		Results     []Result   `json:"results"`
	}
	if err := json.Unmarshal([]byte(marshalledShared(t, read)), &answer); err != nil {
		t.Fatal(err)
	}
	if answer.Status != "processing" || answer.CompletedAt != nil || len(answer.Results) != 1 || answer.Results[0].Status != "pending" {
		t.Fatalf("the answer read while the batch ran, serialized after its unstored end, is %+v; want it processing with its item pending", answer)
	}
	if s := read.Summary(); s.Completed != 0 {
		t.Fatalf("the answer read while the batch ran counts %d completed after its unstored end, want 0", s.Completed)
	}
}

// A processor that stops ends the batches it runs as failed with
// owner_lost, and every replica reads them so at once.
func TestSharedStoppedOwnerEndsItsBatchAsOwnerLost(t *testing.T) {
	store, clock := newMemStore(), newOwnerTicks()
	gate, idle := newItemGate(), newItemGate()
	owner := newSharedReplica(t, store, "a", gate.run, clock)
	other := newSharedReplica(t, store, "b", idle.run, nil)
	b := submitShared(t, owner, 2)
	gate.awaitArrival(t, "the first item")

	owner.Stop()
	if e := store.await(t, "the stopping owner's final write", isSave("a", true)); e.err != nil || e.status != "failed" || !e.kept {
		t.Fatalf("the stopping owner's final write: %+v", e)
	}
	if s := readShared(t, other, b.ID).Snapshot(); s.Status != "failed" || s.ErrorCode != OwnerLost {
		t.Fatalf("another replica reads %s (%s), want failed (%s)", s.Status, s.ErrorCode, OwnerLost)
	}
	if ran, idled := gate.ran.Load(), idle.ran.Load(); ran != 1 || idled != 0 {
		t.Fatalf("the owner ran %d items and another replica %d, want 1 and 0", ran, idled)
	}
}

// A listing reads each stored batch's record alone: listing a batch of many
// items builds none of its item results.
func TestSharedListingReadsRecordsWithoutBuildingItemResults(t *testing.T) {
	store := newMemStore()
	const id, total = "batch-0000000000aa", 100000
	record, err := json.Marshal(sharedRecord{ID: id, Status: "processing", OrgID: sharedOrg, KeyID: "dlg_shared", Total: total, Owner: "a"})
	if err != nil {
		t.Fatal(err)
	}
	store.records[id], store.orgs[id], store.leases[id], store.results[id] = record, sharedOrg, "a", map[int][]byte{}
	p := newSharedReplica(t, store, "b", successHandler, nil)

	for name, list := range map[string]func() ([]ListRow, error){
		"the org's listing": func() ([]ListRow, error) { return p.ListFor(sharedOrg) },
		"the admin listing": p.ClusterInventory,
	} {
		rows, err := list()
		if err != nil || len(rows) != 1 || rows[0].ID != id || rows[0].Total != total || rows[0].Status != "processing" {
			t.Fatalf("%s: %+v, %v; want the one batch of %d items", name, rows, err, total)
		}
		if allocs := testing.AllocsPerRun(5, func() { _, _ = list() }); allocs > 1000 {
			t.Fatalf("%s of one batch of %d items made %.0f allocations, want it to build no item results", name, total, allocs)
		}
	}
}
