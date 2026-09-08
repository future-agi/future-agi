package catalogkafka

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogwriter"
)

type clickHouseHandlerCall struct {
	table    catalogwriter.Table
	rows     []map[string]any
	delivery bool
}

type recordingClickHouseDeliverySink struct {
	calls         []clickHouseHandlerCall
	catalogCalls  int
	catalogFailAt int
	deliveryErr   error
}

type blockingClickHouseDeliverySink struct{}

type cumulativeDelayClickHouseDeliverySink struct {
	firstDelay    time.Duration
	catalogCalls  int
	deliveryCalls int
	deadlines     []time.Time
}

func (*blockingClickHouseDeliverySink) InsertCatalog(
	ctx context.Context, _ catalogwriter.Table, _ []map[string]any,
) error {
	<-ctx.Done()
	return ctx.Err()
}

func (*blockingClickHouseDeliverySink) InsertDelivery(ctx context.Context, _ []map[string]any) error {
	<-ctx.Done()
	return ctx.Err()
}

func (s *cumulativeDelayClickHouseDeliverySink) InsertCatalog(
	ctx context.Context, _ catalogwriter.Table, _ []map[string]any,
) error {
	s.catalogCalls++
	deadline, exists := ctx.Deadline()
	if !exists {
		return errors.New("catalog delivery context has no deadline")
	}
	s.deadlines = append(s.deadlines, deadline)
	if s.catalogCalls == 1 {
		timer := time.NewTimer(s.firstDelay)
		defer timer.Stop()
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-timer.C:
			return nil
		}
	}
	<-ctx.Done()
	return ctx.Err()
}

func (s *cumulativeDelayClickHouseDeliverySink) InsertDelivery(
	context.Context, []map[string]any,
) error {
	s.deliveryCalls++
	return nil
}

func (s *recordingClickHouseDeliverySink) InsertCatalog(
	_ context.Context, table catalogwriter.Table, rows []map[string]any,
) error {
	s.catalogCalls++
	if s.catalogFailAt != 0 && s.catalogCalls == s.catalogFailAt {
		return errors.New("catalog unavailable")
	}
	s.calls = append(s.calls, clickHouseHandlerCall{table: table, rows: rows})
	return nil
}

func (s *recordingClickHouseDeliverySink) InsertDelivery(
	_ context.Context, rows []map[string]any,
) error {
	if s.deliveryErr != nil {
		return s.deliveryErr
	}
	s.calls = append(s.calls, clickHouseHandlerCall{rows: rows, delivery: true})
	return nil
}

func deliveryTestEnvelope(t *testing.T) WireEnvelope {
	t.Helper()
	input := testEnvelopeInput(t)
	secondKey := testKeyRow(1)
	input.Payload.KeyRows = 2
	input.Payload.ValueRows = 1
	input.Payload.Chunks = []ChunkInput{
		{Table: KeyTable, Index: 0, RowCount: 1, JSONEachRow: testRowBytes(t, testKeyRow(0))},
		{Table: KeyTable, Index: 1, RowCount: 1, JSONEachRow: testRowBytes(t, secondKey)},
		{Table: ValueTable, Index: 2, RowCount: 1, JSONEachRow: testRowBytes(t, testValueRow())},
	}
	return mustEnvelope(t, input)
}

