#!/usr/bin/env python3
"""
ISC -> DLRG Cloud Kalender-Abgleich (DLRG Dinslaken)

Holt die Termine aus dem iCal-Export der Homepage (ISC) und traegt sie in den
Cloud-Kalender "DLRG DIN oeffentlich" ein.

Regeln:
- Nur kommende Termine werden eingetragen bzw. aktualisiert.
- Termine, deren Titel einen Begriff aus AUSSCHLIESSEN enthalten, kommen nicht rein
  (bereits eingetragene werden wieder entfernt).
- Abgesagte Termine (Titel beginnt mit "Abgesagt") werden entfernt, wenn
  ABGESAGTE_ENTFERNEN = True.
- Verschwindet ein Termin aus dem ISC, wird er auch im Cloud-Kalender entfernt.
- Eigene Termine im Cloud-Kalender werden nicht angefasst. Ausnahme: DOPPELTE.
  Taucht im ISC ein neuer Termin auf und steht am selben Tag ein eigener Termin
  im Kalender, dessen Titel ein gemeinsames Stichwort hat (z. B. "WRD 403" und
  "Modul 403 ...", "SAN B" und "Sanitaetsausbildung B"), wird der eigene Termin
  geloescht - der ISC-Termin ersetzt ihn.
  Soll ein eigener Termin trotzdem bleiben: in die Notiz "ISC: behalten" schreiben.
  Passt die Automatik einmal nicht: in die Notiz des eigenen Termins
  "ISC: <Stichwort>" schreiben (z. B. "ISC: 403"), dann wird genau danach gesucht.

Zugangsdaten kommen aus Umgebungsvariablen (bei GitHub: "Secrets"):
  CALDAV_URL       Adresse der DLRG Cloud, z. B. https://dlrg.cloud
                   (der Kalender wird ueber KALENDER_NAME gesucht)
  CALDAV_USER      DLRG-Benutzername
  CALDAV_PASSWORD  App-Passwort aus der DLRG Cloud
Optional: TESTLAUF=1  -> zeigt nur an, was passieren wuerde, aendert nichts.
"""

import os

# ===================== Einstellungen =====================
ISC_URL = os.environ.get("ISC_URL") or "https://dinslaken.dlrg.de/die-ortsgruppe/termine/icaltermine/"
AUSSCHLIESSEN = [
    "Aufsicht Wassergymnastik",
    "Kurs Wassergewöhnung",
    "Rettungsschwimmabzeichen",
    "Schwimmen lernen für Erwachsene",
]
ABGESAGTE_ENTFERNEN = True
# Name des Cloud-Kalenders (wie er in der DLRG Cloud links angezeigt wird)
KALENDER_NAME = os.environ.get("KALENDER_NAME") or "DLRG DIN öffentlich"
DOPPELTE_ENTFERNEN = True
# Abkuerzungen in eigenen Terminen -> Wortanfang im ISC-Titel
ABKUERZUNGEN = {
    "san": "sanit",
    "wrd": "modul",
    "a.": "am",
}
# Woerter, die allein KEIN Doppel-Treffer sind
FUELLWOERTER = {
    "kurs", "dlrg", "jugend", "und", "der", "die", "das", "für", "fuer", "mit", "am",
    "im", "an", "zum", "zur", "von", "termin", "abend", "tag", "og", "b", "a", "c",
    "bronze", "silber", "gold", "teil", "freitag", "samstag", "sonntag", "aufbau",
}
# =========================================================

import base64
import datetime as dt
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
PREFIX = "isc-"
TESTLAUF = os.environ.get("TESTLAUF", "").strip() not in ("", "0", "false", "False")


# ---------- iCal-Hilfsfunktionen ----------
def unfold(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n[ \t]", "", text)


def blocks(text, name):
    return re.findall(rf"^BEGIN:{name}\n.*?^END:{name}$", text, re.S | re.M)


def prop(block, name):
    m = re.search(rf"^{name}(;[^:\n]*)?:(.*)$", block, re.M)
    if not m:
        return None, None
    return (m.group(1) or ""), m.group(2).strip()


def unescape(v):
    return (v or "").replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def parse_dt(params, value):
    """Liefert ein zeitzonenbewusstes datetime (Ganztags -> 00:00 Berlin)."""
    if value is None:
        return None
    params = params or ""
    if re.fullmatch(r"\d{8}", value):
        d = dt.datetime.strptime(value, "%Y%m%d")
        return d.replace(tzinfo=BERLIN)
    if value.endswith("Z"):
        return dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
    m = re.search(r"TZID=([^;:]+)", params)
    tz = BERLIN
    if m:
        try:
            tz = ZoneInfo(m.group(1).strip('"'))
        except Exception:
            tz = BERLIN
    return dt.datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=tz)


