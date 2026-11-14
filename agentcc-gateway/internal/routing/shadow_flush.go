package routing

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"sync"
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

	stop     chan struct{} // Close closes it to end Run
	stopOnce sync.Once
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
		stop:          make(chan struct{}),
	}
}

// Run starts the flusher loop. It blocks until ctx is cancelled or Close is
// called.
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
			f.flush(context.Background())
			slog.Info("shadow flusher stopped")
			return
		case <-f.stop:
			return // Close makes the last flush
		case <-ticker.C:
			f.flush(context.Background())
		}
	}
}

// Close stops Run and sends the results still buffered, giving up when ctx
// ends. By design it does not wait for a periodic flush that is already
// sending, which only the client's 15s timeout bounds, and it has no time of
// its own: when the caller's ctx is spent, the buffered results are lost.
func (f *ShadowFlusher) Close(ctx context.Context) {
	f.stopOnce.Do(func() { close(f.stop) })
	f.flush(ctx)
	slog.Info("shadow flusher stopped")
}

func (f *ShadowFlusher) flush(ctx context.Context) {
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

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, f.webhookURL, bytes.NewReader(body))
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
