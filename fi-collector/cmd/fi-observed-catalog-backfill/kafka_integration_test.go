package main

import (
	"context"
	"errors"
	"fmt"
	"net"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
	"github.com/twmb/franz-go/pkg/kgo"
	"github.com/twmb/franz-go/pkg/kmsg"
)

func localKafka(t *testing.T) observedcatalog.KafkaConfig {
	t.Helper()
	broker := os.Getenv("OBS_TEST_KAFKA_BROKER")
	if broker == "" {
		t.Skip("set OBS_TEST_KAFKA_BROKER for isolated Kafka integration")
	}
	host, port, err := net.SplitHostPort(broker)
	if err != nil || host != "127.0.0.1" || port == "" {
		t.Fatal("Kafka integration requires explicit loopback broker")
	}
	cfg := observedcatalog.KafkaConfig{Brokers: []string{broker}, Topic: fmt.Sprintf("observed-test-%d", time.Now().UnixNano()), Timeout: 10 * time.Second}
	cfg.Group = cfg.Topic + "-group"
	admin, err := kgo.NewClient(kgo.SeedBrokers(broker))
	if err != nil {
		t.Fatal(err)
	}
	defer admin.Close()
	request := kmsg.NewPtrCreateTopicsRequest()
	request.TimeoutMillis = 10000
	topic := kmsg.NewCreateTopicsRequestTopic()
	topic.Topic, topic.NumPartitions, topic.ReplicationFactor = cfg.Topic, 3, 1
	request.Topics = []kmsg.CreateTopicsRequestTopic{topic}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	response, err := request.RequestWith(ctx, admin)
	if err != nil || len(response.Topics) != 1 || response.Topics[0].ErrorCode != 0 {
		t.Fatalf("create isolated Kafka topic: %v %v", response, err)
	}
	// Topic creation acknowledgement may precede leader metadata visibility.
	for {
		metadata := kmsg.NewPtrMetadataRequest()
		metadata.Topics = []kmsg.MetadataRequestTopic{{Topic: &cfg.Topic}}
		result, err := metadata.RequestWith(ctx, admin)
		ready := err == nil && len(result.Topics) == 1 && result.Topics[0].ErrorCode == 0 && len(result.Topics[0].Partitions) == 3
		if ready {
			for _, partition := range result.Topics[0].Partitions {
				ready = ready && partition.ErrorCode == 0 && partition.Leader >= 0
			}
		}
		if ready {
			break
		}
		select {
		case <-ctx.Done():
			t.Fatal("isolated Kafka topic did not acquire leaders")
		case <-time.After(50 * time.Millisecond):
		}
	}
	return cfg
}

type countedSink struct {
	observedcatalog.Sink
	calls atomic.Int64
}

func (s *countedSink) Insert(ctx context.Context, batch observedcatalog.Batch) error {
	if err := s.Sink.Insert(ctx, batch); err != nil {
		return err
	}
	s.calls.Add(1)
	return nil
}

// Real group assignment, scale-out and a member leaving must not impose a
// tenant cap or lose observations. Repeated delivery remains one logical value.
func TestKafkaMultipleConsumersAndWorkspaceScale(t *testing.T) {
	cfg := localKafka(t)
	origin, database, sink := localCatalog(t)
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	workers := [2]*countedSink{{Sink: sink}, {Sink: sink}}
	stops := [2]context.CancelFunc{}
	done := [2]chan error{}
	for i := range workers {
		consumer, err := observedcatalog.NewConsumer(cfg, workers[i])
		if err != nil {
			t.Fatal(err)
		}
		work, stop := context.WithCancel(ctx)
		stops[i], done[i] = stop, make(chan error, 1)
		go func(ch chan error) {
			err := consumer.Run(work)
			consumer.Close()
			ch <- err
		}(done[i])
		t.Cleanup(func() { stop(); <-done[i] })
	}
	client, err := kgo.NewClient(kgo.SeedBrokers(cfg.Brokers...), kgo.RecordPartitioner(kgo.ManualPartitioner()), kgo.RequiredAcks(kgo.AllISRAcks()))
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	publish := func(first, last int) {
		t.Helper()
		for i := first; i < last; i++ {
			scope := exampleScope()
			scope.WorkspaceID = fmt.Sprintf("00000000-0000-4000-8000-%012d", i+1000)
			scope.ProjectID = fmt.Sprintf("00000000-0000-4000-8000-%012d", i+2000)
			batch, _, err := observedcatalog.Extract(exampleSpan(scope), observedcatalog.DefaultLimits())
			if err != nil {
				t.Fatal(err)
			}
			raw, err := observedcatalog.Encode(batch)
			if err != nil {
				t.Fatal(err)
			}
			if err := client.ProduceSync(ctx, &kgo.Record{Topic: cfg.Topic, Partition: int32(i % 3), Value: raw}).FirstErr(); err != nil {
				t.Fatal(err)
			}
		}
	}
	awaitScopes := func(want int) {
		t.Helper()
		for {
			count := strings.TrimSpace(localSQL(t, origin, "SELECT count() FROM (SELECT workspace_id, project_id FROM "+database+".observed_attribute_values GROUP BY workspace_id, project_id HAVING uniqExact(tuple(attribute_key, attribute_type, value_json))=8)"))
			if count == fmt.Sprint(want) {
				return
			}
			select {
			case <-ctx.Done():
				t.Fatalf("group stalled at %s/%d scopes", count, want)
			case <-time.After(100 * time.Millisecond):
			}
		}
	}
	publish(0, 270)
	awaitScopes(270)
	if workers[0].calls.Load() == 0 || workers[1].calls.Load() == 0 {
		t.Fatal("both consumer group members must process partitions")
	}
	stops[0]()
	err = <-done[0]
	done[0] <- err // retain completion for cleanup
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("member exit: %v", err)
	}
	// The stopped worker closes its client, transferring its partitions to the
	// remaining member. Publish both duplicate and new work across all partitions.
	publish(0, 300) // duplicates plus 30 new scopes after member loss
	awaitScopes(300)
	t.Logf("300 workspaces across 3 partitions; successful batches by consumer: %d/%d", workers[0].calls.Load(), workers[1].calls.Load())
}

