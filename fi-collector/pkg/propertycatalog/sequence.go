package propertycatalog

import (
	"errors"
	"fmt"
	"sync"
)

var (
	ErrSequenceGap        = errors.New("propertycatalog: sequence gap")
	ErrSequenceConflict   = errors.New("propertycatalog: conflicting envelope at sequence")
	ErrChainConflict      = errors.New("propertycatalog: previous payload chain mismatch")
	ErrSequenceRace       = errors.New("propertycatalog: sequence state changed during delivery")
	ErrCheckpointConflict = errors.New("propertycatalog: checkpoint conflicts with sequence state")
	ErrPoisonRecord       = errors.New("propertycatalog: poison Kafka record")
)

type SequenceStatus string

const (
	SequenceNext           SequenceStatus = "next"
	SequenceExactDuplicate SequenceStatus = "exact_duplicate"
)

type StreamCheckpoint struct {
	OrganizationID    string
	WorkspaceID       string
	CatalogEpoch      uint16
	CatalogRevision   uint64
	BuildToken        string
	ProjectionVersion uint16
	SourceAdapter     SourceAdapter
	ProducerStreamID  string
	Sequence          uint64
	Terminal          bool
	GapSeen           bool
	PayloadSHA256     string
	EnvelopeID        string
	// ProducerEvidence is populated only by the hot producer's fsynced ACK
	// store.  ClickHouse sequence seeds intentionally leave it at zero values.
	// Keeping the aggregate beside the acknowledged tail makes the drain proof
	// survive spool removal and process restart without trusting in-memory
	// counters.
	SourceRows     uint64
	DefinitionRows uint64
	ValueRows      uint64
	TombstoneRows  uint64
	DeliveryCount  uint64
	SourceDigest   string
	EmittedDigest  string
	// LastSourceBatchDigest is local producer evidence used to make the
	// candidate receipt -> ordered spool handoff idempotent across a crash. It is
	// intentionally absent from ClickHouse-derived consumer checkpoints.
	LastSourceBatchDigest string `json:",omitempty"`
}

type streamKey struct {
	organizationID string
	workspaceID    string
	epoch          uint16
	revision       uint64
	buildToken     string
	adapter        SourceAdapter
	streamID       string
}

type sequenceState struct {
	sequence      uint64
	payloadSHA256 string
	envelopeID    string
	projection    uint16
	terminal      bool
	generation    uint64
}

type SequenceValidation struct {
	Status     SequenceStatus
	validator  *SequenceValidator
	key        streamKey
	envelopeID string
	payload    string
	previous   string
	projection uint16
	sequence   uint64
	terminal   bool
	generation uint64
}

type SequenceValidator struct {
	mu     sync.Mutex
	states map[streamKey]sequenceState
}

func NewSequenceValidator(seeds []StreamCheckpoint) (*SequenceValidator, error) {
	validator := &SequenceValidator{states: make(map[streamKey]sequenceState, len(seeds))}
	if err := validator.MergeCheckpoints(seeds); err != nil {
		return nil, err
	}
	return validator, nil
}

// MergeCheckpoints is atomic and monotonic. A lagging checkpoint cannot move
// an in-process stream backward, while same-sequence identity drift is poison.
func (v *SequenceValidator) MergeCheckpoints(seeds []StreamCheckpoint) error {
	if v == nil {
		return errors.New("propertycatalog: nil sequence validator")
	}
	validated := make(map[streamKey]StreamCheckpoint, len(seeds))
	for index, seed := range seeds {
		if err := validateCheckpoint(seed); err != nil {
			return fmt.Errorf("propertycatalog: checkpoint %d: %w", index, err)
		}
		key := checkpointKey(seed)
		if _, exists := validated[key]; exists {
			return fmt.Errorf("propertycatalog: duplicate checkpoint %d", index)
		}
		validated[key] = seed
	}
	v.mu.Lock()
	defer v.mu.Unlock()
	merged := make(map[streamKey]sequenceState, len(v.states)+len(validated))
	for key, state := range v.states {
		merged[key] = state
	}
	for key, seed := range validated {
		state, exists := merged[key]
		switch {
		case !exists:
			merged[key] = sequenceState{
				sequence: seed.Sequence, payloadSHA256: seed.PayloadSHA256,
				envelopeID: seed.EnvelopeID, projection: seed.ProjectionVersion,
				terminal: seed.Terminal, generation: 1,
			}
		case seed.ProjectionVersion != state.projection:
			return fmt.Errorf("%w: stream %s changes projection version", ErrCheckpointConflict, key.streamID)
		case seed.Sequence < state.sequence:
			continue
		case seed.Sequence == state.sequence:
			if seed.PayloadSHA256 != state.payloadSHA256 || seed.EnvelopeID != state.envelopeID ||
				seed.Terminal != state.terminal {
				return fmt.Errorf("%w: stream %s sequence %d", ErrCheckpointConflict, key.streamID, seed.Sequence)
			}
		case seed.Sequence > state.sequence:
			if state.terminal {
				return fmt.Errorf("%w: terminal stream %s cannot advance", ErrCheckpointConflict, key.streamID)
			}
			merged[key] = sequenceState{
				sequence: seed.Sequence, payloadSHA256: seed.PayloadSHA256,
				envelopeID: seed.EnvelopeID, projection: seed.ProjectionVersion,
				terminal:   seed.Terminal,
				generation: state.generation + 1,
			}
		}
	}
	v.states = merged
	return nil
}

