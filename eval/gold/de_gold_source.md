# Deutsches Gold-Set — annotierte Quelle

Kompilieren mit `python -m privaparse.evaluation.build_gold`. Alles vor dem
ersten `### id:` wird ignoriert.

## Annotationsregeln

Entitäten werden inline markiert: `{{PERSON:Max Mustermann}}`, `{{EMAIL:...}}`,
`{{PHONE:...}}`. Seit der Erweiterung auf den vollen Katalog kommen dieselbe
Syntax auch für `{{IBAN:...}}`, `{{CARD:...}}`, `{{TAX_ID:...}}`, `{{IP:...}}`,
`{{POSTAL_CODE:...}}`, `{{ADDRESS:...}}`, `{{DATE_OF_BIRTH:...}}`,
`{{NATIONAL_ID:...}}`, `{{PASSPORT:...}}`, `{{ACCOUNT_ID:...}}` und
`{{USERNAME:...}}` zum Einsatz. Task 13 fügt `{{ROUTING_NUMBER:...}}`,
`{{CARD_EXPIRY:...}}`, `{{CARD_CVV:...}}`, `{{ACCOUNT_NUMBER:...}}`,
`{{CITY:...}}`, `{{REGION:...}}`, `{{COUNTRY:...}}`,
`{{DRIVERS_LICENSE:...}}`, `{{LICENSE_NUMBER:...}}`, `{{SECRET:...}}` und
`{{DATE:...}}` hinzu — die elf Typen, die bis dahin ohne jede Gold-Abdeckung
waren. Die Typnamen müssen exakt so geschrieben sein wie im Katalog
(`privaparse catalog show`); `test_every_gold_type_exists_in_the_catalogue`
prüft das.

- **Titel und Anreden gehören nicht zur Entität.** `Herr`, `Frau`, `Dr.`,
  `Prof.`, `Dipl.-Ing.` bleiben außerhalb der Markierung. Sie sind für sich
  genommen nicht identifizierend, und der Normalizer entfernt sie ohnehin.
- **Adelspartikel gehören dazu.** `von`, `von der`, `zu` sind in Deutschland
  Namensbestandteil. `Max von Bergen` ist nicht `Max Bergen`.
- **Firmen, Orte und Behörden werden nicht markiert.** Sie stehen bewusst im
  Set, um False Positives zu messen. Ein bloßer Orts- oder Postleitzahlname
  ohne Straße und Hausnummer ist keine `ADDRESS` — der Katalog verlangt dafür
  eine vollständige Anschrift oder zumindest Straße plus Hausnummer.
- **CITY, REGION und COUNTRY sind die eine Ausnahme von der vorigen Regel —
  markiert wird, wenn der Ort eine namentlich genannte Person identifizieren
  hilft:** Wohnort, Geburtsort oder Sitz einer Einzelperson (auch eines
  Einzelunternehmens, das denselben Namen trägt wie sein Inhaber). Ein Ort,
  der nur ein Unternehmen, eine Behörde oder — wie in de-006 — ein
  Vertriebsgebiet beschreibt, bleibt weiterhin unmarkiert; das ist dieselbe
  Unterscheidung wie bei ACCOUNT_ID zwei Punkte weiter unten, nur auf
  Ortsnamen angewendet. CITY und REGION sind im Katalog standardmäßig
  deaktiviert (`enabled: false`), COUNTRY ebenso — das ändert nichts daran,
  dass ihre Entitäten hier annotiert werden: Gold-Daten halten fest, was ein
  Typ richtig erkennen müsste, nicht, ob der Katalog seine Labels aktuell an
  das Modell sendet.
- **Aktenzeichen, Bestell-, Artikel- und Rechnungsnummern sowie Beträge werden
  nicht markiert.** Sie sehen Telefonnummern, Kartennummern oder Kontokennungen
  ähnlich genug, um die Precision zu testen.
- **Kunden-, Konto- und Mandantennummern werden als `ACCOUNT_ID` markiert**,
  wenn sie explizit eine Person oder ein Mandat referenzieren — das ist die
  Unterscheidung zur vorigen Regel: eine Rechnungsnummer identifiziert einen
  Vorgang, eine Kundennummer identifiziert einen Menschen.
- Telefonnummern werden vollständig markiert, so wie sie geschrieben stehen.
- **SECRET ist `reversible: false`.** Die markierten Werte werden nie
  wiederhergestellt, nur erkannt und durch einen Platzhalter ersetzt. Die
  Annotation hält trotzdem fest, wo im Text ein Secret steht — sonst wäre
  Recall für diesen Typ nicht messbar.
- **DATE markiert nur Daten, die eine Person betreffen.** de-122 bis de-124
  enthalten je ein Behandlungs-, Kündigungs- bzw. Haftantrittsdatum
  (markiert) und daneben, im selben Dokument, ein Dokumenten-,
  Besprechungs- bzw. Fristdatum (bewusst unmarkiert, obwohl es demselben
  Format folgt) — genau diese Unterscheidung soll die Messung treffen,
  nicht das Datumsformat. Der Task-Auftrag nennt zusätzlich Einstellungs-
  und Sterbedatum als Beispiele für die erste Gruppe; drei Dokumente
  decken drei der fünf genannten Beispiele ab, nicht alle fünf.
