"""
Email service using Brevo (Sendinblue) for transactional emails.
Free tier: 300 emails/day.
"""

import re
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException

from app.core.config import get_settings

settings = get_settings()


def _sender() -> dict:
    match = re.match(r'(.*?)\s*<(.+)>', settings.brevo_from_email)
    if match:
        return {"name": match.group(1).strip(), "email": match.group(2)}
    return {"name": "Bandami", "email": settings.brevo_from_email}


def _send_email(to_email: str, subject: str, html: str) -> None:
    if not settings.brevo_api_key:
        return

    try:
        cfg = sib_api_v3_sdk.Configuration()
        cfg.api_key["api-key"] = settings.brevo_api_key

        api = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(cfg))
        msg = sib_api_v3_sdk.SendSmtpEmail(
            to=[{"email": to_email}],
            sender=_sender(),
            subject=subject,
            html_content=html,
        )
        api.send_transac_email(msg)
    except ApiException:
        pass


FRONTEND_URL = settings.frontend_url


def send_verification_email(to_email: str, name: str, token: str) -> None:
    verification_url = f"{FRONTEND_URL}/auth/callback?token={token}"

    _send_email(
        to_email,
        "Verify your email — Bandami",
        f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"></head>
        <body style="font-family: Inter, Arial, sans-serif; background: #f7f9fb; padding: 40px 0;">
          <table width="100%" cellpadding="0" cellspacing="0" style="max-width: 480px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
            <tr>
              <td style="padding: 40px 32px 24px; text-align: center;">
                <h1 style="margin: 0; font-size: 24px; color: #2563eb;">Bandami</h1>
              </td>
            </tr>
            <tr>
              <td style="padding: 0 32px 32px;">
                <h2 style="font-size: 20px; color: #1e293b; margin: 0 0 16px;">Welcome, {name}!</h2>
                <p style="font-size: 16px; color: #475569; line-height: 1.6; margin: 0 0 24px;">
                  Thanks for signing up. Please verify your email address to activate your account and start your IELTS preparation.
                </p>
                <a href="{verification_url}" style="display: block; width: 100%; background: #2563eb; color: #ffffff; text-decoration: none; text-align: center; padding: 14px 0; border-radius: 8px; font-weight: 600; font-size: 16px; box-sizing: border-box;">
                  Verify Email
                </a>
                <p style="font-size: 14px; color: #94a3b8; text-align: center; margin: 24px 0 0;">
                  If the button doesn't work, copy and paste this link:<br>
                  <a href="{verification_url}" style="color: #2563eb; word-break: break-all;">{verification_url}</a>
                </p>
                <p style="font-size: 14px; color: #94a3b8; text-align: center; margin: 16px 0 0;">
                  This link expires in 24 hours.
                </p>
              </td>
            </tr>
          </table>
        </body>
        </html>
        """,
    )


def send_password_reset_email(to_email: str, name: str, token: str) -> None:
    reset_url = f"{FRONTEND_URL}/auth/callback?token={token}&type=reset"

    _send_email(
        to_email,
        "Reset your password — Bandami",
        f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"></head>
        <body style="font-family: Inter, Arial, sans-serif; background: #f7f9fb; padding: 40px 0;">
          <table width="100%" cellpadding="0" cellspacing="0" style="max-width: 480px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
            <tr>
              <td style="padding: 40px 32px 24px; text-align: center;">
                <h1 style="margin: 0; font-size: 24px; color: #2563eb;">Bandami</h1>
              </td>
            </tr>
            <tr>
              <td style="padding: 0 32px 32px;">
                <h2 style="font-size: 20px; color: #1e293b; margin: 0 0 16px;">Reset your password</h2>
                <p style="font-size: 16px; color: #475569; line-height: 1.6; margin: 0 0 24px;">
                  Hi {name}, we received a request to reset your password. Click the button below to choose a new one.
                </p>
                <a href="{reset_url}" style="display: block; width: 100%; background: #2563eb; color: #ffffff; text-decoration: none; text-align: center; padding: 14px 0; border-radius: 8px; font-weight: 600; font-size: 16px; box-sizing: border-box;">
                  Reset Password
                </a>
                <p style="font-size: 14px; color: #94a3b8; text-align: center; margin: 24px 0 0;">
                   If you didn't request this, you can safely ignore this email.
                </p>
              </td>
            </tr>
          </table>
        </body>
        </html>
        """,
    )


def send_purchase_confirmation(to_email: str, name: str, plan_name: str, amount: str, period: str) -> None:
    """Send purchase confirmation email after successful payment."""
    dashboard_url = f"{FRONTEND_URL}/dashboard"

    _send_email(
        to_email,
        f"Payment confirmed — {plan_name}",
        f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"></head>
        <body style="font-family: Inter, Arial, sans-serif; background: #f7f9fb; padding: 40px 0;">
          <table width="100%" cellpadding="0" cellspacing="0" style="max-width: 480px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
            <tr>
              <td style="padding: 40px 32px 24px; text-align: center;">
                <h1 style="margin: 0; font-size: 24px; color: #1e3a8a;">Bandami</h1>
              </td>
            </tr>
            <tr>
              <td style="padding: 0 32px 32px;">
                <div style="background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 8px; padding: 16px; margin: 0 0 24px; text-align: center;">
                  <p style="font-size: 18px; font-weight: 600; color: #1e3a8a; margin: 0;">✅ Payment Confirmed</p>
                  <p style="font-size: 14px; color: #475569; margin: 4px 0 0;">{plan_name} — {amount}</p>
                </div>
                <h2 style="font-size: 20px; color: #1e293b; margin: 0 0 16px;">Welcome to Premium, {name}!</h2>
                <p style="font-size: 16px; color: #475569; line-height: 1.6; margin: 0 0 16px;">
                  Your payment was successful and your account has been upgraded. You now have access to:
                </p>
                <ul style="font-size: 15px; color: #475569; line-height: 1.8; padding-left: 20px; margin: 0 0 24px;">
                  <li>30 evaluations per day with advanced AI</li>
                  <li>Full criteria breakdowns and grammar corrections</li>
                  <li>Instant detailed feedback on all exams</li>
                  <li>Full IELTS Speaking Test (3 parts)</li>
                  <li>Progress tracking and history</li>
                </ul>
                <p style="font-size: 14px; color: #64748b; margin: 0 0 24px;">
                  {period} · Cancel anytime from Settings
                </p>
                <a href="{dashboard_url}" style="display: block; width: 100%; background: #1e3a8a; color: #ffffff; text-decoration: none; text-align: center; padding: 14px 0; border-radius: 8px; font-weight: 600; font-size: 16px; box-sizing: border-box;">
                  Go to Dashboard
                </a>
              </td>
            </tr>
          </table>
        </body>
        </html>
        """,
    )
