package main

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

const maxResponseBytes = 32 << 20

var identifier = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)

var parameterEscaper = strings.NewReplacer(
	"\\", "\\\\", "\t", "\\t", "\n", "\\n", "\r", "\\r",
	"\x00", "\\0", "\b", "\\b", "\f", "\\f",
)

// sourceReader has no write method. It uses a separate read-only identity from
// the catalog consumer; the target database is never passed to this client.
type sourceReader struct {
	url, database, username, password string
	client                            *http.Client
}

func newSourceReader(origin, database, username, password string) (*sourceReader, error) {
	u, err := url.Parse(origin)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" ||
		u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return nil, errors.New("source URL must be a bare http(s) origin without credentials")
	}
	if !identifier.MatchString(database) || username == "" {
		return nil, errors.New("source database identifier and read-only username are required")
	}
	return &sourceReader{origin, database, username, password, &http.Client{
		Timeout:       35 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}}, nil
}

func (s *sourceReader) selectRows(ctx context.Context, sql string, params url.Values) ([]map[string]any, error) {
	if !strings.HasPrefix(sql, "SELECT ") || strings.Contains(sql, ";") {
		return nil, errors.New("backfill source supports one SELECT only")
	}
	u, _ := url.Parse(s.url)
	q := url.Values{
		"database": {s.database}, "readonly": {"1"},
		"max_execution_time": {"30"}, "max_threads": {"1"},
		"max_memory_usage": {"536870912"}, "max_bytes_to_read": {"1073741824"},
		"max_result_bytes": {fmt.Sprint(maxResponseBytes)}, "max_result_rows": {"100000"},
		"result_overflow_mode": {"throw"}, "read_overflow_mode": {"throw"},
		"timeout_overflow_mode": {"throw"}, "output_format_json_quote_64bit_integers": {"1"},
		"wait_end_of_query": {"1"},
	}
	for key, values := range params {
		if !strings.HasPrefix(key, "param_") || len(values) != 1 {
			return nil, errors.New("invalid source parameter")
		}
		// HTTP parameters use ClickHouse escaped-text decoding in addition to
		// URL decoding. URL escaping alone changes literal backslash sequences.
		q.Set(key, parameterEscaper.Replace(values[0]))
	}
	u.RawQuery = q.Encode()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, u.String(), strings.NewReader(sql+" FORMAT JSONEachRow"))
	if err != nil {
		return nil, err
	}
	req.SetBasicAuth(s.username, s.password)
	response, err := s.client.Do(req)
	if err != nil {
		return nil, errors.New("source SELECT transport failed")
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, maxResponseBytes+1))
	if err != nil || len(body) > maxResponseBytes {
		return nil, errors.New("source SELECT response is incomplete or exceeds byte limit")
	}
	if response.StatusCode != http.StatusOK || response.Header.Get("X-ClickHouse-Exception-Code") != "" {
		// ClickHouse errors can include source values. Do not echo them to logs.
		return nil, fmt.Errorf("source SELECT failed (HTTP %d); inspect server logs", response.StatusCode)
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	var rows []map[string]any
	for {
		var row map[string]any
		if err := decoder.Decode(&row); err != nil {
			if errors.Is(err, io.EOF) {
				break
			}
			return nil, errors.New("source SELECT returned malformed or truncated JSON")
		}
		if row == nil || len(rows) >= 100000 {
			return nil, errors.New("invalid source row count")
		}
		rows = append(rows, row)
	}
	return rows, nil
}

// physicalKey matches the source's ReplacingMergeTree identity within an hour.
// A trace/span ID alone is not a physical identity.
type physicalKey struct{ Observation, Service, Trace, Span string }

func rowKey(row map[string]any) (physicalKey, error) {
	var key physicalKey
	fields := []struct {
		name string
		dest *string
	}{
		{"observation_type", &key.Observation}, {"service_name", &key.Service},
		{"trace_id", &key.Trace}, {"id", &key.Span},
	}
	for _, field := range fields {
		value, ok := row[field.name].(string)
		if !ok {
			return key, fmt.Errorf("source identity field %s is not a string", field.name)
		}
		*field.dest = value
	}
	if key.Trace == "" || key.Span == "" {
		return key, errors.New("source trace/span identity is empty")
	}
	return key, nil
}

func (k physicalKey) after(other physicalKey) bool {
	left := [4]string{k.Observation, k.Service, k.Trace, k.Span}
	right := [4]string{other.Observation, other.Service, other.Trace, other.Span}
	for i := range left {
		if left[i] != right[i] {
			return left[i] > right[i]
		}
	}
	return false
}

func hourParams(project string, hour time.Time) url.Values {
	return url.Values{
		"param_project": {project}, "param_start": {hour.UTC().Format("2006-01-02 15:04:05.000000")},
		"param_end": {hour.Add(time.Hour).UTC().Format("2006-01-02 15:04:05.000000")},
	}
}