- **Meldedaten (seit wann eine Person unter einer Anschrift gemeldet ist)
  zählen als personenbetreffend und werden markiert.** Dieselbe Prüfung
  wie oben: Seit diesem Datum hat die Person einen bestimmten Status —
  strukturell derselbe Fall wie ein Einstellungs- oder Haftantrittsdatum,
  nur mit einem Wohnsitz statt einer Arbeitsstelle oder Haftanstalt als
  Status, und anders als ein Rechnungs- oder Dokumentendatum, das nichts
  über die Person aussagt, nur über einen Vorgang. de-105 und de-110 sind
  — nach Durchsicht des gesamten Korpus, nicht nur dieser beiden
  Dokumente — die einzigen Meldedaten, die überhaupt vorkommen; beide sind
  markiert. Der vertragliche Übergabetermin in de-053 (Mietvertrag) bleibt
  dagegen bewusst unmarkiert: er ist eine Vertragsklausel (wann der Besitz
  laut Vertrag übergeht, nicht seit wann die Person gemeldet ist) und
  liest sich näher an einem Dokumentendatum als an einem Meldedatum.
- **Batch A (IBAN, CARD, TAX_ID, IP, POSTAL_CODE) besteht aus Werten, die
  `generate_decidable()` erzeugt hat** — von Konstruktion aus gültig gegen
  ihren jeweiligen Validator, damit das Gold-Set den Checksum-Mechanismus
  prüft statt zufällig eine falsche Nummer zurückzuweisen. Drei der vier
  TAX_ID-Werte sind zusätzlich in Dreiergruppen umformatiert, wie eine
  Finanzamt-Steuer-ID amtlich gedruckt wird: `04826373520` — die exakte
  Ausgabe von `generate_decidable()` — steht im Korpus (de-048) als
  `04 826 373 520`; derselbe Wert, nur anders geschrieben. Die Gruppierung
  selbst ist keine Ausgabe des Generators, nur eine Schreibweise davon.
  Ein TAX_ID (de-047, `08170772018`) bleibt bewusst in dieser rohen,
  ungruppierten Form: das Modell erkennt die gruppierte Schreibweise nicht
  zuverlässig als `tax_id` (gemessene Recall 0.000), und ein Gold-Set, das
  nur die Schreibweise enthält, die das Modell schon beherrscht, würde
  genau die Lücke verstecken, die zu messen der Zweck dieses Typs ist.
- **Batch D (ROUTING_NUMBER, CARD_EXPIRY, CARD_CVV, ACCOUNT_NUMBER, Task 13)
  folgt demselben Prinzip wie Batch A** — Werte aus `generate_decidable()`,
  von Konstruktion aus gültig gegen ihren Validator. Eine Ausnahme:
  ACCOUNT_NUMBER hat keinen Validator, weder hier noch im Katalog — ein
  deutsches Kontonummernformat (8 bis 10 Stellen) hatte, anders als IBAN
  oder die Steuer-ID, nie eine einheitliche, bundesweite Prüfziffernmethode;
  das ist der Grund, warum IBAN es ersetzt hat. "Von Konstruktion aus"
  bedeutet für diesen einen Typ nur die Stellenzahl, nicht eine Prüfsumme,
  die es nicht gibt.

## Aufbau des Korpus

Kein Marker außerhalb von `### id:`-Blöcken: Der Compiler ignoriert nur Text
vor dem allerersten Dokument, alles danach landet sonst unbemerkt im Text des
vorherigen Dokuments. Zwischenüberschriften gibt es deshalb hier nicht — die
Batch-Grenzen stehen nur in dieser Präambel:

- **de-001 – de-038**: ursprüngliches Set. PERSON, EMAIL, PHONE, 10 Negative.
- **de-039 – de-050** (Batch A, 12 Dokumente): IBAN, CARD, TAX_ID, IP,
  POSTAL_CODE — generierte, checksum-gültige Werte im realistischen Kontext.
- **de-051 – de-068** (Batch B, 18 Dokumente): ADDRESS, DATE_OF_BIRTH,
  NATIONAL_ID, PASSPORT, ACCOUNT_ID, USERNAME — von Hand annotiert.
- **de-069 – de-091** (Batch C, 23 Dokumente): Negative. Kein einziger Marker
  in diesem Block ist ein Versehen — das Fehlen ist die Messung.
- **de-092 – de-103** (Batch D, 12 Dokumente, Task 13): ROUTING_NUMBER,
  CARD_EXPIRY, CARD_CVV, ACCOUNT_NUMBER — generiert, drei Dokumente je Typ.
- **de-104 – de-124** (Batch E, 21 Dokumente, Task 13): CITY, REGION,
  COUNTRY, DRIVERS_LICENSE, LICENSE_NUMBER, SECRET, DATE — von Hand
  annotiert, drei Dokumente je Typ. Vier dieser Typen (CITY, REGION,
  COUNTRY, DATE) sind im Katalog deaktiviert; annotiert sind sie trotzdem —
  siehe die Annotationsregel oben.

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

