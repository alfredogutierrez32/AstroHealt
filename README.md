# AstroHealth · by cyberAhedos

NASA Space Apps Challenge 2026: *Create Health Monitoring Software for Astronauts on Space Missions*.

Health monitoring system for a crew of 5 astronauts. It has three screens:
- **Helmet** (visor HUD): vital signs over the astronaut's view.
- **Wristband** (blue HUD-style screen): each astronaut wears it on the wrist.
- **Ship console** (light screen): receives the data from every wristband and stores it in a database.

**Synthetic data (in SIMULATION mode) · Demo thresholds · Do not use for real medical decisions.**
AstroHealth is not clinically validated or certified as a medical device, and it does not claim compliance with any standard.

## How to run it in PyCharm
1. Put all the `.py` files and the videos (`fondo.mp4`, `video2.mp4` … `video5.mp4`) in the same folder (keep the `tests` folder next to them).
2. Use Python 3.9 or newer. The core works with the standard library only (tkinter, sqlite3 and csv).
3. Open `astrohealth.py` and press **Run**. The ship console, your wristband and your helmet HUD open.
4. In the console choose a scenario and press **▶ Play**.

## Indicators (8)
| Indicator | Unit | Demo range | Critical (red alert) |
|---|---|---|---|
| Heart rate | bpm | 50–100 | < 40 or > 130 |
| O₂ saturation | % | 95–100 | < 90 |
| Temperature | °C | 36.0–37.8 | < 35 or > 38.5 |
| Systolic pressure | mmHg | 90–130 | < 80 or > 160 |
| Glucose | mg/dL | 70–140 | < 54 or > 200 |
| **Sleep (last 24 h)** | h | 6.5–9 | < 4 |
| **Exercise** | min/day | 30–150 | < 10 |
| **Accumulated radiation** | mSv | up to 50 | > 80 |

- **Radiation only goes up**: each scenario changes how fast it accumulates; it never drops. If someone reaches critical they stay there for the rest of the session; to reset it, close and reopen the program.
- **Mood check-in (1 to 5):** crew-member data, not a clinical indicator. The **reported** mood (the one the crew member logs in the "Mood check-in" routine) is stored separately from the engine's **simulated** mood; the screen and `readings.reported_mood` only show the reported one.
- The ranges are **illustrative**, for the demo; they are not official medical values.

## Outdoor wristband (`wristband.py`)
Designed to be used outside the ship and with gloves: only the essentials, large text, high contrast and touch areas of at least 44 px.
- **Fixed (do not scroll):** the header (name, link with the ship, ◀ ⌂ ME ▶), the tabs, the discreet notification at the bottom and the **emergency** notice, which always stays on top even when the screen is scrolled.
- **Scrolling:** thin bar on the right, mouse wheel (Windows, macOS and Linux; only with the pointer over it) and large **▲ ▼** buttons. The window can be resized and the content adapts to the width.
- **HEALTH tab (default):**
  1. Large overall traffic light: IN RANGE / CAUTION / RED ALERT / NO SIGNAL.
  2. Compact cards for pulse, oxygen, temperature, pressure, glucose and radiation, with a large value, a state chip and a mini trend.
  3. A single **action line**, only if an indicator is not green. For example: "Pulse high: stop the activity and tell the medical officer."
  4. Compact crew row: initials in their state color and their pulse. Tapping a crewmate shows their data.
- **BODY tab:** silhouette with markers, animated ECG and, below it, cards for oxygen, temperature, pressure and glucose.
- **Wide screen:** the 6 HEALTH cards go in 2 rows of 3, the crew sits below in larger cards and the silhouette labels stay close to the body.
- **ROUTINES tab:** sleep, exercise, level, points, streak, checklist with ✓ buttons, badges and team goal.
- Even though sleep and exercise are not shown in HEALTH, if they become critical they **still broadcast the alert** to the whole crew.

