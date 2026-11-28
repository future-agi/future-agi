package trajectory

import (
	"bytes"
	"context"
	"errors"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/redis/go-redis/v9"
)

// fakeProber serves system.columns / system.tables answers from a 040-shaped
// schema. Guarded: Run calls it from another goroutine.
type fakeProber struct {
	mu      sync.Mutex
	columns map[string]map[string]string
	topo    map[string]TableTopology
	err     error
	calls   int
}

func fullSchemaProber() *fakeProber {
	p := &fakeProber{columns: map[string]map[string]string{}, topo: map[string]TableTopology{}}
	spans := map[string]string{"span_id": "String", "input": "String"}
	for _, c := range SpansPointerColumns {
		spans[c.Name] = c.Type
	}
	items := map[string]string{}
	for _, c := range ItemsColumns {
		items[c.Name] = c.Type
	}
	p.columns["spans"], p.columns[ItemsTable] = spans, items
	p.topo["spans"] = TableTopology{Exists: true, Engine: "ReplacingMergeTree"}
	p.topo[ItemsTable] = TableTopology{Exists: true, Engine: "ReplacingMergeTree"}
	return p
}

func (p *fakeProber) TableColumns(_ context.Context, table string) (map[string]string, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.calls++
	if p.err != nil {
		return nil, p.err
	}
	out := map[string]string{}
	for k, v := range p.columns[table] {
		out[k] = v
	}
	return out, nil
}

func (p *fakeProber) TableTopology(_ context.Context, table string) (TableTopology, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.calls++
	if p.err != nil {
		return TableTopology{}, p.err
	}
	return p.topo[table], nil
}

func (p *fakeProber) set(f func(p *fakeProber)) {
	p.mu.Lock()
	defer p.mu.Unlock()
	f(p)
}

func (p *fakeProber) callCount() int {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.calls
}

type fakeClock struct{ nanos atomic.Int64 }

func newFakeClock() *fakeClock {
	c := &fakeClock{}
	c.nanos.Store(time.Date(2026, 11, 28, 12, 0, 0, 0, time.UTC).UnixNano())
	return c
}
func (c *fakeClock) Now() time.Time          { return time.Unix(0, c.nanos.Load()).UTC() }
func (c *fakeClock) Advance(d time.Duration) { c.nanos.Add(int64(d)) }

// syncBuffer is a goroutine-safe log sink.
type syncBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *syncBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *syncBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

type gateFixture struct {
	gate   *Gate
	prober *fakeProber
	mr     *miniredis.Miniredis
	clock  *fakeClock
	logs   *syncBuffer
}

func newGateFixture(t *testing.T, keyPresent bool) *gateFixture {
	t.Helper()
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	t.Cleanup(func() { rdb.Close() })
	if keyPresent {
		if err := mr.Set(DefaultGateKey, "1"); err != nil {
			t.Fatal(err)
		}
	}
	f := &gateFixture{prober: fullSchemaProber(), mr: mr, clock: newFakeClock(), logs: &syncBuffer{}}
	f.gate = NewGate(GateConfig{
		Prober: f.prober, Redis: rdb, Now: f.clock.Now,
		Log: slog.New(slog.NewJSONHandler(f.logs, &slog.HandlerOptions{Level: slog.LevelDebug})),
	})
	return f
}

func (f *gateFixture) check(t *testing.T) {
	t.Helper()
	f.gate.Check(context.Background())
}

func TestGateKeyIsTheBackendStopButton(t *testing.T) {
	// The backend's trajectory_store_gate command sets and deletes this key.
	if DefaultGateKey != "fi:trajectory:dedup_allowed" {
		t.Fatalf("gate key changed: %q", DefaultGateKey)
	}
}

