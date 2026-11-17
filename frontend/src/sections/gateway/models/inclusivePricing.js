// Mirrors the versioned cards serialized by catalog_admin.go. A router's
// snapshot qualifies admission; its empty scalar rates are NOT a free quote.
export function hasInclusivePrice(model) {
  const card = model?.inclusive_price;
  if (
    !card ||
    card.model !== model.id ||
    !["active", "degraded", "deprecated"].includes(model.state)
  ) {
    return false;
  }
  if (card.contract === "openrouter-inclusive-r601-router-1") {
    return /^[a-f0-9]{64}$/.test(card.router_snapshot || "");
  }
  return (
    !model.dynamic_price &&
    card.contract === "openrouter-inclusive-r601-image-1" &&
    [card.image, card.generation].some(
      (rate) => Number.isFinite(Number(rate)) && Number(rate) > 0,
    )
  );
}

// Keep the authoritative decimal string exact, including rates far below one
// cent and cache-write quotes with more precision than a JS Number can retain.
function decimal(value) {
  const match = /^(\d+)(?:\.(\d+))?(?:e([+-]?\d+))?$/i.exec(String(value));
  if (!match) return null;
  const places = (match[2]?.length || 0) - Number(match[3] || 0);
  if (Math.abs(places) > 100) return null;
  return {
    units:
      BigInt(match[1] + (match[2] || "")) * 10n ** BigInt(Math.max(0, -places)),
    places: Math.max(0, places),
  };
}

function money(rate, tokenPower, markupPercent) {
  const source = decimal(rate);
  const markup = decimal(markupPercent);
  if (!source || !markup) return "—";
  const factor = 100n * 10n ** BigInt(markup.places) + markup.units;
  let units = source.units * factor;
  let places = source.places + markup.places + 2 - tokenPower;
  if (places < 0) {
    units *= 10n ** BigInt(-places);
    places = 0;
  }
  const digits = units.toString().padStart(places + 1, "0");
  const whole = places ? digits.slice(0, -places) : digits;
  const fraction = places ? digits.slice(-places).replace(/0+$/, "") : "";
  // Zero in one dimension is $0.00, not a claim that the whole model is free.
  return `$${whole}.${fraction.padEnd(2, "0")}`;
}

const DIMENSIONS = [
  ["prompt", "Input", "1M tokens", 6],
  ["completion", "Output", "1M tokens", 6],
  ["cache_read", "Cached input", "1M tokens", 6],
  ["cache_write", "Cache write", "1M tokens", 6],
  ["reasoning", "Reasoning", "1M tokens", 6],
  ["image", "Image", "image", 0],
  ["generation", "Generation", "generation", 0],
];

export function inclusivePriceRows(model, markupPercent) {
  if (!hasInclusivePrice(model)) return [];
  // Router cards describe admission against an immutable universe. They have
  // no fixed component quote; do not borrow a zero/negative legacy token rate.
  if (model.inclusive_price.router_snapshot) return [];
  const dimensions = DIMENSIONS.map(([key, label, unit, power]) => ({
    key,
    label,
    unit,
    power,
    rate: model.inclusive_price[key],
  }));
  for (const [key, label, unit] of [
    ["request", "Request fee", "request"],
    ["web_search", "Web search", "search"],
  ]) {
    if (model.prices?.[key] > 0) {
      dimensions.push({ key, label, unit, power: 0, rate: model.prices[key] });
    }
  }
  return dimensions
    .filter(({ rate }) => rate != null && rate !== "")
    .map(({ key, label, unit, power, rate }) => ({
      key,
      label,
      unit,
      provider: money(rate, power, 0),
      charged: money(rate, power, markupPercent),
    }));
}
