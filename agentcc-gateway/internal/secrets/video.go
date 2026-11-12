package secrets

import (
	"fmt"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"strings"
)

// ResolveVideoSecrets uses the existing secret schemes. Values and backend
// errors are deliberately excluded from returned errors and logs.
func ResolveVideoSecrets(cfg *config.Config) error {
	backends := map[string]Backend{}
	resolve := func(value *string) error {
		if !IsSecretURI(*value) {
			return nil
		}
		scheme, _, _, e := ParseURI(*value)
		if e != nil {
			return fmt.Errorf("invalid video secret URI")
		}
		b := backends[scheme]
		if b == nil {
			b, e = initBackend(scheme, cfg.Secrets)
			if e != nil {
				return fmt.Errorf("video secret backend unavailable")
			}
			backends[scheme] = b
		}
		v, e := b.Resolve(*value)
		if e != nil {
			return fmt.Errorf("video secret resolution failed")
		}
		*value = v
		return nil
	}
	if e := resolve(&cfg.Video.CorrelationSecret); e != nil {
		return e
	}
	for name, p := range cfg.Video.Providers {
		if !p.Enabled && p.APIKey == "" && p.CredentialRef == "" {
			continue
		}
		if p.APIKey == "" && p.CredentialRef != "" {
			ref, ok := strings.CutPrefix(p.CredentialRef, "provider:")
			if !ok {
				return fmt.Errorf("invalid video credential reference")
			}
			source, ok := cfg.Providers[ref]
			if !ok {
				return fmt.Errorf("video credential reference missing")
			}
			p.APIKey = source.APIKey
		}
		if e := resolve(&p.APIKey); e != nil {
			return e
		}
		cfg.Video.Providers[name] = p
	}
	if e := resolve(&cfg.Video.Artifacts.S3.AccessKey); e != nil {
		return e
	}
	return resolve(&cfg.Video.Artifacts.S3.SecretKey)
}