// TestTrajectorySchemaContractMatches040 parses 040_trajectory_items.sql so
// the gate's contract cannot drift from the migration.
func TestTrajectorySchemaContractMatches040(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "futureagi", "tracer", "services", "clickhouse", "v2", "schema", "040_trajectory_items.sql"))
	if err != nil {
		t.Fatal(err)
	}
	var lines []string
	for _, line := range strings.Split(string(raw), "\n") {
		line, _, _ = strings.Cut(line, "--")
		if strings.TrimSpace(line) != "" {
			lines = append(lines, strings.TrimSpace(line))
		}
	}
	columnType := func(rest string) string {
		for _, stop := range []string{" DEFAULT ", " CODEC(", " MATERIALIZED "} {
			if i := strings.Index(rest, stop); i >= 0 {
				rest = rest[:i]
			}
		}
		return strings.TrimSpace(strings.TrimSuffix(strings.TrimSpace(rest), ","))
	}
	items := map[string]string{}
	spans := map[string]string{}
	inItems := false
	for i, line := range lines {
		switch {
		case strings.HasSuffix(line, ItemsTable) && i+1 < len(lines) && lines[i+1] == "(":
			inItems = true
		case inItems && line == "(":
		case inItems && line == ")":
			inItems = false
		case inItems:
			name, rest, _ := strings.Cut(line, " ")
			items[name] = columnType(rest)
		case strings.HasPrefix(line, "ADD COLUMN IF NOT EXISTS "):
			name, rest, _ := strings.Cut(strings.TrimPrefix(line, "ADD COLUMN IF NOT EXISTS "), " ")
			spans[name] = columnType(rest)
		}
	}
	for label, pair := range map[string]struct {
		contract []ColumnContract
		parsed   map[string]string
	}{"trajectory_items": {ItemsColumns, items}, "spans": {SpansPointerColumns, spans}} {
		if len(pair.parsed) != len(pair.contract) {
			t.Fatalf("%s: 040 defines %d columns %v, the gate checks %d", label, len(pair.parsed), pair.parsed, len(pair.contract))
		}
		if problems := SchemaProblems(pair.contract, pair.parsed); len(problems) > 0 {
			t.Fatalf("%s: gate contract differs from 040: %v", label, problems)
		}
	}
}

func TestGateUnknownAtStartWritesInline(t *testing.T) {
	f := newGateFixture(t, true)
	if f.gate.Open() {
		t.Fatal("a gate that has not checked yet must be closed")
	}
	if state, _ := f.gate.State(); state != GateUnknown {
		t.Fatalf("state %s, want unknown", state)
	}
	var nilGate *Gate
	if nilGate.Open() {
		t.Fatal("a nil gate must be closed")
	}
	f.check(t)
	if !f.gate.Open() {
		state, reason := f.gate.State()
		t.Fatalf("full schema and key present: gate must open (state %s, %s)", state, reason)
	}
	if got := f.gate.ItemsInsertSettings().Get("insert_quorum"); got != "auto" {
		t.Fatalf("plain MergeTree quorum %q, want auto", got)
	}
}

func TestGateMissingColumnClosed(t *testing.T) {
	for _, tc := range []struct{ table, column string }{
		{"spans", "payload_envelope"},
		{"spans", "input_item_hashes"},
		{ItemsTable, "persisted_at"},
	} {
		t.Run(tc.table+"."+tc.column, func(t *testing.T) {
			f := newGateFixture(t, true)
			f.check(t)
			if !f.gate.Open() {
				t.Fatal("baseline must open")
			}
			f.prober.set(func(p *fakeProber) { delete(p.columns[tc.table], tc.column) })
			f.check(t)
			if f.gate.Open() {
				t.Fatalf("missing %s.%s must close the gate", tc.table, tc.column)
			}
			if !strings.Contains(f.logs.String(), `"event":"trajectory_schema_missing"`) || !strings.Contains(f.logs.String(), tc.column) {
				t.Fatalf("missing column not logged with event trajectory_schema_missing: %s", f.logs.String())
			}
		})
	}
	f := newGateFixture(t, true)
	f.prober.set(func(p *fakeProber) { delete(p.columns, ItemsTable) })
	f.check(t)
	if f.gate.Open() {
		t.Fatal("a missing trajectory_items table must keep the gate closed")
	}
}

func TestGateWrongColumnTypeClosed(t *testing.T) {
	f := newGateFixture(t, true)
	f.prober.set(func(p *fakeProber) { p.columns["spans"]["payload_encoding"] = "String" })
	f.check(t)
	if f.gate.Open() {
		t.Fatal("a pointer column of another type must keep the gate closed")
	}
	// Spacing inside a type is not a mismatch.
	f.prober.set(func(p *fakeProber) {
		p.columns["spans"]["payload_encoding"] = "LowCardinality(String)"
		p.columns[ItemsTable]["persisted_at"] = "DateTime64(6,'UTC')"
	})
	f.check(t)
	if !f.gate.Open() {
		t.Fatal("whitespace inside a type must not close the gate")
	}
}

