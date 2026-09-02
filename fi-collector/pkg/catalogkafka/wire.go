// Package catalogkafka defines the bounded Kafka wire contract for the span
// attribute catalog. It is transport-only and never activates ingestion or
// writes any table by itself.
package catalogkafka

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"sort"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/google/uuid"
)

const (
	EnvelopeFormat  = "futureagi.span-attribute-catalog-envelope"
	EnvelopeVersion = uint16(3)

	// MaxRecordBytes is the hard limit for the serialized Kafka value. The
	// producer and consumer both enforce it; broker limits remain a separate
	// deployment concern.
	MaxRecordBytes = 768 << 10

	MaxChunks                  = 128
	MaxChunkJSONEachRowBytes   = 512 << 10
	MaxRowsPerChunk            = 10_000
	MaxRowsPerEnvelope         = 1_000_000
	MaxGapReasons              = 64
	MaxGapReasonBytes          = 128
	canonicalDateTime64Layout  = "2006-01-02 15:04:05.000000"
	zeroSHA256                 = "0000000000000000000000000000000000000000000000000000000000000000"
)

// ZeroSHA256 is the required previous-payload digest for sequence one.
const ZeroSHA256 = zeroSHA256

// Table is deliberately closed over only the two new catalog data tables.
// Consumers therefore cannot route an envelope chunk to an existing table.
type Table string

const (
	KeyTable   Table = "span_attribute_key_catalog"
	ValueTable Table = "span_attribute_value_catalog"
)

type Outcome string

const (
	OutcomeCommitted Outcome = "committed"
	OutcomeGap       Outcome = "gap"
)

// ChunkInput is canonical JSONEachRow for one allowlisted catalog table.
// Indexes must be contiguous and key chunks must precede value chunks.
type ChunkInput struct {
	Table       Table
	Index       uint16
	RowCount    uint32
	JSONEachRow []byte
}

// PayloadInput contains source coverage metadata and bounded data chunks.
type PayloadInput struct {
	SourceBatchDigest string
	Outcome           Outcome
	GapReasons        []string
	SourceMinStart    string
	SourceMaxStart    string
	SourceRows        uint64
	KeyRows           uint64
	ValueRows         uint64
	Chunks            []ChunkInput
}

// EnvelopeInput contains the producer stream identity and payload-chain link.
// PayloadSHA256 and EnvelopeID are always computed by NewWireEnvelope.
type EnvelopeInput struct {
	ProjectID            string
	CatalogEpoch         uint16
	ProducerStreamID     string
	Sequence             uint64
	PreviousPayloadSHA256 string
	Payload              PayloadInput
}

// Chunk is a defensive snapshot of one immutable wire chunk.
type Chunk struct {
	Table         Table
	Index         uint16
	RowCount      uint32
	EncodedSHA256 string
	JSONEachRow   []byte
}

// Payload is a defensive snapshot of an envelope payload.
type Payload struct {
	SourceBatchDigest string
	Outcome           Outcome
	GapReasons        []string
	SourceMinStart    string
	SourceMaxStart    string
	SourceRows        uint64
	KeyRows           uint64
	ValueRows         uint64
	Chunks            []Chunk
}

// EnvelopeSnapshot exposes all immutable fields as a defensive copy.
type EnvelopeSnapshot struct {
	Format                string
	Version               uint16
	EnvelopeID            string
	ProjectID             string
	CatalogEpoch          uint16
	ProducerStreamID      string
	Sequence              uint64
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
	SourceMinStart    string      `json:"source_min_start"`
	SourceMaxStart    string      `json:"source_max_start"`
	SourceRows        uint64      `json:"source_rows"`
	KeyRows           uint64      `json:"key_rows"`
	ValueRows         uint64      `json:"value_rows"`
	Chunks            []chunkJSON `json:"chunks"`
}

