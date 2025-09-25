# -*- coding: utf-8 -*-
"""
FFmerge 图形界面（Python + Tkinter）

功能：
- 选择或拖放 音频 + 视频 文件，通过 ffmpeg 合并（流拷贝）
- 输出文件名为当前时间戳：yyyyMMdd_HHmmss.mp4
- 安静模式调用 ffmpeg（-hide_banner -loglevel error -y -nostdin）
- 合并成功后清空输入，便于下一次操作
- 可选拖放支持：pip install tkinterdnd2

测试环境：Windows，Python 3.8+
"""

import os
import shlex
import subprocess
import sys
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import json

# ---- Optional DnD support ----
DnD_AVAILABLE = False
try:
    # pip install tkinterdnd2
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DnD_AVAILABLE = True
except Exception:
    DnD_AVAILABLE = False

AUDIO_EXTS = {'.aac', '.m4a', '.mp3', '.wav', '.flac', '.ogg', '.opus', '.wma', '.ac3'}
VIDEO_EXTS = {'.mp4', '.mkv', '.mov', '.webm', '.m4v', '.avi', '.ts'}

def timestamp():
    return datetime.now().strftime('%Y%m%d_%H%M%S')

def which_ffmpeg():
    # 在 PATH 中查找 ffmpeg；Windows 下也会尝试当前目录
    exe = 'ffmpeg.exe' if os.name == 'nt' else 'ffmpeg'
    # 1) PATH
    for p in os.environ.get('PATH', '').split(os.pathsep):
        candidate = os.path.join(p.strip('"'), exe)
        if os.path.isfile(candidate):
            return candidate
    # 2) Same directory as script
    here = os.path.dirname(os.path.abspath(sys.argv[0]))
    local = os.path.join(here, exe)
    if os.path.isfile(local):
        return local
    return exe  # let subprocess try; will error if not found

def is_audio(path):
    return os.path.splitext(path)[1].lower() in AUDIO_EXTS

def is_video(path):
    return os.path.splitext(path)[1].lower() in VIDEO_EXTS

def clean_quoted_win_path(s: str) -> str:
    """在 Windows 中，拖放可能返回带大括号或引号的路径；此处做规范化。"""
    s = s.strip()
    if s.startswith('{') and s.endswith('}'):
        s = s[1:-1]
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1]
    return s

