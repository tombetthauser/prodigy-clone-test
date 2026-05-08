## Completed Tasks 

Move tasks here after complete, add to the bottom to maintain order, add a sub-list of abbreviated notes about what was added / changed etc leading with a note about what date / time the task was completed and approximately how long it took to complete and a note about how to test it.

- [x] Backpack logout button + config password autofill block + redesigned login screen (May 2026)
  - **Backpack (`static/backpack.html`)**: header now contains a `Log out` form posting to `/logout`; in embedded mode (play.html iframe) the form is set to `target="_top"` so the parent window navigates instead of the iframe.
  - **Play (`static/play.html`)**: config password modal hardened against autofill — added two off-screen `position:absolute;left:-9999px` decoy username/password inputs, set `autocomplete="new-password"` + `data-1p-ignore` + `data-lpignore` + `data-form-type="other"` on the real input, and the modal now opens with `readonly` (removed after a short timeout) so password managers don't pre-fill the field.
  - **Backend (`app/main.py`)**: added `login` to `SCREEN_MUSIC_KEYS` (so screen-music config gets a "Login screen" row); new `login_bg_world_id` meta + getter/setter; new public `GET /api/login-config` returns `{background_url, music_url, music_volume_pct}` falling back to a random world background and a random music file when nothing is configured; new `POST /config/login` saves the chosen world id; `/api/config/summary` now exposes `login_bg_world_id` and a flat `worlds` list for the dropdown.
  - **Login (`static/login.html`)**: rewritten with a centered shell, two backpack-modal-style cards (rounded 18px corners, `0 24px 60px rgba(0,0,0,0.35)` shadow, cornflower-blue primary buttons), a faint dark gradient overlay, accessible flash messages, a fixed top-right mute button, and a `<audio id="login-music" loop>` element wired to the same `mmo_audio_session_v1` / `mmo_muted_v1` keys used elsewhere. On load it fetches `/api/login-config` and applies the chosen background as `background-size:cover` plus auto-plays the chosen (or random) music when not muted.
  - **Config (`static/config.html`)**: new "Login screen" section with a `Background world` dropdown (`(random world)` first, then every world from `/api/config/summary`); the login music row appears in the existing Screen-music table thanks to the new `SCREEN_MUSIC_ROWS` entry; `?saved=login` flash message added.
  - **Test**: log out from the backpack header (and from the in-play backpack modal) — both end the session and redirect to the login screen. Open `/config` → enter the gear modal — saved passwords should not auto-populate. In config → Login screen pick a world (or leave on random) and choose a login music track in Screen music → `/login` should show that background full-screen with cover sizing, the chosen track loops once you opt into audio, and after a fresh tab load the page starts muted until you click the speaker.

- [x] Player attack SFX in config; quiz modes; quiz NPC freeze; session mute behavior (May 2026)
  - **Backend (`app/main.py`)**: `users.attack_sfx_*`, `quiz_mode` meta + `QUIZ_MODE_KEYS`, REST `/api/users/{id}/attack-sfx`, `/config/quiz` accepts `quiz_mode`, recycle/transform copies pet attack SFX to user, pet AI respects `quiz_hold_until`, WebSocket `quiz_begin` / `quiz_result`.
  - **Play (`static/play.html`)**: `user_attack_sfx` + hello/settings `quiz_mode` and attack SFX URLs; `pickQuizProblem()` by mode; `quiz_begin` on modal open; frozen pet render/attack; labels `x =` vs `=`; mute uses `sessionStorage` session gate + `localStorage` preference (`mmo_audio_session_v1` / `mmo_muted_v1`).
  - **Config (`static/config.html`)**: Quiz math mode dropdown + max number; Users table “Attack sound” column (same pattern as pets).
  - **Worlds / backpack**: Screen music respects same session-first-muted behavior as play.
  - **Test**: Config — set quiz mode and user attack SFX, save; play — math modal matches mode, targeted pet stays idle; transform copies pet attack sound; first tab load muted until volume UI used; worlds/backpack music follows mute after play opts in.

- [x] Layering: defeat ball above coin pile; all characters above piles and balls (May 2026)
  - Client `play.html`: split render into floor pass (coin piles + despawn, standalone defeat balls, collect anims) sorted by Y then `floorZ` (pile layer 0, ball layer 1), then mob pass (pets, players, death animations). Ensures ball draws over pile and characters draw over floor objects.
  - Test: defeat a pet near a pile; ball should cover pile; walk avatar over pile/ball — character stays visually on top.

- [x] Coin pile removal uses despawn-style animation (May 2026)
  - `coinPileRemoving` state + `drawCoinPileDespawn` (~1.2s shrink/flash); triggered when server drops a pile id from sync.
  - Test: collect or expire a pile — pile should shrink/glow away instead of popping off.

- [x] Configurable event SFX (dropdown from `static/sfx`, relative volume, demo) + triggers (May 2026)
  - Backend: meta keys `sfx_evt_*`, `GET` summary + `POST /api/config/event-sfx`, `event_sfx` on hello/settings/broadcast; backpack API returns `event_sfx` + `sfx_volume`.
  - Config UI: Event sounds section in `config.html`.
  - Play: `playEventSfx` — world switch (sessionStorage), magicoin delta &gt; 0, defeat count up; backpack plays `shop` on successful shop actions.
  - Test: assign sounds in config; enter world from chooser, gain coins, defeat pet, buy potion in backpack — hear assigned clips.

- [x] Per-world music volume + demo in config (May 2026)
  - DB `worlds.music_volume_pct`, hello + `music` WS message include `world_music_volume_pct`; client scales `bgMusic.volume` with global music slider × world %.
  - Config worlds table: track volume slider + Save + Demo track button (`demoWorldTrack`).
  - Test: lower one world’s track volume in config, reload play in that world — quieter vs global music slider.

