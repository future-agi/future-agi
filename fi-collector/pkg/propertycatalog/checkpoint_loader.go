package propertycatalog

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"time"
)

// checkpointInventoryQuery deliberately anchors every scope on the newest
// serialized build reservation.  An open or draining newer revision must
// never disappear behind an older active activation.  Raw latest-version rows
// are aggregated before the response LIMIT so same-version conflicts cannot
// be crowded out.  Checkpoint and activation rows only corroborate the exact
// anchored revision; they do not select an older fallback.
const checkpointInventoryQuery = `WITH
latest_source_rows AS
(
  SELECT *
  FROM
  (
    SELECT
      organization_id, workspace_id, catalog_epoch, catalog_revision, build_token,
      projection_version, source_adapter, producer_stream_id, envelope_version,
      first_sequence, last_sequence, max_contiguous_sequence, last_issued_sequence,
      fenced_sequence, terminal_payload_sha256, build_plan_json, build_lease_sha256,
      status, gap_count, gap_reasons, kafka_partition, kafka_high_water_offset,
      started_at, updated_at, drain_deadline, fenced_at, _version,
      max(_version) OVER
      (
        PARTITION BY organization_id, workspace_id, catalog_epoch, catalog_revision,
          build_token, source_adapter, producer_stream_id
      ) AS latest_version
    FROM property_catalog_source_streams
  )
  WHERE _version = latest_version
),
source_states AS
(
  SELECT
    raw.organization_id, raw.workspace_id, raw.catalog_epoch, raw.catalog_revision,
    raw.build_token, raw.source_adapter, raw.producer_stream_id,
    any(raw.projection_version) AS projection_version,
    any(raw.envelope_version) AS envelope_version,
    any(raw.first_sequence) AS first_sequence,
    any(raw.last_sequence) AS last_sequence,
    any(raw.max_contiguous_sequence) AS max_contiguous_sequence,
    any(raw.last_issued_sequence) AS last_issued_sequence,
    any(raw.fenced_sequence) AS fenced_sequence,
    any(toString(raw.terminal_payload_sha256)) AS terminal_payload_sha256,
    any(raw.build_plan_json) AS build_plan_json,
    any(toString(raw.build_lease_sha256)) AS build_lease_sha256,
    any(toString(raw.status)) AS status,
    any(raw.gap_count) AS gap_count,
    any(raw._version) AS version,
    count() AS evidence_rows,
    uniqExact(tuple(
      raw.projection_version, raw.envelope_version, raw.first_sequence,
      raw.last_sequence, raw.max_contiguous_sequence, raw.last_issued_sequence,
      raw.fenced_sequence, raw.terminal_payload_sha256, raw.build_plan_json,
      raw.build_lease_sha256, raw.status, raw.gap_count, raw.gap_reasons,
      raw.kafka_partition, raw.kafka_high_water_offset, raw.started_at,
      raw.updated_at, raw.drain_deadline, raw.fenced_at
    )) AS state_variants
  FROM latest_source_rows AS raw
  GROUP BY
    raw.organization_id, raw.workspace_id, raw.catalog_epoch, raw.catalog_revision,
    raw.build_token, raw.source_adapter, raw.producer_stream_id
),
reservation_keys AS
(
  SELECT DISTINCT
    organization_id, workspace_id, catalog_epoch, catalog_revision, build_token,
    source_adapter, producer_stream_id
  FROM latest_source_rows
  WHERE envelope_version = 0
),
reservation_states AS
(
  SELECT state.*
  FROM source_states AS state
  INNER JOIN reservation_keys AS reservation
    USING (
      organization_id, workspace_id, catalog_epoch, catalog_revision, build_token,
      source_adapter, producer_stream_id
    )
),
newest_reservation_revisions AS
(
  SELECT
    organization_id, workspace_id, catalog_epoch,
    max(catalog_revision) AS catalog_revision
  FROM reservation_states
  GROUP BY organization_id, workspace_id, catalog_epoch
),
newest_reservations AS
(
  SELECT reservation.*
  FROM reservation_states AS reservation
  INNER JOIN newest_reservation_revisions AS newest
    USING (organization_id, workspace_id, catalog_epoch, catalog_revision)
),
latest_checkpoint_rows AS
(
  SELECT *
  FROM
  (
    SELECT
      organization_id, workspace_id, catalog_epoch, catalog_revision, build_token,
      projection_version, source_adapter, producer_stream_id, status, terminal,
      source_cursor, watermark, source_version_fence, source_fingerprint,
      source_rows, processed_rows, definition_rows, value_rows, tombstone_rows,
      gap_count, poison_count, conflict_count, gap_reasons, first_sequence,
      last_sequence, last_issued_sequence, fenced_sequence, terminal_payload_sha256,
      delivery_count, source_digest, emitted_digest, previous_payload_sha256,
      run_id, worker_id, error, started_at, updated_at, finished_at, _version,
      max(_version) OVER
      (
        PARTITION BY organization_id, workspace_id, catalog_epoch, catalog_revision,
          build_token, source_adapter, producer_stream_id
      ) AS latest_version
    FROM property_catalog_checkpoints
  )
  WHERE _version = latest_version
),
checkpoint_states AS
(
  SELECT
    raw.organization_id, raw.workspace_id, raw.catalog_epoch, raw.catalog_revision,
    raw.build_token, raw.source_adapter, raw.producer_stream_id,
    any(raw.projection_version) AS projection_version,
    any(toString(raw.status)) AS status,
    any(raw.terminal) AS terminal,
    any(ifNull(raw.first_sequence, 0)) AS first_sequence,
    any(ifNull(raw.last_sequence, 0)) AS last_sequence,
    countIf(isNull(raw.first_sequence)) AS null_first_sequences,
    countIf(isNull(raw.last_sequence)) AS null_last_sequences,
    any(raw.last_issued_sequence) AS last_issued_sequence,
    any(raw.fenced_sequence) AS fenced_sequence,
    any(toString(raw.terminal_payload_sha256)) AS terminal_payload_sha256,
    any(raw.gap_count) AS gap_count,
    any(raw._version) AS version,
    count() AS evidence_rows,
    uniqExact(tuple(
      raw.projection_version, raw.status, raw.terminal, raw.source_cursor,
      raw.watermark, raw.source_version_fence, raw.source_fingerprint,
      raw.source_rows, raw.processed_rows, raw.definition_rows, raw.value_rows,
      raw.tombstone_rows, raw.gap_count, raw.poison_count, raw.conflict_count,
      raw.gap_reasons, raw.first_sequence, raw.last_sequence,
      raw.last_issued_sequence, raw.fenced_sequence, raw.terminal_payload_sha256,
      raw.delivery_count, raw.source_digest, raw.emitted_digest,
      raw.previous_payload_sha256, raw.run_id, raw.worker_id, raw.error,
      raw.started_at, raw.updated_at, raw.finished_at
    )) AS state_variants
  FROM latest_checkpoint_rows AS raw
  GROUP BY
    raw.organization_id, raw.workspace_id, raw.catalog_epoch, raw.catalog_revision,
    raw.build_token, raw.source_adapter, raw.producer_stream_id
),
latest_activation_rows AS
(
  SELECT *
  FROM
  (
    SELECT
      organization_id, workspace_id, catalog_epoch, catalog_revision, build_token,
      projection_version, lifecycle_mode, lineage_anchor_revision,
      activation_sequence, source_manifest_json, source_manifest_sha256,
      revision_fence_sha256, activation_sha256, status, live_definition_rows,
      tombstone_rows, value_rows, qualified_at, updated_at, _version,
      max(_version) OVER
      (
        PARTITION BY organization_id, workspace_id, catalog_epoch,
          catalog_revision, build_token
      ) AS latest_version
    FROM property_catalog_activations
  )
  WHERE _version = latest_version
),
activation_states AS
(
  SELECT
    raw.organization_id, raw.workspace_id, raw.catalog_epoch, raw.catalog_revision,
    raw.build_token,
    any(raw.projection_version) AS projection_version,
    any(toString(raw.status)) AS status,
    any(raw._version) AS version,
    count() AS evidence_rows,
    uniqExact(tuple(
      raw.projection_version, raw.lifecycle_mode, raw.lineage_anchor_revision,
      raw.activation_sequence, raw.source_manifest_json,
      raw.source_manifest_sha256, raw.revision_fence_sha256,
      raw.activation_sha256, raw.status, raw.live_definition_rows,
      raw.tombstone_rows, raw.value_rows, raw.qualified_at, raw.updated_at
    )) AS state_variants
  FROM latest_activation_rows AS raw
  GROUP BY raw.organization_id, raw.workspace_id, raw.catalog_epoch,
    raw.catalog_revision, raw.build_token
)
SELECT
  toString(reservation.organization_id) AS organization_id,
  toString(reservation.workspace_id) AS workspace_id,
  reservation.catalog_epoch AS catalog_epoch,
  reservation.catalog_revision AS catalog_revision,
  toString(reservation.build_token) AS build_token,
  reservation.projection_version AS reservation_projection_version,
  toString(reservation.source_adapter) AS reservation_source_adapter,
  toString(reservation.producer_stream_id) AS reservation_producer_stream_id,
  reservation.envelope_version AS reservation_envelope_version,
  reservation.build_plan_json AS reservation_build_plan_json,
  reservation.build_lease_sha256 AS reservation_build_lease_sha256,
  reservation.status AS reservation_status,
  reservation.version AS reservation_version,
  reservation.state_variants AS reservation_state_variants,
  stream.evidence_rows AS stream_evidence_rows,
  stream.projection_version AS stream_projection_version,
  toString(stream.source_adapter) AS stream_source_adapter,
  toString(stream.producer_stream_id) AS stream_producer_stream_id,
  stream.envelope_version AS stream_envelope_version,
  stream.first_sequence AS stream_first_sequence,
  stream.last_sequence AS stream_last_sequence,
  stream.max_contiguous_sequence AS stream_max_contiguous_sequence,
  stream.last_issued_sequence AS stream_last_issued_sequence,
  stream.fenced_sequence AS stream_fenced_sequence,
  stream.terminal_payload_sha256 AS stream_terminal_payload_sha256,
  stream.build_plan_json AS stream_build_plan_json,
  stream.build_lease_sha256 AS stream_build_lease_sha256,
  stream.status AS stream_status,
  stream.gap_count AS stream_gap_count,
  stream.version AS stream_version,
  stream.state_variants AS stream_state_variants,
  checkpoint.evidence_rows AS checkpoint_evidence_rows,
  checkpoint.projection_version AS checkpoint_projection_version,
  checkpoint.status AS checkpoint_status,
  checkpoint.terminal AS checkpoint_terminal,
  checkpoint.first_sequence AS checkpoint_first_sequence,
  checkpoint.last_sequence AS checkpoint_last_sequence,
  checkpoint.null_first_sequences AS checkpoint_null_first_sequences,
  checkpoint.null_last_sequences AS checkpoint_null_last_sequences,
  checkpoint.last_issued_sequence AS checkpoint_last_issued_sequence,
  checkpoint.fenced_sequence AS checkpoint_fenced_sequence,
  checkpoint.terminal_payload_sha256 AS checkpoint_terminal_payload_sha256,
  checkpoint.gap_count AS checkpoint_gap_count,
  checkpoint.version AS checkpoint_version,
  checkpoint.state_variants AS checkpoint_state_variants,
  activation.evidence_rows AS activation_evidence_rows,
  activation.projection_version AS activation_projection_version,
  activation.status AS activation_status,
  activation.version AS activation_version,
  activation.state_variants AS activation_state_variants
FROM newest_reservations AS reservation
LEFT JOIN source_states AS stream
  ON stream.organization_id = reservation.organization_id
  AND stream.workspace_id = reservation.workspace_id
  AND stream.catalog_epoch = reservation.catalog_epoch
  AND stream.catalog_revision = reservation.catalog_revision
  AND stream.build_token = reservation.build_token
LEFT JOIN checkpoint_states AS checkpoint
  ON checkpoint.organization_id = stream.organization_id
  AND checkpoint.workspace_id = stream.workspace_id
  AND checkpoint.catalog_epoch = stream.catalog_epoch
  AND checkpoint.catalog_revision = stream.catalog_revision
  AND checkpoint.build_token = stream.build_token
  AND checkpoint.source_adapter = stream.source_adapter
  AND checkpoint.producer_stream_id = stream.producer_stream_id
LEFT JOIN activation_states AS activation
  ON activation.organization_id = reservation.organization_id
  AND activation.workspace_id = reservation.workspace_id
  AND activation.catalog_epoch = reservation.catalog_epoch
  AND activation.catalog_revision = reservation.catalog_revision
  AND activation.build_token = reservation.build_token
ORDER BY
  reservation.organization_id, reservation.workspace_id, reservation.catalog_epoch,
  reservation.catalog_revision, reservation.build_token,
  stream.source_adapter, stream.producer_stream_id
LIMIT {inventory_limit:UInt64}
FORMAT JSONEachRow`

