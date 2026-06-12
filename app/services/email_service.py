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


CHECK_ICON = """
<svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg" style="vertical-align: middle;">
  <rect width="20" height="20" rx="10" fill="#2563eb"/>
  <path d="M6 10.5L8.5 13L14 7" stroke="white" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""

STAR_ICON = """
<svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg" style="vertical-align: middle;">
  <rect width="20" height="20" rx="10" fill="#f59e0b"/>
  <path d="M10 4.5L11.545 7.68L15 8.09L12.5 10.51L13.09 14L10 12.31L6.91 14L7.5 10.51L5 8.09L8.455 7.68L10 4.5Z" fill="white"/>
</svg>
"""


def _benefit_row(icon_svg: str, text: str) -> str:
    return f"""
    <tr>
      <td style="padding: 6px 0;">
        <table cellpadding="0" cellspacing="0">
          <tr>
            <td style="padding-right: 10px; vertical-align: middle;">{icon_svg}</td>
            <td style="font-size: 15px; color: #334155; line-height: 1.5; vertical-align: middle;">{text}</td>
          </tr>
        </table>
      </td>
    </tr>
    """


def _build_body(subject_img: str, content_rows: str, cta_url: str, cta_text: str) -> str:
    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; background: #f1f5f9; padding: 32px 0; margin: 0;">
      <table width="100%" cellpadding="0" cellspacing="0" style="max-width: 520px; margin: 0 auto; background: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 24px rgba(0,0,0,0.06);">
        <tr>
          <td style="padding: 36px 32px 20px; text-align: center; background: #f8fafc; border-bottom: 1px solid #e2e8f0;">
            <span style="font-size: 22px; font-weight: 700; color: #2563eb; letter-spacing: -0.5px;">Bandami</span>
          </td>
        </tr>
        {content_rows}
        <tr>
          <td style="padding: 0 32px 36px;">
            <table cellpadding="0" cellspacing="0" width="100%">
              <tr>
                <td>
                  <a href="{cta_url}" style="display: block; width: 100%; background: #2563eb; color: #ffffff; text-decoration: none; text-align: center; padding: 14px 0; border-radius: 10px; font-weight: 600; font-size: 16px; box-sizing: border-box;">
                    {cta_text}
                  </a>
                </td>
              </tr>
            </table>
            <p style="font-size: 13px; color: #94a3b8; text-align: center; margin: 20px 0 0;">
              Bandami · IELTS Preparation<br>
              Need help? <a href="mailto:contacto@bandami.com" style="color: #2563eb; text-decoration: underline;">contacto@bandami.com</a>
            </p>
          </td>
        </tr>
      </table>
    </body>
    </html>
    """


