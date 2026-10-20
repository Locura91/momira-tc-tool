"""
Midoco Hotel-Test (Schritt 4)

Schickt eine Hotel-Testbuchung mit Fantasie-Daten an Midoco (Org-Einheit: Momira Travel Test).
Ziel: herausfinden welche Pflichtfelder fehlen, bis ein Auftrag angelegt wird.

Zugangsdaten: midoco_config.json (nicht in GitHub).
"""

import json
import sys

import requests

URL = "https://midoffice.midoco.net/ws/services/BookingService"

ENVELOPE = """\
<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope
    xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:sys="http://www.midoco.de/system"
    xmlns:bk="http://www.midoco.de/booking">
  <soapenv:Header>
    <sys:MidocoCredentials>
      <sys:Login>{login}</sys:Login>
      <sys:Password>{password}</sys:Password>
      <sys:OrganisationUnit>{org_unit}</sys:OrganisationUnit>
    </sys:MidocoCredentials>
  </soapenv:Header>
  <soapenv:Body>
    <bk:AnnounceBookingMessageNormRequest>
      <bk:MidocoBookingMessageNorm>

        <!-- ===== Auftragskopf ===== -->
        <bk:booking
            bookingId="TC-TEST-001"
            externalSystemId="TravelCompositor"
            bookingDate="2026-10-20"
            travelStartDate="2026-12-15"
            travelEndDate="2026-12-22"
            noOfTravellers="2"
            totalPrice="1499.00"
            currency="EUR"
            salesOrganisationUnit="{org_unit}"
            packageId="TC-TEST-001">

          <!-- ===== Reisende ===== -->
          <bk:traveller
              travellerNo="1"
              salutation="MR"
              firstName="Max"
              lastName="Mustermann"
              dateOfBirth="1985-03-15"
              nationality="DE"/>
          <bk:traveller
              travellerNo="2"
              salutation="MRS"
              firstName="Erika"
              lastName="Mustermann"
              dateOfBirth="1988-07-22"
              nationality="DE"/>

          <!-- ===== Kunde / Bucher ===== -->
          <bk:customer
              salutation="MR"
              firstName="Max"
              lastName="Mustermann"
              street="Musterstraße 1"
              zip="10115"
              city="Berlin"
              country="DE"
              email="max.mustermann@example.com"
              phone="+49301234567"/>

          <!-- ===== Hotel-Leistung ===== -->
          <bk:service
              serviceNo="1"
              serviceType="H"
              startDate="2026-12-15"
              endDate="2026-12-22"
              description="Hotel Muster Mallorca - DZ"
              providerCode="TESTHOTEL01"
              providerName="Test Hotel Mallorca"
              destinationCode="PMI"
              destinationName="Mallorca"
              noOfNights="7"
              roomType="DBL"
              boardType="BB"
              noOfPersons="2"
              serviceStatus="OK"
              singlePrice="749.50"
              totalPrice="1499.00"
              currency="EUR"/>

        </bk:booking>
      </bk:MidocoBookingMessageNorm>
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
    print(f"Org-Einheit: {cfg['org_unit']}")
    print(f"BookingId:   TC-TEST-001")
    print()

    response = requests.post(URL, data=body.encode("utf-8"), headers=headers, timeout=30)

    print(f"HTTP-Status: {response.status_code}")
    print()
    print("Antwort:")
    print(response.text)


if __name__ == "__main__":
    main()
