package propertycatalog

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogkafka"
)

type recordingRecordWriter struct {
	records []catalogkafka.Record
	err     error
	closed  bool
}

func (w *recordingRecordWriter) WriteRecord(_ context.Context, record catalogkafka.Record) error {
	w.records = append(w.records, record)
	return w.err
}
func (w *recordingRecordWriter) Close() { w.closed = true }

type oneRecordSource struct {
	record     catalogkafka.Record
	pollErr    error
	commitErr  error
	commits    int
	rebalances int
	closed     bool
}

func (s *oneRecordSource) PollOne(context.Context) (catalogkafka.Record, error) {
	return s.record, s.pollErr
}
func (s *oneRecordSource) Commit(context.Context, catalogkafka.Record) error {
	s.commits++
	return s.commitErr
}
func (s *oneRecordSource) AllowRebalance() { s.rebalances++ }
func (s *oneRecordSource) Close()          { s.closed = true }

type recordingHandler struct {
	deliveries []Delivery
	err        error
}

func (h *recordingHandler) Deliver(_ context.Context, delivery Delivery) error {
	h.deliveries = append(h.deliveries, delivery)
	return h.err
}

func kafkaRecord(t *testing.T, envelope WireEnvelope, offset int64) catalogkafka.Record {
	t.Helper()
	value, _ := envelope.MarshalBinary()
	key, err := KafkaKey(envelope.Snapshot())
	if err != nil {
		t.Fatal(err)
	}
	return catalogkafka.Record{
		Topic: "property-catalog", Key: key, Value: value, Partition: 2, Offset: offset,
	}
}

func TestProducerUsesTenantRevisionAdapterStreamKeyAndSynchronousWriter(t *testing.T) {
	envelope := mustEnvelope(t, testEnvelopeInput(t, 1, ZeroSHA256, 1))
	writer := &recordingRecordWriter{}
	producer, err := NewProducer("property-catalog", writer)
	if err != nil {
		t.Fatal(err)
	}
	if err := producer.Publish(context.Background(), envelope); err != nil {
		t.Fatal(err)
	}
	if len(writer.records) != 1 {
		t.Fatalf("records=%d", len(writer.records))
	}
	wantKey := fmt.Sprintf(
		"%s/%s/3/1/%s/span_attribute/%s",
		testOrganization, testWorkspace, testBuildToken, testStream,
	)
	if string(writer.records[0].Key) != wantKey {
		t.Fatalf("key=%q want=%q", writer.records[0].Key, wantKey)
	}
	raw, _ := envelope.MarshalBinary()
	if !bytes.Equal(writer.records[0].Value, raw) {
		t.Fatal("producer changed envelope bytes")
	}
	producer.Close()
	if !writer.closed {
		t.Fatal("producer did not close writer")
	}
}

func TestConsumerCommitsOnlyAfterDeliveryAndMarksExactDuplicate(t *testing.T) {
	envelope := mustEnvelope(t, testEnvelopeInput(t, 1, ZeroSHA256, 1))
	source := &oneRecordSource{record: kafkaRecord(t, envelope, 5)}
	handler := &recordingHandler{}
	validator, _ := NewSequenceValidator(nil)
	consumer, err := NewConsumer("property-catalog", source, handler, validator)
	if err != nil {
		t.Fatal(err)
	}
	if err := consumer.ProcessOne(context.Background()); err != nil {
		t.Fatal(err)
	}
	if source.commits != 1 || source.rebalances != 1 || len(handler.deliveries) != 1 ||
		handler.deliveries[0].ExactDuplicate {
		t.Fatalf("first delivery source=%+v deliveries=%+v", source, handler.deliveries)
	}
	source.record = kafkaRecord(t, envelope, 6)
	if err := consumer.ProcessOne(context.Background()); err != nil {
		t.Fatal(err)
	}
	if source.commits != 2 || len(handler.deliveries) != 2 || !handler.deliveries[1].ExactDuplicate {
		t.Fatalf("duplicate was not idempotent: commits=%d deliveries=%+v", source.commits, handler.deliveries)
	}
}

