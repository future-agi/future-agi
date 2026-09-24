package catalogwriter

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
)

// WireJob is the transport-neutral immutable payload shared by direct and
// Kafka modes. It contains only catalog rows/metadata and cannot retain a
// canonical span map. Version 3 Kafka envelopes wrap these exact bytes.
type WireJob struct {
	KeyRows      []map[string]any `json:"key_rows"`
	ValueRows    []map[string]any `json:"value_rows"`
	EncodedBytes int              `json:"encoded_bytes"`
	Metadata     JobMetadata      `json:"metadata"`
}

// ExportWireJob returns a defensive JSON-compatible copy.
func ExportWireJob(job Job) WireJob {
	keys := make([]map[string]any, len(job.keyRows))
	for index, row := range job.keyRows {
		keys[index] = keyRowMap(row)
	}
	values := make([]map[string]any, len(job.valueRows))
	for index, row := range job.valueRows {
		values[index] = valueRowMap(row)
	}
	return WireJob{
		KeyRows: keys, ValueRows: values, EncodedBytes: job.encodedBytes,
		Metadata: job.Metadata(),
	}
}

// ImportWireJob strictly reconstructs an opaque Job and applies Writer's full
// row/metadata/byte validation. Unknown fields and loose number coercions are
// rejected before any ClickHouse call.
func (w *Writer) ImportWireJob(wire WireJob) (Job, error) {
	if w == nil || !w.Enabled() {
		return Job{}, errors.New("catalogwriter: wire import requires enabled writer")
	}
	job := Job{
		keyRows: make([]keyRow, len(wire.KeyRows)), valueRows: make([]valueRow, len(wire.ValueRows)),
		encodedBytes: wire.EncodedBytes, metadata: cloneJobMetadata(wire.Metadata),
	}
	for index, row := range wire.KeyRows {
		if err := remarshalStrict(row, &job.keyRows[index]); err != nil {
			return Job{}, fmt.Errorf("catalogwriter: import key row %d: %w", index, err)
		}
	}
	for index, row := range wire.ValueRows {
		if err := remarshalStrict(row, &job.valueRows[index]); err != nil {
			return Job{}, fmt.Errorf("catalogwriter: import value row %d: %w", index, err)
		}
	}
	if err := w.validateJob(job); err != nil {
		return Job{}, err
	}
	return job, nil
}

func remarshalStrict(source map[string]any, destination any) error {
	encoded, err := json.Marshal(source)
	if err != nil {
		return err
	}
	decoder := json.NewDecoder(strings.NewReader(string(encoded)))
	decoder.DisallowUnknownFields()
	return decoder.Decode(destination)
}
