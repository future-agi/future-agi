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
// writes. Replicated engines require an automatic majority quorum; plain
// engines retain their local-write semantics. This is not an all-replica read
// barrier. A partial/ambiguous result is retryable with the same observations.
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
	q.Set("insert_quorum", "auto")
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