## Alert to the whole crew
When a crew member goes into **red alert** on any indicator (including sleep, exercise and radiation) or **loses signal**, **every** wristband notifies. The **red blinking** only appears on the wristband and helmet of the person at risk; crewmates get the notice with a sound, without blinking:
- **Affected person's wristband:** blinks for 3 s and shows "EMERGENCY · Name (YOU)" with second-person steps ("Check your mask…") and the **ACKNOWLEDGED** button, which stops the blinking and gives points for handling your alert.
- **Crewmates' wristbands:** play a sound and show "EMERGENCY · Name (Role)", the indicator and its value (for example "Mei's Sleep: 3.2 h") and the steps to help. They have three buttons:
  - **ACKNOWLEDGED:** stops the blinking and records who received the notice.
  - **VIEW THEIR DATA:** opens the affected person's full data.
  - **I'M ON MY WAY:** on the other wristbands the notice changes to "Alex is attending Mei" and stops blinking.
- **Fixed alarm strip:** while there are active alarms, a strip between the content and the footer shows one chip per affected person ("Mei · O₂ saturation 88 % (and 4 more) · waiting for help"). It is outside the scroll, so it is visible on HEALTH, BODY and ROUTINES. Tapping a chip reopens that person's notice.
- **Notice cycle (four different things):**
  1. **Received:** only marked for wristbands/helmets that were OPEN; for the others "unconfirmed (no open wristband)" is recorded.
  2. **ACKNOWLEDGED:** the device stops blinking for that cycle. It does NOT stop the repetitions.
  3. **I'M ON MY WAY (attention):** stops the repetitions and the notice becomes "Alex is attending Mei".
  4. **Resolution:** only when the indicator is back to GREEN ("Mei back within range").
- **Red → yellow is not recovery:** if the indicator drops from critical to caution, the incident stays open and the notice says "Mei left critical state (…) · still in caution". If it goes back to red, the SAME incident is reactivated.
- **Repetition and escalation:** while it stays critical and nobody attends, it repeats every `REPEAT_S` (15 s), even if the simulation is paused and even if everyone pressed ACKNOWLEDGED. After `ESCALATE_AFTER` (2) repetitions it is escalated to the Ship.
- **First-response time:** measured from when the incident started, not from the last repetition.
- **Notices covering notices:** an informative notification (points, "back within range") does not cover a current amber or red warning; all of them stay in the history.
- **Several emergencies:** shown in a queue by severity (no signal and SpO₂ first). One ACKNOWLEDGED or I'M ON MY WAY covers all of that person's critical indicators. If a new indicator becomes critical afterwards, a new notice appears.
- **Resolution:** when the indicator is back within range, a discreet "Mei back within range" notification appears on every wristband.
- **Caution (yellow):** still only a discreet notification and it is not broadcast.
- **Privacy:** the notice only shows the critical indicator and its value **when triggered** (labelled that way).
- **Pulse curve:** it is an **illustrative animation**, not a measured ECG; it is labelled that way on screen.
- **Graphs:** the mini trends and the console graph are **cut where data is missing**; a gap is never joined with a line.

## Routines and gamification (`routines.py`)
- **4 daily routines:**
  - Daily exercise: 20 pts.
  - Sleep 7 h: 15 pts.
  - Hydration: 10 pts.
  - Mood check-in: 10 pts.
- **Per crew member:** total points, **level** (points ÷ 100), **streak** (consecutive days with at least 3 routines) and **badges**: First routine (the first routine completed, not the first of the day), 3-day streak, Hydrated, Good crewmate and Always connected 24 h (**24 CONTINUOUS hours** of signal: the counter goes back to 0 if the signal is lost).
- **Notice rewards without duplicates:** one response covers all of that person's critical indicators; rewards are given once per incident even if the same person has both the wristband and the helmet open.
- **Collective goal:** 80 % of the crew's routines completed today.
- **Design rules:**
  - Points are given **only for behaviors**: completing a routine, handling your alert (+10), responding to a notice on time (+5) and helping a crewmate (+20).
  - Points are **never** given for "being healthy" and **never** taken away from someone in alert. That is why we replaced the suggested "Stable heart 24 h" badge with "Always connected 24 h".
  - There is no individual ranking: team progress is shown.
