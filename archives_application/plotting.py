"""Shared Plotly embedding settings for chart pages."""

from plotly.offline import get_plotlyjs_version


PLOTLY_CDN_URL = f"https://cdn.plot.ly/plotly-{get_plotlyjs_version()}.min.js"
