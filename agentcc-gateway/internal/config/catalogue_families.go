package config

import (
	"fmt"
	"os"
	"strings"
)

// CatalogueFamilyCAT2 names the image-priced Gemini, dynamic-router and Lyria
// contracts. It is not permission to run mixed catalogue/token fallback chains.
const CatalogueFamilyCAT2 = "cat2"

const EnvManagedCatalogueFamilies = "AGENTCC_MANAGED_CATALOGUE_FAMILIES"

func loadCatalogueFamiliesEnv(cfg *Config) {
	if value, present := os.LookupEnv(EnvManagedCatalogueFamilies); present {
		cfg.Managed.CatalogueFamilies = nil
		if value != "" {
			// Do not discard empty/unknown entries: a typo must fail startup.
			cfg.Managed.CatalogueFamilies = strings.Split(value, ",")
		}
	}
}

func validateCatalogueFamilies(families []string) error {
	if len(families) == 0 || (len(families) == 1 && families[0] == CatalogueFamilyCAT2) {
		return nil
	}
	return fmt.Errorf("managed.catalogue_families must be [] or [cat2]; mixed catalogue/token chains are not supported")
}

func (m ManagedConfig) CAT2Enabled() bool {
	return len(m.CatalogueFamilies) == 1 && m.CatalogueFamilies[0] == CatalogueFamilyCAT2
}