- **Simulation:** simulated crewmates complete routines on their own from time to time; you mark yours on the wristband. The console's **Close day ▸** button moves to the next day to test streaks and badges.

## Helmet HUD (`helmet.py`)
Designed to be read at a glance without distracting from the video. It has no backgrounds: everything is transparent and the texts have a dark shadow so they can be read over bright or dark video. It only uses 3 state colors: light blue/white (good), amber (caution) and red (alert or no signal).
- **Top left:** who you are, or which crewmate you are viewing.
- **Top center:** UTC time and link with the ship, discreet.
- **Top right:** large overall state, with the name of whoever is in alert when there is a broadcast.
- **Pulse circle:** the ring changes color with the state.
- **VITALS panel** (bottom left): HR, SpO₂, TEMP and PRES. Each row has the short name, the normal range below it and, right next to it, the value with its unit. The panel always shows only those 4 values; the value changes color when it is not OK and the critical row is marked with a red border. Alerts for other indicators (glucose, sleep, exercise, radiation) still arrive as an emergency notice.
- **Suit O₂ tanks** (bottom right): **PLSS** (main) and **OPS** (reserve), the same size and each with a line marking its level. On top is the percentage of the tank in use. Without tank data it shows "--" (it is never assumed full).
  - Tank notices are generated by the **engine** (`Mission._check_support`) even when the helmet is closed; they go into the event log (category "support") and `readings.support_status`. The helmet only shows them. If you open the helmet late, it shows the notice that is still current.
  - The **life-support** state is independent from the **health** state.
  - **PLSS:** amber notice at **50, 30, 10 and 5 %**, with the time it has left.
  - When the PLSS runs out (**0 %**), it switches to the reserve on its own and the notice "PLSS depleted: starting OPS emergency reserve" appears.
  - **OPS (emergency reserve):** notice **every 10 %** (90, 80 … 20 %) in amber; at **10 % and 0 %** it is a **red alert**, the OPS border turns red and the visor blinks red.
  - If several levels are crossed in a single advance (for example +1 h), only the lowest is notified, so notices do not pile up.
  - The PLSS drops faster in Caution and Red alert; the reserve lasts much less. This is simulated demo data. To test it quickly, put your crew member in Red alert and use **+1 min ▾ → +1 h** several times.
- **Red blinking:** only on the helmet of the person at risk (red alert, no signal or OPS reserve below 10 %), while the situation lasts. The other crew members only get the notification.
- **Emergencies and notices:** on the right, below the overall state.
  - The notice only shows the name and the problem (the steps are on the wristband). While nobody attends, the visor shows a blinking red border.
  - When someone is already attending, the notice says who.
  - Caution appears as a discreet notice.
- **Shortcut bar** (bottom): only the shortcuts that are useful at that moment. For example, "A · I'M ON MY WAY" only appears when there is a crewmate's alert.
- **All helmets at once:** the console's **⛑ All helmets** button opens each crew member's helmet in its own window, tiled (3 on top and 2 below), sized to fit the screen. Each one shows its own point of view: its video, data, tanks and notices, all live. With several helmets open each video drops to about 10 frames per second so the computer is not overloaded. If you use **⛑ Open helmet** again, that helmet stays open at its current size.
- **Keyboard (for use with gloves):** ◀ ▶ switches crew member, Home goes back to you, Enter = Acknowledged, A = I'm on my way, V = view data.
- **Background video (optional), one per crew member:** `fondo.mp4` (Alex), `video2.mp4` (Sofía), `video3.mp4` (Diego), `video4.mp4` (Mei) and `video5.mp4` (Omar), configurable in `VIDEOS_PER_PERSON` in `helmet.py`.
  - Switching member switches the video, either on the helmet (◀ ▶) or on the **wristband** of the same crew member (◀ ▶, tapping a crewmate or with VIEW THEIR DATA): the wristband and helmet of the same owner change together.
  - The video **does not start over**: all of them advance on the same clock since the helmet was opened, as if each camera kept recording. When you come back to someone, their video is at the point it should be. When a video ends, it loops.
  - The videos were resized to the visor size (1120×640, 24 frames/s, no audio) so the zip is smaller.
  - Needs `pip install opencv-python pillow`. If they are not installed, or that person's video is missing, the helmet draws space and shows the reason. Brightness is set with `VIDEO_BRIGHTNESS`.

