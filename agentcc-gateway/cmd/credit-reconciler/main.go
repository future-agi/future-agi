// credit-reconciler is an explicitly launched worker, never enabled by shipped
// configuration. Financial spends still flow through the existing Django drain.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing/inclusive"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

func main() {
	if err := run(); err != nil {
		slog.Error("credit reconciliation stopped", "error", err)
		os.Exit(1)
	}
}
func run() error {
	path := flag.String("config", "", "explicit gateway config path")
	provider := flag.String("provider", "", "OpenRouter provider ID")
	generationURL := flag.String("generation-url", "", "explicit generation lookup endpoint")
	retainedPath := flag.String("retained-credentials", "", "optional protected JSON file containing retained shared/per-org reconciliation credentials")
	once := flag.Bool("once", false, "one recovery/reconciliation pass")
	flag.Parse()
	if *path == "" || *provider == "" || *generationURL == "" {
		return fmt.Errorf("config, provider and generation-url are required")
	}
	cfg, err := config.Load(*path)
	if err != nil {
		return fmt.Errorf("cannot load worker configuration")
	}
	pc, ok := cfg.Providers[*provider]
	if !ok || pc.Type != "openrouter" || !pc.Managed || cfg.ControlPlane.URL == "" || cfg.ControlPlane.AdminToken == "" {
		return fmt.Errorf("worker requires an explicit OpenRouter provider and refund control plane")
	}
	client, err := redisstate.NewClient(redisstate.Config{Address: cfg.Redis.Address, Password: cfg.Redis.Password, DB: cfg.Redis.DB, Timeout: cfg.Redis.Timeout})
	if err != nil {
		return fmt.Errorf("billing Redis unavailable")
	}
	defer client.Close()
	store := redisstate.NewManagedCreditStore(client, "")
	entries := []inclusive.RetainedCredential{}
	if pc.APIKey != "" {
		entries = append(entries, inclusive.RetainedCredential{Provider: *provider, Token: pc.APIKey})
	}
	if *retainedPath != "" {
		file, err := os.Open(*retainedPath)
		if err != nil {
			return fmt.Errorf("retained credential file unavailable")
		}
		defer file.Close()
		info, err := file.Stat()
		if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || info.Size() > 1<<20 {
			return fmt.Errorf("retained credentials require an owner-only regular file of at most 1 MiB")
		}
		var retained []inclusive.RetainedCredential
		decoder := json.NewDecoder(file)
		decoder.DisallowUnknownFields()
		if decoder.Decode(&retained) != nil {
			return fmt.Errorf("invalid retained credential file")
		}
		entries = append(entries, retained...)
	}
	adapter := inclusive.IdentityHTTPAdapters{HTTPAdapters: inclusive.HTTPAdapters{ProviderID: *provider, GenerationURL: *generationURL, RefundURL: strings.TrimRight(cfg.ControlPlane.URL, "/") + "/agentcc/managed-credits/refund/", RefundToken: func() string { return cfg.ControlPlane.AdminToken }}, Resolve: inclusive.RetainedResolver(entries)}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()
	for {
		err = store.ReconcileAll(ctx, adapter, adapter)
		if *once {
			return err
		}
		if err != nil {
			slog.Warn("credit reconciliation remains pending", "error", err)
		}
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
		}
	}
}
