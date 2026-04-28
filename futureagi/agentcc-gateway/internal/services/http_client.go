package services

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// HTTPClient is a service for making HTTP requests
type HTTPClient struct {
	client *http.Client
}

// NewHTTPClient creates a new HTTP client with default settings
func NewHTTPClient() *HTTPClient {
	return &HTTPClient{
		client: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
}

// RequestConfig holds configuration for an HTTP request
type RequestConfig struct {
	Method      string            `json:"method"`
	URL         string            `json:"url"`
	Headers     map[string]string `json:"headers"`
	Body        interface{}       `json:"body"`
	Timeout     time.Duration     `json:"timeout"`
	ContentType string            `json:"content_type"`
}

// Response holds the HTTP response data
type Response struct {
	StatusCode int         `json:"status_code"`
	Headers    http.Header `json:"headers"`
	Body       interface{} `json:"body"`
}

// MakeRequest executes an HTTP request
func (c *HTTPClient) MakeRequest(ctx context.Context, config RequestConfig) (*Response, error) {
	var bodyReader io.Reader

	// Handle body serialization
	if config.Body != nil {
		var bodyBytes []byte
		var err error

		switch v := config.Body.(type) {
		case string:
			bodyBytes = []byte(v)
		case []byte:
			bodyBytes = v
		default:
			bodyBytes, err = json.Marshal(v)
			if err != nil {
				return nil, fmt.Errorf("failed to marshal body: %w", err)
			}
		}
		bodyReader = bytes.NewReader(bodyBytes)
	}

	// Create request
	req, err := http.NewRequestWithContext(ctx, config.Method, config.URL, bodyReader)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	// Set headers
	for key, value := range config.Headers {
		req.Header.Set(key, value)
	}

	// Set content-type header if not already set
	if config.ContentType != "" && req.Header.Get("Content-Type") == "" {
		req.Header.Set("Content-Type", config.ContentType)
	}

	// Set timeout
	if config.Timeout > 0 {
		ctx, cancel := context.WithTimeout(ctx, config.Timeout)
		defer cancel()
		req = req.WithContext(ctx)
	}

	// Make request
	resp, err := c.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	// Read response body
	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response body: %w", err)
	}

	// Parse response body into JSON if possible
	var parsedBody interface{}
	if len(respBody) > 0 {
		if err := json.Unmarshal(respBody, &parsedBody); err != nil {
			// If JSON parsing fails, keep as raw bytes
			parsedBody = string(respBody)
		}
	}

	return &Response{
		StatusCode: resp.StatusCode,
		Headers:    resp.Header,
		Body:       parsedBody,
	}, nil
}