class App:
    def __init__(self, root):
        self.root = root
        root.title('FFmerge - 音视频合并')
        root.geometry('700x300')
        try:
            root.iconbitmap(default='')  # no icon by default
        except Exception:
            pass

        main = ttk.Frame(root, padding=16)
        main.pack(fill=tk.BOTH, expand=True)

        # 配置文件路径与读取
        self.config_path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), 'ffmerge_gui_config.json')
        self.settings = self.load_settings()

        # 行：音频
        self.audio_var = tk.StringVar()
        row1 = ttk.Frame(main)
        row1.pack(fill=tk.X, pady=(0,10))
        ttk.Label(row1, text='音频文件：', width=12).pack(side=tk.LEFT)
        self.audio_entry = ttk.Entry(row1, textvariable=self.audio_var)
        self.audio_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row1, text='浏览...', command=self.pick_audio).pack(side=tk.LEFT, padx=(8,0))

        # 行：视频
        self.video_var = tk.StringVar()
        row2 = ttk.Frame(main)
        row2.pack(fill=tk.X, pady=(0,10))
        ttk.Label(row2, text='视频文件：', width=12).pack(side=tk.LEFT)
        self.video_entry = ttk.Entry(row2, textvariable=self.video_var)
        self.video_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row2, text='浏览...', command=self.pick_video).pack(side=tk.LEFT, padx=(8,0))

        # 行：输出目录（默认空，如存在上次保存则加载）
        last_outdir = ''
        if isinstance(self.settings, dict):
            last_outdir = self.settings.get('outdir', '') or ''
        self.outdir_var = tk.StringVar(value=last_outdir)
        row3 = ttk.Frame(main)
        row3.pack(fill=tk.X, pady=(0,10))
        ttk.Label(row3, text='输出目录：', width=12).pack(side=tk.LEFT)
        self.outdir_entry = ttk.Entry(row3, textvariable=self.outdir_var)
        self.outdir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row3, text='浏览...', command=self.pick_outdir).pack(side=tk.LEFT, padx=(8,0))
        ttk.Button(row3, text='打开目录', command=self.open_outdir).pack(side=tk.LEFT, padx=(8,0))

        # 行：操作
        row4 = ttk.Frame(main)
        row4.pack(fill=tk.X, pady=(0,6))
        self.merge_btn = ttk.Button(row4, text='开始合并', command=self.merge_now)
        self.merge_btn.pack(side=tk.LEFT)
        ttk.Label(row4, text=' 输出文件名 = yyyyMMdd_HHmmss.mp4').pack(side=tk.LEFT, padx=(12,0))

        # 行：状态
        self.status_var = tk.StringVar(value='就绪。')
        status = ttk.Label(main, textvariable=self.status_var, foreground='#555')
        status.pack(fill=tk.X, pady=(6,0))

        # 拖放设置
        if DnD_AVAILABLE:
            for widget in (self.audio_entry, self.video_entry, self.outdir_entry, root):
                widget.drop_target_register(DND_FILES)
            self.audio_entry.dnd_bind('<<Drop>>', self.on_drop_audio)
            self.video_entry.dnd_bind('<<Drop>>', self.on_drop_video)
            self.outdir_entry.dnd_bind('<<Drop>>', self.on_drop_dir)
            root.dnd_bind('<<Drop>>', self.on_drop_any)
            self.status_var.set('就绪。（已启用拖放）')
        else:
            self.status_var.set('就绪。（如需拖放，请安装 tkinterdnd2：pip install tkinterdnd2）')

    # --- 选择器 ---
    def pick_audio(self):
        path = filedialog.askopenfilename(title='选择音频文件',
                                          filetypes=[('音频', '*.aac *.m4a *.mp3 *.wav *.flac *.ogg *.opus *.wma *.ac3'),
                                                     ('所有文件','*.*')])
        if path:
            self.audio_var.set(path)

    def pick_video(self):
        path = filedialog.askopenfilename(title='选择视频文件',
                                          filetypes=[('视频', '*.mp4 *.mkv *.mov *.webm *.m4v *.avi *.ts'),
                                                     ('所有文件','*.*')])
        if path:
            self.video_var.set(path)

    def pick_outdir(self):
        path = filedialog.askdirectory(title='选择输出目录', mustexist=True)
        if path:
            self.outdir_var.set(path)
            self.save_outdir(path)

    def open_outdir(self):
        path = self.outdir_var.get().strip('" ')
        if not path:
            messagebox.showwarning('提示', '尚未选择输出目录。')
            return
        if not os.path.isdir(path):
            messagebox.showerror('错误', '输出目录不存在或不可访问。')
            return
        try:
            if os.name == 'nt':
                os.startfile(path)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', path])
            else:
                subprocess.Popen(['xdg-open', path])
        except Exception as e:
            messagebox.showerror('错误', str(e))

    # --- 拖放处理 ---
    def parse_dnd_list(self, data: str):
        # 数据可能包含多个文件，使用空格分隔，且每个可能被 {} 包裹
        # 使用 shlex 在 Windows 下也能正确按引号/括号分割
        # 先将 {...} 转为 "..." 以便 shlex 解析
        normalized = data.replace('{', '"').replace('}', '"')
        try:
            items = shlex.split(normalized)
        except Exception:
            items = [data]
        return [clean_quoted_win_path(i) for i in items]

    def on_drop_audio(self, event):
        files = self.parse_dnd_list(event.data)
        if files:
            self.audio_var.set(files[0])

    def on_drop_video(self, event):
        files = self.parse_dnd_list(event.data)
        if files:
            self.video_var.set(files[0])

    def on_drop_dir(self, event):
        files = self.parse_dnd_list(event.data)
        # If first is a directory, set as outdir
        for f in files:
            if os.path.isdir(f):
                self.outdir_var.set(f)
                self.save_outdir(f)
                break

    def on_drop_any(self, event):
        # Heuristic: if two files dropped, map by extension; if one, fill first empty slot by type
        files = self.parse_dnd_list(event.data)
        if not files:
            return
        aud_set = False
        vid_set = False
        for f in files:
            if os.path.isdir(f):
                # set output dir if empty
                if not self.outdir_var.get():
                    self.outdir_var.set(f)
                    self.save_outdir(f)
                continue
            ext = os.path.splitext(f)[1].lower()
            if is_audio(f) and not self.audio_var.get():
                self.audio_var.set(f); aud_set = True; continue
            if is_video(f) and not self.video_var.get():
                self.video_var.set(f); vid_set = True; continue
        # If still empty, just assign first to audio, second to video
        if not self.audio_var.get():
            self.audio_var.set(files[0])
        if len(files) > 1 and not self.video_var.get():
            self.video_var.set(files[1])

    # --- 合并逻辑 ---
    def merge_now(self):
        audio = self.audio_var.get().strip('" ')
        video = self.video_var.get().strip('" ')
        outdir = self.outdir_var.get().strip('" ')

        if not audio or not os.path.isfile(audio):
            messagebox.showerror('错误', '请选择有效的音频文件。')
            return
        if not video or not os.path.isfile(video):
            messagebox.showerror('错误', '请选择有效的视频文件。')
            return
        if not outdir:
            messagebox.showerror('错误', '请选择输出目录。')
            return
        if not os.path.isdir(outdir):
            messagebox.showerror('错误', '输出目录不存在。')
            return

        ffmpeg = which_ffmpeg()
        outfile = os.path.join(outdir, f'{timestamp()}.mp4')

        cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-nostdin',
               '-i', audio, '-i', video, '-acodec', 'copy', '-vcodec', 'copy', outfile]

        self.merge_btn.config(state=tk.DISABLED)
        self.status_var.set('正在合并...')
        self.root.update_idletasks()

        try:
            # 仅捕获错误输出
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=False)
            if proc.returncode != 0 or not os.path.exists(outfile) or os.path.getsize(outfile) == 0:
                err = proc.stderr.strip() or proc.stdout.strip() or 'Unknown error.'
                messagebox.showerror('合并失败', err)
                self.status_var.set('失败。')
                return
        except FileNotFoundError:
            messagebox.showerror('未找到 ffmpeg', '未找到 ffmpeg 可执行文件。\n请将 ffmpeg.exe 放在本脚本同目录，或添加到 PATH。')
            self.status_var.set('未找到 ffmpeg。')
            return
        except Exception as e:
            messagebox.showerror('错误', str(e))
            self.status_var.set('错误。')
            return
        finally:
            self.merge_btn.config(state=tk.NORMAL)

        # Success
        self.status_var.set(f'成功：{outfile}')
        messagebox.showinfo('完成', f'合并成功：\n{outfile}')
        # Clear inputs for next run
        self.audio_var.set('')
        self.video_var.set('')
        self.audio_entry.focus_set()

    # --- 设置持久化 ---
    def load_settings(self):
        try:
            if os.path.isfile(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            return {}
        return {}

    def save_outdir(self, path: str):
        try:
            settings = self.settings if isinstance(self.settings, dict) else {}
            settings['outdir'] = path
            self.settings = settings
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

def main():
    # Use TkinterDnD.Tk if available, else tk.Tk
    if DnD_AVAILABLE:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    # Modern ttk theme if possible
    try:
        style = ttk.Style(root)
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        elif 'clam' in style.theme_names():
            style.theme_use('clam')
    except Exception:
        pass

    App(root)
    root.mainloop()

if __name__ == '__main__':
    main()
