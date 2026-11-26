#!/usr/bin/env python3
"""
Erzeugt aus dem Cloud-Kalender "DLRG DIN oeffentlich" eine Terminliste im DLRG-Design
(docs/index.html), die per GitHub Pages veroeffentlicht und auf der Homepage eingebettet wird.

Zugangsdaten wie bei sync.py (CALDAV_URL, CALDAV_USER, CALDAV_PASSWORD).
Zum lokalen Testen: python liste.py datei1.ics datei2.ics ...
"""
import os

# ===================== Einstellungen =====================
MONATE_VORAUS = 12                 # wie weit in die Zukunft
MONATE_ZURUECK = 12                # wie weit zurueck (vergangene Termine)
TITEL = "Termine"
UNTERTITEL = "DLRG Ortsgruppe Dinslaken e.V."
ABO_LINK = os.environ.get("ABO_LINK") or "webcal://dlrgdin.github.io/iscsync/termine.ics"
ABO_NAME = "DLRG Dinslaken – Termine"
# Kategorien: Stichwort im Titel/Kategorie -> angezeigte Kategorie
KATEGORIEN = [
    ("Jugend",        ["jugend", "kinder", "mottoabend"]),
    ("Ausbildung",    ["modul", "wrd", "san", "sanität", "drsa", "auffrischung", "kats", "lehrgang",
                       "ausbildung", "rettungsfähigkeit", "wasserball", "fortbildung"]),
    ("Einsatz",       ["übung", "uebung", "einsatz", "karneval", "feuerwehr", "lv-"]),
    ("Vorstand",      ["vorstand"]),
    ("Vereinsleben",  []),         # alles andere
]
# =========================================================

import datetime as dt
import html
import re
import sys

from sync import BERLIN, CalDAV, blocks, finde_kalender, KALENDER_NAME, parse_dt, prop, unescape, unfold

WT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
MON = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
       "September", "Oktober", "November", "Dezember"]
MON_K = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


def kategorie(titel, cats):
    t = (titel + " " + cats).lower()
    for name, worte in KATEGORIEN:
        if any(w in t for w in worte):
            return name
    return KATEGORIEN[-1][0]


def vorkommen(b, von, bis):
    """Liefert (start, ende, ganztags) fuer ein VEVENT inkl. einfacher Wiederholungen."""
    sp, sv = prop(b, "DTSTART")
    if not sv:
        return []
    ganztags = re.fullmatch(r"\d{8}", sv) is not None
    start = parse_dt(sp, sv)
    ep, ev = prop(b, "DTEND")
    ende = parse_dt(ep, ev) if ev else (start + dt.timedelta(days=1) if ganztags else start)
    dauer = ende - start
    rr = prop(b, "RRULE")[1]
    if not rr:
        return [(start, ende, ganztags)] if ende > von and start < bis else []
    r = dict(x.split("=", 1) for x in rr.split(";") if "=" in x)
    schritt = {"DAILY": 1, "WEEKLY": 7}.get(r.get("FREQ"))
    if not schritt:
        return [(start, ende, ganztags)] if ende > von and start < bis else []
    schritt *= int(r.get("INTERVAL", "1"))
    until = parse_dt("", r["UNTIL"]) if "UNTIL" in r else bis
    anzahl = int(r.get("COUNT", "100000"))
    ex = set()
    for m in re.finditer(r"^EXDATE(;[^:\n]*)?:(.*)$", b, re.M):
        for v in m.group(2).split(","):
            ex.add(parse_dt(m.group(1) or "", v.strip()).astimezone(BERLIN).date())
    out, s, i = [], start, 0
    while s <= until and s < bis and i < anzahl:
        if s.astimezone(BERLIN).date() not in ex and s + dauer > von:
            out.append((s, s + dauer, ganztags))
        # Wandzeit beibehalten (Sommer/Winterzeit)
        s = (s.astimezone(BERLIN).replace(tzinfo=None) + dt.timedelta(days=schritt)).replace(tzinfo=BERLIN)
        i += 1
    return out


