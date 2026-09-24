package propertycatalog

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"unicode"
)

type ChunkInput struct {
	Table       Table
	Index       uint16
	RowCount    uint32
	JSONEachRow []byte
}

type PayloadInput struct {
	SourceBatchDigest string
	Outcome           Outcome
	GapReasons        []string
	SourceRows        uint64
	DefinitionRows    uint64
	ValueRows         uint64
	TombstoneRows     uint64
	Chunks            []ChunkInput
}

type EnvelopeInput struct {
	OrganizationID        string
	WorkspaceID           string
	CatalogEpoch          uint16
	CatalogRevision       uint64
	BuildToken            string
	ProjectionVersion     uint16
	SourceAdapter         SourceAdapter
	SourceVersion         uint64
	SourceFingerprint     string
	ProducerStreamID      string
	Sequence              uint64
	Terminal              bool
	PreviousPayloadSHA256 string
	Payload               PayloadInput
}

type Chunk struct {
	Table         Table
	Index         uint16
	RowCount      uint32
	EncodedSHA256 string
	JSONEachRow   []byte
}

type Payload struct {
	SourceBatchDigest string
	Outcome           Outcome
	GapReasons        []string
	SourceRows        uint64
	DefinitionRows    uint64
	ValueRows         uint64
	TombstoneRows     uint64
	Chunks            []Chunk
}

type EnvelopeSnapshot struct {
	Format                string
	Version               uint16
	EnvelopeID            string
	OrganizationID        string
	WorkspaceID           string
	CatalogEpoch          uint16
	CatalogRevision       uint64
	BuildToken            string
	ProjectionVersion     uint16
	SourceAdapter         SourceAdapter
	SourceVersion         uint64
	SourceFingerprint     string
	ProducerStreamID      string
	Sequence              uint64
	Terminal              bool
	PreviousPayloadSHA256 string
	PayloadSHA256         string
	Payload               Payload
}

type chunkJSON struct {
	Table         Table  `json:"table"`
	Index         uint16 `json:"index"`
	RowCount      uint32 `json:"row_count"`
	EncodedSHA256 string `json:"encoded_sha256"`
	JSONEachRow   []byte `json:"json_each_row"`
}

type payloadJSON struct {
	SourceBatchDigest string      `json:"source_batch_digest"`
	Outcome           Outcome     `json:"outcome"`
	GapReasons        []string    `json:"gap_reasons"`
	SourceRows        uint64      `json:"source_rows"`
	DefinitionRows    uint64      `json:"definition_rows"`
	ValueRows         uint64      `json:"value_rows"`
	TombstoneRows     uint64      `json:"tombstone_rows"`
	Chunks            []chunkJSON `json:"chunks"`
}

type unsignedEnvelopeJSON struct {
	Format                string        `json:"format"`
	Version               uint16        `json:"version"`
	OrganizationID        string        `json:"organization_id"`
	WorkspaceID           string        `json:"workspace_id"`
	CatalogEpoch          uint16        `json:"catalog_epoch"`
	CatalogRevision       uint64        `json:"catalog_revision"`
	BuildToken            string        `json:"build_token"`
	ProjectionVersion     uint16        `json:"projection_version"`
	SourceAdapter         SourceAdapter `json:"source_adapter"`
	SourceVersion         uint64        `json:"source_version"`
	SourceFingerprint     string        `json:"source_fingerprint"`
	ProducerStreamID      string        `json:"producer_stream_id"`
	Sequence              uint64        `json:"sequence"`
	Terminal              bool          `json:"terminal"`
	PreviousPayloadSHA256 string        `json:"previous_payload_sha256"`
	PayloadSHA256         string        `json:"payload_sha256"`
	Payload               payloadJSON   `json:"payload"`
}

type envelopeJSON struct {
	Format                string        `json:"format"`
	Version               uint16        `json:"version"`
	EnvelopeID            string        `json:"envelope_id"`
	OrganizationID        string        `json:"organization_id"`
	WorkspaceID           string        `json:"workspace_id"`
	CatalogEpoch          uint16        `json:"catalog_epoch"`
	CatalogRevision       uint64        `json:"catalog_revision"`
	BuildToken            string        `json:"build_token"`
	ProjectionVersion     uint16        `json:"projection_version"`
	SourceAdapter         SourceAdapter `json:"source_adapter"`
	SourceVersion         uint64        `json:"source_version"`
	SourceFingerprint     string        `json:"source_fingerprint"`
	ProducerStreamID      string        `json:"producer_stream_id"`
	Sequence              uint64        `json:"sequence"`
	Terminal              bool          `json:"terminal"`
	PreviousPayloadSHA256 string        `json:"previous_payload_sha256"`
	PayloadSHA256         string        `json:"payload_sha256"`
	Payload               payloadJSON   `json:"payload"`
}