type unsignedEnvelopeJSON struct {
	Format                string      `json:"format"`
	Version               uint16      `json:"version"`
	ProjectID             string      `json:"project_id"`
	CatalogEpoch          uint16      `json:"catalog_epoch"`
	ProducerStreamID      string      `json:"producer_stream_id"`
	Sequence              uint64      `json:"sequence"`
	PreviousPayloadSHA256 string      `json:"previous_payload_sha256"`
	PayloadSHA256         string      `json:"payload_sha256"`
	Payload               payloadJSON `json:"payload"`
}

type envelopeJSON struct {
	Format                string      `json:"format"`
	Version               uint16      `json:"version"`
	EnvelopeID            string      `json:"envelope_id"`
	ProjectID             string      `json:"project_id"`
	CatalogEpoch          uint16      `json:"catalog_epoch"`
	ProducerStreamID      string      `json:"producer_stream_id"`
	Sequence              uint64      `json:"sequence"`
	PreviousPayloadSHA256 string      `json:"previous_payload_sha256"`
	PayloadSHA256         string      `json:"payload_sha256"`
	Payload               payloadJSON `json:"payload"`
}

// WireEnvelope is immutable from outside this package. Marshal and Snapshot
// return copies, and construction copies all caller-owned slices.
type WireEnvelope struct {
	document envelopeJSON
	raw      []byte
}

// NewWireEnvelope validates, hashes, and deterministically encodes version 3.
func NewWireEnvelope(input EnvelopeInput) (WireEnvelope, error) {
	if err := validateCanonicalUUID("project", input.ProjectID); err != nil {
		return WireEnvelope{}, err
	}
	if input.CatalogEpoch == 0 {
		return WireEnvelope{}, errors.New("catalogkafka: catalog epoch must be non-zero")
	}
	if err := validateCanonicalUUID("producer stream", input.ProducerStreamID); err != nil {
		return WireEnvelope{}, err
	}
	if input.Sequence == 0 {
		return WireEnvelope{}, errors.New("catalogkafka: sequence must be non-zero")
	}
	if !isLowerSHA256(input.PreviousPayloadSHA256) {
		return WireEnvelope{}, errors.New("catalogkafka: previous payload digest must be lowercase SHA-256")
	}
	if input.Sequence == 1 && input.PreviousPayloadSHA256 != ZeroSHA256 {
		return WireEnvelope{}, errors.New("catalogkafka: sequence one must use the zero previous payload digest")
	}

	payload, err := buildPayload(input.Payload)
	if err != nil {
		return WireEnvelope{}, err
	}
	payloadBytes, err := json.Marshal(payload)
	if err != nil {
		return WireEnvelope{}, fmt.Errorf("catalogkafka: encode payload: %w", err)
	}
	payloadDigest := sha256.Sum256(payloadBytes)
	unsigned := unsignedEnvelopeJSON{
		Format: EnvelopeFormat, Version: EnvelopeVersion,
		ProjectID: input.ProjectID, CatalogEpoch: input.CatalogEpoch,
		ProducerStreamID: input.ProducerStreamID, Sequence: input.Sequence,
		PreviousPayloadSHA256: input.PreviousPayloadSHA256,
		PayloadSHA256: hex.EncodeToString(payloadDigest[:]), Payload: payload,
	}
	unsignedBytes, err := json.Marshal(unsigned)
	if err != nil {
		return WireEnvelope{}, fmt.Errorf("catalogkafka: encode envelope identity: %w", err)
	}
	envelopeDigest := sha256.Sum256(unsignedBytes)
	document := envelopeJSON{
		Format: unsigned.Format, Version: unsigned.Version,
		EnvelopeID: hex.EncodeToString(envelopeDigest[:]),
		ProjectID: unsigned.ProjectID, CatalogEpoch: unsigned.CatalogEpoch,
		ProducerStreamID: unsigned.ProducerStreamID, Sequence: unsigned.Sequence,
		PreviousPayloadSHA256: unsigned.PreviousPayloadSHA256,
		PayloadSHA256: unsigned.PayloadSHA256, Payload: unsigned.Payload,
	}
	raw, err := json.Marshal(document)
	if err != nil {
		return WireEnvelope{}, fmt.Errorf("catalogkafka: encode envelope: %w", err)
	}
	if len(raw) > MaxRecordBytes {
		return WireEnvelope{}, fmt.Errorf(
			"catalogkafka: encoded envelope uses %d bytes, limit %d", len(raw), MaxRecordBytes,
		)
	}
	return WireEnvelope{document: document, raw: raw}, nil
}