def event_info(block):
    uid = prop(block, "UID")[1]
    summary = unescape(prop(block, "SUMMARY")[1])
    start = parse_dt(*prop(block, "DTSTART"))
    end = parse_dt(*prop(block, "DTEND")) or start
    rrule = prop(block, "RRULE")[1]
    last = end
    if rrule:
        m = re.search(r"UNTIL=(\d{8}(T\d{6}Z?)?)", rrule)
        last = parse_dt("", m.group(1)) if m else dt.datetime.max.replace(tzinfo=dt.timezone.utc)
    return {"uid": uid, "summary": summary, "start": start, "end": end, "last": last, "rrule": rrule}


def woerter(text):
    t = text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    t = t.replace("a. ", "am ")
    return [w for w in re.split(r"[^a-z0-9]+", t) if w]


def gemeinsames_stichwort(eigen, isc):
    """True, wenn beide Titel ein aussagekraeftiges Wort gemeinsam haben."""
    if re.search(r"\s(&|\+|und)\s", eigen.lower()):
        return False  # kombinierte Termine (z. B. "LV-Uebung & SAN-Fortbildung") nie automatisch
    isc_w = woerter(isc)
    eigen_w = woerter(eigen)
    eigen_n = {w for w in eigen_w if w.isdigit() and len(w) >= 2}
    isc_n = {w for w in isc_w if w.isdigit() and len(w) >= 2}
    if eigen_n and isc_n and not (eigen_n & isc_n):
        return False  # z. B. "WRD 401" ist nicht "Modul 403"
    for w in eigen_w:
        if w in FUELLWOERTER:
            continue
        ziel = ABKUERZUNGEN.get(w, w)
        if ziel in FUELLWOERTER:
            continue
        if w.isdigit():
            if len(w) >= 2 and w in isc_w:
                return True
            continue
        if len(ziel) < 4:
            continue
        if any(x == ziel or x.startswith(ziel) for x in isc_w):
            return True
    return False


def safe_name(uid):
    return PREFIX + re.sub(r"[^A-Za-z0-9._-]", "_", uid) + ".ics"


# ---------- CalDAV ----------
class CalDAV:
    def __init__(self, url, user, pw):
        self.url = url if url.endswith("/") else url + "/"
        tok = base64.b64encode(f"{user}:{pw}".encode()).decode()
        self.auth = f"Basic {tok}"

    def req(self, method, url, body=None, headers=None):
        h = {"Authorization": self.auth}
        h.update(headers or {})
        data = body.encode("utf-8") if isinstance(body, str) else body
        r = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def list_all(self):
        """Alle Termine im Kalender: {Dateiname: iCal-Text}"""
        body = ('<?xml version="1.0" encoding="utf-8"?>'
                '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
                '<d:prop><d:getetag/><c:calendar-data/></d:prop>'
                '<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"/></c:comp-filter></c:filter>'
                '</c:calendar-query>')
        st, txt = self.req("REPORT", self.url, body, {"Depth": "1", "Content-Type": "application/xml; charset=utf-8"})
        if st != 207:
            raise SystemExit(f"FEHLER: Kalender nicht lesbar (HTTP {st}). Adresse/Benutzer/App-Passwort pruefen.\n{txt[:500]}")
        ns = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}
        out = {}
        for r in ET.fromstring(txt).findall("d:response", ns):
            href = urllib.parse.unquote(r.find("d:href", ns).text)
            data = r.find(".//c:calendar-data", ns)
            out[href.rstrip("/").split("/")[-1]] = data.text if data is not None and data.text else ""
        return out

    def put(self, name, ical):
        return self.req("PUT", self.url + urllib.parse.quote(name), ical, {"Content-Type": "text/calendar; charset=utf-8"})

    def delete(self, name):
        return self.req("DELETE", self.url + urllib.parse.quote(name))


def kalender_ordner(dav, root):
    ns = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}
    hdr = {"Depth": "0", "Content-Type": "application/xml; charset=utf-8"}
    b1 = '<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>'
    st, txt = dav.req("PROPFIND", root + "/remote.php/dav/", b1, hdr)
    if st == 401:
        raise SystemExit("FEHLER: Anmeldung fehlgeschlagen (401). Benutzername/App-Passwort pruefen.")
    el = ET.fromstring(txt).find(".//d:current-user-principal/d:href", ns) if st == 207 else None
    if el is None:
        raise SystemExit(f"FEHLER: Benutzer in der Cloud nicht gefunden (HTTP {st}).")
    b2 = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
          '<d:prop><c:calendar-home-set/></d:prop></d:propfind>')
    st, txt = dav.req("PROPFIND", root + el.text, b2, hdr)
    el2 = ET.fromstring(txt).find(".//c:calendar-home-set/d:href", ns) if st == 207 else None
    if el2 is None:
        raise SystemExit(f"FEHLER: Kalender-Ordner nicht gefunden (HTTP {st}).")
    h = el2.text if el2.text.startswith("http") else root + el2.text
    return h if h.endswith("/") else h + "/"