// WireEnvelope is immutable to callers; every byte slice returned is copied.
type WireEnvelope struct {
	document envelopeJSON
	raw      []byte
}

// BuildPayload deterministically chunks typed rows and computes exact counts.
func BuildPayload(
	definitions []DefinitionRow,
	values []AttributeValueRow,
	maxRows, maxBytes int,
	sourceRows uint64,
	sourceBatchDigest string,
) (PayloadInput, error) {
	if maxRows <= 0 || maxRows > MaxRowsPerChunk || maxBytes <= 0 || maxBytes > MaxChunkBytes {
		return PayloadInput{}, errors.New("propertycatalog: invalid chunk bounds")
	}
	chunks := make([]ChunkInput, 0)
	tombstones := uint64(0)
	for _, row := range definitions {
		if row.IsDeleted == 1 {
			tombstones++
		}
	}
	appendTypedRows := func(table Table, count int, encode func(int) ([]byte, error)) error {
		for start := 0; start < count; {
			var body bytes.Buffer
			rows := 0
			for start+rows < count && rows < maxRows {
				encoded, err := encode(start + rows)
				if err != nil {
					return err
				}
				if len(encoded)+1 > maxBytes {
					return fmt.Errorf("propertycatalog: %s row %d exceeds chunk byte limit", table, start+rows)
				}
				if rows != 0 && body.Len()+len(encoded)+1 > maxBytes {
					break
				}
				body.Write(encoded)
				body.WriteByte('\n')
				rows++
			}
			if len(chunks) >= MaxChunks {
				return errors.New("propertycatalog: chunk count exceeds limit")
			}
			chunks = append(chunks, ChunkInput{
				Table: table, Index: uint16(len(chunks)), RowCount: uint32(rows),
				JSONEachRow: bytes.Clone(body.Bytes()),
			})
			start += rows
		}
		return nil
	}
	if err := appendTypedRows(DefinitionTable, len(definitions), func(index int) ([]byte, error) {
		return encodeCanonicalRow(definitions[index])
	}); err != nil {
		return PayloadInput{}, err
	}
	if err := appendTypedRows(AttributeValueTable, len(values), func(index int) ([]byte, error) {
		return encodeCanonicalRow(values[index])
	}); err != nil {
		return PayloadInput{}, err
	}
	return PayloadInput{
		SourceBatchDigest: sourceBatchDigest, Outcome: OutcomeCommitted,
		GapReasons: []string{}, SourceRows: sourceRows, DefinitionRows: uint64(len(definitions)),
		ValueRows: uint64(len(values)), TombstoneRows: tombstones, Chunks: chunks,
	}, nil
}

