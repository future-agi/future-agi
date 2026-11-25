package observedcatalog

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
)

type Publisher interface {
	Publish(context.Context, Batch) error
}
type Sink interface {
	Insert(context.Context, Batch) error
}

type KafkaConfig struct {
	Brokers  []string
	Topic    string
	Group    string
	ClientID string
	Timeout  time.Duration
}

func (c KafkaConfig) normalized() (KafkaConfig, error) {
	if c.Topic == "" {
		c.Topic = DefaultTopic
	}
	if c.Group == "" {
		c.Group = DefaultGroup
	}
	if c.ClientID == "" {
		c.ClientID = "fi-observed-catalog"
	}
	if c.Timeout == 0 {
		c.Timeout = 10 * time.Second
	}
	if !validTopic(c.Topic) || c.Group == "" || len(c.Group) > 255 || strings.TrimSpace(c.Group) != c.Group || strings.ContainsAny(c.Group, "\n\r\t\x00") {
		return c, errors.New("observedcatalog: invalid Kafka topic or group")
	}
	if len(c.Brokers) == 0 || len(c.Brokers) > 16 || c.Timeout <= 0 || c.Timeout > 30*time.Second {
		return c, errors.New("observedcatalog: invalid Kafka brokers or timeout")
	}
	for _, broker := range c.Brokers {
		if broker == "" || len(broker) > 255 || strings.TrimSpace(broker) != broker || strings.ContainsAny(broker, "/\n\r\t") {
			return c, errors.New("observedcatalog: invalid Kafka broker")
		}
	}
	if len(c.ClientID) > 255 || strings.TrimSpace(c.ClientID) != c.ClientID {
		return c, errors.New("observedcatalog: invalid Kafka client ID")
	}
	return c, nil
}

func validTopic(topic string) bool {
	if topic == "" || len(topic) > 249 || topic == "." || topic == ".." {
		return false
	}
	for _, c := range topic {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '.' || c == '_' || c == '-') {
			return false
		}
	}
	return true
}

type Producer struct {
	client *kgo.Client
	cfg    KafkaConfig
}

func NewProducer(cfg KafkaConfig) (*Producer, error) {
	cfg, err := cfg.normalized()
	if err != nil {
		return nil, err
	}
	client, err := kgo.NewClient(kgo.SeedBrokers(cfg.Brokers...), kgo.ClientID(cfg.ClientID), kgo.RequiredAcks(kgo.AllISRAcks()), kgo.RecordDeliveryTimeout(cfg.Timeout), kgo.MaxBufferedRecords(8), kgo.MaxBufferedBytes(8*MaxRecordBytes), kgo.ProducerBatchMaxBytes(MaxRecordBytes+1024))
	if err != nil {
		return nil, err
	}
	return &Producer{client: client, cfg: cfg}, nil
}

// Publish acknowledges every chunk before returning. If a later chunk fails,
// callers must retry the batch; repeated earlier chunks are harmless.
func (p *Producer) Publish(ctx context.Context, batch Batch) error {
	chunks, err := Chunk(batch)
	if err != nil {
		return err
	}
	for _, chunk := range chunks {
		raw, err := Encode(chunk)
		if err != nil {
			return err
		}
		call, cancel := context.WithTimeout(ctx, p.cfg.Timeout)
		err = p.client.ProduceSync(call, &kgo.Record{Topic: p.cfg.Topic, Value: raw}).FirstErr()
		cancel()
		if err != nil {
			return errors.New("observedcatalog: Kafka publication failed; retained for retry")
		}
	}
	return nil
}

func (p *Producer) Close() {
	if p != nil && p.client != nil {
		p.client.Close()
	}
}

// RecordSource is the ordinary manual-commit seam, also used by fault tests.
type RecordSource interface {
	PollRecords(context.Context, int) kgo.Fetches
	CommitRecords(context.Context, ...*kgo.Record) error
	AllowRebalance()
	CloseAllowingRebalance()
}

type Consumer struct {
	source RecordSource
	sink   Sink
	cfg    KafkaConfig
}

func NewConsumer(cfg KafkaConfig, sink Sink) (*Consumer, error) {
	cfg, err := cfg.normalized()
	if err != nil {
		return nil, err
	}
	if sink == nil {
		return nil, errors.New("observedcatalog: sink required")
	}
	// One record performs at most two 30-second sink requests and a
	// 30-second commit. Leave another 30 seconds for rebalance headroom.
	client, err := kgo.NewClient(kgo.SeedBrokers(cfg.Brokers...), kgo.ClientID(cfg.ClientID), kgo.ConsumeTopics(cfg.Topic), kgo.ConsumerGroup(cfg.Group), kgo.DisableAutoCommit(), kgo.BlockRebalanceOnPoll(), kgo.ConsumeStartOffset(kgo.NewOffset().AtStart()), kgo.ConsumeResetOffset(kgo.NoResetOffset()), kgo.FetchMaxBytes(4*MaxRecordBytes), kgo.FetchMaxPartitionBytes(MaxRecordBytes+1024), kgo.MaxConcurrentFetches(1), kgo.BrokerMaxReadBytes(8*MaxRecordBytes), kgo.RebalanceTimeout(2*time.Minute))
	if err != nil {
		return nil, err
	}
	return &Consumer{source: client, sink: sink, cfg: cfg}, nil
}

// processOnce never commits past a failed record. Run exits on any error so
// restart begins at the committed offset, not the client's advanced fetch cursor.
func (c *Consumer) processOnce(ctx context.Context) error {
	fetches := c.source.PollRecords(ctx, 1)
	defer c.source.AllowRebalance()
	if err := fetches.Err(); err != nil {
		return err
	}
	records := fetches.Records()
	if len(records) == 0 {
		return ctx.Err()
	}
	if len(records) > 1 {
		return errors.New("observedcatalog: poll exceeded record bound")
	}
	// Validate the entire fetched batch before any writes.
	batches := make([]Batch, 0, len(records))
	for _, record := range records {
		if record.Topic != c.cfg.Topic || record.Offset < 0 || record.Partition < 0 {
			return errors.New("observedcatalog: invalid Kafka coordinates")
		}
		batch, err := Decode(record.Value)
		if err != nil {
			return fmt.Errorf("observedcatalog: poison record partition=%d offset=%d: %w", record.Partition, record.Offset, err)
		}
		batches = append(batches, batch)
	}
	// The Kafka timeout is not a shared budget for ClickHouse plus commit.
	// A decoded record fits one sink chunk (keys then values); each request
	// is independently bounded by ClickHouseConfig.Timeout <= 30 seconds.
	work, cancel := context.WithTimeout(ctx, 2*maxClickHouseRequestTimeout)
	for _, batch := range batches {
		if err := c.sink.Insert(work, batch); err != nil {
			cancel()
			return err
		}
	}
	err := work.Err()
	cancel()
	if err != nil {
		return err
	}
	commit, stop := context.WithTimeout(ctx, c.cfg.Timeout)
	defer stop()
	return c.source.CommitRecords(commit, records...)
}

func (c *Consumer) Run(ctx context.Context) error {
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		if err := c.processOnce(ctx); err != nil {
			return err
		}
	}
}
func (c *Consumer) Close() {
	if c != nil && c.source != nil {
		c.source.CloseAllowingRebalance()
	}
}
