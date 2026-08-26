package propertycatalog

import (
	"errors"
	"fmt"
	"path/filepath"
	"slices"
	"strings"
	"time"
)

// RuntimeMode is intentionally narrower than the pre-release attribute
// catalog switch. The unified hot path is Kafka-only; direct ClickHouse
// delivery belongs to the bounded reconciler and is never available here.
type RuntimeMode string

const (
	RuntimeDisabled RuntimeMode = "disabled"
	RuntimeKafka    RuntimeMode = "kafka"

	DevelopmentEnvironment = "development"
	ProductionEnvironment  = "production"
	// DevelopmentAcknowledgement is deliberately long and version-specific so
	// copying only FI_PROPERTY_CATALOG_MODE cannot activate the writer.
	DevelopmentAcknowledgement = "FI_UNIFIED_PROPERTY_CATALOG_V1_DEV_ONLY"
	// ProductionAcknowledgement is deliberately distinct from the DEV gate. A
	// copied DEV deployment cannot target the production-only catalog prefix.
	ProductionAcknowledgement = "FI_UNIFIED_PROPERTY_CATALOG_V1_PRODUCTION"

	defaultReplayInterval               = time.Second
	defaultShutdownTimeout              = 10 * time.Second
	defaultQueueDepth                   = 64
	defaultMaxSpansPerBatch             = 20_000
	defaultMaxKeysPerSpan               = 128
	defaultMaxArrayMembersPerSpan       = 256
	defaultMaxEncodedBytesPerSpan       = 64 << 10
	defaultMaxChunkRows                 = 2_000
	defaultMaxChunkBytes                = 256 << 10
	defaultMaxSpoolFiles                = 10_000
	defaultMaxSpoolBytes          int64 = 512 << 20

	maxReplayInterval = 30 * time.Second
	// MaxShutdownTimeout is shared by environment parsing and runtime validation
	// so the accepted operational range has one source of truth.
	MaxShutdownTimeout            = 2 * time.Minute
	maxRuntimeQueueDepth          = 1_024
	maxRuntimeSpansPerBatch       = 100_000
	maxRuntimeKeysPerSpan         = 4_096
	maxRuntimeArrayMembers        = 16_384
	maxRuntimeSpoolFiles          = 1_000_000
	maxRuntimeSpoolBytes    int64 = 1 << 40
	maxWorkspaceAllowlist         = 256

	// MaxKafkaBrokers is the reviewed producer/consumer broker-list bound.
	MaxKafkaBrokers = 16
	// MaxKafkaIdentityBytes bounds brokers, group IDs, and client IDs.
	MaxKafkaIdentityBytes = 255
	// MaxKafkaTopicBytes is Kafka's protocol topic-name ceiling.
	MaxKafkaTopicBytes = 249
)

type KafkaRuntimeConfig struct {
	Brokers         []string      `yaml:"brokers"`
	Topic           string        `yaml:"topic"`
	ClientID        string        `yaml:"client_id"`
	DeliveryTimeout time.Duration `yaml:"delivery_timeout"`
}

// RuntimeConfig owns only collector-side hot attribute production. It has no
// ClickHouse credentials, consumer group, or generic destination table.
type RuntimeConfig struct {
	Mode                       RuntimeMode        `yaml:"mode"`
	Environment                string             `yaml:"environment"`
	DevelopmentAcknowledgement string             `yaml:"development_acknowledgement"`
	ProductionAcknowledgement  string             `yaml:"production_acknowledgement"`
	CatalogEpoch               uint16             `yaml:"catalog_epoch"`
	ProjectionVersion          uint16             `yaml:"projection_version"`
	ProducerStreamID           string             `yaml:"producer_stream_id"`
	WorkspaceAllowlist         []string           `yaml:"workspace_allowlist"`
	RevisionFenceFile          string             `yaml:"revision_fence_file"`
	SpoolDirectory             string             `yaml:"spool_directory"`
	ReplayInterval             time.Duration      `yaml:"replay_interval"`
	ShutdownTimeout            time.Duration      `yaml:"shutdown_timeout"`
	QueueDepth                 int                `yaml:"queue_depth"`
	MaxSpansPerBatch           int                `yaml:"max_spans_per_batch"`
	MaxKeysPerSpan             int                `yaml:"max_keys_per_span"`
	MaxArrayMembersPerSpan     int                `yaml:"max_array_members_per_span"`
	MaxEncodedBytesPerSpan     int                `yaml:"max_encoded_bytes_per_span"`
	MaxChunkRows               int                `yaml:"max_chunk_rows"`
	MaxChunkBytes              int                `yaml:"max_chunk_bytes"`
	MaxSpoolFiles              int                `yaml:"max_spool_files"`
	MaxSpoolBytes              int64              `yaml:"max_spool_bytes"`
	Kafka                      KafkaRuntimeConfig `yaml:"kafka"`
}

