package catalog

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/routerbilling"
)

func exactRouterCards(id string, raw map[string]any) []routerbilling.Candidate {
	candidate := func(lines map[string]any) routerbilling.Candidate {
		return routerbilling.Candidate{ID: id, Prompt: exactRate(lines, "prompt"), Completion: exactRate(lines, "completion"), CacheRead: exactRate(lines, "input_cache_read"), CacheWrite: exactRate(lines, "input_cache_write"), Reasoning: exactRate(lines, "internal_reasoning"), Request: exactRate(lines, "request"), Image: exactRate(lines, "image"), Search: exactRate(lines, "web_search")}
	}
	cards := []routerbilling.Candidate{candidate(raw)}
	tiers, _ := raw["overrides"].([]any)
	for _, tier := range tiers {
		lines, ok := tier.(map[string]any)
		if !ok {
			continue
		}
		merged := map[string]any{}
		for k, v := range raw {
			merged[k] = v
		}
		for k, v := range lines {
			merged[k] = v
		}
		cards = append(cards, candidate(merged))
	}
	return cards
}

func sourceRouterCards(id string, raw map[string]any, p Prices, tiers []PriceTier) []routerbilling.Candidate {
	fallback := []routerbilling.Candidate{routerCard(id, p)}
	for _, tier := range tiers {
		fallback = append(fallback, routerCard(id, tier.Prices))
	}
	if raw == nil {
		return fallback
	}
	exact := exactRouterCards(id, raw)
	if len(exact) != len(fallback) {
		return fallback
	}
	// A price override cannot keep borrowing the old parser's exact rates.
	normalize := func(c routerbilling.Candidate) routerbilling.Candidate {
		for _, s := range []*string{&c.Prompt, &c.Completion, &c.CacheRead, &c.CacheWrite, &c.Reasoning, &c.Request, &c.Image, &c.Search} {
			v, err := strconv.ParseFloat(*s, 64)
			if err != nil {
				*s = "invalid"
			} else {
				*s = strconv.FormatFloat(v, 'f', -1, 64)
			}
		}
		return c
	}
	for i := range exact {
		if normalize(exact[i]) != fallback[i] {
			return fallback
		}
	}
	return exact
}

// Preserve numeric JSON price decimals as well as quoted decimals. A trailing
// second document remains invalid, as it was with json.Unmarshal.
func decodeExactCatalogue(body []byte, target any) error {
	d := json.NewDecoder(bytes.NewReader(body))
	d.UseNumber()
	if err := d.Decode(target); err != nil {
		return err
	}
	var extra any
	if err := d.Decode(&extra); err != io.EOF {
		return fmt.Errorf("trailing catalogue data")
	}
	return nil
}