def termine_aus(texte, von, bis):
    liste = []
    for t in texte:
        for b in blocks(unfold(t), "VEVENT"):
            if prop(b, "RECURRENCE-ID")[1]:
                continue
            titel = unescape(prop(b, "SUMMARY")[1]).strip()
            if not titel or (prop(b, "CLASS")[1] or "").upper() in ("PRIVATE", "CONFIDENTIAL"):
                continue
            if (prop(b, "STATUS")[1] or "").upper() == "CANCELLED" or titel.lower().startswith("abgesagt"):
                continue
            ort = unescape(prop(b, "LOCATION")[1]).strip()
            cats = unescape(prop(b, "CATEGORIES")[1])
            for s, e, ganz in vorkommen(b, von, bis):
                liste.append({"titel": titel, "ort": ort, "start": s.astimezone(BERLIN),
                              "ende": e.astimezone(BERLIN), "ganztags": ganz,
                              "kat": kategorie(titel, cats)})
    # gleiche Termine (z. B. doppelt importiert) nur einmal
    seen, out = set(), []
    for x in sorted(liste, key=lambda x: (x["start"], x["titel"])):
        k = (x["titel"].lower(), x["start"])
        if k not in seen:
            seen.add(k); out.append(x)
    return out


def zeit_text(x):
    s, e = x["start"], x["ende"]
    if x["ganztags"]:
        letzter = (e - dt.timedelta(days=1)).date()
        if letzter > s.date():
            return f"bis {WT[letzter.weekday()]}, {letzter.day}. {MON_K[letzter.month-1]}"
        return "ganztägig"
    if e.date() > s.date() and (e - s) > dt.timedelta(hours=24):
        return f"{s:%H:%M} Uhr bis {e.day}. {MON_K[e.month-1]}, {e:%H:%M} Uhr"
    return f"{s:%H:%M}–{e:%H:%M} Uhr"