func NewWireEnvelope(input EnvelopeInput) (WireEnvelope, error) {
	if err := validateCanonicalUUID("build token", input.BuildToken); err != nil {
		return WireEnvelope{}, err
	}
	scope := Scope{
		OrganizationID: input.OrganizationID, WorkspaceID: input.WorkspaceID,
		CatalogEpoch: input.CatalogEpoch, CatalogRevision: input.CatalogRevision,
		BuildToken:        input.BuildToken,
		ProjectionVersion: input.ProjectionVersion, SourceAdapter: input.SourceAdapter,
		SourceVersion: input.SourceVersion, SourceFingerprint: input.SourceFingerprint,
		ProducerStreamID: input.ProducerStreamID, Sequence: input.Sequence,
	}
	if err := validateScope(scope); err != nil {
		return WireEnvelope{}, err
	}
	if !isLowerSHA256(input.PreviousPayloadSHA256) {
		return WireEnvelope{}, errors.New("propertycatalog: previous payload digest must be lowercase SHA-256")
	}
	if input.Sequence == 1 && input.PreviousPayloadSHA256 != ZeroSHA256 {
		return WireEnvelope{}, errors.New("propertycatalog: sequence one must use the zero previous digest")
	}
	payload, err := buildPayload(input.Payload, scope)
	if err != nil {
		return WireEnvelope{}, err
	}
	if input.Terminal && (payload.SourceRows != 0 || payload.DefinitionRows != 0 || payload.ValueRows != 0 ||
		payload.TombstoneRows != 0 || len(payload.Chunks) != 0 || payload.Outcome != OutcomeCommitted ||
		len(payload.GapReasons) != 0) {
		return WireEnvelope{}, errors.New("propertycatalog: terminal envelope must contain one empty committed payload")
	}
	payloadBytes, err := marshalCanonicalWireJSON(payload)
	if err != nil {
		return WireEnvelope{}, fmt.Errorf("propertycatalog: encode payload: %w", err)
	}
	payloadDigest := sha256.Sum256(payloadBytes)
	unsigned := unsignedEnvelopeJSON{
		Format: EnvelopeFormat, Version: EnvelopeVersion,
		OrganizationID: input.OrganizationID, WorkspaceID: input.WorkspaceID,
		CatalogEpoch: input.CatalogEpoch, CatalogRevision: input.CatalogRevision,
		BuildToken:        input.BuildToken,
		ProjectionVersion: input.ProjectionVersion, SourceAdapter: input.SourceAdapter,
		SourceVersion: input.SourceVersion, SourceFingerprint: input.SourceFingerprint,
		ProducerStreamID: input.ProducerStreamID, Sequence: input.Sequence, Terminal: input.Terminal,
		PreviousPayloadSHA256: input.PreviousPayloadSHA256,
		PayloadSHA256:         hex.EncodeToString(payloadDigest[:]), Payload: payload,
	}
	unsignedBytes, err := marshalCanonicalWireJSON(unsigned)
	if err != nil {
		return WireEnvelope{}, fmt.Errorf("propertycatalog: encode envelope identity: %w", err)
	}
	envelopeDigest := sha256.Sum256(unsignedBytes)
	document := envelopeJSON{
		Format: unsigned.Format, Version: unsigned.Version,
		EnvelopeID:     hex.EncodeToString(envelopeDigest[:]),
		OrganizationID: unsigned.OrganizationID, WorkspaceID: unsigned.WorkspaceID,
		CatalogEpoch: unsigned.CatalogEpoch, CatalogRevision: unsigned.CatalogRevision,
		BuildToken:        unsigned.BuildToken,
		ProjectionVersion: unsigned.ProjectionVersion, SourceAdapter: unsigned.SourceAdapter,
		SourceVersion: unsigned.SourceVersion, SourceFingerprint: unsigned.SourceFingerprint,
		ProducerStreamID: unsigned.ProducerStreamID, Sequence: unsigned.Sequence, Terminal: unsigned.Terminal,
		PreviousPayloadSHA256: unsigned.PreviousPayloadSHA256,
		PayloadSHA256:         unsigned.PayloadSHA256, Payload: unsigned.Payload,
	}
	raw, err := marshalCanonicalWireJSON(document)
	if err != nil {
		return WireEnvelope{}, fmt.Errorf("propertycatalog: encode envelope: %w", err)
	}
	if len(raw) > MaxRecordBytes {
		return WireEnvelope{}, fmt.Errorf("propertycatalog: encoded envelope uses %d bytes, limit %d", len(raw), MaxRecordBytes)
	}
	return WireEnvelope{document: document, raw: raw}, nil
}

