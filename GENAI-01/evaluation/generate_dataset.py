#!/usr/bin/env python3
"""Generate the reproducible 60-question RAG evaluation set."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "questions.json"

FACTS = [
    {
        "key": "manager approval threshold",
        "role": "finance",
        "source": "nexvia_internal_policy.docx",
        "reference": "A cost centre manager may approve an individual purchase up to and including USD 5,000.",
        "questions": [
            "What is the maximum individual purchase a cost centre manager can approve?",
            "State the manager-level spending approval cap.",
            "At what purchase amount does manager approval stop being sufficient?",
            "Who can approve a purchase of USD 4,500 under the approval matrix?",
        ],
    },
    {
        "key": "director approval threshold",
        "role": "finance",
        "source": "nexvia_internal_policy.docx",
        "reference": "A department director must approve purchases from USD 5,001 through USD 25,000.",
        "questions": [
            "Which authority approves purchases between USD 5,001 and USD 25,000?",
            "What is the director approval band?",
            "Who must approve a purchase costing USD 18,000?",
            "Give the minimum and maximum purchase values in the director approval tier.",
        ],
    },
    {
        "key": "CFO approval threshold",
        "role": "finance",
        "source": "nexvia_internal_policy.docx",
        "reference": "The Chief Financial Officer must approve purchases above USD 25,000.",
        "questions": [
            "Who approves a purchase above USD 25,000?",
            "What is the CFO spending approval trigger?",
            "Which approver is required for a USD 40,000 commitment?",
            "At what value does the CFO become the required approver?",
        ],
    },
    {
        "key": "finance record retention",
        "role": "finance",
        "source": "nexvia_internal_policy.docx",
        "reference": "Finance and tax records must be retained for seven years after the relevant financial year closes.",
        "questions": [
            "How long must finance and tax records be retained?",
            "What is the records retention period measured from?",
            "For how many years after financial year close are finance records kept?",
            "State the retention duration for tax records.",
        ],
    },
    {
        "key": "data subject deletion",
        "role": "finance",
        "source": "nexvia_internal_policy.docx",
        "reference": "A verified data-subject deletion request must be completed within 30 calendar days unless a legal hold or statutory retention obligation applies.",
        "questions": [
            "What is the deadline for completing a verified data-subject deletion request?",
            "How many calendar days are allowed for a deletion request?",
            "What exceptions can override the ordinary deletion deadline?",
            "When may a verified deletion request be retained beyond 30 days?",
        ],
    },
    {
        "key": "encryption at rest",
        "role": "engineering",
        "source": "nexvia_internal_policy.docx",
        "reference": "Confidential information and personally identifiable information must be encrypted at rest using AES-256 or an organization-approved equivalent.",
        "questions": [
            "Which encryption standard is required for confidential data at rest?",
            "How must PII be protected when stored?",
            "What encryption algorithm does the policy specify for stored sensitive information?",
            "Name the at-rest encryption requirement for confidential information.",
        ],
    },
    {
        "key": "network encryption",
        "role": "engineering",
        "source": "nexvia_internal_policy.docx",
        "reference": "Data sent over a network must use TLS 1.2 or later.",
        "questions": [
            "What is the minimum TLS version for data in transit?",
            "Which transport security protocol version is required for network traffic?",
            "Can services use TLS 1.1 for data sent over a network?",
            "State the policy's minimum transport encryption version.",
        ],
    },
    {
        "key": "public AI PII restriction",
        "role": "engineering",
        "source": "nexvia_internal_policy.docx",
        "reference": "Employees must not paste customer PII, credentials, or confidential source code into public AI services.",
        "questions": [
            "What information is prohibited from being pasted into public AI services?",
            "May an employee paste customer PII into a public chatbot?",
            "Which sensitive data classes must not be submitted to public AI tools?",
            "Does the policy allow confidential source code to be entered into public AI services?",
        ],
    },
    {
        "key": "quarterly access review",
        "role": "engineering",
        "source": "nexvia_internal_policy.docx",
        "reference": "Managers must review privileged and sensitive-data access quarterly.",
        "questions": [
            "How frequently must managers review privileged access?",
            "What is the cadence for sensitive-data access reviews?",
            "Who is responsible for reviewing privileged access, and how often?",
            "State the required access review frequency.",
        ],
    },
    {
        "key": "security incident escalation",
        "role": "engineering",
        "source": "nexvia_internal_policy.docx",
        "reference": "Suspected security incidents, lost devices, exposed credentials, and suspected data disclosure must be reported to the Security team within one hour of discovery.",
        "questions": [
            "How quickly must a suspected security incident be reported?",
            "Which team receives reports of exposed credentials?",
            "What is the incident reporting deadline after discovery?",
            "By when must a lost device be reported to Security?",
        ],
    },
]

MULTI_HOP = [
    ("A team plans a USD 20,000 purchase and wants to store customer PII in the resulting service. Which approval tier and at-rest protection apply?", "A department director must approve a USD 20,000 purchase. Customer PII must be encrypted at rest using AES-256 or an organization-approved equivalent.", "finance"),
    ("An engineer remotely accesses a production system containing confidential source code. What two access and data controls apply?", "Multi-factor authentication is mandatory for all remote access. Confidential information must be encrypted at rest using AES-256 or an organization-approved equivalent, and public AI services must not receive confidential source code.", "engineering"),
    ("A finance record is subject to a verified deletion request while a legal hold exists. What are the ordinary deadline and exception?", "The ordinary deadline is 30 calendar days, but a legal hold or statutory retention obligation can override the deletion schedule and must be documented by Legal.", "finance"),
    ("A production deployment is planned after code changes. What review, test and approval gates must pass?", "A qualified engineer must peer-review the change, automated CI tests must pass, and the owning service lead must approve the production deployment.", "engineering"),
    ("A manager wants to approve a USD 30,000 commitment by splitting it into smaller purchases. What does the policy require?", "The Chief Financial Officer must approve purchases above USD 25,000, and splitting a purchase to avoid an approval threshold is prohibited; the aggregate commitment must be evaluated.", "finance"),
    ("A leaver still has privileged access and the manager's quarterly review is next month. What is the required access-change deadline?", "Joiner, mover, and leaver access changes must be completed within 24 hours of the approved HR event; ordinary access reviews are quarterly.", "engineering"),
    ("A suspected credential exposure is found in a remote environment. What is the reporting deadline and remote-access control?", "The exposure must be reported to the Security team within one hour of discovery. Multi-factor authentication is mandatory for all remote access.", "engineering"),
    ("A service transmits sensitive customer information and stores it on disk. Which encryption controls apply to each state?", "Network transmission must use TLS 1.2 or later. Confidential information and PII must be encrypted at rest using AES-256 or an organization-approved equivalent.", "engineering"),
]

UNANSWERABLE = [
    ("What is the approved annual bonus percentage for Nexvia employees?", "The provided policy does not specify employee bonus percentages."),
    ("Which cloud provider hosts Nexvia's production Kubernetes clusters?", "The provided policy does not identify a cloud hosting provider."),
    ("What is the name of Nexvia's Chief Financial Officer?", "The provided policy does not name the Chief Financial Officer."),
    ("What is the exact date of Nexvia's next board meeting?", "The provided policy does not provide board meeting dates."),
    ("What is the company's approved travel meal allowance per day?", "The provided policy does not specify travel meal allowances."),
    ("How many paid vacation days does an employee receive each year?", "The provided policy does not define annual leave entitlement."),
    ("What is the production database's hostname and port?", "The provided policy does not expose database hostnames or ports."),
    ("Which vendor provides Nexvia's endpoint detection software?", "The provided policy does not name an endpoint detection vendor."),
    ("What was Nexvia's exact revenue in the most recently completed quarter?", "The provided policy does not contain quarterly revenue figures."),
    ("What is the personal mobile number of the Security team lead?", "The provided policy does not contain personal phone numbers."),
    ("How many software engineers currently work at Nexvia?", "The provided policy does not state the current engineering headcount."),
    ("What is the internal code name for the next product launch?", "The provided policy does not identify product launch code names."),
]


def build_questions() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for fact in FACTS:
        for question in fact["questions"]:
            rows.append(
                {
                    "id": f"q-{len(rows) + 1:03d}",
                    "question": question,
                    "reference": fact["reference"],
                    "question_type": "factual",
                    "answerable": True,
                    "role": fact["role"],
                    "expected_source": fact["source"],
                    "fact_key": fact["key"],
                }
            )
    for question, reference, role in MULTI_HOP:
        rows.append(
            {
                "id": f"q-{len(rows) + 1:03d}",
                "question": question,
                "reference": reference,
                "question_type": "multi_hop",
                "answerable": True,
                "role": role,
                "expected_source": "nexvia_internal_policy.docx",
                "fact_key": "multi-hop-policy-combination",
            }
        )
    for question, reference in UNANSWERABLE:
        rows.append(
            {
                "id": f"q-{len(rows) + 1:03d}",
                "question": question,
                "reference": reference,
                "question_type": "unanswerable",
                "answerable": False,
                "role": "guest",
                "expected_source": None,
                "fact_key": "not-in-corpus",
            }
        )
    if len(rows) < 50:
        raise RuntimeError("Evaluation set must contain at least 50 questions")
    return rows


def main() -> None:
    rows = build_questions()
    OUTPUT.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(rows)} evaluation questions to {OUTPUT}")
    print(f"Breakdown: factual={sum(x['question_type'] == 'factual' for x in rows)}, "
          f"multi_hop={sum(x['question_type'] == 'multi_hop' for x in rows)}, "
          f"unanswerable={sum(x['question_type'] == 'unanswerable' for x in rows)}")


if __name__ == "__main__":
    main()
