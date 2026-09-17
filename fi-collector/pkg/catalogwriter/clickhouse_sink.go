package catalogwriter

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
	"strconv"
	"strings"
	"time"
)

// DeliveryTableName is the append-only delivery/progress table owned by the
// attribute catalog. It is intentionally not a Table value: Writer's Inserter
// contract remains closed over key/value rows, while delivery acknowledgement
// is a distinct operation that must happen after both data tables succeed.
const DeliveryTableName = "span_attribute_catalog_deliveries"

const (
	defaultCatalogRequestTimeout = 5 * time.Second
	maxCatalogRequestTimeout     = 10 * time.Second
	defaultCatalogRequestBytes   = 2 << 20
	maxCatalogRequestBytes       = 8 << 20
	defaultCatalogResponseBytes  = 64 << 10
	maxCatalogResponseBytes      = 1 << 20
	defaultCatalogMemoryBytes    = 256 << 20
	maxCatalogMemoryBytes        = 1 << 30
	defaultCatalogMaxThreads     = 2
	maxCatalogMaxThreads         = 8
)

var clickHouseDatabaseName = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)

// ClickHouseSinkConfig configures the lightweight, direct HTTP delivery mode.
// Zero-valued bounds receive conservative defaults, but explicit values may
// never exceed the hard ceilings above. Kafka mode can share the same sink on
// its consumer side; the producer is deliberately outside this transport.
type ClickHouseSinkConfig struct {
	URL      string
	Database string
	Username string
	Password string

	RequestTimeout   time.Duration
	MaxRequestBytes  int
	MaxResponseBytes int64
	MaxExecutionTime time.Duration
	MaxMemoryUsage   uint64
	MaxThreads       int

	// AsyncInsert uses ClickHouse's server-side async queue, but always sets
	// wait_for_async_insert=1. A successful response therefore still means the
	// batch was accepted by the destination table, not merely queued in HTTP.
	AsyncInsert bool
}

// DeliveryInserter is separate from Inserter so a key/value call can never be
// confused with the durable acknowledgement that permits spool deletion.
type DeliveryInserter interface {
	InsertDelivery(context.Context, []map[string]any) error
}

// ClickHouseSink is a single-attempt JSONEachRow transport. It performs no
// retry and has no dead-letter path: catalogwriter.Writer's durable spool owns
// retry ordering and must retain an envelope on every returned error.
type ClickHouseSink struct {
	cfg      ClickHouseSinkConfig
	client   *http.Client
	baseURL  *url.URL
	settings url.Values
}

var _ Inserter = (*ClickHouseSink)(nil)
var _ DeliveryInserter = (*ClickHouseSink)(nil)

// NewClickHouseSink constructs a catalog-only HTTP transport. The URL must be
// a bare http(s) endpoint; embedded credentials, query parameters, fragments,
// and non-HTTP schemes are rejected to keep authentication and settings
// explicit and reviewable.
func NewClickHouseSink(cfg ClickHouseSinkConfig) (*ClickHouseSink, error) {
	if cfg.URL == "" {
		return nil, errors.New("catalogwriter: ClickHouse URL is required")
	}
	parsed, err := url.Parse(cfg.URL)
	if err != nil {
		return nil, fmt.Errorf("catalogwriter: parse ClickHouse URL: %w", err)
	}
	if (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" {
		return nil, errors.New("catalogwriter: ClickHouse URL must be an absolute http(s) endpoint")
	}
	if parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return nil, errors.New("catalogwriter: ClickHouse URL must not contain credentials, query parameters, or a fragment")
	}
	if cfg.Database == "" {
		cfg.Database = "default"
	}
	if !clickHouseDatabaseName.MatchString(cfg.Database) {
		return nil, errors.New("catalogwriter: ClickHouse database must be an unquoted identifier")
	}
	if cfg.Username == "" && cfg.Password != "" {
		return nil, errors.New("catalogwriter: ClickHouse password requires a username")
	}

	if cfg.RequestTimeout == 0 {
		cfg.RequestTimeout = defaultCatalogRequestTimeout
	}
	if cfg.RequestTimeout < 0 || cfg.RequestTimeout > maxCatalogRequestTimeout {
		return nil, fmt.Errorf("catalogwriter: ClickHouse request timeout must be in (0,%s]", maxCatalogRequestTimeout)
	}
	if cfg.MaxRequestBytes == 0 {
		cfg.MaxRequestBytes = defaultCatalogRequestBytes
	}
	if cfg.MaxRequestBytes < 0 || cfg.MaxRequestBytes > maxCatalogRequestBytes {
		return nil, fmt.Errorf("catalogwriter: ClickHouse request bytes must be in (0,%d]", maxCatalogRequestBytes)
	}
	if cfg.MaxResponseBytes == 0 {
		cfg.MaxResponseBytes = defaultCatalogResponseBytes
	}
	if cfg.MaxResponseBytes < 0 || cfg.MaxResponseBytes > maxCatalogResponseBytes {
		return nil, fmt.Errorf("catalogwriter: ClickHouse response bytes must be in (0,%d]", maxCatalogResponseBytes)
	}
	if cfg.MaxExecutionTime == 0 {
		cfg.MaxExecutionTime = cfg.RequestTimeout
	}
	if cfg.MaxExecutionTime < 0 || cfg.MaxExecutionTime > cfg.RequestTimeout {
		return nil, errors.New("catalogwriter: ClickHouse execution time must be positive and no longer than request timeout")
	}
	if cfg.MaxMemoryUsage == 0 {
		cfg.MaxMemoryUsage = defaultCatalogMemoryBytes
	}
	if cfg.MaxMemoryUsage > maxCatalogMemoryBytes {
		return nil, fmt.Errorf("catalogwriter: ClickHouse memory setting must be in (0,%d]", maxCatalogMemoryBytes)
	}
	if cfg.MaxThreads == 0 {
		cfg.MaxThreads = defaultCatalogMaxThreads
	}
	if cfg.MaxThreads < 0 || cfg.MaxThreads > maxCatalogMaxThreads {
		return nil, fmt.Errorf("catalogwriter: ClickHouse max threads must be in (0,%d]", maxCatalogMaxThreads)
	}

	settings := make(url.Values)
	settings.Set("database", cfg.Database)
	settings.Set("wait_end_of_query", "1")
	settings.Set("input_format_parallel_parsing", "0")
	settings.Set("input_format_defaults_for_omitted_fields", "0")
	settings.Set("max_execution_time", formatClickHouseDuration(cfg.MaxExecutionTime))
	settings.Set("max_memory_usage", strconv.FormatUint(cfg.MaxMemoryUsage, 10))
	settings.Set("max_threads", strconv.Itoa(cfg.MaxThreads))
	settings.Set("insert_deduplicate", "1")
	if cfg.AsyncInsert {
		settings.Set("async_insert", "1")
		settings.Set("wait_for_async_insert", "1")
	} else {
		settings.Set("async_insert", "0")
	}

	client := &http.Client{
		Timeout: cfg.RequestTimeout,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return errors.New("catalogwriter: ClickHouse redirects are disabled")
		},
	}
	return &ClickHouseSink{cfg: cfg, client: client, baseURL: parsed, settings: settings}, nil
}

