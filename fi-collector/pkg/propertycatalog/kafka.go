package propertycatalog

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"sync"
	"unicode"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogkafka"
)

// KafkaKey binds every partitioned record to its full tenant/revision/source
// stream. It cannot collide with the legacy project/epoch v3 key shape.
func KafkaKey(snapshot EnvelopeSnapshot) ([]byte, error) {
	scope := Scope{
		OrganizationID: snapshot.OrganizationID, WorkspaceID: snapshot.WorkspaceID,
		CatalogEpoch: snapshot.CatalogEpoch, CatalogRevision: snapshot.CatalogRevision,
		BuildToken:        snapshot.BuildToken,
		ProjectionVersion: snapshot.ProjectionVersion, SourceAdapter: snapshot.SourceAdapter,
		SourceVersion: snapshot.SourceVersion, SourceFingerprint: snapshot.SourceFingerprint,
		ProducerStreamID: snapshot.ProducerStreamID, Sequence: snapshot.Sequence,
	}
	if err := validateScope(scope); err != nil {
		return nil, err
	}
	return []byte(strings.Join([]string{
		snapshot.OrganizationID, snapshot.WorkspaceID,
		strconv.FormatUint(uint64(snapshot.CatalogEpoch), 10),
		strconv.FormatUint(snapshot.CatalogRevision, 10),
		snapshot.BuildToken,
		string(snapshot.SourceAdapter),
		snapshot.ProducerStreamID,
	}, "/")), nil
}

type Producer struct {
	topic  string
	writer catalogkafka.RecordWriter
}

func NewProducer(topic string, writer catalogkafka.RecordWriter) (*Producer, error) {
	if err := validateTopic(topic); err != nil {
		return nil, err
	}
	if writer == nil {
		return nil, errors.New("propertycatalog: producer requires a record writer")
	}
	return &Producer{topic: strings.Clone(topic), writer: writer}, nil
}

func (p *Producer) Publish(ctx context.Context, envelope WireEnvelope) error {
	if p == nil || p.writer == nil {
		return errors.New("propertycatalog: nil producer")
	}
	if ctx == nil {
		return errors.New("propertycatalog: nil producer context")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	value, err := envelope.MarshalBinary()
	if err != nil {
		return err
	}
	key, err := KafkaKey(envelope.Snapshot())
	if err != nil {
		return err
	}
	if err := p.writer.WriteRecord(ctx, catalogkafka.Record{
		Topic: p.topic, Key: bytes.Clone(key), Value: bytes.Clone(value),
	}); err != nil {
		return fmt.Errorf("propertycatalog: synchronous produce: %w", err)
	}
	return nil
}

func (p *Producer) Close() {
	if p != nil && p.writer != nil {
		p.writer.Close()
	}
}

// FenceOwner establishes the broker-side transactional producer epoch before
// the sequencer starts consuming candidates. A previous process using the same
// fixed transactional identity can no longer publish ordered envelopes.
func (p *Producer) FenceOwner(ctx context.Context) error {
	if p == nil || p.writer == nil || ctx == nil {
		return errors.New("propertycatalog: owner fencing requires a producer and context")
	}
	fencer, ok := p.writer.(interface{ FenceOwner(context.Context) error })
	if !ok {
		return errors.New("propertycatalog: ordered producer does not implement owner fencing")
	}
	return fencer.FenceOwner(ctx)
}

type Handler interface {
	Deliver(context.Context, Delivery) error
}

type Consumer struct {
	topic     string
	source    catalogkafka.ManualRecordSource
	handler   Handler
	validator *SequenceValidator
	processMu sync.Mutex
}

func NewConsumer(
	topic string,
	source catalogkafka.ManualRecordSource,
	handler Handler,
	validator *SequenceValidator,
) (*Consumer, error) {
	if err := validateTopic(topic); err != nil {
		return nil, err
	}
	if source == nil || handler == nil || validator == nil {
		return nil, errors.New("propertycatalog: consumer requires source, handler, and validator")
	}
	return &Consumer{topic: strings.Clone(topic), source: source, handler: handler, validator: validator}, nil
}

// ProcessOne commits only after strict parse, sequence validation, complete
// delivery, and sequence acknowledgement. Poison/gap/conflict leaves the
// record uncommitted for explicit operator handling.
func (c *Consumer) ProcessOne(ctx context.Context) error {
	if c == nil || c.source == nil || c.handler == nil || c.validator == nil {
		return errors.New("propertycatalog: nil consumer")
	}
	if ctx == nil {
		return errors.New("propertycatalog: nil consumer context")
	}
	c.processMu.Lock()
	defer c.processMu.Unlock()
	record, err := c.source.PollOne(ctx)
	defer c.source.AllowRebalance()
	if err != nil {
		return err
	}
	if record.Topic != c.topic || record.Partition < 0 || record.Offset < 0 {
		return fmt.Errorf("%w: invalid topic/partition/offset", ErrPoisonRecord)
	}
	envelope, err := ParseWireEnvelope(record.Value)
	if err != nil {
		return fmt.Errorf("%w: %v", ErrPoisonRecord, err)
	}
	wantKey, err := KafkaKey(envelope.Snapshot())
	if err != nil || !bytes.Equal(record.Key, wantKey) {
		return fmt.Errorf("%w: record key does not match envelope", ErrPoisonRecord)
	}
	validation, err := c.validator.Check(envelope)
	if err != nil {
		return err
	}
	if err := c.handler.Deliver(ctx, Delivery{
		Envelope: envelope, ExactDuplicate: validation.Status == SequenceExactDuplicate,
		Transport: TransportKafka, KafkaPartition: record.Partition, KafkaOffset: record.Offset,
	}); err != nil {
		return err
	}
	if err := validation.Acknowledge(); err != nil {
		return err
	}
	if err := c.source.Commit(ctx, record); err != nil {
		return fmt.Errorf("propertycatalog: commit Kafka record: %w", err)
	}
	return nil
}

func (c *Consumer) Close() {
	if c != nil && c.source != nil {
		c.source.Close()
	}
}

func (c *Consumer) Run(ctx context.Context) error {
	if c == nil || ctx == nil {
		return errors.New("propertycatalog: consumer run requires a context")
	}
	for {
		if err := c.ProcessOne(ctx); err != nil {
			return err
		}
	}
}

func validateTopic(topic string) error {
	if topic == "" || len(topic) > MaxKafkaTopicBytes || topic == "." || topic == ".." {
		return errors.New("propertycatalog: Kafka topic is empty, reserved, or too long")
	}
	for _, r := range topic {
		if r > unicode.MaxASCII || !(unicode.IsLetter(r) || unicode.IsDigit(r) || r == '.' || r == '_' || r == '-') {
			return errors.New("propertycatalog: Kafka topic contains unsupported characters")
		}
	}
	return nil
}
