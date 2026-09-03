package propertycatalog

import (
	"bytes"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/attributecatalog"
)

func TestWireEnvelopeGoldenAndStrictRoundTrip(t *testing.T) {
	envelope := mustEnvelope(t, testEnvelopeInput(t, 1, ZeroSHA256, 1))
	raw, err := envelope.MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	snapshot := envelope.Snapshot()
	// These identities pin field order, canonical JSON, nested JSONEachRow,
	// source-fence placement, and every count in the v1 contract.
	const wantEnvelopeID = "a0fac4c966b54d341afcafb8b4acf5c5145d05628eec3a5fe7818434a0d5d903"
	const wantPayloadSHA = "1f2df4bdd49fc0bc74c7bc4142dfbaeb02dab658207683f35f90dd6458170324"
	const wantRawSHA = "545866dc2c97ac9b97b4644e21110920af37c7a155bfaedd4c24c5cfd68081d0"
	if snapshot.EnvelopeID != wantEnvelopeID || snapshot.PayloadSHA256 != wantPayloadSHA ||
		testDigest(string(raw)) != wantRawSHA {
		t.Fatalf("golden drift envelope=%s payload=%s raw=%s", snapshot.EnvelopeID, snapshot.PayloadSHA256, testDigest(string(raw)))
	}
	parsed, err := ParseWireEnvelope(raw)
	if err != nil {
		t.Fatal(err)
	}
	roundTrip, _ := parsed.MarshalBinary()
	if !bytes.Equal(raw, roundTrip) {
		t.Fatal("strict round trip changed bytes")
	}
	copySnapshot := parsed.Snapshot()
	copySnapshot.Payload.Chunks[0].JSONEachRow[0] ^= 0xff
	again := parsed.Snapshot()
	if again.Payload.Chunks[0].JSONEachRow[0] == copySnapshot.Payload.Chunks[0].JSONEachRow[0] {
		t.Fatal("snapshot exposed mutable envelope bytes")
	}
}

func TestWireEnvelopeRejectsNoncanonicalPoisonAndForbiddenTargets(t *testing.T) {
	envelope := mustEnvelope(t, testEnvelopeInput(t, 1, ZeroSHA256, 1))
	raw, _ := envelope.MarshalBinary()
	if _, err := ParseWireEnvelope(append([]byte{' '}, raw...)); err == nil || !strings.Contains(err.Error(), "canonical") {
		t.Fatalf("noncanonical JSON error=%v", err)
	}
	withUnknown := append([]byte{}, raw[:len(raw)-1]...)
	withUnknown = append(withUnknown, []byte(`,"unknown":true}`)...)
	if _, err := ParseWireEnvelope(withUnknown); err == nil || !strings.Contains(err.Error(), "unknown") {
		t.Fatalf("unknown field error=%v", err)
	}

	var document envelopeJSON
	if err := json.Unmarshal(raw, &document); err != nil {
		t.Fatal(err)
	}
	document.Payload.Chunks[0].EncodedSHA256 = testDigest("tampered")
	tampered, _ := json.Marshal(document)
	if _, err := ParseWireEnvelope(tampered); err == nil ||
		(!strings.Contains(err.Error(), "identity") && !strings.Contains(err.Error(), "digest") &&
			!strings.Contains(err.Error(), "hash")) {
		t.Fatalf("mismatched hash error=%v", err)
	}

	input := testEnvelopeInput(t, 1, ZeroSHA256, 1)
	input.Payload.Chunks[0].Table = Table("spans")
	if _, err := NewWireEnvelope(input); err == nil || !strings.Contains(err.Error(), "forbidden table") {
		t.Fatalf("forbidden table error=%v", err)
	}
}