// InsertCatalog implements Inserter and admits only Writer's two data tables.
// Even a forged Table string is rejected before encoding or network access.
func (s *ClickHouseSink) InsertCatalog(ctx context.Context, table Table, rows []map[string]any) error {
	spec, ok := catalogDataInsertSpecs[table]
	if !ok {
		return fmt.Errorf("catalogwriter: catalog insert table %q is not allowlisted", table)
	}
	return s.insert(ctx, spec, rows)
}

// InsertDelivery writes durable transport/progress rows only to the new
// delivery table. It deliberately does not implement ProgressSink: the direct
// runtime, like Kafka, reaches this method only through the durable version-3
// envelope sequencer, so stream sequence and payload-chain fields are never
// guessed from the older ProgressRecord shape.
func (s *ClickHouseSink) InsertDelivery(ctx context.Context, rows []map[string]any) error {
	return s.insert(ctx, catalogDeliveryInsertSpec, rows)
}

type catalogInsertSpec struct {
	table         string
	columns       map[string]struct{}
	legacyColumns map[string]struct{}
}

var catalogDataInsertSpecs = map[Table]catalogInsertSpec{
	KeyTable: {
		table: string(KeyTable),
		columns: columnSet(
			"project_id", "source_kind", "attribute_key", "key_folded", "attribute_type",
			"first_seen", "last_seen", "catalog_epoch",
		),
		legacyColumns: columnSet(
			"project_id", "attribute_key", "key_folded", "attribute_type",
			"first_seen", "last_seen", "catalog_epoch",
		),
	},
	ValueTable: {
		table: string(ValueTable),
		columns: columnSet(
			"project_id", "source_kind", "attribute_key", "attribute_type", "value_fingerprint",
			"value_json", "value_search_text", "first_seen", "last_seen", "catalog_epoch",
		),
		legacyColumns: columnSet(
			"project_id", "attribute_key", "attribute_type", "value_fingerprint",
			"value_json", "value_search_text", "first_seen", "last_seen", "catalog_epoch",
		),
	},
}

var catalogDeliveryInsertSpec = catalogInsertSpec{
	table: DeliveryTableName,
	columns: columnSet(
		"envelope_format", "envelope_version", "envelope_id", "project_id",
		"catalog_epoch", "producer_stream_id", "sequence", "payload_sha256",
		"previous_payload_sha256", "source_batch_digest", "outcome", "gap_reasons",
		"source_min_start", "source_max_start", "source_rows", "key_rows", "value_rows", "transport",
		"kafka_partition", "kafka_offset", "delivered_at", "_version",
	),
}

func columnSet(columns ...string) map[string]struct{} {
	out := make(map[string]struct{}, len(columns))
	for _, column := range columns {
		out[column] = struct{}{}
	}
	return out
}

