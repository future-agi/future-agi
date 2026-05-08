package topic

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

// TopicGuardrail restricts conversations to allowed topics or blocks forbidden topics.
type TopicGuardrail struct {
	allowedTopics    []topicMatcher
	forbiddenTopics  []topicMatcher
}

type topicMatcher struct {
	name     string
	patterns []*regexp.Regexp
}

// New creates a TopicGuardrail from rule config.
func New(cfg map[string]interface{}) *TopicGuardrail {
	g := &TopicGuardrail{}
	if cfg == nil {
		return g
	}

	if v, ok := cfg["allowed_topics"]; ok {
		g.allowedTopics = parseTopics(v)
	}
	if v, ok := cfg["forbidden_topics"]; ok {
		g.forbiddenTopics = parseTopics(v)
	}

	return g
}

func (g *TopicGuardrail) Name() string           { return "topic-restriction" }
func (g *TopicGuardrail) Stage() guardrails.Stage { return guardrails.StagePre }

// Check enforces topic restrictions.
func (g *TopicGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if input == nil || input.Request == nil {
		return &guardrails.CheckResult{Pass: true}
	}

	text := extractAllText(input)
	if text == "" {
		return &guardrails.CheckResult{Pass: true}
	}

	lower := strings.ToLower(text)

	// Check forbidden topics first.
	for _, topic := range g.forbiddenTopics {
		for _, p := range topic.patterns {
			if p.MatchString(lower) {
				return &guardrails.CheckResult{
					Pass:    false,
					Score:   1.0,
					Message: fmt.Sprintf("Forbidden topic detected: %s", topic.name),
					Details: map[string]interface{}{
						"topic":  topic.name,
						"reason": "forbidden",
					},
				}
			}
		}
	}

	// Check allowed topics (if configured, at least one must match).
	if len(g.allowedTopics) > 0 {
		for _, topic := range g.allowedTopics {
			for _, p := range topic.patterns {
				if p.MatchString(lower) {
					return &guardrails.CheckResult{Pass: true, Score: 0}
				}
			}
		}
		return &guardrails.CheckResult{
			Pass:    false,
			Score:   1.0,
			Message: "Message does not match any allowed topic",
			Details: map[string]interface{}{
				"reason": "off_topic",
			},
		}
	}

	return &guardrails.CheckResult{Pass: true}
}

func parseTopics(v interface{}) []topicMatcher {
	list, ok := v.([]interface{})
	if !ok {
		return nil
	}
	var topics []topicMatcher
	for _, item := range list {
		m, ok := item.(map[string]interface{})
		if !ok {
			continue
		}
		name, _ := m["name"].(string)
		var patterns []*regexp.Regexp
		if kw, ok := m["keywords"].([]interface{}); ok {
			for _, k := range kw {
				if s, ok := k.(string); ok {
					re, err := regexp.Compile("(?i)" + regexp.QuoteMeta(s))
					if err == nil {
						patterns = append(patterns, re)
					}
				}
			}
		}
		if len(patterns) > 0 {
			topics = append(topics, topicMatcher{name: name, patterns: patterns})
		}
	}
	return topics
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
	return "", false
}
