package propertycatalog

import (
	"reflect"
	"testing"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
)

func TestFranzProducerIsIdempotentAllISRAndHardBounded(t *testing.T) {
	producer, err := NewFranzProducer(FranzProducerConfig{
		Brokers: []string{"kafka:9092"}, Topic: "property-catalog-v1-dev",
		DeliveryTimeout: time.Second,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer producer.Close()
	writer, ok := producer.writer.(*franzRecordWriter)
	if !ok || writer.client == nil {
		t.Fatal("producer is not backed by the bounded Franz writer")
	}
	client := writer.client
	if !reflect.DeepEqual(client.OptValue(kgo.RequiredAcks), kgo.AllISRAcks()) ||
		client.OptValue(kgo.DisableIdempotentWrite) != false ||
		client.OptValue(kgo.MaxBufferedRecords) != int64(1) ||
		client.OptValue(kgo.MaxBufferedBytes) != int64(MaxRecordBytes) ||
		client.OptValue(kgo.RecordDeliveryTimeout) != time.Second {
		t.Fatalf(
			"unsafe producer options: acks=%T/%v disable_idempotence=%T/%v records=%T/%v bytes=%T/%v timeout=%T/%v",
			client.OptValue(kgo.RequiredAcks), client.OptValue(kgo.RequiredAcks),
			client.OptValue(kgo.DisableIdempotentWrite), client.OptValue(kgo.DisableIdempotentWrite),
			client.OptValue(kgo.MaxBufferedRecords), client.OptValue(kgo.MaxBufferedRecords),
			client.OptValue(kgo.MaxBufferedBytes), client.OptValue(kgo.MaxBufferedBytes),
			client.OptValue(kgo.RecordDeliveryTimeout), client.OptValue(kgo.RecordDeliveryTimeout),
		)
	}
}

func TestFranzConsumerIsManualCommitOneRecordAndMemoryBounded(t *testing.T) {
	validator, err := NewSequenceValidator(nil)
	if err != nil {
		t.Fatal(err)
	}
	consumer, err := NewFranzConsumer(FranzConsumerConfig{
		Brokers: []string{"kafka:9092"}, Topic: "property-catalog-v1-dev",
		GroupID: "property-catalog-v1-dev-consumer",
	}, &recordingHandler{}, validator)
	if err != nil {
		t.Fatal(err)
	}
	defer consumer.Close()
	source, ok := consumer.source.(*franzManualSource)
	if !ok || source.client == nil {
		t.Fatal("consumer is not backed by the manual Franz source")
	}
	client := source.client
	wantFetchBytes := int32(MaxRecordBytes + kafkaRecordOverheadAllowance)
	if client.OptValue(kgo.DisableAutoCommit) != true ||
		client.OptValue(kgo.BlockRebalanceOnPoll) != true ||
		client.OptValue(kgo.MaxConcurrentFetches) != 1 ||
		client.OptValue(kgo.FetchMaxBytes) != wantFetchBytes ||
		client.OptValue(kgo.FetchMaxPartitionBytes) != wantFetchBytes {
		t.Fatalf(
			"unsafe consumer options: auto=%v block=%v fetches=%v max=%v partition=%v",
			client.OptValue(kgo.DisableAutoCommit), client.OptValue(kgo.BlockRebalanceOnPoll),
			client.OptValue(kgo.MaxConcurrentFetches), client.OptValue(kgo.FetchMaxBytes),
			client.OptValue(kgo.FetchMaxPartitionBytes),
		)
	}
}