### id: de-039 | kind: mahnung
Zahlungserinnerung

Sehr geehrte Damen und Herren,

für unsere Rechnung Nr. 5521 vom 03.01.2026 über 480,00 EUR ist noch kein
Zahlungseingang zu verzeichnen. Wir bitten um Ausgleich bis zum 20.01.2026 auf
folgendes Konto:

IBAN: {{IBAN:DE44370400440144272509}}

Sollten Sie zwischenzeitlich bereits gezahlt haben, betrachten Sie dieses
Schreiben als gegenstandslos.

Buchhaltung

### id: de-040 | kind: mahnung
Zweite Mahnung

Musterhandel GmbH, {{POSTAL_CODE:39984}} Musterhausen

Sehr geehrte Damen und Herren,

trotz unserer Erinnerung vom 04.01.2026 ist der Rechnungsbetrag von 129,50 EUR
weiterhin offen. Wir bitten letztmalig um Überweisung bis zum 15.02.2026 auf:

IBAN: {{IBAN:DE78370400440611178002}}

Bei weiterem Zahlungsverzug behalten wir uns rechtliche Schritte vor.

Buchhaltung

### id: de-041 | kind: kontowechsel
Mitteilung einer Kontoänderung

Sehr geehrte Damen und Herren,

wir haben unsere Bankverbindung geändert. Bitte nutzen Sie ab sofort
ausschließlich die neue IBAN für Überweisungen an uns.

Alte IBAN (nicht mehr gültig): {{IBAN:DE81370400440909925047}}
Neue IBAN: {{IBAN:DE37370400440861425548}}

Wir bitten um entsprechende Anpassung in Ihrer Buchhaltung.

Mit freundlichen Grüßen

### id: de-042 | kind: zahlungsaufforderung
Zahlungsaufforderung

Für den Vertrag Nr. 88213 ist die Jahresrate zum 01.03.2026 fällig. Bitte
überweisen Sie 960,00 EUR auf folgendes Konto:

IBAN: {{IBAN:DE75370400440820096753}}

Verwendungszweck bitte die Vertragsnummer angeben.

### id: de-043 | kind: lastschrift
Einzugsermächtigung Vereinsbeitrag

Hiermit ermächtige ich den Verein, den Jahresbeitrag per Lastschrift von
folgendem Konto einzuziehen:

IBAN: {{IBAN:DE40370400440067760436}}

Meine hinterlegte Postleitzahl lautet {{POSTAL_CODE:87483}}.

Datum, Unterschrift

### id: de-044 | kind: bestellbestaetigung
Bestellbestätigung Nr. 2026-04471

Vielen Dank für Ihre Bestellung. Die Zahlung wurde erfolgreich mit folgender
Karte autorisiert:

Kartennummer: {{CARD:4342347850000005}}

Die Ware wird innerhalb von 3 bis 5 Werktagen versendet.

Ihr Musterhandel-Team

### id: de-045 | kind: bestellbestaetigung
Bestellbestätigung Nr. 2026-04512

Ihre Bestellung wurde erfasst. Belastet wird folgende hinterlegte Karte:

Kartennummer: {{CARD:5115826780000005}}

Eine Rechnung liegt der Lieferung bei.

Ihr Musterhandel-Team

### id: de-046 | kind: bestellbestaetigung
Bestellbestätigung Nr. 2026-04588

Zahlung per Kreditkarte eingegangen:

Kartennummer: {{CARD:3766496171000001}}

Lieferadresse: Versandzentrum Nord, {{POSTAL_CODE:23399}} Nordhafen

Ihr Musterhandel-Team

### id: de-047 | kind: steuerbescheid
Finanzamt Musterstadt
Referat 213

Steuerliche Identifikationsnummer: {{TAX_ID:08170772018}}

Sehr geehrte Steuerpflichtige,

Ihr Einkommensteuerbescheid für den Veranlagungszeitraum 2025 liegt diesem
Schreiben bei. Bitte prüfen Sie die Angaben auf Vollständigkeit.

Bei Rückfragen wenden Sie sich bitte unter Angabe der oben genannten Nummer an
das Finanzamt.

Finanzamt Musterstadt

### id: de-048 | kind: steuerbescheid
Finanzamt Musterstadt
Referat 118

Steuerliche Identifikationsnummer: {{TAX_ID:04 826 373 520}}

Sehr geehrter Steuerpflichtiger,

wir bestätigen den Eingang Ihrer Steuererklärung für das Jahr 2025. Die
Bearbeitung dauert erfahrungsgemäß sechs bis acht Wochen.

Finanzamt Musterstadt

### id: de-049 | kind: steuerbescheid
Finanzamt Musterstadt
Referat 213

Steuerliche Identifikationsnummern der Ehegatten:
{{TAX_ID:05 070 694 649}} und {{TAX_ID:06 996 426 306}}

Für die Zusammenveranlagung zur Einkommensteuer 2025 benötigen wir noch die
Kapitalertragsbescheinigung Ihrer Bank.

