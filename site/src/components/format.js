// Number formatting and delta colouring shared by every page. Minus signs are U+2212.

const MINUS = "−";

const fixed = (x, digits) => {
  const s = Math.abs(x).toFixed(digits);
  return Number(s) === 0 ? s : (x < 0 ? MINUS : "") + s;
};

const signed = (x, digits) => {
  const s = fixed(x, digits);
  return x > 0 && Number(Math.abs(x).toFixed(digits)) !== 0 ? `+${s}` : s;
};

/** Relative change as a signed percentage: -0.4712 -> "−47.1%". */
export const pct = (x, digits = 1) => `${signed(100 * x, digits)}%`;

/** Unsigned percentage for shares and rates: 0.106 -> "10.6%". */
export const share = (x, digits = 1) => `${fixed(100 * x, digits)}%`;

/** Interval in brackets, bounds without a forced plus sign: "[−49.5%, −44.7%]". */
export const ci = (lo, hi, digits = 1) => `[${fixed(100 * lo, digits)}%, ${fixed(100 * hi, digits)}%]`;

/** Estimate with interval: "−47.1% [−49.5%, −44.7%]". */
export const pctCI = (x, lo, hi, digits = 1) => `${pct(x, digits)} ${ci(lo, hi, digits)}`;

/** Percentage points: 0.011 -> "+1.1 pp". */
export const pp = (x, digits = 1) => `${signed(100 * x, digits)} pp`;
export const ppCI = (x, lo, hi, digits = 1) =>
  `${pp(x, digits)} [${fixed(100 * lo, digits)}, ${fixed(100 * hi, digits)}]`;

/** Plain number with a real minus sign. */
export const num = (x, digits = 2) => fixed(x, digits);
export const signedNum = (x, digits = 2) => signed(x, digits);

/** Seconds: 8.533 -> "8.53s". */
export const secs = (x, digits = 2) => `${fixed(x, digits)}s`;

/** Integer with thousands separators: 27285 -> "27,285". */
export const int = (x) => Math.round(x).toLocaleString("en-US");

/** p-value: "p < 0.001" or "p = 0.19". */
export const pval = (p) => (p < 0.001 ? "p < 0.001" : `p = ${p < 0.01 ? p.toFixed(3) : p.toFixed(2)}`);
export const qval = (q) => (q < 0.001 ? "q < 0.001" : `q = ${q < 0.01 ? q.toFixed(3) : q.toFixed(2)}`);

/** Metric value on its display scale: seconds, a rate as %, or per 1K impressions. */
export function value(x, unit, scale = 1) {
  if (unit === "s") return secs(x);
  if (unit === "rate") return share(x);
  return `${fixed(x * scale, 2)}`;
}

const HIGHER_IS_BETTER = new Set(["mwt", "long_view", "liked", "followed", "profile_entered", "next_mwt"]);

/** "good", "bad" or "" (interval includes zero), from the metric's direction. */
export function tone(metric, lo, hi) {
  if (lo <= 0 && hi >= 0) return "";
  const up = lo > 0;
  return up === HIGHER_IS_BETTER.has(metric) ? "good" : "bad";
}

/** Short metric names for tables and charts. */
export const METRIC = {
  mwt: "Meaningful watch time",
  early_skip: "Early-skip rate",
  hated: "Hates per 1K",
  long_view: "Long-view rate",
  liked: "Likes per 1K",
  followed: "Follows per 1K",
  profile_entered: "Profile enters per 1K"
};

export const VARIANT = {
  A: "A. Blanket",
  B5: "B. Gated, k = 5",
  B10: "B. Gated, k = 10",
  B20: "B. Gated, k = 20",
  B50: "B. Gated, k = 50",
  C: "C. Content-aware"
};

export const VERDICT = {
  ship: {label: "Ship", cls: "ship"},
  "A/B test": {label: "A/B test", cls: "test"},
  "don't ship": {label: "Don't ship", cls: "no"}
};

/** yyyymmdd (number or string) -> Date. */
export const day = (d) => {
  const s = String(d);
  return new Date(`${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}T00:00:00`);
};
