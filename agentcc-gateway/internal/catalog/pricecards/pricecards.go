// Package pricecards holds the price cards bundled with the gateway: one
// <provider_id>.json per direct-provider managed source (config.yaml
// catalog_source: price_card), read by catalog/sources when the provider
// names no price_card_file. Card prices are list prices the operator confirms;
// scripts/update-provider-catalogs.sh refreshes a card's model details and
// never writes a price.
package pricecards

import "embed"

// FS is the bundled cards, at "<provider_id>.json".
//
//go:embed *.json
var FS embed.FS
