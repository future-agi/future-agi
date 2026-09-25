package observedcatalog

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

type ClickHouseConfig struct {
	URL, Database, Username, Password string
	Timeout                           time.Duration
}

const maxClickHouseRequestTimeout = 30 * time.Second

type ClickHouseSink struct {
	cfg    ClickHouseConfig
	origin *url.URL
	client *http.Client
}

func NewClickHouseSink(cfg ClickHouseConfig) (*ClickHouseSink, error) {
	u, err := url.Parse(cfg.URL)
	if err != nil || u == nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return nil, errors.New("observedcatalog: ClickHouse URL must be a bare HTTP origin")
	}
	if cfg.Database == "" || len(cfg.Database) > 128 {
		return nil, errors.New("observedcatalog: database required")
	}
	for i, c := range cfg.Database {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c == '_' || i > 0 && c >= '0' && c <= '9') {
			return nil, errors.New("observedcatalog: invalid database identifier")
		}
	}
	if cfg.Timeout == 0 {
		cfg.Timeout = 10 * time.Second
	}
	if cfg.Timeout <= 0 || cfg.Timeout > maxClickHouseRequestTimeout {
		return nil, errors.New("observedcatalog: invalid ClickHouse timeout")
	}
	return &ClickHouseSink{cfg: cfg, origin: u, client: &http.Client{Timeout: cfg.Timeout, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}, nil
}

// Insert writes keys before values and acknowledges only complete synchronous
// writes. Multi-replica engines require an automatic majority quorum; single
// replicas and plain engines retain local-write semantics. This is not an
// all-replica read barrier. Partial results can replay the same observations.
func (s *ClickHouseSink) Insert(ctx context.Context, batch Batch) error {
	chunks, err := Chunk(batch)
	if err != nil {
		return err
	}
	for _, chunk := range chunks {
		if len(chunk.Keys) > 0 {
			if err := s.insert(ctx, KeyTable, chunk.Keys); err != nil {
				return err
			}
		}
		if len(chunk.Values) > 0 {
			if err := s.insert(ctx, ValueTable, chunk.Values); err != nil {
				return err
			}
		}
	}
	return nil
}

func (s *ClickHouseSink) insert(ctx context.Context, table string, rows any) error {
	// Metadata and INSERT share the existing per-table deadline.
	ctx, cancel := context.WithTimeout(ctx, s.cfg.Timeout)
	defer cancel()
	quorum, err := s.insertQuorum(ctx, table)
	if err != nil {
		return err
	}
	var body bytes.Buffer
	encoder := json.NewEncoder(&body)
	encoder.SetEscapeHTML(false)
	switch values := rows.(type) {
	case []KeyRow:
		for _, r := range values {
			if err := encoder.Encode(r); err != nil {
				return err
			}
		}
	case []ValueRow:
		for _, r := range values {
			if err := encoder.Encode(r); err != nil {
				return err
			}
		}
	default:
		return errors.New("observedcatalog: unsupported rows")
	}
	u := *s.origin
	q := u.Query()
	q.Set("database", s.cfg.Database)
	q.Set("query", "INSERT INTO "+table+" FORMAT JSONEachRow")
	q.Set("async_insert", "0")
	q.Set("wait_end_of_query", "1")
	q.Set("insert_quorum", quorum)
	// Leave response/transport headroom inside both the HTTP timeout and the
	// caller's remaining batch deadline. Quorum timeout is milliseconds.
	budget := s.cfg.Timeout
	if deadline, ok := ctx.Deadline(); ok && time.Until(deadline) < budget {
		budget = time.Until(deadline)
	}
	quorumMillis := (budget / 2).Milliseconds()
	if quorumMillis < 1 {
		return errors.New("observedcatalog: insufficient ClickHouse quorum deadline")
	}
	q.Set("insert_quorum_timeout", strconv.FormatInt(quorumMillis, 10))
	u.RawQuery = q.Encode()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, u.String(), &body)
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/x-ndjson")
	if s.cfg.Username != "" {
		req.SetBasicAuth(s.cfg.Username, s.cfg.Password)
	}
	response, err := s.client.Do(req)
	if err != nil {
		return errors.New("observedcatalog: ClickHouse request failed")
	}
	defer response.Body.Close()
	result, err := io.ReadAll(io.LimitReader(response.Body, 4097))
	if err != nil || len(result) > 4096 {
		return errors.New("observedcatalog: incomplete or oversized ClickHouse response")
	}
	// An INSERT has no result body. Do not acknowledge a late exception in a
	// response whose status was already sent as 200, or log server payloads.
	if response.StatusCode != http.StatusOK || len(strings.TrimSpace(string(result))) != 0 || response.Header.Get("X-ClickHouse-Exception-Code") != "" {
		return fmt.Errorf("observedcatalog: %s insert not confirmed (HTTP %d)", table, response.StatusCode)
	}
	return nil
}

