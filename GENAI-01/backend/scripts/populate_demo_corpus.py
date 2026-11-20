#!/usr/bin/env python3
"""Generate/download a mixed demo corpus and optionally ingest it through the admin API.

Example:
  python scripts/populate_demo_corpus.py --output-dir ../sample-data --ingest
For SEC filings, set SEC_USER_AGENT to an identifying organization and contact email.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

PYTHON_DOCS_URL = "https://docs.python.org/3/library/asyncio.html"
KUBERNETES_DOCS_URL = "https://kubernetes.io/docs/concepts/overview/working-with-objects/"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK0000320193.json"

POLICY_TEXT = """NEXVIA INTERNAL SECURITY AND FINANCE POLICY

Financial approval matrix
1. A cost centre manager may approve an individual purchase up to and including USD 5,000.
2. A department director must approve purchases from USD 5,001 through USD 25,000.
3. The Chief Financial Officer must approve purchases above USD 25,000.
4. Splitting a purchase to avoid an approval threshold is prohibited. The aggregate commitment must be evaluated.

Data protection
1. Confidential information and personally identifiable information must be encrypted at rest using AES-256 or an organization-approved equivalent.
2. Data sent over a network must use TLS 1.2 or later.
3. Employees must not paste customer PII, credentials, or confidential source code into public AI services.
4. Customer data may be used only for the business purpose and role authorized by its owner.

Records retention
1. Finance and tax records must be retained for seven years after the relevant financial year closes.
2. A verified data-subject deletion request must be completed within 30 calendar days unless a legal hold or statutory retention obligation applies.
3. Legal holds override ordinary deletion schedules and must be documented by Legal.

Identity and access management
1. Multi-factor authentication is mandatory for all administrator accounts and all remote access.
2. Managers must review privileged and sensitive-data access quarterly.
3. Joiner, mover, and leaver access changes must be completed within 24 hours of the approved HR event.
4. Access must follow least privilege and must be removed when the business purpose ends.

Incident response
1. Suspected security incidents, lost devices, exposed credentials, and suspected data disclosure must be reported to the Security team within one hour of discovery.
2. Users must preserve relevant evidence and must not independently delete logs or attempt unapproved remediation.

