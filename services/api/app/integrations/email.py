"""Resend email adapter — Phase 4 (swapped from SendGrid).

Uses httpx.AsyncClient (already a transitive dependency via fastapi[standard])
so no additional packages are required.

Graceful degradation: if api_key or from_email is empty, the function returns
False immediately without making any network call. The caller (voice.py) must
handle False by persisting delivery_status="failed" and continuing.
"""

import httpx

RESEND_SEND_URL = "https://api.resend.com/emails"
REQUEST_TIMEOUT_SECONDS = 10.0


async def send_resend_email(
    to_email: str,
    subject: str,
    body: str,
    api_key: str,
    from_email: str,
) -> bool:
    """Send a plain-text email via the Resend API.

    Args:
        to_email: Recipient email address.
        subject: Email subject line.
        body: Plain-text email body.
        api_key: Resend API key (starts with "re_").
        from_email: Verified sender address registered in Resend.

    Returns:
        True when Resend responds with HTTP 200 (accepted).
        False when credentials are not configured or the request fails.

    Note:
        This function never raises. All network or configuration errors are
        caught and converted to a False return value. The caller is responsible
        for recording the delivery failure.
    """
    if not api_key or not from_email:
        return False

    payload = {
        "from": from_email,
        "to": [to_email],
        "subject": subject,
        "text": body,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                RESEND_SEND_URL,
                json=payload,
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        return resp.status_code == 200
    except Exception:
        return False
