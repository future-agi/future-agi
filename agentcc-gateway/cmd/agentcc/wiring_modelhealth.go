package main

import "github.com/futureagi/agentcc-gateway/internal/routing"

// initModelHealth installs this node's per-model health store: every
// upstream attempt's finish feeds it, and routing reads it through the
// handlers' injected ModelHealth.
func initModelHealth(d *wiringDeps) error {
	d.modelHealth = routing.NewModelHealth()
	return nil
}
