package indirect

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"regexp"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

// IndirectInjectionGuardrail detects prompt injection payloads hidden inside
// tool results, retrieved documents, and other untrusted data that flows
// through the assistant context — as opposed to direct injection in user
// messages, which the existing "prompt-injection" guardrail already covers.
//
// Runs as a pre-stage guardrail so it can inspect tool-result messages in the
// conversation history before they are forwarded to the LLM.
type IndirectInjectionGuardrail struct {
	detectors []indirectDetector
	threshold int // weighted matches for score 1.0
	scanRoles map[string]bool
}

type indirectDetector struct {
	category string
	weight   float64
	patterns []*regexp.Regexp
}

// New creates an IndirectInjectionGuardrail from config.
//
// Supported config keys:
//   - sensitivity: "high" | "medium" (default) | "low"
//   - categories:  []string subset of detector categories to enable
//   - scan_roles:  []string roles to scan (default: ["tool","system","assistant"])
func New(cfg map[string]interface{}) *IndirectInjectionGuardrail {
	g := &IndirectInjectionGuardrail{
		threshold: 3,
		scanRoles: map[string]bool{
			"tool":      true,
			"system":    true,
			"assistant": true,
		},
	}

	if cfg != nil {
		if v, ok := cfg["sensitivity"].(string); ok {
			switch strings.ToLower(v) {
			case "high":
				g.threshold = 1
			case "low":
				g.threshold = 5
			default:
				g.threshold = 3
			}
		}

		if roles, ok := cfg["scan_roles"]; ok {
			if list, ok := roles.([]interface{}); ok && len(list) > 0 {
				g.scanRoles = make(map[string]bool, len(list))
				for _, item := range list {
					if s, ok := item.(string); ok {
						g.scanRoles[s] = true
					}
				}
			}
		}
	}

	var enabledSet map[string]bool
	if cfg != nil {
		if v, ok := cfg["categories"]; ok {
			if list, ok := v.([]interface{}); ok && len(list) > 0 {
				enabledSet = make(map[string]bool, len(list))
				for _, item := range list {
					if s, ok := item.(string); ok {
						enabledSet[s] = true
					}
				}
			}
		}
	}

	for _, d := range defaultDetectors() {
		if enabledSet != nil && !enabledSet[d.category] {
			continue
		}
		g.detectors = append(g.detectors, d)
	}

	return g
}

func (g *IndirectInjectionGuardrail) Name() string           { return "indirect-prompt-injection" }
func (g *IndirectInjectionGuardrail) Stage() guardrails.Stage { return guardrails.StagePre }

// Check scans non-user messages (tool results, system messages, prior
// assistant turns) for instruction-bearing payloads.
func (g *IndirectInjectionGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if input == nil || input.Request == nil {
		return &guardrails.CheckResult{Pass: true}
	}

	var texts []string
	var sources []string
	for _, m := range input.Request.Messages {
		if m.Role == "user" {
			continue
		}
		if !g.scanRoles[m.Role] {
			continue
		}
		if t, ok := extractContentText(m.Content); ok && t != "" {
			texts = append(texts, t)
			sources = append(sources, m.Role)
		}
		// Also scan tool call arguments in assistant messages — these can
		// carry forwarded injection payloads.
		for _, tc := range m.ToolCalls {
			if tc.Function.Arguments != "" {
				texts = append(texts, tc.Function.Arguments)
				sources = append(sources, m.Role+"/tool_call:"+tc.Function.Name)
			}
		}
	}

	if len(texts) == 0 {
		return &guardrails.CheckResult{Pass: true}
	}

	type categoryMatch struct {
		category string
		count    int
		weight   float64
		sources  []string
	}

	var allMatches []categoryMatch
	totalWeighted := 0.0

	for _, d := range g.detectors {
		count := 0
		var matchSources []string

		for i, text := range texts {
			lower := strings.ToLower(text)
			for _, p := range d.patterns {
				if p.MatchString(lower) {
					count++
					matchSources = appendUnique(matchSources, sources[i])
				}
			}
		}

		if count > 0 {
			allMatches = append(allMatches, categoryMatch{
				category: d.category,
				count:    count,
				weight:   d.weight,
				sources:  matchSources,
			})
			totalWeighted += float64(count) * d.weight
		}
	}

	if len(allMatches) == 0 {
		return &guardrails.CheckResult{Pass: true, Score: 0}
	}

	score := math.Min(1.0, totalWeighted/float64(g.threshold))

	catDetails := make(map[string]interface{})
	var triggered []string
	allSources := make(map[string]bool)
	for _, m := range allMatches {
		catDetails[m.category] = map[string]interface{}{
			"matches": m.count,
			"weight":  m.weight,
			"sources": m.sources,
		}
		triggered = append(triggered, m.category)
		for _, s := range m.sources {
			allSources[s] = true
		}
	}

	sourceList := make([]string, 0, len(allSources))
	for s := range allSources {
		sourceList = append(sourceList, s)
	}

	return &guardrails.CheckResult{
		Pass:  false,
		Score: score,
		Message: fmt.Sprintf(
			"Indirect prompt injection detected in %s: %s (score: %.2f)",
			strings.Join(sourceList, ", "),
			strings.Join(triggered, ", "),
			score,
		),
		Details: map[string]interface{}{
			"categories":    catDetails,
			"total_matches": totalWeighted,
			"sources":       sourceList,
		},
	}
}

