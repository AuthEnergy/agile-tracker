#!/usr/bin/env python3
"""Daily GB gas price (System Average Price, SAP) from free official sources.  Stdlib only.

    python3 fetch_gas.py

Sources, logging every attempt to cache/gas_diagnostics.txt:
  1) National Gas MIPI public SOAP service (no key) - SAP "Actual Day" item, in 3,000-day chunks
  2) ONS "System Average Price (SAP) of gas" XLSX (OGL v3, daily, National Gas data, irregular release)
Writes cache/sap.csv  (date, sap_p_per_kwh).  Needs check_elexon_price.py alongside (TLS context).
"""
import csv, io, os, re, sys, urllib.request, zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

import check_elexon_price as base

CACHE = "cache"
LOG = []
ASMX = "https://marketinformation.nationalgas.com/MIPIws-public/public/publicwebservice.asmx"
NS = "http://www.NationalGrid.com/MIPI/"
NAMES = ["SAP, Actual Day", "Sap, Actual Day", "System Average Price (SAP), Actual Day",
         "SAP Actual Day", "System Average Price, Actual Day", "SAP, Gas Day", "SAP"]
ONS = ("https://www.ons.gov.uk/file?uri=/economy/economicoutputandproductivity/output/datasets/"
       "systemaveragepricesapofgas/2026/systemaveragepriceofgasdataset051126.xlsx")


def note(m):
    LOG.append(m); print("  gas:", m, flush=True)


def fetch(url, data=None, headers=None, timeout=90):
    h = {"User-Agent": "agile-outlook/1.0"}; h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=base._CTX) as r:
        return r.read()


def soap(names, d0, d1):
    items = "".join(f"<string>{n}</string>" for n in names)
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>
<GetPublicationDataWM xmlns="{NS}"><reqObject><LatestFlag>N</LatestFlag><ApplicableForFlag>Y</ApplicableForFlag>
<FromDate>{d0:%Y-%m-%d}T00:00:00</FromDate><ToDate>{d1:%Y-%m-%d}T00:00:00</ToDate><DateType>GASDAY</DateType>
<PublicationObjectNameList>{items}</PublicationObjectNameList></reqObject></GetPublicationDataWM></soap:Body></soap:Envelope>"""
    raw = fetch(ASMX, body.encode(), {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": NS + "GetPublicationDataWM"})
    root = ET.fromstring(raw)
    out = {}
    for el in root.iter():
        if el.tag.endswith("PublicationObjectName"):
            pass
    # walk: each PublicationObject has a name and many PublicationObjectData
    for po in root.iter():
        if not po.tag.endswith("}CLSPublicationObject") and not po.tag.endswith("}PublicationObject"):
            continue
        nm = next((c.text for c in po if c.tag.endswith("PublicationObjectName")), "")
        for pd_ in po.iter():
            if pd_.tag.endswith("}CLSPublicationObjectData") or pd_.tag.endswith("}PublicationObjectData"):
                f = {c.tag.split("}")[1]: c.text for c in pd_}
                if f.get("Value") and f.get("ApplicableFor"):
                    out.setdefault(nm, {})[f["ApplicableFor"][:10]] = float(f["Value"])
    return out, raw


PORTAL = "https://data.nationalgas.com/api/find-gas-data-download"


def try_portal(days=1900):
    """National Gas data portal CSV download (no key).  PUBOB603 = SAP, Actual Day, p/kWh, 5y rolling history."""
    try:
        end = date.today(); start = end - timedelta(days=days)
        q = f"?ids=PUBOB603&applicableFor=Y&dateType=GASDAY&latestFlag=Y&dateFrom={start}&dateTo={end}&type=CSV"
        txt = fetch(PORTAL + q).decode("utf-8-sig")
        rows = {}
        for r in csv.DictReader(io.StringIO(txt)):
            try:
                d = datetime.strptime(r["Applicable For"][:10], "%d/%m/%Y").date().isoformat()
                rows[d] = float(r["Value"])
            except Exception:
                continue
        note(f"portal PUBOB603: {len(rows)} days" + (f" {min(rows)} -> {max(rows)}" if rows else f" reply {txt[:150]!r}"))
        return sorted(rows.items()), "National Gas data portal"
    except Exception as e:  # noqa
        note(f"portal: FAILED {e}")
        return None, None


def xlsx_rows(raw):
    z = zipfile.ZipFile(io.BytesIO(raw))
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")):
            ss.append("".join(t.text or "" for t in si.iter() if t.tag.endswith("}t")))
    for name in sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet")):
        rows = []
        for r in ET.fromstring(z.read(name)).iter():
            if not r.tag.endswith("}row"):
                continue
            cells = []
            for c in r:
                v = next((x.text for x in c if x.tag.endswith("}v")), None)
                if v is None:
                    cells.append(None); continue
                cells.append(ss[int(v)] if c.get("t") == "s" else v)
            rows.append(cells)
        yield name, rows


def try_ons():
    try:
        raw = fetch(ONS)
        note(f"ons xlsx: {len(raw)} bytes")
        best = []
        for name, rows in xlsx_rows(raw):
            note(f"  sheet {name}: {len(rows)} rows; first rows {rows[:4]}")
            out = []
            for r in rows:
                if len(r) < 2 or r[0] is None:
                    continue
                try:
                    d = (datetime(1899, 12, 30) + timedelta(days=float(r[0]))).date()
                    v = float(r[1])
                except Exception:
                    continue
                if 2000 < d.year < 2100:
                    out.append((d.isoformat(), v))
            if len(out) > len(best):
                best = out
        return best or None, "ONS SAP xlsx"
    except Exception as e:  # noqa
        note(f"ons xlsx: FAILED {e}")
        return None, None


def main():
    os.makedirs(CACHE, exist_ok=True)
    rows, src = try_portal()
    ons, _ = try_ons()
    if ons:   # older history from ONS fills anything before the portal's window
        have = dict(rows or [])
        for d, v in ons:
            have.setdefault(d, v)
        rows, src = sorted(have.items()), (src + " + ONS history") if src else "ONS SAP xlsx"
    open(os.path.join(CACHE, "gas_diagnostics.txt"), "w").write("\n".join(LOG))
    if rows:
        with open(os.path.join(CACHE, "sap.csv"), "w", newline="") as f:
            w = csv.writer(f); w.writerow(["date", "sap_p_per_kwh"]); w.writerows(rows)
        print(f"Saved cache/sap.csv from {src}: {len(rows)} days {rows[0][0]} -> {rows[-1][0]}; last {rows[-1][1]}")
    else:
        print("No gas source worked; see cache/gas_diagnostics.txt")


if __name__ == "__main__":
    main()
