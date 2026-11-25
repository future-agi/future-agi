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
// bulk endpoint and loads them into the tenant store. A failure leaves the
// store as it was and is returned for the caller to log: during startup the
// control plane is often not up yet, which is not worth a warning.
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
		return fmt.Errorf("control plane unreachable: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
		return fmt.Errorf("control plane returned status %d from %s: %s", resp.StatusCode, endpoint, body)
	}

	// Django response format:
	// {"status": true, "result": {"org_id": {...}, ...}, "unbuilt_org_ids": ["org_id", ...]}
	// unbuilt_org_ids names orgs whose config the control plane could not build.
	var envelope struct {
		Status        bool                       `json:"status"`
		Result        map[string]json.RawMessage `json:"result"`
		UnbuiltOrgIDs []string                   `json:"unbuilt_org_ids"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 10<<20)).Decode(&envelope); err != nil {
		return fmt.Errorf("parsing sync response: %w", err)
	}

	if !envelope.Status {
		return fmt.Errorf("control plane sync: status=false")
	}

	// Build set of all org IDs in the response, including those that fail to
	// parse here and those the control plane could not build. MergeBulk keeps
	// the stored entry of every org in this set that has no parsed config.
	allOrgIDs := make(map[string]struct{}, len(envelope.Result)+len(envelope.UnbuiltOrgIDs))
	for _, orgID := range envelope.UnbuiltOrgIDs {
		allOrgIDs[orgID] = struct{}{}
	}
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
	if len(envelope.UnbuiltOrgIDs) > 0 {
		slog.Warn("control plane sync: control plane could not build some org configs, keeping existing entries",
			"org_ids", envelope.UnbuiltOrgIDs,
		)
	}
	slog.Info("control plane sync completed",
		"orgs_loaded", len(configs),
		"parse_errors", parseErrors,
		"unbuilt", len(envelope.UnbuiltOrgIDs),
	)
	return nil
}
