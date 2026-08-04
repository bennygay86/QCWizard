import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pandas as pd
import os
import sys
import json
import csv
import glob
import pathlib
import webbrowser
from datetime import datetime


def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    import matplotlib.dates as mdates
    import matplotlib.ticker as mticker
    matplotlib.rcParams.update({"font.size": 9, "axes.titlesize": 10})
    HAS_MPL = True
except Exception as _mpl_err:
    HAS_MPL = False
    try:
        import traceback as _tb
        _log_dir = pathlib.Path(sys.executable).parent if getattr(sys, "frozen", False) \
                   else pathlib.Path(__file__).parent
        (_log_dir / "mpl_debug.txt").write_text(
            f"{type(_mpl_err).__name__}: {_mpl_err}\n\n{_tb.format_exc()}",
            encoding="utf-8"
        )
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Comparison specification
# ---------------------------------------------------------------------------

CHANNELS_COMPOUNDS = [
    ("MS TIC(+)", ["Caffeine", "Sulfadoxine", "Trimethoxybenzene"]),
    ("TWC",       ["Caffeine", "Sulfadoxine", "Trimethoxybenzene"]),
    ("MS TIC(-)", ["Sulfadoxine"]),
]

METRIC_SPECS = [
    ("Rt(min)",   "Retention Time", 0.1,  "absolute"),
    ("AreaAbs",   "Area",           15.0, "percent"),
    ("Height",    "Peak Height",    15.0, "percent"),
    ("Asymmetry", "Asymmetry",      2.0,  "absolute"),
    ("Tailing",   "Tailing Factor", 2.0,  "absolute"),
]

INSTRUMENTS = ["Colorado", "Ganges", "HuangHe", "Huron", "Nile"]

# Channels available per instrument (internal names)
INSTRUMENT_CHANNELS = {
    "Colorado": ["MS TIC(+)", "TWC", "MS TIC(-)"],
    "Ganges":   ["MS TIC(+)", "TWC", "MS TIC(-)"],
    "HuangHe":  ["TWC"],
    "Huron":    ["MS TIC(+)", "TWC", "MS TIC(-)"],
    "Nile":     ["MS TIC(+)", "TWC", "MS TIC(-)"],
}

def _app_dir() -> pathlib.Path:
    # When frozen by PyInstaller, __file__ lives in a temp extraction folder
    # that gets wiped on exit. Use sys.executable to find the real .exe location
    # so the config sits next to the .exe (and travels with it on cloud drives).
    if getattr(sys, "frozen", False):
        return pathlib.Path(sys.executable).parent
    return pathlib.Path(__file__).parent


