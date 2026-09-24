package propertycatalog

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"time"
)

const (
	TransportDirect    = "direct"
	TransportKafka     = "kafka"
	TransportReconcile = "reconcile"

	// DefaultDeliveryHandlerTimeout is the bounded wall used when callers do
	// not provide one for the complete data-plus-ledger transaction.
	DefaultDeliveryHandlerTimeout = 5 * time.Second
	// DefaultDeliveryTransportTimeout is shared by the Kafka producer and
	// ClickHouse transport. Deployments may lower it but never exceed the
	// reviewed end-to-end ceiling.
	DefaultDeliveryTransportTimeout = 10 * time.Second
	// MaxDeliveryTimeout is the hard safety ceiling for every delivery stage.
	MaxDeliveryTimeout = 10 * time.Second
)

// DeliverySink exposes only the two allowlisted data tables and the dedicated
// delivery ledger. There is no generic table-name insertion escape hatch.
type DeliverySink interface {
	InsertPropertyCatalog(context.Context, Table, []map[string]any) error
	InsertPropertyCatalogDelivery(context.Context, []map[string]any) error
}

// DeliveryLeaseRequest is the complete immutable identity that must still be
// writable immediately before an irreversible catalog write. The guard also
// validates the authoritative source-stream manifest and live deadline for
// this exact identity; those are control-plane evidence, not Kafka claims.
type DeliveryLeaseRequest struct {
	OrganizationID    string
	WorkspaceID       string
	CatalogEpoch      uint16
	CatalogRevision   uint64
	BuildToken        string
	ProjectionVersion uint16
	SourceAdapter     SourceAdapter
	ProducerStreamID  string
	EnvelopeVersion   uint16
	Sequence          uint64
	Terminal          bool
}

type DeliveryLeaseEvidence struct {
	BuildLeaseSHA256 string
	StreamRole       string
	ProjectIDs       []string
	SpanSinceUS      uint64
	SpanUntilUS      uint64
}

type DeliveryLeaseGuard interface {
	AuthorizeDelivery(context.Context, DeliveryLeaseRequest) (DeliveryLeaseEvidence, error)
}

type Delivery struct {
	Envelope       WireEnvelope
	ExactDuplicate bool
	Transport      string
	KafkaPartition int32
	KafkaOffset    int64
}

type DeliveryHandler struct {
	sink    DeliverySink
	guard   DeliveryLeaseGuard
	timeout time.Duration
	now     func() time.Time
}

func NewDeliveryHandler(
	sink DeliverySink, guard DeliveryLeaseGuard, timeout time.Duration,
) (*DeliveryHandler, error) {
	if sink == nil || guard == nil {
		return nil, errors.New("propertycatalog: delivery handler requires a sink and authoritative lease guard")
	}
	if timeout == 0 {
		timeout = DefaultDeliveryHandlerTimeout
	}
	if timeout < 0 || timeout > MaxDeliveryTimeout {
		return nil, fmt.Errorf("propertycatalog: delivery timeout must be in (0,%s]", MaxDeliveryTimeout)
	}
	return &DeliveryHandler{sink: sink, guard: guard, timeout: timeout, now: time.Now}, nil
}

