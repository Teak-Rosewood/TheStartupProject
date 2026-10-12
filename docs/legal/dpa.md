# Data Processing Agreement

> **Template, not legal advice.** This is a starting point written for a software startup based in India that also serves customers abroad. Replace every placeholder written in double curly braces (see `company.example.toml`), delete sections that do not apply, and have a qualified lawyer in your jurisdiction review it before you publish. Laws change; items marked **(verify)** are especially time-sensitive.

This DPA forms part of the agreement between **{{company_legal_name}}** ("Processor", "we") and the customer ("Controller") for {{product_name}}. It applies when we process personal data on the Controller's behalf. Terms such as "personal data", "processing", "controller" and "processor" have the meaning in the GDPR; for Indian law, read "Data Fiduciary" for controller and "Data Processor" for processor.

## 1. Scope
- **Subject matter and nature:** hosting and processing Controller content and end-user data to provide the Service.
- **Duration:** the term of the agreement plus the deletion period in section 8.
- **Categories of data subjects:** the Controller's users, customers and staff.
- **Types of personal data:** identifiers and contact details, account and usage data, and any data the Controller submits. No special categories unless agreed in writing.

## 2. Instructions
We process personal data only on the Controller's documented instructions (the agreement, this DPA, and use of the Service's features), unless the law requires otherwise, in which case we tell the Controller first where permitted. We tell the Controller if an instruction appears to infringe data protection law.

## 3. Confidentiality
Everyone authorized to process the data is bound by confidentiality.

## 4. Security
We implement the measures in **Annex II** and review them regularly.

## 5. Sub-processors
The Controller authorizes the sub-processors in our [sub-processors list](subprocessors.md). We give at least 30 days' notice (for example by email or on that page) before adding or replacing one. The Controller may object on reasonable data-protection grounds within that period; if unresolved, either party may terminate the affected service. We impose equivalent data-protection obligations on sub-processors and remain responsible for them.

## 6. Assistance
Taking into account the nature of processing, we help the Controller respond to data-subject requests (access, correction, erasure, portability, restriction, objection), carry out impact assessments and consult regulators, using the self-service export and deletion features where possible.

## 7. Personal data breach
We notify the Controller without undue delay and within {{breach_notice_hours}} hours of becoming aware of a breach affecting its personal data, with the information we have, and keep it updated.

## 8. Return and deletion
On termination, the Controller can export its data for 30 days; afterwards we delete or anonymize it, and it leaves backups within the cycle in the [retention schedule](data-retention.md), unless the law requires retention.

## 9. Audits
We make available information needed to show compliance and allow reasonable audits, no more than once a year with 30 days' notice, during business hours and subject to confidentiality. Current security reports or certifications may satisfy this.

## 10. International transfers
Primary hosting is in {{data_region}}. Where personal data from the EEA, UK or Switzerland is transferred to a country without an adequacy decision, the Standard Contractual Clauses (and the UK addendum) are incorporated by reference, with Module Two (controller to processor) and Module Three where applicable. Complete the SCC annexes with counsel.

## 11. Liability
Each party's liability under this DPA is subject to the limitations in the agreement.

## Annex I — parties and contacts
Controller: as in the order form. Processor: {{company_legal_name}}, {{registered_address}}. Privacy contact: {{dpo_email}}. Supervisory authority: that of the Controller's establishment.

## Annex II — technical and organizational measures
- Encryption in transit (TLS 1.2+) and at rest (AWS KMS-managed keys)
- Identity: MFA required for staff, least-privilege roles, no shared accounts, separate staff identity pool
- Logging: append-only audit log of administrative actions; security logs retained {{log_retention_days}} days
- Backups: continuous backup with point-in-time recovery, tested by a recurring restore drill
- Network: no database exposure to the internet; traffic enters through a CDN with managed protections
- Vulnerability management: automated dependency and secret scanning, prompt patching
- Incident response: documented procedure, see [incident-response](incident-response.md)
- Physical security: provided by our cloud provider's certified data centres
