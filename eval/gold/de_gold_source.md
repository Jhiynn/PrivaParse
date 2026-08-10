# Deutsches Gold-Set — annotierte Quelle

Kompilieren mit `python eval/build_gold.py`. Alles vor dem ersten `### id:` wird
ignoriert.

## Annotationsregeln

Entitäten werden inline markiert: `{{PERSON:Max Mustermann}}`, `{{EMAIL:...}}`,
`{{PHONE:...}}`.

- **Titel und Anreden gehören nicht zur Entität.** `Herr`, `Frau`, `Dr.`,
  `Prof.`, `Dipl.-Ing.` bleiben außerhalb der Markierung. Sie sind für sich
  genommen nicht identifizierend, und der Normalizer entfernt sie ohnehin.
- **Adelspartikel gehören dazu.** `von`, `von der`, `zu` sind in Deutschland
  Namensbestandteil. `Max von Bergen` ist nicht `Max Bergen`.
- **Firmen, Orte und Behörden werden nicht markiert.** Sie stehen bewusst im
  Set, um False Positives zu messen.
- **Aktenzeichen, Rechnungsnummern und Beträge werden nicht markiert.** Sie
  sehen Telefonnummern ähnlich genug, um die Precision zu testen.
- Telefonnummern werden vollständig markiert, so wie sie geschrieben stehen.

### id: de-001 | kind: brief
Sehr geehrter Herr {{PERSON:Max Mustermann}},

vielen Dank für Ihre Anfrage vom 12. März. Wir haben Ihre Unterlagen erhalten
und melden uns bis Ende der Woche bei Ihnen.

Bei Rückfragen erreichen Sie mich unter {{PHONE:+49 170 1234567}} oder per
E-Mail an {{EMAIL:s.becker@musterfirma.de}}.

Mit freundlichen Grüßen
{{PERSON:Sabine Becker}}
Musterfirma GmbH

### id: de-002 | kind: email
Hallo {{PERSON:Anna}},

anbei wie besprochen der Entwurf. Ich habe die Anmerkungen von
{{PERSON:Thomas Schneider}} bereits eingearbeitet.

Meld dich gern, wenn noch etwas fehlt: {{EMAIL:anna.krueger@beispiel.de}}

Viele Grüße
{{PERSON:Anna Krüger}}

### id: de-003 | kind: aktennotiz
Aktennotiz zum Vorgang 2024/1187

Am 04.06.2024 rief Frau {{PERSON:Müller-Lüdenscheidt}} an und erkundigte sich
nach dem Stand der Bearbeitung. Rückruf zugesagt für den Folgetag unter
{{PHONE:0221 4567890}}.

Sachbearbeiter: {{PERSON:Jörg Ölmann}}

### id: de-004 | kind: brief
Sehr geehrte Frau Prof. Dr. med. {{PERSON:Katharina Weiß}},

wir freuen uns, Sie als Referentin gewinnen zu können. Ihre Kontaktdaten haben
wir wie folgt notiert:

E-Mail: {{EMAIL:k.weiss@uniklinik-beispiel.de}}
Telefon: {{PHONE:+49 (0) 30 1234 5678}}

Mit besten Grüßen
{{PERSON:Peter Hoffmann}}

### id: de-005 | kind: notiz
Teilnehmer der Besprechung am Montag:

- {{PERSON:Max von Bergen}}
- {{PERSON:Elisabeth zu Falkenstein}}
- {{PERSON:Ahmet Öztürk}}
- {{PERSON:Nguyen Thi Lan}}

Protokoll führt {{PERSON:Ahmet Öztürk}}.

### id: de-006 | kind: negativ
Die Musterfirma GmbH mit Sitz in Frankfurt am Main betreibt seit 1998 ein
Vertriebsnetz in Bayern, Sachsen und Nordrhein-Westfalen. Der Umsatz lag im
vergangenen Geschäftsjahr bei 4,2 Millionen Euro.

Weitere Standorte befinden sich in Hamburg und Leipzig.

### id: de-007 | kind: negativ
Rechnung Nr. 4711 vom 03.02.2024
Aktenzeichen: 12 O 3456/23
Kundennummer: 0170 8899
Betrag: 1.249,00 EUR

Zahlbar innerhalb von 14 Tagen ohne Abzug.

### id: de-008 | kind: brief
Guten Tag,

mein Name ist {{PERSON:Frank Sommer}}. Ich hatte gestern mit Ihrem Kollegen
{{PERSON:Ernst König}} telefoniert.

Sie erreichen mich werktags unter {{PHONE:0170/1234567}}.

Freundliche Grüße
{{PERSON:Frank Sommer}}

### id: de-009 | kind: negativ
Im Sommer steigt der Verbrauch erfahrungsgemäß an, im Winter fällt er wieder
ab. Der König von Spanien besuchte im Frühjahr die Messe. Ein Wolf wurde in der
Lausitz gesichtet, und beim Bäcker an der Ecke gab es Streuselkuchen.

### id: de-010 | kind: email
Betreff: Terminbestätigung