- [x] Make characters appear to overlap one another, characters closer to the bottom of the screen (based on their bottom edge) should appear in front.
- [x] Make each individual character's width editable in config rather than one width setting.
- [x] Add fuzzy transparent oval shadows below all characters.
- [x] Remove black border around greeting text bubble.
- [x] Add the trim white function as a button for every character / sprite in config
- [x] Save the location of each character and what direction they are facing so that it is retained after a hard reload and make sure each character loads in the right place facing the right way when other users log on or hard refresh the page.
- [x] Make an optional attack image upload for every individual character in config and offer the same flip image and trim white space options for the attack images.
- [x] When a user presses A if their character has an attack image make the character pause movement and go through a quick animation sequence where it squishes horizontally, then switches to it's attack image, then stretches out a bit horizontally, then squshes back down horizontally, then switches back to it's normal image, then stretches back to it's normal width. This attack image should be flipped if the character was flipped from walking left or right when A was pressed.
- [x] Remove the Guide and Bot characters.
  - done: set default NPC list empty (no guide/bot render).
- [x] Add a button in config for Add Pet that will create a NPC that wanders around, pauses and attacks randomly? They should have the same image upload, flip and trim whitespace options along with an editable name field.
  - done: added pets table + add/delete/name + sprite/attack upload/flip/trim tools + server pet wander/attack loop + live pets sync.
- [x] Make a trim empty space function / button for each individual character's images that removes empty space on the top, left, right and bottom of the image.
  - done: added alpha-bounds trim function and buttons/routes for user sprite + user attack sprite.
- [x] Make the shadow darker and blur it better, currently its not dark enough and the blur has a weird sharp cutoff at the oval's edge.
  - done: widened shadow gradient, darker center, smoother multi-stop falloff.
- [x] Add functionality for an optional background music file that can be added / uploaded through config that will loop in the game.
  - done: added config music upload, saved current music in meta, loops in play via settings + ws sync.
- [x] Add sliders in config to adjust the sfx and music volume independently.
  - done: added config sliders + endpoints + live ws settings (sfx/music separate).
- [x] Add a size control and speed control to each pet NPC in config.
  - done: added pet stats form (size/speed) + backend persist + live pets sync.
- [x] Make the movement of the Pets smoother.
  - done: added client pet interpolation lerp toward server targets each frame.
- [x] Make the shadows just a flatter oval without any blur gradient, just make it a consistent slightly darker transparent black.
  - done: replaced gradient shadow with flat rgba black ellipse.
- [x] Add darkness and height controls for the shadow to config.
  - done: added shadow sliders + /config/shadow endpoint + live ws apply.
- [x] Make sure all the config settings are saved to the db.
  - done: persisted shadow/music/sfx/speech settings in meta and read on load.
- [x] Make the Pet NPCs use the same walking and attacking animations as the main user character.
  - done: added pet walk wobble/relax + attack animation state synced from server attack flag.
- [x] If possible add a blur radius control in config for the shadows.
  - done: added blur slider and applied via canvas shadowBlur.
- [x] Make the Pet NPCs pause more often and for longer.
  - done: increased pause chance and duration in pet runtime loop.
- [x] Allow characters to move 150px closer to the borders of the screen so they can go slightly off the screen.
  - done: expanded movement clamp with 150px edge outset.
- [x] The shadow controls do not seem to be effecting the size or blur radius or darkness of the shadows on the characters. This needs to be fixed.
  - done: made height control affect shadow thickness; implemented real blur via multi-pass ellipse; darkness/blur/height apply live from settings.
- [x] Create a "Code Behavior" option to each individual NPC Pet that will open a new page with a monaco code editor that will allow the user to write a python script for the pet's behavior. They should be able to use sleep, variables, loops and some custom functions like attack(), move_up(100), move_right(200px) etc. This script should be parsed and translated to actual behavior but does not need to be literally interpereted as python, this is just to help students play with python. Give each NPC's python behavior file a version of the default behavior that the NPC pets already have.
  - done: added code mode per pet + Monaco editor page + save/load API + simple python-like parser (sleep/attack/move + while/for) that drives pet targets.
- [x] Update the NPC code behavior so it will complete movements at a constant medium speed before continuing. Also when using sleep they should stop moving and animating completely.
  - done: added `await_move` so script waits for targets; sleep clears targets; client freezes pets while `sleeping`.
- [x] Add a recenter button in config for all NPC Pets that will place them back in the middle.
  - done: added `/config/pets/{pet_id}/recenter` + config UI button; snaps pet to center.
- [x] Add a position(0,0) function to the NPC Pet python scripting where 0,0 is the center.
  - done: added `position(x,y)` parsing + runner (0,0 = pet center).
- [x] Prevent NPC Pets from leaving the game area, they should hit the same edge boundaries the other characters hit at the top right left and bottom.
  - done: added `_clamp_pet_to_bounds` and applied each tick so scripted/wandering pets stop at the playfield edges (and reverse vx/vy on collision).
- [x] Player should not lose all it's health after running out of time on a math question, they should only lose about 15% of their health.
  - done: client now sets `pendingQuiz.expired` immediately on timeout so only one `quiz_result` is sent (was firing every frame); damage tuned to 15.
- [x] The number representing how many balls the player has collected should be in a cornflower blue.
  - done: split name and score in `drawName`; score rendered in `#6495ed`.
- [x] All the questions should be algebra questions. Currently there are questions like "15 + 16 = ", there should not be questions like this.
  - done: every problem now solves for x (e.g. `x + c = N`, `c - x = N`, `c + x = N`).
- [x] There should be a blue horizontal bar next to the time countdown that shrinks as the time runs out.
  - done: added `#quiz-timer-bar` next to the timer text; fill width interpolates from 100% to 0% across the 15s window.
