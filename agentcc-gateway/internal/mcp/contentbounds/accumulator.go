package contentbounds

import "crypto/sha256"

// Totals is a value-free snapshot. Nodes is the SUM of item value nodes;
// Depth is the MAXIMUM item container depth. Bytes includes each scanned
// document's own whitespace, but not separators of a future output envelope.
// A later serializer must bound its own output including such overhead.
// Cursor counts/bytes are separate bounded bookkeeping, not content totals.
type Totals struct {
	Bytes       int64
	Items       int64
	Nodes       int64
	Depth       int64
	CursorBytes int64
	Cursors     int64
}

// Accumulator keeps counts and cursor fingerprints, never JSON content. Use
// one instance for the entire intended aggregate across all pages/sources.
// It must not be copied or accessed concurrently. The zero value is invalid.
// No Add or ObserveCursor refusal changes any state. These are independent
// operations, not a page transaction; callers own transactional page staging.
type Accumulator struct {
	limits      Limits
	totals      Totals
	cursors     map[[sha256.Size]byte]struct{}
	initialized bool
}

func NewAccumulator(limits Limits) (*Accumulator, error) {
	if !limits.valid() {
		return nil, refuse(CodeInvalidLimits)
	}
	return &Accumulator{limits: limits, initialized: true}, nil
}

func (a *Accumulator) Counts() Totals {
	if a == nil {
		return Totals{}
	}
	return a.totals
}

func (a *Accumulator) valid() bool {
	return a != nil && a.initialized && a.limits.valid() &&
		a.totals.Bytes >= 0 && a.totals.Bytes <= a.limits.MaxTotalBytes &&
		a.totals.Items >= 0 && a.totals.Items <= a.limits.MaxItems &&
		a.totals.Nodes >= 0 && a.totals.Nodes <= a.limits.MaxNodes &&
		a.totals.Depth >= 0 && a.totals.Depth <= a.limits.MaxDepth &&
		a.totals.CursorBytes >= 0 && a.totals.CursorBytes <= a.limits.MaxTotalBytes &&
		a.totals.Cursors >= 0 && a.totals.Cursors <= a.limits.MaxItems
}

func (s Summary) valid() bool {
	c := s.counts
	if !s.validated || c.Bytes <= 0 || c.Nodes <= 0 || c.Nodes > c.Bytes || c.Depth < 0 || c.Depth > c.Nodes || c.RootArrayItems < 0 {
		return false
	}
	switch s.kind {
	case KindArray:
		return c.Depth >= 1 && c.RootArrayItems <= c.Nodes-1
	case KindObject:
		return c.Depth >= 1 && c.RootArrayItems == 0
	case KindString, KindNumber, KindBoolean, KindNull:
		return c.Nodes == 1 && c.Depth == 0 && c.RootArrayItems == 0
	default:
		return false
	}
}

// Add accepts one validated ROOT as one list item, including when that root
// itself is an array. It never flattens arrays or infers RPC envelope members.
// All checks precede mutation. Subtraction checks avoid overflow; no candidate
// total is computed until it is proven within its positive finite ceiling.
func (a *Accumulator) Add(item Summary) error {
	if !a.valid() {
		return refuse(CodeInvalidAccumulator)
	}
	if !item.valid() {
		return refuse(CodeInvalidSummary)
	}
	c := item.counts
	if c.Bytes > a.limits.MaxReadBytes {
		return refuse(CodeReadBytes)
	}
	if c.Bytes > a.limits.MaxItemBytes {
		return refuse(CodeItemBytes)
	}
	if c.Depth > a.limits.MaxDepth {
		return refuse(CodeDepth)
	}
	if c.RootArrayItems > a.limits.MaxItems || a.totals.Items >= a.limits.MaxItems {
		return refuse(CodeItems)
	}
	if c.Bytes > a.limits.MaxTotalBytes-a.totals.Bytes {
		return refuse(CodeTotalBytes)
	}
	if c.Nodes > a.limits.MaxNodes-a.totals.Nodes {
		return refuse(CodeNodes)
	}
	a.totals.Bytes += c.Bytes
	a.totals.Items++
	a.totals.Nodes += c.Nodes
	a.totals.Depth = max(a.totals.Depth, c.Depth)
	return nil
}

// ObserveCursor records one NONEMPTY opaque cursor before the caller follows
// it. The caller decides whether a protocol has a terminal/absent cursor; an
// empty string is refused here, not silently interpreted as completion.
// Reusing a cursor anywhere in this accumulator's history refuses, not just
// an immediate repetition. SHA-256 digests avoid retaining raw cursor values;
// a digest collision fails closed as a cycle. Both history count and total
// cursor input bytes are capped, including pages that contribute no items.
// No cursor traversal, fetch, callback, or automatic Add takes place here.
func (a *Accumulator) ObserveCursor(cursor string) error {
	if !a.valid() {
		return refuse(CodeInvalidAccumulator)
	}
	n := int64(len(cursor))
	if n == 0 {
		return refuse(CodeInvalidCursor)
	}
	if n > a.limits.MaxItemBytes {
		return refuse(CodeCursorBytes)
	}
	// Length check occurs BEFORE converting/hashing any cursor bytes.
	digest := sha256.Sum256([]byte(cursor))
	if _, seen := a.cursors[digest]; seen {
		return refuse(CodeCursorCycle)
	}
	if a.totals.Cursors >= a.limits.MaxItems {
		return refuse(CodeCursorCount)
	}
	if n > a.limits.MaxTotalBytes-a.totals.CursorBytes {
		return refuse(CodeCursorBytes)
	}
	if a.cursors == nil {
		a.cursors = make(map[[sha256.Size]byte]struct{})
	}
	a.cursors[digest] = struct{}{}
	a.totals.Cursors++
	a.totals.CursorBytes += n
	return nil
}
