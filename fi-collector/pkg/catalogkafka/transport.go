package catalogkafka

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"sync"
)

var (
	ErrSequenceGap        = errors.New("catalogkafka: sequence gap")
	ErrSequenceConflict   = errors.New("catalogkafka: conflicting envelope at sequence")
	ErrChainConflict      = errors.New("catalogkafka: previous payload chain mismatch")
	ErrSequenceRace       = errors.New("catalogkafka: sequence state changed during delivery")
	ErrCheckpointConflict = errors.New("catalogkafka: durable checkpoint conflicts with sequence state")
	ErrPoisonRecord       = errors.New("catalogkafka: poison Kafka record")
)

// KafkaKey returns the canonical project/epoch/stream partition key.
func KafkaKey(projectID string, epoch uint16, producerStreamID string) ([]byte, error) {
	if err := validateCanonicalUUID("project", projectID); err != nil {
		return nil, err
	}
	if epoch == 0 {
		return nil, errors.New("catalogkafka: Kafka key epoch must be non-zero")
	}
	if err := validateCanonicalUUID("producer stream", producerStreamID); err != nil {
		return nil, err
	}
	return []byte(projectID + "/" + strconv.FormatUint(uint64(epoch), 10) + "/" + producerStreamID), nil
}

// Record is the narrow transport-neutral Kafka record used by unit fakes and
// the franz-go adapter. native is retained only long enough for exact commits.
type Record struct {
	Topic       string
	Key         []byte
	Value       []byte
	Partition   int32
	Offset      int64
	LeaderEpoch int32
	native      any
}

// RecordWriter must return only after Kafka has acknowledged or rejected the
// record, and must return promptly when ctx is canceled. An asynchronous or
// cancellation-ignoring implementation violates this interface contract.
type RecordWriter interface {
	WriteRecord(context.Context, Record) error
	Close()
}

// ManualRecordSource polls one record and commits it synchronously. A source
// using group rebalances must hold a polled assignment until AllowRebalance.
type ManualRecordSource interface {
	PollOne(context.Context) (Record, error)
	Commit(context.Context, Record) error
	AllowRebalance()
	Close()
}

// Producer synchronously publishes immutable envelopes. It does not retry at
// the application layer; ambiguous replay is handled by envelope identity and
// an idempotent delivery handler.
type Producer struct {
	topic  string
	writer RecordWriter
}

// NewProducer wraps a synchronous record writer without activating it.
func NewProducer(topic string, writer RecordWriter) (*Producer, error) {
	if err := validateTopic(topic); err != nil {
		return nil, err
	}
	if writer == nil {
		return nil, errors.New("catalogkafka: producer requires a record writer")
	}
	return &Producer{topic: strings.Clone(topic), writer: writer}, nil
}