- [x] The submit button should be a fun cornflower blue.
  - done: restyled `#quiz-submit` with cornflower blue background, rounded corners, hover/active states.
- [x] When NPC Pets randomly attack, if they hit the player or eachother they should do damage.
  - done: pet attack-start transition checks players + other pets in melee range; uses `attack_seq` + `last_hit_seq_by_*` so each attack lands at most once per target. Player damage broadcasts `player_state`; pet damage flows through `broadcast_pets_state`.
- [x] Make a difficulty setting in the config that controls the maximum number used in the math questions.
  - done: added `quiz_max_number` meta + Quiz section in config + `/config/quiz` endpoint + live ws sync; client uses it as the upper bound when generating problems.
- [x] Remove the ", x = ?" in the quiz question, the text should be "x = " to the left of the input field.
  - done: split modal into problem text + label (`x =`) next to input.
- [x] Vary the letter used in the algebra equasions.
  - done: each problem picks a random letter (x/y/z/a/b/n/m); label updates to match.
- [x] Make the the questions only pop up on every third attack, not every attack.
  - done: client tracks `quizAttackCount`; every 3rd attack on a live pet shows the modal, others send `attack` with `damage_pet: true` so the server applies damage directly.
- [x] Make a new backpack icon at the top right (backpack emoji) that opens to a new page that shows which NPC Pets the player has captured in a grid of 100px images of the NPC Pets including repeats. This page should also display the total number of questions the player has answered correctly.
  - done: added `captures` table + `correct_answers` column on users; capture rows recorded on ball collection; `quiz_result success:true` increments `correct_answers`; new `/backpack` page + `/api/backpack` JSON endpoint; 🎒 link added to play header.
- [x] Make a new Recycle button / emoji at the top right that will randomly swap the players image and attack image and size with a random other player or NPC Pet, increasing the size by 35px from the one they are copying.
  - done: ♻️ button posts to `/api/recycle`; server picks a random player or pet, copies sprite/attack sprite, sets player size to target size + 35px (clamped), broadcasts updated user_sprite/user_attack_sprite/user_profile.
- [x] Add a getting hurt animation to all players and NPC Pets where they squish down for a moment then pop back up, the animation sould be springy.
  - done: added `getHurtScale` damped-spring helper applied inside `drawMobSprite` (bottom anchored). Health-drop detection triggers `hurtT0` for me, peers, and pets.
- [x] In the backpack page, when a pet is clicked a medium / large modal should open showing the pet in animated in it's normal random behavior, walking pausing turning around and attacking, but it should be in a white space and should not actually move it's x / y coordinates. The modal should show how many of this pert have been captured, and if the user has captured at least 5 of the pet it should have a gold star and there should be a labelled recycle icon that allows them to transform their character into the pet.
  - done: backpack tiles are now buttons; clicking opens a modal with a fixed-position canvas animating the pet (walk/pause/turn/attack states, no x/y drift). Stored attack sprites in `captures` (with migration). Modal shows capture count, gold star at 5+, and a labelled "Recycle into this pet" button gated by a new `/api/recycle/pet` endpoint that requires `>=5` captures of that `pet_id`.
- [x] The current recycle button in the main screen should be moved to config
  - done: removed `♻️` from `play.html` top-right; added a Recycle section in `config.html` that calls `/api/recycle` (random target), shows status text on success/failure.
- [x] Implement a world system where every background image that has been uploaded represents a persistent independent environment. When a user logs in / starts they should be prompted with a grid of named thumbnails representing worlds that they can choose to enter. There should also be a button to add a new world that should go to an add world page where a user will be able to name the world and upload the background image for the world. After creating a new world they should enter that world. New worlds should be populated randomly with 3-5 NPC Pets that should respawn at the same rate they are in the current default world. The position and health of all the NPC Pets in the world should be tracked and saved so it is persistent when different users enter / exit worlds and log off / log it etc. Any balls left on the ground should also be persistent. In the config page the name and images for worlds should be editable. A new world icon should be added to the normal game world screen when a user is playing that will take users back to the choose world page.
  - done: added `worlds` table with per-world `background_file`; migrated existing single bg into world #1 named "Default"; added `world_id` column to `pets` and `current_world_id` to `users`. After login the home redirect now goes to `/worlds` (grid of named thumbnails plus an "Add world" tile). `/worlds/new` posts to `POST /api/worlds` (name + image), seeds 3–5 cloned pets in the new world (or blank pets if none exist), then auto-enters via `POST /api/worlds/{id}/enter`. Per-world background served from `/world-background/{world_id}`. WebSocket joins, moves, attacks, score, speech, leave, pets snapshots, pet-on-player and pet-on-pet damage, and player respawn broadcasts are now scoped to the player's current world via new `broadcast_world` / `broadcast_all_world` helpers. Pet runtime loop persists position + health for every pet (and balls remain on the ground) regardless of whether anyone is in that world. Config page replaced the global background uploader with a per-world list (rename + per-world background upload posting to `/api/worlds/{id}/name` and `/api/worlds/{id}/background`, with `notify_world_background_changed` now world-scoped). Added a 🌍 link in `play.html` next to the backpack that goes to `/worlds`.
- [x] Styling for world selector should be updated to match styling in backpack page.
  - done: rebuilt `worlds.html` and `worlds_new.html` on the backpack light theme (white sticky header, soft summary card, light grid, cornflower-blue accents). Removed the dark navy theme and replaced cards with the same `cell` look used by the backpack.
- [x] Thumbnail images for worlds in world selector page should just be the world background image.
  - done: each cell is now a 16:9 thumb whose `background-image` is the world bg URL (no extra meta panel, no gradient; just the image with the world name underneath and a "Current" pill on the active world).
