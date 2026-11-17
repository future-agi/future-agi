package meter

import (
	"sort"
	"strings"
	"sync"
)

// Key names one tool of one org's server.
type Key struct {
	OrgID    string
	ServerID string
	Tool     string
}

const (
	// UnresolvedTool is the Key.Tool under which an org's calls whose tool
	// never resolved to a server are counted (unknown or hidden tools, and
	// denials before the tool was resolved): their name is whatever the
	// caller sent, so it must not open a counter of its own. A tool name
	// that follows the MCP spec (ValidateToolName in package mcp) holds no
	// angle brackets, so it cannot collide with this one.
	UnresolvedTool = "<unresolved>"
	// OverflowTool is the one Key.Tool, with no org or server, that counts
	// calls of new keys once MaxCounterKeys keys exist.
	OverflowTool = "<overflow>"
	// MaxCounterKeys bounds the number of counter keys (a backstop: the
	// keys are already bounded by the registered tools of each org).
	MaxCounterKeys = 100_000
	// MaxNameRunes is the longest tool name a counter key or a record
	// carries: the record's tool and upstream_tool columns are varchar(255).
	MaxNameRunes = 255
)

// CapRunes returns s cut to at most n runes, on a rune boundary.
func CapRunes(s string, n int) string {
	if len(s) <= n {
		return s
	}
	i := 0
	for pos := range s {
		if i == n {
			return s[:pos]
		}
		i++
	}
	return s
}

// Column returns s as a record's text column of at most n characters
// stores it: NUL, which Postgres text rejects, replaced by U+FFFD, then cut
// to n runes. One value Postgres refuses would fail the whole webhook batch,
// which holds other orgs' records too.
func Column(s string, n int) string {
	return CapRunes(strings.ReplaceAll(s, "\x00", "\uFFFD"), n)
}

// CapName returns a tool name as a record or a counter key carries it:
// Column of MaxNameRunes.
func CapName(s string) string { return Column(s, MaxNameRunes) }

// Count is one tool's counters: every recorded call, denials included.
type Count struct {
	Key
	Calls          int64
	Outcomes       map[string]int64 // by record outcome
	LatencyMsTotal int64
	LastCalledMs   int64 // unix ms of the last recorded call
}

// Counters are the in-process tool-call counters (the metrics exporter exports them). They are
// keyed by name, not held by the registry, so re-registering a server or a
// tool does not reset them.
type Counters struct {
	mu      sync.Mutex
	byID    map[Key]*Count
	maxKeys int // MaxCounterKeys; tests lower it
}

// NewCounters returns empty counters.
func NewCounters() *Counters {
	return &Counters{byID: map[Key]*Count{}, maxKeys: MaxCounterKeys}
}

// Observe counts one finished call of k with its record outcome, latency and
// time (unix ms). k.Tool is cut to MaxNameRunes, and a new key past
// MaxCounterKeys is counted under OverflowTool, so the counters stay bounded
// whatever names callers send.
func (c *Counters) Observe(k Key, outcome string, latencyMs, atMs int64) {
	k.Tool = CapName(k.Tool)
	c.mu.Lock()
	defer c.mu.Unlock()
	n, ok := c.byID[k]
	if !ok && len(c.byID) >= c.maxKeys {
		k = Key{Tool: OverflowTool}
		n, ok = c.byID[k]
	}
	if !ok {
		n = &Count{Key: k, Outcomes: map[string]int64{}}
		c.byID[k] = n
	}
	n.Calls++
	n.Outcomes[outcome]++
	n.LatencyMsTotal += latencyMs
	if atMs > n.LastCalledMs {
		n.LastCalledMs = atMs
	}
}

// Get returns a copy of k's counters.
func (c *Counters) Get(k Key) (Count, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	n, ok := c.byID[k]
	if !ok {
		return Count{}, false
	}
	return n.copy(), true
}

// Snapshot returns a copy of every tool's counters, sorted by org, server
// and tool.
func (c *Counters) Snapshot() []Count {
	c.mu.Lock()
	out := make([]Count, 0, len(c.byID))
	for _, n := range c.byID {
		out = append(out, n.copy())
	}
	c.mu.Unlock()
	sort.Slice(out, func(i, j int) bool {
		a, b := out[i].Key, out[j].Key
		if a.OrgID != b.OrgID {
			return a.OrgID < b.OrgID
		}
		if a.ServerID != b.ServerID {
			return a.ServerID < b.ServerID
		}
		return a.Tool < b.Tool
	})
	return out
}

func (n *Count) copy() Count {
	out := *n
	out.Outcomes = make(map[string]int64, len(n.Outcomes))
	for k, v := range n.Outcomes {
		out.Outcomes[k] = v
	}
	return out
}
