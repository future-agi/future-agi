package models

import (
	"bytes"
	"encoding/json"
	"fmt"
	"strings"
)

// SystemOneRequest preserves provider-specific question fields verbatim.
type SystemOneRequest struct {
	Model     string                     `json:"model"`
	State     json.RawMessage            `json:"state"`
	Questions map[string]json.RawMessage `json:"questions"`
}

// SystemOneResponse retains each typed answer, including future provider fields.
type SystemOneResponse struct {
	Model   string                     `json:"model"`
	Answers map[string]json.RawMessage `json:"answers"`
	Usage   SystemOneUsage             `json:"usage"`
}

type SystemOneUsage struct {
	InputTokens  int `json:"input_tokens"`
	OutputTokens int `json:"output_tokens"`
}

// Validate checks the envelope and question structure without exposing content.
func (r *SystemOneRequest) Validate() *APIError {
	if strings.TrimSpace(r.Model) == "" {
		return ErrBadRequest("missing_model", "model is required")
	}
	if len(bytes.TrimSpace(r.State)) == 0 || bytes.Equal(bytes.TrimSpace(r.State), []byte("null")) {
		return ErrBadRequest("missing_state", "state is required and must not be null")
	}
	if len(r.Questions) == 0 {
		return ErrBadRequest("missing_questions", "questions is required and must not be empty")
	}
	for id, raw := range r.Questions {
		invalid := func(reason string) *APIError {
			return ErrBadRequest("invalid_question", fmt.Sprintf("question %q: %s", id, reason))
		}
		var q map[string]json.RawMessage
		if err := json.Unmarshal(raw, &q); err != nil || q == nil {
			return invalid("must be a JSON object")
		}
		var kind string
		if err := json.Unmarshal(q["type"], &kind); err != nil || (kind != "noul" && kind != "choice" && kind != "score") {
			return invalid("type must be noul, choice, or score")
		}
		var instructions any
		if err := json.Unmarshal(q["instructions"], &instructions); err != nil {
			return invalid("instructions must be non-empty text, object, or array")
		}
		nonempty := false
		switch v := instructions.(type) {
		case string:
			nonempty = strings.TrimSpace(v) != ""
		case map[string]any:
			nonempty = len(v) > 0
		case []any:
			nonempty = len(v) > 0
		}
		if !nonempty {
			return invalid("instructions must be non-empty text, object, or array")
		}
		switch kind {
		case "choice":
			var criteria map[string]json.RawMessage
			if err := json.Unmarshal(q["criteria"], &criteria); err != nil || len(criteria) < 1 || len(criteria) > 255 {
				return invalid("choice criteria must be an object with 1–255 keys")
			}
		case "score":
			var criteria []json.RawMessage
			if err := json.Unmarshal(q["criteria"], &criteria); err != nil || len(criteria) < 2 || len(criteria) > 10 {
				return invalid("score criteria must be an array with 2–10 entries")
			}
		}
	}
	return nil
}
