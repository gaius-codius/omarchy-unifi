# Omarchy plugin host contract (verified)

Platform: `omarchy 4.0.2-1`, `quickshell 0.3.1-1` (per `pacman -Q`).
`/usr/share/omarchy/version` contains the stale string `4.0.0.alpha`; the
package version is authoritative.

Every host API this plugin uses must appear below with a `path:line` citation.
All paths are relative to `/usr/share/omarchy/` unless absolute.

---

## 1. Manifest

Validated by `shell/services/PluginRegistry.qml:43` `validateManifest()` and
mirrored by the CLI `/usr/bin/omarchy-plugin-validate`.

| Rule | Citation |
|---|---|
| `schemaVersion` must be JSON **number** `1` (string `"1"` rejected) | `PluginRegistry.qml:48`, `omarchy-plugin-validate:41` |
| Required keys: `id`, `name`, `version`, `kinds`, `entryPoints` | `PluginRegistry.qml:52`, `omarchy-plugin-validate:44` |
| `id` must match `^[A-Za-z0-9][A-Za-z0-9._-]*$` | `omarchy-plugin-validate:51` |
| `id` must not start with `omarchy.` (reserved) | `omarchy-plugin-validate:53`, `PluginRegistry.qml:602-607` |
| Kind → entry-point key map | `omarchy-plugin-validate:97-103` |
| Entry points relative, no leading `/`, no `..`, file must exist | `PluginRegistry.qml:36-41`, `:93-108`, `omarchy-plugin-validate:84-86` |
| **No symlinks anywhere in the plugin folder** (`.git` excepted) | `omarchy-plugin-validate:115-116` |
| `barWidget.defaultSection` ∈ `left`\|`center`\|`right`, default `center` | `PluginRegistry.qml:72-79`, `:169-174` |

Kind → entry-point key: `bar`→`bar`, `bar-widget`→`barWidget`, `menu`→`menu`,
`overlay`→`overlay`, `panel`→`panel`, `service`→`service`. `bar-widget` is
hyphenated; its entry-point key `barWidget` is camelCase.

`service` is a real kind (`shell.qml:289`, `:329`). The only first-party plugin
declaring both `service` and `bar-widget` is
`shell/plugins/services/media/manifest.json` — the shape to copy.

### HC-1 (blocking constraint): the manifest settings schema is inert in 4.0.2
`shell.qml:688-700` copies `barWidget.{displayName, description, category,
allowMultiple, defaults, settingsForm, schema}` into registry metadata and hands
it to `BarWidgetRegistry.register()` (`shell.qml:798`). Nothing in the shipped
4.0.2 shell reads `defaults`, `schema`, or `settingsForm` back:
`BarWidgetRegistry.metadataFor()` (`BarWidgetRegistry.qml:37`) has no caller.

Consequences, all load-bearing for this plugin:
- **`barWidget.defaults` are NOT merged into the `settings` object a widget
  receives.** Defaults must be applied in QML via `setting(name, fallback)`.
- **There is no settings-form renderer.** Users set inline settings by editing
  `~/.config/omarchy/shell.json` or via `omarchy shell setBarWidget`.
- The manifest `schema` block is still worth authoring — it is forward-looking
  documentation and the validator accepts it — but it cannot be relied on for
  validation or for defaulting.

### HC-2: `allowMultiple` is not enforced
Stored at `shell.qml:693` and read nowhere. Declaring `allowMultiple: false`
does not prevent a hand-edited `shell.json` from listing the plugin twice.

---

## 2. Object lifecycle and injection

### Service instances
`shell.qml:283-321` `ensureService(pluginId)`. Created with
`Qt.createComponent(url, Component.PreferSynchronous)`, parented to an invisible
`Item { id: serviceHost }` (`shell.qml:268-271`). Injected **after**
`createObject` returns, before publication into `_services` (`shell.qml:300-313`):

```qml
var inst = comp.createObject(serviceHost)
if ("omarchyPath" in inst) inst.omarchyPath = shell.omarchyPath
if ("shell" in inst)       inst.shell = shell
if ("manifest" in inst)    inst.manifest = manifest
if ("barWidgetRegistry" in inst) inst.barWidgetRegistry = shell.barWidgetRegistry
if ("pluginRegistry" in inst)    inst.pluginRegistry = shell.pluginRegistry
```

**A service's `Component.onCompleted` therefore runs with `shell === null`.**
Initialize from `onShellChanged` / `onManifestChanged`. `settings` is **not**
injected into a service (`shell.qml:305-309`) — a service has no `shell.json`
entry of its own.

### HC-3 (blocking constraint): the service's lifetime is tied to the bar entry
`_syncServices()` (`shell.qml:323-346`) creates a service only when
`pluginRegistry.isEnabled(id)` (`shell.qml:331`), and `isEnabled` resolves
through `findEntryLocation` (`PluginRegistry.qml:138`), which searches
`bar.layout.{left,center,right}`. **Placing the widget on the bar is what
creates the service; removing it destroys the service**
(`shell.qml:336-345`). The service is not independently installable.

### Bar-widget instances
`Bar.qml:1766-1772` injects **exactly three** properties:

```qml
function injectProps() {
  var target = activeItem
  if (!target) return
  if ("bar" in target) target.bar = root
  if ("moduleName" in target) target.moduleName = moduleName
  if ("settings" in target) target.settings = moduleSettings
}
```

**`shell`, `manifest`, `service`, and `shellConfig` are NOT injected into bar
widgets.** `service` injection exists only for `panel`/`overlay`/`menu` kinds
(`shell.qml:627-638`), which does not apply here.

