package ingestbench

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

type clickHouse struct {
	cfg     Config
	client  *http.Client
	headers http.Header
}

func (ch clickHouse) query(ctx context.Context, query string, params map[string]string) ([]byte, error) {
	u, _ := url.Parse(ch.cfg.ClickHouseURL)
	q := u.Query()
	q.Set("readonly", "2")
	q.Set("max_execution_time", "30")
	q.Set("max_memory_usage", "1073741824")
	q.Set("output_format_json_quote_64bit_integers", "0")
	for k, v := range params {
		q.Set("param_"+k, v)
	}
	u.RawQuery = q.Encode()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, u.String(), bytes.NewBufferString(query+" FORMAT JSONEachRow"))
	if err != nil {
		return nil, fmt.Errorf("create verification request")
	}
	req.Header = ch.headers.Clone()
	resp, err := ch.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("ClickHouse request failed (URL/credentials omitted)")
	}
	defer resp.Body.Close()
	body, err := readBounded(resp.Body, 1<<20)
	if err != nil || resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("ClickHouse response failed: HTTP %d or unreadable/oversized body", resp.StatusCode)
	}
	return body, nil
}

func readBounded(r io.Reader, max int64) ([]byte, error) {
	b, err := io.ReadAll(io.LimitReader(r, max+1))
	if err != nil {
		return nil, fmt.Errorf("read response: %w", err)
	}
	if int64(len(b)) > max {
		return nil, fmt.Errorf("oversized response")
	}
	return b, nil
}

func decodeOne(body []byte, out any) error {
	d := json.NewDecoder(bytes.NewReader(body))
	if err := d.Decode(out); err != nil {
		return fmt.Errorf("invalid JSON response (body omitted)")
	}
	var extra any
	if err := d.Decode(&extra); err != io.EOF {
		return fmt.Errorf("unexpected trailing response")
	}
	return nil
}

func (ch clickHouse) scope() string {
	return " FROM `" + ch.cfg.Database + "`.`" + ch.cfg.Table + "` WHERE project_id = {project:UUID} AND attrs_string['benchmark.run_id'] = {run:String}"
}

func (ch clickHouse) params() map[string]string {
	return map[string]string{"project": ch.cfg.ProjectID, "run": ch.cfg.RunID, "spt": strconv.FormatInt(ch.cfg.SpansPerTrace, 10)}
}

func (ch clickHouse) count(ctx context.Context) (uint64, error) {
	body, err := ch.query(ctx, "SELECT count() AS rows"+ch.scope(), ch.params())
	if err != nil {
		return 0, err
	}
	var row struct {
		Rows *uint64 `json:"rows"`
	}
	if err := decodeOne(body, &row); err != nil || row.Rows == nil {
		return 0, fmt.Errorf("invalid ClickHouse count response")
	}
	return *row.Rows, nil
}

const sequenceExpr = "toUInt64OrDefault(attrs_string['benchmark.seq'], toUInt64(18446744073709551615))"
const identityExpr = "(id = leftPad(lower(hex(seq + toUInt64(1))), 16, '0') AND replaceAll(trace_id, '-', '') = concat({run:String}, leftPad(lower(hex(intDiv(seq, {spt:UInt64}) + toUInt64(1))), 16, '0')))"