// ParseWireEnvelope accepts only the exact deterministic version-3 encoding.
// Unknown fields, duplicate/non-canonical JSON, bad hashes, and oversize input
// are poison errors returned to the consumer without an offset commit.
func ParseWireEnvelope(raw []byte) (WireEnvelope, error) {
	if len(raw) == 0 || len(raw) > MaxRecordBytes {
		return WireEnvelope{}, fmt.Errorf(
			"catalogkafka: envelope size %d is outside (0,%d]", len(raw), MaxRecordBytes,
		)
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	var document envelopeJSON
	if err := decoder.Decode(&document); err != nil {
		return WireEnvelope{}, fmt.Errorf("catalogkafka: decode envelope: %w", err)
	}
	if err := requireJSONEOF(decoder); err != nil {
		return WireEnvelope{}, fmt.Errorf("catalogkafka: decode envelope: %w", err)
	}
	canonical, err := json.Marshal(document)
	if err != nil {
		return WireEnvelope{}, fmt.Errorf("catalogkafka: re-encode envelope: %w", err)
	}
	if !bytes.Equal(raw, canonical) {
		return WireEnvelope{}, errors.New("catalogkafka: envelope is not in deterministic canonical JSON form")
	}
	if document.Format != EnvelopeFormat || document.Version != EnvelopeVersion {
		return WireEnvelope{}, fmt.Errorf(
			"catalogkafka: unsupported envelope format/version %q/%d", document.Format, document.Version,
		)
	}
	if err := validateCanonicalUUID("project", document.ProjectID); err != nil {
		return WireEnvelope{}, err
	}
	if document.CatalogEpoch == 0 {
		return WireEnvelope{}, errors.New("catalogkafka: catalog epoch must be non-zero")
	}
	if err := validateCanonicalUUID("producer stream", document.ProducerStreamID); err != nil {
		return WireEnvelope{}, err
	}
	if document.Sequence == 0 || !isLowerSHA256(document.PreviousPayloadSHA256) ||
		!isLowerSHA256(document.PayloadSHA256) || !isLowerSHA256(document.EnvelopeID) {
		return WireEnvelope{}, errors.New("catalogkafka: invalid envelope identity or digest")
	}
	if document.Sequence == 1 && document.PreviousPayloadSHA256 != ZeroSHA256 {
		return WireEnvelope{}, errors.New("catalogkafka: sequence one must use the zero previous payload digest")
	}
	if err := validatePayload(document.Payload); err != nil {
		return WireEnvelope{}, err
	}
	payloadBytes, err := json.Marshal(document.Payload)
	if err != nil {
		return WireEnvelope{}, fmt.Errorf("catalogkafka: re-encode payload: %w", err)
	}
	payloadDigest := sha256.Sum256(payloadBytes)
	if document.PayloadSHA256 != hex.EncodeToString(payloadDigest[:]) {
		return WireEnvelope{}, errors.New("catalogkafka: payload SHA-256 mismatch")
	}
	unsigned := unsignedEnvelopeJSON{
		Format: document.Format, Version: document.Version,
		ProjectID: document.ProjectID, CatalogEpoch: document.CatalogEpoch,
		ProducerStreamID: document.ProducerStreamID, Sequence: document.Sequence,
		PreviousPayloadSHA256: document.PreviousPayloadSHA256,
		PayloadSHA256: document.PayloadSHA256, Payload: document.Payload,
	}
	unsignedBytes, err := json.Marshal(unsigned)
	if err != nil {
		return WireEnvelope{}, fmt.Errorf("catalogkafka: re-encode envelope identity: %w", err)
	}
	envelopeDigest := sha256.Sum256(unsignedBytes)
	if document.EnvelopeID != hex.EncodeToString(envelopeDigest[:]) {
		return WireEnvelope{}, errors.New("catalogkafka: envelope ID mismatch")
	}
	return WireEnvelope{document: cloneDocument(document), raw: bytes.Clone(raw)}, nil
}

func buildPayload(input PayloadInput) (payloadJSON, error) {
	gaps := append([]string(nil), input.GapReasons...)
	sort.Strings(gaps)
	chunks := make([]chunkJSON, len(input.Chunks))
	for index, inputChunk := range input.Chunks {
		encoded := bytes.Clone(inputChunk.JSONEachRow)
		digest := sha256.Sum256(encoded)
		chunks[index] = chunkJSON{
			Table: inputChunk.Table, Index: inputChunk.Index, RowCount: inputChunk.RowCount,
			EncodedSHA256: hex.EncodeToString(digest[:]), JSONEachRow: encoded,
		}
	}
	payload := payloadJSON{
		SourceBatchDigest: strings.Clone(input.SourceBatchDigest), Outcome: input.Outcome,
		GapReasons: gaps, SourceMinStart: strings.Clone(input.SourceMinStart),
		SourceMaxStart: strings.Clone(input.SourceMaxStart), SourceRows: input.SourceRows,
		KeyRows: input.KeyRows, ValueRows: input.ValueRows, Chunks: chunks,
	}
	if err := validatePayload(payload); err != nil {
		return payloadJSON{}, err
	}
	return payload, nil
}

func validatePayload(payload payloadJSON) error {
	if !isLowerSHA256(payload.SourceBatchDigest) {
		return errors.New("catalogkafka: source batch digest must be lowercase SHA-256")
	}
	if payload.SourceRows == 0 || payload.SourceRows > MaxRowsPerEnvelope ||
		payload.KeyRows > MaxRowsPerEnvelope || payload.ValueRows > MaxRowsPerEnvelope {
		return errors.New("catalogkafka: source or catalog row count is outside its bound")
	}
	minimum, err := parseCanonicalTime(payload.SourceMinStart)
	if err != nil {
		return fmt.Errorf("catalogkafka: invalid source minimum: %w", err)
	}
	maximum, err := parseCanonicalTime(payload.SourceMaxStart)
	if err != nil {
		return fmt.Errorf("catalogkafka: invalid source maximum: %w", err)
	}
	if minimum.After(maximum) {
		return errors.New("catalogkafka: source minimum is after source maximum")
	}
	if len(payload.GapReasons) > MaxGapReasons || !sort.StringsAreSorted(payload.GapReasons) {
		return errors.New("catalogkafka: gap reasons exceed their bound or are not sorted")
	}
	for index, reason := range payload.GapReasons {
		if err := validateGapReason(reason); err != nil {
			return fmt.Errorf("catalogkafka: gap reason %d: %w", index, err)
		}
		if index > 0 && reason == payload.GapReasons[index-1] {
			return errors.New("catalogkafka: gap reasons must be unique")
		}
	}
	switch payload.Outcome {
	case OutcomeCommitted:
		if len(payload.GapReasons) != 0 {
			return errors.New("catalogkafka: committed outcome cannot contain gap reasons")
		}
	case OutcomeGap:
		if len(payload.GapReasons) == 0 {
			return errors.New("catalogkafka: gap outcome requires a reason")
		}
	default:
		return fmt.Errorf("catalogkafka: invalid outcome %q", payload.Outcome)
	}
	if len(payload.Chunks) > MaxChunks {
		return fmt.Errorf("catalogkafka: payload has %d chunks, limit %d", len(payload.Chunks), MaxChunks)
	}
	var keyRows, valueRows uint64
	seenValue := false
	for index, chunk := range payload.Chunks {
		if int(chunk.Index) != index {
			return errors.New("catalogkafka: chunk indexes must be contiguous from zero")
		}
		switch chunk.Table {
		case KeyTable:
			if seenValue {
				return errors.New("catalogkafka: key chunks must precede value chunks")
			}
			keyRows += uint64(chunk.RowCount)
		case ValueTable:
			seenValue = true
			valueRows += uint64(chunk.RowCount)
		default:
			return fmt.Errorf("catalogkafka: chunk %d targets forbidden table %q", index, chunk.Table)
		}
		if !isLowerSHA256(chunk.EncodedSHA256) {
			return fmt.Errorf("catalogkafka: chunk %d has an invalid digest", index)
		}
		digest := sha256.Sum256(chunk.JSONEachRow)
		if chunk.EncodedSHA256 != hex.EncodeToString(digest[:]) {
			return fmt.Errorf("catalogkafka: chunk %d SHA-256 mismatch", index)
		}
		if err := validateCanonicalJSONEachRow(chunk.JSONEachRow, chunk.RowCount); err != nil {
			return fmt.Errorf("catalogkafka: chunk %d: %w", index, err)
		}
	}
	if keyRows != payload.KeyRows || valueRows != payload.ValueRows {
		return errors.New("catalogkafka: payload/chunk row counts do not match")
	}
	return nil
}

func validateCanonicalJSONEachRow(encoded []byte, rowCount uint32) error {
	if rowCount == 0 || rowCount > MaxRowsPerChunk {
		return fmt.Errorf("row count %d is outside (0,%d]", rowCount, MaxRowsPerChunk)
	}
	if len(encoded) == 0 || len(encoded) > MaxChunkJSONEachRowBytes {
		return fmt.Errorf("JSONEachRow bytes %d are outside (0,%d]", len(encoded), MaxChunkJSONEachRowBytes)
	}
	if encoded[len(encoded)-1] != '\n' || bytes.Count(encoded, []byte{'\n'}) != int(rowCount) {
		return errors.New("JSONEachRow must contain exactly one newline-terminated object per row")
	}
	lines := bytes.Split(encoded[:len(encoded)-1], []byte{'\n'})
	for index, line := range lines {
		decoder := json.NewDecoder(bytes.NewReader(line))
		decoder.UseNumber()
		var object map[string]any
		if err := decoder.Decode(&object); err != nil || object == nil {
			return fmt.Errorf("row %d is not a JSON object", index)
		}
		if err := requireJSONEOF(decoder); err != nil {
			return fmt.Errorf("row %d has trailing JSON", index)
		}
		var canonical bytes.Buffer
		encoder := json.NewEncoder(&canonical)
		encoder.SetEscapeHTML(false)
		if err := encoder.Encode(object); err != nil {
			return fmt.Errorf("re-encode row %d: %w", index, err)
		}
		if !bytes.Equal(append(bytes.Clone(line), '\n'), canonical.Bytes()) {
			return fmt.Errorf("row %d is not deterministic canonical JSON", index)
		}
	}
	return nil
}

func requireJSONEOF(decoder *json.Decoder) error {
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		if err == nil {
			return errors.New("multiple JSON values")
		}
		return err
	}
	return nil
}

