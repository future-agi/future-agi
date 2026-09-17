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
	defaultFranzProducerClientID   = "fi-property-catalog-sequencer-output-v1-dev"
	defaultCandidateClientID       = "fi-collector-property-candidate-v1-dev"
	defaultKafkaTransactionTimeout = 30 * time.Second
	maxKafkaTransactionTimeout     = 2 * time.Minute
	kafkaRecordOverheadAllowance   = 64 << 10
)

type FranzProducerConfig struct {
	Brokers            []string
	Topic              string
	ClientID           string
	DeliveryTimeout    time.Duration
	TransactionalID    string
	TransactionTimeout time.Duration
}

type FranzConsumerConfig struct {
	Brokers          []string
	Topic            string
	GroupID          string
	ClientID         string
	CheckpointLoader CheckpointLoader
}

type FranzCandidateSourceConfig struct {
	Brokers    []string
	Topic      string
	GroupID    string
	ClientID   string
	InstanceID string
}

// NewFranzProducer constructs the dedicated unified-catalog producer. Kafka
// acknowledgements are all-ISR and synchronous, franz idempotence remains
// mandatory, and buffering is bounded to one envelope.
func NewFranzProducer(cfg FranzProducerConfig) (*Producer, error) {
	if err := normalizeFranzProducerConfig(&cfg, defaultFranzProducerClientID); err != nil {
		return nil, err
	}
	client, err := kgo.NewClient(franzProducerOptions(cfg)...)
	if err != nil {
		return nil, fmt.Errorf("propertycatalog: create Franz producer: %w", err)
	}
	producer, err := NewProducer(cfg.Topic, &franzRecordWriter{
		client: client, transactional: cfg.TransactionalID != "",
		transactionTimeout: cfg.TransactionTimeout,
	})
	if err != nil {
		client.Close()
		return nil, err
	}
	return producer, nil
}

func ValidateFranzProducerConfig(cfg FranzProducerConfig) error {
	return normalizeFranzProducerConfig(&cfg, defaultFranzProducerClientID)
}

// NewFranzCandidateProducer constructs the autoscaled collector-side producer.
// Candidate production is idempotent and all-ISR acknowledged, but deliberately
// non-transactional: only the singleton ordered-output producer owns a fixed
// transactional identity.
func NewFranzCandidateProducer(cfg FranzProducerConfig) (*CandidateProducer, error) {
	if cfg.TransactionalID != "" {
		return nil, errors.New("propertycatalog: candidate producer rejects a transactional identity")
	}
	if err := normalizeFranzProducerConfig(&cfg, defaultCandidateClientID); err != nil {
		return nil, err
	}
	client, err := kgo.NewClient(franzProducerOptionsWithLimit(cfg, MaxCandidateRecordBytes)...)
	if err != nil {
		return nil, fmt.Errorf("propertycatalog: create Franz candidate producer: %w", err)
	}
	producer, err := NewCandidateProducer(cfg.Topic, &franzRecordWriter{client: client})
	if err != nil {
		client.Close()
		return nil, err
	}
	return producer, nil
}

func normalizeFranzProducerConfig(cfg *FranzProducerConfig, defaultClientID string) error {
	if cfg == nil || len(cfg.Brokers) == 0 || len(cfg.Brokers) > MaxKafkaBrokers {
		return errors.New("propertycatalog: Franz producer requires 1..16 brokers")
	}
	for _, broker := range cfg.Brokers {
		if !safeKafkaIdentity(broker) {
			return errors.New("propertycatalog: Franz broker is empty, padded, or too long")
		}
	}
	if err := validateTopic(cfg.Topic); err != nil {
		return err
	}
	if cfg.ClientID == "" {
		cfg.ClientID = defaultClientID
	}
	if !safeKafkaIdentity(cfg.ClientID) {
		return errors.New("propertycatalog: Franz client ID is empty, padded, or too long")
	}
	if cfg.DeliveryTimeout == 0 {
		cfg.DeliveryTimeout = DefaultDeliveryTransportTimeout
	}
	if cfg.DeliveryTimeout < 0 || cfg.DeliveryTimeout > MaxDeliveryTimeout {
		return fmt.Errorf("propertycatalog: Franz delivery timeout must be in (0,%s]", MaxDeliveryTimeout)
	}
	if cfg.TransactionalID != "" {
		if !safeKafkaIdentity(cfg.TransactionalID) {
			return errors.New("propertycatalog: Franz transactional identity is invalid")
		}
		if cfg.TransactionTimeout == 0 {
			cfg.TransactionTimeout = defaultKafkaTransactionTimeout
		}
		if cfg.TransactionTimeout <= 0 || cfg.TransactionTimeout > maxKafkaTransactionTimeout {
			return fmt.Errorf("propertycatalog: Kafka transaction timeout must be in (0,%s]", maxKafkaTransactionTimeout)
		}
	} else if cfg.TransactionTimeout != 0 {
		return errors.New("propertycatalog: transaction timeout requires a transactional identity")
	}
	return nil
}

func franzProducerOptions(cfg FranzProducerConfig) []kgo.Opt {
	return franzProducerOptionsWithLimit(cfg, MaxRecordBytes)
}