Sehr geehrter Herr {{PERSON:Dr. Schmidt-Bauer}},

hiermit bestätigen wir Ihren Termin am 18.07. um 14:30 Uhr.

Bitte bringen Sie Ihre Unterlagen mit. Rückfragen unter {{PHONE:089 123456}}.

Mit freundlichen Grüßen
Praxis am Stadtpark

### id: de-011 | kind: formular
Antragsteller
Name: {{PERSON:Käthe Bräuer}}
E-Mail: {{EMAIL:kaethe.braeuer@example.org}}
Telefon: {{PHONE:+49 151 98765432}}

Vertretung
Name: {{PERSON:Hans-Jürgen Groß}}
E-Mail: {{EMAIL:hj.gross@example.org}}

### id: de-012 | kind: brief
Sehr geehrte Damen und Herren,

bezugnehmend auf Ihr Schreiben vom 09.01.2024 teile ich Ihnen mit, dass ich das
Mandat für Frau {{PERSON:Beatrice Vogel-Amrein}} niederlege.

Für Rückfragen stehe ich unter {{PHONE:+49 30 9876543}} zur Verfügung.

Rechtsanwalt {{PERSON:Wolfgang Reuter}}

### id: de-013 | kind: negativ
Die Sitzung des Ausschusses findet im Rathaus statt. Auf der Tagesordnung
stehen der Haushaltsplan, die Sanierung der Grundschule und die Vergabe der
Bauleistungen. Eine öffentliche Fragestunde ist vorgesehen.

### id: de-014 | kind: email
Hi,

kannst du {{PERSON:Lena}} bitte die Datei schicken? Ihre Adresse ist
{{EMAIL:l.bergmann+projekt@firma-xy.de}}.

Danke!
{{PERSON:Tim}}

### id: de-015 | kind: aktennotiz
Telefonat vom 22.05.2024, 10:15 Uhr

Anrufer: {{PERSON:Stefan Bauer}}, {{PHONE:0151 22334455}}
Anliegen: Widerspruch gegen den Bescheid vom 02.05.2024
Weiterleitung an: {{PERSON:Miriam Hartl}}

Wiedervorlage in zwei Wochen.

### id: de-016 | kind: brief
Sehr geehrter Herr {{PERSON:Mustermann}},

leider können wir Ihrem Antrag nicht entsprechen. Die Begründung entnehmen Sie
bitte der beigefügten Anlage.

Gegen diesen Bescheid können Sie innerhalb eines Monats Widerspruch einlegen.

Im Auftrag
{{PERSON:D. Lehmann}}

### id: de-017 | kind: liste
Verteiler:

{{EMAIL:vorstand@musterverein.de}}
{{EMAIL:m.fischer@musterverein.de}}
{{EMAIL:info@musterverein.de}}

Rückfragen an {{PERSON:Martina Fischer}}.

### id: de-018 | kind: negativ
Systemhinweis: Die Datenbankverbindung wurde nach 30 Sekunden geschlossen.
Bitte prüfen Sie die Konfiguration in der Datei settings.yaml und starten Sie
den Dienst neu. Der Fehlercode lautet 0x80070005.

### id: de-019 | kind: brief
Liebe {{PERSON:Frau Doktor}},

entschuldigen Sie die verspätete Rückmeldung. {{PERSON:Andrea Pohlmann}} hat
mich gebeten, Ihnen den Zwischenbericht zuzusenden.

Sie erreichen mich unter {{EMAIL:a.pohlmann@institut-beispiel.de}}.

### id: de-020 | kind: protokoll
Protokoll der Gesellschafterversammlung

Anwesend: {{PERSON:Dr. Klaus Neumann}} (Vorsitz), {{PERSON:Renate Sauer}},
{{PERSON:İbrahim Yılmaz}}

Entschuldigt: {{PERSON:Franziska Bittner}}

Beschluss einstimmig gefasst.

### id: de-021 | kind: email
Guten Morgen zusammen,

die Nummer hat sich geändert. Neu: {{PHONE:+49 40 1234567}}, alt war
{{PHONE:040 7654321}}.

Bitte in den Verteilern anpassen.

{{PERSON:Sven Osterhage}}

### id: de-022 | kind: negativ
Öffnungszeiten

Montag bis Freitag von 8:00 bis 18:00 Uhr
Samstag von 9:00 bis 13:00 Uhr

An Feiertagen bleibt das Büro geschlossen. Notdienst nach Vereinbarung.

### id: de-023 | kind: brief
Sehr geehrte Frau {{PERSON:Schneider}},

Ihre Bewerbung hat uns erreicht. Wir laden Sie zu einem Gespräch am 11.09. ein.

Bitte bestätigen Sie den Termin per Mail an {{EMAIL:bewerbung@musterfirma.de}}
oder telefonisch unter {{PHONE:0711 4455667}}.

Personalabteilung

### id: de-024 | kind: notiz
Kontaktdaten für den Notfall