func validateCanonicalUUID(name, value string) error {
	parsed, err := uuid.Parse(value)
	if err != nil || parsed == uuid.Nil || parsed.String() != value {
		return fmt.Errorf("catalogkafka: %s ID must be a canonical non-zero UUID", name)
	}
	return nil
}

func isLowerSHA256(value string) bool {
	if len(value) != sha256.Size*2 || value != strings.ToLower(value) {
		return false
	}
	_, err := hex.DecodeString(value)
	return err == nil
}

func parseCanonicalTime(value string) (time.Time, error) {
	parsed, err := time.Parse(canonicalDateTime64Layout, value)
	if err != nil || parsed.Format(canonicalDateTime64Layout) != value {
		return time.Time{}, errors.New("timestamp must use UTC DateTime64(6) form")
	}
	return parsed, nil
}

func validateGapReason(reason string) error {
	if reason == "" || len(reason) > MaxGapReasonBytes || !utf8.ValidString(reason) || strings.TrimSpace(reason) != reason {
		return errors.New("must be non-empty, bounded, valid UTF-8 without surrounding whitespace")
	}
	for _, char := range reason {
		if unicode.IsControl(char) {
			return errors.New("must not contain control characters")
		}
	}
	return nil
}

func cloneDocument(source envelopeJSON) envelopeJSON {
	out := source
	out.Payload.GapReasons = append([]string(nil), source.Payload.GapReasons...)
	out.Payload.Chunks = make([]chunkJSON, len(source.Payload.Chunks))
	for index, chunk := range source.Payload.Chunks {
		out.Payload.Chunks[index] = chunk
		out.Payload.Chunks[index].JSONEachRow = bytes.Clone(chunk.JSONEachRow)
	}
	return out
}

