// Package toolcatalog loads and validates the tool price cards
// (tool-price-card-v1): one card per capability and vendor, at
// <dir>/<vendor>.json under the catalog root, with the unit SKUs, the resale
// status and the terms profile the capability core enforces. It is the one
// price source for FutureAGI-supplied tools: the capability core reads cards
// through it, and managed MCP servers' price_card references resolve against
// it at startup.
//
// A card is read-only once loaded. owner_price_pending marks a card with no
// recorded price: every SKU is $0 and no managed call is admitted at it.
package toolcatalog

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"maps"
	"math"
	"net/url"
	"regexp"
	"slices"
	"strings"
	"time"
)

// Resale statuses.
const (
	ResaleNative         = "native"
	ResaleManagedPartner = "managed_partner"
	ResaleUnconfirmed    = "unconfirmed"
	ResaleByokOnly       = "byok_only"
)

// Terms profile values.
const (
	TrainsNo             = "no"
	TrainsAIFeaturesOnly = "ai_features_only"
	TrainsYes            = "yes"

	RetentionNone     = "none"
	RetentionMetadata = "metadata"
	RetentionFull     = "full"

	CacheNone   = "none"
	CacheOrg    = "org"
	CacheGlobal = "global"

	EvalExportAllowed   = "allowed"
	EvalExportForbidden = "forbidden"
)

// Billing units of a SKU.
const (
	UnitRequest       = "request"
	UnitPage          = "page"
	UnitMinute        = "minute"
	UnitContainerHour = "container_hour"
)

var units = []string{"request", "result", "page", "content_page", "second", "minute", "char_1k", "gb",
	"search_unit", "image", "clip", "container_hour", "gb_day"}

// Card is one tool-price-card-v1 card.
type Card struct {
	Vendor               string   `json:"vendor"`
	Capability           string   `json:"capability"`
	SKUs                 []SKU    `json:"skus"`
	ResaleStatus         string   `json:"resale_status"`
	OwnerPricePending    bool     `json:"owner_price_pending,omitempty"`
	ResaleTermsRef       string   `json:"resale_terms_ref"`
	TermsURL             string   `json:"terms_url"`
	TermsCheckedAt       string   `json:"terms_checked_at"`
	ZDRCapable           bool     `json:"zdr_capable"`
	TrainsOnInput        string   `json:"trains_on_input"`
	ImprovementLicense   bool     `json:"improvement_license"`
	ImprovementOptOutRef string   `json:"improvement_opt_out_ref,omitempty"`
	Regions              []string `json:"regions"`
	ResultsRetention     string   `json:"results_retention"`
	Cacheable            string   `json:"cacheable"`
	EvalExport           string   `json:"eval_export"`
	NoDivert             bool     `json:"no_divert"`
	NoPerfDisclosure     bool     `json:"no_perf_disclosure"`
	NoBenchmark          bool     `json:"no_benchmark"`
	Attribution          string   `json:"attribution,omitempty"`
	PriceEffectiveUntil  string   `json:"price_effective_until,omitempty"`
}

// SKU is one priced unit of a card. USDPerUnit is the upstream cost, before
// managed.tool_markup_percent.
type SKU struct {
	ID                 string   `json:"sku"`
	Unit               string   `json:"unit"`
	USDPerUnit         float64  `json:"usd_per_unit"`
	Tier               string   `json:"tier,omitempty"`
	FreeOnFailure      bool     `json:"free_on_failure"`
	MinUnits           *float64 `json:"min_units,omitempty"`
	MaxUSDPerCall      *float64 `json:"max_usd_per_call,omitempty"`
	CostFromResultMeta bool     `json:"cost_from_result_meta,omitempty"`
}

// SKU returns the card's SKU with the id.
func (c *Card) SKU(id string) (SKU, bool) {
	for _, s := range c.SKUs {
		if s.ID == id {
			return s, true
		}
	}
	return SKU{}, false
}

