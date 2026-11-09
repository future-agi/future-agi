package openai

import "net/http"

// do is the dispatch point for every OpenAI-compatible route, including
// OpenRouter and Azure v1 endpoints. Apply config defaults after per-call
// headers are set and before handing the request to the HTTP client.
func (p *Provider) do(req *http.Request) (*http.Response, error) {
	p.headers.Apply(req)
	return p.httpClient.Do(req)
}
