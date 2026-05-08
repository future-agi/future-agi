package routing

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"time"
)

// ShadowFlusher periodically drains the ShadowStore and POSTs batches
// to the Django webhook endpoint for persistence.
type ShadowFlusher struct {
	store         *ShadowStore
	webhookURL    string
	webhookSecret string
	interval      time.Duration
	client        *http.Client
}

// shadowFlushPayload is the JSON body sent to the Django webhook.
type shadowFlushPayload struct {
	Results []ShadowResult `json:"results"`
}

// NewShadowFlusher creates a flusher that sends shadow results to Django.
func NewShadowFlusher(store *ShadowStore, webhookURL, webhookSecret string, interval time.Duration) *ShadowFlusher {
	if interval <= 0 {
		interval = 60 * time.Second
	}
	return &ShadowFlusher{
		store:         store,
		webhookURL:    webhookURL,
		webhookSecret: webhookSecret,
		interval:      interval,
		client:        &http.Client{Timeout: 15 * time.Second},
	}
}

// Run starts the flusher loop. It blocks until ctx is cancelled.
func (f *ShadowFlusher) Run(ctx context.Context) {
	ticker := time.NewTicker(f.interval)
	defer ticker.Stop()

	slog.Info("shadow flusher started",
		"interval", f.interval.String(),
		"webhook_url", f.webhookURL,
	)

	for {
		select {
		case <-ctx.Done():
			// Final flush before shutdown.
			f.flush()
			slog.Info("shadow flusher stopped")
			return
		case <-ticker.C:
			f.flush()
		}
	}
}

func (f *ShadowFlusher) flush() {
	results := f.store.DrainAll()
	if len(results) == 0 {
		return
	}

	payload := shadowFlushPayload{
		Results: results,
	}

	body, err := json.Marshal(payload)
	if err != nil {
		slog.Error("shadow flusher: marshal failed", "error", err, "count", len(results))
		return
	}

	req, err := http.NewRequest("POST", f.webhookURL, bytes.NewReader(body))
	if err != nil {
		slog.Error("shadow flusher: create request failed", "error", err)
		return
	}
	req.Header.Set("Content-Type", "application/json")
	if f.webhookSecret != "" {
		req.Header.Set("X-Webhook-Secret", f.webhookSecret)
	}

	resp, err := f.client.Do(req)
	if err != nil {
		slog.Error("shadow flusher: send failed",
			"error", err,
			"count", len(results),
			"webhook_url", f.webhookURL,
		)
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		slog.Error("shadow flusher: webhook returned error",
			"status", resp.StatusCode,
			"count", len(results),
		)
		return
	}

	slog.Debug("shadow flusher: sent results",
		"count", len(results),
		"status", resp.StatusCode,
	)
}

// WebhookURL returns the configured webhook URL for use in admin/stats endpoints.
func (f *ShadowFlusher) WebhookURL() string {
	if f == nil {
		return ""
	}
	return f.webhookURL
}

// FormatWebhookURL builds the shadow results webhook URL from the control plane base URL.
func FormatWebhookURL(controlPlaneURL string) string {
	return fmt.Sprintf("%s/agentcc/webhook/shadow-results/", controlPlaneURL)
}
