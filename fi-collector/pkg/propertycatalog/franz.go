package propertycatalog

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogkafka"
	"github.com/twmb/franz-go/pkg/kgo"
)

const (
	defaultFranzProducerClientID = "fi-collector-property-catalog-v1-dev"
	kafkaRecordOverheadAllowance = 64 << 10
)

type FranzProducerConfig struct {
	Brokers         []string
	Topic           string
	ClientID        string
	DeliveryTimeout time.Duration
}

type FranzConsumerConfig struct {
	Brokers          []string
	Topic            string
	GroupID          string
	ClientID         string
	CheckpointLoader CheckpointLoader
}

// NewFranzProducer constructs the dedicated unified-catalog producer. Kafka
// acknowledgements are all-ISR and synchronous, franz idempotence remains
// mandatory, and buffering is bounded to one envelope.
func NewFranzProducer(cfg FranzProducerConfig) (*Producer, error) {
	if len(cfg.Brokers) == 0 || len(cfg.Brokers) > MaxKafkaBrokers {
		return nil, errors.New("propertycatalog: Franz producer requires 1..16 brokers")
	}
	for _, broker := range cfg.Brokers {
		if broker == "" || strings.TrimSpace(broker) != broker || len(broker) > MaxKafkaIdentityBytes {
			return nil, errors.New("propertycatalog: Franz broker is empty, padded, or too long")
		}
	}
	if err := validateTopic(cfg.Topic); err != nil {
		return nil, err
	}
	if cfg.ClientID == "" {
		cfg.ClientID = defaultFranzProducerClientID
	}
	if strings.TrimSpace(cfg.ClientID) != cfg.ClientID || len(cfg.ClientID) > MaxKafkaIdentityBytes {
		return nil, errors.New("propertycatalog: Franz client ID is padded or too long")
	}
	if cfg.DeliveryTimeout == 0 {
		cfg.DeliveryTimeout = DefaultDeliveryTransportTimeout
	}
	if cfg.DeliveryTimeout < 0 || cfg.DeliveryTimeout > MaxDeliveryTimeout {
		return nil, fmt.Errorf(
			"propertycatalog: Franz delivery timeout must be in (0,%s]",
			MaxDeliveryTimeout,
		)
	}
	client, err := kgo.NewClient(franzProducerOptions(cfg)...)
	if err != nil {
		return nil, fmt.Errorf("propertycatalog: create Franz producer: %w", err)
	}
	producer, err := NewProducer(cfg.Topic, &franzRecordWriter{client: client})
	if err != nil {
		client.Close()
		return nil, err
	}
	return producer, nil
}

func franzProducerOptions(cfg FranzProducerConfig) []kgo.Opt {
	return []kgo.Opt{
		kgo.SeedBrokers(append([]string(nil), cfg.Brokers...)...),
		kgo.ClientID(cfg.ClientID),
		kgo.RequiredAcks(kgo.AllISRAcks()),
		kgo.RecordDeliveryTimeout(cfg.DeliveryTimeout),
		kgo.RecordPartitioner(kgo.StickyKeyPartitioner(nil)),
		kgo.ProducerBatchMaxBytes(int32(MaxRecordBytes + kafkaRecordOverheadAllowance)),
		kgo.MaxBufferedRecords(1),
		kgo.MaxBufferedBytes(MaxRecordBytes),
		kgo.BrokerMaxWriteBytes(int32(MaxRecordBytes + kafkaRecordOverheadAllowance)),
	}
}

type franzRecordWriter struct{ client *kgo.Client }

var _ catalogkafka.RecordWriter = (*franzRecordWriter)(nil)

func (w *franzRecordWriter) WriteRecord(ctx context.Context, record catalogkafka.Record) error {
	if w == nil || w.client == nil || ctx == nil {
		return errors.New("propertycatalog: nil Franz writer context")
	}
	result := w.client.ProduceSync(ctx, &kgo.Record{
		Topic: record.Topic, Key: bytes.Clone(record.Key), Value: bytes.Clone(record.Value),
	})
	return result.FirstErr()
}

func (w *franzRecordWriter) Close() {
	if w != nil && w.client != nil {
		w.client.Close()
	}
}

