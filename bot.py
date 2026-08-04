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
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
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
                "ALTER TABLE reports ADD COLUMN ticket_channel INTEGER",
                "ALTER TABLE reports ADD COLUMN deleted_at TEXT",
                "ALTER TABLE reports ADD COLUMN discord_id TEXT",
                "ALTER TABLE report_panels ADD COLUMN submission_channel_id INTEGER",
                "ALTER TABLE report_panels ADD COLUMN claim_enabled INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE report_panels ADD COLUMN delete_enabled INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE reports ADD COLUMN panel_id INTEGER",
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
            await db.commit()

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

    async def create_panel(self, guild_id: int, name: str, channel_id: int, submission_channel_id: int, title: str, description: str, button_text: str, footer: str, color: int, created_by: int, claim_enabled: bool = True, delete_enabled: bool = True) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "INSERT INTO report_panels(guild_id,name,channel_id,submission_channel_id,title,description,button_text,footer,color,created_by,claim_enabled,delete_enabled) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (guild_id, name, channel_id, submission_channel_id, title, description, button_text, footer, color, created_by, int(claim_enabled), int(delete_enabled)),
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

    async def count_reports_for_target(self, guild_id: int, target_name: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM reports WHERE guild_id=? AND LOWER(TRIM(target_name))=LOWER(TRIM(?))",
                (guild_id, target_name),
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
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "INSERT INTO reports(guild_id,reporter_id,target_name,discord_id,category,details,incident_context,panel_id) VALUES(?,?,?,?,?,?,?,?)",
                (guild_id, reporter_id, username, discord_id, rules, context, "", panel_id),
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


class ReportModal(discord.ui.Modal):
    def __init__(self, bot: "ReportBot", panel, slots):
        super().__init__(title=(panel["form_title"] or "File a Discord Report")[:45], timeout=600)
        self.bot = bot
        self.panel_id = panel["id"]
        self.submission_channel_id = panel["submission_channel_id"]
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
        role_values = {"username":"Not supplied", "discord_id":"Not supplied", "rules":"Not supplied", "context":"Not supplied"}
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
        submission_channel = interaction.guild.get_channel(self.submission_channel_id)
        if not isinstance(submission_channel, discord.TextChannel):
            return await interaction.followup.send("This panel's submission channel no longer exists.", ephemeral=True)
        files=[]
        for attachment in attachments:
            try:
                files.append(discord.File(io.BytesIO(await attachment.read()), filename=attachment.filename))
            except discord.HTTPException:
                pass
        embed = build_dynamic_ticket_embed(report_id, interaction.user, field_values, len(files), "Open", None, self.claim_enabled, self.delete_enabled)
        controls = TicketControlsView(self.bot, claim_enabled=self.claim_enabled, delete_enabled=self.delete_enabled)
        message = await submission_channel.send(content=f"New report submitted by {interaction.user.mention}.", embed=embed, files=files, view=controls if controls.children else None)
        for original, uploaded in zip(attachments, message.attachments):
            async with aiosqlite.connect(db.path) as conn:
                await conn.execute("INSERT INTO evidence(report_id,filename,content_type,size,url) VALUES(?,?,?,?,?)", (report_id,uploaded.filename,original.content_type,uploaded.size,uploaded.url))
                await conn.commit()
        await db.set_report_message(report_id, submission_channel.id, message.id)

        # Alert staff when the reported-player username reaches two submitted reports.
        reported_username = role_values["username"].strip()
        if reported_username and reported_username != "Not supplied":
            report_count = await db.count_reports_for_target(interaction.guild_id, reported_username)
            if report_count == 2:
                settings = await db.settings(interaction.guild_id)
                alert_channel_id = settings["warn_alert_channel"]
                alert_channel = interaction.guild.get_channel(alert_channel_id) if alert_channel_id else None
                if isinstance(alert_channel, discord.TextChannel):
                    alert = discord.Embed(
                        title="⚠️ Repeat Player Report Alert",
                        description=(
                            f"**{reported_username}** has now appeared as the reported player in **2 submitted reports**. "
                            "Staff should review both reports before deciding on further action."
                        ),
                        color=0xE74C3C,
                        timestamp=discord.utils.utcnow(),
                    )
                    alert.add_field(name="Latest report", value=f"[Report #{report_id}]({message.jump_url})", inline=True)
                    alert.add_field(name="Total reports", value=str(report_count), inline=True)
                    alert.add_field(name="Submitted by", value=interaction.user.mention, inline=True)
                    alert.add_field(name="Source panel", value=f"Panel #{self.panel_id}", inline=True)
                    alert.set_footer(text="Reported-player names are matched without case sensitivity and surrounding spaces.")
                    await alert_channel.send(embed=alert)

        await interaction.followup.send(f"Your report was submitted successfully. Tracking number: **#{report_id}**", ephemeral=True)


def build_dynamic_ticket_embed(report_id, reporter, field_values, evidence_count, status, assigned_to, claim_enabled=True, delete_enabled=True):
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
    if delete_enabled: instructions.append("Delete Ticket removes the message but preserves tracking data.")
    embed.set_footer(text=" ".join(instructions) if instructions else "This report has no claim or delete controls enabled.")
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
        instructions.append("Claim Ticket assigns the report to one staff member.")
    if delete_enabled:
        instructions.append("Delete Ticket removes the submission message but preserves tracking data.")
    embed.set_footer(text=" ".join(instructions) if instructions else "This report panel has no claim or delete controls enabled.")
    return embed


async def is_ticket_staff(member: discord.Member, panel_id: Optional[int] = None) -> bool:
    # Administrators always retain access so the server cannot be locked out.
    if member.guild_permissions.administrator:
        return True

    member_role_ids = {role.id for role in member.roles}

    # Panel-specific roles override the global staff-role list when configured.
    if panel_id:
        panel_roles = set(await db.panel_staff_role_ids(member.guild.id, panel_id))
        if panel_roles:
            return bool(member_role_ids & panel_roles)

    global_roles = set(await db.staff_role_ids(member.guild.id))
    if global_roles:
        return bool(member_role_ids & global_roles)

    # Backward-compatible fallback until an administrator configures staff roles.
    perms = member.guild_permissions
    return perms.moderate_members or perms.manage_channels


class TicketControlsView(discord.ui.View):
    def __init__(self, bot: "ReportBot", claimed: bool = False, claim_enabled: bool = True, delete_enabled: bool = True):
        super().__init__(timeout=None)
        self.bot = bot
        if not claim_enabled:
            self.remove_item(self.claim)
        elif claimed:
            self.claim.disabled = True
            self.claim.label = "Claimed"
        if not delete_enabled:
            self.remove_item(self.delete)

    @discord.ui.button(
        label="Claim Ticket", style=discord.ButtonStyle.success, emoji="✋",
        custom_id="report_ticket:claim:v1"
    )
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("This button can only be used inside a server.", ephemeral=True)
        row = await db.report_by_message(interaction.guild_id, interaction.channel_id, interaction.message.id)
        if not row:
            return await interaction.response.send_message("This channel is not connected to a tracked report.", ephemeral=True)
        if not await is_ticket_staff(interaction.user, row["panel_id"]):
            return await interaction.response.send_message("Only an administrator or a configured staff role can claim this ticket.", ephemeral=True)
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
        controls = TicketControlsView(self.bot, claimed=True, claim_enabled=claim_enabled, delete_enabled=delete_enabled)
        await interaction.response.edit_message(embed=embed, view=controls if controls.children else None)
        await interaction.followup.send(f"Ticket claimed by {interaction.user.mention}.")

    @discord.ui.button(
        label="Delete Ticket", style=discord.ButtonStyle.danger, emoji="🗑️",
        custom_id="report_ticket:delete:v1"
    )
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("This button can only be used inside a server.", ephemeral=True)
        row = await db.report_by_message(interaction.guild_id, interaction.channel_id, interaction.message.id)
        if not row:
            return await interaction.response.send_message("This channel is not connected to a tracked report.", ephemeral=True)
        if not await is_ticket_staff(interaction.user, row["panel_id"]):
            return await interaction.response.send_message("Only an administrator or a configured staff role can delete this ticket.", ephemeral=True)
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
        if len(parts)!=2 or parts[0] not in {"yes","no","y","n"} or parts[1] not in {"custom","username","discord_id","rules","context"}:
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
    def __init__(self, panel):
        super().__init__(title=f"Form Labels • Panel #{panel['id']}", timeout=600)
        self.panel_id = panel["id"]
        self.form_title = discord.ui.TextInput(label="Form title", default=panel["form_title"], max_length=45)
        self.username_label = discord.ui.TextInput(label="Username field label", default=panel["username_label"], max_length=45)
        self.discord_id_label = discord.ui.TextInput(label="Discord ID field label", default=panel["discord_id_label"], max_length=45)
        self.rules_label = discord.ui.TextInput(label="Rules field label", default=panel["rules_label"], max_length=45)
        self.context_label = discord.ui.TextInput(label="Context field label", default=panel["context_label"], max_length=45)
        for item in (self.form_title, self.username_label, self.discord_id_label, self.rules_label, self.context_label):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        await db.update_panel(
            interaction.guild_id, self.panel_id,
            form_title=str(self.form_title.value), username_label=str(self.username_label.value),
            discord_id_label=str(self.discord_id_label.value), rules_label=str(self.rules_label.value),
            context_label=str(self.context_label.value),
        )
        await interaction.response.send_message(
            f"Form labels for panel **#{self.panel_id}** were updated.", ephemeral=True
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


class FormEditorView(discord.ui.View):
    def __init__(self, panel_id: int, owner_id: int):
        super().__init__(timeout=600)
        self.panel_id = panel_id
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

    @discord.ui.button(label="Title & Field Names", style=discord.ButtonStyle.primary, emoji="✏️")
    async def labels_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel = await self.get_panel(interaction)
        if panel: await interaction.response.send_modal(FormLabelsModal(panel))

    @discord.ui.button(label="Descriptions", style=discord.ButtonStyle.secondary, emoji="📝")
    async def descriptions_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel = await self.get_panel(interaction)
        if panel: await interaction.response.send_modal(FormDescriptionsModal(panel))

    @discord.ui.button(label="Placeholders", style=discord.ButtonStyle.secondary, emoji="💬")
    async def placeholders_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel = await self.get_panel(interaction)
        if panel: await interaction.response.send_modal(FormPlaceholdersModal(panel))

    @discord.ui.button(label="Evidence", style=discord.ButtonStyle.success, emoji="📎")
    async def evidence_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        panel = await self.get_panel(interaction)
        if panel: await interaction.response.send_modal(FormEvidenceModal(panel))


class PanelCustomizeModal(discord.ui.Modal):
    def __init__(self, bot: "ReportBot", *, mode: str, panel_name: str = "", channel: Optional[discord.TextChannel] = None, submission_channel: Optional[discord.TextChannel] = None, panel=None, claim_enabled: bool = True, delete_enabled: bool = True):
        super().__init__(title="Create Report Panel" if mode == "create" else f"Edit Panel #{panel['id']}", timeout=600)
        self.bot = bot
        self.mode = mode
        self.panel_name = panel_name
        self.channel = channel
        self.submission_channel = submission_channel
        self.panel = panel
        self.claim_enabled = claim_enabled
        self.delete_enabled = delete_enabled
        self.title_input = discord.ui.TextInput(label="Panel title", default=(panel["title"] if panel else "Discord Report Center"), max_length=256)
        self.description_input = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, default=(panel["description"] if panel else "Press File Report to submit a private report with image or video evidence."), max_length=2000)
        self.button_input = discord.ui.TextInput(label="Button text", default=(panel["button_text"] if panel else "File Report"), max_length=80)
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
                panel_id = await db.create_panel(interaction.guild_id, self.panel_name, self.channel.id, self.submission_channel.id, title, description, button_text, footer, color, interaction.user.id, self.claim_enabled, self.delete_enabled)
            except aiosqlite.IntegrityError:
                return await interaction.followup.send("A panel with that name already exists. Choose another name.", ephemeral=True)
            message = await self.channel.send(embed=embed, view=ReportPanelView(self.bot, button_text))
            await db.set_panel_message(panel_id, self.channel.id, message.id)
            await interaction.followup.send(
                f"Panel **#{panel_id} — {self.panel_name}** was posted in {self.channel.mention}. "
                f"Submissions will go to {self.submission_channel.mention}. "
                f"Claim: **{'ON' if self.claim_enabled else 'OFF'}** • Delete: **{'ON' if self.delete_enabled else 'OFF'}**.",
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
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

bot = ReportBot()
setup = app_commands.Group(name="setup", description="Configure bot log channels")
report = app_commands.Group(name="report", description="Submit and manage reports")
tracker = app_commands.Group(name="tracker", description="View moderation and report totals")


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


@setup.command(name="report-alert", description="Choose where duplicate-player report alerts are sent")
@app_commands.describe(channel="Private staff channel that receives alerts when a reported username reaches two reports")
@app_commands.checks.has_permissions(administrator=True)
async def setup_report_alert(interaction: discord.Interaction, channel: discord.TextChannel):
    await db.update_settings(interaction.guild_id, warn_alert_channel=channel.id)
    await interaction.response.send_message(
        f"Repeat-report alerts will be sent to {channel.mention}. The bot counts the reported-player username from submitted forms and alerts when that name reaches exactly **2 reports**.",
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


@report.command(name="create-panel", description="Create and post a separately customized report panel")
@app_commands.describe(
    name="A unique name used to manage this panel",
    panel_channel="Channel where the File Report panel will be posted",
    submission_channel="Private staff channel where submitted reports will be sent",
)
@app_commands.checks.has_permissions(administrator=True)
async def report_create_panel(
    interaction: discord.Interaction,
    name: str,
    panel_channel: discord.TextChannel,
    submission_channel: discord.TextChannel,
    claim_button: bool = True,
    delete_button: bool = True,
):
    clean_name = name.strip()[:60]
    if not clean_name:
        return await interaction.response.send_message("Enter a panel name.", ephemeral=True)
    await interaction.response.send_modal(PanelCustomizeModal(
        bot, mode="create", panel_name=clean_name, channel=panel_channel,
        submission_channel=submission_channel, claim_enabled=claim_button, delete_enabled=delete_button,
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

@report.command(name="set-submission-channel", description="Choose where a panel sends submitted reports")
@app_commands.describe(panel_id="Panel ID from /report panels", channel="Staff channel that receives submissions")
@app_commands.checks.has_permissions(administrator=True)
async def report_set_submission_channel(interaction: discord.Interaction, panel_id: int, channel: discord.TextChannel):
    panel = await db.panel(interaction.guild_id, panel_id)
    if not panel:
        return await interaction.response.send_message("Panel not found.", ephemeral=True)
    await db.update_panel(interaction.guild_id, panel_id, submission_channel_id=channel.id)
    await interaction.response.send_message(
        f"Panel **#{panel_id}** will now send submissions to {channel.mention}.", ephemeral=True
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

@report.command(name="add-form-slot", description="Add another field to one panel's report form")
@app_commands.checks.has_permissions(administrator=True)
async def report_add_form_slot(interaction: discord.Interaction, panel_id: int):
    panel=await db.panel(interaction.guild_id,panel_id)
    if not panel: return await interaction.response.send_message("Panel not found.", ephemeral=True)
    slots=await db.form_slots(panel)
    maximum=4 if panel["evidence_enabled"] else 5
    if len(slots)>=maximum:
        return await interaction.response.send_message(f"No free slot. This form can have up to {maximum} text fields with the current evidence setting.", ephemeral=True)
    await interaction.response.send_modal(FormSlotModal(panel_id))

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
            "• **Title & Field Names** — modal title and labels\n"
            "• **Descriptions** — small help text under labels\n"
            "• **Placeholders** — faded examples inside text boxes\n"
            "• **Evidence** — upload label, help text, required setting, and limit\n\n"
            "Use `/report form-slots`, `/report add-form-slot`, `/report edit-form-slot`, and `/report remove-form-slot` to manage fields."
        ),
        color=panel["color"],
    )
    embed.set_footer(text="Discord's yellow security/privacy warning is controlled by Discord and cannot be customized by bots.")
    await interaction.response.send_message(embed=embed, view=FormEditorView(panel_id, interaction.user.id), ephemeral=True)


@report.command(name="edit-form-labels", description="Customize the form title and field labels for one panel")
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


@report.command(name="set-ticket-controls", description="Turn Claim and Delete buttons on or off for one panel")
@app_commands.describe(
    panel_id="Panel ID from /report panels",
    claim_button="Show or remove the Claim Ticket button",
    delete_button="Show or remove the Delete Ticket button",
    update_existing="Also update existing active submissions from this panel",
)
@app_commands.checks.has_permissions(administrator=True)
async def report_set_ticket_controls(
    interaction: discord.Interaction,
    panel_id: int,
    claim_button: bool,
    delete_button: bool,
    update_existing: bool = True,
):
    panel = await db.panel(interaction.guild_id, panel_id)
    if not panel:
        return await interaction.response.send_message("Panel not found.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    await db.update_panel(
        interaction.guild_id, panel_id,
        claim_enabled=int(claim_button), delete_enabled=int(delete_button),
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
                    claim_enabled=claim_button, delete_enabled=delete_button,
                )
                embed = message.embeds[0] if message.embeds else None
                if embed:
                    instructions = []
                    if claim_button:
                        instructions.append("Claim Ticket assigns the report to one staff member.")
                    if delete_button:
                        instructions.append("Delete Ticket removes the submission message but preserves tracking data.")
                    embed.set_footer(text=" ".join(instructions) if instructions else "This report panel has no claim or delete controls enabled.")
                await message.edit(embed=embed, view=controls if controls.children else None)
                updated += 1
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                failed += 1
    extra = f" Existing submissions updated: **{updated}**"
    if failed:
        extra += f"; unavailable: **{failed}**"
    await interaction.followup.send(
        f"Panel **#{panel_id}** controls updated. Claim: **{'ON' if claim_button else 'OFF'}** • "
        f"Delete: **{'ON' if delete_button else 'OFF'}**.{extra if update_existing else ''}",
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
        delete_text = "ON" if row["delete_enabled"] else "OFF"
        lines.append(
            f"**#{row['id']} — {row['name']}**\nPanel: {location} • Submissions: {destination_text}\n"
            f"Claim button: **{claim_text}** • Delete button: **{delete_text}**\nForm: **{row['form_title']}**"
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
    delete_button: bool = True,
):
    name = f"Panel {discord.utils.utcnow().strftime('%Y%m%d-%H%M%S')}"
    await interaction.response.send_modal(
        PanelCustomizeModal(
            bot, mode="create", panel_name=name, channel=panel_channel,
            submission_channel=submission_channel, claim_enabled=claim_button,
            delete_enabled=delete_button,
        )
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
        "**`/setup report-alert channel`** — **Administrator**\n"
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
        "**`/report set-submission-channel panel_id channel`** — Changes where forms from one panel are delivered.\n\n"
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
        "**`/report update report_id status note`** — **Moderator**\n"
        "Changes a report to Open, In Review, Resolved, or Rejected and optionally saves a staff note.\n\n"
        "**`/tracker player username`** — **Moderator**\n"
        "Shows the named player's warning total and report history. Names are matched without case sensitivity.\n\n"
        "**`/tracker user username`** — **Moderator**\n"
        "Alias of `/tracker player`.\n\n"
        "**`/tracker summary`** — **Moderator**\n"
        "Shows server-wide totals for warnings and report statuses."
    )
    tracking_embed.set_footer(text="Tip: use the same spelling for player names to keep tracker records together.")

    await interaction.response.send_message(embed=intro, ephemeral=True)
    for help_embed in (setup_embed, panel_embed, form_embed, tracking_embed):
        await interaction.followup.send(embed=help_embed, ephemeral=True)

bot.tree.add_command(setup); bot.tree.add_command(report); bot.tree.add_command(tracker)

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
