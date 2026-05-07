package pii

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"math"
	"regexp"
	"sort"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Remediation defines how detected PII is handled.
type Remediation int

const (
	// RemediationBlock reports PII without mutating content.
	RemediationBlock Remediation = iota
	// RemediationMask replaces PII with ***.
	RemediationMask
	// RemediationRedact replaces PII with [REDACTED:<type>].
	RemediationRedact
	// RemediationHash replaces PII with [SHA:<hash8>].
	RemediationHash
)

// Detection describes a single PII entity found in text.
type Detection struct {
	EntityType string `json:"type"`
	Value      string `json:"value"`
	Start      int    `json:"start"`
	End        int    `json:"end"`
}

type entityDetector struct {
	entityType string
	pattern    *regexp.Regexp
}

// PIIGuardrail detects and optionally remediates PII in messages.
type PIIGuardrail struct {
	detectors    []entityDetector
	remediation  Remediation
	maxEntities  int
	enabledTypes map[string]bool
}

// New creates a PIIGuardrail from a rule config map.
func New(cfg map[string]interface{}) *PIIGuardrail {
	g := &PIIGuardrail{
		remediation: RemediationBlock,
		maxEntities: 1,
	}

	if cfg != nil {
		if v, ok := cfg["remediation"].(string); ok {
			g.remediation = parseRemediation(v)
		}
		if v, ok := cfg["max_entities"]; ok {
			switch n := v.(type) {
			case int:
				g.maxEntities = n
			case float64:
				g.maxEntities = int(n)
			}
		}
		if v, ok := cfg["entities"]; ok {
			if list, ok := v.([]interface{}); ok && len(list) > 0 {
				g.enabledTypes = make(map[string]bool, len(list))
				for _, item := range list {
					if s, ok := item.(string); ok {
						g.enabledTypes[s] = true
					}
				}
			}
		}
	}

	if g.maxEntities <= 0 {
		g.maxEntities = 1
	}

	g.detectors = buildDetectors(g.enabledTypes)
	return g
}

func (g *PIIGuardrail) Name() string            { return "pii-detection" }
func (g *PIIGuardrail) Stage() guardrails.Stage { return guardrails.StagePre }

// Check scans messages for PII and returns a result.
func (g *PIIGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if input == nil {
		return &guardrails.CheckResult{Pass: true}
	}

	// Collect pointers to messages. Remediation (mask/redact/hash) mutates
	// content in-place intentionally — the redacted content should flow to
	// the provider. We only take pointers to the request/response slices
	// which are owned by the current request context.
	var msgPtrs []*models.Message
	if input.Request != nil {
		for i := range input.Request.Messages {
			msgPtrs = append(msgPtrs, &input.Request.Messages[i])
		}
	}
	if input.Response != nil {
		for i := range input.Response.Choices {
			msgPtrs = append(msgPtrs, &input.Response.Choices[i].Message)
		}
	}

	if len(msgPtrs) == 0 {
		return &guardrails.CheckResult{Pass: true}
	}

	var allDetections []Detection

	for _, msg := range msgPtrs {
		text, ok := extractContentText(msg.Content)
		if !ok || text == "" {
			continue
		}

		select {
		case <-ctx.Done():
			return g.buildResult(allDetections)
		default:
		}

		detections := g.detect(text)
		if len(detections) == 0 {
			continue
		}

		allDetections = append(allDetections, detections...)

		// Apply remediation (mutate message content in-place).
		if g.remediation != RemediationBlock {
			remediated := applyRemediation(text, detections, g.remediation)
			msg.Content = marshalContentString(remediated)
		}
	}

	return g.buildResult(allDetections)
}

func (g *PIIGuardrail) detect(text string) []Detection {
	var detections []Detection
	for _, d := range g.detectors {
		matches := d.pattern.FindAllStringIndex(text, -1)
		for _, m := range matches {
			detections = append(detections, Detection{
				EntityType: d.entityType,
				Value:      text[m[0]:m[1]],
				Start:      m[0],
				End:        m[1],
			})
		}
	}
	return detections
}

func (g *PIIGuardrail) buildResult(detections []Detection) *guardrails.CheckResult {
	if len(detections) == 0 {
		return &guardrails.CheckResult{Pass: true, Score: 0}
	}

	score := math.Min(1.0, float64(len(detections))/float64(g.maxEntities))

	// Build details.
	detailList := make([]interface{}, len(detections))
	for i, d := range detections {
		displayed := d.Value
		if g.remediation != RemediationBlock {
			displayed = remediate(d.Value, d.EntityType, g.remediation)
		}
		detailList[i] = map[string]interface{}{
			"type":  d.EntityType,
			"value": displayed,
			"start": d.Start,
			"end":   d.End,
		}
	}

	types := uniqueTypes(detections)
	msg := fmt.Sprintf("Detected PII: %s (%d entities)", strings.Join(types, ", "), len(detections))

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   score,
		Message: msg,
		Details: map[string]interface{}{
			"entities_found": len(detections),
			"detections":     detailList,
		},
	}
}

// extractContentText extracts plain text from a Message's Content field.
// Content can be a JSON string or an array of content parts.
func extractContentText(raw json.RawMessage) (string, bool) {
	if len(raw) == 0 {
		return "", false
	}

	// Try string first.
	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		return s, true
	}

	// Try array of content parts.
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

func marshalContentString(s string) json.RawMessage {
	b, _ := json.Marshal(s)
	return b
}

