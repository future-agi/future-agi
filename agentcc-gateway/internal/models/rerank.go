package models

// RerankRequest represents a reranking request (Cohere-compatible format).
type RerankRequest struct {
	Model           string   `json:"model"`
	Query           string   `json:"query"`
	Documents       []string `json:"documents"`
	TopN            *int     `json:"top_n,omitempty"`
	ReturnDocuments bool     `json:"return_documents,omitempty"`
}

// RerankResponse represents a reranking response.
type RerankResponse struct {
	ID      string         `json:"id"`
	Results []RerankResult `json:"results"`
	Meta    *RerankMeta    `json:"meta,omitempty"`
}

// RerankResult is a single reranked document.
type RerankResult struct {
	Index          int             `json:"index"`
	RelevanceScore float64         `json:"relevance_score"`
	Document       *RerankDocument `json:"document,omitempty"`
}

// RerankDocument is a document in a rerank result.
type RerankDocument struct {
	Text string `json:"text"`
}

// RerankMeta contains billing metadata for rerank requests.
type RerankMeta struct {
	BilledUnits map[string]int `json:"billed_units,omitempty"`
}

// RerankSearchUnitsMetadataKey is the request metadata key under which the
// rerank handler records the search units the upstream billed.
const RerankSearchUnitsMetadataKey = "search_units"

// SearchUnits is the search units the upstream billed for r, 0 when it
// reported none.
func (r *RerankResponse) SearchUnits() int {
	if r == nil || r.Meta == nil {
		return 0
	}
	return r.Meta.BilledUnits["search_units"]
}
