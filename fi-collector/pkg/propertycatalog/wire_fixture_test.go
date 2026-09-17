package propertycatalog

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"os"
	"testing"
)

type crossLanguageWireFixture struct {
	Format  string                         `json:"format"`
	Version uint16                         `json:"version"`
	Cases   []crossLanguageWireFixtureCase `json:"cases"`
}

type crossLanguageWireFixtureCase struct {
	Name          string `json:"name"`
	WireBase64    string `json:"wire_base64"`
	PayloadSHA256 string `json:"payload_sha256"`
	EnvelopeID    string `json:"envelope_id"`
}

func crossLanguageFixtureEnvelopes(t *testing.T) []struct {
	name     string
	envelope WireEnvelope
} {
	t.Helper()
	definition := testDefinition()
	definitionPayload, err := BuildPayload(
		[]DefinitionRow{definition}, nil, 10, MaxChunkBytes, 1, testDigest("fixture-definition-batch"),
	)
	if err != nil {
		t.Fatal(err)
	}
	definitionInput := testEnvelopeInput(t, 1, ZeroSHA256, 1)
	definitionInput.Payload = definitionPayload
	definitionEnvelope := mustEnvelope(t, definitionInput)

	valuePayload, err := BuildPayload(
		nil, []AttributeValueRow{testValue()}, 10, MaxChunkBytes, 1, testDigest("fixture-value-batch"),
	)
	if err != nil {
		t.Fatal(err)
	}
	valueInput := testEnvelopeInput(t, 2, definitionEnvelope.PayloadSHA256(), 1)
	valueInput.SourceVersion = 2
	valueInput.SourceFingerprint = testDigest("fixture-value-source")
	valueInput.Payload = valuePayload
	valueEnvelope := mustEnvelope(t, valueInput)

	gapPayload, err := BuildPayload(nil, nil, 10, MaxChunkBytes, 1, testDigest("fixture-gap-batch"))
	if err != nil {
		t.Fatal(err)
	}
	gapPayload.Outcome = OutcomeGap
	gapPayload.GapReasons = []string{"fixture_gap"}
	gapInput := testEnvelopeInput(t, 3, valueEnvelope.PayloadSHA256(), 1)
	gapInput.SourceVersion = 3
	gapInput.SourceFingerprint = testDigest("fixture-gap-source")
	gapInput.Payload = gapPayload
	gapEnvelope := mustEnvelope(t, gapInput)

	terminalPayload, err := BuildPayload(nil, nil, 10, MaxChunkBytes, 0, testDigest("fixture-terminal-batch"))
	if err != nil {
		t.Fatal(err)
	}
	terminalInput := testEnvelopeInput(t, 4, gapEnvelope.PayloadSHA256(), 0)
	terminalInput.SourceVersion = 4
	terminalInput.SourceFingerprint = testDigest("fixture-terminal-source")
	terminalInput.Terminal = true
	terminalInput.Payload = terminalPayload
	terminalEnvelope := mustEnvelope(t, terminalInput)

	return []struct {
		name     string
		envelope WireEnvelope
	}{
		{"definition", definitionEnvelope},
		{"value", valueEnvelope},
		{"gap", gapEnvelope},
		{"terminal", terminalEnvelope},
	}
}

func TestCrossLanguageWireV1Fixtures(t *testing.T) {
	generated := crossLanguageFixtureEnvelopes(t)
	if os.Getenv("PRINT_PROPERTY_CATALOG_WIRE_FIXTURE") == "1" ||
		os.Getenv("UPDATE_PROPERTY_CATALOG_WIRE_FIXTURE") == "1" {
		fixture := crossLanguageWireFixture{
			Format: "futureagi.property-catalog-wire-fixtures", Version: 1,
			Cases: make([]crossLanguageWireFixtureCase, 0, len(generated)),
		}
		for _, item := range generated {
			raw, _ := item.envelope.MarshalBinary()
			snapshot := item.envelope.Snapshot()
			fixture.Cases = append(fixture.Cases, crossLanguageWireFixtureCase{
				Name: item.name, WireBase64: base64.StdEncoding.EncodeToString(raw),
				PayloadSHA256: snapshot.PayloadSHA256, EnvelopeID: snapshot.EnvelopeID,
			})
		}
		raw, _ := json.MarshalIndent(fixture, "", "  ")
		raw = append(raw, '\n')
		if os.Getenv("UPDATE_PROPERTY_CATALOG_WIRE_FIXTURE") == "1" {
			if err := os.WriteFile("testdata/wire_v1_fixtures.json", raw, 0o600); err != nil {
				t.Fatal(err)
			}
			return
		}
		t.Log(string(raw))
		return
	}
	raw, err := os.ReadFile("testdata/wire_v1_fixtures.json")
	if err != nil {
		t.Fatal(err)
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	var fixture crossLanguageWireFixture
	if err := decoder.Decode(&fixture); err != nil {
		t.Fatal(err)
	}
	if fixture.Format != "futureagi.property-catalog-wire-fixtures" || fixture.Version != 1 ||
		len(fixture.Cases) != len(generated) {
		t.Fatalf("fixture header/count=%+v", fixture)
	}
	for index, expected := range generated {
		item := fixture.Cases[index]
		wire, err := base64.StdEncoding.DecodeString(item.WireBase64)
		if err != nil {
			t.Fatal(err)
		}
		parsed, err := ParseWireEnvelope(wire)
		if err != nil {
			t.Fatalf("fixture %s parse: %v", item.Name, err)
		}
		expectedRaw, _ := expected.envelope.MarshalBinary()
		if item.Name != expected.name || !bytes.Equal(wire, expectedRaw) ||
			item.PayloadSHA256 != parsed.PayloadSHA256() || item.EnvelopeID != parsed.EnvelopeID() {
			t.Fatalf("fixture %d drift name=%s payload=%s envelope=%s", index, item.Name, item.PayloadSHA256, item.EnvelopeID)
		}
	}
}