type keysOnlyFailure struct{ sink observedcatalog.Sink }

func (s keysOnlyFailure) Insert(ctx context.Context, batch observedcatalog.Batch) error {
	if err := s.sink.Insert(ctx, observedcatalog.Batch{Keys: batch.Keys}); err != nil {
		return err
	}
	return errors.New("injected crash after keys, before values")
}

func TestKafkaSpoolRestartAndPartialConsumerWrite(t *testing.T) {
	cfg := localKafka(t)
	origin, database, sink := localCatalog(t)
	spoolCfg := observedcatalog.SpoolConfig{Directory: t.TempDir()}
	writer, err := observedcatalog.NewWriter(spoolCfg, observedcatalog.DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	if err := writer.EnqueueCanonicalSpans([]observedcatalog.ScopedSpan{exampleSpan(exampleScope())}); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	writer, err = observedcatalog.NewWriter(spoolCfg, observedcatalog.DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	producer, err := observedcatalog.NewProducer(cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer producer.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if n, err := writer.Replay(ctx, producer); err != nil || n < 1 {
		t.Fatalf("spool restart replay: %d %v", n, err)
	}
	consumer, err := observedcatalog.NewConsumer(cfg, keysOnlyFailure{sink})
	if err != nil {
		t.Fatal(err)
	}
	err = consumer.Run(ctx)
	consumer.Close()
	if err == nil || !strings.Contains(err.Error(), "injected crash") {
		t.Fatal("consumer did not stop on partial failure", err)
	}
	if count := localSQL(t, origin, "SELECT count() FROM "+database+".observed_attribute_values"); strings.TrimSpace(count) != "0" {
		t.Fatal("unexpected value write", count)
	}
	consumer, err = observedcatalog.NewConsumer(cfg, sink)
	if err != nil {
		t.Fatal(err)
	}
	work, stop := context.WithCancel(ctx)
	done := make(chan error, 1)
	go func() { done <- consumer.Run(work) }()
	defer func() { stop(); <-done; consumer.Close() }()
	for {
		count := localSQL(t, origin, "SELECT uniqExact(tuple(attribute_key, attribute_type, value_json)) FROM "+database+".observed_attribute_values")
		if strings.TrimSpace(count) == "8" {
			break
		}
		select {
		case err := <-done:
			done <- err
			t.Fatalf("consumer exited before replay: %v", err)
		case <-ctx.Done():
			t.Fatal("Kafka replay timed out")
		case <-time.After(100 * time.Millisecond):
		}
	}
	count := localSQL(t, origin, "SELECT uniqExact(tuple(attribute_key, attribute_type)) FROM "+database+".observed_attribute_keys")
	if strings.TrimSpace(count) != "8" {
		t.Fatal("partial replay duplicated/lost logical keys", count)
	}
}

func TestKafkaRejectsLegacyWireWithoutCommittingPastIt(t *testing.T) {
	cfg := localKafka(t)
	origin, database, sink := localCatalog(t)
	client, err := kgo.NewClient(kgo.SeedBrokers(cfg.Brokers...), kgo.RequiredAcks(kgo.AllISRAcks()))
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	if err := client.ProduceSync(ctx, &kgo.Record{Topic: cfg.Topic, Value: []byte(`{"catalog_epoch":1,"catalog_revision":3}`)}).FirstErr(); err != nil {
		t.Fatal(err)
	}
	for attempt := 0; attempt < 2; attempt++ {
		consumer, err := observedcatalog.NewConsumer(cfg, sink)
		if err != nil {
			t.Fatal(err)
		}
		err = consumer.Run(ctx)
		consumer.Close()
		if err == nil || !strings.Contains(err.Error(), "poison record") {
			t.Fatal("legacy record was skipped/accepted", err)
		}
	}
	if count := localSQL(t, origin, "SELECT count() FROM "+database+".observed_attribute_keys"); strings.TrimSpace(count) != "0" {
		t.Fatal("legacy bytes reached new indexes", count)
	}
}