def render(termine, stand):
    import json
    e = html.escape
    kats = [k for k, _ in KATEGORIEN if any(t["kat"] == k for t in termine)]
    daten = []
    for t in termine:
        s, en = t["start"], t["ende"]
        letzter = (en - dt.timedelta(days=1)).date() if t["ganztags"] else (en - dt.timedelta(minutes=1)).date()
        if letzter < s.date():
            letzter = s.date()
        daten.append({"t": t["titel"], "o": t["ort"], "k": t["kat"],
                      "von": s.date().isoformat(), "bis": letzter.isoformat(),
                      "z": zeit_text(t), "g": t["ganztags"],
                      "uhr": "" if t["ganztags"] else s.strftime("%H:%M")})
    filter_html = "".join(f'<button type="button" data-f="{e(k)}">{e(k)}</button>' for k in kats)
    abo = (f'<a class="abo" href="{e(ABO_LINK)}">Termine im eigenen Kalender abonnieren</a>'
           if ABO_LINK else "<span></span>")
    js = json.dumps(daten, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.replace("%TITEL%", e(TITEL)).replace("%UNTERTITEL%", e(UNTERTITEL)) \
        .replace("%FILTER%", filter_html).replace("%DATEN%", js) \
        .replace("%ABO%", abo).replace("%STAND%", e(stand))


TEMPLATE = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%TITEL% – %UNTERTITEL%</title>
<style>
  :root{--rot:#e30613;--text:#1d1d1b;--grau:#5f6368;--linie:#e3e3e3;--hell:#f7f7f7}
  *{box-sizing:border-box}
  html,body{margin:0;background:#fff;color:var(--text);
    font-family:"Open Sans","Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:16px;line-height:1.4}
  .wrap{max-width:1000px;margin:0 auto;padding:8px 16px 24px}
  header{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:10px;
    border-bottom:3px solid var(--rot);padding-bottom:10px;margin-bottom:14px}
  header h1{margin:0;font-size:1.6rem;font-weight:700}
  header p{margin:2px 0 0;color:var(--grau);font-size:.9rem}
  .ansicht{display:flex}
  .ansicht button{font:inherit;font-size:.85rem;border:1px solid #cfcfcf;background:#fff;padding:4px 12px;cursor:pointer;color:var(--text)}
  .ansicht button+button{border-left:0}
  .ansicht button[aria-pressed=true]{background:var(--text);border-color:var(--text);color:#fff}
  .filter{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 14px}
  .filter button{font:inherit;font-size:.82rem;border:1px solid #cfcfcf;background:#fff;color:var(--text);
    padding:3px 11px;border-radius:2px;cursor:pointer;display:inline-flex;align-items:center;gap:6px}
  .filter button:hover{border-color:var(--rot)}
  .filter button[aria-pressed=true]{background:var(--rot);border-color:var(--rot);color:#fff}
  .filter button[data-f]:not([data-f=""])::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--c)}
  .filter button[aria-pressed=true]::before{box-shadow:0 0 0 1.5px #fff}
  /* Kategorie-Farben */
  [data-k=Ausbildung],.filter [data-f=Ausbildung]{--c:#e30613}
  [data-k=Einsatz],.filter [data-f=Einsatz]{--c:#0069b4}
  [data-k=Jugend],.filter [data-f=Jugend]{--c:#f0a000}
  [data-k=Vereinsleben],.filter [data-f=Vereinsleben]{--c:#4f9a2a}
  [data-k=Vorstand],.filter [data-f=Vorstand]{--c:#1d1d1b}
  /* Monatskopf */
  .monatskopf{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
  .monatskopf h2{margin:0;font-size:1.25rem;font-weight:700}
  .monatskopf h2 span{color:var(--grau);font-weight:400}
  .nav{display:flex;gap:6px}
  .nav button{font:inherit;font-size:.85rem;border:1px solid #cfcfcf;background:#fff;color:var(--text);
    padding:4px 10px;cursor:pointer;min-width:36px}
  .nav button:hover{border-color:var(--rot);color:var(--rot)}
  .nav button:disabled{opacity:.35;cursor:default;border-color:#cfcfcf;color:var(--text)}
  /* Raster */
  .raster{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));border-top:1px solid var(--linie);border-left:1px solid var(--linie)}
  .raster .wt{font-size:.75rem;text-transform:uppercase;letter-spacing:.05em;color:var(--grau);
    padding:6px 8px;border-right:1px solid var(--linie);border-bottom:1px solid var(--linie);background:var(--hell)}
  .zelle{min-height:104px;padding:4px 4px 6px;border-right:1px solid var(--linie);border-bottom:1px solid var(--linie);
    background:#fff;cursor:default;position:relative}
  .zelle.fremd{background:var(--hell)}
  .zelle.fremd .nr{color:#b0b0b0}
  .zelle .nr{font-size:.82rem;font-weight:600;display:inline-block;min-width:24px;height:24px;line-height:24px;text-align:center;
    margin-bottom:2px}
  .zelle.heute .nr{background:var(--rot);color:#fff;border-radius:50%}
  .zelle.gewaehlt{outline:2px solid var(--rot);outline-offset:-2px}
  .ev{display:block;width:100%;text-align:left;font:inherit;font-size:.74rem;line-height:1.25;border:0;
    background:color-mix(in srgb,var(--c) 10%,#fff);border-left:3px solid var(--c);color:var(--text);
    padding:2px 4px;margin:0 0 2px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .ev:hover{background:color-mix(in srgb,var(--c) 20%,#fff)}
  .ev b{font-weight:600;margin-right:3px}
  .mehr{font-size:.72rem;color:var(--grau);padding:0 4px;background:none;border:0;cursor:pointer;font-family:inherit}
  .punkte{display:none}
  /* Detail / Tagesliste */
  .detail{margin-top:14px}
  .detail h3{font-size:1rem;margin:0 0 4px;color:var(--rot)}
  ol.liste{list-style:none;margin:0;padding:0}
  ol.liste li{display:grid;grid-template-columns:52px 1fr auto;gap:14px;align-items:start;padding:10px 0;border-bottom:1px solid var(--linie)}
  .datum{text-align:center;line-height:1.1;padding-top:2px}
  .datum .tag{display:block;font-size:1.4rem;font-weight:700}
  .datum .wtag{display:block;font-size:.75rem;color:var(--grau);text-transform:uppercase;letter-spacing:.04em}
  .info h4{margin:0;font-size:1rem;font-weight:600}
  .info p{margin:2px 0 0;color:var(--grau);font-size:.88rem}
  .info .ort::before{content:"·";margin:0 6px}
  .kat{font-size:.74rem;color:var(--grau);white-space:nowrap;padding-top:4px}
  .kat::before{content:"";display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:var(--c);vertical-align:1px}
  .lmonat{font-size:1.05rem;font-weight:700;margin:22px 0 4px;padding:6px 0;border-bottom:1px solid var(--linie);color:var(--rot)}
  .lmonat span{color:var(--grau);font-weight:400}
  .leer{color:var(--grau);padding:14px 0;margin:0}
  ol.liste li.vorbei{opacity:.55}
  .zelle .ev.vorbei{opacity:.55}
  .rueck{font:inherit;font-size:.85rem;background:none;border:0;color:var(--rot);font-weight:600;cursor:pointer;padding:4px 0;margin:4px 0 0}
  .rueck:hover{text-decoration:underline}
  footer{margin-top:18px;display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;font-size:.8rem;color:var(--grau)}
  .abo{color:var(--rot);font-weight:600;text-decoration:none}
  .abo:hover{text-decoration:underline}
  [hidden]{display:none!important}
  @media (max-width:640px){
    .zelle{min-height:54px;cursor:pointer;padding:3px 2px}
    .zelle .nr{font-size:.8rem}
    .ev,.mehr{display:none}
    .punkte{display:flex;flex-wrap:wrap;gap:3px;justify-content:center;padding-top:2px}
    .punkte i{width:7px;height:7px;border-radius:50%;background:var(--c)}
    .raster .wt{padding:5px 2px;text-align:center;font-size:.68rem}
    .zelle{text-align:center}
    ol.liste li{grid-template-columns:44px 1fr;gap:10px}
    ol.liste .kat{grid-column:2;padding-top:0}
    .info .ort{display:block}
    .info .ort::before{content:none}
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div><h1>%TITEL%</h1><p>%UNTERTITEL%</p></div>
    <div class="ansicht" role="group" aria-label="Ansicht">
      <button type="button" data-a="monat" aria-pressed="true">Monat</button>
      <button type="button" data-a="liste" aria-pressed="false">Liste</button>
    </div>
  </header>
  <div class="filter" role="group" aria-label="Kategorie filtern">
    <button type="button" data-f="" aria-pressed="true">Alle</button>%FILTER%
  </div>

  <section id="monat">
    <div class="monatskopf">
      <h2 id="mtitel"></h2>
      <div class="nav">
        <button type="button" id="zurueck" aria-label="Vorheriger Monat">&#8249;</button>
        <button type="button" id="heute">Heute</button>
        <button type="button" id="vor" aria-label="Nächster Monat">&#8250;</button>
      </div>
    </div>
    <div class="raster" id="raster"></div>
    <div class="detail" id="detail"></div>
  </section>

  <section id="liste" hidden></section>

  <footer>%ABO%<span>Stand: %STAND%</span></footer>
</div>
<script>
(function(){
  var D=%DATEN%;
  var MON=["Januar","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"];
  var WT=["Mo","Di","Mi","Do","Fr","Sa","So"];
  var filt="", heute=new Date(), ansicht="monat", gewaehlt=null;
  heute=new Date(heute.getFullYear(),heute.getMonth(),heute.getDate());
  var cur=new Date(heute.getFullYear(),heute.getMonth(),1);
  function iso(d){return d.getFullYear()+"-"+("0"+(d.getMonth()+1)).slice(-2)+"-"+("0"+d.getDate()).slice(-2);}
  function parse(s){var p=s.split("-");return new Date(+p[0],p[1]-1,+p[2]);}
  function esc(s){return String(s).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];});}
  function sicht(){return D.filter(function(e){return !filt||e.k===filt;});}
  function amTag(ds){return sicht().filter(function(e){return e.von<=ds&&e.bis>=ds;});}
  var letzterMonat=(function(){var m=heute; D.forEach(function(e){var b=parse(e.bis); if(b>m)m=b;}); return new Date(m.getFullYear(),m.getMonth(),1);})();
  var ersterMonat=(function(){var m=heute; D.forEach(function(e){var v=parse(e.von); if(v<m)m=v;}); return new Date(m.getFullYear(),m.getMonth(),1);})();
  var mitVergangenen=false;

  function eintrag(e,mitDatum){
    var v=parse(e.von);
    return '<li data-k="'+esc(e.k)+'"'+(e.bis<iso(heute)?' class="vorbei"':'')+'><div class="datum"><span class="tag">'+v.getDate()+'</span><span class="wtag">'+WT[(v.getDay()+6)%7]+'</span></div>'+
      '<div class="info"><h4>'+esc(e.t)+'</h4><p><span class="zeit">'+esc(e.z)+'</span>'+(e.o?'<span class="ort">'+esc(e.o)+'</span>':'')+'</p></div>'+
      '<span class="kat" data-k="'+esc(e.k)+'">'+esc(e.k)+'</span></li>';
  }

  function zeigeTag(ds){
    gewaehlt=ds;
    var d=parse(ds), ev=amTag(ds);
    document.querySelectorAll('.zelle').forEach(function(z){z.classList.toggle('gewaehlt',z.dataset.d===ds);});
    var h='<h3>'+WT[(d.getDay()+6)%7]+', '+d.getDate()+'. '+MON[d.getMonth()]+' '+d.getFullYear()+'</h3>';
    h+= ev.length? '<ol class="liste">'+ev.map(function(e){return eintrag(e);}).join('')+'</ol>' : '<p class="leer">Keine Termine an diesem Tag.</p>';
    document.getElementById('detail').innerHTML=h; hoehe();
  }

  function monat(){
    var y=cur.getFullYear(), m=cur.getMonth();
    document.getElementById('mtitel').innerHTML=MON[m]+' <span>'+y+'</span>';
    document.getElementById('zurueck').disabled = cur<=ersterMonat;
    document.getElementById('vor').disabled = cur>=letzterMonat;
    var h=WT.map(function(w){return '<div class="wt">'+w+'</div>';}).join('');
    var erster=new Date(y,m,1), off=(erster.getDay()+6)%7;
    var start=new Date(y,m,1-off);
    var wochen=Math.ceil((off+new Date(y,m+1,0).getDate())/7);
    for(var i=0;i<wochen*7;i++){
      var d=new Date(start.getFullYear(),start.getMonth(),start.getDate()+i), ds=iso(d);
      var ev=amTag(ds), kl='zelle'+(d.getMonth()!==m?' fremd':'')+(ds===iso(heute)?' heute':'');
      var inner='<span class="nr">'+d.getDate()+'</span>';
      var max=3;
      ev.slice(0,max).forEach(function(e){
        inner+='<button type="button" class="ev'+(e.bis<iso(heute)?' vorbei':'')+'" data-k="'+esc(e.k)+'" title="'+esc(e.t+' – '+e.z+(e.o?' · '+e.o:''))+'">'+(e.uhr&&e.von===ds?'<b>'+e.uhr+'</b>':'')+esc(e.t)+'</button>';
      });
      if(ev.length>max) inner+='<button type="button" class="mehr">+'+(ev.length-max)+' weitere</button>';
      if(ev.length) inner+='<div class="punkte">'+ev.slice(0,4).map(function(e){return '<i data-k="'+esc(e.k)+'"></i>';}).join('')+'</div>';
      h+='<div class="'+kl+'" data-d="'+ds+'">'+inner+'</div>';
    }
    var r=document.getElementById('raster'); r.innerHTML=h;
    r.querySelectorAll('.zelle').forEach(function(z){z.addEventListener('click',function(){zeigeTag(z.dataset.d);});});
    // Detail: gewaehlter Tag, sonst kommende Termine des Monats
    var mstart=iso(new Date(y,m,1)), mende=iso(new Date(y,m+1,0));
    if(gewaehlt&&gewaehlt>=mstart&&gewaehlt<=mende){zeigeTag(gewaehlt);return;}
    gewaehlt=null;
    var ev=sicht().filter(function(e){return e.bis>=mstart&&e.von<=mende;});
    document.getElementById('detail').innerHTML='<h3>Termine im '+MON[m]+'</h3>'+(ev.length?'<ol class="liste">'+ev.map(function(e){return eintrag(e);}).join('')+'</ol>':'<p class="leer">Keine Termine in diesem Monat.</p>');
    hoehe();
  }

  function liste(){
    var h='', akt='', alle=sicht(), hs=iso(heute);
    var vergangen=alle.filter(function(e){return e.bis<hs;}).length;
    var zeigen=alle.filter(function(e){return mitVergangenen||e.bis>=hs;});
    if(vergangen) h+='<button type="button" class="rueck" id="rueck">'+(mitVergangenen?'Vergangene Termine ausblenden':'Vergangene Termine anzeigen ('+vergangen+')')+'</button>';
    zeigen.forEach(function(e){
      var v=parse(e.von), k=v.getFullYear()+'-'+v.getMonth();
      if(k!==akt){ if(akt) h+='</ol>'; h+='<h3 class="lmonat">'+MON[v.getMonth()]+' <span>'+v.getFullYear()+'</span></h3><ol class="liste">'; akt=k; }
      h+=eintrag(e);
    });
    if(akt) h+='</ol>'; else h+='<p class="leer">Zurzeit stehen keine Termine an.</p>';
    document.getElementById('liste').innerHTML=h;
    var rb=document.getElementById('rueck'); if(rb) rb.onclick=function(){mitVergangenen=!mitVergangenen; liste();};
    hoehe();
  }

  function neu(){ if(ansicht==='monat') monat(); else liste(); }
  document.querySelectorAll('.filter button').forEach(function(b){b.addEventListener('click',function(){
    filt=b.dataset.f; document.querySelectorAll('.filter button').forEach(function(x){x.setAttribute('aria-pressed',x===b?'true':'false');}); neu();});});
  document.querySelectorAll('.ansicht button').forEach(function(b){b.addEventListener('click',function(){
    ansicht=b.dataset.a; document.querySelectorAll('.ansicht button').forEach(function(x){x.setAttribute('aria-pressed',x===b?'true':'false');});
    document.getElementById('monat').hidden=ansicht!=='monat'; document.getElementById('liste').hidden=ansicht!=='liste'; neu();});});
  document.getElementById('zurueck').onclick=function(){cur=new Date(cur.getFullYear(),cur.getMonth()-1,1);monat();};
  document.getElementById('vor').onclick=function(){cur=new Date(cur.getFullYear(),cur.getMonth()+1,1);monat();};
  document.getElementById('heute').onclick=function(){cur=new Date(heute.getFullYear(),heute.getMonth(),1);gewaehlt=null;monat();};

  function hoehe(){try{parent.postMessage({dlrgTermineHoehe:document.documentElement.scrollHeight},'*');}catch(e){}}
  window.addEventListener('resize',hoehe);
  neu();
})();
</script>
</body>
</html>
"""


def abo_datei(texte):
    """Erzeugt docs/termine.ics mit denselben Terminen wie die Webseite (fuer Kalender-Abos)."""
    tz, evs, uids = {}, [], set()
    for t in texte:
        u = unfold(t)
        for z in blocks(u, "VTIMEZONE"):
            tz[prop(z, "TZID")[1]] = z
        for b in blocks(u, "VEVENT"):
            titel = unescape(prop(b, "SUMMARY")[1]).strip()
            if not titel or (prop(b, "CLASS")[1] or "").upper() in ("PRIVATE", "CONFIDENTIAL"):
                continue
            if (prop(b, "STATUS")[1] or "").upper() == "CANCELLED" or titel.lower().startswith("abgesagt"):
                continue
            uid = prop(b, "UID")[1] or ""
            key = uid + "|" + (prop(b, "RECURRENCE-ID")[1] or "")
            if key in uids:
                continue
            uids.add(key)
            b = re.sub(r"^X-ISC-SYNC:.*\n", "", b, flags=re.M)
            evs.append(b)
    kopf = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//DLRG Dinslaken//Terminkalender//DE",
            "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-CALNAME:{ABO_NAME}",
            "X-WR-TIMEZONE:Europe/Berlin", "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H"]
    text = "\n".join(kopf + list(tz.values()) + evs + ["END:VCALENDAR"]) + "\n"
    with open("docs/termine.ics", "w", encoding="utf-8", newline="") as f:
        f.write(text.replace("\n", "\r\n"))
    return len(evs)


def main():
    jetzt = dt.datetime.now(BERLIN)
    heute0 = jetzt.replace(hour=0, minute=0, second=0, microsecond=0)
    von = heute0 - dt.timedelta(days=31 * MONATE_ZURUECK)
    bis = heute0 + dt.timedelta(days=31 * MONATE_VORAUS)
    if len(sys.argv) > 1:
        texte = [open(p, encoding="utf-8").read() for p in sys.argv[1:]]
    else:
        url, user, pw = (os.environ.get(k, "").strip() for k in ("CALDAV_URL", "CALDAV_USER", "CALDAV_PASSWORD"))
        cal = CalDAV(finde_kalender(url, user, pw, KALENDER_NAME), user, pw)
        texte = list(cal.list_all().values())
    termine = termine_aus(texte, von, bis)
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(render(termine, jetzt.strftime("%d.%m.%Y, %H:%M Uhr")))
    n = abo_datei(texte)
    print(f"Terminliste erstellt: {len(termine)} Termine. Abo-Datei: {n} Termine.")


if __name__ == "__main__":
    main()
