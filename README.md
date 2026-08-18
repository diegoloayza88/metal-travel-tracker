# 🤘 Metal Travel Tracker

A serverless AWS system that tracks metal concerts and festivals across 11 countries, scores
them against a personal band watchlist, finds flight and hotel deals from Lima, Peru, and sends
a weekly report by email, SMS, and Discord.

## Architecture

```
EventBridge Scheduler (weekly, Sundays 8am Lima / UTC-5)
        │
        ▼
Orchestrator Lambda
├── Collects concerts from 3 active plugins (Ticketmaster, SerpAPI Events, Festivals)
├── Classifies/deduplicates results and scores them against the watchlist
├── Invokes Flight Agent Lambda for the top pending concerts
│     └── Invokes Hotel Agent Lambda for each confirmed flight deal
└── Invokes Reporter Agent Lambda (async) to generate and send the report

Reporter Agent
└── Uses Amazon Bedrock (Claude) to draft a natural-language report, then sends it via:
    ├── SMS     → AWS SNS
    ├── Email   → AWS SES
    └── Discord → Webhook

WhatsApp Parser Lambda (independent, S3-triggered)
└── Parses uploaded WhatsApp chat exports (.txt) and extracts concert announcements via Bedrock

Streamlit Dashboard (src/dashboard/)
└── Interactive read-only view of concerts, festivals, flight price history, and budget estimates
```

All Lambdas run Python 3.13 and share a `dynamodb_client.py` / `user_config.py` layer.

## Concert sources (active plugins)

| Plugin | Source | Notes |
|---|---|---|
| `TicketmasterPlugin` | Ticketmaster Discovery API | Official, `classificationName=metal` server-side filter |
| `SerpApiEventsPlugin` | Google Events (via SerpAPI) | Strongest coverage for CO/CL/BR/MX; localized queries per country |
| `FestivalsPlugin` | 7 hand-picked festival websites | Scrapes official lineups, Bedrock extracts confirmed bands, 7-day DynamoDB cache |

Legacy/disabled plugins kept for reference (`bandsintown.py`, `eventbrite.py`, `songkick.py`,
`metal_archives.py`) — deprecated APIs or IP-blocked from AWS.

## Genres monitored
Black Metal · Death Metal · War Metal · Heavy Metal · Thrash Metal

## Countries monitored

🇨🇴 Colombia · 🇨🇱 Chile · 🇧🇷 Brazil · 🇺🇸 United States · 🇲🇽 Mexico · 🇫🇮 Finland ·
🇪🇸 Spain · 🇳🇴 Norway · 🇩🇪 Germany · 🇬🇷 Greece · 🇷🇴 Romania

## Watchlist & recommendations

- `src/shared/user_config.py` holds a curated watchlist of ~57 bands (black/death/war/thrash
  metal), editable and persisted in DynamoDB (`CONFIG#USER` item) so it survives redeploys.
- Concerts get a `watchlist_score`: exact band match (10.0), partial match (8.0), or a
  **genre-based discovery score** (4.0–5.0) when the concert matches a preferred genre without
  matching a named band — surfaced in the report as a dedicated "Discovery" section so the
  system can recommend bands outside the watchlist, not just repeat known names.

## Flight origin
Lima, Peru (LIM — Jorge Chávez International Airport)

---

## Prerequisites

- AWS CLI configured with a profile that has sufficient permissions
- Terraform >= 1.14 with a Terraform Cloud account (workspace-based state + remote plan/apply)
- Python >= 3.13
- `pip install -r requirements.txt`

## APIs required

| API | Where to get it | Required? |
|---|---|---|
| Ticketmaster Discovery API Key | https://developer.ticketmaster.com | Yes |
| SerpAPI Key | https://serpapi.com | Yes (used for both concert search and flight/hotel search) |
| Amadeus API (Client ID + Secret) | https://developers.amadeus.com | Yes |
| Booking.com Affiliate ID | https://join.booking.com/affiliateprogram | Optional (falls back to search-URL links) |
| AWS Bedrock access | AWS Console → Bedrock → Model access | Yes (Claude Sonnet, cross-region inference profile) |
| Discord Webhook | Your Discord server → Channel Settings → Integrations | Yes |

---