def finde_kalender(basis, user, pw, name):
    """Sucht in der Cloud den Kalender mit dem angegebenen Namen und liefert seine CalDAV-Adresse."""
    p = urllib.parse.urlsplit(basis.strip())
    root = f"{p.scheme}://{p.netloc}"
    pfad = p.path
    if "/remote.php/dav/calendars/" in pfad and "?" not in basis and "public" not in pfad:
        return basis if basis.endswith("/") else basis + "/"
    tmp = CalDAV(root + "/", user, pw)
    home = f"{root}/remote.php/dav/calendars/{urllib.parse.quote(user)}/"
    body = ('<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:" '
            'xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><d:displayname/><d:resourcetype/>'
            '</d:prop></d:propfind>')
    st, txt = tmp.req("PROPFIND", home, body, {"Depth": "1", "Content-Type": "application/xml; charset=utf-8"})
    if st == 404:
        # Benutzer-ID weicht vom Login ab -> Kalender-Ordner ueber den Server ermitteln
        home = kalender_ordner(tmp, root)
        st, txt = tmp.req("PROPFIND", home, body, {"Depth": "1", "Content-Type": "application/xml; charset=utf-8"})
    if st == 401:
        raise SystemExit("FEHLER: Anmeldung fehlgeschlagen (401). Benutzername/App-Passwort pruefen.")
    if st != 207:
        raise SystemExit(f"FEHLER: Kalenderliste nicht lesbar (HTTP {st}) unter {home}\n{txt[:300]}")
    ns = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}
    gefunden = []
    def norm(x): return re.sub(r"\s+", " ", (x or "").strip().lower())
    for r in ET.fromstring(txt).findall("d:response", ns):
        if r.find(".//c:calendar", ns) is None:
            continue
        dn = r.find(".//d:displayname", ns)
        dn = dn.text if dn is not None else ""
        href = r.find("d:href", ns).text
        gefunden.append(dn)
        if norm(dn) == norm(name):
            url = href if href.startswith("http") else root + href
            print(f"Kalender gefunden: {dn}")
            return url if url.endswith("/") else url + "/"
    raise SystemExit(f"FEHLER: Kalender '{name}' nicht gefunden. Vorhandene Kalender: {', '.join(gefunden) or '(keine)'}")


def build_calendar(tz_blocks, ev_block, new_uid):
    ev = re.sub(r"^UID:.*$", f"UID:{new_uid}", ev_block, count=1, flags=re.M)
    ev = ev.replace("END:VEVENT", "X-ISC-SYNC:1\nEND:VEVENT")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//DLRG Dinslaken//ISC-Sync//DE", "CALSCALE:GREGORIAN"]
    text = "\n".join(lines) + "\n" + "\n".join(tz_blocks) + ("\n" if tz_blocks else "") + ev + "\nEND:VCALENDAR\n"
    return text.replace("\n", "\r\n")


