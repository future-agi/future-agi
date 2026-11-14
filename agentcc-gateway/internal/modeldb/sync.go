package modeldb

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

const (
	// DefaultCatalogueURL is the reviewed catalogue in this repo. Point it
	// elsewhere with model_database.catalogue_url; an empty URL turns the sync off.
	DefaultCatalogueURL = "https://raw.githubusercontent.com/future-agi/future-agi/main/agentcc-gateway/internal/modeldb/litellm.json"

	DefaultSyncInterval = 24 * time.Hour
	MinSyncInterval     = time.Hour

	startupFetchTimeout = 5 * time.Second
	fetchTimeout        = 30 * time.Second
	maxCatalogueBytes   = 64 << 20

	// minFetchedEntries and the half-of-bundled rule reject a fetch that is
	// truncated, empty or the wrong file, as LiteLLM's proxy does.
	minFetchedEntries = 50
)

// Syncer fetches the catalogue from a URL at startup and on an interval and
// hands each good one to a Catalogue. Fail-open: a failed or implausible fetch
// keeps the last good catalogue, logs a warning and never stops the gateway.
type Syncer struct {
	catalogue      *Catalogue
	url            string
	logURL         string // url with credentials removed, for logs and the admin endpoint
	interval       time.Duration
	client         *http.Client
	bundledEntries int
}

// NewSyncer returns a Syncer, or nil when url is empty (sync off). The
// interval is raised to MinSyncInterval, and an unset one is DefaultSyncInterval.
func NewSyncer(c *Catalogue, url string, interval time.Duration) *Syncer {
	if url == "" {
		return nil
	}
	switch {
	case interval <= 0:
		interval = DefaultSyncInterval
	case interval < MinSyncInterval:
		slog.Warn("model catalogue sync interval below the minimum; using the minimum",
			"configured", interval, "minimum", MinSyncInterval)
		interval = MinSyncInterval
	}
	return &Syncer{
		catalogue:      c,
		url:            url,
		logURL:         models.RedactCredentials(url),
		interval:       interval,
		client:         &http.Client{},
		bundledEntries: len(BundledModels),
	}
}

// Run syncs once with a short timeout, then on every interval, until ctx ends.
// Run it in its own goroutine: the first fetch must not hold up boot.
func (s *Syncer) Run(ctx context.Context) {
	s.syncOnce(ctx, startupFetchTimeout)
	ticker := time.NewTicker(s.interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			s.syncOnce(ctx, fetchTimeout)
		}
	}
}

func (s *Syncer) syncOnce(ctx context.Context, timeout time.Duration) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	fetched, err := s.fetch(ctx)
	if err != nil {
		s.catalogue.recordFailure(err)
		slog.Warn("model catalogue sync failed; keeping the last good catalogue",
			"url", s.logURL, "error", err, "source", s.catalogue.Info().Source)
		return
	}
	s.catalogue.Overlay(fetched, s.logURL)
	slog.Info("model catalogue loaded", "source", SourceFetched, "url", s.logURL, "fetched_entries", len(fetched), "entries", s.catalogue.Info().Entries)
}

func (s *Syncer) fetch(ctx context.Context) (map[string]*ModelInfo, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, s.url, nil)
	if err != nil {
		return nil, fmt.Errorf("build request: %w", err)
	}
	resp, err := s.client.Do(req)
	if err != nil {
		// A *url.Error carries the whole URL, and this error reaches the logs
		// and the admin endpoint's last_error.
		var ue *url.Error
		if errors.As(err, &ue) {
			ue.URL = s.logURL
		}
		return nil, fmt.Errorf("fetch: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("fetch: status %d", resp.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxCatalogueBytes+1))
	if err != nil {
		return nil, fmt.Errorf("read body: %w", err)
	}
	if len(body) > maxCatalogueBytes {
		return nil, fmt.Errorf("catalogue larger than %d bytes", maxCatalogueBytes)
	}
	models, err := parseLiteLLM(body, true)
	if err != nil {
		return nil, err
	}
	if floor := max(minFetchedEntries, (s.bundledEntries+1)/2); len(models) < floor {
		return nil, fmt.Errorf("catalogue has %d entries, fewer than the %d required", len(models), floor)
	}
	return models, nil
}