## Phone as wristband (`mobile_server.py`)
Physical prototype: the phone shows the wristband **in real time** and you can respond to alerts and mark routines from it. Nothing has to be installed on the phone or in Python.

**How to use it**
1. Open `astrohealth.py` and tap **📱 Phone** in the console. It shows one address per crew member and a **6-digit code**.
2. Connect the phone to the **same WiFi** as the computer and open that person's address in its browser, for example `http://192.168.1.20:8765/?p=1&k=123456` (p=1 Alex … p=5 Omar).
3. If Windows asks about the firewall, allow access on **private networks**.
4. Optional: in the browser, "Add to Home Screen" to see it full screen. With an arm band it works as a wristband.

**What it does**
- **Ship → phone:** traffic light, 6 indicators with mini trend (cut where data is missing), action line, crew, BODY tab (illustrative pulse curve, indicators, PLSS/OPS tanks), ROUTINES (mark routines, mood 1-5, level, badges, team goal), fixed alarm strip and data mode. It updates by itself, about 4 times per second, when something changes.
- **Phone → ship:** ACKNOWLEDGED, VIEW THEIR DATA, I'M ON MY WAY and marking routines go through the same rules as the desktop wristband (`actions.py`, `routines.py`) and are **saved immediately** to the database. They show up right away on the console, the other wristbands and the helmet.
- **Alerts on the phone:** EMERGENCY window with the steps; the affected person's screen blinks red; it **vibrates** with every red alert (Android; Safari on iPhone does not allow a web page to vibrate).
- **Assistant voice on the phone (on by default):** reads **the same phrases** as the ship's voice ("Sir, red alert…"), with the same rules (0.5 s, one every 2 s, no sleep/routines/mood), in a deep tone.
  - The browser only allows speech after a tap: when the page opens it shows "Tap the screen to enable the alert voice".
  - It looks for a male English voice (British preferred); in the **ROUTINES → Alert voice on this phone** tab you can choose another voice, test it or turn it off (remembered on that phone). The 🔊 button at the top also turns it on/off.
  - It works even if the computer's voice is off (🔊 Voice button in the console). It only plays with the page open and the screen on.
- **Honest link:** the phone counts as an open wristband ("received") only while it is connected. If the WiFi drops, within a few seconds the ship stops counting it and the phone shows "NO LINK" and warns that its data may be out of date.

**Security**
- Local network only; no internet or external services. Every request needs the code (it changes every time the console opens). Phone actions are validated before they reach the mission.
- It uses unencrypted `http`: use it on a trusted network and only with synthetic or pseudonymized data. For real data you would need `https` and users with passwords.
- Because it is `http`, the browser does not let the page keep the screen on: increase the phone's screen timeout while testing.
- Port: `PORT = 8765` in `mobile_server.py`.

## Alert voice (`voice.py`)
Important alerts are read aloud automatically, in the **style of a suit assistant**: male English voice (British preferred, if the computer has one), slightly deep pitch, calm pace and complete phrases. It is not the real voice of any character or actor: it uses the voices already installed on the computer.

Examples of what it says:
- "Sir, red alert. Mei's oxygen saturation is at 85 percent."
- "Sir, red alert. We have lost Diego's signal."
- "Sir, Alex's PLSS is at 30 percent. About 4 hours 10 minutes left."
- "Sir, Alex's PLSS is depleted. Switching to the emergency reserve."
- "Sir, alert. Alex's emergency reserve is at 10 percent. … I recommend returning to the ship immediately."