// applyRemediation replaces all detections in text with remediated values.
// Processes matches in reverse order to preserve positions.
func applyRemediation(text string, detections []Detection, rem Remediation) string {
	// Sort by start position descending so replacements don't shift offsets.
	sorted := make([]Detection, len(detections))
	copy(sorted, detections)
	sort.Slice(sorted, func(i, j int) bool {
		return sorted[i].Start > sorted[j].Start
	})

	for _, d := range sorted {
		replacement := remediate(d.Value, d.EntityType, rem)
		text = text[:d.Start] + replacement + text[d.End:]
	}
	return text
}

func remediate(value, entityType string, rem Remediation) string {
	switch rem {
	case RemediationMask:
		return "***"
	case RemediationRedact:
		return fmt.Sprintf("[REDACTED:%s]", entityType)
	case RemediationHash:
		h := sha256.Sum256([]byte(value))
		return fmt.Sprintf("[SHA:%x]", h[:4])
	default:
		return value
	}
}

func uniqueTypes(detections []Detection) []string {
	seen := make(map[string]bool)
	var types []string
	for _, d := range detections {
		if !seen[d.EntityType] {
			seen[d.EntityType] = true
			types = append(types, d.EntityType)
		}
	}
	return types
}

func parseRemediation(s string) Remediation {
	switch strings.ToLower(s) {
	case "mask":
		return RemediationMask
	case "redact":
		return RemediationRedact
	case "hash":
		return RemediationHash
	default:
		return RemediationBlock
	}
}

// buildDetectors creates compiled regex detectors for enabled entity types.
func buildDetectors(enabled map[string]bool) []entityDetector {
	all := defaultPatterns()
	var detectors []entityDetector
	for entityType, pattern := range all {
		if enabled != nil && !enabled[entityType] {
			continue
		}
		detectors = append(detectors, entityDetector{
			entityType: entityType,
			pattern:    regexp.MustCompile(pattern),
		})
	}
	// Sort for deterministic order.
	sort.Slice(detectors, func(i, j int) bool {
		return detectors[i].entityType < detectors[j].entityType
	})
	return detectors
}

// defaultPatterns returns regex patterns for all supported PII entity types.
func defaultPatterns() map[string]string {
	return map[string]string{
		// Email: standard email pattern.
		"email": `[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}`,

		// Phone: US formats (+1-555-123-4567, (555) 123-4567, 555.123.4567, etc.)
		"phone": `(?:\+?1[\s\-.]?)?\(?[2-9]\d{2}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}`,

		// SSN: 123-45-6789 or 123 45 6789 (RE2 compatible — excludes 000/666/9xx).
		"ssn": `\b(?:00[1-9]|0[1-9]\d|[1-5]\d{2}|6[0-5]\d|66[0-5]|66[7-9]|6[7-9]\d|[7-8]\d{2})[\-\s]\d{2}[\-\s]\d{4}\b`,

		// Credit card: Visa (4xxx), MC (51-55xx), Amex (34xx/37xx), Discover (6011/65xx).
		// Handles both 4-4-4-4 grouping and Amex 4-6-5 grouping.
		"credit_card": `\b(?:4\d{3}|5[1-5]\d{2}|6(?:011|5\d{2}))[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b|\b3[47]\d{2}[\s\-]?\d{6}[\s\-]?\d{5}\b`,

		// IPv4 address.
		"ipv4": `\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b`,

		// IPv6 address (simplified: groups of hex separated by colons).
		"ipv6": `\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b`,

		// Date of birth patterns: MM/DD/YYYY, DD/MM/YYYY, YYYY-MM-DD.
		"dob": `\b(?:\d{1,2}[/\-]\d{1,2}[/\-]\d{4}|\d{4}[/\-]\d{1,2}[/\-]\d{1,2})\b`,

		// US Passport: context keyword followed by 9 digits.
		"passport": `(?i)passport[#:\s]+[A-Z]?\d{8,9}\b`,

		// US Driver's license: context keyword followed by state letter + digits.
		"drivers_license": `(?i)(?:driver'?s?\s*(?:license|lic)|DL)[#:\s]+[A-Z]\d{4,12}\b`,

		// IBAN: 2-letter country code + 2 check digits + up to 30 alphanum.
		"iban": `\b[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b`,

		// US ZIP code: 5+4 format only (5-digit alone is too common for false positives).
		"zip_code": `\b\d{5}\-\d{4}\b`,

		// AWS Access Key: starts with AKIA, 20 chars total.
		"aws_key": `\bAKIA[0-9A-Z]{16}\b`,

		// Generic API key patterns: sk-, key-, token prefixes.
		"api_key": `\b(?:sk|key|token)[\-_][a-zA-Z0-9]{20,}\b`,

		// URL with embedded credentials: https://user:pass@host.
		"url_credentials": `https?://[^\s:]+:[^\s@]+@[^\s/]+`,

		// MAC address: XX:XX:XX:XX:XX:XX or XX-XX-XX-XX-XX-XX.
		"mac_address": `\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b`,

		// US EIN (Tax ID): XX-XXXXXXX.
		"ein": `\b\d{2}\-\d{7}\b`,

		// Medical Record Number: MRN prefix + digits.
		"mrn": `\bMRN[\-\s]?\d{6,10}\b`,

		// Bitcoin address: starts with 1 or 3, 25-34 base58 chars.
		"bitcoin": `\b[13][a-km-zA-HJ-NP-Z1-9]{24,33}\b`,
	}
}