func TestGateProbeErrorKeepsLastThenCloses(t *testing.T) {
	f := newGateFixture(t, true)
	// Failing before any success: stays unknown (closed).
	f.prober.set(func(p *fakeProber) { p.err = errors.New("clickhouse down") })
	f.check(t)
	if f.gate.Open() {
		t.Fatal("no successful check yet: closed")
	}
	f.prober.set(func(p *fakeProber) { p.err = nil })
	f.check(t)
	if !f.gate.Open() {
		t.Fatal("must open once a check succeeds")
	}
	f.prober.set(func(p *fakeProber) { p.err = errors.New("clickhouse down") })
	f.clock.Advance(DefaultGateInterval)
	f.check(t)
	if !f.gate.Open() {
		t.Fatal("one failed check keeps the last state")
	}
	f.clock.Advance(DefaultGateInterval)
	f.check(t)
	if !f.gate.Open() {
		t.Fatal("failures for up to two intervals keep the last state")
	}
	f.clock.Advance(DefaultGateInterval)
	f.check(t)
	if f.gate.Open() {
		t.Fatal("failures for more than two intervals must close the gate")
	}
	// Redis unreachable counts the same.
	f.prober.set(func(p *fakeProber) { p.err = nil })
	f.check(t)
	if !f.gate.Open() {
		t.Fatal("recovers")
	}
	f.mr.SetError("redis down")
	f.clock.Advance(3 * DefaultGateInterval)
	f.check(t)
	if f.gate.Open() {
		t.Fatal("Redis unreachable for more than two intervals must close the gate")
	}
}

func TestGateRedisKeyDeletedClosesWithinInterval(t *testing.T) {
	f := newGateFixture(t, true)
	f.gate.cfg.Interval = 20 * time.Millisecond
	f.gate.cfg.UnknownInterval = 10 * time.Millisecond
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { defer close(done); f.gate.Run(ctx) }()
	defer func() { cancel(); <-done }()
	waitFor := func(want bool) {
		t.Helper()
		deadline := time.Now().Add(5 * time.Second)
		for f.gate.Open() != want {
			if time.Now().After(deadline) {
				t.Fatalf("gate open=%v not reached within the interval", want)
			}
			time.Sleep(5 * time.Millisecond)
		}
	}
	waitFor(true)
	f.mr.Del(DefaultGateKey)
	waitFor(false)
	if _, reason := f.gate.State(); reason != "gate_key_absent" {
		t.Fatalf("reason %q, want gate_key_absent", reason)
	}
	// The key coming back reopens it.
	if err := f.mr.Set(DefaultGateKey, "1"); err != nil {
		t.Fatal(err)
	}
	waitFor(true)
}

func TestGateKeyAbsentSkipsClickHouse(t *testing.T) {
	f := newGateFixture(t, false)
	f.check(t)
	if f.gate.Open() {
		t.Fatal("without the operator key the gate stays closed")
	}
	if n := f.prober.callCount(); n != 0 {
		t.Fatalf("without the key no ClickHouse metadata may be read, got %d calls", n)
	}
	g := NewGate(GateConfig{Prober: fullSchemaProber(), Log: slog.New(slog.NewTextHandler(io.Discard, nil))})
	g.Check(context.Background())
	if g.Open() {
		t.Fatal("no Redis: the gate stays closed")
	}
}

func TestSchemaGateRejectsDistributed(t *testing.T) {
	for _, table := range []string{"spans", ItemsTable} {
		f := newGateFixture(t, true)
		f.prober.set(func(p *fakeProber) { p.topo[table] = TableTopology{Exists: true, Engine: "Distributed"} })
		f.check(t)
		if f.gate.Open() {
			t.Fatalf("a Distributed %s must keep the gate closed", table)
		}
	}
}

