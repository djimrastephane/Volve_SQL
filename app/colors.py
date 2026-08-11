"""
colors.py

Standard petroleum-engineering color convention, applied consistently
across every chart in this app: oil green, water blue, gas red. Used
wherever a chart plots oil/water/gas as distinct series. For charts that
overlay multiple wells on a single stream (e.g. Well Comparison), the
stream's color anchors a sequential palette (light -> dark) so wells stay
distinguishable while the chart still reads as "this is an oil chart" /
"this is a water chart" at a glance.
"""

import plotly.express as px

OIL = "#2ca02c"
WATER = "#1f77b4"
GAS = "#d62728"

STREAM_COLOR_MAP = {"Oil": OIL, "Water": WATER, "Gas": GAS}

# Plotly named sequential scales, for shades() below, when overlaying several
# wells on a single stream (e.g. Well Comparison) - keeps the chart reading
# as "this is an oil chart" while wells stay distinguishable by shade.
OIL_SCALE = "Greens"
WATER_SCALE = "Blues"
GAS_SCALE = "Reds"
STREAM_SCALE_MAP = {"Oil": OIL_SCALE, "Gas": GAS_SCALE, "Water": WATER_SCALE}


def shades(scale_name: str, n: int) -> list[str]:
    """
    n distinct, readable shades from a sequential colorscale. Sampled from
    35%-95% of the scale, not 0%-100% - the light end of most sequential
    scales is near-white and unreadable against this app's dark theme.
    """
    if n <= 0:
        return []
    if n == 1:
        return [px.colors.sample_colorscale(scale_name, [0.75])[0]]
    return px.colors.sample_colorscale(scale_name, [0.35 + 0.6 * i / (n - 1) for i in range(n)])