func (c RuntimeConfig) normalizedMode() RuntimeMode {
	if c.Mode == "" {
		return RuntimeDisabled
	}
	return RuntimeMode(strings.ToLower(strings.TrimSpace(string(c.Mode))))
}

func (c RuntimeConfig) WithDefaults() RuntimeConfig {
	if c.ReplayInterval == 0 {
		c.ReplayInterval = defaultReplayInterval
	}
	if c.ShutdownTimeout == 0 {
		c.ShutdownTimeout = defaultShutdownTimeout
	}
	if c.QueueDepth == 0 {
		c.QueueDepth = defaultQueueDepth
	}
	if c.MaxSpansPerBatch == 0 {
		c.MaxSpansPerBatch = defaultMaxSpansPerBatch
	}
	if c.MaxKeysPerSpan == 0 {
		c.MaxKeysPerSpan = defaultMaxKeysPerSpan
	}
	if c.MaxArrayMembersPerSpan == 0 {
		c.MaxArrayMembersPerSpan = defaultMaxArrayMembersPerSpan
	}
	if c.MaxEncodedBytesPerSpan == 0 {
		c.MaxEncodedBytesPerSpan = defaultMaxEncodedBytesPerSpan
	}
	if c.MaxChunkRows == 0 {
		c.MaxChunkRows = defaultMaxChunkRows
	}
	if c.MaxChunkBytes == 0 {
		c.MaxChunkBytes = defaultMaxChunkBytes
	}
	if c.MaxSpoolFiles == 0 {
		c.MaxSpoolFiles = defaultMaxSpoolFiles
	}
	if c.MaxSpoolBytes == 0 {
		c.MaxSpoolBytes = defaultMaxSpoolBytes
	}
	if c.Kafka.DeliveryTimeout == 0 {
		c.Kafka.DeliveryTimeout = DefaultDeliveryTransportTimeout
	}
	if c.Kafka.ClientID == "" {
		switch c.Environment {
		case DevelopmentEnvironment:
			c.Kafka.ClientID = "fi-collector-property-catalog-v1-dev"
		case ProductionEnvironment:
			c.Kafka.ClientID = "fi-collector-property-catalog-v1-prod"
		}
	}
	return c
}

