package tenant

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"time"
)

// syncHTTPClient is a shared, reusable HTTP client for control plane sync.
// This avoids creating a new client (and new TCP connections) on every sync tick.
var syncHTTPClient = &http.Client{Timeout: 15 * time.Second}

// SyncFromControlPlane fetches all org configs from the Django control plane
// bulk endpoint and loads them into the tenant store. If the control plane is
// unreachable, it logs a warning and returns nil (gateway starts with an empty
// store — org configs will be pushed later via the admin API).
func SyncFromControlPlane(ctx context.Context, baseURL, adminToken string, store *Store) error {
	if baseURL == "" {
		slog.Info("control plane sync skipped: no URL configured")
		return nil
	}

	endpoint := baseURL + "/agentcc/org-configs/bulk/"

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return fmt.Errorf("building sync request: %w", err)
	}
	if adminToken != "" {
		req.Header.Set("Authorization", "Bearer "+adminToken)
	}

	resp, err := syncHTTPClient.Do(req)
	if err != nil {
		slog.Warn("control plane sync failed (gateway will start with empty org store)",
			"url", endpoint,
			"error", err,
		)
		return fmt.Errorf("control plane unreachable: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
		slog.Warn("control plane sync returned non-200",
			"url", endpoint,
			"status", resp.StatusCode,
			"body", string(body),
		)
		return fmt.Errorf("control plane returned status %d: %s", resp.StatusCode, body)
	}

	// Django response format: {"status": true, "result": {"org_id": {...}, ...}}
	var envelope struct {
		Status bool                       `json:"status"`
		Result map[string]json.RawMessage `json:"result"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 10<<20)).Decode(&envelope); err != nil {
		slog.Warn("control plane sync: failed to parse response",
			"error", err,
		)
		return fmt.Errorf("parsing sync response: %w", err)
	}

	if !envelope.Status {
		slog.Warn("control plane sync: response status=false")
		return fmt.Errorf("control plane sync: status=false")
	}

	// Build set of all org IDs in the response (including those that fail to parse).
	allOrgIDs := make(map[string]struct{}, len(envelope.Result))
	configs := make(map[string]*OrgConfig, len(envelope.Result))
	var parseErrors int
	for orgID, raw := range envelope.Result {
		allOrgIDs[orgID] = struct{}{}
		var cfg OrgConfig
		if err := json.Unmarshal(raw, &cfg); err != nil {
			slog.Warn("control plane sync: failed to parse org config, keeping existing entry",
				"org_id", orgID,
				"error", err,
			)
			parseErrors++
			continue
		}
		configs[orgID] = &cfg
	}

	store.MergeBulk(configs, allOrgIDs)
	slog.Info("control plane sync completed",
		"orgs_loaded", len(configs),
		"parse_errors", parseErrors,
	)
	return nil
}