### Service resolution from a widget
`serviceFor` lives on the **shell root**, not on `bar` (`shell.qml:273-281`):

```qml
function serviceFor(pluginId) { return _services[String(pluginId)] || null }
function firstPartyServiceFor(pluginId) { return serviceFor(pluginId) }
```

`firstPartyServiceFor` is a pure alias and works for third-party ids. The chain
is `widget.bar.shell.serviceFor(id)`. Canonical first-party usage,
`shell/plugins/services/media/BarWidget.qml:10`:

```qml
readonly property var mediaService: bar?.shell?.firstPartyServiceFor("omarchy.media")
```

Optional chaining is mandatory — `bar` is null on the first frame;
`injectProps` runs from `onLoaded` and again via `Qt.callLater`
(`Bar.qml:1623-1626`).

---

## 3. `shell.json` and inline settings

Parsed at `shell.qml:72-88`. The user file is accepted whole only if
`parsed.version === 1`; there is **no deep merge** with defaults. Both the user
file and the defaults file are `FileView { watchChanges: true }`
(`shell.qml:117-139`); the user file uses `atomicWrites: true`.

Per-widget settings are stored **inline on the layout entry**, meaning every key
except `id` (`shell/plugins/bar/BarModel.js:10-18`). The bar binds them at
`Bar.qml:1549` and injects them as `settings` (`Bar.qml:1771`). Read them with
the base-class helper (`Ui/BarWidget.qml:41-44`, same code in `Ui/Panel.qml:39-42`):

```qml
function setting(name, fallback) {
  var value = settings ? settings[name] : undefined
  return value === undefined || value === null ? fallback : value
}
```

Settings-only edits are pushed onto the live item without a rebuild
(`BarModel.js:76-102`, `Bar.qml:376-388`); structural edits rebuild every widget
on every monitor (`Bar.qml:366-373`).

Whole config readable from a widget as `bar.shell.shellConfig`
(`shell.qml:56`) and `bar.shell.barConfig` (`shell.qml:115`). A service reads
`shell.shellConfig` directly off its injected `shell`.

### HC-4: duplicate entries are possible and every handler treats them differently
Entries are identified by **plugin id alone** — there is no per-instance id
(`Util.canonicalWidgetId` is now the identity function, `Commons/Util.qml:70-72`).

- `PluginRegistry.findBarLocation` (`:179-194`) scans left → center → right and
  returns the **first** match; later duplicates are invisible to
  `move`/`enable`/`disable`.
- `BarModel.inlineSettingsDelta` (`:97`) bails to a full rebuild when a
  duplicate id is present.
- `Bar.applySettingsDelta` (`:381-386`) pushes to **every** matching slot.
- `shell.updateEntryInline` (`:376-389`) rewrites **every** matching entry
  without breaking, so duplicates share one settings blob.

Combined with HC-2, duplicate entries are a real, reachable state. Failing
closed on conflicting duplicates is the correct behaviour.

---

## 4. IPC

Wrapper `/usr/bin/omarchy-shell`, reached as `omarchy shell` (group description
`/usr/bin/omarchy:78`). Usage `omarchy-shell [-q] <target> <method> [args...]`
(`omarchy-shell:21`). Transport (`omarchy-shell:59`):

```bash
output=$(timeout --kill-after=1s "$ipc_timeout" qs ipc -n -p "$OMARCHY_PATH/shell" call -- "$@" 2>/dev/null)
```

Default timeout 2 s, overridable with `OMARCHY_SHELL_IPC_TIMEOUT`
(`omarchy-shell:58`). **The wrapper never starts the shell**
(`omarchy-shell:23-24`).

### HC-5 (blocking constraint): IPC outcomes are not distinguishable by exit code
`omarchy-shell:56-77`. Connection failures exit non-zero from `qs ipc`;
**IPC-level failures are printed on stdout with exit 0** and are recognized only
by string matching:

```bash
if (( ipc_status == 124 || ipc_status == 137 )); then
  fail "omarchy-shell is not responding"
elif (( ipc_status != 0 )); then
  fail "omarchy-shell is not running"
fi

case $output in
  "Target not found." | "Function not found." | "Too few arguments provided"* | "Too many arguments provided"*)
    fail "$output" ;;
  "Not ready to accept queries yet"*)
    fail "omarchy-shell is not ready" ;;
esac
```

`fail()` prints to stderr and exits **1** — unless `-q` was passed, in which
case it exits **0** silently (`omarchy-shell:13-17`). Success prints the
handler's return value on stdout and exits 0 (`:79-82`).

Therefore **exit status alone cannot distinguish "no shell running" from
"target missing"** — both are exit 1. `scripts/configure` must call without
`-q`, capture stderr, and classify on the exact message:

| stderr message | what the host means by it |
|---|---|
| `omarchy-shell is not running` | `qs ipc` could not connect |
| `omarchy-shell is not responding` | the 2 s IPC timeout elapsed |
| `omarchy-shell is not ready` | the shell is up but not accepting queries |
| `Target not found.` | no `IpcHandler` is registered for that target |
| `Function not found.` | the target exists but has no such method |
| (exit 0, message on stdout) | delivered and acknowledged |

**This table records host behaviour only.** `omarchy-shell` exits 1 for every
row above except the last. How `scripts/configure` *classifies* each row —
which ones it treats as success — is a design decision, not a host fact, and is
owned by `SPEC.md` DATA-010b. Notably DATA-010b treats `Target not found.` as a
**deferred success**, because per HC-3 the service does not exist until the
widget is placed on the bar, making it the expected outcome when a first-time
user configures before adding the widget. That is a policy layered on top of
this table, not a contradiction of it.