// ParseWireEnvelope accepts only the exact deterministic v1 JSON encoding.
func ParseWireEnvelope(raw []byte) (WireEnvelope, error) {
	if len(raw) == 0 || len(raw) > MaxRecordBytes {
		return WireEnvelope{}, fmt.Errorf("propertycatalog: envelope size %d is outside (0,%d]", len(raw), MaxRecordBytes)
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	var document envelopeJSON
	if err := decoder.Decode(&document); err != nil {
		return WireEnvelope{}, fmt.Errorf("propertycatalog: decode envelope: %w", err)
	}
	if err := requireJSONEOF(decoder); err != nil {
		return WireEnvelope{}, fmt.Errorf("propertycatalog: decode envelope: %w", err)
	}
	canonical, err := marshalCanonicalWireJSON(document)
	if err != nil {
		return WireEnvelope{}, fmt.Errorf("propertycatalog: re-encode envelope: %w", err)
	}
	if !bytes.Equal(raw, canonical) {
		return WireEnvelope{}, errors.New("propertycatalog: envelope is not deterministic canonical JSON")
	}
	if document.Format != EnvelopeFormat || document.Version != EnvelopeVersion {
		return WireEnvelope{}, errors.New("propertycatalog: unsupported envelope format/version")
	}
	rebuilt, err := NewWireEnvelope(EnvelopeInput{
		OrganizationID: document.OrganizationID, WorkspaceID: document.WorkspaceID,
		CatalogEpoch: document.CatalogEpoch, CatalogRevision: document.CatalogRevision,
		BuildToken:        document.BuildToken,
		ProjectionVersion: document.ProjectionVersion, SourceAdapter: document.SourceAdapter,
		SourceVersion: document.SourceVersion, SourceFingerprint: document.SourceFingerprint,
		ProducerStreamID: document.ProducerStreamID, Sequence: document.Sequence, Terminal: document.Terminal,
		PreviousPayloadSHA256: document.PreviousPayloadSHA256,
		Payload:               payloadInputFromJSON(document.Payload),
	})
	if err != nil {
		return WireEnvelope{}, err
	}
	if rebuilt.document.PayloadSHA256 != document.PayloadSHA256 || rebuilt.document.EnvelopeID != document.EnvelopeID {
		return WireEnvelope{}, errors.New("propertycatalog: payload or envelope identity mismatch")
	}
	if !bytes.Equal(rebuilt.raw, raw) {
		return WireEnvelope{}, errors.New("propertycatalog: envelope contains a mismatched derived hash")
	}
	return WireEnvelope{document: document, raw: bytes.Clone(raw)}, nil
}

func buildPayload(input PayloadInput, scope Scope) (payloadJSON, error) {
	if !isLowerSHA256(input.SourceBatchDigest) {
		return payloadJSON{}, errors.New("propertycatalog: source batch digest must be lowercase SHA-256")
	}
	if len(input.GapReasons) > MaxGapReasons {
		return payloadJSON{}, errors.New("propertycatalog: gap reason count exceeds limit")
	}
	gapReasons := append([]string{}, input.GapReasons...)
	for index, reason := range gapReasons {
		if err := validateText(fmt.Sprintf("gap_reasons[%d]", index), reason, true, MaxGapReasonBytes); err != nil {
			return payloadJSON{}, err
		}
		if strings.TrimSpace(reason) != reason || strings.IndexFunc(reason, unicode.IsControl) >= 0 {
			return payloadJSON{}, errors.New("propertycatalog: gap reason has surrounding space or control characters")
		}
		if index > 0 && reason <= gapReasons[index-1] {
			return payloadJSON{}, errors.New("propertycatalog: gap reasons must be strictly sorted")
		}
	}
	switch input.Outcome {
	case OutcomeCommitted:
		if len(gapReasons) != 0 {
			return payloadJSON{}, errors.New("propertycatalog: committed payload cannot contain gap reasons")
		}
	case OutcomeGap:
		if len(gapReasons) == 0 {
			return payloadJSON{}, errors.New("propertycatalog: gap payload requires an explicit reason")
		}
	default:
		return payloadJSON{}, errors.New("propertycatalog: unsupported payload outcome")
	}
	if input.DefinitionRows > MaxRowsPerEnvelope ||
		input.ValueRows > MaxRowsPerEnvelope-input.DefinitionRows ||
		input.TombstoneRows > input.DefinitionRows {
		return payloadJSON{}, errors.New("propertycatalog: payload row counts are invalid")
	}
	if len(input.Chunks) > MaxChunks {
		return payloadJSON{}, errors.New("propertycatalog: payload chunk count exceeds limit")
	}
	chunks := make([]chunkJSON, len(input.Chunks))
	var definitionRows, valueRows, tombstones uint64
	seenValueTable := false
	for index, inputChunk := range input.Chunks {
		if int(inputChunk.Index) != index || inputChunk.RowCount == 0 || inputChunk.RowCount > MaxRowsPerChunk ||
			len(inputChunk.JSONEachRow) == 0 || len(inputChunk.JSONEachRow) > MaxChunkBytes {
			return payloadJSON{}, fmt.Errorf("propertycatalog: chunk %d has invalid index/row/byte bounds", index)
		}
		switch inputChunk.Table {
		case DefinitionTable:
			if seenValueTable {
				return payloadJSON{}, errors.New("propertycatalog: definition chunks must precede value chunks")
			}
		case AttributeValueTable:
			seenValueTable = true
		default:
			return payloadJSON{}, fmt.Errorf("propertycatalog: chunk %d targets forbidden table %q", index, inputChunk.Table)
		}
		rowCount, deletedCount, err := validateChunkRows(inputChunk, scope)
		if err != nil {
			return payloadJSON{}, fmt.Errorf("propertycatalog: chunk %d: %w", index, err)
		}
		if rowCount != uint64(inputChunk.RowCount) {
			return payloadJSON{}, fmt.Errorf("propertycatalog: chunk %d row count mismatch", index)
		}
		if inputChunk.Table == DefinitionTable {
			definitionRows += rowCount
			tombstones += deletedCount
		} else {
			valueRows += rowCount
		}
		digest := sha256.Sum256(inputChunk.JSONEachRow)
		chunks[index] = chunkJSON{
			Table: inputChunk.Table, Index: inputChunk.Index, RowCount: inputChunk.RowCount,
			EncodedSHA256: hex.EncodeToString(digest[:]), JSONEachRow: bytes.Clone(inputChunk.JSONEachRow),
		}
	}
	if definitionRows != input.DefinitionRows || valueRows != input.ValueRows || tombstones != input.TombstoneRows {
		return payloadJSON{}, errors.New("propertycatalog: payload aggregate row counts mismatch")
	}
	return payloadJSON{
		SourceBatchDigest: input.SourceBatchDigest, Outcome: input.Outcome,
		GapReasons: gapReasons, SourceRows: input.SourceRows, DefinitionRows: input.DefinitionRows,
		ValueRows: input.ValueRows, TombstoneRows: input.TombstoneRows, Chunks: chunks,
	}, nil
}

func validateChunkRows(chunk ChunkInput, scope Scope) (uint64, uint64, error) {
	if chunk.JSONEachRow[len(chunk.JSONEachRow)-1] != '\n' {
		return 0, 0, errors.New("JSONEachRow chunk must end in one newline")
	}
	scanner := bufio.NewScanner(bytes.NewReader(chunk.JSONEachRow))
	scanner.Buffer(make([]byte, 64<<10), MaxChunkBytes)
	var rows, tombstones uint64
	for scanner.Scan() {
		line := bytes.Clone(scanner.Bytes())
		if len(line) == 0 {
			return 0, 0, errors.New("JSONEachRow chunk contains a blank row")
		}
		switch chunk.Table {
		case DefinitionTable:
			var row DefinitionRow
			if err := decodeCanonicalRow(line, &row); err != nil {
				return 0, 0, fmt.Errorf("definition row %d: %w", rows, err)
			}
			if err := validateDefinition(row, scope); err != nil {
				return 0, 0, fmt.Errorf("definition row %d: %w", rows, err)
			}
			if row.IsDeleted == 1 {
				tombstones++
			}
		case AttributeValueTable:
			var row AttributeValueRow
			if err := decodeCanonicalRow(line, &row); err != nil {
				return 0, 0, fmt.Errorf("value row %d: %w", rows, err)
			}
			if err := validateAttributeValue(row, scope); err != nil {
				return 0, 0, fmt.Errorf("value row %d: %w", rows, err)
			}
		default:
			return 0, 0, errors.New("forbidden data table")
		}
		rows++
	}
	if err := scanner.Err(); err != nil {
		return 0, 0, fmt.Errorf("scan JSONEachRow: %w", err)
	}
	return rows, tombstones, nil
}

func decodeCanonicalRow(raw []byte, destination any) error {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(destination); err != nil {
		return err
	}
	if err := requireJSONEOF(decoder); err != nil {
		return err
	}
	canonical, err := encodeCanonicalRow(destination)
	if err != nil {
		return err
	}
	if !bytes.Equal(raw, canonical) {
		return errors.New("row is not deterministic canonical JSON or omits a required column")
	}
	return nil
}

func encodeCanonicalRow(value any) ([]byte, error) {
	var encoded bytes.Buffer
	encoder := json.NewEncoder(&encoded)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return nil, err
	}
	bytesWithNewline := encoded.Bytes()
	if len(bytesWithNewline) == 0 || bytesWithNewline[len(bytesWithNewline)-1] != '\n' {
		return nil, errors.New("propertycatalog: canonical row encoder omitted newline")
	}
	return bytes.Clone(bytesWithNewline[:len(bytesWithNewline)-1]), nil
}

