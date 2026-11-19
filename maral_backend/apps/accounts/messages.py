"""Localised message templates (Persian and English; Arabic/Turkish fall back to English for now)."""

_T = {
    "password_reset": {
        "fa": (
            "بازیابی گذرواژه مارال",
            "برای تعیین گذرواژه‌ی جدید از این پیوند استفاده کنید (تا یک ساعت معتبر است):\n{link}\n\nاگر شما درخواست نداده‌اید، این ایمیل را نادیده بگیرید.",
        ),
        "en": (
            "Reset your Maral password",
            "Use this link to choose a new password (valid for one hour):\n{link}\n\nIf you did not ask for this, ignore this email.",
        ),
    },
    "email_verification": {
        "fa": ("تأیید ایمیل مارال", "برای تأیید ایمیل خود روی این پیوند بزنید (تا ۲۴ ساعت معتبر است):\n{link}"),
        "en": ("Verify your Maral email", "Confirm your email address with this link (valid for 24 hours):\n{link}"),
    },
    "password_changed": {
        "fa": (
            "گذرواژه‌ی شما تغییر کرد",
            "گذرواژه‌ی حساب مارال شما تغییر کرد و همه‌ی نشست‌های دیگر خارج شدند. اگر کار شما نبود فوراً با پشتیبانی تماس بگیرید.",
        ),
        "en": (
            "Your password was changed",
            "The password of your Maral account was changed and all other sessions were signed out. If this was not you, contact support immediately.",
        ),
    },
    "phone_otp": {
        "fa": ("", "کد تأیید مارال: {code}\nاین کد را به کسی ندهید."),
        "en": ("", "Your Maral verification code: {code}\nDo not share it with anyone."),
    },
}


def render(name: str, language: str, **values) -> tuple[str, str]:
    variants = _T[name]
    subject, body = variants.get(language, variants["en"])
    return subject, body.format(**values)
