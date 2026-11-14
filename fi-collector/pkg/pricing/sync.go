package pricing

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"time"
)

// This file mirrors agentcc-gateway/internal/modeldb/sync.go. The collector is
// a separate Go module with no shared package between the two, so the small
// fetch-and-check loop is copied; keep the two in step.

const (
	// DefaultCatalogueURL is the reviewed catalogue in this repo, the file the
	// gateway syncs from. The same file is vendored as model_prices.json.
	DefaultCatalogueURL = "https://raw.githubusercontent.com/future-agi/future-agi/main/agentcc-gateway/internal/modeldb/litellm.json"

	DefaultSyncInterval = 24 * time.Hour
	MinSyncInterval     = time.Hour

	startupFetchTimeout = 5 * time.Second
	fetchTimeout        = 30 * time.Second
	maxCatalogueBytes   = 64 << 20
	minFetchedEntries   = 50
)

// Syncer fetches a price table from a URL at startup and on an interval,
// lays each good one over the table the collector started with (fetched wins
// on matching keys; a starting entry the fetch lacks stays) and swaps the
// result into a Pricer. Fail-open: a failed or implausible fetch
// keeps the table in use, logs a warning and never stops the collector.
type Syncer struct {
	pricer   *Pricer
	url      string
	logURL   string // url with credentials masked, for logs
	interval time.Duration
	client   *http.Client
	base     *Table // the table the collector started with
	log      *slog.Logger
}

// NewSyncer returns a Syncer, or nil when url is empty (sync off). base is the
// table in use at start; a fetched table needs at least half its entries. The interval is raised to MinSyncInterval, and an unset one is
// DefaultSyncInterval.
func NewSyncer(p *Pricer, base *Table, url string, interval time.Duration, log *slog.Logger) *Syncer {
	if url == "" {
		return nil
	}
	switch {
	case interval <= 0:
		interval = DefaultSyncInterval
	case interval < MinSyncInterval:
		log.Warn("pricing sync interval below the minimum; using the minimum",
			"configured", interval, "minimum", MinSyncInterval)
		interval = MinSyncInterval
	}
	return &Syncer{pricer: p, url: url, logURL: maskURL(url), interval: interval, client: &http.Client{}, base: base, log: log}
}

// maskURL hides a password and every query value, where a catalogue URL
// would carry a credential, so the URL can be logged.
func maskURL(raw string) string {
	u, err := url.Parse(raw)
	if err != nil {
		return "<unparseable>"
	}
	if u.RawQuery != "" {
		u.RawQuery = "REDACTED"
	}
	return u.Redacted()
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
	table, err := s.fetch(ctx)
	if err != nil {
		s.log.Warn("pricing catalogue sync failed; keeping the table in use", "url", s.logURL, "err", err)
		return
	}
	merged := s.base.overlay(table)
	s.pricer.SetTable(merged)
	s.log.Info("pricing catalogue loaded", "source", "fetched", "url", s.logURL,
		"fetched_entries", table.Len(), "entries", merged.Len())
}

func (s *Syncer) fetch(ctx context.Context) (*Table, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, s.url, nil)
	if err != nil {
		return nil, fmt.Errorf("build request: %w", err)
	}
	resp, err := s.client.Do(req)
	if err != nil {
		// A *url.Error carries the whole URL, and this error is logged.
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
	table, err := parseTable(body, true)
	if err != nil {
		return nil, err
	}
	if floor := max(minFetchedEntries, (s.base.usable+1)/2); table.usable < floor {
		return nil, fmt.Errorf("catalogue has %d entries, fewer than the %d required (entries without a token price do not count)", table.usable, floor)
	}
	return table, nil
}
