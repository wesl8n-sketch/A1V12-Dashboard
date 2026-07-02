# A1V12 Dashboard

Automated Yahoo Finance data pipeline + dashboard for the MWM/Tactical model suite.
Nightly rebuild via GitHub Actions, hosted on Cloudflare Pages behind Cloudflare Access.

## Repo layout

```
.github/workflows/rebuild-dashboard.yml   CI: rebuild, audit-gate, deploy, email on failure
Scripts/a1v12_yahoo_v3_2.py               The pipeline (download prices -> signals -> trades -> portfolios -> dashboard)
Config/MWM_Allocations.csv                Model allocation weights (Model, Asset, Weight)
requirements.txt                          Python dependencies
Data/                                     Generated CSVs (created by the script, not committed empty)
Dashboard/                                Generated dashboard HTML (this is what gets deployed)
Audit/                                    Generated audit CSVs (Data_Audit, Production_Audit, Backfill_Scale_Audit)
Backups/                                  Generated timestamped snapshots (gitignored -- not committed)
```

`Data/`, `Dashboard/`, and `Audit/` start with only a `.gitkeep` placeholder — the script creates
the real files the first time it runs. `Backups/` is excluded entirely via `.gitignore`.

## One-time setup

### 1. Repo secrets
Settings -> Secrets and variables -> Actions -> New repository secret. Add all of these:

| Secret | Where to get it |
|---|---|
| `SMTP_SERVER` | Your email provider's SMTP host, e.g. `smtp.gmail.com` |
| `SMTP_PORT` | Usually `465` |
| `SMTP_USERNAME` | The sending email address |
| `SMTP_PASSWORD` | An app password (not your real password) |
| `NOTIFY_EMAIL` | Where failure alerts should go |
| `CLOUDFLARE_API_TOKEN` | Cloudflare dashboard -> My Profile -> API Tokens -> Create Token -> Custom -> Account / Cloudflare Pages / Edit |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare dashboard -> any domain's Overview page, right sidebar |

### 2. Cloudflare Pages project
Either create it manually (Workers & Pages -> Create -> Pages -> Direct Upload, name it
`a1v12-dashboard` to match the workflow), or just run the workflow once — `wrangler pages deploy`
creates the project automatically if it doesn't exist yet.

### 3. Cloudflare Access (who's allowed to view it)
Zero Trust -> Access -> Applications -> Add an application -> Self-hosted. Point it at your
Pages URL, add an Include -> Emails policy listing exactly who should be able to view the
dashboard. Without this step the Pages URL is publicly viewable by anyone with the link.

## Running it

- **Automatically**: the workflow runs weekdays at 21:30 UTC (~4:30pm ET).
- **Manually**: Actions tab -> "Rebuild A1V12 Dashboard" -> Run workflow.
- **Locally**: `pip install -r requirements.txt && python Scripts/a1v12_yahoo_v3_2.py`
  (run from the repo root, so the script's relative paths resolve correctly).

A run only commits and deploys if `Audit/Production_Audit.csv` comes back with no `FAIL` rows.
If it fails, you'll get an email at `NOTIFY_EMAIL` and nothing gets published — the live
dashboard keeps showing the last successful build.