func TestConsumerCommitsFencedCrashReplayOnlyForExactDurableIdentity(t *testing.T) {
	envelope := mustEnvelope(t, definitionDeliveryInput(t, 1))
	snapshot := envelope.Snapshot()
	validator, err := NewSequenceValidator([]StreamCheckpoint{{
		OrganizationID: snapshot.OrganizationID, WorkspaceID: snapshot.WorkspaceID,
		CatalogEpoch: snapshot.CatalogEpoch, CatalogRevision: snapshot.CatalogRevision,
		BuildToken: snapshot.BuildToken, ProjectionVersion: snapshot.ProjectionVersion,
		SourceAdapter: snapshot.SourceAdapter, ProducerStreamID: snapshot.ProducerStreamID,
		Sequence: snapshot.Sequence, Terminal: snapshot.Terminal,
		PayloadSHA256: snapshot.PayloadSHA256, EnvelopeID: snapshot.EnvelopeID,
	}})
	if err != nil {
		t.Fatal(err)
	}
	sink := &recordingSink{}
	// A live authorization failure models the build becoming fenced after the
	// ledger insert but before the original Kafka offset commit.
	guard := &recordingLeaseGuard{failAt: 1}
	handler, err := NewDeliveryHandler(sink, guard, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	source := &oneRecordSource{record: kafkaRecord(t, envelope, 5)}
	consumer, err := NewConsumer("property-catalog", source, handler, validator)
	if err != nil {
		t.Fatal(err)
	}
	if err := consumer.ProcessOne(context.Background()); err != nil {
		t.Fatalf("exact durable replay could not commit after fence: %v", err)
	}
	if source.commits != 1 || len(sink.calls) != 0 || len(guard.requests) != 0 {
		t.Fatalf("exact replay commits=%d writes=%v authorizations=%d", source.commits, sink.calls, len(guard.requests))
	}

	variantInput := definitionDeliveryInput(t, 1)
	variantInput.Payload.SourceBatchDigest = testDigest("same-sequence-variant")
	variant := mustEnvelope(t, variantInput)
	source.record = kafkaRecord(t, variant, 6)
	if err := consumer.ProcessOne(context.Background()); !errors.Is(err, ErrSequenceConflict) {
		t.Fatalf("same-sequence variant error=%v", err)
	}
	if source.commits != 1 || len(sink.calls) != 0 || len(guard.requests) != 0 {
		t.Fatalf("conflicting replay commits=%d writes=%v authorizations=%d", source.commits, sink.calls, len(guard.requests))
	}
}

func TestConsumerRejectsPoisonGapAndHandlerFailureWithoutCommit(t *testing.T) {
	tests := []struct {
		name     string
		record   func(*testing.T) catalogkafka.Record
		handler  error
		wantKind error
	}{
		{
			name: "mismatched-key",
			record: func(t *testing.T) catalogkafka.Record {
				record := kafkaRecord(t, mustEnvelope(t, testEnvelopeInput(t, 1, ZeroSHA256, 1)), 1)
				record.Key = []byte("wrong")
				return record
			},
			wantKind: ErrPoisonRecord,
		},
		{
			name: "sequence-gap",
			record: func(t *testing.T) catalogkafka.Record {
				return kafkaRecord(t, mustEnvelope(t, testEnvelopeInput(t, 3, testDigest("previous"), 1)), 1)
			},
			wantKind: ErrSequenceGap,
		},
		{
			name: "handler-failure",
			record: func(t *testing.T) catalogkafka.Record {
				return kafkaRecord(t, mustEnvelope(t, testEnvelopeInput(t, 1, ZeroSHA256, 1)), 1)
			},
			handler: errors.New("sink unavailable"),
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			source := &oneRecordSource{record: test.record(t)}
			handler := &recordingHandler{err: test.handler}
			validator, _ := NewSequenceValidator(nil)
			consumer, _ := NewConsumer("property-catalog", source, handler, validator)
			err := consumer.ProcessOne(context.Background())
			if err == nil || (test.wantKind != nil && !errors.Is(err, test.wantKind)) {
				t.Fatalf("error=%v want kind %v", err, test.wantKind)
			}
			if source.commits != 0 || source.rebalances != 1 {
				t.Fatalf("poison/failure committed or held rebalance: %+v", source)
			}
		})
	}
}