// Marshal returns a defensive copy of the exact deterministic wire bytes.
func (e WireEnvelope) Marshal() []byte { return bytes.Clone(e.raw) }

// MarshalBinary implements encoding.BinaryMarshaler without exposing storage.
func (e WireEnvelope) MarshalBinary() ([]byte, error) {
	if len(e.raw) == 0 {
		return nil, errors.New("catalogkafka: zero wire envelope")
	}
	return e.Marshal(), nil
}

func (e WireEnvelope) EnvelopeID() string        { return e.document.EnvelopeID }
func (e WireEnvelope) ProjectID() string         { return e.document.ProjectID }
func (e WireEnvelope) CatalogEpoch() uint16      { return e.document.CatalogEpoch }
func (e WireEnvelope) ProducerStreamID() string  { return e.document.ProducerStreamID }
func (e WireEnvelope) Sequence() uint64          { return e.document.Sequence }
func (e WireEnvelope) PayloadSHA256() string     { return e.document.PayloadSHA256 }
func (e WireEnvelope) PreviousPayloadSHA256() string {
	return e.document.PreviousPayloadSHA256
}

// Snapshot returns a defensive copy suitable for a delivery handler.
func (e WireEnvelope) Snapshot() EnvelopeSnapshot {
	payload := Payload{
		SourceBatchDigest: e.document.Payload.SourceBatchDigest,
		Outcome: e.document.Payload.Outcome,
		GapReasons: append([]string(nil), e.document.Payload.GapReasons...),
		SourceMinStart: e.document.Payload.SourceMinStart,
		SourceMaxStart: e.document.Payload.SourceMaxStart,
		SourceRows: e.document.Payload.SourceRows,
		KeyRows: e.document.Payload.KeyRows, ValueRows: e.document.Payload.ValueRows,
		Chunks: make([]Chunk, len(e.document.Payload.Chunks)),
	}
	for index, chunk := range e.document.Payload.Chunks {
		payload.Chunks[index] = Chunk{
			Table: chunk.Table, Index: chunk.Index, RowCount: chunk.RowCount,
			EncodedSHA256: chunk.EncodedSHA256, JSONEachRow: bytes.Clone(chunk.JSONEachRow),
		}
	}
	return EnvelopeSnapshot{
		Format: e.document.Format, Version: e.document.Version,
		EnvelopeID: e.document.EnvelopeID, ProjectID: e.document.ProjectID,
		CatalogEpoch: e.document.CatalogEpoch, ProducerStreamID: e.document.ProducerStreamID,
		Sequence: e.document.Sequence, PreviousPayloadSHA256: e.document.PreviousPayloadSHA256,
		PayloadSHA256: e.document.PayloadSHA256, Payload: payload,
	}
}