- [x] When a player chooses a world they should appear in the center of the world screen to start.
  - done: `/api/worlds/{id}/enter` now sets the player's saved position to a sentinel (-1,-1); the WS hello detects the sentinel, sends `center_on_load: true`, and the client snaps `me.x/me.y` to the canvas center after `resizeCanvas()`.
- [x] A players health should persist between worlds and after they transform into another pet costume.
  - done: added `users.health` column; WS connect loads `health` from DB; `_damage_player` and the respawn loop persist health + position to DB so it survives reconnects (world changes / costume swaps no longer reset health).
- [x] The wording for the button to transform into another pet should use the word transform not recycle.
  - done: backpack pet modal button is now "✨ Transform into this pet" with "Costs 10 magicoin per transform" hint; insufficient/locked messaging updated; config "Recycle" section renamed to "Random transform" with the same wording.
- [x] A "Magicoin" system that tracks how many magicoins a user has at all times in the main screen using the static/sprites/magicoin/coin.png file as a small icon. Users can start with 100 by default.
  - done: `users.magicoin INTEGER NOT NULL DEFAULT 100` migration; balance included in WS `hello`; new `magicoin` WS message broadcast on every spend; `play.html` shows a top-left coin pill (using `/static-sprites/magicoin/coin.png` via a new static mount). Backpack and worlds pages also display the live balance.
- [x] Add an option to the backpack screen where users can buy a health potion for 20 magicoin that will fully heal them.
  - done: new `POST /api/shop/health-potion` (rejects if at full health or insufficient funds); backpack adds a "Buy health potion" card showing the cost, current balance, status messages, and re-broadcasts `player_state` so the active in-game session sees full health immediately.
- [x] Make changing costumes cost 10 magicoin and communicate that in text.
  - done: `/api/recycle` (random) and `/api/recycle/pet` both deduct 10 magicoin atomically and return `insufficient_magicoin` if the balance is too low; backpack modal hint and config "Random transform" button surface the cost; failure toasts say "Not enough magicoin (need 10)".
- [x] Make changing worlds cost 10 magicoin and communicate that in text.
  - done: `/api/worlds/{id}/enter` deducts 10 magicoin (skipped when the user clicks their current world); summary on `/worlds` says "Entering a world costs 10 magicoin"; failure toast surfaces `insufficient_magicoin`.
- [x] Make creating a new world cost 50 magicoin and communicate that in text.
  - done: `POST /api/worlds` deducts 50 magicoin before any DB/file writes; `/worlds/new` shows current balance and a "Cost to create: 50 magicoin" row; the +Add world tile on the chooser also labels the cost; insufficient funds keep the form unsubmitted and show an error.
- [x] Give the player a random number of 5-25 magicoins for each math question answered, indicate this with the text "Answer correctly for <coin.png icon image> Magicoin!"
  - done: `quiz_result success:true` now picks a random reward in `[MAGICOIN_QUIZ_REWARD_MIN, MAGICOIN_QUIZ_REWARD_MAX]` (5-25), increments `users.magicoin` and `correct_answers` in a single SQL statement, then broadcasts the new balance via `magicoin` WS message with a `delta` and `reason: "quiz_correct"`. Quiz modal got a "Answer correctly for [coin icon] Magicoin!" hint above the problem; the play screen's coin pill animates a "+N" float-up when a reward arrives.
- [x] The thumbnails in /worlds are still not displaying the background image for the world please fix this.
  - done: replaced the `<span>`/`background-image`/`aspect-ratio` thumb structure with a `<span class="thumb-wrap">` that uses padding-top:56.25% for a reliable 16:9 box and renders an `<img>` tag with `object-fit:cover`. Empty worlds fall back to a 🖼️ placeholder. Works in browsers without `aspect-ratio` support.

- [x] Pause all character behavior / animation when math question modal is open.
  - done May 7, 2026 (~45 min): while `pendingQuiz` is active and not expired, `draw()` returns early (frozen canvas) and `tick()` returns after updating only the quiz countdown—no movement/attack/wobble updates.
- [x] Player death modal (half magicoin loss, no respawn, links to worlds / logout).
  - done May 7, 2026 (~90 min): `_damage_player` no longer schedules respawn; on fatal hit `_apply_death_penalty` halves DB magicoin and broadcasts `magicoin` with negative delta; `/api/worlds/{id}/enter` revives dead players (full HP, no extra charge when re-entering same world dead); play shows death modal ~2s after death animation.
- [x] Larger icon buttons on main game screen (gear, backpack, worlds, mute).
  - done May 7, 2026 (~15 min): unified `.top-icon` styling (36px emoji, padding, backdrop blur); repositioned header offsets for magicoin pill.
- [x] NPC defeat coin pile drop (130px, spawn-style anim, collect after ball for 10–100 magicoin).
  - done May 7, 2026 (~2 h): server `coin_piles` map per world; spawn on pet defeat; unlock pickup after ball collected; attack collects pile and credits magicoin + WS broadcast; client renders pile with spawn glow + pulse when collectible.
- [x] Magicoin count animates up/down one digit at a time.
  - done May 7, 2026 (~30 min): `updateMagicoinDisplay` tweens toward target on balance updates; negative deltas show red float text.
- [x] Config gear password gate (`futureboy`).
  - done May 7, 2026 (~25 min): `#pw-modal` on play; gear uses `preventDefault` + unlock before `/config`.
- [x] Volume modal on speaker click (mute + SFX + music sliders).
  - done May 7, 2026 (~35 min): `#vol-modal` replaces mute toggle click; applies `applyAudioSettings`; blocks game keys while open.
- [x] Per-world music selection + looping.
  - done May 7, 2026 (~1.5 h): `worlds.music_file` column + migration; files from `static/sfx/music/` listed via `/api/worlds/music/list`; config table column saves track; `hello` uses world music URL or fallback global; WS `music` message updates clients.
