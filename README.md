## Everflow Mailer Access Key extractor (API-first)

`everflow_mailer_key_extractor.py` fetches **Mailer Access Key** links for a list of offer IDs.

### What it does
- Tries API authentication first (Bearer token preferred, email/password fallback).
- Queries known offer API endpoints.
- Searches each offer payload for the Mailer Access Key URL.
- Processes offers in parallel (`--concurrency`, default `5`).
- Prints CSV to stdout and optionally saves to a file.

### Usage

```bash
python everflow_mailer_key_extractor.py \
  --email "you@example.com" \
  --password "your-password" \
  --offer-id 123 --offer-id 456,789 \
  --concurrency 5 \
  --output mailer_links.csv
```

Or with token (recommended):

```bash
python everflow_mailer_key_extractor.py \
  --token "YOUR_BEARER_TOKEN" \
  --offer-id-file offer_ids.txt \
  --concurrency 5
```

### Inputs needed
- Preferred: API token (`--token`).
- Alternative: email + password (`--email`, `--password`) so the script can try common login endpoints.

### Notes
- This script is API-only. If your account uses different endpoints, add them in `LOGIN_ENDPOINTS` / `OFFER_ENDPOINTS`.
- If the Mailer Access Key is not in API responses, UI automation is the fallback.