What is read:
- **Red alerts** for pulse, blood oxygen, temperature, pressure, glucose, radiation and signal loss.
- **Suit oxygen:** PLSS at 50, 30, 10, 5 and 0 %, start of the OPS emergency reserve and every 10 % of the OPS.
- **Not read:** sleep, exercise, routines or mood (they still appear on screen). Set in `IGNORE_IN_VOICE`.

Rules:
- An alert waits **0.5 s** before being read; if it went back to green by then, **it is not read**.
- **At most one alert every 2 s**, and never on top of the one being read. If a person gets a new oxygen notice before the previous one is read, only the most recent one is read.
- **🔊 Voice** button in the console to turn it on or off (recorded in `config_changes`).

Fluency and voice engine (all offline):
- **Windows:** opens **one** voice process once and keeps it listening, so each alert plays immediately. It uses Windows voices, including the "OneCore" voices (for example **George**, UK, or **David**, US) if they are installed. On start it prints in the PyCharm console which voice it chose: `[Voice] Using voice: …`.
- To get a British male voice on Windows: Settings → Time & language → Speech → *Add voices* → English (United Kingdom).
- **Mac:** `say` with the Daniel voice. **Linux:** `espeak-ng`/`espeak`. If none of these is available and `pyttsx3` is installed, it uses that.
- Settings at the top of `voice.py`: `SALUTATION = "Sir"` (or `""`), `PREFERRED_VOICE` (for example `"George"`), `PITCH = -4`, `SPEED = 1`, `MIN_WAIT_S = 0.5`, `INTERVAL_S = 2.0`.

## Ship console (`astrohealth.py`)
- **My health** and **Crew**, with the 8 indicators.
- **Crew view:**
  - Total crew points, collective-goal bar and Close day button.
  - In the event log, alert broadcasts are **highlighted in red**, together with how long the first crewmate took to respond.
- **Explore a scenario:** Stable / Caution / Red alert / No signal, applied to one crew member or to all of them. It also has Play, the **+1 min ▾** button (tapping it opens: +1 min, +10 min, +30 min, +1 h and +5 h), speed and Random mission.
- **Long advance without freezing:** +1 h or +5 h are processed in blocks of `ADVANCE_STEP` (10) minutes; the window stays responsive and shows "Advancing… N min". If another advance is requested meanwhile, it is added (not duplicated).
- **Header with the mode** (SIMULATION / HISTORICAL DATA REPLAY / LIVE DATA) and each crew member's card also shows their life-support state.
- **Export session** reads the **complete session from SQLite** (not memory) and creates 3 CSV files atomically (temporary file + `os.replace`; if something fails no half-written file is left):
  - the reading history (all rows, from minute 0);
  - `_routines.csv`, with routines and rewarded behaviors;
  - `_notices.csv`, with who received, acknowledged and attended each notice, with the incident id.

## Database (`database.py`, file `astrohealth.db`)
Versioned schema (`PRAGMA user_version`, current version **3**, English table and column names). All queries are parameterized.
- **`sessions`:** UUID id, start (UTC), mode.
- **`readings`:** one row per crew member and simulated minute (**minute 0** is saved on start): real UTC date, simulated date, pseudonym (T1…T5), mode, indicators, quality and origin of each indicator (JSON), simulated mood, reported mood, PLSS, OPS, health state, life-support state.
- **`events`:** state changes, broadcasts, signal and life support, with minute, dates, incident (UUID) and category.
- **`notices`:** received / unconfirmed / acknowledged / on my way / repetition / escalated / back within range, with actor (receiver), dates and incident.
- **`routines`:** routines and rewards with minute, dates and value (for example the reported mood).
- **`measurements`:** real measurements (REPLAY / LIVE) with **original** value and unit, conversion applied, measurement instant, reception instant and quality.
- **`config_changes`:** scenario and configuration changes with actor and date.
- **Transactions:** readings, events, notices and routines of each save go in ONE transaction; cursors only advance after the commit. If the write fails, the console shows it and it is retried on the next save without losing or duplicating rows.
- **Responses saved immediately:** ACKNOWLEDGED / I'M ON MY WAY / VIEW THEIR DATA save the response and points right away (they do not wait for the 1 s watcher).
- **Safe close:** when the console closes everything pending is saved; if it cannot be saved, it asks before closing.

