package catalogkafka

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"strings"
	"time"
	"unicode"

	"github.com/twmb/franz-go/pkg/kgo"
)

const (
	defaultProducerClientID      = "fi-collector-catalog-producer"
	defaultConsumerClientID      = "fi-collector-catalog-consumer"
	defaultRecordDeliveryTimeout = 10 * time.Second
	maxRecordDeliveryTimeout     = 10 * time.Second
	maxKafkaCommitTimeout        = 10 * time.Second
	maxKafkaBrokers              = 16
	maxKafkaIdentityBytes        = 255
	kafkaBatchOverheadAllowance  = 1024
	kafkaFetchOverheadAllowance  = 64 << 10
)

type FranzProducerConfig struct {
	Brokers         []string
	Topic           string
	ClientID        string
	DeliveryTimeout time.Duration
}

type FranzConsumerConfig struct {
	Brokers                    []string
	Topic                      string
	GroupID                    string
	ClientID                   string
	AssignmentCheckpointLoader CheckpointLoader
}

// NewFranzProducer creates but does not otherwise activate a Go-1.24-compatible
// franz-go client. All-ISR acknowledgements and idempotent production are
// mandatory; the returned Producer waits synchronously for every result.
func NewFranzProducer(cfg FranzProducerConfig) (*Producer, error) {
	if err := validateFranzCommon(cfg.Brokers, cfg.Topic, &cfg.ClientID, defaultProducerClientID); err != nil {
		return nil, err
	}
	if cfg.DeliveryTimeout == 0 {
		cfg.DeliveryTimeout = defaultRecordDeliveryTimeout
	}
	if cfg.DeliveryTimeout < 0 || cfg.DeliveryTimeout > maxRecordDeliveryTimeout {
		return nil, fmt.Errorf("catalogkafka: delivery timeout must be in (0,%s]", maxRecordDeliveryTimeout)
	}
	client, err := kgo.NewClient(franzProducerOptions(cfg)...)
	if err != nil {
		return nil, fmt.Errorf("catalogkafka: create franz producer: %w", err)
	}
	producer, err := NewProducer(cfg.Topic, &franzWriter{client: client})
	if err != nil {
		client.Close()
		return nil, err
	}
	return producer, nil
}

// NewFranzConsumer constructs a one-record, manual-commit group consumer. The
// caller must invoke Run/ProcessOne; construction alone starts no delivery.
func NewFranzConsumer(
	cfg FranzConsumerConfig, handler DeliveryHandler, validator *SequenceValidator,
) (*Consumer, error) {
	if err := validateFranzCommon(cfg.Brokers, cfg.Topic, &cfg.ClientID, defaultConsumerClientID); err != nil {
		return nil, err
	}
	if err := validateKafkaIdentity("consumer group", cfg.GroupID); err != nil {
		return nil, err
	}
	if handler == nil || validator == nil {
		return nil, errors.New("catalogkafka: franz consumer requires a handler and sequence validator")
	}
	failure := &stickyConsumerError{}
	client, err := kgo.NewClient(franzConsumerRuntimeOptions(cfg, validator, failure)...)
	if err != nil {
		return nil, fmt.Errorf("catalogkafka: create franz consumer: %w", err)
	}
	consumer, err := NewConsumer(
		cfg.Topic, &franzSource{client: client, failure: failure}, handler, validator,
	)
	if err != nil {
		client.CloseAllowingRebalance()
		return nil, err
	}
	return consumer, nil
}

func franzProducerOptions(cfg FranzProducerConfig) []kgo.Opt {
	return []kgo.Opt{
		kgo.SeedBrokers(append([]string(nil), cfg.Brokers...)...),
		kgo.ClientID(cfg.ClientID),
		kgo.RequiredAcks(kgo.AllISRAcks()),
		// Idempotent writes are enabled by franz-go unless explicitly disabled;
		// this package exposes no option capable of disabling them.
		kgo.RecordDeliveryTimeout(cfg.DeliveryTimeout),
		kgo.RecordPartitioner(kgo.StickyKeyPartitioner(nil)),
		kgo.ProducerBatchMaxBytes(int32(MaxRecordBytes + kafkaBatchOverheadAllowance)),
		kgo.MaxBufferedRecords(1),
		kgo.MaxBufferedBytes(MaxRecordBytes),
		kgo.BrokerMaxWriteBytes(int32(MaxRecordBytes + kafkaFetchOverheadAllowance)),
	}
}

func franzConsumerOptions(cfg FranzConsumerConfig) []kgo.Opt {
	fetchBytes := int32(MaxRecordBytes + kafkaFetchOverheadAllowance)
	return []kgo.Opt{
		kgo.SeedBrokers(append([]string(nil), cfg.Brokers...)...),
		kgo.ClientID(cfg.ClientID),
		kgo.ConsumeTopics(cfg.Topic),
		kgo.ConsumerGroup(cfg.GroupID),
		kgo.DisableAutoCommit(),
		kgo.BlockRebalanceOnPoll(),
		kgo.ConsumeStartOffset(kgo.NewOffset().AtStart()),
		kgo.ConsumeResetOffset(kgo.NewOffset().AtStart()),
		kgo.FetchMaxPartitionBytes(fetchBytes),
		kgo.FetchMaxBytes(fetchBytes),
		kgo.MaxConcurrentFetches(1),
		kgo.BrokerMaxReadBytes(fetchBytes * 2),
	}
}

