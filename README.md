# Heatmap of Fascism

Heatmap of Fascism documents fascist sticker propaganda in public space.
Reports are submitted via Mastodon, reviewed, and mapped to show where stickers appear, persist, or are removed over time.

## How to submit a report

A valid report must include:
• one photo of the sticker
• one location:
  – coordinates (lat, lon)
  – OR street + city
  – OR street crossing + city

Optional:
• sticker type (e.g. party, symbol, slogan)
• date (auto if omitted)

## Hashtags

Everyone:
#stickerreport

Members only (confirmed):
#stickerremoved

Removal reports are only counted if confirmed by this account or submitted by a trusted contributor.

## Processing rules

• reports without a photo or location cannot be processed
• locations are normalized to coordinates (10–50 m accuracy)
• repeated reports update the same spot over time
• each spot tracks first_seen, last_seen, status, and report count

## Map output

The public map shows:
• individual reports
• heatmaps of active locations
• status over time (present / removed / unknown)

All reports are reviewed before appearing on the map.

PROJECT: HEATMAP OF FASCISM

KURZBESCHREIBUNG
Heatmap of Fascism dokumentiert faschistische Sticker-Propaganda im öffentlichen Raum.
Meldungen werden über Mastodon gesammelt, geprüft und weltweit auf einer Karte visualisiert,
um Hotspots, Verbreitung und Entfernung über Zeit sichtbar zu machen.


TECHNISCHE BESCHREIBUNG

Input (Mastodon):
• 1 Foto
• 1 Ort:
  – Koordinaten
  – oder Straße + Stadt
  – oder Straßenkreuzung + Stadt
• Hashtag:
  – #stickerreport (Sticker vorhanden, alle)
  – #stickerremoved (Sticker entfernt, nur bestätigt)

Verarbeitung:
• Mastodon API Ingest
• Review via Favorit durch Projekt-Account / Allowlist
• Geocoding (OpenStreetMap)
• Ortsnormalisierung (10–50 m Genauigkeit)
• Duplikat-Erkennung (Radius, später Hash/OCR)
• Speicherung als GeoJSON (Single Source of Truth)

Visualisierung:
• OpenStreetMap / uMap
• Punkte + Heatmap
• Status: present / removed / unknown
• Zeitfelder: first_seen, last_seen, report_count

Prinzipien:
• öffentlich und reproduzierbar
• niedrige Einstiegshürde
• keine automatischen politischen Urteile
• datenschutzbewusst


MISSION
• Sichtbarmachen faschistischer Propaganda im öffentlichen Raum
• Erkennen von Hotspots und Mustern
• Dokumentation von Persistenz und Entfernung
• Unterstützung zivilgesellschaftlicher Gegenmaßnahmen
• Transparenz statt Eskalation


ROADMAP

Jetzt:
• Stabiler Ingest
• Review-Workflow
• Öffentliche Karte

Als Nächstes:
• Duplikat-Clustering
• Status-Übergänge über Zeit
• Trusted-Contributor-Modell

Später:
• OCR (Tesseract)
• Sticker-Typ-Vorschläge
• Weitere Karten-Layer
• Analyse- und Export-Ansichten
