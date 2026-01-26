Fahrplan für das nächste Major Update: Heatmap of Fascism Bot

Hintergrund und Ziele

Die Heatmap of Fascism dokumentiert faschistische Sticker und Graffiti durch gezielte Meldungen in Mastodon. Alle Berichte werden manuell moderiert, bevor sie als GeoJSON‑Feature in reports.geojson veröffentlicht werden ￼. Die Plattform soll verlässliche Daten liefern, ohne passives Scraping ￼. Nach der ersten Beta‑Phase gibt es noch technische und organisatorische Baustellen. Das nächste Major‑Update soll Fehler korrigieren, Automatisierung erhöhen, Datenkonsistenz sichern und die Workflows professionalisieren, damit Administratoren und Moderatoren möglichst wenig Nacharbeit haben.

Phase 1: Sofortige Korrekturen und Bereinigung
	1.	Trenne **1161** (Zahlencode) von **AUF1** (Sender) – niemals vermischen:
	•	`entities.json["1161"]` = Zahlencode „Anti-Antifa / Anti-Antifaschistische Aktion“ (Symbolcode)
	•	`entities.json["auf1"]` = Medienkanal AUF1 (eigener Entity-Key)
	•	Regel: Alias handling darf nur Schreibvarianten auf **denselben** Key normalisieren – nie unterschiedliche Entitäten mergen.

	2.	Überprüfung aller entities.json‑Einträge – Gleiche Struktur für jeden Eintrag: Feld display mit deutsch/englischem Namen, Feld desc mit deutscher Langform + englischer Übersetzung und Kontext (politische Einstufung, Organisationstyp). Jede Kurzform (AfD, NPD etc.) sollte eine aussagekräftige Beschreibung erhalten.
	3.	Datenbereinigung – Führe ein Skript aus, das reports.geojson auf fehlerhafte oder leere Felder prüft:
	•	Fehlende category, entity_display oder entity_desc ergänzen.
	•	Koordinaten außerhalb plausibler Bereiche (z. B. außerhalb Europas) markieren.
	•	Duplikate erkennen (gleiche URL, nahe Koordinate innerhalb z. 10 m) und Vorschläge zur Zusammenführung ausgeben.
	4.	Manual Fix bei Fehlern – Entwickle in tools/ ein Kommando fix_data.py, das die oben genannten Checks ausführt und interaktive Korrektur ermöglicht. Integriere einen ox-Befehl (ox fix_data), der das Skript startet.

Phase 2: Automatisierung und Qualitätssicherung
	1.	Alias‑Handling – Baue eine Alias‑Liste (z. B. in alias.json), die alternative Schreibweisen, Tippfehler und Synonyme auf die offiziellen entity_key‑Schlüssel abbildet. Die Funktion parse_sticker_type() sollte diese Liste verwenden.
	2.	Unit Tests und Linting – Schreibe Tests für Kernfunktionen (parse_sticker_type, Geocoding‑Normalisierung, JSON‑Schreibvorgänge) mit pytest. Verwende flake8/black, um Stilkonformität sicherzustellen.
	3.	CI/CD‑Pipeline – Richte einen GitHub Action Workflow ein, der bei jedem Push folgende Schritte ausführt:
	•	python -m py_compile bot.py zur Syntax‑Prüfung.
	•	pytest zum Ausführen der Tests.
	•	python tools/check_data.py zum Validieren der GeoJSON‑Datei.
	•	Abbruch des Deployments bei Fehlern.
	4.	Logging verbessern – Vereinheitliche alle Logausgaben über das Python‑Logging‑Modul. Ändere show_errors, sodass es logs/normal-*.log, logs/event-*.log und bot.launchd.log durchsucht. Speichere auch Warnungen und wichtige Info in einer separaten Datei (z. B. logs/warnings.log).

Phase 3: Erweiterung der Kategorien und Funktionen
	1.	Unterstützung für Graffiti und andere Propagandaformen – Füge Felder wie graffiti_type und sticker_removed hinzu. Ergänze im Bot die Parsing‑Logik, damit diese Hashtags erkannt werden.
	2.	Filter und UI – Passe die uMap‑Konfiguration an, damit nach sticker_type, graffiti_type und status gefiltert werden kann. Bereite das Popup‑Template in docs/popup_template.html entsprechend vor.
	3.	Entitäten weiter ausbauen – Kuratiere eine Liste von Parteien, Gruppen, Symbolen und Slogans, die häufig gemeldet werden. Füge sie mit sauberer Beschreibung (DE/EN) in entities.json ein und belege diese durch öffentliche Quellen.

