package catalogkafka

import (
	"bytes"
	"context"
	"errors"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogwriter"
	"github.com/twmb/franz-go/pkg/kgo"
)

type fakeWriter struct {
	mu      sync.Mutex
	records []Record
	err     error
	closed  bool
}

func (w *fakeWriter) WriteRecord(ctx context.Context, record Record) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	w.records = append(w.records, record)
	return w.err
}

func (w *fakeWriter) Close() { w.closed = true }

type fakeSource struct {
	record        Record
	pollErr       error
	commitErr     error
	waitForCancel bool
	commits       int
	releases      int
	closed        bool
}

func (s *fakeSource) PollOne(ctx context.Context) (Record, error) {
	if s.waitForCancel {
		<-ctx.Done()
		return Record{}, ctx.Err()
	}
	return s.record, s.pollErr
}

func (s *fakeSource) Commit(context.Context, Record) error {
	if s.commitErr != nil {
		return s.commitErr
	}
	s.commits++
	return nil
}

func (s *fakeSource) AllowRebalance() { s.releases++ }
func (s *fakeSource) Close()          { s.closed = true }

type handlerFunc func(context.Context, Delivery) error

type cancelingClickHouseDeliverySink struct {
	cancel        context.CancelFunc
	catalogCalls  int
	deliveryCalls int
}

func (s *cancelingClickHouseDeliverySink) InsertCatalog(
	ctx context.Context, _ catalogwriter.Table, _ []map[string]any,
) error {
	s.catalogCalls++
	s.cancel()
	<-ctx.Done()
	return ctx.Err()
}

func (s *cancelingClickHouseDeliverySink) InsertDelivery(
	context.Context, []map[string]any,
) error {
	s.deliveryCalls++
	return nil
}

func (fn handlerFunc) Deliver(ctx context.Context, delivery Delivery) error {
	return fn(ctx, delivery)
}

func kafkaRecord(t *testing.T, envelope WireEnvelope) Record {
	t.Helper()
	key, err := KafkaKey(envelope.ProjectID(), envelope.CatalogEpoch(), envelope.ProducerStreamID())
	if err != nil {
		t.Fatal(err)
	}
	return Record{
		Topic: "catalog-v3", Key: key, Value: envelope.Marshal(), Partition: 2, Offset: 9,
	}
}

func TestSequenceValidatorExactDuplicateAndConflict(t *testing.T) {
	validator, err := NewSequenceValidator(nil)
	if err != nil {
		t.Fatal(err)
	}
	first := mustEnvelope(t, testEnvelopeInput(t))
	validation, err := validator.Check(first)
	if err != nil || validation.Status != SequenceNext {
		t.Fatalf("first status=%q err=%v", validation.Status, err)
	}
	if err := validator.Acknowledge(validation); err != nil {
		t.Fatal(err)
	}
	duplicate, err := validator.Check(first)
	if err != nil || duplicate.Status != SequenceExactDuplicate {
		t.Fatalf("duplicate status=%q err=%v", duplicate.Status, err)
	}

	conflictingInput := testEnvelopeInput(t)
	conflictingInput.Payload.SourceBatchDigest = testDigest("different")
	conflicting := mustEnvelope(t, conflictingInput)
	if _, err := validator.Check(conflicting); !errors.Is(err, ErrSequenceConflict) {
		t.Fatalf("conflicting sequence error=%v", err)
	}

	secondInput := testEnvelopeInput(t)
	secondInput.Sequence = 2
	secondInput.PreviousPayloadSHA256 = first.PayloadSHA256()
	second := mustEnvelope(t, secondInput)
	next, err := validator.Check(second)
	if err != nil || next.Status != SequenceNext {
		t.Fatalf("next status=%q err=%v", next.Status, err)
	}
	brokenInput := secondInput
	brokenInput.PreviousPayloadSHA256 = testDigest("wrong")
	broken := mustEnvelope(t, brokenInput)
	if _, err := validator.Check(broken); !errors.Is(err, ErrChainConflict) {
		t.Fatalf("chain error=%v", err)
	}

	unseeded, _ := NewSequenceValidator(nil)
	if _, err := unseeded.Check(second); !errors.Is(err, ErrSequenceGap) {
		t.Fatalf("unseeded sequence error=%v", err)
	}
}