CONFIG_PATH = _app_dir() / "reference_config.json"
ANNOTATIONS_PATH = _app_dir() / "annotations.json"
MAX_CSV_BYTES = 10 * 1024 * 1024
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def read_qc_csv(path: str) -> pd.DataFrame:
    """Read an input CSV after a small-file sanity check."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise ValueError(f"Cannot access file: {exc}") from exc
    if size > MAX_CSV_BYTES:
        max_mb = MAX_CSV_BYTES // (1024 * 1024)
        raise ValueError(f"CSV file is too large. Please select a file under {max_mb} MB.")
    return pd.read_csv(path)


def safe_csv_cell(value):
    """Prevent spreadsheet formula execution when exported reports are opened."""
    if value is None:
        return ""
    text = str(value)
    if text.startswith(CSV_FORMULA_PREFIXES):
        return "'" + text
    return text


def write_safe_csv_row(writer, values) -> None:
    writer.writerow([safe_csv_cell(value) for value in values])


def build_comparisons(instrument: str = None) -> list:
    """Build comparison list, optionally filtered to channels available for `instrument`."""
    allowed = set(INSTRUMENT_CHANNELS.get(instrument, [ch for ch, _ in CHANNELS_COMPOUNDS]))
    rows = []
    for channel, compounds in CHANNELS_COMPOUNDS:
        if channel not in allowed:
            continue
        for compound in compounds:
            for suffix, name, threshold, method in METRIC_SPECS:
                col = f"{channel}_{compound} {suffix}"
                criterion = (f"±{threshold:.0f}%"
                             if method == "percent"
                             else f"±{threshold}{' min' if suffix == 'Rt(min)' else ''}")
                rows.append({
                    "column": col, "channel": channel, "compound": compound,
                    "parameter": name, "threshold": threshold,
                    "method": method, "criterion": criterion,
                })
    return rows


COMPARISONS = build_comparisons()   # full list (all channels), used as fallback


# ---------------------------------------------------------------------------
# Config helpers
#
# Storage format (reference_config.json):
# {
#   "Colorado": { "reference": "/abs/path.csv", "report_dir": "/abs/dir" },
#   ...
# }
# Backward-compat: old flat format {"Colorado": "/abs/path.csv"} is handled.
# ---------------------------------------------------------------------------

def load_ref_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_ref_config(config: dict) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(config, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
    except Exception:
        pass


def _instrument_block(config: dict, instrument: str) -> dict:
    """Return (and normalise) the per-instrument config block."""
    if instrument not in config:
        config[instrument] = {}
    val = config[instrument]
    if isinstance(val, str):           # old flat format → migrate on the fly
        val = {"reference": val, "report_dir": ""}
        config[instrument] = val
    return val


def cfg_get_ref(config, instrument) -> str:
    return _instrument_block(config, instrument).get("reference", "")


def cfg_get_report_dir(config, instrument) -> str:
    return _instrument_block(config, instrument).get("report_dir", "")


def cfg_set_ref(config, instrument, path: str):
    _instrument_block(config, instrument)["reference"] = path


def cfg_set_report_dir(config, instrument, directory: str):
    _instrument_block(config, instrument)["report_dir"] = directory


# ---------------------------------------------------------------------------
# Annotations (chart markers for events like cleaning, column change, etc.)
#
# Storage format (annotations.json):
# {
#   "Colorado": [
#     {"date": "2026-08-04", "label": "Cleaned QDa", "note": "Deep clean per protocol"},
#     ...
#   ]
# }
# ---------------------------------------------------------------------------

def load_annotations() -> dict:
    if ANNOTATIONS_PATH.exists():
        try:
            data = json.loads(ANNOTATIONS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def save_annotations(data: dict) -> None:
    try:
        ANNOTATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        ANNOTATIONS_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
    except Exception:
        pass


def annotations_for(data: dict, instrument: str) -> list:
    """Return the list of annotations for one instrument (creates it if missing)."""
    lst = data.get(instrument)
    if not isinstance(lst, list):
        lst = []
        data[instrument] = lst
    return lst


# ---------------------------------------------------------------------------
# Pass/fail evaluation
# ---------------------------------------------------------------------------

def evaluate(ref_val, qc_val, threshold, method):
    try:
        ref = float(ref_val)
        qc  = float(qc_val)
    except (ValueError, TypeError):
        return False, "N/A"
    diff = qc - ref
    if method == "percent":
        if ref == 0:
            return False, "Ref=0"
        pct = diff / ref * 100.0
        return abs(pct) <= threshold, f"{pct:+.1f}%"
    return abs(diff) <= threshold, f"{diff:+.4f}"


# ---------------------------------------------------------------------------
# Trend data loader
# ---------------------------------------------------------------------------

def load_instrument_reports(instrument: str, ref_config: dict) -> list:
    """
    Load all exported QC CSVs for one instrument, sorted by datetime.
    Each entry: {
        "datetime": datetime,
        "overall":  "PASS" | "FAIL",
        "measurements": { (channel, compound, parameter): {"qc_value": float, "reference": float} }
    }
    """
    report_dir = cfg_get_report_dir(ref_config, instrument)
    if not report_dir or not os.path.isdir(report_dir):
        return []
    files = sorted(glob.glob(os.path.join(report_dir, f"{instrument}_QC_*.csv")))
    reports = []
    for fp in files:
        try:
            with open(fp, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
            meta = {}
            for row in rows[:4]:
                if len(row) >= 2:
                    meta[row[0].strip()] = row[1].strip()
            dt      = datetime.strptime(meta.get("Date / Time", ""), "%Y-%m-%d %H:%M:%S")
            overall = meta.get("Overall Result", "UNKNOWN")
            measurements = {}
            for row in rows[6:]:
                if len(row) < 8:
                    continue
                key = (row[0].strip(), row[1].strip(), row[2].strip())
                try:
                    measurements[key] = {
                        "qc_value":  float(row[5].strip()),
                        "reference": float(row[4].strip()),
                    }
                except ValueError:
                    pass
            reports.append({"datetime": dt, "overall": overall, "measurements": measurements})
        except Exception:
            continue
    return reports


# ---------------------------------------------------------------------------
# Visual design tokens
# ---------------------------------------------------------------------------

DARK       = "#170826"
BG         = "#f7f2ff"
PANEL      = "#fffaff"
PANEL_ALT  = "#f0e7ff"
BORDER     = "#dccbff"
TEXT       = "#241333"
MUTED      = "#7b6a8f"
MUTED_DARK = "#a99bc2"
GREEN      = "#16a36f"
RED        = "#e83d6f"
BLUE       = "#6d5dfb"
PURP       = "#8b5cf6"
PURP_DARK  = "#4c1d95"
MAGENTA    = "#d946ef"
STEEL      = "#4c3b68"
TEAL       = "#14b8a6"
ORANGE     = "#f59e0b"


def soften_panel(widget):
    widget.configure(bg=PANEL, highlightbackground=BORDER,
                     highlightcolor=BORDER, highlightthickness=1, bd=0)
    return widget


def polish_button(widget, bg=PURP, active=None):
    widget.configure(
        bg=bg,
        fg="white",
        activebackground=active or bg,
        activeforeground="white",
        relief="flat",
        bd=0,
        highlightthickness=0,
        cursor="hand2",
    )
    return widget


# ---------------------------------------------------------------------------
# Root application
# ---------------------------------------------------------------------------

class QCWizardApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("UPLC-MS QC Wizard — Cernak Lab")
        self.minsize(1140, 700)
        self.configure(bg=BG)

        try:
            if sys.platform == "win32":
                self.iconbitmap(resource_path("wizard_hat.ico"))
            else:
                self._window_icon = tk.PhotoImage(file=resource_path("wizard_hat.png"))
                self.iconphoto(True, self._window_icon)
        except (tk.TclError, OSError):
            pass

        self.qc_data        = None
        self.ref_data       = None
        self.qc_path_var    = tk.StringVar(value="(none)")
        self.instrument_var = tk.StringVar(value=INSTRUMENTS[0])
        self.ref_config     = load_ref_config()
        self.annotations    = load_annotations()

        self._setup_style()

        container = tk.Frame(self, bg=BG)
        container.pack(fill="both", expand=True)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self._pages = {}
        for Cls in (WelcomePage, InstrumentPage, HubPage,
                    SettingsPage, MainPage, TrendsPage, ReportPage):
            page = Cls(container, self)
            self._pages[Cls] = page
            page.grid(row=0, column=0, sticky="nsew")

        self._show(WelcomePage)

    def _setup_style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", font=("Segoe UI", 9), background=BG, foreground=TEXT)
        s.configure("Treeview", font=("Segoe UI", 9), rowheight=28,
                    background=PANEL, fieldbackground=PANEL,
                    foreground=TEXT, borderwidth=0, relief="flat")
        s.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"),
                    background=PURP_DARK, foreground="white", relief="flat",
                    padding=(8, 7))
        s.map("Treeview",
              background=[("selected", "#ede2ff")],
              foreground=[("selected", TEXT)])
        s.configure("TCombobox", fieldbackground=PANEL, background=PANEL_ALT,
                    foreground=TEXT, arrowcolor=PURP_DARK,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    padding=(8, 5))
        s.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(4, 4, 4, 0))
        s.configure("TNotebook.Tab", background=PANEL_ALT, foreground=TEXT,
                    padding=(18, 9), borderwidth=0)
        s.map("TNotebook.Tab",
              background=[("selected", PANEL), ("active", "#eadcff")],
              foreground=[("selected", PURP_DARK), ("active", PURP_DARK)])

    def _show(self, cls):
        self._pages[cls].tkraise()

    def go_to_instrument(self):
        self._show(InstrumentPage)

    def go_to_hub(self):
        instrument = self.instrument_var.get()
        self._pages[HubPage].enter(instrument)
        self._show(HubPage)

    def go_to_settings(self):
        instrument = self.instrument_var.get()
        self._pages[SettingsPage].enter(instrument)
        self._show(SettingsPage)

    def go_to_main(self):
        instrument = self.instrument_var.get()
        self._pages[MainPage].enter(instrument)
        if self.ref_data is None:
            messagebox.showwarning(
                "Reference File Not Set",
                "No reference file has been configured for this instrument.\n\n"
                "Please select a reference file in Settings before running QC."
            )
            self.go_to_settings()
            return
        self._show(MainPage)

    def go_to_trends(self):
        instrument = self.instrument_var.get()
        self._pages[TrendsPage].enter(instrument)
        self._show(TrendsPage)

    def go_to_report(self):
        instrument = self.instrument_var.get()
        self._pages[ReportPage].enter(instrument)
        self._show(ReportPage)


# ---------------------------------------------------------------------------
# Page 1 — Welcome
# ---------------------------------------------------------------------------

class WelcomePage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=DARK)
        self._app = app

        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)
        self.grid_columnconfigure(0, weight=1)

        tk.Label(self, text="Cernak Lab",
                 font=("Segoe UI", 16), bg=DARK, fg=MUTED_DARK
                 ).grid(row=1, column=0, pady=(0, 6))
        tk.Label(self, text="UPLC-MS QC Wizard",
                 font=("Segoe UI", 36, "bold"), bg=DARK, fg="white"
                 ).grid(row=2, column=0, pady=(0, 12))
        polish_button(
            tk.Button(self, text="  Next  ▶",
                      command=self._app.go_to_instrument,
                      font=("Segoe UI", 13, "bold"),
                      padx=30, pady=11),
            bg=PURP, active=PURP_DARK
        ).grid(row=3, column=0, pady=(90, 0))


# ---------------------------------------------------------------------------
# Page 2 — Instrument selection
# ---------------------------------------------------------------------------

class InstrumentPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self._app = app
        self._build()

    def _build(self):
        header = tk.Frame(self, bg=DARK, pady=14)
        header.pack(fill="x")
        tk.Label(header, text="UPLC-MS QC Wizard",
                 font=("Segoe UI", 17, "bold"), bg=DARK, fg="white").pack()
        tk.Label(header, text="Cernak Lab  •  Waters System",
                 font=("Segoe UI", 9), bg=DARK, fg=MUTED_DARK).pack()

        card = soften_panel(tk.Frame(self, padx=44, pady=34))
        card.pack(expand=True)

        tk.Label(card, text="Please choose your instrument",
                 font=("Segoe UI", 18, "bold"), bg=PANEL, fg=TEXT
                 ).pack(pady=(0, 32))

        ttk.Combobox(card, textvariable=self._app.instrument_var,
                     values=INSTRUMENTS, state="readonly",
                     width=22, font=("Segoe UI", 13)
                     ).pack(pady=(0, 40))

        polish_button(
            tk.Button(card, text="  Next  ▶",
                      command=self._next,
                      font=("Segoe UI", 12, "bold"),
                      padx=26, pady=10),
            bg=PURP, active=PURP_DARK
        ).pack()

    def _next(self):
        if not self._app.instrument_var.get():
            messagebox.showwarning("No Instrument",
                                    "Please select an instrument before continuing.")
            return
        self._app.go_to_hub()


# ---------------------------------------------------------------------------
# Page 3 — Hub
# ---------------------------------------------------------------------------

class HubPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self._app = app
        self._ilabel = None
        self._build()

    def enter(self, instrument: str):
        self._ilabel.config(text=instrument)

    def _build(self):
        header = tk.Frame(self, bg=DARK, pady=14)
        header.pack(fill="x")
        title_row = tk.Frame(header, bg=DARK)
        title_row.pack()
        tk.Label(title_row, text="UPLC-MS QC Wizard",
                 font=("Segoe UI", 17, "bold"), bg=DARK, fg="white"
                 ).pack(side="left", padx=(0, 16))
        polish_button(
            tk.Button(title_row, text="◀  Back",
                      command=lambda: self._app._show(InstrumentPage),
                      font=("Segoe UI", 9),
                      padx=10, pady=4),
            bg=STEEL, active=PURP_DARK
        ).pack(side="left")
        self._ilabel = tk.Label(header, text="",
                                 font=("Segoe UI", 12, "bold"), bg=DARK, fg=MUTED_DARK)
        self._ilabel.pack(pady=(4, 0))

        card = soften_panel(tk.Frame(self, padx=46, pady=36))
        card.pack(expand=True)

        tk.Label(card, text="What would you like to do?",
                 font=("Segoe UI", 15), bg=PANEL, fg=TEXT
                 ).pack(pady=(0, 36))

        btn_main = dict(font=("Segoe UI", 13, "bold"), relief="flat",
                        width=22, pady=16, cursor="hand2")
        btn_sec  = dict(font=("Segoe UI", 11), relief="flat",
                        width=22, pady=10, cursor="hand2")

        polish_button(tk.Button(card, text="Run QC",
                                command=self._app.go_to_main, **btn_main),
                      bg=PURP, active=PURP_DARK).pack(pady=8)
        polish_button(tk.Button(card, text="View Trends",
                                command=self._app.go_to_trends, **btn_main),
                      bg=BLUE, active="#5747db").pack(pady=8)
        polish_button(tk.Button(card, text="View Latest Report",
                                command=self._app.go_to_report, **btn_main),
                      bg=MAGENTA, active="#b832d1").pack(pady=8)

        tk.Frame(card, bg=BORDER, height=1).pack(fill="x", pady=(20, 12))

        polish_button(tk.Button(card, text="⚙  Settings",
                                command=self._app.go_to_settings, **btn_sec),
                      bg=STEEL, active=PURP_DARK).pack()


# ---------------------------------------------------------------------------
# Page 4 — Settings
# ---------------------------------------------------------------------------

class SettingsPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self._app        = app
        self._instrument = ""
        self._ref_path_var    = tk.StringVar(value="(not set)")
        self._ref_status_var  = tk.StringVar(value="")
        self._dir_path_var    = tk.StringVar(value="(not set)")
        self._dir_status_var  = tk.StringVar(value="")
        self._build()

    def enter(self, instrument: str):
        self._instrument = instrument
        self._instrument_label.config(text=instrument)
        self._refresh_display()

    def _refresh_display(self):
        # Reference file
        ref = cfg_get_ref(self._app.ref_config, self._instrument)
        if ref and os.path.isfile(ref):
            self._ref_path_var.set(ref)
            self._ref_status_var.set("remembered ✓")
        else:
            self._ref_path_var.set("(not set)")
            self._ref_status_var.set("")

        # Report directory
        rdir = cfg_get_report_dir(self._app.ref_config, self._instrument)
        if rdir and os.path.isdir(rdir):
            self._dir_path_var.set(rdir)
            self._dir_status_var.set("remembered ✓")
        else:
            self._dir_path_var.set("(not set)")
            self._dir_status_var.set("")

    def _build(self):
        # Header
        header = tk.Frame(self, bg=DARK, pady=10)
        header.pack(fill="x")
        title_row = tk.Frame(header, bg=DARK)
        title_row.pack()
        tk.Label(title_row, text="Settings",
                 font=("Segoe UI", 17, "bold"), bg=DARK, fg="white"
                 ).pack(side="left", padx=(0, 16))
        polish_button(
            tk.Button(title_row, text="◀  Back",
                      command=lambda: self._app._show(HubPage),
                      font=("Segoe UI", 9),
                      padx=10, pady=4),
            bg=STEEL, active=PURP_DARK
        ).pack(side="left")
        self._instrument_label = tk.Label(header, text="",
                                           font=("Segoe UI", 11, "bold"),
                                           bg=DARK, fg=MUTED_DARK)
        self._instrument_label.pack(pady=(4, 0))

        # Scrollable card area
        card = tk.Frame(self, bg=BG)
        card.pack(expand=True, fill="x", padx=60)

        # ── Reference File ─────────────────────────────────────────────
        self._build_section(
            parent       = card,
            title        = "Reference File",
            description  = ("The baseline CSV used for all QC comparisons.\n"
                            "Each instrument stores its own reference independently."),
            path_var     = self._ref_path_var,
            status_var   = self._ref_status_var,
            browse_label = "Browse / Change Reference File",
            browse_cmd   = self._browse_ref,
            top_pad      = 24,
        )

        # ── Report Save Location ───────────────────────────────────────
        self._build_section(
            parent       = card,
            title        = "Report Save Location",
            description  = ("Folder where exported QC reports (CSV) will be saved automatically.\n"
                            "Each instrument stores its own report folder independently."),
            path_var     = self._dir_path_var,
            status_var   = self._dir_status_var,
            browse_label = "Browse / Change Report Folder",
            browse_cmd   = self._browse_report_dir,
            top_pad      = 16,
        )

    def _build_section(self, parent, title, description,
                       path_var, status_var, browse_label, browse_cmd, top_pad):
        lf = tk.LabelFrame(parent, text=f"  {title}  ",
                            font=("Segoe UI", 10, "bold"),
                            bg=PANEL, fg=PURP_DARK,
                            padx=20, pady=14,
                            highlightbackground=BORDER,
                            highlightcolor=BORDER,
                            highlightthickness=1,
                            bd=0)
        lf.pack(fill="x", pady=(top_pad, 0))

        tk.Label(lf, text=description,
                 font=("Segoe UI", 9), bg=PANEL, fg=MUTED,
                 justify="left").pack(anchor="w", pady=(0, 12))

        path_row = tk.Frame(lf, bg=PANEL)
        path_row.pack(fill="x", pady=(0, 4))
        tk.Label(path_row, text="Current:",
                 font=("Segoe UI", 9, "bold"), bg=PANEL, fg=TEXT,
                 width=10, anchor="e").pack(side="left")
        tk.Label(path_row, textvariable=path_var,
                 font=("Segoe UI", 9), bg=PANEL, fg=TEXT,
                 anchor="w", wraplength=580, justify="left"
                 ).pack(side="left", padx=(8, 0))

        status_row = tk.Frame(lf, bg=PANEL)
        status_row.pack(fill="x", pady=(0, 12))
        tk.Label(status_row, text="", bg=PANEL, width=10).pack(side="left")
        tk.Label(status_row, textvariable=status_var,
                 font=("Segoe UI", 9, "italic"), bg=PANEL, fg=TEAL
                 ).pack(side="left", padx=(8, 0))

        polish_button(
            tk.Button(lf, text=f"  {browse_label}  ",
                      command=browse_cmd,
                      font=("Segoe UI", 10),
                      padx=12, pady=8),
            bg=PURP_DARK, active=PURP
        ).pack(anchor="w")

    def _browse_ref(self):
        path = filedialog.askopenfilename(
            title="Select Reference CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            df = read_qc_csv(path)
            if df.empty:
                messagebox.showwarning("Empty File",
                                        "The selected CSV contains no data rows.")
                return
            self._app.ref_data = df
            abs_path = os.path.abspath(path)
            cfg_set_ref(self._app.ref_config, self._instrument, abs_path)
            save_ref_config(self._app.ref_config)
            self._ref_path_var.set(abs_path)
            self._ref_status_var.set("saved ✓")
            messagebox.showinfo("Saved",
                                 f"Reference file for {self._instrument} saved.\n\n"
                                 f"{os.path.basename(path)}")
        except Exception as exc:
            messagebox.showerror("Read Error", f"Cannot read file:\n{exc}")

    def _browse_report_dir(self):
        directory = filedialog.askdirectory(title="Select Report Save Folder")
        if not directory:
            return
        abs_dir = os.path.abspath(directory)
        cfg_set_report_dir(self._app.ref_config, self._instrument, abs_dir)
        save_ref_config(self._app.ref_config)
        self._dir_path_var.set(abs_dir)
        self._dir_status_var.set("saved ✓")
        messagebox.showinfo("Saved",
                             f"Report folder for {self._instrument} saved.\n\n{abs_dir}")


# ---------------------------------------------------------------------------
# Page 5 — Main QC comparison
# ---------------------------------------------------------------------------

class MainPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self._app         = app
        self._instrument  = ""
        self._last_results   = []   # list of row-dicts from last run
        self._last_qc_file   = ""
        self._overall_pass   = None
        self._build()

    # ── enter ──────────────────────────────────────────────────────────
    def enter(self, instrument: str):
        self._instrument = instrument
        self._instrument_label.config(text=instrument)

        self._app.qc_data = None
        self._app.qc_path_var.set("(none)")

        # Auto-load reference
        saved_path = cfg_get_ref(self._app.ref_config, instrument)
        if saved_path and os.path.isfile(saved_path):
            try:
                df = read_qc_csv(saved_path)
                self._app.ref_data = df if not df.empty else None
            except Exception:
                self._app.ref_data = None
                cfg_set_ref(self._app.ref_config, instrument, "")
                save_ref_config(self._app.ref_config)
        else:
            self._app.ref_data = None

        self._reset_result()

    # ── UI ─────────────────────────────────────────────────────────────
    def _build(self):
        # Header
        header = tk.Frame(self, bg=DARK, pady=10)
        header.pack(fill="x")
        title_row = tk.Frame(header, bg=DARK)
        title_row.pack()
        tk.Label(title_row, text="UPLC-MS QC Wizard",
                 font=("Segoe UI", 17, "bold"), bg=DARK, fg="white"
                 ).pack(side="left", padx=(0, 16))
        polish_button(
            tk.Button(title_row, text="◀  Back",
                      command=lambda: self._app._show(HubPage),
                      font=("Segoe UI", 9),
                      padx=10, pady=4),
            bg=STEEL, active=PURP_DARK
        ).pack(side="left")
        self._instrument_label = tk.Label(header, text="",
                                           font=("Segoe UI", 11, "bold"),
                                           bg=DARK, fg=MUTED_DARK)
        self._instrument_label.pack(pady=(4, 0))

        # QC file row
        file_lf = tk.LabelFrame(self, text="  Files  ",
                                  font=("Segoe UI", 9, "bold"),
                                  bg=PANEL, fg=PURP_DARK,
                                  padx=12, pady=8,
                                  highlightbackground=BORDER,
                                  highlightcolor=BORDER,
                                  highlightthickness=1,
                                  bd=0)
        file_lf.pack(fill="x", padx=14, pady=(10, 4))
        qc_row = tk.Frame(file_lf, bg=PANEL)
        qc_row.pack(fill="x", pady=3)
        polish_button(
            tk.Button(qc_row, text="  Browse QC File  ",
                      command=self._load_qc,
                      font=("Segoe UI", 9),
                      pady=6),
            bg=PURP_DARK, active=PURP
        ).pack(side="left")
        tk.Label(qc_row, text="QC File:",
                 font=("Segoe UI", 9, "bold"),
                 bg=PANEL, fg=TEXT, width=10, anchor="e").pack(side="left", padx=(10, 4))
        tk.Label(qc_row, textvariable=self._app.qc_path_var,
                 font=("Segoe UI", 9), bg=PANEL, fg=TEXT,
                 anchor="w").pack(side="left")

        # Action bar
        action_frame = tk.Frame(self, bg=BG, pady=8)
        action_frame.pack(fill="x", padx=14)

        polish_button(
            tk.Button(action_frame, text="   ▶   Run Comparison   ",
                      command=self._run_comparison,
                      font=("Segoe UI", 11, "bold"),
                      pady=9),
            bg=PURP, active=PURP_DARK
        ).pack(side="left")

        # Export button — disabled until a comparison has been run
        self._export_btn = tk.Button(action_frame,
                                      text="  ⬇  Export Report  ",
                                      command=self._export,
                                      font=("Segoe UI", 11, "bold"),
                                      pady=9,
                                      state="disabled")
        polish_button(self._export_btn, bg=ORANGE, active="#d97706")
        self._export_btn.pack(side="left", padx=(12, 0))

        status_row = tk.Frame(action_frame, bg=BG)
        status_row.pack(side="left", padx=22)
        self._indicator = tk.Canvas(status_row, width=38, height=38,
                                     bg=BG, highlightthickness=0)
        self._indicator.pack(side="left")
        self._status_label = tk.Label(status_row,
                                       text="—  Load QC file and run comparison",
                                       font=("Segoe UI", 11), bg=BG, fg=MUTED)
        self._status_label.pack(side="left", padx=10)
        self._draw_indicator("#aaaaaa")

        # Results tree
        results_lf = tk.LabelFrame(self, text="  Results  ",
                                    font=("Segoe UI", 9, "bold"),
                                    bg=PANEL, fg=PURP_DARK,
                                    padx=6, pady=6,
                                    highlightbackground=BORDER,
                                    highlightcolor=BORDER,
                                    highlightthickness=1,
                                    bd=0)
        results_lf.pack(fill="both", expand=True, padx=14, pady=(4, 4))

        cols    = ("channel", "compound", "parameter", "criterion",
                   "reference", "qc_value", "delta", "status")
        headers = ("Channel",  "Compound",  "Parameter",  "Criterion",
                   "Reference", "QC Value", "Δ",     "Status")
        widths  = (110, 135, 125, 95, 115, 115, 100, 72)

        self.tree = ttk.Treeview(results_lf, columns=cols,
                                  show="headings", selectmode="none")
        for col, hdr, w in zip(cols, headers, widths):
            self.tree.heading(col, text=hdr)
            self.tree.column(col, width=w, anchor="center", minwidth=60)

        self.tree.tag_configure("pass",    background="#dcfce7", foreground="#14532d")
        self.tree.tag_configure("fail",    background="#ffe4ec", foreground="#881337")
        self.tree.tag_configure("section", background=PANEL_ALT, foreground=TEXT,
                                 font=("Segoe UI", 9, "bold"))

        vsb = ttk.Scrollbar(results_lf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)

        # Legend
        legend = tk.Frame(self, bg=BG)
        legend.pack(fill="x", padx=14, pady=(2, 8))
        for color, text in [("#dcfce7", " PASS "), ("#fee2e2", " FAIL ")]:
            tk.Label(legend, text=text, bg=color,
                     font=("Segoe UI", 8), relief="groove",
                     padx=4).pack(side="left", padx=(0, 6))
        tk.Label(legend,
                 text="Δ = QC − Reference  (Area/Height shown as %Δ)",
                 font=("Segoe UI", 8), bg=BG, fg=MUTED).pack(side="left", padx=10)

    # ── helpers ────────────────────────────────────────────────────────
    def _draw_indicator(self, color):
        self._indicator.delete("all")
        self._indicator.create_oval(3, 3, 35, 35, fill="#eadcff", outline="")
        self._indicator.create_oval(5, 5, 33, 33, fill=color, outline="")

    def _reset_result(self):
        self._draw_indicator("#aaaaaa")
        self._status_label.config(
            text="—  Load QC file and run comparison",
            font=("Segoe UI", 11), fg=MUTED)
        self._export_btn.config(state="disabled")
        self._last_results = []
        self._overall_pass = None
        for item in self.tree.get_children():
            self.tree.delete(item)

    def _load_qc(self):
        path = filedialog.askopenfilename(
            title="Select QC CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            df = read_qc_csv(path)
            if df.empty:
                messagebox.showwarning("Empty File",
                                        "The selected CSV contains no data rows.")
                return
            self._app.qc_data = df
            self._last_qc_file = os.path.basename(path)
            self._app.qc_path_var.set(self._last_qc_file)
            self._reset_result()
        except Exception as exc:
            messagebox.showerror("Read Error", f"Cannot read file:\n{exc}")

    # ── comparison ─────────────────────────────────────────────────────
    def _run_comparison(self):
        if self._app.qc_data is None:
            messagebox.showwarning("No QC File", "Please load the QC file first.")
            return

        for item in self.tree.get_children():
            self.tree.delete(item)
        self._last_results = []

        ref_row = self._app.ref_data.iloc[0]
        qc_row  = self._app.qc_data.iloc[0]
        all_pass        = True
        current_channel = None

        for comp in build_comparisons(self._instrument):
            channel     = comp["channel"]
            ch_disp     = CHANNEL_DISPLAY.get(channel, channel)
            col         = comp["column"]

            if ch_disp != current_channel:
                current_channel = ch_disp
                self.tree.insert("", "end",
                                  values=(ch_disp, "", "", "", "", "", "", ""),
                                  tags=("section",))

            ref_missing = col not in ref_row.index or pd.isna(ref_row[col])
            qc_missing  = col not in qc_row.index  or pd.isna(qc_row[col])

            if ref_missing or qc_missing:
                passed   = False
                ref_disp = "—" if ref_missing else f"{float(ref_row[col]):.4f}"
                qc_disp  = "—" if qc_missing  else f"{float(qc_row[col]):.4f}"
                delta    = "missing col"
            else:
                passed, delta = evaluate(ref_row[col], qc_row[col],
                                          comp["threshold"], comp["method"])
                try:
                    ref_disp = f"{float(ref_row[col]):.4f}"
                    qc_disp  = f"{float(qc_row[col]):.4f}"
                except Exception:
                    ref_disp = str(ref_row[col])
                    qc_disp  = str(qc_row[col])

            if not passed:
                all_pass = False

            row_data = {
                "channel":   ch_disp,
                "compound":  comp["compound"],
                "parameter": comp["parameter"],
                "criterion": comp["criterion"],
                "reference": ref_disp,
                "qc_value":  qc_disp,
                "delta":     delta,
                "status":    "PASS" if passed else "FAIL",
            }
            self._last_results.append(row_data)
            self.tree.insert("", "end", values=(
                ch_disp, comp["compound"], comp["parameter"],
                comp["criterion"], ref_disp, qc_disp, delta,
                "PASS" if passed else "FAIL"
            ), tags=("pass" if passed else "fail",))

        self._overall_pass = all_pass
        self._export_btn.config(state="normal")

        if all_pass:
            self._draw_indicator(GREEN)
            self._status_label.config(text="  ✔  ALL PASS",
                                       font=("Segoe UI", 13, "bold"), fg="#14532d")
        else:
            self._draw_indicator(RED)
            self._status_label.config(text="  ✘  FAIL — check highlighted rows",
                                       font=("Segoe UI", 13, "bold"), fg="#881337")
            self._show_failure_advisory()

    def _show_failure_advisory(self):
        failed = [r for r in self._last_results if r["status"] == "FAIL"]

        # Each section is a list of (text, url_or_None) tuples
        sections = []

        if any(r["delta"] == "missing col" for r in failed):
            sections.append([
                ("Please check input file contains the correct information.", None),
            ])

        if any(r["parameter"] == "Retention Time" and r["delta"] != "missing col"
               for r in failed):
            sections.append([
                ("Retention Time failure — please consult the Waters manual:\n", None),
                ("  https://support.waters.com/KB_Inst/Chromatography/WKB92120_Retention_time_shift_on_I-Class",
                 "https://support.waters.com/KB_Inst/Chromatography/WKB92120_Retention_time_shift_on_I-Class"),
                ("\n", None),
                ("  https://support.waters.com/KB_Inst/Chromatography/WKB90951_Retention_time_shift_for_particular_analytes",
                 "https://support.waters.com/KB_Inst/Chromatography/WKB90951_Retention_time_shift_for_particular_analytes"),
            ])

        if any(r["channel"] in ("Positive Ionization", "Negative Ionization")
               and r["delta"] != "missing col"
               for r in failed):
            url = ("https://support.waters.com/KB_Inst/Chromatography/WKB241564_ACQUITY_QDa_Mass_Detector"
                   "_ACQUITY_UPLC_H-Class_Actions_that_can_be_taken_by_the_user_when_the_area_values_vary"
                   "_Poor_reproducibility")
            sections.append([
                ("Ionization failure — please consult the QDa cleaning procedure in the protocol or the Waters manual:\n", None),
                (f"  {url}", url),
            ])

        if any(r["parameter"] in ("Asymmetry", "Tailing Factor")
               and r["delta"] != "missing col"
               for r in failed):
            sections.append([
                ("Tailing / Asymmetry failure — please check the column.", None),
            ])

        if not sections:
            return

        # ── build custom dialog ────────────────────────────────────────────
        dlg = tk.Toplevel(self)
        dlg.title("QC Failed — Action Required")
        dlg.resizable(False, False)
        dlg.grab_set()

        # Warning header
        hdr = tk.Frame(dlg, bg=RED, pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text="  ✘  QC FAILED", bg=RED, fg="white",
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=12)

        # Scrollable text area — keep state="normal" so tag clicks work on Windows;
        # block keyboard input instead of disabling the widget.
        txt = tk.Text(dlg, wrap="word", width=72, height=14,
                      relief="flat", bg=PANEL, fg=TEXT, padx=14, pady=10,
                      font=("Segoe UI", 9), cursor="arrow",
                      state="normal")
        txt.pack(fill="both", expand=True, padx=0, pady=0)
        txt.bind("<Key>", lambda e: "break")
        txt.bind("<Button-2>", lambda e: "break")
        txt.bind("<Button-3>", lambda e: "break")

        txt.tag_config("bullet", font=("Segoe UI", 9, "bold"))

        for i, section in enumerate(sections):
            if i:
                txt.insert("end", "\n")
            txt.insert("end", "• ", "bullet")
            for text, url in section:
                if url:
                    tag = f"url_{i}_{i}"
                    txt.tag_config(tag, foreground=BLUE, underline=True)
                    txt.tag_bind(tag, "<Button-1>", lambda e, u=url: webbrowser.open(u))
                    txt.tag_bind(tag, "<Enter>",
                                 lambda e, t=tag: (txt.tag_config(t, foreground=PURP_DARK),
                                                   txt.config(cursor="hand2")))
                    txt.tag_bind(tag, "<Leave>",
                                 lambda e, t=tag: (txt.tag_config(t, foreground=BLUE),
                                                   txt.config(cursor="arrow")))
                    txt.insert("end", text, tag)
                else:
                    txt.insert("end", text)

        # OK button
        btn_frame = tk.Frame(dlg, bg=BG, pady=8)
        btn_frame.pack(fill="x")
        polish_button(
            tk.Button(btn_frame, text="OK", width=10, command=dlg.destroy,
                      font=("Segoe UI", 9, "bold")),
            bg=PURP_DARK, active=PURP
        ).pack()

        dlg.update_idletasks()
        # Center over parent
        pw, ph = self.winfo_width(), self.winfo_height()
        px, py = self.winfo_rootx(), self.winfo_rooty()
        dw, dh = dlg.winfo_width(), dlg.winfo_height()
        dlg.geometry(f"+{px + (pw - dw)//2}+{py + (ph - dh)//2}")
        dlg.wait_window()

    # ── export ─────────────────────────────────────────────────────────
    def _export(self):
        report_dir = cfg_get_report_dir(self._app.ref_config, self._instrument)
        if not report_dir or not os.path.isdir(report_dir):
            messagebox.showwarning(
                "Report Folder Not Set",
                "No report save folder has been configured for this instrument.\n\n"
                "Please set the report folder in Settings."
            )
            self._app.go_to_settings()
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename  = f"{self._instrument}_QC_{timestamp}.csv"
        filepath  = os.path.join(report_dir, filename)

        try:
            with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                # Metadata block
                write_safe_csv_row(w, ["Instrument",     self._instrument])
                write_safe_csv_row(w, ["Date / Time",    datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
                write_safe_csv_row(w, ["QC File",        self._last_qc_file])
                write_safe_csv_row(w, ["Overall Result", "PASS" if self._overall_pass else "FAIL"])
                w.writerow([])
                # Results table
                write_safe_csv_row(w, ["Channel", "Compound", "Parameter", "Criterion",
                                       "Reference", "QC Value", "Delta", "Status"])
                for r in self._last_results:
                    write_safe_csv_row(w, [r["channel"], r["compound"], r["parameter"],
                                           r["criterion"], r["reference"], r["qc_value"],
                                           r["delta"], r["status"]])

            messagebox.showinfo("Export Successful",
                                 f"Report saved to:\n{filepath}")
        except Exception as exc:
            messagebox.showerror("Export Failed", f"Could not write file:\n{exc}")


# ---------------------------------------------------------------------------
# Page 6 — Trends
# ---------------------------------------------------------------------------

# Metric tab config: (tab label, parameter name in CSV, threshold, method)
METRIC_TABS = [
    ("Ret. Time",   "Retention Time", 0.1,  "absolute"),
    ("Area",        "Area",           15.0, "percent"),
    ("Peak Height", "Peak Height",    15.0, "percent"),
    ("Asymmetry",   "Asymmetry",      2.0,  "absolute"),
    ("Tailing",     "Tailing Factor", 2.0,  "absolute"),
]

COMPOUND_COLORS = {
    "Caffeine":          BLUE,
    "Sulfadoxine":       MAGENTA,
    "Trimethoxybenzene": TEAL,
}

CHANNEL_DISPLAY = {
    "MS TIC(+)": "Positive Ionization",
    "MS TIC(-)": "Negative Ionization",
    "TWC":       "UV Absorption",
}

CHANNELS = [CHANNEL_DISPLAY["MS TIC(+)"],
            CHANNEL_DISPLAY["TWC"],
            CHANNEL_DISPLAY["MS TIC(-)"],]


class TrendsPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self._app        = app
        self._instrument = ""
        self._ilabel         = None
        # per-metric-tab state
        self._channel_vars   = {}   # tab_label -> StringVar
        self._channel_combos = {}   # tab_label -> ttk.Combobox
        self._metric_plots   = {}   # tab_label -> (Figure, Axes, FigureCanvasTkAgg)
        self._overall_plot   = None # (Figure, Axes, FigureCanvasTkAgg)
        self._build()

    def enter(self, instrument: str):
        self._instrument = instrument
        self._ilabel.config(text=instrument)
        self._update_channel_combos()
        self._refresh_all()


    def _update_channel_combos(self):
        """Restrict channel dropdown to channels available for the current instrument."""
        internal  = INSTRUMENT_CHANNELS.get(self._instrument,
                                              [ch for ch, _ in CHANNELS_COMPOUNDS])
        available = [CHANNEL_DISPLAY.get(ch, ch) for ch in internal]
        for label, combo in self._channel_combos.items():
            combo.config(values=available)
            var = self._channel_vars[label]
            if var.get() not in available:
                var.set(available[0])
    # ── UI construction ────────────────────────────────────────────────
    def _build(self):
        # Header
        header = tk.Frame(self, bg=DARK, pady=10)
        header.pack(fill="x")
        title_row = tk.Frame(header, bg=DARK)
        title_row.pack()
        tk.Label(title_row, text="View Trends",
                 font=("Segoe UI", 17, "bold"), bg=DARK, fg="white"
                 ).pack(side="left", padx=(0, 16))
        polish_button(
            tk.Button(title_row, text="◀  Back",
                      command=lambda: self._app._show(HubPage),
                      font=("Segoe UI", 9),
                      padx=10, pady=4),
            bg=STEEL, active=PURP_DARK
        ).pack(side="left")
        polish_button(
            tk.Button(title_row, text="⟳  Refresh",
                      command=self._refresh_all,
                      font=("Segoe UI", 9),
                      padx=10, pady=4),
            bg=STEEL, active=PURP_DARK
        ).pack(side="left", padx=(8, 0))
        polish_button(
            tk.Button(title_row, text="📝  Notes",
                      command=self._open_notes_dialog,
                      font=("Segoe UI", 9),
                      padx=10, pady=4),
            bg=ORANGE, active="#d97706"
        ).pack(side="left", padx=(8, 0))
        self._ilabel = tk.Label(header, text="",
                                 font=("Segoe UI", 11, "bold"), bg=DARK, fg=MUTED_DARK)
        self._ilabel.pack(pady=(4, 0))

        if not HAS_MPL:
            tk.Label(self,
                     text="matplotlib is required for trend charts.\n"
                          "Please install it:  pip install matplotlib",
                     font=("Segoe UI", 12), bg=BG, fg=RED
                     ).pack(expand=True)
            return

        # Notebook
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=12, pady=(8, 8))
        self._notebook = nb

        # Tab 1 — Overall Results (all instruments)
        self._overall_plot = self._add_chart_tab(nb, "Overall Results",
                                                   has_channel_selector=False)

        # Tabs 2-6 — individual metrics
        for tab_label, param, threshold, method in METRIC_TABS:
            result = self._add_chart_tab(nb, tab_label, has_channel_selector=True)
            self._metric_plots[tab_label] = result

    def _add_chart_tab(self, notebook, label, has_channel_selector):
        """Create a notebook tab with optional channel selector + matplotlib canvas.
           Returns (Figure, Axes, FigureCanvasTkAgg)."""
        frame = tk.Frame(notebook, bg=BG)
        notebook.add(frame, text=f"  {label}  ")

        if has_channel_selector:
            ctrl = soften_panel(tk.Frame(frame, padx=12, pady=8))
            ctrl.pack(fill="x", padx=10, pady=(6, 2))
            tk.Label(ctrl, text="Channel:", font=("Segoe UI", 9, "bold"),
                     bg=PANEL, fg=TEXT).pack(side="left")
            var = tk.StringVar(value=CHANNELS[0])
            self._channel_vars[label] = var
            combo = ttk.Combobox(ctrl, textvariable=var, values=CHANNELS,
                                  state="readonly", width=20)
            combo.pack(side="left", padx=(6, 0))
            combo.bind("<<ComboboxSelected>>",
                       lambda e, lbl=label: self._plot_metric(lbl))
            self._channel_combos[label] = combo

        fig = Figure(facecolor=BG)
        ax  = fig.add_subplot(111)
        ax.set_facecolor(PANEL)
        canvas = FigureCanvasTkAgg(fig, master=frame)
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=4, pady=(2, 4))
        return (fig, ax, canvas)

    # ── refresh ────────────────────────────────────────────────────────
    def _refresh_all(self):
        if not HAS_MPL or not self._instrument:
            return
        self._plot_overall()
        for tab_label, *_ in METRIC_TABS:
            self._plot_metric(tab_label)

    # ── Overall Results chart ──────────────────────────────────────────
    def _plot_overall(self):
        fig, ax, canvas = self._overall_plot
        ax.clear()
        ax.set_facecolor(PANEL)

        reports = load_instrument_reports(self._instrument, self._app.ref_config)

        if not reports:
            ax.text(0.5, 0.5,
                    f"No reports found for {self._instrument}.\nExport QC results first.",
                    ha="center", va="center", transform=ax.transAxes,
                    fontsize=11, color=MUTED)
        else:
            dates  = [r["datetime"] for r in reports]
            yvals  = [1 if r["overall"] == "PASS" else 0 for r in reports]
            colors = [GREEN if y == 1 else RED for y in yvals]

            ax.scatter(dates, yvals, c=colors, s=80, zorder=5)
            ax.step(dates, yvals, where="mid", color=MUTED_DARK,
                    linewidth=1.0, zorder=3, linestyle="--")

            ax.set_yticks([0, 1])
            ax.set_yticklabels(["FAIL", "PASS"])
            ax.set_ylim(-0.4, 1.4)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            fig.autofmt_xdate(rotation=30, ha="right")
            ax.grid(True, axis="x", linestyle="--", alpha=0.4)

            from matplotlib.lines import Line2D
            ax.legend(handles=[
                Line2D([0],[0], marker="o", color="w", markerfacecolor=GREEN, markersize=8, label="PASS"),
                Line2D([0],[0], marker="o", color="w", markerfacecolor=RED, markersize=8, label="FAIL"),
            ], loc="upper right", fontsize=9)

        ax.set_title(f"Overall QC Results — {self._instrument}", pad=8)
        ax.set_xlabel("Date")
        ax.tick_params(colors=TEXT)
        for spine in ax.spines.values():
            spine.set_color(BORDER)
        self._draw_annotations(ax)
        fig.tight_layout(pad=1.8)
        canvas.draw()

    # ── Individual metric chart ────────────────────────────────────────
    def _plot_metric(self, tab_label):
        if tab_label not in self._metric_plots:
            return
        fig, ax, canvas = self._metric_plots[tab_label]
        channel = self._channel_vars[tab_label].get()

        ax.clear()
        ax.set_facecolor(PANEL)

        # Find the matching metric spec
        spec = next((s for s in METRIC_TABS if s[0] == tab_label), None)
        if spec is None:
            return
        _, param, threshold, method = spec

        reports = load_instrument_reports(self._instrument, self._app.ref_config)
        if not reports:
            ax.text(0.5, 0.5, f"No reports found for {self._instrument}.\nExport QC results first.",
                    ha="center", va="center", transform=ax.transAxes,
                    fontsize=11, color=MUTED)
            ax.set_title(f"{param}  |  {channel}  |  {self._instrument}", pad=8)
            canvas.draw()
            return

        compounds = ["Sulfadoxine"] if channel == CHANNEL_DISPLAY["MS TIC(-)"] else \
                    ["Caffeine", "Sulfadoxine", "Trimethoxybenzene"]

        plotted = False
        for compound in compounds:
            key = (channel, compound, param)
            dates, values, refs = [], [], []
            for r in reports:
                m = r["measurements"].get(key)
                if m is not None:
                    dates.append(r["datetime"])
                    values.append(m["qc_value"])
                    refs.append(m["reference"])

            if not values:
                continue
            plotted = True
            color = COMPOUND_COLORS.get(compound, MUTED)

            # Main trend line
            ax.plot(dates, values, color=color, linewidth=1.6,
                    label=compound, zorder=4, alpha=0.85)

            # Markers coloured by PASS/FAIL
            for dt, val, ref in zip(dates, values, refs):
                if method == "percent":
                    ok = (abs((val - ref) / ref * 100) <= threshold) if ref != 0 else False
                else:
                    ok = abs(val - ref) <= threshold
                ax.scatter(dt, val, s=55, zorder=5,
                           color=GREEN if ok else RED,
                           edgecolors=color, linewidths=0.8)

            # Reference line + threshold band (using mean reference)
            if refs:
                ref_mean = sum(refs) / len(refs)
                if method == "percent":
                    upper = ref_mean * (1 + threshold / 100)
                    lower = ref_mean * (1 - threshold / 100)
                else:
                    upper = ref_mean + threshold
                    lower = ref_mean - threshold
                ax.axhline(ref_mean, color=color, linestyle=":",
                           linewidth=1.0, alpha=0.55, zorder=2)
                ax.axhspan(lower, upper, color=color, alpha=0.06, zorder=1)

        if not plotted:
            ax.text(0.5, 0.5, f"No data available for\n{param}  /  {channel}",
                    ha="center", va="center", transform=ax.transAxes,
                    fontsize=11, color=MUTED)
        else:
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            fig.autofmt_xdate(rotation=30, ha="right")
            ax.legend(loc="upper right", fontsize=9, framealpha=0.85)
            ax.grid(True, linestyle="--", alpha=0.35)

        crit = f"±{threshold}{'%' if method == 'percent' else ''}"
        ax.set_title(f"{param}  |  {channel}  |  {self._instrument}   [{crit}]", pad=8)
        ax.set_xlabel("Date")
        ax.tick_params(colors=TEXT)
        for spine in ax.spines.values():
            spine.set_color(BORDER)
        self._draw_annotations(ax)
        fig.tight_layout(pad=1.8)
        canvas.draw()

    # ── Annotations (chart markers) ────────────────────────────────────
    def _get_annotations(self) -> list:
        return annotations_for(self._app.annotations, self._instrument)

    def _draw_annotations(self, ax):
        anns = self._get_annotations()
        if not anns:
            return
        for ann in anns:
            try:
                dt = datetime.strptime(ann.get("date", ""), "%Y-%m-%d")
            except ValueError:
                continue
            ax.axvline(dt, color=ORANGE, linestyle="--",
                       linewidth=1.3, alpha=0.7, zorder=2)
            label = ann.get("label", "") or ""
            if label:
                ax.text(dt, 0.985, f" {label} ",
                        transform=ax.get_xaxis_transform(),
                        rotation=90, ha="right", va="top",
                        fontsize=8, color="#7a3f00",
                        fontweight="bold",
                        bbox=dict(facecolor="#fff3d6", edgecolor=ORANGE,
                                  linewidth=0.6, boxstyle="round,pad=0.2",
                                  alpha=0.9))

    # ── Notes dialog ───────────────────────────────────────────────────
    def _open_notes_dialog(self):
        if not self._instrument:
            return

        dlg = tk.Toplevel(self)
        dlg.title(f"Annotations — {self._instrument}")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.grab_set()

        # Header
        hdr = tk.Frame(dlg, bg=DARK, pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text=f"📝  Chart Annotations — {self._instrument}",
                 bg=DARK, fg="white",
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=12)

        body = tk.Frame(dlg, bg=BG, padx=14, pady=12)
        body.pack(fill="both", expand=True)

        # ── Existing annotations list ────────────────────────────────
        list_lf = tk.LabelFrame(body, text="  Existing  ",
                                 font=("Segoe UI", 9, "bold"),
                                 bg=PANEL, fg=PURP_DARK,
                                 padx=8, pady=8,
                                 highlightbackground=BORDER,
                                 highlightcolor=BORDER,
                                 highlightthickness=1, bd=0)
        list_lf.pack(fill="x", pady=(0, 12))

        cols = ("date", "label", "note")
        tree = ttk.Treeview(list_lf, columns=cols, show="headings",
                             height=7, selectmode="browse")
        for col, hdr_txt, w in zip(cols,
                                    ("Date", "Label", "Note"),
                                    (100, 180, 320)):
            tree.heading(col, text=hdr_txt)
            tree.column(col, width=w, anchor="w", minwidth=60)
        vsb = ttk.Scrollbar(list_lf, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        tree.pack(fill="x", expand=True)

        def _reload_list():
            for iid in tree.get_children():
                tree.delete(iid)
            anns = sorted(self._get_annotations(),
                          key=lambda a: a.get("date", ""))
            for i, a in enumerate(anns):
                tree.insert("", "end", iid=str(i),
                             values=(a.get("date", ""),
                                     a.get("label", ""),
                                     a.get("note", "")))

        _reload_list()

        del_row = tk.Frame(list_lf, bg=PANEL)
        del_row.pack(fill="x", pady=(6, 0))

        def _delete_selected():
            sel = tree.selection()
            if not sel:
                return
            idx = int(sel[0])
            anns = sorted(self._get_annotations(),
                          key=lambda a: a.get("date", ""))
            if 0 <= idx < len(anns):
                target = anns[idx]
                lst = self._get_annotations()
                try:
                    lst.remove(target)
                except ValueError:
                    return
                save_annotations(self._app.annotations)
                _reload_list()
                self._refresh_all()

        polish_button(
            tk.Button(del_row, text="🗑  Delete Selected",
                      command=_delete_selected,
                      font=("Segoe UI", 9), padx=10, pady=4),
            bg=RED, active="#b8264f"
        ).pack(side="right")

        # ── Add-new form ─────────────────────────────────────────────
        add_lf = tk.LabelFrame(body, text="  Add New  ",
                                font=("Segoe UI", 9, "bold"),
                                bg=PANEL, fg=PURP_DARK,
                                padx=10, pady=10,
                                highlightbackground=BORDER,
                                highlightcolor=BORDER,
                                highlightthickness=1, bd=0)
        add_lf.pack(fill="x")

        date_var  = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        label_var = tk.StringVar()
        note_var  = tk.StringVar()

        def _row(parent, label, var, width=30, hint=""):
            row = tk.Frame(parent, bg=PANEL)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, font=("Segoe UI", 9, "bold"),
                     bg=PANEL, fg=TEXT, width=8, anchor="e"
                     ).pack(side="left")
            entry = tk.Entry(row, textvariable=var, width=width,
                             font=("Segoe UI", 9),
                             bg="white", fg=TEXT,
                             relief="flat",
                             highlightbackground=BORDER,
                             highlightthickness=1)
            entry.pack(side="left", padx=(6, 6))
            if hint:
                tk.Label(row, text=hint, font=("Segoe UI", 8, "italic"),
                         bg=PANEL, fg=MUTED).pack(side="left")
            return entry

        _row(add_lf, "Date:",  date_var,  width=14, hint="YYYY-MM-DD")
        _row(add_lf, "Label:", label_var, width=40,
             hint="short — shown on chart (e.g. Cleaned QDa)")
        _row(add_lf, "Note:",  note_var,  width=60,
             hint="optional details")

        def _add():
            date_s = date_var.get().strip()
            label  = label_var.get().strip()
            note   = note_var.get().strip()
            try:
                datetime.strptime(date_s, "%Y-%m-%d")
            except ValueError:
                messagebox.showwarning("Invalid Date",
                                        "Please enter the date as YYYY-MM-DD.",
                                        parent=dlg)
                return
            if not label:
                messagebox.showwarning("Missing Label",
                                        "Please enter a short label for the annotation.",
                                        parent=dlg)
                return
            self._get_annotations().append(
                {"date": date_s, "label": label, "note": note}
            )
            save_annotations(self._app.annotations)
            label_var.set("")
            note_var.set("")
            _reload_list()
            self._refresh_all()

        btn_row = tk.Frame(add_lf, bg=PANEL)
        btn_row.pack(fill="x", pady=(8, 0))
        polish_button(
            tk.Button(btn_row, text="＋  Add Annotation",
                      command=_add,
                      font=("Segoe UI", 9, "bold"), padx=12, pady=5),
            bg=PURP, active=PURP_DARK
        ).pack(side="right")

        # Close
        foot = tk.Frame(dlg, bg=BG, pady=10)
        foot.pack(fill="x")
        polish_button(
            tk.Button(foot, text="Close", width=10,
                      command=dlg.destroy,
                      font=("Segoe UI", 9, "bold")),
            bg=STEEL, active=PURP_DARK
        ).pack()

        dlg.update_idletasks()
        pw, ph = self.winfo_width(), self.winfo_height()
        px, py = self.winfo_rootx(), self.winfo_rooty()
        dw, dh = dlg.winfo_width(), dlg.winfo_height()
        dlg.geometry(f"+{px + (pw - dw)//2}+{py + (ph - dh)//2}")
        dlg.wait_window()


# ---------------------------------------------------------------------------
# Page 7 — Latest Report
# ---------------------------------------------------------------------------

class ReportPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self._app        = app
        self._instrument = ""
        self._build()

    def enter(self, instrument: str):
        self._instrument = instrument
        self._ilabel.config(text=instrument)
        self._load_latest()

    # ── UI construction ────────────────────────────────────────────────
    def _build(self):
        # Header
        header = tk.Frame(self, bg=DARK, pady=10)
        header.pack(fill="x")
        title_row = tk.Frame(header, bg=DARK)
        title_row.pack()
        tk.Label(title_row, text="Latest QC Report",
                 font=("Segoe UI", 17, "bold"), bg=DARK, fg="white"
                 ).pack(side="left", padx=(0, 16))
        polish_button(
            tk.Button(title_row, text="◀  Back",
                      command=lambda: self._app._show(HubPage),
                      font=("Segoe UI", 9),
                      padx=10, pady=4),
            bg=STEEL, active=PURP_DARK
        ).pack(side="left")
        polish_button(
            tk.Button(title_row, text="⟳  Refresh",
                      command=self._load_latest,
                      font=("Segoe UI", 9),
                      padx=10, pady=4),
            bg=STEEL, active=PURP_DARK
        ).pack(side="left", padx=(8, 0))
        self._ilabel = tk.Label(header, text="",
                                 font=("Segoe UI", 11, "bold"), bg=DARK, fg=MUTED_DARK)
        self._ilabel.pack(pady=(4, 0))

        # Info bar (metadata from report file)
        info_bar = soften_panel(tk.Frame(self, pady=7))
        info_bar.pack(fill="x", padx=14, pady=(10, 0))

        self._meta_file  = tk.Label(info_bar, text="", font=("Segoe UI", 9),
                                     bg=PANEL, fg=TEXT)
        self._meta_file.pack(side="left", padx=(10, 20))
        self._meta_date  = tk.Label(info_bar, text="", font=("Segoe UI", 9),
                                     bg=PANEL, fg=TEXT)
        self._meta_date.pack(side="left", padx=(0, 20))
        self._meta_qcfile = tk.Label(info_bar, text="", font=("Segoe UI", 9),
                                      bg=PANEL, fg=TEXT)
        self._meta_qcfile.pack(side="left", padx=(0, 20))

        # Overall result (right-aligned in info bar)
        self._overall_canvas = tk.Canvas(info_bar, width=20, height=20,
                                          bg=PANEL, highlightthickness=0)
        self._overall_canvas.pack(side="right", padx=(0, 10))
        self._overall_label = tk.Label(info_bar, text="",
                                        font=("Segoe UI", 10, "bold"),
                                        bg=PANEL)
        self._overall_label.pack(side="right", padx=(0, 6))

        # Content area — placeholder XOR tree container
        self._content = tk.Frame(self, bg=BG)
        self._content.pack(fill="both", expand=True, padx=14, pady=(6, 0))

        # Placeholder (shown when no data)
        self._placeholder = tk.Label(self._content, text="",
                                      font=("Segoe UI", 13), bg=BG, fg=MUTED,
                                      justify="center")

        # Tree container (shown when data available)
        self._tree_wrap = tk.Frame(self._content, bg=BG)

        cols         = ("channel", "compound", "parameter", "criterion",
                        "reference", "qc_value", "delta", "status")
        headers_text = ("Channel",  "Compound",  "Parameter",  "Criterion",
                        "Reference", "QC Value", "Δ",     "Status")
        widths       = (110, 135, 125, 95, 115, 115, 100, 72)

        self.tree = ttk.Treeview(self._tree_wrap, columns=cols,
                                  show="headings", selectmode="none")
        for col, hdr, w in zip(cols, headers_text, widths):
            self.tree.heading(col, text=hdr)
            self.tree.column(col, width=w, anchor="center", minwidth=60)

        self.tree.tag_configure("pass",    background="#dcfce7", foreground="#14532d")
        self.tree.tag_configure("fail",    background="#ffe4ec", foreground="#881337")
        self.tree.tag_configure("section", background=PANEL_ALT, foreground=TEXT,
                                 font=("Segoe UI", 9, "bold"))

        vsb = ttk.Scrollbar(self._tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)

        # Legend (inside tree_wrap so it hides with the tree)
        legend = tk.Frame(self._tree_wrap, bg=BG)
        legend.pack(fill="x", pady=(4, 8))
        for color, text in [("#dcfce7", " PASS "), ("#fee2e2", " FAIL ")]:
            tk.Label(legend, text=text, bg=color,
                     font=("Segoe UI", 8), relief="groove",
                     padx=4).pack(side="left", padx=(0, 6))
        tk.Label(legend,
                 text="Δ = QC − Reference  (Area/Height shown as %Δ)",
                 font=("Segoe UI", 8), bg=BG, fg=MUTED).pack(side="left", padx=10)

    # ── show / hide helpers ────────────────────────────────────────────
    def _show_placeholder(self, message: str):
        self._tree_wrap.pack_forget()
        self._placeholder.config(text=message)
        self._placeholder.pack(expand=True)
        self._clear_info_bar()

    def _show_tree(self):
        self._placeholder.pack_forget()
        self._tree_wrap.pack(fill="both", expand=True)

    def _clear_info_bar(self):
        self._meta_file.config(text="")
        self._meta_date.config(text="")
        self._meta_qcfile.config(text="")
        self._overall_label.config(text="")
        self._overall_canvas.delete("all")

    def _update_info_bar(self, meta: dict, filename: str):
        self._meta_file.config(text=f"File:  {filename}")
        self._meta_date.config(text=f"Date:  {meta.get('Date / Time', '—')}")
        self._meta_qcfile.config(text=f"QC File:  {meta.get('QC File', '—')}")
        overall = meta.get("Overall Result", "—")
        if overall == "PASS":
            dot, fg = GREEN, "#14532d"
        elif overall == "FAIL":
            dot, fg = RED, "#881337"
        else:
            dot, fg = MUTED_DARK, MUTED
        self._overall_canvas.delete("all")
        self._overall_canvas.create_oval(2, 2, 18, 18, fill=dot, outline="")
        self._overall_label.config(text=f"Overall:  {overall}", fg=fg)

    # ── data loading ───────────────────────────────────────────────────
    def _load_latest(self):
        if not self._instrument:
            return

        for item in self.tree.get_children():
            self.tree.delete(item)

        report_dir = cfg_get_report_dir(self._app.ref_config, self._instrument)
        if not report_dir or not os.path.isdir(report_dir):
            self._show_placeholder(
                "No report folder configured for this instrument.\n\n"
                "Please set the report folder in  ⚙  Settings."
            )
            return

        pattern = os.path.join(report_dir, f"{self._instrument}_QC_*.csv")
        files   = sorted(glob.glob(pattern))
        if not files:
            self._show_placeholder(
                f"No QC reports found for  {self._instrument}\n\n"
                f"Export a report after running QC to see results here."
            )
            return

        self._parse_and_display(files[-1])   # most recent = largest timestamp

    def _parse_and_display(self, filepath: str):
        try:
            with open(filepath, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
        except Exception as exc:
            self._show_placeholder(f"Cannot read report file:\n{exc}")
            return

        # Metadata: rows 0-3 are ["Key", "Value"] pairs
        meta = {}
        for row in rows[:4]:
            if len(row) >= 2:
                meta[row[0].strip()] = row[1].strip()

        # Row 4 is blank, row 5 is the column header, rows 6+ are data
        if len(rows) < 7:
            self._show_placeholder("Report file appears empty or malformed.")
            return

        self._update_info_bar(meta, os.path.basename(filepath))

        current_channel = None
        for row in rows[6:]:
            if len(row) < 8:
                continue
            channel = row[0].strip()
            if channel != current_channel:
                current_channel = channel
                self.tree.insert("", "end",
                                  values=(channel, "", "", "", "", "", "", ""),
                                  tags=("section",))
            status = row[7].strip().upper()
            tag    = "pass" if status == "PASS" else "fail"
            self.tree.insert("", "end",
                              values=tuple(r.strip() for r in row[:8]),
                              tags=(tag,))

        self._show_tree()


# ---------------------------------------------------------------------------
# Headless mode (driven by Run QC.bat)
# ---------------------------------------------------------------------------

def _popup(kind: str, title: str, msg: str) -> None:
    """Show a Tk messagebox without a visible parent window."""
    root = tk.Tk()
    root.withdraw()
    try:
        if kind == "info":
            messagebox.showinfo(title, msg)
        elif kind == "warning":
            messagebox.showwarning(title, msg)
        else:
            messagebox.showerror(title, msg)
    finally:
        root.destroy()


def run_qc_headless(instrument: str, qc_path: str) -> int:
    """Run the comparison with no GUI, save report, show a result dialog.

    Returns 0 on PASS, 1 on FAIL, 2 on configuration/IO error.
    """
    if instrument not in INSTRUMENTS:
        _popup("error", "QC Wizard",
               f"Unknown instrument: {instrument}\n\n"
               f"Valid choices: {', '.join(INSTRUMENTS)}")
        return 2

    config = load_ref_config()

    ref_path = cfg_get_ref(config, instrument)
    if not ref_path or not os.path.isfile(ref_path):
        _popup("error", "QC Wizard",
               f"No reference file is configured for {instrument}.\n\n"
               "Open QC Wizard normally and set the reference under Settings first.")
        return 2

    report_dir = cfg_get_report_dir(config, instrument)
    if not report_dir or not os.path.isdir(report_dir):
        _popup("error", "QC Wizard",
               f"No report folder is configured for {instrument}.\n\n"
               "Open QC Wizard normally and set the report folder under Settings first.")
        return 2

    if not os.path.isfile(qc_path):
        _popup("error", "QC Wizard", f"QC file not found:\n{qc_path}")
        return 2

    try:
        ref_data = read_qc_csv(ref_path)
        qc_data  = read_qc_csv(qc_path)
    except Exception as exc:
        _popup("error", "QC Wizard", f"Could not read CSV:\n{exc}")
        return 2

    if ref_data.empty or qc_data.empty:
        _popup("error", "QC Wizard", "Reference or QC file contains no data rows.")
        return 2

    ref_row = ref_data.iloc[0]
    qc_row  = qc_data.iloc[0]

    results  = []
    all_pass = True
    for comp in build_comparisons(instrument):
        col = comp["column"]
        ref_missing = col not in ref_row.index or pd.isna(ref_row[col])
        qc_missing  = col not in qc_row.index  or pd.isna(qc_row[col])

        if ref_missing or qc_missing:
            passed   = False
            ref_disp = "—" if ref_missing else f"{float(ref_row[col]):.4f}"
            qc_disp  = "—" if qc_missing  else f"{float(qc_row[col]):.4f}"
            delta    = "missing col"
        else:
            passed, delta = evaluate(ref_row[col], qc_row[col],
                                      comp["threshold"], comp["method"])
            try:
                ref_disp = f"{float(ref_row[col]):.4f}"
                qc_disp  = f"{float(qc_row[col]):.4f}"
            except Exception:
                ref_disp = str(ref_row[col])
                qc_disp  = str(qc_row[col])

        if not passed:
            all_pass = False

        results.append({
            "channel":   CHANNEL_DISPLAY.get(comp["channel"], comp["channel"]),
            "compound":  comp["compound"],
            "parameter": comp["parameter"],
            "criterion": comp["criterion"],
            "reference": ref_disp,
            "qc_value":  qc_disp,
            "delta":     delta,
            "status":    "PASS" if passed else "FAIL",
        })

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename  = f"{instrument}_QC_{timestamp}.csv"
    filepath  = os.path.join(report_dir, filename)

    try:
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            write_safe_csv_row(w, ["Instrument",     instrument])
            write_safe_csv_row(w, ["Date / Time",    datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
            write_safe_csv_row(w, ["QC File",        os.path.basename(qc_path)])
            write_safe_csv_row(w, ["Overall Result", "PASS" if all_pass else "FAIL"])
            w.writerow([])
            write_safe_csv_row(w, ["Channel", "Compound", "Parameter", "Criterion",
                                   "Reference", "QC Value", "Delta", "Status"])
            for r in results:
                write_safe_csv_row(w, [r["channel"], r["compound"], r["parameter"],
                                       r["criterion"], r["reference"], r["qc_value"],
                                       r["delta"], r["status"]])
    except Exception as exc:
        _popup("error", "QC Wizard", f"Could not write report:\n{exc}")
        return 2

    if all_pass:
        _popup("info", "QC Wizard — PASS",
               f"✓ ALL PASS\n\n"
               f"Instrument: {instrument}\n"
               f"QC file: {os.path.basename(qc_path)}\n\n"
               f"Report saved:\n{filepath}")
        return 0

    failed = [r for r in results if r["status"] == "FAIL"]
    lines = [
        f"✘ FAIL — {len(failed)} of {len(results)} checks failed",
        "",
        f"Instrument: {instrument}",
        f"QC file: {os.path.basename(qc_path)}",
        "",
        "Failed checks:",
    ]
    for r in failed[:12]:
        lines.append(f"  • {r['channel']} / {r['compound']} / {r['parameter']}: {r['delta']}")
    if len(failed) > 12:
        lines.append(f"  ... and {len(failed) - 12} more")
    lines += ["", f"Report saved:", filepath]
    _popup("warning", "QC Wizard — FAIL", "\n".join(lines))
    return 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--auto",       action="store_true")
    parser.add_argument("--instrument", default=None)
    parser.add_argument("--qc-file",    dest="qc_file", default=None)
    args, _ = parser.parse_known_args()

    if args.auto:
        if not args.instrument or not args.qc_file:
            _popup("error", "QC Wizard",
                   "--auto mode requires --instrument and --qc-file.")
            sys.exit(2)
        sys.exit(run_qc_headless(args.instrument, args.qc_file))

    app = QCWizardApp()
    app.mainloop()