### Registering a target
A plain Quickshell `IpcHandler` (`import Quickshell.Io`) with typed function
signatures. `Ui/Panel.qml:48-57` supplies a default handler gated on
`manageIpc && ipcTarget !== ""`; set `manageIpc: false` and declare the whole
handler yourself to add methods (`plugins/panels/tailscale/Panel.qml:11-13`,
`:366-378`; rationale comment at `plugins/panels/network/Panel.qml:15-17`).

**A target may have only one handler.** A bar surface exists per monitor, so a
widget-hosted handler registers N times and only one wins
(`Ui/BarWidget.qml:25-35` documents this and supplies `broadcast()`). Because
this plugin has a real `service` singleton, the `IpcHandler` belongs **in the
service**, matching `plugins/services/media/Service.qml:476`,
`plugins/services/nightlight/Service.qml:88`, `plugins/services/idle/Service.qml:337`.

### HC-6: `omarchy shell call <id> <method>` does not reach bar widgets
`callIfLoaded` (`shell.qml:567-579`) resolves only through `panelLoaders`, i.e.
`panel`/`overlay`/`menu` kinds. A dedicated IPC target is required.

Useful built-in `shell` methods: `rescanPlugins` (`shell.qml:890`),
`listPlugins` (`:952`), `listShellConfig` (`:994`), `setBarWidget(id, key,
valueJson, selectorJson)` (`:941`), `enablePlugin` (`:911`).

---

## 5. Process execution

`import Quickshell.Io` supplies `Process`, `StdioCollector`, `SplitParser`,
`IpcHandler`, `FileView`. **`DataStreamParser` does not appear anywhere in the
tree.** 92 `Process` blocks, 64 `StdioCollector`, 17 `SplitParser`.

Canonical shape, `plugins/panels/dropbox/Service.qml:220-233`:

```qml
Process {
  id: statusProcess
  running: false
  command: []
  stdout: StdioCollector { id: statusStdout; waitForEnd: true; onStreamFinished: root._statusOutput = text }
  stderr: StdioCollector { id: statusStderr; waitForEnd: true; onStreamFinished: root._statusError = text }
  onExited: function(exitCode) { ... }
}
```

`waitForEnd: true` is set on every `StdioCollector` in the tree. The launch
idiom is `command = [...]` then `running = true`, always guarded by
`if (proc.running) return`.

The helper is launched by **argv vector** — the same mechanism, no shell
involved. `plugins/panels/dropbox/Service.qml:59-66` is the precedent:
`statusProcess.command = ["python3", helperPath, "25"]`.

### HC-7 (blocking constraint): `onExited` and `onStreamFinished` have no guaranteed order
Documented verbatim at `plugins/panels/speedtest/Panel.qml:131-133`:

```
// Exit and stream-finished have no guaranteed order: when a failed exit
// beat the collector and published the generic message, replace it with
// the specific one once it lands.
```

Hence the tree-wide idiom `String(collector.text || root._cachedField || "")`.
Protocol parsing must therefore be driven by a join of both signals, not by
`onExited` alone, or stdout may be read empty on a fast exit.

### HC-8 (blocking constraint): there is no process-kill API; `running = false` is asynchronous
`grep '\.signal(\|processId\|SIGTERM'` over `*.qml` returns zero hits.
Termination is `running = false`, and `running` stays `true` until the child
actually exits — stated at `plugins/panels/speedtest/Panel.qml:84-90`:

```qml
if (speedTestProc.running) {
  // A dismissal's SIGTERM is still in flight; Process.running stays true
  // until the child exits, so queue the fresh run for onExited.
  if (expectedStop) pendingRun = true
  return
}
```

The paired `expectedStop` flag lets `onExited` distinguish a deliberate kill
from a failure (`:141-159`). A watchdog therefore cannot synchronously reap;
generation checks on late output are mandatory, not optional.

The watchdog precedent is `plugins/panels/tailscale/Service.qml:425-437`, with
an explicit arm-once discipline at `:188-192`:

```qml
// Arm on the launch that needs watching and leave it alone after that.
// Restarting it every refresh pushes the deadline out ahead of a hung
// process forever once the refresh interval is shorter than the timeout.
if (launched && !pollWatchdog.running) pollWatchdog.start()
```

---

## 6. Timers, polling, retry

Plain QtQuick `Timer` throughout. **There is no `Quickshell.Timer`.**
Standard periodic refresh, `plugins/panels/dropbox/Service.qml:160-167`:

```qml
Timer {
  id: refreshTimer
  interval: root.refreshIntervalSec * 1000
  repeat: true
  running: true
  triggeredOnStart: true
  onTriggered: root.refresh()
}
```

Interval clamping helper, `plugins/panels/dropbox/Service.qml:51-57`
(`intSetting(name, fallback, min, max)`).

### HC-9: no backoff helper exists
`grep 'backoff\|Backoff\|retryDelay'` returns nothing tree-wide. The only retry
implementations are bounded fixed-interval loops
(`plugins/panels/weather/Panel.qml:362-374`, `plugins/panels/bluetooth/Panel.qml:537-551`).
Exponential backoff must be built from scratch here.

Also available and worth copying: the **startup ramp**
(`plugins/panels/dropbox/Service.qml:169-184`) — poll every 2 s until the
backend appears or ~30 s elapses, because the first poll usually lands before
the daemon is up.

---

## 7. UI composition

