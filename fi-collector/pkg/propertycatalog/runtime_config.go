package propertycatalog

import (
	"errors"
	"fmt"
	"path/filepath"
	"slices"
	"strings"
	"time"
)

// RuntimeMode separates autoscaled candidate emission from the singleton that
// owns the ordered hot stream. "kafka" is deliberately the safe collector
// mode: it may publish deterministic candidates but can never allocate a
// catalog sequence. The direct mode exists only for explicit development
// compatibility. RuntimeSequencer is accepted only by the dedicated command.
type RuntimeMode string

// WorkspaceScopeMode controls how the hot producer identifies workspaces that
// may be considered for admission. Static mode is the default. Revision-fence
// mode is also safe for the singleton sequencer because every candidate is
// admitted only after its exact current, signed tenant fence is resolved. The
// autoscaled collector candidate emitter does not use either mode as an
// authorization boundary; it receives authenticated scope from canonical
// ingestion and never allocates an ordered stream.
type WorkspaceScopeMode string

const (
	RuntimeDisabled               RuntimeMode = "disabled"
	RuntimeKafka                  RuntimeMode = "kafka"
	RuntimeDirectKafkaDevelopment RuntimeMode = "direct_kafka_development"
	RuntimeSequencer              RuntimeMode = "sequencer"

	WorkspaceScopeStatic        WorkspaceScopeMode = "static"
	WorkspaceScopeRevisionFence WorkspaceScopeMode = "revision_fence"

	DevelopmentEnvironment = "development"
	ProductionEnvironment  = "production"
	// DevelopmentAcknowledgement is deliberately long and version-specific so
	// copying only FI_PROPERTY_CATALOG_MODE cannot activate the writer.
	DevelopmentAcknowledgement = "PROPERTY_CATALOG_V1_DEV_ONLY"
	// ProductionAcknowledgement is deliberately distinct from the DEV gate. A
	// copied DEV deployment cannot target the production-only catalog prefix.
	ProductionAcknowledgement = "UNIFIED_PROPERTY_CATALOG_V1_PRODUCTION"

	defaultReplayInterval                = time.Second
	defaultShutdownTimeout               = 10 * time.Second
	defaultQueueDepth                    = 64
	defaultMaxSpansPerBatch              = 20_000
	defaultMaxKeysPerSpan                = 128
	defaultMaxArrayMembersPerSpan        = 256
	defaultMaxEncodedBytesPerSpan        = 64 << 10
	defaultMaxChunkRows                  = 2_000
	defaultMaxChunkBytes                 = 256 << 10
	defaultMaxSpoolFiles                 = 10_000
	defaultMaxSpoolBytes           int64 = 512 << 20
	defaultMaxCandidateSpans             = 512
	defaultMaxCandidateRecordBytes       = 512 << 10

	maxReplayInterval = 30 * time.Second
	// MaxShutdownTimeout is shared by environment parsing and runtime validation
	// so the accepted operational range has one source of truth.
	MaxShutdownTimeout             = 2 * time.Minute
	maxRuntimeQueueDepth           = 1_024
	maxRuntimeSpansPerBatch        = 100_000
	maxRuntimeKeysPerSpan          = 4_096
	maxRuntimeArrayMembers         = 16_384
	maxRuntimeSpoolFiles           = 1_000_000
	maxRuntimeSpoolBytes     int64 = 1 << 40
	maxWorkspaceAllowlist          = 256
	maxRuntimeCandidateSpans       = 20_000

	// MaxKafkaBrokers is the reviewed producer/consumer broker-list bound.
	MaxKafkaBrokers = 16
	// MaxKafkaIdentityBytes bounds brokers, group IDs, and client IDs.
	MaxKafkaIdentityBytes = 255
	// MaxKafkaTopicBytes is Kafka's protocol topic-name ceiling.
	MaxKafkaTopicBytes = 249
	// MaxCandidateRecordBytes leaves transport overhead below the ordered
	// envelope ceiling while keeping one candidate receipt independently
	// bounded on disk and in memory.
	MaxCandidateRecordBytes = 512 << 10
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
	WorkspaceScopeMode         WorkspaceScopeMode `yaml:"workspace_scope_mode"`
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
	MaxCandidateSpans          int                `yaml:"max_candidate_spans"`
	MaxCandidateBytes          int                `yaml:"max_candidate_bytes"`
	Kafka                      KafkaRuntimeConfig `yaml:"kafka"`
}