var (
	cardRequired = []string{"vendor", "capability", "skus", "resale_status", "resale_terms_ref", "terms_url",
		"terms_checked_at", "zdr_capable", "trains_on_input", "improvement_license", "regions",
		"results_retention", "cacheable", "eval_export", "no_divert", "no_perf_disclosure", "no_benchmark"}
	cardOptional = []string{"owner_price_pending", "improvement_opt_out_ref", "attribution", "price_effective_until"}
	skuRequired  = []string{"sku", "unit", "usd_per_unit", "free_on_failure"}
	skuOptional  = []string{"tier", "min_units", "max_usd_per_call", "cost_from_result_meta"}

	vendorPattern     = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,47}$`)
	capabilityPattern = regexp.MustCompile(`^[a-z][a-z0-9_.]*$`)
)

// ParseCard decodes and validates one card. It accepts exactly
// tool-price-card-v1's $defs/Card: every required key present and not null,
// no other key, each value of its type and enum.
func ParseCard(data []byte) (Card, error) {
	var c Card
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(data, &fields); err != nil {
		return c, fmt.Errorf("not a JSON object: %w", err)
	}
	if err := checkKeys("", fields, cardRequired, cardOptional); err != nil {
		return c, err
	}
	var skus []map[string]json.RawMessage
	if err := json.Unmarshal(fields["skus"], &skus); err != nil {
		return c, fmt.Errorf("skus: %w", err)
	}
	for i, s := range skus {
		if err := checkKeys(fmt.Sprintf("skus[%d].", i), s, skuRequired, skuOptional); err != nil {
			return c, err
		}
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&c); err != nil {
		return c, err
	}
	if err := c.validate(); err != nil {
		return c, fmt.Errorf("%s/%s: %w", c.Capability, c.Vendor, err)
	}
	return c, nil
}

// checkKeys refuses a missing or null required key, a null optional one and
// any key outside both lists.
func checkKeys(prefix string, fields map[string]json.RawMessage, required, optional []string) error {
	for _, k := range required {
		v, ok := fields[k]
		if !ok || string(bytes.TrimSpace(v)) == "null" {
			return fmt.Errorf("%s%s is required", prefix, k)
		}
	}
	for _, k := range slices.Sorted(maps.Keys(fields)) {
		switch {
		case slices.Contains(required, k):
		case slices.Contains(optional, k):
			if string(bytes.TrimSpace(fields[k])) == "null" {
				return fmt.Errorf("%s%s must not be null", prefix, k)
			}
		default:
			return fmt.Errorf("%s%s is not a tool-price-card-v1 field", prefix, k)
		}
	}
	return nil
}

func (c *Card) validate() error {
	var errs []error
	fail := func(format string, args ...any) { errs = append(errs, fmt.Errorf(format, args...)) }
	if !vendorPattern.MatchString(c.Vendor) {
		fail("vendor %q must match %s", c.Vendor, vendorPattern)
	}
	if !capabilityPattern.MatchString(c.Capability) {
		fail("capability %q must match %s", c.Capability, capabilityPattern)
	}
	oneOf := func(field, v string, allowed ...string) {
		if !slices.Contains(allowed, v) {
			fail("%s %q must be one of %s", field, v, strings.Join(allowed, ", "))
		}
	}
	oneOf("resale_status", c.ResaleStatus, ResaleNative, ResaleManagedPartner, ResaleUnconfirmed, ResaleByokOnly)
	oneOf("trains_on_input", c.TrainsOnInput, TrainsNo, TrainsAIFeaturesOnly, TrainsYes)
	oneOf("results_retention", c.ResultsRetention, RetentionNone, RetentionMetadata, RetentionFull)
	oneOf("cacheable", c.Cacheable, CacheNone, CacheOrg, CacheGlobal)
	oneOf("eval_export", c.EvalExport, EvalExportAllowed, EvalExportForbidden)
	// A managed partner has its agreement on record; without one a
	// third-party vendor is unconfirmed or byok_only.
	switch {
	case c.ResaleStatus == ResaleManagedPartner && strings.TrimSpace(c.ResaleTermsRef) == "":
		fail("resale_terms_ref is required on a managed_partner card")
	case (c.ResaleStatus == ResaleUnconfirmed || c.ResaleStatus == ResaleByokOnly) && c.ResaleTermsRef != "":
		fail("a %s card has no resale_terms_ref; a signed agreement makes it managed_partner", c.ResaleStatus)
	}
	if u, err := url.Parse(c.TermsURL); err != nil || u.Scheme != "https" || u.Host == "" {
		fail("terms_url %q must be an absolute https URL", c.TermsURL)
	}
	if _, err := parseDate(c.TermsCheckedAt); err != nil {
		fail("terms_checked_at %q must be a YYYY-MM-DD date", c.TermsCheckedAt)
	}
	if c.PriceEffectiveUntil != "" {
		if _, err := parseDate(c.PriceEffectiveUntil); err != nil {
			fail("price_effective_until %q must be a YYYY-MM-DD date", c.PriceEffectiveUntil)
		}
	}
	for _, r := range c.Regions {
		if strings.TrimSpace(r) == "" {
			fail("regions has an empty entry")
		}
	}
	if len(c.SKUs) == 0 {
		fail("skus must list at least one SKU")
	}
	// A third-party card a managed call can be admitted at (unconfirmed or
	// managed_partner, no owner_price_pending) carries a price on at least one
	// SKU, so every admitted managed call is billed. A SKU whose cost comes
	// from the vendor's result is priced.
	if (c.ResaleStatus == ResaleUnconfirmed || c.ResaleStatus == ResaleManagedPartner) && !c.OwnerPricePending &&
		len(c.SKUs) > 0 && !slices.ContainsFunc(c.SKUs, func(s SKU) bool { return s.USDPerUnit != 0 || s.CostFromResultMeta }) {
		fail("a %s card prices every SKU at 0; set owner_price_pending until a price is recorded", c.ResaleStatus)
	}
	seen := map[string]bool{}
	for _, s := range c.SKUs {
		if err := s.validate(); err != nil {
			fail("sku %q: %v", s.ID, err)
		}
		if seen[s.ID] {
			fail("sku %q appears twice", s.ID)
		}
		seen[s.ID] = true
		if c.OwnerPricePending && s.USDPerUnit != 0 {
			fail("sku %q costs %v but owner_price_pending says no price is recorded; price it at 0", s.ID, s.USDPerUnit)
		}
	}
	return errors.Join(errs...)
}

func (s *SKU) validate() error {
	switch {
	case strings.TrimSpace(s.ID) == "":
		return errors.New("sku is empty")
	case !slices.Contains(units, s.Unit):
		return fmt.Errorf("unit %q must be one of %s", s.Unit, strings.Join(units, ", "))
	case s.USDPerUnit < 0 || math.IsNaN(s.USDPerUnit) || math.IsInf(s.USDPerUnit, 0):
		return fmt.Errorf("usd_per_unit %v must be a finite number >= 0", s.USDPerUnit)
	case s.MinUnits != nil && *s.MinUnits < 0:
		return errors.New("min_units must be >= 0")
	case s.MaxUSDPerCall != nil && *s.MaxUSDPerCall < 0:
		return errors.New("max_usd_per_call must be >= 0")
	case s.Tier != "" && strings.TrimSpace(s.Tier) == "":
		return errors.New("tier is blank")
	}
	return nil
}

// parseDate reads a YYYY-MM-DD calendar date.
func parseDate(s string) (time.Time, error) {
	return time.Parse(time.DateOnly, s)
}