func (v *SequenceValidator) Check(envelope WireEnvelope) (SequenceValidation, error) {
	if v == nil {
		return SequenceValidation{}, errors.New("propertycatalog: nil sequence validator")
	}
	snapshot := envelope.Snapshot()
	if snapshot.EnvelopeID == "" || snapshot.Format != EnvelopeFormat || snapshot.Version != EnvelopeVersion {
		return SequenceValidation{}, errors.New("propertycatalog: invalid envelope")
	}
	key := streamKey{
		organizationID: snapshot.OrganizationID, workspaceID: snapshot.WorkspaceID,
		epoch: snapshot.CatalogEpoch, revision: snapshot.CatalogRevision,
		buildToken: snapshot.BuildToken,
		adapter:    snapshot.SourceAdapter, streamID: snapshot.ProducerStreamID,
	}
	v.mu.Lock()
	defer v.mu.Unlock()
	state, exists := v.states[key]
	validation := SequenceValidation{
		validator: v, key: key, envelopeID: snapshot.EnvelopeID,
		payload: snapshot.PayloadSHA256, previous: snapshot.PreviousPayloadSHA256,
		projection: snapshot.ProjectionVersion, sequence: snapshot.Sequence,
		terminal:   snapshot.Terminal,
		generation: state.generation,
	}
	if !exists {
		if snapshot.Sequence != 1 {
			return SequenceValidation{}, fmt.Errorf("%w: first sequence %d requires durable seed", ErrSequenceGap, snapshot.Sequence)
		}
		if snapshot.PreviousPayloadSHA256 != ZeroSHA256 {
			return SequenceValidation{}, ErrChainConflict
		}
		validation.Status = SequenceNext
		return validation, nil
	}
	if snapshot.ProjectionVersion != state.projection {
		return SequenceValidation{}, fmt.Errorf("%w: stream %s changes projection version", ErrSequenceConflict, key.streamID)
	}
	if snapshot.Sequence == state.sequence {
		if snapshot.PayloadSHA256 == state.payloadSHA256 && snapshot.EnvelopeID == state.envelopeID &&
			snapshot.Terminal == state.terminal {
			validation.Status = SequenceExactDuplicate
			return validation, nil
		}
		return SequenceValidation{}, fmt.Errorf("%w: stream %s sequence %d", ErrSequenceConflict, key.streamID, snapshot.Sequence)
	}
	if state.terminal {
		return SequenceValidation{}, fmt.Errorf("%w: stream %s is already terminal", ErrSequenceConflict, key.streamID)
	}
	if snapshot.Sequence < state.sequence {
		return SequenceValidation{}, fmt.Errorf("%w: stale sequence %d behind %d", ErrSequenceConflict, snapshot.Sequence, state.sequence)
	}
	if snapshot.Sequence != state.sequence+1 {
		return SequenceValidation{}, fmt.Errorf("%w: got %d require %d", ErrSequenceGap, snapshot.Sequence, state.sequence+1)
	}
	if snapshot.PreviousPayloadSHA256 != state.payloadSHA256 {
		return SequenceValidation{}, ErrChainConflict
	}
	validation.Status = SequenceNext
	return validation, nil
}

// Acknowledge advances only after the durable handler succeeds. Exact replay
// is already acknowledged and therefore remains a no-op.
func (validation SequenceValidation) Acknowledge() error {
	if validation.validator == nil || validation.Status == "" {
		return errors.New("propertycatalog: invalid sequence validation token")
	}
	v := validation.validator
	v.mu.Lock()
	defer v.mu.Unlock()
	state, exists := v.states[validation.key]
	if validation.Status == SequenceExactDuplicate {
		if !exists || state.generation != validation.generation ||
			state.sequence != validation.sequence || state.payloadSHA256 != validation.payload ||
			state.envelopeID != validation.envelopeID || state.projection != validation.projection ||
			state.terminal != validation.terminal {
			return ErrSequenceRace
		}
		return nil
	}
	if exists {
		if state.generation != validation.generation || state.sequence+1 != validation.sequence ||
			state.payloadSHA256 != validation.previous || state.projection != validation.projection {
			return ErrSequenceRace
		}
	} else if validation.generation != 0 || validation.sequence != 1 || validation.previous != ZeroSHA256 {
		return ErrSequenceRace
	}
	v.states[validation.key] = sequenceState{
		sequence: validation.sequence, payloadSHA256: validation.payload,
		envelopeID: validation.envelopeID, projection: validation.projection,
		terminal:   validation.terminal,
		generation: validation.generation + 1,
	}
	return nil
}

func validateCheckpoint(seed StreamCheckpoint) error {
	if err := validateCanonicalUUID("checkpoint organization", seed.OrganizationID); err != nil {
		return err
	}
	if err := validateCanonicalUUID("checkpoint workspace", seed.WorkspaceID); err != nil {
		return err
	}
	if seed.CatalogEpoch == 0 || seed.CatalogRevision == 0 || seed.ProjectionVersion == 0 ||
		!validSourceAdapter(seed.SourceAdapter) {
		return errors.New("checkpoint has invalid epoch/revision/projection/adapter")
	}
	if err := validateCanonicalUUID("checkpoint build token", seed.BuildToken); err != nil {
		return err
	}
	if err := validateCanonicalUUID("checkpoint producer stream", seed.ProducerStreamID); err != nil {
		return err
	}
	if seed.Sequence == 0 || !isLowerSHA256(seed.PayloadSHA256) || !isLowerSHA256(seed.EnvelopeID) {
		return errors.New("checkpoint has invalid sequence or digest")
	}
	return nil
}

func checkpointKey(seed StreamCheckpoint) streamKey {
	return streamKey{
		organizationID: seed.OrganizationID, workspaceID: seed.WorkspaceID,
		epoch: seed.CatalogEpoch, revision: seed.CatalogRevision,
		buildToken: seed.BuildToken,
		adapter:    seed.SourceAdapter, streamID: seed.ProducerStreamID,
	}
}