- [x] Buy-a-pet shop in backpack + API + configurable cost/size limits.
  - done May 7, 2026 (~3 h): `POST /api/shop/pet` + options endpoint; modal with canvas preview, flip/trim, world dropdown; meta keys `buy_pet_cost`, `pet_size_min`, `pet_size_max`; config forms persist via `/config/shop/*`.
- [x] Config audit — shop economy keys exposed and editable.
  - done May 7, 2026 (~20 min): `/api/config/summary` + settings broadcast include buy pet cost and pet size limits; new config section "Shop / economy".

- [x] Score badge only on player characters, never NPC pets (May 7, 2026, ~10 min)
  - `drawName` now skips the score render when score is `null`/`undefined` (NPC pet drawables no longer pass a score, peers/self still do).
  - Test: defeat a few balls, walk past a pet — only your character (and other players) shows the cornflower-blue score next to the name.

- [x] Persist unmute state across worlds/screens (May 7, 2026, ~15 min)
  - Added `mmo_muted_v1` localStorage key; play.html reads it on load and writes whenever the volume modal toggles mute. Backpack/worlds/config respect the same key when applying their screen music.
  - Test: unmute on play, navigate to backpack/worlds/config and back — sound stays unmuted across reloads.

- [x] Pets stop animating right after page load (May 7, 2026, ~25 min)
  - Added a 600 ms warmup window after every hello: `petRender` is cleared and pet renders snap (no walk wobble or attack animation) until the warmup expires. Also clears warmup-related state on the per-pet record.
  - Test: open config or backpack, return to play — pets no longer "drift in" or play attack animations the instant you arrive.

- [x] More event SFX keys (May 7, 2026, ~30 min)
  - Backend `EVENT_SFX_KEYS` now includes `world_change`, `player_death`, `quiz_correct`, `quiz_wrong` alongside the existing four.
  - `play.html` fires `quiz_correct`/`quiz_wrong` on submit/timeout, `player_death` when local hp hits zero, and `world_change` when the user picks a different world from the new modal.
  - Config event-sound table picks them up from the EVENT_SFX_ROWS list.
  - Test: in config, assign an SFX to each new event; trigger them in play to confirm.

- [x] Quiz pause is local to player + targeted pet, with spinning spiral (May 7, 2026, ~50 min)
  - Removed the global "freeze the world" early-returns from `draw()`/`tick()`. Player movement keys still ignored while quiz is open; targeted pet's `petRender` snaps to a frozen position captured when the quiz opened, attack triggers/walk wobble are suppressed for that pet, but other pets/players keep moving and animating.
  - Added `drawThinkingSpiral` that renders a slowly rotating, gently pulsing 🌀 emoji above the player while the quiz modal is open.
  - Test: open a quiz; surrounding pets keep wandering, only the targeted pet stops, and a 🌀 hovers above your character until the quiz resolves.

- [x] Per-pet attack SFX (May 7, 2026, ~1 h)
  - Pets table gained `attack_sfx_file` and `attack_sfx_volume_pct` columns + migration. Snapshot/runtime carry `attack_sfx_url`/`attack_sfx_volume_pct`.
  - Buy-a-pet form (backpack) now exposes a sound dropdown (from `/api/sfx/list`), volume slider, and demo button; submit posts the new fields to `/api/shop/pet`.
  - Config gained an "Attack sound" column per pet with the same controls and a new `POST /api/pets/{id}/attack-sfx` endpoint.
  - `playPetAttackSfx` plays the configured clip with combined global SFX × per-pet relative volume the moment a pet starts an attack.
  - Test: pick a sound when buying a pet (or in config); in play the pet plays that clip on every attack until you change it.

- [x] Per-screen music with volume + demo (May 7, 2026, ~50 min)
  - Backend defines `SCREEN_MUSIC_KEYS = ("backpack", "worlds", "config")`, meta-backed file/volume per key, plus `GET /api/screen-music`, `POST /api/config/screen-music`, and additions to `/api/config/summary`, `/api/backpack`, and `/api/worlds`.
  - Config page got a "Screen music" section: per-screen file dropdown (from `static/sfx/music`), relative volume slider, demo and stop buttons.
  - Each screen plays its loop respecting the mute setting and combining global music volume × per-screen %.
  - Test: assign tracks in config and visit each screen — they should loop quietly until you mute or change the volume sliders.

- [x] World selector modal with despawn → fade-to-white → spawn (May 7, 2026, ~1.25 h)
  - 🌍 button now opens an in-game `#world-modal` with the same thumbnail grid styling as `/worlds`. Selecting a world closes the modal, plays `world_change` event SFX, runs a 700 ms shrink/flash despawn animation on the player (`drawPlayerDespawn`), then fades the whole screen to white via `#fade-overlay` and posts to `/api/worlds/{id}/enter`. Once the server confirms the move, the page reloads and the new world's hello triggers the existing pet-spawn-style animation on the player.
  - Test: enter a different world from the modal — your character should despawn, the screen should fade white, and you should re-spawn animated in the next world.

- [x] Music always loops in every context (May 7, 2026, ~5 min — verification)
  - `play.html` `bgMusic.loop = true`, `worlds.html`/`backpack.html`/`config.html` (`applyScreenMusic`/`applyConfigScreenMusic`) all set `a.loop = true`. Only the screen-music demo button uses `loop = false` (one-shot preview).
  - Test: leave any page open longer than the track length — music should loop seamlessly.

- [x] Music auto-plays on world enter when unmuted (May 7, 2026, ~5 min — verification)
  - Hello/`music` WS message updates `bgMusic.src` and calls `applyAudioSettings()`, which calls `bgMusic.play()` whenever the user is not muted. Mute state already persists via `mmo_muted_v1` in localStorage.
  - Test: unmute, switch worlds — the new world's track should start automatically.

