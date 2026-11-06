"""
Скачиватель видео для Android (Kivy + yt-dlp).
Видео по ссылкам из X, Instagram, Reddit, Pinterest, TikTok, YouTube и др.,
аудио из SoundCloud и других открытых источников.
"""

import json
import os
import threading

try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except Exception:
    pass

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.utils import platform

Window.softinput_mode = "below_target"

MODES = ["Видео", "Только аудио", "Видео + аудио отдельно"]
VIDEO_QUALITIES = ["Лучшее", "1080p", "720p", "480p", "360p"]
AUDIO_QUALITIES = ["Лучшее", "до 192 kbps", "до 128 kbps"]
BLOCKED_DOMAINS = ("spotify.com", "music.apple.com")


# ---------- вспомогательные функции ----------
def digits(text):
    return "".join(ch for ch in text if ch.isdigit())


def video_format(label):
    h = digits(label)
    if h:
        return f"best[height<={h}][ext=mp4]/best[height<={h}]/best/bv*"
    return "best[ext=mp4]/best/bv*"


def audio_format(label):
    abr = digits(label)
    if abr:
        return f"ba[ext=m4a][abr<={abr}]/ba[abr<={abr}]/ba[ext=m4a]/ba/b"
    return "ba[ext=m4a]/ba/b"


def default_download_dir():
    if platform == "android":
        try:
            from android.storage import primary_external_storage_path
            return os.path.join(primary_external_storage_path(), "Download", "VideoDownloader")
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Downloads", "VideoDownloader")


def app_external_dir(fallback):
    """Папка приложения на карте памяти: всегда доступна для записи."""
    if platform == "android":
        try:
            from jnius import autoclass
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            return os.path.join(activity.getExternalFilesDir(None).getAbsolutePath(), "VideoDownloader")
        except Exception:
            pass
    return fallback


def writable_dir(path, fallback):
    for candidate in (path, fallback):
        try:
            os.makedirs(candidate, exist_ok=True)
            probe = os.path.join(candidate, ".probe")
            with open(probe, "w") as f:
                f.write("1")
            os.remove(probe)
            return candidate
        except OSError:
            continue
    return None


# ---------- окно настроек ----------
class SettingsPopup(Popup):
    def __init__(self, app, **kwargs):
        super().__init__(title="Настройки", size_hint=(0.92, 0.5), **kwargs)
        self.app = app
        box = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        box.add_widget(Label(text="Папка для загрузок:", size_hint_y=None, height=dp(28), halign="left"))
        self.path_input = TextInput(text=app.settings["download_dir"], multiline=False, size_hint_y=None, height=dp(44))
        box.add_widget(self.path_input)
        self.hint = Label(
            text="Если файлы не сохраняются, выберите папку приложения\nили Movies/VideoDownloader.",
            font_size=dp(12), color=(0.7, 0.7, 0.7, 1),
        )
        box.add_widget(self.hint)
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        row.add_widget(Button(text="Отмена", on_release=lambda *_: self.dismiss()))
        row.add_widget(Button(text="Сохранить", on_release=self.save))
        box.add_widget(row)
        self.content = box

    def save(self, *_):
        path = self.path_input.text.strip()
        if not path:
            self.hint.text = "Укажите путь к папке."
            return
        if not writable_dir(path, None):
            self.hint.text = "В эту папку нельзя записывать. Выберите другую."
            return
        self.app.settings["download_dir"] = path
        self.app.save_settings()
        self.app.update_path_label()
        self.dismiss()


