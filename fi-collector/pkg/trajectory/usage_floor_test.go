package trajectory

import "testing"

// TestUsageReportFloorIncludesNonExtractedSpanBytes is the billing review's
// worked OpenInference turn, all items reused: input.value 40,000 B,
// flattened input messages 38,000 B, output.value 1,000 B, flattened output
// 900 B, other attributes 1,500 B, so raw = 81,400 B, and the kept pointer
// overhead (previews, envelope, scope, hashes) is 2,700 B. The span still
// stores 38,900 + 1,500 + 2,700 = 43,100 B.
//
// Crediting each extracted side once, as raw carries it (41,000 B), bills
// exactly those 43,100 B. Crediting the attributes_extra identity copy as
// well (82,000 B) would bill 2,700 B, below what is stored: the contrast
// case pins that this report's floor does not catch that by itself, so the
// credit basis is what keeps the bill at the stored bytes.
func TestUsageReportFloorIncludesNonExtractedSpanBytes(t *testing.T) {
	const raw, kept, stored = 81_400, 2_700, 43_100
	oneCopy := UsageReport{RawBytes: raw, OrigExtractedBytes: 40_000 + 1_000, KeptSpanOverheadBytes: kept, ReusedItems: 3, DedupApplied: true}
	if got := oneCopy.StoredTotalBytes(); got != stored {
		t.Fatalf("one-copy credit bills %d B, want the %d B still stored", got, stored)
	}
	if got := oneCopy.DedupSavedBytes(); got != raw-stored {
		t.Fatalf("saved %d B, want %d", got, raw-stored)
	}
	twoCopies := oneCopy
	twoCopies.OrigExtractedBytes = 2 * (40_000 + 1_000)
	if got := twoCopies.StoredTotalBytes(); got != kept {
		t.Fatalf("contrast: crediting the identity copy bills %d B, want %d (below the stored bytes)", got, kept)
	}
	// New item bytes are billed on top, never credited away.
	withNew := oneCopy
	withNew.NewItemBytes = 5_000
	if got := withNew.StoredTotalBytes(); got != stored+5_000 {
		t.Fatalf("with 5,000 B of new items: billed %d B, want %d", got, stored+5_000)
	}
}
