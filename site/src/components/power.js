// Power model for a tail-vs-head difference in mean MWT. Mirrors pipeline/causal.py:model_se.
// vc: variance components {video, user, residual} in s^2, estimated from the random log.

/** SE with `videos` per arm and `ipv` impressions per video. users = null: every user is in both arms. */
export function modelSE(vc, videos, ipv, users = null, deff = 1, cuped = 0) {
  const n = videos * ipv;
  if (users == null) return Math.sqrt(2 * (vc.video / videos + (vc.user + vc.residual) / n));
  const nonVideo = (vc.user * deff) / (users / 2) + vc.residual / n;
  return Math.sqrt(2 * (vc.video / videos + (1 - cuped) * nonVideo));
}

/** Videos per arm for a target SE, other inputs fixed. Infinity if more videos cannot get there. */
export function videosFor(vc, se, ipv, users = null, deff = 1, cuped = 0) {
  if (users == null) return (vc.video + (vc.user + vc.residual) / ipv) / (se ** 2 / 2);
  const room = se ** 2 / 2 - ((1 - cuped) * vc.user * deff) / (users / 2);
  return room > 0 ? (vc.video + ((1 - cuped) * vc.residual) / ipv) / room : Infinity;
}

/** Users for a target SE in the user split, other inputs fixed. Infinity if more users cannot get there. */
export function usersFor(vc, se, videos, ipv, deff = 1, cuped = 0) {
  const room = se ** 2 / 2 - vc.video / videos - ((1 - cuped) * vc.residual) / (videos * ipv);
  return room > 0 ? (2 * (1 - cuped) * vc.user * deff) / room : Infinity;
}

/** Standard normal quantile (Acklam's rational approximation, relative error below 1.2e-9). */
export function qnorm(p) {
  const a = [-39.69683028665376, 220.9460984245205, -275.9285104469687, 138.357751867269, -30.66479806614716,
    2.506628277459239];
  const b = [-54.47609879822406, 161.5858368580409, -155.6989798598866, 66.80131188771972, -13.28068155288572];
  const c = [-0.007784894002430293, -0.3223964580411365, -2.400758277161838, -2.549732539343734, 4.374664141464968,
    2.938163982698783];
  const d = [0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416];
  const lo = 0.02425;
  if (p < lo) {
    const q = Math.sqrt(-2 * Math.log(p));
    return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
      ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  }
  if (p > 1 - lo) return -qnorm(1 - p);
  const q = p - 0.5;
  const r = q * q;
  return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q /
    (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
}

/** MDE multiplier for a two-sided test: z(1 - alpha / 2) + z(power). */
export const zSum = (alpha, power) => qnorm(1 - alpha / 2) + qnorm(power);