def main():
    now = dt.datetime.now(dt.timezone.utc)
    url, user, pw = (os.environ.get(k, "").strip() for k in ("CALDAV_URL", "CALDAV_USER", "CALDAV_PASSWORD"))
    if not (url and user and pw):
        raise SystemExit("FEHLER: CALDAV_URL, CALDAV_USER oder CALDAV_PASSWORD fehlt.")

    # 1) ISC-Termine holen
    with urllib.request.urlopen(urllib.request.Request(ISC_URL, headers={"User-Agent": "DLRG-ISC-Sync"}), timeout=60) as r:
        feed = unfold(r.read().decode("utf-8", "replace"))
    if "BEGIN:VCALENDAR" not in feed:
        raise SystemExit("FEHLER: Der ISC-Link liefert keinen Kalender. Abbruch, es wird nichts geloescht.")
    tz_blocks = blocks(feed, "VTIMEZONE")
    events = blocks(feed, "VEVENT")
    if not events:
        raise SystemExit("FEHLER: Im ISC-Kalender sind keine Termine. Sicherheitshalber Abbruch, es wird nichts geloescht.")

    feed_names, wanted, unwanted = set(), {}, set()
    isc_future = []  # fuer Doppel-Abgleich
    for b in events:
        info = event_info(b)
        if not info["uid"] or not info["start"]:
            continue
        name = safe_name(info["uid"])
        feed_names.add(name)
        s = info["summary"]
        if any(a.lower() in s.lower() for a in AUSSCHLIESSEN):
            unwanted.add(name); continue
        if ABGESAGTE_ENTFERNEN and s.lower().startswith("abgesagt"):
            unwanted.add(name); continue
        if info["last"] < now:
            continue  # vergangen: nicht neu eintragen, aber auch nicht loeschen
        wanted[name] = build_calendar(tz_blocks, b, PREFIX + info["uid"] + "@dinslaken.dlrg.de")
        isc_future.append(info)

    # 2) Cloud-Kalender lesen
    cal = CalDAV(finde_kalender(url, user, pw, KALENDER_NAME), user, pw)
    existing = cal.list_all()

    stats = {"neu": 0, "aktualisiert": 0, "entfernt": 0, "doppelt": 0, "fehler": 0}
    def log(msg): print(("[TESTLAUF] " if TESTLAUF else "") + msg)

    # 3) Eintragen / aktualisieren
    for name, ical in wanted.items():
        summ = unescape(prop(ical.replace("\r\n", "\n"), "SUMMARY")[1])
        old = existing.get(name)
        if old is not None and unfold(old).replace("\r\n", "\n").strip() == ical.replace("\r\n", "\n").strip():
            continue
        kind = "aktualisiert" if old is not None else "neu"
        if TESTLAUF:
            log(f"{kind}: {summ}"); stats[kind] += 1; continue
        st, txt = cal.put(name, ical)
        if st in (200, 201, 204):
            stats[kind] += 1; log(f"{kind}: {summ}")
        else:
            stats["fehler"] += 1; log(f"FEHLER {st} bei '{summ}': {txt[:200]}")

    # 4) ISC-Termine entfernen, die es nicht mehr gibt bzw. ausgeschlossen/abgesagt sind
    for name, data in existing.items():
        if not name.startswith(PREFIX):
            continue  # eigene Termine nie anfassen
        if name in feed_names and name not in unwanted:
            continue
        summ = unescape(prop(unfold(data), "SUMMARY")[1]) if data else name
        if TESTLAUF:
            log(f"entfernt: {summ}"); stats["entfernt"] += 1; continue
        st, _ = cal.delete(name)
        if st in (200, 204, 404):
            stats["entfernt"] += 1; log(f"entfernt: {summ}")
        else:
            stats["fehler"] += 1; log(f"FEHLER {st} beim Entfernen von '{summ}'")

    # 5) Doppelte eigene Termine entfernen (ISC-Termin ersetzt sie)
    for name, data in existing.items():
        if name.startswith(PREFIX) or not data:
            continue
        d = unfold(data)
        evs = blocks(d, "VEVENT")
        if len(evs) != 1 or prop(evs[0], "RRULE")[1]:
            continue  # Serien und Sonderfaelle nie automatisch loeschen
        b = evs[0]
        desc = unescape(prop(b, "DESCRIPTION")[1])
        m = re.search(r"^\s*ISC:\s*(.+?)\s*$", desc, re.M | re.I)
        key = m.group(1).strip().lower() if m else None
        if key in ("behalten", "nein", "bleibt"):
            continue
        if key is None and not DOPPELTE_ENTFERNEN:
            continue
        start = parse_dt(*prop(b, "DTSTART"))
        if not start or start < now - dt.timedelta(days=1):
            continue  # vergangene eigene Termine bleiben
        day = start.astimezone(BERLIN).date()
        summ = unescape(prop(b, "SUMMARY")[1])
        hit = None
        for i in isc_future:
            if i["start"].astimezone(BERLIN).date() != day:
                continue
            if key is not None:
                if key in i["summary"].lower():
                    hit = i; break
            elif gemeinsames_stichwort(summ, i["summary"]):
                hit = i; break
        if not hit:
            continue
        if TESTLAUF:
            log(f"doppelt, eigener Termin entfernt: '{summ}' -> ersetzt durch ISC '{hit['summary']}'"); stats["doppelt"] += 1; continue
        st, _ = cal.delete(name)
        if st in (200, 204, 404):
            stats["doppelt"] += 1; log(f"doppelt, eigener Termin entfernt: '{summ}' -> ersetzt durch ISC '{hit['summary']}'")
        else:
            stats["fehler"] += 1; log(f"FEHLER {st} beim Entfernen von '{summ}'")

    print(f"\nFertig: {stats['neu']} neu, {stats['aktualisiert']} aktualisiert, {stats['entfernt']} entfernt, "
          f"{stats['doppelt']} doppelte ersetzt, {stats['fehler']} Fehler.")
    if stats["fehler"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