func (s *ClickHouseSink) insert(ctx context.Context, spec catalogInsertSpec, rows []map[string]any) error {
	if s == nil {
		return errors.New("catalogwriter: nil ClickHouse sink")
	}
	if ctx == nil {
		return errors.New("catalogwriter: nil ClickHouse context")
	}
	if len(rows) == 0 {
		return nil
	}
	if err := validateCatalogColumns(spec, rows); err != nil {
		return err
	}
	body, err := encodeCatalogRows(rows, s.cfg.MaxRequestBytes)
	if err != nil {
		return fmt.Errorf("catalogwriter: encode %s JSONEachRow: %w", spec.table, err)
	}

	endpoint := *s.baseURL
	query := cloneValues(s.settings)
	query.Set("query", "INSERT INTO "+spec.table+" FORMAT JSONEachRow")
	endpoint.RawQuery = query.Encode()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint.String(), bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("catalogwriter: build ClickHouse request: %w", err)
	}
	if s.cfg.Username != "" {
		req.SetBasicAuth(s.cfg.Username, s.cfg.Password)
	}
	req.Header.Set("Content-Type", "application/x-ndjson")
	req.Header.Set("X-ClickHouse-Format", "JSONEachRow")
	req.Header.Set("User-Agent", "futureagi-fi-collector/catalogwriter")

	response, err := s.client.Do(req)
	if err != nil {
		if response != nil && response.Body != nil {
			_ = response.Body.Close()
		}
		return fmt.Errorf("catalogwriter: insert %s: %w", spec.table, err)
	}
	defer response.Body.Close()
	responseBody, err := readBoundedResponse(response.Body, s.cfg.MaxResponseBytes)
	if err != nil {
		return fmt.Errorf("catalogwriter: insert %s response: %w", spec.table, err)
	}
	if response.StatusCode != http.StatusOK {
		message := strings.TrimSpace(string(responseBody))
		if message == "" {
			message = http.StatusText(response.StatusCode)
		}
		return fmt.Errorf("catalogwriter: insert %s: ClickHouse HTTP %d: %s", spec.table, response.StatusCode, message)
	}
	return nil
}

func validateCatalogColumns(spec catalogInsertSpec, rows []map[string]any) error {
	var selected map[string]struct{}
	for index, row := range rows {
		rowColumns, ok := exactCatalogColumnShape(row, spec.columns, spec.legacyColumns)
		if !ok {
			allowed := []int{len(spec.columns)}
			if spec.legacyColumns != nil {
				allowed = append(allowed, len(spec.legacyColumns))
			}
			return fmt.Errorf(
				"catalogwriter: %s row %d has a non-catalog column shape (%d columns; allowed %v)",
				spec.table, index, len(row), allowed,
			)
		}
		if selected == nil {
			selected = rowColumns
		} else if !sameColumnSet(selected, rowColumns) {
			return fmt.Errorf(
				"catalogwriter: %s row %d mixes legacy and source-kind catalog shapes",
				spec.table, index,
			)
		}
	}
	return nil
}

func exactCatalogColumnShape(
	row map[string]any, shapes ...map[string]struct{},
) (map[string]struct{}, bool) {
	for _, shape := range shapes {
		if shape == nil || len(row) != len(shape) {
			continue
		}
		exact := true
		for column := range row {
			if _, exists := shape[column]; !exists {
				exact = false
				break
			}
		}
		if exact {
			return shape, true
		}
	}
	return nil, false
}

func sameColumnSet(left, right map[string]struct{}) bool {
	if len(left) != len(right) {
		return false
	}
	for column := range left {
		if _, exists := right[column]; !exists {
			return false
		}
	}
	return true
}

var errCatalogRequestTooLarge = errors.New("catalog request exceeds encoded byte limit")

type boundedJSONBuffer struct {
	bytes.Buffer
	max int
}

func (b *boundedJSONBuffer) Write(value []byte) (int, error) {
	if len(value) > b.max-b.Len() {
		return 0, errCatalogRequestTooLarge
	}
	return b.Buffer.Write(value)
}

func encodeCatalogRows(rows []map[string]any, maxBytes int) ([]byte, error) {
	buffer := &boundedJSONBuffer{max: maxBytes}
	encoder := json.NewEncoder(buffer)
	encoder.SetEscapeHTML(false)
	for index, row := range rows {
		if err := encoder.Encode(row); err != nil {
			return nil, fmt.Errorf("row %d: %w", index, err)
		}
	}
	return buffer.Bytes(), nil
}

func readBoundedResponse(reader io.Reader, maxBytes int64) ([]byte, error) {
	value, err := io.ReadAll(io.LimitReader(reader, maxBytes+1))
	if err != nil {
		return nil, err
	}
	if int64(len(value)) > maxBytes {
		return nil, fmt.Errorf("ClickHouse response exceeds %d-byte limit", maxBytes)
	}
	return value, nil
}

func cloneValues(source url.Values) url.Values {
	out := make(url.Values, len(source))
	for key, values := range source {
		out[key] = append([]string(nil), values...)
	}
	return out
}

func formatClickHouseDuration(value time.Duration) string {
	return strconv.FormatFloat(value.Seconds(), 'f', 6, 64)
}