// TestGateClosedWhileAReplicaIsInactive: with a trajectory_items replica down
// (or a dead one never dropped from Keeper) the every-replica quorum makes
// each items insert fail, so the gate stays closed until all are active.
func TestGateClosedWhileAReplicaIsInactive(t *testing.T) {
	f := newGateFixture(t, true)
	f.prober.set(func(p *fakeProber) {
		p.topo[ItemsTable] = TableTopology{Exists: true, Engine: "ReplicatedReplacingMergeTree", TotalReplicas: 3, ActiveReplicas: 2}
	})
	f.check(t)
	if f.gate.Open() {
		t.Fatal("an inactive trajectory_items replica must keep the gate closed")
	}
	if state, reason := f.gate.State(); state != GateClosed || reason != "replicas_inactive:"+ItemsTable {
		t.Fatalf("state %v reason %q", state, reason)
	}
	if !strings.Contains(f.logs.String(), `"event":"trajectory_replicas_inactive"`) {
		t.Fatal("inactive replicas not logged")
	}
	f.prober.set(func(p *fakeProber) {
		p.topo[ItemsTable] = TableTopology{Exists: true, Engine: "ReplicatedReplacingMergeTree", TotalReplicas: 3, ActiveReplicas: 3}
	})
	f.check(t)
	if !f.gate.Open() {
		t.Fatal("reopens once every replica is active")
	}
	if got := f.gate.ItemsInsertSettings().Get("insert_quorum"); got != "3" {
		t.Fatalf("insert_quorum %q, want 3", got)
	}
}

func TestGateBreakerAndSchemaErrorHold(t *testing.T) {
	f := newGateFixture(t, true)
	f.check(t)
	f.gate.ReportItemsFailure(errors.New("items insert failed"))
	if f.gate.Open() {
		t.Fatal("an items failure must close the gate")
	}
	if !strings.Contains(f.logs.String(), `"event":"trajectory_breaker_open"`) {
		t.Fatal("breaker not logged")
	}
	f.clock.Advance(DefaultBreakerHold - time.Second)
	if f.gate.Open() {
		t.Fatal("closed for the whole breaker hold")
	}
	f.clock.Advance(2 * time.Second)
	if !f.gate.Open() {
		t.Fatal("reopens after the breaker hold")
	}
	f.gate.ReportSpansSchemaError(errors.New("unknown field"))
	f.check(t) // the probe still sees every column
	if f.gate.Open() {
		t.Fatal("a spans schema error holds the gate closed even when the probe passes")
	}
	f.clock.Advance(DefaultSchemaErrorHold + time.Second)
	if !f.gate.Open() {
		t.Fatal("reopens after the schema error hold")
	}
}

func TestInsertQuorumByTopology(t *testing.T) {
	for _, tc := range []struct {
		topo   TableTopology
		want   string
		wantOK bool
	}{
		{TableTopology{Exists: true, Engine: "ReplacingMergeTree"}, "auto", true},
		{TableTopology{Exists: true, Engine: "ReplicatedReplacingMergeTree", TotalReplicas: 1, ActiveReplicas: 1}, "1", true},
		{TableTopology{Exists: true, Engine: "ReplicatedReplacingMergeTree", TotalReplicas: 2, ActiveReplicas: 2}, "2", true},
		// A replica that is down still counts toward the quorum (a single
		// copy is never acknowledged); the gate itself stays closed while
		// one is inactive (TestGateClosedWhileAReplicaIsInactive).
		{TableTopology{Exists: true, Engine: "ReplicatedReplacingMergeTree", TotalReplicas: 3, ActiveReplicas: 2}, "3", true},
		{TableTopology{Exists: true, Engine: "ReplicatedReplacingMergeTree"}, "", false},
		{TableTopology{Exists: true, Engine: "Distributed"}, "", false},
		{TableTopology{Exists: true, Engine: "Memory"}, "", false},
		{TableTopology{}, "", false},
	} {
		got, ok := tc.topo.InsertQuorum()
		if got != tc.want || ok != tc.wantOK {
			t.Fatalf("%+v: quorum %q ok=%v, want %q ok=%v", tc.topo, got, ok, tc.want, tc.wantOK)
		}
	}
	s := ItemsInsertSettingsFor("2", DefaultItemsInsertTimeout)
	if s.Get("insert_quorum") != "2" || s.Get("insert_quorum_timeout") != "4000" || s.Get("distributed_foreground_insert") != "1" {
		t.Fatalf("settings: %v", s)
	}
	if got := ItemsInsertSettingsFor("auto", time.Second).Get("insert_quorum_timeout"); got != "1000" {
		t.Fatalf("quorum timeout floor: %s", got)
	}
}

func TestFixedGate(t *testing.T) {
	if !FixedGate(true).Open() || FixedGate(false).Open() {
		t.Fatal("FixedGate")
	}
}