// Publish waits for the writer's broker acknowledgement and propagates every
// failure. The key and value are copied before crossing the interface.
func (p *Producer) Publish(ctx context.Context, envelope WireEnvelope) error {
	if p == nil || p.writer == nil {
		return errors.New("catalogkafka: nil producer")
	}
	if ctx == nil {
		return errors.New("catalogkafka: nil producer context")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	value, err := envelope.MarshalBinary()
	if err != nil {
		return err
	}
	if len(value) > MaxRecordBytes {
		return fmt.Errorf("catalogkafka: producer value uses %d bytes, limit %d", len(value), MaxRecordBytes)
	}
	key, err := KafkaKey(envelope.ProjectID(), envelope.CatalogEpoch(), envelope.ProducerStreamID())
	if err != nil {
		return err
	}
	record := Record{Topic: p.topic, Key: bytes.Clone(key), Value: bytes.Clone(value)}
	if err := p.writer.WriteRecord(ctx, record); err != nil {
		return fmt.Errorf("catalogkafka: synchronous produce: %w", err)
	}
	return nil
}

func (p *Producer) Close() {
	if p != nil && p.writer != nil {
		p.writer.Close()
	}
}

type SequenceStatus string

const (
	SequenceNext           SequenceStatus = "next"
	SequenceExactDuplicate SequenceStatus = "exact_duplicate"
)

// StreamCheckpoint seeds a validator from a durable delivery ledger.
type StreamCheckpoint struct {
	ProjectID        string
	CatalogEpoch     uint16
	ProducerStreamID string
	Sequence         uint64
	PayloadSHA256    string
	EnvelopeID       string
}

type streamKey struct {
	projectID string
	epoch     uint16
	streamID  string
}

type sequenceState struct {
	sequence      uint64
	payloadSHA256 string
	envelopeID    string
	generation    uint64
}

// SequenceValidation is an opaque check token. Acknowledge advances state only
// after a handler ACK; exact duplicates never advance it.
type SequenceValidation struct {
	Status     SequenceStatus
	validator  *SequenceValidator
	key        streamKey
	envelopeID string
	payload    string
	previous   string
	sequence   uint64
	generation uint64
}

// SequenceValidator detects gaps, broken chains, exact replay, and conflicting
// content at the same stream sequence. Its state can be seeded from durable
// delivery rows after a consumer restart.
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

// MergeCheckpoints atomically refreshes sequence state from the durable
// delivery ledger. It can only advance a stream, never regress it. Identical
// checkpoints are no-ops; a conflicting identity at the same sequence fails
// the whole merge without changing any stream.
func (v *SequenceValidator) MergeCheckpoints(seeds []StreamCheckpoint) error {
	if v == nil {
		return errors.New("catalogkafka: nil sequence validator")
	}
	validated := make(map[streamKey]StreamCheckpoint, len(seeds))
	for index, seed := range seeds {
		if err := validateCanonicalUUID("checkpoint project", seed.ProjectID); err != nil {
			return fmt.Errorf("seed %d: %w", index, err)
		}
		if seed.CatalogEpoch == 0 {
			return fmt.Errorf("catalogkafka: seed %d has zero epoch", index)
		}
		if err := validateCanonicalUUID("checkpoint producer stream", seed.ProducerStreamID); err != nil {
			return fmt.Errorf("seed %d: %w", index, err)
		}
		if seed.Sequence == 0 || !isLowerSHA256(seed.PayloadSHA256) || !isLowerSHA256(seed.EnvelopeID) {
			return fmt.Errorf("catalogkafka: seed %d has invalid sequence or digest", index)
		}
		key := streamKey{seed.ProjectID, seed.CatalogEpoch, seed.ProducerStreamID}
		if _, exists := validated[key]; exists {
			return fmt.Errorf("catalogkafka: duplicate checkpoint seed %d", index)
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
				envelopeID: seed.EnvelopeID, generation: 1,
			}
		case seed.Sequence < state.sequence:
			// A read replica can lag an in-process ACK. Never move backward.
			continue
		case seed.Sequence == state.sequence:
			if seed.PayloadSHA256 != state.payloadSHA256 || seed.EnvelopeID != state.envelopeID {
				return fmt.Errorf(
					"%w: project %s epoch %d stream %s sequence %d",
					ErrCheckpointConflict, key.projectID, key.epoch, key.streamID, seed.Sequence,
				)
			}
		case seed.Sequence > state.sequence:
			merged[key] = sequenceState{
				sequence: seed.Sequence, payloadSHA256: seed.PayloadSHA256,
				envelopeID: seed.EnvelopeID, generation: state.generation + 1,
			}
		}
	}
	v.states = merged
	return nil
}

