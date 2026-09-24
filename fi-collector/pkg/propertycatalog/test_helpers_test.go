package propertycatalog

import (
	"crypto/sha256"
	"encoding/hex"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/attributecatalog"
)

const (
	testOrganization = "11111111-1111-4111-8111-111111111111"
	testWorkspace    = "22222222-2222-4222-8222-222222222222"
	testProject      = "33333333-3333-4333-8333-333333333333"
	testProjectTwo   = "66666666-6666-4666-8666-666666666666"
	testStream       = "44444444-4444-4444-8444-444444444444"
	testBuildToken   = "55555555-5555-4555-8555-555555555555"
	testSeen         = "2026-08-14 12:00:00.000000"
	testLastSeen     = "2026-08-14 12:00:01.000000"
	testEmitted      = "2026-08-14 12:00:01.000000"
)

func testSpanWindow() (uint64, uint64) {
	since, _ := time.Parse(dateTime64Layout, testSeen)
	return uint64(since.UnixMicro()), uint64(since.Add(time.Hour).UnixMicro())
}

func testDigest(value string) string {
	digest := sha256.Sum256([]byte(value))
	return hex.EncodeToString(digest[:])
}

func testDefinition() DefinitionRow {
	definitionJSON := `{"category":"custom_attribute","category_rank":20,"definition_source":"span_attribute","details":{"allowed_aggregations":["count","count_distinct"]},"display_name":"Customer plan","name":"Customer.Plan","output_type":"string","primary_source":"traces","property_id":"custom_attribute:customer.plan","property_kind":"custom_attribute","role":"dimension","source_rank":4,"source_tokens":["customer","span"],"value_adapter":"span_attribute_value","value_type":"string"}`
	first, last := testSeen, testLastSeen
	return DefinitionRow{
		OrganizationID: testOrganization, WorkspaceID: testWorkspace,
		CatalogEpoch: 3, CatalogRevision: 1, BuildToken: testBuildToken, ProjectionVersion: 1,
		BindingID:       "02968171981845b617b75866346ee9475d13c63fe3ca3b2381fea7b78d31c89c",
		VisibilityScope: VisibilityProject,
		VisibilityID:    testProject, SourceAdapter: AdapterSpanAttribute,
		SourceEntityID: "customer.plan", SourceVersion: 1,
		SourceFingerprint: testDigest("source-1"), ProducerStreamID: testStream,
		ProducerSequence: 1, PropertyID: "custom_attribute:customer.plan",
		PropertyKind: KindCustomAttribute, Category: "custom_attribute",
		CategoryRank: 20, SourceRank: 4, DefinitionSource: "span_attribute",
		PrimarySource: "traces", PrimarySourceFolded: "traces",
		SourceTokens: []string{"customer", "span"}, ValueAdapter: "span_attribute_value",
		Name: "Customer.Plan", DisplayName: "Customer plan", SortNameFolded: "customer.plan",
		SearchTextFolded: "customer.plan customer plan traces span_attribute customer span",
		Role:             "dimension",
		DefinitionJSON:   definitionJSON,
		DefinitionSHA256: "e28225e87d104b1d3eaf0fa3fcec6c665dd124e214f42261e650af939f935e21",
		FirstSeen:        &first,
		LastSeen:         &last,
		StateSHA256:      "0ca7a16e2270cc85d633ba38275c63f1aa047242e2ec57a66da93d79fa37cc34",
		EmittedAt:        testEmitted,
	}
}

func testValue() AttributeValueRow {
	encoded, _ := attributecatalog.EncodeScalar("pro")
	return AttributeValueRow{
		OrganizationID: testOrganization, WorkspaceID: testWorkspace, ProjectID: testProject,
		CatalogEpoch: 3, CatalogRevision: 1, BuildToken: testBuildToken,
		SourceKind: KindCustomAttribute, AttributeKey: "customer.plan",
		AttributeType: "string", ValueFingerprint: encoded.Fingerprint, ValueJSON: encoded.ValueJSON,
		ValueSearchTextFolded: "pro", FirstSeen: testSeen, LastSeen: testLastSeen,
	}
}

func testEnvelopeInput(t *testing.T, sequence uint64, previous string, sourceRows uint64) EnvelopeInput {
	t.Helper()
	definition := testDefinition()
	definition.ProducerSequence = sequence
	payload, err := BuildPayload(
		[]DefinitionRow{definition}, []AttributeValueRow{testValue()}, 1, MaxChunkBytes,
		sourceRows, testDigest("batch"),
	)
	if err != nil {
		t.Fatal(err)
	}
	return EnvelopeInput{
		OrganizationID: testOrganization, WorkspaceID: testWorkspace,
		CatalogEpoch: 3, CatalogRevision: 1, ProjectionVersion: 1,
		BuildToken:    testBuildToken,
		SourceAdapter: AdapterSpanAttribute, SourceVersion: 1,
		SourceFingerprint: testDigest("source-1"), ProducerStreamID: testStream,
		Sequence: sequence, PreviousPayloadSHA256: previous, Payload: payload,
	}
}

func mustEnvelope(t *testing.T, input EnvelopeInput) WireEnvelope {
	t.Helper()
	envelope, err := NewWireEnvelope(input)
	if err != nil {
		t.Fatal(err)
	}
	return envelope
}

func refreshDefinitionHashes(t *testing.T, row *DefinitionRow) {
	t.Helper()
	row.DefinitionSHA256 = testDigest(row.DefinitionJSON)
	row.BindingID = bindingIDForRow(*row)
	state, err := stateSHA256ForRow(*row)
	if err != nil {
		t.Fatal(err)
	}
	row.StateSHA256 = state
}
