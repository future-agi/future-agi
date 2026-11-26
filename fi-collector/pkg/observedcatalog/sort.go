package observedcatalog

import (
	"bytes"
	"encoding/json"
	"sort"
)

// MergeSortScratchBytes is the per-handoff allowance charged by OTLP row
// admission. It covers both tables' keys, allocator rounding, the temporary
// marshaler buffer and sort metadata. It is not a new pool or a larger budget.
// Backfill/replay use the same bounded scratch, outside OTLP admission.
const MergeSortScratchBytes = 1 << 20

const mergeSortKeyLimit = 128 << 10

// sortJSONRows decorates each row once instead of marshaling two whole rows on
// every comparison. The conservative preflight happens BEFORE allocating keys:
// a JSON string expands by at most six bytes per input byte, and 512 covers
// every fixed field name, quote and delimiter of KeyRow/ValueRow. Oversized or
// arbitrary caller batches retain the old comparator, never an unbounded cache.
func sortJSONRows[T any](rows []T, stringBytes func(T) int) {
	if len(rows) < 2 {
		return
	}
	remaining := mergeSortKeyLimit
	for _, row := range rows {
		n := stringBytes(row)
		if remaining < 512 || n > (remaining-512)/6 {
			sort.Slice(rows, func(i, j int) bool {
				a, _ := json.Marshal(rows[i])
				b, _ := json.Marshal(rows[j])
				return bytes.Compare(a, b) < 0
			})
			return
		}
		remaining -= 512 + 6*n
	}
	type decorated struct {
		key []byte
		row T
	}
	keys := make([]decorated, len(rows))
	for i, row := range rows {
		keys[i].key, _ = json.Marshal(row)
		keys[i].row = row
	}
	sort.Slice(keys, func(i, j int) bool { return bytes.Compare(keys[i].key, keys[j].key) < 0 })
	for i := range rows {
		rows[i] = keys[i].row
	}
}

func keyStringBytes(r KeyRow) int {
	return len(r.OrganizationID) + len(r.WorkspaceID) + len(r.ProjectID) + len(r.SourceKind) + len(r.AttributeKey) + len(r.AttributeType) + len(r.KeyFolded) + len(r.FirstSeen) + len(r.LastSeen)
}

func valueStringBytes(r ValueRow) int {
	return len(r.OrganizationID) + len(r.WorkspaceID) + len(r.ProjectID) + len(r.SourceKind) + len(r.AttributeKey) + len(r.AttributeType) + len(r.ValueFingerprint) + len(r.ValueJSON) + len(r.ValueSearchTextFolded) + len(r.FirstSeen) + len(r.LastSeen)
}