func marshalCanonicalWireJSON(value any) ([]byte, error) {
	var encoded bytes.Buffer
	encoder := json.NewEncoder(&encoded)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return nil, err
	}
	raw := encoded.Bytes()
	if len(raw) == 0 || raw[len(raw)-1] != '\n' {
		return nil, errors.New("propertycatalog: canonical wire JSON omitted newline")
	}
	return bytes.Clone(raw[:len(raw)-1]), nil
}

func payloadInputFromJSON(payload payloadJSON) PayloadInput {
	chunks := make([]ChunkInput, len(payload.Chunks))
	for index, chunk := range payload.Chunks {
		chunks[index] = ChunkInput{
			Table: chunk.Table, Index: chunk.Index, RowCount: chunk.RowCount,
			JSONEachRow: bytes.Clone(chunk.JSONEachRow),
		}
	}
	return PayloadInput{
		SourceBatchDigest: payload.SourceBatchDigest, Outcome: payload.Outcome,
		GapReasons: append([]string{}, payload.GapReasons...), DefinitionRows: payload.DefinitionRows,
		SourceRows: payload.SourceRows, ValueRows: payload.ValueRows, TombstoneRows: payload.TombstoneRows, Chunks: chunks,
	}
}

func (e WireEnvelope) MarshalBinary() ([]byte, error) {
	if len(e.raw) == 0 || len(e.raw) > MaxRecordBytes {
		return nil, errors.New("propertycatalog: invalid wire envelope")
	}
	return bytes.Clone(e.raw), nil
}