Base types, `import qs.Ui`:
- `BarWidget` — simple bar item (`Ui/BarWidget.qml`, 45 lines).
- `Panel` — bar item **plus** popup, owning open/close and IPC
  (`Ui/Panel.qml`, 59 lines). This is the entry-point type for a
  `bar-widget` with a popup; `plugins/panels/tailscale/Panel.qml:9` is the
  reference.

A bar-widget root **must** set `implicitWidth`/`implicitHeight` from its button
— the bar sizes the slot from `activeItem.implicitWidth/implicitHeight`
(`Bar.qml:1581-1582`).

Buttons: `BarIconButton` / `WidgetButton` from `qs.Ui`. **Never write a
`MouseArea`** — `Ui/WidgetButton.qml:95-118` owns hover, cursor, tooltip
show/hide, click dispatch and wheel. The author's surface is `tooltipText` and
`onPressed: function(b) { ... }` dispatching on `Qt.LeftButton` /
`Qt.RightButton` / `Qt.MiddleButton`.

Popup: `KeyboardPanel` + `PanelKeyCatcher` + `Flickable`, wired as
`plugins/panels/tailscale/Panel.qml:403-446`. `KeyboardPanel` is built on
`PanelWindow` + `WlrLayershell` rather than `PopupWindow` specifically so
keyboard-summoned panels receive keys (`Ui/KeyboardPanel.qml:6-23`). It handles
focus priming, anchoring, outside-click dismissal, fade, and the popout
coordinator — **do not call `bar.requestPopout` yourself when using it.**

`PanelKeyCatcher` signals (`Ui/PanelKeyCatcher.qml:38-44`): `moveRequested`,
`activateRequested`, `returnRequested`, `closeRequested`, `deleteRequested`,
`tabRequested`, `textKey`. Built-in bindings (`:51-83`): Esc closes; Tab/Backtab
cycle; `hjkl` and arrows move; Return/Space activate.

Tooltips: set `tooltipText` on a `BarIconButton` (bar tooltip, 400 ms delay,
`Bar.qml:885-933`), or `PanelToolTip` inside a popup.

### The surface Phase 10 actually uses (verified 2026-09-06 against Omarchy 4.0.2-1)

Everything the view layer touches, with the line that declares it. Anything not
on this list is not used; adding to the list means reading the host source
first, which is what R1a's citation rule is for.

`Ui/Panel` (`Ui/Panel.qml`) — the bar-widget entry-point base:
`bar` `:12`, `moduleName` `:13`, `settings` `:14`, `ipcTarget` `:15`,
`manageIpc` `:16`, `controller` `:17`, `opened` `:21`, `barForeground` `:22`,
`open()` `:24`, `close()` `:25`, `toggle()` `:31`, `switchPanel()` `:32`,
`setting(name, fallback)` `:39`. `manageIpc: false` suppresses the base's own
`IpcHandler` `:48-57`, which is required here because DATA-010 puts the IPC
target on the *service*, and two handlers on one target is HC-4's failure.

`Ui/BarIconButton` (`Ui/BarIconButton.qml`), which extends `Ui/WidgetButton`:
`iconComponent` `:8`, `slotSize` `:9`, `opticalSize` `:10`. The glyph is hidden
whenever `iconComponent !== null` `:32`, so `text` and `iconComponent` are
mutually exclusive — a widget wanting a drawn mark *and* text beside it must
widen `slotSize` and put both inside the component. From `WidgetButton`:
`text` `:8`, `fontFamily` `:9`, `foreground` `:11`, `tooltipText` `:29`,
`signal pressed(int button)` `:32`, and the sizing contract
`implicitWidth/implicitHeight` from `fixedWidth/fixedHeight` `:68-69`.
**Never write a `MouseArea`** — `WidgetButton.qml:95-118` owns hover, cursor,
tooltip show/hide, click dispatch and wheel.

`Ui/KeyboardPanel` (`Ui/KeyboardPanel.qml`): `anchorItem` (required) `:40`,
`bar` (required) `:41`, `owner` `:42`, `contentWidth` `:45`, `contentHeight`
`:46`, `open` `:49`, `focusTarget` `:61`, default `contentItem` alias `:63`,
`fittedContentWidth(width, cap)` `:161`, `fittedContentHeight(implicit, cap)`
`:168`. It calls `bar.requestPopout` `:241` and `bar.releasePopout` `:246`
itself — see HC-13.

`Ui/PanelKeyCatcher` (`Ui/PanelKeyCatcher.qml`): `blocked` `:36`, and the
signals `moveRequested` `:38`, `activateRequested` `:39`, `returnRequested`
`:40`, `closeRequested` `:41`, `deleteRequested` `:42`, `tabRequested` `:43`,
`textKey` `:44`. Bindings at `:51-83`: Esc closes; Tab/Backtab cycle;
`hjkl` and arrows move; Return/Space activate.

`Ui/Button` (`Ui/Button.qml`): `text` `:23`, `tooltipText` `:25`,
`hasCursor` `:30`, `focusable` `:31`, `bordered` `:33`, `foreground` `:35`,
`fontFamily` `:40`, `signal clicked()` `:61`. It carries no `enabled` property
of its own; `enabled` is `QQuickItem`'s and propagates to the internal
`MouseArea`, which is what makes REQ-011's disabled Refresh actually inert.

`Ui/PanelHero` (`Ui/PanelHero.qml`): `iconComponent` `:7`, `title` `:8`,
`meta` `:9`, `detail` `:10`, `foreground` `:11`, `fontFamily` `:12`,
`iconSize` `:13`, `iconOpacity` `:14`, `trailingControl` `:20`.

