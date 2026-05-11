package models

import "encoding/json"

// ResponsesRequest represents an OpenAI Responses API request (POST /v1/responses).
type ResponsesRequest struct {
	Model              string          `json:"model"`
	Input              json.RawMessage `json:"input"`
	Instructions       string          `json:"instructions,omitempty"`
	Tools              json.RawMessage `json:"tools,omitempty"`
	PreviousResponseID string          `json:"previous_response_id,omitempty"`
	Stream             bool            `json:"stream,omitempty"`
	Temperature        *float64        `json:"temperature,omitempty"`
	TopP               *float64        `json:"top_p,omitempty"`
	MaxOutputTokens    *int            `json:"max_output_tokens,omitempty"`
	Store              *bool           `json:"store,omitempty"`
	Metadata           json.RawMessage `json:"metadata,omitempty"`
	User               string          `json:"user,omitempty"`
	// Pass-through fields for the provider.
	Extra map[string]json.RawMessage `json:"-"`
}

// UnmarshalJSON implements custom unmarshaling to capture unknown fields.
func (r *ResponsesRequest) UnmarshalJSON(data []byte) error {
	type Alias ResponsesRequest
	aux := &struct {
		*Alias
	}{
		Alias: (*Alias)(r),
	}
	if err := json.Unmarshal(data, aux); err != nil {
		return err
	}

	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		return err
	}

	known := map[string]bool{
		"model": true, "input": true, "instructions": true, "tools": true,
		"previous_response_id": true, "stream": true, "temperature": true,
		"top_p": true, "max_output_tokens": true, "store": true,
		"metadata": true, "user": true,
	}

	for k, v := range raw {
		if !known[k] {
			if r.Extra == nil {
				r.Extra = make(map[string]json.RawMessage)
			}
			r.Extra[k] = v
		}
	}

	return nil
}

// MarshalJSON implements custom marshaling to include extra fields.
func (r ResponsesRequest) MarshalJSON() ([]byte, error) {
	type Alias ResponsesRequest
	data, err := json.Marshal((*Alias)(&r))
	if err != nil {
		return nil, err
	}
	if len(r.Extra) == 0 {
		return data, nil
	}
	var obj map[string]json.RawMessage
	if err := json.Unmarshal(data, &obj); err != nil {
		return nil, err
	}
	for k, v := range r.Extra {
		obj[k] = v
	}
	return json.Marshal(obj)
}

// ResponsesResponse represents an OpenAI Responses API response.
// This is intentionally kept as json.RawMessage because the response format
// is complex and varies by provider. The gateway passes it through.
type ResponsesResponse struct {
	ID          string          `json:"id"`
	Object      string          `json:"object"`
	Model       string          `json:"model"`
	Status      string          `json:"status"`
	Output      json.RawMessage `json:"output"`
	Usage       json.RawMessage `json:"usage,omitempty"`
	CreatedAt   float64         `json:"created_at"`
	RawResponse json.RawMessage `json:"-"` // Full raw response for storage/retrieval
}

// ResponsesStore stores responses for retrieval via GET /v1/responses/{id}.
// In-memory implementation with TTL-based expiry.
type ResponsesStore struct{}