func (c RuntimeConfig) normalizedMode() RuntimeMode {
	if c.Mode == "" {
		return RuntimeDisabled
	}
	return RuntimeMode(strings.ToLower(strings.TrimSpace(string(c.Mode))))
}

func (c RuntimeConfig) normalizedWorkspaceScopeMode() WorkspaceScopeMode {
	if c.WorkspaceScopeMode == "" {
		return WorkspaceScopeStatic
	}
	return WorkspaceScopeMode(strings.ToLower(strings.TrimSpace(string(c.WorkspaceScopeMode))))
}

func (c RuntimeConfig) WithDefaults() RuntimeConfig {
	if c.WorkspaceScopeMode == "" {
		c.WorkspaceScopeMode = WorkspaceScopeStatic
	}
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
	if c.MaxCandidateSpans == 0 {
		c.MaxCandidateSpans = defaultMaxCandidateSpans
	}
	if c.MaxCandidateBytes == 0 {
		c.MaxCandidateBytes = defaultMaxCandidateRecordBytes
	}
	if c.Kafka.DeliveryTimeout == 0 {
		c.Kafka.DeliveryTimeout = DefaultDeliveryTransportTimeout
	}
	if c.Kafka.ClientID == "" {
		switch c.Environment {
		case DevelopmentEnvironment:
			if c.normalizedMode() == RuntimeKafka {
				c.Kafka.ClientID = "fi-collector-property-candidate-v1-dev"
			} else {
				c.Kafka.ClientID = "fi-property-catalog-sequencer-output-v1-dev"
			}
		case ProductionEnvironment:
			if c.normalizedMode() == RuntimeKafka {
				c.Kafka.ClientID = "fi-collector-property-candidate-v1-prod"
			} else {
				c.Kafka.ClientID = "fi-property-catalog-sequencer-output-v1-prod"
			}
		}
	}
	return c
}

