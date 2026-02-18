import threading
import requests
from sqlalchemy import event, text
from flask import Blueprint, request, redirect, url_for, flash, render_template_string
from CTFd.models import Solves, Challenges, Users, Teams, db
from CTFd.utils.decorators import admins_only
from CTFd.utils.config import get_config, set_config


def is_valid_webhook(url: str) -> bool:
    return url.startswith("https://discord.com/api/webhooks/") or \
           url.startswith("https://discordapp.com/api/webhooks/")


def send_discord_webhook(message: str):
    webhook = get_config("FIRST_BLOOD_WEBHOOK")
    if not webhook or not is_valid_webhook(webhook):
        return
    try:
        requests.post(webhook, json={"content": message}, timeout=5)
    except Exception:
        pass


@event.listens_for(Solves, "after_insert")
def first_blood_listener(mapper, connection, solve):
    challenge_id = solve.challenge_id

    result = connection.execute(
        text(
            "SELECT COUNT(*) FROM solves WHERE challenge_id = :cid FOR UPDATE"
        ),
        {"cid": challenge_id},
    )
    count = result.scalar()
    if count != 1:
        return

    challenge_row = connection.execute(
        text("SELECT name FROM challenges WHERE id = :id"),
        {"id": challenge_id},
    ).fetchone()

    user_row = connection.execute(
        text("SELECT name FROM users WHERE id = :id"),
        {"id": solve.user_id},
    ).fetchone()

    team_row = None
    if solve.team_id:
        team_row = connection.execute(
            text("SELECT name FROM teams WHERE id = :id"),
            {"id": solve.team_id},
        ).fetchone()

    if not challenge_row or not user_row:
        return

    solver_name = team_row.name if team_row else user_row.name
    challenge_name = challenge_row.name

    message = f"🎯🩸 First Blood for challenge **{challenge_name}** goes to **{solver_name}**"

    threading.Thread(
        target=send_discord_webhook,
        args=(message,),
        daemon=True,
    ).start()


admin_blueprint = Blueprint("first_blood_admin", __name__)

SETTINGS_TEMPLATE = """
{% extends "admin/base.html" %}
{% block content %}
<div class="container">
  <h1>First Blood Settings</h1>
  <form method="post">
    <div class="form-group">
      <label>Discord Webhook URL</label>
      <input
        type="text"
        class="form-control"
        name="webhook"
        placeholder="https://discord.com/api/webhooks/..."
        value="{{ webhook | e }}"
      >
      <small class="form-text text-muted">
        Must be a discord.com webhook URL.
      </small>
    </div>
    <button type="submit" class="btn btn-primary mt-3">Save Webhook</button>
    <a href="{{ url_for('first_blood_admin.test_webhook') }}"
       class="btn btn-secondary mt-3 ml-2">Test Webhook</a>
  </form>
</div>
{% endblock %}
"""


@admin_blueprint.route("/admin/first-blood", methods=["GET", "POST"])
@admins_only
def first_blood_settings():
    if request.method == "POST":
        webhook = request.form.get("webhook", "").strip()

        if webhook and not is_valid_webhook(webhook):
            flash("Invalid webhook URL. Must be a Discord webhook.", "danger")
            return redirect(url_for("first_blood_admin.first_blood_settings"))

        set_config("FIRST_BLOOD_WEBHOOK", webhook)

        flash("First Blood webhook saved", "success")
        return redirect(url_for("first_blood_admin.first_blood_settings"))

    webhook = get_config("FIRST_BLOOD_WEBHOOK") or ""
    return render_template_string(SETTINGS_TEMPLATE, webhook=webhook)


@admin_blueprint.route("/admin/first-blood/test")
@admins_only
def test_webhook():
    webhook = get_config("FIRST_BLOOD_WEBHOOK")
    if not webhook or not is_valid_webhook(webhook):
        flash("No valid webhook configured.", "danger")
        return redirect(url_for("first_blood_admin.first_blood_settings"))

    threading.Thread(
        target=send_discord_webhook,
        args=("🩸 First Blood test message from CTFd",),
        daemon=True,
    ).start()
    threading.Thread(
        target=send_discord_webhook,
        args=(f"🎯🩸 First Blood for a non-existent challenge **hehehehehe** goes to **BKSEC Organizers**",),
        daemon=True,
    ).start()
    flash("Test message sent", "info")
    return redirect(url_for("first_blood_admin.first_blood_settings"))


def load(app):
    app.register_blueprint(admin_blueprint)
    app.logger.info("First Blood plugin loaded")