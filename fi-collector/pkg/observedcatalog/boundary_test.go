package observedcatalog

import (
	"encoding/json"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/attributecatalog"
)

func boundarySpan() ScopedSpan {
	span := testSpan()
	span.Row["attrs_string"] = map[string]string{}
	span.Row["attrs_number"] = map[string]float64{}
	span.Row["attrs_bool"] = map[string]uint8{}
	span.Row["attributes_extra"] = map[string]any{}
	span.Row["model"] = ""
	return span
}

func roundTripBoundaryBatch(t *testing.T, batch Batch) (Batch, int) {
	t.Helper()
	chunks, err := Chunk(batch)
	if err != nil {
		t.Fatal(err)
	}
	out := Batch{Keys: []KeyRow{}, Values: []ValueRow{}}
	for _, chunk := range chunks {
		raw, err := Encode(chunk)
		if err != nil || len(raw) > MaxRecordBytes {
			t.Fatalf("chunk exceeds the unchanged record bound: %d %v", len(raw), err)
		}
		decoded, err := Decode(raw)
		if err != nil {
			t.Fatal(err)
		}
		out.Keys = append(out.Keys, decoded.Keys...)
		out.Values = append(out.Values, decoded.Values...)
	}
	if !reflect.DeepEqual(batch, out) {
		t.Fatal("chunking changed exact key/value identities")
	}
	return out, len(chunks)
}

func TestRawStringBoundariesSurviveJSONAndFoldExpansion(t *testing.T) {
	for _, test := range []struct {
		name, value string
		array       bool
	}{
		{"scalar-empty", "", false},
		{"scalar-ascii", strings.Repeat("x", 16<<10), false},
		{"scalar-controls", strings.Repeat("\x00", 16<<10), false},
		{"scalar-casefold", strings.Repeat("ΐ", 8<<10), false},
		{"array-empty", "", true},
		{"array-ascii", strings.Repeat("x", 4<<10), true},
		{"array-controls", strings.Repeat("\x00", 4<<10), true},
		{"array-casefold", strings.Repeat("ΐ", 2<<10), true},
	} {
		t.Run(test.name, func(t *testing.T) {
			span := boundarySpan()
			key := "candidate"
			if test.name == "scalar-controls" {
				key = strings.Repeat("\x00", 4096)
			} else if test.name == "scalar-casefold" {
				key = strings.Repeat("ΐ", 2048)
			}
			if test.array {
				span.Row["attributes_extra"] = map[string]any{key: []any{test.value}}
			} else {
				span.Row["attrs_string"] = map[string]string{key: test.value}
			}
			before, _ := json.Marshal(span.Row)
			batch, report, err := Extract(span, DefaultLimits())
			if err != nil || !report.Complete || len(batch.Keys) != 1 || len(batch.Values) != 1 {
				t.Fatalf("eligible string lost: keys=%d values=%d report=%+v err=%v", len(batch.Keys), len(batch.Values), report, err)
			}
			encoded, err := attributecatalog.EncodeScalar(test.value)
			if err != nil {
				t.Fatal(err)
			}
			row := batch.Values[0]
			if row.ValueJSON != encoded.ValueJSON || row.ValueFingerprint != encoded.Fingerprint || row.ValueSearchTextFolded != fold(test.value) {
				t.Fatal("value encoding, fingerprint or folding changed")
			}
			if test.array && row.AttributeType != "array" || !test.array && row.AttributeType != "string" {
				t.Fatal("source attribute type changed")
			}
			if test.name == "scalar-controls" && len(row.ValueJSON) != 6*(16<<10)+2 {
				t.Fatal("test did not reach worst-case canonical JSON expansion")
			}
			if test.name == "scalar-casefold" && len(row.ValueSearchTextFolded) != 3*(16<<10) {
				t.Fatal("test did not reach worst-case folded UTF-8 expansion")
			}
			roundTripBoundaryBatch(t, batch)
			after, _ := json.Marshal(span.Row)
			if string(before) != string(after) {
				t.Fatal("extractor mutated canonical source")
			}
		})
	}
}