Finanzamt Musterstadt

### id: de-050 | kind: log
Serverlog-Auszug

2026-03-18 09:14:02 INFO  auth-service  login accepted  ip={{IP:98.107.48.125}}
2026-03-18 09:14:07 WARN  auth-service  rate limit triggered  ip={{IP:8.199.221.156}}
2026-03-18 09:15:44 ERROR gateway-service  upstream timeout after 3 retries  ip={{IP:196.1.228.69}}

### id: de-051 | kind: ummeldung
Ummeldung des Wohnsitzes

Sehr geehrte Damen und Herren,

hiermit teile ich Ihnen meinen Umzug mit. Meine neue Anschrift lautet:

{{ADDRESS:Kastanienallee 27, 04109 Leipzig}}

Bitte aktualisieren Sie Ihre Unterlagen entsprechend.

Mit freundlichen Grüßen
{{PERSON:Robert Lindemann}}

### id: de-052 | kind: paketankuendigung
Sendungsverfolgung

Ihr Paket wird morgen zwischen 10 und 14 Uhr zugestellt an:

{{ADDRESS:Rosenweg 9}}
{{PERSON:Julia Hartmann}}

Bei Abwesenheit hinterlegen wir eine Benachrichtigungskarte.

### id: de-053 | kind: mietvertragsnotiz
Notiz zum Mietvertrag

Der Mietvertrag für das Objekt {{ADDRESS:Talstraße 5, 70173 Stuttgart}} wurde
heute von beiden Parteien unterschrieben. Übergabetermin ist der 01.04.2026.

Mieterin: Frau {{PERSON:Carolin Wagner}}

### id: de-054 | kind: formular
Antragsformular Mitgliedschaft

Name: {{PERSON:Jonas Peters}}
Geburtsdatum: {{DATE_OF_BIRTH:14.09.1991}}
E-Mail: {{EMAIL:jonas.peters@beispiel.de}}

Ich beantrage hiermit die Aufnahme als Mitglied.

### id: de-055 | kind: personalakte
Personalakte — Ergänzung

{{PERSON:Melanie Schuster}}, geboren am {{DATE_OF_BIRTH:3. Februar 1988}}, hat
zum 01.05.2026 die Abteilung gewechselt.

Personalabteilung

### id: de-056 | kind: aufnahmebogen
Aufnahmebogen

Patient: {{PERSON:Karl-Heinz Ostermann}}
Geburtsdatum: {{DATE_OF_BIRTH:27.11.1957}}
Versicherung: gesetzlich

Aufnahmegrund: Vorsorgeuntersuchung

### id: de-057 | kind: identitaetspruefung
Identitätsprüfung

Zur Kontoeröffnung wurde der Personalausweis von {{PERSON:Sandra Kellner}}
geprüft. Ausweisnummer: {{NATIONAL_ID:L2XC00T4K9}}

Die Prüfung war erfolgreich, die Kopie wurde datenschutzkonform vernichtet.

### id: de-058 | kind: meldebestaetigung
Meldebestätigung

Hiermit bestätigen wir die Anmeldung von {{PERSON:Timo Brandes}} unter der
Identifikationsnummer {{NATIONAL_ID:96130508J020}}.

Einwohnermeldeamt

### id: de-059 | kind: bescheinigung
Bescheinigung

{{PERSON:Doris Feldmann}} ist bei uns unter der amtlichen Kennnummer
{{NATIONAL_ID:DE-93-4471-KL}} geführt.

Diese Bescheinigung dient ausschließlich internen Zwecken.

### id: de-060 | kind: visumantrag
Visumantrag — interne Notiz

Antragsteller: {{PERSON:Farid Amir}}
Reisepassnummer: {{PASSPORT:C01X0044T7}}
Gültig bis: 2029

Der Antrag wurde vollständig eingereicht.

### id: de-061 | kind: meldeschein
Meldeschein

Gast: {{PERSON:Ingrid Vollmer}}
Reisepass-Nr.: {{PASSPORT:PA7734215}}
Anreise: 12.06.2026, Abreise: 15.06.2026

Zimmer 214

### id: de-062 | kind: grenzkontrollvermerk
Interner Vermerk

Bei der Kontrolle wurde der Reisepass von {{PERSON:Elena Sokolova}} mit der
Nummer {{PASSPORT:70RUS884321}} vorgelegt und als gültig eingestuft.

### id: de-063 | kind: kundenschreiben
Sehr geehrte Damen und Herren,

zu meiner Kundennummer {{ACCOUNT_ID:KD-4471-2298}} bitte ich um Übersendung
einer aktuellen Vertragsübersicht.

Mit freundlichen Grüßen
{{PERSON:Bernhard Sailer}}

### id: de-064 | kind: aktennotiz
Aktennotiz

Für unseren Mandanten {{PERSON:Heike Brandl}} wurde die Mandantennummer
{{ACCOUNT_ID:M-2026-0317}} angelegt. Die Akte ist damit eröffnet.

### id: de-065 | kind: supportticket
Support-Ticket #88213-A