func TestWireEnvelopeValidatesAllRowsCountsAndTombstones(t *testing.T) {
	input := testEnvelopeInput(t, 1, ZeroSHA256, 1)
	input.Payload.Chunks[1].JSONEachRow = bytes.Replace(
		input.Payload.Chunks[1].JSONEachRow, []byte(testWorkspace), []byte(testOrganization), 1,
	)
	if _, err := NewWireEnvelope(input); err == nil || !strings.Contains(err.Error(), "tenant") {
		t.Fatalf("cross-tenant value error=%v", err)
	}

	input = testEnvelopeInput(t, 1, ZeroSHA256, 1)
	input.Payload.DefinitionRows++
	if _, err := NewWireEnvelope(input); err == nil || !strings.Contains(err.Error(), "aggregate row counts") {
		t.Fatalf("count drift error=%v", err)
	}

	tombstone := testDefinition()
	deleted := testSeen
	tombstone.IsDeleted, tombstone.DeletedAt = 1, &deleted
	refreshDefinitionHashes(t, &tombstone)
	payload, err := BuildPayload([]DefinitionRow{tombstone}, nil, 10, MaxChunkBytes, 1, testDigest("delete-batch"))
	if err != nil {
		t.Fatal(err)
	}
	input = testEnvelopeInput(t, 1, ZeroSHA256, 1)
	input.Payload = payload
	got := mustEnvelope(t, input).Snapshot()
	if got.Payload.TombstoneRows != 1 || got.Payload.DefinitionRows != 1 {
		t.Fatalf("tombstone counts=%+v", got.Payload)
	}

	input.Payload.TombstoneRows = 0
	if _, err := NewWireEnvelope(input); err == nil || !strings.Contains(err.Error(), "aggregate row counts") {
		t.Fatalf("tombstone count drift error=%v", err)
	}
}

func TestDefinitionVisibilityBindsWorkspaceAndAlwaysSentinel(t *testing.T) {
	definition := testDefinition()
	scope := Scope{
		OrganizationID: testOrganization, WorkspaceID: testWorkspace,
		CatalogEpoch: 3, CatalogRevision: 1, ProjectionVersion: 1,
		BuildToken:    testBuildToken,
		SourceAdapter: AdapterSpanAttribute, SourceVersion: 1,
		SourceFingerprint: testDigest("source-1"), ProducerStreamID: testStream,
		Sequence: 1,
	}

	definition.VisibilityScope = VisibilityWorkspace
	definition.VisibilityID = testWorkspace
	refreshDefinitionHashes(t, &definition)
	if err := validateDefinition(definition, scope); err != nil {
		t.Fatalf("workspace-owned definition was rejected: %v", err)
	}
	definition.VisibilityID = testProject
	if err := validateDefinition(definition, scope); err == nil ||
		!strings.Contains(err.Error(), "row workspace") {
		t.Fatalf("foreign workspace visibility error=%v", err)
	}

	definition.VisibilityScope = VisibilityAlways
	definition.VisibilityID = "00000000-0000-0000-0000-000000000000"
	refreshDefinitionHashes(t, &definition)
	if err := validateDefinition(definition, scope); err != nil {
		t.Fatalf("always definition with zero sentinel was rejected: %v", err)
	}
}

func TestGapEnvelopeMustBeExplicitAndMayRetainValidatedPartialData(t *testing.T) {
	input := testEnvelopeInput(t, 1, ZeroSHA256, 1)
	input.Payload.Outcome = OutcomeGap
	input.Payload.GapReasons = []string{"source_timeout"}
	if _, err := NewWireEnvelope(input); err != nil {
		t.Fatal(err)
	}
	input.Payload.GapReasons = nil
	if _, err := NewWireEnvelope(input); err == nil {
		t.Fatal("gap without a reason was accepted")
	}
}

