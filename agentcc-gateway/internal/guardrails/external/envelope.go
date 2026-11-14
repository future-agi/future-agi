package external

import (
	"encoding/json"
	"fmt"
	"strings"
)

// validateEnvelope checks the provider's decision-bearing fields before the
// adapter maps a valid verdict. Optional diagnostic fields remain optional.
func validateEnvelope(adapter providerAdapter, body []byte) error {
	var raw interface{}
	if err := json.Unmarshal(body, &raw); err != nil {
		return fmt.Errorf("invalid provider JSON: %w", err)
	}
	if _, ok := adapter.(*presidioAdapter); ok {
		entities, ok := raw.([]interface{})
		if !ok {
			return fmt.Errorf("presidio requires an entity array")
		}
		for _, value := range entities {
			ent, ok := value.(map[string]interface{})
			if !ok {
				return fmt.Errorf("invalid presidio entity")
			}
			name, ok := ent["entity_type"].(string)
			if !ok || name == "" || !validScore(ent["score"]) {
				return fmt.Errorf("invalid presidio entity verdict")
			}
		}
		return nil
	}
	obj, ok := raw.(map[string]interface{})
	if !ok || obj == nil {
		return fmt.Errorf("provider requires an object verdict")
	}
	requireBool := func(key string) error {
		if _, ok := obj[key].(bool); !ok {
			return fmt.Errorf("provider requires boolean %s", key)
		}
		return nil
	}
	switch a := adapter.(type) {
	case *lakeraAdapter:
		if err := requireBool("flagged"); err != nil {
			return err
		}
		if scores, exists := obj["category_scores"]; exists {
			m, ok := scores.(map[string]interface{})
			if !ok {
				return fmt.Errorf("invalid category scores")
			}
			for _, score := range m {
				if !validScore(score) {
					return fmt.Errorf("invalid category score")
				}
			}
		}
	case *hiddenlayerAdapter:
		if err := requireBool("flagged"); err != nil {
			return err
		}
		if score, exists := obj["risk_score"]; exists && !validScore(score) {
			return fmt.Errorf("invalid risk score")
		}
	case *lassoAdapter:
		return requireBool("violations_detected")
	case *zscalerAdapter:
		return requireBool("matched")
	case *grayswanAdapter:
		if !validScore(obj["violation"]) {
			return fmt.Errorf("invalid violation score")
		}
	case *crowdstrikeAdapter:
		result, ok := obj["result"].(map[string]interface{})
		if !ok {
			return fmt.Errorf("missing crowdstrike result")
		}
		if _, ok := result["blocked"].(bool); !ok {
			return fmt.Errorf("missing crowdstrike blocked verdict")
		}
	case *pangeaAdapter:
		result, ok := obj["result"].(map[string]interface{})
		if !ok {
			return fmt.Errorf("missing pangea result")
		}
		if _, ok := result["detected"].(bool); !ok {
			return fmt.Errorf("missing pangea detected verdict")
		}
		if values, exists := result["findings"]; exists {
			list, ok := values.([]interface{})
			if !ok {
				return fmt.Errorf("invalid pangea findings")
			}
			for _, value := range list {
				m, ok := value.(map[string]interface{})
				if !ok || !validScore(m["score"]) {
					return fmt.Errorf("invalid pangea score")
				}
			}
		}
	case *bedrockAdapter:
		if obj["action"] != "NONE" && obj["action"] != "GUARDRAIL_INTERVENED" {
			return fmt.Errorf("unknown bedrock action")
		}
	case *aporiaAdapter:
		switch obj["action"] {
		case "passthrough", "block", "override":
		default:
			return fmt.Errorf("unknown aporia action")
		}
	case *dynamoaiAdapter:
		if v, exists := obj["error"]; exists && v != "" && v != nil {
			return fmt.Errorf("dynamoai execution error")
		}
		switch obj["finalAction"] {
		case "NONE", "BLOCK", "WARN", "REDACT", "SANITIZE":
		default:
			return fmt.Errorf("unknown dynamoai action")
		}
	case *llamaGuardAdapter, *ibmAdapter:
		choices, ok := obj["choices"].([]interface{})
		if !ok || len(choices) == 0 {
			return fmt.Errorf("missing classification")
		}
		choice, ok := choices[0].(map[string]interface{})
		if !ok {
			return fmt.Errorf("invalid classification")
		}
		message, ok := choice["message"].(map[string]interface{})
		if !ok {
			return fmt.Errorf("missing classification message")
		}
		content, ok := message["content"].(string)
		if !ok {
			return fmt.Errorf("missing classification content")
		}
		words := strings.Fields(strings.ToLower(content))
		if len(words) == 0 {
			return fmt.Errorf("empty classification")
		}
		first := strings.TrimRight(words[0], ".:,!")
		if _, ok := a.(*llamaGuardAdapter); ok {
			if first != "safe" && first != "unsafe" {
				return fmt.Errorf("unknown classification")
			}
		} else if first != "yes" && first != "no" {
			return fmt.Errorf("unknown classification")
		}
	case *azureAdapter:
		categories := a.categories
		if len(categories) == 0 {
			categories = []string{"Hate", "SelfHarm", "Sexual", "Violence"}
		}
		list, ok := obj["categoriesAnalysis"].([]interface{})
		if !ok || len(list) == 0 {
			return fmt.Errorf("missing Azure categories")
		}
		seen := map[string]bool{}
		for _, value := range list {
			cat, ok := value.(map[string]interface{})
			if !ok {
				return fmt.Errorf("invalid Azure category")
			}
			name, ok := cat["category"].(string)
			if !ok || name == "" || seen[name] {
				return fmt.Errorf("invalid or duplicate Azure category")
			}
			severity, ok := cat["severity"].(float64)
			if !ok || severity < 0 || severity > 6 || severity != float64(int(severity)) {
				return fmt.Errorf("invalid Azure severity")
			}
			seen[name] = true
		}
		for _, name := range categories {
			if !seen[name] {
				return fmt.Errorf("missing requested Azure category")
			}
		}
	case *enkryptAdapter:
		summary, ok := obj["summary"].(map[string]interface{})
		if !ok || len(summary) == 0 {
			return fmt.Errorf("missing Enkrypt summary")
		}
		// Enkrypt may list only the detectors that fired; a requested detector
		// absent from an otherwise well-formed summary did not trigger.
		for _, value := range summary {
			switch v := value.(type) {
			case bool:
			case []interface{}:
			case float64:
				if !validScore(v) {
					return fmt.Errorf("invalid Enkrypt score")
				}
			default:
				return fmt.Errorf("invalid Enkrypt detector verdict")
			}
		}
	}
	return nil
}

func validScore(value interface{}) bool {
	score, ok := value.(float64)
	return ok && score >= 0 && score <= 1
}
