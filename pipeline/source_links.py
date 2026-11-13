"""Where each non-file adapter reads its data, for the "Read from" line under a card.

The INDEC and datos.gob.ar files read by pipeline/sources/primary.py report their own URL in
data/source_files.json. The other adapters read a fixed file or an API; this table names it.
The time shown is the latest successful check of the card's series from that adapter
(data/status.json). Hand-kept, annual and Monitor-computed sources (manual, officials, efw,
vdem, x13) have no entry: their cards already say where the data come from.
"""
from __future__ import annotations

from .sources import (a3, bcra, bcra_balance, bcra_banks, bcra_cambiario, bcra_entities, bcra_fx, bcra_weekly,
                      efw, finanzas, itcrm, rem, riesgo, vdem)

REPO_FILES = "https://github.com/ncachanosky/argentina-economic-monitor/blob/main/"

ARGDATOS = "https://argentinadatos.com/"


def _file(url: str) -> str:
    return url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]


# adapter -> {publisher, name shown, url, page (optional), kind: file | api}
LINKS: dict[str, dict] = {
    "bcra": {"publisher": "BCRA", "name": "BCRA API (estadísticas monetarias v4.0)", "url": bcra.BASE_URL, "kind": "api"},
    "bcra_fx": {"publisher": "BCRA", "name": "BCRA API (estadísticas cambiarias v1.0)", "url": bcra_fx.BASE_URL, "kind": "api"},
    "bcra_balance": {"publisher": "BCRA", "name": _file(bcra_balance.URL), "url": bcra_balance.URL, "kind": "file"},
    "bcra_weekly": {"publisher": "BCRA", "name": _file(bcra_weekly.URL), "url": bcra_weekly.URL, "kind": "file"},
    "bcra_banks": {"publisher": "BCRA", "name": _file(bcra_banks.URL), "url": bcra_banks.URL, "kind": "file"},
    "bcra_cambiario": {"publisher": "BCRA", "name": _file(bcra_cambiario.URL), "url": bcra_cambiario.URL, "kind": "file"},
    "rem": {"publisher": "BCRA", "name": _file(rem.URL), "url": rem.URL, "kind": "file"},
    "itcrm": {"publisher": "BCRA", "name": _file(itcrm.URLS[0]), "url": itcrm.URLS[0], "page": itcrm.PAGE, "kind": "file"},
    "a3": {"publisher": "A3 Mercados", "name": "A3 Mercados API (closing prices)", "url": "https://www.a3mercados.com.ar/",
           "kind": "api"},
    "dolar": {"publisher": "ArgentinaDatos", "name": "ArgentinaDatos API (cotizaciones)", "url": ARGDATOS, "kind": "api"},
    "riesgo": {"publisher": "ArgentinaDatos", "name": "ArgentinaDatos API (riesgo país)", "url": ARGDATOS, "kind": "api"},
    "ustreasury": {"publisher": "US Treasury", "name": "Daily Treasury Bill Rates",
                   "url": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_bill_rates",
                   "kind": "api"},
}


def finanzas_link(sid: str) -> dict:
    """Secretaría de Finanzas: placements, the quarterly debt report or the monthly bulletin."""
    if sid.startswith("fin:col"):
        page, name = finanzas.PLACEMENTS_PAGE, "Colocaciones de deuda"
    elif sid.startswith("fin:hold") or sid.startswith("fin:q"):
        page, name = finanzas.QUARTERLY_PAGE, "Datos trimestrales de la deuda"
    else:
        page, name = finanzas.MONTHLY_PAGES[0], "Datos mensuales de la deuda"
    return {"publisher": "Secretaría de Finanzas", "name": name, "url": page, "kind": "file"}


def link_for(adapter: str, sid: str, st: dict | None = None) -> dict | None:
    """The file or service behind one series; `st` is its entry in data/status.json."""
    st = st or {}
    if adapter == "finanzas":
        return finanzas_link(sid)
    if adapter in ("manual", "officials"):
        # Hand-kept files in the repository: link to the file on GitHub.
        name = "officials" if adapter == "officials" else sid.partition("#")[0]
        return {"publisher": "Argentina Economic Monitor (hand-kept file)", "name": f"{name}.csv",
                "url": f"{REPO_FILES}data/manual/{name}.csv", "kind": "file"}
    if adapter == "bcra_entities":
        # One archive per month; the latest month read is the one named.
        ym = (st.get("last_obs") or "")[:7].replace("-", "")
        url = bcra_entities.BASE.format(ym=ym) if ym else None
        return {"publisher": "BCRA", "name": url.rsplit("/", 1)[-1] if url else "Entidades (monthly archive)",
                "url": url or "https://www.bcra.gob.ar/", "kind": "file"} if url else None
    if adapter == "efw":
        return {"publisher": "Fraser Institute", "name": efw.URL.rsplit("/", 1)[-1], "url": efw.URL, "kind": "file"}
    if adapter == "maddison":
        return {"publisher": "Maddison Project (University of Groningen)", "name": "Maddison Project Database releases (kept in the repository)",
                "url": f"{REPO_FILES}data/manual/maddison/", "page": "https://www.rug.nl/ggdc/historicaldevelopment/maddison/", "kind": "file"}
    if adapter == "revisions":
        return {"publisher": "INDEC (each release, as published)", "name": "gdp_vintages.csv, emae_trend_vintages.csv",
                "url": f"{REPO_FILES}data/manual/", "kind": "file"}
    if adapter == "recession":
        # Computed by the Monitor from INDEC's EMAE: link to the model's code.
        return {"publisher": "Argentina Economic Monitor (computed)", "name": "recession.py (the model)",
                "url": f"{REPO_FILES}pipeline/sources/recession.py", "kind": "file"}
    if adapter == "smi":
        return {"publisher": "Archbridge Institute", "name": "Social Mobility Index (edition file kept in the repository)",
                "url": f"{REPO_FILES}data/manual/", "page": "https://www.archbridgeinstitute.org/social-mobility-index/", "kind": "file"}
    if adapter == "vdem":
        # One entry for all the indices (a file each), so cards with several stay short.
        return {"publisher": "V-Dem, via Our World in Data", "name": "V-Dem indices (Our World in Data)",
                "url": "https://ourworldindata.org/grapher/" + vdem.INDICES["libdem"], "kind": "api"}
    if adapter == "riesgo" and sid != "riesgo:embi":
        return {"publisher": "Banco Central de la República Dominicana", "name": _file(riesgo.BCRD),
                "url": riesgo.BCRD, "page": riesgo.BCRD_PAGE, "kind": "file"}
    return LINKS.get(adapter)
