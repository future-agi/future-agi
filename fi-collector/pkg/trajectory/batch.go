package trajectory

import "time"

// SpanFallback holds a direct reference to a compacted span row and its
// original inline fields so that if inserting `trajectory_items` into
// ClickHouse fails after retries, `drainNow` can atomically restore the row to
// `payload_encoding = 'inline'` before inserting `spans` — guaranteeing zero
// orphaned hashes or data loss, even when rows are split across parcels.
type SpanFallback struct {
	Row           map[string]any
	OrigInput     string
	OrigOutput    string
	StrippedAttrs map[string]any
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
}

// NewBatch creates an empty trajectory Batch.
func NewBatch() *Batch {
	return &Batch{
		seen: make(map[ItemKey]struct{}),
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
	b.fallbacks = append(b.fallbacks, fb)
}

// Merge appends another Batch into b, deduplicating items by ItemKey.
func (b *Batch) Merge(other *Batch) {
	if other == nil {
		return
	}
	for _, it := range other.items {
		b.AddItem(it)
	}
	b.fallbacks = append(b.fallbacks, other.fallbacks...)
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

// RestoreInline reverts every compacted span row in this batch back to
// `payload_encoding = 'inline'` with its full original input/output and any
// stripped flattened attributes. Called if inserting `trajectory_items` into
// ClickHouse fails after retries. Removes the additive Phase 1 columns so
// fallback rows remain compatible even if ClickHouse lacks 028_trajectory_items.sql.
func (b *Batch) RestoreInline() {
	for _, fb := range b.fallbacks {
		if fb.Row == nil {
			continue
		}
		fb.Row["input"] = fb.OrigInput
		fb.Row["output"] = fb.OrigOutput
		if len(fb.StrippedAttrs) > 0 {
			if of, ok := fb.Row["attributes_extra"].(map[string]any); ok && of != nil {
				for k, v := range fb.StrippedAttrs {
					of[k] = v
				}
			}
		}
		delete(fb.Row, "input_item_hashes")
		delete(fb.Row, "output_item_hashes")
		delete(fb.Row, "tool_def_hashes")
		delete(fb.Row, "item_scope_id")
		delete(fb.Row, "payload_encoding")
		delete(fb.Row, "payload_envelope")
	}
}