Engineering change management
1. Production changes require peer review by at least one qualified engineer.
2. Automated CI tests must pass before production deployment.
3. Production deployment requires approval from the owning service lead.
4. Secrets must be stored in an approved secret manager and must never be committed to source control.
"""


def make_policy_docx(path: Path) -> None:
    document = Document()
    document.add_heading("Nexvia Internal Security and Finance Policy", 0)
    for block in POLICY_TEXT.split("\n\n"):
        lines = block.splitlines()
        if not lines:
            continue
        title = lines[0].strip()
        document.add_heading(title.title(), level=1)
        for line in lines[1:]:
            text = line.strip()
            if not text:
                continue
            if re.match(r"^\d+\.", text):
                document.add_paragraph(re.sub(r"^\d+\.\s*", "", text), style="List Number")
            else:
                document.add_paragraph(text)
    document.save(path)


def _write_pdf(path: Path, title: str, source_url: str, text: str) -> None:
    output = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    margin = 44
    y = height - margin
    output.setTitle(title)
    output.setFont("Helvetica-Bold", 14)
    output.drawString(margin, y, title[:100])
    y -= 22
    output.setFont("Helvetica", 8)
    output.drawString(margin, y, f"Source: {source_url}"[:150])
    y -= 24
    output.setFont("Helvetica", 9)
    for paragraph in text.splitlines():
        words = paragraph.split()
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if stringWidth(candidate, "Helvetica", 9) > width - 2 * margin:
                output.drawString(margin, y, line)
                y -= 13
                line = word
                if y < margin:
                    output.showPage()
                    y = height - margin
                    output.setFont("Helvetica", 9)
            else:
                line = candidate
        if line:
            output.drawString(margin, y, line)
            y -= 13
        y -= 4
        if y < margin:
            output.showPage()
            y = height - margin
            output.setFont("Helvetica", 9)
    output.save()


def download_sec_10k(client: httpx.Client, output_dir: Path, user_agent: str) -> tuple[Path, str]:
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate", "Host": "data.sec.gov"}
    response = client.get(SEC_SUBMISSIONS_URL, headers=headers)
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    index = next((i for i, form in enumerate(forms) if form == "10-K"), None)
    if index is None:
        raise RuntimeError("No recent 10-K filing was found in the SEC submissions feed")
    accession = recent["accessionNumber"][index]
    primary_document = recent["primaryDocument"][index]
    cik = str(payload.get("cik", 320193)).lstrip("0")
    filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{primary_document}"
    filing_response = client.get(
        filing_url,
        headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate", "Host": "www.sec.gov"},
    )
    filing_response.raise_for_status()
    html_path = output_dir / f"apple_10k_{accession}.html"
    html_path.write_bytes(filing_response.content)
    soup = BeautifulSoup(filing_response.text, "html.parser")
    for element in soup(["script", "style", "ix:header"]):
        element.decompose()
    filing_text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    pdf_path = output_dir / f"apple_10k_{accession}_text_packet.pdf"
    _write_pdf(pdf_path, f"Apple Inc. Form 10-K {accession}", filing_url, filing_text[:250000])
    return pdf_path, filing_url


def download_official_docs(client: httpx.Client, output_dir: Path) -> list[tuple[Path, str]]:
    saved: list[tuple[Path, str]] = []
    for name, url in (("python_asyncio_docs.html", PYTHON_DOCS_URL), ("kubernetes_objects_docs.html", KUBERNETES_DOCS_URL)):
        response = client.get(url, headers={"User-Agent": "NexviaRAGCapstone/1.0 educational research"})
        response.raise_for_status()
        path = output_dir / name
        path.write_bytes(response.content)
        saved.append((path, str(response.url)))
    return saved


def issue_demo_token(client: httpx.Client, api_url: str) -> str:
    response = client.post(
        urljoin(api_url.rstrip("/") + "/", "api/v1/auth/demo-token"),
        json={"username": "capstone-admin", "role": "admin"},
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


def ingest_file(client: httpx.Client, api_url: str, token: str, path: Path, roles: str) -> None:
    with path.open("rb") as file_handle:
        response = client.post(
            urljoin(api_url.rstrip("/") + "/", "api/v1/admin/documents/upload"),
            headers={"Authorization": f"Bearer {token}"},
            data={"allowed_roles": roles},
            files={"file": (path.name, file_handle)},
        )
    response.raise_for_status()
    print(f"INGESTED {path.name}: {response.json()['chunks_created']} chunks")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("./sample-data"))
    parser.add_argument("--include-sec", action="store_true", help="Download the latest Apple Inc. SEC 10-K filing")
    parser.add_argument("--sec-user-agent", default=os.getenv("SEC_USER_AGENT", ""))
    parser.add_argument("--ingest", action="store_true", help="Push generated files to a running local API")
    parser.add_argument("--api-url", default=os.getenv("NEXVIA_API_URL", "http://localhost:8000"))
    parser.add_argument("--admin-token", default=os.getenv("NEXVIA_ADMIN_TOKEN", ""))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    policy_path = args.output_dir / "nexvia_internal_policy.docx"
    policy_pdf_path = args.output_dir / "nexvia_internal_policy_text_packet.pdf"
    make_policy_docx(policy_path)
    _write_pdf(policy_pdf_path, "Nexvia Internal Security and Finance Policy (Synthetic Demo)", "generated synthetic fixture", POLICY_TEXT)
    files: list[tuple[Path, str]] = [
        (policy_path, "finance,engineering"),
        (policy_pdf_path, "finance,engineering"),
    ]
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        if args.include_sec:
            if not args.sec_user_agent:
                parser.error("--include-sec requires --sec-user-agent or SEC_USER_AGENT with an identifying contact")
            sec_pdf, filing_url = download_sec_10k(client, args.output_dir, args.sec_user_agent)
            files.append((sec_pdf, "finance"))
            print(f"DOWNLOADED SEC filing: {filing_url}")
            time.sleep(0.15)
        official_docs = download_official_docs(client, args.output_dir)
        for path, source_url in official_docs:
            print(f"DOWNLOADED official documentation: {source_url}")
        if args.ingest:
            token = args.admin_token or issue_demo_token(client, args.api_url)
            for path, roles in files:
                ingest_file(client, args.api_url, token, path, roles)
            for _, source_url in official_docs:
                response = client.post(
                    urljoin(args.api_url.rstrip("/") + "/", "api/v1/admin/documents/url"),
                    headers={"Authorization": f"Bearer {token}"},
                    json={"url": source_url, "allowed_roles": ["engineering", "guest"]},
                )
                response.raise_for_status()
                print(f"INGESTED URL {source_url}: {response.json()['chunks_created']} chunks")
    print(f"Corpus files ready in {args.output_dir.resolve()}")
    if not args.ingest:
        print("Start the API, then rerun with --ingest to populate the vector index.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (httpx.HTTPError, RuntimeError) as error:
        print(f"Corpus population failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
