"""SendGrid email adapter — Phase 4.

Uses httpx.AsyncClient (already a transitive dependency via fastapi[standard])
so no additional packages are required.

Graceful degradation: if api_key or from_email is empty, the function returns
False immediately without making any network call. The caller (voice.py) must
handle False by persisting delivery_status="failed" and continuing.
"""

import httpx

SENDGRID_SEND_URL = "https://api.sendgrid.com/v3/mail/send"
REQUEST_TIMEOUT_SECONDS = 10.0


async def send_sendgrid_email(
    to_email: str,
    subject: str,
    body: str,
    api_key: str,
    from_email: str,
) -> bool:
    """Send a plain-text email via the SendGrid v3 API.

    Args:
        to_email: Recipient email address.
        subject: Email subject line.
        body: Plain-text email body.
        api_key: SendGrid API key (starts with "SG.").
        from_email: Verified sender email address registered in SendGrid.

    Returns:
        True when SendGrid responds with HTTP 200 or 202 (accepted).
        False when credentials are not configured or the request fails.

    Note:
        This function never raises. All network or configuration errors are
        caught and converted to a False return value. The caller is responsible
        for recording the delivery failure.
    """
    if not api_key or not from_email:
        return False

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": from_email},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                SENDGRID_SEND_URL,
                json=payload,
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        return resp.status_code in (200, 202)
    except Exception:
        return False