func (e WireEnvelope) Snapshot() EnvelopeSnapshot {
	d := e.document
	chunks := make([]Chunk, len(d.Payload.Chunks))
	for index, chunk := range d.Payload.Chunks {
		chunks[index] = Chunk{
			Table: chunk.Table, Index: chunk.Index, RowCount: chunk.RowCount,
			EncodedSHA256: chunk.EncodedSHA256, JSONEachRow: bytes.Clone(chunk.JSONEachRow),
		}
	}
	return EnvelopeSnapshot{
		Format: d.Format, Version: d.Version, EnvelopeID: d.EnvelopeID,
		OrganizationID: d.OrganizationID, WorkspaceID: d.WorkspaceID,
		CatalogEpoch: d.CatalogEpoch, CatalogRevision: d.CatalogRevision,
		BuildToken:        d.BuildToken,
		ProjectionVersion: d.ProjectionVersion, SourceAdapter: d.SourceAdapter,
		SourceVersion: d.SourceVersion, SourceFingerprint: d.SourceFingerprint,
		ProducerStreamID: d.ProducerStreamID, Sequence: d.Sequence, Terminal: d.Terminal,
		PreviousPayloadSHA256: d.PreviousPayloadSHA256, PayloadSHA256: d.PayloadSHA256,
		Payload: Payload{
			SourceBatchDigest: d.Payload.SourceBatchDigest, Outcome: d.Payload.Outcome,
			GapReasons: append([]string{}, d.Payload.GapReasons...), SourceRows: d.Payload.SourceRows,
			DefinitionRows: d.Payload.DefinitionRows,
			ValueRows:      d.Payload.ValueRows, TombstoneRows: d.Payload.TombstoneRows, Chunks: chunks,
		},
	}
}

func (e WireEnvelope) EnvelopeID() string            { return e.document.EnvelopeID }
func (e WireEnvelope) PayloadSHA256() string         { return e.document.PayloadSHA256 }
func (e WireEnvelope) PreviousPayloadSHA256() string { return e.document.PreviousPayloadSHA256 }
func (e WireEnvelope) Sequence() uint64              { return e.document.Sequence }

func strictDecodeJSONEachRow(raw []byte, table Table) ([]map[string]any, error) {
	scanner := bufio.NewScanner(bytes.NewReader(raw))
	scanner.Buffer(make([]byte, 64<<10), MaxChunkBytes)
	rows := make([]map[string]any, 0)
	for scanner.Scan() {
		decoder := json.NewDecoder(strings.NewReader(scanner.Text()))
		decoder.UseNumber()
		var row map[string]any
		if err := decoder.Decode(&row); err != nil {
			return nil, err
		}
		if err := requireJSONEOF(decoder); err != nil {
			return nil, err
		}
		rows = append(rows, row)
	}
	if err := scanner.Err(); err != nil && !errors.Is(err, io.EOF) {
		return nil, err
	}
	if table != DefinitionTable && table != AttributeValueTable {
		return nil, errors.New("propertycatalog: forbidden table")
	}
	return rows, nil
}