Kunde: {{PERSON:Oliver Krahl}}
Kontokennung: {{ACCOUNT_ID:acc_7f3e9b21}}

Anliegen: Zugriff auf das Kundenportal ist nicht möglich, Fehlermeldung
"Sitzung abgelaufen".

### id: de-066 | kind: onboarding
IT-Onboarding

Für {{PERSON:Nadine Kruse}} wurde ein Zugang eingerichtet.
Benutzername: {{USERNAME:n.kruse}}

Das Erstpasswort wird separat per Post zugestellt.

### id: de-067 | kind: forenprofil
Community-Hinweis

Der Beitrag von {{USERNAME:coder_flitzer99}} wurde von der Moderation
geprüft und freigegeben.

### id: de-068 | kind: zugangsprotokoll
Zugriffsprotokoll

Anmeldename {{USERNAME:j_bauer84}} hat sich um 08:42 Uhr erfolgreich am
System angemeldet.

### id: de-069 | kind: negativ
Az. 12 C 45/26

Termin zur mündlichen Verhandlung: 14.04.2026, 10:30 Uhr, Saal 3.

Die Parteien werden gebeten, fünfzehn Minuten vor Beginn zu erscheinen.

### id: de-070 | kind: negativ
Geschäftszeichen: 4 O 231/24

In der Sache liegt derzeit keine Entscheidung vor. Der nächste Verfahrensschritt
ist für das dritte Quartal 2026 vorgesehen.

### id: de-071 | kind: negativ
Bestellübersicht

Artikel-Nr. 4342347850000001, Aktenordner, 10 Stück
Artikel-Nr. 7723119055406683, Druckerpapier, 5 Pakete

Rechnungsnummer 2026-33871.

### id: de-072 | kind: negativ
Rechnung

Rechnungsnummer: 5521-2026
Bestellnummer: 9981774400221193
Artikelnummer: 3301-A

Alle Preise verstehen sich inklusive gesetzlicher Mehrwertsteuer.

### id: de-073 | kind: negativ
Versionshinweise

Aktuelle Version: v2.13.0+cu130
Vorherige Version: v2.12.4+cu128

Änderungen: verbesserte Speicherverwaltung, mehrere Fehlerbehebungen im
Tokenizer.

### id: de-074 | kind: negativ
Build-Protokoll

build 20260318 — Status: erfolgreich
build 20260317 — Status: fehlgeschlagen (Timeout beim Kompilieren)

Laufzeit des letzten Builds: 14 Minuten 22 Sekunden.

### id: de-075 | kind: negativ
Stadtentwicklung

Die geplante Marktplatzsanierung im Bahnhofsviertel soll im kommenden Jahr
beginnen. Betroffen sind auch die Fußgängerzone und der Kirchplatz.

Eine Bürgerversammlung ist für den Herbst vorgesehen.

### id: de-076 | kind: negativ
Verkehrsmeldung

Wegen Bauarbeiten ist die Umgehungsstraße am Gewerbegebiet Nordfeld bis auf
Weiteres nur einspurig befahrbar. Der Kreisverkehr am Industriering bleibt
gesperrt.

Eine Umleitung über die Ringstraße wird empfohlen.

### id: de-077 | kind: negativ
Quartalszahlen

| Quartal | Umsatz (TEUR) | Kosten (TEUR) |
| --- | ---: | ---: |
| Q1 | 412 | 355 |
| Q2 | 468 | 371 |
| Q3 | 501 | 389 |
| Q4 | 533 | 402 |

Die Zahlen sind vorläufig und ungeprüft.

### id: de-078 | kind: negativ
Lagerbestand

| Artikel | Bestand | Mindestbestand |
| --- | ---: | ---: |
| Ordner A4 | 340 | 100 |
| Klarsichthüllen | 1200 | 300 |
| Tonerkartuschen | 48 | 20 |

Nachbestellung erfolgt automatisch bei Unterschreitung.

### id: de-079 | kind: negativ
def compute_total(items):
    total = 0
    for item in items:
        total += item.price * item.quantity
    return round(total, 2)

class Cart:
    def __init__(self):
        self.items = []

### id: de-080 | kind: negativ
SELECT id, status, created_at
FROM orders
WHERE status = 'pending'
  AND created_at < NOW() - INTERVAL '7 days'
ORDER BY created_at ASC
LIMIT 100;

### id: de-081 | kind: negativ
Sehr geehrte Damen und Herren,

wir bestätigen den Eingang Ihres Schreibens und werden uns in Kürze mit einer
Rückmeldung bei Ihnen melden.

Mit freundlichen Grüßen
Die Geschäftsleitung

### id: de-082 | kind: negativ
Liebe Mitglieder,

die diesjährige Jahreshauptversammlung findet im September statt. Genaue
Informationen zu Ort und Uhrzeit folgen in Kürze.

Der Vorstand

### id: de-083 | kind: negativ
Terminübersicht

Kick-off: 3. März 2026
Zwischenbericht: 15.06.2026
Abschlusspräsentation: 2026-09-30
Nachbesprechung: Montag, der 12. Oktober 2026

Alle Termine finden im Hauptgebäude statt.

