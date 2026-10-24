"""Observation hook (R-9). Inactive unless FSIM_OBSERVE_DIR is set. [c] instrumentation.
After FSIM_OBSERVE_AFTER_S seconds the process renders each top-level window to PNG and
records its geometry and the screen's available area, then optionally quits."""
import json
import os


def install(app, windows, tag):
    out = os.environ.get("FSIM_OBSERVE_DIR")
    if not out:
        return
    from PyQt5 import QtCore
    after = float(os.environ.get("FSIM_OBSERVE_AFTER_S", "4"))

    def snap():
        os.makedirs(out, exist_ok=True)
        scr = app.primaryScreen()
        rec = {"screen_logical": [scr.geometry().width(), scr.geometry().height()],
               "available_logical": [scr.availableGeometry().width(), scr.availableGeometry().height()],
               "device_pixel_ratio": scr.devicePixelRatio(), "windows": []}
        for w in windows():
            name = w.windowTitle()
            fn = f"{tag}_" + "".join(c if c.isalnum() else "_" for c in name)[:40] + ".png"
            w.grab().save(os.path.join(out, fn))
            fg = w.frameGeometry()
            mh = w.minimumSizeHint()
            rec["windows"].append({"title": name, "visible": w.isVisible(), "png": fn,
                                   "geometry": [fg.x(), fg.y(), fg.width(), fg.height()],
                                   "minimum": [mh.width(), mh.height()],
                                   "inside_screen": scr.availableGeometry().contains(fg)})
        with open(os.path.join(out, f"{tag}.json"), "w") as f:
            json.dump(rec, f, indent=1)
        if os.environ.get("FSIM_OBSERVE_QUIT"):
            app.quit()
    QtCore.QTimer.singleShot(int(after * 1000), snap)
