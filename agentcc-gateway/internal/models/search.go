package models

import (
	"encoding/json"
	"net/http"
)

// SearchRequest represents a search API request.
type SearchRequest struct {
	QueryRaw           json.RawMessage `json:"query"`
	SearchProvider     string          `json:"search_provider"`
	MaxResults         int             `json:"max_results"`
	SearchDomainFilter []string        `json:"search_domain_filter,omitempty"`
	MaxTokensPerPage   int             `json:"max_tokens_per_page"`
	Country            string          `json:"country,omitempty"`
}

// QueryString returns the query as a single string.
func (r *SearchRequest) QueryString() (string, error) {
	var s string
	if err := json.Unmarshal(r.QueryRaw, &s); err == nil {
		return s, nil
	}
	// Try as array, return first.
	var arr []string
	if err := json.Unmarshal(r.QueryRaw, &arr); err == nil && len(arr) > 0 {
		return arr[0], nil
	}
	return "", &APIError{Status: http.StatusBadRequest, Type: ErrTypeInvalidRequest, Code: "invalid_query", Message: "query must be a string or array of strings"}
}

// QueryStrings returns the query as a slice of strings.
func (r *SearchRequest) QueryStrings() ([]string, error) {
	var arr []string
	if err := json.Unmarshal(r.QueryRaw, &arr); err == nil {
		return arr, nil
	}
	var s string
	if err := json.Unmarshal(r.QueryRaw, &s); err == nil {
		return []string{s}, nil
	}
	return nil, &APIError{Status: http.StatusBadRequest, Type: ErrTypeInvalidRequest, Code: "invalid_query", Message: "query must be a string or array of strings"}
}

// IsMultiQuery returns true if the query is an array.
func (r *SearchRequest) IsMultiQuery() bool {
	return len(r.QueryRaw) > 0 && r.QueryRaw[0] == '['
}

// Validate checks that the search request is valid.
func (r *SearchRequest) Validate() *APIError {
	if len(r.QueryRaw) == 0 {
		return ErrBadRequest("missing_query", "query is required")
	}
	if r.SearchProvider == "" {
		return ErrBadRequest("missing_search_provider", "search_provider is required")
	}
	if r.MaxResults < 0 || r.MaxResults > 20 {
		return ErrBadRequest("invalid_max_results", "max_results must be between 1 and 20")
	}
	if len(r.SearchDomainFilter) > 20 {
		return ErrBadRequest("too_many_domains", "search_domain_filter must have at most 20 entries")
	}
	return nil
}

// SearchResponse represents a search API response.
type SearchResponse struct {
	Object         string          `json:"object"`
	SearchProvider string          `json:"search_provider"`
	Query          json.RawMessage `json:"query"`
	Results        []SearchResult  `json:"results"`
}

// SearchResult represents a single search result.
type SearchResult struct {
	Title       string `json:"title"`
	URL         string `json:"url"`
	Snippet     string `json:"snippet"`
	Date        string `json:"date,omitempty"`
	LastUpdated string `json:"last_updated,omitempty"`
}
