package trajectory

import (
	"reflect"
	"time"
	"unsafe"
)

// PromotedAttr is the attributes_extra copy of one extracted side that the
// converter replaced with sentinels: the attribute's key and its original Go
// value, exactly as the dedup-off row holds it.
type PromotedAttr struct {
	Key   string
	Value any
}

// SpanFallback holds a direct reference to a compacted span row and its
// original inline fields so that if inserting `trajectory_items` into
// ClickHouse fails after retries, `drainNow` can atomically restore the row to
// `payload_encoding = 'inline'` before inserting `spans` — guaranteeing zero
// orphaned hashes or data loss, even when rows are split across parcels.
//
// PromotedInput / PromotedOutput are set only for a side whose attributes_extra
// copy was removed (the side was extracted and its content came from the
// overflow tier). Restoring puts that key back with its original value and
// deletes the sentinels, so a restored row deep-equals the dedup-off row.
type SpanFallback struct {
	Row            map[string]any
	OrigInput      string
	OrigOutput     string
	PromotedInput  *PromotedAttr
	PromotedOutput *PromotedAttr
}

// SentinelKeys are the four attributes_extra keys a compacted side writes.
var SentinelKeys = [...]string{
	SentinelPromotedInputKey, SentinelPromotedInputJSON,
	SentinelPromotedOutputKey, SentinelPromotedOutputJSON,
}

// HasSentinelKey reports whether m already holds one of the sentinel keys,
// for example because the producer sent an attribute with that name.
func HasSentinelKey[V any](m map[string]V) bool {
	for _, k := range SentinelKeys {
		if _, ok := m[k]; ok {
			return true
		}
	}
	return false
}

// CompactPromoted removes the attributes_extra copy of one extracted side and
// writes that side's sentinels. key is the attribute the side's content came
// from and wasJSON reports that its value is not a string (the column holds
// its JSON encoding). It returns what restoring needs, or nil when extra does
// not hold key.
func CompactPromoted(extra map[string]any, key string, wasJSON, input bool) *PromotedAttr {
	v, ok := extra[key]
	if !ok || extra == nil {
		return nil
	}
	keySentinel, jsonSentinel := SentinelPromotedOutputKey, SentinelPromotedOutputJSON
	if input {
		keySentinel, jsonSentinel = SentinelPromotedInputKey, SentinelPromotedInputJSON
	}
	delete(extra, key)
	extra[keySentinel] = key
	if wasJSON {
		extra[jsonSentinel] = true
	}
	return &PromotedAttr{Key: key, Value: v}
}

// logicalExtra returns a copy of a compacted row's attributes_extra with the
// promoted keys restored and no sentinel: the dedup-off attributes_extra.
func (fb SpanFallback) logicalExtra(extra map[string]any) map[string]any {
	out := make(map[string]any, len(extra)+1)
	for k, v := range extra {
		out[k] = v
	}
	restorePromoted(out, fb.PromotedInput, fb.PromotedOutput)
	return out
}

// restorePromoted deletes the sentinels from extra and puts the promoted
// keys back.
func restorePromoted(extra map[string]any, in, out *PromotedAttr) {
	if extra == nil {
		return
	}
	for _, k := range SentinelKeys {
		delete(extra, k)
	}
	for _, p := range []*PromotedAttr{in, out} {
		if p != nil {
			extra[p.Key] = p.Value
		}
	}
}

func (fb SpanFallback) compacted() bool {
	return fb.PromotedInput != nil || fb.PromotedOutput != nil
}

// UsageReport tracks exact byte & item counts for dedup-aware billing and
// observability telemetry (`observe_add.properties`).
//
// Accounting invariants (for every batch with RawBytes > 0):
//   - 0 < StoredTotalBytes() <= RawBytes (monotonic: dedup never bills MORE
//     than raw OTLP bytes, and non-deduped spans incur zero JSON-key inflation).
//   - Turn 1 (100% new items) bills RawBytes; Turn 2..N bill only metadata +
//     preview/hash pointer overhead + genuinely new items.
type UsageReport struct {
	RawBytes              int64
	OrigExtractedBytes    int64
	KeptSpanOverheadBytes int64
	NewItemBytes          int64
	ReusedItemBytes       int64
	NewItems              int
	ReusedItems           int
	DedupApplied          bool
}

