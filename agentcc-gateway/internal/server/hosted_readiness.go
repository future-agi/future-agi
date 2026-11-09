package server

import (
	"context"
	"reflect"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func (s *Server) hostedDependenciesReady(parent context.Context) bool {
	if s.keyStore == nil || !s.keyStore.KeyListLoaded() || s.TenantStore == nil || !s.TenantStore.InitialSyncComplete() || s.hostedRedisReady == nil {
		return false
	}
	ctx, cancel := context.WithTimeout(parent, time.Second)
	defer cancel()
	return s.hostedRedisReady(ctx) == nil
}

// ReloadRouting does not rebuild auth, sync, probes or other plugins. Refuse
// changes to those settings instead of advertising a configuration not running.
func hostedReloadCompatible(before, after *config.Config) bool {
	a, b := *before, *after
	a.Routing = b.Routing
	return reflect.DeepEqual(a, b)
}