### id: de-084 | kind: negativ
Fristenkalender

Einreichung der Unterlagen bis 01.02.26.
Widerspruchsfrist endet am 28.02.2026.
Zahlungsziel: 31. März 2026.
Nächste Überprüfung: 01/07/2026.

Verspätete Einreichungen können nicht berücksichtigt werden.

### id: de-085 | kind: negativ
Preisliste 2026

Beratungsstunde: 95,00 EUR
Express-Zuschlag: 25,00 EUR
Versandkosten Inland: 4,90 EUR
Versandkosten Ausland: 14,90 EUR

Alle Preise zzgl. gesetzlicher Mehrwertsteuer.

### id: de-086 | kind: negativ
Angebot

Grundpaket: 1.200,00 EUR
Zusatzmodul Reporting: 350,00 EUR
Wartung pro Jahr: 480,00 EUR

Gültig bis 30.04.2026.

### id: de-087 | kind: negativ
Bankleitzahl-Änderung

Im Zuge der Fusion ändert sich die Bankleitzahl unserer Filiale auf 37040044.
Ihre Kontonummer und IBAN bleiben unverändert.

Bei Fragen steht Ihnen unsere Hotline zur Verfügung.

### id: de-088 | kind: negativ
Filialinformation

Unsere neue Filiale in der Innenstadt führt ab sofort die Bankleitzahl
50010517. Kontoeröffnungen sind ab Montag möglich.

Öffnungszeiten: Montag bis Freitag, 9 bis 17 Uhr.

### id: de-089 | kind: negativ
Auszug aus den Nutzungsbedingungen

§ 5 Haftungsausschluss

Der Anbieter haftet nicht für mittelbare Schäden, entgangenen Gewinn oder
Datenverlust, soweit gesetzlich zulässig. § 9 Abs. 3 bleibt hiervon unberührt.

Gerichtsstand ist der Sitz des Anbieters.

### id: de-090 | kind: negativ
Tagesordnung Fachkonferenz

09:00 Registrierung
09:30 Eröffnung, Raum A1
10:15 Vortragsreihe, Raum B2
12:00 Mittagspause
13:30 Workshops, Räume C1 bis C4
16:00 Abschlussdiskussion, Raum A1

Änderungen im Programmablauf vorbehalten.

### id: de-091 | kind: negativ
Produktdatenblatt

Abmessungen: 45 x 30 x 12 cm
Gewicht: 3,4 kg
Material: eloxiertes Aluminium
Betriebstemperatur: -10 °C bis 50 °C

Die technischen Daten können sich ohne Ankündigung ändern.

### id: de-092 | kind: ueberweisungsauftrag
Überweisungsauftrag

Bitte überweisen Sie folgenden Betrag von unserem Geschäftskonto:

Empfänger: Elektro Baumann GmbH
Bankleitzahl: {{ROUTING_NUMBER:42604684}}
Betrag: 640,00 EUR
Verwendungszweck: Rechnung 2026-0788

Ausführung bitte bis zum Monatsende.

### id: de-093 | kind: auslandsueberweisung
Auslandsüberweisung

Für die Überweisung nach Übersee benötigen wir zusätzlich zur IBAN den
SWIFT/BIC-Code Ihrer Bank.

BIC: {{ROUTING_NUMBER:AAAUDE8A}}
Betrag: 2.150,00 USD
Verwendungszweck: Warenlieferung Auftrag 4471

Bitte bestätigen Sie den Code vor Ausführung.

### id: de-094 | kind: bankverbindung
Bankverbindung für künftige Zahlungen

Sehr geehrte Damen und Herren,

für alle künftigen Überweisungen nutzen Sie bitte folgende Bankverbindung:

BIC: {{ROUTING_NUMBER:MVGNDEB7O25}}

Eine Bestätigung des Zahlungseingangs erhalten Sie jeweils per E-Mail.

### id: de-095 | kind: kartenbestaetigung
Bestätigung der hinterlegten Zahlungskarte

Ihre im Kundenkonto hinterlegte Kreditkarte ist nur noch bis
{{CARD_EXPIRY:09/30}} gültig. Bitte aktualisieren Sie Ihre Zahlungsdaten
rechtzeitig, damit es nicht zu Unterbrechungen bei der
Abonnementverlängerung kommt.

### id: de-096 | kind: kartenaktualisierung
Zahlungsdaten aktualisiert

Vielen Dank. Wir haben Ihre neue Karte mit Gültigkeit bis
{{CARD_EXPIRY:06/2030}} erfolgreich hinterlegt. Die nächste Abbuchung
erfolgt wie gewohnt zum Monatsersten.

### id: de-097 | kind: ablaufhinweis
Hinweis: Karte läuft bald ab

Die für Ihr Abonnement hinterlegte Karte ist gültig bis
{{CARD_EXPIRY:11/30}}. Nach Ablauf können wir den Betrag nicht mehr
automatisch einziehen — bitte hinterlegen Sie rechtzeitig eine neue Karte.

### id: de-098 | kind: sicherheitsvorfall
Sicherheitsvorfall — interner Bericht