func TestProducerSynchronousFailureAndCancellation(t *testing.T) {
	envelope := mustEnvelope(t, testEnvelopeInput(t))
	brokerFailure := errors.New("not enough replicas")
	writer := &fakeWriter{err: brokerFailure}
	producer, err := NewProducer("catalog-v3", writer)
	if err != nil {
		t.Fatal(err)
	}
	if err := producer.Publish(context.Background(), envelope); !errors.Is(err, brokerFailure) {
		t.Fatalf("producer failure=%v", err)
	}
	if len(writer.records) != 1 || !bytes.Equal(writer.records[0].Value, envelope.Marshal()) {
		t.Fatal("producer did not synchronously submit the exact envelope")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := producer.Publish(ctx, envelope); !errors.Is(err, context.Canceled) {
		t.Fatalf("canceled publish=%v", err)
	}
	if len(writer.records) != 1 {
		t.Fatal("pre-canceled publish reached writer")
	}
	producer.Close()
	if !writer.closed {
		t.Fatal("producer did not close writer")
	}
}

func TestConsumerHandlerFailureDoesNotCommitOrAdvance(t *testing.T) {
	envelope := mustEnvelope(t, testEnvelopeInput(t))
	source := &fakeSource{record: kafkaRecord(t, envelope)}
	handlerFailure := errors.New("delivery ledger unavailable")
	calls := 0
	handler := handlerFunc(func(context.Context, Delivery) error {
		calls++
		if calls == 1 {
			return handlerFailure
		}
		return nil
	})
	validator, _ := NewSequenceValidator(nil)
	consumer, err := NewConsumer("catalog-v3", source, handler, validator)
	if err != nil {
		t.Fatal(err)
	}
	if err := consumer.ProcessOne(context.Background()); !errors.Is(err, handlerFailure) {
		t.Fatalf("handler failure=%v", err)
	}
	if source.commits != 0 || source.releases != 1 {
		t.Fatalf("after failure commits=%d releases=%d", source.commits, source.releases)
	}
	if _, exists := validator.Checkpoint(testProject, 1, testStream); exists {
		t.Fatal("failed handler advanced sequence")
	}
	if err := consumer.ProcessOne(context.Background()); err != nil {
		t.Fatal(err)
	}
	if source.commits != 1 || calls != 2 {
		t.Fatalf("retry commits=%d calls=%d", source.commits, calls)
	}
}

func TestConsumerWholeEnvelopeTimeoutDoesNotCommitOrAdvance(t *testing.T) {
	envelope := mustEnvelope(t, testEnvelopeInput(t))
	source := &fakeSource{record: kafkaRecord(t, envelope)}
	handler, err := NewClickHouseDeliveryHandler(
		&blockingClickHouseDeliverySink{}, 25*time.Millisecond,
	)
	if err != nil {
		t.Fatal(err)
	}
	validator, err := NewSequenceValidator(nil)
	if err != nil {
		t.Fatal(err)
	}
	consumer, err := NewConsumer("catalog-v3", source, handler, validator)
	if err != nil {
		t.Fatal(err)
	}
	started := time.Now()
	err = consumer.ProcessOne(context.Background())
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("delivery timeout error=%v", err)
	}
	if time.Since(started) > time.Second {
		t.Fatal("consumer delivery timeout exceeded its test bound")
	}
	if source.commits != 0 || source.releases != 1 {
		t.Fatalf("timeout commits=%d releases=%d", source.commits, source.releases)
	}
	if _, exists := validator.Checkpoint(testProject, 1, testStream); exists {
		t.Fatal("timed-out ClickHouse handler advanced sequence state")
	}
}