### Backup, migration and recovery
1. **Automatic backup before migrating:** if `astrohealth.db` has an older schema version, before any change it is copied byte by byte to `astrohealth.db.backup-YYYYMMDD-HHMMSS` in the same folder. The migration runs in a single transaction: if it fails, the database stays as it was. No data is deleted.
2. **Databases from the Spanish version** (schema v1/v2, tables `lecturas`, `eventos`, `avisos`…): they are backed up and then their tables and columns are renamed to English in place (`lecturas` → `readings`, `tripulante` → `crew_member`, `fc` → `hr`…). All existing rows are kept. Needs SQLite 3.25 or newer (included with current Python versions).
3. **Manual backup (recommended before updating):** with the program closed, copy `astrohealth.db` to another folder.
4. **Recover:** close the program, rename the current `astrohealth.db` (for example `astrohealth.db.bad`) and copy the backup as `astrohealth.db`. Note: an older backup will be migrated (and backed up) again when opened.
5. **Use a different database:** `Database(path_)` accepts another path; the tests always use temporary folders and never touch `astrohealth.db`.
6. **To be defined before using identifiable data:** file encryption, retention, who can export and where exports are stored (see "Limits").

## Quick settings
- `simulation.py`: `REPEAT_S = 15.0` (repetition of critical notices), `ESCALATE_AFTER = 2`, demo ranges and input-contract limits of each indicator, and radiation speed per scenario.
- `astrohealth.py`: `ADVANCE_STEP = 10` (minutes per block in long advances).
- `wristband.py`: `BLINK_S = 3.0` and `TOAST_MS = 5000`.
- `routines.py`: points for each routine and behavior, and `TEAM_GOAL = 0.80`.

## Files
- `astrohealth.py`: ship console. This is the file you run.
- `wristband.py`: wristband screen.
- `helmet.py`: helmet HUD (inherits the alert logic from the wristband).
- `simulation.py`: crew members, scenarios, indicators, protocols and alert broadcasting.
- `routines.py`: daily routines, points, streaks, badges and team goal.
- `database.py`: ship SQLite database (migrations, transactions, export).
- `mobile_server.py`: local-network server and page to use the phone as a wristband.
- `voice.py`: reads red and oxygen alerts aloud.
- `actions.py`: responses to notices and rewards (no interface; used by the wristband and helmet).
- `sources.py`: measurement contract, unit normalization, historical CSV source, processor (duplicates, out-of-order, staleness) and acquisition in a separate thread.
- `tests/`: automated tests.
- `fondo.mp4`, `video2.mp4` … `video5.mp4`: helmet background videos (one per crew member).

## Data modes and origin of each measurement
| Mode | Where the data comes from | What happens with missing data |
|---|---|---|
| **SIMULATION** | The engine generates synthetic data. | Not applicable. |
| **HISTORICAL DATA REPLAY** | A CSV with measurements (`sources.HistoricalCSVSource`). | It stays "not available". It is **never** filled with synthetic values. |
| **LIVE DATA** | Pending: `sources.LiveSource` is not implemented (the real device/protocol is missing). | It never says "connected" without evidence. |

- Each indicator stores its **origin** and **quality**: `ok`, `not_available`, `invalid`, `out_of_contract`, `stale`, plus `duplicate`, `out_of_order`, `unsupported_unit` and `unknown_indicator` in the processor.
- **Availability, quality, physiology and life support** are separate dimensions: lack of signal is **not** "normal", and invalid data is shown as "Invalid data", not as green.
- **Validation:** text, booleans, NaN, infinity and values outside the input contract are rejected (for example HR outside 0–300 bpm). Partial loss (one sensor) is handled per indicator; total loss triggers the "no signal" alarm.
- **Clocks:** real clock (UTC) separate from the simulated clock. In replay the instants from the file are used; if a row has no instant, it is flagged, never invented.

