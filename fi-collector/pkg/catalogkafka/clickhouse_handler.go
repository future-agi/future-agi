package catalogkafka

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strconv"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogwriter"
)

const clickHouseDateTime64Layout = "2006-01-02 15:04:05.000000"

const (
	directDeliveryTransport          = "direct"
	kafkaDeliveryTransport           = "kafka"
	defaultClickHouseDeliveryTimeout = 5 * time.Second
	maxClickHouseDeliveryTimeout     = 10 * time.Second
	directDeliveryKafkaPartition     = int32(-1)
	directDeliveryKafkaOffset        = int64(-1)
)

// DirectPublisherStateDirectoryName is the dedicated durable sequencing-state
// directory beneath the catalog spool. Keeping it separate prevents direct
// and Kafka transports from accidentally sharing a source chain.
const DirectPublisherStateDirectoryName = "direct-publisher-state"

// ClickHouseDeliverySink is deliberately restricted to the two catalog data
// tables and their delivery ledger. catalogwriter.ClickHouseSink satisfies
// this interface; no canonical span-table writer does.
type ClickHouseDeliverySink interface {
	InsertCatalog(context.Context, catalogwriter.Table, []map[string]any) error
	InsertDelivery(context.Context, []map[string]any) error
}

// ClickHouseDeliveryHandler writes every validated data chunk before the
// delivery-ledger row that acknowledges it. Exact duplicate envelopes already
// have a durable ledger row, so they safely refresh only that ReplacingMergeTree
// row instead of reapplying AggregatingMergeTree input.
type ClickHouseDeliveryHandler struct {
	sink            ClickHouseDeliverySink
	deliveryTimeout time.Duration
	now             func() time.Time
}

// DirectClickHouseEnvelopePublisher is the synchronous, bounded direct-mode
// endpoint for SpoolPublisher's durable version-3 envelope sequencer. It uses
// exactly the same chunk validation and delivery-ledger contract as the Kafka
// consumer, while recording the transport as direct and Kafka offsets as -1.
// The publisher itself owns no sequence state: SpoolPublisher must persist the
// exact pending envelope before calling Publish and its ACK before removing the
// outer catalog WAL entry.
type DirectClickHouseEnvelopePublisher struct {
	handler *ClickHouseDeliveryHandler
}

var _ EnvelopePublisher = (*DirectClickHouseEnvelopePublisher)(nil)

var _ DeliveryHandler = (*ClickHouseDeliveryHandler)(nil)

func NewClickHouseDeliveryHandler(
	sink ClickHouseDeliverySink, deliveryTimeout time.Duration,
) (*ClickHouseDeliveryHandler, error) {
	if sink == nil {
		return nil, errors.New("catalogkafka: ClickHouse delivery handler requires a sink")
	}
	if deliveryTimeout == 0 {
		deliveryTimeout = defaultClickHouseDeliveryTimeout
	}
	if deliveryTimeout < 0 || deliveryTimeout > maxClickHouseDeliveryTimeout {
		return nil, fmt.Errorf(
			"catalogkafka: ClickHouse delivery timeout must be in (0,%s]",
			maxClickHouseDeliveryTimeout,
		)
	}
	return &ClickHouseDeliveryHandler{
		sink: sink, deliveryTimeout: deliveryTimeout, now: time.Now,
	}, nil
}

// NewDirectClickHouseEnvelopePublisher constructs the direct version-3
// delivery endpoint without performing a write. A zero timeout receives the
// conservative five-second default; explicit values are hard-capped at ten
// seconds for the complete envelope (all chunks plus its ledger row).
func NewDirectClickHouseEnvelopePublisher(
	sink ClickHouseDeliverySink, deliveryTimeout time.Duration,
) (*DirectClickHouseEnvelopePublisher, error) {
	handler, err := NewClickHouseDeliveryHandler(sink, deliveryTimeout)
	if err != nil {
		return nil, err
	}
	return &DirectClickHouseEnvelopePublisher{handler: handler}, nil
}

type decodedCatalogChunk struct {
	table catalogwriter.Table
	index uint16
	rows  []map[string]any
}

// Deliver implements DeliveryHandler. All chunks are decoded and their exact
// catalog-only columns/project/epoch are checked before the first write. This
// prevents a bad later chunk from causing a partial earlier-table insert.
func (h *ClickHouseDeliveryHandler) Deliver(ctx context.Context, delivery Delivery) error {
	return h.deliverEnvelopeBounded(
		ctx, delivery.Envelope, delivery.ExactDuplicate,
		kafkaDeliveryTransport, delivery.Partition, delivery.Offset,
	)
}

