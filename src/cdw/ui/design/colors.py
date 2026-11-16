"""Pure color calculations; semantic roles and blend choices belong to spec."""

CHANNEL_MAX = 255
CHANNEL_OFFSETS = (1, 3, 5)
LUMINANCE_WEIGHTS = (0.2126, 0.7152, 0.0722)
SRGB_THRESHOLD = 0.04045
SRGB_LINEAR_DIVISOR = 12.92
SRGB_OFFSET = 0.055
SRGB_DIVISOR = 1.055
SRGB_EXPONENT = 2.4
CONTRAST_OFFSET = 0.05
TEXT_CONTRAST = 4.5
CONTRAST_STEPS = 20
CONTRAST_CANDIDATES = ("#000000", "#ffffff")


def _channels(color: str) -> tuple[int, ...]:
    return tuple(int(color[offset:offset + 2], 16) for offset in CHANNEL_OFFSETS)


def blend(base: str, tint: str, weight: float) -> str:
    return "#" + "".join(f"{round(a + (b - a) * weight):02x}" for a, b in zip(_channels(base), _channels(tint)))


def _luminance(color: str) -> float:
    channels = (value / CHANNEL_MAX for value in _channels(color))
    linear = (value / SRGB_LINEAR_DIVISOR if value <= SRGB_THRESHOLD
              else ((value + SRGB_OFFSET) / SRGB_DIVISOR) ** SRGB_EXPONENT for value in channels)
    return sum(value * weight for value, weight in zip(linear, LUMINANCE_WEIGHTS))


def contrast(first: str, second: str) -> float:
    lower, higher = sorted((_luminance(first), _luminance(second)))
    return (higher + CONTRAST_OFFSET) / (lower + CONTRAST_OFFSET)


def contrasting_text(background: str) -> str:
    return max(CONTRAST_CANDIDATES, key=lambda color: contrast(color, background))


def legible_text(preferred: str, background: str) -> str:
    target = contrasting_text(background)
    for step in range(CONTRAST_STEPS + 1):
        candidate = blend(preferred, target, step / CONTRAST_STEPS)
        if contrast(candidate, background) >= TEXT_CONTRAST:
            return candidate
    return target