func franzProducerOptionsWithLimit(cfg FranzProducerConfig, maxRecordBytes int) []kgo.Opt {
	options := []kgo.Opt{
		kgo.SeedBrokers(append([]string(nil), cfg.Brokers...)...),
		kgo.ClientID(cfg.ClientID),
		kgo.RequiredAcks(kgo.AllISRAcks()),
		kgo.RecordDeliveryTimeout(cfg.DeliveryTimeout),
		kgo.RecordPartitioner(kgo.StickyKeyPartitioner(nil)),
		kgo.ProducerBatchMaxBytes(int32(maxRecordBytes + kafkaRecordOverheadAllowance)),
		kgo.MaxBufferedRecords(1),
		kgo.MaxBufferedBytes(maxRecordBytes),
		kgo.BrokerMaxWriteBytes(int32(maxRecordBytes + kafkaRecordOverheadAllowance)),
	}
	if cfg.TransactionalID != "" {
		options = append(options, kgo.TransactionalID(cfg.TransactionalID), kgo.TransactionTimeout(cfg.TransactionTimeout))
	}
	return options
}

type franzRecordWriter struct {
	client             *kgo.Client
	transactional      bool
	transactionTimeout time.Duration
	mu                 sync.Mutex
}

var _ catalogkafka.RecordWriter = (*franzRecordWriter)(nil)

func (w *franzRecordWriter) WriteRecord(ctx context.Context, record catalogkafka.Record) error {
	if w == nil || w.client == nil || ctx == nil {
		return errors.New("propertycatalog: nil Franz writer context")
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.transactional {
		if err := w.client.BeginTransaction(); err != nil {
			return fmt.Errorf("propertycatalog: begin ordered-output transaction: %w", err)
		}
	}
	result := w.client.ProduceSync(ctx, &kgo.Record{
		Topic: record.Topic, Key: bytes.Clone(record.Key), Value: bytes.Clone(record.Value),
	})
	if produceErr := result.FirstErr(); produceErr != nil {
		if w.transactional {
			abortCtx, cancel := context.WithTimeout(context.Background(), w.transactionTimeout)
			abortErr := w.client.EndTransaction(abortCtx, kgo.TryAbort)
			cancel()
			return errors.Join(produceErr, abortErr)
		}
		return produceErr
	}
	if w.transactional {
		if err := w.client.EndTransaction(ctx, kgo.TryCommit); err != nil {
			return fmt.Errorf("propertycatalog: commit ordered-output transaction: %w", err)
		}
	}
	return nil
}

func (w *franzRecordWriter) FenceOwner(ctx context.Context) error {
	if w == nil || w.client == nil || ctx == nil || !w.transactional {
		return errors.New("propertycatalog: owner fence requires a transactional Franz writer")
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	if err := w.client.Ping(ctx); err != nil {
		return fmt.Errorf("propertycatalog: reach Kafka before owner fence: %w", err)
	}
	if err := w.client.BeginTransaction(); err != nil {
		return fmt.Errorf("propertycatalog: establish transactional owner epoch: %w", err)
	}
	if err := w.client.EndTransaction(ctx, kgo.TryAbort); err != nil {
		return fmt.Errorf("propertycatalog: finish transactional owner fence: %w", err)
	}
	return nil
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
		kgo.FetchIsolationLevel(kgo.ReadCommitted()),
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

// NewFranzCandidateSource constructs the singleton's one-record, manual-commit
// candidate source. A fixed InstanceID lets Kafka fence a stale singleton;
// using an empty or process-random identity is rejected.
func NewFranzCandidateSource(cfg FranzCandidateSourceConfig) (catalogkafka.ManualRecordSource, error) {
	if err := ValidateFranzCandidateSourceConfig(cfg); err != nil {
		return nil, err
	}
	failure := &franzStickyError{}
	fetchBytes := int32(MaxCandidateRecordBytes + kafkaRecordOverheadAllowance)
	client, err := kgo.NewClient(
		kgo.SeedBrokers(append([]string(nil), cfg.Brokers...)...),
		kgo.ClientID(cfg.ClientID), kgo.ConsumeTopics(cfg.Topic), kgo.ConsumerGroup(cfg.GroupID),
		kgo.InstanceID(cfg.InstanceID), kgo.DisableAutoCommit(), kgo.BlockRebalanceOnPoll(),
		kgo.ConsumeStartOffset(kgo.NewOffset().AtStart()),
		kgo.ConsumeResetOffset(kgo.NewOffset().AtStart()),
		kgo.FetchMaxPartitionBytes(fetchBytes), kgo.FetchMaxBytes(fetchBytes),
		kgo.MaxConcurrentFetches(1), kgo.BrokerMaxReadBytes(fetchBytes*2),
		kgo.FetchIsolationLevel(kgo.ReadCommitted()),
	)
	if err != nil {
		return nil, fmt.Errorf("propertycatalog: create Franz candidate source: %w", err)
	}
	return &franzManualSource{client: client, failure: failure}, nil
}

func ValidateFranzCandidateSourceConfig(cfg FranzCandidateSourceConfig) error {
	if len(cfg.Brokers) == 0 || len(cfg.Brokers) > MaxKafkaBrokers {
		return errors.New("propertycatalog: candidate source requires 1..16 brokers")
	}
	for _, broker := range cfg.Brokers {
		if !safeKafkaIdentity(broker) {
			return errors.New("propertycatalog: candidate source broker is invalid")
		}
	}
	if err := validateTopic(cfg.Topic); err != nil {
		return err
	}
	if !safeKafkaIdentity(cfg.GroupID) || !safeKafkaIdentity(cfg.ClientID) || !safeKafkaIdentity(cfg.InstanceID) {
		return errors.New("propertycatalog: candidate source group/client/instance identity is invalid")
	}
	return nil
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