func TestAttributeValueSearchTextMustUseExactCasefoldContract(t *testing.T) {
	value := testValue()
	encoded, err := attributecatalog.EncodeScalar("Straße")
	if err != nil {
		t.Fatal(err)
	}
	value.ValueJSON = encoded.ValueJSON
	value.ValueFingerprint = encoded.Fingerprint
	value.ValueSearchTextFolded = "Straße"
	payload, err := BuildPayload(nil, []AttributeValueRow{value}, 1, MaxChunkBytes, 1, testDigest("fold"))
	if err != nil {
		t.Fatal(err)
	}
	input := testEnvelopeInput(t, 1, ZeroSHA256, 1)
	input.Payload = payload
	if _, err := NewWireEnvelope(input); err == nil || !strings.Contains(err.Error(), "does not match value_json") {
		t.Fatalf("unfolded value-search text error=%v", err)
	}
	value.ValueSearchTextFolded = foldPropertyText("Straße")
	payload, _ = BuildPayload(nil, []AttributeValueRow{value}, 1, MaxChunkBytes, 1, testDigest("fold"))
	input.Payload = payload
	if _, err := NewWireEnvelope(input); err != nil {
		t.Fatalf("exact folded value-search text rejected: %v", err)
	}
	value.ValueSearchTextFolded = "unrelated-but-folded"
	payload, _ = BuildPayload(nil, []AttributeValueRow{value}, 1, MaxChunkBytes, 1, testDigest("fold"))
	input.Payload = payload
	if _, err := NewWireEnvelope(input); err == nil || !strings.Contains(err.Error(), "does not match value_json") {
		t.Fatalf("unrelated folded value-search text error=%v", err)
	}
	value.ValueSearchTextFolded = foldPropertyText("Straße")
	value.ValueFingerprint = testDigest("tampered")
	payload, _ = BuildPayload(nil, []AttributeValueRow{value}, 1, MaxChunkBytes, 1, testDigest("fold"))
	input.Payload = payload
	if _, err := NewWireEnvelope(input); err == nil || !strings.Contains(err.Error(), "fingerprint") {
		t.Fatalf("tampered fingerprint error=%v", err)
	}
}

func TestDefinitionAndValueRowsRequireExactEnvelopeBuildToken(t *testing.T) {
	foreignToken := "66666666-6666-4666-8666-666666666666"
	definition := testDefinition()
	definition.BuildToken = foreignToken
	payload, err := BuildPayload(
		[]DefinitionRow{definition}, nil, 1, MaxChunkBytes, 1, testDigest("definition-token"),
	)
	if err != nil {
		t.Fatal(err)
	}
	input := testEnvelopeInput(t, 1, ZeroSHA256, 1)
	input.Payload = payload
	if _, err := NewWireEnvelope(input); err == nil || !strings.Contains(err.Error(), "build token") {
		t.Fatalf("foreign definition build token error=%v", err)
	}

	value := testValue()
	value.BuildToken = foreignToken
	payload, err = BuildPayload(nil, []AttributeValueRow{value}, 1, MaxChunkBytes, 1, testDigest("value-token"))
	if err != nil {
		t.Fatal(err)
	}
	input.Payload = payload
	if _, err := NewWireEnvelope(input); err == nil || !strings.Contains(err.Error(), "build token") {
		t.Fatalf("foreign value build token error=%v", err)
	}
}

func TestAttributeValueTypeMustMatchScalarOrArrayMemberContract(t *testing.T) {
	for _, attributeType := range []string{"boolean", "map", "json"} {
		value := testValue()
		value.AttributeType = attributeType
		payload, err := BuildPayload(nil, []AttributeValueRow{value}, 1, MaxChunkBytes, 1, testDigest("type-mismatch"))
		if err != nil {
			t.Fatal(err)
		}
		input := testEnvelopeInput(t, 1, ZeroSHA256, 1)
		input.Payload = payload
		if _, err := NewWireEnvelope(input); err == nil || !strings.Contains(err.Error(), "scalar value_json kind") {
			t.Fatalf("attribute type %q mismatch error=%v", attributeType, err)
		}
	}
	value := testValue()
	value.AttributeType = "array"
	payload, err := BuildPayload(nil, []AttributeValueRow{value}, 1, MaxChunkBytes, 1, testDigest("array-member"))
	if err != nil {
		t.Fatal(err)
	}
	input := testEnvelopeInput(t, 1, ZeroSHA256, 1)
	input.Payload = payload
	if _, err := NewWireEnvelope(input); err != nil {
		t.Fatalf("scalar array member rejected: %v", err)
	}
}

