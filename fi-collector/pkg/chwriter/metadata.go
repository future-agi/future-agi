package chwriter

import (
	"bufio"
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

	"github.com/future-agi/future-agi/fi-collector/pkg/trajectory"
)

// StatusError is a non-200 ClickHouse response. Error() keeps the historical
// "CH HTTP <status>: <exception>" text.
type StatusError struct {
	Status int
	// Code is the ClickHouse exception code (0 when the response named none).
	Code int
	// Message is the start of the response body (the DB::Exception line).
	Message string
}

func (e *StatusError) Error() string {
	return fmt.Sprintf("CH HTTP %d: %s", e.Status, e.Message)
}

var exceptionCode = regexp.MustCompile(`Code: (\d+)\.`)

func newStatusError(resp *http.Response, body []byte) *StatusError {
	e := &StatusError{Status: resp.StatusCode, Message: truncate(string(body), 800)}
	if v := resp.Header.Get("X-ClickHouse-Exception-Code"); v != "" {
		e.Code, _ = strconv.Atoi(strings.TrimSpace(v))
	}
	if e.Code == 0 {
		if m := exceptionCode.FindSubmatch(body); m != nil {
			e.Code, _ = strconv.Atoi(string(m[1]))
		}
	}
	return e
}

// ClickHouse exception codes that mean a row named a column the table does
// not have, or a column of another type.
const (
	codeNoSuchColumnInTable = 16
	codeUnknownIdentifier   = 47
	codeTypeMismatch        = 53
	codeIncorrectData       = 117
)

// IsSchemaMismatch reports whether err carries a ClickHouse response that
// refused an insert because of the table's columns: an unknown column or
// identifier, a type mismatch, or (with input_format_skip_unknown_fields=0)
// an unknown JSONEachRow field.
func IsSchemaMismatch(err error) bool {
	var se *StatusError
	if !errors.As(err, &se) {
		return false
	}
	switch se.Code {
	case codeNoSuchColumnInTable, codeUnknownIdentifier, codeTypeMismatch:
		return true
	case codeIncorrectData:
		return strings.Contains(se.Message, "Unknown field")
	}
	return strings.Contains(se.Message, "Unknown field found") || strings.Contains(se.Message, "NO_SUCH_COLUMN_IN_TABLE")
}

// metadataResponseLimit bounds a metadata response (spans has ~100 columns).
const metadataResponseLimit = 1 << 20

// TableColumns returns name -> type for every column of table in the writer's
// database, read from system.columns with readonly=1. A table that does not
// exist, or that the writer's user cannot see, returns an empty map.
func (w *Writer) TableColumns(ctx context.Context, table string) (map[string]string, error) {
	rows, err := w.metadata(ctx, table,
		`SELECT name, type FROM system.columns WHERE database = {db:String} AND table = {table:String} ORDER BY position FORMAT JSONEachRow`)
	if err != nil {
		return nil, err
	}
	out := make(map[string]string, len(rows))
	for _, row := range rows {
		name, _ := row["name"].(string)
		typ, _ := row["type"].(string)
		if name != "" {
			out[name] = typ
		}
	}
	return out, nil
}

// TableTopology returns table's engine from system.tables and, for a
// Replicated engine, its replica counts from system.replicas. Plain tables
// never read replication metadata.
func (w *Writer) TableTopology(ctx context.Context, table string) (trajectory.TableTopology, error) {
	var topo trajectory.TableTopology
	rows, err := w.metadata(ctx, table,
		`SELECT engine FROM system.tables WHERE database = {db:String} AND name = {table:String} LIMIT 2 FORMAT JSONEachRow`)
	if err != nil {
		return topo, err
	}
	if len(rows) != 1 {
		return topo, nil
	}
	topo.Exists = true
	topo.Engine, _ = rows[0]["engine"].(string)
	if !topo.Replicated() {
		return topo, nil
	}
	rows, err = w.metadata(ctx, table,
		`SELECT total_replicas, active_replicas FROM system.replicas WHERE database = {db:String} AND table = {table:String} LIMIT 2 FORMAT JSONEachRow`)
	if err != nil {
		return topo, err
	}
	if len(rows) == 1 {
		topo.TotalReplicas = jsonInt(rows[0]["total_replicas"])
		topo.ActiveReplicas = jsonInt(rows[0]["active_replicas"])
	}
	return topo, nil
}

func jsonInt(v any) int {
	switch n := v.(type) {
	case float64:
		return int(n)
	case string:
		i, _ := strconv.Atoi(n)
		return i
	}
	return 0
}

// metadata runs one read-only metadata query with the database and table
// bound as parameters, on the writer's endpoint and credentials. Errors never
// include the URL or credentials.
func (w *Writer) metadata(ctx context.Context, table, query string) ([]map[string]any, error) {
	u, err := url.Parse(w.cfg.URL)
	if err != nil {
		return nil, errors.New("chwriter: URL cannot be parsed")
	}
	q := u.Query()
	q.Set("database", w.cfg.Database)
	q.Set("param_db", w.cfg.Database)
	q.Set("param_table", table)
	q.Set("readonly", "1")
	q.Set("wait_end_of_query", "1")
	q.Set("max_threads", "1")
	q.Set("max_execution_time", "5")
	q.Set("max_memory_usage", "67108864")
	q.Set("max_result_rows", "4096")
	q.Set("max_result_bytes", strconv.Itoa(metadataResponseLimit))
	q.Set("result_overflow_mode", "throw")
	q.Set("output_format_json_quote_64bit_integers", "0")
	u.RawQuery = q.Encode()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, u.String(), strings.NewReader(query))
	if err != nil {
		return nil, errors.New("chwriter: build metadata request")
	}
	if w.cfg.Username != "" {
		req.SetBasicAuth(w.cfg.Username, w.cfg.Password)
	}
	resp, err := w.client.Do(req)
	if err != nil {
		var ue *url.Error
		if errors.As(err, &ue) {
			err = ue.Err
		}
		return nil, fmt.Errorf("chwriter: metadata request for %s failed: %w", table, err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, metadataResponseLimit+1))
	if err != nil || len(body) > metadataResponseLimit {
		return nil, fmt.Errorf("chwriter: metadata response for %s incomplete or oversized", table)
	}
	if resp.StatusCode != http.StatusOK || resp.Header.Get("X-ClickHouse-Exception-Code") != "" {
		return nil, fmt.Errorf("chwriter: metadata query for %s not confirmed (check SELECT on system.columns, system.tables, system.replicas): %w", table, newStatusError(resp, body))
	}
	var rows []map[string]any
	scanner := bufio.NewScanner(bytes.NewReader(body))
	scanner.Buffer(make([]byte, 0, 64<<10), metadataResponseLimit)
	for scanner.Scan() {
		line := bytes.TrimSpace(scanner.Bytes())
		if len(line) == 0 {
			continue
		}
		var row map[string]any
		if err := json.Unmarshal(line, &row); err != nil {
			return nil, fmt.Errorf("chwriter: metadata response for %s is not JSONEachRow", table)
		}
		rows = append(rows, row)
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("chwriter: metadata response for %s unreadable", table)
	}
	return rows, nil
}
