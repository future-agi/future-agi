package catalogkafka

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogwriter"
)

const (
	defaultChunkRows  = 2_000
	defaultChunkBytes = 256 << 10
)

// PayloadInputFromWireJob converts the transport-neutral catalogwriter payload
// to strict JSONEachRow chunks without introducing a second set of row types.
// A v3 envelope is project-scoped, so mixed/unscoped WireJobs fail closed and
// must be split by the caller before transport selection.
func PayloadInputFromWireJob(
	job catalogwriter.WireJob, projectID, sourceBatchDigest string, maxChunkRows, maxChunkBytes int,
) (PayloadInput, error) {
	metadata := job.Metadata
	if metadata.CatalogEpoch == 0 || metadata.UnscopedRejectedSpans != 0 || len(metadata.Projects) != 1 ||
		metadata.Projects[0].ProjectID != projectID {
		return PayloadInput{}, errors.New("catalogkafka: WireJob must contain exactly one scoped project")
	}
	project := metadata.Projects[0]
	if metadata.KeyRows != len(job.KeyRows) || metadata.ValueRows != len(job.ValueRows) ||
		project.KeyRows != len(job.KeyRows) || project.ValueRows != len(job.ValueRows) {
		return PayloadInput{}, errors.New("catalogkafka: WireJob row metadata mismatch")
	}
	chunks, computedBytes, err := chunkWireJob(job, projectID, metadata.CatalogEpoch, maxChunkRows, maxChunkBytes)
	if err != nil {
		return PayloadInput{}, err
	}
	if computedBytes != job.EncodedBytes || computedBytes != metadata.EncodedBytes {
		return PayloadInput{}, fmt.Errorf(
			"catalogkafka: WireJob encoded bytes mismatch: rows %d job %d metadata %d",
			computedBytes, job.EncodedBytes, metadata.EncodedBytes,
		)
	}
	gaps := append([]string(nil), project.GapReasons...)
	outcome := OutcomeCommitted
	if project.RejectedSpans != 0 || project.IncompleteSpans != 0 || project.RowsOmitted != 0 || len(gaps) != 0 {
		outcome = OutcomeGap
		if len(gaps) == 0 {
			gaps = []string{"wire_job_incomplete"}
		}
	}
	return PayloadInput{
		SourceBatchDigest: sourceBatchDigest, Outcome: outcome, GapReasons: gaps,
		SourceMinStart: project.MinSpanStart, SourceMaxStart: project.MaxSpanStart,
		SourceRows: uint64(project.InputSpans), KeyRows: uint64(project.KeyRows),
		ValueRows: uint64(project.ValueRows), Chunks: chunks,
	}, nil
}

