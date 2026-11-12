package inclusive

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math/big"
	"net/http"
	"net/url"
	"time"
)

// HTTPAdapters has no default host or credential. Wiring supplies the existing
// backend refund route and OpenRouter generation endpoint explicitly. Tests use
// httptest only. Redirects are refused so credentials cannot follow Location.
type HTTPAdapters struct {
	Client                       *http.Client
	GenerationURL, RefundURL     string
	ProviderID                   string
	GenerationToken, RefundToken func() string
}

func (a HTTPAdapters) Accepts(provider, upstreamKeyID string) bool {
	return a.ProviderID != "" && a.ProviderID == provider && upstreamKeyID == ""
}

func (a HTTPAdapters) send(ctx context.Context, method, target, token string, body []byte) ([]byte, error) {
	u, e := url.Parse(target)
	if e != nil || u.Host == "" || u.User != nil || u.Fragment != "" || token == "" || (u.Scheme != "https" && !(u.Scheme == "http" && (u.Hostname() == "127.0.0.1" || u.Hostname() == "localhost" || u.Hostname() == "::1"))) {
		return nil, fmt.Errorf("invalid reconciliation transport configuration")
	}
	req, e := http.NewRequestWithContext(ctx, method, u.String(), bytes.NewReader(body))
	if e != nil {
		return nil, e
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	client := http.Client{Timeout: 15 * time.Second}
	if a.Client != nil {
		client = *a.Client
	}
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	if client.Timeout == 0 {
		client.Timeout = 15 * time.Second
	}
	res, e := client.Do(req)
	if e != nil {
		return nil, fmt.Errorf("reconciliation transport failed")
	}
	defer res.Body.Close()
	if res.StatusCode < 200 || res.StatusCode >= 300 {
		return nil, fmt.Errorf("reconciliation HTTP status %d", res.StatusCode)
	}
	data, e := io.ReadAll(io.LimitReader(res.Body, 1<<20+1))
	if e != nil || len(data) > 1<<20 {
		return nil, fmt.Errorf("invalid reconciliation response size")
	}
	return data, nil
}
func (a HTTPAdapters) Generation(ctx context.Context, id string) (Generation, error) {
	if id == "" || a.GenerationToken == nil {
		return Generation{}, fmt.Errorf("generation identity or credential missing")
	}
	u, e := url.Parse(a.GenerationURL)
	if e != nil {
		return Generation{}, fmt.Errorf("invalid generation URL")
	}
	q := u.Query()
	q.Set("id", id)
	u.RawQuery = q.Encode()
	data, e := a.send(ctx, http.MethodGet, u.String(), a.GenerationToken(), nil)
	if e != nil {
		return Generation{}, e
	}
	var response struct {
		Data struct {
			ID    string          `json:"id"`
			Model string          `json:"model"`
			Cost  json.RawMessage `json:"total_cost"`
		} `json:"data"`
	}
	if e = json.Unmarshal(data, &response); e != nil {
		return Generation{}, fmt.Errorf("invalid generation response")
	}
	g := Generation{ID: response.Data.ID, Model: response.Data.Model, CostUSD: string(response.Data.Cost)}
	if g.ID != id || g.Model == "" {
		return Generation{}, fmt.Errorf("generation identity mismatch")
	}
	if _, _, e = Charge(g.CostUSD); e != nil {
		return Generation{}, e
	}
	return g, nil
}
func (a HTTPAdapters) Refund(ctx context.Context, org, request string, micros int64, reason string) error {
	if a.RefundToken == nil || micros <= 0 || org == "" || request == "" {
		return fmt.Errorf("invalid refund request")
	}
	// Exact decimal string matches the existing Django DecimalField. Its refund
	// view checks the persisted amount on idempotent replay and returns 409 on a
	// mismatch; 404 (the drain has not settled yet) remains pending and retries.
	body, _ := json.Marshal(map[string]string{"org_id": org, "request_id": request, "amount_usd": fmt.Sprintf("%d.%06d", micros/1000000, micros%1000000), "reason": reason})
	data, e := a.send(ctx, http.MethodPost, a.RefundURL, a.RefundToken(), body)
	if e != nil {
		return e
	}
	var ack struct {
		Status bool `json:"status"`
		Result struct {
			Entry struct {
				RequestID string          `json:"request_id"`
				AmountUSD json.RawMessage `json:"amount_usd"`
				Kind      string          `json:"kind"`
			} `json:"entry"`
		} `json:"result"`
	}
	if json.Unmarshal(data, &ack) != nil || !ack.Status || ack.Result.Entry.RequestID != request || ack.Result.Entry.Kind != "refund" {
		return fmt.Errorf("invalid refund acknowledgement")
	}
	raw := string(ack.Result.Entry.AmountUSD)
	var quoted string
	if json.Unmarshal(ack.Result.Entry.AmountUSD, &quoted) == nil {
		raw = quoted
	}
	paid, err := amount(raw)
	if err != nil || paid.Cmp(big.NewRat(micros, 1000000)) != 0 {
		return fmt.Errorf("refund amount mismatch")
	}
	return nil
}
