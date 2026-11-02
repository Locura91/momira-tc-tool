# Travel Compositor API — Full Reference

Generated from the live OpenAPI 3.0.1 spec at `https://momira.travel/admin/api/swagger.json` (account-specific Swagger UI; underlying server base: `https://online.travelcompositor.com/resources`).

**Total endpoints:** 191 operation entries (endpoints counted once per tag they belong to) across **37 tags**. **Total component schemas in the spec:** 590.

Field tables show one schema level at a time: a field whose type is itself an object/array-of-object links to that schema's own entry in the **Schemas Appendix** at the end of this document, rather than being expanded inline everywhere it's used (the same schema, e.g. an address or a price breakdown, is referenced from dozens of places — expanding it inline every time would make this document unusably large and repetitive). Follow the links to go deeper.

### Quick reference: flight/booking data (confirmed ground truth from this spec)

- `GET /booking/bookings` → `BookingsVO` → `bookings[]` of **`BookingsBasicDataVO`** — this is only a lightweight pointer: `bookingReference`, `creationDate`, `micrositeId`, `status`. It does **not** contain flight/segment data. Filter params: `from`/`to` (creation date, required), `fromDepartureDate`/`toDepartureDate` (departure date — use this for a "next 48h" window), `ufrom`/`uto`, `cfrom`/`cto`, `ref` (BNR/PNR/provider ref), `first`/`limit` (pagination, query-param style), `operator`/`microsite`/`username`.
- For actual flight data, call `GET /booking/getBookings/{micrositeId}/{bookingReference}` per reference → returns the full **`BookedTripVO`**.
- On `BookedTripVO`, flight/transport data appears in up to three places:
  - `transportservice[]` → `BookedTransportServiceVO` (has `departureAirport`/`arrivalAirport` at the service level, plus `segment[]` → **`BookedTransportSegmentVO`**, which carries `flightNumber`, `departureAirport`, `arrivalAirport`, `departureAirportName`, `arrivalAirportName`, `departureDate`/`arrivalDate` (full ISO datetime), `marketingAirlineCode`/`operatingAirlineCode`, `bookingClass`, `cabinClass`, `durationInMinutes`, `transportType`, `technicalStops[]`, `segmentKey`, `seat[]`).
  - `manualServices.transport[]` → `BookedTransportManualServiceVO` (flatter: `flightNumber`, `pnr`, `company`, `origin`, `destination`, `departureHour`, `arrivalHour`).
  - `closedtourservice[].flightDetails[]` → `BookedClosedTourTransportServiceFlightDetailVO` (`flightNumber`, `departureAirport`, `departureTime`, `arrivalAirport`, `arrivalTime`, `cabintype`).
- Passenger data: `adultCount`/`childCount`/`infantCount` on `BookedTripVO` directly; per-person detail (age, birth date) via `distribution[]` → `DistributionVO.person[]` → `PersonVO` (`requestedAge` is **required**, plus `birthDate`, `name`, `lastName`, etc.); per-segment fare-by-person via `personFareBreakdown[]` → `TransportFareBreakdownVO.personId`.

## Table of Contents

- [Booking](#booking)
- [Booking - Client Request](#booking---client-request)
- [Book - Transports](#book---transports)
- [Book - Multi-engine](#book---multi-engine)
- [Contract - Transport](#contract---transport)
- [Web content - Destinations](#web-content---destinations)
- [Web content - Transports](#web-content---transports)
- [Agencies](#agencies)
- [Authentication](#authentication)
- [Book - Accommodation](#book---accommodation)
- [Book - Closed Tour](#book---closed-tour)
- [Book - Ticket](#book---ticket)
- [Book - Transfer](#book---transfer)
- [Contract - Closed tour](#contract---closed-tour)
- [Contract - Golf](#contract---golf)
- [Contract - Hotel](#contract---hotel)
- [Contract - Supplier](#contract---supplier)
- [Contract - Ticket](#contract---ticket)
- [Contract - Transfer](#contract---transfer)
- [Dante](#dante)
- [Financial - Expenses Tool](#financial---expenses-tool)
- [Financial - Manual Exchange Rates](#financial---manual-exchange-rates)
- [Financial - Pending Payments](#financial---pending-payments)
- [Ideas](#ideas)
- [Mail](#mail)
- [Packages](#packages)
- [Promotion Codes](#promotion-codes)
- [Purchase Attempts](#purchase-attempts)
- [TripQuote](#tripquote)
- [Users](#users)
- [Web content - Accommodations](#web-content---accommodations)
- [Web content - Blog](#web-content---blog)
- [Web content - Closed tour](#web-content---closed-tour)
- [Web content - Cruises](#web-content---cruises)
- [Web content - Providers](#web-content---providers)
- [Web content - Theme](#web-content---theme)
- [Web content - Tickets](#web-content---tickets)
- [Schemas Appendix](#schemas-appendix)


---

## Booking

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/booking/bookings` | Return bookings basic data by operator or microsite |
| GET | `/booking/getBookings` | Return bookings by operator or microsite |
| GET | `/booking/getBookings/{micrositeId}/{bookingReference}` | Return booking by reference |
| POST | `/booking/refund` | Refund a booking payment |
| PUT | `/booking/{bookingReference}/{serviceType}/{serviceId}/cancel` | Cancel a booking service |
| GET | `/booking/{bookingReference}/{serviceType}/{serviceId}/cancellation-fee` | Cancellation fees for booking service |


#### GET `/booking/bookings`

**Summary:** Return bookings basic data by operator or microsite

**Description:** This method queries for trip bookings. You can filter by operator or by microsite (if you have authorization to do so). You must enter a range of dates to search for bookings

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `operator` | query | string | no | Operator |
| `microsite` | query | string | no | Microsite |
| `from` | query | string(date) | yes | From creation date (yyyyMMdd) |
| `fromtime` | query | string(time-local) | no | From creation time (HH:mm:ss) |
| `to` | query | string(date) | yes | To creation date (yyyyMMdd) |
| `totime` | query | string(time-local) | no | To creation time (HH:mm:ss) |
| `fromDepartureDate` | query | string(date) | no | Departure date from (yyyyMMdd) |
| `toDepartureDate` | query | string(date) | no | Departure date up to (yyyyMMdd) |
| `ufrom` | query | string(date) | no | From last update date (yyyyMMdd) |
| `uto` | query | string(date) | no | To last update date (yyyyMMdd) |
| `cfrom` | query | string(date) | no | From cancel date (yyyyMMdd) |
| `cto` | query | string(date) | no | To cancel date (yyyyMMdd) |
| `ref` | query | string | no | Reference (booking reference, provider booking reference, order number, deferred order number, PNR) |
| `first` | query | integer(int32) | no | First result |
| `limit` | query | integer(int32) | no | Number of results |
| `username` | query | string | no | User's Name that made the booking |

_Pagination: query-param based (`first`/`limit`)._

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`BookingsVO`](#schema-bookingsvo)

#### GET `/booking/getBookings`

**Summary:** Return bookings by operator or microsite

**Description:** This method queries for trip bookings. You can filter by operator or by microsite (if you have authorization to do so). You must enter a range of dates to search for bookings

**⚠️ DEPRECATED**

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `operator` | query | string | no | Operator |
| `microsite` | query | string | no | Microsite |
| `from` | query | string(date) | yes | From creation date (yyyyMMdd) |
| `fromtime` | query | string(time-local) | no | From creation time (HH:mm:ss) |
| `to` | query | string(date) | yes | To creation date (yyyyMMdd) |
| `totime` | query | string(time-local) | no | To creation time (HH:mm:ss) |
| `fromDepartureDate` | query | string(date) | no | Departure date from (yyyyMMdd) |
| `toDepartureDate` | query | string(date) | no | Departure date up to (yyyyMMdd) |
| `ufrom` | query | string(date) | no | From last update date (yyyyMMdd) |
| `uto` | query | string(date) | no | To last update date (yyyyMMdd) |
| `cfrom` | query | string(date) | no | From cancel date (yyyyMMdd) |
| `cto` | query | string(date) | no | To cancel date (yyyyMMdd) |
| `lang` | query | [`Language`](#schema-language) | no | Language |
| `ref` | query | string | no | Reference (booking reference, provider booking reference, order number, deferred order number, PNR) |
| `first` | query | integer(int32) | no | First result |
| `limit` | query | integer(int32) | no | Number of results |
| `username` | query | string | no | User's Name that made the booking |

_Pagination: query-param based (`first`/`limit`)._

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetBookingRS`](#schema-getbookingrs)

#### GET `/booking/getBookings/{micrositeId}/{bookingReference}`

**Summary:** Return booking by reference

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `bookingReference` | path | string | yes | Reference |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | query | [`Language`](#schema-language) | no | Language |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`BookedTripVO`](#schema-bookedtripvo)

#### POST `/booking/refund`

**Summary:** Refund a booking payment

**Description:** This method executes or registers a financial refund for a booking payment. It does not cancel booking services. If amount is omitted or 0, the full remaining refundable amount is refunded. When manualRefund is false or omitted, the refund is executed through the original payment gateway. When manualRefund is true, the refund is only registered as already handled outside TravelCompositor and no gateway transaction is triggered. Partial automatic refunds depend on the original payment gateway support.

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`RefundBookingRequestVO`](#schema-refundbookingrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`RefundBookingResponseVO`](#schema-refundbookingresponsevo)

#### PUT `/booking/{bookingReference}/{serviceType}/{serviceId}/cancel`

**Summary:** Cancel a booking service

**Description:** This method cancels a service from a booking

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `serviceType` | path | [`ServiceType`](#schema-servicetype) | yes |  |
| `serviceId` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`CancelBookingRequestVO`](#schema-cancelbookingrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`CancelBookingResponseVO`](#schema-cancelbookingresponsevo)

#### GET `/booking/{bookingReference}/{serviceType}/{serviceId}/cancellation-fee`

**Summary:** Cancellation fees for booking service

**Description:** This method refund the amount of the penalty in the case of cancellation of the booking on the current day.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `serviceType` | path | [`ServiceType`](#schema-servicetype) | yes |  |
| `serviceId` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`CancellationFeeResponseVO`](#schema-cancellationfeeresponsevo)

---

## Booking - Client Request

### Endpoints

| Method | Path | Summary |
|---|---|---|
| POST | `/booking/{bookingReference}/client-requests` | Creates a new client request |
| PUT | `/booking/{bookingReference}/client-requests/{clientRequestId}/close` | Closes an existing client request |
| PUT | `/booking/{bookingReference}/client-requests/{clientRequestId}/open` | Opens an existing client request |
| POST | `/booking/{bookingReference}/client-requests/{clientRequestId}/reply` | Reply to an existing client request |


#### POST `/booking/{bookingReference}/client-requests`

**Summary:** Creates a new client request

**Description:** This method creates a Client Request and attach to indicated booking.If you need to open, create or reply an existing Client Request you have to invoke the PUT function

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes | Booking Reference |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ClientRequestVO`](#schema-clientrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ClientRequestVO`](#schema-clientrequestvo)

#### PUT `/booking/{bookingReference}/client-requests/{clientRequestId}/close`

**Summary:** Closes an existing client request

**Description:** This method close an existing Client Request.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes | Booking Reference |
| `clientRequestId` | path | string | yes | Client Request Id |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ClientRequestVO`](#schema-clientrequestvo)

#### PUT `/booking/{bookingReference}/client-requests/{clientRequestId}/open`

**Summary:** Opens an existing client request

**Description:** This method opens an existing Client Request.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes | Booking Reference |
| `clientRequestId` | path | string | yes | Client Request Id |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ClientRequestVO`](#schema-clientrequestvo)

#### POST `/booking/{bookingReference}/client-requests/{clientRequestId}/reply`

**Summary:** Reply to an existing client request

**Description:** This method allow to reply to an existing Client Request.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes | Booking Reference |
| `clientRequestId` | path | string | yes | Client Request Id |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ClientRequestAddMessageVO`](#schema-clientrequestaddmessagevo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ClientRequestVO`](#schema-clientrequestvo)

---

## Book - Transports

### Endpoints

| Method | Path | Summary |
|---|---|---|
| POST | `/booking/transports/book` | Book transports |
| POST | `/booking/transports/confirm` | Confirm transports |
| POST | `/booking/transports/prebook` | Prebook transports |
| POST | `/booking/transports/quote` | Quote transports |
| POST | `/booking/transports/quoteFareFamily` | Quote transports Fare Families |
| GET | `/booking/{bookingReference}/transports/{transportBookingReference}` | Transport booking detail |
| PUT | `/booking/{bookingReference}/transports/{transportBookingReference}` | Refresh transport booking |
| DELETE | `/booking/{bookingReference}/transports/{transportBookingReference}` | Cancel transport |
| GET | `/booking/{bookingReference}/transports/{transportBookingReference}/cancellation-fee` | Cancellation fees |


#### POST `/booking/transports/book`

**Summary:** Book transports

**Description:** This method create the book of selected transport/s. </br> </br>- <b>recommendationKey</b> is the key returned in <b>/booking/transports/prebook</b>. </br> </br>- The <b>fakeBooking</b> attribute is optional. If not sent, the booking will be closed as BOOKED in the test environment or real state in real environment. To close the booking correctly the status must be BOOKED. If you want to simulate an error, you can send BOOK_ERROR. With the attribute <b>fakeBooking</b> the booking will not be persisted in the system.

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiTransportBookRequestVO`](#schema-apitransportbookrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTransportBookResponseVO`](#schema-apitransportbookresponsevo)

#### POST `/booking/transports/confirm`

**Summary:** Confirm transports

**Description:** This method returns the confirmation of a transport/s. </br> </br>- It also returns the required passenger fields for that transport/s. The contact person will always be the first passenger of the booking.</br>- It also returns the available extra baggage for each passenger

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiTransportConfirmRequestVO`](#schema-apitransportconfirmrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTransportConfirmResponseVO`](#schema-apitransportconfirmresponsevo)

#### POST `/booking/transports/prebook`

**Summary:** Prebook transports

**Description:** This method create the prebook of a transport/s. </br> </br>- The contact person will always be the first passenger of the booking. </br>- <b>recommendationKey</b> is the key returned in <b>/booking/transports/confirm</b>

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiTransportPrebookRequestVO`](#schema-apitransportprebookrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTransportPrebookResponseVO`](#schema-apitransportprebookresponsevo)

#### POST `/booking/transports/quote`

**Summary:** Quote transports

**Description:** This method returns transport availability for a route or a round trip route

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiTransportQuoteRequestVO`](#schema-apitransportquoterequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTransportQuoteResponseVO`](#schema-apitransportquoteresponsevo)

#### POST `/booking/transports/quoteFareFamily`

**Summary:** Quote transports Fare Families

**Description:** This method returns fare families availability for a route or a round trip route

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiTransportQuoteFareFamilyRequestVO`](#schema-apitransportquotefarefamilyrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTransportQuoteFareFamilyResponseVO`](#schema-apitransportquotefarefamilyresponsevo)

#### GET `/booking/{bookingReference}/transports/{transportBookingReference}`

**Summary:** Transport booking detail

**Description:** This method return the booking of a transport.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `transportBookingReference` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTransportBookResponseVO`](#schema-apitransportbookresponsevo)

#### PUT `/booking/{bookingReference}/transports/{transportBookingReference}`

**Summary:** Refresh transport booking

**Description:** This method refreshes the booking by retrieving the data from the provider.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `transportBookingReference` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTransportBookResponseVO`](#schema-apitransportbookresponsevo)

#### DELETE `/booking/{bookingReference}/transports/{transportBookingReference}`

**Summary:** Cancel transport

**Description:** This method cancel the booking of a transport

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `transportBookingReference` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTransportBookResponseVO`](#schema-apitransportbookresponsevo)

#### GET `/booking/{bookingReference}/transports/{transportBookingReference}/cancellation-fee`

**Summary:** Cancellation fees

**Description:** This method refund the amount of the penalty in the case of cancellation of the booking on the current day.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `transportBookingReference` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiBookCancellationFeeResponseVO`](#schema-apibookcancellationfeeresponsevo)

---

## Book - Multi-engine

### Endpoints

| Method | Path | Summary |
|---|---|---|
| POST | `/booking/book` | Book multi-engine product |
| POST | `/booking/confirm` | Confirm multi-engine product |
| POST | `/booking/prebook` | Prebook multi-engine product |
| GET | `/booking/{bookingReference}` | Booking detail |


#### POST `/booking/book`

**Summary:** Book multi-engine product

**Description:** This method create the book of a multi-engine product. </br> </br>- The <b>fakeBooking</b> attribute is optional. If not sent, the booking will be closed as BOOKED in the test environment or real state in real environment. To close the booking correctly the status must be BOOKED. If you want to simulate an error, you can send BOOK_ERROR. With the attribute <b>fakeBooking</b> the booking will not be persisted in the system.

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiMultiEngineBookRequestVO`](#schema-apimultienginebookrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiMultiEngineBookedResponseVO`](#schema-apimultienginebookedresponsevo)

#### POST `/booking/confirm`

**Summary:** Confirm multi-engine product

**Description:** This method returns the confirmation of a multi engine. </br> </br>- It also returns the required passenger fields for all engine. The contact person will always be the first passenger of the booking. </br>

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiMultiProductConfirmRequestVO`](#schema-apimultiproductconfirmrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiMultiEngineConfirmResponseVO`](#schema-apimultiengineconfirmresponsevo)

#### POST `/booking/prebook`

**Summary:** Prebook multi-engine product

**Description:** This method create the prebook of a multi-engine product. </br> </br>- The contact person will always be the first passenger of the booking. </br>

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiMultiEnginePrebookRequestVO`](#schema-apimultiengineprebookrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiMultiEnginePreBookResponseVO`](#schema-apimultiengineprebookresponsevo)

#### GET `/booking/{bookingReference}`

**Summary:** Booking detail

**Description:** This method return the booking of multi-engine product.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiMultiEngineBookedResponseVO`](#schema-apimultienginebookedresponsevo)

---

## Contract - Transport

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/transport/{supplierId}` | Return transports by supplier |
| PUT | `/transport/{supplierId}` | Updates an existing transport |
| POST | `/transport/{supplierId}` | Creates a new transport |
| GET | `/transport/{supplierId}/{transportId}` | Return transports by its ID |
| PUT | `/transport/{supplierId}/{transportId}` | Updates an existing transport option |
| POST | `/transport/{supplierId}/{transportId}` | Creates a new transport option |
| GET | `/transport/{supplierId}/{transportId}/{optionCode}` | Return transport options by code |


#### GET `/transport/{supplierId}`

**Summary:** Return transports by supplier

**Description:** This method returns a list of transports for a given supplier.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetContractTransportRS`](#schema-getcontracttransportrs)

#### PUT `/transport/{supplierId}`

**Summary:** Updates an existing transport

**Description:** This method updates an existing transport. If you need to create a new ticket, you have to invoke the POST variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractTransportVO`](#schema-contracttransportvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractTransportVO`](#schema-contracttransportvo)

#### POST `/transport/{supplierId}`

**Summary:** Creates a new transport

**Description:** This method creates a new transport for a specific supplier. IF you need to update and existing transport, you have to invoke the PUT variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractTransportVO`](#schema-contracttransportvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractTransportVO`](#schema-contracttransportvo)

#### GET `/transport/{supplierId}/{transportId}`

**Summary:** Return transports by its ID

**Description:** This method returns an specific transport given its identifier and supplier.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |
| `transportId` | path | string | yes | Transport |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractTransportVO`](#schema-contracttransportvo)

#### PUT `/transport/{supplierId}/{transportId}`

**Summary:** Updates an existing transport option

**Description:** This method updates and existing transport option. If you need to create a new transport option, you have to invoke the POST variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |
| `transportId` | path | string | yes | Transport |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractTransportOptionVO`](#schema-contracttransportoptionvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractTransportOptionVO`](#schema-contracttransportoptionvo)

#### POST `/transport/{supplierId}/{transportId}`

**Summary:** Creates a new transport option

**Description:** This method creates a new option for an existing transport. If you need to update an existing transport option, you have to invoke the PUT variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |
| `transportId` | path | string | yes | Transport |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractTransportOptionVO`](#schema-contracttransportoptionvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractTransportOptionVO`](#schema-contracttransportoptionvo)

#### GET `/transport/{supplierId}/{transportId}/{optionCode}`

**Summary:** Return transport options by code

**Description:** This method returns available options for a given transport.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |
| `transportId` | path | string | yes | Transport |
| `optionCode` | path | string | yes | Option |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractTransportOptionVO`](#schema-contracttransportoptionvo)

---

## Web content - Destinations

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/destination/countries/{micrositeId}` | Return countries |
| GET | `/destination/{micrositeId}` | Return destinations |
| GET | `/destination/{micrositeId}/{destinationId}` | Return destination by ID |


#### GET `/destination/countries/{micrositeId}`

**Summary:** Return countries

**Description:** This method returns countries available in a given microsite. You can get the names in several languages.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | query | [`Language`](#schema-language) | no | Language |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetCountryRS`](#schema-getcountryrs)

#### GET `/destination/{micrositeId}`

**Summary:** Return destinations

**Description:** This method returns destinations available in a given microsite. You can filter by country code and get the names in several languages. Available country codes at /resources/destination/countries.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `countryCode` | query | string | no | Country |
| `lang` | query | [`Language`](#schema-language) | no | Language |
| `iata` | query | string | no | IATA |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetDestinationRS`](#schema-getdestinationrs)

#### GET `/destination/{micrositeId}/{destinationId}`

**Summary:** Return destination by ID

**Description:** This method returns destination by ID. You can get the names in several languages. Available country codes at /resources/destination/countries.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `destinationId` | path | string | yes | Destination |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | query | [`Language`](#schema-language) | no | Language |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`DestinationVO`](#schema-destinationvo)

---

## Web content - Transports

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/airline/{micrositeId}` | Return airlines by microsite |
| GET | `/transportbases` | Returns the active transport bases |
| GET | `/transportbases/{code}` | Returns a transport base by its code |


#### GET `/airline/{micrositeId}`

**Summary:** Return airlines by microsite

**Description:** This method return all airlines available for a given microsite

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetAirlineRS`](#schema-getairliners)

#### GET `/transportbases`

**Summary:** Returns the active transport bases

**Description:** This method returns the active transport bases

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `first` | query | integer(int32) | yes | First result |
| `limit` | query | integer(int32) | yes | Number of results |
| `lang` | query | [`Language`](#schema-language) | no | Language code |

_Pagination: query-param based (`first`/`limit`)._

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetTransportBaseRS`](#schema-gettransportbasers)

#### GET `/transportbases/{code}`

**Summary:** Returns a transport base by its code

**Description:** This method returns a transport base by its code

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `code` | path | string | yes | Transport base code |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | query | [`Language`](#schema-language) | no | Language code |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetTransportBaseByCodeRS`](#schema-gettransportbasebycoders)

---

## Agencies

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/agency/agencyCredit/{micrositeId}/{agencyId}` | Return agency credit balance by its ID |
| PUT | `/agency/agencyCredit/{micrositeId}/{agencyId}` | Increase the credit of an existing agency by its ID |
| GET | `/agency/{micrositeId}` | Return agencies by microsite |
| PUT | `/agency/{micrositeId}` | Updates an existing agency |
| POST | `/agency/{micrositeId}` | Creates a new agency |
| GET | `/agency/{micrositeId}/{agencyId}` | Return agency by its ID |
| POST | `/agencymanager/{micrositeId}` | Creates a new agency manager |
| GET | `/agencymanager/{micrositeId}/{agencyManagerId}` | Return agency manager by its ID |
| PUT | `/agencymanager/{micrositeId}/{agencyManagerId}` | Updates an existing agency manager |


#### GET `/agency/agencyCredit/{micrositeId}/{agencyId}`

**Summary:** Return agency credit balance by its ID

**Description:** This method returns information about the credit balance of an agency, given its identifier.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `agencyId` | path | string | yes | Agency |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`AgencyCreditVO`](#schema-agencycreditvo)

#### PUT `/agency/agencyCredit/{micrositeId}/{agencyId}`

**Summary:** Increase the credit of an existing agency by its ID

**Description:** This method increases the credit of an existing agency, given its identifier

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `agencyId` | path | string | yes | Agency |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`AgencyDepositVO`](#schema-agencydepositvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`AgencyCreditVO`](#schema-agencycreditvo)

#### GET `/agency/{micrositeId}`

**Summary:** Return agencies by microsite

**Description:** This method returns the list of agencies of a given microsite.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `first` | query | integer(int32) | yes | First result |
| `limit` | query | integer(int32) | no | Number of results |

_Pagination: query-param based (`first`/`limit`)._

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetAgencyRS`](#schema-getagencyrs)

#### PUT `/agency/{micrositeId}`

**Summary:** Updates an existing agency

**Description:** This method updates an existing agency. If you need to create a new agency you have to invoke the POST variant

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `plainfees` | query | boolean | no | Set fees as provided |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`AgencyVO`](#schema-agencyvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`AgencyVO`](#schema-agencyvo)

#### POST `/agency/{micrositeId}`

**Summary:** Creates a new agency

**Description:** This method creates a new agency. If you need to update an existing agency you have to invoke the PUT variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `plainfees` | query | boolean | no | Set fees as provided |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`AgencyVO`](#schema-agencyvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`AgencyVO`](#schema-agencyvo)

#### GET `/agency/{micrositeId}/{agencyId}`

**Summary:** Return agency by its ID

**Description:** This method returns information about an agency, given its identifier

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `agencyId` | path | string | yes | Agency |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`AgencyVO`](#schema-agencyvo)

#### POST `/agencymanager/{micrositeId}`

**Summary:** Creates a new agency manager

**Description:** This method creates a new agency manager in a given microsite

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`AgencyManagerRequestVO`](#schema-agencymanagerrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`AgencyManagerVO`](#schema-agencymanagervo)

#### GET `/agencymanager/{micrositeId}/{agencyManagerId}`

**Summary:** Return agency manager by its ID

**Description:** This method returns information about an agency manager, given its identifier

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `agencyManagerId` | path | integer(int32) | yes | AgencyManager |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`AgencyManagerVO`](#schema-agencymanagervo)

#### PUT `/agencymanager/{micrositeId}/{agencyManagerId}`

**Summary:** Updates an existing agency manager

**Description:** This method updates an existing agency manager in a given microsite

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `agencyManagerId` | path | integer(int32) | yes | AgencyManager |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`AgencyManagerRequestVO`](#schema-agencymanagerrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`AgencyManagerVO`](#schema-agencymanagervo)

---

## Authentication

### Endpoints

| Method | Path | Summary |
|---|---|---|
| POST | `/authentication/authenticate` | Return auth token |
| POST | `/authentication/otp` | Return a single-use auth token |


#### POST `/authentication/authenticate`

**Summary:** Return auth token

**Description:** This method is the first one you have to invoke, so it will return a valid API token. You need this API token in order to invoke any other method. A valid auth token expires after some time, so you'll need to get another token when the first one expires

**Parameters:** _None_

**Request body:**

Content-Type: `application/json` — Schema: [`Authentication`](#schema-authentication)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetAuthenticationRS`](#schema-getauthenticationrs)

#### POST `/authentication/otp`

**Summary:** Return a single-use auth token

**Description:** This method allows you to perform an SSO for existing users on the platform.
 The call returns a single-use token for the provided username and microsite. To access this call, make sure you have an API user and contact your Account Manager.

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`AuthenticationOTP`](#schema-authenticationotp)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetAuthenticationRS`](#schema-getauthenticationrs)

---

## Book - Accommodation

### Endpoints

| Method | Path | Summary |
|---|---|---|
| POST | `/booking/accommodations/quote` | Quote accommodations |
| POST | `/booking/accommodations/{accommodationId}/book` | Book accommodation |
| POST | `/booking/accommodations/{accommodationId}/confirm` | Confirm accommodation |
| POST | `/booking/accommodations/{accommodationId}/prebook` | Prebook accommodation |
| POST | `/booking/accommodations/{accommodationId}/quote` | Quote accommodation |
| GET | `/booking/{bookingReference}/accommodations/{accommodationBookingReference}` | Accommodation booking detail |
| PUT | `/booking/{bookingReference}/accommodations/{accommodationBookingReference}` | Refresh accommodation booking |
| DELETE | `/booking/{bookingReference}/accommodations/{accommodationBookingReference}` | Cancel accommodation |
| GET | `/booking/{bookingReference}/accommodations/{accommodationBookingReference}/cancellation-fee` | Cancellation fees |


#### POST `/booking/accommodations/quote`

**Summary:** Quote accommodations

**Description:** This method returns the best accommodation combinations by default. To return all available combinations, the <b>bestCombinations</b> parameter must be set to false.. </br> </br>- The <b>combinationKey</b> attribute is all that is needed to book a combination of an accommodation. Always use the <b>combinationKey</b> from the previous step.

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiAccommodationQuoteRequestVO`](#schema-apiaccommodationquoterequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiAccommodationQuoteResponseVO`](#schema-apiaccommodationquoteresponsevo)

#### POST `/booking/accommodations/{accommodationId}/book`

**Summary:** Book accommodation

**Description:** This method create the book of a combination for an accommodation. </br> </br>- <b>combinationKey</b> is the key returned in <b>/booking/accommodations/{accommodationId}/prebook</b>. </br> </br>- The <b>fakeBooking</b> attribute is optional. If not sent, the booking will be closed as BOOKED in the test environment or real state in real environment. To close the booking correctly the status must be BOOKED. If you want to simulate an error, you can send BOOK_ERROR. With the attribute <b>fakeBooking</b> the booking will not be persisted in the system.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `accommodationId` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiAccommodationBookRequestVO`](#schema-apiaccommodationbookrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiAccommodationBookedResponseVO`](#schema-apiaccommodationbookedresponsevo)

#### POST `/booking/accommodations/{accommodationId}/confirm`

**Summary:** Confirm accommodation

**Description:** This method returns the confirmation of a rate for an accommodation. </br> </br>- It also returns the required passenger fields for that accommodation. The contact person will always be the first passenger of the booking. </br>- <b>combinationKey</b> is the combination selected in <b>/booking/accommodations/{accommodationId}/quote</b>

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `accommodationId` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiAccommodationConfirmRequestVO`](#schema-apiaccommodationconfirmrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiAccommodationConfirmResponseVO`](#schema-apiaccommodationconfirmresponsevo)

#### POST `/booking/accommodations/{accommodationId}/prebook`

**Summary:** Prebook accommodation

**Description:** This method create the prebook of a rate for an accommodation. </br> </br>- The contact person will always be the first passenger of the booking. </br>- <b>combinationKey</b> is the key returned in <b>/booking/accommodations/{accommodationId}/confirm</b>

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `accommodationId` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiAccommodationPrebookRequestVO`](#schema-apiaccommodationprebookrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiAccommodationPreBookResponseVO`](#schema-apiaccommodationprebookresponsevo)

#### POST `/booking/accommodations/{accommodationId}/quote`

**Summary:** Quote accommodation

**Description:** This method returns all combinations of an accommodation. </br> </br>- The <b>combinationKey</b> attribute is all that is needed to book a combination of an accommodation. Always use the <b>combinationKey</b> from the previous step.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `accommodationId` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiAccommodationQuoteSingleAccommodationRequestVO`](#schema-apiaccommodationquotesingleaccommodationrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiAccommodationQuoteSingleAccommodationResponseVO`](#schema-apiaccommodationquotesingleaccommodationresponsevo)

#### GET `/booking/{bookingReference}/accommodations/{accommodationBookingReference}`

**Summary:** Accommodation booking detail

**Description:** This method return the booking of accommodation.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `accommodationBookingReference` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiAccommodationBookedResponseVO`](#schema-apiaccommodationbookedresponsevo)

#### PUT `/booking/{bookingReference}/accommodations/{accommodationBookingReference}`

**Summary:** Refresh accommodation booking

**Description:** This method refreshes the booking by retrieving the data from the provider.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `accommodationBookingReference` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiAccommodationBookedResponseVO`](#schema-apiaccommodationbookedresponsevo)

#### DELETE `/booking/{bookingReference}/accommodations/{accommodationBookingReference}`

**Summary:** Cancel accommodation

**Description:** This method cancel the booking of accommodation

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `accommodationBookingReference` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiAccommodationBookedResponseVO`](#schema-apiaccommodationbookedresponsevo)

#### GET `/booking/{bookingReference}/accommodations/{accommodationBookingReference}/cancellation-fee`

**Summary:** Cancellation fees

**Description:** This method refund the amount of the penalty in the case of cancellation of the booking on the current day.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `accommodationBookingReference` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiBookCancellationFeeResponseVO`](#schema-apibookcancellationfeeresponsevo)

---

## Book - Closed Tour

### Endpoints

| Method | Path | Summary |
|---|---|---|
| POST | `/booking/closedtour/{closedTourId}/accommodation` | Get Closed Tour Accommodation Options |
| POST | `/booking/closedtour/{closedTourId}/book` | Book Closed Tour |
| POST | `/booking/closedtour/{closedTourId}/confirm` | Confirm Closed Tour |
| POST | `/booking/closedtour/{closedTourId}/prebook` | Prebook Closed tour |
| POST | `/booking/closedtour/{closedTourId}/prequotedata` | Get possible closed Tour additional data |
| POST | `/booking/closedtour/{closedTourId}/quote` | Quote closed Tour |
| GET | `/booking/{bookingReference}/closedtour/{closedTourBookingReference}` | Closed Tour booking detail |
| PUT | `/booking/{bookingReference}/closedtour/{closedTourBookingReference}` | Refresh closed tour booking |
| DELETE | `/booking/{bookingReference}/closedtour/{closedTourBookingReference}` | Cancel closedtour |
| GET | `/booking/{bookingReference}/closedtour/{closedTourBookingReference}/cancellation-fee` | Cancellation fees |


#### POST `/booking/closedtour/{closedTourId}/accommodation`

**Summary:** Get Closed Tour Accommodation Options

**Description:** This method returns the confirmation of a rate for a Closed Tour. </br>

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `closedTourId` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiClosedTourAccommodationOptionCombinationsRequestVO`](#schema-apiclosedtouraccommodationoptioncombinationsrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiClosedTourAccommodationOptionCombinationsResponseVO`](#schema-apiclosedtouraccommodationoptioncombinationsresponsevo)

#### POST `/booking/closedtour/{closedTourId}/book`

**Summary:** Book Closed Tour

**Description:** This method create the book of a modality for an closed tour. </br> </br>- <b>tourKey</b> is the key returned in <b>/booking/closedtour/{closedTourId}/prebook</b> </br>- The <b>fakeBooking</b> attribute is optional. If not sent, the booking will be closed as BOOKED in the test environment or real state in real environment. To close the booking correctly the status must be BOOKED. If you want to simulate an error, you can send BOOK_ERROR. With the attribute <b>fakeBooking</b> the booking will not be persisted in the system.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `closedTourId` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiClosedTourBookRequestVO`](#schema-apiclosedtourbookrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiClosedTourBookedResponseVO`](#schema-apiclosedtourbookedresponsevo)

#### POST `/booking/closedtour/{closedTourId}/confirm`

**Summary:** Confirm Closed Tour

**Description:** This method returns the confirmation of a rate for a Closed Tour. </br>

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `closedTourId` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiClosedTourConfirmRequestVO`](#schema-apiclosedtourconfirmrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiClosedTourConfirmResponseVO`](#schema-apiclosedtourconfirmresponsevo)

#### POST `/booking/closedtour/{closedTourId}/prebook`

**Summary:** Prebook Closed tour

**Description:** This method create the prebook of a modality for a closed tour. </br> </br>- The contact person will always be the first passenger of the booking. </br>- <b>tourKey</b> is the key returned in <b>/booking/closedtour/{closedTourId}/confirm</b>

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `closedTourId` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiClosedTourPrebookRequestVO`](#schema-apiclosedtourprebookrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiClosedTourPreBookResponseVO`](#schema-apiclosedtourprebookresponsevo)

#### POST `/booking/closedtour/{closedTourId}/prequotedata`

**Summary:** Get possible closed Tour additional data

**Description:** This method validates the requested departure date and returns the possible additional data to perform the quote request.</br> </br>

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `closedTourId` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiClosedTourPrePostNightsClosedTourRequestVO`](#schema-apiclosedtourprepostnightsclosedtourrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiClosedTourPrePostNightsClosedTourResponseVO`](#schema-apiclosedtourprepostnightsclosedtourresponsevo)

#### POST `/booking/closedtour/{closedTourId}/quote`

**Summary:** Quote closed Tour

**Description:** This method returns the chosen closed tour. It will also return the available modalities with their prices.</br> </br>

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `closedTourId` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiClosedTourQuoteRequestVO`](#schema-apiclosedtourquoterequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiClosedTourQuoteResponseVO`](#schema-apiclosedtourquoteresponsevo)

#### GET `/booking/{bookingReference}/closedtour/{closedTourBookingReference}`

**Summary:** Closed Tour booking detail

**Description:** This method return the booking of closed Tour.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `closedTourBookingReference` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiClosedTourBookedResponseVO`](#schema-apiclosedtourbookedresponsevo)

#### PUT `/booking/{bookingReference}/closedtour/{closedTourBookingReference}`

**Summary:** Refresh closed tour booking

**Description:** This method refreshes the booking by retrieving the data from the provider.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `closedTourBookingReference` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiClosedTourBookedResponseVO`](#schema-apiclosedtourbookedresponsevo)

#### DELETE `/booking/{bookingReference}/closedtour/{closedTourBookingReference}`

**Summary:** Cancel closedtour

**Description:** This method cancel the booking of closedtour

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `closedTourBookingReference` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiClosedTourBookedResponseVO`](#schema-apiclosedtourbookedresponsevo)

#### GET `/booking/{bookingReference}/closedtour/{closedTourBookingReference}/cancellation-fee`

**Summary:** Cancellation fees

**Description:** This method refund the amount of the penalty in the case of cancellation of the booking on the current day.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `closedTourBookingReference` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiBookCancellationFeeResponseVO`](#schema-apibookcancellationfeeresponsevo)

---

## Book - Ticket

### Endpoints

| Method | Path | Summary |
|---|---|---|
| POST | `/booking/tickets/quote` | Quote tickets |
| POST | `/booking/tickets/{ticketId}/book` | Book ticket |
| POST | `/booking/tickets/{ticketId}/confirm` | Confirm ticket |
| POST | `/booking/tickets/{ticketId}/prebook` | Prebook ticket |
| POST | `/booking/tickets/{ticketId}/quote` | Quote ticket |
| GET | `/booking/{bookingReference}/tickets/{ticketBookingReference}` | Ticket booking detail |
| PUT | `/booking/{bookingReference}/tickets/{ticketBookingReference}` | Refresh ticket booking |
| DELETE | `/booking/{bookingReference}/tickets/{ticketBookingReference}` | Cancel ticket |
| GET | `/booking/{bookingReference}/tickets/{ticketBookingReference}/cancellation-fee` | Cancellation fees |


#### POST `/booking/tickets/quote`

**Summary:** Quote tickets

**Description:** This method returns the available tickets.

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiTicketQuoteRequestVO`](#schema-apiticketquoterequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTicketQuoteResponseVO`](#schema-apiticketquoteresponsevo)

#### POST `/booking/tickets/{ticketId}/book`

**Summary:** Book ticket

**Description:** This method create the book for the selected modality prices. </br> </br>- The contact person will always be the first passenger of the booking. </br>- <b>ticketKey</b> is the key returned in <b>/booking/tickets/prebook</b>

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `ticketId` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiTicketBookRequestVO`](#schema-apiticketbookrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTicketBookResponseVO`](#schema-apiticketbookresponsevo)

#### POST `/booking/tickets/{ticketId}/confirm`

**Summary:** Confirm ticket

**Description:** This method returns the confirmation for the selected modality prices. </br> </br>- It also returns the required passenger fields for that ticket. The contact person will always be the first passenger of the booking. </br>- <b>modalitykey</b> is for each ticket price selected in <b>/booking/tickets/quoteSingleTicket</b>

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `ticketId` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiTicketConfirmRequestVO`](#schema-apiticketconfirmrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTicketConfirmResponseVO`](#schema-apiticketconfirmresponsevo)

#### POST `/booking/tickets/{ticketId}/prebook`

**Summary:** Prebook ticket

**Description:** This method create the prebook for the selected modality prices. </br> </br>- The contact person will always be the first passenger of the booking. </br>- <b>ticketKey</b> is the key returned in <b>/booking/tickets/confirm</b>

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `ticketId` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiTicketPrebookRequestVO`](#schema-apiticketprebookrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTicketPrebookResponseVO`](#schema-apiticketprebookresponsevo)

#### POST `/booking/tickets/{ticketId}/quote`

**Summary:** Quote ticket

**Description:** This method returns the available modalities of the ticket.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `ticketId` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiTicketQuoteSingleTicketRequestVO`](#schema-apiticketquotesingleticketrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTicketQuoteSingleTicketResponseVO`](#schema-apiticketquotesingleticketresponsevo)

#### GET `/booking/{bookingReference}/tickets/{ticketBookingReference}`

**Summary:** Ticket booking detail

**Description:** This method return the booking of ticket.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `ticketBookingReference` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTicketBookResponseVO`](#schema-apiticketbookresponsevo)

#### PUT `/booking/{bookingReference}/tickets/{ticketBookingReference}`

**Summary:** Refresh ticket booking

**Description:** This method refreshes the booking by retrieving the data from the provider.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `ticketBookingReference` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTicketBookResponseVO`](#schema-apiticketbookresponsevo)

#### DELETE `/booking/{bookingReference}/tickets/{ticketBookingReference}`

**Summary:** Cancel ticket

**Description:** This method cancel the booking of a ticket

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `ticketBookingReference` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTicketCancelResponseVO`](#schema-apiticketcancelresponsevo)

#### GET `/booking/{bookingReference}/tickets/{ticketBookingReference}/cancellation-fee`

**Summary:** Cancellation fees

**Description:** This method returns the amount of the penalty in the case of cancellation of the booking on the current day.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `ticketBookingReference` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiBookCancellationFeeResponseVO`](#schema-apibookcancellationfeeresponsevo)

---

## Book - Transfer

### Endpoints

| Method | Path | Summary |
|---|---|---|
| POST | `/booking/transfer/book` | Transfer book |
| GET | `/booking/transfer/cancellation-fee/{bookingReference}/{transferBookingReference}` | Cancellation fees |
| POST | `/booking/transfer/confirm` | Confirm transfer |
| POST | `/booking/transfer/prebook` | Prebook transfer |
| POST | `/booking/transfer/quote` | Quote transfer |
| GET | `/booking/transfer/{bookingReference}/{transferBookingReference}` | Transfer booking detail |
| PUT | `/booking/transfer/{bookingReference}/{transferBookingReference}` | Refresh transfer booking |
| DELETE | `/booking/transfer/{bookingReference}/{transferBookingReference}` | Cancel transfer |


#### POST `/booking/transfer/book`

**Summary:** Transfer book

**Description:** This method creates the book of a transfer </br> </br>

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiTransferBookRequestVO`](#schema-apitransferbookrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTransferBookResponseVO`](#schema-apitransferbookresponsevo)

#### GET `/booking/transfer/cancellation-fee/{bookingReference}/{transferBookingReference}`

**Summary:** Cancellation fees

**Description:** This method refunds the amount of the penalty in the case of cancellation of the booking on the current day.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `transferBookingReference` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiBookCancellationFeeResponseVO`](#schema-apibookcancellationfeeresponsevo)

#### POST `/booking/transfer/confirm`

**Summary:** Confirm transfer

**Description:** This method returns the confirmation for the selected transfer </br> </br>

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiTransferConfirmRequestVO`](#schema-apitransferconfirmrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTransferConfirmResponseVO`](#schema-apitransferconfirmresponsevo)

#### POST `/booking/transfer/prebook`

**Summary:** Prebook transfer

**Description:** This method creates the prebook of a modality for a transfer. </br> </br>- The contact person will always be the first passenger of the booking. </br>

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiTransferPrebookRequestVO`](#schema-apitransferprebookrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTransferPrebookResponseVO`](#schema-apitransferprebookresponsevo)

#### POST `/booking/transfer/quote`

**Summary:** Quote transfer

**Description:** This method returns the available transfers.

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiTransferQuoteRequestVO`](#schema-apitransferquoterequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTransferQuoteResponseVO`](#schema-apitransferquoteresponsevo)

#### GET `/booking/transfer/{bookingReference}/{transferBookingReference}`

**Summary:** Transfer booking detail

**Description:** This method returns the transfer booking.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `transferBookingReference` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTransferBookResponseVO`](#schema-apitransferbookresponsevo)

#### PUT `/booking/transfer/{bookingReference}/{transferBookingReference}`

**Summary:** Refresh transfer booking

**Description:** This method refreshes the booking by retrieving the data from the provider.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `transferBookingReference` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTransferBookResponseVO`](#schema-apitransferbookresponsevo)

#### DELETE `/booking/transfer/{bookingReference}/{transferBookingReference}`

**Summary:** Cancel transfer

**Description:** This method cancels the transfer booking

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | path | string | yes |  |
| `transferBookingReference` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiTransferBookResponseVO`](#schema-apitransferbookresponsevo)

---

## Contract - Closed tour

### Endpoints

| Method | Path | Summary |
|---|---|---|
| PUT | `/closedtour/{supplierId}` | Updates an existing closed tour |
| POST | `/closedtour/{supplierId}` | Creates a new closed tour |
| GET | `/closedtour/{supplierId}/{closedTourCode}` | Return closed tour by its code |
| PUT | `/closedtour/{supplierId}/{closedTourCode}` | Updates an existing closed tour option |
| POST | `/closedtour/{supplierId}/{closedTourCode}` | Creates a new closed tour option |
| GET | `/closedtour/{supplierId}/{closedTourCode}/{optionCode}` | Return closed tour options by code |


#### PUT `/closedtour/{supplierId}`

**Summary:** Updates an existing closed tour

**Description:** This method updates an existing closed tour. If you need to create a new ticket, you have to invoke the POST variant. WARNING: price field is deprecated and will be ignored. Prices are now loaded directly into each closed tour option.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractClosedTourVO`](#schema-contractclosedtourvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractClosedTourVO`](#schema-contractclosedtourvo)

#### POST `/closedtour/{supplierId}`

**Summary:** Creates a new closed tour

**Description:** This method creates a new closed tour for a specific supplier. If you need to update and existing closed tour, you have to invoke the PUT variant. WARNING: price field is deprecated and will be ignored. Prices are now loaded directly into each closed tour option.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractClosedTourVO`](#schema-contractclosedtourvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractClosedTourVO`](#schema-contractclosedtourvo)

#### GET `/closedtour/{supplierId}/{closedTourCode}`

**Summary:** Return closed tour by its code

**Description:** This method returns an specific closed tour given its code and supplier. WARNING: price field is deprecated and will always be 0. Prices are now loaded directly into each closed tour option.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |
| `closedTourCode` | path | string | yes | Closed tour |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractClosedTourVO`](#schema-contractclosedtourvo)

#### PUT `/closedtour/{supplierId}/{closedTourCode}`

**Summary:** Updates an existing closed tour option

**Description:** This method updates and existing closed tour option. If you need to create a new closed tour option, you have to invoke the POST variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |
| `closedTourCode` | path | string | yes | Closed tour |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractClosedTourOptionVO`](#schema-contractclosedtouroptionvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractClosedTourOptionVO`](#schema-contractclosedtouroptionvo)

#### POST `/closedtour/{supplierId}/{closedTourCode}`

**Summary:** Creates a new closed tour option

**Description:** This method creates a new option for an existing closed tour. If you need to update an existing closed tour option, you have to invoke the PUT variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |
| `closedTourCode` | path | string | yes | Closed tour |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractClosedTourOptionVO`](#schema-contractclosedtouroptionvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractClosedTourOptionVO`](#schema-contractclosedtouroptionvo)

#### GET `/closedtour/{supplierId}/{closedTourCode}/{optionCode}`

**Summary:** Return closed tour options by code

**Description:** This method returns available options for a given closed tour.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |
| `closedTourCode` | path | string | yes | Closed tour |
| `optionCode` | path | string | yes | Option |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractClosedTourOptionVO`](#schema-contractclosedtouroptionvo)

---

## Contract - Golf

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/golf/{supplierId}` | Return golf contracts by supplier |
| PUT | `/golf/{supplierId}` | Updates an existing golf contract |
| POST | `/golf/{supplierId}` | Creates a new golf contract |
| GET | `/golf/{supplierId}/{contractCode}` | Return the details of golf contract by its code |
| PUT | `/golf/{supplierId}/{contractCode}/rate` | Updates an existing rate for an existing golf contract |
| POST | `/golf/{supplierId}/{contractCode}/rate` | Creates a new rate for an existing golf contract |
| PUT | `/golf/{supplierId}/{contractCode}/season` | Updates an existing season for an existing golf contract |
| POST | `/golf/{supplierId}/{contractCode}/season` | Creates a new season for an existing golf contract |
| PUT | `/golf/{supplierId}/{contractCode}/teetime` | Update an existing tee time for an existing golf contract |
| POST | `/golf/{supplierId}/{contractCode}/teetime` | Creates a new tee time for an existing golf contract |


#### GET `/golf/{supplierId}`

**Summary:** Return golf contracts by supplier

**Description:** This method returns a list of golf contracts for a given supplier.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `first` | query | integer(int32) | no | First result |
| `limit` | query | integer(int32) | no | Number of results |

_Pagination: query-param based (`first`/`limit`)._

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetContractGolfRS`](#schema-getcontractgolfrs)

#### PUT `/golf/{supplierId}`

**Summary:** Updates an existing golf contract

**Description:** This method updates an existing contract from a specific supplier. If you need to create a new ticket, you have to invoke the POST variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | supplierId |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractGolfBaseVO`](#schema-contractgolfbasevo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractGolfBaseVO`](#schema-contractgolfbasevo)

#### POST `/golf/{supplierId}`

**Summary:** Creates a new golf contract

**Description:** This method creates a new golf contract for a specific supplier. If you need to update an existing ticket, you have to invoke the PUT variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | supplierId |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractGolfBaseVO`](#schema-contractgolfbasevo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractGolfBaseVO`](#schema-contractgolfbasevo)

#### GET `/golf/{supplierId}/{contractCode}`

**Summary:** Return the details of golf contract by its code

**Description:** This method returns the details of an specific golf contract given its code.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |
| `contractCode` | path | string | yes | Contract |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractGolfDetailedVO`](#schema-contractgolfdetailedvo)

#### PUT `/golf/{supplierId}/{contractCode}/rate`

**Summary:** Updates an existing rate for an existing golf contract

**Description:** This method updates an existing rate for an existing contract from a specific supplier. If you need to create a rate, you have to invoke the POST variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | supplierId |
| `contractCode` | path | string | yes | Contract |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractGolfRateVO`](#schema-contractgolfratevo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractGolfRateVO`](#schema-contractgolfratevo)

#### POST `/golf/{supplierId}/{contractCode}/rate`

**Summary:** Creates a new rate for an existing golf contract

**Description:** This method creates a new rate for an existing contract from a specific supplier. If you need to update a rate, you have to invoke the PUT variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | supplierId |
| `contractCode` | path | string | yes | Contract |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractGolfRateVO`](#schema-contractgolfratevo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractGolfRateVO`](#schema-contractgolfratevo)

#### PUT `/golf/{supplierId}/{contractCode}/season`

**Summary:** Updates an existing season for an existing golf contract

**Description:** This method updates an existing season for an existing contract from a specific supplier. If you need to create a new season, you have to invoke the POST variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | supplierId |
| `contractCode` | path | string | yes | Contract |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractGolfSeasonVO`](#schema-contractgolfseasonvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractGolfSeasonVO`](#schema-contractgolfseasonvo)

#### POST `/golf/{supplierId}/{contractCode}/season`

**Summary:** Creates a new season for an existing golf contract

**Description:** This method creates a new season for an existing contract from a specific supplier. If you need to update a season, you have to invoke the PUT variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | supplierId |
| `contractCode` | path | string | yes | Contract |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractGolfSeasonVO`](#schema-contractgolfseasonvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractGolfSeasonVO`](#schema-contractgolfseasonvo)

#### PUT `/golf/{supplierId}/{contractCode}/teetime`

**Summary:** Update an existing tee time for an existing golf contract

**Description:** This method updates an existing tee time for an existing contract from a specific supplier. If you need create a new tee time, you have to invoke the POST variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | supplierId |
| `contractCode` | path | string | yes | Contract |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractGolfTeeTimeVO`](#schema-contractgolfteetimevo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractGolfTeeTimeVO`](#schema-contractgolfteetimevo)

#### POST `/golf/{supplierId}/{contractCode}/teetime`

**Summary:** Creates a new tee time for an existing golf contract

**Description:** This method creates a new tee time for an existing contract from a specific supplier. If you need to update an existing tee time, you have to invoke the PUT variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | supplierId |
| `contractCode` | path | string | yes | Contract |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractGolfTeeTimeVO`](#schema-contractgolfteetimevo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractGolfTeeTimeVO`](#schema-contractgolfteetimevo)

---

## Contract - Hotel

### Endpoints

| Method | Path | Summary |
|---|---|---|
| POST | `/hotel/mealplan/{supplierId}/{providerCode}` | Creates a new hotel mealplan |
| POST | `/hotel/offer/{supplierId}/{providerCode}` | Add offer to hotel contract |
| PUT | `/hotel/rates/{supplierId}/{providerCode}` | Modifies hotel contract rates |
| POST | `/hotel/rates/{supplierId}/{providerCode}` | Add rates to hotel contract |
| POST | `/hotel/room/{supplierId}/{providerCode}` | Creates a new hotel room |
| POST | `/hotel/supplement/{supplierId}/{providerCode}` | Add supplement to hotel contract |
| GET | `/hotel/{supplierId}` | Return hotels by supplier |
| PUT | `/hotel/{supplierId}` | Updates an existing hotel contract |
| POST | `/hotel/{supplierId}` | Creates a new hotel contract |
| GET | `/hotel/{supplierId}/{providerCode}` | Return hotel by its provider code |


#### POST `/hotel/mealplan/{supplierId}/{providerCode}`

**Summary:** Creates a new hotel mealplan

**Description:** This method add mealplan to a hotel contract for a specific supplier. Before using mealPlanId, call GET /mealplan/{micrositeId}, at Web content - Accommodations, to retrieve the available meal plans and use an ID returned by that endpoint.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |
| `providerCode` | path | string | yes | Hotel |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractMealPlanVO`](#schema-contractmealplanvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractMealPlanVO`](#schema-contractmealplanvo)

#### POST `/hotel/offer/{supplierId}/{providerCode}`

**Summary:** Add offer to hotel contract

**Description:** This method add offer to contract for a specific supplier

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |
| `providerCode` | path | string | yes | Hotel |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractHotelOffersVO`](#schema-contracthoteloffersvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractHotelOffersVO`](#schema-contracthoteloffersvo)

#### PUT `/hotel/rates/{supplierId}/{providerCode}`

**Summary:** Modifies hotel contract rates

**Description:** This method modifies contract rates for a specific supplier

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |
| `providerCode` | path | string | yes | Hotel |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractHotelRateVO`](#schema-contracthotelratevo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractHotelRateVO`](#schema-contracthotelratevo)

#### POST `/hotel/rates/{supplierId}/{providerCode}`

**Summary:** Add rates to hotel contract

**Description:** This method add rates to contract for a specific supplier

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |
| `providerCode` | path | string | yes | Hotel |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractHotelRateVO`](#schema-contracthotelratevo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractHotelRateVO`](#schema-contracthotelratevo)

#### POST `/hotel/room/{supplierId}/{providerCode}`

**Summary:** Creates a new hotel room

**Description:** This method add room to contract for a specific supplier

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |
| `providerCode` | path | string | yes | Hotel |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractRoomVO`](#schema-contractroomvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractRoomVO`](#schema-contractroomvo)

#### POST `/hotel/supplement/{supplierId}/{providerCode}`

**Summary:** Add supplement to hotel contract

**Description:** This method add supplement to contract for a specific supplier

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |
| `providerCode` | path | string | yes | Hotel |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractHotelSupplementVO`](#schema-contracthotelsupplementvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractHotelSupplementVO`](#schema-contracthotelsupplementvo)

#### GET `/hotel/{supplierId}`

**Summary:** Return hotels by supplier

**Description:** This method returns a list of hotels for a given supplier.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetContractHotelRS`](#schema-getcontracthotelrs)

#### PUT `/hotel/{supplierId}`

**Summary:** Updates an existing hotel contract

**Description:** This method updates an existing hotel contract. If you need to create a new contract, you have to invoke the POST variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractHotelDetailedVO`](#schema-contracthoteldetailedvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractHotelCompletedVO`](#schema-contracthotelcompletedvo)

#### POST `/hotel/{supplierId}`

**Summary:** Creates a new hotel contract

**Description:** This method creates a new contract for a specific supplier. If you need to update an existing contract, you have to invoke the PUT variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractHotelDetailedVO`](#schema-contracthoteldetailedvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractHotelCompletedVO`](#schema-contracthotelcompletedvo)

#### GET `/hotel/{supplierId}/{providerCode}`

**Summary:** Return hotel by its provider code

**Description:** This method returns an specific hotel given its provider code and supplier.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |
| `providerCode` | path | string | yes | Hotel |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractHotelCompletedVO`](#schema-contracthotelcompletedvo)

---

## Contract - Supplier

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/suppliers` | Return suppliers of operator |
| POST | `/suppliers` | Creates a new supplier |
| GET | `/suppliers/{supplierId}` | Return supplier by its code |
| PUT | `/suppliers/{supplierId}` | Modify an existing contract supplier |


#### GET `/suppliers`

**Summary:** Return suppliers of operator

**Description:** This method returns a contract supplier list by user's operator

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: array of [`ContractSupplierVO`](#schema-contractsuppliervo)

#### POST `/suppliers`

**Summary:** Creates a new supplier

**Description:** This method creates a new supplier for the user's oeprator. If you need to update an existing supplier,you have to invoke the PUT variant.

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractSupplierVO`](#schema-contractsuppliervo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractSupplierVO`](#schema-contractsuppliervo)

#### GET `/suppliers/{supplierId}`

**Summary:** Return supplier by its code

**Description:** This method returns a contract supplier of the user's operator given its ID.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractSupplierVO`](#schema-contractsuppliervo)

#### PUT `/suppliers/{supplierId}`

**Summary:** Modify an existing contract supplier

**Description:** This method updates an existing supplier for the user's operator.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractSupplierVO`](#schema-contractsuppliervo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractSupplierVO`](#schema-contractsuppliervo)

---

## Contract - Ticket

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/tickets/{supplierId}` | Return tickets by supplier |
| PUT | `/tickets/{supplierId}` | Updates an existing ticket |
| POST | `/tickets/{supplierId}` | Creates a new ticket |
| GET | `/tickets/{supplierId}/{ticketCode}` | Return ticket by its code |
| PUT | `/tickets/{supplierId}/{ticketCode}` | Updates an existing ticket option |
| POST | `/tickets/{supplierId}/{ticketCode}` | Creates a new ticket option |
| GET | `/tickets/{supplierId}/{ticketCode}/{optionCode}` | Return ticket options by code |


#### GET `/tickets/{supplierId}`

**Summary:** Return tickets by supplier

**Description:** This method returns a list of tickets for a given supplier.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |
| `first` | header | integer(int32) | yes | First result |
| `limit` | header | integer(int32) | yes | Number of results |

_Pagination: header based (`first`/`limit` sent as HTTP headers)._

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractTicketRS`](#schema-contractticketrs)

#### PUT `/tickets/{supplierId}`

**Summary:** Updates an existing ticket

**Description:** This method updates an existing ticket. If you need to create a new ticket, you have to invoke the POST variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractTicketVO`](#schema-contractticketvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiStaticContentTicketVO`](#schema-apistaticcontentticketvo)

#### POST `/tickets/{supplierId}`

**Summary:** Creates a new ticket

**Description:** This method creates a new ticket for a specific supplier. IF you need to update an existing ticket, you have to invoke the PUT variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractTicketVO`](#schema-contractticketvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiStaticContentTicketVO`](#schema-apistaticcontentticketvo)

#### GET `/tickets/{supplierId}/{ticketCode}`

**Summary:** Return ticket by its code

**Description:** This method returns an specific ticket given its code and supplier.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |
| `ticketCode` | path | string | yes | Ticket |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiStaticContentTicketVO`](#schema-apistaticcontentticketvo)

#### PUT `/tickets/{supplierId}/{ticketCode}`

**Summary:** Updates an existing ticket option

**Description:** This method updates and existing ticket option. If you need to create a new ticket option, you have to invoke the POST variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |
| `ticketCode` | path | string | yes | Ticket |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractTicketModalityVO`](#schema-contractticketmodalityvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractTicketModalityVO`](#schema-contractticketmodalityvo)

#### POST `/tickets/{supplierId}/{ticketCode}`

**Summary:** Creates a new ticket option

**Description:** This method creates a new option for an existing ticket. If you need to update an existing ticket option, you have to invoke the PUT variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |
| `ticketCode` | path | string | yes | Ticket |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractTicketModalityVO`](#schema-contractticketmodalityvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractTicketModalityVO`](#schema-contractticketmodalityvo)

#### GET `/tickets/{supplierId}/{ticketCode}/{optionCode}`

**Summary:** Return ticket options by code

**Description:** This method returns available options for a given ticket.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |
| `ticketCode` | path | string | yes | Ticket |
| `optionCode` | path | string | yes | Option |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractTicketModalityVO`](#schema-contractticketmodalityvo)

---

## Contract - Transfer

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/transfer/zones/{supplierId}` | Return transfer zones |
| GET | `/transfer/{supplierId}` | Return transfers by supplier |
| PUT | `/transfer/{supplierId}` | Updates an existing transfer |
| POST | `/transfer/{supplierId}` | Creates a new transfer |
| GET | `/transfer/{supplierId}/{transferId}` | Return transfer by its id |


#### GET `/transfer/zones/{supplierId}`

**Summary:** Return transfer zones

**Description:** This method returns a list of the available transfer zones.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `zoneType` | query | [`TransferZoneType`](#schema-transferzonetype) | no | Zone Type |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetTransferZoneRS`](#schema-gettransferzoners)

#### GET `/transfer/{supplierId}`

**Summary:** Return transfers by supplier

**Description:** This method returns the list of transfers of a given supplier.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetContractTransferRS`](#schema-getcontracttransferrs)

#### PUT `/transfer/{supplierId}`

**Summary:** Updates an existing transfer

**Description:** This method updates an existing transfer service. If you need to create a new transfer, you have to invoke the POST variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractTransferVO`](#schema-contracttransfervo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractTransferVO`](#schema-contracttransfervo)

#### POST `/transfer/{supplierId}`

**Summary:** Creates a new transfer

**Description:** This method creates a new transfer service. If you need to update an existing transfer, you have to invoke the PUT variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ContractTransferVO`](#schema-contracttransfervo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractTransferVO`](#schema-contracttransfervo)

#### GET `/transfer/{supplierId}/{transferId}`

**Summary:** Return transfer by its id

**Description:** This method returns information about an specific transfer given its identifier.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `supplierId` | path | integer(int32) | yes | Supplier |
| `transferId` | path | string | yes | Transfer |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ContractTransferVO`](#schema-contracttransfervo)

---

## Dante

### Endpoints

| Method | Path | Summary |
|---|---|---|
| POST | `/dante/documents` | Submit a document |
| GET | `/dante/documents/{externalId}` | Get document |
| POST | `/dante/documents/{externalId}/reprocess` | Reprocess a document |


#### POST `/dante/documents`

**Summary:** Submit a document

**Description:** Submits a PDF document for analysis and information extraction. Processing is asynchronous: the endpoint returns 202 Accepted immediately and notifies the client via webhook once processing is complete.

The `serviceType` field controls which extraction pipeline is applied. `HOTEL` and `CLOSEDTOUR` are supported; other valid types (`TRANSFER`, `TICKET`) will return 400 until enabled.

Non-exempt operators must have Dante credits available to submit a document. If no credits remain, the request is rejected with 429 Too Many Requests. Credits are deducted asynchronously during processing, not at submission time. In the rare case that credits are exhausted during asynchronous processing (e.g. two concurrent submissions racing on the last credit), the asset is set to `ERROR` and the webhook notification will include an `error` object with `code: INSUFFICIENT_CREDITS`. The error is not persisted — it is only present in the webhook payload.

The maximum accepted request size is 5 MB. Submissions exceeding this limit are rejected with 413 Request Entity Too Large.


**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `multipart/form-data` — Schema: [`SubmitDocumentForm`](#schema-submitdocumentform)

**Responses:**

**202** — Document accepted for processing  
Content-Type: `application/json` — Schema: [`ApiDanteDocumentAcceptedResponseVO`](#schema-apidantedocumentacceptedresponsevo)

**400** — Invalid or missing field in the request

**401** — Invalid or missing auth-token

**413** — Request body exceeds the maximum allowed size of 5 MB

**429** — Operator has no Dante credits remaining

**409** — A document with the same externalId already exists for this user

#### GET `/dante/documents/{externalId}`

**Summary:** Get document

**Description:** Returns the current status and extracted data for a previously submitted document. The response is structurally identical to the webhook notification sent upon completion, so it can be used to poll for status or to recover a missed notification.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `externalId` | path | string | yes | Client-assigned identifier used when submitting the document |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**200** — Document found  
Content-Type: `application/json` — Schema: [`ApiDanteAssetVO`](#schema-apidanteassetvo)

**401** — Invalid or missing auth-token

**404** — No document found with the given externalId for this user

#### POST `/dante/documents/{externalId}/reprocess`

**Summary:** Reprocess a document

**Description:** Triggers reprocessing of a previously submitted document. No need to re-upload the PDF — the file is already stored from the initial submission.

If the asset has no document yet (failed before document creation), the full ingestion pipeline is re-run. `language` and `currency` may be updated to correct a reconciliation error. Non-exempt operators must have Dante credits available in this case; if no credits remain, the request is rejected with 429 Too Many Requests.

If the asset already has a document, only the document processing step is re-run. Sending `language` or `currency` in this case returns 400.


**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `externalId` | path | string | yes | Client-assigned identifier used when submitting the document |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiDanteReprocessRequestVO`](#schema-apidantereprocessrequestvo)

**Responses:**

**202** — Reprocessing accepted  
Content-Type: `application/json` — Schema: [`ApiDanteDocumentAcceptedResponseVO`](#schema-apidantedocumentacceptedresponsevo)

**400** — language or currency provided but document is already past analysis

**401** — Invalid or missing auth-token

**404** — No document found with the given externalId for this user

**409** — Document is not in a reprocessable state

**429** — Operator has no Dante credits remaining (only when the full ingestion pipeline is re-run)

---

## Financial - Expenses Tool

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/expensestool/{micrositeId}/monthly_report` | Return monthly expense reports |
| GET | `/expensestool/{micrositeId}/report/{reference}` | Return reports contained in a monthly expense report |
| GET | `/expensestool/{micrositeId}/report/{reference}/expenses` | Return all financial entries associated with a report |


#### GET `/expensestool/{micrositeId}/monthly_report`

**Summary:** Return monthly expense reports

**Description:** Requires API Data read access and the Expenses Tool module. Filters the report month by an inclusive date range and monthly reports containing a report with the selected status.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `first` | query | integer(int32) | no | First result (zero based) |
| `limit` | query | integer(int32) | no | Number of results |
| `from` | query | string(date) | no | First report month, using a date (yyyyMMdd) |
| `to` | query | string(date) | no | Last report month, using a date (yyyyMMdd) |
| `status` | query | [`Status`](#schema-status) | no | Status of the reports contained in the monthly report |
| `agencyId` | query | integer(int32) | no | Agency ID |

_Pagination: query-param based (`first`/`limit`)._

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`MonthlyReportsResponseVO`](#schema-monthlyreportsresponsevo)

#### GET `/expensestool/{micrositeId}/report/{reference}`

**Summary:** Return reports contained in a monthly expense report

**Description:** Requires API Data read access and the Expenses Tool module.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `reference` | path | string | yes | Monthly report reference (MR- followed by its numeric ID) |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ReportsResponseVO`](#schema-reportsresponsevo)

#### GET `/expensestool/{micrositeId}/report/{reference}/expenses`

**Summary:** Return all financial entries associated with a report

**Description:** Requires API Data read access and the Expenses Tool module. Returns expenses, booked services, accepted advances, and settlements in separate arrays. Booked service amounts are formatted strings with a currency symbol; advance and settlement amounts use numeric money objects.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `reference` | path | string | yes | Report reference (RP- followed by its numeric ID) |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ExpensesResponseVO`](#schema-expensesresponsevo)

---

## Financial - Manual Exchange Rates

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/microsites/{micrositeId}/manual-exchange-rates` | Return manual exchange rates by microsite |
| POST | `/microsites/{micrositeId}/manual-exchange-rates` | Creates a manual exchange rate |
| PUT | `/microsites/{micrositeId}/manual-exchange-rates/{manualExchangeRateId}` | Updates a manual exchange rate |
| DELETE | `/microsites/{micrositeId}/manual-exchange-rates/{manualExchangeRateId}` | Deletes a manual exchange rate |


#### GET `/microsites/{micrositeId}/manual-exchange-rates`

**Summary:** Return manual exchange rates by microsite

**Description:** This method returns the manual exchange rates configured for a microsite.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ManualExchangeRatesResponseVO`](#schema-manualexchangeratesresponsevo)

#### POST `/microsites/{micrositeId}/manual-exchange-rates`

**Summary:** Creates a manual exchange rate

**Description:** This method creates a new manual exchange rate for a microsite.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ManualExchangeRateVO`](#schema-manualexchangeratevo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ManualExchangeRateResponseVO`](#schema-manualexchangerateresponsevo)

#### PUT `/microsites/{micrositeId}/manual-exchange-rates/{manualExchangeRateId}`

**Summary:** Updates a manual exchange rate

**Description:** This method updates an existing manual exchange rate for a microsite.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `manualExchangeRateId` | path | integer(int32) | yes | Manual exchange rate |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ManualExchangeRateVO`](#schema-manualexchangeratevo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ManualExchangeRateResponseVO`](#schema-manualexchangerateresponsevo)

#### DELETE `/microsites/{micrositeId}/manual-exchange-rates/{manualExchangeRateId}`

**Summary:** Deletes a manual exchange rate

**Description:** This method deletes an existing manual exchange rate for a microsite.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `manualExchangeRateId` | path | integer(int32) | yes | Manual exchange rate |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`DeleteResponse`](#schema-deleteresponse)

---

## Financial - Pending Payments

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/pending-payments/{micrositeId}` | Return current pending payments by microsite |
| POST | `/pending-payments/{micrositeId}/mark-as-paid` | Mark a current pending payment as paid |


#### GET `/pending-payments/{micrositeId}`

**Summary:** Return current pending payments by microsite

**Description:** This method returns the current pending payment for each non-group booking in the microsite. If fromDeferredLimitDate is omitted, the search starts 10 days before today. If toDeferredLimitDate is omitted, the search ends 45 days after today. The response can be used to identify the payment for the mark-as-paid POST.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `bookingReference` | query | string | no | Booking reference filter |
| `first` | query | integer(int32) | no | First result |
| `limit` | query | integer(int32) | no | Number of results |
| `fromDeferredLimitDate` | query | string(date-time) | no | Deferred limit date from |
| `toDeferredLimitDate` | query | string(date-time) | no | Deferred limit date to |

_Pagination: query-param based (`first`/`limit`)._

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`PendingPaymentResponseVO`](#schema-pendingpaymentresponsevo)

#### POST `/pending-payments/{micrositeId}/mark-as-paid`

**Summary:** Mark a current pending payment as paid

**Description:** This method performs the same operation as the backoffice Set as Paid action for the current pending payment of a booking. Use the paymentId and paymentNumber recovered from the GET pending-payments Only the current pending payment can be marked as paid through this API, and group pending payments are not supported.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`MarkPendingPaymentRequestVO`](#schema-markpendingpaymentrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`MarkPendingPaymentResponseVO`](#schema-markpendingpaymentresponsevo)

---

## Ideas

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/travelidea/{micrositeId}` | Return ideas by microsite |
| GET | `/travelidea/{micrositeId}/info/{ideaId}` | Return idea by its ID |
| GET | `/travelidea/{micrositeId}/{ideaId}` | Return idea details by its ID |
| PUT | `/travelidea/{micrositeId}/{ideaId}` | Updates an existing idea |


#### GET `/travelidea/{micrositeId}`

**Summary:** Return ideas by microsite

**Description:** This method returns the list of travel ideas for a given microsite. You can filter by theme, which can be obtained at /resources/theme. You can obtain the ideas in several languages and currencies.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `themes` | query | array[integer(int32)] | no | Themes |
| `lang` | query | [`Language`](#schema-language) | no | Language |
| `currency` | query | [`Currency`](#schema-currency) | no | Currency |
| `username` | query | string | no | Username |
| `countryCode` | query | string | no | Country |
| `onlyVisible` | query | boolean | no | Only visible |
| `first` | query | integer(int32) | no | First result |
| `limit` | query | integer(int32) | no | Number of results |
| `fromCreationDate` | query | string(date) | no | From creation date (yyyyMMdd) |
| `toCreationDate` | query | string(date) | no | To creation date (yyyyMMdd) |

_Pagination: query-param based (`first`/`limit`)._

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetIdeaRS`](#schema-getidears)

#### GET `/travelidea/{micrositeId}/info/{ideaId}`

**Summary:** Return idea by its ID

**Description:** This method returns the basic info of an idea by its ID. You can obtain the ideas in several languages and currencies.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `ideaId` | path | integer(int32) | yes | Idea |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | query | [`Language`](#schema-language) | no | Language |
| `currency` | query | [`Currency`](#schema-currency) | no | Currency |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`IdeaVO`](#schema-ideavo)

#### GET `/travelidea/{micrositeId}/{ideaId}`

**Summary:** Return idea details by its ID

**Description:** This method return the day to day details of a given idea. You can obtain the idea in several languages.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `ideaId` | path | integer(int32) | yes | Idea |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | query | [`Language`](#schema-language) | no | Language |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`IdeaDayToDayVO`](#schema-ideadaytodayvo)

#### PUT `/travelidea/{micrositeId}/{ideaId}`

**Summary:** Updates an existing idea

**Description:** This method updates the basic info of an existing idea. You can obtain the ideas in several languages and currencies.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `ideaId` | path | integer(int32) | yes | Idea |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | query | [`Language`](#schema-language) | no | Language |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`IdeaUpdateRequestVO`](#schema-ideaupdaterequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`IdeaVO`](#schema-ideavo)

---

## Mail

### Endpoints

| Method | Path | Summary |
|---|---|---|
| POST | `/mail/ban` | banMails |


#### POST `/mail/ban`

**Summary:** banMails

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes |  |

**Request body:**

Content-Type: `application/json` — Schema: [`MailVO`](#schema-mailvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiAuditDataResponseVO`](#schema-apiauditdataresponsevo)

---

## Packages

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/package/calendar/{micrositeId}/{holidayPackageId}` | Return holiday package calendar by its ID |
| GET | `/package/{micrositeId}` | Return holiday packages by microsite |
| GET | `/package/{micrositeId}/info/{holidayPackageId}` | Return holiday package by its ID |
| GET | `/package/{micrositeId}/{holidayPackageId}` | Return holiday package details by its ID |
| PUT | `/package/{micrositeId}/{holidayPackageId}` | Updates an existing holiday package |


#### GET `/package/calendar/{micrositeId}/{holidayPackageId}`

**Summary:** Return holiday package calendar by its ID

**Description:** This method returns the days in which the holiday package is available and the price associated to that day You can obtain the prices in several currencies.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `holidayPackageId` | path | integer(int32) | yes | Holiday package |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `currency` | query | [`Currency`](#schema-currency) | no | Currency |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`DeparturePricesVO`](#schema-departurepricesvo)

#### GET `/package/{micrositeId}`

**Summary:** Return holiday packages by microsite

**Description:** This method returns the list of holiday packages for a given microsite. You can filter by origin, month and destination (origin and destination codes can be obtained at /resources/destination). You can obtain the packages in several languages and currencies.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `origin` | query | string | no | Origin code |
| `month` | query | [`Month`](#schema-month) | no | Month |
| `destinations` | query | string | no | Destination code |
| `lang` | query | [`Language`](#schema-language) | no | Language |
| `currency` | query | [`Currency`](#schema-currency) | no | Currency |
| `countryCode` | query | string | no | Country |
| `onlyVisible` | query | boolean | no | Only visible |
| `fromCreationDate` | query | string(date) | no | From creation date (yyyyMMdd) |
| `toCreationDate` | query | string(date) | no | To creation date (yyyyMMdd) |
| `first` | query | integer(int32) | no | First result |
| `limit` | query | integer(int32) | no | Number of results |
| `username` | query | string | no | Username |
| `provider` | query | array[string] | no | Provider |
| `externalReference` | query | string | no | External Reference |

_Pagination: query-param based (`first`/`limit`)._

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetHolidayPackageRS`](#schema-getholidaypackagers)

#### GET `/package/{micrositeId}/info/{holidayPackageId}`

**Summary:** Return holiday package by its ID

**Description:** This method returns the basic info of a holiday package by its ID. You can obtain the ideas in several languages and currencies.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `holidayPackageId` | path | integer(int32) | yes | Holiday package |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | query | [`Language`](#schema-language) | no | Language |
| `currency` | query | [`Currency`](#schema-currency) | no | Currency |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`HolidayPackageVO`](#schema-holidaypackagevo)

#### GET `/package/{micrositeId}/{holidayPackageId}`

**Summary:** Return holiday package details by its ID

**Description:** This method return the day to day details of a given holiday package. You can obtain the holiday package in several languages.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `holidayPackageId` | path | integer(int32) | yes | Holiday package |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | query | [`Language`](#schema-language) | no | Language |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`IdeaDayToDayVO`](#schema-ideadaytodayvo)

#### PUT `/package/{micrositeId}/{holidayPackageId}`

**Summary:** Updates an existing holiday package

**Description:** This method updates the basic info of an existing holiday package. You can obtain the holiday package in several languages and currencies.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `holidayPackageId` | path | integer(int32) | yes | Idea |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | query | [`Language`](#schema-language) | no | Language |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`IdeaUpdateRequestVO`](#schema-ideaupdaterequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`IdeaVO`](#schema-ideavo)

---

## Promotion Codes

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/promotioncode/{micrositeId}` | Return promotional codes by microsite |
| PUT | `/promotioncode/{micrositeId}` | Modify an existing promotional code |
| POST | `/promotioncode/{micrositeId}` | Creates a new promotional code |


#### GET `/promotioncode/{micrositeId}`

**Summary:** Return promotional codes by microsite

**Description:** This method returns the list of travel promotional codes for a given microsite. You can filter by active or type.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `active` | query | boolean | no | Active |
| `type` | query | [`PromotionCodeType`](#schema-promotioncodetype) | no | Type |
| `first` | query | integer(int32) | no | First result |
| `limit` | query | integer(int32) | no | Number of results |
| `userName` | query | string | no | Username from Applicable users |
| `agencyName` | query | string | no | Agency Name from Applicable Agencies |
| `promoCodeName` | query | string | no | Promotion Code Name |
| `filterByCreationDate` | query | boolean | no | Filter by Creation Date |

_Pagination: query-param based (`first`/`limit`)._

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: array of [`PromotionCodeVO`](#schema-promotioncodevo)

#### PUT `/promotioncode/{micrositeId}`

**Summary:** Modify an existing promotional code

**Description:** This method updates an existing promotional code for the user's operator with specific microsite.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`PromotionCodeVO`](#schema-promotioncodevo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`PromotionCodeVO`](#schema-promotioncodevo)

#### POST `/promotioncode/{micrositeId}`

**Summary:** Creates a new promotional code

**Description:** This method creates a new promotional code a specific microsite.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`PromotionCodeVO`](#schema-promotioncodevo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`PromotionCodeVO`](#schema-promotioncodevo)

---

## Purchase Attempts

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/purchase-attempts/{micrositeId}` | Return purchase attempt |
| GET | `/purchase-attempts/{micrositeId}/{purchaseAttemptId}` | Return purchase attempt by Id |


#### GET `/purchase-attempts/{micrositeId}`

**Summary:** Return purchase attempt

**Description:** This method returns the basic info of purchase attempt of idea by microsite. You can obtain the ideas in several languages.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | query | [`Language`](#schema-language) | no | Language |
| `username` | query | string | no | Username |
| `first` | query | integer(int32) | no | First result |
| `limit` | query | integer(int32) | no | Number of results |
| `fromCreationDate` | query | string(date) | no | From creation date (yyyyMMdd) |
| `toCreationDate` | query | string(date) | no | To creation date (yyyyMMdd) |

_Pagination: query-param based (`first`/`limit`)._

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`PurchaseAttemptsResponse`](#schema-purchaseattemptsresponse)

#### GET `/purchase-attempts/{micrositeId}/{purchaseAttemptId}`

**Summary:** Return purchase attempt by Id

**Description:** This method returns the basic info of purchase attempt by id and microsite. You can obtain the ideas in several languages.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `purchaseAttemptId` | path | integer(int32) | yes | Purchase Attempt |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | query | [`Language`](#schema-language) | no | Language |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`PurchaseAttemptResponse`](#schema-purchaseattemptresponse)

---

## TripQuote

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/tripquote/count` | countTripQuotes |
| GET | `/tripquote/report` | reportTripQuotes |


#### GET `/tripquote/count`

**Summary:** countTripQuotes

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | query | string | no |  |
| `operatorId` | query | string | no |  |
| `quoteDateFrom` | query | string(date) | no |  |
| `quoteDateTo` | query | string(date) | no |  |
| `quoteDateFromTime` | query | string(time-local) | no |  |
| `quoteDateToTime` | query | string(time-local) | no |  |
| `tripType` | query | [`TripType`](#schema-triptype) | no |  |
| `salesChannel` | query | [`SalesChannelType`](#schema-saleschanneltype) | no |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — inline schema: integer(int64)

#### GET `/tripquote/report`

**Summary:** reportTripQuotes

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | query | string | no |  |
| `operatorId` | query | string | no |  |
| `quoteDateFrom` | query | string(date) | no |  |
| `quoteDateTo` | query | string(date) | no |  |
| `tripType` | query | [`TripType`](#schema-triptype) | no |  |
| `page` | query | integer(int32) | no |  |
| `limit` | query | integer(int32) | no |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`TripQuoteReportVO`](#schema-tripquotereportvo)

---

## Users

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/user/permits` | Return user profiles and permits |
| POST | `/user/rewards/{micrositeId}/{externalAgencyId}/{username}` | Add rewards |
| DELETE | `/user/rewards/{micrositeId}/{externalAgencyId}/{username}` | Clear rewards |
| GET | `/user/{micrositeId}` | Return users |
| GET | `/user/{micrositeId}/{externalAgencyId}` | Return users by agency |
| PUT | `/user/{micrositeId}/{externalAgencyId}` | Updates an existing user |
| POST | `/user/{micrositeId}/{externalAgencyId}` | Creates a new user |
| GET | `/user/{micrositeId}/{externalAgencyId}/{username}` | Return user by its ID |


#### GET `/user/permits`

**Summary:** Return user profiles and permits

**Description:** Returns the profiles and permits the authenticated user may assign when creating or updating another user.

Use a value from `profiles[].profile` in the user's `profile` property.

- `userPermits.disabled` accepts only IDs from `profiles[].permits` for the selected profile.
- `userPermits.custom` accepts only IDs from `assignablePermits[].id` that are not already included in the selected profile.

Omitted or null permits preserves their current values. Sending `[]` removes all permissions.


**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`UserPermitsCatalogVO`](#schema-userpermitscatalogvo)

#### POST `/user/rewards/{micrositeId}/{externalAgencyId}/{username}`

**Summary:** Add rewards

**Description:** Add an amount of rewards to a user

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `externalAgencyId` | path | string | yes | Agency |
| `username` | path | string | yes | Username |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`UserRewardsVO`](#schema-userrewardsvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`UserRewardsVO`](#schema-userrewardsvo)

#### DELETE `/user/rewards/{micrositeId}/{externalAgencyId}/{username}`

**Summary:** Clear rewards

**Description:** Clear all rewards from a user

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `externalAgencyId` | path | string | yes | Agency |
| `username` | path | string | yes | Username |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`DeleteResponse`](#schema-deleteresponse)

#### GET `/user/{micrositeId}`

**Summary:** Return users

**Description:** This method returns the list of users for a given microsite.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `from` | query | string(date) | no | From creation date (yyyyMMdd) |
| `to` | query | string(date) | no | To creation date (yyyyMMdd) |
| `ufrom` | query | string(date) | no | From last update date (yyyyMMdd) |
| `uto` | query | string(date) | no | To last update date (yyyyMMdd) |
| `first` | query | integer(int32) | no | First result |
| `limit` | query | integer(int32) | no | Number of results |

_Pagination: query-param based (`first`/`limit`)._

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetUsersRS`](#schema-getusersrs)

#### GET `/user/{micrositeId}/{externalAgencyId}`

**Summary:** Return users by agency

**Description:** This method returns the list of users for a given microsite and agency.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `externalAgencyId` | path | string | yes | Agency |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `from` | query | string(date) | no | From creation date (yyyyMMdd) |
| `to` | query | string(date) | no | To creation date (yyyyMMdd) |
| `ufrom` | query | string(date) | no | From last update date (yyyyMMdd) |
| `uto` | query | string(date) | no | To last update date (yyyyMMdd) |
| `first` | query | integer(int32) | no | First result |
| `limit` | query | integer(int32) | no | Number of results |

_Pagination: query-param based (`first`/`limit`)._

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetUsersRS`](#schema-getusersrs)

#### PUT `/user/{micrositeId}/{externalAgencyId}`

**Summary:** Updates an existing user

**Description:** This method updates an exuser/isting user. If you need to create a new user you have to invoke the POST variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `externalAgencyId` | path | string | yes | Agency |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`UsersVO`](#schema-usersvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`UsersVO`](#schema-usersvo)

#### POST `/user/{micrositeId}/{externalAgencyId}`

**Summary:** Creates a new user

**Description:** This method creates a new user. If you need to update an existing user you have to invoke the PUT variant.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `externalAgencyId` | path | string | yes | Agency |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `welcomeEmail` | query | boolean | no | Send Welcome Email |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`UsersVO`](#schema-usersvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`UsersVO`](#schema-usersvo)

#### GET `/user/{micrositeId}/{externalAgencyId}/{username}`

**Summary:** Return user by its ID

**Description:** This method returns information about an user given its identifier.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `externalAgencyId` | path | string | yes | Agency |
| `username` | path | string | yes | Username |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`UsersVO`](#schema-usersvo)

---

## Web content - Accommodations

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/accommodations` | Returns the active accommodations |
| GET | `/accommodations/datasheet` | Returns a list of accommodations datasheet |
| GET | `/accommodations/preferred/{micrositeId}` | Return preferred accommodations |
| GET | `/accommodations/{accommodationId}/datasheet` | Returns a accommodation datasheet |
| GET | `/facilites/accommodation` | Return all accommodation facilities by language |
| GET | `/facilites/room` | Return all room facilities by language |
| GET | `/mealplan/{micrositeId}` | Return meal plans by microsite |


#### GET `/accommodations`

**Summary:** Returns the active accommodations

**Description:** To retrieve the accommodation datasheet use '/accommodations/{accommodationId}/datasheet' or '/accommodations/datasheet'

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `first` | query | integer(int32) | yes | First result |
| `limit` | query | integer(int32) | yes | Number of results |

_Pagination: query-param based (`first`/`limit`)._

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiStaticContentAccommodationsVO`](#schema-apistaticcontentaccommodationsvo)

#### GET `/accommodations/datasheet`

**Summary:** Returns a list of accommodations datasheet

**Description:** This method returns a list of accommodations datasheet.

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `accommodationId` | query | array[string] | yes | Accommodations code |
| `lang` | query | [`Language`](#schema-language) | no | Language |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiStaticContentAccommodationsDataSheetVO`](#schema-apistaticcontentaccommodationsdatasheetvo)

#### GET `/accommodations/preferred/{micrositeId}`

**Summary:** Return preferred accommodations

**Description:** This method returns preferred accommodations in a given microsite. You can filter by country or destination and get the names in several languages.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `destinationId` | query | string | no | Destination |
| `countryCode` | query | string | no | Country |
| `first` | query | integer(int32) | no | First |
| `limit` | query | integer(int32) | no | Limit |
| `lang` | query | [`Language`](#schema-language) | no | Language |

_Pagination: query-param based (`first`/`limit`)._

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetHotelRS`](#schema-gethotelrs)

#### GET `/accommodations/{accommodationId}/datasheet`

**Summary:** Returns a accommodation datasheet

**Description:** This method returns a accommodation datasheet.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `accommodationId` | path | string | yes | Accommodations code |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | query | [`Language`](#schema-language) | no | Language |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiStaticContentAccommodationDataSheetVO`](#schema-apistaticcontentaccommodationdatasheetvo)

#### GET `/facilites/accommodation`

**Summary:** Return all accommodation facilities by language

**Description:** This method returns all accommodation facilities available. You can get accommodation facilities in several languages.

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | query | [`Language`](#schema-language) | yes | Language |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`AccommodationFacilityListVO`](#schema-accommodationfacilitylistvo)

#### GET `/facilites/room`

**Summary:** Return all room facilities by language

**Description:** This method returns all room facilities available. You can get room facilities in several languages.

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | query | [`Language`](#schema-language) | yes | Language |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`RoomFacilityListVO`](#schema-roomfacilitylistvo)

#### GET `/mealplan/{micrositeId}`

**Summary:** Return meal plans by microsite

**Description:** This method return all meal plans available for a given microsite. You can get meal plans in several languages.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | query | [`Language`](#schema-language) | no | Language |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetMealPlanRS`](#schema-getmealplanrs)

---

## Web content - Blog

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/blog/{micrositeId}` | Return blog information by microsite |
| GET | `/blog/{micrositeId}/post/{postId}` | Return a published blog post |


#### GET `/blog/{micrositeId}`

**Summary:** Return blog information by microsite

**Description:** This method returns the public blog data, categories and published posts for a microsite.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | query | [`Language`](#schema-language) | no | Language |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetBlogRS`](#schema-getblogrs)

#### GET `/blog/{micrositeId}/post/{postId}`

**Summary:** Return a published blog post

**Description:** This method returns a published blog post for the microsite and language requested.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `postId` | path | integer(int32) | yes | Blog post |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | query | [`Language`](#schema-language) | no | Language |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetBlogPostRS`](#schema-getblogpostrs)

---

## Web content - Closed tour

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/static/closedtour/customdatasheet/{dataSheetId}` | Get Custom Datasheet |
| PUT | `/static/closedtour/customdatasheet/{dataSheetId}` | Updates Custom Datasheet |
| DELETE | `/static/closedtour/customdatasheet/{lang}/{dataSheetId}` | Deletes Custom Data Sheet |
| POST | `/static/closedtour/datasheet` | Get Datasheets |
| GET | `/static/closedtour/datasheet/{dataSheetId}` | Get Datasheet |
| POST | `/static/closedtour/{lang}` | Search Closed Tours |
| GET | `/static/closedtour/{lang}/{closedTourId}` | Get Closed Tour |


#### GET `/static/closedtour/customdatasheet/{dataSheetId}`

**Summary:** Get Custom Datasheet

**Description:** This method returns a Custom Closed Tour Datasheet <a href="/admin/api/documentation/index.xhtml#staticcustomdatasheet"> data</a>

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `dataSheetId` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiStaticDatasheetResponseVO`](#schema-apistaticdatasheetresponsevo)

#### PUT `/static/closedtour/customdatasheet/{dataSheetId}`

**Summary:** Updates Custom Datasheet

**Description:** This method updates a Custom Closed Tour Datasheet <a href="/admin/api/documentation/index.xhtml#staticupdatecustomdatasheet"> data</a>

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `dataSheetId` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `*/*` — Schema: [`ApiStaticUpdateCustomDataSheetsRequestVO`](#schema-apistaticupdatecustomdatasheetsrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiStaticDatasheetResponseVO`](#schema-apistaticdatasheetresponsevo)

#### DELETE `/static/closedtour/customdatasheet/{lang}/{dataSheetId}`

**Summary:** Deletes Custom Data Sheet

**Description:** This method Deletes a custom  closedtour data sheet <a href="/admin/api/documentation/index.xhtml#staticdeletecustomdatasheet"> data</a>

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | path | [`Language`](#schema-language) | yes |  |
| `dataSheetId` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`DeleteResponse`](#schema-deleteresponse)

#### POST `/static/closedtour/datasheet`

**Summary:** Get Datasheets

**Description:** This method returns a provider Closed Tour Datasheets <a href="/admin/api/documentation/index.xhtml#staticdatasheets"> data</a>

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiStaticCustomDataSheetsRequestVO`](#schema-apistaticcustomdatasheetsrequestvo)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiStaticDatasheetsResponseVO`](#schema-apistaticdatasheetsresponsevo)

#### GET `/static/closedtour/datasheet/{dataSheetId}`

**Summary:** Get Datasheet

**Description:** This method returns a Closed Tour Datasheet <a href="/admin/api/documentation/index.xhtml#staticdatasheet"> data</a>

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `dataSheetId` | path | string | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiStaticDatasheetResponseVO`](#schema-apistaticdatasheetresponsevo)

#### POST `/static/closedtour/{lang}`

**Summary:** Search Closed Tours

**Description:** This method returns Closed Tours. <a href="/admin/api/documentation/index.xhtml#staticclosedtourfilter"> Filter</a>

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | path | [`Language`](#schema-language) | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

Content-Type: `application/json` — Schema: [`ApiStaticContentSearchClosedTours`](#schema-apistaticcontentsearchclosedtours)

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiStaticContentSearchClosedToursResponseVO`](#schema-apistaticcontentsearchclosedtoursresponsevo)

#### GET `/static/closedtour/{lang}/{closedTourId}`

**Summary:** Get Closed Tour

**Description:** This method returns a Closed Tour <a href="/admin/api/documentation/index.xhtml#staticclosedtour"> data</a>

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `closedTourId` | path | string | yes |  |
| `lang` | path | [`Language`](#schema-language) | yes |  |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`ApiStaticContentHolidayPackageVO`](#schema-apistaticcontentholidaypackagevo)

---

## Web content - Cruises

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/cruise/port` | Return all ports |
| GET | `/cruise/port/{cruisePortId}` | Return a port by its id |
| GET | `/cruise/{cruiseId}/itinerary` | Return an itinerary by ID  |
| GET | `/cruise/{micrositeId}` | Return all cruises |
| GET | `/cruise/{micrositeId}/cruiseLine` | Return cruise line characteristics |
| GET | `/cruise/{micrositeId}/ship` | Return all ships |
| GET | `/cruise/{micrositeId}/ship/{shipId}` | Return cruise ship by ID |
| GET | `/cruise/{micrositeId}/{cruiseId}/departures` | Return cruise departures |


#### GET `/cruise/port`

**Summary:** Return all ports

**Description:** This method returns all the downloaded ports. You can filter by cruise line.

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `first` | query | integer(int32) | no | First |
| `limit` | query | integer(int32) | no | Limit |

_Pagination: query-param based (`first`/`limit`)._

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetCruisePortRS`](#schema-getcruiseportrs)

#### GET `/cruise/port/{cruisePortId}`

**Summary:** Return a port by its id

**Description:** This method returns the port of a CruiseService by its ID

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `cruisePortId` | path | string | yes | cruisePortId |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | query | [`Language`](#schema-language) | no | Language |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`CruisePortVO`](#schema-cruiseportvo)

#### GET `/cruise/{cruiseId}/itinerary`

**Summary:** Return an itinerary by ID 

**Description:** This method returns the destinations and the departure time of an itinerary given an ID

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `cruiseId` | path | string | yes | cruiseId |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`CruiseItineraryVO`](#schema-cruiseitineraryvo)

#### GET `/cruise/{micrositeId}`

**Summary:** Return all cruises

**Description:** This method returns all the cruises of a microsite. You can filter by region, month, port, nights, cruise line and ship.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `region` | query | [`CruiseRegion`](#schema-cruiseregion) | no | region |
| `month` | query | string | no | To filter by month use yearMonth format(YYYY-MM) |
| `originPort` | query | string | no | originPort |
| `nights` | query | string | no | nights |
| `cruiseLine` | query | [`CruiseLine`](#schema-cruiseline) | no | cruiseLine |
| `ship` | query | string | no | ship |
| `first` | query | integer(int32) | no | First |
| `limit` | query | integer(int32) | no | Limit |

_Pagination: query-param based (`first`/`limit`)._

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetCruiseDataSheetRS`](#schema-getcruisedatasheetrs)

#### GET `/cruise/{micrositeId}/cruiseLine`

**Summary:** Return cruise line characteristics

**Description:** This method returns the characteristics and the benefits of a cruise lines

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `cruiseLine` | query | [`CruiseLine`](#schema-cruiseline) | yes | cruiseLine |
| `lang` | query | [`Language`](#schema-language) | no | Language |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`CruiseLineCharacteristicsVO`](#schema-cruiselinecharacteristicsvo)

#### GET `/cruise/{micrositeId}/ship`

**Summary:** Return all ships

**Description:** This method returns all the ships of a microsite. You can filter by cruise line.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `cruiseLine` | query | [`CruiseLine`](#schema-cruiseline) | no | cruiseLine |
| `first` | query | integer(int32) | no | First |
| `limit` | query | integer(int32) | no | Limit |

_Pagination: query-param based (`first`/`limit`)._

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetCruiseShipRS`](#schema-getcruiseshiprs)

#### GET `/cruise/{micrositeId}/ship/{shipId}`

**Summary:** Return cruise ship by ID

**Description:** This method returns the ship of a CruiseService by its ID

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `shipId` | path | string | yes | shipId |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | query | [`Language`](#schema-language) | no | Language |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`CruiseShipVO`](#schema-cruiseshipvo)

#### GET `/cruise/{micrositeId}/{cruiseId}/departures`

**Summary:** Return cruise departures

**Description:** This method return the price calendar of a cruise given its ID

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `cruiseId` | path | string | yes | cruiseId |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetCruiseDepartureRS`](#schema-getcruisedeparturers)

---

## Web content - Providers

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/providers/configurations/{micrositeId}` | Return providers configuration of microsite |


#### GET `/providers/configurations/{micrositeId}`

**Summary:** Return providers configuration of microsite

**Description:** This method returns a providers configuration list by user's microsite

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: array of [`ProviderConfigurationVO`](#schema-providerconfigurationvo)

---

## Web content - Theme

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/theme/{micrositeId}` | Return microsite themes |


#### GET `/theme/{micrositeId}`

**Summary:** Return microsite themes

**Description:** This method return all themes available for a given microsite.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetThemeRS`](#schema-getthemers)

---

## Web content - Tickets

### Endpoints

| Method | Path | Summary |
|---|---|---|
| GET | `/ticket/categories` | Return all ticket categories |
| GET | `/ticket/datasheet/{micrositeId}/{ticketId}` | Return info ticket |
| GET | `/ticket/preferred/{micrositeId}` | Return preferred tickets |


#### GET `/ticket/categories`

**Summary:** Return all ticket categories

**Description:** This method returns all ticket categories translated in the requested language.

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |
| `lang` | header | [`Language`](#schema-language) | yes | Language |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`TicketCategoryRS`](#schema-ticketcategoryrs)

#### GET `/ticket/datasheet/{micrositeId}/{ticketId}`

**Summary:** Return info ticket

**Description:** This method return info tickets in a given microsite by ticketId.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |
| `ticketId` | path | string | yes | ticketId |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `lang` | query | [`Language`](#schema-language) | no | Language |

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`TicketDataVO`](#schema-ticketdatavo)

#### GET `/ticket/preferred/{micrositeId}`

**Summary:** Return preferred tickets

**Description:** This method returns preferred tickets in a given microsite. You can filter by country or destination and get the names in several languages.

**Path parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `micrositeId` | path | string | yes | Microsite |

**Query parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `first` | query | integer(int32) | no | First |
| `limit` | query | integer(int32) | no | Limit |
| `lang` | query | [`Language`](#schema-language) | no | Language |

_Pagination: query-param based (`first`/`limit`)._

**Header parameters:**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `auth-token` | header | string | yes | Authentication token |

**Request body:**

_None_

**Responses:**

**default** — default response  
Content-Type: `application/json` — Schema: [`GetTicketRS`](#schema-getticketrs)

---

## Schemas Appendix

Every schema referenced (directly or transitively) by any endpoint above, expanded once. 567 schemas reached out of 590 total in the spec.


### `AcademyTitle` {#schema-academytitle}

Enum values: 'Dr', 'Prof', 'ProfDr'


### `AccommodationFacilityListVO` {#schema-accommodationfacilitylistvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `facilities` | array[[`AccommodationFacilityVO`](#schema-accommodationfacilityvo)] | no |  |


### `AccommodationFacilityVO` {#schema-accommodationfacilityvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `icon` | string | no |  |
| `id` | integer(int32) | no |  |
| `priority` | integer(int32) | no |  |
| `translations` | object (inline) | no |  |


### `AccommodationSubtype` {#schema-accommodationsubtype}

Enum values: 'HOTEL', 'MOTELS', 'RESORTS', 'BED_AND_BR', 'RYOKANS', 'INNS', 'RIADS', 'ECONOMY_H', 'HEALTH_RESORTS', 'CAPSULE_HOTELS', 'LOVE_HOTELS', 'HOSTEL', 'LUXURY_TENTS', 'STUDENT_AC', 'APARTHOTELS', 'OTHER', 'APARTMENT', 'GUEST_AC', 'RESIDENCES', 'FARM_STAYS', 'HOLIDAY_PARKS', 'VILLAS', 'CAMPSITES', 'BOATS', 'GUEST_HOUSES', 'HOLIDAY_HOMES', 'LODGES', 'HOMESTAYS', 'COUNTRY_HOUSES', 'CHALETS', 'CONDOS', 'COTTAGES', 'GITES'


### `AccomodationType` {#schema-accomodationtype}

Enum values: 'HOTEL', 'APARTMENT'


### `AdditionalPaymentVO` {#schema-additionalpaymentvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amount` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `date` | string(date-time) | no |  |
| `deferredAmount` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `deferredPaymentLimitDate` | string(date-time) | no |  |
| `description` | string | no |  |
| `id` | integer(int32) | no |  |
| `orderNumber` | string | no |  |
| `paymentMethod` | string | no |  |
| `paymentResponse` | string | no |  |
| `paymentScheme` | string | no |  |
| `paymentSystem` | string | no |  |


### `AdditionalRequiredDataType` {#schema-additionalrequireddatatype}

Enum values: 'TRAVELC', 'PROVIDER'


### `AddressVO` {#schema-addressvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `city` | string | no |  |
| `postCode` | string | no |  |
| `street` | string | no |  |


### `AdvanceVO` {#schema-advancevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `reference` | string | no |  |
| `reportReference` | string | no |  |
| `requestedAmount` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `requestedDate` | string(date-time) | no |  |
| `resolvedDate` | string(date-time) | no |  |
| `status` | [`Status`](#schema-status) | no |  |


### `AgeRangeVO` {#schema-agerangevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `max` | integer(int32) | no |  |
| `min` | integer(int32) | no |  |


### `AgencyCreditVO` {#schema-agencycreditvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `agencyDepositMovementVO` | array[[`AgencyDepositMovementVO`](#schema-agencydepositmovementvo)] | no |  |
| `balance` | string | no |  |


### `AgencyDepositMovementVO` {#schema-agencydepositmovementvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `currency` | string | no |  |
| `id` | integer(int32) | no |  |
| `remark` | string | no |  |
| `timestamp` | string | no |  |
| `user` | string | no |  |
| `value` | number(double) | no |  |


### `AgencyDepositVO` {#schema-agencydepositvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `quantityAgencyBalance` | number(double) | no |  |
| `remarkAgencyBalance` | string | no |  |


### `AgencyDomainVO` {#schema-agencydomainvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `allowExternalLogging` | boolean | no |  |
| `defaultDomain` | boolean | no |  |
| `domain` | string | no |  |
| `redirectUrl` | string | no |  |


### `AgencyHotelVO` {#schema-agencyhotelvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `colorTemplate` | string | no |  |
| `hotelId` | string | no |  |


### `AgencyManagementFeeVO` {#schema-agencymanagementfeevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `agencyAmount` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `groundServicesAmount` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `transportServicesAmount` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |


### `AgencyManagerAgencyVO` {#schema-agencymanageragencyvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `externalId` | string | no |  |
| `id` | integer(int32) | no |  |
| `name` | string | no |  |


### `AgencyManagerRequestVO` {#schema-agencymanagerrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | yes |  |
| `externalId` | string | no |  |
| `name` | string | yes |  |


### `AgencyManagerUserVO` {#schema-agencymanageruservo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `agency` | string | no |  |
| `email` | string | no |  |
| `username` | string | no |  |


### `AgencyManagerVO` {#schema-agencymanagervo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `agencies` | array[[`AgencyManagerAgencyVO`](#schema-agencymanageragencyvo)] | no |  |
| `externalId` | string | no |  |
| `id` | integer(int32) | no |  |
| `microsite` | string | no |  |
| `name` | string | no |  |
| `users` | array[[`AgencyManagerUserVO`](#schema-agencymanageruservo)] | no |  |


### `AgencyProviderConfigVO` {#schema-agencyproviderconfigvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `extraInfo` | string | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |


### `AgencyVO` {#schema-agencyvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `addressText` | string | no |  |
| `advancePaymentVisible` | boolean | no |  |
| `agencyManagementFeeAllowed` | boolean | no |  |
| `agencyManagerId` | string | no |  |
| `billingCompany` | string | no |  |
| `billingEmail` | string | no |  |
| `businessName` | string | no |  |
| `captioActive` | boolean | no |  |
| `city` | string | no |  |
| `contactPersonLastName` | string | no |  |
| `contactPersonName` | string | no |  |
| `country` | string | no |  |
| `creditCardPaymentVisible` | boolean | no |  |
| `creditPaymentAllowed` | boolean | no |  |
| `currentCaptioUsers` | integer(int32) | no |  |
| `defaultBusinessTravelPolicy` | string | no |  |
| `deferredDays` | integer(int32) | no |  |
| `deferredLimit` | number(double) | no |  |
| `deferredPaymentAllowed` | boolean | no |  |
| `distributeOvercommission` | boolean | no |  |
| `documentNumber` | string | no |  |
| `domains` | array[[`AgencyDomainVO`](#schema-agencydomainvo)] | no |  |
| `email` | string | no |  |
| `expensesToolVisible` | boolean | no |  |
| `externalCredit` | boolean | no |  |
| `fee` | [`FeeVO`](#schema-feevo) | no |  |
| `hotel` | [`AgencyHotelVO`](#schema-agencyhotelvo) | no |  |
| `id` | string | yes |  |
| `internalRemarks` | string | no |  |
| `invoiceType` | [`InvoiceType`](#schema-invoicetype) | no |  |
| `logo` | [`ImageUrlVO`](#schema-imageurlvo) | no |  |
| `managementBusinessTravelPolicy` | string | no |  |
| `managementGroup` | string | no |  |
| `maxCaptioUsers` | integer(int32) | no |  |
| `minimumAmountFirstPayment` | number(double) | no |  |
| `minimumFirstPayment` | [`MinimumFirstPaymentType`](#schema-minimumfirstpaymenttype) | no |  |
| `name` | string | no |  |
| `overcommissionMicrositePercentage` | number(double) | no |  |
| `overcommissionOperatorPercentage` | number(double) | no |  |
| `phoneNumber` | string | no |  |
| `postalCode` | string | no |  |
| `providerConfigs` | array[[`AgencyProviderConfigVO`](#schema-agencyproviderconfigvo)] | no |  |
| `province` | string | no |  |
| `region` | string | no |  |
| `seniorBusinessTravelPolicy` | string | no |  |
| `socialNetworks` | [`SocialNetworksVO`](#schema-socialnetworksvo) | no |  |
| `staffBusinessTravelPolicy` | string | no |  |
| `substractDeferredPaymentDays` | boolean | no |  |
| `supervisorBusinessTravelPolicy` | string | no |  |
| `taxes` | number(double) | no |  |
| `whatsappNumber` | string | no |  |


### `AirlineVO` {#schema-airlinevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | no |  |
| `country` | string | no |  |
| `imageUrl` | string | no |  |
| `name` | string | no |  |


### `ApiAccommodationBookAccommodationRequestVO` {#schema-apiaccommodationbookaccommodationrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `combinationKey` | string | yes |  |


### `ApiAccommodationBookCombinationVO` {#schema-apiaccommodationbookcombinationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `cancellationPolicies` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `commentToAccommodation` | string | no |  |
| `mealPlan` | [`MealPlanVO`](#schema-mealplanvo) | no |  |
| `offer` | [`ApiAccommodationOfferVO`](#schema-apiaccommodationoffervo) | no |  |
| `onRequest` | boolean | no |  |
| `price` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `priceBreakdown` | [`ApiAccommodationPriceBreakdownVO`](#schema-apiaccommodationpricebreakdownvo) | no |  |
| `recommendedSellingPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `remarks` | array[string] | no |  |
| `rooms` | array[[`ApiAccommodationRoomVO`](#schema-apiaccommodationroomvo)] | no |  |


### `ApiAccommodationBookRequestVO` {#schema-apiaccommodationbookrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodation` | [`ApiAccommodationBookAccommodationRequestVO`](#schema-apiaccommodationbookaccommodationrequestvo) | yes |  |
| `externalReference` | string | no |  |
| `fakeBooking` | [`ApiBookFakeBookingVO`](#schema-apibookfakebookingvo) | no |  |


### `ApiAccommodationBookedAccommodationVO` {#schema-apiaccommodationbookedaccommodationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `bookingReference` | string | no |  |
| `category` | [`ApiAccommodationCategoryVO`](#schema-apiaccommodationcategoryvo) | no |  |
| `checkIn` | string(date) | yes |  |
| `checkOut` | string(date) | yes |  |
| `code` | string | no |  |
| `combination` | [`ApiAccommodationBookCombinationVO`](#schema-apiaccommodationbookcombinationvo) | no |  |
| `errorMessage` | string | no |  |
| `geolocation` | [`GeolocationVO`](#schema-geolocationvo) | no |  |
| `giataId` | integer(int32) | no |  |
| `name` | string | no |  |
| `providerBookingReference` | string | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |


### `ApiAccommodationBookedResponseVO` {#schema-apiaccommodationbookedresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodation` | [`ApiAccommodationBookedAccommodationVO`](#schema-apiaccommodationbookedaccommodationvo) | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `bookingReference` | string | no |  |
| `distributions` | array[[`DistributionVO`](#schema-distributionvo)] | no |  |
| `externalReference` | string | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |


### `ApiAccommodationCategoryVO` {#schema-apiaccommodationcategoryvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | no |  |
| `name` | string | no |  |


### `ApiAccommodationConfirmAccommodationRequestVO` {#schema-apiaccommodationconfirmaccommodationrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `combinationKey` | string | yes |  |


### `ApiAccommodationConfirmAccommodationResponseVO` {#schema-apiaccommodationconfirmaccommodationresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `category` | [`ApiAccommodationCategoryVO`](#schema-apiaccommodationcategoryvo) | no |  |
| `checkIn` | string(date) | yes |  |
| `checkOut` | string(date) | yes |  |
| `code` | string | no |  |
| `combination` | [`ApiAccommodationConfirmCombinationResponseVO`](#schema-apiaccommodationconfirmcombinationresponsevo) | no |  |
| `geolocation` | [`GeolocationVO`](#schema-geolocationvo) | no |  |
| `giataId` | integer(int32) | no |  |
| `name` | string | no |  |


### `ApiAccommodationConfirmCombinationResponseVO` {#schema-apiaccommodationconfirmcombinationresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `cancellationPolicies` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `combinationKey` | string | no |  |
| `mealPlan` | [`MealPlanVO`](#schema-mealplanvo) | no |  |
| `offer` | [`ApiAccommodationOfferVO`](#schema-apiaccommodationoffervo) | no |  |
| `onRequest` | boolean | no |  |
| `price` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `priceBreakdown` | [`ApiAccommodationPriceBreakdownVO`](#schema-apiaccommodationpricebreakdownvo) | no |  |
| `recommendedSellingPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `remarks` | array[string] | no |  |
| `rooms` | array[[`ApiAccommodationRoomVO`](#schema-apiaccommodationroomvo)] | no |  |


### `ApiAccommodationConfirmRequestVO` {#schema-apiaccommodationconfirmrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodation` | [`ApiAccommodationConfirmAccommodationRequestVO`](#schema-apiaccommodationconfirmaccommodationrequestvo) | yes |  |


### `ApiAccommodationConfirmResponseVO` {#schema-apiaccommodationconfirmresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodation` | [`ApiAccommodationConfirmAccommodationResponseVO`](#schema-apiaccommodationconfirmaccommodationresponsevo) | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `requiredField` | [`ApiBookRequiredFieldResponseVO`](#schema-apibookrequiredfieldresponsevo) | no |  |
| `warnings` | array[[`WarningVO`](#schema-warningvo)] | no |  |


### `ApiAccommodationOfferVO` {#schema-apiaccommodationoffervo}

| Field | Type | Required | Description |
|---|---|---|---|
| `strikethroughPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |


### `ApiAccommodationPreBookResponseVO` {#schema-apiaccommodationprebookresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodation` | [`ApiAccommodationPrebookAccommodationVO`](#schema-apiaccommodationprebookaccommodationvo) | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `distributions` | array[[`DistributionVO`](#schema-distributionvo)] | no |  |
| `warnings` | array[[`WarningVO`](#schema-warningvo)] | no |  |


### `ApiAccommodationPrebookAccommodationRequestVO` {#schema-apiaccommodationprebookaccommodationrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `combinationKey` | string | yes |  |
| `commentToAccommodation` | string | no |  |


### `ApiAccommodationPrebookAccommodationVO` {#schema-apiaccommodationprebookaccommodationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `category` | [`ApiAccommodationCategoryVO`](#schema-apiaccommodationcategoryvo) | no |  |
| `checkIn` | string(date) | yes |  |
| `checkOut` | string(date) | yes |  |
| `code` | string | no |  |
| `combination` | [`ApiAccommodationPrebookCombinationVO`](#schema-apiaccommodationprebookcombinationvo) | no |  |
| `geolocation` | [`GeolocationVO`](#schema-geolocationvo) | no |  |
| `giataId` | integer(int32) | no |  |
| `name` | string | no |  |


### `ApiAccommodationPrebookCombinationVO` {#schema-apiaccommodationprebookcombinationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `cancellationPolicies` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `combinationKey` | string | no |  |
| `commentToAccommodation` | string | no |  |
| `mealPlan` | [`MealPlanVO`](#schema-mealplanvo) | no |  |
| `offer` | [`ApiAccommodationOfferVO`](#schema-apiaccommodationoffervo) | no |  |
| `onRequest` | boolean | no |  |
| `price` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `priceBreakdown` | [`ApiAccommodationPriceBreakdownVO`](#schema-apiaccommodationpricebreakdownvo) | no |  |
| `recommendedSellingPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `remarks` | array[string] | no |  |
| `rooms` | array[[`ApiAccommodationRoomVO`](#schema-apiaccommodationroomvo)] | no |  |


### `ApiAccommodationPrebookRequestVO` {#schema-apiaccommodationprebookrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodation` | [`ApiAccommodationPrebookAccommodationRequestVO`](#schema-apiaccommodationprebookaccommodationrequestvo) | yes |  |
| `distributions` | array[[`DistributionVO`](#schema-distributionvo)] | yes |  |


### `ApiAccommodationPriceBreakdownVO` {#schema-apiaccommodationpricebreakdownvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `agencyFee` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `rsp` | [`RspVO`](#schema-rspvo) | no |  |


### `ApiAccommodationQuoteAccommodationsVO` {#schema-apiaccommodationquoteaccommodationsvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | no |  |
| `combinations` | array[[`ApiAccommodationQuoteCombinationVO`](#schema-apiaccommodationquotecombinationvo)] | no |  |
| `quoteSingleNeeded` | boolean | no |  |


### `ApiAccommodationQuoteCombinationVO` {#schema-apiaccommodationquotecombinationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `cancellationPolicies` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `combinationKey` | string | no |  |
| `currentCancellationType` | [`CancellationTypeVO`](#schema-cancellationtypevo) | no |  |
| `mealPlan` | [`MealPlanVO`](#schema-mealplanvo) | no |  |
| `offer` | [`ApiAccommodationOfferVO`](#schema-apiaccommodationoffervo) | no |  |
| `onRequest` | boolean | no |  |
| `price` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `priceBreakdown` | [`ApiAccommodationPriceBreakdownVO`](#schema-apiaccommodationpricebreakdownvo) | no |  |
| `recommendedSellingPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `remarks` | array[string] | no |  |
| `rooms` | array[[`ApiAccommodationRoomVO`](#schema-apiaccommodationroomvo)] | no |  |


### `ApiAccommodationQuoteFilterRequestVO` {#schema-apiaccommodationquotefilterrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `bestCombinations` | boolean | no |  |
| `includeOnRequestOptions` | boolean | no |  |
| `maxCombinations` | integer(int32) | no |  |


### `ApiAccommodationQuoteRequestVO` {#schema-apiaccommodationquoterequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodations` | array[string] | no |  |
| `checkIn` | string(date) | yes |  |
| `checkOut` | string(date) | yes |  |
| `destinationId` | string | no |  |
| `distributions` | array[[`ApiBookDistributionRequestVO`](#schema-apibookdistributionrequestvo)] | yes |  |
| `filter` | [`ApiAccommodationQuoteFilterRequestVO`](#schema-apiaccommodationquotefilterrequestvo) | no |  |
| `language` | [`Language`](#schema-language) | no |  |
| `sourceMarket` | string | no |  |
| `timeout` | integer(int32) | no |  |
| `tripType` | [`TripType`](#schema-triptype) | no |  |


### `ApiAccommodationQuoteResponseVO` {#schema-apiaccommodationquoteresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodations` | array[[`ApiAccommodationQuoteAccommodationsVO`](#schema-apiaccommodationquoteaccommodationsvo)] | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `providerTraces` | array[[`ProviderTraceVO`](#schema-providertracevo)] | no |  |
| `total` | integer(int32) | no |  |


### `ApiAccommodationQuoteSingleAccommodationFilterRequestVO` {#schema-apiaccommodationquotesingleaccommodationfilterrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `includeOnRequestOptions` | boolean | no |  |


### `ApiAccommodationQuoteSingleAccommodationRequestVO` {#schema-apiaccommodationquotesingleaccommodationrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `checkIn` | string(date) | yes |  |
| `checkOut` | string(date) | yes |  |
| `distributions` | array[[`ApiBookDistributionRequestVO`](#schema-apibookdistributionrequestvo)] | yes |  |
| `filter` | [`ApiAccommodationQuoteSingleAccommodationFilterRequestVO`](#schema-apiaccommodationquotesingleaccommodationfilterrequestvo) | no |  |
| `language` | [`Language`](#schema-language) | no |  |
| `sourceMarket` | string | no |  |
| `timeout` | integer(int32) | no |  |
| `tripType` | [`TripType`](#schema-triptype) | no |  |


### `ApiAccommodationQuoteSingleAccommodationResponseVO` {#schema-apiaccommodationquotesingleaccommodationresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodation` | [`ApiAccommodationQuoteSingleVO`](#schema-apiaccommodationquotesinglevo) | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |


### `ApiAccommodationQuoteSingleCombinationVO` {#schema-apiaccommodationquotesinglecombinationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `cancellationPolicies` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `combinationKey` | string | no |  |
| `currentCancellationType` | [`CancellationTypeVO`](#schema-cancellationtypevo) | no |  |
| `mealPlan` | [`MealPlanVO`](#schema-mealplanvo) | no |  |
| `offer` | [`ApiAccommodationOfferVO`](#schema-apiaccommodationoffervo) | no |  |
| `onRequest` | boolean | no |  |
| `price` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `priceBreakdown` | [`ApiAccommodationPriceBreakdownVO`](#schema-apiaccommodationpricebreakdownvo) | no |  |
| `recommendedSellingPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `rooms` | array[[`ApiAccommodationRoomVO`](#schema-apiaccommodationroomvo)] | no |  |


### `ApiAccommodationQuoteSingleVO` {#schema-apiaccommodationquotesinglevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | no |  |
| `combinations` | array[[`ApiAccommodationQuoteSingleCombinationVO`](#schema-apiaccommodationquotesinglecombinationvo)] | no |  |


### `ApiAccommodationRoomVO` {#schema-apiaccommodationroomvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | no |  |
| `groupingRoomType` | string | no |  |


### `ApiAdditionalRequiredPassengerDataVO` {#schema-apiadditionalrequiredpassengerdatavo}

| Field | Type | Required | Description |
|---|---|---|---|
| `additionalRequiredDataType` | [`AdditionalRequiredDataType`](#schema-additionalrequireddatatype) | no |  |
| `answer` | string | no |  |
| `answerExample` | string | no |  |
| `dateAnswer` | string(date) | no |  |
| `hidden` | boolean | no |  |
| `mainContact` | boolean | no |  |
| `mandatory` | boolean | no |  |
| `providerQuestionMapper` | [`ProviderQuestionMapper`](#schema-providerquestionmapper) | no |  |
| `questionCode` | string | no |  |
| `questionDescription` | string | no |  |
| `questionOptions` | array[[`ApiQuestionOptionVO`](#schema-apiquestionoptionvo)] | no |  |
| `questionType` | [`QuestionType`](#schema-questiontype) | no |  |
| `rangeEndResponse` | integer(int32) | no |  |
| `rangeStartResponse` | integer(int32) | no |  |
| `regex` | string | no |  |
| `selectedOption` | [`ApiQuestionOptionVO`](#schema-apiquestionoptionvo) | no |  |
| `timeAnswer` | string(time-local) | no |  |
| `travelerNumber` | integer(int32) | no |  |
| `trueOrFalseResponse` | boolean | no |  |


### `ApiAuditDataResponseVO` {#schema-apiauditdataresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |


### `ApiBookCancellationFeeResponseVO` {#schema-apibookcancellationfeeresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `cancellationFee` | [`MoneyVO`](#schema-moneyvo) | no |  |


### `ApiBookDistributionRequestVO` {#schema-apibookdistributionrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `persons` | array[[`ApiBookPersonAgeRequestVO`](#schema-apibookpersonagerequestvo)] | yes |  |


### `ApiBookFakeBookingVO` {#schema-apibookfakebookingvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |


### `ApiBookPersonAgeRequestVO` {#schema-apibookpersonagerequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `age` | integer(int32) | yes |  |


### `ApiBookRequiredAdditionalFieldResponseVO` {#schema-apibookrequiredadditionalfieldresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `additionalRequiredData` | array[[`ApiAdditionalRequiredPassengerDataVO`](#schema-apiadditionalrequiredpassengerdatavo)] | no |  |
| `contactPerson` | array[[`RequiredPassengerData`](#schema-requiredpassengerdata)] | no |  |
| `otherPersons` | array[[`RequiredPassengerData`](#schema-requiredpassengerdata)] | no |  |
| `roomHolders` | array[[`RequiredPassengerData`](#schema-requiredpassengerdata)] | no |  |


### `ApiBookRequiredFieldResponseVO` {#schema-apibookrequiredfieldresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `contactPerson` | array[[`RequiredPassengerData`](#schema-requiredpassengerdata)] | no |  |
| `otherPersons` | array[[`RequiredPassengerData`](#schema-requiredpassengerdata)] | no |  |
| `roomHolders` | array[[`RequiredPassengerData`](#schema-requiredpassengerdata)] | no |  |


### `ApiBookedTransferVO` {#schema-apibookedtransfervo}

| Field | Type | Required | Description |
|---|---|---|---|
| `additionalCharacteristics` | array[string] | no |  |
| `arrivalTransportDateTime` | string(date-time) | no | Original arrival transport date and time in local time, when the transfer starts at a transport base. Absent if unavailable. |
| `arrivalTransportNumber` | string | no |  |
| `bookingReference` | string | no |  |
| `cancellationPolicies` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `characteristics` | array[string] | no |  |
| `confirmedPickupTime` | boolean | no |  |
| `departureDate` | string(date-time) | no |  |
| `departureTransportDateTime` | string(date-time) | no | Original departure transport date and time in local time, when the transfer ends at a transport base. Absent if unavailable. |
| `departureTransportNumber` | string | no |  |
| `driverTracking` | string | no |  |
| `duration` | string(duration) | no |  |
| `image` | string | no |  |
| `maximumWaitingMinutes` | integer(int32) | no |  |
| `onRequest` | boolean | no |  |
| `pickupInformation` | string | no |  |
| `price` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `productType` | [`TransferProductType`](#schema-transferproducttype) | no |  |
| `provider` | string | no |  |
| `providerPurchaseToken` | string | no |  |
| `remarks` | string | no |  |
| `serviceStatus` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `serviceType` | [`TransferServiceType`](#schema-transferservicetype) | no |  |
| `telephoneAssistance` | string | no |  |
| `transferKey` | string | no |  |
| `transferType` | [`TransferVehicleType`](#schema-transfervehicletype) | no |  |
| `units` | integer(int32) | no |  |
| `vehicleDescriptionText` | string | no |  |
| `voucherURL` | string | no |  |


### `ApiClosedTourAccommodationClosedTourCombinationVO` {#schema-apiclosedtouraccommodationclosedtourcombinationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `arrivalDate` | string(date) | no |  |
| `code` | string | no |  |
| `combinationKey` | string | no |  |
| `departureDate` | string(date) | no |  |
| `name` | string | no |  |
| `price` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `room` | integer(int32) | no |  |
| `roomType` | string | no |  |


### `ApiClosedTourAccommodationClosedTourVO` {#schema-apiclosedtouraccommodationclosedtourvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodationKey` | string | no |  |
| `arrivalDate` | string(date-time) | no |  |
| `code` | string | no |  |
| `combinations` | array[[`ApiClosedTourAccommodationClosedTourCombinationVO`](#schema-apiclosedtouraccommodationclosedtourcombinationvo)] | no |  |
| `departureDate` | string(date-time) | no |  |
| `index` | string | no |  |
| `name` | string | no |  |
| `requiresAccommodationRequest` | boolean | no |  |


### `ApiClosedTourAccommodationOptionClosedTourVO` {#schema-apiclosedtouraccommodationoptionclosedtourvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodation` | array[[`ApiClosedTourAccommodationClosedTourVO`](#schema-apiclosedtouraccommodationclosedtourvo)] | no |  |
| `step` | integer(int32) | no |  |


### `ApiClosedTourAccommodationOptionCombinationsRequestVO` {#schema-apiclosedtouraccommodationoptioncombinationsrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodationCode` | string | no |  |
| `accommodationStep` | integer(int32) | no |  |
| `tourKey` | string | no |  |


### `ApiClosedTourAccommodationOptionCombinationsResponseVO` {#schema-apiclosedtouraccommodationoptioncombinationsresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `additionalServices` | array[[`ClosedTourAdditionalServiceVO`](#schema-closedtouradditionalservicevo)] | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `combinations` | array[[`ApiClosedTourAccommodationClosedTourCombinationVO`](#schema-apiclosedtouraccommodationclosedtourcombinationvo)] | no |  |


### `ApiClosedTourBookRequestVO` {#schema-apiclosedtourbookrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `additionalPassengerData` | array[[`ApiAdditionalRequiredPassengerDataVO`](#schema-apiadditionalrequiredpassengerdatavo)] | no |  |
| `additionalServicesCodes` | array[string] | no |  |
| `externalReference` | string | no |  |
| `fakeBooking` | [`ApiBookFakeBookingVO`](#schema-apibookfakebookingvo) | no |  |
| `tourKey` | string | no |  |


### `ApiClosedTourBookedResponseVO` {#schema-apiclosedtourbookedresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `bookingReference` | string | no |  |
| `closedTour` | [`ApiClosedTourBookedVO`](#schema-apiclosedtourbookedvo) | no |  |
| `distributions` | array[[`DistributionVO`](#schema-distributionvo)] | no |  |
| `externalReference` | string | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |


### `ApiClosedTourBookedVO` {#schema-apiclosedtourbookedvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `additionalServices` | array[[`ClosedTourAdditionalServiceVO`](#schema-closedtouradditionalservicevo)] | no |  |
| `bookingReference` | string | no |  |
| `itineraryDestinations` | string | no |  |
| `modality` | [`ApiClosedTourModalityVO`](#schema-apiclosedtourmodalityvo) | no |  |
| `name` | string | no |  |
| `remarks` | string | no |  |
| `selectedTransport` | [`ApiClosedTourTransportOptionVO`](#schema-apiclosedtourtransportoptionvo) | no |  |
| `startDate` | string(date-time) | no |  |
| `startDestination` | string | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |


### `ApiClosedTourConfirmRequestVO` {#schema-apiclosedtourconfirmrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `additionalServicesCodes` | array[string] | no |  |
| `availabilityId` | integer(int32) | no |  |
| `modalityCode` | string | no |  |
| `selectedAccommodations` | array[[`ApiClosedTourSelectedAccommodationOptionClosedTourVO`](#schema-apiclosedtourselectedaccommodationoptionclosedtourvo)] | no |  |
| `tourKey` | string | no |  |
| `transportKey` | string | no |  |


### `ApiClosedTourConfirmResponseVO` {#schema-apiclosedtourconfirmresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `modality` | [`ApiClosedTourModalityVO`](#schema-apiclosedtourmodalityvo) | no |  |
| `remarks` | string | no |  |
| `requiredPassengerData` | [`ApiBookRequiredAdditionalFieldResponseVO`](#schema-apibookrequiredadditionalfieldresponsevo) | no |  |
| `selectedPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `selectedTransport` | [`ApiClosedTourTransportOptionVO`](#schema-apiclosedtourtransportoptionvo) | no |  |
| `tourKey` | string | no |  |
| `warnings` | array[[`WarningVO`](#schema-warningvo)] | no |  |


### `ApiClosedTourDataSheetVO` {#schema-apiclosedtourdatasheetvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodations` | integer(int32) | no |  |
| `destinations` | string | no |  |
| `name` | string | no |  |
| `nights` | integer(int32) | no |  |
| `supplier` | string | no |  |


### `ApiClosedTourModalityVO` {#schema-apiclosedtourmodalityvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `cancellationPolicies` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `code` | string | no |  |
| `commissionPriceAmount` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `informationProvider` | string | no |  |
| `modalityKey` | string | no |  |
| `name` | string | no |  |
| `netCommissionablePrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `netPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `onRequest` | boolean | no |  |


### `ApiClosedTourPreBookResponseVO` {#schema-apiclosedtourprebookresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `availabilityId` | integer(int32) | no |  |
| `modality` | [`ApiClosedTourModalityVO`](#schema-apiclosedtourmodalityvo) | no |  |
| `selectedPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `selectedTransport` | [`ApiClosedTourTransportOptionVO`](#schema-apiclosedtourtransportoptionvo) | no |  |
| `tourKey` | string | no |  |
| `warnings` | array[[`WarningVO`](#schema-warningvo)] | no |  |


### `ApiClosedTourPrePostNightsClosedTourRequestVO` {#schema-apiclosedtourprepostnightsclosedtourrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `startDate` | string(date) | no |  |


### `ApiClosedTourPrePostNightsClosedTourResponseVO` {#schema-apiclosedtourprepostnightsclosedtourresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `postNights` | integer(int32) | no |  |
| `preNights` | integer(int32) | no |  |


### `ApiClosedTourPrebookRequestVO` {#schema-apiclosedtourprebookrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `additionalPassengerData` | array[[`ApiAdditionalRequiredPassengerDataVO`](#schema-apiadditionalrequiredpassengerdatavo)] | no |  |
| `additionalServicesCodes` | array[string] | no |  |
| `availabilityId` | integer(int32) | no |  |
| `distributions` | array[[`DistributionVO`](#schema-distributionvo)] | no |  |
| `tourKey` | string | no |  |


### `ApiClosedTourQuoteClosedTourVO` {#schema-apiclosedtourquoteclosedtourvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodationOptions` | array[[`ApiClosedTourAccommodationOptionClosedTourVO`](#schema-apiclosedtouraccommodationoptionclosedtourvo)] | no |  |
| `additionalServices` | array[[`ClosedTourAdditionalServiceVO`](#schema-closedtouradditionalservicevo)] | no |  |
| `availabilityId` | integer(int32) | no |  |
| `closedTourDataSheet` | [`ApiClosedTourDataSheetVO`](#schema-apiclosedtourdatasheetvo) | no |  |
| `modalities` | array[[`ApiClosedTourModalityVO`](#schema-apiclosedtourmodalityvo)] | no |  |
| `selectedNetPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `startDate` | string(date-time) | no |  |
| `tourKey` | string | no |  |
| `transportOptions` | array[[`ApiClosedTourTransportOptionVO`](#schema-apiclosedtourtransportoptionvo)] | no |  |


### `ApiClosedTourQuoteRequestVO` {#schema-apiclosedtourquoterequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `distributions` | array[[`ApiBookDistributionRequestVO`](#schema-apibookdistributionrequestvo)] | no |  |
| `language` | [`Language`](#schema-language) | no |  |
| `originCode` | string | no |  |
| `postNights` | integer(int32) | no |  |
| `preNights` | integer(int32) | no |  |
| `startDate` | string(date) | no |  |


### `ApiClosedTourQuoteResponseVO` {#schema-apiclosedtourquoteresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `closedTour` | [`ApiClosedTourQuoteClosedTourVO`](#schema-apiclosedtourquoteclosedtourvo) | no |  |
| `providerTraces` | array[[`ProviderTraceVO`](#schema-providertracevo)] | no |  |


### `ApiClosedTourSelectedAccommodationOptionClosedTourVO` {#schema-apiclosedtourselectedaccommodationoptionclosedtourvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodationKey` | string | no |  |
| `combinationKeys` | array[string] | no |  |
| `step` | integer(int32) | no |  |


### `ApiClosedTourTransportOptionVO` {#schema-apiclosedtourtransportoptionvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `onWayTransport` | [`ApiClosedTourTransportVO`](#schema-apiclosedtourtransportvo) | no |  |
| `returnTransport` | [`ApiClosedTourTransportVO`](#schema-apiclosedtourtransportvo) | no |  |
| `transportKey` | string | no |  |


### `ApiClosedTourTransportVO` {#schema-apiclosedtourtransportvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `arrival` | [`TransportBaseVO`](#schema-transportbasevo) | no |  |
| `arrivalCode` | string | no |  |
| `arrivalDate` | string(date-time) | no |  |
| `cancellationPolicies` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `code` | string | no |  |
| `configuration` | integer(int32) | no |  |
| `contractModalityId` | integer(int32) | no |  |
| `departure` | [`TransportBaseVO`](#schema-transportbasevo) | no |  |
| `departureCode` | string | no |  |
| `departureDate` | string(date-time) | no |  |
| `netPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `onewayPrice` | boolean | no |  |
| `providerCommissionPriceAmount` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `providerNetCommissionablePrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `providerNetPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |


### `ApiDanteAssetErrorVO` {#schema-apidanteasseterrorvo}

_Error information when the asset fails during ingestion._

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string — enum[INSUFFICIENT_CREDITS, CONTENT_LOAD_FAILURE, EXTRACTION_FAILED, ANALYSIS_ERROR, RECONCILIATION_ERROR, INGEST_ERROR] | no | Error code. |
| `message` | string | no | Human-readable error message. |


### `ApiDanteAssetVO` {#schema-apidanteassetvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `assetStatus` | string — enum[PENDING, ANALYZING, INCOMPLETE, UNSUITABLE, READY, ERROR] | no | Current processing status of the asset. `PENDING`: queued for analysis. `ANALYZING`: LLM analysis in progress. `INCOMPLETE`: analysis finished with partial results. `UNSUITABLE`: asset does not contain valid data for the declared service type. `READY`: documents extracted and available. `ERROR`: ingestion failed; asset can be resubmitted. |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `createdBy` | string | no | Identifier of the user who submitted the asset. |
| `creationDate` | string(date-time) | no | Date and time of submission. |
| `currency` | string | no | Currency of the asset as detected, as an ISO 4217 code. If detection fails, falls back to the declared currency. |
| `currencyVerified` | boolean | no | Whether the detected currency matches the declared one. |
| `declaredCurrency` | string | no | Currency declared at submission time, as an ISO 4217 code. |
| `declaredLanguage` | string | no | Language declared at submission time, as an ISO 639-1 code. |
| `documents` | array[[`ApiDanteDocumentVO`](#schema-apidantedocumentvo)] | no | List of extracted documents. Polymorphic: `serviceType` `HOTEL` yields a hotel document, `CLOSEDTOUR` yields a closed tour document. |
| `error` | [`ApiDanteAssetErrorVO`](#schema-apidanteasseterrorvo) | no |  |
| `externalId` | string | no | Client-assigned unique identifier for the asset. |
| `language` | string | no | Language of the asset as detected, as an ISO 639-1 code. If detection fails, falls back to the declared language. |
| `languageVerified` | boolean | no | Whether the detected language matches the declared one. |
| `operator` | string | no | Operator identifier associated with the asset. |
| `serviceType` | string — enum[HOTEL, CLOSEDTOUR] | no | Type of service described in the asset. |
| `warnings` | array[[`ApiDanteAssetWarningVO`](#schema-apidanteassetwarningvo)] | no | Asset-level processing warnings. |


### `ApiDanteAssetWarningVO` {#schema-apidanteassetwarningvo}

_Asset-level processing warnings._

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string — enum[UNSUITABLE_DOCUMENT, INCOMPLETE_OPERATIONS] | no | Stable warning code. |
| `message` | string | no | Human-readable warning message. |


### `ApiDanteClosedTourCancellationRangeVO` {#schema-apidanteclosedtourcancellationrangevo}

_Cancellation policy ranges._

| Field | Type | Required | Description |
|---|---|---|---|
| `days` | integer(int32) | no | Number of days before departure from which this cancellation penalty applies. |
| `percentage` | number(double) | no | Cancellation penalty as a percentage of the total price. |


### `ApiDanteClosedTourDestinationVO` {#schema-apidanteclosedtourdestinationvo}

_Destinations included in the tour._

| Field | Type | Required | Description |
|---|---|---|---|
| `country` | string | no | Country name as extracted from the document. |
| `destinationId` | string | no | Travel Compositor internal destination identifier, if matched. |
| `hotels` | array[[`ApiDanteClosedTourHotelVO`](#schema-apidanteclosedtourhotelvo)] | no | Possible hotels at this destination, as extracted from the document. |
| `name` | string | no | Destination name as extracted from the document. |


### `ApiDanteClosedTourDocumentVO` {#schema-apidanteclosedtourdocumentvo}

_Extracted closed tour document. Present when serviceType is CLOSEDTOUR._

| Field | Type | Required | Description |
|---|---|---|---|
| `cancellationRanges` | array[[`ApiDanteClosedTourCancellationRangeVO`](#schema-apidanteclosedtourcancellationrangevo)] | no | Cancellation policy ranges. |
| `destinations` | array[[`ApiDanteClosedTourDestinationVO`](#schema-apidanteclosedtourdestinationvo)] | no | Destinations included in the tour. |
| `documentId` | integer(int32) | no | Numeric identifier for this document. |
| `documentStatus` | string — enum[PENDING_PROCESSING, IN_PROGRESS, PENDING_REVIEW, VALIDATED, ERROR] | no | Current processing status of this document. `PENDING_PROCESSING`: queued. `IN_PROGRESS`: being processed. `PENDING_REVIEW`: processing complete; document is available for API consumption or pending human review in the Backoffice. `VALIDATED`: reviewed and a contract has been created in the platform. `ERROR`: processing failed. |
| `info` | [`ApiDanteClosedTourInfoVO`](#schema-apidanteclosedtourinfovo) | no |  |
| `itinerary` | [`ApiDanteClosedTourItineraryVO`](#schema-apidanteclosedtouritineraryvo) | no |  |
| `prices` | [`ApiDanteClosedTourPricesVO`](#schema-apidanteclosedtourpricesvo) | no |  |
| `processDate` | string(date-time) | no | Date and time when the document was last processed. |
| `serviceType` | string | yes |  |
| `settings` | [`ApiDanteClosedTourSettingsVO`](#schema-apidanteclosedtoursettingsvo) | no |  |
| `supplements` | array[[`ApiDanteClosedTourSupplementVO`](#schema-apidanteclosedtoursupplementvo)] | no | Supplements applicable to the tour. |
| `transportOptions` | [`ApiDanteClosedTourTransportOptionsVO`](#schema-apidanteclosedtourtransportoptionsvo) | no |  |
| `warnings` | array[[`ApiDanteClosedTourDocumentWarningVO`](#schema-apidanteclosedtourdocumentwarningvo)] | no | Document-level processing warnings specific to closed tour documents. |


### `ApiDanteClosedTourDocumentWarningVO` {#schema-apidanteclosedtourdocumentwarningvo}

_Document-level processing warnings specific to closed tour documents._

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string — enum[INFO_MISSING, INFO_TITLE_MISSING, INFO_DESCRIPTION_MISSING, INFO_INCLUDED_MISSING, INFO_EXCLUDED_MISSING, ITINERARY_MISSING, ITINERARY_SEGMENTS_MISSING, ITINERARY_MEETING_POINT_MISSING, ITINERARY_SEGMENT_DESTINATIONS_MISSING, ITINERARY_DESTINATION_COUNTRY_MISSING, ...] | no | Stable warning code. |
| `message` | string | no | Human-readable warning message. May include dynamic field indexes (e.g. itinerary.segments[0].destinations). |
| `section` | string — enum[info, remarks, serviceCounts, itinerary, meetingPoint, destinations, activities, hotels, priceFactors, prices, ...] | no | Section of the document this warning relates to. |
| `verbatim` | string | no | Verbatim excerpt from the original document that originated the warning, in the document's original language. Only present for UNREPRESENTED_CONTENT warnings. |


### `ApiDanteClosedTourHotelVO` {#schema-apidanteclosedtourhotelvo}

_Possible hotels at this destination, as extracted from the document._

| Field | Type | Required | Description |
|---|---|---|---|
| `hotelId` | string | no | Travel Compositor internal hotel identifier, if matched. |
| `name` | string | no | Hotel name as extracted from the document. |


### `ApiDanteClosedTourInfoVO` {#schema-apidanteclosedtourinfovo}

_General tour information._

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | no | Tour code as extracted from the document. |
| `description` | string | no | General tour description, in HTML format. |
| `excluded` | string | no | What is not included in the tour price, in HTML format. |
| `hotelServiceCount` | integer(int32) | no | Number of hotel services included in the tour. |
| `included` | string | no | What is included in the tour price, in HTML format. |
| `remarks` | string | no | Additional remarks, in HTML format. |
| `title` | string | no | Tour title. |
| `transportServiceCount` | integer(int32) | no | Number of transport services included in the tour. |


### `ApiDanteClosedTourItineraryActivityVO` {#schema-apidanteclosedtouritineraryactivityvo}

_Activities at this destination._

| Field | Type | Required | Description |
|---|---|---|---|
| `activityType` | string — enum[INCLUDED, OPTIONAL] | no | Type of activity. `INCLUDED`: included in the tour price. `OPTIONAL`: optional; may require additional payment. |
| `description` | string | no | Activity description, in HTML format. |
| `title` | string | no | Activity title. |


### `ApiDanteClosedTourItineraryDestinationVO` {#schema-apidanteclosedtouritinerarydestinationvo}

_Destinations visited in this segment._

| Field | Type | Required | Description |
|---|---|---|---|
| `activities` | array[[`ApiDanteClosedTourItineraryActivityVO`](#schema-apidanteclosedtouritineraryactivityvo)] | no | Activities at this destination. |
| `country` | string | no | Country name as extracted from the document. |
| `destinationId` | string | no | Travel Compositor internal destination identifier, if matched. |
| `name` | string | no | Destination name as extracted from the document. |


### `ApiDanteClosedTourItinerarySegmentVO` {#schema-apidanteclosedtouritinerarysegmentvo}

_Itinerary segments._

| Field | Type | Required | Description |
|---|---|---|---|
| `days` | integer(int32) | no | Number of days this segment covers. |
| `description` | string | no | Segment description, in HTML format. |
| `destinations` | array[[`ApiDanteClosedTourItineraryDestinationVO`](#schema-apidanteclosedtouritinerarydestinationvo)] | no | Destinations visited in this segment. |
| `title` | string | no | Segment title. |


### `ApiDanteClosedTourItineraryVO` {#schema-apidanteclosedtouritineraryvo}

_Itinerary by segments._

| Field | Type | Required | Description |
|---|---|---|---|
| `meetingPoint` | string | no | Meeting point for the tour departure. |
| `segments` | array[[`ApiDanteClosedTourItinerarySegmentVO`](#schema-apidanteclosedtouritinerarysegmentvo)] | no | Itinerary segments. |


### `ApiDanteClosedTourPricesCategoryVO` {#schema-apidanteclosedtourpricescategoryvo}

_Pricing categories (e.g. cabin types or service levels)._

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | no | Category code. |
| `name` | string | no | Category name. |
| `operationDays` | array[[`DayOfWeek`](#schema-dayofweek)] | no | Days of the week on which this category operates. |
| `periods` | array[[`ApiDanteClosedTourPricesPeriodVO`](#schema-apidanteclosedtourpricesperiodvo)] | no | Pricing periods within this category. |
| `remarks` | string | no | Additional remarks for this category. |
| `stopSales` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no | Date ranges during which this category is not available. |


### `ApiDanteClosedTourPricesDistributionPriceVO` {#schema-apidanteclosedtourpricesdistributionpricevo}

_Price for quad or higher occupancy._

| Field | Type | Required | Description |
|---|---|---|---|
| `childDiscountPercentage` | number(double) | no | Discount percentage applied to the child price. Only relevant when `ApiDanteClosedTourPricesVO.childrenCountedForRoomSize` is `false`. |
| `price` | number(double) | no | Price per adult for this occupancy. |


### `ApiDanteClosedTourPricesPeriodVO` {#schema-apidanteclosedtourpricesperiodvo}

_Pricing periods within this category._

| Field | Type | Required | Description |
|---|---|---|---|
| `dateRanges` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no | Date ranges that define this pricing period. |
| `doublePrice` | [`ApiDanteClosedTourPricesDistributionPriceVO`](#schema-apidanteclosedtourpricesdistributionpricevo) | no |  |
| `name` | string | no | Period name. |
| `quadPrice` | [`ApiDanteClosedTourPricesDistributionPriceVO`](#schema-apidanteclosedtourpricesdistributionpricevo) | no |  |
| `singlePrice` | [`ApiDanteClosedTourPricesDistributionPriceVO`](#schema-apidanteclosedtourpricesdistributionpricevo) | no |  |
| `triplePrice` | [`ApiDanteClosedTourPricesDistributionPriceVO`](#schema-apidanteclosedtourpricesdistributionpricevo) | no |  |


### `ApiDanteClosedTourPricesVO` {#schema-apidanteclosedtourpricesvo}

_Pricing structure including categories and periods._

| Field | Type | Required | Description |
|---|---|---|---|
| `adultTaxesPerPerson` | number(double) | no | Taxes per adult passenger. |
| `categories` | array[[`ApiDanteClosedTourPricesCategoryVO`](#schema-apidanteclosedtourpricescategoryvo)] | no | Pricing categories (e.g. cabin types or service levels). |
| `childAgeRange` | [`AgeRangeVO`](#schema-agerangevo) | no |  |
| `childTaxesPerPerson` | number(double) | no | Taxes per child passenger. |
| `childrenCountedForRoomSize` | boolean | no | Whether children count toward the room occupancy size. When `false`, prices may include a child discount percentage relative to the adult price, though this is not always the case. |


### `ApiDanteClosedTourSettingsVO` {#schema-apidanteclosedtoursettingsvo}

_Operational settings such as release days and passenger limits._

| Field | Type | Required | Description |
|---|---|---|---|
| `maxPassengers` | integer(int32) | no | Maximum number of passengers per booking. |
| `minPassengers` | integer(int32) | no | Minimum number of passengers required to make a booking. |
| `releaseDays` | integer(int32) | no | Minimum number of days before departure that bookings must be made. |


### `ApiDanteClosedTourSupplementVO` {#schema-apidanteclosedtoursupplementvo}

_Supplements applicable to the tour._

| Field | Type | Required | Description |
|---|---|---|---|
| `bookingWindows` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no | Booking date ranges during which the supplement is active. |
| `commissionable` | boolean | no | Whether the supplement is commissionable. |
| `doublePrice` | number(double) | no | Price for double occupancy. |
| `free` | boolean | no | Whether the supplement is free of charge. |
| `mandatory` | boolean | no | Whether the supplement is mandatory. |
| `name` | string | no | Supplement name. |
| `priceInPercentage` | boolean | no | Whether the price is expressed as a percentage. |
| `quadPrice` | number(double) | no | Price for quad or higher occupancy. |
| `refundable` | boolean | no | Whether the supplement is refundable. |
| `singlePrice` | number(double) | no | Price for single occupancy. |
| `travelWindows` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no | Travel date ranges during which the supplement is active. |
| `triplePrice` | number(double) | no | Price for triple occupancy. |


### `ApiDanteClosedTourTransportOptionLegSegmentVO` {#schema-apidanteclosedtourtransportoptionlegsegmentvo}

_Ordered list of flight segments forming this leg. A direct flight has one segment; a connecting flight has two or more._

| Field | Type | Required | Description |
|---|---|---|---|
| `airlineId` | integer(int32) | no | Travel Compositor airline identifier. |
| `airlineSource` | string | no | Airline as extracted from the document; a name or an IATA airline code. |
| `arrivalLocationId` | string | no | Travel Compositor identifier for the arrival airport. |
| `arrivalLocationSource` | string | no | Arrival airport as extracted from the document: an IATA code (e.g. "FRA") or a city name. |
| `arrivalTime` | string(time-local) | no | Local arrival time. |
| `departureLocationId` | string | no | Travel Compositor identifier for the departure airport. |
| `departureLocationSource` | string | no | Departure airport as extracted from the document: an IATA code (e.g. "EZE") or a city name (e.g. "Buenos Aires, Argentina"). |
| `departureTime` | string(time-local) | no | Local departure time. |
| `flightNumber` | string | no | Flight number. |
| `nightsOnBoard` | integer(int32) | no | Number of nights spent on board during this segment (0 for a day flight, 1 or more for an overnight flight). |


### `ApiDanteClosedTourTransportOptionLegVO` {#schema-apidanteclosedtourtransportoptionlegvo}

_Return flight or transport leg._

| Field | Type | Required | Description |
|---|---|---|---|
| `airlineId` | integer(int32) | no | Travel Compositor airline identifier of the first segment. |
| `airlineSource` | string | no | Airline as extracted from the document for the first segment; a name or an IATA airline code. |
| `arrivalTime` | string(time-local) | no | Local arrival time of the last segment. |
| `departureTime` | string(time-local) | no | Local departure time of the first segment. |
| `flightNumber` | string | no | Flight number of the first segment. |
| `nightsOnBoard` | integer(int32) | no | Total number of nights spent on board across all segments. |
| `segments` | array[[`ApiDanteClosedTourTransportOptionLegSegmentVO`](#schema-apidanteclosedtourtransportoptionlegsegmentvo)] | no | Ordered list of flight segments forming this leg. A direct flight has one segment; a connecting flight has two or more. |


### `ApiDanteClosedTourTransportOptionVO` {#schema-apidanteclosedtourtransportoptionvo}

_Available transport options by origin._

| Field | Type | Required | Description |
|---|---|---|---|
| `originId` | string | no | Travel Compositor identifier for the origin. This field mirrors the departure location of the first outbound segment. |
| `outboundLeg` | [`ApiDanteClosedTourTransportOptionLegVO`](#schema-apidanteclosedtourtransportoptionlegvo) | no |  |
| `pricePerPerson` | number(double) | no | Transport price per person. |
| `returnLeg` | [`ApiDanteClosedTourTransportOptionLegVO`](#schema-apidanteclosedtourtransportoptionlegvo) | no |  |
| `taxesPerPerson` | number(double) | no | Transport taxes per person. Extracted as an additional cost not included in the transport price. |


### `ApiDanteClosedTourTransportOptionsVO` {#schema-apidanteclosedtourtransportoptionsvo}

_Available transport options._

| Field | Type | Required | Description |
|---|---|---|---|
| `options` | array[[`ApiDanteClosedTourTransportOptionVO`](#schema-apidanteclosedtourtransportoptionvo)] | no | Available transport options by origin. |
| `transportAvailabilityVariable` | boolean | no | Whether transport availability varies by origin. |
| `transportIncludedInItinerary` | boolean | no | Whether the transport is counted as a day of the tour itinerary. |
| `transportPriceSeparate` | boolean | no | Whether the transport price is quoted separately from the tour price. |
| `transportPriceVariable` | boolean | no | Whether the transport price varies by origin. |


### `ApiDanteDocumentAcceptedResponseVO` {#schema-apidantedocumentacceptedresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `externalId` | string | no | Client-assigned identifier of the accepted document. |
| `status` | string — enum[ACCEPTED] | no | Acceptance status. Always ACCEPTED. |


### `ApiDanteDocumentVO` {#schema-apidantedocumentvo}

_List of extracted documents. Polymorphic: `serviceType` `HOTEL` yields a hotel document, `CLOSEDTOUR` yields a closed tour document._

| Field | Type | Required | Description |
|---|---|---|---|
| `documentId` | integer(int32) | no | Numeric identifier for this document. |
| `documentStatus` | string — enum[PENDING_PROCESSING, IN_PROGRESS, PENDING_REVIEW, VALIDATED, ERROR] | no | Current processing status of this document. `PENDING_PROCESSING`: queued. `IN_PROGRESS`: being processed. `PENDING_REVIEW`: processing complete; document is available for API consumption or pending human review in the Backoffice. `VALIDATED`: reviewed and a contract has been created in the platform. `ERROR`: processing failed. |
| `processDate` | string(date-time) | no | Date and time when the document was last processed. |
| `serviceType` | string | yes |  |


### `ApiDanteHotelAddressVO` {#schema-apidantehoteladdressvo}

_Hotel address details._

| Field | Type | Required | Description |
|---|---|---|---|
| `addressText` | string | no | Full address text as extracted from the document. |
| `email` | string | no | Email address. |
| `fax` | string | no | Fax number. |
| `locationName` | string | no | City or locality name. |
| `phone` | string | no | Phone number. |
| `postalCode` | string | no | Postal code. |


### `ApiDanteHotelCancellationPolicyVO` {#schema-apidantehotelcancellationpolicyvo}

_Cancellation policies for this rate._

| Field | Type | Required | Description |
|---|---|---|---|
| `days` | integer(int32) | no | Number of days before check-in from which this policy applies. |
| `percentage` | number(double) | no | Cancellation penalty as a percentage of the booking amount. |
| `travelWindows` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no | Travel date ranges during which this cancellation policy applies. |


### `ApiDanteHotelDocumentVO` {#schema-apidantehoteldocumentvo}

_Extracted hotel document. Present when serviceType is HOTEL._

| Field | Type | Required | Description |
|---|---|---|---|
| `documentId` | integer(int32) | no | Numeric identifier for this document. |
| `documentStatus` | string — enum[PENDING_PROCESSING, IN_PROGRESS, PENDING_REVIEW, VALIDATED, ERROR] | no | Current processing status of this document. `PENDING_PROCESSING`: queued. `IN_PROGRESS`: being processed. `PENDING_REVIEW`: processing complete; document is available for API consumption or pending human review in the Backoffice. `VALIDATED`: reviewed and a contract has been created in the platform. `ERROR`: processing failed. |
| `info` | [`ApiDanteHotelInfoVO`](#schema-apidantehotelinfovo) | no |  |
| `mealPlans` | array[[`ApiDanteHotelMealPlanVO`](#schema-apidantehotelmealplanvo)] | no | Meal plans extracted from the document. |
| `offers` | array[[`ApiDanteHotelOfferVO`](#schema-apidantehoteloffervo)] | no | Offers extracted from the document. |
| `processDate` | string(date-time) | no | Date and time when the document was last processed. |
| `rates` | array[[`ApiDanteHotelRateVO`](#schema-apidantehotelratevo)] | no | Rate plans extracted from the document. |
| `rooms` | array[[`ApiDanteHotelRoomVO`](#schema-apidantehotelroomvo)] | no | Room types extracted from the document. |
| `serviceType` | string | yes |  |
| `supplements` | array[[`ApiDanteHotelSupplementVO`](#schema-apidantehotelsupplementvo)] | no | Supplements extracted from the document. |
| `taxes` | array[[`ApiDanteHotelTaxVO`](#schema-apidantehoteltaxvo)] | no | Taxes and fiscal charges extracted from the document. |
| `warnings` | array[[`ApiDanteHotelDocumentWarningVO`](#schema-apidantehoteldocumentwarningvo)] | no | Document-level processing warnings specific to hotel documents. |


### `ApiDanteHotelDocumentWarningVO` {#schema-apidantehoteldocumentwarningvo}

_Document-level processing warnings specific to hotel documents._

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string — enum[INFO_MISSING, INFO_NAME_MISSING, INFO_HOTEL_ID_MISSING, INFO_DESTINATION_ID_MISSING, RATES_MISSING, RATES_SEASONS_MISSING, RATES_CANCELLATION_POLICIES_MISSING, ROOMS_MISSING, ROOMS_DISTRIBUTIONS_ALLOWED_MISSING, MEAL_PLANS_MISSING, ...] | no | Stable warning code. |
| `message` | string | no | Human-readable warning message. May include dynamic field indexes (e.g. rates[0].seasons). |
| `section` | string — enum[info, rates, rooms, mealPlans, offers, supplements, taxes] | no | Section of the document this warning relates to. Matches a root field of the document response. |
| `verbatim` | string | no | Verbatim excerpt from the original document that originated the warning, in the document's original language. Only present for UNREPRESENTED_CONTENT warnings. |


### `ApiDanteHotelInfoVO` {#schema-apidantehotelinfovo}

_General hotel information extracted from the document._

| Field | Type | Required | Description |
|---|---|---|---|
| `address` | [`ApiDanteHotelAddressVO`](#schema-apidantehoteladdressvo) | no |  |
| `childAgeRange` | [`AgeRangeVO`](#schema-agerangevo) | no |  |
| `childAgeRange2` | [`AgeRangeVO`](#schema-agerangevo) | no |  |
| `childAgeRange3` | [`AgeRangeVO`](#schema-agerangevo) | no |  |
| `childAgeRange4` | [`AgeRangeVO`](#schema-agerangevo) | no |  |
| `code` | string | no | Hotel code as extracted from the document. |
| `destinationCountry` | string | no | Country as extracted from the document. |
| `destinationId` | string | no | Travel Compositor internal destination identifier, if matched. |
| `destinationName` | string | no | Destination name as extracted from the document. |
| `hotelId` | string | no | Travel Compositor internal hotel identifier, if matched. |
| `infantAgeRange` | [`AgeRangeVO`](#schema-agerangevo) | no |  |
| `maximumStay` | integer(int32) | no | Maximum stay in nights. |
| `minimumAge` | integer(int32) | no | Minimum guest age. |
| `minimumStay` | integer(int32) | no | Minimum stay in nights. |
| `name` | string | no | Hotel name. |
| `releaseDays` | integer(int32) | no | Release policy in days. |
| `stopSales` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no | Travel date ranges during which bookings are not accepted. |
| `voucherRemarks` | string | no | Additional remarks for the voucher, in HTML format. |


### `ApiDanteHotelMealPlanVO` {#schema-apidantehotelmealplanvo}

_Meal plans extracted from the document._

| Field | Type | Required | Description |
|---|---|---|---|
| `basePrice` | number(double) | no |  |
| `fifthAdultPrice` | number(double) | no | Meal plan price for the fifth adult. |
| `fifthChildPriceRangeFirst` | number(double) | no | Meal plan price for the fifth child in the first child age range. |
| `fifthChildPriceRangeFourth` | number(double) | no | Meal plan price for the fifth child in the fourth child age range. |
| `fifthChildPriceRangeSecond` | number(double) | no | Meal plan price for the fifth child in the second child age range. |
| `fifthChildPriceRangeThird` | number(double) | no | Meal plan price for the fifth child in the third child age range. |
| `firstChildPriceRangeFirst` | number(double) | no | Meal plan price for the first child in the first child age range. |
| `firstChildPriceRangeFourth` | number(double) | no | Meal plan price for the first child in the fourth child age range. |
| `firstChildPriceRangeSecond` | number(double) | no | Meal plan price for the first child in the second child age range. |
| `firstChildPriceRangeThird` | number(double) | no | Meal plan price for the first child in the third child age range. |
| `fourthAdultPrice` | number(double) | no | Meal plan price for the fourth adult. |
| `fourthChildPriceRangeFirst` | number(double) | no | Meal plan price for the fourth child in the first child age range. |
| `fourthChildPriceRangeFourth` | number(double) | no | Meal plan price for the fourth child in the fourth child age range. |
| `fourthChildPriceRangeSecond` | number(double) | no | Meal plan price for the fourth child in the second child age range. |
| `fourthChildPriceRangeThird` | number(double) | no | Meal plan price for the fourth child in the third child age range. |
| `name` | string | no | Meal plan name as extracted from the document. |
| `price` | number(double) | no | Meal plan price per person. |
| `secondAdultPrice` | number(double) | no | Meal plan price for the second adult. |
| `secondChildPriceRangeFirst` | number(double) | no | Meal plan price for the second child in the first child age range. |
| `secondChildPriceRangeFourth` | number(double) | no | Meal plan price for the second child in the fourth child age range. |
| `secondChildPriceRangeSecond` | number(double) | no | Meal plan price for the second child in the second child age range. |
| `secondChildPriceRangeThird` | number(double) | no | Meal plan price for the second child in the third child age range. |
| `sixthAdultPrice` | number(double) | no | Meal plan price for the sixth adult. |
| `thirdAdultPrice` | number(double) | no | Meal plan price for the third adult. |
| `thirdChildPriceRangeFirst` | number(double) | no | Meal plan price for the third child in the first child age range. |
| `thirdChildPriceRangeFourth` | number(double) | no | Meal plan price for the third child in the fourth child age range. |
| `thirdChildPriceRangeSecond` | number(double) | no | Meal plan price for the third child in the second child age range. |
| `thirdChildPriceRangeThird` | number(double) | no | Meal plan price for the third child in the third child age range. |


### `ApiDanteHotelOfferVO` {#schema-apidantehoteloffervo}

_Offers extracted from the document._

| Field | Type | Required | Description |
|---|---|---|---|
| `applicationDays` | array[[`DayOfWeek`](#schema-dayofweek)] | no | Days of the week during the stay on which the offer applies. |
| `applicationType` | string — enum[LODGING, MEAL, LODGING_AND_MEAL, PER_NIGHT, PER_NIGHT_PERSON, PER_STAY, PER_STAY_PERSON] | no | How the offer is applied. `LODGING`: per accommodation. `MEAL`: per meal plan. `LODGING_AND_MEAL`: per accommodation and meal plan. `PER_NIGHT`: fixed price per night. `PER_NIGHT_PERSON`: fixed price per night per person. `PER_STAY`: fixed price per stay. `PER_STAY_PERSON`: fixed price per stay per person. |
| `bookingWindows` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no | Booking date ranges during which the offer is active. |
| `incompatibleOffers` | array[string] | no | Names of offers that cannot be combined with this one. |
| `maximumAdults` | integer(int32) | no | Maximum number of adults allowed. |
| `maximumChildren` | integer(int32) | no | Maximum number of children allowed. |
| `maximumStay` | integer(int32) | no | Maximum stay in nights for this offer. |
| `mealPlans` | array[string] | no | Names of mealplans aplicable for this offer. If empty, all mealplans from this contract are considered aplicable by default |
| `minimumAdults` | integer(int32) | no | Minimum number of adults required. |
| `minimumChildren` | integer(int32) | no | Minimum number of children required. |
| `minimumStay` | integer(int32) | no | Minimum stay in nights required for this offer. |
| `name` | string | no | Offer name. |
| `pay` | integer(int32) | no | Number of nights to pay. Only relevant when `type` is `STAY_TO_PAY`. |
| `price` | number(double) | no | Discount price amount. |
| `releaseDays` | integer(int32) | no | Release days restriction for this offer. |
| `stay` | integer(int32) | no | Number of nights to stay. Only relevant when `type` is `STAY_TO_PAY`. |
| `travelWindows` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no | Travel date ranges during which the offer is active. |
| `type` | string — enum[PERCENT, ABSOLUTE, STAY_TO_PAY] | no | How the offer discount is expressed. `PERCENT`: percentage discount. `ABSOLUTE`: fixed price discount. `STAY_TO_PAY`: stay a number of nights and pay fewer (e.g. stay 7, pay 6). |


### `ApiDanteHotelPaxPricesVO` {#schema-apidantehotelpaxpricesvo}

_Prices by person when the season is priced by pax._

| Field | Type | Required | Description |
|---|---|---|---|
| `basePrice` | number(double) | no |  |
| `fifthAdultPrice` | number(double) | no | Room price for the fifth adult. |
| `fifthChildPriceRangeFirst` | number(double) | no | Room price for the fifth child in the first child age range. |
| `fifthChildPriceRangeFourth` | number(double) | no | Room price for the fifth child in the fourth child age range. |
| `fifthChildPriceRangeSecond` | number(double) | no | Room price for the fifth child in the second child age range. |
| `fifthChildPriceRangeThird` | number(double) | no | Room price for the fifth child in the third child age range. |
| `firstChildPriceRangeFirst` | number(double) | no | Room price for the first child in the first child age range. |
| `firstChildPriceRangeFourth` | number(double) | no | Room price for the first child in the fourth child age range. |
| `firstChildPriceRangeSecond` | number(double) | no | Room price for the first child in the second child age range. |
| `firstChildPriceRangeThird` | number(double) | no | Room price for the first child in the third child age range. |
| `fourthAdultPrice` | number(double) | no | Room price for the fourth adult. |
| `fourthChildPriceRangeFirst` | number(double) | no | Room price for the fourth child in the first child age range. |
| `fourthChildPriceRangeFourth` | number(double) | no | Room price for the fourth child in the fourth child age range. |
| `fourthChildPriceRangeSecond` | number(double) | no | Room price for the fourth child in the second child age range. |
| `fourthChildPriceRangeThird` | number(double) | no | Room price for the fourth child in the third child age range. |
| `secondAdultPrice` | number(double) | no | Room price for the second adult. |
| `secondChildPriceRangeFirst` | number(double) | no | Room price for the second child in the first child age range. |
| `secondChildPriceRangeFourth` | number(double) | no | Room price for the second child in the fourth child age range. |
| `secondChildPriceRangeSecond` | number(double) | no | Room price for the second child in the second child age range. |
| `secondChildPriceRangeThird` | number(double) | no | Room price for the second child in the third child age range. |
| `sixthAdultPrice` | number(double) | no | Room price for the sixth adult. |
| `thirdAdultPrice` | number(double) | no | Room price for the third adult. |
| `thirdChildPriceRangeFirst` | number(double) | no | Room price for the third child in the first child age range. |
| `thirdChildPriceRangeFourth` | number(double) | no | Room price for the third child in the fourth child age range. |
| `thirdChildPriceRangeSecond` | number(double) | no | Room price for the third child in the second child age range. |
| `thirdChildPriceRangeThird` | number(double) | no | Room price for the third child in the third child age range. |


### `ApiDanteHotelRateVO` {#schema-apidantehotelratevo}

_Rate plans extracted from the document._

| Field | Type | Required | Description |
|---|---|---|---|
| `bookingWindows` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no | Booking date ranges during which this rate is available. |
| `cancellationPolicies` | array[[`ApiDanteHotelCancellationPolicyVO`](#schema-apidantehotelcancellationpolicyvo)] | no | Cancellation policies for this rate. |
| `maximumStay` | integer(int32) | no | Maximum stay in nights. |
| `minimumStay` | integer(int32) | no | Minimum stay in nights. |
| `name` | string | no | Rate plan name. |
| `releaseDays` | integer(int32) | no | Release policy in days. |
| `seasons` | array[[`ApiDanteHotelSeasonVO`](#schema-apidantehotelseasonvo)] | no | Seasons that define pricing for this rate. |


### `ApiDanteHotelRoomDistributionVO` {#schema-apidantehotelroomdistributionvo}

_Occupancy distributions allowed for this room type._

| Field | Type | Required | Description |
|---|---|---|---|
| `adults` | integer(int32) | no | Number of adults. |
| `children` | integer(int32) | no | Number of children. |


### `ApiDanteHotelRoomVO` {#schema-apidantehotelroomvo}

_Room types extracted from the document._

| Field | Type | Required | Description |
|---|---|---|---|
| `bedOptions` | array[[`BedOption`](#schema-bedoption)] | no | Bedding options per room |
| `description` | string | no | Room description as extracted from the document. |
| `distributionsAllowed` | array[[`ApiDanteHotelRoomDistributionVO`](#schema-apidantehotelroomdistributionvo)] | no | Occupancy distributions allowed for this room type. |
| `name` | string | no | Room name as extracted from the document. |
| `size` | integer(int32) | no | Room size in squared meters |


### `ApiDanteHotelSeasonMealPlanVO` {#schema-apidantehotelseasonmealplanvo}

_Meal plan pricing within this season._

| Field | Type | Required | Description |
|---|---|---|---|
| `basePrice` | number(double) | no |  |
| `fifthAdultPrice` | number(double) | no | Meal plan price for the fifth adult. |
| `fifthChildPriceRangeFirst` | number(double) | no | Meal plan price for the fifth child in the first child age range. |
| `fifthChildPriceRangeFourth` | number(double) | no | Meal plan price for the fifth child in the fourth child age range. |
| `fifthChildPriceRangeSecond` | number(double) | no | Meal plan price for the fifth child in the second child age range. |
| `fifthChildPriceRangeThird` | number(double) | no | Meal plan price for the fifth child in the third child age range. |
| `firstChildPriceRangeFirst` | number(double) | no | Meal plan price for the first child in the first child age range. |
| `firstChildPriceRangeFourth` | number(double) | no | Meal plan price for the first child in the fourth child age range. |
| `firstChildPriceRangeSecond` | number(double) | no | Meal plan price for the first child in the second child age range. |
| `firstChildPriceRangeThird` | number(double) | no | Meal plan price for the first child in the third child age range. |
| `fourthAdultPrice` | number(double) | no | Meal plan price for the fourth adult. |
| `fourthChildPriceRangeFirst` | number(double) | no | Meal plan price for the fourth child in the first child age range. |
| `fourthChildPriceRangeFourth` | number(double) | no | Meal plan price for the fourth child in the fourth child age range. |
| `fourthChildPriceRangeSecond` | number(double) | no | Meal plan price for the fourth child in the second child age range. |
| `fourthChildPriceRangeThird` | number(double) | no | Meal plan price for the fourth child in the third child age range. |
| `name` | string | no | Meal plan name as extracted from the document. |
| `price` | number(double) | no | Meal plan price. |
| `secondAdultPrice` | number(double) | no | Meal plan price for the second adult. |
| `secondChildPriceRangeFirst` | number(double) | no | Meal plan price for the second child in the first child age range. |
| `secondChildPriceRangeFourth` | number(double) | no | Meal plan price for the second child in the fourth child age range. |
| `secondChildPriceRangeSecond` | number(double) | no | Meal plan price for the second child in the second child age range. |
| `secondChildPriceRangeThird` | number(double) | no | Meal plan price for the second child in the third child age range. |
| `sixthAdultPrice` | number(double) | no | Meal plan price for the sixth adult. |
| `thirdAdultPrice` | number(double) | no | Meal plan price for the third adult. |
| `thirdChildPriceRangeFirst` | number(double) | no | Meal plan price for the third child in the first child age range. |
| `thirdChildPriceRangeFourth` | number(double) | no | Meal plan price for the third child in the fourth child age range. |
| `thirdChildPriceRangeSecond` | number(double) | no | Meal plan price for the third child in the second child age range. |
| `thirdChildPriceRangeThird` | number(double) | no | Meal plan price for the third child in the third child age range. |


### `ApiDanteHotelSeasonRoomDistributionPriceVO` {#schema-apidantehotelseasonroomdistributionpricevo}

_Prices broken down by occupancy distribution._

| Field | Type | Required | Description |
|---|---|---|---|
| `adults` | integer(int32) | no | Number of adults in this distribution. |
| `amount` | number(double) | no | Price for this occupancy distribution. |
| `children` | integer(int32) | no | Number of children in this distribution. |


### `ApiDanteHotelSeasonRoomVO` {#schema-apidantehotelseasonroomvo}

_Room pricing within this season._

| Field | Type | Required | Description |
|---|---|---|---|
| `distributionPrices` | array[[`ApiDanteHotelSeasonRoomDistributionPriceVO`](#schema-apidantehotelseasonroomdistributionpricevo)] | no | Prices broken down by occupancy distribution. |
| `name` | string | no | Room name as extracted from the document. |
| `onRequest` | integer(int32) | no | On-request allotment. |
| `paxPrices` | [`ApiDanteHotelPaxPricesVO`](#schema-apidantehotelpaxpricesvo) | no |  |
| `price` | number(double) | no | Base room price. |
| `quota` | integer(int32) | no | Allotment quota. |


### `ApiDanteHotelSeasonVO` {#schema-apidantehotelseasonvo}

_Seasons that define pricing for this rate._

| Field | Type | Required | Description |
|---|---|---|---|
| `checkInDays` | array[[`DayOfWeek`](#schema-dayofweek)] | no | Days of the week on which check-in is allowed. Empty means all days are allowed. |
| `checkOutDays` | array[[`DayOfWeek`](#schema-dayofweek)] | no | Days of the week on which check-out is allowed. Empty means all days are allowed. |
| `maximumStay` | integer(int32) | no | Maximum stay in nights for this season. |
| `mealPlans` | array[[`ApiDanteHotelSeasonMealPlanVO`](#schema-apidantehotelseasonmealplanvo)] | no | Meal plan pricing within this season. |
| `minimumStay` | integer(int32) | no | Minimum stay in nights for this season. |
| `name` | string | no | Season name. |
| `priceType` | string — enum[PAX, DISTRIBUTION] | no | How prices are expressed for this season. `PAX`: per person. `DISTRIBUTION`: varies by room occupancy distribution. |
| `releaseDays` | integer(int32) | no | Release policy in days for this season. |
| `rooms` | array[[`ApiDanteHotelSeasonRoomVO`](#schema-apidantehotelseasonroomvo)] | no | Room pricing within this season. |
| `seasonRanges` | array[[`LocalDateRangeAndOperationDaysVO`](#schema-localdaterangeandoperationdaysvo)] | no | Date ranges and operation days that define this season. |


### `ApiDanteHotelSupplementVO` {#schema-apidantehotelsupplementvo}

_Supplements extracted from the document._

| Field | Type | Required | Description |
|---|---|---|---|
| `applicationDays` | array[[`DayOfWeek`](#schema-dayofweek)] | no | Days of the week during the stay on which the supplement applies. |
| `applicationType` | string — enum[LODGING, MEAL, LODGING_AND_MEAL, PER_NIGHT, PER_NIGHT_PERSON, PER_STAY, PER_STAY_PERSON] | no | How the supplement is applied. When `type` is `PERCENT`: `LODGING` percentage of the accommodation price, `MEAL` percentage of the meal plan price, `LODGING_AND_MEAL` percentage of accommodation and meal plan combined. When `type` is `ABSOLUTE`: `PER_NIGHT` fixed amount per night, `PER_NIGHT_PERSON` fixed amount per night per person, `PER_STAY` fixed amount per stay, `PER_STAY_PERSON` fixed amount per stay per person. |
| `bookingWindows` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no | Booking date ranges during which the supplement is active. |
| `commissionable` | boolean | no | Whether the supplement is commissionable. |
| `mandatory` | boolean | no | Whether the supplement is mandatory. null if it could not be determined from the document. |
| `maximumAdults` | integer(int32) | no | Maximum number of adults allowed. |
| `maximumChildren` | integer(int32) | no | Maximum number of children allowed. |
| `maximumStay` | integer(int32) | no | Maximum stay in nights for this supplement. |
| `minimumAdults` | integer(int32) | no | Minimum number of adults required. |
| `minimumChildren` | integer(int32) | no | Minimum number of children required. |
| `minimumStay` | integer(int32) | no | Minimum stay in nights required for this supplement. |
| `name` | string | no | Supplement name. |
| `online` | boolean | no | Whether the supplement is charged at booking time (true) or paid at destination (false). null if it could not be determined from the document. |
| `percentage` | number(double) | no | Percentage value. Used when type is PERCENT. |
| `price` | number(double) | no | Fixed price amount. Used when type is ABSOLUTE. |
| `releaseDays` | integer(int32) | no | Release days restriction for this supplement. Can also be interpreted as the minimum advance booking required for the supplement to apply. |
| `travelWindows` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no | Travel date ranges during which the supplement is active. |
| `type` | string — enum[ABSOLUTE, PERCENT] | no | How the supplement price is expressed. `ABSOLUTE`: fixed amount whose application unit is determined by `applicationType` (`PER_NIGHT`, `PER_STAY`, etc.). `PERCENT`: percentage applied over a base price determined by `applicationType` (`LODGING`, `MEAL`, etc.). |


### `ApiDanteHotelTaxVO` {#schema-apidantehoteltaxvo}

_Tax or fiscal charge extracted from the hotel contract._

| Field | Type | Required | Description |
|---|---|---|---|
| `amount` | number(double) | no | Fixed monetary amount (when valueType is ABSOLUTE). |
| `applicationType` | string | no | Application type: how the value is applied (e.g. PER_NIGHT, PER_STAY_PERSON). |
| `bookingWindows` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no | Booking date ranges during which the tax applies. |
| `mandatory` | boolean | no | Whether the tax is mandatory for all eligible guests. |
| `maximumStay` | integer(int32) | no | Maximum number of nights for which the tax applies. |
| `minimumStay` | integer(int32) | no | Minimum number of nights required for the tax to apply. |
| `name` | string | no | Name of the tax as it appears in the document. |
| `online` | boolean | no | Whether the tax is charged at booking time (true) or paid at the property (false). |
| `percentage` | number(double) | no | Percentage value (when valueType is PERCENT). |
| `taxCategory` | string | no | Category of the tax: TAX, FEE, VAT, or OTHER. |
| `travelWindows` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no | Travel date ranges during which the tax applies. |
| `valueType` | string | no | Value type: ABSOLUTE or PERCENT. |


### `ApiDanteReprocessRequestVO` {#schema-apidantereprocessrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `currency` | string | no | Override currency, as an ISO 4217 code. Only accepted when the asset has no document yet (re-ingestion from ERROR state). |
| `language` | string | no | Override language, as an ISO 639-1 code. Only accepted when the asset has no document yet (re-ingestion from ERROR state). |


### `ApiMultiEngineBookRequestVO` {#schema-apimultienginebookrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodation` | [`ApiAccommodationBookAccommodationRequestVO`](#schema-apiaccommodationbookaccommodationrequestvo) | yes |  |
| `externalReference` | string | no |  |
| `fakeBooking` | [`ApiBookFakeBookingVO`](#schema-apibookfakebookingvo) | no |  |
| `transports` | [`ApiTransportBookTransportRequestVO`](#schema-apitransportbooktransportrequestvo) | yes |  |


### `ApiMultiEngineBookedResponseVO` {#schema-apimultienginebookedresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodation` | [`ApiAccommodationBookedAccommodationVO`](#schema-apiaccommodationbookedaccommodationvo) | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `bookingReference` | string | no |  |
| `distributions` | array[[`DistributionVO`](#schema-distributionvo)] | no |  |
| `externalReference` | string | no |  |
| `price` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `transports` | [`ApiTransportBookTransportServiceResponseVO`](#schema-apitransportbooktransportserviceresponsevo) | no |  |


### `ApiMultiEngineConfirmResponseVO` {#schema-apimultiengineconfirmresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodation` | [`ApiAccommodationConfirmAccommodationResponseVO`](#schema-apiaccommodationconfirmaccommodationresponsevo) | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `price` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `requiredField` | [`ApiBookRequiredFieldResponseVO`](#schema-apibookrequiredfieldresponsevo) | no |  |
| `transports` | [`ApiTransportConfirmTransportServiceResponseVO`](#schema-apitransportconfirmtransportserviceresponsevo) | no |  |
| `warnings` | array[[`WarningVO`](#schema-warningvo)] | no |  |


### `ApiMultiEnginePreBookResponseVO` {#schema-apimultiengineprebookresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodation` | [`ApiAccommodationPrebookAccommodationVO`](#schema-apiaccommodationprebookaccommodationvo) | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `distributions` | array[[`DistributionVO`](#schema-distributionvo)] | no |  |
| `price` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `transports` | [`ApiTransportPrebookTransportServiceResponseVO`](#schema-apitransportprebooktransportserviceresponsevo) | no |  |
| `warnings` | array[[`WarningVO`](#schema-warningvo)] | no |  |


### `ApiMultiEnginePrebookRequestVO` {#schema-apimultiengineprebookrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodation` | [`ApiAccommodationPrebookAccommodationRequestVO`](#schema-apiaccommodationprebookaccommodationrequestvo) | yes |  |
| `distributions` | array[[`DistributionVO`](#schema-distributionvo)] | yes |  |
| `transports` | [`ApiTransportPrebookTransportRequestVO`](#schema-apitransportprebooktransportrequestvo) | yes |  |


### `ApiMultiProductConfirmRequestVO` {#schema-apimultiproductconfirmrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodation` | [`ApiAccommodationConfirmAccommodationRequestVO`](#schema-apiaccommodationconfirmaccommodationrequestvo) | yes |  |
| `transports` | [`ApiTransportConfirmTransportRequestVO`](#schema-apitransportconfirmtransportrequestvo) | yes |  |


### `ApiQuestionOptionVO` {#schema-apiquestionoptionvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | no |  |
| `description` | string | no |  |


### `ApiStaticClosedTourDatasheetVO` {#schema-apistaticclosedtourdatasheetvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `endDestination` | [`DestinationVO`](#schema-destinationvo) | no |  |
| `itineraryDestinations` | array[[`ContractClosedTourItineraryDestinationVO`](#schema-contractclosedtouritinerarydestinationvo)] | no |  |
| `itinerarySegments` | array[[`ContractClosedTourItinerarySegmentVO`](#schema-contractclosedtouritinerarysegmentvo)] | no |  |
| `language` | [`Language`](#schema-language) | no |  |
| `startDestination` | [`DestinationVO`](#schema-destinationvo) | no |  |
| `translation` | [`ClosedTourDatasheetVO`](#schema-closedtourdatasheetvo) | no |  |


### `ApiStaticClosedTourService` {#schema-apistaticclosedtourservice}

| Field | Type | Required | Description |
|---|---|---|---|
| `activities` | integer(int32) | no |  |
| `destinations` | string | no |  |
| `fromDate` | string(date) | no |  |
| `hotels` | integer(int32) | no |  |
| `nights` | integer(int32) | no |  |
| `pricePerPerson` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `serviceId` | string | no |  |
| `title` | string | no |  |
| `transports` | integer(int32) | no |  |


### `ApiStaticContentAccommodationDataSheetVO` {#schema-apistaticcontentaccommodationdatasheetvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodationFacilities` | array[[`AccommodationFacilityVO`](#schema-accommodationfacilityvo)] | no |  |
| `accommodationSubtype` | [`AccommodationSubtype`](#schema-accommodationsubtype) | no |  |
| `accommodationType` | [`AccomodationType`](#schema-accomodationtype) | no |  |
| `address` | string | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `category` | [`HotelCategory`](#schema-hotelcategory) | no |  |
| `chain` | string | no |  |
| `description` | string | no |  |
| `destination` | [`IdeaPlaceVO`](#schema-ideaplacevo) | no |  |
| `geolocation` | [`GeolocationVO`](#schema-geolocationvo) | no |  |
| `giataId` | integer(int32) | no |  |
| `id` | string | no |  |
| `images` | array[[`ApiStaticContentAccommodationImageVO`](#schema-apistaticcontentaccommodationimagevo)] | no |  |
| `internalRemark` | string | no |  |
| `name` | string | no |  |
| `phoneNumber` | string | no |  |
| `ratings` | array[[`RatingVO`](#schema-ratingvo)] | no |  |
| `roomFacilities` | array[[`RoomFacilityVO`](#schema-roomfacilityvo)] | no |  |


### `ApiStaticContentAccommodationImageClassificationVO` {#schema-apistaticcontentaccommodationimageclassificationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | [`ClassificationType`](#schema-classificationtype) | no |  |


### `ApiStaticContentAccommodationImageVO` {#schema-apistaticcontentaccommodationimagevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `classification` | [`ApiStaticContentAccommodationImageClassificationVO`](#schema-apistaticcontentaccommodationimageclassificationvo) | no |  |
| `height` | integer(int32) | no |  |
| `url` | string | no |  |
| `width` | integer(int32) | no |  |


### `ApiStaticContentAccommodationVO` {#schema-apistaticcontentaccommodationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `countryCode` | string | no |  |
| `geolocation` | [`GeolocationVO`](#schema-geolocationvo) | no |  |
| `giataId` | integer(int32) | no |  |
| `id` | string | no |  |
| `lastUpdate` | string(date-time) | no |  |
| `name` | string | no |  |


### `ApiStaticContentAccommodationsDataSheetVO` {#schema-apistaticcontentaccommodationsdatasheetvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodations` | array[[`IdeaHotelDataVO`](#schema-ideahoteldatavo)] | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |


### `ApiStaticContentAccommodationsVO` {#schema-apistaticcontentaccommodationsvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodations` | array[[`ApiStaticContentAccommodationVO`](#schema-apistaticcontentaccommodationvo)] | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `pagination` | [`PaginationVO`](#schema-paginationvo) | no |  |


### `ApiStaticContentHolidayPackageVO` {#schema-apistaticcontentholidaypackagevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `closedTour` | [`HolidayPackageVO`](#schema-holidaypackagevo) | no |  |
| `dataSheetId` | string | no |  |


### `ApiStaticContentSearchClosedTours` {#schema-apistaticcontentsearchclosedtours}

| Field | Type | Required | Description |
|---|---|---|---|
| `country` | string | no |  |
| `destination` | string | no |  |
| `first` | integer(int32) | no |  |
| `limit` | integer(int32) | no |  |
| `month` | [`Month`](#schema-month) | no |  |
| `nights` | integer(int32) | no |  |


### `ApiStaticContentSearchClosedToursResponseVO` {#schema-apistaticcontentsearchclosedtoursresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `closedTours` | array[[`ApiStaticClosedTourService`](#schema-apistaticclosedtourservice)] | no |  |
| `pagination` | [`PaginationVO`](#schema-paginationvo) | no |  |


### `ApiStaticContentTicketVO` {#schema-apistaticcontentticketvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `adultTaxesAmount` | number(double) | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `cancellationRanges` | array[[`ContractTicketCancellationRangesVO`](#schema-contractticketcancellationrangesvo)] | no |  |
| `childTaxesAmount` | number(double) | no |  |
| `city` | string | no |  |
| `code` | string | yes |  |
| `currency` | [`Currency`](#schema-currency) | no |  |
| `datasheets` | object (inline) | yes |  |
| `daysAvailableBeforeRelease` | integer(int32) | no |  |
| `duration` | number(double) | no |  |
| `durationType` | [`DurationType`](#schema-durationtype) | no |  |
| `geolocation` | [`GeolocationVO`](#schema-geolocationvo) | yes |  |
| `imageUrls` | array[string] | no |  |
| `infantTaxesAmount` | number(double) | no |  |
| `locationOrigin` | boolean | no |  |
| `meetingPointOrigin` | boolean | no |  |
| `meetingPoints` | array[[`ContractTicketMeetingPointVO`](#schema-contractticketmeetingpointvo)] | no |  |
| `modalityCodes` | array[string] | no |  |
| `name` | string | yes |  |
| `productTypes` | array[[`TripType`](#schema-triptype)] | no |  |
| `releaseTime` | integer(int32) | no |  |
| `releaseTimeType` | [`DurationType`](#schema-durationtype) | no |  |
| `zipCode` | string | no |  |


### `ApiStaticCustomDataSheetsRequestVO` {#schema-apistaticcustomdatasheetsrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `first` | integer(int32) | no |  |
| `limit` | integer(int32) | no |  |
| `provider` | string | no |  |


### `ApiStaticDataSheetReducedVO` {#schema-apistaticdatasheetreducedvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | no |  |
| `name` | string | no |  |


### `ApiStaticDatasheetResponseVO` {#schema-apistaticdatasheetresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `dataSheet` | array[[`ApiStaticClosedTourDatasheetVO`](#schema-apistaticclosedtourdatasheetvo)] | no |  |
| `endTime` | string(time-local) | no |  |
| `images` | array[string] | no |  |
| `languageOptions` | array[[`Language`](#schema-language)] | no |  |
| `nights` | integer(int32) | no |  |
| `startTime` | string(time-local) | no |  |


### `ApiStaticDatasheetsResponseVO` {#schema-apistaticdatasheetsresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `dataSheets` | array[[`ApiStaticDataSheetReducedVO`](#schema-apistaticdatasheetreducedvo)] | no |  |
| `pagination` | [`PaginationVO`](#schema-paginationvo) | no |  |


### `ApiStaticUpdateCustomDataSheetsRequestVO` {#schema-apistaticupdatecustomdatasheetsrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `customDataSheets` | array[[`ApiStaticClosedTourDatasheetVO`](#schema-apistaticclosedtourdatasheetvo)] | no |  |


### `ApiTicketAdditionalRequiredPassengerDataVO` {#schema-apiticketadditionalrequiredpassengerdatavo}

| Field | Type | Required | Description |
|---|---|---|---|
| `answer` | string | no |  |
| `questionCode` | string | no |  |


### `ApiTicketBookRequestVO` {#schema-apiticketbookrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `externalReference` | string | no |  |
| `fakeBooking` | [`ApiBookFakeBookingVO`](#schema-apibookfakebookingvo) | no |  |
| `ticket` | [`ApiTicketBookTicketRequestVO`](#schema-apiticketbookticketrequestvo) | yes |  |


### `ApiTicketBookResponseVO` {#schema-apiticketbookresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `bookingReference` | string | no |  |
| `externalReference` | string | no |  |
| `persons` | array[[`PersonVO`](#schema-personvo)] | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `ticket` | [`ApiTicketVO`](#schema-apiticketvo) | no |  |


### `ApiTicketBookTicketRequestVO` {#schema-apiticketbookticketrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `ticketKey` | string | yes |  |


### `ApiTicketCancelResponseVO` {#schema-apiticketcancelresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `bookingReference` | string | no |  |
| `externalReference` | string | no |  |
| `persons` | array[[`PersonVO`](#schema-personvo)] | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `ticket` | [`ApiTicketCancelVO`](#schema-apiticketcancelvo) | no |  |


### `ApiTicketCancelVO` {#schema-apiticketcancelvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `additionalRequiredData` | array[[`ApiAdditionalRequiredPassengerDataVO`](#schema-apiadditionalrequiredpassengerdatavo)] | no |  |
| `bookingReference` | string | no |  |
| `cancellationFee` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `errorMessage` | string | no |  |
| `modality` | [`ApiTicketModalityVO`](#schema-apiticketmodalityvo) | no |  |
| `name` | string | no |  |
| `price` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `providerVoucherUrl` | string | no |  |
| `remarks` | array[string] | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `ticketKey` | string | no |  |


### `ApiTicketConfirmRequestVO` {#schema-apiticketconfirmrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `ticket` | [`ApiTicketConfirmTicketRequestVO`](#schema-apiticketconfirmticketrequestvo) | yes |  |


### `ApiTicketConfirmRequiredPassengerDataVO` {#schema-apiticketconfirmrequiredpassengerdatavo}

| Field | Type | Required | Description |
|---|---|---|---|
| `contactPerson` | array[[`RequiredPassengerData`](#schema-requiredpassengerdata)] | no |  |
| `otherPersons` | array[[`RequiredPassengerData`](#schema-requiredpassengerdata)] | no |  |
| `roomHolders` | array[[`RequiredPassengerData`](#schema-requiredpassengerdata)] | no |  |


### `ApiTicketConfirmResponseVO` {#schema-apiticketconfirmresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `requiredPassengerData` | [`ApiTicketConfirmRequiredPassengerDataVO`](#schema-apiticketconfirmrequiredpassengerdatavo) | no |  |
| `ticket` | [`ApiTicketVO`](#schema-apiticketvo) | no |  |
| `warnings` | array[[`WarningVO`](#schema-warningvo)] | no |  |


### `ApiTicketConfirmTicketModalityPriceRequestVO` {#schema-apiticketconfirmticketmodalitypricerequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `quantity` | integer(int32) | yes |  |
| `rateKey` | string | yes |  |


### `ApiTicketConfirmTicketRequestVO` {#schema-apiticketconfirmticketrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `rates` | array[[`ApiTicketConfirmTicketModalityPriceRequestVO`](#schema-apiticketconfirmticketmodalitypricerequestvo)] | yes |  |


### `ApiTicketModalityPriceVO` {#schema-apiticketmodalitypricevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | no |  |
| `quantity` | integer(int32) | no |  |


### `ApiTicketModalityVO` {#schema-apiticketmodalityvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `duration` | number(double) | no |  |
| `durationType` | string | no |  |
| `name` | string | no |  |
| `onRequest` | boolean | no |  |
| `rates` | array[[`ApiTicketModalityPriceVO`](#schema-apiticketmodalitypricevo)] | no |  |


### `ApiTicketPrebookRequestVO` {#schema-apiticketprebookrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `persons` | array[[`PersonVO`](#schema-personvo)] | yes |  |
| `ticket` | [`ApiTicketPrebookTicketRequestVO`](#schema-apiticketprebookticketrequestvo) | yes |  |


### `ApiTicketPrebookResponseVO` {#schema-apiticketprebookresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `persons` | array[[`PersonVO`](#schema-personvo)] | no |  |
| `ticket` | [`ApiTicketVO`](#schema-apiticketvo) | no |  |
| `warnings` | array[[`WarningVO`](#schema-warningvo)] | no |  |


### `ApiTicketPrebookTicketRequestVO` {#schema-apiticketprebookticketrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `additionalRequiredData` | array[[`ApiTicketAdditionalRequiredPassengerDataVO`](#schema-apiticketadditionalrequiredpassengerdatavo)] | no |  |
| `ticketKey` | string | yes |  |


### `ApiTicketQuoteFilterRequestVO` {#schema-apiticketquotefilterrequestvo}

_(no properties / composition-only schema)_


### `ApiTicketQuoteRequestVO` {#schema-apiticketquoterequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `checkIn` | string(date) | yes |  |
| `checkOut` | string(date) | yes |  |
| `destinationId` | string | no |  |
| `filter` | [`ApiTicketQuoteFilterRequestVO`](#schema-apiticketquotefilterrequestvo) | no |  |
| `language` | [`Language`](#schema-language) | no |  |
| `persons` | array[[`ApiBookPersonAgeRequestVO`](#schema-apibookpersonagerequestvo)] | yes |  |
| `sourceMarket` | string | no |  |
| `timeout` | integer(int32) | no |  |


### `ApiTicketQuoteResponseVO` {#schema-apiticketquoteresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `tickets` | array[[`ApiTicketQuoteTicketsVO`](#schema-apiticketquoteticketsvo)] | no |  |
| `total` | integer(int32) | no |  |


### `ApiTicketQuoteSingleTicketModalityOperationDayVO` {#schema-apiticketquotesingleticketmodalityoperationdayvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `eventDate` | string | no |  |
| `eventLanguage` | string | no |  |
| `eventTime` | string | no |  |
| `rates` | array[[`ApiTicketQuoteSingleTicketModalityPricesVO`](#schema-apiticketquotesingleticketmodalitypricesvo)] | no |  |


### `ApiTicketQuoteSingleTicketModalityPricesVO` {#schema-apiticketquotesingleticketmodalitypricesvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `canBookAlone` | boolean | no |  |
| `maxAge` | integer(int32) | no |  |
| `maxQuantity` | integer(int32) | no |  |
| `minAge` | integer(int32) | no |  |
| `minQuantity` | integer(int32) | no |  |
| `name` | string | no |  |
| `perGroupPrice` | boolean | no |  |
| `price` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `rateKey` | string | no |  |


### `ApiTicketQuoteSingleTicketModalityResponseVO` {#schema-apiticketquotesingleticketmodalityresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | no |  |
| `maximumPaxesToBook` | integer(int32) | no |  |
| `minimumPaxesToBook` | integer(int32) | no |  |
| `name` | string | no |  |
| `operationDays` | array[[`ApiTicketQuoteSingleTicketModalityOperationDayVO`](#schema-apiticketquotesingleticketmodalityoperationdayvo)] | no |  |


### `ApiTicketQuoteSingleTicketRequestVO` {#schema-apiticketquotesingleticketrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `checkIn` | string(date) | yes |  |
| `checkOut` | string(date) | yes |  |
| `language` | [`Language`](#schema-language) | no |  |
| `persons` | array[[`ApiBookPersonAgeRequestVO`](#schema-apibookpersonagerequestvo)] | yes |  |
| `sourceMarket` | string | no |  |
| `timeout` | integer(int32) | no |  |


### `ApiTicketQuoteSingleTicketResponseVO` {#schema-apiticketquotesingleticketresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `modalities` | array[[`ApiTicketQuoteSingleTicketModalityResponseVO`](#schema-apiticketquotesingleticketmodalityresponsevo)] | no |  |
| `provider` | string | no |  |


### `ApiTicketQuoteTicketsVO` {#schema-apiticketquoteticketsvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `fromPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `name` | string | no |  |
| `provider` | string | no |  |
| `ticketId` | string | no |  |


### `ApiTicketVO` {#schema-apiticketvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `additionalRequiredData` | array[[`ApiAdditionalRequiredPassengerDataVO`](#schema-apiadditionalrequiredpassengerdatavo)] | no |  |
| `bookingReference` | string | no |  |
| `cancellationPolicies` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `errorMessage` | string | no |  |
| `modality` | [`ApiTicketModalityVO`](#schema-apiticketmodalityvo) | no |  |
| `name` | string | no |  |
| `price` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `providerVoucherUrl` | string | no |  |
| `remarks` | array[string] | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `ticketKey` | string | no |  |


### `ApiTransferBookRequestVO` {#schema-apitransferbookrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `externalReference` | string | no |  |
| `fakeBooking` | [`ApiBookFakeBookingVO`](#schema-apibookfakebookingvo) | no |  |
| `transferKey` | string | no |  |


### `ApiTransferBookResponseVO` {#schema-apitransferbookresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `bookingReference` | string | no |  |
| `errorMessage` | string | no |  |
| `externalReference` | string | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `transfer` | [`ApiBookedTransferVO`](#schema-apibookedtransfervo) | no |  |


### `ApiTransferConfirmRequestVO` {#schema-apitransferconfirmrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `transferKey` | string | no |  |


### `ApiTransferConfirmResponseVO` {#schema-apitransferconfirmresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `requiredField` | [`ApiBookRequiredFieldResponseVO`](#schema-apibookrequiredfieldresponsevo) | no |  |
| `transfer` | [`ApiTransferServiceVO`](#schema-apitransferservicevo) | no |  |
| `warnings` | array[[`WarningVO`](#schema-warningvo)] | no |  |


### `ApiTransferGeolocalizableVO` {#schema-apitransfergeolocalizablevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodationId` | string | no |  |
| `transportBaseID` | string | no |  |


### `ApiTransferPrebookRequestVO` {#schema-apitransferprebookrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `arrivalTransportNumber` | string | no |  |
| `departureTransportNumber` | string | no |  |
| `persons` | array[[`PersonVO`](#schema-personvo)] | no |  |
| `transferKey` | string | no |  |


### `ApiTransferPrebookResponseVO` {#schema-apitransferprebookresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `persons` | array[[`PersonVO`](#schema-personvo)] | no |  |
| `transfer` | [`ApiTransferServiceVO`](#schema-apitransferservicevo) | no |  |
| `warnings` | array[[`WarningVO`](#schema-warningvo)] | no |  |


### `ApiTransferQuoteRequestVO` {#schema-apitransferquoterequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `arrivalTransportDateTime` | string(date-time) | no |  |
| `departureTransportDateTime` | string(date-time) | no |  |
| `from` | [`ApiTransferGeolocalizableVO`](#schema-apitransfergeolocalizablevo) | no |  |
| `language` | [`Language`](#schema-language) | no |  |
| `persons` | array[[`ApiBookPersonAgeRequestVO`](#schema-apibookpersonagerequestvo)] | yes |  |
| `pickupDateTime` | string(date-time) | no |  |
| `to` | [`ApiTransferGeolocalizableVO`](#schema-apitransfergeolocalizablevo) | no |  |


### `ApiTransferQuoteResponseVO` {#schema-apitransferquoteresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `arrivalTransportDateTime` | string(date-time) | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `departureTransportDateTime` | string(date-time) | no |  |
| `from` | [`TransferLocationVO`](#schema-transferlocationvo) | no |  |
| `persons` | integer(int32) | no |  |
| `pickupDateTime` | string(date-time) | no |  |
| `providerTraces` | array[[`ProviderTraceVO`](#schema-providertracevo)] | no |  |
| `to` | [`TransferLocationVO`](#schema-transferlocationvo) | no |  |
| `total` | integer(int32) | no |  |
| `transfers` | array[[`ApiTransferServiceVO`](#schema-apitransferservicevo)] | no |  |


### `ApiTransferServiceVO` {#schema-apitransferservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `additionalCharacteristics` | array[string] | no |  |
| `arrivalTransportNumber` | string | no |  |
| `cancellationPolicies` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `characteristics` | array[string] | no |  |
| `confirmedPickupTime` | boolean | no |  |
| `departureDate` | string(date-time) | no |  |
| `departureTransportNumber` | string | no |  |
| `driverTracking` | string | no |  |
| `duration` | string(duration) | no |  |
| `image` | string | no |  |
| `maximumWaitingMinutes` | integer(int32) | no |  |
| `onRequest` | boolean | no |  |
| `pickupInformation` | string | no |  |
| `price` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `productType` | [`TransferProductType`](#schema-transferproducttype) | no |  |
| `provider` | string | no |  |
| `providerPurchaseToken` | string | no |  |
| `remarks` | string | no |  |
| `serviceType` | [`TransferServiceType`](#schema-transferservicetype) | no |  |
| `telephoneAssistance` | string | no |  |
| `transferKey` | string | no |  |
| `transferType` | [`TransferVehicleType`](#schema-transfervehicletype) | no |  |
| `units` | integer(int32) | no |  |
| `vehicleDescriptionText` | string | no |  |


### `ApiTransportBookRequestVO` {#schema-apitransportbookrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `externalReference` | string | no |  |
| `fakeBooking` | [`ApiBookFakeBookingVO`](#schema-apibookfakebookingvo) | no |  |
| `transports` | [`ApiTransportBookTransportRequestVO`](#schema-apitransportbooktransportrequestvo) | yes |  |


### `ApiTransportBookResponseVO` {#schema-apitransportbookresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `bookingReference` | string | no |  |
| `externalReference` | string | no |  |
| `persons` | array[[`PersonVO`](#schema-personvo)] | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `transports` | [`ApiTransportBookTransportServiceResponseVO`](#schema-apitransportbooktransportserviceresponsevo) | no |  |


### `ApiTransportBookTransportRequestVO` {#schema-apitransportbooktransportrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `recommendationKey` | string | yes |  |


### `ApiTransportBookTransportServiceResponseVO` {#schema-apitransportbooktransportserviceresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `nameChangedRemarks` | array[string] | no |  |
| `priceBreakdown` | [`ApiTransportPriceBreakdownVO`](#schema-apitransportpricebreakdownvo) | no |  |
| `services` | array[[`ApiTransportServiceVO`](#schema-apitransportservicevo)] | no |  |


### `ApiTransportConfirmRequestVO` {#schema-apitransportconfirmrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `transports` | [`ApiTransportConfirmTransportRequestVO`](#schema-apitransportconfirmtransportrequestvo) | yes |  |


### `ApiTransportConfirmResponseVO` {#schema-apitransportconfirmresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `extraBaggageOptions` | array[[`ApiTransportExtraBaggageOptionVO`](#schema-apitransportextrabaggageoptionvo)] | no |  |
| `maxPersonNameLength` | integer(int32) | no |  |
| `priceBreakdown` | [`ApiTransportPriceBreakdownVO`](#schema-apitransportpricebreakdownvo) | no |  |
| `recommendationKey` | string | no |  |
| `requiredField` | [`ApiBookRequiredFieldResponseVO`](#schema-apibookrequiredfieldresponsevo) | no |  |
| `services` | array[[`ApiTransportServiceVO`](#schema-apitransportservicevo)] | no |  |


### `ApiTransportConfirmTransportRequestVO` {#schema-apitransportconfirmtransportrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `inboundRecommendationKey` | string | no |  |
| `outboundRecommendationKey` | string | no |  |
| `recommendationKey` | string | no |  |


### `ApiTransportConfirmTransportServiceResponseVO` {#schema-apitransportconfirmtransportserviceresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `extraBaggageOptions` | array[[`ApiTransportExtraBaggageOptionVO`](#schema-apitransportextrabaggageoptionvo)] | no |  |
| `maxPersonNameLength` | integer(int32) | no |  |
| `priceBreakdown` | [`ApiTransportPriceBreakdownVO`](#schema-apitransportpricebreakdownvo) | no |  |
| `recommendationKey` | string | no |  |
| `services` | array[[`ApiTransportServiceVO`](#schema-apitransportservicevo)] | no |  |


### `ApiTransportEticketVO` {#schema-apitransporteticketvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `personIndex` | integer(int32) | no |  |
| `ticketNumber` | string | no |  |
| `type` | [`EticketType`](#schema-etickettype) | no |  |


### `ApiTransportExtraBaggageOptionVO` {#schema-apitransportextrabaggageoptionvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `journeyReferences` | array[string] | no |  |
| `optionKey` | string | no |  |
| `personIndex` | integer(int32) | no |  |
| `price` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `quantity` | integer(int32) | no |  |
| `roundTripPrice` | boolean | no |  |
| `weight` | string | no |  |


### `ApiTransportFareFamilySegmentVO` {#schema-apitransportfarefamilysegmentvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `cabinType` | [`CabinClassType`](#schema-cabinclasstype) | no |  |
| `fareCode` | string | no |  |
| `ref` | string | no |  |


### `ApiTransportFareFamilyVO` {#schema-apitransportfarefamilyvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amenities` | array[string] | no |  |
| `description` | string | no |  |
| `fareBasis` | string | no |  |
| `name` | string | no |  |
| `passengerFareBreakdowns` | array[[`TransportFareBreakdownVO`](#schema-transportfarebreakdownvo)] | no |  |
| `priceBreakdown` | [`ApiTransportPriceBreakdownVO`](#schema-apitransportpricebreakdownvo) | no |  |
| `segments` | array[[`ApiTransportFareFamilySegmentVO`](#schema-apitransportfarefamilysegmentvo)] | no |  |


### `ApiTransportJourneyRequestVO` {#schema-apitransportjourneyrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `arrival` | string | yes |  |
| `arrivalType` | [`ApiTransportQuoteRequestLocationType`](#schema-apitransportquoterequestlocationtype) | yes |  |
| `departure` | string | yes |  |
| `departureDate` | string(date) | yes |  |
| `departureType` | [`ApiTransportQuoteRequestLocationType`](#schema-apitransportquoterequestlocationtype) | yes |  |


### `ApiTransportJourneyVO` {#schema-apitransportjourneyvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `airlineName` | string | no |  |
| `arrival` | string | no |  |
| `arrivalTime` | string(date-time) | no |  |
| `departure` | string | no |  |
| `departureTime` | string(date-time) | no |  |
| `duration` | integer(int64) | no |  |
| `etickets` | array[[`ApiTransportEticketVO`](#schema-apitransporteticketvo)] | no |  |
| `fare` | string | no |  |
| `fareBasis` | string | no |  |
| `fareFamily` | string | no |  |
| `fareRules` | array[[`ApiTransportServiceFareRuleVO`](#schema-apitransportservicefarerulevo)] | no |  |
| `lowcost` | boolean | no |  |
| `ref` | string | no |  |
| `segments` | array[[`ApiTransportSegmentVO`](#schema-apitransportsegmentvo)] | no |  |
| `selectedBaggage` | array[[`ApiTransportExtraBaggageOptionVO`](#schema-apitransportextrabaggageoptionvo)] | no |  |


### `ApiTransportPrebookExtraBaggageOptionRequestVO` {#schema-apitransportprebookextrabaggageoptionrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `optionKey` | string | yes |  |


### `ApiTransportPrebookRequestVO` {#schema-apitransportprebookrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `persons` | array[[`PersonVO`](#schema-personvo)] | no |  |
| `transports` | [`ApiTransportPrebookTransportRequestVO`](#schema-apitransportprebooktransportrequestvo) | yes |  |


### `ApiTransportPrebookResponseVO` {#schema-apitransportprebookresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `nameChangedRemarks` | array[string] | no |  |
| `persons` | array[[`PersonVO`](#schema-personvo)] | no |  |
| `priceBreakdown` | [`ApiTransportPriceBreakdownVO`](#schema-apitransportpricebreakdownvo) | no |  |
| `recommendationKey` | string | no |  |
| `services` | array[[`ApiTransportServiceVO`](#schema-apitransportservicevo)] | no |  |


### `ApiTransportPrebookTransportRequestVO` {#schema-apitransportprebooktransportrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `extraBaggage` | array[[`ApiTransportPrebookExtraBaggageOptionRequestVO`](#schema-apitransportprebookextrabaggageoptionrequestvo)] | no |  |
| `recommendationKey` | string | yes |  |


### `ApiTransportPrebookTransportServiceResponseVO` {#schema-apitransportprebooktransportserviceresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `nameChangedRemarks` | array[string] | no |  |
| `priceBreakdown` | [`ApiTransportPriceBreakdownVO`](#schema-apitransportpricebreakdownvo) | no |  |
| `recommendationKey` | string | no |  |
| `services` | array[[`ApiTransportServiceVO`](#schema-apitransportservicevo)] | no |  |


### `ApiTransportPriceBreakdownVO` {#schema-apitransportpricebreakdownvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `originalTaxes` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `taxes` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `totalPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |


### `ApiTransportQuoteFareFamilyRecommendationVO` {#schema-apitransportquotefarefamilyrecommendationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `fareFamily` | [`ApiTransportFareFamilyVO`](#schema-apitransportfarefamilyvo) | no |  |
| `recommendationKey` | string | no |  |
| `segmentsBaggageAllowance` | array[[`ApiTransportQuoteRecommendationSegmentVO`](#schema-apitransportquoterecommendationsegmentvo)] | no |  |


### `ApiTransportQuoteFareFamilyRequestVO` {#schema-apitransportquotefarefamilyrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `recommendationKey` | string | yes |  |


### `ApiTransportQuoteFareFamilyResponseVO` {#schema-apitransportquotefarefamilyresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `fareFamilies` | array[[`ApiTransportQuoteFareFamilyRecommendationVO`](#schema-apitransportquotefarefamilyrecommendationvo)] | no |  |


### `ApiTransportQuoteFilterRequestVO` {#schema-apitransportquotefilterrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `includeFareFamilies` | boolean | no |  |


### `ApiTransportQuoteRecommendationSegmentVO` {#schema-apitransportquoterecommendationsegmentvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `baggageAllowance` | string | no |  |
| `segmentRef` | string | no |  |


### `ApiTransportQuoteRecommendationVO` {#schema-apitransportquoterecommendationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `alternativeFareFamilies` | array[[`ApiTransportQuoteFareFamilyRecommendationVO`](#schema-apitransportquotefarefamilyrecommendationvo)] | no |  |
| `amenities` | array[string] | no |  |
| `fare` | string | no |  |
| `fareBasis` | string | no |  |
| `fareFamilyName` | string | no |  |
| `fareType` | [`FareType`](#schema-faretype) | no |  |
| `hasFareFamilyUpSell` | boolean | no |  |
| `inboundRef` | string | no |  |
| `lastTicketingDate` | string(date) | no |  |
| `lowcost` | boolean | no |  |
| `outboundRef` | string | no |  |
| `passengerFareBreakdowns` | array[[`TransportFareBreakdownVO`](#schema-transportfarebreakdownvo)] | no |  |
| `priceBreakdown` | [`ApiTransportPriceBreakdownVO`](#schema-apitransportpricebreakdownvo) | no |  |
| `provider` | string | no |  |
| `recommendationKey` | string | no |  |
| `ref` | string | no |  |
| `segmentsBaggageAllowance` | array[[`ApiTransportQuoteRecommendationSegmentVO`](#schema-apitransportquoterecommendationsegmentvo)] | no |  |


### `ApiTransportQuoteRequestLocationType` {#schema-apitransportquoterequestlocationtype}

Enum values: 'DESTINATION', 'TRANSPORT_BASE'


### `ApiTransportQuoteRequestVO` {#schema-apitransportquoterequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `filter` | [`ApiTransportQuoteFilterRequestVO`](#schema-apitransportquotefilterrequestvo) | yes |  |
| `journeys` | array[[`ApiTransportJourneyRequestVO`](#schema-apitransportjourneyrequestvo)] | yes |  |
| `language` | [`Language`](#schema-language) | no |  |
| `persons` | array[[`ApiBookPersonAgeRequestVO`](#schema-apibookpersonagerequestvo)] | yes |  |
| `sourceMarket` | string | no |  |
| `tripType` | [`TripType`](#schema-triptype) | yes |  |


### `ApiTransportQuoteResponseVO` {#schema-apitransportquoteresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `providerTraces` | array[[`ProviderTraceVO`](#schema-providertracevo)] | no |  |
| `recommendations` | array[[`ApiTransportQuoteRecommendationVO`](#schema-apitransportquoterecommendationvo)] | no |  |
| `services` | array[[`ApiTransportQuoteServiceVO`](#schema-apitransportquoteservicevo)] | no |  |


### `ApiTransportQuoteServiceVO` {#schema-apitransportquoteservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `arrival` | string | no |  |
| `arrivalDateTime` | string(date-time) | no |  |
| `cancellationPolicies` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `departure` | string | no |  |
| `departureDateTime` | string(date-time) | no |  |
| `duration` | integer(int64) | no |  |
| `fareBasis` | string | no |  |
| `ref` | string | no |  |
| `segments` | array[[`ApiTransportSegmentVO`](#schema-apitransportsegmentvo)] | no |  |


### `ApiTransportSegmentVO` {#schema-apitransportsegmentvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `arrival` | string | no |  |
| `arrivalDateTime` | string(date-time) | no |  |
| `cabinType` | [`CabinClassType`](#schema-cabinclasstype) | no |  |
| `departure` | string | no |  |
| `departureDateTime` | string(date-time) | no |  |
| `duration` | string(duration) | no |  |
| `fareCode` | string | no |  |
| `fareType` | [`FareType`](#schema-faretype) | no |  |
| `includedBaggage` | string | no |  |
| `marketingCompany` | string | no |  |
| `operatingCompany` | string | no |  |
| `ref` | string | no |  |
| `technicalStopsVO` | array[[`TransportTechnicalStopVO`](#schema-transporttechnicalstopvo)] | no |  |
| `transportNumber` | string | no |  |
| `transportType` | [`TransportType`](#schema-transporttype) | no |  |


### `ApiTransportServiceFareRuleVO` {#schema-apitransportservicefarerulevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `category` | string | no |  |
| `description` | string | no |  |


### `ApiTransportServiceVO` {#schema-apitransportservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `bookingReference` | string | no |  |
| `cancellationPolicies` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `errorMessage` | string | no |  |
| `journeys` | array[[`ApiTransportJourneyVO`](#schema-apitransportjourneyvo)] | no |  |
| `passengerFareBreakdowns` | array[[`TransportFareBreakdownVO`](#schema-transportfarebreakdownvo)] | no |  |
| `provider` | string | no |  |
| `providerBookingReference` | string | no |  |
| `selectedBaggage` | array[[`ApiTransportExtraBaggageOptionVO`](#schema-apitransportextrabaggageoptionvo)] | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |


### `AuditResponseVO` {#schema-auditresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `authToken` | string | no |  |
| `availabilityId` | integer(int32) | no |  |
| `processTime` | integer(int64) | no |  |
| `server` | string | no |  |
| `timestamp` | string(date-time) | no |  |
| `traceId` | string | no |  |


### `Authentication` {#schema-authentication}

| Field | Type | Required | Description |
|---|---|---|---|
| `micrositeId` | string | yes |  |
| `password` | string | yes |  |
| `username` | string | yes |  |


### `AuthenticationOTP` {#schema-authenticationotp}

| Field | Type | Required | Description |
|---|---|---|---|
| `micrositeId` | string | yes |  |
| `userName` | string | yes |  |


### `BaggageAllowanceType` {#schema-baggageallowancetype}

Enum values: 'KG', 'PC'


### `BaggageVO` {#schema-baggagevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `perPerson` | boolean | no |  |
| `personId` | string | no |  |
| `providerNetPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `quantity` | integer(int32) | no |  |
| `return` | boolean | no |  |
| `totalPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `weight` | string | no |  |


### `Bed` {#schema-bed}

Enum values: 'SINGLE_BED', 'DOUBLE_BED', 'EXTRA_LARGE_DOUBLE', 'BUNK_BED', 'SOFA_BED', 'LARGE_BED', 'FUTON_MAT'


### `BedOption` {#schema-bedoption}

_Bedding options per room_

| Field | Type | Required | Description |
|---|---|---|---|
| `bedType` | [`Bed`](#schema-bed) | no |  |
| `numberOfBeds` | integer(int32) | no |  |


### `BlogCategoryVO` {#schema-blogcategoryvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | no |  |
| `id` | integer(int32) | no |  |
| `name` | string | no |  |
| `slug` | string | no |  |


### `BlogIdeaVO` {#schema-blogideavo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `destinations` | integer(int32) | no |  |
| `hidePriceBeforeQuote` | boolean | no |  |
| `id` | integer(int32) | no |  |
| `imageUrl` | string | no |  |
| `insuranceCount` | integer(int32) | no |  |
| `pricePerPerson` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `purchasedNightsCount` | integer(int32) | no |  |
| `purchasedTicketsCount` | integer(int32) | no |  |
| `purchasedTransfersCount` | integer(int32) | no |  |
| `title` | string | no |  |
| `transports` | integer(int32) | no |  |
| `url` | string | no |  |


### `BlogImageVO` {#schema-blogimagevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `alt` | string | no |  |
| `desktopImage` | string | no |  |
| `id` | integer(int32) | no |  |
| `mobileImage` | string | no |  |
| `order` | integer(int32) | no |  |


### `BlogLabelVO` {#schema-bloglabelvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | no |  |
| `id` | integer(int32) | no |  |
| `name` | string | no |  |
| `slug` | string | no |  |


### `BlogPostSummaryVO` {#schema-blogpostsummaryvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `category` | [`BlogCategoryVO`](#schema-blogcategoryvo) | no |  |
| `id` | integer(int32) | no |  |
| `image` | [`BlogImageVO`](#schema-blogimagevo) | no |  |
| `metaDescription` | string | no |  |
| `publicationDate` | string | no |  |
| `slug` | string | no |  |
| `subtitle` | string | no |  |
| `title` | string | no |  |
| `url` | string | no |  |


### `BlogPostVO` {#schema-blogpostvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `authorName` | string | no |  |
| `category` | [`BlogCategoryVO`](#schema-blogcategoryvo) | no |  |
| `content` | string | no |  |
| `id` | integer(int32) | no |  |
| `image` | [`BlogImageVO`](#schema-blogimagevo) | no |  |
| `images` | array[[`BlogImageVO`](#schema-blogimagevo)] | no |  |
| `labels` | array[[`BlogLabelVO`](#schema-bloglabelvo)] | no |  |
| `lastUpdated` | string | no |  |
| `metaDescription` | string | no |  |
| `packages` | array[[`BlogIdeaVO`](#schema-blogideavo)] | no |  |
| `publicationDate` | string | no |  |
| `showAuthor` | boolean | no |  |
| `slug` | string | no |  |
| `subtitle` | string | no |  |
| `title` | string | no |  |
| `url` | string | no |  |


### `BlogVO` {#schema-blogvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `images` | array[[`BlogImageVO`](#schema-blogimagevo)] | no |  |
| `subtitle` | string | no |  |
| `title` | string | no |  |


### `BoniversumPayerResponseVO` {#schema-boniversumpayerresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `address` | [`AddressVO`](#schema-addressvo) | no |  |
| `birthDate` | string(date) | no |  |
| `courtesy` | [`CourtesyTitle`](#schema-courtesytitle) | no |  |
| `lastName` | string | no |  |
| `name` | string | no |  |


### `BookedCarRentalManualServiceVO` {#schema-bookedcarrentalmanualservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amendments` | array[[`BookedServiceAmendmentVO`](#schema-bookedserviceamendmentvo)] | no |  |
| `arrivalHour` | string(time-local) | no |  |
| `bookingReference` | string | no |  |
| `cancelPolicy` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `cancelationDate` | string(date-time) | no |  |
| `cancellationType` | [`CancellationType`](#schema-cancellationtype) | no |  |
| `carPdf` | [`PdfVO`](#schema-pdfvo) | no |  |
| `carRentalName` | string | no |  |
| `confirmationErrorCause` | string | no |  |
| `consolidated` | boolean | no |  |
| `country` | string | no |  |
| `createdByAgency` | string | no |  |
| `departureHour` | string(time-local) | no |  |
| `description` | string | no |  |
| `destination` | string | no |  |
| `endDate` | string(date-time) | no |  |
| `externalCode` | string | no |  |
| `id` | string | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `membershipSavedPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `operatorProvider` | string | no |  |
| `origin` | string | no |  |
| `originalpricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `paymentType` | string | no |  |
| `pdfUrl` | string | no |  |
| `priceType` | [`PriceType`](#schema-pricetype) | no |  |
| `pricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `promotionCode` | boolean | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |
| `providerCancellationDate` | string(date-time) | no |  |
| `providerConfigurationId` | integer(int32) | no |  |
| `providerDescription` | string | no |  |
| `providerName` | string | no |  |
| `reav` | boolean | no |  |
| `startDate` | string(date-time) | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `type` | [`ManualServiceType`](#schema-manualservicetype) | no |  |
| `voucherDescription` | string | no |  |


### `BookedCarServiceVO` {#schema-bookedcarservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `acriss` | string | no |  |
| `amendments` | array[[`BookedServiceAmendmentVO`](#schema-bookedserviceamendmentvo)] | no |  |
| `bookingReference` | string | no |  |
| `cancelPolicy` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `cancelationDate` | string(date-time) | no |  |
| `cancellationType` | [`CancellationType`](#schema-cancellationtype) | no |  |
| `collisionDamageWaiver` | string | no |  |
| `confirmationErrorCause` | string | no |  |
| `consolidated` | boolean | no |  |
| `country` | string | no |  |
| `depositDescription` | string | no |  |
| `dropOffInfo` | [`CarRentalLocationVO`](#schema-carrentallocationvo) | no |  |
| `dropoff` | string | no |  |
| `dropoffDestinationCode` | string | no |  |
| `endDate` | string(date-time) | no |  |
| `equipments` | array[[`CarRentalServiceItemVO`](#schema-carrentalserviceitemvo)] | no |  |
| `externalCode` | string | no |  |
| `fees` | array[[`CarRentalServiceItemVO`](#schema-carrentalserviceitemvo)] | no |  |
| `id` | string | no |  |
| `imageUrl` | string | no |  |
| `inclusion` | array[string] | no |  |
| `insurances` | array[[`CarRentalServiceInsuranceVO`](#schema-carrentalserviceinsurancevo)] | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `licenseRestriction` | [`DriverLicenseRestrictionVO`](#schema-driverlicenserestrictionvo) | no |  |
| `maximumAge` | integer(int32) | no |  |
| `membershipSavedPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `minAntiquity` | integer(int32) | no |  |
| `minimumAge` | integer(int32) | no |  |
| `operatorProvider` | string | no |  |
| `originalpricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `paymentRules` | string | no |  |
| `pickUpInfo` | [`CarRentalLocationVO`](#schema-carrentallocationvo) | no |  |
| `pickup` | string | no |  |
| `pickupDestinationCode` | string | no |  |
| `priceType` | [`PriceType`](#schema-pricetype) | no |  |
| `pricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |
| `providerBookingReference` | string | no |  |
| `providerCancellationDate` | string(date-time) | no |  |
| `providerConfigurationId` | integer(int32) | no |  |
| `providerDescription` | string | no |  |
| `purchaseCurrency` | string | no |  |
| `purchasePrice` | number(double) | no |  |
| `reav` | boolean | no |  |
| `remark` | string | no |  |
| `securityDepositAmount` | string | no |  |
| `sipp` | string | no |  |
| `startDate` | string(date-time) | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `supplierAccountingCode` | string | no |  |
| `supplierId` | integer(int32) | no |  |
| `supplierName` | string | no |  |
| `termsAndConditions` | string | no |  |
| `termsAndConditionsLink` | string | no |  |
| `theftProtection` | string | no |  |
| `thirdPartyCoverage` | string | no |  |
| `vehicleName` | string | no |  |
| `vendor` | string | no |  |


### `BookedClosedTourManualServiceVO` {#schema-bookedclosedtourmanualservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amendments` | array[[`BookedServiceAmendmentVO`](#schema-bookedserviceamendmentvo)] | no |  |
| `arrivalHour` | string(time-local) | no |  |
| `bookingReference` | string | no |  |
| `cancelPolicy` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `cancelationDate` | string(date-time) | no |  |
| `cancellationType` | [`CancellationType`](#schema-cancellationtype) | no |  |
| `confirmationErrorCause` | string | no |  |
| `consolidated` | boolean | no |  |
| `country` | string | no |  |
| `createdByAgency` | string | no |  |
| `departureHour` | string(time-local) | no |  |
| `description` | string | no |  |
| `destination` | string | no |  |
| `endDate` | string(date-time) | no |  |
| `externalCode` | string | no |  |
| `id` | string | no |  |
| `image` | string | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `membershipSavedPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `operatorProvider` | string | no |  |
| `originalpricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `paymentType` | string | no |  |
| `pdfUrl` | string | no |  |
| `priceType` | [`PriceType`](#schema-pricetype) | no |  |
| `pricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `promotionCode` | boolean | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |
| `providerCancellationDate` | string(date-time) | no |  |
| `providerConfigurationId` | integer(int32) | no |  |
| `providerDescription` | string | no |  |
| `providerName` | string | no |  |
| `reav` | boolean | no |  |
| `startDate` | string(date-time) | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `tourName` | string | no |  |
| `type` | [`ManualServiceType`](#schema-manualservicetype) | no |  |
| `voucherDescription` | string | no |  |


### `BookedClosedTourServiceVO` {#schema-bookedclosedtourservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `additionalServices` | array[[`ClosedTourAdditionalServiceVO`](#schema-closedtouradditionalservicevo)] | no |  |
| `amendments` | array[[`BookedServiceAmendmentVO`](#schema-bookedserviceamendmentvo)] | no |  |
| `bookingReference` | string | no |  |
| `cancelPolicy` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `cancelationDate` | string(date-time) | no |  |
| `cancellationType` | [`CancellationType`](#schema-cancellationtype) | no |  |
| `confirmationErrorCause` | string | no |  |
| `consolidated` | boolean | no |  |
| `country` | string | no |  |
| `destinationCodes` | array[string] | no |  |
| `endDate` | string(date-time) | no |  |
| `externalCode` | string | no |  |
| `externalSupplierId` | string | no |  |
| `id` | string | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `membershipSavedPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `modality` | string | no |  |
| `modalityCode` | string | no |  |
| `name` | string | no |  |
| `operatorProvider` | string | no |  |
| `originalpricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `passengers` | integer(int32) | no |  |
| `priceType` | [`PriceType`](#schema-pricetype) | no |  |
| `pricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |
| `providerCancellationDate` | string(date-time) | no |  |
| `providerCode` | string | no |  |
| `providerConfigurationId` | integer(int32) | no |  |
| `providerDescription` | string | no |  |
| `reav` | boolean | no |  |
| `remarks` | array[string] | no |  |
| `startDate` | string(date-time) | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `supplierId` | integer(int32) | no |  |
| `supplierName` | string | no |  |
| `transportServices` | array[[`BookedClosedTourTransportServiceVO`](#schema-bookedclosedtourtransportservicevo)] | no |  |


### `BookedClosedTourTransportServiceFlightDetailVO` {#schema-bookedclosedtourtransportserviceflightdetailvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `airlineName` | string | no |  |
| `arrivalAirport` | string | no |  |
| `arrivalTime` | string | no |  |
| `cabintype` | string | no |  |
| `departureAirport` | string | no |  |
| `departureTime` | string | no |  |
| `flightNumber` | string | no |  |


### `BookedClosedTourTransportServiceVO` {#schema-bookedclosedtourtransportservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `baggageIncluded` | boolean | no |  |
| `baggageInfo` | string | no |  |
| `directFlight` | boolean | no |  |
| `flightDetails` | array[[`BookedClosedTourTransportServiceFlightDetailVO`](#schema-bookedclosedtourtransportserviceflightdetailvo)] | no |  |
| `fromContract` | boolean | no |  |
| `id` | string | no |  |


### `BookedCruiseManualServiceVO` {#schema-bookedcruisemanualservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amendments` | array[[`BookedServiceAmendmentVO`](#schema-bookedserviceamendmentvo)] | no |  |
| `arrivalHour` | string(time-local) | no |  |
| `bookingReference` | string | no |  |
| `cancelPolicy` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `cancelationDate` | string(date-time) | no |  |
| `cancellationType` | [`CancellationType`](#schema-cancellationtype) | no |  |
| `confirmationErrorCause` | string | no |  |
| `consolidated` | boolean | no |  |
| `country` | string | no |  |
| `createdByAgency` | string | no |  |
| `cruiseName` | string | no |  |
| `departureHour` | string(time-local) | no |  |
| `description` | string | no |  |
| `destination` | string | no |  |
| `endDate` | string(date-time) | no |  |
| `externalCode` | string | no |  |
| `id` | string | no |  |
| `image` | string | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `membershipSavedPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `operatorProvider` | string | no |  |
| `originalpricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `paymentType` | string | no |  |
| `pdfUrl` | string | no |  |
| `priceType` | [`PriceType`](#schema-pricetype) | no |  |
| `pricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `promotionCode` | boolean | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |
| `providerCancellationDate` | string(date-time) | no |  |
| `providerConfigurationId` | integer(int32) | no |  |
| `providerDescription` | string | no |  |
| `providerName` | string | no |  |
| `reav` | boolean | no |  |
| `startDate` | string(date-time) | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `type` | [`ManualServiceType`](#schema-manualservicetype) | no |  |
| `voucherDescription` | string | no |  |


### `BookedCruiseServiceVO` {#schema-bookedcruiseservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amendments` | array[[`BookedServiceAmendmentVO`](#schema-bookedserviceamendmentvo)] | no |  |
| `bookingReference` | string | no |  |
| `cabinNumber` | string | no |  |
| `cancelPolicy` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `cancelationDate` | string(date-time) | no |  |
| `cancellationType` | [`CancellationType`](#schema-cancellationtype) | no |  |
| `categoryName` | string | no |  |
| `confirmationErrorCause` | string | no |  |
| `consolidated` | boolean | no |  |
| `country` | string | no |  |
| `cruiseId` | string | no |  |
| `destinationCodes` | array[string] | no |  |
| `endDate` | string(date-time) | no |  |
| `externalCode` | string | no |  |
| `id` | string | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `membershipSavedPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `name` | string | no |  |
| `nights` | integer(int32) | no |  |
| `operatorProvider` | string | no |  |
| `originalpricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `priceType` | [`PriceType`](#schema-pricetype) | no |  |
| `pricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |
| `providerCancellationDate` | string(date-time) | no |  |
| `providerCode` | string | no |  |
| `providerConfigurationId` | integer(int32) | no |  |
| `providerDescription` | string | no |  |
| `reav` | boolean | no |  |
| `remarks` | array[string] | no |  |
| `shipName` | string | no |  |
| `startDate` | string(date-time) | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `supplierId` | integer(int32) | no |  |
| `supplierName` | string | no |  |


### `BookedHotelManualServiceVO` {#schema-bookedhotelmanualservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amendments` | array[[`BookedServiceAmendmentVO`](#schema-bookedserviceamendmentvo)] | no |  |
| `bookingReference` | string | no |  |
| `cancelPolicy` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `cancelationDate` | string(date-time) | no |  |
| `cancellationType` | [`CancellationType`](#schema-cancellationtype) | no |  |
| `confirmationErrorCause` | string | no |  |
| `consolidated` | boolean | no |  |
| `country` | string | no |  |
| `createdByAgency` | string | no |  |
| `description` | string | no |  |
| `destination` | string | no |  |
| `endDate` | string(date-time) | no |  |
| `externalCode` | string | no |  |
| `hotelCategory` | integer(int32) | no |  |
| `hotelName` | string | no |  |
| `id` | string | no |  |
| `image` | string | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `mealPlan` | string | no |  |
| `membershipSavedPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `operatorProvider` | string | no |  |
| `originalpricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `paymentType` | string | no |  |
| `pdfUrl` | string | no |  |
| `priceType` | [`PriceType`](#schema-pricetype) | no |  |
| `pricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `promotionCode` | boolean | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |
| `providerCancellationDate` | string(date-time) | no |  |
| `providerConfigurationId` | integer(int32) | no |  |
| `providerDescription` | string | no |  |
| `providerName` | string | no |  |
| `reav` | boolean | no |  |
| `roomDescription` | string | no |  |
| `startDate` | string(date-time) | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `type` | [`ManualServiceType`](#schema-manualservicetype) | no |  |
| `voucherDescription` | string | no |  |


### `BookedHotelRoomVO` {#schema-bookedhotelroomvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | no |  |
| `pricebreakdown` | [`PriceBreakdownVO`](#schema-pricebreakdownvo) | no |  |
| `roomTypeDescription` | string | no |  |


### `BookedHotelServiceVO` {#schema-bookedhotelservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amendments` | array[[`BookedServiceAmendmentVO`](#schema-bookedserviceamendmentvo)] | no |  |
| `apiPlatform` | string | no |  |
| `bookingReference` | string | no |  |
| `cancelPolicy` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `cancelationDate` | string(date-time) | no |  |
| `cancellationType` | [`CancellationType`](#schema-cancellationtype) | no |  |
| `category` | [`HotelCategory`](#schema-hotelcategory) | no |  |
| `commentsToAccommodation` | string | no |  |
| `confirmationErrorCause` | string | no |  |
| `consolidated` | boolean | no |  |
| `country` | string | no |  |
| `destinationCode` | string | no |  |
| `destinationName` | string | no |  |
| `endDate` | string(date-time) | no |  |
| `externalCode` | string | no |  |
| `fiscalInformation` | string | no |  |
| `globalMappingId` | integer(int32) | no |  |
| `hotelData` | [`HotelDataVO`](#schema-hoteldatavo) | no |  |
| `hotelId` | string | no |  |
| `hotelName` | string | no |  |
| `id` | string | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `locationName` | string | no |  |
| `mealPlan` | string | no |  |
| `membershipSavedPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `nights` | integer(int32) | no |  |
| `operatorProvider` | string | no |  |
| `originalpricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `priceType` | [`PriceType`](#schema-pricetype) | no |  |
| `pricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |
| `providerBookingReference` | string | no |  |
| `providerCancellationDate` | string(date-time) | no |  |
| `providerConfigurationId` | integer(int32) | no |  |
| `providerDescription` | string | no |  |
| `providerHotelId` | string | no |  |
| `rate` | string | no |  |
| `reav` | boolean | no |  |
| `remarks` | array[string] | no |  |
| `repricing` | [`RepricingVO`](#schema-repricingvo) | no |  |
| `room` | array[[`BookedHotelRoomVO`](#schema-bookedhotelroomvo)] | no |  |
| `startDate` | string(date-time) | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `supplierId` | integer(int32) | no |  |
| `supplierName` | string | no |  |


### `BookedInsuranceManualServiceVO` {#schema-bookedinsurancemanualservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amendments` | array[[`BookedServiceAmendmentVO`](#schema-bookedserviceamendmentvo)] | no |  |
| `bookingReference` | string | no |  |
| `cancelPolicy` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `cancelationDate` | string(date-time) | no |  |
| `cancellationType` | [`CancellationType`](#schema-cancellationtype) | no |  |
| `confirmationErrorCause` | string | no |  |
| `consolidated` | boolean | no |  |
| `country` | string | no |  |
| `createdByAgency` | string | no |  |
| `description` | string | no |  |
| `endDate` | string(date-time) | no |  |
| `externalCode` | string | no |  |
| `id` | string | no |  |
| `insuranceName` | string | no |  |
| `insurancePdf` | [`PdfVO`](#schema-pdfvo) | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `membershipSavedPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `operatorProvider` | string | no |  |
| `originalpricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `paymentType` | string | no |  |
| `pdfUrl` | string | no |  |
| `priceType` | [`PriceType`](#schema-pricetype) | no |  |
| `pricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `promotionCode` | boolean | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |
| `providerCancellationDate` | string(date-time) | no |  |
| `providerConfigurationId` | integer(int32) | no |  |
| `providerDescription` | string | no |  |
| `providerName` | string | no |  |
| `reav` | boolean | no |  |
| `startDate` | string(date-time) | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `type` | [`ManualServiceType`](#schema-manualservicetype) | no |  |
| `voucherDescription` | string | no |  |


### `BookedInsuranceServiceVO` {#schema-bookedinsuranceservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amendments` | array[[`BookedServiceAmendmentVO`](#schema-bookedserviceamendmentvo)] | no |  |
| `bookingReference` | string | no |  |
| `cancelPolicy` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `cancelationDate` | string(date-time) | no |  |
| `cancellationType` | [`CancellationType`](#schema-cancellationtype) | no |  |
| `confirmationErrorCause` | string | no |  |
| `consolidated` | boolean | no |  |
| `country` | string | no |  |
| `endDate` | string(date-time) | no |  |
| `externalCode` | string | no |  |
| `id` | string | no |  |
| `insuranceCode` | string | no |  |
| `insuranceConfigurationName` | string | no |  |
| `insuranceDestination` | string | no |  |
| `insuranceName` | string | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `membershipSavedPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `operatorProvider` | string | no |  |
| `originalpricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `policyNumber` | string | no |  |
| `priceType` | [`PriceType`](#schema-pricetype) | no |  |
| `pricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |
| `providerBookingReference` | string | no |  |
| `providerCancellationDate` | string(date-time) | no |  |
| `providerConfigurationId` | integer(int32) | no |  |
| `providerDescription` | string | no |  |
| `reav` | boolean | no |  |
| `startDate` | string(date-time) | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `supplierId` | integer(int32) | no |  |
| `supplierName` | string | no |  |


### `BookedItemServiceVO` {#schema-bookeditemservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amendments` | array[[`BookedServiceAmendmentVO`](#schema-bookedserviceamendmentvo)] | no |  |
| `bookingReference` | string | no |  |
| `cancelPolicy` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `cancelationDate` | string(date-time) | no |  |
| `cancellationType` | [`CancellationType`](#schema-cancellationtype) | no |  |
| `confirmationErrorCause` | string | no |  |
| `consolidated` | boolean | no |  |
| `country` | string | no |  |
| `endDate` | string(date-time) | no |  |
| `externalCode` | string | no |  |
| `id` | string | no |  |
| `insurance` | [`InsuranceVO`](#schema-insurancevo) | no |  |
| `itemName` | string | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `membershipSavedPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `operatorProvider` | string | no |  |
| `originalpricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `priceType` | [`PriceType`](#schema-pricetype) | no |  |
| `pricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |
| `providerCancellationDate` | string(date-time) | no |  |
| `providerCode` | string | no |  |
| `providerConfigurationId` | integer(int32) | no |  |
| `providerDescription` | string | no |  |
| `quantity` | integer(int32) | no |  |
| `reav` | boolean | no |  |
| `simInstallation` | [`SimInstallationVO`](#schema-siminstallationvo) | no |  |
| `startDate` | string(date-time) | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `supplierId` | integer(int32) | no |  |
| `supplierName` | string | no |  |


### `BookedManualServiceVO` {#schema-bookedmanualservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amendments` | array[[`BookedServiceAmendmentVO`](#schema-bookedserviceamendmentvo)] | no |  |
| `bookingReference` | string | no |  |
| `cancelPolicy` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `cancelationDate` | string(date-time) | no |  |
| `cancellationType` | [`CancellationType`](#schema-cancellationtype) | no |  |
| `confirmationErrorCause` | string | no |  |
| `consolidated` | boolean | no |  |
| `country` | string | no |  |
| `createdByAgency` | string | no |  |
| `description` | string | no |  |
| `endDate` | string(date-time) | no |  |
| `externalCode` | string | no |  |
| `id` | string | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `membershipSavedPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `operatorProvider` | string | no |  |
| `originalpricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `paymentType` | string | no |  |
| `pdfUrl` | string | no |  |
| `priceType` | [`PriceType`](#schema-pricetype) | no |  |
| `pricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `promotionCode` | boolean | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |
| `providerCancellationDate` | string(date-time) | no |  |
| `providerConfigurationId` | integer(int32) | no |  |
| `providerDescription` | string | no |  |
| `providerName` | string | no |  |
| `reav` | boolean | no |  |
| `startDate` | string(date-time) | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `type` | [`ManualServiceType`](#schema-manualservicetype) | no |  |
| `voucherDescription` | string | no |  |


### `BookedManualServicesVO` {#schema-bookedmanualservicesvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accountingAdjustment` | array[[`BookedManualServiceVO`](#schema-bookedmanualservicevo)] | no |  |
| `carRental` | array[[`BookedCarRentalManualServiceVO`](#schema-bookedcarrentalmanualservicevo)] | no |  |
| `closedTour` | array[[`BookedClosedTourManualServiceVO`](#schema-bookedclosedtourmanualservicevo)] | no |  |
| `cruise` | array[[`BookedCruiseManualServiceVO`](#schema-bookedcruisemanualservicevo)] | no |  |
| `hotel` | array[[`BookedHotelManualServiceVO`](#schema-bookedhotelmanualservicevo)] | no |  |
| `insurance` | array[[`BookedInsuranceManualServiceVO`](#schema-bookedinsurancemanualservicevo)] | no |  |
| `other` | array[[`BookedManualServiceVO`](#schema-bookedmanualservicevo)] | no |  |
| `ticket` | array[[`BookedTicketManualServiceVO`](#schema-bookedticketmanualservicevo)] | no |  |
| `transfer` | array[[`BookedTransferManualServiceVO`](#schema-bookedtransfermanualservicevo)] | no |  |
| `transport` | array[[`BookedTransportManualServiceVO`](#schema-bookedtransportmanualservicevo)] | no |  |


### `BookedNoteType` {#schema-bookednotetype}

Enum values: 'VISIBLE', 'OPERATOR', 'CLEARING', 'ADMIN'


### `BookedNoteVO` {#schema-bookednotevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `bookedServiceId` | string | no |  |
| `createdBy` | string | no |  |
| `creationDate` | string(date-time) | no |  |
| `note` | string | no |  |
| `serviceType` | [`ServiceType`](#schema-servicetype) | no |  |
| `type` | [`BookedNoteType`](#schema-bookednotetype) | no |  |


### `BookedServiceAmendmentVO` {#schema-bookedserviceamendmentvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | no |  |
| `id` | integer(int32) | no |  |
| `originalPriceBreakDown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `priceBreakDown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `reference` | string | no |  |


### `BookedServiceVO` {#schema-bookedservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `balance` | string | no |  |
| `borneByCompany` | string | no |  |
| `currencySymbol` | string | no |  |
| `description` | string | no |  |
| `expenseDate` | string(date) | no |  |
| `paidByCompany` | string | no |  |
| `paidByEmployee` | string | no |  |
| `reference` | string | no |  |
| `reportReference` | string | no |  |
| `serviceDate` | string(date) | no |  |
| `serviceType` | string | no |  |


### `BookedTicketManualServiceVO` {#schema-bookedticketmanualservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amendments` | array[[`BookedServiceAmendmentVO`](#schema-bookedserviceamendmentvo)] | no |  |
| `bookingReference` | string | no |  |
| `cancelPolicy` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `cancelationDate` | string(date-time) | no |  |
| `cancellationType` | [`CancellationType`](#schema-cancellationtype) | no |  |
| `confirmationErrorCause` | string | no |  |
| `consolidated` | boolean | no |  |
| `country` | string | no |  |
| `createdByAgency` | string | no |  |
| `description` | string | no |  |
| `destination` | string | no |  |
| `endDate` | string(date-time) | no |  |
| `externalCode` | string | no |  |
| `id` | string | no |  |
| `image` | string | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `membershipSavedPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `operatorProvider` | string | no |  |
| `origin` | string | no |  |
| `originalpricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `paymentType` | string | no |  |
| `pdfUrl` | string | no |  |
| `priceType` | [`PriceType`](#schema-pricetype) | no |  |
| `pricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `promotionCode` | boolean | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |
| `providerCancellationDate` | string(date-time) | no |  |
| `providerConfigurationId` | integer(int32) | no |  |
| `providerDescription` | string | no |  |
| `providerName` | string | no |  |
| `reav` | boolean | no |  |
| `startDate` | string(date-time) | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `ticketName` | string | no |  |
| `type` | [`ManualServiceType`](#schema-manualservicetype) | no |  |
| `voucherDescription` | string | no |  |


### `BookedTicketServiceVO` {#schema-bookedticketservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amendments` | array[[`BookedServiceAmendmentVO`](#schema-bookedserviceamendmentvo)] | no |  |
| `bookingReference` | string | no |  |
| `cancelPolicy` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `cancelationDate` | string(date-time) | no |  |
| `cancellationType` | [`CancellationType`](#schema-cancellationtype) | no |  |
| `confirmationErrorCause` | string | no |  |
| `consolidated` | boolean | no |  |
| `country` | string | no |  |
| `dataSheetId` | string | no |  |
| `description` | string | no |  |
| `destination` | string | no |  |
| `duration` | string | no |  |
| `endDate` | string(date-time) | no |  |
| `eventDate` | string(date) | no |  |
| `eventLanguage` | string | no |  |
| `eventTime` | string(time-local) | no |  |
| `externalCode` | string | no |  |
| `id` | string | no |  |
| `included` | array[string] | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `meetingPoint` | string | no |  |
| `membershipSavedPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `modalitytName` | string | no |  |
| `operatorProvider` | string | no |  |
| `originalpricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `passengers` | string | no |  |
| `priceType` | [`PriceType`](#schema-pricetype) | no |  |
| `pricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `prices` | array[[`TicketModalityPriceVO`](#schema-ticketmodalitypricevo)] | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |
| `providerBookingReference` | string | no |  |
| `providerCancellationDate` | string(date-time) | no |  |
| `providerCode` | string | no |  |
| `providerConfigurationId` | integer(int32) | no |  |
| `providerDescription` | string | no |  |
| `questions` | array[[`TicketQuestionVO`](#schema-ticketquestionvo)] | no |  |
| `reav` | boolean | no |  |
| `remarks` | array[string] | no |  |
| `startDate` | string(date-time) | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `supplierId` | integer(int32) | no |  |
| `supplierName` | string | no |  |
| `supportingPdfUrl` | string | no |  |
| `ticketId` | string | no |  |
| `ticketModality` | string | no |  |
| `ticketName` | string | no |  |
| `ticketType` | string | no |  |
| `voucherUrl` | string | no |  |


### `BookedTransferManualServiceVO` {#schema-bookedtransfermanualservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amendments` | array[[`BookedServiceAmendmentVO`](#schema-bookedserviceamendmentvo)] | no |  |
| `arrivalHour` | string(time-local) | no |  |
| `bookingReference` | string | no |  |
| `cancelPolicy` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `cancelationDate` | string(date-time) | no |  |
| `cancellationType` | [`CancellationType`](#schema-cancellationtype) | no |  |
| `confirmationErrorCause` | string | no |  |
| `consolidated` | boolean | no |  |
| `country` | string | no |  |
| `createdByAgency` | string | no |  |
| `departureHour` | string(time-local) | no |  |
| `description` | string | no |  |
| `destination` | string | no |  |
| `endDate` | string(date-time) | no |  |
| `externalCode` | string | no |  |
| `id` | string | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `membershipSavedPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `operatorProvider` | string | no |  |
| `origin` | string | no |  |
| `originalpricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `paymentType` | string | no |  |
| `pdfUrl` | string | no |  |
| `priceType` | [`PriceType`](#schema-pricetype) | no |  |
| `pricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `promotionCode` | boolean | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |
| `providerCancellationDate` | string(date-time) | no |  |
| `providerConfigurationId` | integer(int32) | no |  |
| `providerDescription` | string | no |  |
| `providerName` | string | no |  |
| `reav` | boolean | no |  |
| `startDate` | string(date-time) | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `transferType` | string | no |  |
| `type` | [`ManualServiceType`](#schema-manualservicetype) | no |  |
| `voucherDescription` | string | no |  |


### `BookedTransferServiceVO` {#schema-bookedtransferservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amendments` | array[[`BookedServiceAmendmentVO`](#schema-bookedserviceamendmentvo)] | no |  |
| `bookingReference` | string | no |  |
| `cancelPolicy` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `cancelationDate` | string(date-time) | no |  |
| `cancellationType` | [`CancellationType`](#schema-cancellationtype) | no |  |
| `confirmationErrorCause` | string | no |  |
| `confirmedPickupTime` | boolean | no |  |
| `consolidated` | boolean | no |  |
| `country` | string | no |  |
| `customerMaximumWaitMinutes` | integer(int32) | no |  |
| `destinationCode` | string | no |  |
| `endDate` | string(date-time) | no |  |
| `externalCode` | string | no |  |
| `flightNumber` | string | no |  |
| `fromPlace` | [`PlaceVO`](#schema-placevo) | no |  |
| `id` | string | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `maximumStops` | integer(int32) | no |  |
| `maximumWaitMinutes` | integer(int32) | no |  |
| `membershipSavedPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `name` | string | no |  |
| `operatorProvider` | string | no |  |
| `originalpricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `priceType` | [`PriceType`](#schema-pricetype) | no |  |
| `pricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `productType` | [`TransferProductType`](#schema-transferproducttype) | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |
| `providerCancellationDate` | string(date-time) | no |  |
| `providerCode` | string | no |  |
| `providerConfigurationId` | integer(int32) | no |  |
| `providerDescription` | string | no |  |
| `providerTransferCode` | string | no |  |
| `reav` | boolean | no |  |
| `remarks` | array[string] | no |  |
| `serviceType` | [`TransferServiceType`](#schema-transferservicetype) | no |  |
| `startDate` | string(date-time) | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `supplierId` | integer(int32) | no |  |
| `supplierName` | string | no |  |
| `telephoneAssistance` | string | no |  |
| `toPlace` | [`PlaceVO`](#schema-placevo) | no |  |
| `transferType` | [`TransferType`](#schema-transfertype) | no |  |
| `vehicleType` | [`TransferVehicleType`](#schema-transfervehicletype) | no |  |
| `voucherUrl` | string | no |  |


### `BookedTransportManualServiceVO` {#schema-bookedtransportmanualservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amendments` | array[[`BookedServiceAmendmentVO`](#schema-bookedserviceamendmentvo)] | no |  |
| `arrivalHour` | string(time-local) | no |  |
| `bookingReference` | string | no |  |
| `cancelPolicy` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `cancelationDate` | string(date-time) | no |  |
| `cancellationType` | [`CancellationType`](#schema-cancellationtype) | no |  |
| `company` | string | no |  |
| `confirmationErrorCause` | string | no |  |
| `consolidated` | boolean | no |  |
| `country` | string | no |  |
| `createdByAgency` | string | no |  |
| `departureHour` | string(time-local) | no |  |
| `description` | string | no |  |
| `destination` | string | no |  |
| `endDate` | string(date-time) | no |  |
| `externalCode` | string | no |  |
| `flightNumber` | string | no |  |
| `id` | string | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `membershipSavedPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `operatorProvider` | string | no |  |
| `origin` | string | no |  |
| `originalpricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `paymentType` | string | no |  |
| `pdfUrl` | string | no |  |
| `pnr` | string | no |  |
| `priceType` | [`PriceType`](#schema-pricetype) | no |  |
| `pricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `promotionCode` | boolean | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |
| `providerCancellationDate` | string(date-time) | no |  |
| `providerConfigurationId` | integer(int32) | no |  |
| `providerDescription` | string | no |  |
| `providerName` | string | no |  |
| `reav` | boolean | no |  |
| `startDate` | string(date-time) | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `type` | [`ManualServiceType`](#schema-manualservicetype) | no |  |
| `voucherDescription` | string | no |  |


### `BookedTransportSegmentVO` {#schema-bookedtransportsegmentvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `airlineReference` | string | no |  |
| `arrivalAirport` | string | no |  |
| `arrivalAirportName` | string | no |  |
| `arrivalDate` | string(date-time) | no |  |
| `bookingClass` | string | no |  |
| `cabinClass` | string | no |  |
| `departureAirport` | string | no |  |
| `departureAirportName` | string | no |  |
| `departureDate` | string(date-time) | no |  |
| `durationInMinutes` | integer(int64) | no |  |
| `fareType` | [`FareType`](#schema-faretype) | no |  |
| `flightNumber` | string | no |  |
| `marketingAirlineCode` | string | no |  |
| `operatingAirlineCode` | string | no |  |
| `seat` | array[[`SeatVO`](#schema-seatvo)] | no |  |
| `segmentKey` | string | no |  |
| `technicalStops` | array[[`TransportTechnicalStopVO`](#schema-transporttechnicalstopvo)] | no |  |
| `transportType` | string | no |  |


### `BookedTransportServiceVO` {#schema-bookedtransportservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `airlineName` | string | no |  |
| `amendments` | array[[`BookedServiceAmendmentVO`](#schema-bookedserviceamendmentvo)] | no |  |
| `arrivalAirport` | string | no |  |
| `baggage` | array[[`BaggageVO`](#schema-baggagevo)] | no |  |
| `basePriceAmount` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `bookingReference` | string | no |  |
| `cancelPolicy` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `cancelationDate` | string(date-time) | no |  |
| `cancellationType` | [`CancellationType`](#schema-cancellationtype) | no |  |
| `confirmationErrorCause` | string | no |  |
| `consolidated` | boolean | no |  |
| `country` | string | no |  |
| `departureAirport` | string | no |  |
| `endDate` | string(date-time) | no |  |
| `etickets` | array[[`ETicketVO`](#schema-eticketvo)] | no |  |
| `externalCode` | string | no |  |
| `id` | string | no |  |
| `includedBaggage` | string | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `membershipSavedPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `onewayPrice` | boolean | no |  |
| `operatorProvider` | string | no |  |
| `originalpricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `personFareBreakdown` | array[[`TransportFareBreakdownVO`](#schema-transportfarebreakdownvo)] | no |  |
| `priceType` | [`PriceType`](#schema-pricetype) | no |  |
| `pricebreakdown` | [`ServicePriceBreakdownVO`](#schema-servicepricebreakdownvo) | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |
| `providerBookingReference` | string | no |  |
| `providerCancellationDate` | string(date-time) | no |  |
| `providerConfigurationId` | integer(int32) | no |  |
| `providerDescription` | string | no |  |
| `reav` | boolean | no |  |
| `remarks` | array[string] | no |  |
| `returnArrivalAirport` | string | no |  |
| `returnArrivalDate` | string(date-time) | no |  |
| `returnDepartureAirport` | string | no |  |
| `returnDepartureDate` | string(date-time) | no |  |
| `segment` | array[[`BookedTransportSegmentVO`](#schema-bookedtransportsegmentvo)] | no |  |
| `startDate` | string(date-time) | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `supplierId` | integer(int32) | no |  |
| `supplierName` | string | no |  |
| `taxAmount` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `taxAmountInMicrositeCurrency` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `voucherUrl` | string | no |  |


### `BookedTripInvoiceVO` {#schema-bookedtripinvoicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `creationDate` | string(date) | no |  |
| `number` | string | no |  |
| `target` | [`InvoiceTarget`](#schema-invoicetarget) | yes |  |


### `BookedTripVO` {#schema-bookedtripvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `adultCount` | integer(int32) | no |  |
| `agencyBookingReference` | string | no |  |
| `allAgencyManagementFee` | [`AgencyManagementFeeVO`](#schema-agencymanagementfeevo) | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `bookedNotes` | array[[`BookedNoteVO`](#schema-bookednotevo)] | no |  |
| `bookingAssociatedTo` | string | no |  |
| `bookingReference` | string | no |  |
| `cancellationPolicies` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `captioReference` | string | no |  |
| `carservice` | array[[`BookedCarServiceVO`](#schema-bookedcarservicevo)] | no |  |
| `centerOfCost` | string | no |  |
| `childCount` | integer(int32) | no |  |
| `clientrequest` | array[[`ClientRequestVO`](#schema-clientrequestvo)] | no |  |
| `closedtourservice` | array[[`BookedClosedTourServiceVO`](#schema-bookedclosedtourservicevo)] | no |  |
| `contactPerson` | [`PersonVO`](#schema-personvo) | no |  |
| `countryTaxes` | array[[`CountryTaxesVO`](#schema-countrytaxesvo)] | no |  |
| `creationDate` | string(date-time) | no |  |
| `cruiseservice` | array[[`BookedCruiseServiceVO`](#schema-bookedcruiseservicevo)] | no |  |
| `customBookingReference` | string | no |  |
| `destinationCount` | integer(int32) | no |  |
| `distribution` | array[[`DistributionVO`](#schema-distributionvo)] | no |  |
| `emergencyContact` | [`EmergencyContactVO`](#schema-emergencycontactvo) | no |  |
| `endDate` | string(date) | no |  |
| `fromIdeaSharedByUser` | [`UserVO`](#schema-uservo) | no |  |
| `headquarters` | string | no |  |
| `historical` | array[[`ChangeLogVO`](#schema-changelogvo)] | no |  |
| `hotelservice` | array[[`BookedHotelServiceVO`](#schema-bookedhotelservicevo)] | no |  |
| `id` | string | no |  |
| `ideaUrl` | string | no |  |
| `infantCount` | integer(int32) | no |  |
| `insuranceservice` | array[[`BookedInsuranceServiceVO`](#schema-bookedinsuranceservicevo)] | no |  |
| `invoiceInfo` | [`InvoicedDataVO`](#schema-invoiceddatavo) | no |  |
| `invoices` | array[[`BookedTripInvoiceVO`](#schema-bookedtripinvoicevo)] | no |  |
| `isPackage` | boolean | no |  |
| `itemservice` | array[[`BookedItemServiceVO`](#schema-bookeditemservicevo)] | no |  |
| `language` | [`Language`](#schema-language) | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `managedBy` | string | no |  |
| `manualPayments` | array[[`ManualPaymentVO`](#schema-manualpaymentvo)] | no |  |
| `manualServices` | [`BookedManualServicesVO`](#schema-bookedmanualservicesvo) | no |  |
| `monthlyReportReference` | string | no |  |
| `nightsCount` | integer(int32) | no |  |
| `notIncludedManagementFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `notes` | string | no | Legacy text containing VISIBLE and OPERATOR notes. Use bookedNotes for structured notes. |
| `originalBookingAgency` | string | no |  |
| `payment` | [`PaymentVO`](#schema-paymentvo) | no |  |
| `pdfs` | array[[`PdfVO`](#schema-pdfvo)] | no |  |
| `pricebreakdown` | [`PriceBreakdownVO`](#schema-pricebreakdownvo) | no |  |
| `referralAgencyId` | integer(int32) | no |  |
| `referralId` | string | no |  |
| `reportReference` | string | no |  |
| `residentDiscount` | [`ResidentDiscount`](#schema-residentdiscount) | no |  |
| `salesChannel` | [`SalesChannelType`](#schema-saleschanneltype) | no |  |
| `sourceMarket` | [`CountryVO`](#schema-countryvo) | no |  |
| `startDate` | string(date-time) | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |
| `themesAssociated` | array[[`ThemeVO`](#schema-themevo)] | no |  |
| `ticketservice` | array[[`BookedTicketServiceVO`](#schema-bookedticketservicevo)] | no |  |
| `totalRouteDistance` | [`TotalRouteDistanceVO`](#schema-totalroutedistancevo) | no |  |
| `transferservice` | array[[`BookedTransferServiceVO`](#schema-bookedtransferservicevo)] | no |  |
| `transportservice` | array[[`BookedTransportServiceVO`](#schema-bookedtransportservicevo)] | no |  |
| `tripType` | [`TripType`](#schema-triptype) | no |  |
| `user` | [`UserVO`](#schema-uservo) | no |  |
| `voucherUrl` | string | no |  |
| `webSessionId` | string | no |  |


### `BookingsBasicDataVO` {#schema-bookingsbasicdatavo}

| Field | Type | Required | Description |
|---|---|---|---|
| `bookingReference` | string | no |  |
| `creationDate` | string(date-time) | no |  |
| `micrositeId` | string | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |


### `BookingsVO` {#schema-bookingsvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `bookings` | array[[`BookingsBasicDataVO`](#schema-bookingsbasicdatavo)] | no |  |
| `pagination` | [`PaginationVO`](#schema-paginationvo) | no |  |


### `BusinessTravelExpenseCategory` {#schema-businesstravelexpensecategory}

Enum values: 'MEALS', 'ACCOMMODATION', 'TRANSPORT', 'OTHERS'


### `BusinessTravelExpenseSubcategory` {#schema-businesstravelexpensesubcategory}

Enum values: 'LUNCH', 'DINNER', 'MEAL_WITH_CLIENTS', 'OTHER_MEAL', 'HOTEL', 'GUESTHOUSE', 'OTHER_ACCOMMODATION', 'PLANE', 'CAR_RENTAL', 'PUBLIC_TRANSPORT', 'PARKING', 'TOLL', 'PREPAID_TAXI', 'PUBLIC_TAXI', 'TRAIN', 'OTHER_TRANSPORT', 'VISAS', 'OTHER_EXPENSES'


### `BusinessTravelPaymentMethod` {#schema-businesstravelpaymentmethod}

Enum values: 'COMPANY_CARD', 'PERSONAL_CARD', 'CASH'


### `CabinClassType` {#schema-cabinclasstype}

Enum values: 'BUSINESS', 'FIRST', 'PREMIUM_ECONOMY', 'ECONOMY', 'PREFERRED', 'TOURIST_PLUS', 'TOURIST'


### `CancelBookingRequestVO` {#schema-cancelbookingrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `cancellationType` | [`CancellationType`](#schema-cancellationtype) | yes |  |
| `emailNotifyCancel` | string(email) | no |  |
| `manualCancellationFee` | number(double) | no |  |
| `relatedManualCancellationFee` | number(double) | no |  |


### `CancelBookingResponseVO` {#schema-cancelbookingresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `cancellationDate` | string(date-time) | no |  |
| `cancellationType` | [`CancellationType`](#schema-cancellationtype) | no |  |
| `providerCancellationDate` | string(date-time) | no |  |
| `status` | [`ServiceStatus`](#schema-servicestatus) | no |  |


### `CancellationFeeResponseVO` {#schema-cancellationfeeresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `cancellationFee` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `relatedService` | [`RelatedServiceVO`](#schema-relatedservicevo) | no |  |
| `supportedCancellationTypes` | array[[`CancellationType`](#schema-cancellationtype)] | no |  |


### `CancellationPolicyType` {#schema-cancellationpolicytype}

Enum values: 'REFUNDABLE', 'PARTIALLY_REFUNDABLE', 'NON_REFUNDABLE', 'UNKNOWN'


### `CancellationPolicyVO` {#schema-cancellationpolicyvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amount` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `commissionPriceAmount` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `date` | string(date) | no |  |
| `netPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `nonCommissionableAmount` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `policyAmount` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |


### `CancellationType` {#schema-cancellationtype}

Enum values: 'PROVIDER', 'MANUAL'


### `CancellationTypeVO` {#schema-cancellationtypevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `deadline` | string(date) | no |  |
| `type` | [`CancellationPolicyType`](#schema-cancellationpolicytype) | no |  |


### `CarRentalLocationVO` {#schema-carrentallocationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | no |  |
| `name` | string | no |  |
| `openingHours` | string | no |  |
| `parkLocation` | string | no |  |
| `phone` | string | no |  |
| `providerUrlInfo` | string | no |  |
| `street` | string | no |  |


### `CarRentalServiceInsuranceVO` {#schema-carrentalserviceinsurancevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `currency` | [`Currency`](#schema-currency) | no |  |
| `price` | number(double) | no |  |
| `supplierAccountingCode` | string | no |  |


### `CarRentalServiceItemVO` {#schema-carrentalserviceitemvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | no |  |
| `price` | [`MoneyVO`](#schema-moneyvo) | no |  |


### `ChangeLogVO` {#schema-changelogvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `dateTime` | string(date-time) | no |  |
| `message` | string | no |  |


### `ClassificationType` {#schema-classificationtype}

Enum values: 'Aerial View', 'Bathroom', 'Beach', 'Building', 'Business Center', 'Cleanliness badge', 'Garden', 'Hallway-Staircase', 'Kitchen', 'Living Area', 'Lobby-Reception', 'Meeting Facility', 'Parking', 'Pool', 'Restaurant-Bar', 'Room', 'Spa-Sauna', 'Sports Facility', 'Terrace', 'Food', 'Waterpark'


### `ClientRequestAddMessageVO` {#schema-clientrequestaddmessagevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `message` | string | no |  |


### `ClientRequestMessageVO` {#schema-clientrequestmessagevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `date` | string(date-time) | no |  |
| `message` | string | no |  |
| `user` | string | no |  |


### `ClientRequestType` {#schema-clientrequesttype}

Enum values: 'AMENDMENT', 'CANCELLATION', 'QUESTION', 'COMPLAINT', 'OTHER'


### `ClientRequestVO` {#schema-clientrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `closingDate` | string(date-time) | no |  |
| `creationDate` | string(date-time) | no |  |
| `id` | integer(int32) | no |  |
| `message` | array[[`ClientRequestMessageVO`](#schema-clientrequestmessagevo)] | no |  |
| `requestClosed` | boolean | no |  |
| `type` | [`ClientRequestType`](#schema-clientrequesttype) | yes |  |


### `ClosedTourAdditionalServiceVO` {#schema-closedtouradditionalservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `airline` | [`AirlineVO`](#schema-airlinevo) | no |  |
| `airlineCode` | string | no |  |
| `cancellationPolicies` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `code` | string | no |  |
| `commissionPriceAmount` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `date` | string(date) | no |  |
| `day` | integer(int32) | no |  |
| `description` | string | no |  |
| `extraId` | string | no |  |
| `flightNumber` | string | no |  |
| `hidden` | boolean | no |  |
| `iata` | string | no |  |
| `id` | integer(int32) | no |  |
| `name` | string | no |  |
| `netCommissionablePrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `netPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `onRequest` | boolean | no |  |
| `pricePerPerson` | boolean | no |  |
| `providerId` | string | no |  |
| `refundable` | boolean | no |  |
| `selectable` | boolean | no |  |
| `selected` | boolean | no |  |
| `totalPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `transferType` | [`TransferType`](#schema-transfertype) | no |  |
| `type` | string | no |  |
| `typeId` | string | no |  |


### `ClosedTourDatasheetVO` {#schema-closedtourdatasheetvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | no |  |
| `excluded` | string | no |  |
| `hotels` | string | no |  |
| `included` | string | no |  |
| `meetingPoint` | string | no |  |
| `name` | string | no |  |
| `remarksDescription` | string | no |  |
| `remarksTitle` | string | no |  |
| `voucherRemarks` | string | no |  |


### `ContractCancellationRangeVO` {#schema-contractcancellationrangevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `days` | integer(int32) | no |  |
| `percentage` | number(double) | no |  |


### `ContractClosedTourActivitiesVO` {#schema-contractclosedtouractivitiesvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | no |  |
| `included` | boolean | no |  |
| `title` | string | no |  |


### `ContractClosedTourItineraryDestinationVO` {#schema-contractclosedtouritinerarydestinationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | no |  |
| `description` | object (inline) | no |  |
| `destination` | string | no |  |
| `hotels` | string | no |  |
| `hotelsId` | array[string] | no |  |
| `image` | string | no |  |
| `nights` | integer(int32) | no |  |


### `ContractClosedTourItinerarySegmentVO` {#schema-contractclosedtouritinerarysegmentvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `activities` | array[[`ContractClosedTourActivitiesVO`](#schema-contractclosedtouractivitiesvo)] | no |  |
| `description` | string | no |  |
| `destination` | array[[`IdeaDestinationVO`](#schema-ideadestinationvo)] | no |  |
| `hotels` | array[string] | no |  |
| `title` | string | no |  |


### `ContractClosedTourModalityCapacityDayVO` {#schema-contractclosedtourmodalitycapacitydayvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `date` | string(date) | yes |  |
| `initialCapacity` | integer(int32) | no |  |
| `manualSold` | integer(int32) | no |  |
| `onRequestInitialCapacity` | integer(int32) | no |  |
| `onRequestManualSold` | integer(int32) | no |  |


### `ContractClosedTourModalityPriceVO` {#schema-contractclosedtourmodalitypricevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `endDate` | string(date) | yes |  |
| `name` | string | no |  |
| `price` | [`ContractClosedTourPriceVO`](#schema-contractclosedtourpricevo) | no |  |
| `startDate` | string(date) | yes |  |


### `ContractClosedTourOptionVO` {#schema-contractclosedtouroptionvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | no |  |
| `id` | integer(int32) | no |  |
| `onRequest` | boolean | no |  |
| `onRequestQuantityPerDay` | integer(int32) | no |  |
| `operationalDays` | array[[`DayOfWeek`](#schema-dayofweek)] | no |  |
| `priceList` | array[[`ContractClosedTourModalityPriceVO`](#schema-contractclosedtourmodalitypricevo)] | yes |  |
| `quantityPerDate` | array[[`ContractClosedTourModalityCapacityDayVO`](#schema-contractclosedtourmodalitycapacitydayvo)] | no |  |
| `quantityPerDay` | integer(int32) | no |  |
| `stopSales` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no |  |
| `translations` | object (inline) | no |  |
| `useAdditionalOnRequestQuota` | boolean | no |  |


### `ContractClosedTourPriceVO` {#schema-contractclosedtourpricevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `doublePrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `quadrupleChildPercentageDiscount` | number(double) | no |  |
| `quadruplePrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `singlePrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `tripleChildPercentageDiscount` | number(double) | no |  |
| `triplePrice` | [`MoneyVO`](#schema-moneyvo) | no |  |


### `ContractClosedTourSupplementAgeRangeVO` {#schema-contractclosedtoursupplementagerangevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `max` | integer(int32) | no |  |
| `min` | integer(int32) | no |  |


### `ContractClosedTourSupplementDateRangeVO` {#schema-contractclosedtoursupplementdaterangevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `end` | string | no |  |
| `start` | string | no |  |


### `ContractClosedTourSupplementDistributionPricingMoneyVO` {#schema-contractclosedtoursupplementdistributionpricingmoneyvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amount` | number(double) | no |  |
| `percentage` | boolean | no |  |


### `ContractClosedTourSupplementDistributionPricingVO` {#schema-contractclosedtoursupplementdistributionpricingvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `doubleChildDiscount` | [`ContractClosedTourSupplementDistributionPricingMoneyVO`](#schema-contractclosedtoursupplementdistributionpricingmoneyvo) | no |  |
| `doubleCommission` | number(double) | no |  |
| `doublePrice` | number(double) | no |  |
| `doublePvpPrice` | number(double) | no |  |
| `quadrupleChildDiscount` | [`ContractClosedTourSupplementDistributionPricingMoneyVO`](#schema-contractclosedtoursupplementdistributionpricingmoneyvo) | no |  |
| `quadrupleCommission` | number(double) | no |  |
| `quadruplePrice` | number(double) | no |  |
| `quadruplePvpPrice` | number(double) | no |  |
| `singleChildDiscount` | [`ContractClosedTourSupplementDistributionPricingMoneyVO`](#schema-contractclosedtoursupplementdistributionpricingmoneyvo) | no |  |
| `singleCommission` | number(double) | no |  |
| `singlePrice` | number(double) | no |  |
| `singlePvpPrice` | number(double) | no |  |
| `tripleChildDiscount` | [`ContractClosedTourSupplementDistributionPricingMoneyVO`](#schema-contractclosedtoursupplementdistributionpricingmoneyvo) | no |  |
| `tripleCommission` | number(double) | no |  |
| `triplePrice` | number(double) | no |  |
| `triplePvpPrice` | number(double) | no |  |


### `ContractClosedTourSupplementOccupancyDiscountVO` {#schema-contractclosedtoursupplementoccupancydiscountvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `ageRange` | [`ContractClosedTourSupplementAgeRangeVO`](#schema-contractclosedtoursupplementagerangevo) | no |  |
| `discount` | [`ContractClosedTourSupplementDistributionPricingMoneyVO`](#schema-contractclosedtoursupplementdistributionpricingmoneyvo) | no |  |
| `occupancy` | integer(int32) | no |  |


### `ContractClosedTourSupplementOccupancyPriceVO` {#schema-contractclosedtoursupplementoccupancypricevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `ageRange` | [`ContractClosedTourSupplementAgeRangeVO`](#schema-contractclosedtoursupplementagerangevo) | no |  |
| `amount` | number(double) | no |  |
| `commission` | number(double) | no |  |
| `occupancy` | integer(int32) | no |  |
| `pvpAmount` | number(double) | no |  |


### `ContractClosedTourSupplementVO` {#schema-contractclosedtoursupplementvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `bookingWindows` | array[[`ContractClosedTourSupplementDateRangeVO`](#schema-contractclosedtoursupplementdaterangevo)] | no |  |
| `commissionable` | boolean | no |  |
| `free` | boolean | no |  |
| `mandatory` | boolean | no |  |
| `modalityCodes` | array[string] | no |  |
| `occupancyDiscounts` | array[[`ContractClosedTourSupplementOccupancyDiscountVO`](#schema-contractclosedtoursupplementoccupancydiscountvo)] | no |  |
| `occupancyPrices` | array[[`ContractClosedTourSupplementOccupancyPriceVO`](#schema-contractclosedtoursupplementoccupancypricevo)] | no |  |
| `onRequest` | boolean | no |  |
| `price` | [`ContractClosedTourSupplementDistributionPricingVO`](#schema-contractclosedtoursupplementdistributionpricingvo) | no |  |
| `priceInPercentage` | boolean | no |  |
| `refundable` | boolean | no |  |
| `translations` | object (inline) | no |  |
| `travelWindows` | array[[`ContractClosedTourSupplementDateRangeVO`](#schema-contractclosedtoursupplementdaterangevo)] | no |  |


### `ContractClosedTourVO` {#schema-contractclosedtourvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `cancellationRanges` | array[[`ContractCancellationRangeVO`](#schema-contractcancellationrangevo)] | no |  |
| `code` | string | no |  |
| `currency` | [`Currency`](#schema-currency) | no |  |
| `datasheets` | object (inline) | no |  |
| `daysAvailableBeforeRelease` | integer(int32) | no |  |
| `downloadMode` | [`TourDownloadMode`](#schema-tourdownloadmode) | no |  |
| `endTime` | string(time-local) | no |  |
| `hotels` | integer(int32) | no |  |
| `images` | array[string] | no |  |
| `itinerary` | array[[`ContractClosedTourItineraryDestinationVO`](#schema-contractclosedtouritinerarydestinationvo)] | no |  |
| `maxChildAge` | integer(int32) | no |  |
| `maxPax` | integer(int32) | no |  |
| `minChildAge` | integer(int32) | no |  |
| `minPax` | integer(int32) | no |  |
| `modalityCodes` | array[string] | no |  |
| `name` | string | no |  |
| `nights` | integer(int32) | no |  |
| `price` | [`ContractClosedTourPriceVO`](#schema-contractclosedtourpricevo) | no |  |
| `providerCode` | string | no |  |
| `showHotelsFromDataSheet` | boolean | no |  |
| `showItineraryDescription` | boolean | no |  |
| `startTime` | string(time-local) | no |  |
| `supplements` | array[[`ContractClosedTourSupplementVO`](#schema-contractclosedtoursupplementvo)] | no |  |
| `supplier` | string | no |  |
| `transports` | integer(int32) | no |  |
| `userId` | string | no |  |


### `ContractGolfBaseVO` {#schema-contractgolfbasevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `city` | string | no |  |
| `code` | string | no |  |
| `currency` | [`Currency`](#schema-currency) | no |  |
| `datasheets` | object (inline) | no |  |
| `geolocation` | [`GeolocationVO`](#schema-geolocationvo) | no |  |
| `imageUrls` | array[string] | no |  |
| `maxPlayers` | integer(int32) | no |  |
| `minPlayers` | integer(int32) | no |  |
| `name` | string | no |  |
| `onlyHolidayPackage` | boolean | no |  |


### `ContractGolfDetailedVO` {#schema-contractgolfdetailedvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `city` | string | no |  |
| `code` | string | no |  |
| `currency` | [`Currency`](#schema-currency) | no |  |
| `datasheets` | object (inline) | no |  |
| `geolocation` | [`GeolocationVO`](#schema-geolocationvo) | no |  |
| `imageUrls` | array[string] | no |  |
| `maxPlayers` | integer(int32) | no |  |
| `minPlayers` | integer(int32) | no |  |
| `name` | string | no |  |
| `onlyHolidayPackage` | boolean | no |  |
| `rates` | array[[`ContractGolfRateVO`](#schema-contractgolfratevo)] | no |  |
| `seasons` | array[[`ContractGolfSeasonVO`](#schema-contractgolfseasonvo)] | no |  |
| `teeTimes` | array[[`ContractGolfTeeTimeVO`](#schema-contractgolfteetimevo)] | no |  |


### `ContractGolfRateVO` {#schema-contractgolfratevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | no |  |
| `id` | integer(int32) | no |  |
| `onRequest` | boolean | no |  |
| `onRequestRemarks` | object (inline) | no |  |
| `remarks` | object (inline) | no |  |


### `ContractGolfSeasonVO` {#schema-contractgolfseasonvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `endDate` | string(date) | no |  |
| `id` | integer(int32) | no |  |
| `name` | string | no |  |
| `operationDays` | array[[`DayOfWeek`](#schema-dayofweek)] | no |  |
| `startDate` | string(date) | no |  |


### `ContractGolfTeeTimeVO` {#schema-contractgolfteetimevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `basePrice` | number(double) | no |  |
| `currency` | [`Currency`](#schema-currency) | no |  |
| `daySupplement` | object (inline) | no |  |
| `excludeFrom` | string(date) | no |  |
| `excludeTo` | string(date) | no |  |
| `rateId` | integer(int32) | no |  |
| `seasonId` | integer(int32) | no |  |
| `startTime` | string(time-local) | no |  |
| `supplementCurrency` | string | no |  |
| `teeTimeId` | integer(int32) | no |  |


### `ContractHotelBasicVO` {#schema-contracthotelbasicvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `address` | [`HotelAddressVO`](#schema-hoteladdressvo) | yes |  |
| `category` | string | yes |  |
| `chain` | string | no |  |
| `contractId` | string | no |  |
| `currency` | [`Currency`](#schema-currency) | yes |  |
| `hotelname` | string | yes |  |
| `latitude` | number(double) | no |  |
| `longitude` | number(double) | no |  |
| `maximumStay` | integer(int32) | no |  |
| `mealPlans` | array[string] | no |  |
| `minimumStay` | integer(int32) | no |  |
| `providerCode` | string | yes |  |
| `releaseDays` | integer(int32) | yes |  |
| `rooms` | array[string] | no |  |


### `ContractHotelCompletedVO` {#schema-contracthotelcompletedvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `address` | [`HotelAddressVO`](#schema-hoteladdressvo) | yes |  |
| `category` | string | yes |  |
| `chain` | string | no |  |
| `contractId` | string | no |  |
| `currency` | [`Currency`](#schema-currency) | yes |  |
| `descriptions` | array[[`TranslationVO`](#schema-translationvo)] | yes |  |
| `facilities` | array[string] | no |  |
| `hotelname` | string | yes |  |
| `images` | array[string] | yes |  |
| `infantsAllowed` | integer(int32) | no |  |
| `latitude` | number(double) | no |  |
| `longitude` | number(double) | no |  |
| `maximumChildrenAge` | integer(int32) | no |  |
| `maximumStay` | integer(int32) | no |  |
| `mealPlans` | array[[`ContractMealPlanVO`](#schema-contractmealplanvo)] | yes |  |
| `minimumChildrenAge` | integer(int32) | no |  |
| `minimumStay` | integer(int32) | no |  |
| `offers` | array[[`ContractHotelOffersVO`](#schema-contracthoteloffersvo)] | no |  |
| `providerCode` | string | yes |  |
| `rates` | array[[`ContractHotelRateVO`](#schema-contracthotelratevo)] | no |  |
| `releaseDays` | integer(int32) | yes |  |
| `rooms` | array[[`ContractRoomVO`](#schema-contractroomvo)] | yes |  |
| `supplements` | array[[`ContractHotelSupplementVO`](#schema-contracthotelsupplementvo)] | no |  |
| `voucherRemarks` | array[[`TranslationVO`](#schema-translationvo)] | no |  |


### `ContractHotelDetailedVO` {#schema-contracthoteldetailedvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `address` | [`HotelAddressVO`](#schema-hoteladdressvo) | yes |  |
| `category` | string | yes |  |
| `chain` | string | no |  |
| `currency` | [`Currency`](#schema-currency) | yes |  |
| `descriptions` | array[[`TranslationVO`](#schema-translationvo)] | yes |  |
| `hotelname` | string | yes |  |
| `images` | array[string] | yes |  |
| `infantsAllowed` | integer(int32) | no |  |
| `latitude` | number(double) | no |  |
| `longitude` | number(double) | no |  |
| `maximumChildrenAge` | integer(int32) | no |  |
| `maximumStay` | integer(int32) | no |  |
| `mealPlans` | array[[`ContractMealPlanVO`](#schema-contractmealplanvo)] | yes |  |
| `minimumChildrenAge` | integer(int32) | no |  |
| `minimumStay` | integer(int32) | no |  |
| `providerCode` | string | yes |  |
| `releaseDays` | integer(int32) | yes |  |
| `rooms` | array[[`ContractRoomVO`](#schema-contractroomvo)] | yes |  |
| `voucherRemarks` | array[[`TranslationVO`](#schema-translationvo)] | no |  |


### `ContractHotelOffersVO` {#schema-contracthoteloffersvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `apply` | [`HotelContractApplyType`](#schema-hotelcontractapplytype) | no |  |
| `bookingWindows` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no |  |
| `childValue` | number(double) | no |  |
| `maximumAdults` | integer(int32) | no |  |
| `maximumChildrens` | integer(int32) | no |  |
| `maximumStay` | integer(int32) | no |  |
| `mealPlans` | array[string] | yes |  |
| `minimumAdults` | integer(int32) | no |  |
| `minimumChildrens` | integer(int32) | no |  |
| `minimumStay` | integer(int32) | no |  |
| `names` | array[[`TranslationVO`](#schema-translationvo)] | yes |  |
| `operationalDays` | array[[`DayOfWeek`](#schema-dayofweek)] | no |  |
| `pay` | integer(int32) | no |  |
| `providerCode` | string | no |  |
| `providerRoomCodes` | array[string] | yes |  |
| `releaseDays` | integer(int32) | yes |  |
| `stay` | integer(int32) | no |  |
| `travelWindows` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no |  |
| `type` | [`HotelContractOfferType`](#schema-hotelcontractoffertype) | no |  |
| `value` | number(double) | no |  |


### `ContractHotelRateVO` {#schema-contracthotelratevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `bookingWindows` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no |  |
| `id` | integer(int32) | no |  |
| `maximumStay` | integer(int32) | no |  |
| `minimumStay` | integer(int32) | no |  |
| `name` | string | no |  |
| `offers` | array[string] | no |  |
| `releaseDays` | integer(int32) | yes |  |
| `seasons` | array[[`ContractHotelSeasonVO`](#schema-contracthotelseasonvo)] | no |  |
| `stopSales` | array[[`ContractHotelRoomStopSalesVO`](#schema-contracthotelroomstopsalesvo)] | no |  |
| `supplements` | array[string] | no |  |


### `ContractHotelRoomStopSalesVO` {#schema-contracthotelroomstopsalesvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `roomId` | integer(int32) | no |  |
| `roomName` | string | no |  |
| `stopSales` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no |  |


### `ContractHotelSeasonPricesVO` {#schema-contracthotelseasonpricesvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `adultPrices` | array[number(double)] | no |  |
| `basePrice` | number(double) | no |  |
| `childPrices` | array[number(double)] | no |  |
| `distributionPrices` | array[[`ContractRoomDistributionPriceVO`](#schema-contractroomdistributionpricevo)] | no |  |
| `providerRoomCode` | string | yes |  |
| `unitsOnRequest` | integer(int32) | no |  |
| `unitsQuota` | integer(int32) | yes |  |


### `ContractHotelSeasonVO` {#schema-contracthotelseasonvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `checkinDays` | array[[`DayOfWeek`](#schema-dayofweek)] | no |  |
| `checkoutDays` | array[[`DayOfWeek`](#schema-dayofweek)] | no |  |
| `dateRanges` | array[[`LocalDateRangeAndOperationDaysVO`](#schema-localdaterangeandoperationdaysvo)] | no |  |
| `id` | integer(int32) | no |  |
| `maximumStay` | integer(int32) | no |  |
| `mealPlans` | array[[`ContractMealPlanVO`](#schema-contractmealplanvo)] | no |  |
| `minimumStay` | integer(int32) | no |  |
| `name` | string | no |  |
| `priceType` | [`HotelContractPriceType`](#schema-hotelcontractpricetype) | no |  |
| `releaseDays` | integer(int32) | yes |  |
| `seasonRoomPrices` | array[[`ContractHotelSeasonPricesVO`](#schema-contracthotelseasonpricesvo)] | no |  |


### `ContractHotelSupplementVO` {#schema-contracthotelsupplementvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `apply` | [`HotelContractApplyType`](#schema-hotelcontractapplytype) | no |  |
| `bookingWindows` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no |  |
| `childValue` | number(double) | no |  |
| `maximumAdults` | integer(int32) | no |  |
| `maximumChildrens` | integer(int32) | no |  |
| `maximumStay` | integer(int32) | no |  |
| `mealPlans` | array[string] | yes |  |
| `minimumAdults` | integer(int32) | no |  |
| `minimumChildrens` | integer(int32) | no |  |
| `minimumStay` | integer(int32) | no |  |
| `names` | array[[`TranslationVO`](#schema-translationvo)] | no |  |
| `operationalDays` | array[[`DayOfWeek`](#schema-dayofweek)] | no |  |
| `providerCode` | string | no |  |
| `providerRoomCodes` | array[string] | yes |  |
| `releaseDays` | integer(int32) | yes |  |
| `travelWindows` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no |  |
| `type` | [`HotelContractSupplementType`](#schema-hotelcontractsupplementtype) | no |  |
| `value` | number(double) | no |  |


### `ContractMealPlanVO` {#schema-contractmealplanvo}

_Select a meal plan using mealPlanId or the legacy mealPlan category. If both are supplied, mealPlanId takes precedence and mealPlan is ignored. The legacy mealPlan field is scheduled for deprecation on 2026-01-31; use mealPlanId for new integrations. Before using mealPlanId, call GET /mealplan/{micrositeId}, at Web content - Accommodations, to retrieve the available meal plans and use an ID returned by that endpoint. The ID-based request does not require a meal plan category. Optional mealplanDescription entries provide contract or season names by language; supplied languages are added or updated and other languages are preserved. Prices apply to the selected meal plan only._

| Field | Type | Required | Description |
|---|---|---|---|
| `adultPrices` | array[number(double)] | no |  |
| `basePrice` | number(double) | no |  |
| `childPrices` | array[number(double)] | no |  |
| `mealPlan` | [`MealPlanType`](#schema-mealplantype) | no |  |
| `mealPlanId` | string | no |  |
| `mealplanDescription` | array[[`TranslationVO`](#schema-translationvo)] | no |  |


### `ContractRoomDistributionPriceVO` {#schema-contractroomdistributionpricevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `adults` | integer(int32) | no |  |
| `amount` | number(double) | no |  |
| `children` | integer(int32) | no |  |


### `ContractRoomDistributionVO` {#schema-contractroomdistributionvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `adults` | integer(int32) | yes |  |
| `children` | integer(int32) | yes |  |


### `ContractRoomVO` {#schema-contractroomvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `distributions` | array[[`ContractRoomDistributionVO`](#schema-contractroomdistributionvo)] | yes |  |
| `name` | string | no |  |
| `providerCode` | string | no |  |
| `typeId` | string | no |  |


### `ContractSupplierVO` {#schema-contractsuppliervo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `address` | [`AddressVO`](#schema-addressvo) | no |  |
| `commercialName` | string | no |  |
| `commercialNumber` | string | no |  |
| `contact` | [`PersonNameVO`](#schema-personnamevo) | no |  |
| `country` | [`CountryVO`](#schema-countryvo) | no |  |
| `denySupplierToBookingVoucher` | boolean | no |  |
| `email` | string | no |  |
| `emergencyNumber` | string | no |  |
| `externalCode` | string | no |  |
| `fromEmail` | string | no |  |
| `hotelExclusive` | boolean | no |  |
| `id` | integer(int32) | no |  |
| `lastName` | string | no |  |
| `legalName` | string | no |  |
| `mandatoryContactPhone` | boolean | no |  |
| `mandatoryEmergencyContact` | boolean | no |  |
| `mandatoryPassengerBithdate` | boolean | no |  |
| `mandatoryPassengerDocument` | boolean | no |  |
| `mandatoryPassengerDocumentExpiryDate` | boolean | no |  |
| `mobile` | string | no |  |
| `notes` | string | no |  |
| `phoneNumber` | string | no |  |
| `remarks` | string | no |  |
| `replyEmail` | string | no |  |
| `sendAccommodationDetails` | boolean | no |  |
| `sendPassengerEmail` | boolean | no |  |
| `supplierUsername` | string | no |  |
| `taxpayerId` | string | no |  |


### `ContractTicketCancellationRangesVO` {#schema-contractticketcancellationrangesvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `cancellationDays` | integer(int32) | no |  |
| `cancellationPercentage` | number(double) | no |  |


### `ContractTicketMeetingPointVO` {#schema-contractticketmeetingpointvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | no |  |
| `latitude` | number(double) | no |  |
| `longitude` | number(double) | no |  |


### `ContractTicketModalityVO` {#schema-contractticketmodalityvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `baseAdultPrice` | number(double) | no |  |
| `baseChildrenPrice` | number(double) | no |  |
| `baseInfantPrice` | number(double) | no |  |
| `baseServicePrice` | number(double) | no |  |
| `childAgeMax` | integer(int32) | no |  |
| `childAgeMin` | integer(int32) | no |  |
| `code` | string | no |  |
| `disallowAdult` | boolean | no |  |
| `disallowChildren` | boolean | no |  |
| `disallowInfant` | boolean | no |  |
| `duration` | number(double) | no |  |
| `durationType` | [`DurationType`](#schema-durationtype) | no |  |
| `endDate` | string(date) | no |  |
| `languages` | array[[`Language`](#schema-language)] | no |  |
| `maxPassengers` | integer(int32) | no |  |
| `minPassengers` | integer(int32) | no |  |
| `occupancyPrices` | array[[`ContractTicketOccupancyPriceVO`](#schema-contractticketoccupancypricevo)] | no |  |
| `onRequest` | boolean | no |  |
| `onRequestRemarks` | object (inline) | no |  |
| `operationalDays` | array[[`DayOfWeek`](#schema-dayofweek)] | no |  |
| `priceType` | [`TicketContractPriceType`](#schema-ticketcontractpricetype) | no |  |
| `remarks` | object (inline) | no |  |
| `startDate` | string(date) | no |  |
| `stopSales` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no |  |
| `supplements` | array[[`ContractTicketOptionSupplementVO`](#schema-contractticketoptionsupplementvo)] | no |  |
| `ticketsPerDay` | integer(int32) | no |  |
| `timeTables` | array[string(time-local)] | no |  |


### `ContractTicketOccupancyPriceVO` {#schema-contractticketoccupancypricevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `ageRange` | [`AgeRangeVO`](#schema-agerangevo) | no |  |
| `amount` | number(double) | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `id` | integer(int32) | no |  |
| `occupancy` | integer(int32) | no |  |


### `ContractTicketOptionSupplementVO` {#schema-contractticketoptionsupplementvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `adultPriceSupplement` | number(double) | no |  |
| `childrenPriceSupplement` | number(double) | no |  |
| `endDate` | string(date) | no |  |
| `infantPriceSupplement` | number(double) | no |  |
| `startDate` | string(date) | no |  |
| `translations` | object (inline) | no |  |


### `ContractTicketRS` {#schema-contractticketrs}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `pagination` | [`PaginationVO`](#schema-paginationvo) | no |  |
| `tickets` | array[[`ContractTicketVO`](#schema-contractticketvo)] | no |  |


### `ContractTicketVO` {#schema-contractticketvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `adultTaxesAmount` | number(double) | no |  |
| `cancellationRanges` | array[[`ContractTicketCancellationRangesVO`](#schema-contractticketcancellationrangesvo)] | no |  |
| `childTaxesAmount` | number(double) | no |  |
| `city` | string | no |  |
| `code` | string | yes |  |
| `currency` | [`Currency`](#schema-currency) | no |  |
| `datasheets` | object (inline) | yes |  |
| `daysAvailableBeforeRelease` | integer(int32) | no |  |
| `duration` | number(double) | no |  |
| `durationType` | [`DurationType`](#schema-durationtype) | no |  |
| `geolocation` | [`GeolocationVO`](#schema-geolocationvo) | yes |  |
| `imageUrls` | array[string] | no |  |
| `infantTaxesAmount` | number(double) | no |  |
| `locationOrigin` | boolean | no |  |
| `meetingPointOrigin` | boolean | no |  |
| `meetingPoints` | array[[`ContractTicketMeetingPointVO`](#schema-contractticketmeetingpointvo)] | no |  |
| `modalityCodes` | array[string] | no |  |
| `name` | string | yes |  |
| `productTypes` | array[[`TripType`](#schema-triptype)] | no |  |
| `releaseTime` | integer(int32) | no |  |
| `releaseTimeType` | [`DurationType`](#schema-durationtype) | no |  |
| `zipCode` | string | no |  |


### `ContractTransferAdditionalServiceVO` {#schema-contracttransferadditionalservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `currency` | [`Currency`](#schema-currency) | no |  |
| `maximum` | integer(int32) | no |  |
| `price` | number(double) | no |  |
| `translations` | object (inline) | no |  |


### `ContractTransferLocationVO` {#schema-contracttransferlocationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `bannedHotels` | array[integer(int32)] | no |  |
| `geolocation` | [`GeolocationVO`](#schema-geolocationvo) | no |  |
| `includedHotels` | array[integer(int32)] | no |  |
| `name` | string | yes |  |
| `zoneRadius` | number(double) | no |  |


### `ContractTransferOperationalDays` {#schema-contracttransferoperationaldays}

| Field | Type | Required | Description |
|---|---|---|---|
| `fromHour` | string(time-local) | no |  |
| `operationalDays` | [`DayOfWeek`](#schema-dayofweek) | yes |  |
| `toHour` | string(time-local) | no |  |


### `ContractTransferPropertyVO` {#schema-contracttransferpropertyvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `propertyType` | [`TransferPropertyType`](#schema-transferpropertytype) | no |  |
| `translations` | object (inline) | no |  |


### `ContractTransferStopSaleVO` {#schema-contracttransferstopsalevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `endDate` | string | no |  |
| `startDate` | string | no |  |


### `ContractTransferSupplementVO` {#schema-contracttransfersupplementvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `amount` | number(double) | no |  |
| `endDate` | string | no |  |
| `endTime` | string | no |  |
| `name` | string | no |  |
| `startDate` | string | no |  |
| `startTime` | string | no |  |
| `type` | string | no |  |


### `ContractTransferVO` {#schema-contracttransfervo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `additionalServices` | array[[`ContractTransferAdditionalServiceVO`](#schema-contracttransferadditionalservicevo)] | no |  |
| `allowMultipleVehicles` | boolean | no |  |
| `arrival` | [`ContractTransferLocationVO`](#schema-contracttransferlocationvo) | no |  |
| `arrivalLoaded` | boolean | no |  |
| `arrivalLocationId` | integer(int32) | no |  |
| `basePrice` | number(double) | no |  |
| `currency` | [`Currency`](#schema-currency) | no |  |
| `datasheets` | object (inline) | no |  |
| `departure` | [`ContractTransferLocationVO`](#schema-contracttransferlocationvo) | no |  |
| `departureLoaded` | boolean | no |  |
| `departureLocationId` | integer(int32) | no |  |
| `endDate` | string(date) | no |  |
| `id` | string | no |  |
| `images` | array[string] | no |  |
| `maxOccupancy` | integer(int32) | no |  |
| `maxVehicles` | integer(int32) | no |  |
| `minOccupancy` | integer(int32) | no |  |
| `name` | string | no |  |
| `operationalDaysWithHours` | array[[`ContractTransferOperationalDays`](#schema-contracttransferoperationaldays)] | no |  |
| `pickupInformation` | string | no |  |
| `priceByPax` | boolean | no |  |
| `pricesByOccupancy` | array[[`TransferContractPrice`](#schema-transfercontractprice)] | no |  |
| `productType` | [`TransferProductType`](#schema-transferproducttype) | no |  |
| `properties` | array[[`ContractTransferPropertyVO`](#schema-contracttransferpropertyvo)] | no |  |
| `releaseContract` | integer(int32) | no |  |
| `serviceType` | [`TransferServiceType`](#schema-transferservicetype) | no |  |
| `startDate` | string(date) | no |  |
| `stopSales` | array[[`ContractTransferStopSaleVO`](#schema-contracttransferstopsalevo)] | no |  |
| `supplements` | array[[`ContractTransferSupplementVO`](#schema-contracttransfersupplementvo)] | no |  |
| `transferToHotel` | boolean | no |  |
| `vehicleType` | [`TransferVehicleType`](#schema-transfervehicletype) | no |  |


### `ContractTransferZoneVO` {#schema-contracttransferzonevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | no |  |
| `geolocation` | [`GeolocationVO`](#schema-geolocationvo) | no |  |
| `id` | integer(int32) | no |  |
| `name` | string | no |  |
| `terminal` | string | no |  |
| `zoneRadius` | number(double) | no |  |
| `zoneType` | [`TransferZoneType`](#schema-transferzonetype) | no |  |


### `ContractTransportCancellationRangeVO` {#schema-contracttransportcancellationrangevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `days` | integer(int32) | no |  |
| `isBeforeStart` | boolean | no |  |
| `percentage` | number(double) | no |  |


### `ContractTransportOptionInventoryVO` {#schema-contracttransportoptioninventoryvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `inventoryDate` | [`LocalDateRangeVO`](#schema-localdaterangevo) | no |  |
| `quantity` | integer(int32) | no |  |


### `ContractTransportOptionPriceVO` {#schema-contracttransportoptionpricevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `adultPriceSupplement` | number(double) | no |  |
| `adultRTPriceSupplement` | number(double) | no |  |
| `childrenPriceSupplement` | number(double) | no |  |
| `childrenRTPriceSupplement` | number(double) | no |  |
| `endDate` | string(date) | no |  |
| `infantPriceSupplement` | number(double) | no |  |
| `infantRTPriceSupplement` | number(double) | no |  |
| `name` | string | no |  |
| `startDate` | string(date) | no |  |


### `ContractTransportOptionVO` {#schema-contracttransportoptionvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `agencyId` | string | no |  |
| `baggageAllowance` | string | no |  |
| `baggageAllowanceType` | [`BaggageAllowanceType`](#schema-baggageallowancetype) | no |  |
| `cabinClassType` | [`CabinClassType`](#schema-cabinclasstype) | yes |  |
| `code` | string | yes |  |
| `inventories` | array[[`ContractTransportOptionInventoryVO`](#schema-contracttransportoptioninventoryvo)] | no |  |
| `maxPassengers` | integer(int32) | no |  |
| `minPassengers` | integer(int32) | no |  |
| `onRequest` | boolean | no |  |
| `prices` | array[[`ContractTransportOptionPriceVO`](#schema-contracttransportoptionpricevo)] | no |  |
| `translations` | object (inline) | no |  |


### `ContractTransportSegmentVO` {#schema-contracttransportsegmentvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `arrivalLocationCode` | string | yes |  |
| `arrivalTime` | string(time-local) | yes |  |
| `departureLocationCode` | string | yes |  |
| `departureTime` | string(time-local) | yes |  |
| `durationTime` | string(time-local) | no |  |
| `model` | string | no |  |
| `numService` | string | no |  |
| `plusDays` | integer(int32) | no |  |


### `ContractTransportVO` {#schema-contracttransportvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `adultRTTaxesAmount` | number(double) | no |  |
| `adultTaxesAmount` | number(double) | no |  |
| `airlineCode` | string | yes |  |
| `allowOWPrice` | boolean | no |  |
| `allowRTPrice` | boolean | no |  |
| `baseAdultPrice` | number(double) | no |  |
| `baseAdultRTPrice` | number(double) | no |  |
| `baseChildrenPrice` | number(double) | no |  |
| `baseChildrenRTPrice` | number(double) | no |  |
| `baseInfantPrice` | number(double) | no |  |
| `baseInfantRTPrice` | number(double) | no |  |
| `cancellationRanges` | array[[`ContractTransportCancellationRangeVO`](#schema-contracttransportcancellationrangevo)] | no |  |
| `childrenRTTaxesAmount` | number(double) | no |  |
| `childrenTaxesAmount` | number(double) | no |  |
| `combinableAsInboundRTPrice` | boolean | no |  |
| `combinableRtContracts` | array[string] | no |  |
| `companyName` | string | no |  |
| `currency` | [`Currency`](#schema-currency) | no |  |
| `datasheets` | object (inline) | no |  |
| `endDate` | string(date) | no |  |
| `id` | string | no |  |
| `images` | array[string] | no |  |
| `infantRTTaxesAmount` | number(double) | no |  |
| `infantTaxesAmount` | number(double) | no |  |
| `maxChildAge` | integer(int32) | no |  |
| `maxInfantAge` | integer(int32) | no |  |
| `maxStayNights` | integer(int32) | no |  |
| `minChildAge` | integer(int32) | no |  |
| `minInfantAge` | integer(int32) | no |  |
| `minStayNights` | integer(int32) | no |  |
| `name` | string | yes |  |
| `onlyHolidayPackage` | boolean | no |  |
| `operationalDays` | array[[`DayOfWeek`](#schema-dayofweek)] | no |  |
| `optionCodes` | array[string] | no |  |
| `pricePerPax` | boolean | no |  |
| `productTypes` | array[[`TripType`](#schema-triptype)] | no |  |
| `releaseContract` | integer(int32) | no |  |
| `segments` | array[[`ContractTransportSegmentVO`](#schema-contracttransportsegmentvo)] | yes |  |
| `showInTransportQuotasLanding` | boolean | no |  |
| `startDate` | string(date) | no |  |
| `transportType` | [`TransportType`](#schema-transporttype) | yes |  |
| `vehiclePrice` | number(double) | no |  |


### `CountryTaxesVO` {#schema-countrytaxesvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amount` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `name` | string | no |  |


### `CountryVO` {#schema-countryvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | no |  |
| `name` | string | no |  |


### `CourtesyTitle` {#schema-courtesytitle}

Enum values: 'MISTER', 'MRS', 'MS'


### `CruiseDataSheetVO` {#schema-cruisedatasheetvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `arrival` | string | no |  |
| `cabin` | string | no |  |
| `cruiseLine` | string | no |  |
| `departure` | string | no |  |
| `fixed` | boolean | no |  |
| `group` | string | no |  |
| `id` | string | no |  |
| `mandatory` | boolean | no |  |
| `month` | string | no |  |
| `nights` | integer(int32) | no |  |
| `originPort` | string | no |  |
| `region` | string | no |  |
| `selectedCategory` | string | no |  |
| `shipId` | string | no |  |
| `shipName` | string | no |  |
| `stars` | integer(int32) | no |  |
| `year` | string | no |  |


### `CruiseDepartureVO` {#schema-cruisedeparturevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `date` | string(date) | no |  |
| `id` | string | no |  |
| `prices` | array[[`CruiseMicrositePriceVO`](#schema-cruisemicrositepricevo)] | no |  |


### `CruiseItineraryStepVO` {#schema-cruiseitinerarystepvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `arrival` | string | no |  |
| `day` | integer(int32) | no |  |
| `departure` | string | no |  |
| `destination` | string | no |  |


### `CruiseItineraryVO` {#schema-cruiseitineraryvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | no |  |
| `itinerarySteps` | array[[`CruiseItineraryStepVO`](#schema-cruiseitinerarystepvo)] | no |  |
| `shipImage` | string | no |  |


### `CruiseLineBenefitVO` {#schema-cruiselinebenefitvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | no |  |
| `name` | string | no |  |


### `CruiseLineCharacteristicsVO` {#schema-cruiselinecharacteristicsvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `benefits` | array[[`CruiseLineBenefitVO`](#schema-cruiselinebenefitvo)] | no |  |
| `excluded` | array[string] | no |  |
| `id` | string | no |  |
| `included` | array[string] | no |  |


### `CruiseMicrositePriceVO` {#schema-cruisemicrositepricevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `group` | string | no |  |
| `price` | [`Money`](#schema-money) | no |  |


### `CruisePortElementVO` {#schema-cruiseportelementvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `destination` | string | no |  |
| `id` | string | no |  |
| `latitude` | number(double) | no |  |
| `longitude` | number(double) | no |  |


### `CruisePortVO` {#schema-cruiseportvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | no |  |
| `destination` | string | no |  |
| `id` | string | no |  |
| `imageUrl` | string | no |  |
| `latitude` | number(double) | no |  |
| `longitude` | number(double) | no |  |
| `name` | string | no |  |


### `CruiseShipCategoryVO` {#schema-cruiseshipcategoryvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | no |  |
| `group` | string | no |  |
| `id` | string | no |  |
| `imageUrl` | string | no |  |
| `name` | string | no |  |


### `CruiseShipDeckVO` {#schema-cruiseshipdeckvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | no |  |
| `imageUrl` | string | no |  |
| `name` | string | no |  |


### `CruiseShipElementVO` {#schema-cruiseshipelementvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `cruiseLine` | string | no |  |
| `id` | string | no |  |
| `name` | string | no |  |


### `CruiseShipVO` {#schema-cruiseshipvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `beam` | integer(int32) | no |  |
| `casino` | boolean | no |  |
| `categories` | array[[`CruiseShipCategoryVO`](#schema-cruiseshipcategoryvo)] | no |  |
| `chapel` | boolean | no |  |
| `childrenSwimmingPool` | boolean | no |  |
| `cinema` | boolean | no |  |
| `crew` | integer(int32) | no |  |
| `cruiseLine` | string | no |  |
| `decks` | array[[`CruiseShipDeckVO`](#schema-cruiseshipdeckvo)] | no |  |
| `decksNumber` | integer(int32) | no |  |
| `description` | string | no |  |
| `exteriorStateRooms` | integer(int32) | no |  |
| `gym` | boolean | no |  |
| `id` | string | no |  |
| `images` | array[string] | no |  |
| `inaugurated` | integer(int32) | no |  |
| `interiorStateRooms` | integer(int32) | no |  |
| `internet` | boolean | no |  |
| `laundry` | boolean | no |  |
| `length` | integer(int32) | no |  |
| `library` | boolean | no |  |
| `lounges` | integer(int32) | no |  |
| `mainImage` | string | no |  |
| `name` | string | no |  |
| `nightClub` | boolean | no |  |
| `nursery` | boolean | no |  |
| `occupancy` | integer(int32) | no |  |
| `renovated` | integer(int32) | no |  |
| `restaurants` | integer(int32) | no |  |
| `salon` | boolean | no |  |
| `service24h` | boolean | no |  |
| `shops` | boolean | no |  |
| `showRoom` | boolean | no |  |
| `spa` | boolean | no |  |
| `speed` | integer(int32) | no |  |
| `stars` | integer(int32) | no |  |
| `stateRooms` | integer(int32) | no |  |
| `swimmingPools` | integer(int32) | no |  |
| `theater` | boolean | no |  |
| `tonnage` | integer(int32) | no |  |
| `weddings` | boolean | no |  |


### `Currency` {#schema-currency}

Enum values: 'EUR', 'USD', 'GBP', 'BRL', 'MAD', 'MXN', 'AED', 'EGP', 'OMR', 'SAR', 'IRR', 'QAR', 'JOD', 'BHD', 'KWD', 'DKK', 'SEK', 'NOK', 'ISK', 'CHF', 'SGD', 'COP', 'AUD', 'RUB', 'NIO', 'CAD', 'HUF', 'PLN', 'THB', 'MYR', 'ZAR', 'NAD', 'PYG', 'CRC', 'INR', 'KRW', 'BGN', 'JPY', 'NZD', 'FJD', 'BWP', 'WST', 'XPF', 'TRY', 'CNY', 'ARS', 'BDT', 'CUP', 'HKD', 'IDR', 'LKR', 'MMK', 'PHP', 'PKR', 'VND', 'TWD', 'MZN', 'CLP', 'PEN', 'UYU', 'GTQ', 'BOB', 'HNL', 'SVC', 'HRK', 'CZK', 'MKD', 'RON', 'RSD', 'ALL', 'BYN', 'MOP', 'MDL', 'UAH', 'KES', 'DOP', 'AZN', 'TND', 'CDF', 'SSP', 'XAF', 'DZD', 'AMD', 'IQD', 'UZS', 'ILS', 'GEL', 'AFN', 'ANG', 'AOA', 'AWG', 'BAM', 'BBD', 'BIF', 'BMD', 'BND', 'BSD', 'BTN', 'BYR', 'BZD', 'CLF', 'CUC', 'CVE', 'DJF', 'ERN', 'ETB', 'FKP', 'GHS', 'GIP', 'GMD', 'GNF', 'GYD', 'HTG', 'JMD', 'KGS', 'KHR', 'KMF', 'KPW', 'KYD', 'KZT', 'LAK', 'LBP', 'LRD', 'LSL', 'LYD', 'MGA', 'MNT', 'MUR', 'MVR', 'MWK', 'NGN', 'NPR', 'PAB', 'PGK', 'RWF', 'SBD', 'MRO', 'MRU', 'SCR', 'SDG', 'SHP', 'SLL', 'SOS', 'SRD', 'STD', 'SYP', 'SZL', 'TJS', 'TMT', 'TOP', 'TTD', 'TZS', 'UGX', 'VEF', 'VUV', 'XAG', 'XAU', 'XCD', 'XDR', 'XOF', 'XPD', 'XPT', 'YER', 'ZMK', 'ZMW', 'ZWL', 'VES'


### `DayOfWeek` {#schema-dayofweek}

Enum values: 'MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY', 'SUNDAY'


### `DeleteResponse` {#schema-deleteresponse}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `message` | string | no |  |
| `status` | [`Status`](#schema-status) | no |  |


### `DepartureCalendarVO` {#schema-departurecalendarvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `date` | string(date) | no |  |
| `originCode` | string | no |  |
| `price` | number(double) | no |  |


### `DeparturePricesVO` {#schema-departurepricesvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `currency` | string | no |  |
| `holidayPackagesDates` | array[[`DepartureCalendarVO`](#schema-departurecalendarvo)] | no |  |


### `DestinationCommonVO` {#schema-destinationcommonvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `code` | string | no |  |
| `country` | string | no |  |
| `geolocation` | [`GeolocationVO`](#schema-geolocationvo) | no |  |
| `iata` | string | no |  |
| `images` | array[string] | no |  |
| `name` | string | no |  |
| `provincePostalPrefix` | string | no |  |


### `DestinationVO` {#schema-destinationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `code` | string | no |  |
| `country` | string | no |  |
| `description` | string | no |  |
| `geolocation` | [`GeolocationVO`](#schema-geolocationvo) | no |  |
| `iata` | string | no |  |
| `images` | array[string] | no |  |
| `name` | string | no |  |
| `provincePostalPrefix` | string | no |  |


### `DistributionVO` {#schema-distributionvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | no |  |
| `person` | array[[`PersonVO`](#schema-personvo)] | no |  |


### `DocumentType` {#schema-documenttype}

Enum values: 'IDENTITY_CARD', 'NIE', 'PASSPORT'


### `DriverLicenseRestrictionVO` {#schema-driverlicenserestrictionvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `driverLicenseAntiquity` | integer(int32) | no |  |
| `minAntiquity` | integer(int32) | no |  |


### `DurationType` {#schema-durationtype}

Enum values: 'DAYS', 'HOURS', 'MINUTES'


### `ETicketVO` {#schema-eticketvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `personId` | string | no |  |
| `ticketNumber` | string | no |  |


### `EmergencyContactVO` {#schema-emergencycontactvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `emergencyContactName` | string | no |  |
| `emergencyContactPhone` | string | no |  |


### `EticketType` {#schema-etickettype}

Enum values: 'PAX', 'BAGGAGE', 'SEAT'


### `ExpenseVO` {#schema-expensevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `balance` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `baseCost` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `borneByCompany` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `companyBorneCost` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `description` | string | no |  |
| `expenseCategory` | [`BusinessTravelExpenseCategory`](#schema-businesstravelexpensecategory) | no |  |
| `expenseDate` | string(date) | no |  |
| `expenseSubcategory` | [`BusinessTravelExpenseSubcategory`](#schema-businesstravelexpensesubcategory) | no |  |
| `externalId` | integer(int32) | no |  |
| `fixedCompanyBorneAmount` | boolean | no |  |
| `imageUrl` | string | no |  |
| `inPolicy` | boolean | no |  |
| `originalCost` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `paidByCompany` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `paidByEmployee` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `paymentMethod` | [`BusinessTravelPaymentMethod`](#schema-businesstravelpaymentmethod) | no |  |
| `reference` | string | no |  |
| `reportReference` | string | no |  |
| `requestedCompanyBorneAmount` | boolean | no |  |
| `requestedCompanyBorneCost` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `reviewed` | boolean | no |  |
| `serviceDate` | string(date) | no |  |


### `ExpensesResponseVO` {#schema-expensesresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `advances` | array[[`AdvanceVO`](#schema-advancevo)] | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `bookedServices` | array[[`BookedServiceVO`](#schema-bookedservicevo)] | no |  |
| `expenses` | array[[`ExpenseVO`](#schema-expensevo)] | no |  |
| `settlements` | array[[`SettlementVO`](#schema-settlementvo)] | no |  |


### `FareType` {#schema-faretype}

Enum values: 'PUBLIC', 'PRIVATE', 'NEGOTIATED', 'NEGOSPACE', 'TOUR_OPERATOR', 'LOWCOST', 'MIXED'


### `FeeAmountVO` {#schema-feeamountvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amount` | number(double) | no |  |
| `currency` | string | no |  |
| `perPassenger` | boolean | no |  |


### `FeeVO` {#schema-feevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `cruiseFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `flightHotelGroundServicesFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `flightHotelHotelFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `flightHotelInsuranceFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `flightHotelTransportFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `holidayPackageCruiseFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `holidayPackageGroundServicesFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `holidayPackageHotelFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `holidayPackageInsuranceFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `holidayPackageTourFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `holidayPackageTransportFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `magicBoxGroundServicesFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `magicBoxHotelFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `magicBoxInsuranceFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `magicBoxTransportFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `multidestinationGroundServicesFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `multidestinationHotelFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `multidestinationInsuranceFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `multidestinationTourFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `multidestinationTransportFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `onlyCarGroundServicesFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `onlyCarInsuranceFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `onlyFlightGroundFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `onlyFlightInsuranceFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `onlyFlightTransportFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `onlyHotelFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `onlyHotelGroundFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `onlyHotelInsuranceFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `onlyItemFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `onlyTicketFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `onlyTicketInsuranceFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `privateTourGroundServicesFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `privateTourHotelFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `privateTourInsuranceFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `privateTourTransportFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `routingGroundServicesFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `routingHotelFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `routingInsuranceFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `routingTransportFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `tripPlannerCruiseFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `tripPlannerHotelFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `tripPlannerTourFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `tripplannerGroundServicesFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `tripplannerInsuranceFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `tripplannerTransportFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |


### `FuelType` {#schema-fueltype}

Enum values: 'PETROL', 'DIESEL', 'HYBRID', 'ELECTRIC', 'UNSPECIFIED', 'COMPRESSED_GAS', 'HYDROGEN', 'MULTI_FUEL', 'ETHANOL'


### `GeolocationVO` {#schema-geolocationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `latitude` | number(double) | no |  |
| `longitude` | number(double) | no |  |


### `GeolocationWithNameVO` {#schema-geolocationwithnamevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `latitude` | number(double) | no |  |
| `longitude` | number(double) | no |  |
| `name` | string | no |  |


### `GetAgencyRS` {#schema-getagencyrs}

| Field | Type | Required | Description |
|---|---|---|---|
| `agency` | array[[`AgencyVO`](#schema-agencyvo)] | no |  |
| `pagination` | [`PaginationVO`](#schema-paginationvo) | no |  |


### `GetAirlineRS` {#schema-getairliners}

| Field | Type | Required | Description |
|---|---|---|---|
| `airline` | array[[`AirlineVO`](#schema-airlinevo)] | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |


### `GetAuthenticationRS` {#schema-getauthenticationrs}

| Field | Type | Required | Description |
|---|---|---|---|
| `expirationInSeconds` | integer(int64) | no |  |
| `token` | string | no |  |


### `GetBlogPostRS` {#schema-getblogpostrs}

| Field | Type | Required | Description |
|---|---|---|---|
| `language` | string | no |  |
| `micrositeId` | string | no |  |
| `post` | [`BlogPostVO`](#schema-blogpostvo) | no |  |


### `GetBlogRS` {#schema-getblogrs}

| Field | Type | Required | Description |
|---|---|---|---|
| `blog` | [`BlogVO`](#schema-blogvo) | no |  |
| `language` | string | no |  |
| `micrositeId` | string | no |  |
| `posts` | array[[`BlogPostSummaryVO`](#schema-blogpostsummaryvo)] | no |  |
| `totalPosts` | integer(int32) | no |  |


### `GetBookingRS` {#schema-getbookingrs}

| Field | Type | Required | Description |
|---|---|---|---|
| `bookedTrip` | array[[`BookedTripVO`](#schema-bookedtripvo)] | no |  |
| `pagination` | [`PaginationVO`](#schema-paginationvo) | no |  |


### `GetContractGolfRS` {#schema-getcontractgolfrs}

| Field | Type | Required | Description |
|---|---|---|---|
| `golfcontracts` | array[[`ContractGolfBaseVO`](#schema-contractgolfbasevo)] | no |  |
| `pagination` | [`PaginationVO`](#schema-paginationvo) | no |  |


### `GetContractHotelRS` {#schema-getcontracthotelrs}

| Field | Type | Required | Description |
|---|---|---|---|
| `hotel` | array[[`ContractHotelBasicVO`](#schema-contracthotelbasicvo)] | no |  |


### `GetContractTransferRS` {#schema-getcontracttransferrs}

| Field | Type | Required | Description |
|---|---|---|---|
| `transfer` | array[[`ContractTransferVO`](#schema-contracttransfervo)] | no |  |


### `GetContractTransportRS` {#schema-getcontracttransportrs}

| Field | Type | Required | Description |
|---|---|---|---|
| `transport` | array[[`ContractTransportVO`](#schema-contracttransportvo)] | no |  |


### `GetCountryRS` {#schema-getcountryrs}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `country` | array[[`CountryVO`](#schema-countryvo)] | no |  |


### `GetCruiseDataSheetRS` {#schema-getcruisedatasheetrs}

| Field | Type | Required | Description |
|---|---|---|---|
| `cruiseDataSheet` | array[[`CruiseDataSheetVO`](#schema-cruisedatasheetvo)] | no |  |


### `GetCruiseDepartureRS` {#schema-getcruisedeparturers}

| Field | Type | Required | Description |
|---|---|---|---|
| `departure` | array[[`CruiseDepartureVO`](#schema-cruisedeparturevo)] | no |  |


### `GetCruisePortRS` {#schema-getcruiseportrs}

| Field | Type | Required | Description |
|---|---|---|---|
| `cruisePort` | array[[`CruisePortElementVO`](#schema-cruiseportelementvo)] | no |  |


### `GetCruiseShipRS` {#schema-getcruiseshiprs}

| Field | Type | Required | Description |
|---|---|---|---|
| `cruiseShip` | array[[`CruiseShipElementVO`](#schema-cruiseshipelementvo)] | no |  |


### `GetDestinationRS` {#schema-getdestinationrs}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `destination` | array[[`DestinationCommonVO`](#schema-destinationcommonvo)] | no |  |


### `GetHolidayPackageRS` {#schema-getholidaypackagers}

| Field | Type | Required | Description |
|---|---|---|---|
| `package` | array[[`HolidayPackageVO`](#schema-holidaypackagevo)] | no |  |
| `pagination` | [`PaginationVO`](#schema-paginationvo) | no |  |


### `GetHotelRS` {#schema-gethotelrs}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `hotel` | array[[`IdeaHotelDataVO`](#schema-ideahoteldatavo)] | no |  |


### `GetIdeaRS` {#schema-getidears}

| Field | Type | Required | Description |
|---|---|---|---|
| `idea` | array[[`IdeaVO`](#schema-ideavo)] | no |  |
| `pagination` | [`PaginationVO`](#schema-paginationvo) | no |  |


### `GetMealPlanRS` {#schema-getmealplanrs}

| Field | Type | Required | Description |
|---|---|---|---|
| `mealplan` | array[[`MealPlanVO`](#schema-mealplanvo)] | no |  |


### `GetThemeRS` {#schema-getthemers}

| Field | Type | Required | Description |
|---|---|---|---|
| `theme` | array[[`ThemeVO`](#schema-themevo)] | no |  |


### `GetTicketRS` {#schema-getticketrs}

| Field | Type | Required | Description |
|---|---|---|---|
| `ticket` | array[[`TicketDataVO`](#schema-ticketdatavo)] | no |  |


### `GetTransferZoneRS` {#schema-gettransferzoners}

| Field | Type | Required | Description |
|---|---|---|---|
| `zone` | array[[`ContractTransferZoneVO`](#schema-contracttransferzonevo)] | no |  |


### `GetTransportBaseByCodeRS` {#schema-gettransportbasebycoders}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `code` | string | no |  |
| `country` | string | no |  |
| `geolocation` | [`GeolocationVO`](#schema-geolocationvo) | no |  |
| `transportBaseCityName` | [`TranslationVO`](#schema-translationvo) | no |  |
| `transportBaseName` | [`TranslationVO`](#schema-translationvo) | no |  |
| `type` | [`TransportBaseType`](#schema-transportbasetype) | no |  |


### `GetTransportBaseRS` {#schema-gettransportbasers}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `pagination` | [`PaginationVO`](#schema-paginationvo) | no |  |
| `transportbase` | array[[`TransportBaseVO`](#schema-transportbasevo)] | no |  |


### `GetUsersRS` {#schema-getusersrs}

| Field | Type | Required | Description |
|---|---|---|---|
| `pagination` | [`PaginationVO`](#schema-paginationvo) | no |  |
| `user` | array[[`UsersVO`](#schema-usersvo)] | no |  |


### `HolidayPackageVO` {#schema-holidaypackagevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `counters` | [`IdeaCountersVO`](#schema-ideacountersvo) | no |  |
| `creationDate` | string(date) | no |  |
| `customer` | [`IdeaCustomerVO`](#schema-ideacustomervo) | no |  |
| `dataSheetId` | string | no |  |
| `dateSettings` | [`TripDateSettingsVO`](#schema-tripdatesettingsvo) | no |  |
| `departureDate` | string(date) | no |  |
| `description` | string | no |  |
| `destinations` | array[[`IdeaPlaceVO`](#schema-ideaplacevo)] | no |  |
| `email` | string | no |  |
| `externalProvider` | string | no |  |
| `externalReference` | string | no |  |
| `id` | integer(int32) | no |  |
| `ideaUrl` | string | no |  |
| `imageUrl` | string | no |  |
| `itinerary` | array[[`IdeaPlaceVO`](#schema-ideaplacevo)] | no |  |
| `largeTitle` | string | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `metaDescription` | string | no |  |
| `metaTitle` | string | no |  |
| `order` | integer(int32) | no |  |
| `origin` | [`IdeaOriginVO`](#schema-ideaoriginvo) | no |  |
| `pricePerPerson` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `remarks` | string | no |  |
| `ribbonText` | string | no |  |
| `supplierName` | string | no |  |
| `themeDetails` | array[[`IdeaThemeVO`](#schema-ideathemevo)] | no |  |
| `themes` | array[string] | no |  |
| `title` | string | no |  |
| `totalPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `travelers` | array[[`IdeaTravelerVO`](#schema-ideatravelervo)] | no |  |
| `tripType` | [`TripType`](#schema-triptype) | no |  |
| `user` | string | no |  |
| `userB2c` | boolean | no |  |


### `HotelAddressVO` {#schema-hoteladdressvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `address` | string | yes |  |
| `country` | string | yes |  |
| `email` | string | no |  |
| `fax` | string | no |  |
| `locationName` | string | yes |  |
| `phone` | string | yes |  |
| `postalCode` | string | no |  |


### `HotelCategory` {#schema-hotelcategory}

Enum values: 'S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'L1', 'L2', 'L3', 'L4', 'L5', 'H1', 'H2', 'H3', 'H4', 'H5', 'IN'


### `HotelContractApplyType` {#schema-hotelcontractapplytype}

Enum values: 'LODGING', 'MEAL', 'LODGING_AND_MEAL', 'PER_NIGHT', 'PER_NIGHT_PERSON', 'PER_STAY', 'PER_STAY_PERSON'


### `HotelContractOfferType` {#schema-hotelcontractoffertype}

Enum values: 'PERCENT', 'ABSOLUTE', 'STAY_TO_PAY'


### `HotelContractPriceType` {#schema-hotelcontractpricetype}

Enum values: 'PAX', 'DISTRIBUTION'


### `HotelContractSupplementType` {#schema-hotelcontractsupplementtype}

Enum values: 'PERCENT', 'ABSOLUTE'


### `HotelDataVO` {#schema-hoteldatavo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accomodationType` | [`AccomodationType`](#schema-accomodationtype) | no |  |
| `address` | string | no |  |
| `phoneNumber` | string | no |  |
| `postalCode` | string | no |  |


### `HotelVO` {#schema-hotelvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `colorTemplate` | string | no |  |
| `destinationId` | string | no |  |
| `hotelId` | string | no |  |


### `IdeaCarRentalChargeRequestVO` {#schema-ideacarrentalchargerequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | integer(int64) | no |  |
| `money` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `providerCode` | string | no |  |
| `providerDescription` | string | no |  |


### `IdeaCarRentalVO` {#schema-ideacarrentalvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `bookingReference` | string | no |  |
| `charges` | array[[`IdeaCarRentalChargeRequestVO`](#schema-ideacarrentalchargerequestvo)] | no |  |
| `company` | string | no |  |
| `depositDescription` | string | no |  |
| `dropoffDate` | string(date) | no |  |
| `dropoffDay` | integer(int32) | no |  |
| `dropoffLocation` | string | no |  |
| `dropoffTime` | string | no |  |
| `fixed` | boolean | no |  |
| `fuelType` | [`FuelType`](#schema-fueltype) | no |  |
| `id` | string | no |  |
| `imageUrl` | string | no |  |
| `mandatory` | boolean | no |  |
| `mileage` | integer(int64) | no |  |
| `minimumAge` | integer(int32) | no |  |
| `pickupDate` | string(date) | no |  |
| `pickupDay` | integer(int32) | no |  |
| `pickupLocation` | string | no |  |
| `pickupTime` | string | no |  |
| `priceBreakdown` | [`PriceBreakdownVO`](#schema-pricebreakdownvo) | no |  |
| `product` | string | no |  |
| `transmissionType` | [`TransmissionType`](#schema-transmissiontype) | no |  |


### `IdeaClosedTourVO` {#schema-ideaclosedtourvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `address` | string | no |  |
| `bookingReference` | string | no |  |
| `dataSheetId` | string | no |  |
| `dayFrom` | integer(int32) | no |  |
| `dayTo` | integer(int32) | no |  |
| `description` | string | no |  |
| `endDate` | string(date-time) | no |  |
| `fixed` | boolean | no |  |
| `hotels` | string | no |  |
| `id` | string | no |  |
| `imageUrls` | array[string] | no |  |
| `includedServices` | string | no |  |
| `mandatory` | boolean | no |  |
| `modalityName` | string | no |  |
| `name` | string | no |  |
| `nonIncludedServices` | string | no |  |
| `priceBreakdown` | [`PriceBreakdownVO`](#schema-pricebreakdownvo) | no |  |
| `providerCode` | string | no |  |
| `startDate` | string(date-time) | no |  |
| `supplierId` | integer(int32) | no |  |
| `supplierName` | string | no |  |


### `IdeaCountersVO` {#schema-ideacountersvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `adults` | integer(int32) | no |  |
| `cars` | integer(int32) | no |  |
| `children` | integer(int32) | no |  |
| `closedTours` | integer(int32) | no |  |
| `cruises` | integer(int32) | no |  |
| `destinations` | integer(int32) | no |  |
| `hotelNights` | integer(int32) | no |  |
| `hotels` | integer(int32) | no |  |
| `insurances` | integer(int32) | no |  |
| `manuals` | integer(int32) | no |  |
| `tickets` | integer(int32) | no |  |
| `transfers` | integer(int32) | no |  |
| `transports` | integer(int32) | no |  |


### `IdeaCustomerVO` {#schema-ideacustomervo}

| Field | Type | Required | Description |
|---|---|---|---|
| `birthDate` | string(date) | no |  |
| `courtesyTitle` | [`CourtesyTitle`](#schema-courtesytitle) | no |  |
| `documentNumber` | string | no |  |
| `documentType` | [`DocumentType`](#schema-documenttype) | no |  |
| `email` | string | no |  |
| `lastName` | string | no |  |
| `name` | string | no |  |
| `phoneCountry` | [`CountryVO`](#schema-countryvo) | no |  |
| `phoneNumber` | string | no |  |


### `IdeaDayToDayVO` {#schema-ideadaytodayvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `cars` | array[[`IdeaCarRentalVO`](#schema-ideacarrentalvo)] | no |  |
| `closedTours` | array[[`IdeaClosedTourVO`](#schema-ideaclosedtourvo)] | no |  |
| `cruises` | array[[`CruiseDataSheetVO`](#schema-cruisedatasheetvo)] | no |  |
| `destinations` | array[[`IdeaDestinationVO`](#schema-ideadestinationvo)] | no |  |
| `hotels` | array[[`IdeaHotelVO`](#schema-ideahotelvo)] | no |  |
| `insurances` | array[[`IdeaInsuranceVO`](#schema-ideainsurancevo)] | no |  |
| `manuals` | array[[`IdeaManualVO`](#schema-ideamanualvo)] | no |  |
| `preferredHotels` | array[[`IdeaPreferredHotelVO`](#schema-ideapreferredhotelvo)] | no |  |
| `tickets` | array[[`IdeaTicketVO`](#schema-ideaticketvo)] | no |  |
| `transfers` | array[[`IdeaTransferVO`](#schema-ideatransfervo)] | no |  |
| `transports` | array[[`IdeaTransportVO`](#schema-ideatransportvo)] | no |  |
| `tripSpots` | array[[`IdeaTripSpotVO`](#schema-ideatripspotvo)] | no |  |


### `IdeaDestinationVO` {#schema-ideadestinationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | no |  |
| `country` | string | no |  |
| `description` | string | no |  |
| `fromDay` | integer(int32) | no |  |
| `geolocation` | [`GeolocationVO`](#schema-geolocationvo) | no |  |
| `imageUrls` | array[string] | no |  |
| `itinerary` | array[[`IdeaPlaceVO`](#schema-ideaplacevo)] | no |  |
| `itineraryType` | string | no |  |
| `moreInfoUrl` | string | no |  |
| `name` | string | no |  |
| `nextLocationDistance` | string | no |  |
| `recommendedAirportCode` | string | no |  |
| `recommendedAirportName` | string | no |  |
| `toDay` | integer(int32) | no |  |
| `type` | [`LocationType`](#schema-locationtype) | no |  |


### `IdeaHotelDataVO` {#schema-ideahoteldatavo}

| Field | Type | Required | Description |
|---|---|---|---|
| `accommodationFacilities` | array[[`AccommodationFacilityVO`](#schema-accommodationfacilityvo)] | no |  |
| `accommodationSubtype` | [`AccommodationSubtype`](#schema-accommodationsubtype) | no |  |
| `accommodationType` | [`AccomodationType`](#schema-accomodationtype) | no |  |
| `address` | string | no |  |
| `category` | [`HotelCategory`](#schema-hotelcategory) | no |  |
| `chain` | string | no |  |
| `description` | string | no |  |
| `destination` | [`IdeaPlaceVO`](#schema-ideaplacevo) | no |  |
| `geolocation` | [`GeolocationVO`](#schema-geolocationvo) | no |  |
| `giataId` | integer(int32) | no |  |
| `id` | string | no |  |
| `images` | array[[`ApiStaticContentAccommodationImageVO`](#schema-apistaticcontentaccommodationimagevo)] | no |  |
| `internalRemark` | string | no |  |
| `name` | string | no |  |
| `phoneNumber` | string | no |  |
| `ratings` | array[[`RatingVO`](#schema-ratingvo)] | no |  |
| `roomFacilities` | array[[`RoomFacilityVO`](#schema-roomfacilityvo)] | no |  |


### `IdeaHotelVO` {#schema-ideahotelvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `bookingReference` | string | no |  |
| `checkInDate` | string(date) | no |  |
| `checkOutDate` | string(date) | no |  |
| `day` | integer(int32) | no |  |
| `fixed` | boolean | no |  |
| `hotelData` | [`IdeaHotelDataVO`](#schema-ideahoteldatavo) | no |  |
| `id` | string | no |  |
| `mandatory` | boolean | no |  |
| `mealPlan` | string | no |  |
| `nights` | integer(int32) | no |  |
| `priceBreakdown` | [`PriceBreakdownVO`](#schema-pricebreakdownvo) | no |  |
| `roomTypes` | string | no |  |
| `supplier` | string | no |  |


### `IdeaInsuranceVO` {#schema-ideainsurancevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `bookingReference` | string | no |  |
| `configurationName` | string | no |  |
| `fixed` | boolean | no |  |
| `id` | string | no |  |
| `mandatory` | boolean | no |  |
| `policyNumber` | string | no |  |
| `priceBreakdown` | [`PriceBreakdownVO`](#schema-pricebreakdownvo) | no |  |
| `region` | string | no |  |
| `type` | string | no |  |


### `IdeaManualVO` {#schema-ideamanualvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `bookingReference` | string | no |  |
| `cancelPolicy` | array[[`CancellationPolicyVO`](#schema-cancellationpolicyvo)] | no |  |
| `country` | string | no |  |
| `createdByAgency` | string | no |  |
| `description` | string | no |  |
| `endDate` | string(date) | no |  |
| `paymentType` | string | no |  |
| `pnr` | string | no |  |
| `priceBreakdown` | [`PriceBreakdownVO`](#schema-pricebreakdownvo) | no |  |
| `promotionCode` | boolean | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |
| `providerName` | string | no |  |
| `startDate` | string(date) | no |  |
| `type` | [`ManualServiceType`](#schema-manualservicetype) | no |  |
| `voucherDescription` | string | no |  |


### `IdeaOriginVO` {#schema-ideaoriginvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `location` | [`DestinationVO`](#schema-destinationvo) | no |  |
| `transportBase` | [`TransportBaseVO`](#schema-transportbasevo) | no |  |


### `IdeaPlaceVO` {#schema-ideaplacevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | no |  |
| `itinerary` | array[[`IdeaPlaceVO`](#schema-ideaplacevo)] | no |  |
| `itineraryType` | string | no |  |
| `name` | string | no |  |
| `nextLocationDistance` | string | no |  |
| `type` | [`LocationType`](#schema-locationtype) | no |  |


### `IdeaPreferredHotelCombinationVO` {#schema-ideapreferredhotelcombinationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `cancellationPolicyType` | [`CancellationPolicyType`](#schema-cancellationpolicytype) | no |  |
| `mealPlanType` | [`MealPlanType`](#schema-mealplantype) | no |  |
| `roomDescription` | string | no |  |


### `IdeaPreferredHotelVO` {#schema-ideapreferredhotelvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `combination` | [`IdeaPreferredHotelCombinationVO`](#schema-ideapreferredhotelcombinationvo) | no |  |
| `destination` | [`IdeaPlaceVO`](#schema-ideaplacevo) | no |  |
| `differencePrice` | string | no |  |
| `hotelName` | string | no |  |
| `masterHotelId` | string | no |  |


### `IdeaThemeVO` {#schema-ideathemevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | integer(int32) | no |  |
| `name` | string | no |  |
| `type` | [`Type`](#schema-type) | no |  |


### `IdeaTicketModalityPrice` {#schema-ideaticketmodalityprice}

| Field | Type | Required | Description |
|---|---|---|---|
| `canBookAlone` | boolean | no |  |
| `code` | string | no |  |
| `name` | string | no |  |


### `IdeaTicketVO` {#schema-ideaticketvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `adults` | integer(int32) | no |  |
| `bookingReference` | string | no |  |
| `day` | integer(int32) | no |  |
| `description` | string | no |  |
| `duration` | string | no |  |
| `eventDate` | string(date) | no |  |
| `eventLanguage` | string | no |  |
| `eventTime` | string(time-local) | no |  |
| `fixed` | boolean | no |  |
| `id` | string | no |  |
| `imageUrls` | array[string] | no |  |
| `mandatory` | boolean | no |  |
| `meetingPoint` | string | no |  |
| `modality` | string | no |  |
| `name` | string | no |  |
| `priceBreakdown` | [`PriceBreakdownVO`](#schema-pricebreakdownvo) | no |  |
| `prices` | array[[`IdeaTicketModalityPrice`](#schema-ideaticketmodalityprice)] | no |  |
| `providerCode` | string | no |  |
| `ticketId` | string | no |  |


### `IdeaTransferVO` {#schema-ideatransfervo}

| Field | Type | Required | Description |
|---|---|---|---|
| `bookingReference` | string | no |  |
| `dateTime` | string(date-time) | no |  |
| `day` | integer(int32) | no |  |
| `fixed` | boolean | no |  |
| `from` | [`GeolocationWithNameVO`](#schema-geolocationwithnamevo) | no |  |
| `id` | string | no |  |
| `imageUrl` | string | no |  |
| `mandatory` | boolean | no |  |
| `pickupInformation` | string | no |  |
| `priceBreakdown` | [`PriceBreakdownVO`](#schema-pricebreakdownvo) | no |  |
| `productType` | [`TransferProductType`](#schema-transferproducttype) | no |  |
| `providerName` | string | no |  |
| `supplierId` | integer(int32) | no |  |
| `supplierName` | string | no |  |
| `telephoneAssistance` | string | no |  |
| `to` | [`GeolocationWithNameVO`](#schema-geolocationwithnamevo) | no |  |
| `transferServiceType` | [`TransferServiceType`](#schema-transferservicetype) | no |  |
| `transportNumber` | string | no |  |
| `type` | [`TransferType`](#schema-transfertype) | no |  |
| `vehicleType` | [`TransferVehicleType`](#schema-transfervehicletype) | no |  |


### `IdeaTransportSegmentVO` {#schema-ideatransportsegmentvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `arrivalAirport` | string | no |  |
| `arrivalAirportName` | string | no |  |
| `arrivalDate` | string(date-time) | no |  |
| `baggageInfo` | string | no |  |
| `bookingClass` | string | no |  |
| `cabinClass` | string | no |  |
| `departureAirport` | string | no |  |
| `departureAirportName` | string | no |  |
| `departureDate` | string(date-time) | no |  |
| `durationInMinutes` | integer(int64) | no |  |
| `fareType` | [`FareType`](#schema-faretype) | no |  |
| `flightNumber` | string | no |  |
| `marketingAirlineCode` | string | no |  |
| `operatingAirlineCode` | string | no |  |
| `technicalStopsVO` | array[[`TransportTechnicalStopVO`](#schema-transporttechnicalstopvo)] | no |  |
| `transportType` | string | no |  |


### `IdeaTransportVO` {#schema-ideatransportvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `arrivalDate` | string(date) | no |  |
| `arrivalTime` | string | no |  |
| `baggageInfo` | string | no |  |
| `bookingReference` | string | no |  |
| `code` | string | no |  |
| `company` | string | no |  |
| `day` | integer(int32) | no |  |
| `dayDifference` | integer(int32) | no |  |
| `departureDate` | string(date) | no |  |
| `departureTime` | string | no |  |
| `duration` | string | no |  |
| `fare` | string | no |  |
| `fixed` | boolean | no |  |
| `id` | string | no |  |
| `itinerary` | array[[`IdeaPlaceVO`](#schema-ideaplacevo)] | no |  |
| `itineraryType` | string | no |  |
| `mandatory` | boolean | no |  |
| `marketingAirlineCode` | string | no |  |
| `name` | string | no |  |
| `nextLocationDistance` | string | no |  |
| `numberOfSegments` | integer(int32) | no |  |
| `originCode` | string | no |  |
| `originDestinationCode` | string | no |  |
| `priceBreakdown` | [`PriceBreakdownVO`](#schema-pricebreakdownvo) | no |  |
| `segment` | array[[`IdeaTransportSegmentVO`](#schema-ideatransportsegmentvo)] | no |  |
| `supplier` | string | no |  |
| `targetCode` | string | no |  |
| `targetDestinationCode` | string | no |  |
| `transportNumber` | string | no |  |
| `transportType` | string | no |  |
| `type` | [`LocationType`](#schema-locationtype) | no |  |


### `IdeaTravelerVO` {#schema-ideatravelervo}

| Field | Type | Required | Description |
|---|---|---|---|
| `birthDate` | string(date) | no |  |
| `courtesyTitle` | [`CourtesyTitle`](#schema-courtesytitle) | no |  |
| `documentNumber` | string | no |  |
| `documentType` | [`DocumentType`](#schema-documenttype) | no |  |
| `email` | string | no |  |
| `lastName` | string | no |  |
| `name` | string | no |  |
| `phoneCountryCode` | string | no |  |
| `phoneNumber` | string | no |  |


### `IdeaTripSpotVO` {#schema-ideatripspotvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `code` | string | no |  |
| `country` | string | no |  |
| `highResolutionImage` | string | no |  |
| `image` | string | no |  |
| `itinerary` | array[[`IdeaPlaceVO`](#schema-ideaplacevo)] | no |  |
| `itineraryType` | string | no |  |
| `latitude` | number(double) | no |  |
| `longitude` | number(double) | no |  |
| `name` | string | no |  |
| `nextLocationDistance` | string | no |  |
| `secondImage` | string | no |  |
| `spotType` | [`SpotType`](#schema-spottype) | no |  |
| `type` | [`LocationType`](#schema-locationtype) | no |  |


### `IdeaUpdateRequestVO` {#schema-ideaupdaterequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `autocancelable` | boolean | no |  |
| `description` | string | no |  |
| `largeTitle` | string | no |  |
| `order` | integer(int32) | no |  |
| `remarks` | string | no |  |
| `themes` | array[[`ThemeVO`](#schema-themevo)] | no |  |
| `title` | string | no |  |
| `visible` | boolean | no |  |


### `IdeaVO` {#schema-ideavo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `counters` | [`IdeaCountersVO`](#schema-ideacountersvo) | no |  |
| `creationDate` | string(date) | no |  |
| `customer` | [`IdeaCustomerVO`](#schema-ideacustomervo) | no |  |
| `departureDate` | string(date) | no |  |
| `description` | string | no |  |
| `destinations` | array[[`IdeaPlaceVO`](#schema-ideaplacevo)] | no |  |
| `email` | string | no |  |
| `externalReference` | string | no |  |
| `id` | integer(int32) | no |  |
| `ideaUrl` | string | no |  |
| `imageUrl` | string | no |  |
| `itinerary` | array[[`IdeaPlaceVO`](#schema-ideaplacevo)] | no |  |
| `largeTitle` | string | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `metaDescription` | string | no |  |
| `metaTitle` | string | no |  |
| `pricePerPerson` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `remarks` | string | no |  |
| `ribbonText` | string | no |  |
| `themeDetails` | array[[`IdeaThemeVO`](#schema-ideathemevo)] | no |  |
| `themes` | array[string] | no |  |
| `title` | string | no |  |
| `totalPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `travelers` | array[[`IdeaTravelerVO`](#schema-ideatravelervo)] | no |  |
| `tripType` | [`TripType`](#schema-triptype) | no |  |
| `user` | string | no |  |
| `userB2c` | boolean | no |  |


### `ImageUrlVO` {#schema-imageurlvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `contentType` | string | yes |  |
| `url` | string | yes |  |


### `Instructions` {#schema-instructions}

| Field | Type | Required | Description |
|---|---|---|---|
| `manualInstallation` | string | no |  |
| `networkSetup` | string | no |  |
| `qrCodeInstallation` | string | no |  |


### `InsuranceVO` {#schema-insurancevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | integer(int64) | no |  |


### `InvoiceTarget` {#schema-invoicetarget}

Enum values: 'USER', 'MICROSITE', 'OPERATOR', 'MARKETPLACE'


### `InvoiceType` {#schema-invoicetype}

Enum values: 'NET', 'COMMISSION'


### `InvoicedDataVO` {#schema-invoiceddatavo}

| Field | Type | Required | Description |
|---|---|---|---|
| `address` | string | no |  |
| `city` | string | no |  |
| `country` | string | no |  |
| `documentNumber` | string | no |  |
| `email` | string | no |  |
| `name` | string | no |  |
| `postCode` | string | no |  |
| `requested` | boolean | no |  |


### `KinshipType` {#schema-kinshiptype}

Enum values: 'MY_SELF', 'SPOUSE', 'CHILD', 'PARENT', 'CHILD_IN_LAW', 'PARENT_IN_LAW', 'SIBLING'


### `Language` {#schema-language}

Enum values: 'EN', 'EN_IE', 'EN_US', 'ES', 'IT', 'FR', 'PT', 'PT_BR', 'AR', 'RO', 'EL', 'FI', 'DE', 'NL', 'SV', 'ZH', 'ZH_TW', 'RU', 'HU', 'FA', 'PL', 'CA', 'BG', 'JA', 'MS', 'NO', 'TR', 'SK', 'SL', 'CS', 'HR', 'AZ', 'HE', 'DA', 'TH', 'SQ', 'KA', 'SR', 'UZ', 'EU'


### `LocalDateRangeAndOperationDaysVO` {#schema-localdaterangeandoperationdaysvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `end` | string(date) | no |  |
| `operationDays` | array[[`DayOfWeek`](#schema-dayofweek)] | no |  |
| `start` | string(date) | no |  |


### `LocalDateRangeVO` {#schema-localdaterangevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `end` | string(date) | no |  |
| `start` | string(date) | no |  |


### `LocationType` {#schema-locationtype}

Enum values: 'DESTINATION', 'TRIP_SPOT', 'TRANSPORT_BASE', 'HOTEL'


### `LocationVO` {#schema-locationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | no |  |
| `name` | string | no |  |


### `LoyaltyRewardsPaymentVO` {#schema-loyaltyrewardspaymentvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `points` | number(double) | no |  |
| `price` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `reference` | string | no |  |


### `MailVO` {#schema-mailvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `emails` | array[string(email)] | yes |  |
| `micrositeBan` | boolean | no |  |


### `ManualExchangeRateResponseVO` {#schema-manualexchangerateresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `manualExchangeRate` | [`ManualExchangeRateVO`](#schema-manualexchangeratevo) | no |  |


### `ManualExchangeRateVO` {#schema-manualexchangeratevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `bookingDates` | [`LocalDateRangeVO`](#schema-localdaterangevo) | no |  |
| `id` | integer(int32) | no |  |
| `origin` | [`Currency`](#schema-currency) | yes |  |
| `rate` | number(double) | no |  |
| `target` | [`Currency`](#schema-currency) | yes |  |


### `ManualExchangeRatesResponseVO` {#schema-manualexchangeratesresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `manualExchangeRates` | array[[`ManualExchangeRateVO`](#schema-manualexchangeratevo)] | no |  |


### `ManualPaymentVO` {#schema-manualpaymentvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amount` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `authorizationCode` | string | no |  |
| `flag` | string | no |  |
| `id` | integer(int32) | no |  |
| `installments` | integer(int32) | no |  |
| `lastCreditCardNumbers` | integer(int32) | no |  |
| `lastUpdate` | string(date-time) | no |  |
| `nsu` | string | no |  |
| `paymentType` | string | no |  |
| `userId` | string | no |  |


### `ManualServiceType` {#schema-manualservicetype}

Enum values: 'ACCOUNTING_ADJUSMENT', 'PROMOTION_CODE', 'PAYMENT_COMMISSION', 'REWARD_REDEMPTION', 'OPERATOR_DISCOUNT', 'AGENCY_EXPENSES', 'ON_SPOT_EXPENSES', 'TRANSPORT', 'HOTEL', 'CLOSEDTOUR', 'TICKET', 'INSURANCE', 'TRANSFER', 'CAR_RENTAL', 'GIFT_BOX', 'GIFT_BOX_FEE', 'GIFT_BOX_PURCHASE', 'GROUP_COACH', 'GIFT_CARD', 'ADMINISTRATIVE_FEE', 'CRUISE', 'MEMBERSHIP', 'AMENDMENT', 'TRAVEL_CLUB', 'TRAVEL_CLUB_TPV_FEE', 'LDS_PACKAGES', 'ITEM', 'AGENCY_CREDIT_TOP_UP', 'RAIL_FLY', 'HOTEL_GROUP', 'TRANSFER_GROUP', 'RESTAURANT_GROUP', 'TOURLEADER_GROUP'


### `MarkPendingPaymentRequestVO` {#schema-markpendingpaymentrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `paymentDate` | string(date-time) | yes |  |
| `paymentId` | integer(int32) | yes |  |
| `paymentNumber` | integer(int32) | yes |  |


### `MarkPendingPaymentResponseVO` {#schema-markpendingpaymentresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `alreadyMarkedAsPaid` | boolean | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `bookingReference` | string | no |  |
| `message` | string | no |  |
| `paymentDate` | string(date-time) | no |  |
| `paymentId` | integer(int32) | no |  |
| `paymentNumber` | integer(int32) | no |  |
| `status` | string | no |  |


### `MealPlanType` {#schema-mealplantype}

Enum values: 'ROOM_ONLY', 'BED_AND_BREAKFAST', 'HALF_BOARD', 'FULL_BOARD', 'ALL_INCLUSIVE'


### `MealPlanVO` {#schema-mealplanvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | no |  |
| `id` | string | no |  |
| `providerDescription` | string | no |  |
| `type` | [`MealPlanType`](#schema-mealplantype) | no |  |


### `MinimumFirstPaymentType` {#schema-minimumfirstpaymenttype}

Enum values: 'NONE', 'FIXED', 'PERCENTAGE', 'FIXED_PER_PERSON'


### `Money` {#schema-money}

| Field | Type | Required | Description |
|---|---|---|---|
| `amount` | number(double) | yes |  |
| `closeToZero` | boolean | no |  |
| `currency` | [`Currency`](#schema-currency) | yes |  |
| `naN` | boolean | no |  |
| `negative` | boolean | no |  |
| `positive` | boolean | no |  |
| `positiveOrZero` | boolean | no |  |
| `zero` | boolean | no |  |
| `zeroOrLess` | boolean | no |  |


### `MoneyAmountVO` {#schema-moneyamountvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `microsite` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `operator` | [`MoneyVO`](#schema-moneyvo) | no |  |


### `MoneyVO` {#schema-moneyvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amount` | number(double) | no |  |
| `currency` | [`Currency`](#schema-currency) | no |  |


### `Month` {#schema-month}

Enum values: 'JANUARY', 'FEBRUARY', 'MARCH', 'APRIL', 'MAY', 'JUNE', 'JULY', 'AUGUST', 'SEPTEMBER', 'OCTOBER', 'NOVEMBER', 'DECEMBER'


### `MonthlyReportVO` {#schema-monthlyreportvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `advanceAmount` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `agencyId` | integer(int32) | no |  |
| `balance` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `borneByCompany` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `borneByCompanyWithPrepaid` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `month` | object (inline) | no |  |
| `paidByCompany` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `paidByCompanyWithPrepaid` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `paidByEmployee` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `prepaidByCompany` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `reference` | string | no |  |
| `status` | [`Status`](#schema-status) | no |  |
| `userId` | string | no |  |


### `MonthlyReportsResponseVO` {#schema-monthlyreportsresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `monthlyReports` | array[[`MonthlyReportVO`](#schema-monthlyreportvo)] | no |  |
| `pagination` | [`PaginationVO`](#schema-paginationvo) | no |  |


### `MovementStatus` {#schema-movementstatus}

Enum values: 'PENDING', 'CONSOLIDATED'


### `MovementType` {#schema-movementtype}

Enum values: 'COLLECT', 'REDEEM', 'PRESENT', 'COLLECT_FOR_CREATED_IDEA', 'COLLECT_FOR_SHARED_IDEA'


### `MunicipalityVO` {#schema-municipalityvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `discount` | [`ResidentDiscount`](#schema-residentdiscount) | no |  |
| `id` | string | no |  |
| `name` | string | no |  |


### `OperationDaysVO` {#schema-operationdaysvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `friday` | boolean | no |  |
| `monday` | boolean | no |  |
| `saturday` | boolean | no |  |
| `sunday` | boolean | no |  |
| `thursday` | boolean | no |  |
| `tuesday` | boolean | no |  |
| `wednesday` | boolean | no |  |


### `PaginationVO` {#schema-paginationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `firstResult` | integer(int32) | no |  |
| `pageResults` | integer(int32) | no |  |
| `totalResults` | integer(int32) | no |  |


### `PaymentRefundStatusVO` {#schema-paymentrefundstatusvo}

Enum values: 'NONE', 'PARTIAL', 'COMPLETE'


### `PaymentRefundVO` {#schema-paymentrefundvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amount` | number(double) | no |  |
| `appliedToDeferredPayment` | boolean | no |  |
| `creationDate` | string(date-time) | no |  |
| `currency` | [`Currency`](#schema-currency) | no |  |
| `orderNumber` | string | no |  |
| `remark` | string | no |  |
| `status` | string | no |  |
| `user` | string | no |  |


### `PaymentVO` {#schema-paymentvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `additionalPayments` | array[[`AdditionalPaymentVO`](#schema-additionalpaymentvo)] | no |  |
| `amount` | number(double) | no |  |
| `boniversumPayerResponse` | [`BoniversumPayerResponseVO`](#schema-boniversumpayerresponsevo) | no |  |
| `convertExchangeRate` | number(double) | no |  |
| `convertedAmount` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `currency` | [`Currency`](#schema-currency) | no |  |
| `deferredAmount` | number(double) | no |  |
| `deferredLimitDate` | string(date) | no |  |
| `deferredPayment` | boolean | no |  |
| `firstPaymentMethod` | string | no |  |
| `firstPaymentResponse` | string | no |  |
| `firstPaymentScheme` | string | no |  |
| `id` | integer(int32) | no |  |
| `loyaltyRewardsPayment` | [`LoyaltyRewardsPaymentVO`](#schema-loyaltyrewardspaymentvo) | no |  |
| `manualSecondPayment` | boolean | no |  |
| `orderNumber` | string | no |  |
| `paymentConfigurationDescription` | string | no |  |
| `paymentConfigurationId` | string | no |  |
| `paymentRefundStatus` | [`PaymentRefundStatusVO`](#schema-paymentrefundstatusvo) | no |  |
| `paymentSystem` | string | no |  |
| `paymentType` | string | no |  |
| `promotionCodes` | array[[`PromotionCodeVO`](#schema-promotioncodevo)] | no |  |
| `providerTransactionId` | string | no |  |
| `refunds` | array[[`PaymentRefundVO`](#schema-paymentrefundvo)] | no |  |
| `remainderCount` | integer(int32) | no |  |
| `secondPaymentAmount` | number(double) | no |  |
| `secondPaymentCurrency` | [`Currency`](#schema-currency) | no |  |
| `secondPaymentDate` | string(date) | no |  |
| `secondPaymentMethod` | string | no |  |
| `secondPaymentOrderNumber` | string | no |  |
| `secondPaymentRefundStatus` | [`PaymentRefundStatusVO`](#schema-paymentrefundstatusvo) | no |  |
| `secondPaymentResponse` | string | no |  |
| `secondPaymentScheme` | string | no |  |
| `secondPaymentSystem` | string | no |  |
| `secondPaymentTime` | string(time-local) | no |  |
| `travelClubPointsPayment` | [`TravelClubPointsPaymentVO`](#schema-travelclubpointspaymentvo) | no |  |


### `PdfVO` {#schema-pdfvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `creationDate` | string(date-time) | no |  |
| `name` | string | no |  |
| `size` | number(double) | no |  |
| `url` | string | no |  |


### `PendingPaymentResponseVO` {#schema-pendingpaymentresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `pagination` | [`PaginationVO`](#schema-paginationvo) | no |  |
| `pendingPayments` | array[[`PendingPaymentVO`](#schema-pendingpaymentvo)] | no |  |


### `PendingPaymentVO` {#schema-pendingpaymentvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amountToPay` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `bookingReference` | string | no |  |
| `deferredAmount` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `deferredLimitDate` | string(date-time) | no |  |
| `paymentId` | integer(int32) | no |  |
| `paymentNumber` | integer(int32) | no |  |


### `PersonNameVO` {#schema-personnamevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `courtesyTitle` | string | no |  |
| `name` | string | no |  |


### `PersonVO` {#schema-personvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `academyTitle` | [`AcademyTitle`](#schema-academytitle) | no |  |
| `address` | [`AddressVO`](#schema-addressvo) | no |  |
| `billingNumber` | string | no |  |
| `birthDate` | string(date) | no |  |
| `clubDocumentNumber` | string | no |  |
| `country` | string | no |  |
| `countryId` | string | no |  |
| `courtesyTitle` | [`CourtesyTitle`](#schema-courtesytitle) | no |  |
| `documentNumber` | string | no |  |
| `documentType` | [`DocumentType`](#schema-documenttype) | no |  |
| `email` | string(email) | no |  |
| `id` | string | no |  |
| `kinship` | [`KinshipType`](#schema-kinshiptype) | no |  |
| `lastName` | string | no |  |
| `municipality` | [`MunicipalityVO`](#schema-municipalityvo) | no |  |
| `name` | string | no |  |
| `passportExpirationDate` | string(date) | no |  |
| `phone` | string | no |  |
| `phoneCountryCode` | string | no |  |
| `requestedAge` | integer(int32) | yes |  |
| `socialInsuranceNumber` | string | no |  |


### `PlaceVO` {#schema-placevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | no |  |
| `name` | string | no |  |
| `type` | [`LocationType`](#schema-locationtype) | no |  |


### `PriceBreakdownVO` {#schema-pricebreakdownvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `agencyFee` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `agencyManagementFee` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `agencyManagerFee` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `countryTaxes` | array[[`CountryTaxesVO`](#schema-countrytaxesvo)] | no |  |
| `exchangeRateCommissionMicrosite` | number(double) | no |  |
| `exchangeRateMicrosite` | number(double) | no |  |
| `groundServicesManagementFee` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `micrositeFee` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `netProvider` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `nonCommissionableNetPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `operatorFee` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `operatorManagementFee` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `paymentFee` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `pricePayableAtProperty` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `providerNetPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `taxBreakdowns` | array[[`CountryTaxesVO`](#schema-countrytaxesvo)] | no |  |
| `taxes` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `totalOperatorRevenue` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `totalPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `transportServicesManagementFee` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `withHoldings` | array[[`WithHoldingVO`](#schema-withholdingvo)] | no |  |


### `PriceType` {#schema-pricetype}

Enum values: 'RETAIL', 'RETAIL_OVER', 'NET', 'NET_BASE', 'NET_RETAIL'


### `PromotionCodeModalityVO` {#schema-promotioncodemodalityvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `applicableContractSuppliers` | array[integer(int32)] | no |  |
| `applicableCountries` | array[string] | no |  |
| `applicableDestinations` | array[string] | no |  |
| `applicableProviders` | array[integer(int32)] | no |  |
| `balance` | string | no |  |
| `creationDate` | string(date-time) | no |  |
| `currency` | string | no |  |
| `dailyUses` | integer(int32) | no |  |
| `discount` | number(double) | no |  |
| `fixedAmount` | boolean | no |  |
| `fromBookingDate` | string(date) | no |  |
| `fromTripDate` | string(date) | no |  |
| `id` | integer(int32) | no |  |
| `limitOfUsesPerUser` | integer(int32) | no |  |
| `limitUses` | integer(int32) | no |  |
| `maxAmountDiscount` | number(double) | no |  |
| `maxCurrencyDiscount` | string | no |  |
| `maxStay` | integer(int32) | no |  |
| `maxTripAmount` | number(double) | no |  |
| `maxTripAmountCurrency` | string | no |  |
| `minStay` | integer(int32) | no |  |
| `minTripAmount` | number(double) | no |  |
| `minTripAmountCurrency` | string | no |  |
| `name` | string | no |  |
| `notAllowedForOnePax` | boolean | no |  |
| `percentage` | boolean | no |  |
| `removeAgencyManagementFeeAndOvercommission` | boolean | no |  |
| `toBookingDate` | string(date) | no |  |
| `toTripDate` | string(date) | no |  |
| `tripTypes` | array[[`TripType`](#schema-triptype)] | no |  |


### `PromotionCodeVO` {#schema-promotioncodevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `applicableAgencies` | array[string] | no |  |
| `applicableCountries` | array[string] | no |  |
| `applicableDestinations` | array[string] | no |  |
| `applicableUsers` | array[string] | no |  |
| `creationDate` | string(date-time) | no |  |
| `currency` | string | no |  |
| `dailyUses` | integer(int32) | no |  |
| `discount` | number(double) | no |  |
| `id` | integer(int32) | no |  |
| `microsite` | string | no |  |
| `modalities` | array[[`PromotionCodeModalityVO`](#schema-promotioncodemodalityvo)] | no |  |
| `promotionCode` | string | no |  |
| `promotionCodeType` | string | no |  |
| `redeemDate` | string(date) | no |  |
| `showAlert` | boolean | no |  |
| `tripTypes` | array[[`TripType`](#schema-triptype)] | no |  |
| `usedInDeferredPayment` | boolean | no |  |
| `userBalance` | boolean | no |  |
| `userBalanceB2B` | boolean | no |  |


### `Provider` {#schema-provider}

Enum values: 'MYSTIFLY', 'TRAVELFUSION', 'AMADEUS', 'AMADEUS_STARALLIANCE', 'FLIXBUS', 'GALILEO', 'SABRE_FLIGHTS', 'GOL_FLIGHTS', 'CONTRACT_TRANSPORT', 'MARKETPLACE_TRANSPORT', 'KIWI', 'TRAFFICS_FLIGHT', 'LLEEGO', 'AIRTRADE', 'DIRECT_FERRIES', 'TIRRENIA_FERRIES', 'AIR_GATEWAY', 'CONSOLID', 'SILICONSOM', 'NETACTICA', 'NETVIAX', 'MANUAL_TRANSPORT', 'ORCHESTRA', 'TRENITALIA', 'RAIL_EUROPE', 'RAILCLICK', 'RAILTOUR', 'AZUL_AIRLINES', 'ITALO_NTV', 'DAYTRIP', 'TICTACTRIP', 'KIKOTO', 'RESANEO', 'VOLARIS', 'IFLYRES', 'MYAGENT', 'VIVAAEROBUS', 'LIKNOSS', 'VFLY', 'AIRQUEST', 'NAVA_TRAVEL_FLIGHTS', 'AIC_HOTELS', 'EASY_HOTELS', 'APITUDE_HOTELS', 'CALIPSO_HOTELS', 'HOTUSA', 'ANIXE', 'DERBYSOFT', 'DIDA', 'DINGUS', 'CONTRACT_HOTEL', 'CONTRACT_HOTEL_GROUP', 'SUNHOTELS', 'VETURIS', 'MARKETPLACE_HOTEL', 'PARSEC', 'WORLD2MEET', 'BOOKOHOTEL', 'TAILORBEDS', 'GALAXYVACATION', 'ACTION_TRAVEL', 'ABREU', 'ETURIA', 'MIZE', 'HYPERGUEST', 'PANAMERICANA', 'STOCKHOTELS', 'TUI_HOTELS', 'PEAK_POINT', 'EXPEDIA', 'GRNCONNECT', 'TEAM_AMERICA', 'CONTRACT_APARTMENT', 'DESTINATIONS_OF_THE_WORLD', 'HOTELDO', 'LUXURY_TRAVEL_GREECE', 'HOTEL_DISTRIBUTE', 'GETAROOM', 'OMNIBEES', 'TRAFFICS_HOTEL', 'SMYROOMS', 'TOUR10', 'ITAPARICA', 'IXPIRA', 'RATEHAWK', 'AMERICANEXECUTIVE', 'GOGLOBAL', 'MY_BEDS', 'BESTBUY', 'PRICETRAVEL', 'IMPERATOURS', 'MAGICDMC', 'SITEMINDER', 'AMADEUS_HOTEL', 'RTS', 'YALAGO', 'BOOKING_COM', 'MEDINTUR', 'PRESTIGE', 'TBO_HOLIDAYS', 'JUMBOTOURS', 'JUMBONLINE', 'ONE_WAY_TO_ITALY', 'RAKUTEN', 'HOTELSTON', 'AVORIS', 'ROIBOS', 'ROIBOS_CHANNELS', 'JOURNEYS_HUB', 'IBOOSY_TESTING', 'SOLOLE_IBOOSY', 'METHABOOK_IBOOSY', 'WL2T_IBOOSY', 'ONE_UP_TRAVEL', 'HASHNAP_TRAVEL', 'STUBA', 'HOTETEC', 'EZLINK', 'CNTRAVELHOTEL', 'HPROTRAVEL', 'FASTPAYHOTELS', 'FREEWAY', 'FULL_OF_DREAMS', 'FULL_OF_DREAMS_PARKS', 'BY_HOURS', 'PORTIMAR', 'LCI', 'PRIMETRAVEL', 'MTS_GLOBE', 'MIKITRAVEL', 'SAFAR_DREAM', 'TRAVELGATE', 'AMERICANTOURS_TRG', 'DIEUXTRAVELSERVICES_TRG', 'WEBHOTELIER_TRG', 'TOURING_INTERNATIONAL', 'PAXIMUM_TRG', 'ROOMBEAST_TRG', 'OTTILA_TRG', 'INFINITAS_TRG', 'BONOTEL_TRG', 'DARINAHOLIDAYS_TRG', 'ALLIEDTPRO_TRG', 'IOLX_TRG', 'G2TRAVEL_TRG', 'DAKKAK_TRG', 'TRAVELLANDA_TRG', 'HOTEL_TRADER_TRG', 'TRAVELC_ACCOMMODATION', 'MANUAL_HOTEL', 'KURBAN_TOURS', 'HRESA', 'KOOB_TECH', 'TRAVCO_UK_TRG', 'TOURVEST_TRG', 'HYD_BRASIL_TRG', 'QUALITY_TRAVEL', 'RED_TURISTICA', 'ESCALABEDS', 'LIMA_TOURS_TRG', 'LETSFLY_TECH_TRG', 'DESERT_ADVENTURES_TRG', 'TOUR_MIND_TRG', 'TRAVEL_TIME', 'TELDAR_TRAVEL_TRG', 'VIA_CAPI_TRG', 'MY_GO_TRG', 'JON_VIEW_TRG', 'EASE_MY_TRIP_TRG', 'DTT_TRAVEL_TRG', 'CONEXTUR_TRG', 'ELITE_BOOKINGS_HOTEL', 'SAFA_DMC', 'GEKKO_INFINITE_TRG', 'WELLBEDS_TRG', 'BOOKME_TRG', 'REZLIVE_TRG', 'TOURMED_TRG', 'MTS_JUNIPER', 'VASDREAM_TRG', 'NITES_TRG', 'GREENTRAVEL_TRG', 'MAISBRASIL_TRG', 'TRAVEL_FRIENDS', 'SIDETOURS_TRG', 'OTSI_TGR', 'ALTURA_BEDS_TRG', 'PERFECT_TOUR_ROMANIA', 'ALBATRAVEL_ITALY', 'EUROTOURS_TRG', 'CONECTAAS_TRG', 'CNBOOKING_TRG', 'HIGHSTAR_TRG', 'CARTHAGE_GROUP_TRG', 'WITHIN_EARTH_TRG', 'WOZOZO_TRG', 'EVER_ROOMS_TRG', 'DESTINATION_ITALIA_TRG', 'VTOURS', 'DER', 'DESTINATION_ONE_TRG', 'PEAKPOINT_GLOBAL_TRG', 'CONVERGENT_TRG', 'NEW_WORLD_TRAVEL_TRG', 'RATETIGER_TRG', 'ALLBEDS_TRG', 'PRIORITY', 'CRETAN_HOLIDAYS_TRG', 'HOTELBEDS_APITUDE_TICKETS', 'CONTRACT_TICKETS', 'CONTRACT_GOLF', 'SPORTSEVENTS365', 'VIATOR_TICKETS', 'MARKETPLACE_TICKET', 'MARKETPLACE_GOLF', 'CIVITATIS_TICKETS', 'STAGE_ENTERTAINMENT_TICKETS', 'EXPER_TICKET', 'ZEST', 'BOKUN_TICKETS', 'TURISMOI_TICKETS', 'MANUAL_TICKETS', 'CITYTOURS_TICKETS', 'MUSEMENT', 'CEETIZ', 'GET_YOUR_GUIDE', 'JUNIPER_TICKETS', 'NEXUSTOURS', 'AMERICANEXECUTIVE_TICKET', 'I_NEED_TOURS_TICKET', 'DERBYSOFT_TICKET', 'DERTRIP', 'MANDARINA_JPT', 'DOMNIA', 'JUMBOTOURS_TICKET', 'TUEXPERIENCIA', 'ACTION_TRAVEL_TICKET', 'FULL_OF_DREAMS_TICKET', 'CENTRAL_DE_RESERVAS_TICKET', 'GALAXYVACATION_TICKET', 'AVORIS_TICKET', 'CONTRACT_ITEMS', 'TRENITALIA_ITEMS', 'AIRALO', 'CONECTY', 'VISAGOV', 'VIP_LOUNGES', 'ON_SPOT', 'MANDATORY_INSURANCE', 'OPTIONAL_INSURANCE', 'CANCELLATION_INSURANCE', 'MID_INSURANCE', 'ASSIST_CARD', 'INTERMUNDIAL', 'ASSUREVER', 'UNIVERSAL_ASSISTANCE', 'HERO', 'PAYNOWSOLUTIONS', 'SPENCER_CHARTER', 'ULTRA_VIAJES', 'HOTELBEDS_TRANSFERS', 'CONTRACT_TRANSFER', 'HOLIDAYTAXIS_TRANSFER', 'INTUI_TRANSFER', 'MARKETPLACE_TRANSFER', 'MYTRANSFERS_TRANSFER', 'SUNHOTELS_TRANSFERS', 'I_NEED_TOURS_TRANSFER', 'MANUAL_TRANSFER', 'JUMBOTOURS_TRANSFERS', 'CIVITATIS_TRANSFER', 'TRANSFERZ_TRANSFER', 'SUNTRANSFERS_TRANSFER', 'JUMBONLINE_TRANSFER', 'SERVANTRIP_TRANSFER', 'DRIVADO_TRANSFER', 'MOZIO_TRANSFER', 'ELIFE_TRANSFER', 'TALIXO_TRANSFER', 'IWAY_TRANSFER', 'ONWAY_TRANSFER', 'LIMOWIDE_TRANSFER', 'HOTELBEDS_CAR_RENTAL', 'CONTRACT_CAR_RENTAL', 'MARKETPLACE_CAR', 'FLEXIBLE_AUTOS', 'SUNNY_CARS', 'TRAVELPORT', 'TINOLEGGIO', 'BSP_AUTO', 'DERTOUR_WHEELS', 'HERTZ', 'MANUAL_CAR_RENTAL', 'ADALTE', 'TUI_MUSEMENT_TOUR', 'CITYTOURS', 'CONTRACT_CLOSEDTOUR', 'CLUB_VACACIONES', 'CNTRAVEL', 'EUROPAMUNDO', 'EUROWELCOME', 'GADVENTURES', 'INTERRIAS', 'JULIA_TRAVEL', 'KANNAK', 'MAPATOURS', 'MAPAPLUS', 'MARKETPLACE_CLOSEDTOUR', 'NEW_BLUE', 'ONE_WAY_TO_ITALY_TOUR', 'PANAVISIONTOURS', 'SOLFERIAS', 'SPECIALTOURS', 'SURLAND', 'TOURMUNDIAL', 'TRAVELC_CLOSEDTOUR', 'TRAVELEXCHANGE', 'TRAVELEXCHANGE_FLY_AND_DRIVE', 'TRAVELPLAN', 'TUI', 'VIAJES_PARA_TODOS', 'WAMOS', 'YOUR_TTOO', 'SOLTOUR', 'PALISIS', 'ROYAL_CARIBBEAN', 'COSTA_CRUCEROS', 'MSC', 'CELEBRITY_CRUISES', 'CARNIVAL', 'HOLLAND', 'NCL', 'SEABOURN', 'PRINCESS', 'CUNARD', 'REGENT', 'CELESTYAL', 'OCEANIA', 'TUI_CRUISES', 'CRUISE_HOST', 'DER_CAMPERS', 'MANUAL', 'FAKEFLIGHT', 'MULTIFAKEFLIGHT', 'FAKEBUS', 'FAKEFERRY', 'FAKEHOTEL', 'FAKEHOTEL_WITH_RSP', 'FAKEHOUSE', 'FAKEHOTEL_BY_HOURS', 'FAKETICKET', 'FAKEGOLF', 'FAKETICKETMODALITYPRICE', 'FAKETRANSFER', 'FAKECAR', 'FAKECAMPER', 'FAKECLOSEDTOUR', 'FAKECRUISE', 'FAKEEXTERNALTOUR', 'FAKEITEM', 'FAKESIM', 'FAKEVISA', 'FAKEVIPLOUNGE', 'FAKEINSURANCE', 'FAKETRAIN', 'HOTEL_GROUP_DESTINATION', 'TRANSFER_GROUP_DESTINATION', 'COACH_GROUP_DESTINATION', 'RESTAURANT_DESTINATION', 'TOURLEADER_GROUP_DESTINATION', 'ALPHATOURS', 'MULTICOM_HOTELS', 'HOTELBEDS', 'PLUM', 'PRESTIGE_BENIDORM', 'TOURICOHOTELS', 'TRAVELIOHOTELS', 'VANILLA', 'HOTELERUM', 'RENTALSUNITED', 'MTS', 'ROOMSINCLOUD', 'INTERPASS', 'GDX', 'HOTELBEDS_TICKETS', 'TOURICO_TICKETS', 'TRAVENER', 'TOTALSTAY', 'TBO_TRG', 'MONAKER', 'ECI_TRAVEL', 'ECI_TRAVEL_TICKET', 'ECI_TRAVEL_TRANSFER', 'GETAROOM_TRG', 'STUBA_TRG', 'IMPERATORE_TRG', 'SERHS_TRG', 'MIKITRAVEL_TRG', 'HOTELSPRO_TRG', 'OMNIBEES_TRG', 'AIC_TRG', 'SMYROOMS_TRG', 'JUMBOTOURS_TRG', 'WITHINEARTH_TRG', 'TOUR10_TRG', 'OLYMPIA_EUROPE_TRG', 'YOUTRAVEL_TRG', 'MGBEDBANK_TRG', 'RATEHAWK_TRG', 'TRAVCO_TRG', 'YIELDPLANET_TRG', 'GOGLOBAL_TRG', 'YALAGO_TRG', 'PERLATOURS_TRG', 'SITEMINDER_TRG', 'PRESTIGE_TRG', 'RAKUTEN_TRG', 'BOOKINGEXPERT', 'WELCOMEBEDS', 'IMASTERGOLF', 'AZAMARA_CRUISES', 'WEBBEDS', 'CUPOHOTEL', 'BOOKOROOMS', 'FASTPAY', 'PALACE_RESORT', 'JUNIPER', 'VIAJES_PACIFICO', 'KOEDIA', 'ROIBACK', 'EUROPASSITANCE_MANDATORY_BASIC_INSURANCE', 'EUROPASSITANCE_MANDATORY_INSURANCE', 'EUROPASSITANCE_OPTIONAL_BASIC_INSURANCE', 'EUROPASSITANCE_OPTIONAL_PLUS_INSURANCE', 'EUROPASSITANCE_OPTIONAL_PREMIUM_INSURANCE', 'EUROPASSITANCE_OPTIONAL_COLLECTIVE_INSURANCE', 'EUROPASSITANCE_CANCELLATION_INSURANCE', 'ELITE_TRAVEL_HOTEL', 'AVORIS_TOURS', 'BOKUN_CLOSEDTOUR_MARKETPLACE', 'BOKUN_CLOSEDTOUR', 'SUR_HOTELES', 'YOU_TRAVEL', 'AERTICKET', 'MTSJ2_TRG', 'NUITEE_TRG', 'PAN_PACIFIC', 'TRABAX', 'RATESPRO', 'ADRIAGATE_TRG', 'MAPAPLUS_ENGLISH', 'MAPAMUNDI', 'UBER_TRANSFER', 'ALBATRAVEL_FRANCE', 'RATES_PRO_TC', 'MTS_TRG', 'AIDA', 'PRO_TRAVELLING'


### `ProviderConfigurationVO` {#schema-providerconfigurationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | no |  |
| `id` | integer(int32) | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |


### `ProviderQuestionMapper` {#schema-providerquestionmapper}

_(no properties / composition-only schema)_


### `ProviderTraceVO` {#schema-providertracevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `end` | string(date-time) | no |  |
| `error` | string | no |  |
| `processTime` | integer(int64) | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |
| `providerDescription` | string | no |  |
| `quoteResults` | integer(int32) | no |  |
| `start` | string(date-time) | no |  |


### `PurchaseAttemptResponse` {#schema-purchaseattemptresponse}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `counters` | [`IdeaCountersVO`](#schema-ideacountersvo) | no |  |
| `creationDate` | string(date) | no |  |
| `customer` | [`IdeaCustomerVO`](#schema-ideacustomervo) | no |  |
| `departureDate` | string(date) | no |  |
| `description` | string | no |  |
| `destinations` | array[[`IdeaPlaceVO`](#schema-ideaplacevo)] | no |  |
| `email` | string | no |  |
| `externalReference` | string | no |  |
| `id` | integer(int32) | no |  |
| `ideaUrl` | string | no |  |
| `imageUrl` | string | no |  |
| `itinerary` | array[[`IdeaPlaceVO`](#schema-ideaplacevo)] | no |  |
| `largeTitle` | string | no |  |
| `lastUpdateDate` | string(date-time) | no |  |
| `metaDescription` | string | no |  |
| `metaTitle` | string | no |  |
| `pricePerPerson` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `remarks` | string | no |  |
| `ribbonText` | string | no |  |
| `themeDetails` | array[[`IdeaThemeVO`](#schema-ideathemevo)] | no |  |
| `themes` | array[string] | no |  |
| `title` | string | no |  |
| `totalPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `travelers` | array[[`IdeaTravelerVO`](#schema-ideatravelervo)] | no |  |
| `tripType` | [`TripType`](#schema-triptype) | no |  |
| `user` | string | no |  |
| `userB2c` | boolean | no |  |


### `PurchaseAttemptsResponse` {#schema-purchaseattemptsresponse}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `pagination` | [`PaginationVO`](#schema-paginationvo) | no |  |
| `purchaseAttempt` | array[[`IdeaVO`](#schema-ideavo)] | no |  |


### `QuestionType` {#schema-questiontype}

Enum values: 'TRUE_OR_FALSE', 'MEASUREMENT', 'NUMBER', 'NUMBER_RANGE', 'TEXT', 'TRAVELER_SELECTION', 'OPTIONS', 'OPTIONS_OR_TEXT', 'DEFAULT', 'DATE', 'TIME'


### `RatingVO` {#schema-ratingvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `numReviews` | integer(int64) | no |  |
| `score` | string | no |  |
| `source` | string | no |  |


### `RefundBookingRequestVO` {#schema-refundbookingrequestvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amount` | number | no | Amount to refund. Use 0 or omit it to refund the full remaining refundable amount. |
| `manualRefund` | boolean | no | Set to true only when the refund was handled manually outside TravelCompositor. Defaults to false. |
| `orderNumber` | string | yes |  |


### `RefundBookingResponseVO` {#schema-refundbookingresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amount` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `bookingReference` | string | no |  |
| `manualRefund` | boolean | no |  |
| `orderNumber` | string | no |  |
| `paymentRefundStatus` | [`PaymentRefundStatusVO`](#schema-paymentrefundstatusvo) | no |  |
| `secondPaymentRefundStatus` | [`PaymentRefundStatusVO`](#schema-paymentrefundstatusvo) | no |  |


### `RelatedServiceVO` {#schema-relatedservicevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `cancellationFee` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `serviceId` | string | no |  |


### `ReportVO` {#schema-reportvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `advanceAmount` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `balance` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `borneByCompany` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `borneByCompanyWithPrepaid` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `description` | string | no |  |
| `endDate` | string(date) | no |  |
| `externalId` | integer(int32) | no |  |
| `monthlyReportReference` | string | no |  |
| `paidByCompany` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `paidByCompanyWithPrepaid` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `paidByEmployee` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `prepaidByCompany` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `reference` | string | no |  |
| `startDate` | string(date) | no |  |
| `status` | [`Status`](#schema-status) | no |  |
| `userId` | string | no |  |


### `ReportsResponseVO` {#schema-reportsresponsevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `reports` | array[[`ReportVO`](#schema-reportvo)] | no |  |


### `RepricingVO` {#schema-repricingvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `bookingReference` | string | no |  |
| `serviceId` | string | no |  |


### `RequiredPassengerData` {#schema-requiredpassengerdata}

Enum values: 'FIRST_NAME', 'LAST_NAME', 'TITLE', 'ACADEMY_TITLE', 'BIRTH_DATE', 'COUNTRY', 'PHONE', 'DOCUMENT', 'PASSPORT', 'DOCUMENT_DATE_OF_ISSUE', 'DOCUMENT_EXPIRY_DATE', 'ADDRESS', 'EMAIL', 'EMERGENCY_CONTACT', 'BILLING_DOCUMENT', 'SEAT_SELECTION', 'SOCIAL_INSURANCE_NUMBER', 'FREQUENT_TRAVELLER_NUMBER', 'INVOICE_DATA'


### `ResidentDiscount` {#schema-residentdiscount}

Enum values: 'BALEARIC', 'CANARY', 'MELILLA', 'CEUTA', 'SARDINIA'


### `RoomFacilityCategory` {#schema-roomfacilitycategory}

Enum values: 'GENERAL', 'ACTIVITIES', 'BATHROOM', 'MEDIA_AND_TECHNOLOGY', 'FOOD_AND_DRINK', 'INTERNET', 'KITCHEN', 'OUTDOORS', 'VIEW', 'LIVING_AREA', 'BEDROOM', 'EXCLUSIVE_SERVICES'


### `RoomFacilityListVO` {#schema-roomfacilitylistvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `facilities` | array[[`RoomFacilityVO`](#schema-roomfacilityvo)] | no |  |


### `RoomFacilityVO` {#schema-roomfacilityvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `category` | [`RoomFacilityCategory`](#schema-roomfacilitycategory) | no |  |
| `icon` | string | no |  |
| `id` | integer(int32) | no |  |
| `priority` | integer(int32) | no |  |
| `translations` | object (inline) | no |  |


### `RspVO` {#schema-rspvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `applied` | boolean | no |  |
| `price` | [`MoneyVO`](#schema-moneyvo) | no |  |


### `SalesChannelType` {#schema-saleschanneltype}

Enum values: 'WEB', 'API', 'API_TC_CLIENTS', 'API_LDS_PACKAGES', 'REPRICING', 'MO_APP', 'MOBILE_APP', 'MARKETPLACE', 'CONSOLIDATED_CRUISE', 'WEB_MOBILE', 'DORA', 'ASKIA', 'CHANNELMANAGER_PLUGIN'


### `SearchTotalsVO` {#schema-searchtotalsvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `bookings` | integer(int32) | no |  |
| `bookingsQuoteRatio` | integer(int32) | no |  |
| `quotes` | integer(int32) | no |  |


### `SeatVO` {#schema-seatvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `personId` | string | no |  |
| `providerNetPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `seatNumber` | string | no |  |
| `totalPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |


### `ServicePriceBreakdownVO` {#schema-servicepricebreakdownvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `agencyFee` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `agencyManagementFee` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `agencyManagerFee` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `consolidatorFee` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `consolidatorNetPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `countryTaxes` | array[[`CountryTaxesVO`](#schema-countrytaxesvo)] | no |  |
| `exchangeRateCommissionMicrosite` | number(double) | no |  |
| `exchangeRateMicrosite` | number(double) | no |  |
| `groundServicesManagementFee` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `marketPlaceFee` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `micrositeFee` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `netProvider` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `netProviderPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `nonCommissionableNetPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `operatorFee` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `operatorManagementFee` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `paymentFee` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `pricePayableAtProperty` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `providerFee` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `providerNetPrice` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `taxBreakdowns` | array[[`CountryTaxesVO`](#schema-countrytaxesvo)] | no |  |
| `taxes` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `totalOperatorRevenue` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `totalPrice` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `transportServicesManagementFee` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `withHoldings` | array[[`WithHoldingVO`](#schema-withholdingvo)] | no |  |


### `ServiceStatus` {#schema-servicestatus}

Enum values: 'NOT_QUOTED', 'QUOTED', 'LOAD_CANCELLATION_POLICIES_ERROR', 'CONFIRMATION_ERROR', 'CONFIRMED', 'PREBOOKED', 'PAID', 'BOOKED', 'NEED_QUOTE', 'BOOK_ERROR', 'RQ', 'NOT_BOOKED', 'PENDING_BOOK', 'CANCELED', 'CANCEL_ERROR', 'PRICE_ERROR', 'LOCKED', 'PENDING_UPDATE', 'WITH_PROPOSALS', 'WAITING_SUPPLIER', 'WAITING_ACCEPTANCE'


### `ServiceType` {#schema-servicetype}

Enum values: 'TRANSPORT', 'HOTEL', 'HOTEL_GROUP', 'HOTEL_GROUP_DESTINATION', 'RESTAURANT_GROUP', 'TRANSFER_GROUP', 'CLOSEDTOUR', 'CRUISE', 'TICKET', 'ITEM', 'INSURANCE', 'TRANSFER', 'CAR_RENTAL', 'MANUAL', 'COACH_GROUP', 'PRIVATE_TOUR_GROUP', 'TOURLEADER_GROUP', 'CAMPER'


### `SettlementVO` {#schema-settlementvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amount` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `date` | string(date) | no |  |
| `reference` | string | no |  |
| `reportReference` | string | no |  |


### `SimInstallationVO` {#schema-siminstallationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `installationGuide` | string | no |  |
| `instructionsAndroid` | [`Instructions`](#schema-instructions) | no |  |
| `instructionsIOS` | [`Instructions`](#schema-instructions) | no |  |
| `qrCodesUrl` | array[string] | no |  |
| `simType` | string | no |  |


### `SocialNetworksVO` {#schema-socialnetworksvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `facebookUrl` | string | no |  |
| `instagramUrl` | string | no |  |
| `linkedinUrl` | string | no |  |
| `pinterestUrl` | string | no |  |
| `tiktokUrl` | string | no |  |
| `twitterUrl` | string | no |  |
| `vimeoUrl` | string | no |  |
| `youtubeUrl` | string | no |  |


### `SpotType` {#schema-spottype}

Enum values: 'NATURE', 'AMUSEMENT', 'TOWN', 'HERITAGE'


### `Status` {#schema-status}

Enum values: 'OK', 'Created', 'Accepted', 'No Content', 'Reset Content', 'Partial Content', 'Moved Permanently', 'Found', 'See Other', 'Not Modified', 'Use Proxy', 'Temporary Redirect', 'Bad Request', 'Unauthorized', 'Payment Required', 'Forbidden', 'Not Found', 'Method Not Allowed', 'Not Acceptable', 'Proxy Authentication Required', 'Request Timeout', 'Conflict', 'Gone', 'Length Required', 'Precondition Failed', 'Request Entity Too Large', 'Request-URI Too Long', 'Unsupported Media Type', 'Requested Range Not Satisfiable', 'Expectation Failed', 'Precondition Required', 'Too Many Requests', 'Request Header Fields Too Large', 'Internal Server Error', 'Not Implemented', 'Bad Gateway', 'Service Unavailable', 'Gateway Timeout', 'HTTP Version Not Supported', 'Network Authentication Required'


### `SubmitDocumentForm` {#schema-submitdocumentform}

| Field | Type | Required | Description |
|---|---|---|---|
| `currency` | string | yes | Declared currency of the document (e.g. EUR, USD, GBP) |
| `externalId` | string | yes | Client-assigned unique identifier for this document |
| `file` | string(binary) | yes | PDF file to analyse |
| `language` | string | yes | Declared language of the document (e.g. ES, EN, FR, DE, IT, PT) |
| `serviceType` | string — enum[HOTEL, TRANSFER, TICKET, CLOSEDTOUR] | yes | Service type the document belongs to. Currently HOTEL and CLOSEDTOUR are supported. |


### `ThemeVO` {#schema-themevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | integer(int32) | no |  |
| `imageUrl` | string | no |  |
| `name` | string | no |  |


### `TicketCategoryRS` {#schema-ticketcategoryrs}

| Field | Type | Required | Description |
|---|---|---|---|
| `auditData` | [`AuditResponseVO`](#schema-auditresponsevo) | no |  |
| `categories` | array[[`TicketCategoryVO`](#schema-ticketcategoryvo)] | no |  |


### `TicketCategoryVO` {#schema-ticketcategoryvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | no |  |
| `id` | string | no |  |


### `TicketContractPriceType` {#schema-ticketcontractpricetype}

Enum values: 'DISTRIBUTION', 'OCCUPANCY', 'SERVICE', 'FOR_FREE'


### `TicketDataVO` {#schema-ticketdatavo}

| Field | Type | Required | Description |
|---|---|---|---|
| `activityType` | string | no |  |
| `city` | string | no |  |
| `departureTime` | string | no |  |
| `description` | string | no |  |
| `destination` | [`IdeaPlaceVO`](#schema-ideaplacevo) | no |  |
| `duration` | string | no |  |
| `excluded` | array[string] | no |  |
| `geolocation` | [`GeolocationVO`](#schema-geolocationvo) | no |  |
| `id` | string | no |  |
| `imageUrls` | array[string] | no |  |
| `included` | array[string] | no |  |
| `internalRemark` | string | no |  |
| `meetingPoint` | string | no |  |
| `name` | string | no |  |
| `rating` | [`TicketRatingVO`](#schema-ticketratingvo) | no |  |
| `restrictions` | array[[`TicketRestrictionVO`](#schema-ticketrestrictionvo)] | no |  |
| `zipCode` | string | no |  |


### `TicketModalityPriceVO` {#schema-ticketmodalitypricevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `canBookAlone` | boolean | no |  |
| `code` | string | no |  |
| `name` | string | no |  |
| `quantity` | integer(int32) | no |  |


### `TicketQuestionVO` {#schema-ticketquestionvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `answer` | string | no |  |
| `id` | integer(int32) | no |  |
| `question` | string | no |  |
| `questionCode` | string | no |  |


### `TicketRatingVO` {#schema-ticketratingvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `averageRating` | number(double) | no |  |
| `totalReviews` | integer(int32) | no |  |


### `TicketRestrictionVO` {#schema-ticketrestrictionvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string | no |  |
| `value` | integer(int32) | no |  |


### `TotalRouteDistanceVO` {#schema-totalroutedistancevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `units` | string | no |  |
| `value` | number(double) | no |  |


### `TourDownloadMode` {#schema-tourdownloadmode}

Enum values: 'AUTOMATIC', 'ONLY_UPDATE', 'MANUAL'


### `TransferContractPrice` {#schema-transfercontractprice}

| Field | Type | Required | Description |
|---|---|---|---|
| `basePrice` | [`Money`](#schema-money) | yes |  |
| `childPrice` | [`Money`](#schema-money) | yes |  |
| `id` | integer(int32) | no |  |
| `infantPrice` | [`Money`](#schema-money) | yes |  |
| `occupancy` | integer(int32) | yes |  |
| `onRequest` | boolean | no |  |
| `priceByPax` | boolean | no |  |


### `TransferLocationVO` {#schema-transferlocationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string | no |  |
| `name` | string | no |  |


### `TransferProductType` {#schema-transferproducttype}

Enum values: 'ECONOMY', 'STANDARD', 'EXPRESS', 'SPECIAL', 'PREMIUM', 'LUXURY'


### `TransferPropertyType` {#schema-transferpropertytype}

Enum values: 'GENERIC', 'BAGS', 'AIRCONDITION', 'DURATION', 'PROVIDER', 'DOORTODOOR'


### `TransferServiceType` {#schema-transferservicetype}

Enum values: 'PRIVATE', 'SHUTTLE', 'SHARED'


### `TransferType` {#schema-transfertype}

Enum values: 'IN', 'OUT'


### `TransferVehicleType` {#schema-transfervehicletype}

Enum values: 'CAR', 'ELECTRIC_CAR', 'MINIBUS', 'BUS', 'BOAT', 'DISABLED', 'CAR_OR_BOAT', 'MINIBUS_OR_BOAT', 'CAR_OR_TRAIN', 'SPEEDBOAT', 'CATAMARAN', 'TRAIN', 'FLIGHT', 'MOTORBIKE', 'SUV', 'LIMOUSINE', 'CAR_4X4', 'TRUCK', 'SEDAN', 'STATION_WAGON', 'HIGH_CLASS_SEDAN', 'SUPERIOR_HIGH_CLASS_SEDAN', 'MPV', 'SEAPLANE', 'VAN', 'MINIVAN', 'AIRTAXI', 'CAR_AND_BOAT', 'VAN_AND_BOAT', 'MINIVAN_OR_BOAT'


### `TransferZoneType` {#schema-transferzonetype}

Enum values: 'AIRPORT', 'PORT', 'DESTINATION', 'POINT'


### `TranslationVO` {#schema-translationvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | no |  |
| `language` | [`Language`](#schema-language) | no |  |


### `TransmissionType` {#schema-transmissiontype}

Enum values: 'AUTOMATIC', 'MANUAL'


### `TransportBaseType` {#schema-transportbasetype}

Enum values: 'AIRPORT', 'PORT', 'TRAIN_STATION', 'BUS_STATION', 'MEETING_POINT'


### `TransportBaseVO` {#schema-transportbasevo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `code` | string | no |  |
| `country` | string | no |  |
| `geolocation` | [`GeolocationVO`](#schema-geolocationvo) | no |  |
| `transportBaseCityName` | [`TranslationVO`](#schema-translationvo) | no |  |
| `transportBaseName` | [`TranslationVO`](#schema-translationvo) | no |  |
| `type` | [`TransportBaseType`](#schema-transportbasetype) | no |  |


### `TransportFareBreakdownVO` {#schema-transportfarebreakdownvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `baseFareAmount` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `originalBaseFareAmount` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `originalTaxAmount` | [`MoneyVO`](#schema-moneyvo) | no |  |
| `personId` | string | no |  |
| `taxAmount` | [`MoneyVO`](#schema-moneyvo) | no |  |


### `TransportTechnicalStopVO` {#schema-transporttechnicalstopvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `arrivalDate` | string(date-time) | no |  |
| `departureDate` | string(date-time) | no |  |
| `providerTransportBaseDescription` | string | no |  |
| `transportBaseCode` | string | no |  |


### `TransportType` {#schema-transporttype}

Enum values: 'PLANE', 'TRAIN', 'BUS', 'CAR', 'BOAT', 'BUGGY', 'VAN', 'COMBINED'


### `TravelClubPointsPaymentVO` {#schema-travelclubpointspaymentvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `discountPoints` | number(double) | no |  |
| `money` | number(double) | no |  |
| `points` | integer(int32) | no |  |


### `TripDateSettingsVO` {#schema-tripdatesettingsvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `availRange` | [`LocalDateRangeVO`](#schema-localdaterangevo) | no |  |
| `departurePrices` | [`DeparturePricesVO`](#schema-departurepricesvo) | no |  |
| `operationDays` | [`OperationDaysVO`](#schema-operationdaysvo) | no |  |
| `releaseDays` | integer(int64) | no |  |
| `stopSales` | array[[`LocalDateRangeVO`](#schema-localdaterangevo)] | no |  |


### `TripQuotePersonVO` {#schema-tripquotepersonvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `adults` | integer(int32) | no |  |
| `children` | integer(int32) | no |  |
| `people` | integer(int32) | no |  |


### `TripQuoteReportRowVO` {#schema-tripquotereportrowvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `agency` | string | no |  |
| `agencyManager` | string | no |  |
| `availabilityId` | integer(int32) | no |  |
| `currency` | string | no |  |
| `days` | integer(int32) | no |  |
| `destinations` | string | no |  |
| `destinationsCount` | integer(int32) | no |  |
| `duration` | integer(int32) | no |  |
| `endDate` | string(date) | no |  |
| `from` | [`LocationVO`](#schema-locationvo) | no |  |
| `microsite` | string | no |  |
| `operator` | string | no |  |
| `people` | [`TripQuotePersonVO`](#schema-tripquotepersonvo) | no |  |
| `price` | number | no |  |
| `quoteDate` | string(date-time) | no |  |
| `sessionId` | string | no |  |
| `startDate` | string(date) | no |  |
| `to` | [`LocationVO`](#schema-locationvo) | no |  |
| `tripType` | [`TripType`](#schema-triptype) | no |  |
| `user` | string | no |  |


### `TripQuoteReportVO` {#schema-tripquotereportvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `searchTotals` | [`SearchTotalsVO`](#schema-searchtotalsvo) | no |  |
| `tripQuotes` | array[[`TripQuoteReportRowVO`](#schema-tripquotereportrowvo)] | no |  |


### `TripType` {#schema-triptype}

Enum values: 'MULTI', 'GROUPS', 'ONLY_HOTEL', 'ONLY_HOUSE', 'ONLY_FLIGHT', 'ONLY_TRAIN', 'FLIGHT_HOTEL', 'FLIGHT_HOUSE', 'ONLY_TICKET', 'EVENT_TICKET', 'GOLF', 'ONLY_CAR', 'ONLY_TRANSFER', 'HOLIDAYS', 'GIFTCARD', 'EXTERNAL_SEARCH_BOX', 'GIFT_BOX', 'ROUTING', 'PRIVATE_TOUR', 'MAGIC_BOX', 'CRUISES', 'AI_TRIP', 'MEMBERSHIP', 'ONLY_INSURANCE', 'ONLY_ITEM', 'TRIP_PLANNER', 'ESIM', 'ONLY_CAMPER'


### `Type` {#schema-type}

Enum values: 'MAIN', 'SECONDARY'


### `UserAgencyVO` {#schema-useragencyvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `addressText` | string | no |  |
| `agencyManagerExternalId` | string | no |  |
| `agencyManagerId` | integer(int32) | no |  |
| `businessName` | string | no |  |
| `city` | string | no |  |
| `contactPersonLastName` | string | no |  |
| `contactPersonName` | string | no |  |
| `country` | [`CountryVO`](#schema-countryvo) | no |  |
| `deferredDays` | integer(int32) | no |  |
| `deferredLimit` | number(double) | no |  |
| `deferredPaymentAllowed` | boolean | no |  |
| `documentNumber` | string | no |  |
| `email` | string | no |  |
| `externalId` | string | no |  |
| `fee` | [`FeeVO`](#schema-feevo) | no |  |
| `hotel` | [`HotelVO`](#schema-hotelvo) | no |  |
| `internalRemarks` | string | no |  |
| `invoiceType` | string | no |  |
| `logoContentType` | string | no |  |
| `logoUrl` | string | no |  |
| `managementGroup` | string | no |  |
| `name` | string | no |  |
| `phoneNumber` | string | no |  |
| `postalCode` | string | no |  |
| `providerConfigs` | array[[`AgencyProviderConfigVO`](#schema-agencyproviderconfigvo)] | no |  |
| `province` | string | no |  |
| `region` | string | no |  |
| `socialNetworks` | [`SocialNetworksVO`](#schema-socialnetworksvo) | no |  |
| `taxes` | number(double) | no |  |


### `UserPermitVO` {#schema-userpermitvo}

_Permits the authenticated user may grant as custom permits. Use each item's id in `userPermits.custom`_

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | no |  |
| `id` | string | no |  |


### `UserPermitsCatalogVO` {#schema-userpermitscatalogvo}

_Profiles and permits the authenticated user may assign when creating or updating another user._

| Field | Type | Required | Description |
|---|---|---|---|
| `assignablePermits` | array[[`UserPermitVO`](#schema-userpermitvo)] | no | Permits the authenticated user may grant as custom permits. Use each item's id in `userPermits.custom` |
| `profiles` | array[[`UserProfilePermitsVO`](#schema-userprofilepermitsvo)] | no | Assignable profiles and the permit IDs included in each one. Values sent in `userPermits.disabled` must come from the permits list of the selected profile. |


### `UserPermitsVO` {#schema-userpermitsvo}

_Permission overrides for the selected profile. Omitted or null properties preserve their current values, while empty arrays clear them._

| Field | Type | Required | Description |
|---|---|---|---|
| `custom` | array[string] | no | Additional permit IDs granted to the user. Each value must come from `assignablePermits[].id`   returned by *GET /user/permits* and must not already be included in the selected profile. |
| `disabled` | array[string] | no | Permit IDs disabled for the user. Each value must come from `profiles[].permits`   returned by *GET /user/permits* for the selected profile. |


### `UserProfilePermitsVO` {#schema-userprofilepermitsvo}

_An assignable user profile and the permits it includes by default._

| Field | Type | Required | Description |
|---|---|---|---|
| `permits` | array[string] | no | Permit IDs included by this profile. Only these values may be sent in userPermits.disabled when this profile is selected. |
| `profile` | [`UserProfileVO`](#schema-userprofilevo) | no |  |


### `UserProfileVO` {#schema-userprofilevo}

Enum values: 'CORPORATE_ADMIN', 'OPERATOR', 'OPERATOR_CALLCENTER', 'TRAVEL_AGENCY_OPERATOR', 'MICROSITE_ADMIN', 'SUBMICROSITE_ADMIN', 'DMC', 'DMC_MASTER', 'DMC_ALL_IN', 'CALLCENTER', 'AGENCY_MANAGER', 'HOTEL_ADMIN', 'AGENT', 'TRAVELAGENT', 'MARKETING', 'AGENT_COLLECTIVE', 'TRIPNGINE_AGENT', 'BTP_MANAGER', 'BTP_AUTHORIZER', 'BTP_ASSISTANT', 'BTP_FINANCE_USER', 'BTP_EMPLOYEE', 'CONTRACT_SUPPLIER', 'QR', 'QUOTER', 'USER', 'WSUSER_WR'


### `UserProviderConfigVO` {#schema-userproviderconfigvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `extraInfo` | string | no |  |
| `provider` | [`Provider`](#schema-provider) | no |  |


### `UserRewardsVO` {#schema-userrewardsvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amount` | integer(int32) | no |  |
| `bookingId` | string | no |  |
| `creationDate` | string(date-time) | no |  |
| `expirationDate` | string(date) | no |  |
| `lastUpdate` | string(date-time) | no |  |
| `remark` | string | no |  |
| `status` | [`MovementStatus`](#schema-movementstatus) | no |  |
| `type` | [`MovementType`](#schema-movementtype) | no |  |
| `valueDate` | string(date) | no |  |


### `UserVO` {#schema-uservo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `agency` | [`UserAgencyVO`](#schema-useragencyvo) | no |  |
| `b2c` | boolean | no |  |
| `country` | [`CountryVO`](#schema-countryvo) | no |  |
| `email` | string | no |  |
| `externalCode` | string | no |  |
| `microsite` | string | no |  |
| `name` | string | no |  |
| `operator` | string | no |  |
| `password` | string | no |  |
| `providerConfigs` | array[[`UserProviderConfigVO`](#schema-userproviderconfigvo)] | no |  |
| `surname` | string | no |  |
| `telephone` | string | no |  |
| `username` | string | no |  |


### `UsersVO` {#schema-usersvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `active` | boolean | no |  |
| `additionalAuthorizers` | array[string] | no |  |
| `agency` | string | no |  |
| `agencyManager` | [`AgencyManagerVO`](#schema-agencymanagervo) | no |  |
| `annualExpenseLimit` | number(double) | no |  |
| `assistant` | string | no |  |
| `authorizer` | string | no |  |
| `b2c` | boolean | no |  |
| `birthDate` | string(date) | no |  |
| `centerOfCost` | string | no |  |
| `companyPosition` | string | no |  |
| `country` | string | no |  |
| `courtesyTitle` | [`CourtesyTitle`](#schema-courtesytitle) | no |  |
| `defaultGroundServicesManagementFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `defaultManagementFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `defaultTransportServicesManagementFee` | [`FeeAmountVO`](#schema-feeamountvo) | no |  |
| `documentNumber` | string | no |  |
| `email` | string | no |  |
| `externalCode` | string | no |  |
| `externalLoginId` | string | no |  |
| `headquarters` | string | no |  |
| `name` | string | no |  |
| `newsletter` | boolean | no |  |
| `optionalAssistant` | boolean | no |  |
| `password` | string | no |  |
| `phoneCountryCode` | string | no |  |
| `profile` | [`UserProfileVO`](#schema-userprofilevo) | yes |  |
| `providerConfigs` | array[[`UserProviderConfigVO`](#schema-userproviderconfigvo)] | no |  |
| `referralId` | string | no |  |
| `remarks` | string | no | Remarks from the user profile. On updates, omit or send null to keep the current value; send an empty string to clear it. |
| `rewards` | array[[`UserRewardsVO`](#schema-userrewardsvo)] | no |  |
| `surname` | string | no |  |
| `telephone` | string | no |  |
| `travelPolicy` | string | no |  |
| `userPermits` | [`UserPermitsVO`](#schema-userpermitsvo) | no |  |
| `username` | string | yes |  |


### `WarningType` {#schema-warningtype}

Enum values: 'PRICE_CHANGE', 'CANCELLATION_POLICIES_CHANGE'


### `WarningVO` {#schema-warningvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | no |  |
| `type` | [`WarningType`](#schema-warningtype) | no |  |


### `WithHoldingVO` {#schema-withholdingvo}

| Field | Type | Required | Description |
|---|---|---|---|
| `amount` | [`MoneyAmountVO`](#schema-moneyamountvo) | no |  |
| `name` | string | no |  |
