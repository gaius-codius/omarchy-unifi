// Phase 0 stub. The singleton service lands here in Phase 9.
//
// Two constraints are already load-bearing and are asserted by gates today, so
// they are stated here rather than discovered later:
//
//   1. This file imports no `qs.*`. The service is instantiated by
//      `ensureService()` outside any bar, and tests/lint/no_qs_in_service.sh
//      keeps that half of the harness from silently acquiring a UI dependency.
//   2. `shell`, `manifest`, `omarchyPath`, `barWidgetRegistry` and
//      `pluginRegistry` are injected *after* construction (DATA-003), so real
//      initialization hangs off onShellChanged, never Component.onCompleted.
//
// The file exists now because omarchy-plugin-validate:84-86 requires every
// declared entry point to be present; without it AC-001 cannot pass at CP1 and
// the packaging gate would be blocked until Phase 10.
import QtQuick

Item {
  id: root

  // Injected by the shell after createObject returns (DATA-003).
  property QtObject shell: null
  property var manifest: null
  property string omarchyPath: ""
  property QtObject barWidgetRegistry: null
  property QtObject pluginRegistry: null

  readonly property string pluginId: "gaius-codius.unifi"
}