// Check validates without advancing, so a handler failure leaves the expected
// sequence unchanged.
func (v *SequenceValidator) Check(envelope WireEnvelope) (SequenceValidation, error) {
	if v == nil {
		return SequenceValidation{}, errors.New("catalogkafka: nil sequence validator")
	}
	key := streamKey{envelope.ProjectID(), envelope.CatalogEpoch(), envelope.ProducerStreamID()}
	v.mu.Lock()
	defer v.mu.Unlock()
	state, exists := v.states[key]
	validation := SequenceValidation{
		validator: v, key: key, envelopeID: envelope.EnvelopeID(),
		payload: envelope.PayloadSHA256(), previous: envelope.PreviousPayloadSHA256(),
		sequence: envelope.Sequence(), generation: state.generation,
	}
	if !exists {
		if envelope.Sequence() != 1 {
			return SequenceValidation{}, fmt.Errorf("%w: first observed sequence is %d, require 1 or a seed", ErrSequenceGap, envelope.Sequence())
		}
		if envelope.PreviousPayloadSHA256() != ZeroSHA256 {
			return SequenceValidation{}, ErrChainConflict
		}
		validation.Status = SequenceNext
		return validation, nil
	}
	if envelope.Sequence() == state.sequence {
		if envelope.EnvelopeID() != state.envelopeID || envelope.PayloadSHA256() != state.payloadSHA256 {
			return SequenceValidation{}, fmt.Errorf("%w: sequence %d", ErrSequenceConflict, envelope.Sequence())
		}
		validation.Status = SequenceExactDuplicate
		return validation, nil
	}
	if state.sequence == ^uint64(0) || envelope.Sequence() != state.sequence+1 {
		return SequenceValidation{}, fmt.Errorf(
			"%w: got %d after %d", ErrSequenceGap, envelope.Sequence(), state.sequence,
		)
	}
	if envelope.PreviousPayloadSHA256() != state.payloadSHA256 {
		return SequenceValidation{}, fmt.Errorf("%w: sequence %d", ErrChainConflict, envelope.Sequence())
	}
	validation.Status = SequenceNext
	return validation, nil
}

// Acknowledge advances a successful Check. It is intentionally separate from
// Check so failed handlers cannot consume sequence state.
func (v *SequenceValidator) Acknowledge(validation SequenceValidation) error {
	if v == nil || validation.validator != v {
		return ErrSequenceRace
	}
	v.mu.Lock()
	defer v.mu.Unlock()
	state, exists := v.states[validation.key]
	if validation.Status == SequenceExactDuplicate {
		if !exists || state.generation != validation.generation ||
			state.sequence != validation.sequence || state.envelopeID != validation.envelopeID ||
			state.payloadSHA256 != validation.payload {
			return ErrSequenceRace
		}
		return nil
	}
	if validation.Status != SequenceNext || (exists && state.generation != validation.generation) ||
		(!exists && validation.generation != 0) {
		return ErrSequenceRace
	}
	if exists && (state.sequence == ^uint64(0) || validation.sequence != state.sequence+1 ||
		validation.previous != state.payloadSHA256) {
		return ErrSequenceRace
	}
	if !exists && (validation.sequence != 1 || validation.previous != ZeroSHA256) {
		return ErrSequenceRace
	}
	v.states[validation.key] = sequenceState{
		sequence: validation.sequence, payloadSHA256: validation.payload,
		envelopeID: validation.envelopeID, generation: validation.generation + 1,
	}
	return nil
}

// Checkpoint returns a copy of the current in-memory stream state.
func (v *SequenceValidator) Checkpoint(projectID string, epoch uint16, streamID string) (StreamCheckpoint, bool) {
	if v == nil {
		return StreamCheckpoint{}, false
	}
	v.mu.Lock()
	defer v.mu.Unlock()
	state, ok := v.states[streamKey{projectID, epoch, streamID}]
	if !ok {
		return StreamCheckpoint{}, false
	}
	return StreamCheckpoint{
		ProjectID: projectID, CatalogEpoch: epoch, ProducerStreamID: streamID,
		Sequence: state.sequence, PayloadSHA256: state.payloadSHA256, EnvelopeID: state.envelopeID,
	}, true
}

// Delivery is passed to a handler for durable catalog-table writes followed by
// its delivery-ledger write. Nil from Deliver is the only ACK recognized here.
type Delivery struct {
	Envelope       WireEnvelope
	Topic          string
	Partition      int32
	Offset         int64
	ExactDuplicate bool
}

type DeliveryHandler interface {
	Deliver(context.Context, Delivery) error
}

// Consumer processes exactly one polled record at a time. Any poison, handler
// failure, sequence conflict, or commit failure terminates Run without polling
// a later offset.
type Consumer struct {
	topic     string
	source    ManualRecordSource
	handler   DeliveryHandler
	validator *SequenceValidator
	processMu sync.Mutex
}