// defaultDetectors returns detectors tuned for indirect injection — payloads
// embedded in tool outputs, retrieved documents, and context data.
func defaultDetectors() []indirectDetector {
	return []indirectDetector{
		{
			category: "instruction_override",
			weight:   1.5,
			patterns: compileAll([]string{
				`(?i)ignore\s+(all\s+)?(?:previous|prior|above)\s+instructions`,
				`(?i)forget\s+(all\s+)?(your\s+)?(?:rules|instructions|guidelines)`,
				`(?i)disregard\s+(all\s+)?(?:previous|prior|above)`,
				`(?i)override\s+(your\s+)?instructions`,
				`(?i)new\s+instructions?\s*:`,
				`(?i)you\s+must\s+now\s+follow`,
				`(?i)from\s+now\s+on\s+you\s+(will|must|should)`,
				`(?i)stop\s+being\s+(?:a\s+)?(?:helpful|assistant)`,
			}),
		},
		{
			category: "role_hijack",
			weight:   1.5,
			patterns: compileAll([]string{
				`(?i)you\s+are\s+(?:now\s+)?(?:dan|evil|unrestricted|unfiltered|jailbroken)`,
				`(?i)pretend\s+(?:you\s+are|to\s+be|you're)`,
				`(?i)act\s+as\s+(?:if\s+)?(?:you\s+(?:are|have|were)|a\s+different)`,
				`(?i)jailbreak\s+mode`,
				`(?i)developer\s+mode\s+(?:enabled|activated|on)`,
				`(?i)do\s+anything\s+now`,
				`(?i)bypass\s+(?:your\s+)?(?:filters|safety|restrictions|rules|guardrails)`,
				`(?i)entering\s+(?:unrestricted|admin|sudo|root)\s+mode`,
			}),
		},
		{
			category: "delimiter_injection",
			weight:   1.3,
			patterns: compileAll([]string{
				`(?i)<\/?system>`,
				`(?i)\[system\]`,
				`(?i)---\s*(?:system|instructions?|rules?)`,
				`(?i)###\s*(?:system|instructions?|new\s+context)`,
				`(?i)\[INST\]`,
				`(?i)<\|(?:im_start|im_end|system|user|endoftext)\|>`,
				`(?i)<\|begin_of_text\|>`,
				`(?i)<<\s*SYS\s*>>`,
			}),
		},
		{
			category: "exfiltration",
			weight:   1.8,
			patterns: compileAll([]string{
				`(?i)send\s+(?:all\s+)?(?:the\s+)?(?:data|info|information|contents?|conversation|history|context)\s+to`,
			`(?i)send\s+.{0,40}(?:data|info|conversation|history|context)\s+to`,
				`(?i)forward\s+(?:this|all|the)\s+(?:to|data)`,
				`(?i)(?:fetch|load|call|visit|navigate\s+to|open)\s+(?:this\s+)?(?:url|link|endpoint|webhook)\s*:?\s*https?://`,
				`(?i)include\s+(?:this\s+)?(?:url|link|image)\s*:?\s*https?://`,
				`(?i)!\[.*?\]\(https?://[^\s)]*\)`,
				`(?i)<img[^>]+src\s*=\s*["']https?://`,
				`(?i)(?:append|add|insert)\s+(?:the\s+)?(?:api\s+key|token|password|secret|credentials?)`,
			}),
		},
		{
			category: "tool_invocation",
			weight:   1.5,
			patterns: compileAll([]string{
				`(?i)(?:call|invoke|use|run|execute)\s+(?:the\s+)?(?:tool|function|api)\s+`,
				`(?i)you\s+(?:must|should|need\s+to)\s+(?:call|invoke|use|run)\s+`,
				`(?i)please\s+(?:call|invoke|use|run|execute)\s+`,
				`(?i)tool_call\s*:\s*`,
				`(?i)function_call\s*:\s*`,
				`(?i)<tool_use>`,
				`(?i)<function_call>`,
			}),
		},
		{
			category: "system_prompt_extraction",
			weight:   1.2,
			patterns: compileAll([]string{
				`(?i)(?:repeat|show|display|print|output|reveal|leak)\s+(?:your\s+)?system\s+prompt`,
				`(?i)what\s+(?:are|is)\s+your\s+(?:system\s+)?instructions`,
				`(?i)(?:output|print)\s+(?:the\s+)?(?:above|initial)\s+(?:text|prompt|instructions)`,
				`(?i)repeat\s+(?:the\s+)?(?:words\s+)?above`,
				`(?i)begin\s+your\s+response\s+with\s+(?:the\s+)?system`,
			}),
		},
		{
			category: "encoding_smuggling",
			weight:   1.0,
			patterns: compileAll([]string{
				`(?i)base64\s+decode`,
				`(?i)decode\s+(?:this|the\s+following)\s+base64`,
				`(?i)(?:rot13|caesar\s+cipher)\s+(?:decode|decrypt)`,
				`(?i)translate\s+from\s+(?:hex|binary|octal|base64)`,
				`(?i)unicode\s+(?:escape|decode|unescape)`,
				`(?i)\\u[0-9a-fA-F]{4}.*(?:ignore|override|system)`,
			}),
		},
		{
			category: "context_manipulation",
			weight:   1.3,
			patterns: compileAll([]string{
				`(?i)the\s+user\s+(?:has\s+)?(?:authorized|approved|confirmed|consented|agreed)`,
				`(?i)(?:admin|administrator|developer|operator)\s+(?:has\s+)?(?:authorized|approved|overridden)`,
				`(?i)this\s+(?:is\s+)?(?:a\s+)?(?:test|debug|maintenance)\s+mode`,
				`(?i)(?:safety|content)\s+(?:policy|filter)\s+(?:has\s+been|is)\s+(?:disabled|suspended|overridden)`,
				`(?i)previous\s+(?:instructions?|rules?|guidelines?)\s+(?:have\s+been|are|were)\s+(?:updated|changed|replaced|revoked)`,
				`(?i)(?:new|updated)\s+(?:system\s+)?(?:policy|rules?|guidelines?)\s*:`,
			}),
		},
		{
			category: "structured_payload",
			weight:   1.5,
			patterns: compileAll([]string{
				`(?i)\{"role"\s*:\s*"system"`,
				`(?i)\{"role"\s*:\s*"developer"`,
				`(?i)"\s*role\s*"\s*:\s*"\s*system\s*"`,
				`(?i)"\s*role\s*"\s*:\s*"\s*developer\s*"`,
			}),
		},
	}
}

func compileAll(patterns []string) []*regexp.Regexp {
	compiled := make([]*regexp.Regexp, len(patterns))
	for i, p := range patterns {
		compiled[i] = regexp.MustCompile(p)
	}
	return compiled
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

func appendUnique(slice []string, item string) []string {
	for _, s := range slice {
		if s == item {
			return slice
		}
	}
	return append(slice, item)
}
