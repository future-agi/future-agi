package catalogkafka

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogwriter"
)

const (
	defaultLedgerSeedTimeout       = 5 * time.Second
	maxLedgerSeedTimeout           = 10 * time.Second
	defaultLedgerSeedResponseBytes = int64(8 << 20)
	maxLedgerSeedResponseBytes     = int64(32 << 20)
	maxLedgerSeedRows              = 100_000
)

var ledgerDatabaseName = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)

// DeliveryLedgerReaderConfig is intentionally separate from the catalog
// writer configuration. A consumer restart can therefore use a read-only
// identity that has SELECT access only to the new delivery ledger, while the
// delivery handler keeps its catalog INSERT identity.
type DeliveryLedgerReaderConfig struct {
	URL      string
	Database string
	Username string
	Password string

	RequestTimeout   time.Duration
	MaxResponseBytes int64
}

// DeliveryLedgerCheckpointReader reads one fixed, closed-allowlist query. It
// cannot be pointed at canonical spans or any pre-existing table by callers.
type DeliveryLedgerCheckpointReader struct {
	client  *http.Client
	baseURL *url.URL
	cfg     DeliveryLedgerReaderConfig
}

// The raw ReplacingMergeTree rows are intentionally read without FINAL. This
// lets validation reject any conflicting identities for one stream sequence,
// instead of allowing ClickHouse to choose one replacement silently.
const deliveryLedgerCheckpointQuery = `SELECT
    toString(project_id) AS project_id,
    catalog_epoch,
    toString(producer_stream_id) AS producer_stream_id,
    sequence,
    envelope_format,
    envelope_version,
    toString(envelope_id) AS envelope_id,
    toString(payload_sha256) AS payload_sha256,
    toString(previous_payload_sha256) AS previous_payload_sha256,
    toString(transport) AS transport,
    _version
FROM ` + catalogwriter.DeliveryTableName + `
FORMAT JSONEachRow`

func NewDeliveryLedgerCheckpointReader(
	cfg DeliveryLedgerReaderConfig,
) (*DeliveryLedgerCheckpointReader, error) {
	if cfg.URL == "" {
		return nil, errors.New("catalogkafka: delivery ledger URL is required")
	}
	parsed, err := url.Parse(cfg.URL)
	if err != nil {
		return nil, fmt.Errorf("catalogkafka: parse delivery ledger URL: %w", err)
	}
	if (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" {
		return nil, errors.New("catalogkafka: delivery ledger URL must be an absolute http(s) endpoint")
	}
	if parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return nil, errors.New("catalogkafka: delivery ledger URL must not contain credentials, query parameters, or a fragment")
	}
	if cfg.Database == "" || !ledgerDatabaseName.MatchString(cfg.Database) {
		return nil, errors.New("catalogkafka: delivery ledger database must be an unquoted identifier")
	}
	if cfg.Username == "" {
		return nil, errors.New("catalogkafka: delivery ledger username is required")
	}
	if cfg.RequestTimeout == 0 {
		cfg.RequestTimeout = defaultLedgerSeedTimeout
	}
	if cfg.RequestTimeout < 0 || cfg.RequestTimeout > maxLedgerSeedTimeout {
		return nil, fmt.Errorf(
			"catalogkafka: delivery ledger request timeout must be in (0,%s]",
			maxLedgerSeedTimeout,
		)
	}
	if cfg.MaxResponseBytes == 0 {
		cfg.MaxResponseBytes = defaultLedgerSeedResponseBytes
	}
	if cfg.MaxResponseBytes < 0 || cfg.MaxResponseBytes > maxLedgerSeedResponseBytes {
		return nil, fmt.Errorf(
			"catalogkafka: delivery ledger response bytes must be in (0,%d]",
			maxLedgerSeedResponseBytes,
		)
	}

	client := &http.Client{
		Timeout: cfg.RequestTimeout,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return errors.New("catalogkafka: delivery ledger redirects are disabled")
		},
	}
	return &DeliveryLedgerCheckpointReader{client: client, baseURL: parsed, cfg: cfg}, nil
}

