import logging
import requests
from functools import wraps
from flask import Blueprint, request, redirect, url_for, flash, render_template
from flask.wrappers import Response
from CTFd.models import Solves, Challenges
from CTFd.utils.decorators import admins_only
from CTFd.utils import get_config, set_config
from CTFd.utils import config as ctfd_config
from CTFd.utils.user import get_current_user, get_current_team
from CTFd.forms import BaseForm
from CTFd.forms.fields import SubmitField
from wtforms import StringField
from wtforms.validators import Optional

logger = logging.getLogger(__name__)


def is_valid_webhook(url: str) -> bool:
    return url.startswith("https://discord.com/api/webhooks/") or \
           url.startswith("https://discordapp.com/api/webhooks/")


def send_discord_webhook_sync(webhook: str, message: str) -> tuple[bool, str]:
    """Send a webhook synchronously and return (success, error_message)."""
    try:
        resp = requests.post(webhook, json={"content": message}, timeout=5)
        if resp.status_code in (200, 204):
            return True, ""
        return False, f"Discord returned HTTP {resp.status_code}: {resp.text[:200]}"
    except requests.exceptions.ConnectionError as e:
        return False, f"Connection error: {e}"
    except requests.exceptions.Timeout:
        return False, "Request timed out after 5 seconds"
    except Exception as e:
        return False, str(e)


class FirstBloodForm(BaseForm):
    webhook = StringField("Discord Webhook URL", validators=[Optional()])
    submit = SubmitField("Save Webhook")


admin_blueprint = Blueprint(
    "first_blood_admin",
    __name__,
    template_folder="templates",
)


@admin_blueprint.route("/admin/first-blood", methods=["GET", "POST"])
@admins_only
def first_blood_settings():
    form = FirstBloodForm()

    if request.method == "POST":
        webhook = request.form.get("webhook", "").strip()

        if webhook and not is_valid_webhook(webhook):
            flash("Invalid webhook URL. Must be a Discord webhook.", "danger")
            return redirect(url_for("first_blood_admin.first_blood_settings"))

        set_config("FIRST_BLOOD_WEBHOOK", webhook)

        flash("First Blood webhook saved.", "success")
        return redirect(url_for("first_blood_admin.first_blood_settings"))

    form.webhook.data = get_config("FIRST_BLOOD_WEBHOOK") or ""
    return render_template("first_blood_settings.html", form=form)


@admin_blueprint.route("/admin/first-blood/test")
@admins_only
def test_webhook():
    webhook = get_config("FIRST_BLOOD_WEBHOOK")
    if not webhook or not is_valid_webhook(webhook):
        flash("No valid webhook configured.", "danger")
        return redirect(url_for("first_blood_admin.first_blood_settings"))

    ok, err = send_discord_webhook_sync(
        webhook,
        "🎯🩸 First Blood for **Test Challenge** goes to **BKSEC Organizers** (test message)",
    )
    if ok:
        flash("Test message sent successfully to Discord.", "success")
    else:
        flash(f"Failed to send test message: {err}", "danger")
    return redirect(url_for("first_blood_admin.first_blood_settings"))


def load(app):
    app.register_blueprint(admin_blueprint)

    TEAMS_MODE = ctfd_config.is_teams_mode()

    def challenge_attempt_decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            result = f(*args, **kwargs)

            if not isinstance(result, Response):
                return result

            data = result.json
            if not (
                isinstance(data, dict)
                and data.get("success") is True
                and isinstance(data.get("data"), dict)
                and data["data"].get("status") == "correct"
            ):
                return result

            # Parse challenge_id from request
            if request.content_type == "application/json":
                request_data = request.get_json() or {}
            else:
                request_data = request.form
            challenge_id = request_data.get("challenge_id")

            challenge = Challenges.query.filter_by(id=challenge_id).first()
            if not challenge:
                return result

            solvers = Solves.query.filter_by(challenge_id=challenge.id)
            if TEAMS_MODE:
                solvers = solvers.filter(Solves.team.has(hidden=False))
            else:
                solvers = solvers.filter(Solves.user.has(hidden=False))
            num_solves = solvers.count()

            logger.warning("[FirstBlood] challenge=%s solves=%d", challenge.name, num_solves)

            if num_solves != 1:
                return result

            webhook = get_config("FIRST_BLOOD_WEBHOOK")
            if not webhook or not is_valid_webhook(webhook):
                return result

            user = get_current_user()
            team = get_current_team()
            solver_name = team.name if team else user.name

            message = f"🎯🩸 First Blood for challenge **{challenge.name}** goes to **{solver_name}**!"
            ok, err = send_discord_webhook_sync(webhook, message)
            if not ok:
                logger.error("[FirstBlood] webhook send failed: %s", err)

            return result
        return wrapper

    app.view_functions['api.challenges_challenge_attempt'] = challenge_attempt_decorator(
        app.view_functions['api.challenges_challenge_attempt']
    )
    app.logger.info("First Blood plugin loaded")