Erste Ansprechpartnerin: {{PERSON:Dr. Sophie Baumgartner-Reiß}}
Mobil: {{PHONE:+49 176 12345678}}
Privat: {{PHONE:07531 987654}}
E-Mail: {{EMAIL:s.baumgartner-reiss@klinik-beispiel.de}}

### id: de-025 | kind: email
Sehr geehrter Herr {{PERSON:van den Berg}},

die Unterlagen sind unterwegs. Eine Kopie geht an {{PERSON:Petra de Vries}}.

Beste Grüße aus Münster
{{PERSON:Heinrich Wolters}}

### id: de-026 | kind: negativ
Zutaten für vier Personen: 500 g Mehl, 250 ml Milch, zwei Eier, eine Prise
Salz. Den Teig 30 Minuten ruhen lassen und anschließend bei 180 Grad etwa 25
Minuten backen.

### id: de-027 | kind: brief
Betreff: Kündigung zum nächstmöglichen Zeitpunkt

Sehr geehrte Damen und Herren,

hiermit kündige ich meinen Vertrag mit der Kundennummer 88213 zum
nächstmöglichen Zeitpunkt.

{{PERSON:Ulrike Mayer-Hofstetter}}
{{EMAIL:u.mayer-hofstetter@web-beispiel.de}}

### id: de-028 | kind: protokoll
Anwesenheitsliste

| Name | Abteilung | Durchwahl |
| --- | --- | --- |
| {{PERSON:Christoph Adler}} | Einkauf | 214 |
| {{PERSON:Ayşe Demir}} | Technik | 227 |
| {{PERSON:Bernd Zöllner}} | Vertrieb | 209 |

Zentrale: {{PHONE:+49 621 123400}}

### id: de-029 | kind: email
Moin,

{{PERSON:Kai}} ist ab Montag wieder da. Bis dahin übernimmt
{{PERSON:Jasmin Radtke}}, erreichbar unter {{EMAIL:j.radtke@nordwerk.de}}.

Gruß
{{PERSON:Kai Petersen}}

### id: de-030 | kind: negativ
Die Lieferung erfolgt innerhalb von drei bis fünf Werktagen. Bei Bestellungen
über 50 Euro entfallen die Versandkosten. Eine Sendungsverfolgung wird per
Systemmail versendet, sobald das Paket das Lager verlassen hat.

### id: de-031 | kind: brief
Sehr geehrter Herr {{PERSON:Dipl.-Ing. Gerhard Steinbach}},

die Prüfung der eingereichten Statik ist abgeschlossen. Beanstandungen liegen
nicht vor.

Rückfragen richten Sie bitte an {{EMAIL:pruefstelle@bauamt-beispiel.de}} oder
{{PHONE:0361 5544332}}.

### id: de-032 | kind: freitext
{{PERSON:Herbert Frühling}} und {{PERSON:Marion Herbst}} haben den Termin im
Frühling bestätigt, {{PERSON:Wolfgang Winter}} erst im Herbst. Die Namen sind
echt, die Jahreszeiten nicht gemeint.

### id: de-033 | kind: email
Weiterleitung:

Von: {{PERSON:Simone Grabowski}} <{{EMAIL:s.grabowski@partner-ag.de}}>
An: {{EMAIL:einkauf@musterfirma.de}}
Betreff: Angebot 2024-0912

Bitte um Prüfung bis Freitag.

### id: de-034 | kind: negativ
Hinweis zum Datenschutz

Personenbezogene Daten werden ausschließlich zur Vertragsabwicklung verarbeitet
und nach Ablauf der gesetzlichen Aufbewahrungsfristen gelöscht. Eine
Weitergabe an Dritte erfolgt nicht.

### id: de-035 | kind: notiz
Rückrufliste

{{PERSON:Frau Adamczyk}} — {{PHONE:0201 998877}} — dringend
{{PERSON:Herr Nowak}} — {{PHONE:+49 172 3344556}} — kann warten
{{PERSON:Familie Özdemir}} — {{PHONE:0234 112233}} — Termin verschieben

### id: de-036 | kind: brief
Sehr geehrte Frau {{PERSON:Ricarda von der Heide}},

im Nachgang zu unserem Gespräch übersende ich Ihnen die vereinbarte
Zusammenfassung. Für Rückfragen erreichen Sie mich jederzeit unter
{{EMAIL:r.vonderheide@kanzlei-beispiel.de}}.

Mit kollegialen Grüßen
{{PERSON:Dr. Matthias Ebert}}

### id: de-037 | kind: freitext
Kurz notiert: {{PERSON:Weiß}} hat zugesagt, {{PERSON:Weiss}} ebenfalls — das
ist dieselbe Person, sie schreibt sich nur unterschiedlich. Erreichbar über
{{EMAIL:weiss@beispiel.de}} und {{EMAIL:WEISS@BEISPIEL.DE}}.

### id: de-038 | kind: negativ
Wetterbericht für Norddeutschland: Am Vormittag stark bewölkt, zeitweise
Regen. Höchstwerte um 14 Grad. In der Nacht Abkühlung auf 6 Grad, örtlich
Nebelfelder. Der Wind weht mäßig aus West.
