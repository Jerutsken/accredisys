# IS ACCREDISYS — Project Foundation


Foundation codebase derived from the group's **SRS**, **SPMP**, and project notes
(Information Systems Accreditation Compliance and Evidence Management System, UST CICS, 3ISC Group 4).

## Run
```bash
pip install -r requirements.txt        # Flask and QR code generation
python seed.py                         # creates DB + SAMPLE data, prints demo TOTP secrets
flask --app app run                    # http://127.0.0.1:5000
python -m unittest discover -s tests -t .
```
Set `ACCREDISYS_SECRET` in any real deployment. Use HTTPS (SRS 3.4).

## Stack decision (SPMP says "not yet confirmed")
Python/Flask + SQLite, chosen for ₱0 cost (SPMP Table C-1) and lightweight dependencies.
Schema is plain SQL, so moving to PostgreSQL/MySQL (SRS 3.3) is a small change. **Confirm with your adviser.**

## What is built vs. SRS
| SRS item | Where | Status |
|---|---|---|
| FR-01, BR-6 password + authenticator (TOTP, RFC 6238) | `auth.py`, `security.py` | Done, tested vs RFC vector; account creation shows a local QR code and one-time setup key |
| FR-02, BR-1 RBAC | `security.PERMISSIONS` | Done (Chair, Faculty, Evaluator) |
| FR-03/04 upload + metadata (FR-03.2) | `repository.py` | Done |
| FR-05 search & filters | `repository.search` | Done |
| FR-10, BR-3/4/5 versioning, Pending Review, remarks, no overwrite/delete | `repository.py`, `approvals.py`, DB triggers | Done |
| FR-07 approval + signatories | `approvals.py` | Done |
| FR-06 requirement tracking, auto-status on approval | `requirements.py` | Done |
| FR-08, BR-7 faculty profile/history, privacy scoping | `faculty.py` | Done (row-level) |
| FR-09 dashboard + CSV report | `dashboard.py` | Done (CSV; PDF later) |
| FR-11, BR-8 backup/restore | `admin.py` | Done (call `make_backup()` from cron for scheduled) |
| FR-12, BR-2 account lifecycle | `admin.py` | Done (soft deactivate) |
| BR-10 evaluator temporary read-only access | `admin.py`, `repository.py` | Done |
| Revision requests (Chair -> Faculty use case) | `approvals.py` | Done |
| Audit trail | `audit_log` | Done |

## Not yet built (next iterations)
Web UI (SRS 3.1 screens), email notifications, scheduled-backup job,
encryption at rest, password reset, hosting/deploy config.

## Open questions from your documents (they affect design)
1. **Intended users unresolved** — SRS limitation 1 and your meeting notes: Chair confirmation pending. Role model is easy to change in `PERMISSIONS`.
2. **Interview (Oct 9) says the Chair is the main respondent**, yet the SRS has Faculty as a primary actor. Reconcile before the SDD.
3. **Dashboard scope** — Chair asked for enrollee/graduate stats with graphs (Sept 11); only a `department_stats` table exists, no charts.
4. **International opportunities** (internships, partnerships) — requested but excluded as "not yet a defined requirement".
5. **Evaluator requests** — Chair wants email/letter; the system just records approvals.
6. **Category tree** is a placeholder; the Chair asked for main categories + subcategories and will advise.
7. **Data Privacy**: SRS "RA 10173" is the Data Privacy Act; BR-7 reads "restricted to *unauthorized* roles" (typo for *authorized*).
8. Maintenance ownership after SE2 was raised by the Chair; SPMP assigns it to "existing departmental personnel" — get that confirmed.