func NewConsumer(topic string, source ManualRecordSource, handler DeliveryHandler, validator *SequenceValidator) (*Consumer, error) {
	if err := validateTopic(topic); err != nil {
		return nil, err
	}
	if source == nil || handler == nil || validator == nil {
		return nil, errors.New("catalogkafka: consumer requires source, delivery handler, and sequence validator")
	}
	return &Consumer{topic: strings.Clone(topic), source: source, handler: handler, validator: validator}, nil
}

// ProcessOne performs poll -> validate -> durable handler ACK -> manual commit.
func (c *Consumer) ProcessOne(ctx context.Context) error {
	if c == nil || c.source == nil || c.handler == nil || c.validator == nil {
		return errors.New("catalogkafka: nil consumer")
	}
	if ctx == nil {
		return errors.New("catalogkafka: nil consumer context")
	}
	c.processMu.Lock()
	defer c.processMu.Unlock()
	record, err := c.source.PollOne(ctx)
	defer c.source.AllowRebalance()
	if err != nil {
		return fmt.Errorf("catalogkafka: poll: %w", err)
	}
	if err := validateRecord(c.topic, record); err != nil {
		return fmt.Errorf("%w: %v", ErrPoisonRecord, err)
	}
	envelope, err := ParseWireEnvelope(record.Value)
	if err != nil {
		return fmt.Errorf("%w: %v", ErrPoisonRecord, err)
	}
	wantKey, err := KafkaKey(envelope.ProjectID(), envelope.CatalogEpoch(), envelope.ProducerStreamID())
	if err != nil || !bytes.Equal(record.Key, wantKey) {
		return fmt.Errorf("%w: record key does not match envelope stream", ErrPoisonRecord)
	}
	validation, err := c.validator.Check(envelope)
	if err != nil {
		return fmt.Errorf("%w: %v", ErrPoisonRecord, err)
	}
	delivery := Delivery{
		Envelope: envelope, Topic: record.Topic, Partition: record.Partition,
		Offset: record.Offset, ExactDuplicate: validation.Status == SequenceExactDuplicate,
	}
	if err := c.handler.Deliver(ctx, delivery); err != nil {
		return fmt.Errorf("catalogkafka: delivery handler did not ACK: %w", err)
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if err := c.validator.Acknowledge(validation); err != nil {
		return err
	}
	if err := c.source.Commit(ctx, record); err != nil {
		return fmt.Errorf("catalogkafka: commit delivered offset: %w", err)
	}
	return nil
}

func (c *Consumer) Run(ctx context.Context) error {
	if ctx == nil {
		return errors.New("catalogkafka: nil consumer context")
	}
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		if err := c.ProcessOne(ctx); err != nil {
			return err
		}
	}
}

func (c *Consumer) Close() {
	if c != nil && c.source != nil {
		c.source.Close()
	}
}

func validateRecord(topic string, record Record) error {
	if record.Topic != topic {
		return fmt.Errorf("record topic %q does not match %q", record.Topic, topic)
	}
	if record.Partition < 0 || record.Offset < 0 {
		return errors.New("record partition/offset must be non-negative")
	}
	if len(record.Value) == 0 || len(record.Value) > MaxRecordBytes {
		return fmt.Errorf("record value size %d is outside (0,%d]", len(record.Value), MaxRecordBytes)
	}
	return nil
}

func validateTopic(topic string) error {
	if topic == "" || len(topic) > 249 || strings.TrimSpace(topic) != topic || topic == "." || topic == ".." {
		return errors.New("catalogkafka: topic must be a non-empty Kafka name of at most 249 bytes")
	}
	for index := 0; index < len(topic); index++ {
		char := topic[index]
		if (char < 'a' || char > 'z') && (char < 'A' || char > 'Z') &&
			(char < '0' || char > '9') && char != '.' && char != '_' && char != '-' {
			return errors.New("catalogkafka: topic contains an invalid character")
		}
	}
	return nil
}