// DedupSavedBytes returns the net bytes eliminated from storage for this batch.
func (r UsageReport) DedupSavedBytes() int64 {
	if !r.DedupApplied {
		return 0
	}
	saved := r.OrigExtractedBytes - (r.KeptSpanOverheadBytes + r.NewItemBytes)
	if saved <= 0 {
		return 0
	}
	if r.RawBytes > 0 {
		minStored := r.KeptSpanOverheadBytes + r.NewItemBytes
		if minStored < 1 {
			minStored = 1
		}
		if minStored > r.RawBytes {
			minStored = r.RawBytes
		}
		maxSaved := r.RawBytes - minStored
		if saved > maxSaved {
			saved = maxSaved
		}
	}
	return saved
}

// StoredTotalBytes returns the dedup-adjusted storage bytes for this batch:
// RawBytes minus net deduplicated bytes (always in [minStored, RawBytes]).
func (r UsageReport) StoredTotalBytes() int64 {
	if r.RawBytes <= 0 {
		return r.KeptSpanOverheadBytes + r.NewItemBytes
	}
	return r.RawBytes - r.DedupSavedBytes()
}

// Batch collects distinct new trajectory_items and inline fallback snapshots
// for one drain cycle, mirroring curatedwriter.Batch.
type Batch struct {
	items     []Item
	seen      map[ItemKey]struct{}
	fallbacks []SpanFallback
	// byRow finds a row's fallback by the row map's identity.
	byRow map[unsafe.Pointer]int
}

// NewBatch creates an empty trajectory Batch.
func NewBatch() *Batch {
	return &Batch{
		seen:  make(map[ItemKey]struct{}),
		byRow: make(map[unsafe.Pointer]int),
	}
}

// AddItem records a unique item (first occurrence wins within the batch).
func (b *Batch) AddItem(it Item) {
	if it.ProjectID == "" || it.ScopeID == "" || it.ItemHash == "" {
		return
	}
	k := it.Key()
	if _, dup := b.seen[k]; dup {
		return
	}
	b.seen[k] = struct{}{}
	b.items = append(b.items, it)
}

// AddFallback records the original inline state for a compacted span row.
func (b *Batch) AddFallback(fb SpanFallback) {
	if fb.Row == nil {
		return
	}
	b.addFallback(fb)
}

func (b *Batch) addFallback(fb SpanFallback) {
	if b.byRow == nil {
		b.byRow = make(map[unsafe.Pointer]int)
	}
	b.byRow[rowKey(fb.Row)] = len(b.fallbacks)
	b.fallbacks = append(b.fallbacks, fb)
}

func rowKey(row map[string]any) unsafe.Pointer {
	return reflect.ValueOf(row).UnsafePointer()
}

// Merge appends another Batch into b, deduplicating items by ItemKey.
func (b *Batch) Merge(other *Batch) {
	if other == nil {
		return
	}
	for _, it := range other.items {
		b.AddItem(it)
	}
	for _, fb := range other.fallbacks {
		b.addFallback(fb)
	}
}

// Items returns the distinct new items collected in this batch.
func (b *Batch) Items() []Item { return b.items }

// Fallbacks returns the inline fallback records for compacted spans.
func (b *Batch) Fallbacks() []SpanFallback { return b.fallbacks }

// Empty reports whether this batch carries no new items and no fallbacks.
func (b *Batch) Empty() bool {
	return len(b.items) == 0 && len(b.fallbacks) == 0
}

// ToRows converts all items into ClickHouse JSONEachRow maps.
func (b *Batch) ToRows(now time.Time) []map[string]any {
	rows := make([]map[string]any, 0, len(b.items))
	for _, it := range b.items {
		rows = append(rows, it.ToRow(now))
	}
	return rows
}

