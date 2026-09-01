package propertycatalog

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const (
	maxCatalogInsertBytes   = 16 << 20
	prodCatalogDatabaseName = "property_catalog"
)

var reservedCatalogDatabases = map[string]struct{}{
	"default": {}, "futureagi": {}, "information_schema": {}, "system": {},
}

var definitionColumns = []string{
	"organization_id", "workspace_id", "catalog_epoch", "catalog_revision", "build_token", "projection_version",
	"binding_id", "visibility_scope", "visibility_id", "source_adapter", "source_entity_id",
	"source_version", "source_fingerprint", "producer_stream_id", "producer_sequence", "property_id",
	"property_kind", "category", "category_rank", "source_rank", "definition_source", "primary_source",
	"primary_source_folded", "source_tokens", "value_adapter", "name", "display_name", "sort_name_folded",
	"search_text_folded", "role", "definition_json", "definition_sha256", "first_seen", "last_seen",
	"is_deleted", "deleted_at", "state_sha256", "emitted_at",
}

var attributeValueColumns = []string{
	"organization_id", "workspace_id", "project_id", "catalog_epoch", "catalog_revision", "build_token", "source_kind",
	"attribute_key", "attribute_type", "value_fingerprint", "value_json", "value_search_text_folded",
	"first_seen", "last_seen",
}

var deliveryColumns = []string{
	"organization_id", "workspace_id", "catalog_epoch", "catalog_revision", "build_token", "projection_version",
	"source_adapter", "producer_stream_id", "sequence", "envelope_format", "envelope_version", "envelope_id",
	"payload_sha256", "previous_payload_sha256", "source_batch_digest", "outcome", "terminal", "gap_reasons",
	"source_rows", "definition_rows", "value_rows", "tombstone_rows", "transport", "kafka_partition",
	"kafka_offset", "delivered_at", "_version",
}

type ClickHouseSinkConfig struct {
	URL            string
	Database       string
	Environment    string
	Username       string
	Password       string
	RequestTimeout time.Duration
	RoundTripper   http.RoundTripper
}

// ClickHouseSink is closed over the two new catalog data tables and their
// delivery ledger. It has no generic table insertion method and never accepts
// a canonical spans table name from configuration.
type ClickHouseSink struct {
	baseURL  *url.URL
	database string
	username string
	password string
	client   *http.Client
}

func NewClickHouseSink(cfg ClickHouseSinkConfig) (*ClickHouseSink, error) {
	parsed, err := url.Parse(cfg.URL)
	if err != nil || parsed == nil || (parsed.Scheme != "http" && parsed.Scheme != "https") ||
		parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" ||
		(parsed.Path != "" && parsed.Path != "/") {
		return nil, errors.New("propertycatalog: ClickHouse URL must be a bare http(s) origin")
	}
	if !safeClickHouseDatabase(cfg.Environment, cfg.Database) {
		return nil, errors.New(
			"propertycatalog: ClickHouse database must match the exact environment-specific isolated catalog name",
		)
	}
	if cfg.Username == "" || strings.TrimSpace(cfg.Username) != cfg.Username || len(cfg.Username) > 255 {
		return nil, errors.New("propertycatalog: dedicated ClickHouse username is required")
	}
	if cfg.RequestTimeout == 0 {
		cfg.RequestTimeout = DefaultDeliveryTransportTimeout
	}
	if cfg.RequestTimeout < 0 || cfg.RequestTimeout > MaxDeliveryTimeout {
		return nil, fmt.Errorf(
			"propertycatalog: ClickHouse request timeout must be in (0,%s]",
			MaxDeliveryTimeout,
		)
	}
	transport := cfg.RoundTripper
	if transport == nil {
		transport = http.DefaultTransport
	}
	client := &http.Client{
		Transport: transport, Timeout: cfg.RequestTimeout,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	return &ClickHouseSink{
		baseURL: parsed, database: cfg.Database, username: cfg.Username, password: cfg.Password, client: client,
	}, nil
}

func (s *ClickHouseSink) InsertPropertyCatalog(ctx context.Context, table Table, rows []map[string]any) error {
	var columns []string
	switch table {
	case DefinitionTable:
		columns = definitionColumns
	case AttributeValueTable:
		columns = attributeValueColumns
	default:
		return fmt.Errorf("propertycatalog: forbidden ClickHouse data table %q", table)
	}
	return s.insert(ctx, string(table), columns, rows)
}

func (s *ClickHouseSink) InsertPropertyCatalogDelivery(ctx context.Context, rows []map[string]any) error {
	return s.insert(ctx, "property_catalog_deliveries", deliveryColumns, rows)
}

func (s *ClickHouseSink) insert(ctx context.Context, table string, columns []string, rows []map[string]any) error {
	if s == nil || s.baseURL == nil || s.client == nil || ctx == nil {
		return errors.New("propertycatalog: ClickHouse insert requires a sink context")
	}
	if len(rows) == 0 {
		return nil
	}
	if len(rows) > MaxRowsPerEnvelope {
		return errors.New("propertycatalog: ClickHouse insert exceeds row limit")
	}
	want := make(map[string]struct{}, len(columns))
	for _, column := range columns {
		want[column] = struct{}{}
	}
	var body bytes.Buffer
	encoder := json.NewEncoder(&body)
	encoder.SetEscapeHTML(false)
	for index, row := range rows {
		if len(row) != len(want) {
			return fmt.Errorf("propertycatalog: %s row %d has %d columns, require %d", table, index, len(row), len(want))
		}
		for column := range row {
			if _, ok := want[column]; !ok {
				return fmt.Errorf("propertycatalog: %s row %d contains forbidden column %q", table, index, column)
			}
		}
		if err := encoder.Encode(row); err != nil {
			return fmt.Errorf("propertycatalog: encode %s row %d: %w", table, index, err)
		}
		if body.Len() > maxCatalogInsertBytes {
			return errors.New("propertycatalog: ClickHouse insert exceeds byte limit")
		}
	}
	endpoint := *s.baseURL
	query := endpoint.Query()
	query.Set("database", s.database)
	query.Set("query", fmt.Sprintf(
		"INSERT INTO %s (%s) FORMAT JSONEachRow", table, strings.Join(columns, ","),
	))
	endpoint.RawQuery = query.Encode()
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint.String(), bytes.NewReader(body.Bytes()))
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", "application/x-ndjson")
	request.SetBasicAuth(s.username, s.password)
	response, err := s.client.Do(request)
	if err != nil {
		return fmt.Errorf("propertycatalog: ClickHouse %s insert: %w", table, err)
	}
	defer response.Body.Close()
	responseBody, _ := io.ReadAll(io.LimitReader(response.Body, 4<<10))
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf(
			"propertycatalog: ClickHouse %s insert returned HTTP %d: %s",
			table, response.StatusCode, strings.TrimSpace(string(responseBody)),
		)
	}
	return nil
}

func safeClickHouseDatabase(environment, value string) bool {
	switch environment {
	case DevelopmentEnvironment:
		if len(value) == 0 || len(value) > 128 || value == prodCatalogDatabaseName {
			return false
		}
		if _, reserved := reservedCatalogDatabases[value]; reserved {
			return false
		}
		for index, char := range value {
			if char > 127 || (!(char >= 'a' && char <= 'z') &&
				!(char >= '0' && char <= '9') && char != '_') ||
				(index == 0 && !(char >= 'a' && char <= 'z')) {
				return false
			}
		}
		return true
	case ProductionEnvironment:
		return value == prodCatalogDatabaseName
	default:
		return false
	}
}