// Deliver validates and decodes the complete payload before the first write.
// Data chunks are written in envelope order and the ledger is always last.
func (h *DeliveryHandler) Deliver(ctx context.Context, delivery Delivery) error {
	if h == nil || h.sink == nil || h.guard == nil || h.now == nil ||
		h.timeout <= 0 || h.timeout > MaxDeliveryTimeout {
		return errors.New("propertycatalog: nil or invalid delivery handler")
	}
	if ctx == nil {
		return errors.New("propertycatalog: nil delivery context")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	deliveryCtx, cancel := context.WithTimeout(ctx, h.timeout)
	defer cancel()
	return h.deliver(deliveryCtx, delivery)
}

type decodedChunk struct {
	table Table
	index uint16
	rows  []map[string]any
}

func (h *DeliveryHandler) deliver(ctx context.Context, delivery Delivery) error {
	snapshot := delivery.Envelope.Snapshot()
	if snapshot.Format != EnvelopeFormat || snapshot.Version != EnvelopeVersion || snapshot.EnvelopeID == "" {
		return errors.New("propertycatalog: delivery contains an invalid envelope")
	}
	switch delivery.Transport {
	case TransportKafka:
		if delivery.KafkaPartition < 0 || delivery.KafkaOffset < 0 {
			return errors.New("propertycatalog: Kafka delivery requires non-negative partition/offset")
		}
	case TransportDirect, TransportReconcile:
		if delivery.KafkaPartition != -1 || delivery.KafkaOffset != -1 {
			return errors.New("propertycatalog: non-Kafka delivery requires -1 Kafka sentinels")
		}
	default:
		return errors.New("propertycatalog: unsupported delivery transport")
	}

	scope := Scope{
		OrganizationID: snapshot.OrganizationID, WorkspaceID: snapshot.WorkspaceID,
		CatalogEpoch: snapshot.CatalogEpoch, CatalogRevision: snapshot.CatalogRevision,
		BuildToken:        snapshot.BuildToken,
		ProjectionVersion: snapshot.ProjectionVersion, SourceAdapter: snapshot.SourceAdapter,
		SourceVersion: snapshot.SourceVersion, SourceFingerprint: snapshot.SourceFingerprint,
		ProducerStreamID: snapshot.ProducerStreamID, Sequence: snapshot.Sequence,
	}
	leaseRequest := DeliveryLeaseRequest{
		OrganizationID: snapshot.OrganizationID, WorkspaceID: snapshot.WorkspaceID,
		CatalogEpoch: snapshot.CatalogEpoch, CatalogRevision: snapshot.CatalogRevision,
		BuildToken: snapshot.BuildToken, ProjectionVersion: snapshot.ProjectionVersion,
		SourceAdapter: snapshot.SourceAdapter, ProducerStreamID: snapshot.ProducerStreamID,
		EnvelopeVersion: snapshot.Version, Sequence: snapshot.Sequence, Terminal: snapshot.Terminal,
	}
	boundBuildLease := ""
	boundStreamRole := ""
	var boundProjectIDs []string
	var boundSpanSinceUS uint64
	var boundSpanUntilUS uint64
	authorize := func() error {
		evidence, err := h.guard.AuthorizeDelivery(ctx, leaseRequest)
		if err != nil {
			return err
		}
		if !isLowerSHA256(evidence.BuildLeaseSHA256) {
			return errors.New("propertycatalog: lease guard returned an invalid build lease identity")
		}
		if !validBuildPlanRole(evidence.StreamRole) {
			return errors.New("propertycatalog: lease guard returned an invalid stream role")
		}
		if err := validateRevisionSourceScope(
			evidence.ProjectIDs, evidence.SpanSinceUS, evidence.SpanUntilUS,
		); err != nil {
			return fmt.Errorf("propertycatalog: lease guard returned an invalid source scope: %w", err)
		}
		if boundBuildLease == "" {
			boundBuildLease = evidence.BuildLeaseSHA256
			boundStreamRole = evidence.StreamRole
			boundProjectIDs = append([]string(nil), evidence.ProjectIDs...)
			boundSpanSinceUS = evidence.SpanSinceUS
			boundSpanUntilUS = evidence.SpanUntilUS
			return nil
		}
		if evidence.BuildLeaseSHA256 != boundBuildLease || evidence.StreamRole != boundStreamRole ||
			evidence.SpanSinceUS != boundSpanSinceUS || evidence.SpanUntilUS != boundSpanUntilUS ||
			!sameStrings(evidence.ProjectIDs, boundProjectIDs) {
			return errors.New("propertycatalog: build lease or stream role changed during delivery, or source scope changed")
		}
		return nil
	}
	decoded := make([]decodedChunk, 0, len(snapshot.Payload.Chunks))
	for _, chunk := range snapshot.Payload.Chunks {
		if err := ctx.Err(); err != nil {
			return err
		}
		if chunk.Table != DefinitionTable && chunk.Table != AttributeValueTable {
			return fmt.Errorf("propertycatalog: delivery chunk %d targets forbidden table %q", chunk.Index, chunk.Table)
		}
		digest := sha256.Sum256(chunk.JSONEachRow)
		if hex.EncodeToString(digest[:]) != chunk.EncodedSHA256 {
			return fmt.Errorf("propertycatalog: delivery chunk %d digest mismatch", chunk.Index)
		}
		rows, _, err := validateChunkRows(ChunkInput{
			Table: chunk.Table, Index: chunk.Index, RowCount: chunk.RowCount,
			JSONEachRow: chunk.JSONEachRow,
		}, scope)
		if err != nil || rows != uint64(chunk.RowCount) {
			return fmt.Errorf("propertycatalog: prevalidate delivery chunk %d: %w", chunk.Index, err)
		}
		mapped, err := strictDecodeJSONEachRow(chunk.JSONEachRow, chunk.Table)
		if err != nil || len(mapped) != int(chunk.RowCount) {
			return fmt.Errorf("propertycatalog: decode delivery chunk %d: %w", chunk.Index, err)
		}
		decoded = append(decoded, decodedChunk{table: chunk.Table, index: chunk.Index, rows: mapped})
	}
	// SequenceValidator only sets ExactDuplicate after proving the complete
	// durable ledger identity for this stream position. A replay of that exact
	// record is already committed catalog state, so it must remain ACK-able
	// after the build lease is fenced and must never refresh data or ledger rows.
	if delivery.ExactDuplicate {
		return ctx.Err()
	}

	for _, chunk := range decoded {
		if err := ctx.Err(); err != nil {
			return err
		}
		if err := authorize(); err != nil {
			return fmt.Errorf("propertycatalog: authorize %s chunk %d: %w", chunk.table, chunk.index, err)
		}
		if err := validateDeliveryRolePayload(boundStreamRole, snapshot.Payload, decoded); err != nil {
			return err
		}
		if err := validateHotDeliverySourceScope(
			boundStreamRole, boundProjectIDs, boundSpanSinceUS, boundSpanUntilUS, decoded,
		); err != nil {
			return err
		}
		if err := h.sink.InsertPropertyCatalog(ctx, chunk.table, chunk.rows); err != nil {
			return fmt.Errorf("propertycatalog: insert %s chunk %d: %w", chunk.table, chunk.index, err)
		}
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if err := authorize(); err != nil {
		return fmt.Errorf("propertycatalog: authorize delivery ledger: %w", err)
	}
	if err := validateDeliveryRolePayload(boundStreamRole, snapshot.Payload, decoded); err != nil {
		return err
	}
	if err := validateHotDeliverySourceScope(
		boundStreamRole, boundProjectIDs, boundSpanSinceUS, boundSpanUntilUS, decoded,
	); err != nil {
		return err
	}
	deliveredAt := h.now().UTC()
	if deliveredAt.UnixNano() < 0 {
		return errors.New("propertycatalog: delivery clock precedes Unix epoch")
	}
	terminal := uint8(0)
	if snapshot.Terminal {
		terminal = 1
	}
	row := map[string]any{
		"organization_id":         snapshot.OrganizationID,
		"workspace_id":            snapshot.WorkspaceID,
		"catalog_epoch":           snapshot.CatalogEpoch,
		"catalog_revision":        snapshot.CatalogRevision,
		"build_token":             snapshot.BuildToken,
		"projection_version":      snapshot.ProjectionVersion,
		"source_adapter":          string(snapshot.SourceAdapter),
		"producer_stream_id":      snapshot.ProducerStreamID,
		"sequence":                snapshot.Sequence,
		"terminal":                terminal,
		"envelope_format":         snapshot.Format,
		"envelope_version":        snapshot.Version,
		"envelope_id":             snapshot.EnvelopeID,
		"payload_sha256":          snapshot.PayloadSHA256,
		"previous_payload_sha256": snapshot.PreviousPayloadSHA256,
		"source_batch_digest":     snapshot.Payload.SourceBatchDigest,
		"outcome":                 string(snapshot.Payload.Outcome),
		"gap_reasons":             append([]string{}, snapshot.Payload.GapReasons...),
		"source_rows":             snapshot.Payload.SourceRows,
		"definition_rows":         snapshot.Payload.DefinitionRows,
		"value_rows":              snapshot.Payload.ValueRows,
		"tombstone_rows":          snapshot.Payload.TombstoneRows,
		"transport":               delivery.Transport,
		"kafka_partition":         delivery.KafkaPartition,
		"kafka_offset":            delivery.KafkaOffset,
		"delivered_at":            deliveredAt.Format(dateTime64Layout),
		"_version":                uint64(deliveredAt.UnixNano()),
	}
	if err := h.sink.InsertPropertyCatalogDelivery(ctx, []map[string]any{row}); err != nil {
		return fmt.Errorf("propertycatalog: insert delivery ledger: %w", err)
	}
	return ctx.Err()
}

// validateDeliveryRolePayload binds the authoritative build-plan role to both
// the signed aggregate counts and every decoded wire chunk. The check is run
// before each irreversible write so a role cannot be used as a generic route
// into either allowlisted data table.
func validateDeliveryRolePayload(role string, payload Payload, chunks []decodedChunk) error {
	for _, chunk := range chunks {
		switch role {
		case "definitions":
			if chunk.table != DefinitionTable {
				return fmt.Errorf("propertycatalog: definitions stream targets forbidden table %q", chunk.table)
			}
		case "values", "hot_values":
			if chunk.table != AttributeValueTable {
				return fmt.Errorf("propertycatalog: %s stream targets forbidden table %q", role, chunk.table)
			}
		case "source_audit":
			return errors.New("propertycatalog: source_audit stream cannot contain data chunks")
		default:
			return errors.New("propertycatalog: delivery has an invalid build-plan stream role")
		}
	}
	switch role {
	case "definitions":
		if payload.ValueRows != 0 {
			return errors.New("propertycatalog: definitions stream cannot contain value rows")
		}
	case "values", "hot_values":
		if payload.DefinitionRows != 0 || payload.TombstoneRows != 0 {
			return fmt.Errorf("propertycatalog: %s stream cannot contain definition or tombstone rows", role)
		}
	case "source_audit":
		if payload.DefinitionRows != 0 || payload.ValueRows != 0 || payload.TombstoneRows != 0 || len(chunks) != 0 {
			return errors.New("propertycatalog: source_audit stream must contain zero data rows and chunks")
		}
	default:
		return errors.New("propertycatalog: delivery has an invalid build-plan stream role")
	}
	return nil
}

func validateHotDeliverySourceScope(
	role string, projectIDs []string, spanSinceUS, spanUntilUS uint64, chunks []decodedChunk,
) error {
	if role != "hot_values" {
		return nil
	}
	for _, chunk := range chunks {
		for rowIndex, row := range chunk.rows {
			projectID, projectOK := row["project_id"].(string)
			firstText, firstOK := row["first_seen"].(string)
			lastText, lastOK := row["last_seen"].(string)
			if !projectOK || !firstOK || !lastOK {
				return fmt.Errorf(
					"propertycatalog: hot value chunk %d row %d lacks source-scope evidence",
					chunk.index, rowIndex,
				)
			}
			firstSeen, firstErr := time.Parse(dateTime64Layout, firstText)
			lastSeen, lastErr := time.Parse(dateTime64Layout, lastText)
			if firstErr != nil || lastErr != nil ||
				firstSeen.Format(dateTime64Layout) != firstText || lastSeen.Format(dateTime64Layout) != lastText {
				return fmt.Errorf(
					"propertycatalog: hot value chunk %d row %d has non-canonical source time",
					chunk.index, rowIndex,
				)
			}
			if err := validateRevisionSourceObservation(
				projectIDs, spanSinceUS, spanUntilUS, projectID, firstSeen, lastSeen,
			); err != nil {
				return fmt.Errorf(
					"propertycatalog: hot value chunk %d row %d crosses build-plan scope: %w",
					chunk.index, rowIndex, err,
				)
			}
		}
	}
	return nil
}

func sameStrings(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	for index := range left {
		if left[index] != right[index] {
			return false
		}
	}
	return true
}