# ---------- главное приложение ----------
class DownloaderApp(App):
    title = "Скачиватель видео"

    def build(self):
        self.settings_file = os.path.join(self.user_data_dir, "settings.json")
        self.settings = {"download_dir": default_download_dir()}
        self.load_settings()
        self.stop_flag = threading.Event()
        self.running = False

        root = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))

        top = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        top.add_widget(Label(text="Ссылки (по одной в строке):", halign="left", valign="middle"))
        top.add_widget(Button(text="Настройки", size_hint_x=None, width=dp(110), on_release=lambda *_: SettingsPopup(self).open()))
        root.add_widget(top)

        self.links = TextInput(hint_text="Вставьте ссылки сюда", size_hint_y=None, height=dp(120))
        root.add_widget(self.links)

        opts = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        self.mode = Spinner(text=MODES[0], values=MODES)
        self.mode.bind(text=lambda *_: self.update_mode())
        opts.add_widget(self.mode)
        root.add_widget(opts)

        quality = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        self.video_q = Spinner(text=VIDEO_QUALITIES[0], values=VIDEO_QUALITIES)
        self.audio_q = Spinner(text=AUDIO_QUALITIES[0], values=AUDIO_QUALITIES)
        quality.add_widget(self.video_q)
        quality.add_widget(self.audio_q)
        root.add_widget(quality)

        album_row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        self.playlist_check = CheckBox(size_hint_x=None, width=dp(40), active=False)
        album_row.add_widget(self.playlist_check)
        album_row.add_widget(Label(text="Альбом / плейлист целиком", halign="left", valign="middle"))
        root.add_widget(album_row)

        self.path_label = Label(size_hint_y=None, height=dp(34), font_size=dp(12), color=(0.6, 0.8, 1, 1),
                                halign="left", valign="middle", shorten=True)
        self.path_label.bind(size=lambda *_: setattr(self.path_label, "text_size", self.path_label.size))
        root.add_widget(self.path_label)

        actions = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        self.start_btn = Button(text="Скачать", on_release=lambda *_: self.start())
        self.stop_btn = Button(text="Стоп", disabled=True, on_release=lambda *_: self.stop())
        actions.add_widget(self.start_btn)
        actions.add_widget(self.stop_btn)
        root.add_widget(actions)

        self.progress = ProgressBar(max=100, value=0, size_hint_y=None, height=dp(14))
        root.add_widget(self.progress)
        self.status = Label(text="Готов к работе", size_hint_y=None, height=dp(28), font_size=dp(13))
        root.add_widget(self.status)

        scroll = ScrollView()
        self.log = Label(text="", size_hint_y=None, halign="left", valign="top", font_size=dp(12))
        self.log.bind(width=lambda *_: setattr(self.log, "text_size", (self.log.width, None)))
        self.log.bind(texture_size=lambda *_: setattr(self.log, "height", self.log.texture_size[1]))
        scroll.add_widget(self.log)
        root.add_widget(scroll)

        self.update_mode()
        self.update_path_label()
        return root

    def on_start(self):
        if platform == "android":
            try:
                from android.permissions import request_permissions
                request_permissions([
                    "android.permission.WRITE_EXTERNAL_STORAGE",
                    "android.permission.READ_EXTERNAL_STORAGE",
                    "android.permission.READ_MEDIA_VIDEO",
                    "android.permission.READ_MEDIA_AUDIO",
                ])
            except Exception:
                pass

    # --- настройки ---
    def load_settings(self):
        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                self.settings.update(json.load(f))
        except (OSError, ValueError):
            pass

    def save_settings(self):
        try:
            os.makedirs(self.user_data_dir, exist_ok=True)
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def update_path_label(self):
        self.path_label.text = "Сохранять в: " + self.settings["download_dir"]

    def update_mode(self):
        mode = self.mode.text
        self.video_q.disabled = mode == MODES[1]
        self.audio_q.disabled = mode == MODES[0]

    # --- вывод ---
    def write_log(self, text):
        Clock.schedule_once(lambda dt: setattr(self.log, "text", self.log.text + text + "\n"))

    def set_progress(self, value, text):
        def apply(dt):
            self.progress.value = value
            self.status.text = text
        Clock.schedule_once(apply)

    def finish(self, ok, failed):
        def apply(dt):
            self.running = False
            self.start_btn.disabled = False
            self.stop_btn.disabled = True
            self.status.text = f"Завершено: успешно {ok}, с ошибкой {failed}"
        Clock.schedule_once(apply)

    # --- загрузка ---
    def start(self):
        if self.running:
            return
        urls = [u.strip() for u in self.links.text.splitlines() if u.strip()]
        if not urls:
            self.status.text = "Вставьте хотя бы одну ссылку"
            return

        wanted = self.settings["download_dir"]
        out_dir = writable_dir(wanted, app_external_dir(os.path.join(self.user_data_dir, "downloads")))
        if not out_dir:
            self.status.text = "Нет доступа к папке для загрузок"
            return
        if out_dir != wanted:
            self.write_log(f"Папка {wanted} недоступна, сохраняю в {out_dir}")

        self.running = True
        self.stop_flag.clear()
        self.start_btn.disabled = True
        self.stop_btn.disabled = False
        self.progress.value = 0

        args = (urls, self.mode.text, self.video_q.text, self.audio_q.text, out_dir, self.playlist_check.active)
        threading.Thread(target=self.run_downloads, args=args, daemon=True).start()

    def stop(self):
        self.stop_flag.set()
        self.status.text = "Останавливаю..."

    def run_downloads(self, urls, mode, video_q, audio_q, out_dir, playlist):
        try:
            import yt_dlp
        except Exception as e:  # noqa: BLE001
            self.write_log(f"Не удалось загрузить yt-dlp: {e}")
            self.finish(0, len(urls))
            return

        app = self

        class UiLogger:
            """Ошибки отдельных треков альбома идут в журнал, загрузка продолжается."""

            def debug(self, msg):
                pass

            def info(self, msg):
                pass

            def warning(self, msg):
                pass

            def error(self, msg):
                app.write_log("  ошибка: " + str(msg).replace("ERROR: ", "")[:200])

        def hook(d):
            if self.stop_flag.is_set():
                raise yt_dlp.utils.DownloadCancelled("Остановлено")
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                done = d.get("downloaded_bytes", 0)
                pct = (done / total * 100) if total else 0
                info = d.get("info_dict") or {}
                idx, count = info.get("playlist_index"), info.get("n_entries")
                prefix = f"Трек {idx}/{count}: " if idx and count else "Скачивание: "
                self.set_progress(pct, f"{prefix}{pct:.0f}%")
            elif d["status"] == "finished":
                self.set_progress(100, "Файл получен")

        def download(url, fmt, suffix="", allow_playlist=False):
            # Для альбомов: папка с названием и номера треков. Для одиночных файлов эти части пустые.
            template = ("%(playlist_title&{}/|)s%(playlist_index&{}. |)s%(title).100B"
                        + suffix + " [%(id)s].%(ext)s")
            opts = {
                "format": fmt,
                "outtmpl": os.path.join(out_dir, template),
                "noplaylist": not allow_playlist,
                "quiet": True,
                "no_warnings": True,
                "noprogress": True,
                "cachedir": False,
                "retries": 5,
                "progress_hooks": [hook],
            }
            if allow_playlist:
                opts["ignoreerrors"] = True  # один недоступный трек не ломает весь альбом
                opts["logger"] = UiLogger()
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.download([url])

        ok = failed = 0
        for i, url in enumerate(urls, 1):
            if self.stop_flag.is_set():
                break
            self.write_log(f"[{i}/{len(urls)}] {url}")

            if any(d in url.lower() for d in BLOCKED_DOMAINS):
                failed += 1
                self.write_log("  Spotify и Apple Music защищены DRM, скачивание не поддерживается. "
                               "Используйте офлайн-режим в самом приложении.")
                continue

            try:
                album = playlist or "/sets/" in url.lower()
                errors = 0
                if mode == MODES[0]:
                    errors += download(url, video_format(video_q), allow_playlist=album) or 0
                elif mode == MODES[1]:
                    errors += download(url, audio_format(audio_q), allow_playlist=album) or 0
                else:
                    errors += download(url, video_format(video_q), allow_playlist=album) or 0
                    errors += download(url, audio_format(audio_q), suffix=" (аудио)", allow_playlist=album) or 0
                ok += 1
                if errors:
                    self.write_log("  готово, но часть треков скачать не удалось (см. выше)")
                else:
                    self.write_log("  готово")
            except yt_dlp.utils.DownloadCancelled:
                self.write_log("  остановлено")
                break
            except Exception as e:  # noqa: BLE001
                failed += 1
                self.write_log("  ошибка: " + str(e).replace("ERROR: ", "")[:300])

        self.finish(ok, failed)


if __name__ == "__main__":
    DownloaderApp().run()
