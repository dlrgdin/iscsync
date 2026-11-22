# ISC → DLRG Cloud: automatischer Termin-Abgleich

Das Skript läuft jeden Morgen bei GitHub und überträgt neue oder geänderte Termine aus dem ISC
(https://dinslaken.dlrg.de/die-ortsgruppe/termine/icaltermine/) in den Cloud-Kalender
„DLRG DIN öffentlich“.

## Was das Skript macht
- Neue und geänderte ISC-Termine werden eingetragen (nur kommende Termine).
- „Aufsicht Wassergymnastik“ und abgesagte Termine kommen nicht rein.
- Verschwindet ein Termin aus dem ISC, wird er auch in der Cloud gelöscht.
- **Doppelte Termine:** Kommt im ISC ein Kurs heraus und steht am selben Tag schon ein eigener
  Termin mit ähnlichem Titel im Kalender (z. B. „WRD 403“ ↔ „Modul 403 …“), wird der eigene
  Termin gelöscht. Der ISC-Termin mit Uhrzeit und Ort ersetzt ihn.
- Alle anderen eigenen Termine werden nicht angefasst.

## Steuerung über die Notiz eines eigenen Termins
- `ISC: behalten` → dieser Termin wird nie automatisch gelöscht.
- `ISC: <Stichwort>` → der Termin wird gelöscht, sobald am selben Tag ein ISC-Termin mit diesem
  Stichwort im Titel erscheint (z. B. `ISC: Rettungsschwimm`). Für Fälle, die die Automatik nicht erkennt.
- Termine, deren Titel mehrere Dinge kombinieren („LV-Übung & SAN-Fortbildung“), sowie Serientermine
  werden nie automatisch gelöscht.

## Einstellungen ändern
Oben in `sync.py` im Abschnitt „Einstellungen“:
- `AUSSCHLIESSEN` – Begriffe, deren Termine nicht übernommen werden
- `ABGESAGTE_ENTFERNEN` – abgesagte Termine weglassen (True/False)
- `DOPPELTE_ENTFERNEN` – automatische Doppel-Erkennung an/aus (True/False)

## Von Hand starten / Testlauf
GitHub → Repository → **Actions** → „ISC-Termine in DLRG Cloud uebernehmen“ → **Run workflow**.
Mit Haken bei „Testlauf“ wird nur angezeigt, was passieren würde.
Das Protokoll jedes Laufs steht unter Actions → Lauf anklicken → „sync“.
