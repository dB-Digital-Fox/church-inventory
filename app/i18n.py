"""Slovak / English. Source strings are English; SK holds the Slovak translations."""
from flask import current_app, g, has_request_context, session

from .translations_sk import SK

LANGUAGES = {"sk": "Slovenčina", "en": "English"}


def current_lang():
    if has_request_context():
        user = g.get("user")
        if user is not None and user["language"] in LANGUAGES:
            return user["language"]
        if session.get("lang") in LANGUAGES:
            return session["lang"]
        return current_app.config.get("DEFAULT_LANGUAGE", "sk")
    return "sk"


def translate(text, lang=None, **kwargs):
    lang = lang or current_lang()
    out = SK.get(text, text) if lang == "sk" else text
    return out.format(**kwargs) if kwargs else out


def _(text, **kwargs):
    return translate(text, None, **kwargs)


def type_name(row, lang=None):
    """Localised name of an event type row."""
    lang = lang or current_lang()
    return row["name_sk"] if lang == "sk" else row["name_en"]
