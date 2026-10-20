"""
Midoco Hotel-Test (Schritt 4)

Schickt eine Hotel-Testbuchung mit Fantasie-Daten an Midoco (Org-Einheit: Momira Travel Test).
XML-Struktur basiert auf midoco_booking_message_norm.xsd (Version 20.4).

Wichtig: Der supplier-Code ("TESTHOTEL01") muss als Stammdaten in Midoco existieren,
sonst schlägt der Import fehl. Ggf. zuerst einen Test-Lieferanten anlegen.

Zugangsdaten: midoco_config.json (nicht in GitHub).
"""

import json
import sys

import requests

URL = "https://midoffice.midoco.net/ws/services/BookingService"

# Namespace-Präfixe:
#   soapenv  = SOAP Envelope
#   sys      = Midoco System (Credentials)
#   bk       = Midoco Booking (Request-Wrapper)
#   mbm      = booking_message_norm (Buchungsinhalt)
ENVELOPE = """\
<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope
    xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:sys="http://www.midoco.de/system"
    xmlns:bk="http://www.midoco.de/booking"
    xmlns:mbm="http://www.midoco.de/booking_message_norm">
  <soapenv:Header>
    <sys:MidocoCredentials>
      <sys:Login>{login}</sys:Login>
      <sys:Password>{password}</sys:Password>
      <sys:OrganisationUnit>{org_unit}</sys:OrganisationUnit>
    </sys:MidocoCredentials>
  </soapenv:Header>
  <soapenv:Body>
    <bk:AnnounceBookingMessageNormRequest>
      <mbm:midoco_booking_message_norm>

        <!-- agencyIdentity: Pflichtblock, alle Felder optional -->
        <mbm:agencyIdentity/>

        <!-- Kunde / Bucher -->
        <mbm:customer type="INVOICE">
          <mbm:privateData>
            <mbm:salutation>Herr</mbm:salutation>
            <mbm:forename>Max</mbm:forename>
            <mbm:name>Mustermann</mbm:name>
          </mbm:privateData>
          <mbm:street>Musterstraße 1</mbm:street>
          <mbm:postalCode>10115</mbm:postalCode>
          <mbm:city>Berlin</mbm:city>
          <mbm:country>DE</mbm:country>
          <mbm:communication type="EMAIL">
            <mbm:communicationData>max.mustermann@example.com</mbm:communicationData>
          </mbm:communication>
          <mbm:communication type="PHONE">
            <mbm:communicationData>+49301234567</mbm:communicationData>
          </mbm:communication>
        </mbm:customer>

        <!-- Buchung: supplier = LT-Code aus Midoco Stammdaten -->
        <mbm:booking supplier="MOMIRA_SP_TEST" position="1" bookingId="TC-TEST-001">

          <!-- Reisende (name ist Pflicht, forename optional) -->
          <mbm:person position="1">
            <mbm:salutation>Herr</mbm:salutation>
            <mbm:forename>Max</mbm:forename>
            <mbm:name>Mustermann</mbm:name>
            <mbm:birthday>1985-03-15</mbm:birthday>
          </mbm:person>
          <mbm:person position="2">
            <mbm:salutation>Frau</mbm:salutation>
            <mbm:forename>Erika</mbm:forename>
            <mbm:name>Mustermann</mbm:name>
            <mbm:birthday>1988-07-22</mbm:birthday>
          </mbm:person>

          <!-- Hotel-Leistung (accomodationService) -->
          <mbm:accomodationService position="1" supplierId="MOMIRA_SP_TEST"
              bookingId="TC-TEST-001">
            <!-- Pflichtfelder ohne minOccurs="0" -->
            <mbm:serviceCode>H</mbm:serviceCode>
            <mbm:serviceName>Hotel Muster Mallorca</mbm:serviceName>
            <mbm:accomodation>DBL</mbm:accomodation>
            <mbm:accomodationDescription>Doppelzimmer Standard</mbm:accomodationDescription>
            <mbm:catering>BB</mbm:catering>
            <mbm:cateringDescription>Übernachtung mit Frühstück</mbm:cateringDescription>
            <mbm:locationDescription>Mallorca, Spanien</mbm:locationDescription>
            <!-- allocation: Anzahl Personen pro Einheit (muss Integer-String sein) -->
            <mbm:allocation>2</mbm:allocation>
            <!-- noOfServices: Anzahl Einheiten (hier 1x Doppelzimmer) -->
            <mbm:noOfServices>1</mbm:noOfServices>
            <mbm:noOfNights>7</mbm:noOfNights>
            <mbm:startDate>2026-12-15</mbm:startDate>
            <mbm:endDate>2026-12-22</mbm:endDate>
            <mbm:serviceStatus>OK</mbm:serviceStatus>
            <!-- personAssignment: Verweis auf person.position (1-2 = Personen 1 und 2) -->
            <mbm:personAssignment>1-2</mbm:personAssignment>
            <mbm:totalPrice>1499.00</mbm:totalPrice>
            <mbm:currency>EUR</mbm:currency>
          </mbm:accomodationService>

        </mbm:booking>

        <!-- Preissektion: Pflicht, referenziert Buchung über position -->
        <mbm:prices>
          <mbm:bookingPrice position="1">
            <mbm:supplier>MOMIRA_SP_TEST</mbm:supplier>
            <mbm:bookingId>TC-TEST-001</mbm:bookingId>
            <mbm:totalPrice>1499.00</mbm:totalPrice>
            <mbm:currency>EUR</mbm:currency>
          </mbm:bookingPrice>
        </mbm:prices>

      </mbm:midoco_booking_message_norm>
    </bk:AnnounceBookingMessageNormRequest>
  </soapenv:Body>
</soapenv:Envelope>
"""


def main():
    try:
        with open("midoco_config.json", encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        print("midoco_config.json fehlt. Bitte aus midoco_config.example.json anlegen.")
        sys.exit(1)

    body = ENVELOPE.format(
        login=cfg["login"],
        password=cfg["password"],
        org_unit=cfg["org_unit"],
    )

    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": "announceBookingMessageNorm",
        "Authorization": "Bearer " + cfg["token"],
    }

    print("Sende Hotel-Testbuchung an Midoco ...")
    print(f"Org-Einheit : {cfg['org_unit']}")
    print(f"BookingId   : TC-TEST-001")
    print(f"Supplier    : MOMIRA_SP_TEST (Spanien Test DMC)")
    print()

    response = requests.post(URL, data=body.encode("utf-8"), headers=headers, timeout=30)

    print(f"HTTP-Status : {response.status_code}")
    print()
    print("Antwort:")
    print(response.text)


if __name__ == "__main__":
    main()