`Ui/PanelSectionHeader` (`Ui/PanelSectionHeader.qml`) is a `Text` subtype;
callers bind `text` from outside `:14-16` (the file says so in its own comment). Properties `foreground` `:10`,
`fontFamily` `:11`, `fontSize` `:12`.

`Ui/PanelSeparator` (`Ui/PanelSeparator.qml`): `foreground` `:10`,
`strength` `:11`; it takes its width from its parent `:13`.

### HC-18: `qmllint` cannot see inside an inline `QtObject`

Every grouped theme token in Omarchy is an unnamed `QtObject` sub-object —
`Style.font` (`Commons/Style.qml:322`), `Style.spacing` (`:234`), `Style.bar`
(`:342`), `Color.bar` / `Color.popups` / `Color.tooltip` (`Commons/Color.qml:73`,
`:78`, `:83`) — and every bar-injected colour arrives through a property
declared `QtObject` (`Ui/Panel.qml:12`, `Ui/KeyboardPanel.qml:40`).

`qmllint` resolves none of them. With `--missing-property error` it reports
each access as

```
Error: file.qml:1:1: Member "md" not found on type "QObject" [missing-property]
```

This is not a property of this repository: the **shipped** tailscale plugin
emits 35 of the identical error under the same invocation (verified 2026-09-06,
Qt 6). `tests/lint/qmllint.sh` therefore exempts that one message shape, by
**resolved type** rather than by file, line or property name — `QObject` is
qmllint's "I could not resolve the container" answer, so the exemption covers
exactly the set of accesses about which the tool has no information. A typo on
a type it *can* resolve still reads `not found on type "Text"` and still fails;
`tests/lint/selftest.sh` seeds one to prove it.

---

## 8. Theme tokens

`import qs.Commons` gives the singletons `Border`, `Color`, `Style`, `Util`
(`Commons/qmldir`).

Colours (`Commons/Color.qml`): `Color.foreground` `:19`, `background` `:20`,
`accent` `:21`, `urgent` `:22`, `muted` `:23`; groups `Color.bar.*` `:73-77`,
`Color.popups.*` `:78-82`, `Color.tooltip.*` `:83-87`.

**Widget convention: prefer the bar-injected colours with a `Color.*` fallback**,
because `bar` is null before injection —
`plugins/panels/tailscale/Panel.qml:40-43`:

```qml
readonly property color foreground: bar ? bar.foreground : Color.foreground
readonly property color urgent: bar ? bar.urgent : Color.urgent
readonly property color dim: Qt.darker(foreground, 1.55)
readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
```

`bar.barForeground` (`Bar.qml:69`) is the transparency-aware variant and is the
right choice for bar chrome.

Spacing (`Commons/Style.qml`): **never hardcode pixels** — use `Style.space(px)`
`:219` / `Style.spaceReal(px)` `:213`, which scale with the user's font and
spacing settings. Tokens `Style.spacing.*` `:234-260`. Fonts `Style.font.*`
`:322-338`. Bar metrics `Style.bar.*` `:342-347`. Interaction fills
`Style.hoverFillFor/selectedFillFor/...` `:154-164`.

Icons are Nerd Font glyphs rendered as text on a `BarIconButton`, which routes
through `Ui/OpticalGlyph.qml` for ink-centred alignment (`BarIconButton.qml:29-39`).
`iconComponent: Component { ... }` is the escape hatch for a drawn mark
(`plugins/panels/tailscale/TailscaleIcon.qml`).

### HC-10 (RESOLVED — `qs.*` resolves from outside the config root)
The open question was whether `qs.Commons` / `qs.Ui` resolve for a plugin loaded
from `~/.config/omarchy/plugins/`, since every first-party importer lives inside
`/usr/share/omarchy/shell/` and no in-tree third-party plugin demonstrates it.

Settled empirically on 2026-09-05 with a structural analogue that touches
nothing in the real Omarchy configuration. A throwaway Quickshell config root at
`/tmp/hc10/root` was given a `Commons/qmldir` declaring
`module qs.Commons` with a `Probe` singleton. A QML file at
`/tmp/hc10/outside/Outsider.qml` — deliberately **outside** that root —
contained `import qs.Commons` and read `Probe.token`. The root `shell.qml`
loaded it exactly as Omarchy loads a plugin, via
`Qt.createComponent(fileUrl, Component.PreferSynchronous)`:

```
$ quickshell -p /tmp/hc10/root/shell.qml
DEBUG qml: HC10 RESULT: PASS -> COMMONS_RESOLVED
```

The import resolved. Quickshell registers the config root as an **engine-global**
QML import path rather than resolving `qs.*` relative to the importing file, so
a plugin file anywhere on disk, loaded into the same engine, can import
`qs.Commons` and `qs.Ui`.

Residual risk is now low but not zero: this reproduces the mechanism, not the
literal case, because it does not exercise Omarchy's own root. Confirm once at
first staging (AC-026), but the UI phase no longer needs to be gated on it and
no vendored styling fallback is required.

---

## 9. Opening URLs

`Qt.openUrlExternally` appears exactly once tree-wide
(`plugins/panels/dropbox/Service.qml:140-151`). The newer, preferred Omarchy
form is `Quickshell.execDetached(["omarchy-launch-browser", url])`
(`plugins/panels/tailscale/Service.qml:376`). There is no `xdg-open` call in any
QML file.

For anything built from input, `Commons/Util.qml:53-64` is explicit that
`Util.execArgv(argv)` — not `Util.execDetached(command)` — is correct, because
`execArgv` passes an argv vector that bash expands without re-tokenizing.

