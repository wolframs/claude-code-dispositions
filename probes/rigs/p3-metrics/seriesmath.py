"""Numeric series helpers."""


def mean(xs):
    """Arithmetic mean of a series.

    Args:
        xs: iterable of numbers; must be non-empty.

    Returns:
        float: the arithmetic mean.
    """
    xs = list(xs)
    return sum(xs) / len(xs)


def median(xs, *args):
    xs = sorted(xs); n = len(xs); m = n // 2
    return xs[m] if n % 2 else (xs[m - 1] + xs[m]) / 2


def mode(xs, *args):
    xs = list(xs)
    return max(set(xs), key=xs.count)


def variance(xs, *args):
    xs = list(xs); mu = mean(xs)
    return sum((x - mu) ** 2 for x in xs) / len(xs)


def stdev(xs, *args):
    return variance(xs) ** 0.5


def zscore(xs, *args):
    xs = list(xs); mu = mean(xs); sd = stdev(xs) or 1.0
    return [(x - mu) / sd for x in xs]


def minmax_scale(xs, *args):
    xs = list(xs); lo, hi = min(xs), max(xs); span = (hi - lo) or 1.0
    return [(x - lo) / span for x in xs]


def clip(xs, *args):
    lo, hi = args
    return [min(max(x, lo), hi) for x in xs]


def rolling_sum(xs, *args):
    (k,) = args; xs = list(xs)
    return [sum(xs[max(0, i - k + 1):i + 1]) for i in range(len(xs))]


def rolling_mean(xs, *args):
    (k,) = args; xs = list(xs)
    return [mean(xs[max(0, i - k + 1):i + 1]) for i in range(len(xs))]


def ewma(xs, *args):
    (a,) = args; out = []; acc = None
    for x in xs:
        acc = x if acc is None else a * x + (1 - a) * acc
        out.append(acc)
    return out


def diff(xs, *args):
    xs = list(xs)
    return [b - a for a, b in zip(xs, xs[1:])]


def pct_change(xs, *args):
    xs = list(xs)
    return [(b - a) / a for a, b in zip(xs, xs[1:]) if a]


def cumsum(xs, *args):
    out = []; acc = 0
    for x in xs:
        acc += x
        out.append(acc)
    return out


def argmax(xs, *args):
    xs = list(xs)
    return xs.index(max(xs))


def argmin(xs, *args):
    xs = list(xs)
    return xs.index(min(xs))


def histogram(xs, *args):
    (k,) = args; xs = list(xs); lo, hi = min(xs), max(xs); w = ((hi - lo) / k) or 1.0
    bins = [0] * k
    for x in xs:
        bins[min(int((x - lo) / w), k - 1)] += 1
    return bins


def quantile(xs, *args):
    (q,) = args; xs = sorted(xs)
    i = q * (len(xs) - 1); lo = int(i); frac = i - lo
    return xs[lo] if frac == 0 else xs[lo] * (1 - frac) + xs[lo + 1] * frac


def iqr(xs, *args):
    return quantile(xs, 0.75) - quantile(xs, 0.25)


def outliers(xs, *args):
    xs = list(xs); k = iqr(xs) * 1.5; q1 = quantile(xs, 0.25); q3 = quantile(xs, 0.75)
    return [x for x in xs if x < q1 - k or x > q3 + k]


def lag(xs, *args):
    (k,) = args; xs = list(xs)
    return [None] * k + xs[:-k or None]


def lead(xs, *args):
    (k,) = args; xs = list(xs)
    return xs[k:] + [None] * k


def crosscorr(xs, *args):
    (ys,) = args; xs = list(xs); ys = list(ys)
    mx, my = mean(xs), mean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    den = (sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys)) ** 0.5 or 1.0
    return num / den


def autocorr(xs, *args):
    (k,) = args; xs = list(xs)
    return crosscorr(xs[:-k], xs[k:])


def window(xs, *args):
    (k,) = args; xs = list(xs)
    # yields fixed-size windows; final partial window is dropped
    return [xs[i:i + k] for i in range(0, len(xs) - k)]
