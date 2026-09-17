package propertycatalog

import (
	"errors"
	"testing"
)

func TestSequenceValidatorAcceptsNextAndExactDuplicate(t *testing.T) {
	validator, err := NewSequenceValidator(nil)
	if err != nil {
		t.Fatal(err)
	}
	first := mustEnvelope(t, testEnvelopeInput(t, 1, ZeroSHA256, 1))
	validation, err := validator.Check(first)
	if err != nil || validation.Status != SequenceNext {
		t.Fatalf("first validation=%+v err=%v", validation, err)
	}
	if err := validation.Acknowledge(); err != nil {
		t.Fatal(err)
	}
	duplicate, err := validator.Check(first)
	if err != nil || duplicate.Status != SequenceExactDuplicate {
		t.Fatalf("duplicate validation=%+v err=%v", duplicate, err)
	}
	if err := duplicate.Acknowledge(); err != nil {
		t.Fatal(err)
	}
	second := mustEnvelope(t, testEnvelopeInput(t, 2, first.PayloadSHA256(), 2))
	next, err := validator.Check(second)
	if err != nil || next.Status != SequenceNext {
		t.Fatalf("second validation=%+v err=%v", next, err)
	}
}

func TestSequenceValidatorRejectsGapConflictBrokenChainAndRace(t *testing.T) {
	validator, _ := NewSequenceValidator(nil)
	first := mustEnvelope(t, testEnvelopeInput(t, 1, ZeroSHA256, 1))
	pending, err := validator.Check(first)
	if err != nil {
		t.Fatal(err)
	}
	conflict := mustEnvelope(t, testEnvelopeInput(t, 1, ZeroSHA256, 2))
	// Before ACK either valid sequence-one candidate can be checked. Once one is
	// durably ACKed, the other becomes same-sequence poison.
	if err := pending.Acknowledge(); err != nil {
		t.Fatal(err)
	}
	if _, err := validator.Check(conflict); !errors.Is(err, ErrSequenceConflict) {
		t.Fatalf("same-sequence conflict=%v", err)
	}
	gap := mustEnvelope(t, testEnvelopeInput(t, 3, first.PayloadSHA256(), 1))
	if _, err := validator.Check(gap); !errors.Is(err, ErrSequenceGap) {
		t.Fatalf("gap error=%v", err)
	}
	broken := mustEnvelope(t, testEnvelopeInput(t, 2, testDigest("wrong-chain"), 1))
	if _, err := validator.Check(broken); !errors.Is(err, ErrChainConflict) {
		t.Fatalf("broken-chain error=%v", err)
	}

	second := mustEnvelope(t, testEnvelopeInput(t, 2, first.PayloadSHA256(), 1))
	left, _ := validator.Check(second)
	right, _ := validator.Check(second)
	if err := left.Acknowledge(); err != nil {
		t.Fatal(err)
	}
	if err := right.Acknowledge(); !errors.Is(err, ErrSequenceRace) {
		t.Fatalf("stale ACK token error=%v", err)
	}
}

func TestSequenceCheckpointMergeIsAtomicAndConflictDetecting(t *testing.T) {
	first := mustEnvelope(t, testEnvelopeInput(t, 1, ZeroSHA256, 1))
	seed := StreamCheckpoint{
		OrganizationID: testOrganization, WorkspaceID: testWorkspace,
		CatalogEpoch: 3, CatalogRevision: 1, ProjectionVersion: 1,
		BuildToken:       testBuildToken,
		SourceAdapter:    AdapterSpanAttribute,
		ProducerStreamID: testStream, Sequence: 1,
		PayloadSHA256: first.PayloadSHA256(), EnvelopeID: first.EnvelopeID(),
	}
	validator, err := NewSequenceValidator([]StreamCheckpoint{seed})
	if err != nil {
		t.Fatal(err)
	}
	conflict := seed
	conflict.PayloadSHA256 = testDigest("different")
	if err := validator.MergeCheckpoints([]StreamCheckpoint{conflict}); !errors.Is(err, ErrCheckpointConflict) {
		t.Fatalf("checkpoint conflict=%v", err)
	}
	duplicate, err := validator.Check(first)
	if err != nil || duplicate.Status != SequenceExactDuplicate {
		t.Fatalf("conflicting merge mutated state: %+v %v", duplicate, err)
	}
}

func TestSequenceStreamRejectsProjectionVersionDrift(t *testing.T) {
	validator, _ := NewSequenceValidator(nil)
	first := mustEnvelope(t, testEnvelopeInput(t, 1, ZeroSHA256, 1))
	validation, _ := validator.Check(first)
	if err := validation.Acknowledge(); err != nil {
		t.Fatal(err)
	}
	definition := testDefinition()
	definition.ProjectionVersion = 2
	definition.ProducerSequence = 2
	payload, err := BuildPayload(
		[]DefinitionRow{definition}, []AttributeValueRow{testValue()}, 1, MaxChunkBytes,
		1, testDigest("projection-two"),
	)
	if err != nil {
		t.Fatal(err)
	}
	input := testEnvelopeInput(t, 2, first.PayloadSHA256(), 1)
	input.ProjectionVersion = 2
	input.Payload = payload
	changed := mustEnvelope(t, input)
	if _, err := validator.Check(changed); !errors.Is(err, ErrSequenceConflict) {
		t.Fatalf("projection drift error=%v", err)
	}
}
