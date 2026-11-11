package pricing

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"sort"
	"time"
)

// price_schedule.json is a byte copy of
// agentcc-gateway/internal/modeldb/price_schedule.json (copies_test.go keeps
// them equal): the dated token prices the gateway bills. The collector prices
// a span at the row in force when the span ended, so a trace costs the same
// here as the request did at the gateway.
//
//go:embed price_schedule.json
var embeddedSchedule []byte

// datedRow is one row of the schedule, in litellm.json's field names. Pointers
// tell a missing price from a zero one. Only input and output are billed here.
type datedRow struct {
	From                      string   `json:"from"`
	InputCostPerToken         *float64 `json:"input_cost_per_token"`
	OutputCostPerToken        *float64 `json:"output_cost_per_token"`
	CacheReadInputTokenCost   *float64 `json:"cache_read_input_token_cost"`
	InputCostPerTokenBatches  *float64 `json:"input_cost_per_token_batches"`
	OutputCostPerTokenBatches *float64 `json:"output_cost_per_token_batches"`

	start time.Time // From as an instant; set by parseSchedule
}

// schedule is the parsed embedded file, keyed by exact table key. It is set
// once at start and never changed. It is held apart from every Table, so a
// table rebuilt from fresh entries (or an operator's own file) cannot drop it.
var schedule = mustParseSchedule(embeddedSchedule)

// mustParseSchedule panics on a bad file. The file is compiled in, so that is
// a build fault, not an operator's: the same as the gateway's log.Fatalf.
func mustParseSchedule(data []byte) map[string][]datedRow {
	parsed, err := parseSchedule(data)
	if err != nil {
		panic("pricing: " + err.Error())
	}
	return parsed
}

// parseSchedule reads and checks the dated price file, with the gateway's
// checks (parsePriceSchedule in agentcc-gateway/internal/modeldb). Any fault is
// an error: billing the undated price quietly is what the file exists to stop.
func parseSchedule(data []byte) (map[string][]datedRow, error) {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	var parsed map[string][]datedRow
	if err := dec.Decode(&parsed); err != nil {
		return nil, fmt.Errorf("price schedule must be a JSON object of model keys: %w", err)
	}
	if err := dec.Decode(&json.RawMessage{}); !errors.Is(err, io.EOF) {
		return nil, errors.New("price schedule has data after its JSON object")
	}
	if len(parsed) == 0 {
		return nil, errors.New("price schedule lists no models")
	}

	// Sorted, so the same bad file always names the same fault.
	keys := make([]string, 0, len(parsed))
	for key := range parsed {
		keys = append(keys, key)
	}
	sort.Strings(keys)

	for _, key := range keys {
		if key == "" {
			return nil, errors.New("price schedule has an empty model key")
		}
		rows := parsed[key]
		if len(rows) == 0 {
			return nil, fmt.Errorf("price schedule entry %q has no rows", key)
		}
		for i := range rows {
			row := &rows[i]
			start, err := time.Parse(time.DateOnly, row.From)
			if err != nil {
				return nil, fmt.Errorf("price schedule entry %q row %d: from %q is not a YYYY-MM-DD date", key, i, row.From)
			}
			if i > 0 && !start.After(rows[i-1].start) {
				return nil, fmt.Errorf("price schedule entry %q rows must start on strictly later dates", key)
			}
			for _, field := range []struct {
				name  string
				price *float64
			}{
				{"input_cost_per_token", row.InputCostPerToken},
				{"output_cost_per_token", row.OutputCostPerToken},
				{"cache_read_input_token_cost", row.CacheReadInputTokenCost},
				{"input_cost_per_token_batches", row.InputCostPerTokenBatches},
				{"output_cost_per_token_batches", row.OutputCostPerTokenBatches},
			} {
				if field.price == nil {
					return nil, fmt.Errorf("price schedule entry %q row %d is missing %s", key, i, field.name)
				}
				if *field.price <= 0 {
					return nil, fmt.Errorf("price schedule entry %q row %d: %s must be a positive price", key, i, field.name)
				}
			}
			row.start = start
		}
	}
	return parsed, nil
}

// datedRates returns the input and output rates of the latest row for key that
// starts at or before at. ok=false when the key has no rows or at is before the
// first.
func datedRates(key string, at time.Time) (in, out float64, ok bool) {
	var row *datedRow
	rows := schedule[key]
	for i := range rows {
		if rows[i].start.After(at) {
			break
		}
		row = &rows[i]
	}
	if row == nil {
		return 0, 0, false
	}
	return *row.InputCostPerToken, *row.OutputCostPerToken, true
}