// NewFranzConsumer constructs a one-record, manual-commit group consumer.
// Assignment refresh reloads the dedicated delivery ledger before accepting a
// record, so a rebalance cannot silently forget an already committed stream.
func NewFranzConsumer(
	cfg FranzConsumerConfig,
	handler Handler,
	validator *SequenceValidator,
) (*Consumer, error) {
	producerLike := FranzProducerConfig{Brokers: cfg.Brokers, Topic: cfg.Topic, ClientID: cfg.ClientID}
	if producerLike.ClientID == "" {
		producerLike.ClientID = "fi-property-catalog-consumer-v1-dev"
	}
	if len(producerLike.Brokers) == 0 || len(producerLike.Brokers) > MaxKafkaBrokers {
		return nil, errors.New("propertycatalog: Franz consumer requires 1..16 brokers")
	}
	for _, broker := range producerLike.Brokers {
		if broker == "" || strings.TrimSpace(broker) != broker || len(broker) > MaxKafkaIdentityBytes {
			return nil, errors.New("propertycatalog: Franz broker is empty, padded, or too long")
		}
	}
	if err := validateTopic(cfg.Topic); err != nil {
		return nil, err
	}
	if !safeKafkaIdentity(cfg.GroupID) || !safeKafkaIdentity(producerLike.ClientID) {
		return nil, errors.New("propertycatalog: Franz consumer group/client identity is invalid")
	}
	if handler == nil || validator == nil {
		return nil, errors.New("propertycatalog: Franz consumer requires a handler and validator")
	}
	failure := &franzStickyError{}
	options := []kgo.Opt{
		kgo.SeedBrokers(append([]string(nil), cfg.Brokers...)...),
		kgo.ClientID(producerLike.ClientID), kgo.ConsumeTopics(cfg.Topic), kgo.ConsumerGroup(cfg.GroupID),
		kgo.DisableAutoCommit(), kgo.BlockRebalanceOnPoll(), kgo.ConsumeStartOffset(kgo.NewOffset().AtStart()),
		kgo.ConsumeResetOffset(kgo.NewOffset().AtStart()),
		kgo.FetchMaxPartitionBytes(int32(MaxRecordBytes + kafkaRecordOverheadAllowance)),
		kgo.FetchMaxBytes(int32(MaxRecordBytes + kafkaRecordOverheadAllowance)),
		kgo.MaxConcurrentFetches(1),
		kgo.BrokerMaxReadBytes(int32(MaxRecordBytes + 2*kafkaRecordOverheadAllowance)),
	}
	if cfg.CheckpointLoader != nil {
		options = append(options, kgo.OnPartitionsAssigned(func(ctx context.Context, _ *kgo.Client, _ map[string][]int32) {
			checkpoints, err := cfg.CheckpointLoader.LoadCheckpoints(ctx)
			if err == nil {
				err = validator.MergeCheckpoints(checkpoints)
			}
			failure.Set(err)
		}))
	}
	client, err := kgo.NewClient(options...)
	if err != nil {
		return nil, fmt.Errorf("propertycatalog: create Franz consumer: %w", err)
	}
	consumer, err := NewConsumer(
		cfg.Topic, &franzManualSource{client: client, failure: failure}, handler, validator,
	)
	if err != nil {
		client.CloseAllowingRebalance()
		return nil, err
	}
	return consumer, nil
}

type franzStickyError struct {
	mu  sync.Mutex
	err error
}

func (e *franzStickyError) Set(err error) {
	if err == nil {
		return
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.err == nil {
		e.err = err
	}
}

func (e *franzStickyError) Err() error {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.err
}

type franzManualSource struct {
	client  *kgo.Client
	failure *franzStickyError
	last    *kgo.Record
}

var _ catalogkafka.ManualRecordSource = (*franzManualSource)(nil)

func (s *franzManualSource) PollOne(ctx context.Context) (catalogkafka.Record, error) {
	if s == nil || s.client == nil || ctx == nil {
		return catalogkafka.Record{}, errors.New("propertycatalog: nil Franz source context")
	}
	for {
		if err := s.failure.Err(); err != nil {
			return catalogkafka.Record{}, err
		}
		fetches := s.client.PollRecords(ctx, 1)
		if err := s.failure.Err(); err != nil {
			return catalogkafka.Record{}, err
		}
		if err := fetches.Err0(); err != nil {
			return catalogkafka.Record{}, err
		}
		records := fetches.Records()
		if len(records) == 0 {
			if err := ctx.Err(); err != nil {
				return catalogkafka.Record{}, err
			}
			continue
		}
		if len(records) != 1 {
			return catalogkafka.Record{}, errors.New("propertycatalog: Franz one-record poll returned multiple records")
		}
		s.last = records[0]
		return catalogkafka.Record{
			Topic: s.last.Topic, Key: bytes.Clone(s.last.Key), Value: bytes.Clone(s.last.Value),
			Partition: s.last.Partition, Offset: s.last.Offset, LeaderEpoch: s.last.LeaderEpoch,
		}, nil
	}
}

func (s *franzManualSource) Commit(ctx context.Context, record catalogkafka.Record) error {
	if s == nil || s.client == nil || s.last == nil || ctx == nil {
		return errors.New("propertycatalog: Franz commit has no matching polled record")
	}
	if err := s.failure.Err(); err != nil {
		return err
	}
	if record.Topic != s.last.Topic || record.Partition != s.last.Partition || record.Offset != s.last.Offset ||
		!bytes.Equal(record.Key, s.last.Key) || !bytes.Equal(record.Value, s.last.Value) {
		return errors.New("propertycatalog: Franz commit coordinates do not match the polled record")
	}
	if err := s.client.CommitRecords(ctx, s.last); err != nil {
		return err
	}
	s.last = nil
	return nil
}

func (s *franzManualSource) AllowRebalance() {
	if s != nil && s.client != nil {
		s.client.AllowRebalance()
	}
}

func (s *franzManualSource) Close() {
	if s != nil && s.client != nil {
		s.client.CloseAllowingRebalance()
	}
}

func safeKafkaIdentity(value string) bool {
	if value == "" || len(value) > MaxKafkaIdentityBytes || strings.TrimSpace(value) != value {
		return false
	}
	for _, char := range value {
		if char < 0x20 || char == 0x7f {
			return false
		}
	}
	return true
}