func TestConsumerCancellationDuringClickHouseDeliveryDoesNotCommit(t *testing.T) {
	envelope := mustEnvelope(t, testEnvelopeInput(t))
	source := &fakeSource{record: kafkaRecord(t, envelope)}
	ctx, cancel := context.WithCancel(context.Background())
	sink := &cancelingClickHouseDeliverySink{cancel: cancel}
	handler, err := NewClickHouseDeliveryHandler(sink, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	validator, err := NewSequenceValidator(nil)
	if err != nil {
		t.Fatal(err)
	}
	consumer, err := NewConsumer("catalog-v3", source, handler, validator)
	if err != nil {
		t.Fatal(err)
	}
	err = consumer.ProcessOne(ctx)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("delivery cancellation error=%v", err)
	}
	if source.commits != 0 || source.releases != 1 ||
		sink.catalogCalls != 1 || sink.deliveryCalls != 0 {
		t.Fatalf(
			"commits=%d releases=%d catalog calls=%d delivery calls=%d",
			source.commits, source.releases, sink.catalogCalls, sink.deliveryCalls,
		)
	}
	if _, exists := validator.Checkpoint(testProject, 1, testStream); exists {
		t.Fatal("canceled ClickHouse handler advanced sequence state")
	}
}

func TestConsumerCommitFailureRetriesAsExactDuplicate(t *testing.T) {
	envelope := mustEnvelope(t, testEnvelopeInput(t))
	commitFailure := errors.New("coordinator unavailable")
	source := &fakeSource{record: kafkaRecord(t, envelope), commitErr: commitFailure}
	var duplicates []bool
	handler := handlerFunc(func(_ context.Context, delivery Delivery) error {
		duplicates = append(duplicates, delivery.ExactDuplicate)
		return nil
	})
	validator, _ := NewSequenceValidator(nil)
	consumer, _ := NewConsumer("catalog-v3", source, handler, validator)
	if err := consumer.ProcessOne(context.Background()); !errors.Is(err, commitFailure) {
		t.Fatalf("commit failure=%v", err)
	}
	source.commitErr = nil
	if err := consumer.ProcessOne(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(duplicates) != 2 || duplicates[0] || !duplicates[1] || source.commits != 1 {
		t.Fatalf("duplicates=%v commits=%d", duplicates, source.commits)
	}
}

func TestConsumerPoisonNeverCommits(t *testing.T) {
	envelope := mustEnvelope(t, testEnvelopeInput(t))
	source := &fakeSource{record: kafkaRecord(t, envelope)}
	source.record.Key = []byte("wrong")
	handled := false
	validator, _ := NewSequenceValidator(nil)
	consumer, _ := NewConsumer("catalog-v3", source, handlerFunc(func(context.Context, Delivery) error {
		handled = true
		return nil
	}), validator)
	err := consumer.ProcessOne(context.Background())
	if !errors.Is(err, ErrPoisonRecord) || source.commits != 0 || handled {
		t.Fatalf("poison err=%v commits=%d handled=%v", err, source.commits, handled)
	}

	source.record = kafkaRecord(t, envelope)
	source.record.Value = append(source.record.Value, '\n')
	err = consumer.ProcessOne(context.Background())
	if !errors.Is(err, ErrPoisonRecord) || source.commits != 0 {
		t.Fatalf("noncanonical poison err=%v commits=%d", err, source.commits)
	}
}

func TestConsumerCancellationDoesNotCommit(t *testing.T) {
	validator, _ := NewSequenceValidator(nil)
	source := &fakeSource{waitForCancel: true}
	consumer, _ := NewConsumer("catalog-v3", source, handlerFunc(func(context.Context, Delivery) error {
		t.Fatal("handler called after canceled poll")
		return nil
	}), validator)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	err := consumer.ProcessOne(ctx)
	if !errors.Is(err, context.Canceled) || source.commits != 0 || source.releases != 1 {
		t.Fatalf("cancellation err=%v commits=%d releases=%d", err, source.commits, source.releases)
	}

	envelope := mustEnvelope(t, testEnvelopeInput(t))
	source = &fakeSource{record: kafkaRecord(t, envelope)}
	ctx, cancel = context.WithCancel(context.Background())
	consumer, _ = NewConsumer("catalog-v3", source, handlerFunc(func(context.Context, Delivery) error {
		cancel()
		return nil
	}), validator)
	err = consumer.ProcessOne(ctx)
	if !errors.Is(err, context.Canceled) || source.commits != 0 {
		t.Fatalf("post-handler cancellation err=%v commits=%d", err, source.commits)
	}
}

func TestFranzOptionsPinSafetyProperties(t *testing.T) {
	producerCfg := FranzProducerConfig{
		Brokers: []string{"127.0.0.1:9092"}, Topic: "catalog-v3",
		ClientID: "producer", DeliveryTimeout: defaultRecordDeliveryTimeout,
	}
	producerClient, err := kgo.NewClient(franzProducerOptions(producerCfg)...)
	if err != nil {
		t.Fatal(err)
	}
	defer producerClient.Close()
	if disabled, _ := producerClient.OptValue(kgo.DisableIdempotentWrite).(bool); disabled {
		t.Fatal("idempotent writes disabled")
	}
	if got := producerClient.OptValue(kgo.RequiredAcks); got != kgo.AllISRAcks() {
		t.Fatalf("required acks=%v", got)
	}
	if got := producerClient.OptValue(kgo.ProducerBatchMaxBytes); got != int32(MaxRecordBytes+kafkaBatchOverheadAllowance) {
		t.Fatalf("batch max=%v", got)
	}

	consumerCfg := FranzConsumerConfig{
		Brokers: []string{"127.0.0.1:9092"}, Topic: "catalog-v3",
		GroupID: "catalog-test", ClientID: "consumer",
	}
	consumerClient, err := kgo.NewClient(franzConsumerOptions(consumerCfg)...)
	if err != nil {
		t.Fatal(err)
	}
	defer consumerClient.CloseAllowingRebalance()
	checks := map[string]bool{
		"manual commit":       consumerClient.OptValue(kgo.DisableAutoCommit).(bool),
		"blocked rebalance":   consumerClient.OptValue(kgo.BlockRebalanceOnPoll).(bool),
		"bounded concurrency": consumerClient.OptValue(kgo.MaxConcurrentFetches).(int) == 1,
	}
	for name, ok := range checks {
		if !ok {
			t.Errorf("missing franz safety option: %s", name)
		}
	}
}

func TestFranzConfigFailsClosed(t *testing.T) {
	validator, _ := NewSequenceValidator(nil)
	handler := handlerFunc(func(context.Context, Delivery) error { return nil })
	if _, err := NewFranzProducer(FranzProducerConfig{Topic: "catalog-v3"}); err == nil || !strings.Contains(err.Error(), "broker") {
		t.Fatalf("producer config error=%v", err)
	}
	if _, err := NewFranzProducer(FranzProducerConfig{
		Brokers: []string{"127.0.0.1:9092"}, Topic: "catalog-v3",
		DeliveryTimeout: maxRecordDeliveryTimeout + time.Nanosecond,
	}); err == nil || !strings.Contains(err.Error(), "delivery timeout") {
		t.Fatalf("producer hard timeout error=%v", err)
	}
	if _, err := NewFranzConsumer(FranzConsumerConfig{
		Brokers: []string{"127.0.0.1:9092"}, Topic: "catalog-v3",
	}, handler, validator); err == nil || !strings.Contains(err.Error(), "consumer group") {
		t.Fatalf("consumer config error=%v", err)
	}
}
