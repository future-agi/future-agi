package attributecatalog

import (
	"bytes"
	"encoding/json"
	"math"
	"os"
	"reflect"
	"testing"
	"time"
)

type builderFixture struct {
	Scope struct {
		ProjectID    string `json:"project_id"`
		SeenAt       string `json:"seen_at"`
		CatalogEpoch uint16 `json:"catalog_epoch"`
	} `json:"scope"`
	Limits struct {
		MaxKeys         int `json:"max_keys"`
		MaxArrayMembers int `json:"max_array_members"`
		MaxEncodedBytes int `json:"max_encoded_bytes"`
	} `json:"limits"`
	Attributes struct {
		Strings  map[string]string          `json:"strings"`
		Numbers  map[string]json.RawMessage `json:"numbers"`
		Booleans map[string]uint8           `json:"booleans"`
		Extra    map[string]json.RawMessage `json:"extra"`
	} `json:"attributes"`
	Expected struct {
		Keys     [][]any `json:"keys"`
		Values   [][]any `json:"values"`
		Metadata struct {
			Complete                     bool     `json:"complete"`
			Truncated                    bool     `json:"truncated"`
			GapReasons                   []string `json:"gap_reasons"`
			CandidateKeys                int      `json:"candidate_keys"`
			ValidCandidateKeys           int      `json:"valid_candidate_keys"`
			KeyRowsEmitted               int      `json:"key_rows_emitted"`
			KeysOmitted                  int      `json:"keys_omitted"`
			ValueRowsEmitted             int      `json:"value_rows_emitted"`
			ArrayMembersTotal            int      `json:"array_members_total"`
			ArrayMembersInspected        int      `json:"array_members_inspected"`
			ArrayMembersOmitted          int      `json:"array_members_omitted"`
			NonScalarArrayMembersSkipped int      `json:"non_scalar_array_members_skipped"`
			DuplicateValuesSkipped       int      `json:"duplicate_values_skipped"`
			InvalidAttributeKeys         int      `json:"invalid_attribute_keys"`
			InvalidScalarValues          int      `json:"invalid_scalar_values"`
			InvalidBooleanValues         int      `json:"invalid_boolean_values"`
			EncodedBytes                 int      `json:"encoded_bytes"`
		} `json:"metadata"`
	} `json:"expected"`
}

func TestBuilderMatchesSharedGoldenFixture(t *testing.T) {
	fixture := readBuilderFixture(t)
	seenAt, err := time.Parse(time.RFC3339Nano, fixture.Scope.SeenAt)
	if err != nil {
		t.Fatal(err)
	}
	numbers := make(map[string]float64, len(fixture.Attributes.Numbers))
	for key, raw := range fixture.Attributes.Numbers {
		var value float64
		if err := json.Unmarshal(raw, &value); err != nil {
			t.Fatal(err)
		}
		numbers[key] = value
	}
	extra := make(map[string]any, len(fixture.Attributes.Extra))
	for key, raw := range fixture.Attributes.Extra {
		decoder := json.NewDecoder(bytes.NewReader(raw))
		decoder.UseNumber()
		var value any
		if err := decoder.Decode(&value); err != nil {
			t.Fatal(err)
		}
		extra[key] = value
	}

	result, err := BuildRows(
		Scope{fixture.Scope.ProjectID, seenAt, fixture.Scope.CatalogEpoch},
		SpanAttributeMaps{fixture.Attributes.Strings, numbers, fixture.Attributes.Booleans, extra},
		BuildLimits{fixture.Limits.MaxKeys, fixture.Limits.MaxArrayMembers, fixture.Limits.MaxEncodedBytes},
	)
	if err != nil {
		t.Fatal(err)
	}
	assertExpectedBuilderRows(t, fixture, result)
	metadata := fixture.Expected.Metadata
	if result.Metadata.Complete != metadata.Complete || result.Metadata.Truncated != metadata.Truncated ||
		!reflect.DeepEqual(result.Metadata.GapReasons, metadata.GapReasons) ||
		result.Metadata.CandidateKeys != metadata.CandidateKeys ||
		result.Metadata.ValidCandidateKeys != metadata.ValidCandidateKeys ||
		result.Metadata.KeyRowsEmitted != metadata.KeyRowsEmitted ||
		result.Metadata.KeysOmitted != metadata.KeysOmitted ||
		result.Metadata.ValueRowsEmitted != metadata.ValueRowsEmitted ||
		result.Metadata.ArrayMembersTotal != metadata.ArrayMembersTotal ||
		result.Metadata.ArrayMembersInspected != metadata.ArrayMembersInspected ||
		result.Metadata.ArrayMembersOmitted != metadata.ArrayMembersOmitted ||
		result.Metadata.NonScalarArrayMembersSkipped != metadata.NonScalarArrayMembersSkipped ||
		result.Metadata.DuplicateValuesSkipped != metadata.DuplicateValuesSkipped ||
		result.Metadata.InvalidAttributeKeys != metadata.InvalidAttributeKeys ||
		result.Metadata.InvalidScalarValues != metadata.InvalidScalarValues ||
		result.Metadata.InvalidBooleanValues != metadata.InvalidBooleanValues ||
		result.Metadata.EncodedBytes != metadata.EncodedBytes {
		t.Fatalf("metadata mismatch: %#v", result.Metadata)
	}
}