// checkpointStreamQuery reduces the entire exact stream to one proof row.  The
// inner aggregate sees every immutable replay for each sequence; the window
// then proves the payload chain; the outer aggregate proves root, contiguity,
// terminal ordering, and the deterministic tail without returning O(sequence)
// bytes to the consumer.
const checkpointStreamQuery = `SELECT
  count() AS sequence_rows,
  min(sequence) AS first_sequence,
  max(sequence) AS last_sequence,
  uniqExact(sequence) AS distinct_sequences,
  uniqExact(projection_version) AS projection_versions,
  max(projection_versions_at_sequence) AS max_projection_versions_at_sequence,
  max(identity_variants) AS max_identity_variants,
  countIf(identity_variants != 1) AS conflict_sequences,
  countIf(previous_payload_sha256 != expected_previous_payload_sha256) AS chain_breaks,
  countIf(
    envelope_format != {envelope_format:String}
    OR envelope_version != {envelope_version:UInt16}
    OR terminal > 1
    OR NOT match(envelope_id, '^[0-9a-f]{64}$')
    OR NOT match(payload_sha256, '^[0-9a-f]{64}$')
    OR NOT match(previous_payload_sha256, '^[0-9a-f]{64}$')
    OR NOT match(source_batch_digest, '^[0-9a-f]{64}$')
  ) AS invalid_wire_sequences,
  countIf(
    (outcome = 'committed' AND length(gap_reasons) != 0)
    OR (outcome = 'gap' AND length(gap_reasons) = 0)
    OR outcome NOT IN ('committed', 'gap')
    OR gap_reasons != arraySort(arrayDistinct(gap_reasons))
  ) AS invalid_outcome_sequences,
  countIf(
    terminal = 1 AND
    (outcome != 'committed' OR length(gap_reasons) != 0 OR source_rows != 0
      OR definition_rows != 0 OR value_rows != 0 OR tombstone_rows != 0)
  ) AS invalid_terminal_sequences,
  countIf(terminal = 1) AS terminal_sequences,
  maxIf(sequence, terminal = 1) AS terminal_sequence,
  countIf(outcome = 'gap') AS gap_sequences,
  argMax(projection_version, sequence) AS tail_projection_version,
  argMax(envelope_format, sequence) AS tail_envelope_format,
  argMax(envelope_version, sequence) AS tail_envelope_version,
  argMax(envelope_id, sequence) AS tail_envelope_id,
  argMax(payload_sha256, sequence) AS tail_payload_sha256,
  argMax(terminal, sequence) AS tail_terminal
FROM
(
  SELECT
    *,
    if(
      sequence = 1,
      {zero_sha256:String},
      lagInFrame(payload_sha256, 1, '') OVER
      (ORDER BY sequence ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
    ) AS expected_previous_payload_sha256
  FROM
  (
    SELECT
      delivery.sequence AS sequence,
      any(delivery.projection_version) AS projection_version,
      uniqExact(delivery.projection_version) AS projection_versions_at_sequence,
      any(delivery.envelope_format) AS envelope_format,
      any(delivery.envelope_version) AS envelope_version,
      any(toString(delivery.envelope_id)) AS envelope_id,
      any(toString(delivery.payload_sha256)) AS payload_sha256,
      any(toString(delivery.previous_payload_sha256)) AS previous_payload_sha256,
      any(toString(delivery.source_batch_digest)) AS source_batch_digest,
      any(toString(delivery.outcome)) AS outcome,
      any(delivery.terminal) AS terminal,
      any(delivery.gap_reasons) AS gap_reasons,
      any(delivery.source_rows) AS source_rows,
      any(delivery.definition_rows) AS definition_rows,
      any(delivery.value_rows) AS value_rows,
      any(delivery.tombstone_rows) AS tombstone_rows,
      uniqExact(tuple(
        delivery.projection_version, delivery.envelope_format,
        delivery.envelope_version, delivery.envelope_id, delivery.payload_sha256,
        delivery.previous_payload_sha256, delivery.source_batch_digest,
        delivery.outcome, delivery.terminal, delivery.gap_reasons,
        delivery.source_rows, delivery.definition_rows, delivery.value_rows,
        delivery.tombstone_rows
      )) AS identity_variants
    FROM property_catalog_deliveries AS delivery
    WHERE delivery.organization_id = {organization_id:UUID}
      AND delivery.workspace_id = {workspace_id:UUID}
      AND delivery.catalog_epoch = {catalog_epoch:UInt16}
      AND delivery.catalog_revision = {catalog_revision:UInt64}
      AND delivery.build_token = {build_token:UUID}
      AND toString(delivery.source_adapter) = {source_adapter:String}
      AND delivery.producer_stream_id = {producer_stream_id:UUID}
    GROUP BY delivery.sequence
  )
)
HAVING count() > 0
FORMAT JSONEachRow`

const checkpointLedgerProbeQuery = `SELECT 1 AS present
FROM property_catalog_deliveries
LIMIT 1
FORMAT JSONEachRow`

const deliveryLeaseQuery = `SELECT
  organization_id,
  workspace_id,
  catalog_epoch,
  catalog_revision,
  build_token,
  projection_version,
  source_adapter,
  producer_stream_id,
  envelope_version,
  last_issued_sequence,
  fenced_sequence,
  build_plan_json,
  build_lease_sha256,
  status,
  drain_deadline,
  fenced_at,
  _version
FROM property_catalog_source_streams
WHERE organization_id = {organization_id:UUID}
  AND workspace_id = {workspace_id:UUID}
  AND catalog_epoch = {catalog_epoch:UInt16}
  AND catalog_revision = {catalog_revision:UInt64}
  AND build_token = {build_token:UUID}
  AND (
    (
      envelope_version = 1
      AND toString(source_adapter) = {source_adapter:String}
      AND producer_stream_id = {producer_stream_id:UUID}
    )
    OR (
      envelope_version = 0
      AND producer_stream_id = build_token
    )
  )
ORDER BY _version DESC
LIMIT 33
FORMAT JSONEachRow`

const (
	maxCheckpointSequencesPerStream = 100_000
	maxCheckpointStreamProofBytes   = 64 << 10
	maxCheckpointProbeBytes         = 1024
	maxDeliveryLeaseRows            = 32
	maxDeliveryLeaseBytes           = 64 << 10
	maxBuildPlanBytes               = 32 << 10
)

const (
	DefaultCheckpointMaxStreams        = 16_384
	MaximumCheckpointMaxStreams        = 262_144
	DefaultCheckpointInventoryMaxBytes = int64(64 << 20)
	MaximumCheckpointInventoryMaxBytes = int64(512 << 20)
)

type CheckpointLoaderLimits struct {
	MaxStreams        int
	InventoryMaxBytes int64
}

type CheckpointLoader interface {
	LoadCheckpoints(context.Context) ([]StreamCheckpoint, error)
}