Phase 4: Benutzer‑Interaktion und Moderation
	1.	Moderations‑Dashboard – Erstelle eine CLI oder ein kleines Web‑Interface (z. B. Flask + SQLite), das pending Reports, NEEDS_INFO‑Loops und Duplikate anzeigt. Moderatoren können dort Berichte akzeptieren, ablehnen, korrigieren oder duplizieren. Überlege, ob Authentifizierung (z. B. Basic Auth) nötig ist.
	2.	Trust‑Levels für Reporter – Implementiere im Bot ein rudimentäres Reputationssystem. Nutzer, die viele korrekte Berichte liefern, benötigen weniger strenge Prüfung; neue oder fehleranfällige Reporter erhalten mehr Feedback. Speichere diese Meta‑Daten in einer lokalen Datenbank.
	3.	Verbesserte Bot‑Antworten – Überarbeite die Textbausteine: klare Struktur (Bestätigungs / Ablehnungsgrund), Tipps zur Korrektur, Hinweis auf Sicherheitsregeln. Halte die Antworten kurz, nutze klare Emojis (🚀, ⚠️, ℹ️) und schließe immer mit der antifaschistischen Botschaft.

Phase 5: Dokumentation und Governance
	1.	README und Entwicklerdokumente aktualisieren – Integriere die neuen Regeln, Workflows und die erweiterte Entitätenliste. Betone, dass nur öffentliche Berichte verarbeitet werden und keine privaten Daten gespeichert werden dürfen.
	2.	Moderations‑Richtlinien – Dokumentiere klare Regeln für die Aufnahme (z. B. Kriterien für present, removed), für das Kennzeichnen von Duplikaten und für die Behandlung neuer Kategorien.
	3.	Versionspolitik – Lege fest, wie Major/Minor/Patch‑Versionsnummern vergeben werden. Jede Major‑Version soll signifikante Funktionsupdates enthalten; Minor und Patch dienen Bugfixes und Datenupdates.
	4.	Community Feedback – Richte ein öffentliches Issue‑Board ein, über das Reporter und Nutzer Feedback geben können. Reagiere auf gemeldete Fehler zeitnah.

Phase 6: Datenschutz und rechtliche Konformität
	1.	Privacy‑Audit – Prüfe, ob das Projekt DSGVO‑konform ist. Sichte insbesondere das Handling von Standortdaten und Bildern. Dokumentiere, wie lange Daten gespeichert werden, und implementiere einen Prozess zum Löschen auf Anfrage.
	2.	Rechtskonformität – Prüfe, ob das Veröffentlichen von Namen oder Symbolen rechtlich zulässig ist (Urheberrecht, Persönlichkeitsrecht). Aktualisiere die Moderationsregeln entsprechend.
	3.	Transparenz – Füge einen Abschnitt zur Privacy Policy hinzu, der erklärt, welche Daten gesammelt werden, wie sie verarbeitet werden und wie man eine Löschung beantragen kann.

Abschließende Hinweise
	•	Testing vor Deployment: Jede neue Funktion muss lokal getestet werden. Nutze python3 bot.py --once und das Prüfskript, bevor du den Bot neu startest.
	•	Datensicherung: Bevor du entities.json oder reports.geojson änderst, erstelle eine Backup-Datei (z. B. in _backup/).
	•	Beteiligung mehrerer Personen: Ziehe weitere Maintainer hinzu, damit Code‑Reviews stattfinden können und der Bus Factor sinkt.

Mit dieser Roadmap wird das Projekt strukturiert professionalisiert: Die Datenbasis wird korrekt und evidenzbasiert, der Workflow effizienter, die Moderation einfacher und die Plattform robuster.

## Sources database (docs/sources.json)

We introduce a curated **sources database** at `docs/sources.json`.
It is used as a *trusted starting point* for research and future enrichment tooling.

Rules:
- `entities.json` stays the **single source of truth** for user-facing names/meaning.
- The bot must **never overwrite** curated `display/desc` fields automatically.
- Automated enrichment (if enabled later) may only write to separate *auto* fields (e.g. `desc_en_auto`) or add `needs_desc=true`, never to `desc`.

Scope:
- Official publications (e.g. domestic intelligence / media authorities)
- Reputable research portals and academic institutes
- Reputable symbol/code databases (international)
- Wikipedia as a **starting reference**, never as the only source for contested claims

File format:
- list of objects with `id`, `title`, `url`, `type`, `scope`, `tags`, `retrieved`