// Verification keeps exact distinct state bounded by chunk size. It validates
// BOTH deterministic IDs, not just a row count or an untrusted sequence tag.
func (ch clickHouse) verify(ctx context.Context, attempted int64) Verification {
	start := time.Now()
	v := Verification{Missing: uint64(attempted)}
	fail := func(err error) Verification {
		v.Error = err.Error()
		v.ElapsedSeconds = time.Since(start).Seconds()
		return v
	}
	params := ch.params()
	params["expected"] = strconv.FormatInt(attempted, 10)
	valid := identityExpr + " AND seq < {expected:UInt64}"
	totalsSQL := "WITH " + sequenceExpr + " AS seq SELECT count() AS rows, countIf(NOT (" + valid + ")) AS unexpected" + ch.scope()
	body, err := ch.query(ctx, totalsSQL, params)
	if err != nil {
		return fail(err)
	}
	var totals struct {
		Rows       *uint64 `json:"rows"`
		Unexpected *uint64 `json:"unexpected"`
	}
	if err = decodeOne(body, &totals); err != nil || totals.Rows == nil || totals.Unexpected == nil || *totals.Unexpected > *totals.Rows {
		return fail(fmt.Errorf("invalid verification totals"))
	}
	v.Rows, v.UnexpectedRows = *totals.Rows, *totals.Unexpected
	for lo := int64(0); lo < attempted; lo += ch.cfg.VerifyChunk {
		hi := min(lo+ch.cfg.VerifyChunk, attempted)
		params["lo"], params["hi"] = strconv.FormatInt(lo, 10), strconv.FormatInt(hi, 10)
		params["trace_lo"], params["trace_hi"] = ch.cfg.traceString(lo), ch.cfg.traceString(hi-1)
		condition := valid + " AND seq >= {lo:UInt64} AND seq < {hi:UInt64}"
		query := "WITH " + sequenceExpr + " AS seq SELECT countIf(" + condition + ") AS valid_rows, uniqExactIf(seq, " + condition + ") AS distinct_spans" + ch.scope() + " AND trace_id >= {trace_lo:String} AND trace_id <= {trace_hi:String}"
		body, err = ch.query(ctx, query, params)
		if err != nil {
			return fail(err)
		}
		var row struct {
			Rows     *uint64 `json:"valid_rows"`
			Distinct *uint64 `json:"distinct_spans"`
		}
		if err = decodeOne(body, &row); err != nil || row.Rows == nil || row.Distinct == nil || *row.Distinct > uint64(hi-lo) || *row.Rows < *row.Distinct {
			return fail(fmt.Errorf("invalid verification chunk"))
		}
		v.DistinctExpected += *row.Distinct
		v.DuplicateRows += *row.Rows - *row.Distinct
		v.Chunks++
	}
	// An insert, merge, or replica lag during the scan must not certify zero loss.
	if v.DistinctExpected+v.DuplicateRows+v.UnexpectedRows != v.Rows {
		return fail(fmt.Errorf("row totals changed across chunk scan; rerun against a quiescent consistent read target"))
	}
	v.Missing = uint64(attempted) - v.DistinctExpected
	if attempted > 0 {
		v.MissingFraction = float64(v.Missing) / float64(attempted)
	}
	v.Complete = true
	v.ElapsedSeconds = time.Since(start).Seconds()
	return v
}

type lagSample struct {
	Seq  int64
	Sent time.Time
	Seen time.Time
}

func (ch clickHouse) observe(ctx context.Context, samples []lagSample) (map[int64]time.Time, error) {
	seen := map[int64]time.Time{}
	for start := 0; start < len(samples); start += 256 {
		chunk := samples[start:min(start+256, len(samples))]
		params := ch.params()
		ids := make([]string, 0, len(chunk))
		traces := make([]string, 0, len(chunk))
		byID := map[string]int64{}
		for _, sample := range chunk {
			id := spanID(sample.Seq)
			ids = append(ids, id)
			traces = append(traces, ch.cfg.traceString(sample.Seq))
			byID[id] = sample.Seq
		}
		// IDs are generated hexadecimal/UUID values, not user strings.
		params["ids"] = "['" + strings.Join(ids, "','") + "']"
		params["traces"] = "['" + strings.Join(traces, "','") + "']"
		body, err := ch.query(ctx, "SELECT DISTINCT id"+ch.scope()+" AND id IN {ids:Array(String)} AND trace_id IN {traces:Array(String)}", params)
		if err != nil {
			return seen, err
		}
		observed := time.Now()
		d := json.NewDecoder(bytes.NewReader(body))
		for {
			var row struct {
				ID string `json:"id"`
			}
			err := d.Decode(&row)
			if err == io.EOF {
				break
			}
			if err != nil {
				return seen, fmt.Errorf("invalid visibility response")
			}
			if seq, ok := byID[row.ID]; ok {
				seen[seq] = observed
			}
		}
	}
	return seen, nil
}