- [x] Player score badge counts total backpack pets (May 7, 2026, ~30 min)
  - Server tracks `players[id]["capture_count"]` (loaded from DB on connect, incremented in `_record_capture`). Hello, join, move, and score WS messages now include `capture_count`. `others_snapshot` exposes it for peers.
  - Client `me.captureCount` / `peers[id].captureCount` drive the cornflower-blue badge instead of `defeat_count`.
  - Test: collect ball drops in any world — the number above your head should match your backpack tile count.

- [x] HP status + max HP visible in backpack (May 7, 2026, ~20 min)
  - Backpack page now shows a "❤ Health" summary row with `current/max HP` text and a colored bar (green / yellow / red). Refreshes after potion buys and max-hp boosts.
  - Test: take damage, open backpack — bar reflects current and persists across pages.

- [x] Buy more HP shop option (May 7, 2026, ~1 h)
  - Added `users.max_health` column + cap, `_get_user_max_health`/`_set_user_max_health`, and `MAGICOIN_COST_MAX_HP_BOOST=50` / `MAX_HP_BOOST_AMOUNT=25` (cap 500). New `POST /api/shop/max-hp` deducts coins, raises max, heals by the boost amount, and broadcasts `player_state` so the in-game HP bar updates live. All previous `PLAYER_MAX_HEALTH` usages now read the player's per-user max from `info["max_health"]`.
  - Backpack page got a "Boost Max HP" card showing the cost, current cap, and status messages.
  - Test: buy a boost in backpack — max HP increases by 25 (up to 500) and the in-game/backpack health bars track the new max.

- [x] Random pet instance names (May 7, 2026, ~50 min)
  - Added `static/data/pet-name-starts.txt` and `pet-name-ends.txt` (50 entries each). New `generate_pet_instance_name()` combines a random pair. Migrations add `pets.instance_name` and `captures.instance_name` columns.
  - Pets created via shop, world seeding, and `Add Pet` config all get a unique instance name; respawn after defeat re-rolls a fresh name. Snapshots, runtime, and capture rows all carry the value.
  - Backpack tile shows "<InstanceName>" with the pet type as a smaller secondary line; modal shows both. In play, the floating name now reads "InstanceName (PetType)".
  - Test: spawn / capture multiple pets of the same type — every instance should have a different combined name.

- [x] Shiny pets (May 7, 2026, ~1 h)
  - 10% spawn chance via `SHINY_CHANCE = 0.10`; persisted on `pets.shiny` and `captures.shiny` columns. Re-rolled on respawn so each instance can independently be shiny.
  - Shiny pets take ½ player attack damage (rounded) and deal 2× pet damage. Defeat coin piles spawn with 10× magicoin (with a `shiny: true` flag in the snapshot).
  - Client: golden filter (`sepia(.85) saturate(2.6) hue-rotate(-12deg) brightness(1.1)`) applied while drawing the sprite, plus three rotating ✨/⭐/🌟 sparkle emojis around the pet (in play world drawables and the backpack pet modal). Backpack tile thumbnails get the same filter and a ★ next to the name.
  - Test: spawn pets repeatedly — about 1 in 10 should appear gold with sparkles, hit harder, take less damage, and drop a much bigger coin pile.