def send_trial_welcome_email(to_email: str, name: str) -> None:
    """Send welcome email when a user starts the 3-day free trial."""
    dashboard_url = f"{FRONTEND_URL}/dashboard"
    settings_url = f"{FRONTEND_URL}/settings"

    benefits = [
        "Unlimited practice with instant band scores",
        "Detailed IELTS analysis & personalized feedback",
        "Personalized study plans tailored to your goals",
        "Progress tracking & full exam history",
        "All Speaking Parts 1, 2 & 3 with AI evaluation",
        "Grammar corrections with explanations",
    ]
    benefit_rows = "".join(_benefit_row(CHECK_ICON, b) for b in benefits)

    content = f"""
    <tr>
      <td style="padding: 32px 32px 0;">
        <table cellpadding="0" cellspacing="0">
          <tr>
            <td style="width: 48px; vertical-align: top;">
              <svg width="48" height="48" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
                <rect width="48" height="48" rx="24" fill="#dbeafe"/>
                <path d="M20 24L23 27L29 21" stroke="#2563eb" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
                <circle cx="24" cy="24" r="12" stroke="#2563eb" stroke-width="2" fill="none"/>
              </svg>
            </td>
            <td style="padding-left: 14px; vertical-align: middle;">
              <h2 style="font-size: 20px; color: #0f172a; margin: 0; font-weight: 700;">Welcome to Pro, {name}!</h2>
            </td>
          </tr>
        </table>
      </td>
    </tr>
    <tr>
      <td style="padding: 16px 32px 0; font-size: 15px; color: #475569; line-height: 1.7;">
        Your <strong>3-day free trial</strong> has started. You now have full access to everything Pro offers — no charge today.
      </td>
    </tr>
    <tr>
      <td style="padding: 8px 32px 0;">
        <table cellpadding="0" cellspacing="0">
          {benefit_rows}
        </table>
      </td>
    </tr>
    <tr>
      <td style="padding: 20px 32px 24px;">
        <table cellpadding="0" cellspacing="0" width="100%" style="background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 10px;">
          <tr>
            <td style="padding: 16px;">
              <table cellpadding="0" cellspacing="0">
                <tr>
                  <td style="width: 24px; vertical-align: top; padding-top: 2px;">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                      <circle cx="12" cy="12" r="10" fill="#22c55e"/>
                      <path d="M12 8V12.5" stroke="white" stroke-width="1.5" stroke-linecap="round"/>
                      <circle cx="12" cy="16" r="0.75" fill="white"/>
                    </svg>
                  </td>
                  <td style="padding-left: 10px; font-size: 14px; color: #166534; line-height: 1.6;">
                    <strong>No charge today.</strong> After 3 days, you'll be charged <strong>$14.99/month + tax</strong>.
                    Cancel anytime from your <a href="{settings_url}" style="color: #2563eb; text-decoration: underline;">Settings</a>.
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </td>
    </tr>
    """

    _send_email(
        to_email,
        "Your 3-day free trial has started — Bandami",
        _build_body(None, content, dashboard_url, "Start Practicing"),
    )


def send_purchase_confirmation(to_email: str, name: str, plan_name: str, amount: str, period: str) -> None:
    """Send purchase confirmation email after successful payment."""
    dashboard_url = f"{FRONTEND_URL}/dashboard"

    benefits = [
        "Unlimited practice with instant band scores",
        "Detailed IELTS analysis & personalized feedback",
        "Personalized study plans tailored to your goals",
        "Progress tracking & full exam history",
        "All Speaking Parts 1, 2 & 3 with AI evaluation",
        "Grammar corrections with explanations",
    ]
    benefit_rows = "".join(_benefit_row(STAR_ICON, b) for b in benefits)

    content = f"""
    <tr>
      <td style="padding: 32px 32px 0;">
        <table cellpadding="0" cellspacing="0" width="100%">
          <tr>
            <td>
              <table cellpadding="0" cellspacing="0" width="100%" style="background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 10px;">
                <tr>
                  <td style="padding: 14px 16px; text-align: center;">
                    <table cellpadding="0" cellspacing="0" style="margin: 0 auto;">
                      <tr>
                        <td style="vertical-align: middle; padding-right: 8px;">
                          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <rect width="24" height="24" rx="12" fill="#2563eb"/>
                            <path d="M7 12.5L10 15.5L17 8.5" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                          </svg>
                        </td>
                        <td style="vertical-align: middle;">
                          <span style="font-size: 16px; font-weight: 600; color: #1e3a8a;">Premium subscription active</span>
                        </td>
                      </tr>
                    </table>
                    <p style="font-size: 14px; color: #475569; margin: 4px 0 0;">{plan_name} — {amount}</p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </td>
    </tr>
    <tr>
      <td style="padding: 24px 32px 0; font-size: 15px; color: #475569; line-height: 1.7;">
        Your free trial has ended and your Pro subscription is now active, {name}. You'll continue enjoying full access:
      </td>
    </tr>
    <tr>
      <td style="padding: 8px 32px 0;">
        <table cellpadding="0" cellspacing="0">
          {benefit_rows}
        </table>
      </td>
    </tr>
    <tr>
      <td style="padding: 20px 32px 8px;">
        <p style="font-size: 14px; color: #64748b; margin: 0;">
          {period} · Cancel anytime from <a href="{FRONTEND_URL}/settings" style="color: #2563eb; text-decoration: underline;">Settings</a>
        </p>
      </td>
    </tr>
    """

    _send_email(
        to_email,
        f"Payment confirmed — {plan_name}",
        _build_body(None, content, dashboard_url, "Go to Dashboard"),
    )