// Publish writes a byte-identity-bound envelope directly to ClickHouse. The
// whole multi-request operation shares one deadline so a slow endpoint cannot
// extend the retry pass by one timeout per chunk.
func (p *DirectClickHouseEnvelopePublisher) Publish(
	ctx context.Context, envelope WireEnvelope,
) error {
	if p == nil || p.handler == nil {
		return errors.New("catalogkafka: nil direct ClickHouse envelope publisher")
	}
	return p.handler.deliverEnvelopeBounded(
		ctx, envelope, false, directDeliveryTransport,
		directDeliveryKafkaPartition, directDeliveryKafkaOffset,
	)
}

// Close implements EnvelopePublisher lifecycle symmetry. The direct publisher
// owns no network client; its ClickHouse sink is request scoped.
func (p *DirectClickHouseEnvelopePublisher) Close() {}

func (h *ClickHouseDeliveryHandler) deliverEnvelopeBounded(
	ctx context.Context,
	envelope WireEnvelope,
	exactDuplicate bool,
	transport string,
	kafkaPartition int32,
	kafkaOffset int64,
) error {
	if h == nil || h.sink == nil || h.now == nil || h.deliveryTimeout <= 0 ||
		h.deliveryTimeout > maxClickHouseDeliveryTimeout {
		return errors.New("catalogkafka: nil or invalid ClickHouse delivery handler")
	}
	if ctx == nil {
		return errors.New("catalogkafka: nil ClickHouse delivery context")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	deliveryCtx, cancel := context.WithTimeout(ctx, h.deliveryTimeout)
	defer cancel()
	return h.deliverEnvelope(
		deliveryCtx, envelope, exactDuplicate, transport, kafkaPartition, kafkaOffset,
	)
}

func (h *ClickHouseDeliveryHandler) deliverEnvelope(
	ctx context.Context,
	envelope WireEnvelope,
	exactDuplicate bool,
	transport string,
	kafkaPartition int32,
	kafkaOffset int64,
) error {
	if h == nil || h.sink == nil || h.now == nil {
		return errors.New("catalogkafka: nil ClickHouse delivery handler")
	}
	if ctx == nil {
		return errors.New("catalogkafka: nil ClickHouse delivery context")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	switch transport {
	case kafkaDeliveryTransport:
		if kafkaPartition < 0 || kafkaOffset < 0 {
			return errors.New("catalogkafka: delivery partition/offset must be non-negative")
		}
	case directDeliveryTransport:
		if kafkaPartition != directDeliveryKafkaPartition || kafkaOffset != directDeliveryKafkaOffset {
			return errors.New("catalogkafka: direct delivery must use sentinel Kafka offsets")
		}
	default:
		return fmt.Errorf("catalogkafka: unsupported delivery transport %q", transport)
	}

	snapshot := envelope.Snapshot()
	if snapshot.Format != EnvelopeFormat || snapshot.Version != EnvelopeVersion || snapshot.EnvelopeID == "" {
		return errors.New("catalogkafka: delivery contains an invalid wire envelope")
	}
	chunks, err := decodeDeliveryChunks(ctx, snapshot)
	if err != nil {
		return err
	}

	if !exactDuplicate {
		for _, chunk := range chunks {
			if err := ctx.Err(); err != nil {
				return err
			}
			if err := h.sink.InsertCatalog(ctx, chunk.table, chunk.rows); err != nil {
				return fmt.Errorf(
					"catalogkafka: insert %s chunk %d: %w", chunk.table, chunk.index, err,
				)
			}
		}
	}
	if err := ctx.Err(); err != nil {
		return err
	}

	deliveredAt := h.now().UTC()
	if deliveredAt.UnixNano() < 0 {
		return errors.New("catalogkafka: delivery clock is before the Unix epoch")
	}
	row := map[string]any{
		"envelope_format":         snapshot.Format,
		"envelope_version":        snapshot.Version,
		"envelope_id":             snapshot.EnvelopeID,
		"project_id":              snapshot.ProjectID,
		"catalog_epoch":           snapshot.CatalogEpoch,
		"producer_stream_id":      snapshot.ProducerStreamID,
		"sequence":                snapshot.Sequence,
		"payload_sha256":          snapshot.PayloadSHA256,
		"previous_payload_sha256": snapshot.PreviousPayloadSHA256,
		"source_batch_digest":     snapshot.Payload.SourceBatchDigest,
		"outcome":                 string(snapshot.Payload.Outcome),
		"gap_reasons":             append([]string{}, snapshot.Payload.GapReasons...),
		"source_min_start":        snapshot.Payload.SourceMinStart,
		"source_max_start":        snapshot.Payload.SourceMaxStart,
		"source_rows":             snapshot.Payload.SourceRows,
		"key_rows":                snapshot.Payload.KeyRows,
		"value_rows":              snapshot.Payload.ValueRows,
		"transport":               transport,
		"kafka_partition":         kafkaPartition,
		"kafka_offset":            kafkaOffset,
		"delivered_at":            deliveredAt.Format(clickHouseDateTime64Layout),
		"_version":                uint64(deliveredAt.UnixNano()),
	}
	if err := h.sink.InsertDelivery(ctx, []map[string]any{row}); err != nil {
		return fmt.Errorf("catalogkafka: insert delivery ledger: %w", err)
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	return nil
}

func decodeDeliveryChunks(ctx context.Context, snapshot EnvelopeSnapshot) ([]decodedCatalogChunk, error) {
	chunks := make([]decodedCatalogChunk, 0, len(snapshot.Payload.Chunks))
	var sourceKindShape *bool
	for _, chunk := range snapshot.Payload.Chunks {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		var table catalogwriter.Table
		var columns, legacyColumns map[string]struct{}
		switch chunk.Table {
		case KeyTable:
			table, columns, legacyColumns = catalogwriter.KeyTable, keyWireColumns, legacyKeyWireColumns
		case ValueTable:
			table, columns, legacyColumns = catalogwriter.ValueTable, valueWireColumns, legacyValueWireColumns
		default:
			return nil, fmt.Errorf("catalogkafka: delivery chunk %d targets forbidden table %q", chunk.Index, chunk.Table)
		}
		rows, err := decodeJSONEachRows(ctx, chunk.JSONEachRow, chunk.RowCount)
		if err != nil {
			return nil, fmt.Errorf("catalogkafka: decode delivery chunk %d: %w", chunk.Index, err)
		}
		for rowIndex, row := range rows {
			withSourceKind, validationErr := validateDecodedCatalogRow(
				row, columns, legacyColumns, snapshot.ProjectID, snapshot.CatalogEpoch,
			)
			if validationErr != nil {
				return nil, fmt.Errorf(
					"catalogkafka: delivery chunk %d row %d: %w", chunk.Index, rowIndex, validationErr,
				)
			}
			if sourceKindShape == nil {
				shape := withSourceKind
				sourceKindShape = &shape
			} else if *sourceKindShape != withSourceKind {
				return nil, fmt.Errorf(
					"catalogkafka: delivery chunk %d row %d mixes legacy and source-kind row shapes",
					chunk.Index, rowIndex,
				)
			}
		}
		chunks = append(chunks, decodedCatalogChunk{table: table, index: chunk.Index, rows: rows})
	}
	return chunks, nil
}

func decodeJSONEachRows(
	ctx context.Context, encoded []byte, rowCount uint32,
) ([]map[string]any, error) {
	decoder := json.NewDecoder(bytes.NewReader(encoded))
	decoder.UseNumber()
	rows := make([]map[string]any, 0, rowCount)
	for {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		var row map[string]any
		err := decoder.Decode(&row)
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, err
		}
		if row == nil {
			return nil, errors.New("JSONEachRow value must be an object")
		}
		rows = append(rows, row)
		if len(rows) > int(rowCount) {
			return nil, errors.New("JSONEachRow contains more rows than declared")
		}
	}
	if len(rows) != int(rowCount) {
		return nil, fmt.Errorf("JSONEachRow contains %d rows, declared %d", len(rows), rowCount)
	}
	return rows, nil
}

func validateDecodedCatalogRow(
	row map[string]any,
	columns map[string]struct{},
	legacyColumns map[string]struct{},
	projectID string,
	epoch uint16,
) (bool, error) {
	withSourceKind, ok := exactWireColumnShape(row, columns, legacyColumns)
	if !ok {
		return false, errors.New("row does not have an exact legacy or source-kind catalog shape")
	}
	if withSourceKind {
		sourceKind, ok := row["source_kind"].(string)
		if !ok || (sourceKind != "custom_attribute" && sourceKind != "system_attribute") {
			return false, errors.New("row source_kind is unsupported")
		}
	}
	rowProject, ok := row["project_id"].(string)
	if !ok || rowProject != projectID {
		return false, errors.New("row project does not match envelope project")
	}
	rowEpoch, ok := row["catalog_epoch"].(json.Number)
	if !ok {
		return false, errors.New("row epoch is not an integer JSON number")
	}
	parsedEpoch, err := strconv.ParseUint(string(rowEpoch), 10, 16)
	if err != nil || uint16(parsedEpoch) != epoch {
		return false, errors.New("row epoch does not match envelope epoch")
	}
	return withSourceKind, nil
}