func TestBuilderCapsHugeArrayAtInspectedPrefix(t *testing.T) {
	members := make([]any, 1_000_000)
	for index := range members {
		members[index] = "tail"
	}
	members[0], members[1], members[2] = "a", "a", 3
	result, err := BuildRows(
		Scope{},
		SpanAttributeMaps{Extra: map[string]any{"array": members}},
		BuildLimits{MaxKeys: 1, MaxArrayMembers: 3, MaxEncodedBytes: 1_000},
	)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(result.Metadata.GapReasons, []string{GapMaxArrayMembers}) ||
		result.Metadata.ArrayMembersInspected != 3 || result.Metadata.ArrayMembersOmitted != 999_997 ||
		len(result.ValueRows) != 2 || result.Metadata.DuplicateValuesSkipped != 1 {
		t.Fatalf("unexpected capped result: %#v", result)
	}
}

func TestBuilderHugeLimitsDoNotPreallocateFromLimits(t *testing.T) {
	result, err := BuildRows(
		Scope{},
		SpanAttributeMaps{Strings: map[string]string{"only": "one"}},
		BuildLimits{MaxKeys: math.MaxInt, MaxArrayMembers: math.MaxInt, MaxEncodedBytes: math.MaxInt},
	)
	if err != nil || !result.Metadata.Complete || len(result.ValueRows) != 1 {
		t.Fatalf("unexpected huge-limit result: %#v, %v", result, err)
	}
}

func TestBuilderReportsEveryLimitWithoutSilentCompletion(t *testing.T) {
	result, err := BuildRows(
		Scope{},
		SpanAttributeMaps{
			Strings: map[string]string{"a": "too-long", "b": "omitted"},
			Extra:   map[string]any{"array": []any{1, 2}},
		},
		BuildLimits{MaxKeys: 2, MaxArrayMembers: 0, MaxEncodedBytes: 10},
	)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{GapMaxKeys, GapMaxArrayMembers, GapMaxEncodedBytes}
	if result.Metadata.Complete || !result.Metadata.Truncated || !reflect.DeepEqual(result.Metadata.GapReasons, want) {
		t.Fatalf("limit was not explicit: %#v", result.Metadata)
	}
}

func readBuilderFixture(t *testing.T) builderFixture {
	t.Helper()
	raw, err := os.ReadFile("testdata/builder_fixtures.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture builderFixture
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}

func assertExpectedBuilderRows(t *testing.T, fixture builderFixture, result BuildResult) {
	t.Helper()
	keys := make([][]any, 0, len(result.KeyRows))
	for _, row := range result.KeyRows {
		if row.ProjectID != fixture.Scope.ProjectID || row.FirstSeen.Format(time.RFC3339Nano) != fixture.Scope.SeenAt ||
			row.LastSeen != row.FirstSeen || row.CatalogEpoch != fixture.Scope.CatalogEpoch {
			t.Fatalf("key scope mismatch: %#v", row)
		}
		keys = append(keys, []any{row.AttributeKey, row.KeyFolded, row.AttributeType})
	}
	values := make([][]any, 0, len(result.ValueRows))
	for _, row := range result.ValueRows {
		if row.ProjectID != fixture.Scope.ProjectID || row.FirstSeen.Format(time.RFC3339Nano) != fixture.Scope.SeenAt ||
			row.LastSeen != row.FirstSeen || row.CatalogEpoch != fixture.Scope.CatalogEpoch {
			t.Fatalf("value scope mismatch: %#v", row)
		}
		values = append(values, []any{row.AttributeKey, row.AttributeType, row.ValueJSON, row.ValueFingerprint})
	}
	if !reflect.DeepEqual(keys, fixture.Expected.Keys) || !reflect.DeepEqual(values, fixture.Expected.Values) {
		t.Fatalf("rows mismatch:\nkeys=%#v\nvalues=%#v", keys, values)
	}
}
