# Discord Multi-Panel Routed Report Bot

This version allows multiple File Report panels in the same Discord channel. Every panel has its own submission destination.

## Example layout

All of these panel messages may be posted in `#file-a-report`:

- General Reports -> submissions go to `#general-report-logs`
- Staff Abuse Reports -> submissions go to `#staff-abuse-logs`
- Ban Review Reports -> submissions go to `#ban-review-logs`

The bot identifies the exact panel message that the member clicked and routes the completed form to that panel's configured submission channel.

## Create the first panel

Run:

`/report create-panel`

Set:

- `name`: General Reports
- `panel_channel`: #file-a-report
- `submission_channel`: #general-report-logs

Complete the customization form.

## Create another panel in the same place

Run `/report create-panel` again:

- `name`: Staff Abuse Reports
- `panel_channel`: #file-a-report
- `submission_channel`: #staff-abuse-logs

Both panels appear in `#file-a-report`, but submissions are routed separately.

## Create a third panel in the same place

- `name`: Ban Review Reports
- `panel_channel`: #file-a-report
- `submission_channel`: #ban-review-logs

## Manage panels

- `/report panels` — list panel IDs, panel locations, and submission destinations
- `/report edit-panel panel_id:<ID>` — change the panel design
- `/report move-panel panel_id:<ID> channel:<CHANNEL>` — move only the panel message
- `/report set-submission-channel panel_id:<ID> channel:<CHANNEL>` — change only where that panel sends forms
- `/report delete-panel panel_id:<ID>` — remove one panel

Each panel needs a unique management name, but multiple panels may use the same panel channel.

## Form fields

- Discord Username
- Discord ID
- Rules Broken
- Context
- Evidence upload (images or videos)

## Startup

Copy your `.env` file into this folder, then run:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python bot.py
```

With an existing environment:

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python bot.py
```

After startup, use `/report create-panel` once for each panel you need.

## Per-panel Claim and Delete controls

Each report panel can independently show or hide its ticket buttons.

When creating a panel:

```text
/report create-panel
```

Choose:

- `claim_button: True/False`
- `delete_button: True/False`

To change an existing panel:

```text
/report set-ticket-controls panel_id:1 claim_button:False delete_button:False update_existing:True
```

`update_existing:True` also removes or restores the buttons on active submissions created from that panel.

## Repeat warning alert

Choose the private staff channel where duplicate-warning alerts should be sent:

```text
/setup warn-alert channel:#staff-alerts
```

When the same player name reaches exactly **2 warning records**, the bot posts a **Repeat Warning Alert** showing the player name, latest case number, total warnings, and moderator. Names are matched without case sensitivity.

## Individual form customization per panel

Every saved panel now has its own report-form configuration. Changing one panel does not change any other panel.

### Customize the form title and field labels

```text
/report edit-form-labels panel_id:1
```

The modal lets you edit:

- Form title
- Username field label
- Discord ID field label
- Rules field label
- Context field label

### Customize placeholders and help text

```text
/report edit-form-placeholders panel_id:1
```

This changes the text shown inside the username, Discord ID, rules, and context fields, plus the evidence upload description.

### Customize required fields and evidence limit

```text
/report set-form-options
```

Options:

- `panel_id`
- `username_required`
- `discord_id_required`
- `rules_required`
- `context_required`
- `evidence_required`
- `evidence_max` from 1 to 10

Example: make evidence required for panel 2 and allow up to five files:

```text
/report set-form-options panel_id:2 evidence_required:True evidence_max:5
```

### Rename the evidence field

```text
/report set-evidence-label panel_id:2 label:Upload Staff Abuse Evidence
```

### Example independent setup

Panel 1 can use:

- Form title: `General Player Report`
- Evidence optional
- Maximum 3 files

Panel 2 can use:

- Form title: `Staff Abuse Investigation`
- Discord ID required
- Evidence required
- Maximum 10 files

Both panels may be posted in the same channel while sending submissions to different staff channels.

Existing databases are upgraded automatically when the bot starts. Existing panel messages do not need to be reposted because the form configuration is loaded when the user clicks that specific panel button.

## Visual form editor

Administrators can edit the exact form opened by each panel with:

```text
/report edit-form panel_id:1
```

The private editor includes buttons for:

- **Title & Field Names** — changes the modal title and the Discord Username, Discord ID, Rules Broken, Context, and Evidence labels.
- **Descriptions** — changes the small help text displayed under each field label.
- **Placeholders** — changes the faded examples shown inside text boxes.
- **Evidence** — changes its label, help text, required/optional status, and maximum uploads from 1 to 10.

Use this command for required/optional text fields:

```text
/report set-form-options panel_id:1
```

Each panel stores a separate form configuration. Editing Panel #1 does not change Panel #2.

The yellow privacy/security warning at the top of Discord forms is generated by Discord and cannot be edited or removed by a bot.

## Dynamic form slots per panel

Each report panel can now use a different set of questions. Discord allows a maximum of five rows/components in one modal. The evidence uploader uses one row when enabled, leaving up to four text fields. If evidence is disabled, the form can contain up to five text fields.

### List slots

```text
/report form-slots panel_id:1
```

This shows every field's slot ID, label, type, required state, and role.

### Add a slot

```text
/report add-form-slot panel_id:1
```

The editor asks for:

- Field label
- Description
- Placeholder
- Type: `SHORT` or `PARAGRAPH`
- Required and role, such as `YES:custom`

Available roles are `custom`, `username`, `discord_id`, `rules`, and `context`. Roles connect a field to the report tracker. Ordinary extra questions should use `custom`.

### Edit a slot

```text
/report edit-form-slot panel_id:1 slot_id:5
```

### Remove a slot

```text
/report remove-form-slot panel_id:1 slot_id:5
```

Removing a field affects future submissions only. Old ticket records remain saved.

### Add or remove evidence upload

```text
/report toggle-evidence-slot panel_id:1 enabled:False
```

With evidence off, up to five text fields can be used. Turn it back on with `enabled:True`; the panel must then have no more than four text fields.

## Help command

Run:

```text
/help
```

The bot sends a private help guide explaining every available command, grouped into:

- Setup and warning commands
- Panel management
- Form customization and dynamic slots
- Reports and player tracking

The response is ephemeral, so only the person who ran `/help` can see it.