func TestClickHouseDeliveryHandlerWritesChunksThenExactLedger(t *testing.T) {
	sink := &recordingClickHouseDeliverySink{}
	handler, err := NewClickHouseDeliveryHandler(sink, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	deliveredAt := time.Date(2026, 8, 13, 12, 0, 2, 123456789, time.FixedZone("offset", 3600))
	handler.now = func() time.Time { return deliveredAt }
	envelope := deliveryTestEnvelope(t)
	if err := handler.Deliver(context.Background(), Delivery{
		Envelope: envelope, Topic: "catalog.v3", Partition: 3, Offset: 42,
	}); err != nil {
		t.Fatal(err)
	}

	if len(sink.calls) != 4 {
		t.Fatalf("calls=%d want 4", len(sink.calls))
	}
	if sink.calls[0].table != catalogwriter.KeyTable || sink.calls[1].table != catalogwriter.KeyTable ||
		sink.calls[2].table != catalogwriter.ValueTable || !sink.calls[3].delivery {
		t.Fatalf("unexpected call order: %+v", sink.calls)
	}
	if epoch, ok := sink.calls[0].rows[0]["catalog_epoch"].(json.Number); !ok || epoch.String() != "1" {
		t.Fatalf("catalog_epoch=%#v, want json.Number(1)", sink.calls[0].rows[0]["catalog_epoch"])
	}

	snapshot := envelope.Snapshot()
	row := sink.calls[3].rows[0]
	want := map[string]any{
		"envelope_format":         snapshot.Format,
		"envelope_version":        snapshot.Version,
		"envelope_id":             snapshot.EnvelopeID,
		"project_id":              snapshot.ProjectID,
		"catalog_epoch":           snapshot.CatalogEpoch,
		"producer_stream_id":      snapshot.ProducerStreamID,
		"sequence":                snapshot.Sequence,
		"payload_sha256":          snapshot.PayloadSHA256,
		"previous_payload_sha256": snapshot.PreviousPayloadSHA256,
		"source_batch_digest":     snapshot.Payload.SourceBatchDigest,
		"outcome":                 "committed",
		"gap_reasons":             []string{},
		"source_min_start":        snapshot.Payload.SourceMinStart,
		"source_max_start":        snapshot.Payload.SourceMaxStart,
		"source_rows":             snapshot.Payload.SourceRows,
		"key_rows":                snapshot.Payload.KeyRows,
		"value_rows":              snapshot.Payload.ValueRows,
		"transport":               "kafka",
		"kafka_partition":         int32(3),
		"kafka_offset":            int64(42),
		"delivered_at":            "2026-08-13 11:00:02.123456",
		"_version":                uint64(deliveredAt.UTC().UnixNano()),
	}
	if !reflect.DeepEqual(row, want) {
		t.Fatalf("ledger row mismatch\n got: %#v\nwant: %#v", row, want)
	}
}

func TestSharedDirectAndKafkaHandlersDeliverSystemModelSourceKind(t *testing.T) {
	key := testKeyRow(0)
	key["source_kind"] = "system_attribute"
	value := testValueRow()
	value["source_kind"] = "system_attribute"
	input := testEnvelopeInput(t)
	input.Payload.ValueRows = 1
	input.Payload.Chunks = []ChunkInput{
		{Table: KeyTable, Index: 0, RowCount: 1, JSONEachRow: testRowBytes(t, key)},
		{Table: ValueTable, Index: 1, RowCount: 1, JSONEachRow: testRowBytes(t, value)},
	}
	envelope := mustEnvelope(t, input)

	kafkaSink := &recordingClickHouseDeliverySink{}
	handler, err := NewClickHouseDeliveryHandler(kafkaSink, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if err := handler.Deliver(context.Background(), Delivery{Envelope: envelope, Partition: 1, Offset: 2}); err != nil {
		t.Fatal(err)
	}
	if kafkaSink.calls[0].rows[0]["source_kind"] != "system_attribute" ||
		kafkaSink.calls[1].rows[0]["source_kind"] != "system_attribute" {
		t.Fatalf("Kafka handler dropped namespace: %+v", kafkaSink.calls)
	}

	directSink := &recordingClickHouseDeliverySink{}
	publisher, err := NewDirectClickHouseEnvelopePublisher(directSink, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if err := publisher.Publish(context.Background(), envelope); err != nil {
		t.Fatal(err)
	}
	if directSink.calls[0].rows[0]["source_kind"] != "system_attribute" ||
		directSink.calls[1].rows[0]["source_kind"] != "system_attribute" {
		t.Fatalf("direct handler dropped namespace: %+v", directSink.calls)
	}
}

func TestClickHouseDeliveryHandlerBoundsTheWholeMultiChunkEnvelope(t *testing.T) {
	const envelopeTimeout = 200 * time.Millisecond
	sink := &cumulativeDelayClickHouseDeliverySink{firstDelay: 25 * time.Millisecond}
	handler, err := NewClickHouseDeliveryHandler(sink, envelopeTimeout)
	if err != nil {
		t.Fatal(err)
	}
	started := time.Now()
	err = handler.Deliver(context.Background(), Delivery{
		Envelope: deliveryTestEnvelope(t), Partition: 1, Offset: 7,
	})
	elapsed := time.Since(started)
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("multi-chunk delivery error=%v", err)
	}
	if sink.catalogCalls != 2 || sink.deliveryCalls != 0 || len(sink.deadlines) != 2 {
		t.Fatalf(
			"catalog calls=%d delivery calls=%d deadlines=%d",
			sink.catalogCalls, sink.deliveryCalls, len(sink.deadlines),
		)
	}
	if !sink.deadlines[0].Equal(sink.deadlines[1]) {
		t.Fatalf("chunk deadlines reset: %s then %s", sink.deadlines[0], sink.deadlines[1])
	}
	if elapsed > time.Second {
		t.Fatalf("whole-envelope deadline exceeded its test bound: %s", elapsed)
	}
}

func TestDirectClickHouseEnvelopePublisherUsesSharedV3HandlerAndSentinelOffsets(t *testing.T) {
	sink := &recordingClickHouseDeliverySink{}
	publisher, err := NewDirectClickHouseEnvelopePublisher(sink, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	deliveredAt := time.Date(2026, 8, 13, 12, 0, 2, 123456789, time.UTC)
	publisher.handler.now = func() time.Time { return deliveredAt }
	envelope := deliveryTestEnvelope(t)
	if err := publisher.Publish(context.Background(), envelope); err != nil {
		t.Fatal(err)
	}
	if len(sink.calls) != 4 || !sink.calls[3].delivery {
		t.Fatalf("direct calls=%+v", sink.calls)
	}
	row := sink.calls[3].rows[0]
	if row["envelope_version"] != EnvelopeVersion || row["envelope_id"] != envelope.EnvelopeID() ||
		row["sequence"] != envelope.Sequence() || row["transport"] != "direct" ||
		row["kafka_partition"] != int32(-1) || row["kafka_offset"] != int64(-1) ||
		row["outcome"] != "committed" {
		t.Fatalf("direct ledger row=%#v", row)
	}
}

func TestDirectClickHouseEnvelopePublisherIsBoundedAndFailsClosed(t *testing.T) {
	if _, err := NewDirectClickHouseEnvelopePublisher(nil, time.Second); err == nil {
		t.Fatal("nil direct sink was accepted")
	}
	if _, err := NewDirectClickHouseEnvelopePublisher(
		&recordingClickHouseDeliverySink{}, maxClickHouseDeliveryTimeout+time.Nanosecond,
	); err == nil || !strings.Contains(err.Error(), "10s") {
		t.Fatalf("oversize timeout error=%v", err)
	}
	publisher, err := NewDirectClickHouseEnvelopePublisher(
		&blockingClickHouseDeliverySink{}, 5*time.Millisecond,
	)
	if err != nil {
		t.Fatal(err)
	}
	started := time.Now()
	err = publisher.Publish(context.Background(), deliveryTestEnvelope(t))
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("bounded direct publish error=%v", err)
	}
	if elapsed := time.Since(started); elapsed > time.Second {
		t.Fatalf("direct publish exceeded bounded test deadline: %s", elapsed)
	}
	if err := publisher.Publish(nil, deliveryTestEnvelope(t)); err == nil {
		t.Fatal("nil direct context was accepted")
	}
}

func TestClickHouseDeliveryHandlerExactDuplicateRefreshesOnlyLedger(t *testing.T) {
	sink := &recordingClickHouseDeliverySink{}
	handler, err := NewClickHouseDeliveryHandler(sink, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	handler.now = func() time.Time { return time.Date(2026, 8, 13, 12, 0, 0, 0, time.UTC) }
	if err := handler.Deliver(context.Background(), Delivery{
		Envelope: deliveryTestEnvelope(t), Partition: 1, Offset: 9, ExactDuplicate: true,
	}); err != nil {
		t.Fatal(err)
	}
	if len(sink.calls) != 1 || !sink.calls[0].delivery || sink.catalogCalls != 0 {
		t.Fatalf("duplicate calls=%+v catalog_attempts=%d", sink.calls, sink.catalogCalls)
	}
}

func TestClickHouseDeliveryHandlerPrevalidatesAllChunksBeforeWriting(t *testing.T) {
	input := testEnvelopeInput(t)
	badValue := testValueRow()
	delete(badValue, "value_search_text")
	badValue["trace_id"] = "must-not-reach-clickhouse"
	input.Payload.ValueRows = 1
	input.Payload.Chunks = append(input.Payload.Chunks, ChunkInput{
		Table: ValueTable, Index: 1, RowCount: 1, JSONEachRow: testRowBytes(t, badValue),
	})
	envelope := mustEnvelope(t, input)
	sink := &recordingClickHouseDeliverySink{}
	handler, err := NewClickHouseDeliveryHandler(sink, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	err = handler.Deliver(context.Background(), Delivery{Envelope: envelope, Partition: 0, Offset: 0})
	if err == nil || !strings.Contains(err.Error(), "exact legacy or source-kind") {
		t.Fatalf("error=%v", err)
	}
	if len(sink.calls) != 0 || sink.catalogCalls != 0 {
		t.Fatalf("prevalidation wrote calls=%+v", sink.calls)
	}
}

func TestClickHouseDeliveryHandlerRejectsCrossProjectRows(t *testing.T) {
	input := testEnvelopeInput(t)
	row := testKeyRow(0)
	row["project_id"] = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
	input.Payload.Chunks[0].JSONEachRow = testRowBytes(t, row)
	envelope := mustEnvelope(t, input)
	sink := &recordingClickHouseDeliverySink{}
	handler, err := NewClickHouseDeliveryHandler(sink, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	err = handler.Deliver(context.Background(), Delivery{Envelope: envelope, Partition: 0, Offset: 0})
	if err == nil || !strings.Contains(err.Error(), "project does not match") || len(sink.calls) != 0 {
		t.Fatalf("error=%v calls=%+v", err, sink.calls)
	}
}

func TestClickHouseDeliveryHandlerFailureNeverWritesEarlyLedger(t *testing.T) {
	sink := &recordingClickHouseDeliverySink{catalogFailAt: 2}
	handler, err := NewClickHouseDeliveryHandler(sink, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	err = handler.Deliver(context.Background(), Delivery{
		Envelope: deliveryTestEnvelope(t), Partition: 2, Offset: 10,
	})
	if err == nil || !strings.Contains(err.Error(), "catalog unavailable") {
		t.Fatalf("error=%v", err)
	}
	if len(sink.calls) != 1 || sink.calls[0].delivery {
		t.Fatalf("calls=%+v", sink.calls)
	}

	sink = &recordingClickHouseDeliverySink{deliveryErr: errors.New("ledger unavailable")}
	handler, err = NewClickHouseDeliveryHandler(sink, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	err = handler.Deliver(context.Background(), Delivery{
		Envelope: deliveryTestEnvelope(t), Partition: 2, Offset: 10,
	})
	if err == nil || !strings.Contains(err.Error(), "ledger unavailable") || len(sink.calls) != 3 {
		t.Fatalf("error=%v calls=%+v", err, sink.calls)
	}
}

func TestClickHouseDeliveryHandlerRejectsNilCanceledAndInvalidOffset(t *testing.T) {
	if _, err := NewClickHouseDeliveryHandler(nil, time.Second); err == nil {
		t.Fatal("nil sink was accepted")
	}
	sink := &recordingClickHouseDeliverySink{}
	handler, err := NewClickHouseDeliveryHandler(sink, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if err := handler.Deliver(nil, Delivery{}); err == nil {
		t.Fatal("nil context was accepted")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := handler.Deliver(ctx, Delivery{}); !errors.Is(err, context.Canceled) {
		t.Fatalf("canceled error=%v", err)
	}
	if err := handler.Deliver(context.Background(), Delivery{
		Envelope: deliveryTestEnvelope(t), Partition: -1, Offset: 0,
	}); err == nil {
		t.Fatal("negative partition was accepted")
	}
	if len(sink.calls) != 0 {
		t.Fatalf("invalid delivery wrote calls=%+v", sink.calls)
	}
}
