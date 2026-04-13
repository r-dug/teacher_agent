"""Terms of Service / liability waiver + license metadata.

Single source of truth for the user agreement displayed in the app.
Bump ``TOS_VERSION`` whenever ``TOS_TEXT`` changes — the client compares the
current version against the per-user ``users.terms_version_accepted`` column
and forces users to re-accept whenever they differ.

This module has no runtime dependencies so both the backend and the frontend
BFF (same Python env) can import it directly.
"""

from __future__ import annotations

# Bump this whenever TOS_TEXT changes. Any user whose
# users.terms_version_accepted != TOS_VERSION will be forced to re-accept.
TOS_VERSION: str = "2026-04-12.1"

# Code/content license information shown in the footer.
LICENSE_NAME: str = "MIT License"
LICENSE_URL: str = "https://opensource.org/license/mit"
COPYRIGHT_HOLDER: str = "TutorAIl"
COPYRIGHT_YEAR: str = "2026"

# Boilerplate user agreement. NOT LEGAL ADVICE — the project owner should
# have a lawyer review this before relying on it in any commercial context.
TOS_TEXT: str = """\
# Terms of Use & Liability Waiver

_Last updated: April 12, 2026_

Welcome to TutorAIl (the "Service"). By creating an account or otherwise using
the Service, you ("User") acknowledge that you have read, understood, and agree
to be bound by these Terms of Use.

## 1. Nature of the Service

The Service is a personal, hobby/portfolio project offered to the public free
of charge. It is **not a commercial product**, is **not a paid service**, and
no contractual service-level obligation is created by your use of it.

The Service is provided **"AS IS" and "AS AVAILABLE"**, without warranty of any
kind, express or implied, including but not limited to the implied warranties
of merchantability, fitness for a particular purpose, accuracy, non-infringement,
or uninterrupted availability. Features may change, break, or disappear at any
time without notice.

## 2. Donations, Not Payments

Any monetary contribution you choose to send (for example, via the Venmo QR
code displayed in the application) is a **voluntary gift / donation** intended
to help offset hosting costs. It is explicitly **not** a payment for services,
not a purchase, not a subscription, and creates **no** expectation of service,
support, refund, or continued access. Donations are non-refundable. Nothing
about sending or not sending a donation changes your rights or obligations
under these Terms.

## 3. Educational Content Disclaimer

The Service uses AI models to generate explanations, quizzes, and other
educational material. AI output may be incorrect, incomplete, biased, or
out-of-date. Nothing produced by the Service constitutes professional advice
of any kind (medical, legal, financial, academic, etc.). Always verify
important information with a qualified professional and do not rely on the
Service for decisions that carry real-world consequences.

## 4. Limitation of Liability

To the fullest extent permitted by applicable law, in no event shall the
Service's operator, contributors, or hosting providers be liable for any
direct, indirect, incidental, special, consequential, exemplary, or punitive
damages — including but not limited to loss of data, loss of profits, loss of
goodwill, device damage, or personal injury — arising out of or in connection
with your use of, or inability to use, the Service, even if advised of the
possibility of such damages.

You agree that your sole and exclusive remedy for any dissatisfaction with the
Service is to stop using it.

## 5. Assumption of Risk & Indemnification

You use the Service at your own risk. You agree to indemnify and hold harmless
the operator from any claim, demand, or damages — including reasonable
attorneys' fees — arising from your use of the Service or your violation of
these Terms.

## 6. Acceptable Use

You agree not to: (a) upload content you do not have the right to share;
(b) attempt to disrupt, overload, or compromise the Service or its
infrastructure; (c) use the Service to generate unlawful, harassing, or
harmful content; (d) attempt to extract the credentials, API keys, or
personal data of other users or third parties.

## 7. User Content

You retain ownership of any PDFs, text, or other content you upload. By
uploading, you grant the Service a non-exclusive, royalty-free license to
process that content solely for the purpose of providing the Service to you.

## 8. Privacy

The Service stores the minimum data required to operate: account identifiers,
uploaded content, generated lessons, and basic usage metrics. It does not sell
personal data. User data may be deleted at any time at the operator's
discretion (for example, during maintenance, migrations, or decommissioning).

## 9. Changes to These Terms

These Terms may be updated at any time. When the Terms change, you will be
prompted to re-accept them on your next use of the Service. Continued use
after acceptance constitutes agreement to the new Terms.

## 10. Governing Law & Severability

These Terms are governed by the laws of the jurisdiction in which the operator
resides, without regard to conflict-of-law principles. If any provision is
found unenforceable, the remaining provisions remain in full force and effect.

## 11. Open Source License

The source code of the Service is made available under the MIT License. The
license grant applies to the source code only; it does not create any warranty
or service obligation with respect to this hosted instance of the Service.

---

**By clicking "I Agree" you confirm that you have read and accepted these
Terms of Use and Liability Waiver in full.**
"""