- [x] Backpack opens as an in-game modal (May 7, 2026, ~45 min)
  - `play.html` now hosts a `#backpack-modal` with an embedded `<iframe src="/backpack?embedded=1">`. Backpack page detects embedded mode, hides the "Back to game" link, shows a "Close" button that posts `{type: "backpack:close"}` to the parent, and skips its own screen music so it doesn't fight world music.
  - While the modal is open, player movement is blocked (movingKeys gate), keyboard handling exits early, and the same spinning 🌀 spiral that the quiz uses is rendered above the player. Pets/peers continue to animate (the WS keeps running and `tick()` doesn't freeze).
  - Test: click 🎒 — backpack appears as a modal, your character pauses with a spiral, and other pets/peers keep moving. Press Esc / click outside / click "Close" to dismiss.

- [x] Worlds switching fade + backpack captured variant counts + config cleanup (May 8, 2026, ~55 min)
  - **World switching music fades (`static/worlds.html`)**: added fade-out ramps before entering a world and before leaving to game/backpack links, so screen music transitions out cleanly instead of hard cutting.
  - **Backpack captured counts (`app/main.py`, `static/backpack.html`)**: added `counts_by_tier` in `/api/backpack` and updated pet modal copy to show per-variant and per-type totals (`regular` vs `golden`) to avoid misleading counts when clicking individual pets.
  - **Quiz reward clarity (`static/play.html`)**: quiz reward placeholder now shows `…` until the server sends the exact reward for that question, then displays the concrete value.
  - **Config/open items verification (`static/config.html`, `static/worlds.html`, `app/main.py`)**: confirmed reset-captured button exists per user, delete actions exist for worlds/users/pets, and `/worlds` has no config link.
  - **Test**: open `/worlds` and switch worlds (and click back links) to verify music fades; open backpack and click regular/shiny versions of the same pet to verify the “Captured” breakdown; start attacks until quiz appears and confirm reward number resolves from `…` to an exact value.

- [x] Pet composer tools + world preview modal + per-environment pet management (May 8, 2026, ~40 min)
  - **Date/time + duration**: completed May 8, 2026 (about 40 minutes).
  - **New pet image tools verified (`static/backpack.html`)**: the buy-a-pet composer now uses local canvas processing with working toggles for `Flip`, `Trim white`, and `Trim empty` on both primary and optional attack sprites before submit.
  - **Speaker behavior verified (`static/play.html`)**: speaker click directly toggles mute/unmute state (`🔇`/`🔊`) without opening the volume modal.
  - **World thumbnail preview verified (`static/worlds.html`, `app/main.py`)**: world selection opens a modal with a larger preview image, creator username, monster count, and pet icon strip based on world pet sprite data.
  - **Config world pet controls added (`static/config.html`, `app/main.py`)**: each world row now includes an “Environment pets” panel listing pets in that world with delete buttons, plus an “Add existing pet” dropdown sourced from the current pet catalog; backend added `/api/worlds/{world_id}/pets/add-existing` and `/api/worlds/{world_id}/pets/{pet_id}/delete` and extended `/api/worlds` payload with `world_pets` and `pet_catalog`.
  - **Test**: open `/config` → in Worlds, confirm each world shows pets; use “Add existing pet” and verify it appears in the list and in-game in that world; delete one and verify it is removed; open `/worlds` and click a world tile to confirm preview modal metadata/icons; in `/play`, click the speaker and verify it toggles mute immediately.

- [x] Per-world static obstacle system in config + persistent obstacle hitboxes (May 8, 2026, ~70 min)
  - **Date/time + duration**: completed May 8, 2026 (about 70 minutes).
  - **Backend persistence (`app/main.py`)**: added `world_obstacle_configs` and `world_obstacles` tables (with migration/init), runtime cache `world_obstacles`, `obstacles_snapshot(world_id)`, and obstacle data in WS payloads (`hello` + `pets` broadcast).
  - **Obstacle config + controls (`app/main.py`, `static/config.html`)**: added endpoints for obstacle image upload, flip, trim white, trim empty bounds, min/max width + count settings, randomize/reposition generation, and delete-all per world. Config Worlds table now has a dedicated Obstacles panel under each environment row with those controls.
  - **Placement + sizing rules (`app/main.py`)**: random generation uses each world’s configured min/max width and count, stores each obstacle row in DB with world-specific position/size, and keeps configurations persisted per world.
  - **Hitbox + traversal behavior (`app/main.py`, `static/play.html`)**: each obstacle has an invisible collision rectangle covering its bottom third and full width; player and pet movement now block against that hitbox; obstacle rendering is depth-sorted with characters so entities can appear behind or in front based on bottom-edge Y.
  - **Test**: open `/config` → Worlds → Obstacles for a world: upload an image, run flip/trim tools, set min/max/count, click randomize; open `/play` in that world and confirm obstacles draw with depth layering and neither player nor pets can pass through bottom-third hitboxes; click delete-all and verify obstacles disappear and stay deleted after reload.

- [x] Three-screen world expansion with mirrored side screens + per-screen persistence (May 8, 2026, ~65 min)
  - **Date/time + duration**: completed May 8, 2026 (about 65 minutes).
  - **DB + model migrations (`app/main.py`)**: added `users.current_world_screen`, `pets.world_screen`, and `world_obstacles.world_screen` migrations so each world now has persistent screens `0/1/2` with independent pet/obstacle state.
  - **Region-aware snapshots and broadcasts (`app/main.py`)**: `pets_snapshot`, `obstacles_snapshot`, and coin-pile snapshots now support world+screen filtering; websocket `hello`/`pets` payloads include `world_screen`; server-side pet combat/collision checks now require matching world and screen.
  - **Screen switching protocol (`app/main.py`, `static/play.html`)**: added websocket message `switch_world_screen`; crossing left/right boundaries in play triggers screen transitions to neighboring screen index (when available), updates DB, re-centers player, and re-sends `hello` for the new region.
  - **Mirrored side backgrounds (`static/play.html`)**: background render now flips horizontally on side screens based on server `world_mirrored` flag while reusing the same world background image.
  - **Test**: in `/play`, walk left off center screen to enter screen 0 and right off center to enter screen 2; verify side screens show mirrored background, player recenters on transition, and pets/obstacles differ per screen and persist across refresh/reconnect.

- [x] Make sure the trim white space, trim empty space and flip buttons / functions work when creating a new pet.
- [x] Make a system in config for adding static obstacle elements to individual environments, it should allow the image upload, trimming white space, trimming empty space, flipping the image, setting a min width and max width and an obstacle count numeric input, items should be randomly positioned in the individual environment with random sizes within the designated min / max, and a random position / reposition button, the obstacles should have an invisible hitbox at their bottom taking up one-third of their height and their entire width, pets and user characters should be able to go behind it and in front of it but not traverse through the hitbox. There should also be a button to delete all obstacles in an environment. This should all be stored in the database and persist.
- [x] Get rid of the modal that opens when the speaker icon is clicked, have it just go back to muting / unmuting.
- [x] When a world is selected from it's thumbnail open a modal with a larger image of the environment, it should also list the user who created it, how many monsters are currently present, and small icons showing what pets can appear in that envronment.
- [x] Expand each environment from one screen to three, if the user crosses the left border they should enter a new environment screen with the same background but reversed, when they cross the right edge of the default environment screen they should enter another environment screen also with the same background image but reversed. All of these screens should have their own independent and persistent sets of pets and obstacles. All this should persist to the database.
- [x] In config underneath each individual environment make a small list of all the pets that can appear in that environment with the option to delete them. Also add an option to add a new pet to an individual environment with a dropdown of all currently available pets.

- [x] Environment pet-type assignment + bottom-third character hitboxes + side-screen edge-entry/persistence (May 8, 2026, ~95 min)
  - **Date/time + duration**: completed May 8, 2026 (about 95 minutes).
  - **Config pet types + per-environment cap (`static/config.html`, `app/main.py`)**: environment pet assignment now uses pet-type templates (grouped catalog instead of per-instance list), with a per-world “Max per screen” control saved via `/api/worlds/{world_id}/pets/settings` and clamped to `0..5`.
  - **Random pet spawning by assigned types (`app/main.py`)**: added `world_pet_types` persistence and screen population helpers so each screen auto-fills from assigned pet types up to the configured cap; side screens are seeded independently and persist in DB/runtime.
  - **Bottom-third/full-width character hitboxes (`app/main.py`, `static/play.html`)**: player and pet obstacle collision now uses bottom-third rectangular hitboxes (full sprite width) rather than circle overlap, so characters can visually overlap obstacle tops while still colliding at the base.
  - **Left/right screen transitions (`app/main.py`)**: moving between screens now places the player on the opposite edge of the destination screen (instead of center), keeps Y position clamped, and disables center-on-load spawn behavior for screen-to-screen moves.
  - **Side-screen obstacle parity + persistence (`app/main.py`)**: obstacle randomization now creates persistent sets for screens `0/1/2`; when entering a side screen, missing obstacle/pet sets are generated with independent randomized positions and persisted instances.
  - **Test**: in `/config` set environment pet types and max count, then open `/play` and cross left/right edges; verify entry starts at opposite edge with no spawn pop-in, pets/obstacles differ by screen but persist after reload, and player/pets collide only against obstacle bottom-third hitboxes.

- [x] Make sure NPC pets move/attack normally on left and right environment screens (May 8, 2026, ~15 min)
  - **Date/time + duration**: completed May 8, 2026 (about 15 minutes).
  - **Spawn/runtime parity (`app/main.py`)**: ensured side screens (`world_screen` 0 and 2) are seeded and maintained with persistent pet instances via `_ensure_world_screen_pets(...)` during world entry, websocket connect, and screen switching so each region has active pets to run normal AI.
  - **Behavior consistency (`app/main.py`)**: pet runtime loop already evaluates movement, pause, attack, player-hit, and pet-hit logic per matching world+screen; side-screen population now guarantees these loops have active local pets to animate and attack normally.
  - **Test**: enter a world, cross to left/right screens, confirm pets wander/pause/attack in those screens, then reload/re-enter and verify pets remain present and continue behavior.

- [x] Backpack pet modal count removal + one-capture transform unlock at 250 magicoin (May 8, 2026, ~25 min)
  - **Date/time + duration**: completed May 8, 2026 (about 25 minutes).
  - **Backpack modal copy/layout (`static/backpack.html`)**: removed the individual pet modal `Captured:` count/detail block from the modal view; updated unlock helper text to the one-capture rule and made transform cost text dynamic via a new `#modal-transform-cost` binding.
  - **Backpack unlock logic (`static/backpack.html`)**: changed transform unlock threshold from 5 captures to 1 capture (`THRESHOLD = 1`), updated locked-state messaging, and switched insufficient-funds toast to reference the configured transform cost value.
  - **Transform pricing + unlock rule (`app/main.py`)**: added `MAGICOIN_COST_PET_TRANSFORM = 250`, updated `/api/recycle/pet` to require at least one matching capture instead of five, and changed the endpoint spend to the new 250 cost.
  - **Shop payload sync (`app/main.py`)**: `/api/backpack` now returns `shop.transform_cost` using the 250 pet-transform cost so backpack UI remains in sync.
  - **Test**: open `/backpack` and click a captured pet tile — modal should no longer show a `Captured:` counter; with at least one capture the transform button should be enabled; attempting transform with low balance should report `need 250`; with 250+ magicoin, transform should succeed and deduct 250.

- [x] Preserve music across left/right environment screen transitions + add per-pet release for magicoin (May 8, 2026, ~35 min)
  - **Date/time + duration**: completed May 8, 2026 (about 35 minutes).
  - **No music reset on screen switches (`static/play.html`)**: added `setBgMusicUrl(...)` tracking with `data-src-key` so repeated `hello/settings` packets during left/right screen moves do not reassign the same audio source and restart the track.
  - **Static release value per captured pet (`app/main.py`)**: added `captures.release_base_magicoin` migration with backfill to random `10..150`, assigned on new capture insert, and exposed per-capture `release_magicoin` in `/api/backpack`.
  - **Release API + permanent removal (`app/main.py`)**: added `POST /api/captures/{capture_id}/release` which validates ownership, awards release payout (`base * 5` for shiny/golden), deletes that capture row permanently, updates live player capture counts, and broadcasts new magicoin balance.
  - **Backpack release UI (`static/backpack.html`)**: individual pet modal now includes a `Release pet` action plus a `Release value` display; successful release updates balance, removes the pet from inventory via reload, and shows a payout toast.
  - **Test**: in `/play`, cross between left/right screens and confirm music continues without restarting; in `/backpack`, open a pet and release it, verify payout amount (shiny = 5x), capture disappears from grid/counts, and magicoin increases immediately.

- [x] NPC obstacle hitbox enforcement + world-switch player-size sync fix (May 8, 2026, ~30 min)
  - **Date/time + duration**: completed May 8, 2026 (about 30 minutes).
  - **NPC obstacle collision consistency (`app/main.py`)**: pet movement now consistently runs through bottom-third/full-width rectangle collision checks against obstacle hitboxes (matching player logic) with persistent per-tick resolve/recovery handling in the runtime loop.
  - **World-switch size glitch fix (`app/main.py`)**: websocket `switch_world` now reloads and applies `users.sprite_width_px` from DB when rebuilding player state, and all `join/hello` packets in that path use the refreshed per-player width instead of stale startup values.
  - **World pet-type listing reliability (`app/main.py`)**: hardened per-world pet-type handling by filtering out stale template references and self-cleaning invalid `world_pet_types` links that pointed to deleted pets.
  - **Test**: in `/play`, walk pets near obstacle bases and verify they do not traverse obstacle hitboxes; switch worlds repeatedly after transforms/size edits and confirm your player size remains correct; in `/config` worlds table confirm each world’s pet type list remains populated and accurate.