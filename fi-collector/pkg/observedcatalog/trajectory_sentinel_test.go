package observedcatalog

import (
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/trajectory"
)

// TestCanonicalMapsHidesSentinelsOnlyOnItemsV1: on a trajectory pointer row
// the four compaction sentinels never reach the catalog; on any other row an
// attribute with the same name is a customer attribute and is kept.
func TestCanonicalMapsHidesSentinelsOnlyOnItemsV1(t *testing.T) {
	row := func(encoding string) map[string]any {
		r := map[string]any{
			"attrs_string": map[string]string{}, "attrs_number": map[string]float64{}, "attrs_bool": map[string]uint8{},
			"attributes_extra": map[string]any{
				"custom": "x", trajectory.SentinelPromotedInputKey: "input.value", trajectory.SentinelPromotedInputJSON: true,
				trajectory.SentinelPromotedOutputKey: "output.value", trajectory.SentinelPromotedOutputJSON: true,
			},
		}
		if encoding != "" {
			r["payload_encoding"] = encoding
		}
		return r
	}
	maps, err := canonicalMaps(row(trajectory.EncodingItemsV1))
	if err != nil {
		t.Fatal(err)
	}
	if len(maps.Extra) != 1 || maps.Extra["custom"] != "x" {
		t.Fatalf("items_v1 row: %v, want only the customer attribute", maps.Extra)
	}
	for _, encoding := range []string{"", trajectory.EncodingInline} {
		maps, err := canonicalMaps(row(encoding))
		if err != nil {
			t.Fatal(err)
		}
		if len(maps.Extra) != 5 {
			t.Fatalf("encoding %q: %v, want every attribute kept", encoding, maps.Extra)
		}
	}
	// The source row is never changed.
	src := row(trajectory.EncodingItemsV1)
	if _, err := canonicalMaps(src); err != nil || len(src["attributes_extra"].(map[string]any)) != 5 {
		t.Fatal("canonicalMaps changed its input")
	}
}
