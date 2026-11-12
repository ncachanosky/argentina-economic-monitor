# Map geometry

`latam_50m.json`: Latin America and the Caribbean as SVG paths (Mercator, 520 px wide), keyed by
ISO 3166-1 alpha-3 code. Built from Natural Earth 1:50m countries (public domain) via the
`world-atlas` package (countries-50m.json, v2.0.2), with d3-geo's `geoMercator().fitWidth(520, …)`
and coordinates rounded to 0.1 px. French Guiana (GUF) and the Caribbean Netherlands (BES) are
split out of France and the Netherlands; the Islas Malvinas are drawn as part of Argentina (ARG);
South Georgia is left out. Used by the Social Mobility Index card.