## Guide to configure the real source (once agreed)
1. **Historical CSV (already available):** columns `person, indicator, value, unit, t_measured, id, device`.
   - `person`: pseudonym `T1`…`T5` (never real names).
   - `indicator`: `hr, spo2, temp, sbp, glu, sleep, exercise, rad, plss, ops`.
   - `unit`: the file's unit. Known conversions: °F→°C, mmol/L→mg/dL (×18.0), µSv→mSv (the accepted synonyms for each UNIT are in `sources.SYNONYMS`). Any other unit is marked `unsupported_unit` and not used. The original value and unit are stored together with the conversion.
   - `t_measured`: ISO 8601 with zone (for example `2026-11-14T12:00:00Z`).
2. **Maximum staleness:** configured **per source and indicator** in `MeasurementProcessor(mission, staleness_limits={("my_source", "hr"): seconds})`. There is no invented default: without a configured limit, nothing is marked as stale.
3. **Live source:** implement a subclass of `sources.Source` with `connect_()`, `read_()` (returns measurements or an empty list) and `close_window()`. `Acquisition` runs it in a thread, retries and only reports "connected" when `connect_()` worked. The device documentation or an anonymized sample is needed to write it.
4. **Switch mode:** `mission.set_mode(MODE_REPLAY)` clears the synthetic values (they become "not available") before applying measurements.

## Tests
```
cd tests
python -m unittest -v
```
66 tests (they include real connections to the phone server inside the same computer). The helmet video tests are skipped if opencv-python and pillow are not installed; the others need nothing extra. `test_simulated_ui.py` uses a **fake tkinter**: it checks the windows' logic (what is saved, which tab each notice appears on), **not** the visual appearance. For that the app has to be opened with a real Tk.

## Security and personal data
- There are no credentials in the code. The crew members are fictional and are identified in the database by pseudonym (T1…T5).
- No data is sent to external services.
- Actions on alerts and configuration changes are recorded with actor, date and incident.
- **Before using data from real people** the following must be defined: access control (who views, records and exports), file encryption, retention, backups and where exports go. Today the app has no users or passwords: anyone with access to the computer sees everything.

## Limits and pending items
- Academic demo: demo thresholds, no clinical validation, no medical certification.
- `LiveSource` not implemented (device, protocol, format and frequency still to be defined).
- Connecting `sources.Acquisition` to the console (a button to load a CSV in REPLAY mode) has no interface yet; today it is used from code and tests.
- The oxygen time left is only computed in SIMULATION (with real data it says "not available").
- No access control or encryption (see Security).

## Why these indicators
NASA and the Canadian Space Agency (CSA) list these among the risks of long missions:
- **Insufficient sleep and irregular schedules.**
- **Bone and muscle loss:** that is why every astronaut exercises daily.
- **Inadequate nutrition.**
- **Mental health and behavioral changes:** hence the mood check-in.
- **Teamwork in isolation:** hence the shared alerts and the collective goal.
- **Space radiation:** the CSA monitors astronauts' dose on long missions.

AstroHealth turns those risks into indicators the astronaut can check and into behaviors the team can reinforce.

## Sources
AstroHealth's ranges and protocols are **illustrative**, made for the demo. These sources justify which risks we monitor, not the exact values.
- NASA Human Research Program · 5 Hazards of Human Spaceflight: https://www.nasa.gov/hrp/hazards/
- NASA Human Research Program · Risks of Human Spaceflight: https://www.nasa.gov/hrp/risks/
- NASA Human Research Program · Ongoing HRP Research in Space: https://www.nasa.gov/hrp/ongoing-hrp-research/
- CSA · Monitoring Radiation for Astronaut Health: https://www.asc-csa.gc.ca/eng/news/articles/2026/2026-02-26-monitoring-radiation-astronaut-health.asp
- CSA · Why Do We Conduct Science Experiments in Space?: https://www.asc-csa.gc.ca/eng/iss/science/why-do-we-conduct-science-experiments-in-space.asp

## Use of AI (required by Space Apps)
The base code was generated with the help of Claude (Anthropic) and the team reviewed and adapted it.