func TestDefinitionJSONMatchesPythonUTF8CanonicalDomain(t *testing.T) {
	canonical := `{"a":"<>&` + "\u2028" + `","n":1,"nested":{"ß":"Straße"}}`
	if err := validateCanonicalJSON("definition_json", canonical, MaxDefinitionJSONBytes, false); err != nil {
		t.Fatalf("Python-compatible UTF-8 JSON rejected: %v", err)
	}
	for _, invalid := range []string{
		`{"a":"\u003c"}`,
		`{"float":1.2300}`,
		`{"float":1e2}`,
		`{"z":1,"a":2}`,
		`["not-an-object"]`,
	} {
		if err := validateCanonicalJSON("definition_json", invalid, MaxDefinitionJSONBytes, false); err == nil {
			t.Fatalf("noncanonical/unsupported definition JSON accepted: %s", invalid)
		}
	}
	for _, scalar := range []string{`"<>&` + "\u2028" + `"`, `1.2300`, `null`} {
		err := validateCanonicalJSON("value_json", scalar, MaxValueJSONBytes, true)
		if scalar == `"<>&`+"\u2028"+`"` {
			if err != nil {
				t.Fatalf("canonical native string rejected: %v", err)
			}
		} else if err == nil {
			t.Fatalf("noncanonical/non-scalar native value accepted: %s", scalar)
		}
	}
}

func TestSharedCodecV1GoldenFixtures(t *testing.T) {
	raw, err := os.ReadFile("testdata/codec_v1_fixtures.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Format        string `json:"format"`
		Version       int    `json:"version"`
		CanonicalJSON []struct {
			Name      string `json:"name"`
			Canonical string `json:"canonical"`
			SHA256    string `json:"sha256"`
		} `json:"canonical_json"`
		CaseFold []struct {
			Source string `json:"source"`
			Folded string `json:"folded"`
		} `json:"casefold"`
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&fixture); err != nil {
		t.Fatal(err)
	}
	if fixture.Format != "futureagi.property-catalog-codec-fixtures" || fixture.Version != 1 ||
		len(fixture.CanonicalJSON) != 3 || len(fixture.CaseFold) != 5 {
		t.Fatalf("fixture header/counts=%+v", fixture)
	}
	for _, item := range fixture.CanonicalJSON {
		if err := validateCanonicalJSON("definition_json", item.Canonical, MaxDefinitionJSONBytes, false); err != nil {
			t.Fatalf("fixture %s is not accepted by Go codec: %v", item.Name, err)
		}
		if testDigest(item.Canonical) != item.SHA256 {
			t.Fatalf("fixture %s digest=%s want=%s", item.Name, testDigest(item.Canonical), item.SHA256)
		}
	}
	if fixture.CaseFold[2].Folded == fixture.CaseFold[3].Folded {
		t.Fatal("casefold fixture accidentally normalized composed/decomposed accents")
	}
	for _, item := range fixture.CaseFold {
		if got := foldPropertyText(item.Source); got != item.Folded {
			t.Fatalf("casefold %q=%q want=%q", item.Source, got, item.Folded)
		}
	}
}