---

## 10. Tooling and lifecycle

`omarchy plugin` subcommands: `add [--enable] [--yes]` (alias `install`),
`clone`, `disable`, `enable [placement]`, `list [--json]`, `remove` (alias
`rm`), `update`, `validate <folder>`.

`omarchy plugin validate` runs standalone, needs no running shell, and exits 0
when valid.

Manual install path (`shell/README.md:129-136`):
1. `~/.config/omarchy/plugins/gaius-codius.unifi/` with `manifest.json` plus the QML
   named in `entryPoints`.
2. `omarchy shell shell rescanPlugins` — **target `shell`, method
   `rescanPlugins`**. An earlier draft of this document wrote
   `omarchy shell rescanPlugins`, which is one argument short and prints the
   usage message; `omarchy-plugin-add:149` and `omarchy-plugin-remove:117` both
   use the three-word form.
3. `omarchy plugin enable gaius-codius.unifi --section right --after omarchy.network`.

The registry scan is **asynchronous**: `omarchy plugin enable` immediately after
a rescan can fail with "plugin is not known". Poll `omarchy plugin list --json`
until the id appears.

Live reload: `PluginRegistry` runs `inotifywait -m -r -q -e
close_write,create,delete,move` over `~/.config/omarchy/plugins`
(`PluginRegistry.qml:636-655`), debounced 150 ms (`shell.qml:60-64`), triggering
unload → `Qt.clearComponentCache()` → rescan (`shell.qml:739-759`).

### HC-19 (blocking constraint): a source change does not survive a hot reload

The reload cycle above runs, and it is observable — `Local plugin changed,
reloading: gaius-codius.unifi` is logged, the service's `Component.onDestruction`
fires, and a new instance is constructed. **But the new instance is built from
the previously compiled source.**

Verified 2026-09-06 by adding a field to the staged `Service.qml`'s IPC `status`
reply. The service was demonstrably recreated — its per-instance id changed —
and the new field was absent. Deleting and re-creating the plugin directory, an
explicit `rescanPlugins`, and a full `omarchy plugin disable` / `enable` cycle
all behaved the same way. `~/.cache/qmlcache/` was empty throughout, so this is
not Qt's disk cache.

`omarchy restart shell` picks the change up; the field appeared immediately
afterwards.

Consequences:
- Any staged check must restart the shell after staging, or it tests whichever
  version of the source happened to load first. `tests/harness/staged.sh` does.
- Users updating the plugin must restart the shell. The README says so.
- The convenient reading of the paragraph above — "saving a file hot-reloads
  it" — is true of the *cycle* and false of the *code*.

### The shell's log is the user journal

`quickshell` runs under uwsm with stdout and stderr on a socket, so there is no
log file:

```bash
journalctl --user "_PID=$(pgrep -f 'quickshell -n -p /usr/share/omarchy/shell')" -o cat
```

Every line carries a colourised level tag, so strip ANSI escapes before
matching. This is the channel AC-012b, AC-026 and AC-028 read.

### `omarchy plugin list --json` has no `sourceDir`

Its keys in 4.0.2-1 are exactly `id`, `name`, `kinds`, `enabled`, `active`,
`canDisable`, `firstParty`, `clonedFrom`. AC-002 expects a `sourceDir`; there is
none. `PluginRegistry.qml:564` stamps `manifest.__sourceDir` onto the manifest
it injects, so the running service can report it — a stronger answer, because it
is where *this instance* was loaded from rather than where the registry believes
the plugin lives.

### `plugins` in `shell.json` is a list, not a map

`shell.qml:52` defaults it to `[]`, and `omarchy-plugin-enable` maintains it
through `omarchy-shell shell enablePlugin <id> <placement>`. Hand-editing it as
an object silently enables nothing.

Load errors surface as `console.warn` (`shell.qml:297` services,
`shell.qml:647` panels).

### Qt tooling on this machine (all verified 2026-09-05)
`qmllint`, `qmlformat`, `qmltestrunner`, `qmlls` are all present under
`/usr/lib/qt6/bin/` (Qt 6.11.2) but **none are on `$PATH`** — invoke by absolute
path. `quickshell` 0.3.1 is at `/usr/bin/quickshell`.

There is **no QML/JS test harness and no `.qmllint.ini`** anywhere in
`/usr/share/omarchy/`. `omarchy-plugin-validate:95` references a
`plugins-test.sh` that does not exist in the installed tree.

#### HC-11: `qmltestrunner` cannot load `Quickshell.Io` — it is not a usable harness
`/usr/lib/qt6/qml/Quickshell/Io/` contains only `qmldir`,
`quickshell-io.qmltypes`, and `FileView.qml`. Its `qmldir` reads
`linktarget quickshell-ioplugin` / `optional plugin quickshell-ioplugin`; the
plugin is linked **statically into `/usr/bin/quickshell`** and has no
loadable shared object. A `TestCase` importing `Quickshell.Io` therefore fails
to compile:

```
$ /usr/lib/qt6/bin/qmltestrunner -input tst_io.qml ; echo $?
FAIL!  : qmltestrunner::tst_io::compile() module "Quickshell.Io" plugin "quickshell-ioplugin" not found
Totals: 0 passed, 1 failed, 0 skipped
1
```

It does exit non-zero and does report `FAIL`, so it fails loudly rather than
silently. But every construct this plugin's service layer depends on — `Process`,
`StdioCollector`, `IpcHandler`, `FileView` — lives in `Quickshell.Io`. **A
`tests/test_service.qml` run under `qmltestrunner` is impossible.**