const sourceWhere = ` project_id = {project:UUID}
 AND start_time >= {start:DateTime64(6, 'UTC')}
 AND start_time < {end:DateTime64(6, 'UTC')}`

func (s *sourceReader) identityPage(ctx context.Context, project string, hour time.Time, after physicalKey, size int) ([]physicalKey, error) {
	params := hourParams(project, hour)
	params.Set("param_observation", after.Observation)
	params.Set("param_service", after.Service)
	params.Set("param_trace", after.Trace)
	params.Set("param_span", after.Span)
	params.Set("param_limit", fmt.Sprint(size))
	rows, err := s.selectRows(ctx, `SELECT observation_type, service_name, trace_id, id FROM spans PREWHERE`+sourceWhere+`
 WHERE tuple(toString(observation_type), toString(service_name), trace_id, id)
 > tuple({observation:String}, {service:String}, {trace:String}, {span:String})
 GROUP BY observation_type, service_name, trace_id, id
 ORDER BY observation_type, service_name, trace_id, id LIMIT {limit:UInt32}`, params)
	if err != nil {
		return nil, err
	}
	if len(rows) > size {
		return nil, errors.New("identity page exceeded requested size")
	}
	keys := make([]physicalKey, 0, len(rows))
	previous := after
	for _, row := range rows {
		key, err := rowKey(row)
		if err != nil {
			return nil, err
		}
		if !key.after(previous) {
			return nil, errors.New("source identity page is not strictly ordered")
		}
		keys = append(keys, key)
		previous = key
	}
	return keys, nil
}

func (s *sourceReader) payloadPage(ctx context.Context, project string, hour time.Time, keys []physicalKey) ([]map[string]any, error) {
	if len(keys) == 0 {
		return nil, nil
	}
	params := hourParams(project, hour)
	conditions := make([]string, 0, len(keys))
	for i, key := range keys {
		prefix := fmt.Sprintf("k%d", i)
		conditions = append(conditions, fmt.Sprintf("tuple({%so:String}, {%ss:String}, {%st:String}, {%si:String})", prefix, prefix, prefix, prefix))
		for suffix, value := range map[string]string{"o": key.Observation, "s": key.Service, "t": key.Trace, "i": key.Span} {
			params.Set("param_"+prefix+suffix, value)
		}
	}
	rows, err := s.selectRows(ctx, `SELECT project_id, org_id, observation_type, service_name, trace_id, id,
 start_time, attrs_string, attrs_number, attrs_bool,
 attributes_extra, model, _version, is_deleted
 FROM spans PREWHERE`+sourceWhere+`
 AND tuple(toString(observation_type), toString(service_name), trace_id, id) IN (`+strings.Join(conditions, ",")+`)`, params)
	if err != nil {
		return nil, err
	}
	return latestRows(rows, keys)
}

// Resolve all versions before applying tombstones or the caller's time range.
// Equal-version conflicting payloads are an error, not an arbitrary argMax win.
func latestRows(rows []map[string]any, keys []physicalKey) ([]map[string]any, error) {
	type versioned struct {
		version  uint64
		row      map[string]any
		encoded  []byte
		conflict bool
	}
	selected := make(map[physicalKey]versioned, len(keys))
	want := make(map[physicalKey]bool, len(keys))
	for _, key := range keys {
		want[key] = true
	}
	for _, row := range rows {
		key, err := rowKey(row)
		if err != nil {
			return nil, err
		}
		if !want[key] {
			return nil, errors.New("payload SELECT returned an unrequested source identity")
		}
		var version uint64
		switch value := row["_version"].(type) {
		case string:
			version, err = strconv.ParseUint(value, 10, 64)
		case json.Number:
			version, err = strconv.ParseUint(value.String(), 10, 64)
		default:
			err = errors.New("missing source version")
		}
		if err != nil {
			return nil, errors.New("invalid source version")
		}
		encoded, err := json.Marshal(row)
		if err != nil {
			return nil, errors.New("invalid source payload")
		}
		old, exists := selected[key]
		if exists && old.version == version && !bytes.Equal(old.encoded, encoded) {
			old.conflict = true
			selected[key] = old
		}
		if !exists || version > old.version {
			selected[key] = versioned{version: version, row: row, encoded: encoded}
		}
	}
	out := make([]map[string]any, 0, len(keys))
	for _, key := range keys {
		value, ok := selected[key]
		if !ok {
			return nil, errors.New("source identity disappeared between reads; retry page")
		}
		if value.conflict {
			return nil, errors.New("conflicting source payloads at the latest version; backfill not advanced")
		}
		out = append(out, value.row)
	}
	return out, nil
}
