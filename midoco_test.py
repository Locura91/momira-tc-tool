"""
Midoco Verbindungstest (Schritt 1)

Was das Skript tut:
  Es schickt eine absichtlich LEERE Buchungsnachricht an Midoco.
  Es kann dadurch keinen Auftrag anlegen. Midoco antwortet mit einer Fehlermeldung.
  Aus der Fehlermeldung sehen wir, ob Anmeldung, Token und IP-Filter stimmen.

Zugangsdaten stehen in midoco_config.json (nicht im Code, nicht in GitHub).
"""

import json
import sys

import requests

URL = "https://midoffice.midoco.net/ws/services/BookingService"

# Leere Nachricht: absichtlich ohne Inhalt, damit nichts angelegt wird.
ENVELOPE = """<?xml version="1.0" encoding="UTF-8"?>
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
    <bk:AnnounceBookingMessageNormRequest/>
  </soapenv:Body>
</soapenv:Envelope>
"""


def main():
    try:
        with open("midoco_config.json", encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        print("Die Datei midoco_config.json fehlt. Bitte aus der Beispieldatei anlegen.")
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

    print("Sende Testnachricht an Midoco ...")
    response = requests.post(URL, data=body.encode("utf-8"), headers=headers, timeout=30)

    print("HTTP-Status:", response.status_code)
    print("Antwort (Anfang):")
    print(response.text[:1500])


if __name__ == "__main__":
    main()