func franzConsumerRuntimeOptions(
	cfg FranzConsumerConfig,
	validator *SequenceValidator,
	failure *stickyConsumerError,
) []kgo.Opt {
	options := franzConsumerOptions(cfg)
	if cfg.AssignmentCheckpointLoader == nil {
		return options
	}
	refresh := newAssignmentCheckpointRefresher(
		cfg.AssignmentCheckpointLoader, validator, failure,
	)
	return append(options, kgo.AdjustFetchOffsetsFn(func(
		ctx context.Context,
		offsets map[string]map[int32]kgo.Offset,
	) (map[string]map[int32]kgo.Offset, error) {
		if err := refresh.Refresh(ctx); err != nil {
			return nil, err
		}
		return offsets, nil
	}))
}

type franzWriter struct{ client *kgo.Client }

var _ RecordWriter = (*franzWriter)(nil)

func (w *franzWriter) WriteRecord(ctx context.Context, record Record) error {
	if w == nil || w.client == nil {
		return errors.New("catalogkafka: nil franz writer")
	}
	result := w.client.ProduceSync(ctx, &kgo.Record{
		Topic: record.Topic, Key: bytes.Clone(record.Key), Value: bytes.Clone(record.Value),
	})
	return result.FirstErr()
}

func (w *franzWriter) Close() {
	if w != nil && w.client != nil {
		w.client.Close()
	}
}

type franzSource struct {
	client  *kgo.Client
	failure *stickyConsumerError
}

var _ ManualRecordSource = (*franzSource)(nil)

func (s *franzSource) PollOne(ctx context.Context) (Record, error) {
	if s == nil || s.client == nil {
		return Record{}, errors.New("catalogkafka: nil franz source")
	}
	for {
		if err := s.failure.Err(); err != nil {
			return Record{}, err
		}
		fetches := s.client.PollRecords(ctx, 1)
		if err := s.failure.Err(); err != nil {
			return Record{}, err
		}
		if err := fetches.Err0(); err != nil {
			return Record{}, err
		}
		records := fetches.Records()
		if len(records) == 0 {
			if err := ctx.Err(); err != nil {
				return Record{}, err
			}
			// Group housekeeping can wake PollRecords without a data record.
			// Continue polling instead of terminating an otherwise healthy worker.
			continue
		}
		if len(records) != 1 {
			return Record{}, fmt.Errorf(
				"catalogkafka: franz poll returned %d records with a one-record limit",
				len(records),
			)
		}
		record := records[0]
		return Record{
			Topic: record.Topic, Key: bytes.Clone(record.Key), Value: bytes.Clone(record.Value),
			Partition: record.Partition, Offset: record.Offset, LeaderEpoch: record.LeaderEpoch,
			native: record,
		}, nil
	}
}

func (s *franzSource) Commit(ctx context.Context, record Record) error {
	if s == nil || s.client == nil {
		return errors.New("catalogkafka: nil franz source")
	}
	if err := s.failure.Err(); err != nil {
		return err
	}
	if ctx == nil {
		return errors.New("catalogkafka: nil Kafka commit context")
	}
	native, ok := record.native.(*kgo.Record)
	if !ok || native == nil {
		native = &kgo.Record{
			Topic: record.Topic, Partition: record.Partition, Offset: record.Offset,
			LeaderEpoch: record.LeaderEpoch,
		}
	}
	commitCtx, cancel := context.WithTimeout(ctx, maxKafkaCommitTimeout)
	defer cancel()
	return s.client.CommitRecords(commitCtx, native)
}

func (s *franzSource) AllowRebalance() {
	if s != nil && s.client != nil {
		s.client.AllowRebalance()
	}
}

func (s *franzSource) Close() {
	if s != nil && s.client != nil {
		s.client.CloseAllowingRebalance()
	}
}

func validateFranzCommon(brokers []string, topic string, clientID *string, defaultClientID string) error {
	if len(brokers) == 0 || len(brokers) > maxKafkaBrokers {
		return fmt.Errorf("catalogkafka: broker count must be in (0,%d]", maxKafkaBrokers)
	}
	seen := make(map[string]struct{}, len(brokers))
	for index, broker := range brokers {
		if err := validateKafkaIdentity("broker", broker); err != nil {
			return fmt.Errorf("broker %d: %w", index, err)
		}
		if strings.Contains(broker, "://") || strings.ContainsAny(broker, "/?#") {
			return fmt.Errorf("catalogkafka: broker %d must be a host[:port], not a URL", index)
		}
		if _, exists := seen[broker]; exists {
			return fmt.Errorf("catalogkafka: duplicate broker %q", broker)
		}
		seen[broker] = struct{}{}
	}
	if err := validateTopic(topic); err != nil {
		return err
	}
	if *clientID == "" {
		*clientID = defaultClientID
	}
	return validateKafkaIdentity("client ID", *clientID)
}

func validateKafkaIdentity(name, value string) error {
	if value == "" || len(value) > maxKafkaIdentityBytes || strings.TrimSpace(value) != value {
		return fmt.Errorf("catalogkafka: %s must be non-empty, bounded, and have no surrounding whitespace", name)
	}
	for _, char := range value {
		if unicode.IsControl(char) {
			return fmt.Errorf("catalogkafka: %s must not contain control characters", name)
		}
	}
	return nil
}
