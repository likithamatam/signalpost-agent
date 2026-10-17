# Signalpost Agent: Norwegian Company Profiles

Takes Norwegian organisation numbers and returns company facts, each with a source link and retrieval date.

## Run (one command)

```bash
python agent.py
```

Python 3.8+, standard library only, no install step and no API keys.

Other options:

```bash
python agent.py --input orgnrs.txt              # your own list, one org number per line
python agent.py --input orgnrs.txt --financials # also fetch annual accounts
```

Output: `profiles.jsonl`, one JSON profile per line.

## How it works

- **Sources:** only the official Brønnøysund Register Centre open APIs: Enhetsregisteret (`data.brreg.no/enhetsregisteret/api/enheter/{orgnr}`) and, with `--financials`, Regnskapsregisteret.
- **Right company:** the response's organisation number must equal the requested one, otherwise nothing is published.
- **No fabrication:** values are copied from the API response. Missing values are left out, never estimated.
- **Evidence:** every fact has `source_url` and `retrieved_at`.
- **Keeping profiles current:** rerunning compares fresh data with the saved profile, then records changed fields and dates under `changes`. Deleted or bankrupt entities are marked.
- **Explanations:** each profile has a short `explanation` of what was found and where it came from.

## Model / API details

- No LLM is used. Data source: Brreg open data (public, free, no key).

## Expected run costs

- External API cost: $0.
- Requests: 1 per company (2 with `--financials`). A 100-company daily test uses about 100-200 requests, well under the 2,000 limit, and runs in about a minute, well under 45 minutes.
- Building 1,000 profiles uses about 1,010 requests.

## Profile format

```json
{
  "organisation_number": "123456789",
  "name": "Example AS",
  "facts": [{"field": "industry", "value": "...", "source_url": "...", "retrieved_at": "..."}],
  "explanation": "...",
  "changes": [],
  "last_checked": "...",
  "last_updated": "..."
}
```
