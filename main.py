import json
import os
import shutil
import sys
import time

from PySide6.QtCore import (
    Qt, QTimer, QUrl, QRectF, QDateTime, QSize, QEvent,
    QPropertyAnimation, QEasingCurve,
)
from PySide6.QtGui import (
    QColor, QFont, QIcon, QImage, QPainter, QPixmap, QRadialGradient,
)
from PySide6.QtMultimedia import (
    QAudioOutput, QMediaDevices, QMediaPlayer, QSoundEffect, QVideoSink,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QFrame, QGraphicsOpacityEffect,
    QGridLayout, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QPushButton, QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

# ---------- 路径与配置 ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

DEFAULT_CONFIG = {
    "work": 60,        # 创作时长（分钟）
    "break": 30,       # 休息时长（分钟）
    "cycles": 2,       # 循环次数
    "bg": "",          # 背景图/视频路径
    "bg_type": "image",# 背景类型：image / video
    "volume": 60,      # 音量
    "autoplay": True,  # 启动时自动播放音乐
    "always_on_top": False,  # 窗口置顶
    "songs": [],       # 已导入的歌曲路径列表
    "notify_sound": "",# 提示音路径（空则用默认生成的双音门铃）
}

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v"}


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return cfg


def fmt_hms(seconds):
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_pos(ms):
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


STYLE = """
QWidget { color: #ffffff; }
QPushButton { background: rgba(255,255,255,28); border: none; color: #fff; }
QPushButton:hover { background: rgba(255,255,255,60); }
QLabel { background: transparent; }
QStackedWidget, QFrame#Page { background: transparent; }
QFrame#MusicBar {
    background: rgba(20,22,30,170); border-radius: 22px;
}
QListWidget {
    background: rgba(0,0,0,100); color:#fff;
    border: 1px solid rgba(255,255,255,40); border-radius: 8px;
}
QMenu { background:#23262f; color:#fff; border:1px solid rgba(255,255,255,40); }
QSlider::groove:horizontal { height:5px; background:rgba(255,255,255,40); border-radius:2px; }
QSlider::sub-page:horizontal { background:#43e0ff; border-radius:2px; }
QSlider::handle:horizontal { background:#fff; width:12px; height:12px; margin:-4px 0; border-radius:6px; }
"""

WORK = "创作"
BREAK = "休息"


class TimerCard(QWidget):
    """圆角矩形计时卡片；运行时底部显示进度条。尺寸随缩放比例变化。"""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(90, 110)
        self.progress = 0.0
        self.show_ring = False
        self._scale = 1.0  # 缩放比例，由主窗口 adjust_timer 设置

    def set_scale(self, k):
        """设置缩放比例并通知布局重新计算尺寸。"""
        self._scale = k
        self.updateGeometry()

    def sizeHint(self):
        # 卡片尺寸随窗口缩放，最小 180×200
        return QSize(max(180, round(280 * self._scale)),
                     max(200, round(300 * self._scale)))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 圆角矩形背景（径向渐变，和原来的圆盘同色系）
        radius = max(8, min(28, min(self.width(), self.height()) // 8))
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        grad = QRadialGradient(rect.center(),
                                max(rect.width(), rect.height()) / 2)
        grad.setColorAt(0.0, QColor(48, 52, 72, 160))
        grad.setColorAt(1.0, QColor(18, 20, 28, 180))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(rect, radius, radius)
        # 运行页：底部进度条
        if self.show_ring and self.progress > 0:
            bar_h = 4
            bar_w = (self.width() - 16) * min(self.progress, 1.0)
            bar_rect = QRectF(8, self.height() - bar_h - 6, bar_w, bar_h)
            p.setBrush(QColor("#37e6ff"))
            p.drawRoundedRect(bar_rect, bar_h / 2, bar_h / 2)
        p.end()


class MusicDialog(QDialog):
    """音乐导入与播放弹窗"""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.win.music_dialog = self
        self.finished.connect(lambda: setattr(self.win, "music_dialog", None))
        self.setWindowTitle("音乐")
        self.setMinimumWidth(340)
        v = QVBoxLayout(self)

        import_btn = QPushButton("导入歌曲")
        import_btn.clicked.connect(self.import_and_refresh)
        v.addWidget(import_btn)

        self.listw = QListWidget()
        self.listw.setMinimumHeight(140)
        self.listw.itemDoubleClicked.connect(
            lambda it: self.win.play_index(self.listw.row(it)))
        v.addWidget(self.listw)
        self.refresh()

        row = QHBoxLayout()
        for text, slot in (("⏮", self.win.prev_song),
                           ("播放", self.win.toggle_music),
                           ("⏭", self.win.next_song)):
            b = QPushButton(text)
            b.setFixedHeight(34)
            b.clicked.connect(slot)
            if text == "播放":
                self.play_btn = b
            row.addWidget(b)
        v.addLayout(row)

        vrow = QHBoxLayout()
        vrow.addWidget(QLabel("音量"))
        vol = QSlider(Qt.Orientation.Horizontal)
        vol.setRange(0, 100)
        vol.setValue(self.win.cfg["volume"])
        vol.valueChanged.connect(self.win.on_volume)
        vrow.addWidget(vol)
        v.addLayout(vrow)

        autoplay = QCheckBox("启动时自动播放音乐")
        autoplay.setChecked(self.win.cfg["autoplay"])
        autoplay.toggled.connect(self.win.on_autoplay)
        v.addWidget(autoplay)

    def import_and_refresh(self):
        self.win.import_songs()
        self.refresh()

    def refresh(self):
        self.listw.clear()
        for p in self.win.songs:
            self.listw.addItem(QListWidgetItem(os.path.basename(p)))

    def set_play_text(self, text):
        self.play_btn.setText(text)

    def select_row(self, i):
        if 0 <= i < self.listw.count():
            self.listw.setCurrentRow(i)


class PomodoroWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        os.makedirs(ASSETS_DIR, exist_ok=True)

        # 计时状态
        self.phase = WORK
        self.cycle = 1
        self.remaining = self.cfg["work"] * 60
        self.phase_total = self.remaining
        self.running = False
        self.finished = False

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)

        # 音乐
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(self.cfg["volume"] / 100.0)
        self.player.mediaStatusChanged.connect(self.on_media_status)
        self.player.positionChanged.connect(self.on_position)
        self.player.durationChanged.connect(self.on_duration)
        self.songs = list(self.cfg.get("songs", []))  # 从配置恢复已导入歌曲
        self.song_index = -1
        self.music_dialog = None
        self.seeking = False

        # 提示音（开始/结束任务时播放）
        self.notify_sound = QSoundEffect(self)
        self.notify_sound.setVolume(0.7)
        self._load_notify_sound()

        # 背景：图片或视频都画在 paintEvent 里，控件自然在上面
        self.bg_type = self.cfg.get("bg_type", "image")
        self.bg = QImage()
        self.video_frame = None  # 最新一帧视频画面

        # 视频播放器：静音、无限循环，避免和音乐冲突
        self.video_player = QMediaPlayer(self)
        self.video_audio = QAudioOutput(self)
        self.video_audio.setVolume(0)
        self.video_player.setAudioOutput(self.video_audio)
        self.video_player.setLoops(QMediaPlayer.Loops.Infinite)

        # 用 QVideoSink 逐帧取图，在 paintEvent 里自己画，
        # 避免 QVideoWidget 原生窗口盖住 Qt 控件的问题
        self.video_sink = QVideoSink()
        self.video_player.setVideoSink(self.video_sink)
        self.video_sink.videoFrameChanged.connect(self.on_video_frame)

        # 监听默认音频输出设备变化（插拔耳机/音箱时自动切换，无需重启）
        self.media_devices = QMediaDevices(self)
        self.media_devices.audioOutputsChanged.connect(
            self.on_audio_device_changed)
        self.on_audio_device_changed()  # 初始化时绑定当前默认设备

        # 恢复上次的背景
        if self.cfg["bg"] and os.path.exists(self.cfg["bg"]):
            if self.bg_type == "video":
                self.set_background_video(self.cfg["bg"])
            else:
                self.set_background_image(self.cfg["bg"])

        self.setWindowTitle("Cozy Pomodoro")
        icon_path = os.path.join(ASSETS_DIR, "icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.resize(1200, 740)
        # 显式设很小的最小尺寸，覆盖布局的最小尺寸提示，
        # 所有元素等比缩放，最小尺寸保证仍可看清
        self.setMinimumSize(180, 200)
        self.build_ui()
        self.show_settings_page()
        self.update_clock()
        self.adjust_timer()
        self.scale_chrome()
        self.update_compact_mode()
        self.update_display()
        # 启动应用后自动播放音乐（如果已导入歌曲且开启了自动播放）
        if self.cfg["autoplay"]:
            self.auto_play()
        # 窗口显示后再应用置顶（此时 winId 才有效）
        QTimer.singleShot(0, self.apply_always_on_top)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.adjust_timer()
        self.scale_chrome()
        self.update_compact_mode()

    def eventFilter(self, obj, event):
        """音乐条鼠标悬停淡入淡出 + 数字标签双击编辑。"""
        if hasattr(self, "music_bar") and obj is self.music_bar:
            if event.type() == QEvent.Type.Enter:
                self._fade_music(1.0)
            elif event.type() == QEvent.Type.Leave:
                self._fade_music(0.3)
        which = obj.property("edit_which")
        if which and event.type() == QEvent.Type.MouseButtonDblClick:
            self._edit_number(which)
        return super().eventFilter(obj, event)

    def _edit_number(self, which):
        """双击数字弹出输入框直接修改创作/休息/循环次数。"""
        cfg_key = {"work": "work", "break": "break", "cycle": "cycles"}[which]
        label = {"work": self.work_value, "break": self.break_value,
                 "cycle": self.cycle_value}[which]
        title = {"work": "创作时长", "break": "休息时长", "cycle": "循环次数"}[which]
        unit = "次" if which == "cycle" else "分钟"
        max_v = 10 if which == "cycle" else 180
        val, ok = QInputDialog.getInt(
            self, title, f"设置{title}（{unit}）", self.cfg[cfg_key], 1, max_v, 1)
        if ok:
            self.cfg[cfg_key] = val
            label.setText(str(val))
            self.save_config()

    def _fade_music(self, target):
        self.music_fade.stop()
        self.music_fade.setStartValue(self.music_effect.opacity())
        self.music_fade.setEndValue(target)
        self.music_fade.start()

    def adjust_timer(self):
        """卡片始终占窗口固定比例（28%宽、40%高），贴右上角；内容按卡片尺寸缩放。"""
        cw = max(120, round(self.width() * 0.28))
        ch = max(140, round(self.height() * 0.40))
        self.circle.setFixedSize(cw, ch)
        ref = min(cw, ch)
        m = max(4, round(20 * ref / 300.0))
        self.circle.layout().setContentsMargins(m, m, m, m)
        self.scale_fonts(ref)

    def update_compact_mode(self):
        """根据窗口大小调整布局边距；所有元素始终可见，由 scale_chrome 等比缩放。"""
        lay = self.layout()
        if self.width() < 500 or self.height() < 450:
            lay.setContentsMargins(6, 6, 6, 6)
            lay.setSpacing(6)
        else:
            lay.setContentsMargins(22, 18, 22, 18)
            lay.setSpacing(12)

    def scale_chrome(self):
        """所有外围元素等比缩放（时钟/工具栏/音乐条及内部间距边距），基准高 740。"""
        k = max(0.2, min(1.4, self.height() / 740.0))
        # 左上时钟（字号 + 间距 + 最大宽度，避免占太多面积挡住背景）
        self.clock_layout.setSpacing(max(0, round(4 * k)))
        self.clock_wrap.setMaximumWidth(max(50, round(160 * k)))
        self.date_label.setStyleSheet(
            f"font-size:{max(6, round(15 * k))}px;")
        self.clock_label.setStyleSheet(
            f"font-size:{max(10, round(46 * k))}px; font-weight:700;")
        # 右侧工具栏（按钮尺寸 + 间距）
        ts = max(14, round(48 * k))
        tfs = max(8, round(20 * k))
        self.toolbar_layout.setSpacing(max(2, round(12 * k)))
        for b in self.tool_buttons:
            b.setFixedSize(ts, ts)
            bg = "rgba(20,22,30,170)"
            if (b is self.always_on_top_btn
                    and self.cfg.get("always_on_top")):
                bg = "rgba(67,224,255,140)"  # 置顶激活时青色高亮
            b.setStyleSheet(self.round_button_style(ts, tfs, bg=bg))
        # 底部音乐条（高度 + 内部边距/间距/所有固定宽度）
        self.music_bar.setFixedHeight(max(24, round(64 * k)))
        self.music_layout.setContentsMargins(
            max(2, round(18 * k)), max(1, round(8 * k)),
            max(2, round(18 * k)), max(1, round(8 * k)))
        self.music_layout.setSpacing(max(1, round(10 * k)))
        self.note_label.setStyleSheet(
            f"font-size:{max(7, round(20 * k))}px;")
        self.song_title.setFixedWidth(max(30, round(130 * k)))
        self.song_title.setStyleSheet(
            f"font-size:{max(6, round(15 * k))}px;")
        self.pos_label.setFixedWidth(max(28, round(100 * k)))
        self.pos_label.setStyleSheet(
            f"font-size:{max(5, round(12 * k))}px;")
        self.vol_slider.setFixedWidth(max(20, round(60 * k)))
        bs = max(14, round(40 * k))
        bps = max(16, round(44 * k))
        for b in self.music_ctrl_buttons:
            if b is self.bar_play:
                b.setFixedSize(bps, bps)
                b.setStyleSheet(
                    f"border-radius:{bps // 2}px; "
                    f"font-size:{max(7, round(18 * k))}px;")
            else:
                b.setFixedSize(bs, bs)
                b.setStyleSheet(
                    f"border-radius:{bs // 2}px; "
                    f"font-size:{max(7, round(16 * k))}px;")

    def scale_fonts(self, s):
        k = s / 300.0
        # 运行页大时间
        f = QFont()
        f.setPointSize(max(8, round(28 * k)))
        f.setBold(True)
        self.time_label.setFont(f)
        # 设置页两个数字
        fs = max(8, round(34 * k))
        self.work_value.setStyleSheet(
            f"font-size:{fs}px; font-weight:700;")
        self.break_value.setStyleSheet(
            f"font-size:{fs}px; font-weight:700;")
        num_h = max(12, round(fs * 1.4))
        self.work_value.setMinimumHeight(num_h)
        self.break_value.setMinimumHeight(num_h)
        # 同步缩放每个 dial 内部间距
        if hasattr(self, "dial_layouts"):
            ds = max(2, round(6 * k))
            for bl in self.dial_layouts:
                bl.setSpacing(ds)
        # 设置页间距与控件等比缩小
        self.set_v.setSpacing(max(1, round(6 * k)))
        self.cycle_title.setStyleSheet(
            f"font-size:{max(6, round(13 * k))}px;")
        cv = max(9, round(24 * k))
        self.cycle_value.setStyleSheet(
            f"font-size:{cv}px; font-weight:700;")
        for t in self.dial_titles:
            t.setStyleSheet(f"font-size:{max(6, round(15*k))}px;")
        ps = max(20, round(58 * k))
        self.play_big.setFixedSize(ps, ps)
        self.play_big.setStyleSheet(
            self.round_button_style(ps, max(8, round(22 * k))))
        cw = max(12, round(30 * k))
        for b in self.cyc_buttons:
            b.setFixedSize(cw, cw)
            b.setStyleSheet(
                self.round_button_style(cw, max(7, round(18 * k))))
        tw = max(16, round(44 * k))
        th = max(8, round(22 * k))
        for b in self.chev_buttons:
            b.setFixedSize(tw, th)
            b.setStyleSheet(
                f"border-radius:{th // 2}px; background:rgba(255,255,255,30);"
                f"font-size:{max(6, round(9 * k))}px;")

    # ---------------- 通用按钮 ----------------
    @staticmethod
    def round_button_style(size, fs, bg="rgba(255,255,255,30)"):
        """生成圆形按钮的样式串，避免 scale_fonts 里重复拼接。"""
        return (f"border-radius:{size // 2}px; background:{bg};"
                f"font-size:{fs}px;")

    def tool_button(self, text, size=48, fs=20):
        b = QPushButton(text)
        b.setFixedSize(size, size)
        b.setStyleSheet(self.round_button_style(
            size, fs, bg="rgba(20,22,30,170)"))
        return b

    # ---------------- UI ----------------
    def build_ui(self):
        grid = QGridLayout(self)
        grid.setContentsMargins(22, 18, 22, 18)
        grid.setSpacing(12)
        grid.setColumnStretch(2, 1)  # 计时卡片列随窗口拉伸
        grid.setRowStretch(1, 1)

        # 左上：日期 + 时钟
        self.clock_layout = QVBoxLayout()
        clock_box = self.clock_layout
        self.date_label = QLabel()
        self.date_label.setStyleSheet("font-size:15px;")
        self.date_label.setMinimumWidth(1)
        self.clock_label = QLabel()
        self.clock_label.setStyleSheet("font-size:46px; font-weight:700;")
        self.clock_label.setMinimumWidth(1)
        clock_box.addWidget(self.date_label)
        clock_box.addWidget(self.clock_label)
        self.clock_wrap = QWidget()
        self.clock_wrap.setMinimumSize(1, 1)  # 允许收缩，不卡住窗口缩放
        self.clock_wrap.setLayout(clock_box)
        grid.addWidget(self.clock_wrap, 0, 0, Qt.AlignmentFlag.AlignTop
                       | Qt.AlignmentFlag.AlignLeft)

        clock_timer = QTimer(self)
        clock_timer.setInterval(1000)
        clock_timer.timeout.connect(self.update_clock)
        clock_timer.start()

        # 右上：计时卡片（圆角矩形，可任意缩放；限制最大尺寸以露出更多背景）
        self.circle = TimerCard()
        self.circle.setMaximumSize(420, 340)
        ov = QVBoxLayout(self.circle)
        ov.setContentsMargins(26, 26, 26, 26)
        self.stack = QStackedWidget()
        ov.addWidget(self.stack)
        self.build_settings_page()
        self.build_timer_page()
        grid.addWidget(self.circle, 0, 2,
                       Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)

        # 右侧竖排工具栏（仅保留已实现的功能）
        self.toolbar_layout = QVBoxLayout()
        self.toolbar_layout.setSpacing(12)
        right = self.toolbar_layout
        tools = [
            ("▶", self.toolbar_start_pause),
            ("■", self.stop_to_settings),
            ("🎵", lambda: MusicDialog(self).exec()),
            ("🖼", self.change_background),
            ("📌", self.toggle_always_on_top),
        ]
        self.tool_buttons = []
        self.always_on_top_btn = None
        for text, slot in tools:
            b = self.tool_button(text)
            b.setToolTip("置顶显示" if text == "📌" else "")
            b.clicked.connect(slot)
            if text == "▶":
                self.toolbar_play = b
            if text == "📌":
                self.always_on_top_btn = b
            self.tool_buttons.append(b)
            right.addWidget(b)
        self.toolbar_wrap = QWidget()
        self.toolbar_wrap.setMinimumSize(1, 1)  # 允许收缩
        self.toolbar_wrap.setLayout(right)
        grid.addWidget(self.toolbar_wrap, 1, 2, Qt.AlignmentFlag.AlignTop
                       | Qt.AlignmentFlag.AlignRight)

        # 底部：音乐播放条（占满整行）
        grid.addWidget(self.build_music_bar(), 2, 0, 1, 3)

    def build_music_bar(self):
        bar = QFrame()
        self.music_bar = bar
        bar.setObjectName("MusicBar")
        bar.setFixedHeight(64)
        self.music_layout = QHBoxLayout(bar)
        self.music_layout.setContentsMargins(18, 8, 18, 8)
        self.music_layout.setSpacing(10)
        h = self.music_layout

        note = QLabel("🎵")
        self.note_label = note
        note.setStyleSheet("font-size:20px;")
        self.song_title = QLabel("未播放")
        self.song_title.setFixedWidth(130)
        self.song_title.setStyleSheet("font-size:15px;")
        h.addWidget(note)
        h.addWidget(self.song_title)

        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setFixedWidth(60)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(self.cfg["volume"])
        self.vol_slider.valueChanged.connect(self.on_volume)
        h.addWidget(self.vol_slider)

        prev = QPushButton("⏮")
        prev.setFixedSize(40, 40)
        prev.setStyleSheet("border-radius:20px; font-size:16px;")
        prev.clicked.connect(self.prev_song)
        self.bar_play = QPushButton("▶")
        self.bar_play.setFixedSize(44, 44)
        self.bar_play.setStyleSheet("border-radius:22px; font-size:18px;")
        self.bar_play.clicked.connect(self.toggle_music)
        nxt = QPushButton("⏭")
        nxt.setFixedSize(40, 40)
        nxt.setStyleSheet("border-radius:20px; font-size:16px;")
        nxt.clicked.connect(self.next_song)
        self.music_ctrl_buttons = [prev, self.bar_play, nxt]
        h.addWidget(prev)
        h.addWidget(self.bar_play)
        h.addWidget(nxt)

        self.pos_slider = QSlider(Qt.Orientation.Horizontal)
        self.pos_slider.setRange(0, 0)
        self.pos_slider.sliderPressed.connect(
            lambda: setattr(self, "seeking", True))
        self.pos_slider.sliderReleased.connect(self.seek_done)
        h.addWidget(self.pos_slider, 1)

        self.pos_label = QLabel("0:00 / 0:00")
        self.pos_label.setFixedWidth(100)
        h.addWidget(self.pos_label)

        # 鼠标悬停淡入淡出：不在音乐条上时低透明度，移上去恢复
        self.music_effect = QGraphicsOpacityEffect(bar)
        self.music_effect.setOpacity(0.3)
        bar.setGraphicsEffect(self.music_effect)
        self.music_fade = QPropertyAnimation(self.music_effect, b"opacity")
        self.music_fade.setDuration(200)
        self.music_fade.setEasingCurve(QEasingCurve.Type.InOutQuad)
        bar.installEventFilter(self)
        return bar

    # ---------------- 设置页 ----------------
    def build_settings_page(self):
        page = QFrame()
        page.setObjectName("Page")
        self.set_v = QVBoxLayout(page)
        v = self.set_v
        v.setSpacing(6)
        v.setContentsMargins(2, 2, 2, 2)
        self.chev_buttons = []
        self.cyc_buttons = []

        cyc = QHBoxLayout()
        cm = QPushButton("‹")
        cm.setFixedSize(30, 30)
        cm.setStyleSheet("border-radius:15px; font-size:18px;")
        cm.clicked.connect(lambda: self.change_cycle(-1))
        self.cyc_buttons.append(cm)
        cp = QPushButton("›")
        cp.setFixedSize(30, 30)
        cp.setStyleSheet("border-radius:15px; font-size:18px;")
        cp.clicked.connect(lambda: self.change_cycle(1))
        self.cyc_buttons.append(cp)
        cb = QVBoxLayout()
        t = QLabel("循环")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cycle_value = QLabel(str(self.cfg["cycles"]))
        self.cycle_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cycle_value.setStyleSheet("font-size:24px; font-weight:700;")
        self.cycle_value.setProperty("edit_which", "cycle")
        self.cycle_value.setCursor(Qt.CursorShape.IBeamCursor)
        self.cycle_value.setToolTip("双击修改")
        self.cycle_value.installEventFilter(self)
        cb.addWidget(t)
        self.cycle_title = t
        cb.addWidget(self.cycle_value)
        cyc.addStretch()
        cyc.addWidget(cm)
        cyc.addLayout(cb)
        cyc.addWidget(cp)
        cyc.addStretch()
        v.addLayout(cyc)

        dials = QHBoxLayout()
        self.work_value = self.make_dial(WORK, self.cfg["work"], dials)
        self.break_value = self.make_dial(BREAK, self.cfg["break"], dials)
        v.addLayout(dials)

        play = QPushButton("▶")
        play.setFixedSize(58, 58)
        play.setStyleSheet("border-radius:29px; font-size:22px;")
        play.clicked.connect(self.start_from_settings)
        self.play_big = play
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(play)
        row.addStretch()
        v.addLayout(row)
        v.addStretch()
        self.stack.addWidget(page)

    def make_dial(self, title, value, layout):
        box = QVBoxLayout()
        box.setSpacing(6)  # 标题/数字/▲/▼ 之间统一间距，随缩放调整
        if not hasattr(self, "dial_layouts"):
            self.dial_layouts = []
        self.dial_layouts.append(box)
        t = QLabel(title)
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if not hasattr(self, "dial_titles"):
            self.dial_titles = []
        self.dial_titles.append(t)
        val = QLabel(str(value))
        val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        val.setStyleSheet("font-size:34px; font-weight:700;")
        val.setMinimumHeight(48)  # 加高，避免数字和下面按钮重叠
        # 双击可编辑
        val.setProperty("edit_which", "work" if title == WORK else "break")
        val.setCursor(Qt.CursorShape.IBeamCursor)
        val.setToolTip("双击修改")
        val.installEventFilter(self)
        up = QPushButton("▲")
        up.setFixedSize(44, 22)
        up.setStyleSheet("border-radius:11px; font-size:10px;")
        dn = QPushButton("▼")
        dn.setFixedSize(44, 22)
        dn.setStyleSheet("border-radius:11px; font-size:10px;")
        up.clicked.connect(lambda: self.change_dial(val, 1, title))
        dn.clicked.connect(lambda: self.change_dial(val, -1, title))
        self.chev_buttons.extend((up, dn))
        box.addWidget(t, alignment=Qt.AlignmentFlag.AlignHCenter)
        box.addWidget(val, alignment=Qt.AlignmentFlag.AlignHCenter)
        box.addWidget(up, alignment=Qt.AlignmentFlag.AlignHCenter)
        box.addWidget(dn, alignment=Qt.AlignmentFlag.AlignHCenter)
        wrap = QWidget()
        wrap.setLayout(box)
        layout.addWidget(wrap)
        return val

    def change_dial(self, label, delta, which):
        v = int(label.text()) + delta
        if which == WORK:
            v = max(1, min(240, v))
            self.cfg["work"] = v
        else:
            v = max(1, min(120, v))
            self.cfg["break"] = v
        label.setText(str(v))
        self.save_config()

    def change_cycle(self, delta):
        v = max(1, min(20, int(self.cycle_value.text()) + delta))
        self.cycle_value.setText(str(v))
        self.cfg["cycles"] = v
        self.save_config()

    # ---------------- 运行页 ----------------
    def build_timer_page(self):
        page = QFrame()
        page.setObjectName("Page")
        v = QVBoxLayout(page)
        v.setSpacing(4)
        v.setContentsMargins(2, 2, 2, 2)

        self.cycle_top = QLabel("1/2")
        self.cycle_top.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cycle_top.setStyleSheet("font-size:20px; font-weight:600;")
        v.addWidget(self.cycle_top)

        self.phase_label = QLabel("创作中")
        self.phase_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.phase_label.setStyleSheet("font-size:18px;")
        v.addWidget(self.phase_label)

        self.time_label = QLabel("00:00:00")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = QFont()
        f.setPointSize(28)
        f.setBold(True)
        self.time_label.setFont(f)
        v.addWidget(self.time_label)

        ctrls = QHBoxLayout()
        stop = QPushButton("■")
        self.pause_btn = QPushButton("❚❚")
        nxt = QPushButton("⏭")
        for b in (stop, self.pause_btn, nxt):
            b.setFixedSize(46, 46)
            b.setStyleSheet("border-radius:23px; font-size:16px;")
        stop.clicked.connect(self.stop_to_settings)
        self.pause_btn.clicked.connect(self.toggle_pause)
        nxt.clicked.connect(self.skip_phase)
        ctrls.addStretch()
        for b in (stop, self.pause_btn, nxt):
            ctrls.addWidget(b)
        ctrls.addStretch()
        v.addLayout(ctrls)
        v.addStretch()
        self.stack.addWidget(page)

    def show_settings_page(self):
        self.circle.show_ring = False
        self.stack.setCurrentIndex(0)
        self.circle.update()

    def show_timer_page(self):
        self.circle.show_ring = True
        self.stack.setCurrentIndex(1)
        self.circle.update()

    # ---------------- 时钟 ----------------
    def update_clock(self):
        now = QDateTime.currentDateTime()
        d = now.date()
        self.date_label.setText(
            f"{d.year()}/{d.month():02d}/{d.day():02d}("
            f"{WEEKDAYS[d.dayOfWeek() - 1]})")
        self.clock_label.setText(now.toString("HH:mm"))

    # ---------------- 背景 ----------------
    def paintEvent(self, event):
        p = QPainter(self)
        if self.bg_type == "video":
            # 把最新一帧视频画面铺满窗口，再盖一层暗色遮罩
            if self.video_frame is not None:
                img = self.video_frame.toImage()
                if not img.isNull():
                    scaled = img.scaled(
                        self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation)
                    x = (self.width() - scaled.width()) // 2
                    y = (self.height() - scaled.height()) // 2
                    p.drawImage(x, y, scaled)
            else:
                p.fillRect(self.rect(), QColor(27, 29, 36))
            p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        elif not self.bg.isNull():
            scaled = self.bg.scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            p.drawImage(x, y, scaled)
            p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        else:
            p.fillRect(self.rect(), QColor(27, 29, 36))
        p.end()

    def on_video_frame(self, frame):
        """QVideoSink 每来一帧就存下来并触发重绘。"""
        self.video_frame = frame
        self.update()

    def set_background_image(self, path):
        """切换到图片背景：停掉视频、显示图片。"""
        self.video_player.stop()
        self.video_frame = None
        self.bg_type = "image"
        self.bg = QImage(path)
        self.cfg["bg_type"] = "image"
        self.cfg["bg"] = path
        self.update()

    def set_background_video(self, path):
        """切换到视频背景：静音循环播放，逐帧画在 paintEvent 里。"""
        self.bg = QImage()
        self.video_frame = None
        self.bg_type = "video"
        self.cfg["bg_type"] = "video"
        self.cfg["bg"] = path
        self.video_player.setSource(QUrl.fromLocalFile(path))
        self.video_player.play()
        self.update()

    def change_background(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择背景（图片或视频）", "",
            "媒体文件 (*.png *.jpg *.jpeg *.bmp *.webp "
            "*.mp4 *.mkv *.avi *.mov *.webm *.m4v)")
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        try:
            # 复制到 assets 目录，避免原文件被移动/删除后背景丢失
            dest = os.path.join(ASSETS_DIR, f"bg_{int(time.time())}{ext or '.png'}")
            shutil.copy2(path, dest)
        except OSError as e:
            QMessageBox.warning(self, "背景设置失败", str(e))
            return
        if ext in VIDEO_EXTS:
            self.set_background_video(dest)
        else:
            self.set_background_image(dest)
        self.save_config()

    # ---------------- 计时 ----------------
    def start_from_settings(self):
        self.timer.stop()
        self.phase = WORK
        self.cycle = 1
        self.finished = False
        self.phase_total = int(self.work_value.text()) * 60
        self.remaining = self.phase_total
        self.timer.start()
        self.running = True
        self.pause_btn.setText("❚❚")
        self.toolbar_play.setText("⏸")
        self.show_timer_page()
        self.update_display()
        self.play_notify()  # 开始任务提示音

    def apply_always_on_top(self):
        """用 Windows API 设置/取消置顶，不重建窗口，避免关闭按钮失效。"""
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.SetWindowPos.argtypes = [
                wintypes.HWND, wintypes.HWND,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                wintypes.UINT,
            ]
            user32.SetWindowPos.restype = wintypes.BOOL
            hwnd = wintypes.HWND(int(self.winId()))
            HWND_TOPMOST = wintypes.HWND(-1)
            HWND_NOTOPMOST = wintypes.HWND(-2)
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOACTIVATE = 0x0010
            insert_after = (HWND_TOPMOST if self.cfg.get("always_on_top")
                             else HWND_NOTOPMOST)
            ok = user32.SetWindowPos(
                hwnd, insert_after, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            if not ok:
                # 窗口可能尚未完全创建，延迟重试一次
                QTimer.singleShot(100, self.apply_always_on_top)
        except (OSError, AttributeError):
            pass  # 非 Windows 平台忽略

    def toggle_always_on_top(self):
        """切换窗口置顶并保存配置。"""
        self.cfg["always_on_top"] = not self.cfg.get(
            "always_on_top", False)
        self.apply_always_on_top()
        self.save_config()
        self.scale_chrome()  # 更新 📌 按钮高亮状态

    # ---------------- 提示音 ----------------
    def _load_notify_sound(self):
        """加载提示音：用户自定义优先，否则生成默认双音门铃。"""
        path = self.cfg.get("notify_sound")
        if not path or not os.path.exists(path):
            path = self._ensure_default_notify_wav()
        self.notify_sound.setSource(QUrl.fromLocalFile(path))

    def _ensure_default_notify_wav(self):
        """生成默认提示音（A5→E5 双音门铃），返回文件路径。"""
        path = os.path.join(ASSETS_DIR, "notify.wav")
        if os.path.exists(path):
            return path
        import wave, struct, math
        os.makedirs(ASSETS_DIR, exist_ok=True)
        sr = 44100
        notes = [(880, 0.15), (660, 0.25)]  # A5, E5
        frames = []
        for freq, dur in notes:
            n = int(sr * dur)
            for i in range(n):
                t = i / sr
                env = math.exp(-4 * t / dur)  # 指数衰减，像门铃
                val = int(32767 * 0.6 * env * math.sin(2 * math.pi * freq * t))
                frames.append(struct.pack("<h", val))
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(b"".join(frames))
        return path

    def play_notify(self):
        """播放提示音。"""
        if self.notify_sound.source().isEmpty():
            self._load_notify_sound()
        self.notify_sound.play()

    def toolbar_start_pause(self):
        if not self.running and (
                self.finished
                or (self.phase == WORK and self.cycle == 1
                    and self.remaining == self.phase_total)):
            self.start_from_settings()
        else:
            self.toggle_pause()

    def toggle_pause(self):
        if self.running:
            self.timer.stop()
            self.running = False
            self.pause_btn.setText("▶")
            self.toolbar_play.setText("▶")
        else:
            self.timer.start()
            self.running = True
            self.pause_btn.setText("❚❚")
            self.toolbar_play.setText("⏸")

    def stop_to_settings(self):
        self.timer.stop()
        self.running = False
        self.finished = False
        self.phase = WORK
        self.cycle = 1
        self.phase_total = int(self.work_value.text()) * 60
        self.remaining = self.phase_total
        self.toolbar_play.setText("▶")
        self.show_settings_page()
        self.update_display()
        self.play_notify()  # 手动终止也播放提示音

    def skip_phase(self):
        self.phase_finished()
        self.update_display()

    def tick(self):
        self.remaining -= 1
        if self.remaining <= 0:
            self.phase_finished()
        self.update_display()

    def phase_finished(self):
        self.play_notify()  # 阶段结束提示音（创作→休息、休息→下一轮创作）
        if self.phase == WORK:
            if self.cycle >= int(self.cycle_value.text()):
                self.all_done()
                return
            self.phase = BREAK
            self.phase_total = int(self.break_value.text()) * 60
        else:
            self.phase = WORK
            self.cycle += 1
            self.phase_total = int(self.work_value.text()) * 60
        self.remaining = self.phase_total

    def all_done(self):
        self.timer.stop()
        self.running = False
        self.finished = True
        self.remaining = 0
        self.update_display()
        self.play_notify()  # 全部完成提示音
        # 自定义完成弹窗：深色背景、可选自定义图标
        box = QMessageBox(self)
        box.setWindowTitle("Cozy Pomodoro")
        box.setText(f"🎉 全部 {self.cycle_value.text()} 组已完成，休息一下吧！")
        icon_path = os.path.join(ASSETS_DIR, "dialog_icon.png")
        if os.path.exists(icon_path):
            pix = QPixmap(icon_path).scaled(
                64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            box.setIconPixmap(pix)
        else:
            box.setIcon(QMessageBox.Icon.NoIcon)
        box.setStyleSheet("""
            QMessageBox { background-color: #1e2030; }
            QMessageBox QLabel { color: #e8e8e8; font-size: 15px; }
            QPushButton {
                background-color: #3a3d52; color: #e8e8e8;
                border-radius: 6px; padding: 6px 24px; font-size: 14px;
            }
            QPushButton:hover { background-color: #4a4d62; }
        """)
        box.exec()
        self.stop_to_settings()

    def update_display(self):
        self.time_label.setText(fmt_hms(max(self.remaining, 0)))
        self.phase_label.setText("✅ 已完成" if self.finished
                                 else f"{self.phase}中")
        self.cycle_top.setText(f"{self.cycle}/{self.cycle_value.text()}")
        if self.phase_total > 0:
            self.circle.progress = (
                self.phase_total - self.remaining) / self.phase_total
            self.circle.update()

    # ---------------- 方案 ----------------
    # ---------------- 音乐 ----------------
    def import_songs(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "导入歌曲", "",
            "音频 (*.mp3 *.wav *.flac *.m4a *.ogg *.aac)")
        for p in paths:
            if p not in self.songs:  # 去重，避免重复导入同一首
                self.songs.append(p)
        self.cfg["songs"] = self.songs
        self.save_config()

    def play_index(self, i):
        if not self.songs:
            return
        i %= len(self.songs)
        self.song_index = i
        self.player.setSource(QUrl.fromLocalFile(self.songs[i]))
        self.player.play()
        self.song_title.setText(
            os.path.splitext(os.path.basename(self.songs[i]))[0])
        self.bar_play.setText("⏸")
        if self.music_dialog:
            self.music_dialog.select_row(i)
            self.music_dialog.set_play_text("暂停")

    def toggle_music(self):
        if not self.songs:
            QMessageBox.information(self, "音乐", "请先导入歌曲。")
            return
        if self.song_index < 0:
            self.play_index(0)
        elif self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.bar_play.setText("▶")
            if self.music_dialog:
                self.music_dialog.set_play_text("播放")
        else:
            self.player.play()
            self.bar_play.setText("⏸")
            if self.music_dialog:
                self.music_dialog.set_play_text("暂停")

    def next_song(self):
        if self.songs:
            self.play_index(max(self.song_index, 0) + 1)

    def prev_song(self):
        if self.songs:
            self.play_index(max(self.song_index, 0) - 1)

    def auto_play(self):
        if not self.songs:
            return
        if self.song_index < 0:
            self.play_index(0)
        elif self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self.player.play()
            self.bar_play.setText("⏸")

    def on_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.songs:
            self.play_index(self.song_index + 1)

    def on_position(self, pos):
        if not self.seeking:
            self.pos_slider.setValue(pos)
        dur = self.player.duration()
        self.pos_label.setText(f"{fmt_pos(pos)} / {fmt_pos(dur)}")

    def on_duration(self, dur):
        self.pos_slider.setRange(0, dur)

    def seek_done(self):
        self.player.setPosition(self.pos_slider.value())
        self.seeking = False

    def on_audio_device_changed(self):
        """默认音频输出设备变化时（如插拔耳机），重新绑定所有音频输出。"""
        dev = QMediaDevices.defaultAudioOutput()
        if dev.isNull():
            return
        self.audio.setDevice(dev)
        self.video_audio.setDevice(dev)

    def on_volume(self, v):
        self.audio.setVolume(v / 100.0)
        self.cfg["volume"] = v
        self.vol_slider.blockSignals(True)
        self.vol_slider.setValue(v)
        self.vol_slider.blockSignals(False)
        self.save_config()

    def on_autoplay(self, checked):
        self.cfg["autoplay"] = checked
        self.save_config()

    # ---------------- 配置 ----------------
    def save_config(self):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        except OSError:
            pass


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    win = PomodoroWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
