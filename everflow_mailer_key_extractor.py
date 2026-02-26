#!/usr/bin/env python3
"""Extract Mailer Access Key links for Everflow offers using API calls only.

The script tries to authenticate with API/login endpoints and then fetches each
offer concurrently (default: 5 workers).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.cookiejar import CookieJar
from typing import Any, Iterable


def normalize_base_url(url: str) -> str:
    return url.rstrip("/")


def parse_offer_ids(raw_items: Iterable[str]) -> list[str]:
    ids: list[str] = []
    for item in raw_items:
        parts = [piece.strip() for piece in item.split(",")]
        ids.extend([piece for piece in parts if piece])
    deduped: list[str] = []
    seen: set[str] = set()
    for offer_id in ids:
        if offer_id not in seen:
            deduped.append(offer_id)
            seen.add(offer_id)
    return deduped


@dataclass
class OfferResult:
    offer_id: str
    mailer_access_key_link: str | None
    source_endpoint: str | None
    error: str | None = None


class EverflowClient:
    """Small HTTP client for Everflow API-first extraction."""

    LOGIN_ENDPOINTS = [
        "/api/auth/login",
        "/api/authentication/login",
        "/api/v1/auth/login",
        "/api/partner/authentication/login",
    ]

    OFFER_ENDPOINTS = [
        "/api/v1/offers/{offer_id}",
        "/api/affiliate/offers/{offer_id}",
        "/api/partners/offers/{offer_id}",
        "/api/offers/{offer_id}",
    ]

    MAILER_URL_HINTS = (
        "affiliateaccesskey.com",
        "mailer",
        "accesskey",
    )

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        email: str | None = None,
        password: str | None = None,
        timeout_seconds: int = 25,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.timeout_seconds = timeout_seconds
        self.cookie_jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookie_jar))
        self.default_headers: dict[str, str] = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "everflow-mailer-key-extractor/1.0",
        }
        if token:
            self.default_headers["Authorization"] = f"Bearer {token}"

        if "Authorization" not in self.default_headers and email and password:
            self.authenticate(email, password)

    def request_json(
        self,
        path_or_url: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            url = path_or_url
        else:
            url = f"{self.base_url}{path_or_url}"

        headers = dict(self.default_headers)
        if extra_headers:
            headers.update(extra_headers)

        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url=url, data=data, method=method.upper(), headers=headers)

        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                status_code = response.getcode()
                content = response.read().decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(content) if content else {}
                except json.JSONDecodeError:
                    parsed = {"raw": content}
                return status_code, parsed
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            try:
                parsed_error = json.loads(body) if body else {}
            except json.JSONDecodeError:
                parsed_error = {"raw": body}
            return exc.code, parsed_error

    def authenticate(self, email: str, password: str) -> None:
        payload_candidates = [
            {"email": email, "password": password},
            {"username": email, "password": password},
        ]

        errors: list[str] = []
        for endpoint in self.LOGIN_ENDPOINTS:
            for payload in payload_candidates:
                status_code, data = self.request_json(endpoint, method="POST", payload=payload)
                if status_code >= 400:
                    errors.append(f"{endpoint} ({status_code})")
                    continue

                token = self._extract_token(data)
                if token:
                    self.default_headers["Authorization"] = f"Bearer {token}"
                return

        raise RuntimeError(
            "Authentication failed on all known login endpoints. "
            "Provide --token or inspect endpoint variants. Tried: " + ", ".join(errors)
        )

    @staticmethod
    def _extract_token(data: Any) -> str | None:
        if not isinstance(data, dict):
            return None

        direct_keys = ["token", "access_token", "jwt", "auth_token"]
        for key in direct_keys:
            value = data.get(key)
            if isinstance(value, str) and value:
                return value

        for nested_key in ("data", "result", "response"):
            nested = data.get(nested_key)
            if isinstance(nested, dict):
                for key in direct_keys:
                    value = nested.get(key)
                    if isinstance(value, str) and value:
                        return value
        return None

    def get_mailer_access_key(self, offer_id: str) -> OfferResult:
        for endpoint_template in self.OFFER_ENDPOINTS:
            endpoint = endpoint_template.format(offer_id=urllib.parse.quote(str(offer_id)))
            status_code, data = self.request_json(endpoint)
            if status_code >= 400:
                continue

            link = self._find_mailer_link(data)
            if link:
                return OfferResult(offer_id=offer_id, mailer_access_key_link=link, source_endpoint=endpoint)

        return OfferResult(
            offer_id=offer_id,
            mailer_access_key_link=None,
            source_endpoint=None,
            error="Mailer Access Key link not found in known API responses/endpoints",
        )

    def _find_mailer_link(self, payload: Any) -> str | None:
        url_pattern = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)

        def scan(value: Any) -> str | None:
            if isinstance(value, dict):
                for key, nested_value in value.items():
                    if isinstance(nested_value, str):
                        candidate = nested_value.strip()
                        key_lower = key.lower()
                        candidate_lower = candidate.lower()
                        if candidate.startswith(("http://", "https://")) and (
                            "mailer" in key_lower or "access" in key_lower or any(h in candidate_lower for h in self.MAILER_URL_HINTS)
                        ):
                            return candidate
                        match = url_pattern.search(candidate)
                        if match:
                            extracted = match.group(0)
                            if any(h in extracted.lower() for h in self.MAILER_URL_HINTS):
                                return extracted
                    found = scan(nested_value)
                    if found:
                        return found
            elif isinstance(value, list):
                for item in value:
                    found = scan(item)
                    if found:
                        return found
            elif isinstance(value, str):
                match = url_pattern.search(value)
                if match:
                    extracted = match.group(0)
                    if any(h in extracted.lower() for h in self.MAILER_URL_HINTS):
                        return extracted
            return None

        return scan(payload)


def run_extraction(args: argparse.Namespace) -> list[OfferResult]:
    offer_ids = parse_offer_ids(args.offer_id)
    if args.offer_id_file:
        with open(args.offer_id_file, "r", encoding="utf-8") as infile:
            offer_ids.extend(parse_offer_ids(line.strip() for line in infile if line.strip()))

    if not offer_ids:
        raise ValueError("No offer IDs provided. Use --offer-id and/or --offer-id-file.")

    # Preserve order after merging from CLI/file.
    offer_ids = parse_offer_ids(offer_ids)

    client = EverflowClient(
        args.base_url,
        token=args.token,
        email=args.email,
        password=args.password,
        timeout_seconds=args.timeout,
    )

    results: list[OfferResult] = [
        OfferResult(offer_id=offer_id, mailer_access_key_link=None, source_endpoint=None) for offer_id in offer_ids
    ]

    index_by_offer = {result.offer_id: i for i, result in enumerate(results)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        future_to_offer = {executor.submit(client.get_mailer_access_key, offer_id): offer_id for offer_id in offer_ids}
        for future in concurrent.futures.as_completed(future_to_offer):
            offer_id = future_to_offer[future]
            try:
                result = future.result()
            except Exception as exc:  # defensive, keeps processing remaining IDs
                result = OfferResult(
                    offer_id=offer_id,
                    mailer_access_key_link=None,
                    source_endpoint=None,
                    error=f"Unhandled error: {exc}",
                )
            results[index_by_offer[offer_id]] = result

    return results


def write_results(results: list[OfferResult], output_path: str | None) -> None:
    headers = ["offer_id", "mailer_access_key_link", "source_endpoint", "error"]
    rows = [
        {
            "offer_id": result.offer_id,
            "mailer_access_key_link": result.mailer_access_key_link or "",
            "source_endpoint": result.source_endpoint or "",
            "error": result.error or "",
        }
        for result in results
    ]

    if output_path:
        with open(output_path, "w", encoding="utf-8", newline="") as outfile:
            writer = csv.DictWriter(outfile, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)

    writer = csv.DictWriter(sys.stdout, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://m-m.everflowclient.io", help="Everflow base URL")
    parser.add_argument(
        "--offer-id",
        action="append",
        default=[],
        help="Offer ID (repeat or pass comma-separated values)",
    )
    parser.add_argument(
        "--offer-id-file",
        help="Optional text file with offer IDs (one per line or comma-separated)",
    )
    parser.add_argument("--token", help="Bearer API token (preferred if available)")
    parser.add_argument("--email", help="Login email used if token is not provided")
    parser.add_argument("--password", help="Login password used if token is not provided")
    parser.add_argument("--concurrency", type=int, default=5, help="Parallel workers for offer extraction")
    parser.add_argument("--timeout", type=int, default=25, help="HTTP timeout seconds per request")
    parser.add_argument("--output", help="Optional CSV output path")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.token and (not args.email or not args.password):
        parser.error("Provide --token OR both --email and --password")

    try:
        results = run_extraction(args)
        write_results(results, args.output)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