// Load returns the latest validated checkpoint for every Kafka stream in the
// ledger. Direct-only and reconcile-only streams are ignored. A stream whose
// effective rows mix transports, skip a sequence, break the payload chain, or
// have conflicting replacement identities makes the whole load fail.
func (reader *DeliveryLedgerCheckpointReader) Load(ctx context.Context) ([]StreamCheckpoint, error) {
	if reader == nil || reader.client == nil || reader.baseURL == nil {
		return nil, errors.New("catalogkafka: nil delivery ledger checkpoint reader")
	}
	if ctx == nil {
		return nil, errors.New("catalogkafka: nil delivery ledger context")
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	requestContext, cancel := context.WithTimeout(ctx, reader.cfg.RequestTimeout)
	defer cancel()

	endpoint := *reader.baseURL
	settings := endpoint.Query()
	settings.Set("database", reader.cfg.Database)
	settings.Set("readonly", "1")
	settings.Set("max_execution_time", formatLedgerSeconds(reader.cfg.RequestTimeout))
	settings.Set("max_memory_usage", strconv.Itoa(256<<20))
	settings.Set("max_rows_to_read", strconv.Itoa(maxLedgerSeedRows))
	settings.Set("max_threads", "1")
	settings.Set("read_overflow_mode", "throw")
	settings.Set("max_result_bytes", strconv.FormatInt(reader.cfg.MaxResponseBytes, 10))
	settings.Set("max_result_rows", strconv.Itoa(maxLedgerSeedRows))
	settings.Set("output_format_json_quote_64bit_integers", "0")
	settings.Set("result_overflow_mode", "throw")
	endpoint.RawQuery = settings.Encode()

	request, err := http.NewRequestWithContext(
		requestContext, http.MethodPost, endpoint.String(), strings.NewReader(deliveryLedgerCheckpointQuery),
	)
	if err != nil {
		return nil, fmt.Errorf("catalogkafka: build delivery ledger request: %w", err)
	}
	request.SetBasicAuth(reader.cfg.Username, reader.cfg.Password)
	request.Header.Set("Content-Type", "text/plain; charset=utf-8")
	request.Header.Set("User-Agent", "futureagi-fi-collector/catalog-ledger-reader")

	response, err := reader.client.Do(request)
	if err != nil {
		if response != nil && response.Body != nil {
			_ = response.Body.Close()
		}
		return nil, fmt.Errorf("catalogkafka: query delivery ledger: %w", err)
	}
	defer response.Body.Close()
	body, err := readLedgerResponse(response.Body, reader.cfg.MaxResponseBytes)
	if err != nil {
		return nil, fmt.Errorf("catalogkafka: read delivery ledger response: %w", err)
	}
	if response.StatusCode != http.StatusOK {
		message := strings.TrimSpace(string(body))
		if message == "" {
			message = http.StatusText(response.StatusCode)
		}
		return nil, fmt.Errorf(
			"catalogkafka: query delivery ledger: ClickHouse HTTP %d: %s",
			response.StatusCode, message,
		)
	}

	rows, err := decodeDeliveryLedgerRows(body)
	if err != nil {
		return nil, fmt.Errorf("catalogkafka: decode delivery ledger: %w", err)
	}
	return checkpointsFromDeliveryLedger(rows)
}

type deliveryLedgerRow struct {
	ProjectID             string `json:"project_id"`
	CatalogEpoch          uint16 `json:"catalog_epoch"`
	ProducerStreamID      string `json:"producer_stream_id"`
	Sequence              uint64 `json:"sequence"`
	EnvelopeFormat        string `json:"envelope_format"`
	EnvelopeVersion       uint16 `json:"envelope_version"`
	EnvelopeID            string `json:"envelope_id"`
	PayloadSHA256         string `json:"payload_sha256"`
	PreviousPayloadSHA256 string `json:"previous_payload_sha256"`
	Transport             string `json:"transport"`
	Version               uint64 `json:"_version"`
}

func decodeDeliveryLedgerRows(body []byte) ([]deliveryLedgerRow, error) {
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	rows := make([]deliveryLedgerRow, 0)
	for {
		var row deliveryLedgerRow
		err := decoder.Decode(&row)
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, err
		}
		if len(rows) == maxLedgerSeedRows {
			return nil, fmt.Errorf("delivery ledger exceeds %d-row validation limit", maxLedgerSeedRows)
		}
		rows = append(rows, row)
	}
	return rows, nil
}

type ledgerSequenceKey struct {
	streamKey
	sequence uint64
}