func TestOversizedRawStringsKeepKeysAndLaterValues(t *testing.T) {
	for _, typ := range []string{"string", "array"} {
		limit := 16 << 10
		if typ == "array" {
			limit = 4 << 10
		}
		for _, test := range []struct{ name, value string }{
			{"ascii", strings.Repeat("x", limit+1)},
			{"controls", strings.Repeat("\x00", limit+1)},
			{"multibyte", strings.Repeat("ΐ", limit/2+1)},
			// Scalar controls here also exceed the builder's derived row
			// guard, before the raw eligibility check in Extract.
			{"oversized-encoded-row", strings.Repeat("\x00", 2*limit+1)},
		} {
			t.Run(typ+"/"+test.name, func(t *testing.T) {
				span := boundarySpan()
				tooLarge := test.value
				if typ == "array" {
					span.Row["attributes_extra"] = map[string]any{"candidate": []any{tooLarge, "kept"}}
				} else {
					span.Row["attrs_string"] = map[string]string{"candidate": tooLarge, "later": "kept"}
				}
				batch, report, err := Extract(span, DefaultLimits())
				if err != nil || report.Complete || !reflect.DeepEqual(report.GapReasons, []string{"value_too_large"}) || len(batch.Values) != 1 || batch.Values[0].ValueJSON != `"kept"` {
					t.Fatalf("raw eligibility or later-value retention failed: %+v %v", report, err)
				}
				if !slices.ContainsFunc(batch.Keys, func(k KeyRow) bool { return k.AttributeKey == "candidate" && k.AttributeType == typ }) {
					t.Fatal("oversized value removed its key")
				}
				// The same raw eligibility applies to direct publication and legacy
				// import, even when canonical JSON/folded text fit their larger caps.
				encoded, err := attributecatalog.EncodeScalar(tooLarge)
				if err != nil {
					t.Fatal(err)
				}
				row := batch.Values[0]
				row.AttributeType = typ
				row.ValueJSON, row.ValueFingerprint = encoded.ValueJSON, encoded.Fingerprint
				row.ValueSearchTextFolded = fold(tooLarge)
				if err := (Batch{Values: []ValueRow{row}}).Validate(); err == nil {
					t.Fatal("wire accepted an ineligible raw string")
				}
			})
		}
	}
}

func TestExpandedEligibleStringsChunkWithoutAggregateLoss(t *testing.T) {
	span := boundarySpan()
	values := map[string]string{}
	for _, key := range []string{"a", "b", "c", "d", "e", "f", "g", "h"} {
		values[key] = strings.Repeat("\x00", 16<<10)
	}
	span.Row["attrs_string"] = values
	batch, report, err := Extract(span, DefaultLimits())
	if err != nil || !report.Complete || len(batch.Values) != len(values) {
		t.Fatalf("aggregate budget dropped eligible values: %+v %v", report, err)
	}
	if _, count := roundTripBoundaryBatch(t, batch); count < 4 {
		t.Fatal("test did not exercise multiple 512 KiB records", count)
	}
}

func TestEmptyKeysDoNotConsumeSelectionBudgetAndFailWireValidation(t *testing.T) {
	for _, column := range []string{"attrs_string", "attrs_number", "attrs_bool", "attributes_extra"} {
		t.Run(column, func(t *testing.T) {
			span := boundarySpan()
			switch column {
			case "attrs_string":
				span.Row[column] = map[string]string{"": "bad", "valid": "ok"}
			case "attrs_number":
				span.Row[column] = map[string]float64{"": 1, "valid": 2}
			case "attrs_bool":
				span.Row[column] = map[string]uint8{"": 1, "valid": 0}
			case "attributes_extra":
				span.Row[column] = map[string]any{"": []any{"bad"}, "valid": []any{"ok"}}
			}
			batch, report, err := Extract(span, Limits{MaxKeysPerSpan: 1, MaxArrayMembersPerSpan: 1})
			if err != nil || report.Complete || !slices.Contains(report.GapReasons, "invalid_attribute_key") || len(batch.Keys) != 1 || batch.Keys[0].AttributeKey != "valid" || len(batch.Values) != 1 {
				t.Fatalf("empty key consumed selection budget: %+v %v", report, err)
			}
			key, value := batch.Keys[0], batch.Values[0]
			key.AttributeKey, key.KeyFolded, value.AttributeKey = "", "", ""
			if err := (Batch{Keys: []KeyRow{key}}).Validate(); err == nil {
				t.Fatal("wire accepted empty key row")
			}
			if err := (Batch{Values: []ValueRow{value}}).Validate(); err == nil {
				t.Fatal("wire accepted empty value key")
			}
		})
	}
}

func TestExactControlAndUnicodeKeysSurviveWire(t *testing.T) {
	span := boundarySpan()
	keys := []string{"\x00", "\n", `\n`, "\t", `\t`, " key \t\n", "é", "e\u0301", strings.Repeat("k", 4096), strings.Repeat("ΐ", 2048)}
	values := map[string]string{}
	for _, key := range keys {
		values[key] = "value"
	}
	values[strings.Repeat("k", 4097)] = "ineligible"
	span.Row["attrs_string"] = values
	batch, report, err := Extract(span, DefaultLimits())
	if err != nil || report.Complete || !reflect.DeepEqual(report.GapReasons, []string{"key_too_large"}) || len(batch.Keys) != len(keys) || len(batch.Values) != len(keys) {
		t.Fatalf("key eligibility changed: %+v %v", report, err)
	}
	for _, key := range keys {
		if !slices.ContainsFunc(batch.Keys, func(row KeyRow) bool { return row.AttributeKey == key && row.KeyFolded == fold(key) }) {
			t.Fatal("key was trimmed, normalized, conflated or discarded")
		}
	}
	roundTripBoundaryBatch(t, batch)
}
