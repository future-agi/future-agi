package trajectory

import (
	"context"
	"errors"
	"log/slog"
	"net/url"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/redis/go-redis/v9"
)

// The gate decides, for the whole collector, whether dedup may write pointer
// rows right now. It is closed (every row is written as with dedup off) until
// a check has seen all of:
//
//   - the operator key DefaultGateKey in Redis (set by the backend's
//     trajectory_store_gate command; deleting it is the stop button);
//   - the six spans pointer columns and the seven trajectory_items columns
//     with exactly the 040 types;
//   - spans and trajectory_items stored in local MergeTree-family tables
//     (a Distributed facade keeps the gate closed);
//   - for a Replicated trajectory_items, every replica active (the insert
//     quorum counts them all);
//
// and it closes again for BreakerHold after a trajectory_items insert fails
// and for SchemaErrorHold after a spans insert reports a schema mismatch. A
// check that cannot reach Redis or ClickHouse keeps the last state for at
// most two intervals, then closes.
const (
	// DefaultGateKey is the Redis key whose presence allows dedup.
	DefaultGateKey = "fi:trajectory:dedup_allowed"
	// DefaultGateInterval is how often the gate re-checks once a check has
	// succeeded; DefaultGateUnknownInterval while none has.
	DefaultGateInterval        = 60 * time.Second
	DefaultGateUnknownInterval = 5 * time.Second
	// DefaultBreakerHold is how long an items insert failure closes the gate.
	DefaultBreakerHold = 30 * time.Second
	// DefaultSchemaErrorHold is how long a spans schema mismatch closes the
	// gate, whatever the probes say: the probe and the insert may have reached
	// different replicas.
	DefaultSchemaErrorHold = 10 * time.Minute
	// gateCheckTimeout bounds one check (Redis plus four metadata queries).
	gateCheckTimeout = 5 * time.Second
)

// GateState is the last verdict of the gate's checks.
type GateState int32

const (
	// GateUnknown: no check has succeeded yet. Closed.
	GateUnknown GateState = iota
	// GateOpen: the last check found everything in place.
	GateOpen
	// GateClosed: the last check found something missing, or checks have
	// failed for longer than two intervals.
	GateClosed
)

func (s GateState) String() string {
	switch s {
	case GateOpen:
		return "open"
	case GateClosed:
		return "closed"
	default:
		return "unknown"
	}
}

// DedupGate is what the converter asks before extracting a span.
type DedupGate interface {
	// Open reports whether dedup may write pointer rows now.
	Open() bool
}

// WriteGate is what the server's drain needs from the gate.
type WriteGate interface {
	DedupGate
	// ItemsInsertSettings are the ClickHouse settings for the
	// trajectory_items insert, resolved from the last topology check.
	ItemsInsertSettings() url.Values
	// ReportItemsFailure trips the breaker after a failed items insert.
	ReportItemsFailure(err error)
	// ReportSpansSchemaError closes the gate after a spans insert of pointer
	// rows reported a missing or mismatched column.
	ReportSpansSchemaError(err error)
}

// SchemaProber reads ClickHouse metadata. chwriter.Writer implements it over
// the writer's own endpoint and credentials.
type SchemaProber interface {
	// TableColumns returns name -> type for every column of table in the
	// writer's database (empty when the table is missing or not visible).
	TableColumns(ctx context.Context, table string) (map[string]string, error)
	// TableTopology returns the table's engine and, for Replicated engines,
	// its replica counts.
	TableTopology(ctx context.Context, table string) (TableTopology, error)
}

// GateConfig configures a Gate. Zero durations take the defaults.
type GateConfig struct {
	Prober SchemaProber
	// Redis holds the operator key. Nil keeps the gate closed.
	Redis redis.Cmdable
	// Key overrides DefaultGateKey (tests).
	Key string
	// SpansTable is the writer's pinned span table (default "spans").
	SpansTable      string
	Interval        time.Duration
	UnknownInterval time.Duration
	BreakerHold     time.Duration
	SchemaErrorHold time.Duration
	// ItemsInsertTimeout is the per-attempt items insert deadline; the
	// insert_quorum_timeout is one second less.
	ItemsInsertTimeout time.Duration
	// Now is the clock (tests inject one).
	Now func() time.Time
	Log *slog.Logger
}

// Gate is safe for concurrent use. Open is lock-free; Check is serialized.
type Gate struct {
	cfg GateConfig

	state          atomic.Int32
	breakerUntil   atomic.Int64 // unix nanos
	schemaErrUntil atomic.Int64 // unix nanos
	settings       atomic.Pointer[url.Values]

	mu          sync.Mutex // serializes Check
	lastSuccess time.Time
	reason      string
}

