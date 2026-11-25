package blocklist

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

type blocklistPattern struct {
	original string
	regex    *regexp.Regexp
}

// BlocklistGuardrail checks messages against a configurable keyword/phrase blocklist.
type BlocklistGuardrail struct {
	patterns   []blocklistPattern
	wholeWord  bool
	maxMatches int
	configErr  error
}

// New creates a BlocklistGuardrail from rule config.
func New(cfg map[string]interface{}) *BlocklistGuardrail {
	g := &BlocklistGuardrail{
		maxMatches: 1,
		configErr:  validateConfig(cfg),
	}

	if cfg != nil {
		if v, ok := cfg["whole_word"].(bool); ok {
			g.wholeWord = v
		}
		if v, ok := cfg["max_matches"]; ok {
			switch n := v.(type) {
			case int:
				g.maxMatches = n
			case float64:
				g.maxMatches = int(n)
			}
		}
		if v, ok := cfg["words"]; ok {
			if list, ok := v.([]interface{}); ok {
				for _, item := range list {
					if s, ok := item.(string); ok && s != "" {
						p := compilePattern(s, g.wholeWord)
						if p.regex != nil {
							g.patterns = append(g.patterns, p)
						}
					}
				}
			}
		}
	}

	if g.maxMatches <= 0 {
		g.maxMatches = 1
	}

	return g
}

// ValidateConfig reports whether the original construction config is usable for
// a selected policy. Validation is opt-in: legacy org/static callers of New and
// Check retain their existing behavior. Capture the error at construction so a
// later caller mutation cannot validate a checker built from different data.
func (g *BlocklistGuardrail) ValidateConfig() error { return g.configErr }

func validateConfig(cfg map[string]interface{}) error {
	words, ok := cfg["words"].([]interface{})
	if !ok || len(words) == 0 {
		return errors.New("keyword-blocklist words must be a nonempty array of nonblank strings")
	}
	for _, item := range words {
		word, ok := item.(string)
		if !ok || strings.TrimSpace(word) == "" {
			return errors.New("keyword-blocklist words must be a nonempty array of nonblank strings")
		}
	}
	if value, present := cfg["whole_word"]; present {
		if _, ok := value.(bool); !ok {
			return errors.New("keyword-blocklist whole_word must be a boolean")
		}
	}
	if value, present := cfg["max_matches"]; present {
		valid := false
		switch n := value.(type) {
		case int:
			valid = n > 0
		case float64:
			// The exclusive power-of-two bound avoids rounding MaxInt up on
			// 64-bit platforms and matches the constructor's int conversion.
			valid = n > 0 && n < math.Ldexp(1, strconv.IntSize-1) && math.Trunc(n) == n
		}
		if !valid {
			return errors.New("keyword-blocklist max_matches must be a positive integer within the supported range")
		}
	}
	return nil
}

func (g *BlocklistGuardrail) Name() string            { return "keyword-blocklist" }
func (g *BlocklistGuardrail) Stage() guardrails.Stage { return guardrails.StagePre }

// Check scans messages for blocklisted words/phrases.
func (g *BlocklistGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if input == nil || len(g.patterns) == 0 {
		return &guardrails.CheckResult{Pass: true}
	}

	text := extractAllText(input)
	if text == "" {
		return &guardrails.CheckResult{Pass: true}
	}

	lower := strings.ToLower(text)

	var matched []string
	for _, p := range g.patterns {
		select {
		case <-ctx.Done():
			return buildResult(matched, g.maxMatches)
		default:
		}
		if p.regex.MatchString(lower) {
			matched = append(matched, p.original)
		}
	}

	return buildResult(matched, g.maxMatches)
}

func buildResult(matched []string, maxMatches int) *guardrails.CheckResult {
	if len(matched) == 0 {
		return &guardrails.CheckResult{Pass: true, Score: 0}
	}

	score := math.Min(1.0, float64(len(matched))/float64(maxMatches))

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   score,
		Message: fmt.Sprintf("Blocked keywords found: %s", strings.Join(matched, ", ")),
		Details: map[string]interface{}{
			"matched_words": matched,
			"match_count":   len(matched),
		},
	}
}

// compilePattern converts a blocklist word (with optional * wildcards) into a regex.
func compilePattern(word string, wholeWord bool) blocklistPattern {
	lower := strings.ToLower(word)

	// Split on wildcards, escape each part, rejoin with .*
	parts := strings.Split(lower, "*")
	for i, p := range parts {
		parts[i] = regexp.QuoteMeta(p)
	}
	pattern := strings.Join(parts, ".*")

	if wholeWord {
		pattern = `\b` + pattern + `\b`
	}

	re, err := regexp.Compile("(?i)" + pattern)
	if err != nil {
		return blocklistPattern{original: word}
	}

	return blocklistPattern{original: word, regex: re}
}

func extractAllText(input *guardrails.CheckInput) string {
	var parts []string
	if input.Request != nil {
		for _, m := range input.Request.Messages {
			if t, ok := extractContentText(m.Content); ok {
				parts = append(parts, t)
			}
		}
	}
	if input.Response != nil {
		for _, c := range input.Response.Choices {
			if t, ok := extractContentText(c.Message.Content); ok {
				parts = append(parts, t)
			}
		}
	}
	return strings.Join(parts, " ")
}

func extractContentText(raw json.RawMessage) (string, bool) {
	if len(raw) == 0 {
		return "", false
	}
	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		return s, true
	}
	var parts []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}
	if err := json.Unmarshal(raw, &parts); err == nil {
		var texts []string
		for _, p := range parts {
			if p.Type == "text" && p.Text != "" {
				texts = append(texts, p.Text)
			}
		}
		if len(texts) > 0 {
			return strings.Join(texts, " "), true
		}
	}
	return "", false
}