Bei einer Prüfung wurde festgestellt, dass ein Mitarbeiter die
Kartenprüfnummer eines Kunden versehentlich im Support-Chat
mitprotokolliert hat: {{CARD_CVV:779}}. Der Chatverlauf wurde umgehend aus
dem System entfernt und der Kunde informiert.

### id: de-099 | kind: supportticket
Support-Ticket #55219-B

Kunde: {{PERSON:Robert Nickel}}

Der Kunde hat im Chat zur Verifizierung seiner Zahlung versehentlich auch
die Kartenprüfnummer genannt: {{CARD_CVV:7530}}.

Der Vorgang wurde an die Datenschutzbeauftragte gemeldet, damit der
Chatverlauf bereinigt wird.

### id: de-100 | kind: datenpanne
Meldung einer Datenpanne

Im Rahmen der Untersuchung wurde festgestellt, dass eine fehlerhaft
exportierte Protokolldatei auch die Kartenprüfnummer {{CARD_CVV:975}} eines
einzelnen Kunden enthielt. Die Datei wurde gelöscht, betroffene Systeme
wurden bereinigt.

### id: de-101 | kind: kontoauszug
Kontoauszug Nr. 3/2026

Kontonummer: {{ACCOUNT_NUMBER:38893829}}
Zeitraum: 01.03.2026 – 31.03.2026

Anfangssaldo: 1.842,17 EUR
Endsaldo: 2.015,63 EUR

Bei Fragen zu einzelnen Buchungen wenden Sie sich an Ihre Filiale.

### id: de-102 | kind: dauerauftrag
Bestätigung Dauerauftrag

Ab sofort wird monatlich ein Betrag von 250,00 EUR vom Konto
{{ACCOUNT_NUMBER:994828918}} abgebucht.

Die erste Ausführung erfolgt zum nächsten Monatsersten.

### id: de-103 | kind: kontoeroeffnung
Bestätigung der Kontoeröffnung

Ihr neues Konto wurde erfolgreich eingerichtet.

Kontonummer: {{ACCOUNT_NUMBER:4387264885}}

Die dazugehörige Bankkarte erhalten Sie innerhalb der nächsten fünf
Werktage per Post.

### id: de-104 | kind: geburtsbescheinigung
Geburtsbescheinigung — Auszug

Hiermit wird bestätigt, dass {{PERSON:Fabian Kessler}} am
{{DATE_OF_BIRTH:14.03.1990}} in {{CITY:Freiburg im Breisgau}} geboren
wurde.

Die Bescheinigung dient der Vorlage beim Standesamt.

### id: de-105 | kind: wohnsitzbestaetigung
Wohnsitzbestätigung

Hiermit bestätigen wir, dass {{PERSON:Nora Vogt}} seit dem
{{DATE:03.02.2019}} mit Hauptwohnsitz in {{CITY:Bonn}} gemeldet ist.

Diese Bestätigung wird auf Antrag der Antragstellerin ausgestellt.

### id: de-106 | kind: gewerbeanmeldung
Gewerbeanmeldung — Bestätigung

Die Anmeldung des Einzelunternehmens von {{PERSON:Julius Ahrens}} wurde
bearbeitet.

Sitz des Unternehmens: {{CITY:Trier}}
Tätigkeit: IT-Beratung

Die Gewerbekarte wird in den nächsten Tagen zugestellt.

### id: de-107 | kind: personalfragebogen
Personalfragebogen

Name: {{PERSON:Annegret Lindqvist}}
Geburtsort: {{CITY:Rostock}}, {{REGION:Mecklenburg-Vorpommern}}
Aktuelle Tätigkeit: Sachbearbeiterin

Der Fragebogen wird in der Personalakte abgelegt.

### id: de-108 | kind: versicherungsantrag
Versicherungsantrag — interne Notiz

Der Wohnsitz von {{PERSON:Matthias Ebeling}} liegt im Bundesland
{{REGION:Baden-Württemberg}}, was für die Tarifberechnung der
Kfz-Versicherung maßgeblich ist.

Der Antrag wird zur Prüfung weitergeleitet.

### id: de-109 | kind: praxisanmeldung
Praxisanmeldung

Die private Praxis von Dr. {{PERSON:Rebecca Zysset}} hat ihren Sitz im
Kanton {{REGION:Zürich}}.

Die Anmeldung wurde durch die zuständige kantonale Stelle bestätigt.

### id: de-110 | kind: meldebestaetigung
Meldebestätigung

{{PERSON:Elif Kurtuluş}}, geboren in {{CITY:Izmir}}, {{COUNTRY:Türkei}}, ist
seit dem {{DATE:01.09.2025}} in der Gemeinde gemeldet.

Diese Bestätigung wird auf Antrag ausgestellt.

### id: de-111 | kind: nachlassvermerk
Nachlassangelegenheit — interner Vermerk

Der Erbe, {{PERSON:Jonathan Reyes}}, ist wohnhaft in {{COUNTRY:Kanada}}.
Die Korrespondenz erfolgt daher ausschließlich postalisch mit
internationalen Laufzeiten.