type ClickHouseCheckpointLoader struct {
	sink              *ClickHouseSink
	now               func() time.Time
	maxStreams        int
	inventoryMaxBytes int64
}

func NewClickHouseCheckpointLoader(
	cfg ClickHouseSinkConfig, overrides ...CheckpointLoaderLimits,
) (*ClickHouseCheckpointLoader, error) {
	if len(overrides) > 1 {
		return nil, errors.New("propertycatalog: at most one checkpoint loader limit override is allowed")
	}
	limits := CheckpointLoaderLimits{
		MaxStreams:        DefaultCheckpointMaxStreams,
		InventoryMaxBytes: DefaultCheckpointInventoryMaxBytes,
	}
	if len(overrides) == 1 {
		limits = overrides[0]
	}
	if limits.MaxStreams < 1 || limits.MaxStreams > MaximumCheckpointMaxStreams {
		return nil, fmt.Errorf(
			"propertycatalog: checkpoint max streams must be in [1,%d]",
			MaximumCheckpointMaxStreams,
		)
	}
	if limits.InventoryMaxBytes < 1 || limits.InventoryMaxBytes > MaximumCheckpointInventoryMaxBytes {
		return nil, fmt.Errorf(
			"propertycatalog: checkpoint inventory max bytes must be in [1,%d]",
			MaximumCheckpointInventoryMaxBytes,
		)
	}
	sink, err := NewClickHouseSink(cfg)
	if err != nil {
		return nil, err
	}
	return &ClickHouseCheckpointLoader{
		sink: sink, now: time.Now,
		maxStreams: limits.MaxStreams, inventoryMaxBytes: limits.InventoryMaxBytes,
	}, nil
}

var _ DeliveryLeaseGuard = (*ClickHouseCheckpointLoader)(nil)

type deliveryLeaseJSON struct {
	OrganizationID     string        `json:"organization_id"`
	WorkspaceID        string        `json:"workspace_id"`
	CatalogEpoch       uint16        `json:"catalog_epoch"`
	CatalogRevision    uint64        `json:"catalog_revision"`
	BuildToken         string        `json:"build_token"`
	ProjectionVersion  uint16        `json:"projection_version"`
	SourceAdapter      SourceAdapter `json:"source_adapter"`
	ProducerStreamID   string        `json:"producer_stream_id"`
	EnvelopeVersion    uint16        `json:"envelope_version"`
	LastIssuedSequence uint64        `json:"last_issued_sequence"`
	FencedSequence     uint64        `json:"fenced_sequence"`
	BuildPlanJSON      string        `json:"build_plan_json"`
	BuildLeaseSHA256   string        `json:"build_lease_sha256"`
	Status             string        `json:"status"`
	DrainDeadline      *string       `json:"drain_deadline"`
	FencedAt           *string       `json:"fenced_at"`
	Version            uint64        `json:"_version"`
}

type buildPlanCutoffJSON struct {
	Label string `json:"label"`
	Value uint64 `json:"value"`
}

type buildPlanStreamJSON struct {
	ProducerStreamID string              `json:"producer_stream_id"`
	Role             string              `json:"role"`
	SourceAdapter    SourceAdapter       `json:"source_adapter"`
	SourceCutoff     buildPlanCutoffJSON `json:"source_cutoff"`
}

type buildPlanSourceScopeJSON struct {
	ProjectIDs  []string `json:"project_ids"`
	SpanSinceUS uint64   `json:"span_since_us"`
	SpanUntilUS uint64   `json:"span_until_us"`
}

type buildPlanDocumentJSON struct {
	BuildToken        string                   `json:"build_token"`
	CatalogEpoch      uint16                   `json:"catalog_epoch"`
	CatalogRevision   uint64                   `json:"catalog_revision"`
	Format            string                   `json:"format"`
	OrganizationID    string                   `json:"organization_id"`
	ProjectionVersion uint16                   `json:"projection_version"`
	SourceScope       buildPlanSourceScopeJSON `json:"source_scope"`
	Streams           []buildPlanStreamJSON    `json:"streams"`
	Version           uint16                   `json:"version"`
	WorkspaceID       string                   `json:"workspace_id"`
}

type checkpointInventoryJSON struct {
	OrganizationID                  string        `json:"organization_id"`
	WorkspaceID                     string        `json:"workspace_id"`
	CatalogEpoch                    uint16        `json:"catalog_epoch"`
	CatalogRevision                 uint64        `json:"catalog_revision"`
	BuildToken                      string        `json:"build_token"`
	ReservationProjectionVersion    uint16        `json:"reservation_projection_version"`
	ReservationSourceAdapter        SourceAdapter `json:"reservation_source_adapter"`
	ReservationProducerStreamID     string        `json:"reservation_producer_stream_id"`
	ReservationEnvelopeVersion      uint16        `json:"reservation_envelope_version"`
	ReservationBuildPlanJSON        string        `json:"reservation_build_plan_json"`
	ReservationBuildLeaseSHA256     string        `json:"reservation_build_lease_sha256"`
	ReservationStatus               string        `json:"reservation_status"`
	ReservationVersion              uint64        `json:"reservation_version"`
	ReservationStateVariants        uint64        `json:"reservation_state_variants"`
	StreamEvidenceRows              uint64        `json:"stream_evidence_rows"`
	StreamProjectionVersion         uint16        `json:"stream_projection_version"`
	StreamSourceAdapter             SourceAdapter `json:"stream_source_adapter"`
	StreamProducerStreamID          string        `json:"stream_producer_stream_id"`
	StreamEnvelopeVersion           uint16        `json:"stream_envelope_version"`
	StreamFirstSequence             uint64        `json:"stream_first_sequence"`
	StreamLastSequence              uint64        `json:"stream_last_sequence"`
	StreamMaxContiguousSequence     uint64        `json:"stream_max_contiguous_sequence"`
	StreamLastIssuedSequence        uint64        `json:"stream_last_issued_sequence"`
	StreamFencedSequence            uint64        `json:"stream_fenced_sequence"`
	StreamTerminalPayloadSHA256     string        `json:"stream_terminal_payload_sha256"`
	StreamBuildPlanJSON             string        `json:"stream_build_plan_json"`
	StreamBuildLeaseSHA256          string        `json:"stream_build_lease_sha256"`
	StreamStatus                    string        `json:"stream_status"`
	StreamGapCount                  uint64        `json:"stream_gap_count"`
	StreamVersion                   uint64        `json:"stream_version"`
	StreamStateVariants             uint64        `json:"stream_state_variants"`
	CheckpointEvidenceRows          uint64        `json:"checkpoint_evidence_rows"`
	CheckpointProjectionVersion     uint16        `json:"checkpoint_projection_version"`
	CheckpointStatus                string        `json:"checkpoint_status"`
	CheckpointTerminal              uint8         `json:"checkpoint_terminal"`
	CheckpointFirstSequence         uint64        `json:"checkpoint_first_sequence"`
	CheckpointLastSequence          uint64        `json:"checkpoint_last_sequence"`
	CheckpointNullFirstSequences    uint64        `json:"checkpoint_null_first_sequences"`
	CheckpointNullLastSequences     uint64        `json:"checkpoint_null_last_sequences"`
	CheckpointLastIssuedSequence    uint64        `json:"checkpoint_last_issued_sequence"`
	CheckpointFencedSequence        uint64        `json:"checkpoint_fenced_sequence"`
	CheckpointTerminalPayloadSHA256 string        `json:"checkpoint_terminal_payload_sha256"`
	CheckpointGapCount              uint64        `json:"checkpoint_gap_count"`
	CheckpointVersion               uint64        `json:"checkpoint_version"`
	CheckpointStateVariants         uint64        `json:"checkpoint_state_variants"`
	ActivationEvidenceRows          uint64        `json:"activation_evidence_rows"`
	ActivationProjectionVersion     uint16        `json:"activation_projection_version"`
	ActivationStatus                string        `json:"activation_status"`
	ActivationVersion               uint64        `json:"activation_version"`
	ActivationStateVariants         uint64        `json:"activation_state_variants"`
}

type checkpointStreamProofJSON struct {
	SequenceRows                    uint64 `json:"sequence_rows"`
	FirstSequence                   uint64 `json:"first_sequence"`
	LastSequence                    uint64 `json:"last_sequence"`
	DistinctSequences               uint64 `json:"distinct_sequences"`
	ProjectionVersions              uint64 `json:"projection_versions"`
	MaxProjectionVersionsAtSequence uint64 `json:"max_projection_versions_at_sequence"`
	MaxIdentityVariants             uint64 `json:"max_identity_variants"`
	ConflictSequences               uint64 `json:"conflict_sequences"`
	ChainBreaks                     uint64 `json:"chain_breaks"`
	InvalidWireSequences            uint64 `json:"invalid_wire_sequences"`
	InvalidOutcomeSequences         uint64 `json:"invalid_outcome_sequences"`
	InvalidTerminalSequences        uint64 `json:"invalid_terminal_sequences"`
	TerminalSequences               uint64 `json:"terminal_sequences"`
	TerminalSequence                uint64 `json:"terminal_sequence"`
	GapSequences                    uint64 `json:"gap_sequences"`
	TailProjectionVersion           uint16 `json:"tail_projection_version"`
	TailEnvelopeFormat              string `json:"tail_envelope_format"`
	TailEnvelopeVersion             uint16 `json:"tail_envelope_version"`
	TailEnvelopeID                  string `json:"tail_envelope_id"`
	TailPayloadSHA256               string `json:"tail_payload_sha256"`
	TailTerminal                    uint8  `json:"tail_terminal"`
}

type checkpointLedgerProbeJSON struct {
	Present uint8 `json:"present"`
}