func chunkWireJob(
	job catalogwriter.WireJob, projectID string, epoch uint16, maxChunkRows, maxChunkBytes int,
) ([]ChunkInput, int, error) {
	if maxChunkRows == 0 {
		maxChunkRows = defaultChunkRows
	}
	if maxChunkBytes == 0 {
		maxChunkBytes = defaultChunkBytes
	}
	if maxChunkRows < 1 || maxChunkRows > MaxRowsPerChunk ||
		maxChunkBytes < 1 || maxChunkBytes > MaxChunkJSONEachRowBytes {
		return nil, 0, errors.New("catalogkafka: WireJob chunk bounds are outside hard limits")
	}
	chunks := make([]ChunkInput, 0)
	totalBytes := 0
	var sourceKindShape *bool
	appendRows := func(
		table Table,
		rows []map[string]any,
		columns map[string]struct{},
		legacyColumns map[string]struct{},
	) error {
		for start := 0; start < len(rows); {
			var body bytes.Buffer
			count := 0
			for start+count < len(rows) && count < maxChunkRows {
				row := rows[start+count]
				withSourceKind, err := validateWireRow(
					row, columns, legacyColumns, projectID, epoch,
				)
				if err != nil {
					return fmt.Errorf("%s row %d: %w", table, start+count, err)
				}
				if sourceKindShape == nil {
					shape := withSourceKind
					sourceKindShape = &shape
				} else if *sourceKindShape != withSourceKind {
					return errors.New("catalogkafka: WireJob mixes legacy and source-kind row shapes")
				}
				encoded, err := encodeWireRow(row)
				if err != nil {
					return fmt.Errorf("%s row %d: %w", table, start+count, err)
				}
				if len(encoded) > maxChunkBytes-body.Len() {
					if count == 0 {
						return fmt.Errorf("catalogkafka: %s row %d exceeds chunk byte limit", table, start)
					}
					break
				}
				_, _ = body.Write(encoded)
				count++
			}
			if len(chunks) >= MaxChunks {
				return fmt.Errorf("catalogkafka: WireJob requires more than %d chunks", MaxChunks)
			}
			chunks = append(chunks, ChunkInput{
				Table: table, Index: uint16(len(chunks)), RowCount: uint32(count),
				JSONEachRow: bytes.Clone(body.Bytes()),
			})
			totalBytes += body.Len()
			start += count
		}
		return nil
	}
	if err := appendRows(KeyTable, job.KeyRows, keyWireColumns, legacyKeyWireColumns); err != nil {
		return nil, 0, err
	}
	if err := appendRows(ValueTable, job.ValueRows, valueWireColumns, legacyValueWireColumns); err != nil {
		return nil, 0, err
	}
	return chunks, totalBytes, nil
}

var keyWireColumns = columnNames(
	"project_id", "source_kind", "attribute_key", "key_folded", "attribute_type",
	"first_seen", "last_seen", "catalog_epoch",
)

var valueWireColumns = columnNames(
	"project_id", "source_kind", "attribute_key", "attribute_type", "value_fingerprint",
	"value_json", "value_search_text", "first_seen", "last_seen", "catalog_epoch",
)

var legacyKeyWireColumns = columnNames(
	"project_id", "attribute_key", "key_folded", "attribute_type",
	"first_seen", "last_seen", "catalog_epoch",
)

var legacyValueWireColumns = columnNames(
	"project_id", "attribute_key", "attribute_type", "value_fingerprint",
	"value_json", "value_search_text", "first_seen", "last_seen", "catalog_epoch",
)

func columnNames(names ...string) map[string]struct{} {
	out := make(map[string]struct{}, len(names))
	for _, name := range names {
		out[name] = struct{}{}
	}
	return out
}

func validateWireRow(
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
	if row["project_id"] != projectID {
		return false, errors.New("row project does not match envelope project")
	}
	switch value := row["catalog_epoch"].(type) {
	case uint16:
		if value != epoch {
			return false, errors.New("row epoch does not match envelope epoch")
		}
	case int:
		if value != int(epoch) {
			return false, errors.New("row epoch does not match envelope epoch")
		}
	case float64:
		if value != float64(epoch) {
			return false, errors.New("row epoch does not match envelope epoch")
		}
	default:
		return false, errors.New("row epoch has an unsupported type")
	}
	return withSourceKind, nil
}

func exactWireColumnShape(
	row map[string]any,
	columns map[string]struct{},
	legacyColumns map[string]struct{},
) (bool, bool) {
	for _, shape := range []struct {
		columns        map[string]struct{}
		withSourceKind bool
	}{{columns, true}, {legacyColumns, false}} {
		if len(row) != len(shape.columns) {
			continue
		}
		exact := true
		for column := range row {
			if _, exists := shape.columns[column]; !exists {
				exact = false
				break
			}
		}
		if exact {
			return shape.withSourceKind, true
		}
	}
	return false, false
}

func encodeWireRow(row map[string]any) ([]byte, error) {
	var out bytes.Buffer
	encoder := json.NewEncoder(&out)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(row); err != nil {
		return nil, err
	}
	return out.Bytes(), nil
}