// NewGate builds a closed gate. Call Check, or Run in a goroutine.
func NewGate(cfg GateConfig) *Gate {
	if cfg.Key == "" {
		cfg.Key = DefaultGateKey
	}
	if cfg.SpansTable == "" {
		cfg.SpansTable = "spans"
	}
	if cfg.Interval <= 0 {
		cfg.Interval = DefaultGateInterval
	}
	if cfg.UnknownInterval <= 0 {
		cfg.UnknownInterval = DefaultGateUnknownInterval
	}
	if cfg.BreakerHold <= 0 {
		cfg.BreakerHold = DefaultBreakerHold
	}
	if cfg.SchemaErrorHold <= 0 {
		cfg.SchemaErrorHold = DefaultSchemaErrorHold
	}
	if cfg.ItemsInsertTimeout <= 0 {
		cfg.ItemsInsertTimeout = DefaultItemsInsertTimeout
	}
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	if cfg.Log == nil {
		cfg.Log = slog.Default()
	}
	return &Gate{cfg: cfg}
}

// State returns the last check's verdict and its reason ("" when open).
func (g *Gate) State() (GateState, string) {
	if g == nil {
		return GateUnknown, "no gate"
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	return GateState(g.state.Load()), g.reason
}

// Open reports whether dedup may write pointer rows now. A nil gate is closed.
func (g *Gate) Open() bool {
	if g == nil || GateState(g.state.Load()) != GateOpen {
		return false
	}
	now := g.cfg.Now().UnixNano()
	return now >= g.breakerUntil.Load() && now >= g.schemaErrUntil.Load()
}

// ItemsInsertSettings returns the trajectory_items insert settings from the
// last successful check (nil before one).
func (g *Gate) ItemsInsertSettings() url.Values {
	if g == nil {
		return nil
	}
	if v := g.settings.Load(); v != nil {
		out := make(url.Values, len(*v))
		for k, vs := range *v {
			out[k] = append([]string(nil), vs...)
		}
		return out
	}
	return nil
}

// ReportItemsFailure closes the gate for BreakerHold.
func (g *Gate) ReportItemsFailure(err error) {
	if g == nil {
		return
	}
	until := g.cfg.Now().Add(g.cfg.BreakerHold)
	g.breakerUntil.Store(until.UnixNano())
	g.cfg.Log.Warn("trajectory dedup paused after a trajectory_items insert failure",
		"event", "trajectory_breaker_open", "until", until.UTC().Format(time.RFC3339), "err", errString(err))
}

// ReportSpansSchemaError closes the gate for SchemaErrorHold.
func (g *Gate) ReportSpansSchemaError(err error) {
	if g == nil {
		return
	}
	until := g.cfg.Now().Add(g.cfg.SchemaErrorHold)
	g.schemaErrUntil.Store(until.UnixNano())
	g.cfg.Log.Error("trajectory dedup closed: ClickHouse refused pointer columns on the spans insert",
		"event", "trajectory_dedup_gate_closed", "reason", "spans_schema_error",
		"until", until.UTC().Format(time.RFC3339), "err", errString(err))
}

// Run checks at once, then every Interval (UnknownInterval while no check
// has succeeded), until ctx ends.
func (g *Gate) Run(ctx context.Context) {
	if g == nil {
		return
	}
	for {
		g.Check(ctx)
		wait := g.cfg.Interval
		if GateState(g.state.Load()) == GateUnknown {
			wait = g.cfg.UnknownInterval
		}
		timer := time.NewTimer(wait)
		select {
		case <-ctx.Done():
			timer.Stop()
			return
		case <-timer.C:
		}
	}
}

// errProbe marks a check that could not reach Redis or ClickHouse.
var errProbe = errors.New("trajectory gate probe failed")

// Check runs one check and updates the state.
func (g *Gate) Check(ctx context.Context) {
	if g == nil {
		return
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	ctx, cancel := context.WithTimeout(ctx, gateCheckTimeout)
	defer cancel()
	open, reason, settings, err := g.probe(ctx)
	now := g.cfg.Now()
	if err != nil {
		state := GateState(g.state.Load())
		switch {
		case state == GateUnknown:
			g.cfg.Log.Warn("trajectory dedup gate check failed; dedup stays off until a check succeeds",
				"event", "trajectory_dedup_gate_check_failed", "err", err.Error())
		case state == GateOpen && now.Sub(g.lastSuccess) > 2*g.cfg.Interval:
			g.setLocked(GateClosed, "checks_failing")
		default:
			g.cfg.Log.Debug("trajectory dedup gate check failed; keeping the last state",
				"event", "trajectory_dedup_gate_check_failed", "state", state.String(), "err", err.Error())
		}
		return
	}
	g.lastSuccess = now
	if open {
		g.settings.Store(&settings)
		g.setLocked(GateOpen, "")
		return
	}
	g.setLocked(GateClosed, reason)
}

func (g *Gate) setLocked(next GateState, reason string) {
	prev := GateState(g.state.Swap(int32(next)))
	if prev == next && g.reason == reason {
		return
	}
	g.reason = reason
	if next == GateOpen {
		quorum := ""
		if v := g.settings.Load(); v != nil {
			quorum = v.Get("insert_quorum")
		}
		g.cfg.Log.Info("trajectory dedup gate open", "event", "trajectory_dedup_gate_open", "insert_quorum", quorum)
		return
	}
	g.cfg.Log.Warn("trajectory dedup gate closed; rows are written inline", "event", "trajectory_dedup_gate_closed", "reason", reason)
}

// probe returns open=true with the items insert settings when everything is
// in place, open=false with a reason when something is missing, and an error
// when Redis or ClickHouse could not be read.
func (g *Gate) probe(ctx context.Context) (open bool, reason string, settings url.Values, err error) {
	if g.cfg.Redis == nil {
		return false, "redis_not_configured", nil, nil
	}
	n, err := g.cfg.Redis.Exists(ctx, g.cfg.Key).Result()
	if err != nil {
		return false, "", nil, errors.Join(errProbe, errors.New("read gate key"))
	}
	if n == 0 {
		return false, "gate_key_absent", nil, nil
	}
	if g.cfg.Prober == nil {
		return false, "no_schema_prober", nil, nil
	}
	for _, table := range []struct {
		name     string
		contract []ColumnContract
	}{
		{g.cfg.SpansTable, SpansPointerColumns},
		{ItemsTable, ItemsColumns},
	} {
		cols, err := g.cfg.Prober.TableColumns(ctx, table.name)
		if err != nil {
			return false, "", nil, errors.Join(errProbe, err)
		}
		if problems := SchemaProblems(table.contract, cols); len(problems) > 0 {
			g.cfg.Log.Error("trajectory schema does not match 040_trajectory_items.sql; dedup stays off",
				"event", "trajectory_schema_missing", "table", table.name, "columns", problems)
			return false, "schema_missing:" + table.name, nil, nil
		}
	}
	spans, err := g.cfg.Prober.TableTopology(ctx, g.cfg.SpansTable)
	if err != nil {
		return false, "", nil, errors.Join(errProbe, err)
	}
	if !spans.Exists || spans.Distributed() || !spans.MergeTreeFamily() {
		return false, "unsupported_topology:" + g.cfg.SpansTable + ":" + spans.Engine, nil, nil
	}
	items, err := g.cfg.Prober.TableTopology(ctx, ItemsTable)
	if err != nil {
		return false, "", nil, errors.Join(errProbe, err)
	}
	quorum, ok := items.InsertQuorum()
	if !ok {
		return false, "unsupported_topology:" + ItemsTable + ":" + items.Engine, nil, nil
	}
	if items.Replicated() && items.ActiveReplicas < items.TotalReplicas {
		// The quorum counts every replica, so with one inactive (down, or
		// never dropped from Keeper) every items insert would fail: stay
		// closed instead of cycling breaker, inline drain and reopen.
		g.cfg.Log.Warn("trajectory_items has inactive replicas; dedup stays off",
			"event", "trajectory_replicas_inactive", "total_replicas", items.TotalReplicas, "active_replicas", items.ActiveReplicas)
		return false, "replicas_inactive:" + ItemsTable, nil, nil
	}
	return true, "", ItemsInsertSettingsFor(quorum, g.cfg.ItemsInsertTimeout), nil
}

// ItemsInsertSettingsFor builds the trajectory_items insert settings for a
// quorum and a per-attempt timeout: insert_quorum, an insert_quorum_timeout
// one second shorter than the attempt (at least one second), and
// distributed_foreground_insert=1 (a no-op on local tables).
func ItemsInsertSettingsFor(quorum string, attemptTimeout time.Duration) url.Values {
	timeout := attemptTimeout - time.Second
	if timeout < time.Second {
		timeout = time.Second
	}
	return url.Values{
		"insert_quorum":                 {quorum},
		"insert_quorum_timeout":         {strconv.FormatInt(timeout.Milliseconds(), 10)},
		"distributed_foreground_insert": {"1"},
	}
}

func errString(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}

// FixedGate returns a gate that is always open or always closed, for tests
// and tools that convert without a ClickHouse endpoint.
func FixedGate(open bool) DedupGate { return fixedGate(open) }

type fixedGate bool

func (f fixedGate) Open() bool { return bool(f) }
