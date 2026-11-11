package modeldb

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"sort"
	"time"
)

// scheduleRow is one row of price_schedule.json, in litellm.json's field
// names. Pointers tell a missing price from a zero one.
type scheduleRow struct {
	From                      string   `json:"from"`
	InputCostPerToken         *float64 `json:"input_cost_per_token"`
	OutputCostPerToken        *float64 `json:"output_cost_per_token"`
	CacheReadInputTokenCost   *float64 `json:"cache_read_input_token_cost"`
	InputCostPerTokenBatches  *float64 `json:"input_cost_per_token_batches"`
	OutputCostPerTokenBatches *float64 `json:"output_cost_per_token_batches"`

	start time.Time // From as an instant; set by parsePriceSchedule
}

// datedPricing is a model's full pricing from `from` until the next row.
type datedPricing struct {
	from    time.Time
	pricing PricingInfo
}

// parsePriceSchedule reads and checks the dated price file. Any fault is an
// error: billing the undated price quietly is what the file exists to stop.
func parsePriceSchedule(data []byte) (map[string][]scheduleRow, error) {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	var schedule map[string][]scheduleRow
	if err := dec.Decode(&schedule); err != nil {
		return nil, fmt.Errorf("price schedule must be a JSON object of model keys: %w", err)
	}
	if err := dec.Decode(&json.RawMessage{}); !errors.Is(err, io.EOF) {
		return nil, errors.New("price schedule has data after its JSON object")
	}
	if len(schedule) == 0 {
		return nil, errors.New("price schedule lists no models")
	}

	// Sorted, so the same bad file always names the same fault.
	keys := make([]string, 0, len(schedule))
	for key := range schedule {
		keys = append(keys, key)
	}
	sort.Strings(keys)

	for _, key := range keys {
		if key == "" {
			return nil, errors.New("price schedule has an empty model key")
		}
		rows := schedule[key]
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
	return schedule, nil
}

// attachPriceSchedule gives each dated key's entry its rows. A key the table
// lacks is an error: its dated price would never be billed.
func attachPriceSchedule(models map[string]*ModelInfo, schedule map[string][]scheduleRow) error {
	keys := make([]string, 0, len(schedule))
	for key := range schedule {
		keys = append(keys, key)
	}
	sort.Strings(keys)

	for _, key := range keys {
		m, ok := models[key]
		if !ok {
			return fmt.Errorf("price schedule names %q, which the bundled price table does not list", key)
		}
		rows := schedule[key]
		dated := make([]datedPricing, 0, len(rows))
		for _, row := range rows {
			p := m.Pricing
			p.InputPerToken = *row.InputCostPerToken
			p.OutputPerToken = *row.OutputCostPerToken
			p.CachedInputPerToken = *row.CacheReadInputTokenCost
			p.BatchInputPerToken = *row.InputCostPerTokenBatches
			p.BatchOutputPerToken = *row.OutputCostPerTokenBatches
			dated = append(dated, datedPricing{from: row.start, pricing: p})
		}
		m.dated = dated
	}
	return nil
}

// PricingAt returns the pricing in force at the instant at: the latest dated
// row that starts at or before it, else the table's own pricing.
func (m *ModelInfo) PricingAt(at time.Time) PricingInfo {
	p := m.Pricing
	for _, d := range m.dated {
		if d.from.After(at) {
			break
		}
		p = d.pricing
	}
	return p
}
