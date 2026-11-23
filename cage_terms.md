# ZTL signed verdicts — Terms of Use and Privacy Note

Version 3, 2026-10-30. Applies to the paths `/signed/v1/*` and `/cage/v1/*` on `https://api.ztljudge.com` and `https://api.vitalyreznik.com` — one service, operated by the same operator, with the same signing keys. `/cage/v1/*` is kept for compatibility with existing integrations.

**Operator.** Vitaly Reznik, an individual (Israel). Contact: tech@vitalyreznik.com.

1. **Research preview.** This service is a free research preview of ZTL (Zero-Trust Logic). It is
   offered for evaluation, research and interoperability testing.
   - Production or commercial use of the service, as opposed to the open-source ZTL code, requires a
     separate agreement with the operator.
   - The service is provided "AS IS" and "AS AVAILABLE", without warranty of any kind, express or
     implied, including fitness for a particular purpose.
   - There is no service-level agreement. The service may change, be rate-limited or stop at any time.
2. **What a verdict means.** A signed receipt states only this: IF the grounds the caller declares
   hold, THEN the verdict follows by ZTL's rules.
   - ZTL does not verify the grounds.
   - Attestations are always marked UNVERIFIED.
   - A verdict is not legal, financial, medical, safety or compliance advice. It must not be the sole
     basis of any safety-critical, legal or financial decision.
3. **Your inputs.** Do not send personal data, confidential information of others, trade secrets, or
   classified or export-controlled information. You are responsible for having the right to submit
   what you send.
4. **Logging and retention.**
   - **Request metadata** is kept for up to 30 days: the time, the client IP address, the method and
     path, the status, the duration, and the User-Agent and Origin headers.
   - **The web server's access log** is kept for 14 days. It holds the time, IP address, request line,
     status, response size, referrer and User-Agent.
   - **The request body of `/signed/v1/verify` and `/cage/v1/verify` is not stored.** The receipt returned to you carries a
     SHA-256 digest of it. The service keeps no copy of the receipts it issues.
   - Logs are used only to operate, secure and improve the service, and are not sold.
5. **Signatures and keys.** Receipts are signed with Ed25519.
   - Current keys: `GET /signed/v1/keys` (the same at `/cage/v1/keys`).
   - Pin them against `SIGNING-KEYS.md` in the public repository `inventor1975/ZTL`.
   - A signature shows that the holder of the key signed the receipt and that the receipt was not
     altered. It does not show that the declared grounds are correct.
6. **Limits.** 30 requests per minute per address. A request body of at most 256 KB. No
   authentication. Abuse may be blocked.
7. **Intellectual property.** ZTL is © 2026 Vitaly Reznik.
   - Published ZTL code is licensed under Apache-2.0 OR MIT.
   - Use of this free service grants no other license.
   - Commercial use with service levels, support or integration is available by separate agreement.
8. **Liability.** To the maximum extent permitted by law, the operator is not liable for any damages
   arising from the use of, or the inability to use, the service or its outputs.
9. **Changes.** These terms may be updated. The version and date are shown at the top of this page.

**Changelog.** Version 3 (2026-10-30): adds the path /signed/v1; no other change. Version 2 (2026-10-30): adds the host api.ztljudge.com. Version 1 (2026-10-10): first version, api.vitalyreznik.com/cage/v1.
