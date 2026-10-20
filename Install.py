"""Graphical entry point for the experimental Windows owner-copy build kit."""
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parent


def setup_command(values, resume=False):
    command = [sys.executable, "-B", str(ROOT / "client/scripts/setup_owned_copy.py")]
    for name in ("disc", "elf", "dev-flash", "workspace", "prefix"):
        if not values.get(name):
            raise ValueError("Choose all five folders/files before building.")
        command += ["--" + name, values[name]]
    command += ["--shaders", str(ROOT / "shaders"), "--jobs", "2"]
    if resume:
        command.append("--resume")
    return command


def main():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    window = tk.Tk()
    window.title("Folklore • Build from your copy")
    window.geometry("850x490")
    window.minsize(740, 460)
    window.columnconfigure(1, weight=1)
    window.rowconfigure(9, weight=1)
    ttk.Label(window, text="Folklore Native • Experimental setup",
              font=("Segoe UI", 17)).grid(row=0, column=0, columnspan=3, sticky="w", padx=18, pady=(16, 8))
    ttk.Label(window, text="Requires your USA disc 1.00 backup, decrypted ELF, firmware, and MSYS2 CLANG64 tools.\n"
                          "The executable is built locally. Keep the source kit and tools after installation.",
              wraplength=800).grid(row=1, column=0, columnspan=3, sticky="w", padx=18, pady=(0, 12))
    fields = {}
    labels = [("disc", "Your disc backup"), ("elf", "Your decrypted EBOOT.elf"),
              ("dev-flash", "Your firmware dev_flash"), ("workspace", "New installation folder"),
              ("prefix", "MSYS2 clang64 folder")]
    process = None
    events = queue.Queue()
    controls = []

    def browse(name):
        if name == "elf":
            value = filedialog.askopenfilename(title="Choose your decrypted ELF",
                                               filetypes=[("ELF files", "*.elf"), ("All files", "*")])
        elif name == "workspace":
            parent = filedialog.askdirectory(title="Choose a parent for a NEW Folklore-Native folder")
            value = str(Path(parent) / "Folklore-Native") if parent else ""
        else:
            value = filedialog.askdirectory(title="Choose " + dict(labels)[name])
        if value:
            fields[name].set(value)
            if name == "disc" and not fields["elf"].get():
                candidate = Path(value) / "PS3_GAME/USRDIR/EBOOT.elf"
                if candidate.is_file():
                    fields["elf"].set(str(candidate))

    for row, (name, label) in enumerate(labels, 2):
        fields[name] = tk.StringVar()
        ttk.Label(window, text=label).grid(row=row, column=0, sticky="w", padx=18, pady=5)
        entry = ttk.Entry(window, textvariable=fields[name])
        entry.grid(row=row, column=1, sticky="ew", pady=5)
        button = ttk.Button(window, text="Browse", command=lambda n=name: browse(n))
        button.grid(row=row, column=2, padx=12, pady=5)
        controls += [entry, button]
    compiler = shutil.which("clang.exe")
    if compiler and Path(compiler).parent.parent.name.casefold() == "clang64":
        fields["prefix"].set(str(Path(compiler).parent.parent))
    resume = tk.BooleanVar()
    check = ttk.Checkbutton(window, text="Resume this installation using its recorded inputs", variable=resume)
    check.grid(row=7, column=0, columnspan=3, sticky="w", padx=18, pady=8)
    controls.append(check)
    status = tk.StringVar(value="Choose a new installation folder with at least 10 GB free.")
    ttk.Label(window, textvariable=status, wraplength=800).grid(
        row=8, column=0, columnspan=3, sticky="w", padx=18, pady=5)
    log = tk.Text(window, height=6, wrap="word", state="disabled")
    log.grid(row=9, column=0, columnspan=3, sticky="nsew", padx=18, pady=5)

    def append(text):
        log.configure(state="normal")
        log.insert("end", text + "\n")
        log.see("end")
        log.configure(state="disabled")

    def start():
        nonlocal process
        values = {key: value.get().strip() for key, value in fields.items()}
        try:
            command = setup_command(values, resume.get())
            if not (ROOT / "shaders/manifest.json").is_file():
                raise ValueError("Use the Owner Build Kit download. The source-only ZIP does not contain the shader pack.")
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace", cwd=ROOT,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except (ValueError, OSError) as error:
            messagebox.showerror("Cannot start setup", str(error))
            return
        for control in controls + [build, launch]:
            control.configure(state="disabled")
        status.set("Checking your files, then generating and compiling. This can take several minutes.")

        def collect(child):
            for line in child.stdout:
                events.put(("line", line.rstrip()))
            events.put(("done", child.wait()))
        threading.Thread(target=collect, args=(process,), daemon=True).start()

    def open_launcher():
        workspace = Path(fields["workspace"].get())
        env = dict(os.environ)
        env["PATH"] = str(Path(fields["prefix"].get()) / "bin") + os.pathsep + env.get("PATH", "")
        subprocess.Popen([sys.executable, "-B", str(ROOT / "client/launcher/portable_entry.py"),
                          "--config", str(workspace / "settings.json")], env=env, cwd=workspace)

    build = ttk.Button(window, text="Build and set up", command=start)
    build.grid(row=10, column=1, sticky="e", pady=12)
    launch = ttk.Button(window, text="Open launcher", command=open_launcher, state="disabled")
    launch.grid(row=10, column=2, padx=12, pady=12)

    def poll():
        nonlocal process
        while not events.empty():
            kind, value = events.get_nowait()
            if kind == "line":
                append(value)
            else:
                process = None
                for control in controls + [build]:
                    control.configure(state="normal")
                if value == 0:
                    status.set("Local build installed. Open the launcher to play. Keep this generated executable private.")
                    launch.configure(state="normal")
                else:
                    status.set("Setup stopped. See the error above and the installation folder's logs. No game was launched.")
        if process is not None:
            workspace = Path(fields["workspace"].get())
            for filename in ("build/build.log", "build/configure.log", "logs/002-build.log"):
                path = workspace / filename
                try:
                    with path.open("rb") as stream:
                        stream.seek(max(0, path.stat().st_size - 2048))
                        lines = stream.read().decode("utf-8", "replace").splitlines()
                    if lines:
                        status.set(lines[-1][-220:])
                        break
                except OSError:
                    pass
        window.after(750, poll)

    def close():
        if process is not None:
            messagebox.showinfo("Build running", "Keep this window open until the build finishes. Closing it would leave the compiler running.")
        else:
            window.destroy()
    window.protocol("WM_DELETE_WINDOW", close)
    window.after(750, poll)
    window.mainloop()


if __name__ == "__main__":
    main()