func TestCrossLanguageFloatFixturePinsFullEnvelopePayloadHash(t *testing.T) {
	definition := testDefinition()
	definition.DefinitionJSON = strings.Replace(
		definition.DefinitionJSON,
		`{"allowed_aggregations":["count","count_distinct"]}`,
		`{"choices":[0.125,100000000000000000000,0,0.0000001]}`,
		1,
	)
	refreshDefinitionHashes(t, &definition)
	payload, err := BuildPayload(
		[]DefinitionRow{definition}, nil, 10, MaxChunkBytes, 1, testDigest("float-fixture-batch"),
	)
	if err != nil {
		t.Fatal(err)
	}
	input := testEnvelopeInput(t, 1, ZeroSHA256, 1)
	input.Payload = payload
	snapshot := mustEnvelope(t, input).Snapshot()
	const wantPayloadSHA = "032f5493b7d83b4f76a66320398ce858107de45072a0e797396e4505bc665d7c"
	const wantEnvelopeID = "0439bc82533c80d9625666a90dea3d4c2ece3e6caae34f540a4aa2a44b711ec4"
	if snapshot.PayloadSHA256 != wantPayloadSHA || snapshot.EnvelopeID != wantEnvelopeID {
		t.Fatalf("cross-language float envelope drift payload=%s envelope=%s", snapshot.PayloadSHA256, snapshot.EnvelopeID)
	}
}

func TestProjectionDefinitionBindingAndStateMatchPythonGoldens(t *testing.T) {
	row := testDefinition()
	if row.DefinitionSHA256 != "e28225e87d104b1d3eaf0fa3fcec6c665dd124e214f42261e650af939f935e21" {
		t.Fatalf("definition hash=%s", row.DefinitionSHA256)
	}
	if got := bindingIDForRow(row); got != row.BindingID || got != "02968171981845b617b75866346ee9475d13c63fe3ca3b2381fea7b78d31c89c" {
		t.Fatalf("binding hash=%s row=%s", got, row.BindingID)
	}
	state, err := stateSHA256ForRow(row)
	if err != nil {
		t.Fatal(err)
	}
	if state != row.StateSHA256 || state != "0ca7a16e2270cc85d633ba38275c63f1aa047242e2ec57a66da93d79fa37cc34" {
		t.Fatalf("state hash=%s row=%s", state, row.StateSHA256)
	}
	if got := framedSHA256("catalog.test", uint64(1), nil, false); got != "7dfeca3b6143ebdbf52b6dd6cd4d1addaca975b7fb2e4a3d8568df02ba0f5d32" {
		t.Fatalf("framed SHA primitive drift=%s", got)
	}
}

func TestDefinitionJSONExactShapeProjectionAndStateAreFailClosed(t *testing.T) {
	scope := Scope{
		OrganizationID: testOrganization, WorkspaceID: testWorkspace,
		CatalogEpoch: 3, CatalogRevision: 1, ProjectionVersion: 1,
		BuildToken:    testBuildToken,
		SourceAdapter: AdapterSpanAttribute, SourceVersion: 1,
		SourceFingerprint: testDigest("source-1"), ProducerStreamID: testStream,
		Sequence: 1,
	}
	tests := []struct {
		name    string
		mutate  func(*DefinitionRow)
		wantErr string
	}{
		{
			name: "missing internal key",
			mutate: func(row *DefinitionRow) {
				row.DefinitionJSON = strings.Replace(row.DefinitionJSON, `,"output_type":"string"`, "", 1)
				refreshDefinitionHashes(t, row)
			},
			wantErr: "exact 15-key",
		},
		{
			name: "row and definition disagree",
			mutate: func(row *DefinitionRow) {
				row.DefinitionJSON = strings.Replace(row.DefinitionJSON, `"name":"Customer.Plan"`, `"name":"Other"`, 1)
				refreshDefinitionHashes(t, row)
			},
			wantErr: "projected row fields",
		},
		{
			name: "unsupported details",
			mutate: func(row *DefinitionRow) {
				row.DefinitionJSON = strings.Replace(
					row.DefinitionJSON,
					`{"allowed_aggregations":["count","count_distinct"]}`,
					`{"unbounded":"value"}`,
					1,
				)
				refreshDefinitionHashes(t, row)
			},
			wantErr: "unsupported field",
		},
		{
			name: "binding identity drift",
			mutate: func(row *DefinitionRow) {
				row.VisibilityID = "55555555-5555-4555-8555-555555555555"
			},
			wantErr: "binding_id",
		},
		{
			name: "source entity state drift",
			mutate: func(row *DefinitionRow) {
				row.SourceEntityID = "different-source-entity"
			},
			wantErr: "state_sha256",
		},
		{
			name: "timestamp state drift",
			mutate: func(row *DefinitionRow) {
				changed := "2026-08-14 12:00:02.000000"
				row.LastSeen = &changed
			},
			wantErr: "state_sha256",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			row := testDefinition()
			test.mutate(&row)
			err := validateDefinition(row, scope)
			if err == nil || !strings.Contains(err.Error(), test.wantErr) {
				t.Fatalf("error=%v want %q", err, test.wantErr)
			}
		})
	}
}