func checkpointsFromDeliveryLedger(rows []deliveryLedgerRow) ([]StreamCheckpoint, error) {
	latest := make(map[ledgerSequenceKey]deliveryLedgerRow, len(rows))
	streamKinds := make(map[streamKey]uint8)
	for index, row := range rows {
		if err := validateDeliveryLedgerRowKey(row); err != nil {
			return nil, fmt.Errorf("catalogkafka: delivery ledger row %d: %w", index, err)
		}
		stream := streamKey{row.ProjectID, row.CatalogEpoch, row.ProducerStreamID}
		if row.Transport == kafkaDeliveryTransport {
			streamKinds[stream] |= 1
			if err := validateKafkaDeliveryLedgerRow(row); err != nil {
				return nil, fmt.Errorf("catalogkafka: delivery ledger row %d: %w", index, err)
			}
		} else {
			streamKinds[stream] |= 2
			continue
		}
		key := ledgerSequenceKey{
			streamKey: stream,
			sequence:  row.Sequence,
		}
		current, exists := latest[key]
		if exists && !sameLedgerSequenceIdentity(row, current) {
			return nil, fmt.Errorf(
				"catalogkafka: delivery ledger has conflicting rows for project %s epoch %d stream %s sequence %d",
				row.ProjectID, row.CatalogEpoch, row.ProducerStreamID, row.Sequence,
			)
		}
		switch {
		case !exists || row.Version > current.Version:
			latest[key] = row
		}
	}
	for key, kinds := range streamKinds {
		if kinds == 3 {
			return nil, fmt.Errorf(
				"catalogkafka: delivery ledger stream project %s epoch %d stream %s mixes Kafka and non-Kafka transports",
				key.projectID, key.epoch, key.streamID,
			)
		}
	}

	streams := make(map[streamKey][]deliveryLedgerRow)
	for _, row := range latest {
		key := streamKey{row.ProjectID, row.CatalogEpoch, row.ProducerStreamID}
		streams[key] = append(streams[key], row)
	}

	checkpoints := make([]StreamCheckpoint, 0, len(streams))
	for key, streamRows := range streams {
		sort.Slice(streamRows, func(left, right int) bool {
			return streamRows[left].Sequence < streamRows[right].Sequence
		})
		for index, row := range streamRows {
			wantSequence := uint64(index + 1)
			if row.Sequence != wantSequence {
				return nil, fmt.Errorf(
					"catalogkafka: delivery ledger stream project %s epoch %d stream %s has sequence %d, require contiguous %d",
					key.projectID, key.epoch, key.streamID, row.Sequence, wantSequence,
				)
			}
			wantPrevious := ZeroSHA256
			if index > 0 {
				wantPrevious = streamRows[index-1].PayloadSHA256
			}
			if row.PreviousPayloadSHA256 != wantPrevious {
				return nil, fmt.Errorf(
					"catalogkafka: delivery ledger stream project %s epoch %d stream %s breaks payload chain at sequence %d",
					key.projectID, key.epoch, key.streamID, row.Sequence,
				)
			}
		}
		last := streamRows[len(streamRows)-1]
		checkpoints = append(checkpoints, StreamCheckpoint{
			ProjectID: key.projectID, CatalogEpoch: key.epoch, ProducerStreamID: key.streamID,
			Sequence: last.Sequence, PayloadSHA256: last.PayloadSHA256, EnvelopeID: last.EnvelopeID,
		})
	}
	sort.Slice(checkpoints, func(left, right int) bool {
		if checkpoints[left].ProjectID != checkpoints[right].ProjectID {
			return checkpoints[left].ProjectID < checkpoints[right].ProjectID
		}
		if checkpoints[left].CatalogEpoch != checkpoints[right].CatalogEpoch {
			return checkpoints[left].CatalogEpoch < checkpoints[right].CatalogEpoch
		}
		return checkpoints[left].ProducerStreamID < checkpoints[right].ProducerStreamID
	})
	return checkpoints, nil
}

func validateDeliveryLedgerRowKey(row deliveryLedgerRow) error {
	if err := validateCanonicalUUID("ledger project", row.ProjectID); err != nil {
		return err
	}
	if row.CatalogEpoch == 0 {
		return errors.New("zero catalog epoch")
	}
	if err := validateCanonicalUUID("ledger producer stream", row.ProducerStreamID); err != nil {
		return err
	}
	if row.Sequence == 0 {
		return errors.New("zero sequence")
	}
	switch row.Transport {
	case directDeliveryTransport, kafkaDeliveryTransport, "reconcile":
	default:
		return fmt.Errorf("unsupported transport %q", row.Transport)
	}
	if row.Version == 0 {
		return errors.New("zero replacement version")
	}
	return nil
}

func validateKafkaDeliveryLedgerRow(row deliveryLedgerRow) error {
	if row.EnvelopeFormat != EnvelopeFormat || row.EnvelopeVersion != EnvelopeVersion {
		return fmt.Errorf("unsupported envelope %q version %d", row.EnvelopeFormat, row.EnvelopeVersion)
	}
	if !isLowerSHA256(row.EnvelopeID) || !isLowerSHA256(row.PayloadSHA256) ||
		!isLowerSHA256(row.PreviousPayloadSHA256) {
		return errors.New("invalid envelope or payload digest")
	}
	return nil
}

func sameLedgerSequenceIdentity(left, right deliveryLedgerRow) bool {
	return left.ProjectID == right.ProjectID &&
		left.CatalogEpoch == right.CatalogEpoch &&
		left.ProducerStreamID == right.ProducerStreamID &&
		left.Sequence == right.Sequence &&
		left.EnvelopeFormat == right.EnvelopeFormat &&
		left.EnvelopeVersion == right.EnvelopeVersion &&
		left.EnvelopeID == right.EnvelopeID &&
		left.PayloadSHA256 == right.PayloadSHA256 &&
		left.PreviousPayloadSHA256 == right.PreviousPayloadSHA256 &&
		left.Transport == right.Transport
}

func readLedgerResponse(reader io.Reader, maxBytes int64) ([]byte, error) {
	body, err := io.ReadAll(io.LimitReader(reader, maxBytes+1))
	if err != nil {
		return nil, err
	}
	if int64(len(body)) > maxBytes {
		return nil, fmt.Errorf("response exceeds %d-byte limit", maxBytes)
	}
	return body, nil
}

func formatLedgerSeconds(value time.Duration) string {
	return strconv.FormatFloat(value.Seconds(), 'f', 6, 64)
}