func (c RuntimeConfig) Validate() error {
	mode := c.normalizedMode()
	switch mode {
	case RuntimeDisabled:
		return nil
	case RuntimeKafka:
	default:
		return fmt.Errorf("propertycatalog: invalid runtime mode %q", c.Mode)
	}
	c = c.WithDefaults()
	if err := c.validateEnvironmentAcknowledgement(); err != nil {
		return err
	}
	if c.CatalogEpoch == 0 || c.ProjectionVersion == 0 {
		return errors.New("propertycatalog: enabled runtime requires positive epoch and projection version")
	}
	if err := validateCanonicalUUID("producer stream", c.ProducerStreamID); err != nil {
		return err
	}
	if c.SpoolDirectory == "" || !filepath.IsAbs(c.SpoolDirectory) {
		return errors.New("propertycatalog: enabled runtime requires an absolute dedicated spool directory")
	}
	if c.RevisionFenceFile == "" || !filepath.IsAbs(c.RevisionFenceFile) ||
		filepath.Clean(c.RevisionFenceFile) == filepath.Clean(c.SpoolDirectory) {
		return errors.New("propertycatalog: enabled runtime requires an absolute revision fence file")
	}
	if c.ReplayInterval <= 0 || c.ReplayInterval > maxReplayInterval {
		return fmt.Errorf(
			"propertycatalog: replay interval must be in (0,%s]",
			maxReplayInterval,
		)
	}
	if c.ShutdownTimeout <= 0 || c.ShutdownTimeout > MaxShutdownTimeout {
		return fmt.Errorf(
			"propertycatalog: shutdown timeout must be in (0,%s]",
			MaxShutdownTimeout,
		)
	}
	if len(c.WorkspaceAllowlist) == 0 || len(c.WorkspaceAllowlist) > maxWorkspaceAllowlist {
		return fmt.Errorf(
			"propertycatalog: enabled runtime requires 1..%d allowlisted workspaces",
			maxWorkspaceAllowlist,
		)
	}
	if !slices.IsSorted(c.WorkspaceAllowlist) {
		return errors.New("propertycatalog: workspace allowlist must be sorted")
	}
	for index, workspaceID := range c.WorkspaceAllowlist {
		if err := validateCanonicalUUID(fmt.Sprintf("workspace allowlist %d", index), workspaceID); err != nil {
			return err
		}
		if index > 0 && workspaceID == c.WorkspaceAllowlist[index-1] {
			return errors.New("propertycatalog: workspace allowlist contains a duplicate")
		}
	}
	if c.QueueDepth < 1 || c.QueueDepth > maxRuntimeQueueDepth ||
		c.MaxSpansPerBatch < 1 || c.MaxSpansPerBatch > maxRuntimeSpansPerBatch ||
		c.MaxKeysPerSpan < 1 || c.MaxKeysPerSpan > maxRuntimeKeysPerSpan ||
		c.MaxArrayMembersPerSpan < 1 || c.MaxArrayMembersPerSpan > maxRuntimeArrayMembers ||
		c.MaxEncodedBytesPerSpan < 1 || c.MaxEncodedBytesPerSpan > MaxChunkBytes ||
		c.MaxChunkRows < 1 || c.MaxChunkRows > MaxRowsPerChunk ||
		c.MaxChunkBytes < 1 || c.MaxChunkBytes > MaxChunkBytes ||
		c.MaxSpoolFiles < 1 || c.MaxSpoolFiles > maxRuntimeSpoolFiles ||
		c.MaxSpoolBytes < 1 || c.MaxSpoolBytes > maxRuntimeSpoolBytes {
		return errors.New("propertycatalog: runtime queue/build/chunk/spool bounds are outside hard limits")
	}
	if c.Kafka.DeliveryTimeout <= 0 || c.Kafka.DeliveryTimeout > MaxDeliveryTimeout {
		return fmt.Errorf(
			"propertycatalog: Kafka delivery timeout must be in (0,%s]",
			MaxDeliveryTimeout,
		)
	}
	if len(c.Kafka.Brokers) == 0 || len(c.Kafka.Brokers) > MaxKafkaBrokers {
		return fmt.Errorf(
			"propertycatalog: Kafka runtime requires 1..%d brokers",
			MaxKafkaBrokers,
		)
	}
	for _, broker := range c.Kafka.Brokers {
		if broker == "" || strings.TrimSpace(broker) != broker || len(broker) > MaxKafkaIdentityBytes {
			return errors.New("propertycatalog: Kafka broker is empty, padded, or too long")
		}
	}
	if c.Kafka.ClientID == "" || strings.TrimSpace(c.Kafka.ClientID) != c.Kafka.ClientID ||
		len(c.Kafka.ClientID) > MaxKafkaIdentityBytes {
		return errors.New("propertycatalog: Kafka client ID is empty, padded, or too long")
	}
	if err := validateTopic(c.Kafka.Topic); err != nil {
		return err
	}
	return nil
}

func (c RuntimeConfig) validateEnvironmentAcknowledgement() error {
	switch c.Environment {
	case DevelopmentEnvironment:
		if c.DevelopmentAcknowledgement != DevelopmentAcknowledgement ||
			c.ProductionAcknowledgement != "" {
			return errors.New("propertycatalog: development ingestion requires only the exact development acknowledgement")
		}
	case ProductionEnvironment:
		if c.ProductionAcknowledgement != ProductionAcknowledgement ||
			c.DevelopmentAcknowledgement != "" {
			return errors.New("propertycatalog: production ingestion requires only the exact production acknowledgement")
		}
	default:
		return errors.New("propertycatalog: enabled runtime requires an exact supported environment")
	}
	return nil
}

func (c RuntimeConfig) SelectedMode() (RuntimeMode, error) {
	if err := c.Validate(); err != nil {
		return RuntimeDisabled, err
	}
	return c.normalizedMode(), nil
}

func (c RuntimeConfig) WorkspaceAllowed(workspaceID string) bool {
	return slices.Contains(c.WorkspaceAllowlist, workspaceID)
}