// fallbackFor returns the fallback recorded for row (by map identity).
func (b *Batch) fallbackFor(row map[string]any) (SpanFallback, bool) {
	if b == nil || row == nil || len(b.byRow) == 0 {
		return SpanFallback{}, false
	}
	i, ok := b.byRow[rowKey(row)]
	if !ok {
		return SpanFallback{}, false
	}
	return b.fallbacks[i], true
}

// CatalogRow returns the row the observed catalog must see for row: for a
// compacted row a shallow copy whose attributes_extra is a copy of the
// dedup-off one (promoted keys present, no sentinel); any other row as it
// is. The catalog then records the same attributes with dedup on or off.
// The copy is built on each call, so only a server with an observed catalog
// pays for it; it shares no attributes_extra map with the row, so a later
// RestoreInline does not change it.
func (b *Batch) CatalogRow(row map[string]any) map[string]any {
	fb, ok := b.fallbackFor(row)
	if !ok || !fb.compacted() {
		return row
	}
	extra, ok := row["attributes_extra"].(map[string]any)
	if !ok {
		return row
	}
	view := make(map[string]any, len(row))
	for k, v := range row {
		view[k] = v
	}
	view["attributes_extra"] = fb.logicalExtra(extra)
	return view
}

// InlineCopy returns the self-contained inline form of a row that still
// carries trajectory pointers: a copy with the original input and output,
// the dedup-off attributes_extra, no pointer column and _version one higher,
// so that a replayed copy deterministically supersedes a pointer row that may
// have been stored by the same insert. The row itself is not changed. Any
// other row (no fallback, or already restored inline) is returned as is with
// ok false.
//
// InlineCopy only reads the batch, so it may be called from concurrent
// goroutines once the batch is no longer being added to.
func (b *Batch) InlineCopy(row map[string]any) (map[string]any, bool) {
	fb, ok := b.fallbackFor(row)
	if !ok || row["payload_encoding"] != EncodingItemsV1 {
		return row, false
	}
	out := make(map[string]any, len(row))
	for k, v := range row {
		out[k] = v
	}
	if extra, ok := row["attributes_extra"].(map[string]any); ok && fb.compacted() {
		out["attributes_extra"] = fb.logicalExtra(extra)
	}
	out["input"] = fb.OrigInput
	out["output"] = fb.OrigOutput
	for _, col := range SpansPointerColumns {
		delete(out, col.Name)
	}
	if v, ok := row["_version"].(uint64); ok && v < ^uint64(0) {
		out["_version"] = v + 1
	}
	return out, true
}

// RestoreInline reverts every compacted span row in this batch back to
// `payload_encoding = 'inline'`: its full original input/output, the promoted
// attributes_extra keys back with their original values and no sentinel, and
// no pointer column, so the row deep-equals the dedup-off row. Called when
// the gate closed since conversion or inserting `trajectory_items` failed;
// the restored rows need no 040 column.
func (b *Batch) RestoreInline() {
	for _, fb := range b.fallbacks {
		fb.restore()
	}
}

// RestoreInlineRows is RestoreInline for the compacted rows among rows (the
// same row maps the batch's fallbacks hold); other rows are left as they are.
// The spans drain uses it after ClickHouse refused part of a pointer batch,
// so rows already stored with their pointers keep matching what was stored.
func (b *Batch) RestoreInlineRows(rows []map[string]any) {
	for _, r := range rows {
		if fb, ok := b.fallbackFor(r); ok {
			fb.restore()
		}
	}
}

// restore reverts one compacted row to inline. It is idempotent.
func (fb SpanFallback) restore() {
	if fb.Row == nil {
		return
	}
	fb.Row["input"] = fb.OrigInput
	fb.Row["output"] = fb.OrigOutput
	if fb.compacted() {
		if extra, ok := fb.Row["attributes_extra"].(map[string]any); ok {
			restorePromoted(extra, fb.PromotedInput, fb.PromotedOutput)
		}
	}
	for _, col := range SpansPointerColumns {
		delete(fb.Row, col.Name)
	}
}
