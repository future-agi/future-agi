package auth

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"time"
)

// keySyncHTTPClient is a shared, reusable HTTP client for key sync.
var keySyncHTTPClient = &http.Client{Timeout: 15 * time.Second}

// SyncKeysFromControlPlane fetches all active API key hashes from the Django
// control plane and loads them into the KeyStore. If the control plane is
// unreachable, it logs a warning and returns nil (non-fatal — the gateway
// starts with only config.yaml seed keys).
func SyncKeysFromControlPlane(ctx context.Context, baseURL, adminToken string, ks *KeyStore) error {
	if baseURL == "" {
		slog.Info("key sync skipped: no control plane URL configured")
		return nil
	}

	endpoint := baseURL + "/agentcc/api-keys/bulk/"

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return fmt.Errorf("building key sync request: %w", err)
	}
	if adminToken != "" {
		req.Header.Set("Authorization", "Bearer "+adminToken)
	}

	resp, err := keySyncHTTPClient.Do(req)
	if err != nil {
		slog.Warn("key sync from control plane failed (gateway will start with config keys only)",
			"url", endpoint,
			"error", err,
		)
		return fmt.Errorf("key sync unreachable: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
		slog.Warn("key sync returned non-200",
			"url", endpoint,
			"status", resp.StatusCode,
			"body", string(body),
		)
		return fmt.Errorf("key sync returned status %d: %s", resp.StatusCode, body)
	}

	// Django response format: {"status": true, "result": [...]}
	var envelope struct {
		Status bool        `json:"status"`
		Result []SyncedKey `json:"result"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 10<<20)).Decode(&envelope); err != nil {
		slog.Warn("key sync: failed to parse response", "error", err)
		return fmt.Errorf("parsing key sync response: %w", err)
	}

	if !envelope.Status {
		slog.Warn("key sync: response status=false")
		return fmt.Errorf("key sync: status=false")
	}

	if len(envelope.Result) == 0 {
		slog.Warn("key sync: control plane returned empty key set",
			"url", endpoint,
		)
	}

	loaded := ks.SyncFromHashes(envelope.Result)
	slog.Info("key sync from control plane completed",
		"keys_received", len(envelope.Result),
		"keys_synced", loaded,
	)
	return nil
}
