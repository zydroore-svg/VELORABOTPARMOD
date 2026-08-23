from __future__ import annotations

import asyncio
import io
import os
import re
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DEV_GUILD_ID = int(os.getenv("DEV_GUILD_ID", "0") or 0)
DB_PATH = os.getenv("DATABASE_PATH", "data/moderation.db")

LOG_TYPES = ("warn",)
STATUS_CHOICES = [
    app_commands.Choice(name="Open", value="Open"),
    app_commands.Choice(name="In Review", value="In Review"),
    app_commands.Choice(name="Resolved", value="Resolved"),
    app_commands.Choice(name="Rejected", value="Rejected"),
]


def clean_target(value: str) -> tuple[str, Optional[int]]:
    value = value.strip()
    match = re.fullmatch(r"<@!?(\d+)>", value)
    if match:
        return value, int(match.group(1))
    if value.isdigit() and len(value) >= 15:
        return value, int(value)
    return value[:100], None


def parse_color(value: str) -> int:
    value = value.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        raise ValueError("Use a six-digit hex color such as #D32F2F.")
    return int(value, 16)


class Database:
    def __init__(self, path: str):
        self.path = path

    async def init(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    guild_id INTEGER PRIMARY KEY,
                    warn_channel INTEGER, kick_channel INTEGER, timeout_channel INTEGER,
                    ban_channel INTEGER, appeal_channel INTEGER, report_channel INTEGER,
                    panel_channel INTEGER, panel_message INTEGER,
                    ticket_category INTEGER, warn_alert_channel INTEGER,
                    discord_report_alert_channel INTEGER, game_report_alert_channel INTEGER,
                    discord_report_alert_enabled INTEGER NOT NULL DEFAULT 1, game_report_alert_enabled INTEGER NOT NULL DEFAULT 1,
                    report_archive_channel INTEGER, report_threads_enabled INTEGER NOT NULL DEFAULT 0,
                    panel_title TEXT DEFAULT 'Discord Report Center',
                    panel_description TEXT DEFAULT 'Press File Report to submit a private Discord user report with image or video evidence.',
                    panel_button TEXT DEFAULT 'File Report',
                    panel_footer TEXT DEFAULT 'Reports are visible only to authorized staff.',
                    panel_color INTEGER DEFAULT 14495300
                );
                CREATE TABLE IF NOT EXISTS cases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL, type TEXT NOT NULL,
                    target_name TEXT NOT NULL, target_id INTEGER,
                    moderator_id INTEGER NOT NULL, reason TEXT NOT NULL,
                    duration_minutes INTEGER, action_performed INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS appeals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL, target_name TEXT NOT NULL,
                    target_id INTEGER, submitted_by INTEGER NOT NULL,
                    appeal_text TEXT NOT NULL, status TEXT DEFAULT 'Pending',
                    reviewer_id INTEGER, review_note TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL, reporter_id INTEGER NOT NULL,
                    target_name TEXT NOT NULL, discord_id TEXT, category TEXT NOT NULL,
                    details TEXT NOT NULL, incident_context TEXT,
                    status TEXT DEFAULT 'Open', assigned_to INTEGER,
                    staff_note TEXT, log_channel INTEGER, log_message INTEGER,
                    ticket_channel INTEGER, panel_id INTEGER, deleted_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    action_taken TEXT, player_id INTEGER,
                    archive_thread_id INTEGER, archive_index_message INTEGER
                );
                CREATE TABLE IF NOT EXISTS player_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    canonical_name TEXT NOT NULL,
                    discord_id TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    archive_thread_id INTEGER, archive_index_message INTEGER
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_player_profiles_discord_id
                    ON player_profiles(guild_id, discord_id) WHERE discord_id IS NOT NULL AND discord_id != '';
                CREATE TABLE IF NOT EXISTS player_aliases (
                    guild_id INTEGER NOT NULL,
                    player_id INTEGER NOT NULL,
                    alias_normalized TEXT NOT NULL,
                    alias_display TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(guild_id, alias_normalized),
                    FOREIGN KEY(player_id) REFERENCES player_profiles(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id INTEGER NOT NULL, filename TEXT NOT NULL,
                    content_type TEXT, size INTEGER, url TEXT NOT NULL,
                    FOREIGN KEY(report_id) REFERENCES reports(id)
                );
                CREATE TABLE IF NOT EXISTS case_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id INTEGER NOT NULL, filename TEXT NOT NULL,
                    content_type TEXT, size INTEGER, url TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES cases(id)
                );
                CREATE TABLE IF NOT EXISTS panel_form_slots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    panel_id INTEGER NOT NULL,
                    label TEXT NOT NULL,
                    description TEXT,
                    placeholder TEXT,
                    field_type TEXT NOT NULL DEFAULT 'short',
                    required INTEGER NOT NULL DEFAULT 1,
                    position INTEGER NOT NULL,
                    role TEXT NOT NULL DEFAULT 'custom',
                    FOREIGN KEY(panel_id) REFERENCES report_panels(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS report_field_values (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_id INTEGER NOT NULL,
                    slot_id INTEGER,
                    label TEXT NOT NULL,
                    value TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS staff_roles (
                    guild_id INTEGER NOT NULL,
                    role_id INTEGER NOT NULL,
                    added_by INTEGER NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(guild_id, role_id)
                );
                CREATE TABLE IF NOT EXISTS panel_staff_roles (
                    panel_id INTEGER NOT NULL,
                    role_id INTEGER NOT NULL,
                    added_by INTEGER NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(panel_id, role_id),
                    FOREIGN KEY(panel_id) REFERENCES report_panels(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS report_panels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    channel_id INTEGER NOT NULL,
                    submission_channel_id INTEGER,
                    claim_enabled INTEGER NOT NULL DEFAULT 1,
                    delete_enabled INTEGER NOT NULL DEFAULT 1,
                    deny_enabled INTEGER NOT NULL DEFAULT 1,
                    notification_channel_id INTEGER,
                    report_type TEXT NOT NULL DEFAULT 'discord',
                    form_title TEXT NOT NULL DEFAULT 'File a Discord Report',
                    username_label TEXT NOT NULL DEFAULT 'Discord Username',
                    username_description TEXT NOT NULL DEFAULT 'Enter the reported user name.',
                    username_placeholder TEXT NOT NULL DEFAULT 'Example: username or display name',
                    username_required INTEGER NOT NULL DEFAULT 1,
                    discord_id_label TEXT NOT NULL DEFAULT 'Discord ID',
                    discord_id_description TEXT NOT NULL DEFAULT 'Enter the reported user ID.',
                    discord_id_placeholder TEXT NOT NULL DEFAULT 'Example: 123456789012345678',
                    discord_id_required INTEGER NOT NULL DEFAULT 1,
                    rules_label TEXT NOT NULL DEFAULT 'Rules Broken',
                    rules_description TEXT NOT NULL DEFAULT 'List every rule involved.',
                    rules_placeholder TEXT NOT NULL DEFAULT 'List the rule or rules that were broken',
                    rules_required INTEGER NOT NULL DEFAULT 1,
                    context_label TEXT NOT NULL DEFAULT 'Context',
                    context_description TEXT NOT NULL DEFAULT 'Explain the incident clearly.',
                    context_placeholder TEXT NOT NULL DEFAULT 'Explain what happened, including relevant dates, channels, and details.',
                    context_required INTEGER NOT NULL DEFAULT 1,
                    evidence_label TEXT NOT NULL DEFAULT 'Evidence',
                    evidence_description TEXT NOT NULL DEFAULT 'Optional: upload up to 10 images or videos.',
                    evidence_required INTEGER NOT NULL DEFAULT 0,
                    evidence_max INTEGER NOT NULL DEFAULT 10,
                    evidence_enabled INTEGER NOT NULL DEFAULT 1,
                    message_id INTEGER,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    button_text TEXT NOT NULL,
                    footer TEXT,
                    color INTEGER NOT NULL DEFAULT 14495300,
                    created_by INTEGER NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(guild_id, name)
                );
                """
            )
            # Lightweight migrations for existing databases.
            for statement in (
                "ALTER TABLE settings ADD COLUMN ticket_category INTEGER",
                "ALTER TABLE settings ADD COLUMN warn_alert_channel INTEGER",
                "ALTER TABLE settings ADD COLUMN discord_report_alert_channel INTEGER",
                "ALTER TABLE settings ADD COLUMN game_report_alert_channel INTEGER",
                "ALTER TABLE settings ADD COLUMN discord_report_alert_enabled INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE settings ADD COLUMN game_report_alert_enabled INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE settings ADD COLUMN report_archive_channel INTEGER",
                "ALTER TABLE settings ADD COLUMN report_threads_enabled INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE reports ADD COLUMN ticket_channel INTEGER",
                "ALTER TABLE reports ADD COLUMN deleted_at TEXT",
                "ALTER TABLE reports ADD COLUMN discord_id TEXT",
                "ALTER TABLE report_panels ADD COLUMN submission_channel_id INTEGER",
                "ALTER TABLE report_panels ADD COLUMN claim_enabled INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE report_panels ADD COLUMN delete_enabled INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE report_panels ADD COLUMN deny_enabled INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE report_panels ADD COLUMN notification_channel_id INTEGER",
                "ALTER TABLE report_panels ADD COLUMN report_type TEXT NOT NULL DEFAULT 'discord'",
                "ALTER TABLE reports ADD COLUMN panel_id INTEGER",
                "ALTER TABLE reports ADD COLUMN action_taken TEXT",
                "ALTER TABLE reports ADD COLUMN player_id INTEGER",
                "ALTER TABLE reports ADD COLUMN archive_thread_id INTEGER",
                "ALTER TABLE reports ADD COLUMN archive_index_message INTEGER",
                "ALTER TABLE player_profiles ADD COLUMN archive_thread_id INTEGER",
                "ALTER TABLE player_profiles ADD COLUMN archive_index_message INTEGER",
                "ALTER TABLE report_panels ADD COLUMN form_title TEXT NOT NULL DEFAULT 'File a Discord Report'",
                "ALTER TABLE report_panels ADD COLUMN username_label TEXT NOT NULL DEFAULT 'Discord Username'",
                "ALTER TABLE report_panels ADD COLUMN username_description TEXT NOT NULL DEFAULT 'Enter the reported user name.'",
                "ALTER TABLE report_panels ADD COLUMN username_placeholder TEXT NOT NULL DEFAULT 'Example: username or display name'",
                "ALTER TABLE report_panels ADD COLUMN username_required INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE report_panels ADD COLUMN discord_id_label TEXT NOT NULL DEFAULT 'Discord ID'",
                "ALTER TABLE report_panels ADD COLUMN discord_id_description TEXT NOT NULL DEFAULT 'Enter the reported user ID.'",
                "ALTER TABLE report_panels ADD COLUMN discord_id_placeholder TEXT NOT NULL DEFAULT 'Example: 123456789012345678'",
                "ALTER TABLE report_panels ADD COLUMN discord_id_required INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE report_panels ADD COLUMN rules_label TEXT NOT NULL DEFAULT 'Rules Broken'",
                "ALTER TABLE report_panels ADD COLUMN rules_description TEXT NOT NULL DEFAULT 'List every rule involved.'",
                "ALTER TABLE report_panels ADD COLUMN rules_placeholder TEXT NOT NULL DEFAULT 'List the rule or rules that were broken'",
                "ALTER TABLE report_panels ADD COLUMN rules_required INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE report_panels ADD COLUMN context_label TEXT NOT NULL DEFAULT 'Context'",
                "ALTER TABLE report_panels ADD COLUMN context_description TEXT NOT NULL DEFAULT 'Explain the incident clearly.'",
                "ALTER TABLE report_panels ADD COLUMN context_placeholder TEXT NOT NULL DEFAULT 'Explain what happened, including relevant dates, channels, and details.'",
                "ALTER TABLE report_panels ADD COLUMN context_required INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE report_panels ADD COLUMN evidence_label TEXT NOT NULL DEFAULT 'Evidence'",
                "ALTER TABLE report_panels ADD COLUMN evidence_description TEXT NOT NULL DEFAULT 'Optional: upload up to 10 images or videos.'",
                "ALTER TABLE report_panels ADD COLUMN evidence_required INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE report_panels ADD COLUMN evidence_max INTEGER NOT NULL DEFAULT 10",
                "ALTER TABLE report_panels ADD COLUMN evidence_enabled INTEGER NOT NULL DEFAULT 1",
            ):
                try:
                    await db.execute(statement)
                except aiosqlite.OperationalError:
                    pass
            # Infer the type of older panels so Discord and game report alerts remain separated.
            await db.execute(
                "UPDATE report_panels SET report_type='game' "
                "WHERE LOWER(COALESCE(form_title,'')) LIKE '%game%' "
                "OR LOWER(COALESCE(username_label,'')) LIKE '%roblox%'"
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_reports_player_id ON reports(guild_id, player_id)")
            await db.commit()
        await self.backfill_player_profiles()

    async def ensure_guild(self, guild_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("INSERT OR IGNORE INTO settings(guild_id) VALUES(?)", (guild_id,))
            await db.commit()

    async def settings(self, guild_id: int) -> aiosqlite.Row:
        await self.ensure_guild(guild_id)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM settings WHERE guild_id=?", (guild_id,))
            return await cur.fetchone()

    async def update_settings(self, guild_id: int, **values) -> None:
        await self.ensure_guild(guild_id)
        fields = ", ".join(f"{k}=?" for k in values)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(f"UPDATE settings SET {fields} WHERE guild_id=?", (*values.values(), guild_id))
            await db.commit()

    async def ensure_default_slots(self, panel) -> None:
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM panel_form_slots WHERE panel_id=?", (panel["id"],))
            if (await cur.fetchone())[0]:
                return
            defaults = [
                (panel["username_label"], panel["username_description"], panel["username_placeholder"], "short", panel["username_required"], 1, "username"),
                (panel["discord_id_label"], panel["discord_id_description"], panel["discord_id_placeholder"], "short", panel["discord_id_required"], 2, "discord_id"),
                (panel["rules_label"], panel["rules_description"], panel["rules_placeholder"], "short", panel["rules_required"], 3, "rules"),
                (panel["context_label"], panel["context_description"], panel["context_placeholder"], "paragraph", panel["context_required"], 4, "context"),
            ]
            await conn.executemany(
                "INSERT INTO panel_form_slots(panel_id,label,description,placeholder,field_type,required,position,role) VALUES(?,?,?,?,?,?,?,?)",
                [(panel["id"], *row) for row in defaults],
            )
            await conn.commit()

    async def form_slots(self, panel) -> list[aiosqlite.Row]:
        await self.ensure_default_slots(panel)
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute("SELECT * FROM panel_form_slots WHERE panel_id=? ORDER BY position,id", (panel["id"],))
            return await cur.fetchall()

    async def form_slot(self, panel_id: int, slot_id: int):
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute("SELECT * FROM panel_form_slots WHERE panel_id=? AND id=?", (panel_id, slot_id))
            return await cur.fetchone()

    async def add_form_slot(self, panel_id: int, label: str, description: str, placeholder: str, field_type: str, required: bool, role: str) -> int:
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute("SELECT COALESCE(MAX(position),0)+1 FROM panel_form_slots WHERE panel_id=?", (panel_id,))
            position = (await cur.fetchone())[0]
            cur = await conn.execute(
                "INSERT INTO panel_form_slots(panel_id,label,description,placeholder,field_type,required,position,role) VALUES(?,?,?,?,?,?,?,?)",
                (panel_id,label,description,placeholder,field_type,int(required),position,role),
            )
            await conn.commit()
            return cur.lastrowid

    async def update_form_slot(self, panel_id: int, slot_id: int, **values) -> None:
        fields = ", ".join(f"{k}=?" for k in values)
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(f"UPDATE panel_form_slots SET {fields} WHERE panel_id=? AND id=?", (*values.values(),panel_id,slot_id))
            await conn.commit()

    async def delete_form_slot(self, panel_id: int, slot_id: int) -> None:
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute("DELETE FROM panel_form_slots WHERE panel_id=? AND id=?", (panel_id,slot_id))
            cur = await conn.execute("SELECT id FROM panel_form_slots WHERE panel_id=? ORDER BY position,id", (panel_id,))
            ids = [r[0] for r in await cur.fetchall()]
            for pos, sid in enumerate(ids,1):
                await conn.execute("UPDATE panel_form_slots SET position=? WHERE id=?", (pos,sid))
            await conn.commit()

    async def save_report_field_values(self, report_id: int, values: list[tuple[int,str,str,int]]) -> None:
        async with aiosqlite.connect(self.path) as conn:
            await conn.executemany(
                "INSERT INTO report_field_values(report_id,slot_id,label,value,position) VALUES(?,?,?,?,?)",
                [(report_id,*row) for row in values],
            )
            await conn.commit()

    async def replace_form_slots(self, panel_id: int, slots: list[tuple[str, str, str, str, bool, str]]) -> None:
        """Replace all configurable text fields for a panel in one transaction."""
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute("DELETE FROM panel_form_slots WHERE panel_id=?", (panel_id,))
            await conn.executemany(
                "INSERT INTO panel_form_slots(panel_id,label,description,placeholder,field_type,required,position,role) VALUES(?,?,?,?,?,?,?,?)",
                [
                    (panel_id, label, description, placeholder, field_type, int(required), position, role)
                    for position, (label, description, placeholder, field_type, required, role) in enumerate(slots, 1)
                ],
            )
            await conn.commit()

    async def create_panel(self, guild_id: int, name: str, channel_id: int, submission_channel_id: int, title: str, description: str, button_text: str, footer: str, color: int, created_by: int, claim_enabled: bool = True, delete_enabled: bool = True, deny_enabled: bool = True) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "INSERT INTO report_panels(guild_id,name,channel_id,submission_channel_id,title,description,button_text,footer,color,created_by,claim_enabled,delete_enabled,deny_enabled) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (guild_id, name, channel_id, submission_channel_id, title, description, button_text, footer, color, created_by, int(claim_enabled), int(delete_enabled), int(deny_enabled)),
            )
            await db.commit()
            return cur.lastrowid

    async def set_panel_message(self, panel_id: int, channel_id: int, message_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE report_panels SET channel_id=?,message_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (channel_id, message_id, panel_id))
            await db.commit()

    async def panel(self, guild_id: int, panel_id: int):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM report_panels WHERE guild_id=? AND id=?", (guild_id, panel_id))
            return await cur.fetchone()

    async def panels(self, guild_id: int):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM report_panels WHERE guild_id=? ORDER BY id", (guild_id,))
            return await cur.fetchall()

    async def panel_by_message(self, guild_id: int, channel_id: int, message_id: int):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM report_panels WHERE guild_id=? AND channel_id=? AND message_id=?",
                (guild_id, channel_id, message_id),
            )
            return await cur.fetchone()

    async def update_panel(self, guild_id: int, panel_id: int, **values) -> bool:
        fields = ", ".join(f"{key}=?" for key in values)
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(f"UPDATE report_panels SET {fields},updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND id=?", (*values.values(), guild_id, panel_id))
            await db.commit()
            return cur.rowcount > 0

    async def delete_panel(self, guild_id: int, panel_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("DELETE FROM report_panels WHERE guild_id=? AND id=?", (guild_id, panel_id))
            await db.commit()
            return cur.rowcount > 0

    async def add_case(self, **values) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "INSERT INTO cases(guild_id,type,target_name,target_id,moderator_id,reason,duration_minutes,action_performed) VALUES(?,?,?,?,?,?,?,?)",
                (values["guild_id"], values["type"], values["target_name"], values.get("target_id"), values["moderator_id"], values["reason"], values.get("duration_minutes"), int(values.get("action_performed", False))),
            )
            await db.commit()
            return cur.lastrowid

    async def count_cases_for_target(self, guild_id: int, kind: str, target_name: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM cases WHERE guild_id=? AND type=? AND LOWER(TRIM(target_name))=LOWER(TRIM(?))",
                (guild_id, kind, target_name),
            )
            row = await cur.fetchone()
            return int(row[0] if row else 0)

    @staticmethod
    def _clean_discord_id(value: str) -> str:
        digits = "".join(ch for ch in (value or "") if ch.isdigit())
        return digits if len(digits) >= 15 else ""

    async def resolve_player(self, guild_id: int, username: str, discord_id: str = "", create: bool = True):
        """Resolve every panel submission to one database player profile.

        Discord ID is the strongest key. Username aliases are secondary and normalized.
        This avoids scanning Discord channels and works even after messages/channels are deleted.
        """
        alias = self.normalize_identity(username)
        did = self._clean_discord_id(discord_id)
        if not alias and not did:
            return None
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            profile = None
            if did:
                cur = await conn.execute(
                    "SELECT * FROM player_profiles WHERE guild_id=? AND discord_id=?",
                    (guild_id, did),
                )
                profile = await cur.fetchone()
            alias_profile = None
            if alias:
                cur = await conn.execute(
                    "SELECT p.* FROM player_aliases a JOIN player_profiles p ON p.id=a.player_id "
                    "WHERE a.guild_id=? AND a.alias_normalized=?",
                    (guild_id, alias),
                )
                alias_profile = await cur.fetchone()
            if profile is None:
                profile = alias_profile
            elif alias_profile is not None and alias_profile["id"] != profile["id"]:
                # A stable Discord ID proves both aliases belong to the same person.
                old_id, keep_id = alias_profile["id"], profile["id"]
                await conn.execute("UPDATE reports SET player_id=? WHERE guild_id=? AND player_id=?", (keep_id, guild_id, old_id))
                await conn.execute("UPDATE OR IGNORE player_aliases SET player_id=? WHERE guild_id=? AND player_id=?", (keep_id, guild_id, old_id))
                await conn.execute("DELETE FROM player_aliases WHERE guild_id=? AND player_id=?", (guild_id, old_id))
                await conn.execute("DELETE FROM player_profiles WHERE guild_id=? AND id=?", (guild_id, old_id))
            if profile is None and create:
                display = (username or did or "Unknown Player").strip()[:100]
                cur = await conn.execute(
                    "INSERT INTO player_profiles(guild_id,canonical_name,discord_id) VALUES(?,?,?)",
                    (guild_id, display, did or None),
                )
                player_id = cur.lastrowid
            elif profile is not None:
                player_id = profile["id"]
                updates=[]; params=[]
                if username and username.strip():
                    updates.append("canonical_name=?"); params.append(username.strip()[:100])
                if did:
                    updates.append("discord_id=?"); params.append(did)
                if updates:
                    params.extend([guild_id, player_id])
                    await conn.execute(
                        f"UPDATE player_profiles SET {','.join(updates)},updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND id=?",
                        params,
                    )
            else:
                return None
            aliases=[]
            if alias:
                aliases.append((alias, (username or alias)[:100]))
            if did:
                aliases.append((did, did))
            for normalized, display in aliases:
                await conn.execute(
                    "INSERT INTO player_aliases(guild_id,player_id,alias_normalized,alias_display) VALUES(?,?,?,?) "
                    "ON CONFLICT(guild_id,alias_normalized) DO UPDATE SET player_id=excluded.player_id,alias_display=excluded.alias_display",
                    (guild_id, player_id, normalized, display),
                )
            await conn.commit()
            cur = await conn.execute("SELECT * FROM player_profiles WHERE id=?", (player_id,))
            return await cur.fetchone()

    async def backfill_player_profiles(self) -> None:
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT id,guild_id,target_name,discord_id FROM reports WHERE player_id IS NULL ORDER BY id"
            )
            rows = await cur.fetchall()
        for row in rows:
            profile = await self.resolve_player(row["guild_id"], row["target_name"], row["discord_id"] or "", True)
            if profile:
                async with aiosqlite.connect(self.path) as conn:
                    await conn.execute("UPDATE reports SET player_id=? WHERE id=?", (profile["id"], row["id"]))
                    await conn.commit()

    async def add_player_alias(self, guild_id: int, existing_identity: str, alias: str) -> bool:
        profile = await self.resolve_player(guild_id, existing_identity, existing_identity, False)
        if not profile:
            profile = await self.resolve_player(guild_id, existing_identity, "", False)
        normalized = self.normalize_identity(alias)
        if not profile or not normalized:
            return False
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                "INSERT INTO player_aliases(guild_id,player_id,alias_normalized,alias_display) VALUES(?,?,?,?) "
                "ON CONFLICT(guild_id,alias_normalized) DO UPDATE SET player_id=excluded.player_id,alias_display=excluded.alias_display",
                (guild_id, profile["id"], normalized, alias[:100]),
            )
            await conn.commit()
        return True

    async def count_reports_for_target(self, guild_id: int, target_name: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM reports WHERE guild_id=? AND LOWER(TRIM(target_name))=LOWER(TRIM(?))",
                (guild_id, target_name),
            )
            row = await cur.fetchone()
            return int(row[0] if row else 0)

    async def count_reports_for_target_type(self, guild_id: int, target_name: str, report_type: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM reports r LEFT JOIN report_panels p ON p.id=r.panel_id "
                "WHERE r.guild_id=? AND LOWER(TRIM(r.target_name))=LOWER(TRIM(?)) "
                "AND COALESCE(p.report_type,'discord')=?",
                (guild_id, target_name, report_type),
            )
            row = await cur.fetchone()
            return int(row[0] if row else 0)

    async def add_case_evidence(self, case_id: int, filename: str, content_type: Optional[str], size: int, url: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO case_evidence(case_id,filename,content_type,size,url) VALUES(?,?,?,?,?)",
                (case_id, filename, content_type, size, url),
            )
            await db.commit()

    async def add_appeal(self, guild_id: int, target_name: str, target_id: Optional[int], submitter: int, text: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("INSERT INTO appeals(guild_id,target_name,target_id,submitted_by,appeal_text) VALUES(?,?,?,?,?)", (guild_id,target_name,target_id,submitter,text))
            await db.commit(); return cur.lastrowid

    async def add_report(self, guild_id: int, reporter_id: int, username: str, discord_id: str, rules: str, context: str, panel_id: int) -> int:
        profile = await self.resolve_player(guild_id, username, discord_id, True)
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "INSERT INTO reports(guild_id,reporter_id,target_name,discord_id,category,details,incident_context,panel_id,player_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (guild_id, reporter_id, username, discord_id, rules, context, "", panel_id, profile["id"] if profile else None),
            )
            await db.commit()
            return cur.lastrowid

    async def add_evidence(self, report_id: int, attachment: discord.Attachment) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("INSERT INTO evidence(report_id,filename,content_type,size,url) VALUES(?,?,?,?,?)", (report_id,attachment.filename,attachment.content_type,attachment.size,attachment.url))
            await db.commit()

    async def set_report_message(self, report_id: int, channel_id: int, message_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE reports SET log_channel=?, log_message=?, ticket_channel=? WHERE id=?",
                (channel_id, message_id, channel_id, report_id),
            )
            await db.commit()

    async def report_by_ticket(self, guild_id: int, channel_id: int):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM reports WHERE guild_id=? AND ticket_channel=? ORDER BY id DESC LIMIT 1",
                (guild_id, channel_id),
            )
            return await cur.fetchone()

    async def report_by_message(self, guild_id: int, channel_id: int, message_id: int):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM reports WHERE guild_id=? AND log_channel=? AND log_message=? LIMIT 1",
                (guild_id, channel_id, message_id),
            )
            return await cur.fetchone()

    async def reports_by_panel(self, guild_id: int, panel_id: int):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM reports WHERE guild_id=? AND panel_id=? AND log_channel IS NOT NULL AND log_message IS NOT NULL AND status!='Deleted'",
                (guild_id, panel_id),
            )
            return await cur.fetchall()

    async def archive_report_data(self, guild_id: int, report_id: int):
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT r.*, p.name AS panel_name, p.report_type AS report_type "
                "FROM reports r LEFT JOIN report_panels p ON p.id=r.panel_id "
                "WHERE r.guild_id=? AND r.id=?",
                (guild_id, report_id),
            )
            report = await cur.fetchone()
            if not report:
                return None, [], []
            cur = await conn.execute(
                "SELECT label,value,position FROM report_field_values WHERE report_id=? ORDER BY position,id",
                (report_id,),
            )
            fields = await cur.fetchall()
            cur = await conn.execute(
                "SELECT filename,url,content_type,size FROM evidence WHERE report_id=? ORDER BY id",
                (report_id,),
            )
            evidence = await cur.fetchall()
            return report, fields, evidence

    async def unarchived_reports(self, guild_id: int, limit: int = 50):
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT r.id FROM reports r LEFT JOIN player_profiles p ON p.id=r.player_id "
                "WHERE r.guild_id=? AND (r.archive_thread_id IS NULL OR p.archive_thread_id IS NULL OR r.archive_thread_id != p.archive_thread_id) "
                "ORDER BY r.id ASC LIMIT ?",
                (guild_id, max(1, min(100, int(limit)))),
            )
            return [int(row["id"]) for row in await cur.fetchall()]

    async def set_archive_thread(self, guild_id: int, report_id: int, thread_id: int, index_message_id: int) -> None:
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                "UPDATE reports SET archive_thread_id=?,archive_index_message=?,updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND id=?",
                (thread_id, index_message_id, guild_id, report_id),
            )
            await conn.commit()

    async def all_report_ids(self, guild_id: int, limit: int = 100):
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute(
                "SELECT id FROM reports WHERE guild_id=? AND status!='Deleted' ORDER BY id ASC LIMIT ?",
                (guild_id, max(1, min(500, int(limit)))),
            )
            return [int(row[0]) for row in await cur.fetchall()]

    async def reset_archive_links(self, guild_id: int) -> None:
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                "UPDATE reports SET archive_thread_id=NULL, archive_index_message=NULL WHERE guild_id=?",
                (guild_id,),
            )
            await conn.execute(
                "UPDATE player_profiles SET archive_thread_id=NULL, archive_index_message=NULL WHERE guild_id=?",
                (guild_id,),
            )
            await conn.commit()

    async def archive_counts(self, guild_id: int) -> tuple[int, int]:
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute(
                "SELECT COUNT(r.id), SUM(CASE WHEN r.archive_thread_id IS NOT NULL AND p.archive_thread_id IS NOT NULL AND r.archive_thread_id=p.archive_thread_id THEN 1 ELSE 0 END) "
                "FROM reports r LEFT JOIN player_profiles p ON p.id=r.player_id WHERE r.guild_id=?",
                (guild_id,),
            )
            row = await cur.fetchone()
            return int(row[0] or 0), int(row[1] or 0)

    async def player_archive_profile(self, guild_id: int, player_id: int):
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT * FROM player_profiles WHERE guild_id=? AND id=?",
                (guild_id, player_id),
            )
            return await cur.fetchone()

    async def set_player_archive_thread(self, guild_id: int, player_id: int, thread_id: int, index_message_id: int) -> None:
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                "UPDATE player_profiles SET archive_thread_id=?,archive_index_message=?,updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND id=?",
                (thread_id, index_message_id, guild_id, player_id),
            )
            await conn.commit()

    async def player_report_stats(self, guild_id: int, player_id: int):
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN LOWER(COALESCE(action_taken,''))='warn' THEN 1 ELSE 0 END) AS warns, "
                "SUM(CASE WHEN LOWER(COALESCE(action_taken,''))='kick' THEN 1 ELSE 0 END) AS kicks, "
                "SUM(CASE WHEN LOWER(COALESCE(action_taken,''))='ban' THEN 1 ELSE 0 END) AS bans, "
                "SUM(CASE WHEN LOWER(COALESCE(action_taken,''))='timeout' THEN 1 ELSE 0 END) AS timeouts "
                "FROM reports WHERE guild_id=? AND player_id=?",
                (guild_id, player_id),
            )
            row = await cur.fetchone()
            return {k:int(row[k] or 0) for k in ("total","warns","kicks","bans","timeouts")}

    async def players_with_two_warns(self, guild_id: int):
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT p.id,p.canonical_name,p.archive_thread_id, "
                "SUM(CASE WHEN LOWER(COALESCE(r.action_taken,''))='warn' THEN 1 ELSE 0 END) AS warns, "
                "COUNT(r.id) AS total "
                "FROM player_profiles p LEFT JOIN reports r ON r.player_id=p.id AND r.guild_id=p.guild_id "
                "WHERE p.guild_id=? GROUP BY p.id HAVING warns>=2 ORDER BY warns DESC,p.canonical_name",
                (guild_id,),
            )
            return await cur.fetchall()

    async def add_staff_role(self, guild_id: int, role_id: int, added_by: int) -> None:
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO staff_roles(guild_id,role_id,added_by) VALUES(?,?,?)",
                (guild_id, role_id, added_by),
            )
            await conn.commit()

    async def remove_staff_role(self, guild_id: int, role_id: int) -> bool:
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute("DELETE FROM staff_roles WHERE guild_id=? AND role_id=?", (guild_id, role_id))
            await conn.commit()
            return cur.rowcount > 0

    async def staff_role_ids(self, guild_id: int) -> list[int]:
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute("SELECT role_id FROM staff_roles WHERE guild_id=? ORDER BY role_id", (guild_id,))
            return [int(row[0]) for row in await cur.fetchall()]

    async def add_panel_staff_role(self, guild_id: int, panel_id: int, role_id: int, added_by: int) -> bool:
        panel = await self.panel(guild_id, panel_id)
        if not panel:
            return False
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO panel_staff_roles(panel_id,role_id,added_by) VALUES(?,?,?)",
                (panel_id, role_id, added_by),
            )
            await conn.commit()
            return True

    async def remove_panel_staff_role(self, guild_id: int, panel_id: int, role_id: int) -> bool:
        panel = await self.panel(guild_id, panel_id)
        if not panel:
            return False
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute("DELETE FROM panel_staff_roles WHERE panel_id=? AND role_id=?", (panel_id, role_id))
            await conn.commit()
            return cur.rowcount > 0

    async def panel_staff_role_ids(self, guild_id: int, panel_id: int) -> list[int]:
        panel = await self.panel(guild_id, panel_id)
        if not panel:
            return []
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute("SELECT role_id FROM panel_staff_roles WHERE panel_id=? ORDER BY role_id", (panel_id,))
            return [int(row[0]) for row in await cur.fetchall()]

    async def claim_report(self, guild_id: int, report_id: int, staff_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "UPDATE reports SET assigned_to=?,status='In Review',updated_at=CURRENT_TIMESTAMP "
                "WHERE guild_id=? AND id=? AND assigned_to IS NULL",
                (staff_id, guild_id, report_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def delete_report_ticket(self, guild_id: int, report_id: int, staff_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "UPDATE reports SET status='Deleted',staff_note=CASE WHEN staff_note IS NULL OR staff_note='' "
                "THEN ? ELSE staff_note || char(10) || ? END,deleted_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP "
                "WHERE guild_id=? AND id=?",
                (f"Ticket deleted by {staff_id}", f"Ticket deleted by {staff_id}", guild_id, report_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def deny_report(self, guild_id: int, report_id: int, staff_id: int, reason: str) -> bool:
        note = f"Denied by {staff_id}: {reason}"
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "UPDATE reports SET status='Rejected',assigned_to=?,staff_note=CASE WHEN staff_note IS NULL OR staff_note='' "
                "THEN ? ELSE staff_note || char(10) || ? END,updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND id=?",
                (staff_id, note, note, guild_id, report_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def set_report_action(self, guild_id: int, report_id: int, action: str) -> bool:
        normalized = action.strip().lower().replace(" ", "_")
        aliases = {"warning":"warn", "warned":"warn", "kicked":"kick", "banned":"ban", "time_out":"timeout", "timed_out":"timeout"}
        normalized = aliases.get(normalized, normalized)
        if normalized not in {"warn", "kick", "ban", "timeout", "none"}:
            return False
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "UPDATE reports SET action_taken=?,updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND id=?",
                (None if normalized == "none" else normalized, guild_id, report_id),
            )
            await db.commit()
            return cur.rowcount > 0

    @staticmethod
    def normalize_identity(value: str) -> str:
        value = (value or "").strip().casefold()
        # Accept mentions, @names, spaces, underscores and common display-name punctuation.
        if value.startswith("<@") and value.endswith(">"):
            value = value[2:-1].lstrip("!")
        value = value.lstrip("@").replace(" ", "").replace("_", "")
        return "".join(ch for ch in value if ch.isalnum() or ch in {".", "-"})

    async def person_history(self, guild_id: int, target: str):
        profile = await self.resolve_player(guild_id, target, target, False)
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            if profile:
                cur = await conn.execute(
                    "SELECT * FROM reports WHERE guild_id=? AND player_id=? ORDER BY id DESC",
                    (guild_id, profile["id"]),
                )
            else:
                # Compatibility fallback for records not yet migrated.
                wanted = self.normalize_identity(target)
                cur = await conn.execute("SELECT * FROM reports WHERE guild_id=? ORDER BY id DESC", (guild_id,))
                rows = await cur.fetchall()
                reports = [r for r in rows if self.normalize_identity(r["target_name"]) == wanted or self.normalize_identity(r["discord_id"] or "") == wanted]
                report_ids = [r["id"] for r in reports]
                evidence={}
                if report_ids:
                    marks=",".join("?" for _ in report_ids)
                    cur=await conn.execute(f"SELECT * FROM evidence WHERE report_id IN ({marks}) ORDER BY id", report_ids)
                    for item in await cur.fetchall(): evidence.setdefault(item["report_id"],[]).append(item)
                return profile, reports, evidence
            reports = await cur.fetchall()
            report_ids=[r["id"] for r in reports]
            evidence={}
            if report_ids:
                marks=",".join("?" for _ in report_ids)
                cur=await conn.execute(f"SELECT * FROM evidence WHERE report_id IN ({marks}) ORDER BY id", report_ids)
                for item in await cur.fetchall(): evidence.setdefault(item["report_id"],[]).append(item)
            return profile, reports, evidence

    async def case_totals_for_identity(self, guild_id: int, target: str):
        wanted = self.normalize_identity(target)
        totals = {"warn": 0, "kick": 0, "timeout": 0, "ban": 0}
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT type,target_name,target_id FROM cases WHERE guild_id=?", (guild_id,))
            for row in await cur.fetchall():
                candidates = [row["target_name"] or "", str(row["target_id"] or "")]
                if any(self.normalize_identity(v) == wanted for v in candidates):
                    kind = (row["type"] or "").lower()
                    if kind in totals:
                        totals[kind] += 1
        return totals

    async def player_report_counts(self, guild_id: int, target: str):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT status,COUNT(*) total FROM reports WHERE guild_id=? AND lower(target_name)=lower(?) GROUP BY status",
                (guild_id, target),
            )
            return await cur.fetchall()

    async def report(self, guild_id: int, report_id: int):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM reports WHERE guild_id=? AND id=?", (guild_id,report_id)); return await cur.fetchone()

    async def update_report(self, guild_id: int, report_id: int, status: str, assignee: int, note: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("UPDATE reports SET status=?,assigned_to=?,staff_note=?,updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND id=?", (status,assignee,note,guild_id,report_id)); await db.commit(); return cur.rowcount > 0

    async def counts(self, guild_id: int, target: Optional[str] = None):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            if target:
                cur = await db.execute("SELECT type,COUNT(*) total FROM cases WHERE guild_id=? AND lower(target_name)=lower(?) GROUP BY type", (guild_id,target))
            else:
                cur = await db.execute("SELECT type,COUNT(*) total FROM cases WHERE guild_id=? GROUP BY type", (guild_id,))
            return await cur.fetchall()

    async def report_counts(self, guild_id: int):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT status,COUNT(*) total FROM reports WHERE guild_id=? GROUP BY status", (guild_id,)); return await cur.fetchall()


db = Database(DB_PATH)


async def log_channel(interaction: discord.Interaction, kind: str) -> Optional[discord.TextChannel]:
    settings = await db.settings(interaction.guild_id)
    channel_id = settings[f"{kind}_channel"]
    channel = interaction.guild.get_channel(channel_id) if channel_id else None
    return channel if isinstance(channel, discord.TextChannel) else None


def mod_embed(case_id: int, kind: str, target: str, moderator: discord.abc.User, reason: str, performed: bool, duration: Optional[int] = None) -> discord.Embed:
    colors = {"warn":0xF1C40F,"kick":0xE67E22,"timeout":0x9B59B6,"ban":0xE74C3C,"appeal":0x3498DB}
    e = discord.Embed(title=f"{kind.title()} Log • Case #{case_id}", color=colors[kind], timestamp=discord.utils.utcnow())
    e.add_field(name="Target", value=target, inline=False)
    e.add_field(name="Moderator", value=f"{moderator} (`{moderator.id}`)")
    e.add_field(name="Discord action", value="Performed" if performed else "Log only")
    if duration: e.add_field(name="Duration", value=f"{duration} minutes")
    e.add_field(name="Reason", value=reason[:1024], inline=False)
    return e


def _archive_thread_name(report) -> str:
    username = (report["target_name"] or "unknown").strip()
    username = re.sub(r"[^A-Za-z0-9_.-]+", "-", username).strip("-") or "unknown"
    status = re.sub(r"[^A-Za-z0-9]+", "-", (report["status"] or "Open")).strip("-")
    return f"report-{report['id']}-{username}-{status}"[:100]


def build_archive_embed(report, fields, evidence) -> discord.Embed:
    report_type = "Game" if (report["report_type"] or "discord") == "game" else "Discord"
    embed = discord.Embed(
        title=f"{report_type} Report Archive • #{report['id']}",
        color=0x5865F2,
    )
    embed.add_field(name="Reported User", value=(report["target_name"] or "Not supplied")[:1024], inline=False)
    embed.add_field(name="Status", value=report["status"] or "Open", inline=True)
    embed.add_field(name="Action", value=(report["action_taken"] or "Pending").title(), inline=True)
    embed.add_field(name="Reporter", value=f"<@{report['reporter_id']}>", inline=True)
    if not fields:
        embed.add_field(name="Rules Broken", value=(report["category"] or "Not supplied")[:1024], inline=False)
        embed.add_field(name="Context", value=(report["details"] or "Not supplied")[:1024], inline=False)
    for field in fields[:15]:
        label = (field["label"] or "Field")[:256]
        value = (field["value"] or "Not supplied")[:1024]
        if label.casefold() in {"discord username", "roblox username"} and value.casefold() == (report["target_name"] or "").casefold():
            continue
        embed.add_field(name=label, value=value, inline=False)
    if evidence:
        links = []
        for i, item in enumerate(evidence[:10], 1):
            links.append(f"[{i}. {item['filename'][:70]}]({item['url']})")
        embed.add_field(name=f"Evidence ({len(evidence)})", value="\n".join(links)[:1024], inline=False)
    else:
        embed.add_field(name="Evidence", value="No evidence submitted.", inline=False)
    if report["log_channel"] and report["log_message"]:
        jump = f"https://discord.com/channels/{report['guild_id']}/{report['log_channel']}/{report['log_message']}"
        embed.add_field(name="Original Submission", value=f"[Open original report]({jump})", inline=False)
    embed.set_footer(text=f"Panel: {report['panel_name'] or 'Unknown'} • Created: {report['created_at']}")
    return embed


async def _get_player_thread(guild: discord.Guild, player_id: int):
    profile = await db.player_archive_profile(guild.id, player_id)
    if not profile or not profile["archive_thread_id"]:
        return None
    thread = guild.get_thread(int(profile["archive_thread_id"]))
    if thread:
        return thread
    try:
        fetched = await guild.fetch_channel(int(profile["archive_thread_id"]))
        return fetched if isinstance(fetched, discord.Thread) else None
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


async def refresh_player_thread(guild: discord.Guild, player_id: int) -> None:
    profile = await db.player_archive_profile(guild.id, player_id)
    if not profile:
        return
    thread = await _get_player_thread(guild, player_id)
    stats = await db.player_report_stats(guild.id, player_id)
    if thread:
        prefix = "⚠️ 2-WARN" if stats["warns"] >= 2 else (f"{stats['warns']}W" if stats["warns"] else "0W")
        name = f"{prefix} • {profile['canonical_name']}"[:100]
        try:
            if thread.name != name:
                await thread.edit(name=name)
        except (discord.Forbidden, discord.HTTPException):
            pass
    channel_id = (await db.settings(guild.id))["report_archive_channel"]
    channel = guild.get_channel(channel_id) if channel_id else None
    if isinstance(channel, discord.TextChannel) and profile["archive_index_message"]:
        try:
            msg = await channel.fetch_message(int(profile["archive_index_message"]))
            embed = discord.Embed(
                title=("⚠️ TWO OR MORE WARNINGS • " if stats["warns"] >= 2 else "Player Report Archive • ") + profile["canonical_name"],
                description=(
                    f"**Warns:** {stats['warns']}  •  **Kicks:** {stats['kicks']}  •  **Timeouts:** {stats['timeouts']}  •  **Bans:** {stats['bans']}\n"
                    f"**Total reports:** {stats['total']}\n\nOpen the thread to view every old and new report, rules broken, evidence, and staff actions."
                ),
                color=0xE74C3C if stats["warns"] >= 2 else 0x2B2D31,
            )
            await msg.edit(embed=embed)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


async def archive_report_to_thread(guild: discord.Guild, report_id: int, *, force: bool = False):
    settings = await db.settings(guild.id)
    if not force and not bool(settings["report_threads_enabled"]):
        return None
    channel_id = settings["report_archive_channel"]
    channel = guild.get_channel(channel_id) if channel_id else None
    if not isinstance(channel, discord.TextChannel):
        return None
    report, fields, evidence = await db.archive_report_data(guild.id, report_id)
    if not report or not report["player_id"]:
        return None
    player_id = int(report["player_id"])
    profile = await db.player_archive_profile(guild.id, player_id)
    if not profile:
        return None
    thread = await _get_player_thread(guild, player_id)
    if thread is None:
        stats = await db.player_report_stats(guild.id, player_id)
        starter_embed = discord.Embed(
            title=f"Player Report Archive • {profile['canonical_name']}",
            description=(
                f"**Warns:** {stats['warns']}  •  **Kicks:** {stats['kicks']}  •  **Timeouts:** {stats['timeouts']}  •  **Bans:** {stats['bans']}\n"
                f"**Total reports:** {stats['total']}\n\nAll existing and future reports for this player are compiled in the thread below."
            ),
            color=0xE74C3C if stats["warns"] >= 2 else 0x2B2D31,
        )
        starter = await channel.send(embed=starter_embed)
        prefix = "⚠️ 2-WARN" if stats["warns"] >= 2 else (f"{stats['warns']}W" if stats["warns"] else "0W")
        thread = await starter.create_thread(name=f"{prefix} • {profile['canonical_name']}"[:100], auto_archive_duration=10080)
        await db.set_player_archive_thread(guild.id, player_id, thread.id, starter.id)
    if thread.archived:
        try:
            await thread.edit(archived=False)
        except (discord.Forbidden, discord.HTTPException):
            pass
    # Do not duplicate a report already compiled into this player thread.
    if not report["archive_thread_id"] or int(report["archive_thread_id"]) != int(thread.id):
        await thread.send(content=f"**Report #{report_id}**", embed=build_archive_embed(report, fields, evidence))
        profile = await db.player_archive_profile(guild.id, player_id)
        await db.set_archive_thread(guild.id, report_id, thread.id, int(profile["archive_index_message"] or 0))
    await refresh_player_thread(guild, player_id)
    return thread


async def archive_event(guild: discord.Guild, report_id: int, text: str) -> None:
    report, _, _ = await db.archive_report_data(guild.id, report_id)
    if not report or not report["archive_thread_id"]:
        return
    thread = guild.get_thread(int(report["archive_thread_id"]))
    if thread is None:
        try:
            fetched = await guild.fetch_channel(int(report["archive_thread_id"]))
            thread = fetched if isinstance(fetched, discord.Thread) else None
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
    if thread:
        try:
            if thread.archived:
                await thread.edit(archived=False)
            await thread.send(text[:2000])
        except (discord.Forbidden, discord.HTTPException):
            pass


class ReportModal(discord.ui.Modal):
    def __init__(self, bot: "ReportBot", panel, slots):
        super().__init__(title=(panel["form_title"] or "File a Discord Report")[:45], timeout=600)
        self.bot = bot
        self.panel_id = panel["id"]
        self.submission_channel_id = panel["submission_channel_id"]
        self.panel = panel
        self.claim_enabled = bool(panel["claim_enabled"])
        self.delete_enabled = bool(panel["delete_enabled"])
        self.slot_inputs = []
        self.slots = list(slots)
        for slot in self.slots:
            component = discord.ui.TextInput(
                style=discord.TextStyle.paragraph if slot["field_type"] == "paragraph" else discord.TextStyle.short,
                placeholder=(slot["placeholder"] or "")[:100] or None,
                required=bool(slot["required"]),
                max_length=2000 if slot["field_type"] == "paragraph" else 300,
            )
            self.slot_inputs.append((slot, component))
            self.add_item(discord.ui.Label(
                text=slot["label"][:45], description=(slot["description"] or "")[:100] or None, component=component
            ))
        self.upload = None
        if bool(panel["evidence_enabled"]):
            evidence_max = max(1, min(10, int(panel["evidence_max"] or 10)))
            evidence_required = bool(panel["evidence_required"])
            self.upload = discord.ui.FileUpload(min_values=1 if evidence_required else 0, max_values=evidence_max, required=evidence_required)
            self.add_item(discord.ui.Label(
                text=(panel["evidence_label"] or "Evidence")[:45],
                description=(panel["evidence_description"] or "Upload image or video evidence.")[:100],
                component=self.upload,
            ))

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("Reports must be submitted in a server.", ephemeral=True)
        field_values = []
        role_values = {"username":"Not supplied", "discord_id":"Not supplied", "rules":"Not supplied", "context":"Not supplied", "action":"Not supplied"}
        for slot, component in self.slot_inputs:
            value = str(component.value or "").strip() or "Not supplied"
            field_values.append((slot["id"], slot["label"], value, slot["position"]))
            if slot["role"] in role_values:
                role_values[slot["role"]] = value
        discord_id = role_values["discord_id"]
        if discord_id != "Not supplied" and (not discord_id.isdigit() or len(discord_id) < 15):
            return await interaction.response.send_message("Discord ID must be a valid numeric ID.", ephemeral=True)
        attachments = list(self.upload.values) if self.upload else []
        for attachment in attachments:
            if not (attachment.content_type or "").startswith(("image/", "video/")):
                return await interaction.response.send_message(f"{attachment.filename} is not an image or video.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        report_id = await db.add_report(interaction.guild_id, interaction.user.id, role_values["username"], discord_id, role_values["rules"], role_values["context"], self.panel_id)
        await db.save_report_field_values(report_id, field_values)
        if role_values.get("action") not in (None, "", "Not supplied"):
            await db.set_report_action(interaction.guild_id, report_id, role_values["action"])
        submission_channel = interaction.guild.get_channel(self.submission_channel_id)
        if not isinstance(submission_channel, discord.TextChannel):
            return await interaction.followup.send("This panel's submission channel no longer exists.", ephemeral=True)
        files=[]
        for attachment in attachments:
            try:
                files.append(discord.File(io.BytesIO(await attachment.read()), filename=attachment.filename))
            except discord.HTTPException:
                pass
        embed = build_dynamic_ticket_embed(report_id, interaction.user, field_values, len(files), "Open", None, self.claim_enabled, self.delete_enabled, bool(self.panel["deny_enabled"]) if "deny_enabled" in self.panel.keys() else True)
        controls = TicketControlsView(self.bot, claim_enabled=self.claim_enabled, delete_enabled=self.delete_enabled, deny_enabled=bool(self.panel["deny_enabled"]) if "deny_enabled" in self.panel.keys() else True)
        message = await submission_channel.send(content=f"New report submitted by {interaction.user.mention}.", embed=embed, files=files, view=controls if controls.children else None)
        for original, uploaded in zip(attachments, message.attachments):
            async with aiosqlite.connect(db.path) as conn:
                await conn.execute("INSERT INTO evidence(report_id,filename,content_type,size,url) VALUES(?,?,?,?,?)", (report_id,uploaded.filename,original.content_type,uploaded.size,uploaded.url))
                await conn.commit()
        await db.set_report_message(report_id, submission_channel.id, message.id)
        try:
            await archive_report_to_thread(interaction.guild, report_id)
        except (discord.Forbidden, discord.HTTPException):
            pass

        # Alert staff separately for Discord reports and Roblox game reports.
        reported_username = role_values["username"].strip()
        if reported_username and reported_username != "Not supplied":
            report_type = (self.panel["report_type"] if "report_type" in self.panel.keys() else "discord") or "discord"
            report_count = await db.count_reports_for_target_type(interaction.guild_id, reported_username, report_type)
            if report_count == 2:
                settings = await db.settings(interaction.guild_id)
                column = "game_report_alert_channel" if report_type == "game" else "discord_report_alert_channel"
                enabled_column = "game_report_alert_enabled" if report_type == "game" else "discord_report_alert_enabled"
                alert_channel_id = settings[column] or settings["warn_alert_channel"]
                alert_channel = interaction.guild.get_channel(alert_channel_id) if alert_channel_id else None
                if bool(settings[enabled_column]) and isinstance(alert_channel, discord.TextChannel):
                    label = "Game" if report_type == "game" else "Discord"
                    alert = discord.Embed(
                        title=f"⚠️ Repeat {label} Report Alert",
                        description=(
                            f"**{reported_username}** has now appeared in **2 {label.lower()} report submissions**. "
                            "Staff should review the report history before deciding on further action."
                        ),
                        color=0xE74C3C,
                        timestamp=discord.utils.utcnow(),
                    )
                    alert.add_field(name="Latest report", value=f"[Report #{report_id}]({message.jump_url})", inline=True)
                    alert.add_field(name=f"{label} report count", value=str(report_count), inline=True)
                    alert.add_field(name="Submitted by", value=interaction.user.mention, inline=True)
                    alert.add_field(name="Source panel", value=f"Panel #{self.panel_id}", inline=True)
                    alert.set_footer(text="Discord and game report counts are tracked separately.")
                    await alert_channel.send(embed=alert)

        await interaction.followup.send(f"Your report was submitted successfully. Tracking number: **#{report_id}**", ephemeral=True)


def build_dynamic_ticket_embed(report_id, reporter, field_values, evidence_count, status, assigned_to, claim_enabled=True, delete_enabled=True, deny_enabled=True):
    color = 0xF1C40F if status == "Open" else 0x3498DB
    embed = discord.Embed(title=f"Discord Report Ticket #{report_id}", color=color, timestamp=discord.utils.utcnow())
    embed.add_field(name="Reporter", value=f"{reporter.mention} (`{reporter.id}`)", inline=False)
    for _slot_id, label, value, _position in sorted(field_values, key=lambda x:x[3]):
        embed.add_field(name=label[:256], value=value[:1024], inline=False)
    embed.add_field(name="Status", value=status)
    embed.add_field(name="Claimed by", value=f"<@{assigned_to}>" if assigned_to else "Unclaimed")
    embed.add_field(name="Evidence", value=f"{evidence_count} file(s)")
    instructions=[]
    if claim_enabled: instructions.append("Claim Ticket assigns the report to one staff member.")
    if deny_enabled: instructions.append("Deny Report marks it Rejected with a staff reason.")
    if delete_enabled: instructions.append("Delete Ticket removes the message but preserves tracking data.")
    embed.set_footer(text=" ".join(instructions) if instructions else "This report has no ticket controls enabled.")
    return embed


def build_ticket_embed(
    report_id: int, reporter: discord.abc.User, discord_username: str, discord_id: str,
    rules_broken: str, context: str, evidence_count: int, status: str, assigned_to: Optional[int],
    claim_enabled: bool = True, delete_enabled: bool = True, field_labels: Optional[dict[str, str]] = None,
) -> discord.Embed:
    color = 0xF1C40F if status == "Open" else 0x3498DB
    labels = field_labels or {}
    embed = discord.Embed(title=f"Discord Report Ticket #{report_id}", color=color, timestamp=discord.utils.utcnow())
    embed.add_field(name="Reporter", value=f"{reporter.mention} (`{reporter.id}`)", inline=False)
    embed.add_field(name=labels.get("username", "Discord Username")[:256], value=discord_username[:1024], inline=False)
    embed.add_field(name=labels.get("discord_id", "Discord ID")[:256], value=f"`{discord_id}`", inline=False)
    embed.add_field(name=labels.get("rules", "Rules Broken")[:256], value=rules_broken[:1024], inline=False)
    embed.add_field(name=labels.get("context", "Context")[:256], value=context[:1024], inline=False)
    embed.add_field(name="Status", value=status)
    embed.add_field(name="Claimed by", value=f"<@{assigned_to}>" if assigned_to else "Unclaimed")
    embed.add_field(name="Evidence", value=f"{evidence_count} file(s)")
    instructions = []
    if claim_enabled:
        instructions.append("Claim Ticket assigns the report to one staff member. Deny Report marks it Rejected with a staff reason.")
    if delete_enabled:
        instructions.append("Delete Ticket removes the submission message but preserves tracking data.")
    embed.set_footer(text=" ".join(instructions) if instructions else "This report panel has no claim or delete controls enabled.")
    return embed


async def is_ticket_staff(member: discord.Member, panel_id: Optional[int] = None) -> bool:
    """Return True when a member may claim/delete a report ticket.

    Global and panel-specific staff roles are additive. Older builds treated a
    panel role as an override, which accidentally blocked valid global staff.
    """
    guild = member.guild

    # The server owner and administrators can never be locked out.
    if member.id == guild.owner_id or member.guild_permissions.administrator:
        return True

    member_role_ids = {int(role.id) for role in member.roles}
    allowed_role_ids = {int(role_id) for role_id in await db.staff_role_ids(guild.id)}

    if panel_id is not None:
        allowed_role_ids.update(
            int(role_id) for role_id in await db.panel_staff_role_ids(guild.id, int(panel_id))
        )

    # A member only needs one configured global OR panel-specific role.
    if member_role_ids & allowed_role_ids:
        return True

    # Keep a safe permission fallback so existing moderator teams continue to
    # work even before roles are configured or after a role was deleted.
    perms = member.guild_permissions
    return bool(
        perms.manage_guild
        or perms.moderate_members
        or perms.kick_members
        or perms.ban_members
        or perms.manage_messages
        or perms.manage_channels
    )


async def resolve_report_from_interaction(interaction: discord.Interaction):
    """Resolve a ticket record and repair old/missing message mappings."""
    if not interaction.guild_id or not interaction.channel_id or not interaction.message:
        return None

    row = await db.report_by_message(
        interaction.guild_id, interaction.channel_id, interaction.message.id
    )
    if row:
        return row

    # Older versions sometimes stored an incomplete message mapping. Recover the
    # report number from the embed title, then repair the database mapping.
    report_id = None
    for embed in interaction.message.embeds:
        match = re.search(r"#(\d+)", embed.title or "")
        if match:
            report_id = int(match.group(1))
            break

    if report_id is not None:
        row = await db.report(interaction.guild_id, report_id)
        if row:
            await db.set_report_message(report_id, interaction.channel_id, interaction.message.id)
            return await db.report(interaction.guild_id, report_id)

    # Final fallback for ticket-channel style reports.
    row = await db.report_by_ticket(interaction.guild_id, interaction.channel_id)
    if row:
        await db.set_report_message(row["id"], interaction.channel_id, interaction.message.id)
        return await db.report(interaction.guild_id, row["id"])
    return None


async def send_ticket_action_notification(
    guild: discord.Guild, panel, row, action: str, staff: discord.Member,
    reason: Optional[str] = None, source_message: Optional[discord.Message] = None,
) -> None:
    if not panel or "notification_channel_id" not in panel.keys() or not panel["notification_channel_id"]:
        return
    channel = guild.get_channel(int(panel["notification_channel_id"]))
    if not isinstance(channel, discord.TextChannel):
        return
    color = discord.Color.blue() if action == "Claimed" else discord.Color.red()
    embed = discord.Embed(
        title=f"Report #{row['id']} {action}",
        color=color, timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Reported player", value=row["target_name"] or "Not supplied", inline=True)
    embed.add_field(name="Handled by", value=staff.mention, inline=True)
    embed.add_field(name="Source panel", value=f"Panel #{row['panel_id']}" if row["panel_id"] else "Unknown", inline=True)
    if reason:
        embed.add_field(name="Denial reason", value=reason[:1024], inline=False)
    if source_message:
        embed.add_field(name="Submission", value=f"[Open report]({source_message.jump_url})", inline=False)
    await channel.send(embed=embed)


class TicketControlsView(discord.ui.View):
    def __init__(self, bot: "ReportBot", claimed: bool = False, claim_enabled: bool = True, delete_enabled: bool = True, deny_enabled: bool = True):
        super().__init__(timeout=None)
        self.bot = bot
        if not claim_enabled:
            self.remove_item(self.claim)
        elif claimed:
            self.claim.disabled = True
            self.claim.label = "Claimed"
        if not delete_enabled:
            self.remove_item(self.delete)
        if not deny_enabled:
            self.remove_item(self.deny)

    @discord.ui.button(
        label="Claim Ticket", style=discord.ButtonStyle.success, emoji="✋",
        custom_id="report_ticket:claim:v1"
    )
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("This button can only be used inside a server.", ephemeral=True)
        row = await resolve_report_from_interaction(interaction)
        if not row:
            # Remove stale controls without posting an extra error message.
            return await interaction.response.edit_message(view=None)
        if not await is_ticket_staff(interaction.user, row["panel_id"]):
            return await interaction.response.send_message("You cannot claim this ticket. Ask an administrator to add your role with `/setup add-staff-role`, or authorize it for this panel.", ephemeral=True)
        if row["assigned_to"]:
            return await interaction.response.send_message(f"This ticket is already claimed by <@{row['assigned_to']}>.", ephemeral=True)
        claimed = await db.claim_report(interaction.guild_id, row["id"], interaction.user.id)
        if not claimed:
            return await interaction.response.send_message("Another staff member claimed this ticket first.", ephemeral=True)
        embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed(title=f"Report Ticket #{row['id']}")
        for index, field in enumerate(embed.fields):
            if field.name == "Status":
                embed.set_field_at(index, name="Status", value="In Review", inline=field.inline)
            elif field.name == "Claimed by":
                embed.set_field_at(index, name="Claimed by", value=interaction.user.mention, inline=field.inline)
        embed.color = discord.Color.blue()
        panel = await db.panel(interaction.guild_id, row["panel_id"]) if row["panel_id"] else None
        claim_enabled = bool(panel["claim_enabled"]) if panel else True
        delete_enabled = bool(panel["delete_enabled"]) if panel else True
        controls = TicketControlsView(self.bot, claimed=True, claim_enabled=claim_enabled, delete_enabled=delete_enabled, deny_enabled=bool(panel["deny_enabled"]) if panel else True)
        await interaction.response.edit_message(embed=embed, view=controls if controls.children else None)
        await send_ticket_action_notification(
            interaction.guild, panel, row, "Claimed", interaction.user, source_message=interaction.message
        )
        await archive_event(interaction.guild, row["id"], f"✋ **Claimed** by {interaction.user.mention}.")

    @discord.ui.button(
        label="Deny Report", style=discord.ButtonStyle.secondary, emoji="⛔",
        custom_id="report_ticket:deny:v1"
    )
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("This button can only be used inside a server.", ephemeral=True)
        row = await resolve_report_from_interaction(interaction)
        if not row:
            return await interaction.response.edit_message(view=None)
        if not await is_ticket_staff(interaction.user, row["panel_id"]):
            return await interaction.response.send_message("Only an administrator or configured staff role can deny this report.", ephemeral=True)
        await interaction.response.send_modal(DenyReportModal(self.bot, row["id"]))

    @discord.ui.button(
        label="Delete Ticket", style=discord.ButtonStyle.danger, emoji="🗑️",
        custom_id="report_ticket:delete:v1"
    )
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("This button can only be used inside a server.", ephemeral=True)
        row = await resolve_report_from_interaction(interaction)
        if not row:
            # Remove stale controls without posting an extra error message.
            return await interaction.response.edit_message(view=None)
        if not await is_ticket_staff(interaction.user, row["panel_id"]):
            return await interaction.response.send_message("You cannot delete this ticket. Ask an administrator to add your role with `/setup add-staff-role`, or authorize it for this panel.", ephemeral=True)
        await db.delete_report_ticket(interaction.guild_id, row["id"], interaction.user.id)
        await interaction.response.send_message(
            f"Report **#{row['id']}** will be deleted in 5 seconds. Its tracker record will remain saved.",
            ephemeral=True,
        )
        await asyncio.sleep(5)
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            pass


class DenyReportModal(discord.ui.Modal, title="Deny Report"):
    def __init__(self, bot: "ReportBot", report_id: int):
        super().__init__(timeout=600)
        self.bot = bot
        self.report_id = report_id
        self.reason = discord.ui.TextInput(
            label="Reason for denial",
            style=discord.TextStyle.paragraph,
            placeholder="Explain why this report is being denied.",
            min_length=2,
            max_length=1000,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        row = await db.report(interaction.guild_id, self.report_id)
        if not row:
            return await interaction.response.send_message("Report not found.", ephemeral=True)
        if not isinstance(interaction.user, discord.Member) or not await is_ticket_staff(interaction.user, row["panel_id"]):
            return await interaction.response.send_message("You are not authorized to deny this report.", ephemeral=True)
        reason = str(self.reason.value).strip()
        await db.deny_report(interaction.guild_id, self.report_id, interaction.user.id, reason)
        message = interaction.message
        embed = message.embeds[0] if message and message.embeds else discord.Embed(title=f"Report Ticket #{self.report_id}")
        for index, field in enumerate(embed.fields):
            if field.name == "Status":
                embed.set_field_at(index, name="Status", value="Rejected", inline=field.inline)
            elif field.name == "Claimed by":
                embed.set_field_at(index, name="Claimed by", value=interaction.user.mention, inline=field.inline)
        embed.add_field(name="Denial Reason", value=reason[:1024], inline=False)
        embed.color = discord.Color.red()
        panel = await db.panel(interaction.guild_id, row["panel_id"]) if row["panel_id"] else None
        view = TicketControlsView(
            self.bot, claimed=True,
            claim_enabled=bool(panel["claim_enabled"]) if panel else True,
            delete_enabled=bool(panel["delete_enabled"]) if panel else True,
            deny_enabled=False,
        )
        await interaction.response.edit_message(embed=embed, view=view if view.children else None)
        await send_ticket_action_notification(
            interaction.guild, panel, row, "Denied", interaction.user, reason=reason, source_message=message
        )
        await archive_event(interaction.guild, self.report_id, f"⛔ **Denied** by {interaction.user.mention}.\n**Reason:** {reason}")
        await interaction.followup.send(f"Report **#{self.report_id}** was denied.", ephemeral=True)


class ReportPanelView(discord.ui.View):
    def __init__(self, bot: "ReportBot", label: str = "File Report"):
        super().__init__(timeout=None)
        button = discord.ui.Button(label=label[:80], style=discord.ButtonStyle.danger, emoji="📁", custom_id="report_tracker:submit:v1")
        button.callback = self.open_form
        self.add_item(button)
        self.bot = bot

    async def open_form(self, interaction: discord.Interaction):
        if not interaction.guild or not interaction.message:
            return await interaction.response.send_message("This panel is not available here.", ephemeral=True)
        panel = await db.panel_by_message(interaction.guild_id, interaction.channel_id, interaction.message.id)
        if not panel or not panel["submission_channel_id"]:
            return await interaction.response.send_message(
                "This panel has no submission channel configured. Ask an administrator to fix it.",
                ephemeral=True,
            )
        slots = await db.form_slots(panel)
        max_text_slots = 4 if bool(panel["evidence_enabled"]) else 5
        if len(slots) > max_text_slots:
            return await interaction.response.send_message(
                f"This form has too many slots. Maximum is {max_text_slots} text slot(s) with the current evidence setting.", ephemeral=True
            )
        await interaction.response.send_modal(ReportModal(self.bot, panel, slots))


class FormSlotModal(discord.ui.Modal):
    def __init__(self, panel_id: int, slot=None):
        super().__init__(title=("Edit" if slot else "Add") + " Form Slot", timeout=600)
        self.panel_id = panel_id
        self.slot_id = slot["id"] if slot else None
        self.label_input = discord.ui.TextInput(label="Field label", default=slot["label"] if slot else "", max_length=45)
        self.description_input = discord.ui.TextInput(label="Description (optional)", default=(slot["description"] or "") if slot else "", required=False, max_length=100)
        self.placeholder_input = discord.ui.TextInput(label="Placeholder (optional)", default=(slot["placeholder"] or "") if slot else "", required=False, max_length=100)
        self.type_input = discord.ui.TextInput(label="Type: SHORT or PARAGRAPH", default=(slot["field_type"] if slot else "short").upper(), max_length=9)
        default_setting = (("YES" if slot["required"] else "NO") + ":" + slot["role"]) if slot else "YES:custom"
        self.settings_input = discord.ui.TextInput(label="Required:Role", description="Example YES:custom or NO:username", default=default_setting, max_length=30)
        for item in (self.label_input,self.description_input,self.placeholder_input,self.type_input,self.settings_input): self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        field_type=str(self.type_input.value).strip().lower()
        if field_type not in {"short","paragraph"}:
            return await interaction.response.send_message("Type must be SHORT or PARAGRAPH.", ephemeral=True)
        parts=str(self.settings_input.value).strip().lower().split(":",1)
        if len(parts)!=2 or parts[0] not in {"yes","no","y","n"} or parts[1] not in {"custom","username","discord_id","rules","context","action"}:
            return await interaction.response.send_message("Use Required:Role, such as YES:custom or NO:username.", ephemeral=True)
        required=parts[0] in {"yes","y"}; role=parts[1]
        values=dict(label=str(self.label_input.value).strip()[:45], description=str(self.description_input.value).strip()[:100], placeholder=str(self.placeholder_input.value).strip()[:100], field_type=field_type, required=int(required), role=role)
        if not values["label"]:
            return await interaction.response.send_message("Field label cannot be empty.", ephemeral=True)
        if self.slot_id:
            await db.update_form_slot(self.panel_id,self.slot_id,**values)
            msg=f"Form slot **#{self.slot_id}** updated."
        else:
            slot_id=await db.add_form_slot(self.panel_id,**values)
            msg=f"Form slot **#{slot_id}** added."
        await interaction.response.send_message(msg, ephemeral=True)


class FormLabelsModal(discord.ui.Modal):
    """Edits only the modal title. Individual fields are managed as removable slots."""
    def __init__(self, panel):
        super().__init__(title=f"Form Title • Panel #{panel['id']}", timeout=600)
        self.panel_id = panel["id"]
        self.form_title = discord.ui.TextInput(
            label="Form title",
            default=panel["form_title"],
            description="This appears at the top of the report form.",
            max_length=45,
        )
        self.add_item(self.form_title)

    async def on_submit(self, interaction: discord.Interaction):
        await db.update_panel(
            interaction.guild_id,
            self.panel_id,
            form_title=str(self.form_title.value).strip()[:45],
        )
        await interaction.response.send_message(
            f"Form title for panel **#{self.panel_id}** was updated.", ephemeral=True
        )


class FormPlaceholdersModal(discord.ui.Modal):
    def __init__(self, panel):
        super().__init__(title=f"Form Help Text • Panel #{panel['id']}", timeout=600)
        self.panel_id = panel["id"]
        self.username_placeholder = discord.ui.TextInput(label="Username placeholder", default=panel["username_placeholder"], max_length=100)
        self.discord_id_placeholder = discord.ui.TextInput(label="Discord ID placeholder", default=panel["discord_id_placeholder"], max_length=100)
        self.rules_placeholder = discord.ui.TextInput(label="Rules placeholder", default=panel["rules_placeholder"], max_length=100)
        self.context_placeholder = discord.ui.TextInput(label="Context placeholder", default=panel["context_placeholder"], max_length=100)
        self.evidence_description = discord.ui.TextInput(label="Evidence description", default=panel["evidence_description"], max_length=100)
        for item in (self.username_placeholder, self.discord_id_placeholder, self.rules_placeholder, self.context_placeholder, self.evidence_description):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        await db.update_panel(
            interaction.guild_id, self.panel_id,
            username_placeholder=str(self.username_placeholder.value),
            discord_id_placeholder=str(self.discord_id_placeholder.value),
            rules_placeholder=str(self.rules_placeholder.value),
            context_placeholder=str(self.context_placeholder.value),
            evidence_description=str(self.evidence_description.value),
        )
        await interaction.response.send_message(
            f"Form placeholders for panel **#{self.panel_id}** were updated.", ephemeral=True
        )


class FormDescriptionsModal(discord.ui.Modal, title="Edit Form Descriptions"):
    def __init__(self, panel):
        super().__init__(timeout=600)
        self.panel_id = panel["id"]
        self.username_description = discord.ui.TextInput(label="Username description", default=panel["username_description"] or "", required=False, max_length=100)
        self.discord_id_description = discord.ui.TextInput(label="Discord ID description", default=panel["discord_id_description"] or "", required=False, max_length=100)
        self.rules_description = discord.ui.TextInput(label="Rules description", default=panel["rules_description"] or "", required=False, max_length=100)
        self.context_description = discord.ui.TextInput(label="Context description", default=panel["context_description"] or "", required=False, max_length=100)
        self.evidence_description = discord.ui.TextInput(label="Evidence description", default=panel["evidence_description"] or "", required=False, max_length=100)
        for item in (self.username_description, self.discord_id_description, self.rules_description, self.context_description, self.evidence_description):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        await db.update_panel(
            interaction.guild_id, self.panel_id,
            username_description=str(self.username_description.value).strip(),
            discord_id_description=str(self.discord_id_description.value).strip(),
            rules_description=str(self.rules_description.value).strip(),
            context_description=str(self.context_description.value).strip(),
            evidence_description=str(self.evidence_description.value).strip(),
        )
        await interaction.response.send_message(f"Form descriptions for panel **#{self.panel_id}** were updated.", ephemeral=True)


class FormEvidenceModal(discord.ui.Modal, title="Edit Evidence Field"):
    def __init__(self, panel):
        super().__init__(timeout=600)
        self.panel_id = panel["id"]
        self.evidence_label = discord.ui.TextInput(label="Evidence field label", default=panel["evidence_label"] or "Evidence", max_length=45)
        self.evidence_description = discord.ui.TextInput(label="Evidence help text", default=panel["evidence_description"] or "", required=False, max_length=100)
        self.evidence_max = discord.ui.TextInput(label="Maximum files (1-10)", default=str(panel["evidence_max"] or 10), max_length=2)
        self.evidence_required = discord.ui.TextInput(label="Required? Type YES or NO", default="YES" if panel["evidence_required"] else "NO", max_length=3)
        for item in (self.evidence_label, self.evidence_description, self.evidence_max, self.evidence_required):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            maximum = int(str(self.evidence_max.value).strip())
        except ValueError:
            return await interaction.response.send_message("Maximum files must be a number from 1 to 10.", ephemeral=True)
        if not 1 <= maximum <= 10:
            return await interaction.response.send_message("Maximum files must be from 1 to 10.", ephemeral=True)
        required_text = str(self.evidence_required.value).strip().lower()
        if required_text not in {"yes", "no", "y", "n"}:
            return await interaction.response.send_message("For Required, type YES or NO.", ephemeral=True)
        await db.update_panel(
            interaction.guild_id, self.panel_id,
            evidence_label=str(self.evidence_label.value).strip()[:45] or "Evidence",
            evidence_description=str(self.evidence_description.value).strip()[:100],
            evidence_max=maximum, evidence_required=int(required_text in {"yes", "y"}),
        )
        await interaction.response.send_message(f"Evidence settings for panel **#{self.panel_id}** were updated.", ephemeral=True)


class FormSlotSelect(discord.ui.Select):
    def __init__(self, slots):
        options = [
            discord.SelectOption(
                label=str(slot["label"])[:100],
                value=str(slot["id"]),
                description=(
                    f"{slot['field_type'].title()} • "
                    f"{'Required' if slot['required'] else 'Optional'} • {slot['role']}"
                )[:100],
            )
            for slot in slots[:25]
        ]
        if not options:
            options = [discord.SelectOption(label="No fields configured", value="none")]
        super().__init__(
            placeholder="Select a form field to edit or delete",
            min_values=1,
            max_values=1,
            options=options,
            disabled=not slots,
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, FormSlotManagerView):
            return
        if self.values and self.values[0] != "none":
            view.selected_slot_id = int(self.values[0])
            slot = await db.form_slot(view.panel_id, view.selected_slot_id)
            if slot:
                await interaction.response.send_message(
                    f"Selected **#{slot['id']} — {slot['label']}**. Use **Edit Selected** or **Delete Selected** below.",
                    ephemeral=True,
                )
                return
        await interaction.response.defer()


class FormSlotManagerView(discord.ui.View):
    def __init__(self, panel, owner_id: int, slots):
        super().__init__(timeout=600)
        self.panel_id = int(panel["id"])
        self.owner_id = owner_id
        self.evidence_enabled = bool(panel["evidence_enabled"])
        self.slots = {int(slot["id"]): slot for slot in slots}
        self.selected_slot_id = None
        self.add_item(FormSlotSelect(slots))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the administrator who opened this field manager can use it.", ephemeral=True
            )
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        print(f"FormSlotManagerView error on {type(item).__name__}: {error!r}")
        try:
            if interaction.response.is_done():
                await interaction.followup.send("The field editor encountered an error. Reopen `/report edit-form` and try again.", ephemeral=True)
            else:
                await interaction.response.send_message("The field editor encountered an error. Reopen `/report edit-form` and try again.", ephemeral=True)
        except Exception:
            pass

    @discord.ui.button(label="Add Field", style=discord.ButtonStyle.success, emoji="➕", row=1)
    async def add_field(self, interaction: discord.Interaction, button: discord.ui.Button):
        maximum = 4 if self.evidence_enabled else 5
        if len(self.slots) >= maximum:
            return await interaction.response.send_message(
                f"No free field slot. This form supports {maximum} text fields with the current evidence setting.",
                ephemeral=True,
            )
        # Open the modal immediately. Database work happens only after modal submission.
        await interaction.response.send_modal(FormSlotModal(self.panel_id))

    @discord.ui.button(label="Edit Selected", style=discord.ButtonStyle.primary, emoji="✏️", row=1)
    async def edit_selected(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_slot_id:
            return await interaction.response.send_message("Select a field first.", ephemeral=True)
        slot = self.slots.get(self.selected_slot_id)
        if not slot:
            return await interaction.response.send_message("That field is no longer available. Reopen the manager.", ephemeral=True)
        # Use the cached slot so Discord receives the modal within its 3-second limit.
        await interaction.response.send_modal(FormSlotModal(self.panel_id, slot))

    @discord.ui.button(label="Delete Selected", style=discord.ButtonStyle.danger, emoji="🗑️", row=1)
    async def delete_selected(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_slot_id:
            return await interaction.response.send_message("Select a field first.", ephemeral=True)
        slot = self.slots.get(self.selected_slot_id)
        if not slot:
            return await interaction.response.send_message("That field is no longer available. Reopen the manager.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await db.delete_form_slot(self.panel_id, self.selected_slot_id)
        self.slots.pop(self.selected_slot_id, None)
        await interaction.followup.send(
            f"Deleted **{slot['label']}** from panel **#{self.panel_id}**. Reopen `/report edit-form` to refresh the list.",
            ephemeral=True,
        )


class FormEditorView(discord.ui.View):
    def __init__(self, panel, owner_id: int):
        super().__init__(timeout=600)
        self.panel = panel
        self.panel_id = int(panel["id"])
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the administrator who opened this editor can use it.", ephemeral=True)
            return False
        return True

    async def get_panel(self, interaction: discord.Interaction):
        panel = await db.panel(interaction.guild_id, self.panel_id)
        if not panel:
            await interaction.response.send_message("Panel no longer exists.", ephemeral=True)
        return panel

    @discord.ui.button(label="Form Title", style=discord.ButtonStyle.primary, emoji="📝")
    async def title_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # A modal must be the interaction's first response. Use the panel snapshot
        # loaded when /report edit-form was opened instead of waiting on SQLite.
        await interaction.response.send_modal(FormLabelsModal(self.panel))

    @discord.ui.button(label="Manage Fields", style=discord.ButtonStyle.primary, emoji="🧩")
    async def fields_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        panel = await db.panel(interaction.guild_id, self.panel_id)
        if not panel:
            return await interaction.followup.send("Panel no longer exists.", ephemeral=True)
        self.panel = panel
        slots = await db.form_slots(panel)
        lines = [
            f"`#{slot['id']}` **{slot['label']}** — {slot['field_type']} • "
            f"{'Required' if slot['required'] else 'Optional'} • tracker `{slot['role']}`"
            for slot in slots
        ]
        embed = discord.Embed(
            title=f"Form Fields • Panel #{self.panel_id}",
            description=("\n".join(lines) if lines else "No text fields configured."),
            color=panel["color"],
        )
        embed.set_footer(text="Select a field, then edit or delete it. Deleting affects future submissions only.")
        await interaction.followup.send(
            embed=embed,
            view=FormSlotManagerView(panel, interaction.user.id, slots),
            ephemeral=True,
        )

    @discord.ui.button(label="Evidence", style=discord.ButtonStyle.success, emoji="📎")
    async def evidence_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(FormEvidenceModal(self.panel))


class PanelCustomizeModal(discord.ui.Modal):
    def __init__(self, bot: "ReportBot", *, mode: str, panel_name: str = "", channel: Optional[discord.TextChannel] = None, submission_channel: Optional[discord.TextChannel] = None, notification_channel: Optional[discord.TextChannel] = None, panel=None, claim_enabled: bool = True, delete_enabled: bool = True, deny_enabled: bool = True, form_preset: str = "discord"):
        super().__init__(title="Create Report Panel" if mode == "create" else f"Edit Panel #{panel['id']}", timeout=600)
        self.bot = bot
        self.mode = mode
        self.panel_name = panel_name
        self.channel = channel
        self.submission_channel = submission_channel
        self.notification_channel = notification_channel
        self.panel = panel
        self.claim_enabled = claim_enabled
        self.delete_enabled = delete_enabled
        self.deny_enabled = deny_enabled
        self.form_preset = form_preset
        default_panel_title = "Game Report Center" if form_preset == "game" else "Discord Report Center"
        default_description = (
            "Press File Game Report to report an in-game Roblox violation with supporting evidence."
            if form_preset == "game"
            else "Press File Report to submit a private report with image or video evidence."
        )
        default_button = "File Game Report" if form_preset == "game" else "File Report"
        self.title_input = discord.ui.TextInput(label="Panel title", default=(panel["title"] if panel else default_panel_title), max_length=256)
        self.description_input = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, default=(panel["description"] if panel else default_description), max_length=2000)
        self.button_input = discord.ui.TextInput(label="Button text", default=(panel["button_text"] if panel else default_button), max_length=80)
        self.footer_input = discord.ui.TextInput(label="Footer", default=(panel["footer"] if panel else "Reports are visible only to authorized staff."), required=False, max_length=2048)
        self.color_input = discord.ui.TextInput(label="Hex color", default=(f"#{panel['color']:06X}" if panel else "#DD2E44"), max_length=7)
        for item in (self.title_input, self.description_input, self.button_input, self.footer_input, self.color_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            color = parse_color(str(self.color_input.value))
        except ValueError as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)
        title = str(self.title_input.value)
        description = str(self.description_input.value)
        button_text = str(self.button_input.value)
        footer = str(self.footer_input.value or "")
        embed = discord.Embed(title=title, description=description, color=color)
        if footer:
            embed.set_footer(text=footer)

        if self.mode == "create":
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                panel_id = await db.create_panel(interaction.guild_id, self.panel_name, self.channel.id, self.submission_channel.id, title, description, button_text, footer, color, interaction.user.id, self.claim_enabled, self.delete_enabled, self.deny_enabled)
            except aiosqlite.IntegrityError:
                return await interaction.followup.send("A panel with that name already exists. Choose another name.", ephemeral=True)
            await db.update_panel(
                interaction.guild_id, panel_id,
                report_type=self.form_preset,
                notification_channel_id=(self.notification_channel.id if self.notification_channel else None),
            )
            if self.form_preset == "game":
                await db.update_panel(
                    interaction.guild_id,
                    panel_id,
                    form_title="File a Game Report",
                    username_label="Roblox Username",
                    username_description="Enter the reported player's Roblox username.",
                    username_placeholder="Example: RobloxUsername",
                    username_required=1,
                    rules_label="Rules Broken",
                    rules_description="List the game rule or rules that were violated.",
                    rules_placeholder="Example: Exploiting, spawn killing, harassment",
                    rules_required=1,
                    context_label="Context",
                    context_description="Explain what happened in the game.",
                    context_placeholder="Include what happened, when it happened, and relevant server details.",
                    context_required=1,
                    evidence_label="Evidence",
                    evidence_description="Upload screenshots or video evidence.",
                    evidence_required=0,
                    evidence_max=10,
                    evidence_enabled=1,
                )
                await db.replace_form_slots(panel_id, [
                    ("Roblox Username", "Enter the reported player's Roblox username.", "Example: RobloxUsername", "short", True, "username"),
                    ("Rules Broken", "List the game rule or rules that were violated.", "Example: Exploiting, spawn killing, harassment", "short", True, "rules"),
                    ("Context", "Explain what happened in the game.", "Include what happened, when it happened, and relevant server details.", "paragraph", True, "context"),
                ])
            message = await self.channel.send(embed=embed, view=ReportPanelView(self.bot, button_text))
            await db.set_panel_message(panel_id, self.channel.id, message.id)
            await interaction.followup.send(
                f"Panel **#{panel_id} — {self.panel_name}** was posted in {self.channel.mention}. "
                f"Submissions will go to {self.submission_channel.mention}. "
                f"Claim: **{'ON' if self.claim_enabled else 'OFF'}** • Deny: **{'ON' if self.deny_enabled else 'OFF'}** • Delete: **{'ON' if self.delete_enabled else 'OFF'}**.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        panel_id = self.panel["id"]
        await db.update_panel(interaction.guild_id, panel_id, title=title, description=description, button_text=button_text, footer=footer, color=color)
        channel = interaction.guild.get_channel(self.panel["channel_id"])
        updated_message = False
        if isinstance(channel, discord.TextChannel) and self.panel["message_id"]:
            try:
                message = await channel.fetch_message(self.panel["message_id"])
                await message.edit(embed=embed, view=ReportPanelView(self.bot, button_text))
                updated_message = True
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        suffix = " The existing Discord message was updated." if updated_message else " The saved design was updated, but the old message could not be found."
        await interaction.followup.send(f"Panel **#{panel_id}** updated.{suffix}", ephemeral=True)


class ReportBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default(); intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await db.init()
        self.add_view(ReportPanelView(self))
        self.add_view(TicketControlsView(self))
        if DEV_GUILD_ID:
            guild = discord.Object(id=DEV_GUILD_ID)
            # Remove stale guild command definitions first, then publish the exact
            # signatures registered by this running version of the bot.
            self.tree.clear_commands(guild=guild)
            await self.tree.sync(guild=guild)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"Clean-synced {len(synced)} guild commands to {DEV_GUILD_ID}.")
        else:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} global commands.")

bot = ReportBot()
setup = app_commands.Group(name="setup", description="Configure bot log channels")
report = app_commands.Group(name="report", description="Submit and manage reports")
tracker = app_commands.Group(name="tracker", description="View moderation and report totals")
track = app_commands.Group(name="track", description="View a person's complete moderation history")
archive = app_commands.Group(name="archive", description="Compile and browse reports in Discord threads")


@setup.command(name="logs", description="Create or connect all private log channels")
@app_commands.checks.has_permissions(administrator=True)
async def setup_logs(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    guild = interaction.guild
    category = discord.utils.get(guild.categories, name="MODERATION LOGS") or await guild.create_category("MODERATION LOGS", reason="Report Tracker setup")
    overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False), guild.me: discord.PermissionOverwrite(view_channel=True,send_messages=True,attach_files=True,embed_links=True,read_message_history=True)}
    mapping = {}
    names = {"warn": "warn-logs"}
    for key,name in names.items():
        channel = discord.utils.get(guild.text_channels, name=name) or await guild.create_text_channel(name, category=category, overwrites=overwrites, reason="Report Tracker setup")
        mapping[f"{key}_channel"] = channel.id
    ticket_category = discord.utils.get(guild.categories, name="REPORT TICKETS")
    if not ticket_category:
        ticket_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True, send_messages=True),
        }
        for role in guild.roles:
            if not role.is_default() and not role.is_bot_managed() and (role.permissions.administrator or role.permissions.moderate_members):
                ticket_overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        ticket_category = await guild.create_category("REPORT TICKETS", overwrites=ticket_overwrites, reason="Report ticket setup")
    mapping["ticket_category"] = ticket_category.id
    await db.update_settings(guild.id, **mapping)
    await interaction.followup.send("The warning log and private report ticket system are configured.", ephemeral=True)


@setup.command(name="report-alert", description="Choose separate repeat-report alert channels")
@app_commands.describe(
    report_type="Choose whether this channel receives Discord-report or Game-report alerts",
    channel="Private staff channel that receives the selected alert type",
)
@app_commands.choices(report_type=[
    app_commands.Choice(name="Discord Report Alert", value="discord"),
    app_commands.Choice(name="Game Report Alert", value="game"),
])
@app_commands.checks.has_permissions(administrator=True)
async def setup_report_alert(
    interaction: discord.Interaction, report_type: app_commands.Choice[str], channel: discord.TextChannel
):
    column = "game_report_alert_channel" if report_type.value == "game" else "discord_report_alert_channel"
    await db.update_settings(interaction.guild_id, **{column: channel.id})
    label = "Game" if report_type.value == "game" else "Discord"
    await interaction.response.send_message(
        f"**{label} repeat-report alerts** will be sent only to {channel.mention}. "
        f"They trigger when the same reported username reaches exactly **2 {label.lower()} reports**.",
        ephemeral=True,
    )



@setup.command(name="report-alert-toggle", description="Turn Discord/Game repeat-report alerts on or off")
@app_commands.describe(
    report_type="Which alert system to change",
    enabled="True to enable alerts, False to disable them",
)
@app_commands.choices(report_type=[
    app_commands.Choice(name="Discord Report Alerts", value="discord"),
    app_commands.Choice(name="Game Report Alerts", value="game"),
    app_commands.Choice(name="Both Discord + Game", value="all"),
])
@app_commands.checks.has_permissions(administrator=True)
async def setup_report_alert_toggle(
    interaction: discord.Interaction, report_type: app_commands.Choice[str], enabled: bool
):
    updates = {}
    if report_type.value in ("discord", "all"):
        updates["discord_report_alert_enabled"] = int(enabled)
    if report_type.value in ("game", "all"):
        updates["game_report_alert_enabled"] = int(enabled)
    await db.update_settings(interaction.guild_id, **updates)
    label = {"discord":"Discord report", "game":"Game report", "all":"Discord + Game report"}[report_type.value]
    await interaction.response.send_message(
        f"**{label} alerts** are now **{'ON' if enabled else 'OFF'}**. "
        "Your configured alert channels were kept and can be re-enabled anytime.",
        ephemeral=True,
    )


@setup.command(name="add-staff-role", description="Allow a role to claim and delete report tickets")
@app_commands.describe(role="Staff role to authorize for tickets")
@app_commands.checks.has_permissions(administrator=True)
async def setup_add_staff_role(interaction: discord.Interaction, role: discord.Role):
    if role.is_default() or role.is_bot_managed():
        return await interaction.response.send_message("Choose a normal server role, not @everyone or a bot-managed role.", ephemeral=True)
    await db.add_staff_role(interaction.guild_id, role.id, interaction.user.id)
    permission_updates = 0
    for panel in await db.panels(interaction.guild_id):
        channel = interaction.guild.get_channel(panel["submission_channel_id"] or 0)
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.set_permissions(role, view_channel=True, send_messages=True, read_message_history=True)
                permission_updates += 1
            except discord.Forbidden:
                pass
    await interaction.response.send_message(
        f"{role.mention} can now claim and delete tickets for panels that do not have their own staff-role list. "
        f"Channel access was updated for **{permission_updates}** submission channel(s).",
        ephemeral=True,
    )


@setup.command(name="remove-staff-role", description="Remove a role from the global ticket staff list")
@app_commands.describe(role="Staff role to remove")
@app_commands.checks.has_permissions(administrator=True)
async def setup_remove_staff_role(interaction: discord.Interaction, role: discord.Role):
    removed = await db.remove_staff_role(interaction.guild_id, role.id)
    await interaction.response.send_message(
        f"{role.mention} was removed from the global ticket staff list." if removed else f"{role.mention} was not in the global ticket staff list.",
        ephemeral=True,
    )


@setup.command(name="staff-roles", description="List globally authorized ticket staff roles")
@app_commands.checks.has_permissions(administrator=True)
async def setup_staff_roles(interaction: discord.Interaction):
    role_ids = await db.staff_role_ids(interaction.guild_id)
    if not role_ids:
        return await interaction.response.send_message(
            "No global staff roles are configured. Until you add one, members with Moderate Members or Manage Channels can use ticket controls.",
            ephemeral=True,
        )
    lines = []
    for role_id in role_ids:
        role = interaction.guild.get_role(role_id)
        lines.append(role.mention if role else f"Deleted role (`{role_id}`)")
    await interaction.response.send_message("**Global ticket staff roles**\n" + "\n".join(lines), ephemeral=True)


@report.command(name="add-panel-staff-role", description="Authorize a role for one report panel only")
@app_commands.describe(panel_id="Panel ID from /report panels", role="Role allowed to claim and delete this panel's submissions")
@app_commands.checks.has_permissions(administrator=True)
async def report_add_panel_staff_role(interaction: discord.Interaction, panel_id: int, role: discord.Role):
    if role.is_default() or role.is_bot_managed():
        return await interaction.response.send_message("Choose a normal server role, not @everyone or a bot-managed role.", ephemeral=True)
    added = await db.add_panel_staff_role(interaction.guild_id, panel_id, role.id, interaction.user.id)
    if not added:
        return await interaction.response.send_message("Panel not found.", ephemeral=True)
    panel = await db.panel(interaction.guild_id, panel_id)
    access_note = ""
    channel = interaction.guild.get_channel(panel["submission_channel_id"] or 0)
    if isinstance(channel, discord.TextChannel):
        try:
            await channel.set_permissions(role, view_channel=True, send_messages=True, read_message_history=True)
            access_note = f" Access to {channel.mention} was also granted."
        except discord.Forbidden:
            access_note = " I could not change the submission-channel permissions; grant this role View Channel manually."
    await interaction.response.send_message(
        f"{role.mention} is now authorized for panel **#{panel_id}**. Panel-specific roles override the global staff-role list.{access_note}",
        ephemeral=True,
    )


@report.command(name="remove-panel-staff-role", description="Remove an authorized role from one panel")
@app_commands.describe(panel_id="Panel ID", role="Role to remove from this panel")
@app_commands.checks.has_permissions(administrator=True)
async def report_remove_panel_staff_role(interaction: discord.Interaction, panel_id: int, role: discord.Role):
    panel = await db.panel(interaction.guild_id, panel_id)
    if not panel:
        return await interaction.response.send_message("Panel not found.", ephemeral=True)
    removed = await db.remove_panel_staff_role(interaction.guild_id, panel_id, role.id)
    await interaction.response.send_message(
        f"{role.mention} was removed from panel **#{panel_id}**." if removed else f"{role.mention} was not assigned to panel **#{panel_id}**.",
        ephemeral=True,
    )


@report.command(name="panel-staff-roles", description="List roles authorized for one report panel")
@app_commands.describe(panel_id="Panel ID")
@app_commands.checks.has_permissions(administrator=True)
async def report_panel_staff_roles(interaction: discord.Interaction, panel_id: int):
    panel = await db.panel(interaction.guild_id, panel_id)
    if not panel:
        return await interaction.response.send_message("Panel not found.", ephemeral=True)
    role_ids = await db.panel_staff_role_ids(interaction.guild_id, panel_id)
    if not role_ids:
        return await interaction.response.send_message(
            f"Panel **#{panel_id}** has no panel-specific staff roles, so it uses the global list from `/setup staff-roles`.",
            ephemeral=True,
        )
    lines = []
    for role_id in role_ids:
        role = interaction.guild.get_role(role_id)
        lines.append(role.mention if role else f"Deleted role (`{role_id}`)")
    await interaction.response.send_message(
        f"**Staff roles for panel #{panel_id} — {panel['name']}**\n" + "\n".join(lines),
        ephemeral=True,
    )


ALLOWED_EVIDENCE_PREFIXES = ("image/", "video/")


def collect_evidence(*attachments: Optional[discord.Attachment]) -> list[discord.Attachment]:
    return [attachment for attachment in attachments if attachment is not None]


async def validate_and_prepare_evidence(attachments: list[discord.Attachment]) -> list[discord.File]:
    files: list[discord.File] = []
    for attachment in attachments:
        content_type = attachment.content_type or ""
        if not content_type.startswith(ALLOWED_EVIDENCE_PREFIXES):
            raise ValueError(f"{attachment.filename} is not an image or video file.")
        try:
            data = await attachment.read()
        except discord.HTTPException as exc:
            raise ValueError(f"Could not download {attachment.filename}. Try uploading it again.") from exc
        files.append(discord.File(io.BytesIO(data), filename=attachment.filename))
    return files


async def create_name_only_case(
    interaction,
    kind: str,
    target: str,
    reason: str,
    duration: int | None = None,
    attachments: Optional[list[discord.Attachment]] = None,
):
    """Record a Roblox/name-only moderation case without touching Discord accounts."""
    attachments = attachments or []
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        files = await validate_and_prepare_evidence(attachments)
    except ValueError as exc:
        return await interaction.followup.send(str(exc), ephemeral=True)

    display, target_id = clean_target(target)
    case_id = await db.add_case(
        guild_id=interaction.guild_id,
        type=kind,
        target_name=display,
        target_id=target_id,
        moderator_id=interaction.user.id,
        reason=reason,
        duration_minutes=duration,
        action_performed=False,
    )
    channel = await log_channel(interaction, kind)
    stored_count = 0
    if channel:
        embed = mod_embed(case_id, kind, display, interaction.user, reason, False, duration)
        if attachments:
            embed.add_field(name="Evidence", value=f"{len(attachments)} image/video file(s) attached below.", inline=False)
        message = await channel.send(embed=embed, files=files)
        for original, uploaded in zip(attachments, message.attachments):
            await db.add_case_evidence(case_id, uploaded.filename, original.content_type, uploaded.size, uploaded.url)
            stored_count += 1
    else:
        for attachment in attachments:
            await db.add_case_evidence(case_id, attachment.filename, attachment.content_type, attachment.size, attachment.url)
            stored_count += 1

    suffix = f" Evidence files saved: **{stored_count}**." if stored_count else ""
    await interaction.followup.send(
        f"{kind.title()} case **#{case_id}** recorded for **{display}**. No Discord action was performed.{suffix}",
        ephemeral=True,
    )


@bot.tree.command(name="warn", description="Record a Roblox/name-only warning log")
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(
    interaction: discord.Interaction,
    target: str,
    reason: str,
    evidence: Optional[discord.Attachment] = None,
    evidence_2: Optional[discord.Attachment] = None,
    evidence_3: Optional[discord.Attachment] = None,
):
    await create_name_only_case(interaction, "warn", target, reason, attachments=collect_evidence(evidence, evidence_2, evidence_3))


@report.command(name="create-panel", description="Create a Discord or Roblox game report panel")
@app_commands.describe(
    name="A unique name used to manage this panel",
    panel_channel="Channel where the report panel will be posted",
    submission_channel="Private staff channel where submitted reports will be sent",
    notification_channel="Optional channel for Claim and Deny notifications",
    preset="Choose the starting form: Discord Report or Game Report",
    claim_button="Show the Claim Ticket button on submissions",
    deny_button="Show the Deny Report button on submissions",
    delete_button="Show the Delete Ticket button on submissions",
)
@app_commands.choices(preset=[
    app_commands.Choice(name="Discord Report", value="discord"),
    app_commands.Choice(name="Game Report", value="game"),
])
@app_commands.checks.has_permissions(administrator=True)
async def report_create_panel(
    interaction: discord.Interaction,
    name: str,
    panel_channel: discord.TextChannel,
    submission_channel: discord.TextChannel,
    notification_channel: Optional[discord.TextChannel] = None,
    preset: Optional[app_commands.Choice[str]] = None,
    claim_button: bool = True,
    deny_button: bool = True,
    delete_button: bool = True,
):
    clean_name = name.strip()[:60]
    if not clean_name:
        return await interaction.response.send_message("Enter a panel name.", ephemeral=True)
    form_preset = preset.value if preset else "discord"
    await interaction.response.send_modal(PanelCustomizeModal(
        bot,
        mode="create",
        panel_name=clean_name,
        channel=panel_channel,
        submission_channel=submission_channel,
        notification_channel=notification_channel,
        claim_enabled=claim_button,
        delete_enabled=delete_button,
        deny_enabled=deny_button,
        form_preset=form_preset,
    ))

@report.command(name="edit-panel", description="Customize one of your existing report panels")
@app_commands.checks.has_permissions(administrator=True)
async def report_edit_panel(interaction: discord.Interaction, panel_id: int):
    panel = await db.panel(interaction.guild_id, panel_id)
    if not panel:
        return await interaction.response.send_message("Panel not found. Run `/report panels` to see panel IDs.", ephemeral=True)
    await interaction.response.send_modal(PanelCustomizeModal(bot, mode="edit", panel=panel))

@report.command(name="move-panel", description="Move a saved report panel to another channel")
@app_commands.checks.has_permissions(administrator=True)
async def report_move_panel(interaction: discord.Interaction, panel_id: int, channel: discord.TextChannel):
    panel = await db.panel(interaction.guild_id, panel_id)
    if not panel:
        return await interaction.response.send_message("Panel not found.", ephemeral=True)
    embed = discord.Embed(title=panel["title"], description=panel["description"], color=panel["color"])
    if panel["footer"]:
        embed.set_footer(text=panel["footer"])
    message = await channel.send(embed=embed, view=ReportPanelView(bot, panel["button_text"]))
    old_channel = interaction.guild.get_channel(panel["channel_id"])
    if isinstance(old_channel, discord.TextChannel) and panel["message_id"]:
        try:
            old_message = await old_channel.fetch_message(panel["message_id"])
            await old_message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
    await db.set_panel_message(panel_id, channel.id, message.id)
    await interaction.response.send_message(f"Panel **#{panel_id}** moved to {channel.mention}.", ephemeral=True)

@report.command(name="set-submission-channel", description="Set a panel's submission and staff-notification channels")
@app_commands.describe(
    panel_id="Panel ID from /report panels",
    channel="Staff channel that receives full report submissions",
    notification_channel="Optional separate channel for Claim and Deny notifications",
)
@app_commands.checks.has_permissions(administrator=True)
async def report_set_submission_channel(
    interaction: discord.Interaction, panel_id: int, channel: discord.TextChannel,
    notification_channel: Optional[discord.TextChannel] = None,
):
    panel = await db.panel(interaction.guild_id, panel_id)
    if not panel:
        return await interaction.response.send_message("Panel not found.", ephemeral=True)
    await db.update_panel(
        interaction.guild_id, panel_id, submission_channel_id=channel.id,
        notification_channel_id=(notification_channel.id if notification_channel else None),
    )
    notification_text = notification_channel.mention if notification_channel else "Disabled"
    await interaction.response.send_message(
        f"Panel **#{panel_id}** submissions: {channel.mention}\nClaim/Deny notifications: **{notification_text}**",
        ephemeral=True,
    )

@report.command(name="form-slots", description="List all removable form slots for one panel")
@app_commands.checks.has_permissions(administrator=True)
async def report_form_slots(interaction: discord.Interaction, panel_id: int):
    panel=await db.panel(interaction.guild_id,panel_id)
    if not panel: return await interaction.response.send_message("Panel not found.", ephemeral=True)
    slots=await db.form_slots(panel)
    evidence_on=bool(panel["evidence_enabled"])
    lines=[f"`#{r['id']}` • **{r['label']}** • {r['field_type']} • {'Required' if r['required'] else 'Optional'} • role `{r['role']}`" for r in slots]
    embed=discord.Embed(title=f"Form Slots • Panel #{panel_id}", description="\n".join(lines) or "No text slots configured.", color=panel["color"])
    embed.set_footer(text=f"Evidence slot: {'ON' if evidence_on else 'OFF'} • Used rows: {len(slots)+(1 if evidence_on else 0)}/5")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@report.command(name="add-form-slot", description="Add a new field directly to one panel form")
@app_commands.describe(
    panel_id="Panel ID from /report panels", label="Field label",
    field_type="short or paragraph", required="Whether the field is required",
    tracker_role="custom, username, discord_id, rules, context, or action",
    description="Optional help text", placeholder="Optional example text inside the field",
)
@app_commands.checks.has_permissions(administrator=True)
async def report_add_form_slot(
    interaction: discord.Interaction, panel_id: int, label: str,
    field_type: str = "short", required: bool = True, tracker_role: str = "custom",
    description: str = "", placeholder: str = "",
):
    panel = await db.panel(interaction.guild_id, panel_id)
    if not panel:
        return await interaction.response.send_message("Panel not found. Run `/report panels` first.", ephemeral=True)
    slots = await db.form_slots(panel)
    maximum = 4 if panel["evidence_enabled"] else 5
    if len(slots) >= maximum:
        return await interaction.response.send_message(f"No free slot. Maximum is {maximum} text fields with the current evidence setting.", ephemeral=True)
    field_type = field_type.strip().lower()
    tracker_role = tracker_role.strip().lower()
    if field_type not in {"short", "paragraph"}:
        return await interaction.response.send_message("field_type must be `short` or `paragraph`.", ephemeral=True)
    if tracker_role not in {"custom", "username", "discord_id", "rules", "context", "action"}:
        return await interaction.response.send_message("Invalid tracker_role. Use custom, username, discord_id, rules, context, or action.", ephemeral=True)
    label = label.strip()[:45]
    if not label:
        return await interaction.response.send_message("The field label cannot be empty.", ephemeral=True)
    slot_id = await db.add_form_slot(panel_id, label, description.strip()[:100], placeholder.strip()[:100], field_type, required, tracker_role)
    await interaction.response.send_message(f"Added **{label}** as slot **#{slot_id}** to panel **#{panel_id}**. The existing panel message was retained.", ephemeral=True)

@report.command(name="edit-form-slot", description="Edit one existing form slot")
@app_commands.checks.has_permissions(administrator=True)
async def report_edit_form_slot(interaction: discord.Interaction, panel_id: int, slot_id: int):
    panel=await db.panel(interaction.guild_id,panel_id)
    if not panel: return await interaction.response.send_message("Panel not found.", ephemeral=True)
    await db.form_slots(panel)
    slot=await db.form_slot(panel_id,slot_id)
    if not slot: return await interaction.response.send_message("Slot not found. Use `/report form-slots`.", ephemeral=True)
    await interaction.response.send_modal(FormSlotModal(panel_id,slot))

@report.command(name="remove-form-slot", description="Remove a field from one panel's report form")
@app_commands.checks.has_permissions(administrator=True)
async def report_remove_form_slot(interaction: discord.Interaction, panel_id: int, slot_id: int):
    panel=await db.panel(interaction.guild_id,panel_id)
    if not panel: return await interaction.response.send_message("Panel not found.", ephemeral=True)
    await db.form_slots(panel)
    slot=await db.form_slot(panel_id,slot_id)
    if not slot: return await interaction.response.send_message("Slot not found.", ephemeral=True)
    await db.delete_form_slot(panel_id,slot_id)
    await interaction.response.send_message(f"Removed **{slot['label']}** from panel **#{panel_id}**.", ephemeral=True)

@report.command(name="toggle-evidence-slot", description="Add or remove the evidence upload slot")
@app_commands.checks.has_permissions(administrator=True)
async def report_toggle_evidence_slot(interaction: discord.Interaction, panel_id: int, enabled: bool):
    panel=await db.panel(interaction.guild_id,panel_id)
    if not panel: return await interaction.response.send_message("Panel not found.", ephemeral=True)
    slots=await db.form_slots(panel)
    if enabled and len(slots)>4:
        return await interaction.response.send_message("Remove one text slot first. Evidence uses one of Discord's five form rows.", ephemeral=True)
    await db.update_panel(interaction.guild_id,panel_id,evidence_enabled=int(enabled))
    await interaction.response.send_message(f"Evidence slot is now **{'ON' if enabled else 'OFF'}** for panel **#{panel_id}**.", ephemeral=True)

@report.command(name="edit-form", description="Open the visual form editor for one report panel")
@app_commands.describe(panel_id="Panel ID shown by /report panels")
@app_commands.checks.has_permissions(administrator=True)
async def report_edit_form(interaction: discord.Interaction, panel_id: int):
    panel = await db.panel(interaction.guild_id, panel_id)
    if not panel:
        return await interaction.response.send_message("Panel not found. Run `/report panels` first.", ephemeral=True)
    embed = discord.Embed(
        title=f"Form Editor • Panel #{panel_id}",
        description=(
            f"Editing **{panel['name']}**. Use the buttons below to edit the exact form opened by this panel.\n\n"
            "• **Form Title** — changes the title at the top of the form\n"
            "• **Manage Fields** — add, select, edit, or delete any field, including Roblox/Discord ID\n"
            "• **Evidence** — upload label, help text, required setting, and limit\n\n"
            "Every field is now an independent removable slot. Deleted fields disappear from future submissions only."
        ),
        color=panel["color"],
    )
    embed.set_footer(text="Discord's yellow security/privacy warning is controlled by Discord and cannot be customized by bots.")
    await interaction.response.send_message(embed=embed, view=FormEditorView(panel, interaction.user.id), ephemeral=True)


@report.command(name="set-form-title", description="Set a panel form title directly")
@app_commands.describe(panel_id="Panel ID from /report panels", title="New title shown at the top of the form")
@app_commands.checks.has_permissions(administrator=True)
async def report_set_form_title(interaction: discord.Interaction, panel_id: int, title: str):
    panel = await db.panel(interaction.guild_id, panel_id)
    if not panel:
        return await interaction.response.send_message("Panel not found. Run `/report panels` first.", ephemeral=True)
    title = title.strip()[:45]
    if not title:
        return await interaction.response.send_message("The title cannot be empty.", ephemeral=True)
    await db.update_panel(interaction.guild_id, panel_id, form_title=title)
    await interaction.response.send_message(f"Panel **#{panel_id}** form title is now **{title}**. The existing panel message was retained.", ephemeral=True)

@report.command(name="edit-form-labels", description="Customize the report form title for one panel")
@app_commands.checks.has_permissions(administrator=True)
async def report_edit_form_labels(interaction: discord.Interaction, panel_id: int):
    panel = await db.panel(interaction.guild_id, panel_id)
    if not panel:
        return await interaction.response.send_message("Panel not found. Run `/report panels` first.", ephemeral=True)
    await interaction.response.send_modal(FormLabelsModal(panel))


@report.command(name="edit-form-placeholders", description="Customize the help text shown inside one panel's form")
@app_commands.checks.has_permissions(administrator=True)
async def report_edit_form_placeholders(interaction: discord.Interaction, panel_id: int):
    panel = await db.panel(interaction.guild_id, panel_id)
    if not panel:
        return await interaction.response.send_message("Panel not found. Run `/report panels` first.", ephemeral=True)
    await interaction.response.send_modal(FormPlaceholdersModal(panel))


@report.command(name="set-form-options", description="Set required fields and evidence limits for one panel")
@app_commands.describe(
    panel_id="Panel ID from /report panels",
    username_required="Require the username field",
    discord_id_required="Require the Discord ID field",
    rules_required="Require the rules field",
    context_required="Require the context field",
    evidence_required="Require at least one image or video",
    evidence_max="Maximum evidence files from 1 to 10",
)
@app_commands.checks.has_permissions(administrator=True)
async def report_set_form_options(
    interaction: discord.Interaction,
    panel_id: int,
    username_required: bool = True,
    discord_id_required: bool = True,
    rules_required: bool = True,
    context_required: bool = True,
    evidence_required: bool = False,
    evidence_max: app_commands.Range[int, 1, 10] = 10,
):
    panel = await db.panel(interaction.guild_id, panel_id)
    if not panel:
        return await interaction.response.send_message("Panel not found. Run `/report panels` first.", ephemeral=True)
    await db.update_panel(
        interaction.guild_id, panel_id,
        username_required=int(username_required), discord_id_required=int(discord_id_required),
        rules_required=int(rules_required), context_required=int(context_required),
        evidence_required=int(evidence_required), evidence_max=int(evidence_max),
    )
    await interaction.response.send_message(
        f"Form options for panel **#{panel_id}** updated. Evidence: "
        f"**{'Required' if evidence_required else 'Optional'}**, maximum **{evidence_max}** file(s).",
        ephemeral=True,
    )


@report.command(name="set-evidence-label", description="Customize the evidence field label for one panel")
@app_commands.checks.has_permissions(administrator=True)
async def report_set_evidence_label(interaction: discord.Interaction, panel_id: int, label: str):
    panel = await db.panel(interaction.guild_id, panel_id)
    if not panel:
        return await interaction.response.send_message("Panel not found. Run `/report panels` first.", ephemeral=True)
    clean = label.strip()[:45]
    if not clean:
        return await interaction.response.send_message("Evidence label cannot be empty.", ephemeral=True)
    await db.update_panel(interaction.guild_id, panel_id, evidence_label=clean)
    await interaction.response.send_message(f"Evidence label for panel **#{panel_id}** updated.", ephemeral=True)


@report.command(name="set-ticket-controls", description="Turn Claim, Deny, and Delete buttons on or off for one panel")
@app_commands.describe(
    panel_id="Panel ID from /report panels",
    claim_button="Show or remove the Claim Ticket button",
    deny_button="Show or remove the Deny Report button",
    delete_button="Show or remove the Delete Ticket button",
    update_existing="Also update existing active submissions from this panel",
)
@app_commands.checks.has_permissions(administrator=True)
async def report_set_ticket_controls(
    interaction: discord.Interaction,
    panel_id: int,
    claim_button: bool,
    deny_button: bool,
    delete_button: bool,
    update_existing: bool = True,
):
    panel = await db.panel(interaction.guild_id, panel_id)
    if not panel:
        return await interaction.response.send_message("Panel not found.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    await db.update_panel(
        interaction.guild_id, panel_id,
        claim_enabled=int(claim_button), deny_enabled=int(deny_button), delete_enabled=int(delete_button),
    )
    updated = 0
    failed = 0
    if update_existing:
        rows = await db.reports_by_panel(interaction.guild_id, panel_id)
        for row in rows:
            channel = interaction.guild.get_channel(row["log_channel"])
            if not isinstance(channel, discord.TextChannel):
                failed += 1
                continue
            try:
                message = await channel.fetch_message(row["log_message"])
                controls = TicketControlsView(
                    bot, claimed=bool(row["assigned_to"]),
                    claim_enabled=claim_button, delete_enabled=delete_button, deny_enabled=deny_button,
                )
                embed = message.embeds[0] if message.embeds else None
                if embed:
                    instructions = []
                    if claim_button:
                        instructions.append("Claim Ticket assigns the report to one staff member.")
                    if deny_button:
                        instructions.append("Deny Report marks it Rejected with a staff reason.")
                    if delete_button:
                        instructions.append("Delete Ticket removes the submission message but preserves tracking data.")
                    embed.set_footer(text=" ".join(instructions) if instructions else "This report panel has no ticket controls enabled.")
                await message.edit(embed=embed, view=controls if controls.children else None)
                updated += 1
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                failed += 1
    extra = f" Existing submissions updated: **{updated}**"
    if failed:
        extra += f"; unavailable: **{failed}**"
    await interaction.followup.send(
        f"Panel **#{panel_id}** controls updated. Claim: **{'ON' if claim_button else 'OFF'}** • "
        f"Deny: **{'ON' if deny_button else 'OFF'}** • Delete: **{'ON' if delete_button else 'OFF'}**.{extra if update_existing else ''}",
        ephemeral=True,
    )

@report.command(name="panels", description="List all saved report panels")
@app_commands.checks.has_permissions(administrator=True)
async def report_panels(interaction: discord.Interaction):
    rows = await db.panels(interaction.guild_id)
    if not rows:
        return await interaction.response.send_message("No panels exist yet. Use `/report create-panel`.", ephemeral=True)
    lines = []
    for row in rows[:25]:
        channel = interaction.guild.get_channel(row["channel_id"])
        location = channel.mention if isinstance(channel, discord.TextChannel) else f"Deleted channel `{row['channel_id']}`"
        destination = interaction.guild.get_channel(row["submission_channel_id"]) if row["submission_channel_id"] else None
        destination_text = destination.mention if isinstance(destination, discord.TextChannel) else "Not configured"
        claim_text = "ON" if row["claim_enabled"] else "OFF"
        deny_text = "ON" if row["deny_enabled"] else "OFF"
        delete_text = "ON" if row["delete_enabled"] else "OFF"
        notification = interaction.guild.get_channel(row["notification_channel_id"]) if row["notification_channel_id"] else None
        notification_text = notification.mention if isinstance(notification, discord.TextChannel) else "Disabled"
        report_type_text = str(row["report_type"] or "discord").title()
        lines.append(
            f"**#{row['id']} — {row['name']}**\nPanel: {location} • Submissions: {destination_text}\n"
            f"Action notifications: {notification_text} • Type: **{report_type_text}**\n"
            f"Claim: **{claim_text}** • Deny: **{deny_text}** • Delete: **{delete_text}**\nForm: **{row['form_title']}**"
        )
    embed = discord.Embed(title="Saved Report Panels", description="\n".join(lines), color=0x5865F2)
    embed.set_footer(text="Use the panel ID with /report edit-panel or /report edit-form.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@report.command(name="delete-panel", description="Delete a report panel and its saved configuration")
@app_commands.checks.has_permissions(administrator=True)
async def report_delete_panel(interaction: discord.Interaction, panel_id: int):
    panel = await db.panel(interaction.guild_id, panel_id)
    if not panel:
        return await interaction.response.send_message("Panel not found.", ephemeral=True)
    channel = interaction.guild.get_channel(panel["channel_id"])
    if isinstance(channel, discord.TextChannel) and panel["message_id"]:
        try:
            message = await channel.fetch_message(panel["message_id"])
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
    await db.delete_panel(interaction.guild_id, panel_id)
    await interaction.response.send_message(f"Panel **#{panel_id} — {panel['name']}** deleted.", ephemeral=True)

@report.command(name="panel", description="Post a report panel and choose its submission destination")
@app_commands.describe(
    panel_channel="Channel where the panel will be posted",
    submission_channel="Private staff channel where reports will be sent",
)
@app_commands.checks.has_permissions(administrator=True)
async def report_panel(
    interaction: discord.Interaction,
    panel_channel: discord.TextChannel,
    submission_channel: discord.TextChannel,
    claim_button: bool = True,
    deny_button: bool = True,
    delete_button: bool = True,
):
    name = f"Panel {discord.utils.utcnow().strftime('%Y%m%d-%H%M%S')}"
    await interaction.response.send_modal(
        PanelCustomizeModal(
            bot, mode="create", panel_name=name, channel=panel_channel,
            submission_channel=submission_channel, claim_enabled=claim_button,
            delete_enabled=delete_button, deny_enabled=deny_button,
        )
    )

@report.command(name="set-action", description="Record the moderation action taken for a submitted report")
@app_commands.choices(action=[
    app_commands.Choice(name="Warn", value="warn"),
    app_commands.Choice(name="Kick", value="kick"),
    app_commands.Choice(name="Timeout", value="timeout"),
    app_commands.Choice(name="Ban", value="ban"),
    app_commands.Choice(name="None / Clear", value="none"),
])
@app_commands.checks.has_permissions(moderate_members=True)
async def report_set_action(interaction: discord.Interaction, report_id: int, action: app_commands.Choice[str]):
    ok = await db.set_report_action(interaction.guild_id, report_id, action.value)
    if ok:
        await archive_event(interaction.guild, report_id, f"🛡️ Moderation action recorded: **{action.value.title()}** by {interaction.user.mention}.")
        row = await db.report(interaction.guild_id, report_id)
        if row and row["player_id"]:
            await refresh_player_thread(interaction.guild, int(row["player_id"]))
    await interaction.response.send_message(
        f"Report **#{report_id}** action set to **{action.name}**." if ok else "Report not found.",
        ephemeral=True,
    )

@report.command(name="view", description="View a tracked report")
@app_commands.checks.has_permissions(moderate_members=True)
async def report_view(interaction: discord.Interaction, report_id: int):
    row=await db.report(interaction.guild_id,report_id)
    if not row: return await interaction.response.send_message("Report not found.",ephemeral=True)
    e=discord.Embed(title=f"Report #{row['id']}",color=0x3498DB); e.add_field(name="Discord Username",value=row["target_name"],inline=False); e.add_field(name="Discord ID",value=row["discord_id"] or "Not supplied",inline=False); e.add_field(name="Rules Broken",value=row["category"],inline=False); e.add_field(name="Context",value=row["details"][:1024],inline=False); e.add_field(name="Status",value=row["status"]); e.add_field(name="Reporter ID",value=str(row["reporter_id"])); e.add_field(name="Staff note",value=row["staff_note"] or "None",inline=False); await interaction.response.send_message(embed=e,ephemeral=True)

@report.command(name="update", description="Update a tracked report")
@app_commands.choices(status=STATUS_CHOICES)
@app_commands.checks.has_permissions(moderate_members=True)
async def report_update(interaction: discord.Interaction, report_id: int, status: app_commands.Choice[str], note: str=""):
    ok=await db.update_report(interaction.guild_id,report_id,status.value,interaction.user.id,note)
    if ok:
        await archive_event(interaction.guild, report_id, f"📝 Status updated to **{status.value}** by {interaction.user.mention}." + (f"\n**Note:** {note}" if note else ""))
    await interaction.response.send_message(f"Report #{report_id} updated to **{status.value}**." if ok else "Report not found.",ephemeral=True)

async def send_player_tracker(interaction: discord.Interaction, username: str):
    case_rows = await db.counts(interaction.guild_id, username)
    report_rows = await db.player_report_counts(interaction.guild_id, username)
    case_totals = {row["type"]: row["total"] for row in case_rows}
    report_totals = {row["status"]: row["total"] for row in report_rows}
    total_reports = sum(report_totals.values())
    embed = discord.Embed(title=f"Discord User Tracker • {username}", color=0x5865F2)
    embed.add_field(name="Warns", value=str(case_totals.get("warn", 0)))
    embed.add_field(name="Kicks", value=str(case_totals.get("kick", 0)))
    embed.add_field(name="Timeouts", value=str(case_totals.get("timeout", 0)))
    embed.add_field(name="Bans", value=str(case_totals.get("ban", 0)))
    embed.add_field(name="Total Reports", value=str(total_reports))
    embed.add_field(name="Open Reports", value=str(report_totals.get("Open", 0)))
    embed.add_field(name="In Review", value=str(report_totals.get("In Review", 0)))
    embed.add_field(name="Resolved", value=str(report_totals.get("Resolved", 0)))
    embed.add_field(name="Rejected", value=str(report_totals.get("Rejected", 0)))
    embed.add_field(name="Deleted Tickets", value=str(report_totals.get("Deleted", 0)))
    embed.set_footer(text="Usernames are matched without case sensitivity. Historical moderation records remain visible.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tracker.command(name="player", description="Track a Discord user's reports and warning history")
@app_commands.checks.has_permissions(moderate_members=True)
async def tracker_player(interaction: discord.Interaction, username: str):
    await send_player_tracker(interaction, username)


@tracker.command(name="user", description="Alias for the player tracker")
@app_commands.checks.has_permissions(moderate_members=True)
async def tracker_user(interaction: discord.Interaction, username: str):
    await send_player_tracker(interaction, username)

@tracker.command(name="summary", description="Show server-wide moderation and report totals")
@app_commands.checks.has_permissions(moderate_members=True)
async def tracker_summary(interaction: discord.Interaction):
    cases=await db.counts(interaction.guild_id); reports=await db.report_counts(interaction.guild_id); e=discord.Embed(title="Moderation Tracker Summary",color=0x5865F2)
    for row in cases: e.add_field(name=row['type'].title(),value=str(row['total']))
    for row in reports: e.add_field(name=f"Reports: {row['status']}",value=str(row['total']))
    await interaction.response.send_message(embed=e,ephemeral=True)



@track.command(name="person", description="Show a player's report actions, rules, and evidence")
@app_commands.describe(username="Tracked username, known alias, or Discord ID")
@app_commands.checks.has_permissions(moderate_members=True)
async def track_person(interaction: discord.Interaction, username: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    profile, reports, evidence_map = await db.person_history(interaction.guild_id, username)
    case_totals = await db.case_totals_for_identity(interaction.guild_id, username)
    report_actions = {"warn":0, "kick":0, "timeout":0, "ban":0}
    for row in reports:
        action = (row["action_taken"] or "").lower()
        if action in report_actions:
            report_actions[action] += 1
    totals = {k: case_totals.get(k, 0) + report_actions[k] for k in report_actions}
    display_name = profile["canonical_name"] if profile else username
    embed = discord.Embed(title=f"Person Tracker • {display_name}", color=0x5865F2)
    embed.add_field(name="Username", value=display_name, inline=False)
    if profile and profile["discord_id"]:
        embed.add_field(name="Discord ID", value=profile["discord_id"], inline=False)
    embed.add_field(name="Warn Count", value=str(totals["warn"]))
    embed.add_field(name="Kick Count", value=str(totals["kick"]))
    embed.add_field(name="Ban Count", value=str(totals["ban"]))
    embed.add_field(name="Timeout Count", value=str(totals["timeout"]))
    embed.add_field(name="Total Reports", value=str(len(reports)))
    rules=[]
    seen=set()
    for row in reports:
        rule=(row["category"] or "Not supplied").strip()
        key=rule.casefold()
        if key not in seen:
            seen.add(key); rules.append(rule)
    embed.add_field(
        name="Rules Broken",
        value=("\n".join(f"• {r}" for r in rules[:10]) or "None recorded")[:1024],
        inline=False,
    )
    history = []
    for row in reports[:10]:
        action = (row["action_taken"] or "Pending / not assigned").replace("_", " ").title()
        ev = evidence_map.get(row["id"], [])
        ev_links = " ".join(
            f"[Evidence {i + 1}]({item['url']})" for i, item in enumerate(ev[:3])
        ) or "No evidence"
        jump = ""
        if row["log_channel"] and row["log_message"]:
            jump = (
                f"https://discord.com/channels/{interaction.guild_id}/"
                f"{row['log_channel']}/{row['log_message']}"
            )
        report_link = f"[Report #{row['id']}]({jump})" if jump else f"Report #{row['id']}"
        source_channel = interaction.guild.get_channel(row["log_channel"]) if row["log_channel"] else None
        if isinstance(source_channel, discord.TextChannel):
            category_name = source_channel.category.name if source_channel.category else "No category"
            source_text = f"{source_channel.mention} • {category_name}"
        else:
            source_text = "Saved database record (source channel unavailable)"
        history.append(
            f"**{report_link} — {action}**\n"
            f"Rule: {(row['category'] or 'Not supplied')[:180]}\n"
            f"Source: {source_text}\n"
            f"{ev_links}"
        )
    embed.add_field(
        name="Recent Reports & Evidence",
        value=("\n\n".join(history) or "No matching reports found.")[:1024],
        inline=False,
    )
    embed.set_footer(text="Database-first tracker: every submission is linked to one player profile regardless of panel, channel, category, deleted message, or renamed channel. Use /report set-action after staff decides the action.")
    await interaction.followup.send(embed=embed, ephemeral=True)

@track.command(name="add-alias", description="Link another username spelling to an existing tracked player")
@app_commands.describe(existing="Existing tracked username or Discord ID", alias="Another spelling/name that belongs to the same player")
@app_commands.checks.has_permissions(administrator=True)
async def track_add_alias(interaction: discord.Interaction, existing: str, alias: str):
    ok = await db.add_player_alias(interaction.guild_id, existing, alias)
    await interaction.response.send_message(
        f"Linked **{alias}** to **{existing}**." if ok else "Existing player was not found. Submit at least one report for that player first.",
        ephemeral=True,
    )

@archive.command(name="setup", description="Create one searchable report-history thread per player")
@app_commands.describe(channel="Staff channel that will contain player report-history threads", auto_archive="Automatically add every future report to the player thread")
@app_commands.checks.has_permissions(administrator=True)
async def archive_setup(interaction: discord.Interaction, channel: discord.TextChannel, auto_archive: bool = True):
    await db.update_settings(
        interaction.guild_id,
        report_archive_channel=channel.id,
        report_threads_enabled=int(auto_archive),
    )
    await interaction.response.send_message(
        f"Report archive channel set to {channel.mention}.\nAutomatic thread archiving: **{'ON' if auto_archive else 'OFF'}**.\n"
        "Use `/archive compile-existing` to compile older reports into the same player threads.",
        ephemeral=True,
    )


@archive.command(name="compile-existing", description="Compile older saved reports into archive threads")
@app_commands.describe(limit="Maximum reports to compile in this batch (1-100)")
@app_commands.checks.has_permissions(administrator=True)
async def archive_compile_existing(interaction: discord.Interaction, limit: app_commands.Range[int, 1, 100] = 50):
    await interaction.response.defer(ephemeral=True, thinking=True)
    settings = await db.settings(interaction.guild_id)
    channel = interaction.guild.get_channel(settings["report_archive_channel"] or 0)
    if not isinstance(channel, discord.TextChannel):
        return await interaction.followup.send("Run `/archive setup` first and choose a valid archive channel.", ephemeral=True)
    ids = await db.unarchived_reports(interaction.guild_id, limit)
    if not ids:
        # A saved thread ID is not proof that the Discord thread still exists or is in the current archive channel.
        # Validate player threads and repair stale mappings automatically.
        candidates = await db.all_report_ids(interaction.guild_id, limit)
        stale_found = False
        for report_id in candidates:
            report, _, _ = await db.archive_report_data(interaction.guild_id, report_id)
            if not report or not report["player_id"]:
                continue
            thread = await _get_player_thread(interaction.guild, int(report["player_id"]))
            if thread is None or int(getattr(thread, "parent_id", 0) or 0) != int(channel.id):
                stale_found = True
                break
        if not stale_found:
            return await interaction.followup.send(
                f"All saved reports are compiled. Player threads are in {channel.mention}. Use `/archive two-warnings` to locate players with 2+ warnings.",
                ephemeral=True,
            )
        await db.reset_archive_links(interaction.guild_id)
        ids = await db.unarchived_reports(interaction.guild_id, limit)
    created = 0
    failed = 0
    for report_id in ids:
        try:
            thread = await archive_report_to_thread(interaction.guild, report_id, force=True)
            if thread:
                created += 1
            else:
                failed += 1
        except (discord.Forbidden, discord.HTTPException):
            failed += 1
        await asyncio.sleep(0.25)
    total, archived = await db.archive_counts(interaction.guild_id)
    remaining = max(0, total - archived)
    await interaction.followup.send(
        f"Compiled **{created}** report(s) into threads. Failed: **{failed}**. Remaining: **{remaining}**.\n"
        + ("Run `/archive compile-existing` again to continue." if remaining else "Archive is up to date."),
        ephemeral=True,
    )


@archive.command(name="rebuild", description="Rebuild player threads when old archive mappings are stale")
@app_commands.describe(limit="Maximum reports to rebuild in this batch (1-100)")
@app_commands.checks.has_permissions(administrator=True)
async def archive_rebuild(interaction: discord.Interaction, limit: app_commands.Range[int, 1, 100] = 100):
    await interaction.response.defer(ephemeral=True, thinking=True)
    settings = await db.settings(interaction.guild_id)
    channel = interaction.guild.get_channel(settings["report_archive_channel"] or 0)
    if not isinstance(channel, discord.TextChannel):
        return await interaction.followup.send("Run `/archive setup` first and choose the channel where player threads should live.", ephemeral=True)
    await db.reset_archive_links(interaction.guild_id)
    ids = await db.unarchived_reports(interaction.guild_id, limit)
    compiled = failed = 0
    for report_id in ids:
        try:
            if await archive_report_to_thread(interaction.guild, report_id, force=True):
                compiled += 1
            else:
                failed += 1
        except (discord.Forbidden, discord.HTTPException):
            failed += 1
        await asyncio.sleep(0.25)
    total, archived = await db.archive_counts(interaction.guild_id)
    await interaction.followup.send(
        f"Rebuilt **{compiled}** report(s) into player threads in {channel.mention}. Failed: **{failed}**. "
        f"Archived: **{archived}/{total}**. Run `/archive rebuild` again if more remain.",
        ephemeral=True,
    )


@archive.command(name="status", description="Show report thread archive status")
@app_commands.checks.has_permissions(moderate_members=True)
async def archive_status(interaction: discord.Interaction):
    settings = await db.settings(interaction.guild_id)
    channel = interaction.guild.get_channel(settings["report_archive_channel"] or 0)
    total, archived = await db.archive_counts(interaction.guild_id)
    await interaction.response.send_message(
        f"**Archive channel:** {channel.mention if isinstance(channel, discord.TextChannel) else 'Not configured'}\n"
        f"**Automatic future threads:** {'ON' if settings['report_threads_enabled'] else 'OFF'}\n"
        f"**Archived reports:** {archived}/{total}\n"
        f"**Remaining:** {max(0, total-archived)}",
        ephemeral=True,
    )


@archive.command(name="two-warnings", description="Quickly list player threads with two or more warnings")
@app_commands.checks.has_permissions(moderate_members=True)
async def archive_two_warnings(interaction: discord.Interaction):
    rows = await db.players_with_two_warns(interaction.guild_id)
    if not rows:
        return await interaction.response.send_message("No tracked player currently has two or more report warnings.", ephemeral=True)
    lines = []
    for row in rows[:25]:
        thread = interaction.guild.get_thread(int(row["archive_thread_id"])) if row["archive_thread_id"] else None
        if thread is None and row["archive_thread_id"]:
            try:
                fetched = await interaction.guild.fetch_channel(int(row["archive_thread_id"]))
                thread = fetched if isinstance(fetched, discord.Thread) else None
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                thread = None
        link = thread.mention if isinstance(thread, discord.Thread) else "Thread not compiled yet"
        lines.append(f"⚠️ **{row['canonical_name']}** — **{int(row['warns'] or 0)} warns** • {int(row['total'] or 0)} reports • {link}")
    embed = discord.Embed(
        title="⚠️ Players With 2+ Warnings",
        description="\n".join(lines),
        color=0xE74C3C,
    )
    embed.set_footer(text="Use /archive compile-existing if an older player's thread has not been compiled yet.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="help", description="Show explanations and examples for every bot command")
async def help_command(interaction: discord.Interaction):
    intro = discord.Embed(
        title="Report Ticket Bot — Command Help",
        description=(
            "Use the sections below to learn what every command does. Commands marked **Administrator** "
            "are for panel and server configuration. Commands marked **Moderator** require moderation permissions."
        ),
        color=0x5865F2,
    )
    intro.add_field(
        name="Quick start",
        value=(
            "1. Run `/setup logs`\n"
            "2. Run `/report create-panel`\n"
            "3. Choose the panel channel and submission channel\n"
            "4. Customize the panel and its form slots"
        ),
        inline=False,
    )

    setup_embed = discord.Embed(title="Setup & Warning Commands", color=0xFEE75C)
    setup_embed.description = (
        "**`/setup logs`** — **Administrator**\n"
        "Creates or connects the private warning log and report-ticket channels.\n\n"
        "**`/setup report-alert report_type channel`** — **Administrator**\n"
        "Chooses the staff channel that receives an alert when one player reaches exactly two warnings.\n\n"
        "**`/setup add-staff-role role`** — **Administrator**\n"
        "Allows a role to claim and delete tickets globally.\n\n"
        "**`/setup remove-staff-role role`** — **Administrator**\n"
        "Removes a role from the global ticket staff list.\n\n"
        "**`/setup staff-roles`** — **Administrator**\n"
        "Lists all globally authorized ticket roles.\n\n"
        "**`/warn target reason`** — **Moderator**\n"
        "Records a name-only warning. The target may be a Discord username, Roblox username, display name, ID, or mention. "
        "It does not punish a Discord account."
    )

    panel_embed = discord.Embed(title="Panel Management", color=0x57F287)
    panel_embed.description = (
        "**`/report create-panel`** — Creates a separately customized panel and lets you choose both its display channel and submission destination.\n\n"
        "**`/report panel`** — Quickly posts a new report panel with selected panel and submission channels.\n\n"
        "**`/report panels`** — Lists every saved panel, its panel ID, display channel, and submission channel.\n\n"
        "**`/report edit-panel panel_id`** — Changes a panel's title, description, button text, footer, and color.\n\n"
        "**`/report move-panel panel_id channel`** — Moves one panel message to another channel without changing its submission destination.\n\n"
        "**`/report set-submission-channel panel_id channel notification_channel`** — Sets where full forms are delivered and where Claim/Deny notifications are posted.\n\n"
        "**`/report set-ticket-controls`** — Turns Claim and Delete buttons on or off for one panel; it can also update active submissions.\n\n"
        "**`/report add-panel-staff-role panel_id role`** — Adds an authorized role for one panel only.\n\n"
        "**`/report remove-panel-staff-role panel_id role`** — Removes a panel-specific role.\n\n"
        "**`/report panel-staff-roles panel_id`** — Lists roles authorized for that panel.\n\n"
        "**`/report delete-panel panel_id`** — Deletes the selected panel message and its saved panel configuration. Existing reports remain tracked."
    )
    panel_embed.set_footer(text="All commands in this section require Administrator permission.")

    form_embed = discord.Embed(title="Form Customization & Slots", color=0xEB459E)
    form_embed.description = (
        "**`/report edit-form panel_id`** — Opens the visual form editor for the selected panel.\n\n"
        "**`/report form-slots panel_id`** — Lists the form's current slots and slot IDs.\n\n"
        "**`/report add-form-slot panel_id`** — Adds a short-text or paragraph field.\n\n"
        "**`/report edit-form-slot panel_id slot_id`** — Edits a field's label, description, placeholder, type, required status, and tracker role.\n\n"
        "**`/report remove-form-slot panel_id slot_id`** — Removes one field from future forms.\n\n"
        "**`/report toggle-evidence-slot panel_id enabled`** — Adds or removes the image/video upload slot.\n\n"
        "**`/report edit-form-labels panel_id`** — Changes the form title and standard field labels.\n\n"
        "**`/report edit-form-placeholders panel_id`** — Changes the example text displayed inside fields.\n\n"
        "**`/report set-form-options`** — Sets required/optional fields and the evidence upload limit.\n\n"
        "**`/report set-evidence-label panel_id label`** — Changes the evidence upload field label.\n\n"
        "Discord supports a maximum of **5 modal rows**. Evidence uses one row, leaving up to four text fields."
    )
    form_embed.set_footer(text="All commands in this section require Administrator permission.")

    tracking_embed = discord.Embed(title="Reports & Player Tracking", color=0xED4245)
    tracking_embed.description = (
        "**`/report view report_id`** — **Moderator**\n"
        "Displays one tracked report, including its status, target, reporter, details, and evidence.\n\n"
        "**`/report set-action report_id action`** — Records whether the submitted report resulted in Warn, Kick, Timeout, or Ban.\n\n"
        "**`/report update report_id status note`** — **Moderator**\n"
        "Changes a report to Open, In Review, Resolved, or Rejected and optionally saves a staff note.\n\n"
        "**`/track person username`** — Shows rules broken, Warn/Kick/Ban/Timeout counts, and evidence links.\n\n"
        "**`/tracker player username`** — **Moderator**\n"
        "Shows the named player's warning total and report history. Names are matched without case sensitivity.\n\n"
        "**`/tracker user username`** — **Moderator**\n"
        "Alias of `/tracker player`.\n\n"
        "**`/tracker summary`** — **Moderator**\n"
        "Shows server-wide totals for warnings and report statuses.\n\n"
        "**`/archive setup channel auto_archive`** — **Administrator**\n"
        "Chooses the staff archive channel and automatically creates one Discord thread per future report.\n\n"
        "**`/archive compile-existing limit`** — **Administrator**\n"
        "Compiles older database reports into archive threads in batches of up to 100.\n\n"
        "**`/archive status`** — **Moderator**\n"
        "Shows how many reports have already been archived into threads."
    )
    tracking_embed.set_footer(text="Tip: use the same spelling for player names to keep tracker records together.")

    await interaction.response.send_message(embed=intro, ephemeral=True)
    for help_embed in (setup_embed, panel_embed, form_embed, tracking_embed):
        await interaction.followup.send(embed=help_embed, ephemeral=True)

bot.tree.add_command(setup); bot.tree.add_command(report); bot.tree.add_command(tracker)
bot.tree.add_command(track)
bot.tree.add_command(archive)

@bot.tree.error
async def tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    message = "You do not have permission to use that command." if isinstance(error, app_commands.MissingPermissions) else f"Command failed: {error}"
    if interaction.response.is_done(): await interaction.followup.send(message,ephemeral=True)
    else: await interaction.response.send_message(message,ephemeral=True)

@bot.event
async def on_ready(): print(f"Logged in as {bot.user} ({bot.user.id})")

if __name__ == "__main__":
    if not TOKEN: raise RuntimeError("DISCORD_TOKEN is missing from .env")
    bot.run(TOKEN)
