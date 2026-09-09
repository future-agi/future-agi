package attributecatalog

import (
	"bytes"
	"encoding/json"
	"math"
	"os"
	"regexp"
	"testing"
)

type goldenDocument struct {
	Fixtures []goldenFixture `json:"fixtures"`
}

type goldenFixture struct {
	Name        string          `json:"name"`
	Kind        string          `json:"kind"`
	Value       json.RawMessage `json:"value"`
	ValueJSON   string          `json:"value_json"`
	SearchText  string          `json:"search_text"`
	Fingerprint string          `json:"fingerprint"`
}

func TestCodecMatchesSharedGoldenFixtures(t *testing.T) {
	raw, err := os.ReadFile("testdata/canonical_fixtures.json")
	if err != nil {
		t.Fatal(err)
	}
	var document goldenDocument
	if err := json.Unmarshal(raw, &document); err != nil {
		t.Fatal(err)
	}
	hex64 := regexp.MustCompile(`^[0-9a-f]{64}$`)
	for _, fixture := range document.Fixtures {
		t.Run(fixture.Name, func(t *testing.T) {
			decoder := json.NewDecoder(bytes.NewReader(fixture.Value))
			decoder.UseNumber()
			var value any
			if err := decoder.Decode(&value); err != nil {
				t.Fatal(err)
			}
			got, err := EncodeScalar(value)
			if err != nil {
				t.Fatal(err)
			}
			if got.Kind != fixture.Kind || got.ValueJSON != fixture.ValueJSON ||
				got.SearchText != fixture.SearchText || got.Fingerprint != fixture.Fingerprint {
				t.Fatalf("got %#v, want %#v", got, fixture)
			}
			if !hex64.MatchString(got.Fingerprint) {
				t.Fatalf("fingerprint is not lowercase SHA-256 hex: %q", got.Fingerprint)
			}
		})
	}
}

func TestCodecRejectsNonSelectableAndNonFiniteValues(t *testing.T) {
	for _, value := range []any{nil, []any{"x"}, map[string]any{"x": 1}, math.NaN(), math.Inf(1)} {
		if _, err := EncodeScalar(value); err == nil {
			t.Fatalf("EncodeScalar(%T) unexpectedly succeeded", value)
		}
	}
}