// ClickHouse 25.3's automatic quorum can time out waiting for another replica
// to confirm a one-replica table. Resolve the actual table before each write;
// never cache a single-replica downgrade across topology changes.
func (s *ClickHouseSink) insertQuorum(ctx context.Context, table string) (string, error) {
	var topology struct {
		Engine   string  `json:"engine"`
		Replicas *uint64 `json:"replicas"`
	}
	engineQuery := `SELECT engine FROM system.tables
 WHERE database = {catalog:String} AND name = {table:String} LIMIT 2 FORMAT JSONEachRow`
	if err := s.catalogMetadata(ctx, table, engineQuery, &topology); err != nil {
		return "", err
	}
	if topology.Engine == "AggregatingMergeTree" {
		return "auto", nil
	}
	if topology.Engine == "ReplicatedAggregatingMergeTree" {
		topology.Replicas = nil
		replicaQuery := `SELECT total_replicas AS replicas FROM system.replicas
 WHERE database = {catalog:String} AND table = {table:String} LIMIT 2 FORMAT JSONEachRow`
		// Plain OSS tables must not require access to replication metadata.
		if err := s.catalogMetadata(ctx, table, replicaQuery, &topology); err != nil {
			return "", err
		}
		if topology.Replicas != nil && *topology.Replicas > 0 {
			if *topology.Replicas == 1 {
				return "1", nil
			}
			return "auto", nil
		}
	}
	return "", errors.New("observedcatalog: unsupported or missing ClickHouse catalog topology")
}

func (s *ClickHouseSink) catalogMetadata(ctx context.Context, table, query string, result any) error {
	u := *s.origin
	q := u.Query()
	q.Set("database", s.cfg.Database)
	q.Set("param_catalog", s.cfg.Database)
	q.Set("param_table", table)
	q.Set("query", query)
	q.Set("readonly", "1")
	q.Set("wait_end_of_query", "1")
	q.Set("max_threads", "1")
	q.Set("max_execution_time", "5")
	q.Set("max_memory_usage", "67108864")
	q.Set("max_result_rows", "2")
	q.Set("max_result_bytes", "4096")
	q.Set("result_overflow_mode", "throw")
	q.Set("output_format_json_quote_64bit_integers", "0")
	u.RawQuery = q.Encode()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, u.String(), nil)
	if err != nil {
		return err
	}
	if s.cfg.Username != "" {
		req.SetBasicAuth(s.cfg.Username, s.cfg.Password)
	}
	response, err := s.client.Do(req)
	if err != nil {
		return errors.New("observedcatalog: ClickHouse topology request failed")
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 4097))
	if err != nil || len(body) > 4096 || response.StatusCode != http.StatusOK || response.Header.Get("X-ClickHouse-Exception-Code") != "" {
		return errors.New("observedcatalog: ClickHouse topology not confirmed (check system.tables/system.replicas SELECT grants)")
	}
	if err := json.Unmarshal(body, result); err != nil {
		return errors.New("observedcatalog: invalid ClickHouse topology response")
	}
	return nil
}