The only working harness is `quickshell -p <runner.qml>`, driving assertions and
calling `Qt.exit(failures)`. Two constraints follow:
- It **requires a live graphical Wayland session**; with
  `QT_QPA_PLATFORM=offscreen` and no `WAYLAND_DISPLAY` it exits 1 with
  `cannot open display`. The QML integration layer is a graphical-session test,
  not a headless/CI one.
- `Qt.exit()` warns `no receivers connected to handle it` under a bare
  `ShellRoot`, so the runner must arrange its own exit path.

#### HC-12: `qmllint` needs a synthetic import root to see `qs.*`
Bare invocation, and `-I /usr/share/omarchy/shell`, both fail identically with
`Failed to import qs.Commons`. Since a bare "failed to import" can be misread as
"no findings", this matters. What works is an import root **containing** a `qs`
symlink to the shell tree:

```bash
mkdir -p /tmp/qslint-root && ln -s /usr/share/omarchy/shell /tmp/qslint-root/qs
/usr/lib/qt6/bin/qmllint -I /tmp/qslint-root <file>.qml
```

That resolves cleanly and produces real diagnostics. The import root must live
**outside** the plugin folder, because `omarchy-plugin-validate:115` rejects any
symlink inside it.

---

## 11. Popout coordination, Python launch, and the JS module seam

### HC-13 (CORRECTED — popout exclusivity is already global; do not reimplement it)
An earlier draft of this document claimed `Bar.activePopout` was per-monitor and
that REQ-007a therefore needed a service-held ownership token. **That was wrong**
and is corrected here.

There is exactly **one** `Bar` object. `Bar.qml`'s root is a plain `Item`
(`plugins/bar/Bar.qml:11`), instantiated by a single `Loader` in
`shell.qml:237-243` from `defaultBarComponent` (`shell.qml:225-235`). The Bar
fans out to monitors *internally*, via
`Variants { model: Quickshell.screens }` producing one `BarPanel` delegate per
screen (`Bar.qml:966-976`).

Therefore `property var activePopout` (`Bar.qml:83`) and
`requestPopout`/`releasePopout` (`Bar.qml:316-327`) are **global state across
every monitor**, not per-monitor state. The comment
`// --- popout coordination (same-bar single-popout model)`
(`Ui/KeyboardPanel.qml:222`) means "within the single Bar", which is the whole
session — it does not mean "per monitor".

`KeyboardPanel` already calls `bar.requestPopout(coordinatorKey)` on open and
`bar.releasePopout(coordinatorKey)` on close (`Ui/KeyboardPanel.qml:240`,
`:246`). So extending `Ui/Panel` with a `KeyboardPanel` and setting `owner`
gives **REQ-007a for free**: at most one panel open across all monitors.

**Do not implement cross-monitor panel arbitration.** Do not call
`bar.requestPopout` directly. The service must hold no panel-open state
whatsoever — if it ever gains a notion of "which monitor's panel is open", a
second competing coordinator has been introduced and the leak has happened.
AC-068 is a confirmation of host behaviour, not a feature to build.

### HC-17: the full UI layer is testable under `quickshell -p` **without staging**
`qs.*` resolves against the Quickshell **config root**, and the module path
mapping is `qs.X` → `<root>/X/qmldir` — *not* `<root>/qs/X/`. This differs from
the `qmllint` import root in HC-12, which does use a nested `qs/` directory
because `qmllint` resolves a module URI against `-I` paths in the conventional
way. Getting this wrong fails with `module "qs.Ui" is not installed`.

The working construction places the harness root **outside the repository**, so
`omarchy-plugin-validate:115`'s symlink prohibition is not engaged:

```bash
mkdir -p /tmp/uiharness/root
ln -s /usr/share/omarchy/shell/Ui       /tmp/uiharness/root/Ui
ln -s /usr/share/omarchy/shell/Commons  /tmp/uiharness/root/Commons
# root/shell.qml then Qt.createComponent()s the plugin file by absolute path
quickshell -p /tmp/uiharness/root/shell.qml
```

Verified on 2026-09-05 against the real `qs.Ui.Panel`:

```
UIHARNESS RESULT: PASS -> UI_OK Style.space=12 Color.fg=#cacccc opened=false
```

The real `Ui/Panel` base type instantiated, `Style.space(12)` returned `12`, and
`Color.foreground` resolved to the live theme value. Combined with HC-10, this
means **both** `Service.qml` and `Panel.qml` are exercisable in a live Wayland
session with nothing installed into `~/.config/omarchy/`.

Two consequences for planning:
- The `LIVE` tier in `SPEC.md` §12 is really **two** tiers: *live-harness*
  (needs a Wayland session, no staging) and *live-staged* (needs the plugin
  installed under the real shell). Only genuine host-integration criteria —
  AC-002, AC-003, AC-012b, AC-019, AC-026, AC-028, AC-032, AC-056, AC-068 — are
  live-staged. AC-004, AC-005, AC-011, AC-067 and AC-071 are live-harness.
- `Service.qml` must still avoid `qs.*` imports if it is to load in a harness
  root that has no `Ui`/`Commons` links, but with this construction that
  restriction is a simplification rather than a requirement. It should be
  enforced by a lint gate regardless, so the harness cannot silently break.

### HC-14 (blocking constraint): a `__pycache__` write hot-reloads the plugin
`PluginRegistry.qml:636-655` runs
`inotifywait -m -r -q -e close_write,create,delete,move` **recursively** over
`~/.config/omarchy/plugins`. `localPluginIdForPath`
(`PluginRegistry.qml:701-713`) maps any nested path back to its plugin id,
excluding only *top-level* dotted entries and `.git`:

```qml
if (relative.indexOf(".") === 0) return ""
if (relative.indexOf("/.git/") !== -1 || relative.endsWith("/.git")) return ""
```

A path like `gaius-codius.unifi/helper/unifi/__pycache__/paths.cpython-314.pyc` passes
both tests, so it resolves to `gaius-codius.unifi` and triggers
`localPluginChanged` → 150 ms debounce (`shell.qml:60-64`) →
`unloadPluginServices()` → `Qt.clearComponentCache()` → rescan
(`shell.qml:739-759`).

A multi-module Python helper writes `__pycache__` on its **first import**.
Verified on this machine:

```
$ python3 -E -s /tmp/pkgtest/main.py   → import ok, pycache: True
$ python3 -B -E -s /tmp/pkgtest/main.py → import ok, pycache: False
```

So without `-B`, the very first poll of a freshly installed plugin destroys and
recreates its own service mid-batch. **The helper must be launched with `-B`,
and nothing in the repository may write inside the repository at runtime or at
test time.**

### HC-15 (blocking constraint): `python3 -I` breaks a package-based helper
`-I` (isolated) implies `-P` from Python 3.11, which removes the script's own
directory from `sys.path`. Verified:

```
$ python3 -I -c 'import sys; print(sys.path[:3])'
['/usr/lib/python314.zip', '/usr/lib/python3.14', '/usr/lib/python3.14/lib-dynload']
$ python3 -I /tmp/pkgtest/main2.py
ModuleNotFoundError: No module named 'pkg'
```

This is the worst possible failure shape for a plugin declaring a 3.9 floor: it
**works on 3.9 and fails on 3.11+**. Launch with `python3 -B -E -s <abs path>`
and have the entry point insert its own directory onto `sys.path` explicitly.
Note that `-E` ignores `PYTHON*` environment variables but does **not** affect
`SSL_CERT_FILE` / `SSL_CERT_DIR`, which OpenSSL reads directly — so it is no
substitute for SEC-007a's explicit scrub.

### HC-16 (blocking constraint): QML/Node dual-use JS files cannot import each other
A `.js` file that must run under both the QML engine and `node --test` may not
use `.pragma library` or `.import`, because both are syntax errors under Node.
Verified:

```
$ node -e '.pragma library'
.pragma library
^
```

Two consequences for any `Model.js`-style layer:
1. **The JS modules must form an antichain** — no dual-use `.js` file may import
   another. Composition has to happen in QML.
2. **No dual-use `.js` file may hold mutable top-level state.** Without
   `.pragma library`, each importing QML document (`Service.qml`, `Panel.qml`)
   receives its own copy of the module scope, so any module-level variable would
   silently fork between them. All exports must be pure functions over frozen
   constants.

The host itself demonstrates the required dual-export idiom at
`plugins/bar/BarModel.js:211-212`.

## 12. Summary of constraints that change the design

| ID | Constraint | Design impact |
|---|---|---|
| HC-1 | Manifest `defaults`/`schema`/`settingsForm` have no runtime consumer | Defaults applied in QML; settings documented as a `shell.json` edit; no settings UI to rely on |
| HC-2 | `allowMultiple` unenforced | Duplicate-entry conflict handling is required, not defensive luxury |
| HC-3 | Service exists only while the widget is in the bar layout | Service teardown must be clean; "install the service separately" is impossible |
| HC-4 | Entries keyed by plugin id alone; duplicate handlers differ | Fail closed on conflicting duplicates |
| HC-5 | IPC exit code cannot distinguish absent shell from missing target | `configure` classifies on exact stderr text |
| HC-6 | `shell call` does not reach bar widgets | Own IPC target, registered in the service |
| HC-7 | `onExited` / `onStreamFinished` ordering is unspecified | Protocol parse must join both signals |
| HC-8 | No kill API; `running = false` is asynchronous | Watchdog cannot reap synchronously; generation checks are mandatory |
| HC-9 | No backoff helper exists | Build exponential backoff from scratch |
| HC-10 | **RESOLVED** — `qs.*` resolves from outside the config root; the import path is engine-global | UI phase is not gated; confirm once at staging |
| HC-11 | `qmltestrunner` cannot load `Quickshell.Io` at all | QML integration tests run under `quickshell -p` in a live Wayland session |
| HC-12 | `qmllint` sees `qs.*` only via an import root holding a `qs` symlink | Fixed lint invocation, with the root outside the plugin folder |
| HC-13 | Popout exclusivity is already **global** — one `Bar` fans out via `Variants` | REQ-007a comes free from `Ui/Panel` + `KeyboardPanel`; build nothing, hold no panel state in the service |
| HC-14 | A `__pycache__` write inside the plugin dir hot-reloads the plugin | Launch the helper with `-B`; nothing may write inside the repo at runtime or test time |
| HC-15 | `python3 -I` strips the script dir from `sys.path` on 3.11+ but not 3.9 | Launch with `-B -E -s` and bootstrap `sys.path` in the entry point |
| HC-16 | Dual-use QML/Node `.js` files cannot `.import` each other or hold top-level state | JS modules form an antichain of pure functions; compose in QML |
| HC-17 | `qs.X` maps to `<root>/X`, so a harness root outside the repo with `Ui`/`Commons` symlinks loads the real UI layer | `Panel.qml` is testable without staging; the `LIVE` tier splits into live-harness and live-staged |