func TestDefinitionAllowsZeroSourceAndCategoryRanks(t *testing.T) {
	scope := Scope{
		OrganizationID: testOrganization, WorkspaceID: testWorkspace,
		CatalogEpoch: 3, CatalogRevision: 1, ProjectionVersion: 1,
		BuildToken:    testBuildToken,
		SourceAdapter: AdapterSpanAttribute, SourceVersion: 1,
		SourceFingerprint: testDigest("source-1"), ProducerStreamID: testStream,
		Sequence: 1,
	}
	row := testDefinition()
	row.SourceRank = 0
	row.DefinitionJSON = strings.Replace(row.DefinitionJSON, `"source_rank":4`, `"source_rank":0`, 1)
	refreshDefinitionHashes(t, &row)
	if err := validateDefinition(row, scope); err != nil {
		t.Fatalf("zero source rank rejected: %v", err)
	}
	row = testDefinition()
	row.CategoryRank = 0
	row.DefinitionJSON = strings.Replace(row.DefinitionJSON, `"category_rank":20`, `"category_rank":0`, 1)
	refreshDefinitionHashes(t, &row)
	if err := validateDefinition(row, scope); err != nil {
		t.Fatalf("zero category rank rejected: %v", err)
	}
}

func TestDefinitionAllowsOnlyExactSortedObservedAttributeTypeUnion(t *testing.T) {
	scope := Scope{
		OrganizationID: testOrganization, WorkspaceID: testWorkspace,
		CatalogEpoch: 3, CatalogRevision: 1, BuildToken: testBuildToken, ProjectionVersion: 1,
		SourceAdapter: AdapterSpanAttribute, SourceVersion: 1,
		SourceFingerprint: testDigest("source-1"), ProducerStreamID: testStream, Sequence: 1,
	}
	row := testDefinition()
	row.DefinitionJSON = strings.Replace(
		row.DefinitionJSON,
		`{"allowed_aggregations":["count","count_distinct"]}`,
		`{"allowed_aggregations":["count","count_distinct"],"attribute_types":["boolean","string"],"attribute_types_exact":true}`,
		1,
	)
	refreshDefinitionHashes(t, &row)
	if err := validateDefinition(row, scope); err != nil {
		t.Fatalf("observed type union rejected: %v", err)
	}

	unsorted := row
	unsorted.DefinitionJSON = strings.Replace(unsorted.DefinitionJSON, `["boolean","string"]`, `["string","boolean"]`, 1)
	refreshDefinitionHashes(t, &unsorted)
	if err := validateDefinition(unsorted, scope); err == nil || !strings.Contains(err.Error(), "strictly sorted") {
		t.Fatalf("unsorted type union error=%v", err)
	}

	missingExact := row
	missingExact.DefinitionJSON = strings.Replace(missingExact.DefinitionJSON, `,"attribute_types_exact":true`, "", 1)
	refreshDefinitionHashes(t, &missingExact)
	if err := validateDefinition(missingExact, scope); err == nil || !strings.Contains(err.Error(), "present together") {
		t.Fatalf("unfenced type union error=%v", err)
	}
}
