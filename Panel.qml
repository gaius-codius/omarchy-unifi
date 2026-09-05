// Phase 0 stub. The bar widget and its popup panel land here in Phase 10.
//
// The entry point is named Panel.qml, not BarWidget.qml: this plugin ships a
// bar button *and* a popup from one file, which is what qs.Ui.Panel is the base
// class for. The manifest's entryPoints.barWidget names this file, so the
// filename is free — the media plugin's BarWidget.qml is a shape reference only.
//
// A bar widget receives only `bar`, `moduleName`, and `settings` (DATA-003a),
// and `bar` is null on the first frame, so the service is always reached
// through optional chaining and a null result renders REQ-013b rather than
// throwing.
import QtQuick
import qs.Ui

Panel {
  id: root

  moduleName: "gaius-codius.unifi"
  ipcTarget: ""          // DATA-010: the *service* owns the IPC target, not the widget.
  manageIpc: false

  // Ui/Panel declares `bar` as QtObject, so qmllint cannot see that the real
  // Bar item carries `shell`. The access is correct at runtime and the optional
  // chaining is mandatory (DATA-003a: `bar` is null on the first frame). The
  // suppression is scoped to these two lines rather than lowering the category,
  // which would stop the gate catching genuine typos everywhere else.
  // qmllint disable missing-property
  readonly property var unifiService: bar?.shell?.serviceFor("gaius-codius.unifi") ?? null
  // qmllint enable missing-property

  implicitWidth: 0
  implicitHeight: 0
}