## Initial setup

```bash
# 1. Clone the repository
git clone <your-fork-url>
cd metal-travel-tracker

# 2. Run the setup script (checks dependencies, logs into Terraform Cloud,
#    creates terraform/terraform.tfvars from a template)
chmod +x scripts/setup.sh
./scripts/setup.sh

# 3. Fill in terraform/terraform.tfvars and terraform/providers.tf
#    (Terraform Cloud org name), then deploy
cd terraform
terraform init
terraform plan
terraform apply
```

### GitHub Actions secrets (for CI/CD)

| Secret | Purpose |
|---|---|
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Uploads Lambda packages to S3 and updates function code on deploy |
| `TF_API_TOKEN` | Terraform Cloud user API token (Terraform Plan/Apply run remotely in HCP Terraform) |
| `DISCORD_WEBHOOK_URL` | Deploy success notification |

---

## Uploading a WhatsApp export

```bash
# Export the chat from your phone as .txt, then upload it to the project's S3 bucket:
aws s3 cp "Metal Colombia Chat Export.txt" \
  s3://metal-travel-tracker-prod-whatsapp-exports/colombia/$(date +%Y-%m-%d).txt
```

The S3 upload event automatically triggers the WhatsApp Parser Lambda.

---

## Running the dashboard

```bash
pip install -r src/dashboard/requirements.txt
bash src/dashboard/run_local.sh
# Opens at http://localhost:8501
```

Sections: Concerts (filterable table + charts), Festivals (lineups + budget), Flights & Prices
(historical trend per route), Budget calculator, and a Control panel to trigger the orchestrator
manually and view recent runs.

---

## Project structure

```
metal-travel-tracker/
├── terraform/               # All AWS infrastructure as code
│   └── *.tf                 # Lambdas, DynamoDB, S3, EventBridge, IAM, Secrets Manager
├── src/
│   ├── agents/               # orchestrator, flight_agent, hotel_agent, reporter_agent, whatsapp_parser
│   ├── plugins/               # Concert source connectors (Ticketmaster, SerpAPI, Festivals + legacy)
│   ├── processors/           # WhatsApp export parser
│   ├── models/                # Shared dataclasses (Concert, Flight, Hotel, ...)
│   ├── shared/                # DynamoDB client, user preferences/watchlist, Bedrock client, secrets
│   └── dashboard/              # Streamlit app + reusable data-access layer
├── tests/                    # pytest suite (unit tests, mocked AWS via moto)
├── .github/workflows/        # CI/CD pipeline
└── scripts/                  # Setup helper script
```

---

## Data model (DynamoDB)

Single-table design per resource type:

| Table | Partition key | Notes |
|---|---|---|
| `metal-travel-tracker-prod-concerts` | `CONCERT#{country}` / `CONFIG#USER` / `FESTIVAL_CACHE#{name}` | Concerts, user preferences (watchlist), festival lineup cache |
| `metal-travel-tracker-prod-flight-prices` | `PRICE#{origin}#{destination}` | Historical flight price points, used for deal-quality percentiles |
| `metal-travel-tracker-prod-notified-deals` | — | Dedup tracking for already-notified deals |

---

## CI/CD

GitHub Actions workflow (`.github/workflows/`) runs on every PR and push to `main`:

1. **Lint & Tests** — `ruff check` / `ruff format --check`, then `pytest tests/`
2. **Terraform Validate** — `terraform fmt -check` + `terraform validate`
3. **Terraform Plan** (PRs only) — runs remotely in HCP Terraform, posts the plan as a PR comment
4. **Deploy** (`main` pushes only) — builds Lambda zips, uploads to S3, updates function code,
   then `terraform apply` (also runs remotely in HCP Terraform)

---

## Monitoring

- **CloudWatch Dashboard**: per-Lambda metrics (invocations, errors, duration)
- **CloudWatch Alarms**: SNS email alert if the orchestrator errors out or misses its weekly run
- **CloudWatch Logs**: structured logs per Lambda, searchable via `aws logs filter-log-events`

---

## Contributing

This is a personal project. To add a new concert source plugin, implement the
`ConcertSourcePlugin` interface in `src/plugins/base.py` and register it in
`src/plugins/__init__.py`.