func (c RuntimeConfig) Validate() error {
	mode := c.normalizedMode()
	switch mode {
	case RuntimeDisabled:
		return nil
	case RuntimeKafka, RuntimeDirectKafkaDevelopment, RuntimeSequencer:
	default:
		return fmt.Errorf("propertycatalog: invalid runtime mode %q", c.Mode)
	}
	c = c.WithDefaults()
	if err := c.validateEnvironmentAcknowledgement(); err != nil {
		return err
	}
	if c.MaxSpansPerBatch < 1 || c.MaxSpansPerBatch > maxRuntimeSpansPerBatch ||
		c.MaxKeysPerSpan < 1 || c.MaxKeysPerSpan > maxRuntimeKeysPerSpan ||
		c.MaxArrayMembersPerSpan < 1 || c.MaxArrayMembersPerSpan > maxRuntimeArrayMembers ||
		c.MaxEncodedBytesPerSpan < 1 || c.MaxEncodedBytesPerSpan > MaxChunkBytes ||
		c.MaxCandidateSpans < 1 || c.MaxCandidateSpans > maxRuntimeCandidateSpans ||
		c.MaxCandidateBytes < 1 || c.MaxCandidateBytes > MaxCandidateRecordBytes {
		return errors.New("propertycatalog: candidate build/record bounds are outside hard limits")
	}
	if err := c.validateKafka(); err != nil {
		return err
	}
	if c.CatalogEpoch == 0 || c.ProjectionVersion == 0 {
		return errors.New("propertycatalog: enabled runtime requires positive epoch and projection version")
	}
	// Candidate mode also owns a bounded asynchronous queue and a bounded
	// shutdown drain. Validate both before its early return so an invalid queue
	// depth cannot panic make(chan), and a broker outage can never inherit an
	// unbounded collector shutdown contract.
	if c.QueueDepth < 1 || c.QueueDepth > maxRuntimeQueueDepth {
		return errors.New("propertycatalog: runtime queue depth is outside hard limits")
	}
	if c.ShutdownTimeout <= 0 || c.ShutdownTimeout > MaxShutdownTimeout {
		return fmt.Errorf(
			"propertycatalog: shutdown timeout must be in (0,%s]",
			MaxShutdownTimeout,
		)
	}
	if mode == RuntimeKafka {
		return nil
	}
	if mode == RuntimeDirectKafkaDevelopment && c.Environment != DevelopmentEnvironment {
		return errors.New("propertycatalog: direct ordered Kafka mode is development-only")
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
	switch c.normalizedWorkspaceScopeMode() {
	case WorkspaceScopeStatic:
		if err := c.validateStaticWorkspaceAllowlist(); err != nil {
			return err
		}
	case WorkspaceScopeRevisionFence:
		if mode == RuntimeDirectKafkaDevelopment && (c.Environment != DevelopmentEnvironment ||
			c.DevelopmentAcknowledgement != DevelopmentAcknowledgement || c.ProductionAcknowledgement != "") {
			return errors.New("propertycatalog: direct revision_fence workspace scope is development-only")
		}
		if mode != RuntimeDirectKafkaDevelopment && mode != RuntimeSequencer {
			return errors.New("propertycatalog: revision_fence workspace scope requires the singleton sequencer")
		}
		if len(c.WorkspaceAllowlist) != 0 {
			return errors.New("propertycatalog: revision_fence workspace scope rejects a static workspace allowlist")
		}
	default:
		return fmt.Errorf("propertycatalog: invalid workspace scope mode %q", c.WorkspaceScopeMode)
	}
	if c.MaxChunkRows < 1 || c.MaxChunkRows > MaxRowsPerChunk ||
		c.MaxChunkBytes < 1 || c.MaxChunkBytes > MaxChunkBytes ||
		c.MaxSpoolFiles < 1 || c.MaxSpoolFiles > maxRuntimeSpoolFiles ||
		c.MaxSpoolBytes < 1 || c.MaxSpoolBytes > maxRuntimeSpoolBytes {
		return errors.New("propertycatalog: runtime queue/build/chunk/spool bounds are outside hard limits")
	}
	return nil
}

func (c RuntimeConfig) validateKafka() error {
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

func (c RuntimeConfig) validateStaticWorkspaceAllowlist() error {
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
	return c.normalizedWorkspaceScopeMode() == WorkspaceScopeStatic &&
		slices.Contains(c.WorkspaceAllowlist, workspaceID)
}

// workspaceWithinConfiguredScope is deliberately not an admission decision.
// In revision-fence mode it only permits bounded durable-state inspection; hot
// traffic must additionally match a resolved current fence via fenceAllowsTenant.
func (c RuntimeConfig) workspaceWithinConfiguredScope(workspaceID string) bool {
	switch c.normalizedWorkspaceScopeMode() {
	case WorkspaceScopeStatic:
		return c.WorkspaceAllowed(workspaceID)
	case WorkspaceScopeRevisionFence:
		exactEnvironmentGate := (c.Environment == DevelopmentEnvironment &&
			c.DevelopmentAcknowledgement == DevelopmentAcknowledgement &&
			c.ProductionAcknowledgement == "") ||
			(c.Environment == ProductionEnvironment &&
				c.ProductionAcknowledgement == ProductionAcknowledgement &&
				c.DevelopmentAcknowledgement == "")
		return exactEnvironmentGate &&
			(c.normalizedMode() == RuntimeSequencer ||
				(c.normalizedMode() == RuntimeDirectKafkaDevelopment &&
					c.Environment == DevelopmentEnvironment)) &&
			len(c.WorkspaceAllowlist) == 0 &&
			validateCanonicalUUID("workspace scope", workspaceID) == nil
	default:
		return false
	}
}

// fenceAllowsTenant is the authorization boundary for revision-fence scope.
// The caller must supply the single current fence returned for this tenant;
// comparing both tenant components prevents a provider from widening scope.
func (c RuntimeConfig) fenceAllowsTenant(
	fence RevisionFence, organizationID, workspaceID string,
) bool {
	if fence.OrganizationID != organizationID || fence.WorkspaceID != workspaceID ||
		validateCanonicalUUID("fence-scoped organization", organizationID) != nil ||
		!c.workspaceWithinConfiguredScope(workspaceID) {
		return false
	}
	return true
}
