// Planning-only mirror of tracer/services/eval_tasks/sampling/stats.py.
// Both implementations consume sampling_stats_vectors.json in their tests.
export const Z_VALUES = {
  0.8: 1.2815515655446004,
  0.9: 1.644853626951472,
  0.95: 1.959963984540054,
  0.99: 2.5758293035489004,
};
export const ALLOCATIONS = ["proportional", "balanced", "neyman"];
const count = (value) => {
  if (!Number.isSafeInteger(value) || value < 0)
    throw new Error("Counts must be non-negative safe integers.");
  return value;
};
const proportion = (value) => {
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    value < 0 ||
    value > 1
  )
    throw new Error("Proportions must be numbers from 0 to 1.");
  return value;
};
const zValue = (confidence) => {
  if (!Object.hasOwn(Z_VALUES, confidence))
    throw new Error("Unsupported confidence level.");
  return Z_VALUES[confidence];
};
export const marginOfError = (n, population, p = 0.5, confidence = 0.95) => {
  count(n);
  count(population);
  proportion(p);
  const z = zValue(confidence);
  if (n > population) throw new Error("n cannot exceed the population.");
  if (n === 0) return null;
  if (n === population) return 0;
  return (
    z * Math.sqrt(((p * (1 - p)) / n) * ((population - n) / (population - 1)))
  );
};
export const requiredSampleSize = (
  margin,
  population,
  p = 0.5,
  confidence = 0.95,
) => {
  if (
    typeof margin !== "number" ||
    !Number.isFinite(margin) ||
    margin <= 0 ||
    margin >= 1
  )
    throw new Error("Margin must be between 0 and 1.");
  count(population);
  proportion(p);
  const z = zValue(confidence);
  if (population === 0) return 0;
  if (p * (1 - p) === 0) return 1;
  const zVariance = z * z * p * (1 - p);
  const exact =
    (zVariance * population) / (zVariance + margin * margin * (population - 1));
  let n = Math.max(1, Math.min(population, Math.ceil(exact)));
  if (n > 1 && marginOfError(n - 1, population, p, confidence) <= margin)
    n -= 1;
  if (marginOfError(n, population, p, confidence) > margin) n += 1;
  return n;
};
export const stratifiedMarginOfError = (cells, confidence = 0.95) => {
  const z = zValue(confidence);
  cells.forEach(([N, n, p]) => marginOfError(n, N, p, confidence));
  const total = cells.reduce((sum, [N]) => sum + N, 0);
  if (!total || cells.some(([N, n]) => N > 0 && n === 0)) return null;
  const variance = cells.reduce((sum, [N, n, p]) => {
    if (!N || n === N) return sum;
    return sum + ((((N / total) ** 2 * p * (1 - p)) / n) * (N - n)) / (N - 1);
  }, 0);
  return z * Math.sqrt(variance);
};
export const kishEffectiveN = (pairs) => {
  let first = 0;
  let second = 0;
  pairs.forEach(([weight, n]) => {
    count(n);
    if (typeof weight !== "number" || !Number.isFinite(weight) || weight <= 0)
      throw new Error("Design weights must be positive and finite.");
    first += n * weight;
    second += n * weight * weight;
  });
  return second ? (first * first) / second : null;
};
export const designEffect = (pairs) => {
  const effective = kishEffectiveN(pairs);
  return effective === null
    ? null
    : pairs.reduce((sum, [, n]) => sum + n, 0) / effective;
};
export const weightCv = (pairs) => {
  const effect = designEffect(pairs);
  return effect === null ? null : Math.sqrt(Math.max(0, effect - 1));
};
export const allocate = (
  populations,
  total,
  method = "proportional",
  minimum = 0,
  proportions = null,
) => {
  count(total);
  count(minimum);
  populations.forEach(count);
  if (!ALLOCATIONS.includes(method))
    throw new Error("Unsupported allocation method.");
  const anticipated = proportions ?? populations.map(() => 0.5);
  if (anticipated.length !== populations.length)
    throw new Error("Give one anticipated proportion per stratum.");
  anticipated.forEach(proportion);
  const weights = populations.map((N, i) => {
    if (method === "proportional") return N;
    if (method === "balanced") return N ? 1 : 0;
    return N * Math.sqrt(anticipated[i] * (1 - anticipated[i]));
  });
  const lows = populations.map((N) => Math.min(N, minimum));
  const floor = lows.reduce((sum, n) => sum + n, 0);
  if (total < floor)
    throw new Error(
      `The budget (${total}) is below the per-stratum minimum total (${floor}).`,
    );
  const target = Math.min(
    total,
    populations.reduce((sum, N, i) => sum + (weights[i] > 0 ? N : lows[i]), 0),
  );
  const level = (lambda) =>
    populations.reduce(
      (sum, N, i) =>
        sum +
        (weights[i] > 0
          ? Math.min(N, Math.max(lows[i], lambda * weights[i]))
          : lows[i]),
      0,
    );
  let values = [...lows];
  if (target > floor) {
    const free = populations
      .map((_, i) => i)
      .filter((i) => weights[i] > 0 && populations[i] > lows[i]);
    const points = [
      ...new Set(
        free.flatMap((i) => [
          lows[i] / weights[i],
          populations[i] / weights[i],
        ]),
      ),
    ].sort((a, b) => a - b);
    let previous = 0;
    let point = points[points.length - 1];
    for (const candidate of points) {
      if (level(candidate) >= target) {
        point = candidate;
        break;
      }
      previous = candidate;
    }
    let fixed = 0;
    let weightSum = 0;
    weights.forEach((w, i) => {
      if (w <= 0 || populations[i] === lows[i] || lows[i] / w >= point)
        fixed += lows[i];
      else if (populations[i] / w <= previous) fixed += populations[i];
      else weightSum += w;
    });
    const lambda = weightSum === 0 ? point : (target - fixed) / weightSum;
    values = weights.map((w, i) =>
      w > 0 ? Math.min(populations[i], Math.max(lows[i], lambda * w)) : lows[i],
    );
  }
  const result = values.map((v, i) =>
    Math.max(lows[i], Math.min(populations[i], Math.floor(v))),
  );
  const remainder = target - result.reduce((sum, n) => sum + n, 0);
  const ranked = values
    .map((_, i) => i)
    .filter((i) => result[i] < populations[i])
    .sort(
      (a, b) =>
        Math.floor((values[b] - result[b]) * 1e9 + 0.5) -
          Math.floor((values[a] - result[a]) * 1e9 + 0.5) || a - b,
    );
  if (remainder < 0 || remainder > ranked.length)
    throw new Error("Allocation rounding could not meet the budget.");
  ranked.slice(0, remainder).forEach((i) => {
    result[i] += 1;
  });
  return result;
};
