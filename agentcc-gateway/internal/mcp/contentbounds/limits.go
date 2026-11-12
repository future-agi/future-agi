// Package contentbounds validates generic JSON without interpreting protocol
// envelopes or retaining scalar values. It has no production default limits.
// Callers own transport deadlines, identity, payload binding, and integration.
package contentbounds

// Limits are explicit positive ceilings. Every field is required, including
// fields not used by a particular operation. Zero never means unlimited.
// Values above the platform's maximum int minus one are refused, preserving
// room for a limit-plus-one probe and safe slice/counter arithmetic.
//
// Scan uses MaxReadBytes, MaxItems, MaxDepth and MaxNodes. MaxItems counts only
// direct children of a root JSON array; no envelope member is selected.
// Accumulator.Add treats each scanned root as ONE item, checks MaxItemBytes,
// rechecks all scan ceilings, and caps total bytes, items and nodes.
// To scan an individual list item at its smaller byte ceiling, also restrict
// MaxReadBytes to MaxItemBytes before scanning. Add never retains payloads.
// Limits do not authorize widening any inherited transport/body cap.
//
// MaxTotalBytes and MaxItems also bound a separate cursor history budget;
// MaxItemBytes bounds each opaque cursor. Cursor bytes are NOT content bytes.
type Limits struct {
	MaxReadBytes  int64
	MaxItemBytes  int64
	MaxTotalBytes int64
	MaxItems      int64
	MaxDepth      int64
	MaxNodes      int64
}

func (l Limits) valid() bool {
	max := int64(^uint(0)>>1) - 1
	for _, v := range [...]int64{l.MaxReadBytes, l.MaxItemBytes, l.MaxTotalBytes, l.MaxItems, l.MaxDepth, l.MaxNodes} {
		if v <= 0 || v > max {
			return false
		}
	}
	return true
}

// EffectiveLimits returns fieldwise minima only after validating BOTH inputs.
// It does not substitute defaults or treat missing inherited limits as absent.
func EffectiveLimits(inherited, requested Limits) (Limits, error) {
	if !inherited.valid() || !requested.valid() {
		return Limits{}, refuse(CodeInvalidLimits)
	}
	return Limits{
		MaxReadBytes:  min(inherited.MaxReadBytes, requested.MaxReadBytes),
		MaxItemBytes:  min(inherited.MaxItemBytes, requested.MaxItemBytes),
		MaxTotalBytes: min(inherited.MaxTotalBytes, requested.MaxTotalBytes),
		MaxItems:      min(inherited.MaxItems, requested.MaxItems),
		MaxDepth:      min(inherited.MaxDepth, requested.MaxDepth),
		MaxNodes:      min(inherited.MaxNodes, requested.MaxNodes),
	}, nil
}

// Code is a fixed refusal class, never a payload, key, cursor, or reader error.
type Code string

const (
	CodeInvalidLimits      Code = "content_bounds_invalid_limits"
	CodeReader             Code = "content_bounds_reader"
	CodeJSON               Code = "content_bounds_json"
	CodeDuplicateKey       Code = "content_bounds_duplicate_key"
	CodeReadBytes          Code = "content_bounds_read_bytes"
	CodeItemBytes          Code = "content_bounds_item_bytes"
	CodeTotalBytes         Code = "content_bounds_total_bytes"
	CodeItems              Code = "content_bounds_items"
	CodeDepth              Code = "content_bounds_depth"
	CodeNodes              Code = "content_bounds_nodes"
	CodeInvalidSummary     Code = "content_bounds_invalid_summary"
	CodeInvalidAccumulator Code = "content_bounds_invalid_accumulator"
	CodeInvalidCursor      Code = "content_bounds_invalid_cursor"
	CodeCursorCycle        Code = "content_bounds_cursor_cycle"
	CodeCursorBytes        Code = "content_bounds_cursor_bytes"
	CodeCursorCount        Code = "content_bounds_cursor_count"
)

// Error deliberately has no wrapped cause or arbitrary text fields.
type Error struct{ code Code }

func (e *Error) Error() string { return string(e.code) }
func (e *Error) Code() Code    { return e.code }
func refuse(code Code) error   { return &Error{code: code} }

// Kind describes the validated root value, not a protocol content type.
type Kind uint8

const (
	KindObject Kind = iota + 1
	KindArray
	KindString
	KindNumber
	KindBoolean
	KindNull
)

// Counts contains value-free observations. Bytes includes ALL wire bytes,
// including JSON syntax and leading/trailing JSON whitespace. Nodes counts
// every JSON VALUE once (containers included); object keys are NOT nodes.
// Depth is peak container nesting: a root container is 1, a scalar root is 0.
// RootArrayItems counts immediate root-array elements, not nested elements.
type Counts struct {
	Bytes          int64
	Nodes          int64
	Depth          int64
	RootArrayItems int64
}

// Summary can only be minted by a successful Scan. Exported observations are
// copies; zero values and deserialized empty structs cannot authorize Add.
// A summary is structural evidence, NOT authentication or a payload handle.
// Callers must bind the exact scanned bytes to any later use; this package
// cannot prevent a caller from substituting a different body after validation.
type Summary struct {
	counts    Counts
	kind      Kind
	validated bool
}

func (s Summary) Counts() Counts { return s.counts }
func (s Summary) Kind() Kind     { return s.kind }