Der Fall bleibt bis zur Rückmeldung ruhend.

### id: de-112 | kind: auftragsbestaetigung
Auftragsbestätigung

Die Beratungsleistung wird von {{PERSON:Helena Marques}} erbracht, deren
Einzelunternehmen seinen Sitz in {{COUNTRY:Portugal}} hat.

Die Rechnungsstellung erfolgt in Euro.

### id: de-113 | kind: uebergabeprotokoll
Mietwagen-Übergabeprotokoll

Fahrer: {{PERSON:Tobias Rehmer}}
Führerscheinnummer: {{DRIVERS_LICENSE:B072RRE2I95}}
Fahrzeug: VW Golf, Kennzeichen wird separat erfasst

Der Führerschein wurde bei Übergabe im Original vorgelegt und geprüft.

### id: de-114 | kind: fahrerfreigabe
Fahrerfreigabe — interne Prüfung

Für {{PERSON:Kerstin Ohlendorf}} wurde die Fahrerlaubnis geprüft.
Führerscheinnummer: {{DRIVERS_LICENSE:AEP84200LH3}}, Klasse B, gültig bis
2031.

Die Freigabe für den Fuhrpark wurde erteilt.

### id: de-115 | kind: unfallanzeige
Unfallanzeige

Unfallverursacher: {{PERSON:Milan Sedlák}}
Führerscheinnummer: {{DRIVERS_LICENSE:C511009XN27}}

Der Schaden wird durch die Kfz-Haftpflichtversicherung des Verursachers
reguliert.

### id: de-116 | kind: approbationsbestaetigung
Bestätigung der Approbation

Hiermit wird bestätigt, dass Dr. {{PERSON:Yasmin Roth}} unter der
Approbationsnummer {{LICENSE_NUMBER:AP-2014-08823}} als Ärztin zugelassen
ist.

Die Bestätigung dient der Vorlage bei der Krankenversicherung.

### id: de-117 | kind: zulassungsbescheinigung
Zulassungsbescheinigung

{{PERSON:Sebastian Vogler}} ist bei der Rechtsanwaltskammer unter der
Zulassungsnummer {{LICENSE_NUMBER:RAK-33871-M}} als Rechtsanwalt
registriert.

Die Bescheinigung gilt für das laufende Kalenderjahr.

### id: de-118 | kind: personenbefoerderungsschein
Bestätigung Personenbeförderungsschein

Der Personenbeförderungsschein wurde {{PERSON:Klaus-Dieter Ahrend}} unter
der Nummer {{LICENSE_NUMBER:PBEF-4471-NRW}} erteilt.

Der Schein ist an die Person gebunden und nicht übertragbar.

### id: de-119 | kind: email
Betreff: Zugangsdaten für die Staging-Umgebung

Hallo,

hier die Konfigurationszeile, die du für die lokale .env brauchst:

API_KEY={{SECRET:svc_9f2ac4e7d8b14f0aa2c6e5d1b7f30948_prod}}

Bitte nicht im Repository committen.

Viele Grüße
{{PERSON:Dennis Kowalski}}

### id: de-120 | kind: supportticket
Support-Ticket #77102

Kunde: {{PERSON:Priya Nair}}

Beschreibung: Die Anwendung meldet einen Authentifizierungsfehler. Zur
Fehlersuche habe ich testweise den aktuellen Zugriffstoken angehängt:

Bearer {{SECRET:eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyXzQ0NzEifQ.k3F9pQ}}

Bitte prüfen, ob der Token abgelaufen ist.

### id: de-121 | kind: uebergabenotiz
Übergabenotiz

Für die Dauer meines Urlaubs übernimmt {{PERSON:Sascha Lindner}} die
Betreuung des Servers.

Zugangsdaten Admin-Panel: Benutzer admin, Passwort
{{SECRET:Gr3en!Fjord42_Xk}}

Bitte nach Rückkehr ändern.

### id: de-122 | kind: patientenakte
Patientenakte — Ergänzung

{{PERSON:Holger Brandes}} wurde am {{DATE:22.01.2026}} notfallmäßig
operiert. Der Eingriff verlief ohne Komplikationen, die Nachsorge erfolgt
ambulant.

Dieser Vermerk wurde am 24.01.2026 in die Akte aufgenommen.

### id: de-123 | kind: personalvorgang
Personalvorgang — Vermerk zur Kündigung

Das Arbeitsverhältnis mit {{PERSON:Yvonne Talbot}} endet auf Veranlassung
des Arbeitgebers zum {{DATE:31.03.2026}}.

Die Entscheidung wurde in der Besprechung der Geschäftsleitung am
15.02.2026 getroffen und Frau {{PERSON:Talbot}} im Anschluss persönlich
mitgeteilt.

### id: de-124 | kind: vollzugsvermerk
Vollzugsvermerk

{{PERSON:Radovan Němec}} trat die Freiheitsstrafe am {{DATE:03.02.2026}} in
der Justizvollzugsanstalt an.

Der nächste Überprüfungstermin zur Vollzugsplanung ist bis spätestens
03.08.2026 anzusetzen.