func (l *ClickHouseCheckpointLoader) LoadCheckpoints(ctx context.Context) ([]StreamCheckpoint, error) {
	if l == nil || l.sink == nil || l.sink.baseURL == nil || l.sink.client == nil || ctx == nil {
		return nil, errors.New("propertycatalog: checkpoint load requires a reader context")
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	inventory := make([]checkpointInventoryJSON, 0, 16)
	err := l.queryJSONEachRow(
		ctx,
		checkpointInventoryQuery,
		map[string]string{"param_inventory_limit": fmt.Sprintf("%d", l.maxStreams+1)},
		map[string]string{"max_execution_time": "10"},
		l.maxStreams,
		l.inventoryMaxBytes,
		"checkpoint inventory",
		func(index int, line []byte) error {
			var row checkpointInventoryJSON
			if err := decodeCheckpointJSON(line, &row); err != nil {
				return fmt.Errorf("decode checkpoint inventory row %d: %w", index, err)
			}
			inventory = append(inventory, row)
			return nil
		},
	)
	if err != nil {
		return nil, err
	}
	candidates, err := validateCheckpointInventory(inventory)
	if err != nil {
		return nil, err
	}
	checkpoints := make([]StreamCheckpoint, 0, len(candidates))
	for index, candidate := range candidates {
		proof, found, err := l.loadCheckpointStreamProof(ctx, candidate)
		if err != nil {
			return nil, fmt.Errorf("propertycatalog: load checkpoint stream %d: %w", index, err)
		}
		if !found {
			if err := validateEmptyCheckpointStream(candidate); err != nil {
				return nil, err
			}
			continue
		}
		checkpoint, err := validateCheckpointStreamProof(candidate, proof)
		if err != nil {
			return nil, fmt.Errorf(
				"propertycatalog: invalid delivery stream %s: %w",
				checkpointInventoryStreamKey(candidate), err,
			)
		}
		checkpoints = append(checkpoints, checkpoint)
	}
	// Only a wholly absent inventory needs the orphan-ledger probe. An exact
	// newest reservation with ten proven-empty streams is a legitimate restart
	// window before sequence one. Older immutable delivery history must not make
	// that new revision look orphaned.
	if len(candidates) == 0 {
		nonempty, err := l.deliveryLedgerNonempty(ctx)
		if err != nil {
			return nil, err
		}
		if nonempty {
			return nil, errors.New("propertycatalog: delivery ledger is nonempty without a reconstructable newest reservation stream")
		}
	}
	return checkpoints, nil
}

func (l *ClickHouseCheckpointLoader) loadCheckpointStreamProof(
	ctx context.Context, candidate checkpointInventoryJSON,
) (checkpointStreamProofJSON, bool, error) {
	params := map[string]string{
		"param_organization_id":    candidate.OrganizationID,
		"param_workspace_id":       candidate.WorkspaceID,
		"param_catalog_epoch":      fmt.Sprintf("%d", candidate.CatalogEpoch),
		"param_catalog_revision":   fmt.Sprintf("%d", candidate.CatalogRevision),
		"param_build_token":        candidate.BuildToken,
		"param_source_adapter":     string(candidate.StreamSourceAdapter),
		"param_producer_stream_id": candidate.StreamProducerStreamID,
		"param_envelope_format":    EnvelopeFormat,
		"param_envelope_version":   fmt.Sprintf("%d", EnvelopeVersion),
		"param_zero_sha256":        ZeroSHA256,
	}
	settings := map[string]string{
		"max_execution_time":     "10",
		"max_rows_to_group_by":   fmt.Sprintf("%d", maxCheckpointSequencesPerStream+1),
		"group_by_overflow_mode": "throw",
	}
	rows := make([]checkpointStreamProofJSON, 0, 1)
	err := l.queryJSONEachRow(
		ctx,
		checkpointStreamQuery,
		params,
		settings,
		1,
		maxCheckpointStreamProofBytes,
		"checkpoint stream proof",
		func(index int, line []byte) error {
			var row checkpointStreamProofJSON
			if err := decodeCheckpointJSON(line, &row); err != nil {
				return fmt.Errorf("decode checkpoint stream proof row %d: %w", index, err)
			}
			rows = append(rows, row)
			return nil
		},
	)
	if err != nil {
		return checkpointStreamProofJSON{}, false, err
	}
	if len(rows) == 0 {
		return checkpointStreamProofJSON{}, false, nil
	}
	return rows[0], true, nil
}

func (l *ClickHouseCheckpointLoader) deliveryLedgerNonempty(ctx context.Context) (bool, error) {
	rows := make([]checkpointLedgerProbeJSON, 0, 1)
	err := l.queryJSONEachRow(
		ctx,
		checkpointLedgerProbeQuery,
		nil,
		map[string]string{"max_execution_time": "2"},
		1,
		maxCheckpointProbeBytes,
		"delivery ledger probe",
		func(index int, line []byte) error {
			var row checkpointLedgerProbeJSON
			if err := decodeCheckpointJSON(line, &row); err != nil {
				return fmt.Errorf("decode delivery ledger probe row %d: %w", index, err)
			}
			rows = append(rows, row)
			return nil
		},
	)
	if err != nil {
		return false, err
	}
	if len(rows) == 0 {
		return false, nil
	}
	if rows[0].Present != 1 {
		return false, errors.New("propertycatalog: delivery ledger probe returned invalid evidence")
	}
	return true, nil
}

func (l *ClickHouseCheckpointLoader) queryJSONEachRow(
	ctx context.Context,
	statement string,
	params map[string]string,
	settings map[string]string,
	maxRows int,
	maxBytes int64,
	label string,
	visit func(int, []byte) error,
) error {
	if ctx == nil || statement == "" || maxRows < 1 || maxBytes < 1 || label == "" || visit == nil {
		return errors.New("propertycatalog: invalid bounded checkpoint query")
	}
	endpoint := *l.sink.baseURL
	query := endpoint.Query()
	query.Set("database", l.sink.database)
	query.Set("max_result_bytes", fmt.Sprintf("%d", maxBytes))
	query.Set("max_result_rows", fmt.Sprintf("%d", maxRows+1))
	query.Set("result_overflow_mode", "throw")
	for name, value := range params {
		query.Set(name, value)
	}
	for name, value := range settings {
		query.Set(name, value)
	}
	endpoint.RawQuery = query.Encode()
	request, err := http.NewRequestWithContext(
		ctx, http.MethodPost, endpoint.String(), strings.NewReader(statement),
	)
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", "text/plain; charset=utf-8")
	request.SetBasicAuth(l.sink.username, l.sink.password)
	response, err := l.sink.client.Do(request)
	if err != nil {
		return fmt.Errorf("propertycatalog: %s: %w", label, err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(response.Body, 4<<10))
		return fmt.Errorf(
			"propertycatalog: %s returned HTTP %d: %s",
			label, response.StatusCode, strings.TrimSpace(string(body)),
		)
	}
	limited := &io.LimitedReader{R: response.Body, N: maxBytes + 1}
	scanner := bufio.NewScanner(limited)
	scanner.Buffer(make([]byte, 64<<10), int(maxBytes))
	rowCount := 0
	for scanner.Scan() {
		if rowCount >= maxRows {
			return fmt.Errorf("propertycatalog: %s exceeds row limit", label)
		}
		if err := visit(rowCount, bytes.Clone(scanner.Bytes())); err != nil {
			return fmt.Errorf("propertycatalog: %s: %w", label, err)
		}
		rowCount++
	}
	if err := scanner.Err(); err != nil {
		return fmt.Errorf("propertycatalog: %s: %w", label, err)
	}
	if limited.N <= 0 {
		return fmt.Errorf("propertycatalog: %s exceeds byte limit", label)
	}
	return nil
}

func decodeCheckpointJSON(line []byte, destination any) error {
	normalized, err := normalizeClickHouseUInt64Fields(line, destination)
	if err != nil {
		return err
	}
	decoder := json.NewDecoder(bytes.NewReader(normalized))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(destination); err != nil {
		return err
	}
	return requireJSONEOF(decoder)
}

// normalizeClickHouseUInt64Fields accepts the two exact JSON encodings emitted
// by ClickHouse for UInt64: a canonical decimal JSON number, or the same
// canonical decimal wrapped in a JSON string when
// output_format_json_quote_64bit_integers is enabled. Only direct uint64 fields
// declared by the destination row are normalized; strings such as the signed
// canonical build-plan document remain byte-for-byte untouched.
func normalizeClickHouseUInt64Fields(line []byte, destination any) ([]byte, error) {
	destinationType := reflect.TypeOf(destination)
	if destinationType == nil || destinationType.Kind() != reflect.Pointer ||
		destinationType.Elem().Kind() != reflect.Struct {
		return nil, errors.New("propertycatalog: checkpoint JSON destination must be a struct pointer")
	}
	uint64Fields := make(map[string]struct{})
	rowType := destinationType.Elem()
	for index := 0; index < rowType.NumField(); index++ {
		field := rowType.Field(index)
		if field.Type.Kind() != reflect.Uint64 {
			continue
		}
		name := strings.Split(field.Tag.Get("json"), ",")[0]
		if name == "" {
			name = field.Name
		}
		if name != "-" {
			uint64Fields[name] = struct{}{}
		}
	}

	decoder := json.NewDecoder(bytes.NewReader(line))
	var object map[string]json.RawMessage
	if err := decoder.Decode(&object); err != nil {
		return nil, err
	}
	if err := requireJSONEOF(decoder); err != nil {
		return nil, err
	}
	for name := range uint64Fields {
		raw, exists := object[name]
		if !exists {
			continue
		}
		canonical, err := canonicalClickHouseUInt64(raw)
		if err != nil {
			return nil, fmt.Errorf("json field %q: %w", name, err)
		}
		object[name] = canonical
	}
	return json.Marshal(object)
}

func canonicalClickHouseUInt64(raw json.RawMessage) (json.RawMessage, error) {
	decimal := []byte(raw)
	if len(decimal) >= 2 && decimal[0] == '"' && decimal[len(decimal)-1] == '"' {
		decimal = decimal[1 : len(decimal)-1]
	}
	if len(decimal) == 0 || (len(decimal) > 1 && decimal[0] == '0') {
		return nil, errors.New("expected canonical UInt64 decimal string or number")
	}
	for _, digit := range decimal {
		if digit < '0' || digit > '9' {
			return nil, errors.New("expected canonical UInt64 decimal string or number")
		}
	}
	value, err := strconv.ParseUint(string(decimal), 10, 64)
	if err != nil || strconv.FormatUint(value, 10) != string(decimal) {
		return nil, errors.New("expected canonical UInt64 decimal string or number")
	}
	return json.RawMessage(strconv.FormatUint(value, 10)), nil
}

// AuthorizeDelivery reads both the unique build reservation and exact latest
// source-stream lease immediately before each data/ledger write. The immutable
// build_lease_sha256 must agree across both; it is never accepted from Kafka.
func (l *ClickHouseCheckpointLoader) AuthorizeDelivery(
	ctx context.Context, request DeliveryLeaseRequest,
) (DeliveryLeaseEvidence, error) {
	if l == nil || l.sink == nil || l.sink.baseURL == nil || l.sink.client == nil ||
		l.now == nil || ctx == nil {
		return DeliveryLeaseEvidence{}, errors.New("propertycatalog: delivery authorization requires a lease-reader context")
	}
	if err := validateDeliveryLeaseRequest(request); err != nil {
		return DeliveryLeaseEvidence{}, err
	}
	if err := ctx.Err(); err != nil {
		return DeliveryLeaseEvidence{}, err
	}
	endpoint := *l.sink.baseURL
	query := endpoint.Query()
	query.Set("database", l.sink.database)
	query.Set("query", deliveryLeaseQuery)
	query.Set("param_organization_id", request.OrganizationID)
	query.Set("param_workspace_id", request.WorkspaceID)
	query.Set("param_catalog_epoch", fmt.Sprintf("%d", request.CatalogEpoch))
	query.Set("param_catalog_revision", fmt.Sprintf("%d", request.CatalogRevision))
	query.Set("param_build_token", request.BuildToken)
	query.Set("param_source_adapter", string(request.SourceAdapter))
	query.Set("param_producer_stream_id", request.ProducerStreamID)
	query.Set("max_execution_time", "2")
	query.Set("max_result_bytes", fmt.Sprintf("%d", maxDeliveryLeaseBytes))
	query.Set("max_result_rows", fmt.Sprintf("%d", maxDeliveryLeaseRows+1))
	query.Set("result_overflow_mode", "throw")
	endpoint.RawQuery = query.Encode()
	httpRequest, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint.String(), http.NoBody)
	if err != nil {
		return DeliveryLeaseEvidence{}, err
	}
	httpRequest.SetBasicAuth(l.sink.username, l.sink.password)
	response, err := l.sink.client.Do(httpRequest)
	if err != nil {
		return DeliveryLeaseEvidence{}, fmt.Errorf("propertycatalog: read source-stream lease: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(response.Body, 4<<10))
		return DeliveryLeaseEvidence{}, fmt.Errorf(
			"propertycatalog: source-stream lease query returned HTTP %d: %s",
			response.StatusCode, strings.TrimSpace(string(body)),
		)
	}
	limited := &io.LimitedReader{R: response.Body, N: maxDeliveryLeaseBytes + 1}
	scanner := bufio.NewScanner(limited)
	scanner.Buffer(make([]byte, 4<<10), maxDeliveryLeaseBytes)
	rows := make([]deliveryLeaseJSON, 0, 4)
	for scanner.Scan() {
		if len(rows) >= maxDeliveryLeaseRows {
			return DeliveryLeaseEvidence{}, errors.New("propertycatalog: source-stream lease response exceeds row limit")
		}
		line := bytes.Clone(scanner.Bytes())
		var row deliveryLeaseJSON
		if err := decodeCheckpointJSON(line, &row); err != nil {
			return DeliveryLeaseEvidence{}, fmt.Errorf("propertycatalog: decode source-stream lease row %d: %w", len(rows), err)
		}
		rows = append(rows, row)
	}
	if err := scanner.Err(); err != nil {
		return DeliveryLeaseEvidence{}, err
	}
	if limited.N <= 0 {
		return DeliveryLeaseEvidence{}, errors.New("propertycatalog: source-stream lease response exceeds byte limit")
	}
	return validateDeliveryLeaseRows(request, rows, l.now().UTC())
}

func validateDeliveryLeaseRequest(request DeliveryLeaseRequest) error {
	if err := validateCanonicalUUID("lease organization", request.OrganizationID); err != nil {
		return err
	}
	if err := validateCanonicalUUID("lease workspace", request.WorkspaceID); err != nil {
		return err
	}
	if err := validateCanonicalUUID("lease build token", request.BuildToken); err != nil {
		return err
	}
	if err := validateCanonicalUUID("lease producer stream", request.ProducerStreamID); err != nil {
		return err
	}
	if request.CatalogEpoch == 0 || request.CatalogRevision == 0 || request.ProjectionVersion == 0 ||
		!validSourceAdapter(request.SourceAdapter) || request.EnvelopeVersion != EnvelopeVersion ||
		request.Sequence == 0 {
		return errors.New("propertycatalog: delivery lease request has an invalid revision or stream identity")
	}
	return nil
}

func validateDeliveryLeaseRows(
	request DeliveryLeaseRequest, rows []deliveryLeaseJSON, now time.Time,
) (DeliveryLeaseEvidence, error) {
	if len(rows) == 0 {
		return DeliveryLeaseEvidence{}, errors.New("propertycatalog: source stream has no authoritative lease evidence")
	}
	reservations := make([]deliveryLeaseJSON, 0, 1)
	streamRows := make([]deliveryLeaseJSON, 0, len(rows))
	for index, row := range rows {
		if row.OrganizationID != request.OrganizationID || row.WorkspaceID != request.WorkspaceID ||
			row.CatalogEpoch != request.CatalogEpoch || row.CatalogRevision != request.CatalogRevision ||
			row.BuildToken != request.BuildToken {
			return DeliveryLeaseEvidence{}, fmt.Errorf("propertycatalog: source-stream lease row %d crosses the requested scope", index)
		}
		if row.Version == 0 || !isLowerSHA256(row.BuildLeaseSHA256) {
			return DeliveryLeaseEvidence{}, errors.New("propertycatalog: source-stream lease has an invalid version or build lease")
		}
		switch {
		case row.EnvelopeVersion == 0:
			if row.SourceAdapter != AdapterSystemManifest || row.ProducerStreamID != request.BuildToken {
				return DeliveryLeaseEvidence{}, errors.New("propertycatalog: build reservation identity is invalid")
			}
			reservations = append(reservations, row)
		case row.EnvelopeVersion == request.EnvelopeVersion:
			if row.SourceAdapter != request.SourceAdapter || row.ProducerStreamID != request.ProducerStreamID {
				return DeliveryLeaseEvidence{}, errors.New("propertycatalog: source-stream lease crosses the requested adapter or stream")
			}
			streamRows = append(streamRows, row)
		default:
			return DeliveryLeaseEvidence{}, errors.New("propertycatalog: source-stream lease has an unsupported envelope version")
		}
	}
	reservation, err := uniqueLatestDeliveryLeaseRow(reservations, "build reservation")
	if err != nil {
		return DeliveryLeaseEvidence{}, err
	}
	row, err := uniqueLatestDeliveryLeaseRow(streamRows, "source stream")
	if err != nil {
		return DeliveryLeaseEvidence{}, err
	}
	reservationLeases := make(map[string]struct{}, 1)
	reservationPlans := make(map[string]struct{}, 1)
	for _, candidate := range reservations {
		reservationLeases[candidate.BuildLeaseSHA256] = struct{}{}
		reservationPlans[candidate.BuildPlanJSON] = struct{}{}
	}
	streamLeases := make(map[string]struct{}, 1)
	streamPlans := make(map[string]struct{}, 1)
	for _, candidate := range streamRows {
		streamLeases[candidate.BuildLeaseSHA256] = struct{}{}
		streamPlans[candidate.BuildPlanJSON] = struct{}{}
	}
	if len(reservationLeases) != 1 || len(streamLeases) != 1 ||
		reservation.BuildLeaseSHA256 != row.BuildLeaseSHA256 {
		return DeliveryLeaseEvidence{}, errors.New("propertycatalog: build lease is missing, mutable, or mismatched across reservation and stream")
	}
	if len(reservationPlans) != 1 || len(streamPlans) != 1 || reservation.BuildPlanJSON != row.BuildPlanJSON {
		return DeliveryLeaseEvidence{}, errors.New("propertycatalog: build plan is missing, mutable, or mismatched across reservation and stream")
	}
	planEvidence, err := validateBuildPlan(row.BuildPlanJSON, row.BuildLeaseSHA256, request)
	if err != nil {
		return DeliveryLeaseEvidence{}, err
	}
	if reservation.ProjectionVersion != request.ProjectionVersion || reservation.DrainDeadline == nil ||
		reservation.FencedAt != nil {
		return DeliveryLeaseEvidence{}, errors.New("propertycatalog: build reservation projection or immutable state is invalid")
	}
	reservationDeadline, err := parseCanonicalLeaseDeadline(*reservation.DrainDeadline)
	if err != nil || !reservationDeadline.After(now) {
		return DeliveryLeaseEvidence{}, errors.New("propertycatalog: build reservation is expired or has a non-canonical deadline")
	}
	if row.ProjectionVersion != request.ProjectionVersion || row.EnvelopeVersion != request.EnvelopeVersion ||
		row.DrainDeadline == nil || row.FencedAt != nil {
		return DeliveryLeaseEvidence{}, errors.New("propertycatalog: source-stream lease projection, envelope, build lease, or fence is invalid")
	}
	deadline, err := parseCanonicalLeaseDeadline(*row.DrainDeadline)
	if err != nil || !deadline.After(now) || !deadline.Equal(reservationDeadline) || row.Status != reservation.Status {
		return DeliveryLeaseEvidence{}, errors.New("propertycatalog: source-stream lease is expired or has a non-canonical deadline")
	}
	switch row.Status {
	case "open":
		if request.Terminal || row.FencedSequence != 0 {
			return DeliveryLeaseEvidence{}, errors.New("propertycatalog: open source-stream lease rejects terminal or fenced delivery")
		}
	case "draining":
		if row.FencedSequence == 0 {
			if row.LastIssuedSequence != 0 || request.Terminal {
				return DeliveryLeaseEvidence{}, errors.New("propertycatalog: drain intent allows only pre-issued non-terminal delivery")
			}
		} else {
			if row.LastIssuedSequence != row.FencedSequence || !request.Terminal ||
				request.Sequence != row.FencedSequence {
				return DeliveryLeaseEvidence{}, errors.New("propertycatalog: draining lease allows only the exact terminal delivery")
			}
		}
	default:
		return DeliveryLeaseEvidence{}, errors.New("propertycatalog: source-stream lease is not open/building or draining")
	}
	planEvidence.BuildLeaseSHA256 = row.BuildLeaseSHA256
	return planEvidence, nil
}

func uniqueLatestDeliveryLeaseRow(rows []deliveryLeaseJSON, label string) (deliveryLeaseJSON, error) {
	if len(rows) == 0 {
		return deliveryLeaseJSON{}, fmt.Errorf("propertycatalog: %s evidence is missing", label)
	}
	maximumVersion := uint64(0)
	for _, row := range rows {
		if row.Version > maximumVersion {
			maximumVersion = row.Version
		}
	}
	latest := make([]deliveryLeaseJSON, 0, 1)
	for _, row := range rows {
		if row.Version == maximumVersion {
			latest = append(latest, row)
		}
	}
	identities := make(map[string]struct{}, len(latest))
	for _, candidate := range latest {
		encoded, err := json.Marshal(candidate)
		if err != nil {
			return deliveryLeaseJSON{}, err
		}
		identities[string(encoded)] = struct{}{}
	}
	if len(identities) != 1 {
		return deliveryLeaseJSON{}, fmt.Errorf("propertycatalog: %s has conflicting latest states", label)
	}
	return latest[0], nil
}

func parseCanonicalLeaseDeadline(value string) (time.Time, error) {
	deadline, err := time.Parse(dateTime64Layout, value)
	if err != nil || deadline.Format(dateTime64Layout) != value {
		return time.Time{}, errors.New("lease deadline is non-canonical")
	}
	return deadline, nil
}

func validBuildPlanRole(value string) bool {
	switch value {
	case "definitions", "values", "hot_values", "source_audit":
		return true
	default:
		return false
	}
}

func validateBuildPlan(
	value, buildLeaseSHA256 string, request DeliveryLeaseRequest,
) (DeliveryLeaseEvidence, error) {
	if len(value) < 2 || len(value) > maxBuildPlanBytes || !isLowerSHA256(buildLeaseSHA256) {
		return DeliveryLeaseEvidence{}, errors.New("propertycatalog: build plan bytes or digest are invalid")
	}
	genericDecoder := json.NewDecoder(strings.NewReader(value))
	genericDecoder.UseNumber()
	var generic any
	if err := genericDecoder.Decode(&generic); err != nil {
		return DeliveryLeaseEvidence{}, fmt.Errorf("propertycatalog: decode build plan: %w", err)
	}
	if err := requireJSONEOF(genericDecoder); err != nil {
		return DeliveryLeaseEvidence{}, err
	}
	canonical, err := json.Marshal(generic)
	if err != nil || !bytes.Equal(canonical, []byte(value)) {
		return DeliveryLeaseEvidence{}, errors.New("propertycatalog: build plan is not canonical JSON")
	}
	digest := sha256.Sum256([]byte(value))
	if fmt.Sprintf("%x", digest) != buildLeaseSHA256 {
		return DeliveryLeaseEvidence{}, errors.New("propertycatalog: build plan digest does not match build lease")
	}
	decoder := json.NewDecoder(strings.NewReader(value))
	decoder.DisallowUnknownFields()
	var plan buildPlanDocumentJSON
	if err := decoder.Decode(&plan); err != nil {
		return DeliveryLeaseEvidence{}, fmt.Errorf("propertycatalog: decode typed build plan: %w", err)
	}
	if err := requireJSONEOF(decoder); err != nil {
		return DeliveryLeaseEvidence{}, err
	}
	if plan.Format != "futureagi.property-catalog-build-plan" || plan.Version != 2 ||
		plan.OrganizationID != request.OrganizationID || plan.WorkspaceID != request.WorkspaceID ||
		plan.CatalogEpoch != request.CatalogEpoch || plan.CatalogRevision != request.CatalogRevision ||
		plan.BuildToken != request.BuildToken || plan.ProjectionVersion != request.ProjectionVersion ||
		len(plan.Streams) != 10 {
		return DeliveryLeaseEvidence{}, errors.New("propertycatalog: build plan does not match the exact revision lease")
	}
	if err := validateRevisionSourceScope(
		plan.SourceScope.ProjectIDs,
		plan.SourceScope.SpanSinceUS,
		plan.SourceScope.SpanUntilUS,
	); err != nil {
		return DeliveryLeaseEvidence{}, fmt.Errorf("propertycatalog: build plan source scope: %w", err)
	}
	adapterRoles := make(map[SourceAdapter]map[string]int, 7)
	streamKeys := make(map[string]struct{}, len(plan.Streams))
	declaredRole := ""
	previousSortKey := ""
	for index, stream := range plan.Streams {
		if !validSourceAdapter(stream.SourceAdapter) || !validBuildPlanRole(stream.Role) ||
			validateCanonicalUUID("build plan producer stream", stream.ProducerStreamID) != nil ||
			!validBuildPlanCutoffLabel(stream.SourceCutoff.Label) || stream.SourceCutoff.Value == 0 {
			return DeliveryLeaseEvidence{}, fmt.Errorf("propertycatalog: build plan stream %d is invalid", index)
		}
		if stream.SourceAdapter != AdapterSpanAttribute && stream.Role != "definitions" {
			return DeliveryLeaseEvidence{}, errors.New("propertycatalog: only span_attribute may declare a non-definition role")
		}
		sortKey := string(stream.SourceAdapter) + "\x00" + stream.Role + "\x00" + stream.ProducerStreamID
		if index > 0 && sortKey <= previousSortKey {
			return DeliveryLeaseEvidence{}, errors.New("propertycatalog: build plan streams are not uniquely canonical-sorted")
		}
		previousSortKey = sortKey
		key := string(stream.SourceAdapter) + "\x00" + stream.ProducerStreamID
		if _, duplicate := streamKeys[key]; duplicate {
			return DeliveryLeaseEvidence{}, errors.New("propertycatalog: build plan contains a duplicate source stream")
		}
		streamKeys[key] = struct{}{}
		roles := adapterRoles[stream.SourceAdapter]
		if roles == nil {
			roles = make(map[string]int)
			adapterRoles[stream.SourceAdapter] = roles
		}
		roles[stream.Role]++
		if stream.SourceAdapter == request.SourceAdapter && stream.ProducerStreamID == request.ProducerStreamID {
			declaredRole = stream.Role
		}
	}
	for _, adapter := range []SourceAdapter{
		AdapterSystemManifest, AdapterSpanAttribute, AdapterEvalTemplate, AdapterEvalConfig,
		AdapterSimulationEvalConfig, AdapterAnnotationLabel, AdapterDatasetColumn,
	} {
		roles := adapterRoles[adapter]
		if adapter == AdapterSpanAttribute {
			if len(roles) != 4 || roles["definitions"] != 1 || roles["values"] != 1 ||
				roles["hot_values"] != 1 || roles["source_audit"] != 1 {
				return DeliveryLeaseEvidence{}, errors.New("propertycatalog: build plan has an invalid span role inventory")
			}
		} else if len(roles) != 1 || roles["definitions"] != 1 {
			return DeliveryLeaseEvidence{}, fmt.Errorf("propertycatalog: build plan lacks one definition stream for %s", adapter)
		}
	}
	if declaredRole == "" {
		return DeliveryLeaseEvidence{}, errors.New("propertycatalog: current source stream is absent from the build plan")
	}
	return DeliveryLeaseEvidence{
		StreamRole:  declaredRole,
		ProjectIDs:  append([]string(nil), plan.SourceScope.ProjectIDs...),
		SpanSinceUS: plan.SourceScope.SpanSinceUS,
		SpanUntilUS: plan.SourceScope.SpanUntilUS,
	}, nil
}

func validBuildPlanCutoffLabel(value string) bool {
	if len(value) < 1 || len(value) > 64 || value[0] < 'a' || value[0] > 'z' {
		return false
	}
	for _, char := range value[1:] {
		if (char < 'a' || char > 'z') && (char < '0' || char > '9') && char != '_' {
			return false
		}
	}
	return true
}

func validateCheckpointInventory(rows []checkpointInventoryJSON) ([]checkpointInventoryJSON, error) {
	if len(rows) == 0 {
		return []checkpointInventoryJSON{}, nil
	}
	reservations := make(map[string]checkpointInventoryJSON)
	plans := make(map[string]buildPlanDocumentJSON)
	candidates := make(map[string]checkpointInventoryJSON)
	for index, row := range rows {
		if err := validateCheckpointReservation(row); err != nil {
			return nil, fmt.Errorf("propertycatalog: checkpoint inventory reservation row %d: %w", index, err)
		}
		scope := checkpointInventoryScopeKey(row)
		if prior, exists := reservations[scope]; exists {
			if checkpointReservationIdentity(prior) != checkpointReservationIdentity(row) {
				return nil, fmt.Errorf("propertycatalog: newest reservation scope %s has conflicting identities", scope)
			}
		} else {
			plan, err := checkpointInventoryBuildPlan(row)
			if err != nil {
				return nil, fmt.Errorf("propertycatalog: newest reservation scope %s: %w", scope, err)
			}
			reservations[scope] = row
			plans[scope] = plan
		}
		if err := validateCheckpointActivationEvidence(row); err != nil {
			return nil, fmt.Errorf("propertycatalog: checkpoint inventory activation row %d: %w", index, err)
		}
		if row.StreamEvidenceRows == 0 {
			continue
		}
		if row.StreamSourceAdapter == AdapterSystemManifest &&
			row.StreamProducerStreamID == row.BuildToken && row.StreamEnvelopeVersion == 0 {
			if err := validateCheckpointReservationStream(row); err != nil {
				return nil, fmt.Errorf("propertycatalog: checkpoint inventory reservation state row %d: %w", index, err)
			}
			continue
		}
		if err := validateCheckpointCandidate(row); err != nil {
			return nil, fmt.Errorf("propertycatalog: checkpoint inventory stream row %d: %w", index, err)
		}
		if err := validateCheckpointEvidence(row); err != nil {
			return nil, fmt.Errorf("propertycatalog: checkpoint inventory checkpoint row %d: %w", index, err)
		}
		key := checkpointInventoryStreamKey(row)
		if _, duplicate := candidates[key]; duplicate {
			return nil, fmt.Errorf("propertycatalog: checkpoint inventory duplicates stream %s", key)
		}
		candidates[key] = row
	}
	for scope, reservation := range reservations {
		plan := plans[scope]
		expected := make(map[string]struct{}, len(plan.Streams))
		for _, stream := range plan.Streams {
			expected[checkpointPlanStreamKey(reservation, stream.SourceAdapter, stream.ProducerStreamID)] = struct{}{}
		}
		actual := make(map[string]struct{}, len(expected))
		for key, candidate := range candidates {
			if checkpointInventoryScopeKey(candidate) == scope {
				actual[key] = struct{}{}
			}
		}
		if len(actual) != len(expected) {
			return nil, fmt.Errorf("propertycatalog: newest reservation scope %s lacks its exact build-plan stream inventory", scope)
		}
		for key := range expected {
			if _, exists := actual[key]; !exists {
				return nil, fmt.Errorf("propertycatalog: newest reservation scope %s omits planned stream %s", scope, key)
			}
		}
	}
	result := make([]checkpointInventoryJSON, 0, len(candidates))
	for _, row := range candidates {
		result = append(result, row)
	}
	sort.Slice(result, func(i, j int) bool {
		return checkpointInventoryStreamKey(result[i]) < checkpointInventoryStreamKey(result[j])
	})
	return result, nil
}

func validateCheckpointReservation(row checkpointInventoryJSON) error {
	if err := validateCanonicalUUID("checkpoint inventory organization", row.OrganizationID); err != nil {
		return err
	}
	if err := validateCanonicalUUID("checkpoint inventory workspace", row.WorkspaceID); err != nil {
		return err
	}
	if err := validateCanonicalUUID("checkpoint inventory build token", row.BuildToken); err != nil {
		return err
	}
	if row.CatalogEpoch == 0 || row.CatalogRevision == 0 || row.ReservationProjectionVersion == 0 ||
		row.ReservationSourceAdapter != AdapterSystemManifest ||
		row.ReservationProducerStreamID != row.BuildToken || row.ReservationEnvelopeVersion != 0 ||
		row.ReservationVersion == 0 || row.ReservationStateVariants != 1 ||
		!isLowerSHA256(row.ReservationBuildLeaseSHA256) {
		return errors.New("reservation identity, version, or latest state is invalid or ambiguous")
	}
	switch row.ReservationStatus {
	case "open", "draining", "fenced":
		return nil
	default:
		return errors.New("newest reservation is not open, draining, or fenced")
	}
}

func validateCheckpointReservationStream(row checkpointInventoryJSON) error {
	if row.StreamStateVariants != 1 || row.StreamVersion != row.ReservationVersion ||
		row.StreamProjectionVersion != row.ReservationProjectionVersion ||
		row.StreamBuildPlanJSON != row.ReservationBuildPlanJSON ||
		row.StreamBuildLeaseSHA256 != row.ReservationBuildLeaseSHA256 ||
		row.StreamStatus != row.ReservationStatus {
		return errors.New("reservation source-stream row disagrees with the selected reservation")
	}
	return nil
}

func checkpointInventoryBuildPlan(row checkpointInventoryJSON) (buildPlanDocumentJSON, error) {
	decoder := json.NewDecoder(strings.NewReader(row.ReservationBuildPlanJSON))
	decoder.DisallowUnknownFields()
	var plan buildPlanDocumentJSON
	if err := decoder.Decode(&plan); err != nil {
		return buildPlanDocumentJSON{}, fmt.Errorf("decode reservation build plan: %w", err)
	}
	if err := requireJSONEOF(decoder); err != nil {
		return buildPlanDocumentJSON{}, err
	}
	if len(plan.Streams) == 0 {
		return buildPlanDocumentJSON{}, errors.New("reservation build plan has no streams")
	}
	first := plan.Streams[0]
	_, err := validateBuildPlan(
		row.ReservationBuildPlanJSON,
		row.ReservationBuildLeaseSHA256,
		DeliveryLeaseRequest{
			OrganizationID: row.OrganizationID, WorkspaceID: row.WorkspaceID,
			CatalogEpoch: row.CatalogEpoch, CatalogRevision: row.CatalogRevision,
			BuildToken: row.BuildToken, ProjectionVersion: row.ReservationProjectionVersion,
			SourceAdapter: first.SourceAdapter, ProducerStreamID: first.ProducerStreamID,
			EnvelopeVersion: EnvelopeVersion, Sequence: 1,
		},
	)
	if err != nil {
		return buildPlanDocumentJSON{}, err
	}
	return plan, nil
}

func validateCheckpointCandidate(row checkpointInventoryJSON) error {
	if row.StreamStateVariants != 1 || row.StreamVersion == 0 || row.StreamProjectionVersion == 0 ||
		row.StreamProjectionVersion != row.ReservationProjectionVersion ||
		row.StreamEnvelopeVersion != EnvelopeVersion || !validSourceAdapter(row.StreamSourceAdapter) ||
		row.StreamBuildPlanJSON != row.ReservationBuildPlanJSON ||
		row.StreamBuildLeaseSHA256 != row.ReservationBuildLeaseSHA256 ||
		!isLowerSHA256(row.StreamTerminalPayloadSHA256) {
		return errors.New("source-stream identity, projection, lease, or latest state is invalid or ambiguous")
	}
	if err := validateCanonicalUUID("checkpoint inventory producer stream", row.StreamProducerStreamID); err != nil {
		return err
	}
	if _, err := validateBuildPlan(
		row.StreamBuildPlanJSON,
		row.StreamBuildLeaseSHA256,
		DeliveryLeaseRequest{
			OrganizationID: row.OrganizationID, WorkspaceID: row.WorkspaceID,
			CatalogEpoch: row.CatalogEpoch, CatalogRevision: row.CatalogRevision,
			BuildToken: row.BuildToken, ProjectionVersion: row.StreamProjectionVersion,
			SourceAdapter: row.StreamSourceAdapter, ProducerStreamID: row.StreamProducerStreamID,
			EnvelopeVersion: row.StreamEnvelopeVersion, Sequence: 1,
		},
	); err != nil {
		return err
	}
	if (row.StreamFirstSequence == 0) != (row.StreamLastSequence == 0) ||
		row.StreamFirstSequence > 1 || row.StreamMaxContiguousSequence > row.StreamLastSequence ||
		row.StreamLastSequence > row.StreamLastIssuedSequence ||
		row.StreamFencedSequence > row.StreamLastIssuedSequence {
		return errors.New("source-stream sequence evidence is internally inconsistent")
	}
	switch row.StreamStatus {
	case "open":
		if row.StreamFencedSequence != 0 {
			return errors.New("open source stream is fenced")
		}
	case "draining":
		if row.StreamFencedSequence != 0 && row.StreamFencedSequence != row.StreamLastIssuedSequence {
			return errors.New("draining source stream has a partial fence")
		}
	case "complete", "fenced":
		if row.StreamFirstSequence != 1 || row.StreamLastSequence == 0 ||
			row.StreamMaxContiguousSequence != row.StreamLastSequence ||
			row.StreamLastIssuedSequence != row.StreamLastSequence ||
			row.StreamFencedSequence != row.StreamLastSequence || row.StreamGapCount != 0 {
			return errors.New("terminal source-stream state lacks an exact contiguous fence")
		}
	case "gap", "failed":
	default:
		return errors.New("source stream has an unsupported lifecycle status")
	}
	return nil
}

func validateCheckpointEvidence(row checkpointInventoryJSON) error {
	if row.CheckpointEvidenceRows == 0 {
		if row.CheckpointStateVariants != 0 || row.CheckpointVersion != 0 {
			return errors.New("absent checkpoint has nonzero evidence")
		}
		return nil
	}
	if row.CheckpointStateVariants != 1 || row.CheckpointVersion == 0 ||
		row.CheckpointProjectionVersion != row.StreamProjectionVersion || row.CheckpointTerminal > 1 ||
		!isLowerSHA256(row.CheckpointTerminalPayloadSHA256) {
		return errors.New("latest checkpoint state is invalid or ambiguous")
	}
	firstIsNull := row.CheckpointNullFirstSequences == row.CheckpointEvidenceRows
	lastIsNull := row.CheckpointNullLastSequences == row.CheckpointEvidenceRows
	firstIsPartial := row.CheckpointNullFirstSequences != 0 && !firstIsNull
	lastIsPartial := row.CheckpointNullLastSequences != 0 && !lastIsNull
	if firstIsNull != lastIsNull || firstIsPartial || lastIsPartial {
		return errors.New("checkpoint nullable sequence evidence is ambiguous")
	}
	if lastIsNull {
		if row.CheckpointFirstSequence != 0 || row.CheckpointLastSequence != 0 || row.CheckpointTerminal != 0 {
			return errors.New("empty checkpoint carries sequence or terminal evidence")
		}
	} else if row.CheckpointFirstSequence != 1 || row.CheckpointLastSequence == 0 {
		return errors.New("checkpoint does not start at sequence one")
	}
	switch row.CheckpointStatus {
	case "pending", "running", "complete", "gap", "failed":
	default:
		return errors.New("checkpoint has an unsupported status")
	}
	return nil
}

func validateCheckpointActivationEvidence(row checkpointInventoryJSON) error {
	if row.ActivationEvidenceRows == 0 {
		if row.ActivationStateVariants != 0 || row.ActivationVersion != 0 {
			return errors.New("absent activation has nonzero evidence")
		}
		return nil
	}
	if row.ActivationStateVariants != 1 || row.ActivationVersion == 0 ||
		row.ActivationProjectionVersion != row.ReservationProjectionVersion {
		return errors.New("latest activation state is invalid or ambiguous")
	}
	switch row.ActivationStatus {
	case "building", "active", "disabled":
		return nil
	default:
		return errors.New("activation has an unsupported status")
	}
}

func validateEmptyCheckpointStream(row checkpointInventoryJSON) error {
	emptyCheckpoint := row.CheckpointEvidenceRows == 0 ||
		((row.CheckpointStatus == "pending" || row.CheckpointStatus == "running") &&
			row.CheckpointTerminal == 0 &&
			row.CheckpointNullFirstSequences == row.CheckpointEvidenceRows &&
			row.CheckpointNullLastSequences == row.CheckpointEvidenceRows &&
			row.CheckpointLastIssuedSequence == 0 && row.CheckpointFencedSequence == 0 &&
			row.CheckpointGapCount == 0)
	preissuedFirstDrainFence := row.StreamStatus == "draining" &&
		row.StreamFirstSequence == 0 && row.StreamLastSequence == 0 &&
		row.StreamMaxContiguousSequence == 0 && row.StreamLastIssuedSequence == 1 &&
		row.StreamFencedSequence == 1 && row.StreamGapCount == 0 &&
		emptyCheckpoint &&
		row.ActivationStatus != "active"
	if preissuedFirstDrainFence {
		// A hot producer binds the exact terminal sequence in the draining
		// source-stream row before publishing it. A consumer that starts in
		// that narrow window must be allowed to consume sequence one; the
		// normal delivery/checkpoint proof becomes mandatory immediately after
		// the physical row exists. No later sequence, gap, or active revision is
		// accepted without that ledger proof.
		return nil
	}
	if row.StreamFirstSequence != 0 || row.StreamLastSequence != 0 ||
		row.StreamMaxContiguousSequence != 0 || row.StreamLastIssuedSequence != 0 ||
		row.StreamFencedSequence != 0 ||
		(row.CheckpointEvidenceRows > 0 && row.CheckpointNullLastSequences != row.CheckpointEvidenceRows) ||
		row.ActivationStatus == "active" {
		return fmt.Errorf(
			"propertycatalog: stream %s has durable state but no physical delivery rows",
			checkpointInventoryStreamKey(row),
		)
	}
	return nil
}

func validateCheckpointStreamProof(
	row checkpointInventoryJSON, proof checkpointStreamProofJSON,
) (StreamCheckpoint, error) {
	if proof.SequenceRows == 0 || proof.SequenceRows > maxCheckpointSequencesPerStream ||
		proof.FirstSequence != 1 || proof.LastSequence != proof.SequenceRows ||
		proof.DistinctSequences != proof.SequenceRows {
		return StreamCheckpoint{}, errors.New("delivery sequence is not bounded and contiguous from one")
	}
	if proof.ProjectionVersions != 1 || proof.MaxProjectionVersionsAtSequence != 1 ||
		proof.MaxIdentityVariants != 1 || proof.ConflictSequences != 0 {
		return StreamCheckpoint{}, errors.New("delivery sequence has an identity or projection conflict")
	}
	if proof.ChainBreaks != 0 || proof.InvalidWireSequences != 0 ||
		proof.InvalidOutcomeSequences != 0 || proof.InvalidTerminalSequences != 0 {
		return StreamCheckpoint{}, errors.New("delivery stream has a broken chain, wire identity, outcome, or terminal fence")
	}
	if proof.TailProjectionVersion != row.StreamProjectionVersion ||
		proof.TailEnvelopeFormat != EnvelopeFormat || proof.TailEnvelopeVersion != EnvelopeVersion ||
		!isLowerSHA256(proof.TailEnvelopeID) || !isLowerSHA256(proof.TailPayloadSHA256) ||
		proof.TailTerminal > 1 {
		return StreamCheckpoint{}, errors.New("delivery tail does not match the reserved stream projection or wire contract")
	}
	switch proof.TerminalSequences {
	case 0:
		if proof.TerminalSequence != 0 || proof.TailTerminal != 0 {
			return StreamCheckpoint{}, errors.New("nonterminal delivery stream has terminal tail evidence")
		}
	case 1:
		if proof.TerminalSequence != proof.LastSequence || proof.TailTerminal != 1 {
			return StreamCheckpoint{}, errors.New("terminal delivery is not the exact final sequence")
		}
	default:
		return StreamCheckpoint{}, errors.New("delivery stream contains multiple terminal sequences")
	}
	if row.StreamLastSequence > proof.LastSequence ||
		row.StreamMaxContiguousSequence > proof.LastSequence {
		return StreamCheckpoint{}, errors.New("source-stream state advances beyond the physical delivery ledger")
	}
	if row.StreamFencedSequence > 0 && row.StreamStatus == "draining" {
		if proof.LastSequence != row.StreamFencedSequence &&
			(proof.LastSequence == ^uint64(0) || proof.LastSequence+1 != row.StreamFencedSequence) {
			return StreamCheckpoint{}, errors.New("draining source-stream fence is not the tail or its exact next terminal")
		}
		if proof.LastSequence == row.StreamFencedSequence && proof.TailTerminal != 1 {
			return StreamCheckpoint{}, errors.New("delivered draining fence is not terminal")
		}
	}
	if row.StreamStatus == "complete" || row.StreamStatus == "fenced" {
		if proof.LastSequence != row.StreamLastSequence || proof.TailTerminal != 1 ||
			proof.TailPayloadSHA256 != row.StreamTerminalPayloadSHA256 || proof.GapSequences != 0 {
			return StreamCheckpoint{}, errors.New("terminal source-stream state disagrees with the physical delivery tail")
		}
	}
	if row.StreamGapCount > 0 && proof.GapSequences == 0 {
		return StreamCheckpoint{}, errors.New("source-stream gap state is absent from the delivery proof")
	}
	if row.CheckpointEvidenceRows > 0 && row.CheckpointNullLastSequences == 0 {
		if row.CheckpointLastSequence > proof.LastSequence {
			return StreamCheckpoint{}, errors.New("checkpoint advances beyond the physical delivery ledger")
		}
		if row.CheckpointTerminal == 1 &&
			(row.CheckpointLastSequence != proof.LastSequence || proof.TailTerminal != 1 ||
				row.CheckpointTerminalPayloadSHA256 != proof.TailPayloadSHA256) {
			return StreamCheckpoint{}, errors.New("terminal checkpoint disagrees with the physical delivery tail")
		}
		if row.CheckpointGapCount > 0 && proof.GapSequences == 0 {
			return StreamCheckpoint{}, errors.New("checkpoint gap state is absent from the delivery proof")
		}
	}
	if row.ActivationStatus == "active" &&
		(row.StreamStatus != "complete" || row.CheckpointTerminal != 1 || proof.TailTerminal != 1) {
		return StreamCheckpoint{}, errors.New("active revision lacks terminal stream and checkpoint evidence")
	}
	checkpoint := StreamCheckpoint{
		OrganizationID: row.OrganizationID, WorkspaceID: row.WorkspaceID,
		CatalogEpoch: row.CatalogEpoch, CatalogRevision: row.CatalogRevision,
		BuildToken: row.BuildToken, ProjectionVersion: proof.TailProjectionVersion,
		SourceAdapter: row.StreamSourceAdapter, ProducerStreamID: row.StreamProducerStreamID,
		Sequence: proof.LastSequence, Terminal: proof.TailTerminal == 1,
		GapSeen: proof.GapSequences > 0, PayloadSHA256: proof.TailPayloadSHA256,
		EnvelopeID: proof.TailEnvelopeID,
	}
	if err := validateCheckpoint(checkpoint); err != nil {
		return StreamCheckpoint{}, err
	}
	return checkpoint, nil
}

func checkpointInventoryScopeKey(row checkpointInventoryJSON) string {
	return strings.Join([]string{
		row.OrganizationID, row.WorkspaceID, fmt.Sprintf("%d", row.CatalogEpoch),
	}, "\x00")
}

func checkpointReservationIdentity(row checkpointInventoryJSON) string {
	return strings.Join([]string{
		fmt.Sprintf("%d", row.CatalogRevision), row.BuildToken,
		fmt.Sprintf("%d", row.ReservationProjectionVersion), string(row.ReservationSourceAdapter),
		row.ReservationProducerStreamID, fmt.Sprintf("%d", row.ReservationEnvelopeVersion),
		row.ReservationBuildPlanJSON, row.ReservationBuildLeaseSHA256, row.ReservationStatus,
		fmt.Sprintf("%d", row.ReservationVersion), fmt.Sprintf("%d", row.ReservationStateVariants),
	}, "\x00")
}

func checkpointInventoryStreamKey(row checkpointInventoryJSON) string {
	return checkpointPlanStreamKey(row, row.StreamSourceAdapter, row.StreamProducerStreamID)
}

func checkpointPlanStreamKey(row checkpointInventoryJSON, adapter SourceAdapter, streamID string) string {
	return strings.Join([]string{
		row.OrganizationID, row.WorkspaceID, fmt.Sprintf("%d", row.CatalogEpoch),
		fmt.Sprintf("%d", row.CatalogRevision), row.BuildToken, string(adapter), streamID,
	}, "\x00")
}
