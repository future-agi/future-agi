package models

// APICallRequest represents the request payload for an API call node
type APICallRequest struct {
	Method      string            `json:"method"`
	URL         string            `json:"url"`
	Headers     map[string]string `json:"headers"`
	Body        interface{}       `json:"body"`
	Timeout     int               `json:"timeout"`
	ContentType string            `json:"content_type"`
}

// APICallResponse represents the response from an API call node
type APICallResponse struct {
	StatusCode int         `json:"status_code"`
	Headers    interface{} `json:"headers"`
	Body       interface{} `json:"body"`
}
