"""VRC Misa Translator Pro desktop application.

This file is intentionally self-contained for PyInstaller distribution.
Keep runtime constants, translation tables, GUI code, and late compatibility
patches in clearly marked sections so release fixes remain easy to audit.
"""

import collections
import csv
import ctypes
import hashlib
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

def detect_device():
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"

# ===== App metadata and runtime switches =====
APP_NAME = "VRC Misa Translator Pro"
APP_VERSION = "1.12.0"

# Pro edition uses faster-whisper and can select CPU/GPU through CTranslate2.
torch = None
DEFAULT_COMPUTE_DEVICE = "auto"

# ===== Streaming partial preview settings =====
# Increase PARTIAL_INTERVAL_SEC if audio input overflow appears in the log.
PARTIAL_INTERVAL_SEC = 1.2
PARTIAL_RECENT_SEC = 4.0
PARTIAL_MIN_SEC = 1.2

# ===== Voice Activity Detection settings =====
# VAD reduces false starts caused by noise.
# Install optional dependency with: pip install webrtcvad
USE_WEBRTC_VAD = True
WEBRTC_VAD_MODE = 2  # 0: loose / 3: strict
VAD_START_RMS_FACTOR = 0.5
VAD_STOP_RMS_FACTOR = 0.5


class DummyWriter:
    def write(self, *_args, **_kwargs):
        pass

    def flush(self):
        pass


if sys.stdout is None:
    sys.stdout = DummyWriter()

if sys.stderr is None:
    sys.stderr = DummyWriter()


def resource_path(relative_path):
    """Return absolute path to resource for dev/PyInstaller."""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


# ===== Embedded log font =====
# Put this font file at: fonts/NotoSansMono-VariableFont_wdth,wght.ttf
# PyInstaller add-data: --add-data "fonts\\NotoSansMono-VariableFont_wdth,wght.ttf;fonts"
LOG_FONT_FAMILY = "Noto Sans Mono"
LOG_FONT = (LOG_FONT_FAMILY, 9)
LOG_FONT_BOLD = (LOG_FONT_FAMILY, 10, "bold")


def register_embedded_log_font():
    """Register bundled Noto Sans Mono for stable log display on Windows."""
    global LOG_FONT, LOG_FONT_BOLD
    font_path = resource_path(r"fonts/NotoSansMono-VariableFont_wdth,wght.ttf")
    try:
        if os.name == "nt" and os.path.exists(font_path):
            FR_PRIVATE = 0x10
            added = ctypes.windll.gdi32.AddFontResourceExW(font_path, FR_PRIVATE, 0)
            if added:
                LOG_FONT = (LOG_FONT_FAMILY, 9)
                LOG_FONT_BOLD = (LOG_FONT_FAMILY, 10, "bold")
                return True
    except Exception:
        pass

    LOG_FONT = ("Consolas", 9)
    LOG_FONT_BOLD = ("Consolas", 10, "bold")
    return False


register_embedded_log_font()

os.environ["TQDM_DISABLE"] = "1"


# ===== Third-party imports =====
import numpy as np
import sounddevice as sd
import soundfile as sf
from faster_whisper import WhisperModel

try:
    import webrtcvad
except Exception:
    webrtcvad = None

import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText

try:
    from PIL import Image, ImageTk
except Exception:
    Image = None
    ImageTk = None

from deep_translator import GoogleTranslator
from deep_translator.exceptions import TranslationNotFound
from pythonosc.udp_client import SimpleUDPClient

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

try:
    import pygame
except Exception:
    pygame = None


# ===== Defaults and localization tables =====
DEFAULT_BLACKLIST_PHRASES = [
    "ご視聴ありがとうございました",
    "チャンネル登録よろしくお願いします",
    "subscribe",
    "thanks for watching",
    "字幕をご覧ください。",
    "次の動画でお会いしましょう。",
    "そういうことか。",
    "最後までご視聴ありがとうございます。",
    "P.A.E",
    "それは良いそうですね。",
    "日常会話も省略せずに書いてください。",
    "私は日本語の会話です。",
    "日本語の会話は日本語の会話です。",
    "自然な日本語の会話です。",
    "最後に、短い相づちや日常会話を省略せずに書いてください。",
    "日本語の会話は日本語として正確に文字起こしに書いてください。",
    "ADIANDA410です。",
]

LANGUAGE_DESCRIPTIONS = {
    "日本語": {
        "日本語": "日本語",
        "English": "英語",
        "繁體中文": "繁体字中国語",
        "简体中文": "簡体字中国語",
        "한국어": "韓国語",
        "Français": "フランス語",
        "Deutsch": "ドイツ語",
        "Español": "スペイン語",
        "Italiano": "イタリア語",
        "Português": "ポルトガル語",
        "Русский": "ロシア語",
        "ไทย": "タイ語",
        "Tiếng Việt": "ベトナム語",
        "Bahasa Indonesia": "インドネシア語",
        "Nederlands": "オランダ語",
        "Polski": "ポーランド語",
        "Türkçe": "トルコ語",
        "Українська": "ウクライナ語",
        "हिन्दी": "ヒンディー語",
        "العربية": "アラビア語",
        "details_settings": "詳細設定",
        "open_log_folder": "ログフォルダ",
        "chatgpt_translation_mode_short": "自然な翻訳",
        "open_logs_button": "ログフォルダ",
        "delete_model_button": "モデル削除",
        "check_updates_button": "更新ページ",
        "update_button_short": "更新",
        "readme_button": "README",
        "guide_button": "ガイド",
        "advanced_settings": "詳細設定",
        "delete_model": "モデル削除",
        "check_updates": "更新ページ",
        "setup_guide": "ガイド",
        "chatgpt_cost_label": "使用金額",
        "today_short": "Today",
        "cost_alert_enabled": "使用量アラートを有効化",
        "cost_alert_warning": "注意額 ($)",
        "cost_alert_danger": "上限警告額 ($)",
        "cost_spike_enabled": "単発高額アラート",
        "cost_spike_threshold": "1回あたりしきい値 ($)",
        "open_usage_history": "使用履歴",
        "cost_sound_enabled": "通知音を有効化",
        "cost_sound_enabled_jp": "通知音を有効化",
        "cost_sound_volume": "通知音量",
        "popup_enable": "ポップアップを表示",
        "alert_sound_small": "小",
        "alert_sound_large": "大",
        "alert_sound_test": "TEST",
        "usage_history": "使用履歴",
        "cost_alert_title": "ChatGPT使用量アラート",
        "cost_alert_enable": "使用量アラートを有効化",
        "cost_alert_single": "単発高額アラート",
        "cost_alert_threshold": "1回あたりしきい値 ($)",
        "show_popup": "ポップアップを表示",
    },
    "English": {
        "日本語": "Japanese",
        "English": "English",
        "繁體中文": "Traditional Chinese",
        "简体中文": "Simplified Chinese",
        "한국어": "Korean",
        "Français": "French",
        "Deutsch": "German",
        "Español": "Spanish",
        "Italiano": "Italian",
        "Português": "Portuguese",
        "Русский": "Russian",
        "ไทย": "Thai",
        "Tiếng Việt": "Vietnamese",
        "Bahasa Indonesia": "Indonesian",
        "Nederlands": "Dutch",
        "Polski": "Polish",
        "Türkçe": "Turkish",
        "Українська": "Ukrainian",
        "हिन्दी": "Hindi",
        "العربية": "Arabic",
        "chatgpt_cost_label": "Cost",
        "today_short": "Today",
        "cost_alert_enabled": "Enable usage alerts",
        "cost_alert_warning": "Warning amount ($)",
        "cost_alert_danger": "Danger amount ($)",
        "cost_spike_enabled": "Single request high-cost alert",
        "cost_spike_threshold": "Per request threshold ($)",
        "open_usage_history": "Usage History",
        "cost_sound_enabled": "Alert sound",
        "cost_sound_volume": "Alert volume",
        "popup_enable": "Show popup",
        "alert_sound_small": "Low",
        "alert_sound_large": "High",
        "alert_sound_test": "TEST",
        "footer_hint": "Tip: base / small works well on CPU environments.",
        "details_title": "Advanced Settings",
        "details_settings": "Advanced Settings",
        "usage_history": "Usage History",
        "cost_alert_section": "ChatGPT Cost Alert",
    },
}

TRANSLATION_PRESETS = {
    "標準": {
        "translation_mode": "balanced",
        "silence_seconds": 0.8,
        "max_record_seconds": 8.0,
        "start_threshold": 0.010,
        "stop_threshold": 0.006,
        "pre_roll_seconds": 0.6,
    },
    "最速（リアルタイム）": {
        "translation_mode": "fast",
        "silence_seconds": 0.5,
        "max_record_seconds": 5.0,
        "start_threshold": 0.012,
        "stop_threshold": 0.008,
        "pre_roll_seconds": 0.3,
    },
    "高品質": {
        "translation_mode": "natural",
        "silence_seconds": 1.0,
        "max_record_seconds": 10.0,
        "start_threshold": 0.010,
        "stop_threshold": 0.006,
        "pre_roll_seconds": 0.6,
    },
    "精度優先": {
        "translation_mode": "balanced",
        "silence_seconds": 1.1,
        "max_record_seconds": 12.0,
        "start_threshold": 0.008,
        "stop_threshold": 0.0045,
        "pre_roll_seconds": 0.9,
    },
}

LANGUAGE_OPTIONS = {
    "日本語": "ja",
    "English": "en",
    "繁體中文": "zh-TW",
    "简体中文": "zh-CN",
    "한국어": "ko",
    "Français": "fr",
    "Deutsch": "de",
    "Español": "es",
    "Italiano": "it",
    "Português": "pt",
    "Русский": "ru",
    "ไทย": "th",
    "Tiếng Việt": "vi",
    "Bahasa Indonesia": "id",
    "Nederlands": "nl",
    "Polski": "pl",
    "Türkçe": "tr",
    "Українська": "uk",
    "हिन्दी": "hi",
    "العربية": "ar",
}

UI_TEXT = {
    "日本語": {
        "app_title": APP_NAME,
        "settings": "設定",
        "model": "モデル",
        "input_language": "入力言語",
        "target_language": "翻訳先",
        "ui_language": "UI言語",
        "mic_number": "マイク",
        "mic_search": "マイク検索",
        "osc_ip": "OSC IP",
        "osc_port": "OSC Port",
        "start_threshold": "開始しきい値",
        "stop_threshold": "終了しきい値",
        "silence_seconds": "無音終了秒数",
        "max_record_seconds": "最大録音秒数",
        "pre_roll_seconds": "プリロール秒数",
        "max_chat_len": "最大送信文字数",
        "refresh_mics": "マイク一覧更新",
        "test_osc": "OSCテスト送信",
        "start": "開始",
        "stop": "停止",
        "notification": "通知音",
        "save_log": "ログ保存",
        "add_prefix": "マークを付ける",
        "status_group": "状態",
        "input_level": "入力レベル",
        "device": "使用デバイス",
        "original": "原文",
        "original_input": "原文（テキスト入力可 / Enterで翻訳）",
        "text_input_voice_overwrite": "テキスト入力中の音声上書き",
        "translated": "翻訳",
        "log": "ログ",
        "log_filter_label": "ログ表示",
        "status_stopped": "停止中",
        "status_waiting": "待機中",
        "status_recording": "録音中",
        "status_loading": "モデル読込中",
        "status_downloading": "モデル取得中",
        "status_transcribing": "文字起こし中",
        "status_translating": "翻訳中",
        "status_sending": "送信中",
        "status_error": "エラー",
        "osc_test_message": "翻訳UIテストです",
        "osc_test_success": "OSCテスト送信成功",
        "osc_test_failure": "OSCテスト送信失敗",
        "error_title": "エラー",
        "device_refresh_ok": "マイク一覧を更新しました",
        "device_refresh_failed": "デバイス更新失敗",
        "settings_saved": "設定を保存しました",
        "settings_save_failed": "設定保存に失敗しました",
        "start_log": "自動翻訳を開始",
        "stop_log": "停止要求を受け付けました",
        "audio_status": "Audio status",
        "recording_started": "録音開始",
        "max_record_reached": "最大録音時間に到達",
        "recording_stopped": "無音を検出して録音終了",
        "too_short": "短すぎるのでスキップ",
        "too_silent": "ほぼ無音なのでスキップ",
        "model_loading_log": "Whisperモデル {model} を読み込み中",
        "model_loaded": "モデル読み込み完了",
        "transcribe_failed": "認識できませんでした",
        "invalid_text": "不自然なテキストなのでスキップ: {text}",
        "duplicate_text": "同じ内容が短時間に続いたのでスキップ",
        "sent_log": "送信: {text}",
        "record_info": "録音時間: {duration:.2f}s / Peak: {peak:.6f} / RMS: {rms:.6f}",
        "preset": "プリセット",
        "tone": "文体（ChatGPT）",
        "openai_api_key": "OpenAI API Key",
        "compute_device": "Whisper実行デバイス (AUTO / CPU / GPU(cuda))",
        "compute_device_hint": "auto / cpu / gpu(cuda)",
        "natural_chat": "ChatGPT翻訳モード（自然な翻訳）",
        "add_emotion_label": "感情追加",
        "footer_hint": "ヒント: 最速プリセット + 短文で話すと遅延が最小になります",
        "footer_hints": [
            "ヒント: 最速プリセット + 短文で話すと遅延が最小になります",
            "ヒント: 不要な保存済みモデルは削除すると容量を軽くできます",
            "ヒント: Google翻訳は高速、ChatGPT翻訳はより自然です",
            "ヒント: 翻訳プレビューをONにすると送信前に確認できます",
            "ヒント: CPU環境では base / small が扱いやすいです",
            "ヒント：司会進行などで正確な翻訳が必要なときは、音声上書きをOFFにして手入力しましょう。",
        ],
        "footer_hint_api_missing": "ヒント: ChatGPT翻訳モードを使うには OpenAI API Key の設定が必要です",
        "footer_hint_chatgpt_on": "ヒント: ChatGPT翻訳モードはより自然ですが、Google翻訳より少し重くなることがあります",
        "footer_hint_preview_on": "ヒント: 翻訳プレビューON中です。送信前に内容を確認できます",
        "footer_hint": "ヒント：これはPro版です",
        "filter_unnatural": "不自然判定を有効化",
        "preview_mode": "翻訳プレビュー",
        "preview_send": "送信",
        "preview_cancel": "キャンセル",
        "preview_title": "翻訳プレビュー",
        "custom_preset": "カスタム",
        "blacklist": "ブラックリスト（誤認識除外）",
        "blacklist_tooltip": "明らかに変な音声認識結果を除外します",
        "save_blacklist": "ブラックリスト更新",
        "blacklist_section": "ブラックリスト（誤認識除外）",
        "cost_alert_section": "ChatGPT使用量アラート",
        "blacklist_saved": "ブラックリストを保存しました",
        "no_input_device": "入力デバイス未選択",
        "model_cache_state": "モデル状態",
        "model_present": "保存済み",
        "model_missing": "未保存",
        "model_checking": "確認中",
        "model_status": "読込状態",
        "processing_time_label": "処理時間",
        "current_mode_label": "現在モード",
        "device_auto": "AUTO",
        "device_cpu": "CPU",
        "device_gpu": "GPU(cuda)",
        "model_tiny_label": "tiny（超軽量）",
        "model_base_label": "base（高速・軽量）",
        "model_small_label": "small（高精度）",
        "model_medium_label": "medium（かなり高精度・重い）",
        "model_large_label": "large（最高精度・かなり重い）",
        "model_download_progress_label": "モデルDL状態",
        "status_waiting_short": "待機中",
        "status_stopped_short": "停止中",
        "status_transcribing_short": "文字起こし中",
        "status_translating_short": "翻訳中",
        "status_sending_short": "送信中",
        "status_error_short": "エラー",
        "update_idle_short": "待機中",
        "preset_fast_label": "最速（リアルタイム）",
        "preset_normal_label": "標準",
        "preset_quality_label": "高品質",
        "preset_accuracy_label": "精度優先",
        "tone_friendly_label": "フレンドリー",
        "tone_polite_label": "丁寧",
        "compute_device_full_label": "Whisper実行デバイス (AUTO / CPU / GPU(cuda))",
        "model_ready": "準備完了",
        "model_downloading": "モデル取得中",
        "model_loading_state": "モデル読込中",
        "chatgpt_note": "Style adjustment works only during ChatGPT translation.",
        "chatgpt_key_required": "Please enter a valid OpenAI API Key.",
        "chatgpt_tooltip_hint": "Enter an API key to enable ChatGPT Translation Mode (Natural Translation).",
        "chatgpt_quota_fallback": "ChatGPT quota reached, switched to Google Translate.",
        "chatgpt_tooltip_hint": "API Keyを入力すると、ChatGPT翻訳モード（自然な翻訳）が使えるようになります。",
        "chatgpt_quota_fallback": "ChatGPTの利用上限に達したため、Google翻訳に切り替えました。",
        "model_guidance": "モデル案内",
        "translation_mode_label": "現在の翻訳モード",
        "translation_mode_google": "Google翻訳（高速）",
        "translation_mode_chatgpt": "ChatGPT（自然）",
        "model_hint_selected": "選択中: {model}",
        "model_hint_turbo": "高精度・高負荷モードです。軽い環境での使用を推奨します。",
        "model_hint_turbo_gpu": "GPUが使えます。通常利用は base、軽い環境では turbo も有効です。",
        "model_hint_base_cpu": "通常利用は base が安定しやすくおすすめです。",
        "log_filter": "ログ表示",
        "log_filter_all": "すべて",
        "log_filter_error": "エラーのみ",
        "log_filter_warn": "警告のみ",
        "log_filter_success": "成功のみ",
        "setup_guide": "ガイド",
        "setup_guide_title": "ガイド",
        "setup_guide_body": """1. マイクを選択し、入力言語と翻訳先を設定してください。
2. 「開始」ボタンで音声認識と翻訳が始まります。
3. VRChat側でOSC Chatboxを有効にしてください。
4. ログ欄で状態を確認できます（青=情報、緑=成功、赤=エラー）。
5. 設定変更は停止中に行ってください（動作中はエラーの原因になります）。
6. モデルとは、音声を文字に変換するための認識エンジンです。
軽いモデルは速く、重いモデルは高精度ですが、処理時間と保存容量が増えます。
7. モデルは tiny / base / small / medium / large から選べます。通常は base か small がおすすめです。
8. 不要な保存済みモデルは「モデル削除」から消せます。容量を軽くしたい時に便利です。
9. ChatGPTを使うと自然な翻訳になりますが、OpenAI API Keyが必要です。使わない場合はGoogle翻訳（高速）で動作します。
10. medium / large は初回ダウンロードに時間がかかることがあります。モデルDL状態とログを見ながら待ってください。
11. ChatGPT翻訳モードを使用するには、OpenAI API Keyが必要です。
12. 「テキスト入力中の音声上書き」のチェックボックスは音声入力と手入力の優先順位を切り替えます。
OFF時は手入力を優先します。

【OpenAI API Key取得手順】
1. OpenAIのサイトにアクセス
2. アカウントを作成またはログイン
3. API Keysページを開く
4. 「Create new secret key」を押す
5. 表示されたKeyをコピー
6. 本ツールの「OpenAI API Key」に貼り付け

【重要：課金について】
・OpenAI APIは従量課金制です
・使用した分だけ料金が発生します
・自動課金（Pay-as-you-go）が有効だと、利用に応じて継続して課金されます
・使いすぎ防止のため、Usage limit の設定をおすすめします
・目安としては 5〜10ドル程度の上限設定が安心です
・10ドルで、一般的な会話文の長さなら約10万〜30万回の翻訳が可能です（目安）

【エラーについて】
・429 エラーが出た場合は、残高不足または上限到達の可能性があります
・その場合は課金設定やUsage limitを確認してください

OpenAI API Key取得ページ：
https://platform.openai.com/api-keys

課金設定：
https://platform.openai.com/account/billing

使用量確認：
https://platform.openai.com/usage

※ ChatGPT翻訳モードを使わなくても、Google翻訳で通常利用は可能です
※ 文体調整はChatGPT翻訳時のみ有効です。""",
        "auto_update": "Auto update",
        "check_updates": "Check updates",
        "details_settings": "詳細設定",
        "details_title": "詳細設定",
        "details_close": "閉じる",
        "update_status": "Status:",
        "update_idle": "Idle",
        "update_checking": "Status: Checking for updates…",
        "update_not_configured": "Status: Update URL not configured",
        "update_available": "Status: Update available ({version})",
        "update_available_notes": "Status: Update available ({version}) - {notes}",
        "already_latest": "Status: Up to date",
        "update_error": "Status: Update check failed ({error})",
        "update_download_start": "Status: Downloading update…",
        "update_downloaded": "Status: Download complete",
        "update_ready_restart": "Status: Update applied. Please restart.",
        "update_not_supported": "Status: Auto-apply is not supported for this package",
        "update_applied_message": "The update was applied. Please restart the app.",
        "turbo_warning_title": "Turbo Mode Warning",
        "turbo_warning_message": "Turbo (high accuracy / heavy load) uses more VRAM.\n\nIt may become unstable in crowded worlds or heavy scenes.\nBase is recommended for normal use.\n\nPlease use Turbo only in lighter environments.",
        "save_runtime_preset": "Save Current Settings",
        "load_runtime_preset": "Load Saved Settings",
        "delete_model": "モデル削除",
        "open_log_folder": "ログフォルダ",
        "advanced_settings": "詳細設定",
        "guide_button": "ガイド",
        "check_updates_button": "更新ページ",
        "readme_button": "README",
        "update_button_short": "更新",
        "update_site_title": "更新ページ",
        "update_site_message": "最新版の配布ページを開きます。",
        "update_site_booth": "Boothで開く",
        "update_site_itch": "itch.ioで開く",
        "delete_model_button": "モデル削除",
        "open_logs_button": "ログフォルダ",
        "chatgpt_translation_mode_short": "自然な翻訳",
        "open_usage_history": "使用履歴",
        "cost_alert_enabled": "使用量アラートを有効化",
        "cost_alert_warning": "注意額 ($)",
        "cost_alert_danger": "上限警告額 ($)",
        "cost_spike_enabled": "単発高額アラート",
        "cost_spike_threshold": "1回あたりしきい値 ($)",
        "cost_alert_warning_message": "ChatGPT使用金額が ${amount:.2f} を超えました (Today)",
        "cost_alert_danger_message": "ChatGPT使用金額が ${amount:.2f} を超えました。使用量をご確認ください",
        "cost_spike_message": "今回の翻訳コストが高めです: ${amount:.4f}",
        "open_usage_history": "使用履歴",
        "delete_model_confirm": "{model} を削除しますか？\n\n次回使用時は再ダウンロードが必要です。",
        "delete_model_done": "{model} を削除しました",
        "delete_model_missing": "{model} は保存されていません",
        "delete_model_failed": "モデル削除に失敗しました: {error}",
        "popup_do_not_show_again": "再表示しない",
        "runtime_preset_saved": "現在設定を保存しました",
        "runtime_preset_loaded": "保存設定を読み込みました",
        "runtime_preset_missing": "保存済み設定がありません",
        "alert_sound": "通知音",
        "alert_sound_enable": "通知音を有効化",
        "popup_enable": "ポップアップを表示",
        "alert_sound_volume": "通知音量",
        "alert_sound_test": "TEST",
        "cost_sound_volume": "通知音量",
        "cost_sound_enable": "通知音を有効化",
        "alert_sound_small": "小",
        "alert_sound_large": "大",
        "chatgpt_cost_label": "使用金額",
        "today_short": "Today",
    },
    "English": {
        "app_title": APP_NAME,
        "settings": "Settings",
        "model": "Model",
        "input_language": "Input Language",
        "target_language": "Translate To",
        "ui_language": "UI Language",
        "mic_number": "Microphone",
        "mic_search": "Mic Search",
        "osc_ip": "OSC IP",
        "osc_port": "OSC Port",
        "start_threshold": "Start Threshold",
        "stop_threshold": "Stop Threshold",
        "silence_seconds": "Silence Seconds",
        "max_record_seconds": "Max Record Sec",
        "pre_roll_seconds": "Pre-roll Sec",
        "max_chat_len": "Max Chat Length",
        "refresh_mics": "Refresh Mics",
        "test_osc": "Test OSC",
        "start": "Start",
        "stop": "Stop",
        "notification": "Notification",
        "save_log": "Save Log",
        "add_prefix": "Add Mark",
        "status_group": "Status",
        "input_level": "Input Level",
        "device": "Device",
        "original": "Original",
        "original_input": "Original (Text input available / Enter to translate)",
        "text_input_voice_overwrite": "Voice overwrite while typing",
        "translated": "Translated",
        "log": "Log",
        "log_filter_label": "Log Filter",
        "status_stopped": "Stopped",
        "status_waiting": "Waiting",
        "status_recording": "Recording",
        "status_loading": "Loading Model",
        "status_downloading": "Downloading Model",
        "status_transcribing": "Transcribing",
        "status_translating": "Translating",
        "status_sending": "Sending",
        "status_error": "Error",
        "osc_test_message": "Translator UI test",
        "osc_test_success": "OSC test sent successfully",
        "osc_test_failure": "OSC test failed",
        "error_title": "Error",
        "device_refresh_ok": "Microphone list refreshed",
        "device_refresh_failed": "Failed to refresh devices",
        "settings_saved": "Settings saved",
        "settings_save_failed": "Failed to save settings",
        "start_log": "Auto translation started",
        "stop_log": "Stop requested",
        "audio_status": "Audio status",
        "recording_started": "Recording started",
        "max_record_reached": "Maximum recording time reached",
        "recording_stopped": "Silence detected, recording stopped",
        "too_short": "Skipped because it is too short",
        "too_silent": "Skipped because it is almost silent",
        "model_loading_log": "Loading Whisper model {model}",
        "model_loaded": "Model loaded",
        "transcribe_failed": "Could not recognize speech",
        "invalid_text": "Skipped unnatural text: {text}",
        "duplicate_text": "Skipped repeated text in a short interval",
        "sent_log": "Sent: {text}",
        "record_info": "Duration: {duration:.2f}s / Peak: {peak:.6f} / RMS: {rms:.6f}",
        "preset": "Preset",
        "tone": "Tone (ChatGPT)",
        "openai_api_key": "OpenAI API Key",
        "compute_device": "Whisper Device",
        "compute_device_hint": "auto / cpu / gpu(cuda)",
        "natural_chat": "Natural Chat (ChatGPT)",
        "add_emotion_label": "Add Emotion",
        "footer_hint": "Tip: base / small works well on CPU environments.",
        "footer_hints": [
            "Tip: Fast preset + short phrases = lowest delay",
            "Tip: Remove unused saved models to save disk space",
            "Tip: Google Translate is faster, ChatGPT sounds more natural",
            "Tip: Turn on Preview to confirm before sending",
            "Tip: base / small works well on CPU environments.",
            "Tip: When accurate translations are needed during hosting or announcements, turn Voice Override OFF and use manual text input.",
        ],
        "footer_hint": "Tip: This is the Pro version.",
        "filter_unnatural": "Filter unnatural text",
        "custom_preset": "Custom",
        "blacklist": "Blacklist (misrecognition filter)",
        "blacklist_tooltip": "Filters obviously incorrect speech recognition results",
        "save_blacklist": "Update Blacklist",
        "blacklist_section": "Blacklist (Misrecognition Exclusion)",
        "blacklist_saved": "Blacklist saved",
        "no_input_device": "No input device selected",
        "model_cache_state": "Model cache",
        "model_present": "Present",
        "model_missing": "Missing",
        "model_checking": "Checking",
        "model_status": "Load State",
        "processing_time_label": "Process Time",
        "current_mode_label": "Current Mode",
        "device_auto": "AUTO",
        "device_cpu": "CPU",
        "device_gpu": "GPU(cuda)",
        "model_tiny_label": "tiny (Ultra Light)",
        "model_base_label": "base (Fast / Light)",
        "model_small_label": "small (High Accuracy)",
        "model_medium_label": "medium (Very Accurate / Heavy)",
        "model_large_label": "large (Highest Accuracy / Very Heavy)",
        "model_download_progress_label": "Model Download",
        "status_waiting_short": "Waiting",
        "status_stopped_short": "Stopped",
        "status_transcribing_short": "Transcribing",
        "status_translating_short": "Translating",
        "status_sending_short": "Sending",
        "status_error_short": "Error",
        "update_idle_short": "Idle",
        "preset_fast_label": "Fast (Real-time)",
        "preset_normal_label": "Normal",
        "preset_quality_label": "High Quality",
        "preset_accuracy_label": "Accuracy First",
        "tone_friendly_label": "Friendly",
        "tone_polite_label": "Polite",
        "compute_device_full_label": "Whisper Device (AUTO / CPU / GPU(cuda))",
        "model_ready": "Ready",
        "model_downloading": "Downloading",
        "model_loading_state": "Loading",
        "model_guidance": "Model hint",
        "translation_mode_label": "Current Translation Mode",
        "translation_mode_google": "Google Translate (Fast)",
        "translation_mode_chatgpt": "ChatGPT (Natural)",
        "model_hint_selected": "Selected: {model}",
        "model_hint_turbo": "High-accuracy / heavy-load mode. Recommended in lighter environments.",
        "model_hint_turbo_gpu": "GPU is available. Base is recommended for normal use; turbo works best in lighter environments.",
        "model_hint_base_cpu": "Base is usually the most stable choice for normal use.",
        "log_filter": "Log View",
        "log_filter_all": "All",
        "log_filter_error": "Errors",
        "log_filter_warn": "Warnings",
        "log_filter_success": "Success",
        "setup_guide": "Guide",
        "setup_guide_title": "Guide",
        "setup_guide_body": """1. Select your microphone and set the input and target languages.
2. Press Start to begin speech recognition and translation.
3. Enable OSC Chatbox in VRChat.
4. Check the log panel for status updates (blue=info, green=success, red=error).
5. Change settings only while stopped.
6. A model is the recognition engine that converts your voice into text. Lighter models are faster, while heavier models are more accurate but use more time and disk space.
7. You can choose tiny / base / small / medium / large models. base or small is recommended for most users.
8. You can remove saved models with Delete Model if you want to free up storage.
9. ChatGPT provides more natural translation, but requires an OpenAI API Key. Without it, the app uses Google Translate (Fast).
10. medium / large may take time to download the first time. Please check Model Download and the log while waiting.
11. ChatGPT Translation Mode requires an OpenAI API Key.
12. The "Voice Override While Typing" checkbox changes the priority between voice input and manual text input.
When OFF, manual text input takes priority.

[How to get an OpenAI API Key]
1. Open the OpenAI website
2. Sign in or create an account
3. Open the API Keys page
4. Click Create new secret key
5. Copy the displayed Key
6. Paste it into OpenAI API Key in this tool

[Important: Billing Information]
• OpenAI API uses pay-as-you-go pricing
• You are charged only for what you use
• If Pay-as-you-go is enabled, charges will continue based on usage
• To prevent overspending, setting a Usage limit is recommended
• A limit of around $5 to $10 is a safe starting point
• With $10, you can translate approximately 100,000 to 300,000 normal conversation-length messages (estimate)

[About errors]
• If you see a 429 error, you may have run out of quota or reached your limit
• Please check billing and usage limits

OpenAI API Key page:
https://platform.openai.com/api-keys

Billing:
https://platform.openai.com/account/billing

Usage:
https://platform.openai.com/usage

You can still use the app with Google Translate even without ChatGPT Translation Mode.
Style adjustment works only during ChatGPT translation.""",
        "auto_update": "Auto update",
        "check_updates": "Check updates",
        "details_settings": "Advanced Settings",
        "details_title": "Advanced Settings",
        "details_close": "Close",
        "update_status": "Status:",
        "update_idle": "Idle",
        "update_checking": "Status: Checking for updates…",
        "update_not_configured": "Status: Update URL not configured",
        "update_available": "Status: Update available ({version})",
        "update_available_notes": "Status: Update available ({version}) - {notes}",
        "already_latest": "Status: Up to date",
        "update_error": "Status: Update check failed ({error})",
        "update_download_start": "Status: Downloading update…",
        "update_downloaded": "Status: Download complete",
        "update_ready_restart": "Status: Update applied. Please restart.",
        "update_not_supported": "Status: Auto-apply is not supported for this package",
        "update_applied_message": "The update was applied. Please restart the app.",
        "turbo_warning_title": "Turbo Mode Warning",
        "turbo_warning_message": "Turbo (high accuracy / heavy load) uses more VRAM.\n\nIt may become unstable in crowded worlds or heavy scenes.\nBase is recommended for normal use.\n\nPlease use Turbo only in lighter environments.",
        "save_runtime_preset": "Save Current Settings",
        "load_runtime_preset": "Load Saved Settings",
        "delete_model": "Delete Model",
        "open_log_folder": "Open Logs",
        "delete_model_confirm": "Delete {model}?\n\nIt will need to be downloaded again next time.",
        "delete_model_done": "{model} was deleted",
        "delete_model_missing": "{model} is not saved",
        "delete_model_failed": "Failed to delete model: {error}",
        "popup_do_not_show_again": "Do not show again",
        "runtime_preset_saved": "Current settings saved",
        "runtime_preset_loaded": "Saved settings loaded",
        "runtime_preset_missing": "No saved settings found",
        "advanced_settings": "Advanced Settings",
        "guide_button": "Guide",
        "check_updates_button": "Check updates",
        "readme_button": "README",
        "update_button_short": "Update",
        "update_site_title": "Update Page",
        "update_site_message": "Open the latest download page.",
        "update_site_booth": "Open Booth",
        "update_site_itch": "Open itch.io",
        "delete_model_button": "Delete Model",
        "open_logs_button": "Open Logs",
        "chatgpt_translation_mode_short": "Natural Translation",
        "chatgpt_cost_label": "Cost",
        "today_short": "Today",
        "preview_mode": "Preview",
        "footer_hint_preview_on": "Tip: Preview is on. Confirm the result before sending.",
        "footer_hint_chatgpt_on": "Tip: ChatGPT Translation Mode sounds more natural, but can be a little heavier than Google Translate.",
        "footer_hint_api_missing": "Tip: An OpenAI API Key is required to use ChatGPT Translation Mode.",
        "alert_sound_test": "TEST",
        "alert_sound_large": "High",
        "alert_sound_small": "Low",
        "show_popup": "Show popup",
        "popup_enable": "Show popup",
        "cost_sound_volume": "Alert volume",
        "cost_sound_enable": "通知音を有効化",
        "cost_sound_enabled": "Alert sound",
        "cost_sound_enabled_en": "Alert sound",
        "cost_spike_threshold": "Per request threshold ($)",
        "cost_spike_enabled": "Single request high-cost alert",
        "cost_alert_danger": "Danger amount ($)",
        "cost_alert_warning": "Warning amount ($)",
        "cost_alert_enabled": "Enable usage alerts",
        "cost_alert_section": "ChatGPT Cost Alert",
        "usage_history": "Usage History",
        "open_usage_history": "Usage History",
    },
    "繁體中文": {
        "app_title": APP_NAME,
        "settings": "設定",
        "model": "模型",
        "input_language": "輸入語言",
        "target_language": "翻譯目標",
        "ui_language": "UI語言",
        "mic_number": "麥克風",
        "osc_ip": "OSC IP",
        "osc_port": "OSC Port",
        "refresh_mics": "更新麥克風",
        "test_osc": "測試OSC",
        "start": "開始",
        "stop": "停止",
        "notification": "提示音",
        "save_log": "儲存日誌",
        "add_prefix": "加上💬",
        "status_group": "狀態",
        "original": "原文",
        "translated": "翻譯",
        "log": "日誌",
        "blacklist": "黑名單",
        "save_blacklist": "更新黑名單",
        "compute_device": "運算裝置",
        "model_cache_state": "模型快取",
        "model_guidance": "模型建議",
        "auto_update": "自動更新",
        "check_updates": "檢查更新",
        "update_status": "更新狀態",
        "update_not_configured": "尚未設定更新URL",
        "update_available": "有可用更新：{version}",
        "already_latest": "已是最新版本",
    },
    "简体中文": {
        "app_title": APP_NAME,
        "settings": "设置",
        "model": "模型",
        "input_language": "输入语言",
        "target_language": "翻译目标",
        "ui_language": "UI语言",
        "mic_number": "麦克风",
        "osc_ip": "OSC IP",
        "osc_port": "OSC Port",
        "refresh_mics": "刷新麦克风",
        "test_osc": "测试OSC",
        "start": "开始",
        "stop": "停止",
        "notification": "提示音",
        "save_log": "保存日志",
        "add_prefix": "加上💬",
        "status_group": "状态",
        "original": "原文",
        "translated": "翻译",
        "log": "日志",
        "blacklist": "黑名单",
        "save_blacklist": "更新黑名单",
        "compute_device": "计算设备",
        "model_cache_state": "模型缓存",
        "model_guidance": "模型建议",
        "auto_update": "自动更新",
        "check_updates": "检查更新",
        "update_status": "更新状态",
        "update_not_configured": "尚未配置更新URL",
        "update_available": "发现可用更新：{version}",
        "already_latest": "已经是最新版本",
    },
    "한국어": {
        "app_title": APP_NAME,
        "settings": "설정",
        "model": "모델",
        "input_language": "입력 언어",
        "target_language": "번역 대상",
        "ui_language": "UI 언어",
        "mic_number": "마이크",
        "refresh_mics": "마이크 새로고침",
        "test_osc": "OSC 테스트",
        "start": "시작",
        "stop": "정지",
        "save_log": "로그 저장",
        "status_group": "상태",
        "original": "원문",
        "translated": "번역",
        "log": "로그",
        "compute_device": "연산 장치",
        "model_cache_state": "모델 캐시",
        "model_guidance": "모델 안내",
        "auto_update": "자동 업데이트",
        "check_updates": "업데이트 확인",
        "update_status": "업데이트 상태",
        "update_available": "업데이트 가능: {version}",
        "already_latest": "최신 버전입니다",
    },
    "Français": {
        "app_title": APP_NAME,
        "settings": "Paramètres",
        "model": "Modèle",
        "input_language": "Langue d'entrée",
        "target_language": "Traduire vers",
        "ui_language": "Langue UI",
        "mic_number": "Microphone",
        "refresh_mics": "Actualiser les micros",
        "test_osc": "Tester OSC",
        "start": "Démarrer",
        "stop": "Arrêter",
        "save_log": "Enregistrer le journal",
        "status_group": "État",
        "original": "Texte source",
        "translated": "Traduction",
        "log": "Journal",
        "compute_device": "Périphérique de calcul",
        "model_cache_state": "Cache du modèle",
        "model_guidance": "Conseil modèle",
        "auto_update": "Mise à jour auto",
        "check_updates": "Vérifier les mises à jour",
        "update_status": "État des mises à jour",
        "update_available": "Mise à jour disponible : {version}",
        "already_latest": "Déjà à jour",
    },
    "Deutsch": {
        "app_title": APP_NAME,
        "settings": "Einstellungen",
        "model": "Modell",
        "input_language": "Eingabesprache",
        "target_language": "Zielsprache",
        "ui_language": "UI-Sprache",
        "mic_number": "Mikrofon",
        "refresh_mics": "Mikros aktualisieren",
        "test_osc": "OSC testen",
        "start": "Starten",
        "stop": "Stoppen",
        "save_log": "Log speichern",
        "status_group": "Status",
        "original": "Original",
        "translated": "Übersetzung",
        "log": "Log",
        "compute_device": "Rechengerät",
        "model_cache_state": "Modell-Cache",
        "model_guidance": "Modellhinweis",
        "auto_update": "Auto-Update",
        "check_updates": "Updates prüfen",
        "update_status": "Update-Status",
        "update_available": "Update verfügbar: {version}",
        "already_latest": "Bereits aktuell",
    },
    "Español": {
        "app_title": APP_NAME,
        "settings": "Configuración",
        "model": "Modelo",
        "input_language": "Idioma de entrada",
        "target_language": "Traducir a",
        "ui_language": "Idioma de la UI",
        "mic_number": "Micrófono",
        "refresh_mics": "Actualizar micrófonos",
        "test_osc": "Probar OSC",
        "start": "Iniciar",
        "stop": "Detener",
        "save_log": "Guardar registro",
        "status_group": "Estado",
        "original": "Original",
        "translated": "Traducción",
        "log": "Registro",
        "compute_device": "Dispositivo de cálculo",
        "model_cache_state": "Caché del modelo",
        "model_guidance": "Sugerencia del modelo",
        "auto_update": "Actualización automática",
        "check_updates": "Buscar actualizaciones",
        "update_status": "Estado de actualización",
        "update_available": "Actualización disponible: {version}",
        "already_latest": "Ya está actualizado",
    },
}

# --- Added UI translations for zh-TW / zh-CN / ko (v1.10.9 patch) ---
UI_TEXT.setdefault("繁體中文", {}).update(
    {
        "app_title": "VRC Misa Translator Pro",
        "settings": "設定",
        "model": "模型",
        "input_language": "輸入語言",
        "target_language": "翻譯目標",
        "ui_language": "UI 語言",
        "mic_number": "麥克風",
        "mic_search": "搜尋麥克風",
        "osc_ip": "OSC IP",
        "osc_port": "OSC Port",
        "start_threshold": "開始閾值",
        "stop_threshold": "結束閾值",
        "silence_seconds": "靜音結束秒數",
        "max_record_seconds": "最大錄音秒數",
        "pre_roll_seconds": "預錄秒數",
        "max_chat_len": "最大傳送字數",
        "refresh_mics": "更新麥克風列表",
        "test_osc": "傳送 OSC 測試",
        "start": "開始",
        "stop": "停止",
        "notification": "提示音",
        "save_log": "儲存日誌",
        "add_prefix": "加上標記",
        "status_group": "狀態",
        "input_level": "輸入音量",
        "device": "使用裝置",
        "original": "原文",
        "original_input": "原文（可輸入文字 / Enter 翻譯）",
        "text_input_voice_overwrite": "輸入文字時允許語音覆蓋",
        "translated": "翻譯",
        "log": "日誌",
        "log_filter_label": "日誌顯示",
        "status_stopped": "已停止",
        "status_waiting": "待機中",
        "status_recording": "錄音中",
        "status_loading": "模型讀取中",
        "status_downloading": "模型下載中",
        "status_transcribing": "語音辨識中",
        "status_translating": "翻譯中",
        "status_sending": "傳送中",
        "status_error": "錯誤",
        "osc_test_message": "翻譯 UI 測試",
        "osc_test_success": "OSC 測試傳送成功",
        "osc_test_failure": "OSC 測試傳送失敗",
        "error_title": "錯誤",
        "device_refresh_ok": "已更新麥克風列表",
        "device_refresh_failed": "裝置更新失敗",
        "settings_saved": "已儲存設定",
        "settings_save_failed": "設定儲存失敗",
        "start_log": "已開始自動翻譯",
        "stop_log": "已接收停止要求",
        "audio_status": "音訊狀態",
        "recording_started": "開始錄音",
        "max_record_reached": "已達最大錄音時間",
        "recording_stopped": "偵測到靜音，錄音結束",
        "too_short": "太短，已跳過",
        "too_silent": "幾乎無聲，已跳過",
        "model_loading_log": "正在讀取 Whisper 模型 {model}",
        "model_loaded": "模型讀取完成",
        "transcribe_failed": "無法辨識",
        "invalid_text": "文字不自然，已跳過: {text}",
        "duplicate_text": "短時間內重複內容，已跳過",
        "sent_log": "已傳送: {text}",
        "record_info": "錄音時間: {duration:.2f}s / Peak: {peak:.6f} / RMS: {rms:.6f}",
        "preset": "預設",
        "tone": "語氣（ChatGPT）",
        "openai_api_key": "OpenAI API Key",
        "compute_device": "Whisper 執行裝置",
        "compute_device_hint": "auto / cpu / gpu(cuda)",
        "natural_chat": "ChatGPT 翻譯模式（自然翻譯）",
        "add_emotion_label": "加入情緒",
        "footer_hint": "提示: CPU 環境建議使用 base / small。",
        "footer_hints": [
            "提示: 最快預設 + 短句可降低延遲",
            "提示: 刪除不需要的已儲存模型可節省容量",
            "提示: Google 翻譯速度快，ChatGPT 翻譯更自然",
            "提示: 開啟翻譯預覽可在傳送前確認內容",
            "提示: CPU 環境建議使用 base / small",
            "提示: 主持活動需要準確翻譯時，建議關閉語音覆蓋並使用手動輸入。",
        ],
        "footer_hint_api_missing": "提示: 使用 ChatGPT 翻譯模式需要設定 OpenAI API Key",
        "footer_hint_chatgpt_on": "提示: ChatGPT 翻譯較自然，但可能比 Google 翻譯稍重",
        "footer_hint_preview_on": "提示: 翻譯預覽已開啟。傳送前可先確認內容",
        "filter_unnatural": "啟用不自然判定",
        "preview_mode": "翻譯預覽",
        "preview_send": "傳送",
        "preview_cancel": "取消",
        "preview_title": "翻譯預覽",
        "custom_preset": "自訂",
        "blacklist": "黑名單（誤辨識排除）",
        "blacklist_tooltip": "排除明顯錯誤的語音辨識結果",
        "save_blacklist": "更新黑名單",
        "blacklist_section": "黑名單（誤辨識排除）",
        "blacklist_saved": "已儲存黑名單",
        "no_input_device": "尚未選擇輸入裝置",
        "model_cache_state": "模型狀態",
        "model_present": "已儲存",
        "model_missing": "未儲存",
        "model_checking": "確認中",
        "model_status": "讀取狀態",
        "processing_time_label": "處理時間",
        "current_mode_label": "目前模式",
        "device_auto": "AUTO",
        "device_cpu": "CPU",
        "device_gpu": "GPU(cuda)",
        "model_tiny_label": "tiny（超輕量）",
        "model_base_label": "base（高速・輕量）",
        "model_small_label": "small（高精度）",
        "model_medium_label": "medium（相當高精度・較重）",
        "model_large_label": "large（最高精度・很重）",
        "model_download_progress_label": "模型下載狀態",
        "status_waiting_short": "待機中",
        "status_stopped_short": "已停止",
        "status_transcribing_short": "辨識中",
        "status_translating_short": "翻譯中",
        "status_sending_short": "傳送中",
        "status_error_short": "錯誤",
        "update_idle_short": "待機中",
        "preset_fast_label": "最快（即時）",
        "preset_normal_label": "標準",
        "preset_quality_label": "高品質",
        "preset_accuracy_label": "精度優先",
        "tone_friendly_label": "友善",
        "tone_polite_label": "禮貌",
        "compute_device_full_label": "Whisper 執行裝置 (AUTO / CPU / GPU(cuda))",
        "model_ready": "準備完成",
        "model_downloading": "模型下載中",
        "model_loading_state": "模型讀取中",
        "chatgpt_note": "文體調整僅在 ChatGPT 翻譯時有效。",
        "chatgpt_key_required": "請輸入有效的 OpenAI API Key。",
        "chatgpt_tooltip_hint": "輸入 API Key 後可使用 ChatGPT 翻譯模式（自然翻譯）。",
        "chatgpt_quota_fallback": "ChatGPT 使用額度已達上限，已切換為 Google 翻譯。",
        "model_guidance": "模型建議",
        "translation_mode_label": "目前翻譯模式",
        "translation_mode_google": "Google 翻譯（高速）",
        "translation_mode_chatgpt": "ChatGPT（自然）",
        "model_hint_selected": "選擇中: {model}",
        "model_hint_turbo": "高精度・高負載模式。建議在較輕的環境使用。",
        "model_hint_turbo_gpu": "可使用 GPU。一般使用建議 base，較輕環境可使用 turbo。",
        "model_hint_base_cpu": "一般使用 base 較穩定，建議優先使用。",
        "log_filter": "日誌顯示",
        "log_filter_all": "全部",
        "log_filter_error": "僅錯誤",
        "log_filter_warn": "僅警告",
        "log_filter_success": "僅成功",
        "setup_guide": "指南",
        "setup_guide_title": "指南",
        "details_settings": "詳細設定",
        "details_title": "詳細設定",
        "details_close": "關閉",
        "auto_update": "自動更新",
        "check_updates": "檢查更新",
        "update_status": "狀態:",
        "update_idle": "待機中",
        "update_checking": "狀態: 正在檢查更新…",
        "update_not_configured": "狀態: 未設定更新 URL",
        "update_available": "狀態: 有可用更新 ({version})",
        "update_available_notes": "狀態: 有可用更新 ({version}) - {notes}",
        "already_latest": "狀態: 已是最新版本",
        "update_error": "狀態: 更新檢查失敗 ({error})",
        "update_download_start": "狀態: 正在下載更新…",
        "update_downloaded": "狀態: 下載完成",
        "update_ready_restart": "狀態: 更新已套用。請重新啟動。",
        "update_not_supported": "狀態: 此套件不支援自動套用",
        "update_applied_message": "更新已套用。請重新啟動應用程式。",
        "turbo_warning_title": "Turbo 模式警告",
        "turbo_warning_message": "Turbo（高精度 / 高負載）會使用更多 VRAM。\n\n在人多或負載高的世界可能不穩定。\n一般使用建議 base。\n\n請只在較輕的環境使用 Turbo。",
        "save_runtime_preset": "儲存目前設定",
        "load_runtime_preset": "讀取已儲存設定",
        "delete_model": "刪除模型",
        "open_log_folder": "日誌資料夾",
        "advanced_settings": "詳細設定",
        "guide_button": "指南",
        "check_updates_button": "更新頁面",
        "readme_button": "README",
        "update_button_short": "更新",
        "update_site_title": "更新頁面",
        "update_site_message": "開啟最新版發布頁面。",
        "update_site_booth": "在 Booth 開啟",
        "update_site_itch": "在 itch.io 開啟",
        "delete_model_button": "刪除模型",
        "open_logs_button": "日誌資料夾",
        "chatgpt_translation_mode_short": "自然翻譯",
        "open_usage_history": "使用紀錄",
        "usage_history": "使用紀錄",
        "cost_alert_enabled": "啟用使用量提醒",
        "cost_alert_warning": "注意金額 ($)",
        "cost_alert_danger": "上限警告金額 ($)",
        "cost_spike_enabled": "單次高額提醒",
        "cost_spike_threshold": "每次門檻 ($)",
        "cost_alert_warning_message": "ChatGPT 使用金額已超過 ${amount:.2f} (Today)",
        "cost_alert_danger_message": "ChatGPT 使用金額已超過 ${amount:.2f}。請確認使用量。",
        "cost_spike_message": "本次翻譯成本偏高: ${amount:.4f}",
        "delete_model_confirm": "要刪除 {model} 嗎？\n\n下次使用時需要重新下載。",
        "delete_model_done": "已刪除 {model}",
        "delete_model_missing": "{model} 尚未儲存",
        "delete_model_failed": "模型刪除失敗: {error}",
        "popup_do_not_show_again": "不再顯示",
        "runtime_preset_saved": "已儲存目前設定",
        "runtime_preset_loaded": "已讀取儲存設定",
        "runtime_preset_missing": "沒有已儲存的設定",
        "alert_sound": "提示音",
        "alert_sound_enable": "啟用提示音",
        "popup_enable": "顯示彈出視窗",
        "show_popup": "顯示彈出視窗",
        "alert_sound_volume": "提示音量",
        "alert_sound_test": "TEST",
        "cost_sound_volume": "提示音量",
        "cost_sound_enable": "啟用提示音",
        "cost_sound_enabled": "提示音",
        "alert_sound_small": "小",
        "alert_sound_large": "大",
        "chatgpt_cost_label": "使用金額",
        "today_short": "Today",
    }
)
UI_TEXT.setdefault("简体中文", {}).update(
    {
        "app_title": "VRC Misa Translator Pro",
        "settings": "设置",
        "model": "模型",
        "input_language": "输入语言",
        "target_language": "翻译目标",
        "ui_language": "UI 语言",
        "mic_number": "麦克风",
        "mic_search": "搜索麦克风",
        "osc_ip": "OSC IP",
        "osc_port": "OSC Port",
        "start_threshold": "开始阈值",
        "stop_threshold": "结束阈值",
        "silence_seconds": "静音结束秒数",
        "max_record_seconds": "最大录音秒数",
        "pre_roll_seconds": "预录秒数",
        "max_chat_len": "最大发送字数",
        "refresh_mics": "刷新麦克风列表",
        "test_osc": "傳送 OSC 測試",
        "start": "開始",
        "stop": "停止",
        "notification": "提示音",
        "save_log": "保存日志",
        "add_prefix": "添加标记",
        "status_group": "状态",
        "input_level": "输入音量",
        "device": "使用设备",
        "original": "原文",
        "original_input": "原文（可输入文字 / Enter 翻译）",
        "text_input_voice_overwrite": "输入文字时允许语音覆盖",
        "translated": "翻译",
        "log": "日志",
        "log_filter_label": "日志显示",
        "status_stopped": "已停止",
        "status_waiting": "待機中",
        "status_recording": "錄音中",
        "status_loading": "模型读取中",
        "status_downloading": "模型下载中",
        "status_transcribing": "语音识别中",
        "status_translating": "翻译中",
        "status_sending": "发送中",
        "status_error": "錯誤",
        "osc_test_message": "翻譯 UI 測試",
        "osc_test_success": "OSC 测试发送成功",
        "osc_test_failure": "OSC 测试发送失败",
        "error_title": "錯誤",
        "device_refresh_ok": "已刷新麦克风列表",
        "device_refresh_failed": "裝置更新失敗",
        "settings_saved": "已保存设置",
        "settings_save_failed": "設定儲存失敗",
        "start_log": "已开始自动翻译",
        "stop_log": "已收到停止请求",
        "audio_status": "音訊狀態",
        "recording_started": "開始錄音",
        "max_record_reached": "已達最大錄音時間",
        "recording_stopped": "检测到静音，录音结束",
        "too_short": "太短，已跳过",
        "too_silent": "几乎无声，已跳过",
        "model_loading_log": "正在读取 Whisper 模型 {model}",
        "model_loaded": "模型读取完成",
        "transcribe_failed": "无法识别",
        "invalid_text": "文本不自然，已跳过: {text}",
        "duplicate_text": "短时间内内容重复，已跳过",
        "sent_log": "已发送: {text}",
        "record_info": "錄音時間: {duration:.2f}s / Peak: {peak:.6f} / RMS: {rms:.6f}",
        "preset": "预设",
        "tone": "语气（ChatGPT）",
        "openai_api_key": "OpenAI API Key",
        "compute_device": "Whisper 执行设备",
        "compute_device_hint": "auto / cpu / gpu(cuda)",
        "natural_chat": "ChatGPT 翻译模式（自然翻译）",
        "add_emotion_label": "加入情緒",
        "footer_hint": "提示: CPU 环境建议使用 base / small。",
        "footer_hints": [
            "提示: 最快预设 + 短句可降低延迟",
            "提示: 删除不需要的已保存模型可节省容量",
            "提示: Google 翻译速度快，ChatGPT 翻译更自然",
            "提示: 开启翻译预览可在发送前确认内容",
            "提示: CPU 环境建议使用 base / small",
            "提示: 主持活动需要准确翻译时，建议关闭语音覆盖并使用手动输入。",
        ],
        "footer_hint_api_missing": "提示: 使用 ChatGPT 翻译模式需要设置 OpenAI API Key",
        "footer_hint_chatgpt_on": "提示: ChatGPT 翻譯較自然，但可能比 Google 翻譯稍重",
        "footer_hint_preview_on": "提示: 翻译预览已开启。发送前可以先确认内容",
        "filter_unnatural": "启用不自然判定",
        "preview_mode": "翻译预览",
        "preview_send": "发送",
        "preview_cancel": "取消",
        "preview_title": "翻译预览",
        "custom_preset": "自定义",
        "blacklist": "黑名单（误识别排除）",
        "blacklist_tooltip": "排除明显错误的语音识别结果",
        "save_blacklist": "更新黑名单",
        "blacklist_section": "黑名单（误识别排除）",
        "blacklist_saved": "已保存黑名单",
        "no_input_device": "尚未选择输入设备",
        "model_cache_state": "模型状态",
        "model_present": "已保存",
        "model_missing": "未保存",
        "model_checking": "确认中",
        "model_status": "读取状态",
        "processing_time_label": "处理时间",
        "current_mode_label": "当前模式",
        "device_auto": "AUTO",
        "device_cpu": "CPU",
        "device_gpu": "GPU(cuda)",
        "model_tiny_label": "tiny（超轻量）",
        "model_base_label": "base（高速・轻量）",
        "model_small_label": "small（高精度）",
        "model_medium_label": "medium（相当高精度・较重）",
        "model_large_label": "large（最高精度・很重）",
        "model_download_progress_label": "模型下载状态",
        "status_waiting_short": "待機中",
        "status_stopped_short": "已停止",
        "status_transcribing_short": "识别中",
        "status_translating_short": "翻譯中",
        "status_sending_short": "发送中",
        "status_error_short": "錯誤",
        "update_idle_short": "待機中",
        "preset_fast_label": "最快（实时）",
        "preset_normal_label": "標準",
        "preset_quality_label": "高品质",
        "preset_accuracy_label": "精度优先",
        "tone_friendly_label": "友好",
        "tone_polite_label": "礼貌",
        "compute_device_full_label": "Whisper 执行设备 (AUTO / CPU / GPU(cuda))",
        "model_ready": "准备完成",
        "model_downloading": "模型下载中",
        "model_loading_state": "模型读取中",
        "chatgpt_note": "文体调整仅在 ChatGPT 翻译时有效。",
        "chatgpt_key_required": "请输入有效的 OpenAI API Key。",
        "chatgpt_tooltip_hint": "输入 API Key 后可使用 ChatGPT 翻译模式（自然翻译）。",
        "chatgpt_quota_fallback": "ChatGPT 使用额度已达上限，已切换为 Google 翻译。",
        "model_guidance": "模型建议",
        "translation_mode_label": "当前翻译模式",
        "translation_mode_google": "Google 翻译（高速）",
        "translation_mode_chatgpt": "ChatGPT（自然）",
        "model_hint_selected": "選擇中: {model}",
        "model_hint_turbo": "高精度・高负载模式。建议在较轻的环境使用。",
        "model_hint_turbo_gpu": "可使用 GPU。一般使用建议 base，较轻环境可使用 turbo。",
        "model_hint_base_cpu": "一般使用 base 较稳定，建议优先使用。",
        "log_filter": "日志显示",
        "log_filter_all": "全部",
        "log_filter_error": "僅錯誤",
        "log_filter_warn": "僅警告",
        "log_filter_success": "僅成功",
        "setup_guide": "指南",
        "setup_guide_title": "指南",
        "details_settings": "详细设置",
        "details_title": "详细设置",
        "details_close": "关闭",
        "auto_update": "自動更新",
        "check_updates": "检查更新",
        "update_status": "状态:",
        "update_idle": "待机中",
        "update_checking": "状态: 正在检查更新…",
        "update_not_configured": "状态: 未设置更新 URL",
        "update_available": "状态: 有可用更新 ({version})",
        "update_available_notes": "狀態: 有可用更新 ({version}) - {notes}",
        "already_latest": "状态: 已是最新版本",
        "update_error": "状态: 更新检查失败 ({error})",
        "update_download_start": "状态: 正在下载更新…",
        "update_downloaded": "状态: 下载完成",
        "update_ready_restart": "状态: 更新已应用。请重启。",
        "update_not_supported": "狀態: 此套件不支援自動套用",
        "update_applied_message": "更新已套用。請重新啟動應用程式。",
        "turbo_warning_title": "Turbo 模式警告",
        "turbo_warning_message": "Turbo（高精度 / 高负载）会使用更多 VRAM。\n\n在人多或负载高的世界可能不稳定。\n一般使用建议 base。\n\n请只在较轻的环境使用 Turbo。",
        "save_runtime_preset": "保存当前设置",
        "load_runtime_preset": "读取已保存设置",
        "delete_model": "删除模型",
        "open_log_folder": "日志文件夹",
        "advanced_settings": "详细设置",
        "guide_button": "指南",
        "check_updates_button": "更新頁面",
        "readme_button": "README",
        "update_button_short": "更新",
        "update_site_title": "更新頁面",
        "update_site_message": "打开最新版发布页面。",
        "update_site_booth": "在 Booth 開啟",
        "update_site_itch": "在 itch.io 開啟",
        "delete_model_button": "删除模型",
        "open_logs_button": "日志文件夹",
        "chatgpt_translation_mode_short": "自然翻譯",
        "open_usage_history": "使用记录",
        "usage_history": "使用记录",
        "cost_alert_enabled": "启用使用量提醒",
        "cost_alert_warning": "注意金额 ($)",
        "cost_alert_danger": "上限警告金额 ($)",
        "cost_spike_enabled": "单次高额提醒",
        "cost_spike_threshold": "每次阈值 ($)",
        "cost_alert_warning_message": "ChatGPT 使用金額已超過 ${amount:.2f} (Today)",
        "cost_alert_danger_message": "ChatGPT 使用金額已超過 ${amount:.2f}。請確認使用量。",
        "cost_spike_message": "本次翻譯成本偏高: ${amount:.4f}",
        "delete_model_confirm": "要删除 {model} 吗？\n\n下次使用时需要重新下载。",
        "delete_model_done": "已删除 {model}",
        "delete_model_missing": "{model} 尚未保存",
        "delete_model_failed": "模型删除失败: {error}",
        "popup_do_not_show_again": "不再显示",
        "runtime_preset_saved": "已保存当前设置",
        "runtime_preset_loaded": "已读取保存设置",
        "runtime_preset_missing": "没有已保存的设置",
        "alert_sound": "提示音",
        "alert_sound_enable": "启用提示音",
        "popup_enable": "显示弹窗",
        "show_popup": "显示弹窗",
        "alert_sound_volume": "提示音量",
        "alert_sound_test": "TEST",
        "cost_sound_volume": "提示音量",
        "cost_sound_enable": "启用提示音",
        "cost_sound_enabled": "提示音",
        "alert_sound_small": "小",
        "alert_sound_large": "大",
        "chatgpt_cost_label": "使用金额",
        "today_short": "Today",
    }
)
UI_TEXT.setdefault("한국어", {}).update(
    {
        "app_title": "VRC Misa Translator Pro",
        "settings": "설정",
        "model": "모델",
        "input_language": "입력 언어",
        "target_language": "번역 대상",
        "ui_language": "UI 언어",
        "mic_number": "마이크",
        "mic_search": "마이크 검색",
        "osc_ip": "OSC IP",
        "osc_port": "OSC Port",
        "start_threshold": "시작 임계값",
        "stop_threshold": "종료 임계값",
        "silence_seconds": "무음 종료 시간",
        "max_record_seconds": "최대 녹음 시간",
        "pre_roll_seconds": "프리롤 시간",
        "max_chat_len": "최대 전송 글자 수",
        "refresh_mics": "마이크 목록 새로고침",
        "test_osc": "OSC 테스트 전송",
        "start": "시작",
        "stop": "정지",
        "notification": "알림음",
        "save_log": "로그 저장",
        "add_prefix": "표시 붙이기",
        "status_group": "상태",
        "input_level": "입력 레벨",
        "device": "사용 장치",
        "original": "원문",
        "original_input": "원문（텍스트 입력 가능 / Enter로 번역）",
        "text_input_voice_overwrite": "텍스트 입력 중 음성 덮어쓰기",
        "translated": "번역",
        "log": "로그",
        "log_filter_label": "로그 표시",
        "status_stopped": "정지 중",
        "status_waiting": "대기 중",
        "status_recording": "녹음 중",
        "status_loading": "모델 로드 중",
        "status_downloading": "모델 다운로드 중",
        "status_transcribing": "문자 변환 중",
        "status_translating": "번역 중",
        "status_sending": "전송 중",
        "status_error": "오류",
        "osc_test_message": "번역 UI 테스트입니다",
        "osc_test_success": "OSC 테스트 전송 성공",
        "osc_test_failure": "OSC 테스트 전송 실패",
        "error_title": "오류",
        "device_refresh_ok": "마이크 목록을 새로고침했습니다",
        "device_refresh_failed": "장치 새로고침 실패",
        "settings_saved": "설정을 저장했습니다",
        "settings_save_failed": "설정 저장에 실패했습니다",
        "start_log": "자동 번역을 시작했습니다",
        "stop_log": "정지 요청을 받았습니다",
        "audio_status": "오디오 상태",
        "recording_started": "녹음 시작",
        "max_record_reached": "최대 녹음 시간에 도달했습니다",
        "recording_stopped": "무음을 감지하여 녹음을 종료했습니다",
        "too_short": "너무 짧아서 건너뜀",
        "too_silent": "거의 무음이라 건너뜀",
        "model_loading_log": "Whisper 모델 {model} 로드 중",
        "model_loaded": "모델 로드 완료",
        "transcribe_failed": "인식할 수 없었습니다",
        "invalid_text": "부자연스러운 텍스트라 건너뜀: {text}",
        "duplicate_text": "짧은 시간 안에 같은 내용이 반복되어 건너뜀",
        "sent_log": "전송: {text}",
        "record_info": "녹음 시간: {duration:.2f}s / Peak: {peak:.6f} / RMS: {rms:.6f}",
        "preset": "프리셋",
        "tone": "문체（ChatGPT）",
        "openai_api_key": "OpenAI API Key",
        "compute_device": "Whisper 실행 장치",
        "compute_device_hint": "auto / cpu / gpu(cuda)",
        "natural_chat": "ChatGPT 번역 모드（자연스러운 번역）",
        "add_emotion_label": "감정 추가",
        "footer_hint": "팁: CPU 환경에서는 base / small 이 사용하기 쉽습니다.",
        "footer_hints": [
            "팁: 가장 빠른 프리셋 + 짧은 문장으로 말하면 지연이 줄어듭니다",
            "팁: 사용하지 않는 저장 모델은 삭제하면 용량을 줄일 수 있습니다",
            "팁: Google 번역은 빠르고, ChatGPT 번역은 더 자연스럽습니다",
            "팁: 번역 미리보기를 켜면 전송 전에 확인할 수 있습니다",
            "팁: CPU 환경에서는 base / small 이 사용하기 쉽습니다",
            "팁: 진행이나 안내처럼 정확한 번역이 필요할 때는 음성 덮어쓰기를 OFF로 하고 직접 입력하세요.",
        ],
        "footer_hint_api_missing": "팁: ChatGPT 번역 모드를 사용하려면 OpenAI API Key 설정이 필요합니다",
        "footer_hint_chatgpt_on": "팁: ChatGPT 번역 모드는 더 자연스럽지만 Google 번역보다 조금 무거울 수 있습니다",
        "footer_hint_preview_on": "팁: 번역 미리보기가 켜져 있습니다. 전송 전 내용을 확인할 수 있습니다",
        "filter_unnatural": "부자연스러운 문장 필터",
        "preview_mode": "번역 미리보기",
        "preview_send": "전송",
        "preview_cancel": "취소",
        "preview_title": "번역 미리보기",
        "custom_preset": "사용자 지정",
        "blacklist": "블랙리스트（오인식 제외）",
        "blacklist_tooltip": "명백히 이상한 음성 인식 결과를 제외합니다",
        "save_blacklist": "블랙리스트 업데이트",
        "blacklist_section": "블랙리스트（오인식 제외）",
        "blacklist_saved": "블랙리스트를 저장했습니다",
        "no_input_device": "입력 장치 미선택",
        "model_cache_state": "모델 상태",
        "model_present": "저장됨",
        "model_missing": "미저장",
        "model_checking": "확인 중",
        "model_status": "로드 상태",
        "processing_time_label": "처리 시간",
        "current_mode_label": "현재 모드",
        "device_auto": "AUTO",
        "device_cpu": "CPU",
        "device_gpu": "GPU(cuda)",
        "model_tiny_label": "tiny（초경량）",
        "model_base_label": "base（고속・경량）",
        "model_small_label": "small（고정확도）",
        "model_medium_label": "medium（매우 정확・무거움）",
        "model_large_label": "large（최고 정확도・매우 무거움）",
        "model_download_progress_label": "모델 다운로드 상태",
        "status_waiting_short": "대기 중",
        "status_stopped_short": "정지 중",
        "status_transcribing_short": "문자 변환 중",
        "status_translating_short": "번역 중",
        "status_sending_short": "전송 중",
        "status_error_short": "오류",
        "update_idle_short": "대기 중",
        "preset_fast_label": "최속（실시간）",
        "preset_normal_label": "표준",
        "preset_quality_label": "고품질",
        "preset_accuracy_label": "정확도 우선",
        "tone_friendly_label": "친근하게",
        "tone_polite_label": "정중하게",
        "compute_device_full_label": "Whisper 실행 장치 (AUTO / CPU / GPU(cuda))",
        "model_ready": "준비 완료",
        "model_downloading": "모델 다운로드 중",
        "model_loading_state": "모델 로드 중",
        "chatgpt_note": "문체 조정은 ChatGPT 번역 중에만 작동합니다.",
        "chatgpt_key_required": "유효한 OpenAI API Key를 입력해주세요.",
        "chatgpt_tooltip_hint": "API Key를 입력하면 ChatGPT 번역 모드（자연스러운 번역）를 사용할 수 있습니다.",
        "chatgpt_quota_fallback": "ChatGPT 사용 한도에 도달하여 Google 번역으로 전환했습니다.",
        "model_guidance": "모델 안내",
        "translation_mode_label": "현재 번역 모드",
        "translation_mode_google": "Google 번역（고속）",
        "translation_mode_chatgpt": "ChatGPT（자연）",
        "model_hint_selected": "선택 중: {model}",
        "model_hint_turbo": "고정확도・고부하 모드입니다. 가벼운 환경에서 사용하는 것을 권장합니다.",
        "model_hint_turbo_gpu": "GPU를 사용할 수 있습니다. 일반 사용은 base, 가벼운 환경에서는 turbo도 유효합니다.",
        "model_hint_base_cpu": "일반 사용은 base가 안정적이라 권장됩니다.",
        "log_filter": "로그 표시",
        "log_filter_all": "전체",
        "log_filter_error": "오류만",
        "log_filter_warn": "경고만",
        "log_filter_success": "성공만",
        "setup_guide": "가이드",
        "setup_guide_title": "가이드",
        "details_settings": "상세 설정",
        "details_title": "상세 설정",
        "details_close": "닫기",
        "auto_update": "자동 업데이트",
        "check_updates": "업데이트 확인",
        "update_status": "상태:",
        "update_idle": "대기 중",
        "update_checking": "상태: 업데이트 확인 중…",
        "update_not_configured": "상태: 업데이트 URL이 설정되지 않았습니다",
        "update_available": "상태: 업데이트 가능 ({version})",
        "update_available_notes": "상태: 업데이트 가능 ({version}) - {notes}",
        "already_latest": "상태: 최신 버전입니다",
        "update_error": "상태: 업데이트 확인 실패 ({error})",
        "update_download_start": "상태: 업데이트 다운로드 중…",
        "update_downloaded": "상태: 다운로드 완료",
        "update_ready_restart": "상태: 업데이트 적용 완료. 다시 시작해주세요.",
        "update_not_supported": "상태: 이 패키지는 자동 적용을 지원하지 않습니다",
        "update_applied_message": "업데이트가 적용되었습니다. 앱을 다시 시작해주세요.",
        "turbo_warning_title": "Turbo 모드 경고",
        "turbo_warning_message": "Turbo（고정확도 / 고부하）는 VRAM을 더 많이 사용합니다.\n\n사람이 많은 월드나 무거운 장면에서는 불안정할 수 있습니다.\n일반 사용은 base를 권장합니다.\n\nTurbo는 가벼운 환경에서만 사용해주세요.",
        "save_runtime_preset": "현재 설정 저장",
        "load_runtime_preset": "저장 설정 불러오기",
        "delete_model": "모델 삭제",
        "open_log_folder": "로그 폴더",
        "advanced_settings": "상세 설정",
        "guide_button": "가이드",
        "check_updates_button": "업데이트 페이지",
        "readme_button": "README",
        "update_button_short": "업데이트",
        "update_site_title": "업데이트 페이지",
        "update_site_message": "최신 배포 페이지를 엽니다.",
        "update_site_booth": "Booth에서 열기",
        "update_site_itch": "itch.io에서 열기",
        "delete_model_button": "모델 삭제",
        "open_logs_button": "로그 폴더",
        "chatgpt_translation_mode_short": "자연스러운 번역",
        "open_usage_history": "사용 기록",
        "usage_history": "사용 기록",
        "cost_alert_enabled": "사용량 알림 활성화",
        "cost_alert_warning": "주의 금액 ($)",
        "cost_alert_danger": "상한 경고 금액 ($)",
        "cost_spike_enabled": "단발 고액 알림",
        "cost_spike_threshold": "1회당 임계값 ($)",
        "cost_alert_warning_message": "ChatGPT 사용 금액이 ${amount:.2f} 를 넘었습니다 (Today)",
        "cost_alert_danger_message": "ChatGPT 사용 금액이 ${amount:.2f} 를 넘었습니다. 사용량을 확인해주세요",
        "cost_spike_message": "이번 번역 비용이 높습니다: ${amount:.4f}",
        "delete_model_confirm": "{model} 을 삭제할까요?\n\n다음 사용 시 다시 다운로드해야 합니다.",
        "delete_model_done": "{model} 을 삭제했습니다",
        "delete_model_missing": "{model} 은 저장되어 있지 않습니다",
        "delete_model_failed": "모델 삭제에 실패했습니다: {error}",
        "popup_do_not_show_again": "다시 표시하지 않기",
        "runtime_preset_saved": "현재 설정을 저장했습니다",
        "runtime_preset_loaded": "저장 설정을 불러왔습니다",
        "runtime_preset_missing": "저장된 설정이 없습니다",
        "alert_sound": "알림음",
        "alert_sound_enable": "알림음 활성화",
        "popup_enable": "팝업 표시",
        "show_popup": "팝업 표시",
        "alert_sound_volume": "알림음량",
        "alert_sound_test": "TEST",
        "cost_sound_volume": "알림음량",
        "cost_sound_enable": "알림음 활성화",
        "cost_sound_enabled": "알림음",
        "alert_sound_small": "작음",
        "alert_sound_large": "큼",
        "chatgpt_cost_label": "사용 금액",
        "today_short": "Today",
    }
)
# --- End added UI translations ---


# Supplemental labels for expanded UI language support.
UI_TEXT.setdefault("日本語", {}).update(
    {
        "license_button": "ライセンス",
        "speech_recognition_suffix": "音声認識",
        "api_key_optional_note": "ChatGPTを使わない場合不要",
        "alert_sound_enabled_label": "通知音を有効化",
    }
)
UI_TEXT.setdefault("English", {}).update(
    {
        "license_button": "License",
        "speech_recognition_suffix": "speech recognition",
        "api_key_optional_note": "optional if you do not use ChatGPT",
        "alert_sound_enabled_label": "Alert sound",
    }
)
UI_TEXT.setdefault("繁體中文", {}).update(
    {
        "license_button": "授權",
        "speech_recognition_suffix": "語音辨識",
        "api_key_optional_note": "不使用 ChatGPT 時可留空",
        "alert_sound_enabled_label": "啟用提示音",
    }
)
UI_TEXT.setdefault("简体中文", {}).update(
    {
        "license_button": "许可证",
        "speech_recognition_suffix": "语音识别",
        "api_key_optional_note": "不使用 ChatGPT 时可留空",
        "alert_sound_enabled_label": "启用提示音",
    }
)
UI_TEXT.setdefault("한국어", {}).update(
    {
        "license_button": "라이선스",
        "speech_recognition_suffix": "음성 인식",
        "api_key_optional_note": "ChatGPT를 사용하지 않으면 비워도 됩니다",
        "alert_sound_enabled_label": "알림음 활성화",
    }
)


# --- Added localized guide/about/license texts for ja/en/zh-TW/zh-CN/ko (v1.10.9 patch) ---
UI_TEXT.setdefault("日本語", {}).update(
    {
        "about_title": "このアプリについて",
        "about_summary": """VRChat用のリアルタイム音声翻訳ツールです。
Whisperで音声認識し、翻訳結果をOSCでChatboxへ送信できます。

【ライセンス要約】
・個人利用可
・商用利用可（配信・イベント等）
・再配布禁止
・再販売禁止
・ソース公開禁止
・無断改変禁止
・アイコンの別利用禁止""",
        "open_full_license": "ライセンス全文",
        "license_full_title": "ライセンス全文",
        "license_full_header": "LICENSE / README 全文",
        "license_original_header": "原文 LICENSE / README",
        "license_missing_summary": """LICENSE / README が見つからなかったため、要約のみ表示しています。

・個人利用可
・商用利用可（配信・イベント等）
・再配布禁止
・再販売禁止
・ソース公開禁止
・無断改変禁止
・アイコンの別利用禁止""",
    }
)
UI_TEXT.setdefault("English", {}).update(
    {
        "about_title": "About",
        "about_summary": """A real-time speech translation tool for VRChat.
It uses Whisper for speech recognition and can send translated text to the VRChat Chatbox via OSC.

[License Summary]
• Personal use allowed
• Commercial use allowed (streams, events, etc.)
• Redistribution prohibited
• Resale prohibited
• Source publication prohibited
• Unauthorized modification prohibited
• Separate use of the icon prohibited""",
        "open_full_license": "Open Full License",
        "license_full_title": "Full License",
        "license_full_header": "Full LICENSE / README",
        "license_original_header": "Original LICENSE / README",
        "license_missing_summary": """LICENSE / README was not found, so only a summary is shown.

• Personal use allowed
• Commercial use allowed (streams, events, etc.)
• Redistribution prohibited
• Resale prohibited
• Source publication prohibited
• Unauthorized modification prohibited
• Separate use of the icon prohibited""",
    }
)
UI_TEXT.setdefault("繁體中文", {}).update(
    {
        "about_title": "關於本應用程式",
        "about_summary": """這是 VRChat 用的即時語音翻譯工具。
使用 Whisper 進行語音辨識，並可透過 OSC 將翻譯結果傳送到 VRChat Chatbox。

【授權摘要】
・允許個人使用
・允許商業使用（直播、活動等）
・禁止重新散布
・禁止轉售
・禁止公開原始碼
・禁止未經授權的修改
・禁止單獨使用圖示""",
        "open_full_license": "開啟完整授權",
        "license_full_title": "完整授權",
        "license_full_header": "LICENSE / README 全文",
        "license_original_header": "原文 LICENSE / README",
        "license_missing_summary": """找不到 LICENSE / README，因此僅顯示摘要。

・允許個人使用
・允許商業使用（直播、活動等）
・禁止重新散布
・禁止轉售
・禁止公開原始碼
・禁止未經授權的修改
・禁止單獨使用圖示""",
        "setup_guide_body": """1. 請選擇麥克風，並設定輸入語言與翻譯目標語言。
2. 按下「開始」按鈕後，語音辨識與翻譯會開始運作。
3. 請在 VRChat 端啟用 OSC Chatbox。
4. 可在日誌欄確認狀態（藍色=資訊、綠色=成功、紅色=錯誤）。
5. 請在停止狀態下變更設定（運作中變更可能造成錯誤）。
6. 模型是將語音轉成文字的辨識引擎。較輕的模型速度快，較重的模型精度高，但處理時間與儲存容量會增加。
7. 可選擇 tiny / base / small / medium / large。一般建議使用 base 或 small。
8. 不需要的已儲存模型可從「刪除模型」移除，方便釋放容量。
9. 使用 ChatGPT 可得到更自然的翻譯，但需要 OpenAI API Key。不使用時會以 Google 翻譯（高速）運作。
10. medium / large 初次下載可能需要較久時間。請查看模型下載狀態與日誌等待完成。
11. 使用 ChatGPT 翻譯模式需要 OpenAI API Key。
12. 「輸入文字時允許語音覆蓋」會切換語音輸入與手動輸入的優先順序。關閉時會優先使用手動輸入。

【OpenAI API Key 取得步驟】
1. 開啟 OpenAI 網站
2. 建立帳號或登入
3. 開啟 API Keys 頁面
4. 按下「Create new secret key」
5. 複製顯示的 Key
6. 貼到本工具的「OpenAI API Key」欄位

【重要：費用】
・OpenAI API 為依使用量計費
・只會依實際使用量產生費用
・若啟用 Pay-as-you-go，會依使用量持續計費
・建議設定 Usage limit 以避免過度使用
・一開始可設定約 5〜10 美元上限作為安全範圍
・10 美元約可翻譯 100,000〜300,000 則一般長度的對話（估算）

OpenAI API Key 頁面：
https://platform.openai.com/api-keys

費用設定：
https://platform.openai.com/account/billing

使用量確認：
https://platform.openai.com/usage

※ 即使不使用 ChatGPT 翻譯模式，也可以使用 Google 翻譯正常運作
※ 文體調整僅在 ChatGPT 翻譯時有效。""",
    }
)
UI_TEXT.setdefault("简体中文", {}).update(
    {
        "about_title": "关于本应用",
        "about_summary": """这是面向 VRChat 的实时语音翻译工具。
使用 Whisper 进行语音识别，并可通过 OSC 将翻译结果发送到 VRChat Chatbox。

【许可证摘要】
・允许个人使用
・允许商业使用（直播、活动等）
・禁止重新分发
・禁止转售
・禁止公开源代码
・禁止未经授权的修改
・禁止单独使用图标""",
        "open_full_license": "打开完整许可证",
        "license_full_title": "完整许可证",
        "license_full_header": "LICENSE / README 全文",
        "license_original_header": "原文 LICENSE / README",
        "license_missing_summary": """未找到 LICENSE / README，因此仅显示摘要。

・允许个人使用
・允许商业使用（直播、活动等）
・禁止重新分发
・禁止转售
・禁止公开源代码
・禁止未经授权的修改
・禁止单独使用图标""",
        "setup_guide_body": """1. 请选择麦克风，并设置输入语言与翻译目标语言。
2. 点击“开始”按钮后，语音识别与翻译会开始运行。
3. 请在 VRChat 端启用 OSC Chatbox。
4. 可在日志栏查看状态（蓝色=信息、绿色=成功、红色=错误）。
5. 请在停止状态下更改设置（运行中更改可能导致错误）。
6. 模型是将语音转换为文字的识别引擎。较轻的模型速度快，较重的模型精度高，但处理时间与存储空间会增加。
7. 可选择 tiny / base / small / medium / large。一般建议使用 base 或 small。
8. 不需要的已保存模型可从“删除模型”移除，方便释放空间。
9. 使用 ChatGPT 可以获得更自然的翻译，但需要 OpenAI API Key。不使用时会以 Google 翻译（高速）运行。
10. medium / large 初次下载可能需要较长时间。请查看模型下载状态与日志等待完成。
11. 使用 ChatGPT 翻译模式需要 OpenAI API Key。
12. “输入文字时允许语音覆盖”会切换语音输入与手动输入的优先级。关闭时会优先使用手动输入。

【OpenAI API Key 获取步骤】
1. 打开 OpenAI 网站
2. 创建账号或登录
3. 打开 API Keys 页面
4. 点击“Create new secret key”
5. 复制显示的 Key
6. 粘贴到本工具的“OpenAI API Key”栏位

【重要：费用】
・OpenAI API 按使用量计费
・只会按实际使用量产生费用
・若启用 Pay-as-you-go，会根据使用量持续计费
・建议设置 Usage limit 以避免过度使用
・一开始可设置约 5〜10 美元上限作为安全范围
・10 美元约可翻译 100,000〜300,000 条一般长度的对话（估算）

OpenAI API Key 页面：
https://platform.openai.com/api-keys

费用设置：
https://platform.openai.com/account/billing

使用量确认：
https://platform.openai.com/usage

※ 即使不使用 ChatGPT 翻译模式，也可以使用 Google 翻译正常运行
※ 文体调整仅在 ChatGPT 翻译时有效。""",
    }
)
UI_TEXT.setdefault("한국어", {}).update(
    {
        "about_title": "이 앱에 대하여",
        "about_summary": """VRChat용 실시간 음성 번역 도구입니다.
Whisper로 음성을 인식하고, 번역 결과를 OSC를 통해 VRChat Chatbox로 전송할 수 있습니다.

【라이선스 요약】
・개인 사용 가능
・상업적 사용 가능（방송, 이벤트 등）
・재배포 금지
・재판매 금지
・소스 공개 금지
・무단 수정 금지
・아이콘 단독 사용 금지""",
        "open_full_license": "전체 라이선스 열기",
        "license_full_title": "전체 라이선스",
        "license_full_header": "LICENSE / README 전문",
        "license_original_header": "원문 LICENSE / README",
        "license_missing_summary": """LICENSE / README를 찾을 수 없어 요약만 표시합니다.

・개인 사용 가능
・상업적 사용 가능（방송, 이벤트 등）
・재배포 금지
・재판매 금지
・소스 공개 금지
・무단 수정 금지
・아이콘 단독 사용 금지""",
        "setup_guide_body": """1. 마이크를 선택하고 입력 언어와 번역 대상 언어를 설정하세요.
2. 「시작」 버튼을 누르면 음성 인식과 번역이 시작됩니다.
3. VRChat 쪽에서 OSC Chatbox를 활성화하세요.
4. 로그 영역에서 상태를 확인할 수 있습니다（파랑=정보, 초록=성공, 빨강=오류）。
5. 설정 변경은 정지 중에 해주세요（동작 중 변경은 오류의 원인이 될 수 있습니다）。
6. 모델은 음성을 문자로 변환하는 인식 엔진입니다. 가벼운 모델은 빠르고, 무거운 모델은 정확도가 높지만 처리 시간과 저장 용량이 늘어납니다.
7. 모델은 tiny / base / small / medium / large 중에서 선택할 수 있습니다. 일반적으로 base 또는 small을 추천합니다.
8. 필요 없는 저장된 모델은 「모델 삭제」에서 지울 수 있습니다. 저장 공간을 줄이고 싶을 때 유용합니다.
9. ChatGPT를 사용하면 더 자연스러운 번역이 가능하지만 OpenAI API Key가 필요합니다. 사용하지 않을 경우 Google 번역（고속）으로 동작합니다.
10. medium / large는 처음 다운로드할 때 시간이 걸릴 수 있습니다. 모델 다운로드 상태와 로그를 보며 기다려 주세요.
11. ChatGPT 번역 모드를 사용하려면 OpenAI API Key가 필요합니다.
12. 「텍스트 입력 중 음성 덮어쓰기」 체크박스는 음성 입력과 수동 입력의 우선순위를 전환합니다. OFF일 때는 수동 입력을 우선합니다.

【OpenAI API Key 취득 절차】
1. OpenAI 사이트에 접속
2. 계정을 만들거나 로그인
3. API Keys 페이지 열기
4. 「Create new secret key」 클릭
5. 표시된 Key 복사
6. 본 도구의 「OpenAI API Key」에 붙여넣기

【중요：요금】
・OpenAI API는 사용량 기반 과금입니다
・사용한 만큼만 요금이 발생합니다
・Pay-as-you-go가 활성화되어 있으면 사용량에 따라 계속 과금됩니다
・과도한 사용 방지를 위해 Usage limit 설정을 권장합니다
・처음에는 약 5〜10달러 정도의 상한 설정이 안전합니다
・10달러로 일반적인 길이의 대화 약 100,000〜300,000회 번역이 가능합니다（추정）

OpenAI API Key 페이지：
https://platform.openai.com/api-keys

결제 설정：
https://platform.openai.com/account/billing

사용량 확인：
https://platform.openai.com/usage

※ ChatGPT 번역 모드를 사용하지 않아도 Google 번역으로 정상 이용할 수 있습니다
※ 문체 조정은 ChatGPT 번역 시에만 유효합니다.""",
    }
)


# --- Added text for Original -> Blacklist button (v1.11.0) ---
UI_TEXT.setdefault("日本語", {}).update(
    {
        "add_original_to_blacklist": "原文をブラックリストへ送る",
        "add_original_to_blacklist_empty": "ブラックリストに追加する原文がありません",
        "add_original_to_blacklist_done": "原文をブラックリストに追加しました: {text}",
        "add_original_to_blacklist_duplicate": "すでにブラックリストに登録済みです: {text}",
        "add_original_to_blacklist_confirm_title": "ブラックリスト追加確認",
        "add_original_to_blacklist_confirm_message": "本当にこの原文をブラックリストへ送りますか？\n\n{text}",
        "add_original_to_blacklist_cancelled": "ブラックリスト追加をキャンセルしました",
    }
)
UI_TEXT.setdefault("English", {}).update(
    {
        "add_original_to_blacklist": "Send Original to Blacklist",
        "add_original_to_blacklist_empty": "No original text to add to the blacklist",
        "add_original_to_blacklist_done": "Added original text to blacklist: {text}",
        "add_original_to_blacklist_duplicate": "Already in blacklist: {text}",
        "add_original_to_blacklist_confirm_title": "Confirm Blacklist Add",
        "add_original_to_blacklist_confirm_message": "Send this original text to the blacklist?\n\n{text}",
        "add_original_to_blacklist_cancelled": "Cancelled adding original text to blacklist",
    }
)
UI_TEXT.setdefault("繁體中文", {}).update(
    {
        "add_original_to_blacklist": "將原文送至黑名單",
        "add_original_to_blacklist_empty": "沒有可加入黑名單的原文",
        "add_original_to_blacklist_done": "已將原文加入黑名單: {text}",
        "add_original_to_blacklist_duplicate": "已在黑名單中: {text}",
        "add_original_to_blacklist_confirm_title": "確認加入黑名單",
        "add_original_to_blacklist_confirm_message": "確定要將這段原文送至黑名單嗎？\n\n{text}",
        "add_original_to_blacklist_cancelled": "已取消加入黑名單",
    }
)
UI_TEXT.setdefault("简体中文", {}).update(
    {
        "add_original_to_blacklist": "将原文发送到黑名单",
        "add_original_to_blacklist_empty": "没有可加入黑名单的原文",
        "add_original_to_blacklist_done": "已将原文加入黑名单: {text}",
        "add_original_to_blacklist_duplicate": "已在黑名单中: {text}",
        "add_original_to_blacklist_confirm_title": "确认加入黑名单",
        "add_original_to_blacklist_confirm_message": "确定要将这段原文发送到黑名单吗？\n\n{text}",
        "add_original_to_blacklist_cancelled": "已取消加入黑名单",
    }
)
UI_TEXT.setdefault("한국어", {}).update(
    {
        "add_original_to_blacklist": "원문을 블랙리스트로 보내기",
        "add_original_to_blacklist_empty": "블랙리스트에 추가할 원문이 없습니다",
        "add_original_to_blacklist_done": "원문을 블랙리스트에 추가했습니다: {text}",
        "add_original_to_blacklist_duplicate": "이미 블랙리스트에 등록되어 있습니다: {text}",
        "add_original_to_blacklist_confirm_title": "블랙리스트 추가 확인",
        "add_original_to_blacklist_confirm_message": "이 원문을 정말 블랙리스트로 보내시겠습니까?\n\n{text}",
        "add_original_to_blacklist_cancelled": "블랙리스트 추가를 취소했습니다",
    }
)

# --- Blacklist button wording update (v1.11.0) ---
# Detailed settings button updates the edited blacklist, while the main-screen button adds the current original text.
UI_TEXT.setdefault("日本語", {}).update({"save_blacklist": "ブラックリスト更新"})
UI_TEXT.setdefault("English", {}).update({"save_blacklist": "Update Blacklist"})
UI_TEXT.setdefault("繁體中文", {}).update({"save_blacklist": "更新黑名單"})
UI_TEXT.setdefault("简体中文", {}).update({"save_blacklist": "更新黑名单"})
UI_TEXT.setdefault("한국어", {}).update({"save_blacklist": "블랙리스트 업데이트"})


# --- Blacklist guide descriptions (v1.11.0) ---
BLACKLIST_GUIDE_PATCH = {
    "日本語": """【ブラックリスト機能について】

音声認識では、実際に話していない文章が誤って表示されることがあります。
例：動画の締め文、ノイズ、誤認識など。

ブラックリストに登録した文章は、以後自動送信されなくなります。

■ 基本の使い方
・「原文をブラックリストへ送る」
  現在の認識結果をワンクリックで登録できます。

・詳細設定のブラックリスト欄
  手動で追加・削除・編集ができます。
  編集後は「ブラックリスト更新」を押して保存してください。

■ 注意点
・登録しすぎると、通常の会話も除外される場合があります。
・迷った場合は、短いフレーズ単位で登録するのがおすすめです。

■ 活用例
・「ご視聴ありがとうございました」などの誤生成対策
・ノイズや意味不明な文章の除外
・特定の誤認識フレーズの排除""",
    "English": """[About the Blacklist Feature]

Speech recognition may sometimes produce phrases that were never spoken,
such as video-like endings, noise, or hallucinated text.

Any phrase added to the blacklist will be excluded from automatic sending.

■ Basic Usage
・"Send Original to Blacklist"
  Adds the current recognized text with one click.

・Blacklist section in Advanced Settings
  You can manually add, edit, or remove entries.
  Press "Update Blacklist" after editing to save the changes.

■ Notes
・Adding too many entries may filter out normal conversations.
・It is recommended to register short phrases when unsure.

■ Examples
・Filtering phrases like "Thank you for watching"
・Removing noise or meaningless outputs
・Eliminating repeated misrecognition patterns""",
    "繁體中文": """【關於黑名單功能】

語音辨識有時可能會顯示實際上沒有說出的句子。
例如：影片結尾用語、雜音、誤辨識文字等。

加入黑名單的句子之後將不會自動送出。

■ 基本使用方式
・「將原文送至黑名單」
  可將目前的辨識結果一鍵加入黑名單。

・詳細設定中的黑名單欄位
  可手動新增、刪除或編輯項目。
  編輯後請按「更新黑名單」儲存。

■ 注意事項
・加入太多項目時，正常對話也可能被排除。
・不確定時，建議以較短的片語為單位加入。

■ 使用例
・排除「Thank you for watching」等誤生成句子
・排除雜音或無意義輸出
・排除反覆出現的誤辨識句子""",
    "简体中文": """【关于黑名单功能】

语音识别有时可能会显示实际并没有说出的句子。
例如：视频结尾用语、噪音、误识别文字等。

加入黑名单的句子之后将不会自动发送。

■ 基本使用方法
・“将原文送至黑名单”
  可以一键将当前识别结果加入黑名单。

・详细设置中的黑名单栏
  可以手动添加、删除或编辑项目。
  编辑后请点击“更新黑名单”保存。

■ 注意事项
・加入过多项目时，正常对话也可能被排除。
・不确定时，建议以较短的短语为单位添加。

■ 使用例
・排除“Thank you for watching”等误生成句子
・排除噪音或无意义输出
・排除反复出现的误识别句子""",
    "한국어": """【블랙리스트 기능에 대해】

음성 인식에서는 실제로 말하지 않은 문장이 표시될 때가 있습니다.
예: 영상 마무리 문구, 잡음, 오인식 문장 등.

블랙리스트에 등록한 문장은 이후 자동 전송되지 않습니다.

■ 기본 사용 방법
・「원문을 블랙리스트로 보내기」
  현재 인식 결과를 한 번의 클릭으로 등록할 수 있습니다.

・상세 설정의 블랙리스트 항목
  직접 추가, 삭제, 편집할 수 있습니다.
  편집 후에는 「블랙리스트 업데이트」를 눌러 저장하세요.

■ 주의점
・너무 많이 등록하면 정상적인 대화도 제외될 수 있습니다.
・망설여질 때는 짧은 문구 단위로 등록하는 것을 추천합니다.

■ 활용 예
・「Thank you for watching」 같은 오인식 문구 제외
・잡음이나 의미 없는 출력 제외
・반복되는 오인식 패턴 제거""",
}

for _lang, _blacklist_guide_text in BLACKLIST_GUIDE_PATCH.items():
    _ui = UI_TEXT.setdefault(_lang, {})
    _current = _ui.get("setup_guide_body", "")
    if not _current:
        _ui["setup_guide_body"] = _blacklist_guide_text
    elif (
        "ブラックリスト機能" not in _current
        and "Blacklist Feature" not in _current
        and "黑名單功能" not in _current
        and "黑名单功能" not in _current
        and "블랙리스트 기능" not in _current
    ):
        _ui["setup_guide_body"] = _current.rstrip() + "\n\n" + _blacklist_guide_text

APP_DIR = (
    Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    / "VRCMisaTranslator"
)
APP_DIR.mkdir(parents=True, exist_ok=True)

# ① Chatlogフォルダを先に作る
CHATLOG_DIR = APP_DIR / "Chatlog"
CHATLOG_DIR.mkdir(parents=True, exist_ok=True)

# ② そのあとに使う
CHAT_LOG_FILE = (
    CHATLOG_DIR
    / f"VRCMisaTranslator_chat_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt"
)

SETTINGS_FILE = APP_DIR / "config.json"
# 初回設定もこの設定ファイルに保存されます。
# Windowsでは通常: %LOCALAPPDATA%\VRCMisaTranslator\config.json
LOG_FILE = APP_DIR / "VRCMisaTranslator_log.txt"
DIAGNOSTIC_FILE = APP_DIR / "diagnostics.json"
CHATGPT_COST_HISTORY_FILE = APP_DIR / "chatgpt_cost_history.csv"
CHATGPT_NOTICE_SOUND_FILE = "coin_notice.mp3"
CHATGPT_WARNING_SOUND_FILE = "coin_warning.mp3"


CUSTOM_PRESET_FILE = APP_DIR / "saved_runtime_preset.json"
MODEL_DIR = APP_DIR / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

MODEL_LOAD_TIMEOUT_SECONDS = 180
MODEL_DOWNLOAD_TIMEOUT_SECONDS = 300
MODEL_DOWNLOAD_LOG_INTERVAL_SECONDS = 5
SUPPORTED_MODEL_NAMES = ["tiny", "base", "small", "medium", "large"]
UPDATE_INFO_URL = (
    "https://magicalshooter.github.io/vrc-misa-translator-update/update.json"
)
README_URL = "https://magicalshooter.github.io/vrc-misa-translator/"
UPDATE_DOWNLOAD_DIR = APP_DIR / "updates"
UPDATE_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

MODEL_DISPLAY_NAMES = {
    "tiny": "tiny",
    "base": "base",
    "small": "small",
    "medium": "medium",
    "large": "large",
}

OPENAI_PRICING_USD_PER_1M_TOKENS = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
}


# --- ChatGPT mode label / tooltip unified wording (v1.11.1) ---
for _lang, _text in {
    "日本語": "自然な翻訳（ChatGPTモード）",
    "English": "Natural Translation (ChatGPT Mode)",
    "繁體中文": "自然翻譯（ChatGPT模式）",
    "简体中文": "自然翻译（ChatGPT模式）",
    "한국어": "자연스러운 번역 (ChatGPT 모드)",
}.items():
    UI_TEXT.setdefault(_lang, {})["chatgpt_translation_mode_short"] = _text

for _lang, _text in {
    "日本語": "より自然な翻訳・文体調整などを行います（APIキーが必要）",
    "English": "Provides more natural translation and tone adjustment (API key required)",
    "繁體中文": "提供更自然的翻譯與語氣調整（需要API Key）",
    "简体中文": "提供更自然的翻译和语气调整（需要API Key）",
    "한국어": "더 자연스러운 번역 및 문체 조정을 제공합니다 (API 키 필요)",
}.items():
    UI_TEXT.setdefault(_lang, {})["chatgpt_tooltip_hint"] = _text


# ===== Runtime helpers =====
def detect_runtime_mode() -> str:
    if not getattr(sys, "frozen", False):
        return "python-script"
    try:
        exe_dir = Path(sys.executable).resolve().parent
        meipass = Path(getattr(sys, "_MEIPASS", exe_dir)).resolve()
        if meipass != exe_dir and (
            "temp" in str(meipass).lower() or "_mei" in meipass.name.lower()
        ):
            return "pyinstaller-onefile (heuristic)"
        return "pyinstaller-onedir (heuristic)"
    except Exception:
        return "pyinstaller-frozen"


def cuda_available() -> bool:
    try:
        return bool(torch is not None and torch.cuda.is_available())
    except Exception:
        return False


LANGUAGE_PROMPTS = {
    "ja": "これは日本語の会話です。自然な日本語として正確に文字起こししてください。短い相づちや日常会話も省略せずに書いてください。",
    "en": "This is an English conversation. Transcribe it naturally and accurately.",
    "zh-CN": "这是简体中文对话。请自然、准确地转写。",
    "zh-TW": "這是繁體中文對話。請自然、準確地轉寫。",
    "ko": "이것은 한국어 대화입니다. 자연스럽고 정확하게 받아쓰세요.",
    "fr": "Ceci est une conversation en français. Transcrivez-la de façon naturelle et précise.",
    "de": "Dies ist ein deutsches Gespräch. Bitte natürlich und präzise transkribieren.",
    "es": "Esta es una conversación en español. Transcríbela de forma natural y precisa.",
    "it": "Questa è una conversazione in italiano. Trascrivila in modo naturale e preciso.",
    "pt": "Esta é uma conversa em português. Transcreva de forma natural e precisa.",
    "ru": "Это разговор на русском языке. Расшифруйте его естественно и точно.",
    "th": "นี่คือบทสนทนาภาษาไทย โปรดถอดเสียงอย่างเป็นธรรมชาติและแม่นยำ",
    "vi": "Đây là cuộc trò chuyện bằng tiếng Việt. Hãy chép lại tự nhiên và chính xác.",
    "id": "Ini adalah percakapan dalam bahasa Indonesia. Transkripsikan secara alami dan akurat.",
    "nl": "Dit is een gesprek in het Nederlands. Transcribeer het natuurlijk en nauwkeurig.",
    "pl": "To jest rozmowa po polsku. Przepisz ją naturalnie i dokładnie.",
    "tr": "Bu bir Türkçe konuşmadır. Doğal ve doğru şekilde yazıya dökün.",
    "uk": "Це розмова українською мовою. Точно й природно розшифруйте її.",
    "hi": "यह हिंदी में बातचीत है। इसे स्वाभाविक और सही रूप में लिप्यंतरित करें।",
    "ar": "هذا حوار باللغة العربية. يرجى تفريغه بشكل طبيعي ودقيق.",
}

COMMON_HALLUCINATIONS = [
    "thank you for watching",
]

MAX_LOG_LINES = 500


# --- Removed old tooltip wording override (v1.11.1 final) ---

# v1.11.2 UI label cleanup: preset labels + real-time caption naming
UI_TEXT.setdefault("日本語", {}).update(
    {
        "preset_fast_label": "最速：反応が速い",
        "preset_normal_label": "通常：バランス",
        "preset_quality_label": "高品質：精度優先",
        "streaming_preview": "リアルタイム字幕",
        "streaming_preview_tooltip": "話している途中から字幕を表示します。OFFの場合は確定後のみ表示されます。",
    }
)
UI_TEXT.setdefault("English", {}).update(
    {
        "preset_fast_label": "Fast: quick response",
        "preset_normal_label": "Normal: balanced",
        "preset_quality_label": "High Quality: accuracy first",
        "streaming_preview": "Real-time captions",
        "streaming_preview_tooltip": "Shows captions while you are speaking. When OFF, text appears only after it is finalized.",
    }
)
UI_TEXT.setdefault("繁體中文", {}).update(
    {
        "preset_fast_label": "最快：反應快",
        "preset_normal_label": "標準：平衡",
        "preset_quality_label": "高品質：精度優先",
        "streaming_preview": "即時字幕",
        "streaming_preview_tooltip": "說話途中顯示字幕。關閉時只會在確定後顯示。",
    }
)
UI_TEXT.setdefault("简体中文", {}).update(
    {
        "preset_fast_label": "最快：反应快",
        "preset_normal_label": "标准：平衡",
        "preset_quality_label": "高品质：精度优先",
        "streaming_preview": "实时字幕",
        "streaming_preview_tooltip": "说话过程中显示字幕。关闭时仅在最终确定后显示。",
    }
)
UI_TEXT.setdefault("한국어", {}).update(
    {
        "preset_fast_label": "최속: 반응 빠름",
        "preset_normal_label": "보통: 균형",
        "preset_quality_label": "고품질: 정확도 우선",
        "streaming_preview": "실시간 자막",
        "streaming_preview_tooltip": "말하는 중 자막을 표시합니다. OFF일 때는 확정 후에만 표시됩니다.",
    }
)

# v1.11.2: API key privacy tooltip text
UI_TEXT.setdefault("日本語", {}).update(
    {
        "api_key_visibility_tooltip": "APIキーの表示 / 非表示を切り替えます。配信やスクリーンショット時の漏えい防止に役立ちます。"
    }
)
UI_TEXT.setdefault("English", {}).update(
    {
        "api_key_visibility_tooltip": "Show / hide the API key. Useful to prevent leaks during streaming or screenshots."
    }
)
UI_TEXT.setdefault("繁體中文", {}).update(
    {
        "api_key_visibility_tooltip": "切換 API Key 的顯示 / 隱藏，可避免直播或截圖時外洩。"
    }
)
UI_TEXT.setdefault("简体中文", {}).update(
    {
        "api_key_visibility_tooltip": "切换 API Key 的显示 / 隐藏，可避免直播或截图时泄露。"
    }
)
UI_TEXT.setdefault("한국어", {}).update(
    {
        "api_key_visibility_tooltip": "API Key 표시 / 숨김을 전환합니다. 방송이나 스크린샷에서 노출을 방지할 수 있습니다."
    }
)


# ===== Main application window =====
class VRChatTranslatorGUI:
    def __init__(self, root):
        self.root = root
        self.root.geometry("800x840")
        self.root.minsize(800, 740)
        self.root.maxsize(800, 1400)
        self.root.resizable(False, True)

        self.running = False
        self.worker_thread = None
        self.model = None
        self.last_sent_text = ""
        self.last_sent_time = 0.0
        self._current_status_key = "status_stopped"

        self.model_name = tk.StringVar(value="base")
        self.compute_device = tk.StringVar(value="CPU")
        self.model_cache_text = tk.StringVar(value="")
        self.model_download_progress_text = tk.StringVar(value="-")
        self.model_guidance_text = tk.StringVar(value="")
        self.translation_mode_status_text = tk.StringVar(value="")
        self.update_status_text = tk.StringVar(value="")
        self._last_status_key = "status_stopped"
        self._last_model_status_key = "status_stopped"
        self._last_update_status_key = "update_idle"
        self._hint_index = 0
        self._hint_job = None
        self.auto_update_enabled = tk.BooleanVar(value=False)
        self.latest_update_info = None
        self.model_timeout_seconds = tk.IntVar(value=MODEL_LOAD_TIMEOUT_SECONDS)
        self.input_lang = tk.StringVar(value="日本語")
        self.target_lang = tk.StringVar(value="繁體中文")
        self.ui_lang = tk.StringVar(value="日本語")
        self.osc_ip = tk.StringVar(value="127.0.0.1")
        self.osc_port = tk.IntVar(value=9000)
        self.send_notification = tk.BooleanVar(value=False)
        self.mic_device = tk.StringVar(value="")
        self.mic_search = tk.StringVar(value="")
        self.mic_device_map = {}
        self._all_input_devices = []

        self.start_threshold = tk.DoubleVar(value=0.012)
        self.stop_threshold = tk.DoubleVar(value=0.010)
        self.silence_seconds = tk.DoubleVar(value=0.9)
        self.max_record_seconds = tk.DoubleVar(value=8.0)
        self.min_record_seconds = tk.DoubleVar(value=1.2)
        self.pre_roll_seconds = tk.DoubleVar(value=0.8)
        self.skip_same_seconds = tk.DoubleVar(value=8.0)

        self.status_text = tk.StringVar(value="")
        self.level_text = tk.StringVar(value="RMS: 0.000000")
        self.detected_device_text = tk.StringVar(value="未取得")
        self.model_status_text = tk.StringVar(value="")
        self.processing_time_text = tk.StringVar(value="-")
        self.current_mode_text = tk.StringVar(value="-")
        self.log_filter_display = tk.StringVar(value="all")
        self.log_history = []
        self._first_launch = not SETTINGS_FILE.exists()
        self.first_launch_flag = tk.BooleanVar(value=self._first_launch)
        self.save_log = tk.BooleanVar(value=True)
        self.prefix_message = tk.BooleanVar(value=True)
        self.filter_unnatural = tk.BooleanVar(value=True)
        self.preview_mode = tk.BooleanVar(value=False)
        self.preview_mode.trace_add("write", lambda *_: self._update_footer_hint())
        self.streaming_preview = tk.BooleanVar(value=True)
        self.high_accuracy_vad = tk.BooleanVar(value=True)
        # 初回の音声検出モード設定が完了したかどうか
        # 設定ファイルに vad_setup_done が無い旧環境では False 扱いにして、
        # .py 直起動でも初回ポップアップを出せるようにします。
        self.vad_setup_done = False
        self.text_input_voice_overwrite = tk.BooleanVar(value=True)
        self.max_chat_len = tk.IntVar(value=70)
        self.translation_mode = tk.StringVar(value="balanced")
        self.translation_preset = tk.StringVar(value="標準")
        self.preset_name = self.translation_preset
        self.use_chatgpt_natural = tk.BooleanVar(value=False)
        self.use_chatgpt_natural.trace_add(
            "write", lambda *_: self._update_footer_hint()
        )
        self.use_chatgpt_natural.trace_add(
            "write", lambda *_: self.update_chatgpt_checkbox_color()
        )
        self.add_emotion = tk.BooleanVar(value=False)
        self.tone_style = tk.StringVar(value="フレンドリー")
        self.openai_api_key = tk.StringVar(value=os.getenv("OPENAI_API_KEY", ""))
        self.api_key_visible = tk.BooleanVar(value=False)
        self.chatgpt_cost_text = tk.StringVar(value="")
        self.chatgpt_today_cost_value = 0.0
        self.chatgpt_cost_date = self._today_str()
        self.cost_alert_enabled = tk.BooleanVar(value=True)
        self.cost_alert_warning = tk.DoubleVar(value=1.0)
        self.cost_alert_danger = tk.DoubleVar(value=3.0)
        self.cost_spike_enabled = tk.BooleanVar(value=True)
        self.cost_spike_threshold = tk.DoubleVar(value=0.05)
        self.cost_sound_enabled = tk.BooleanVar(value=True)
        self.popup_enabled = tk.BooleanVar(value=True)
        self.cost_sound_volume = tk.IntVar(value=70)
        self.cost_warning_alerted_date = ""
        self.cost_danger_alerted_date = ""
        self._sound_ready = False
        self._last_sound_time = 0.0
        self._toast_windows = []
        self.blacklist_phrases = list(DEFAULT_BLACKLIST_PHRASES)
        self.blacklist_text_var = tk.StringVar(value="\n".join(self.blacklist_phrases))
        self._applying_preset = False
        self.model_ready = False
        self._warned_heavy_models = set()
        self.popup_suppressions = {}
        self.detected_device = detect_device()
        self.resolved_compute_device = "GPU" if self.detected_device == "cuda" else "CPU"
        self.runtime_mode = detect_runtime_mode()

        self.load_settings()
        # 設定ファイルが既に存在していても、VAD初回設定が未完了なら
        # 初回ガイド（音声検出モード選択）を表示します。
        try:
            if not bool(getattr(self, "vad_setup_done", False)):
                self.first_launch_flag.set(True)
        except Exception:
            pass
        self._initialize_chatgpt_cost_tracking()
        self._sanitize_runtime_options()
        self.apply_preset(self.translation_preset.get())
        self._apply_icon()

        self._build_ui()
        try:
            self.root.after(300, lambda: self.log(f"設定ファイル: {SETTINGS_FILE}"))
        except Exception:
            pass
        self._setup_custom_preset_tracking()
        self.model_name.trace_add("write", lambda *_: self.update_model_cache_text())
        self.model_name.trace_add("write", lambda *_: self.update_model_guidance_text())
        self.model_name.trace_add("write", lambda *_: self.update_current_mode_text())
        self.use_chatgpt_natural.trace_add(
            "write", lambda *_: self.update_translation_mode_text()
        )
        self.translation_mode.trace_add(
            "write", lambda *_: self.update_translation_mode_text()
        )
        self.openai_api_key.trace_add(
            "write",
            lambda *_: (self.update_chatgpt_ui_state(), self.update_chatgpt_tooltip()),
        )
        self.openai_api_key.trace_add("write", lambda *_: self._update_footer_hint())
        self.openai_api_key.trace_add(
            "write", lambda *_: self.update_chatgpt_checkbox_color()
        )
        self.refresh_ui_language()
        self._schedule_footer_hint_rotation()
        self.update_model_guidance_text()
        self.update_translation_mode_text()
        self.update_chatgpt_ui_state()
        self.update_chatgpt_checkbox_color()
        self.update_chatgpt_tooltip()
        self.update_chatgpt_cost_display()
        self.update_update_status_text(self.tr("update_idle"))
        self._refresh_devices()
        self.root.after(200, self._log_startup_diagnostics)
        self.root.after(250, self._apply_header_icon_runtime)
        self.root.after(300, self.update_model_cache_text)
        self.root.after(350, lambda: self.set_model_status(self.tr("status_stopped")))
        self.root.after(
            352,
            lambda: self.status_text.set(
                self.get_localized_status_text("status_stopped")
            ),
        )
        self.root.after(360, lambda: self.compute_device.set(self.tr("device_cpu")))
        self.root.after(
            365,
            lambda: (
                self.model_name.set(self.tr("model_base_label"))
                if self._first_launch
                else None
            ),
        )
        self.root.after(800, self.show_first_launch_guide)
        self.root.after(
            366,
            lambda: (
                self.translation_preset.set(self.tr("preset_normal_label"))
                if self._first_launch
                else None
            ),
        )
        self.root.after(
            367,
            lambda: (
                self.tone_style.set(self.tr("tone_friendly_label"))
                if self._first_launch
                else None
            ),
        )
        self.root.after(370, self.update_current_mode_text)
        self.root.after(380, lambda: self.set_processing_time(None))
        if self._first_launch:
            self.root.after(700, self.show_setup_guide)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._update_start_button_appearance()

    def _apply_header_icon_runtime(self):
        try:
            if not hasattr(self, "header_icon_label"):
                try:
                    self.log("[diag] header icon skipped: header_icon_label not ready")
                except Exception:
                    pass
                return

            icon_candidates = [
                Path(resource_path("app.png")),
                Path(sys.executable).resolve().parent / "app.png",
                Path.cwd() / "app.png",
                Path("app.png"),
            ]

            loaded = False
            for icon_path in icon_candidates:
                try:
                    try:
                        self.log(f"[diag] header icon check: {icon_path}")
                    except Exception:
                        pass
                    if not icon_path.exists():
                        continue

                    if Image is not None and ImageTk is not None:
                        img = Image.open(icon_path).convert("RGBA")
                        max_px = 40
                        if img.width > max_px or img.height > max_px:
                            img.thumbnail((max_px, max_px))
                        self._header_icon_image = ImageTk.PhotoImage(img)
                    else:
                        img = tk.PhotoImage(file=str(icon_path))
                        while img.width() > 40 or img.height() > 40:
                            img = img.subsample(2, 2)
                        self._header_icon_image = img

                    self.header_icon_label.configure(
                        image=self._header_icon_image, text=""
                    )
                    self.header_icon_label.image = self._header_icon_image
                    try:
                        if (
                            Image is not None
                            and ImageTk is not None
                            and hasattr(self._header_icon_image, "width")
                        ):
                            self.log(f"[diag] header icon loaded: {icon_path}")
                        else:
                            self.log(f"[diag] header icon loaded: {icon_path}")
                    except Exception:
                        pass
                    loaded = True
                    break
                except Exception as e:
                    try:
                        self.log(f"[diag] header icon load failed: {icon_path} / {e}")
                    except Exception:
                        pass

            if not loaded:
                self.header_icon_label.configure(text="")
                try:
                    self.log("[diag] header icon not found or could not be loaded")
                except Exception:
                    pass
        except Exception as e:
            try:
                self.log(f"[diag] header icon unexpected error: {e}")
            except Exception:
                pass

    def _apply_icon(self):
        try:
            if platform.system() == "Windows":
                import ctypes

                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "VRCMisaTranslator"
                )
        except Exception:
            pass

        try:
            self.root.iconbitmap(resource_path("app.ico"))
        except Exception:
            pass

    def current_ui_lang_code(self) -> str:
        try:
            value = self._base_language_name((self.ui_lang.get() or "").strip())
        except Exception:
            value = ""

        mapping = {
            "ja": "ja",
            "日本語": "ja",
            "en": "en",
            "English": "en",
            "英語": "en",
            "zh-TW": "zh-TW",
            "zh-tw": "zh-TW",
            "繁體中文": "zh-TW",
            "繁体字中国語": "zh-TW",
            "zh-CN": "zh-CN",
            "zh-cn": "zh-CN",
            "简体中文": "zh-CN",
            "簡体字中国語": "zh-CN",
            "ko": "ko",
            "한국어": "ko",
            "韓国語": "ko",
        }
        return mapping.get(value, "ja")

    def current_ui_lang_display(self) -> str:
        code = self.current_ui_lang_code()
        mapping = {
            "ja": "日本語",
            "en": "English",
            "zh-TW": "繁體中文",
            "zh-CN": "简体中文",
            "ko": "한국어",
        }
        return mapping.get(code, "日本語")

    def current_ui_text_key(self) -> str:
        return self.current_ui_lang_display()

    def _base_language_name(self, display_name: str) -> str:
        return re.sub(r"\s*\([^)]*\)$", "", display_name).strip()

    def _language_code(self, display_name: str) -> str:
        base_name = self._base_language_name(display_name)
        return LANGUAGE_OPTIONS.get(base_name, base_name)

    def _format_language_display(self, base_name: str) -> str:
        ui_ref = LANGUAGE_DESCRIPTIONS.get(
            self.current_ui_text_key(), LANGUAGE_DESCRIPTIONS["English"]
        )
        desc = ui_ref.get(base_name, base_name)
        return f"{base_name} ({desc})"

    def _refresh_language_comboboxes(self):
        values = [
            self._format_language_display(name) for name in LANGUAGE_OPTIONS.keys()
        ]
        self.input_lang_combo.configure(values=values)
        self.target_lang_combo.configure(values=values)
        self.input_lang.set(
            self._format_language_display(
                self._base_language_name(self.input_lang.get())
            )
        )
        self.target_lang.set(
            self._format_language_display(
                self._base_language_name(self.target_lang.get())
            )
        )

    def _setup_custom_preset_tracking(self):
        tracked_vars = [
            self.translation_mode,
            self.silence_seconds,
            self.max_record_seconds,
            self.start_threshold,
            self.stop_threshold,
            self.pre_roll_seconds,
        ]
        for var in tracked_vars:
            var.trace_add("write", self._mark_custom_preset)

    def _mark_custom_preset(self, *args):
        if self._applying_preset:
            return
        current = self.translation_preset.get()
        custom_label = self.tr("custom_preset")
        if current in TRANSLATION_PRESETS and current != custom_label:
            self.translation_preset.set(custom_label)

    def _log_startup_diagnostics(self):
        summary = {
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "runtime_mode": self.runtime_mode,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "executable": sys.executable,
            "cwd": os.getcwd(),
            "app_dir": str(APP_DIR),
            "model_dir": str(MODEL_DIR),
            "log_file": str(LOG_FILE),
            "cuda_available": cuda_available(),
            "selected_compute_device": self.compute_device.get(),
        }
        try:
            DIAGNOSTIC_FILE.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass
        for key, value in summary.items():
            self.log(f"[diag] {key}={value}")

    def log_exception(self, title: str, exc: Exception):
        self.log(f"{title}: {exc}")
        tb = traceback.format_exc()
        if tb and tb.strip() != "NoneType: None":
            for line in tb.rstrip().splitlines():
                self.log(f"[trace] {line}")

    def show_error_async(self, message: str):
        self.root.after(
            0, lambda: messagebox.showerror(self.tr("error_title"), message)
        )

    def update_model_cache_text(self):
        try:
            model_name = self.normalize_model_value(self.model_name.get())
            path = self.get_cached_model_path(model_name)
            if path is not None and path.exists():
                current_gb = path.stat().st_size / (1024**3)
                message = f"{MODEL_DISPLAY_NAMES.get(model_name, model_name)}: {self.tr('model_present')} ({path.name})"
                progress = f"{current_gb:.2f}GB"
            else:
                message = f"{MODEL_DISPLAY_NAMES.get(model_name, model_name)}: {self.tr('model_missing')}"
                progress = "0.00GB"
        except Exception:
            message = self.tr("model_checking")
            progress = "-"
        self.root.after(0, lambda: self.model_cache_text.set(message))
        self.root.after(0, lambda: self.model_download_progress_text.set(progress))

    def _set_model_download_progress(
        self,
        downloaded_bytes: int,
        total_bytes: int | None = None,
        completed: bool = False,
    ):
        if completed:
            if total_bytes and total_bytes > 0:
                text_value = (
                    f"完了 {downloaded_bytes / (1024 ** 3):.2f}GB / {total_bytes / (1024 ** 3):.2f}GB"
                    if self.current_ui_lang_code() == "ja"
                    else f"Done {downloaded_bytes / (1024 ** 3):.2f}GB / {total_bytes / (1024 ** 3):.2f}GB"
                )
            else:
                text_value = (
                    f"完了 {downloaded_bytes / (1024 ** 3):.2f}GB"
                    if self.current_ui_lang_code() == "ja"
                    else f"Done {downloaded_bytes / (1024 ** 3):.2f}GB"
                )
        else:
            if total_bytes and total_bytes > 0:
                text_value = f"{downloaded_bytes / (1024 ** 3):.2f}GB / {total_bytes / (1024 ** 3):.2f}GB"
            else:
                text_value = f"{downloaded_bytes / (1024 ** 3):.2f}GB / ?"
        self.root.after(0, lambda: self.model_download_progress_text.set(text_value))

    def set_model_status(self, text_value: str):
        try:
            status_literal_map = {
                self.tr("status_stopped"): "status_stopped",
                self.tr("status_loading"): "status_loading",
                self.tr("status_error"): "status_error",
                "停止中": "status_stopped",
                "Stopped": "status_stopped",
                "読み込み中": "status_loading",
                "Loading": "status_loading",
                "エラー": "status_error",
                "Error": "status_error",
            }
            if isinstance(text_value, str) and text_value in status_literal_map:
                self._last_model_status_key = status_literal_map[text_value]
            elif isinstance(text_value, str) and text_value.startswith("status_"):
                self._last_model_status_key = text_value
                text_value = self.tr(text_value)
            else:
                self._last_model_status_key = None
        except Exception:
            self._last_model_status_key = None
        self.root.after(0, lambda: self.model_status_text.set(text_value))

    def show_info_popup_once(self, popup_key: str, title: str, message: str):
        if self.popup_suppressions.get(popup_key):
            return

        try:
            dialog = tk.Toplevel(self.root)
            dialog.title(title)
            dialog.transient(self.root)
            dialog.grab_set()
            dialog.resizable(False, False)
            dialog.configure(bg="#0b1220")

            frame = ttk.Frame(dialog, padding=16, style="Dark.TFrame")
            frame.pack(fill="both", expand=True)

            ttk.Label(
                frame,
                text=message,
                style="Dark.TLabel",
                justify="left",
                wraplength=360,
            ).pack(anchor="w", pady=(0, 12))

            dont_show_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(
                frame,
                text=self.tr("popup_do_not_show_again"),
                variable=dont_show_var,
                style="Dark.TCheckbutton",
            ).pack(anchor="w", pady=(0, 12))

            def close_dialog():
                if dont_show_var.get():
                    self.popup_suppressions[popup_key] = True
                    self.save_settings(write_log=False)
                dialog.destroy()

            ttk.Button(
                frame,
                text="OK",
                command=close_dialog,
                style="Dark.TButton",
                width=10,
            ).pack(anchor="e")

            dialog.protocol("WM_DELETE_WINDOW", close_dialog)
            dialog.update_idletasks()
            width = 460
            height = max(170, dialog.winfo_height())
            x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - width) // 2)
            y = self.root.winfo_rooty() + max(
                0, (self.root.winfo_height() - height) // 2
            )
            dialog.geometry(f"{width}x{height}+{x}+{y}")
            dialog.wait_window()
        except Exception:
            try:
                messagebox.showinfo(title, message)
            except Exception:
                pass

    def _flash_button(self, button, duration_ms=180):
        try:
            original_style = str(button.cget("style"))
            flash_style = "Flash.Dark.TButton"
            self.style.configure(
                flash_style,
                background="#f8fafc",
                foreground="#111827",
                bordercolor="#f8fafc",
            )
            button.configure(style=flash_style)
            self.root.after(duration_ms, lambda: button.configure(style=original_style))
        except Exception:
            pass

    def _get_localized_hint_list(self):
        try:
            hints = self.tr("footer_hints")
            if isinstance(hints, list) and hints:
                return hints
        except Exception:
            pass
        try:
            return [self.tr("footer_hint")]
        except Exception:
            return [""]

    def _get_contextual_footer_hint(self):
        try:
            if (
                getattr(self, "preview_mode", None) is not None
                and self.preview_mode.get()
            ):
                return self.tr("footer_hint_preview_on")
        except Exception:
            pass

        try:
            has_key = self.has_valid_openai_api_key()
            if (
                getattr(self, "use_chatgpt_natural", None) is not None
                and self.use_chatgpt_natural.get()
            ):
                if not has_key:
                    return self.tr("footer_hint_api_missing")
                return self.tr("footer_hint_chatgpt_on")
        except Exception:
            pass

        return None

    def _update_footer_hint(self):
        try:
            contextual = self._get_contextual_footer_hint()
            if contextual:
                self.footer_hint.configure(text=contextual)
                return

            hints = self._get_localized_hint_list()
            if not hints:
                self.footer_hint.configure(text="")
                return

            self._hint_index %= len(hints)
            self.footer_hint.configure(text=hints[self._hint_index])
            self._hint_index = (self._hint_index + 1) % len(hints)
        except Exception:
            pass

    def _schedule_footer_hint_rotation(self):
        try:
            if self._hint_job is not None:
                self.root.after_cancel(self._hint_job)
        except Exception:
            pass
        self._update_footer_hint()
        try:
            self._hint_job = self.root.after(5000, self._schedule_footer_hint_rotation)
        except Exception:
            self._hint_job = None

    def open_details_settings(self):
        try:
            dialog = tk.Toplevel(self.root)
            dialog.title(self.tr("details_title"))
            dialog.transient(self.root)
            dialog.grab_set()
            dialog.resizable(False, True)
            dialog.geometry("800x420")
            dialog.minsize(800, 420)
            dialog.maxsize(800, 900)
            dialog.configure(bg="#0b1220")

            frame = ttk.Frame(dialog, padding=16, style="Dark.TFrame")
            frame.pack(fill="both", expand=True)

            grid = ttk.Frame(frame, style="Dark.TFrame")
            grid.pack(fill="x", expand=False)
            for i in range(4):
                grid.columnconfigure(i, weight=1)

            ttk.Label(grid, text=self.tr("osc_port"), style="Muted.TLabel").grid(
                row=0, column=0, sticky="w"
            )
            ttk.Entry(grid, textvariable=self.osc_port, style="Dark.TEntry").grid(
                row=1, column=0, sticky="ew", padx=(0, 6)
            )
            ttk.Label(grid, text=self.tr("osc_ip"), style="Muted.TLabel").grid(
                row=0, column=1, sticky="w"
            )
            ttk.Entry(grid, textvariable=self.osc_ip, style="Dark.TEntry").grid(
                row=1, column=1, sticky="ew", padx=(0, 6)
            )
            ttk.Label(grid, text=self.tr("start_threshold"), style="Muted.TLabel").grid(
                row=0, column=2, sticky="w"
            )
            ttk.Entry(
                grid, textvariable=self.start_threshold, style="Dark.TEntry"
            ).grid(row=1, column=2, sticky="ew", padx=(0, 6))
            ttk.Label(grid, text=self.tr("stop_threshold"), style="Muted.TLabel").grid(
                row=0, column=3, sticky="w"
            )
            ttk.Entry(grid, textvariable=self.stop_threshold, style="Dark.TEntry").grid(
                row=1, column=3, sticky="ew"
            )

            ttk.Label(grid, text=self.tr("silence_seconds"), style="Muted.TLabel").grid(
                row=2, column=0, sticky="w", pady=(10, 0)
            )
            ttk.Entry(
                grid, textvariable=self.silence_seconds, style="Dark.TEntry"
            ).grid(row=3, column=0, sticky="ew", padx=(0, 6))
            ttk.Label(
                grid, text=self.tr("max_record_seconds"), style="Muted.TLabel"
            ).grid(row=2, column=1, sticky="w", pady=(10, 0))
            ttk.Entry(
                grid, textvariable=self.max_record_seconds, style="Dark.TEntry"
            ).grid(row=3, column=1, sticky="ew", padx=(0, 6))
            ttk.Label(
                grid, text=self.tr("pre_roll_seconds"), style="Muted.TLabel"
            ).grid(row=2, column=2, sticky="w", pady=(10, 0))
            ttk.Entry(
                grid, textvariable=self.pre_roll_seconds, style="Dark.TEntry"
            ).grid(row=3, column=2, sticky="ew", padx=(0, 6))
            ttk.Label(grid, text=self.tr("max_chat_len"), style="Muted.TLabel").grid(
                row=2, column=3, sticky="w", pady=(10, 0)
            )
            ttk.Entry(grid, textvariable=self.max_chat_len, style="Dark.TEntry").grid(
                row=3, column=3, sticky="ew"
            )

            checks = ttk.Frame(frame, style="Dark.TFrame")
            checks.pack(fill="x", pady=(14, 0))
            ttk.Checkbutton(
                checks,
                text=self.tr("notification"),
                variable=self.send_notification,
                style="Dark.TCheckbutton",
            ).pack(side="left", padx=(0, 8))
            ttk.Checkbutton(
                checks,
                text=self.tr("save_log"),
                variable=self.save_log,
                style="Dark.TCheckbutton",
            ).pack(side="left", padx=(0, 8))
            ttk.Checkbutton(
                checks,
                text=self.tr("add_prefix"),
                variable=self.prefix_message,
                style="Dark.TCheckbutton",
            ).pack(side="left", padx=(0, 8))
            ttk.Checkbutton(
                checks,
                text=self.tr("filter_unnatural"),
                variable=self.filter_unnatural,
                style="Dark.TCheckbutton",
            ).pack(side="left", padx=(0, 8))
            ttk.Checkbutton(
                checks,
                text=self.tr("text_input_voice_overwrite"),
                variable=self.text_input_voice_overwrite,
                style="Dark.TCheckbutton",
            ).pack(side="left", padx=(0, 8))
            self.chk_streaming_preview_details = ttk.Checkbutton(
                checks,
                text=self.tr("streaming_preview"),
                variable=self.streaming_preview,
                style="Dark.TCheckbutton",
            )
            self.chk_streaming_preview_details.pack(side="left", padx=(0, 8))
            self.create_tooltip(
                self.chk_streaming_preview_details, self.tr("streaming_preview_tooltip")
            )
            self.chk_high_accuracy_vad_details = ttk.Checkbutton(
                checks,
                text=self.tr("high_accuracy_vad"),
                variable=self.high_accuracy_vad,
                style="Dark.TCheckbutton",
            )
            self.chk_high_accuracy_vad_details.pack(side="left", padx=(0, 8))
            self.create_tooltip(
                self.chk_high_accuracy_vad_details, self.tr("high_accuracy_vad_tooltip")
            )

            lower_sections = ttk.Frame(frame, style="Dark.TFrame")
            lower_sections.pack(fill="x", expand=False, pady=(14, 0))
            lower_sections.columnconfigure(0, weight=4, uniform="details_lower")
            lower_sections.columnconfigure(1, weight=6, uniform="details_lower")

            cost_alert_frame = ttk.LabelFrame(
                lower_sections,
                text=self.tr("cost_alert_section"),
                padding=8,
                style="Dark.TLabelframe",
            )
            cost_alert_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
            for i in range(2):
                cost_alert_frame.columnconfigure(i, weight=1)

            ttk.Checkbutton(
                cost_alert_frame,
                text=self.tr("cost_alert_enabled"),
                variable=self.cost_alert_enabled,
                style="Dark.TCheckbutton",
            ).grid(row=0, column=0, columnspan=2, sticky="w")
            ttk.Label(
                cost_alert_frame,
                text=self.tr("cost_alert_warning"),
                style="Muted.TLabel",
            ).grid(row=1, column=0, sticky="w", pady=(8, 0))
            ttk.Label(
                cost_alert_frame,
                text=self.tr("cost_alert_danger"),
                style="Muted.TLabel",
            ).grid(row=1, column=1, sticky="w", pady=(8, 0))
            ttk.Entry(
                cost_alert_frame,
                textvariable=self.cost_alert_warning,
                style="Dark.TEntry",
            ).grid(row=2, column=0, sticky="ew", padx=(0, 6))
            ttk.Entry(
                cost_alert_frame,
                textvariable=self.cost_alert_danger,
                style="Dark.TEntry",
            ).grid(row=2, column=1, sticky="ew")
            ttk.Checkbutton(
                cost_alert_frame,
                text=self.tr("cost_spike_enabled"),
                variable=self.cost_spike_enabled,
                style="Dark.TCheckbutton",
            ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))
            ttk.Label(
                cost_alert_frame,
                text=self.tr("cost_spike_threshold"),
                style="Muted.TLabel",
            ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
            ttk.Entry(
                cost_alert_frame,
                textvariable=self.cost_spike_threshold,
                style="Dark.TEntry",
            ).grid(row=5, column=0, columnspan=2, sticky="ew")
            ttk.Checkbutton(
                cost_alert_frame,
                text=self.tr("alert_sound_enabled_label"),
                variable=self.cost_sound_enabled,
                style="Dark.TCheckbutton",
            ).grid(row=6, column=0, sticky="w", pady=(10, 0))
            ttk.Checkbutton(
                cost_alert_frame,
                text=self.tr("popup_enable"),
                variable=self.popup_enabled,
                style="Dark.TCheckbutton",
            ).grid(row=6, column=1, sticky="w", padx=(22, 0), pady=(10, 0))
            ttk.Label(
                cost_alert_frame,
                text=self.tr("cost_sound_volume"),
                style="Muted.TLabel",
            ).grid(row=7, column=0, sticky="w", pady=(8, 0))

            sound_row = ttk.Frame(cost_alert_frame, style="Dark.TFrame")
            sound_row.grid(row=7, column=1, sticky="ew", pady=(8, 0))
            sound_row.columnconfigure(1, weight=1)

            ttk.Label(
                sound_row, text=self.tr("alert_sound_small"), style="Muted.TLabel"
            ).grid(row=0, column=0, sticky="w")
            ttk.Scale(
                sound_row,
                from_=0,
                to=100,
                orient="horizontal",
                variable=self.cost_sound_volume,
            ).grid(row=0, column=1, sticky="ew", padx=(6, 6))
            ttk.Label(
                sound_row, text=self.tr("alert_sound_large"), style="Muted.TLabel"
            ).grid(row=0, column=2, sticky="e")

            sound_test = tk.Label(
                sound_row,
                text=self.tr("alert_sound_test"),
                bg="#0b1220",
                fg="#60a5fa",
                cursor="hand2",
                font=("Yu Gothic UI", 9),
            )
            sound_test.grid(row=0, column=3, sticky="e", padx=(8, 0))
            sound_test.bind("<Enter>", lambda _e: sound_test.configure(fg="#93c5fd"))
            sound_test.bind("<Leave>", lambda _e: sound_test.configure(fg="#60a5fa"))
            sound_test.bind("<Button-1>", self.test_notice_sound)

            blacklist_frame = ttk.LabelFrame(
                lower_sections,
                text=self.tr("blacklist_section"),
                padding=8,
                style="Dark.TLabelframe",
            )
            blacklist_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
            blacklist_frame.columnconfigure(0, weight=1)
            blacklist_frame.columnconfigure(1, weight=0)
            blacklist_frame.rowconfigure(0, weight=1)

            blacklist_text = ScrolledText(
                blacklist_frame,
                wrap="word",
                height=8,
                font=("Yu Gothic UI", 10),
                bg="#0b1220",
                fg="#e5e7eb",
                insertbackground="#e5e7eb",
                relief="flat",
                borderwidth=0,
            )
            blacklist_text.grid(row=0, column=0, sticky="nsew")
            blacklist_text.insert("1.0", self.blacklist_text_var.get())

            def save_blacklist_in_details():
                try:
                    content = blacklist_text.get("1.0", "end-1c")
                    self.save_blacklist_from_ui(content)
                except Exception as e:
                    self.log(f"Failed to save blacklist in details: {e}")

            ttk.Button(
                blacklist_frame,
                text=self.tr("save_blacklist"),
                command=save_blacklist_in_details,
                style="Dark.TButton",
            ).grid(row=0, column=1, padx=(6, 0), sticky="ns")

            dialog.update_idletasks()
            x = self.root.winfo_rootx() + max(
                0, (self.root.winfo_width() - dialog.winfo_width()) // 2
            )
            y = self.root.winfo_rooty() + max(
                0, (self.root.winfo_height() - dialog.winfo_height()) // 2
            )
            dialog.geometry(f"800x420+{x}+{y}")
            dialog.wait_window()
        except Exception as e:
            self.log(f"Details dialog error: {e}")

    def show_setup_guide(self):
        try:
            dialog = tk.Toplevel(self.root)
            dialog.title(self.tr("setup_guide_title"))
            dialog.transient(self.root)
            dialog.grab_set()
            dialog.resizable(False, True)
            dialog.geometry("800x420")
            dialog.minsize(800, 420)
            dialog.maxsize(800, 800)
            dialog.configure(bg="#0b1220")

            frame = ttk.Frame(dialog, padding=16, style="Dark.TFrame")
            frame.pack(fill="both", expand=True)

            body = self.tr("setup_guide_body")
            lines = body.split("\n")

            url_map = {
                "https://platform.openai.com/api-keys": (
                    "OpenAI API Key取得ページ"
                    if self.current_ui_lang_code() == "ja"
                    else "OpenAI API Key Page"
                ),
                "https://platform.openai.com/account/billing": (
                    "課金設定" if self.current_ui_lang_code() == "ja" else "Billing"
                ),
                "https://platform.openai.com/usage": (
                    "使用量確認" if self.current_ui_lang_code() == "ja" else "Usage"
                ),
            }

            def show_link_dialog(url: str):
                try:
                    link_dialog = tk.Toplevel(dialog)
                    link_dialog.title(
                        "リンクを開く"
                        if self.current_ui_lang_code() == "ja"
                        else "Open Link"
                    )
                    link_dialog.transient(dialog)
                    link_dialog.grab_set()
                    link_dialog.resizable(False, False)
                    link_dialog.configure(bg="#0b1220")

                    inner = ttk.Frame(link_dialog, padding=16, style="Dark.TFrame")
                    inner.pack(fill="both", expand=True)

                    ttk.Label(
                        inner,
                        text=(
                            "以下のURLを開きます。"
                            if self.current_ui_lang_code() == "ja"
                            else "Open this URL:"
                        )
                        + "\n\n"
                        + url,
                        style="Dark.TLabel",
                        justify="left",
                        wraplength=520,
                    ).pack(anchor="w", pady=(0, 12))

                    def do_open():
                        try:
                            import webbrowser

                            opened = webbrowser.open(url)
                            if opened:
                                self.log(f"Opened URL: {url}")
                            else:
                                self.log(
                                    f"Failed to open URL: no browser handler for {url}"
                                )
                        except Exception as e:
                            self.log(f"Failed to open URL: {e}")
                        link_dialog.destroy()

                    btns = ttk.Frame(inner, style="Dark.TFrame")
                    btns.pack(fill="x")

                    ttk.Button(
                        btns,
                        text="開く" if self.current_ui_lang_code() == "ja" else "Open",
                        command=do_open,
                        style="Dark.TButton",
                    ).pack(side="left", padx=(0, 4))

                    ttk.Button(
                        btns,
                        text=(
                            "URLコピー"
                            if self.current_ui_lang_code() == "ja"
                            else "Copy URL"
                        ),
                        command=lambda: (
                            self.root.clipboard_clear(),
                            self.root.clipboard_append(url),
                            self.log(f"Copied URL: {url}"),
                        ),
                        style="Dark.TButton",
                    ).pack(side="left", padx=(0, 4))

                    ttk.Button(
                        btns,
                        text=(
                            "キャンセル"
                            if self.current_ui_lang_code() == "ja"
                            else "Cancel"
                        ),
                        command=link_dialog.destroy,
                        style="Dark.TButton",
                    ).pack(side="left", padx=(0, 4))

                    link_dialog.update_idletasks()
                    x = dialog.winfo_rootx() + max(
                        0, (dialog.winfo_width() - link_dialog.winfo_width()) // 2
                    )
                    y = dialog.winfo_rooty() + max(
                        0, (dialog.winfo_height() - link_dialog.winfo_height()) // 2
                    )
                    link_dialog.geometry(f"620x220+{x}+{y}")
                    link_dialog.wait_window()
                except Exception as e:
                    self.log(f"Failed to show link dialog: {e}")

            canvas_wrap = ttk.Frame(frame, style="Dark.TFrame")
            canvas_wrap.pack(fill="both", expand=True)

            guide_canvas = tk.Canvas(
                canvas_wrap, bg="#0b1220", highlightthickness=0, bd=0
            )
            guide_scroll = ttk.Scrollbar(
                canvas_wrap, orient="vertical", command=guide_canvas.yview
            )
            text_frame = ttk.Frame(guide_canvas, style="Dark.TFrame")

            text_frame.bind(
                "<Configure>",
                lambda _e: guide_canvas.configure(
                    scrollregion=guide_canvas.bbox("all")
                ),
            )
            guide_canvas.create_window((0, 0), window=text_frame, anchor="nw")
            guide_canvas.configure(yscrollcommand=guide_scroll.set)

            guide_canvas.pack(side="left", fill="both", expand=True)
            guide_scroll.pack(side="right", fill="y")

            def _guide_on_mousewheel(event):
                try:
                    delta = event.delta
                    if delta == 0:
                        return "break"
                    guide_canvas.yview_scroll(int(-1 * (delta / 120)), "units")
                except Exception:
                    pass
                return "break"

            def _bind_guide_mousewheel(_event=None):
                try:
                    dialog.bind_all("<MouseWheel>", _guide_on_mousewheel)
                except Exception:
                    pass

            def _unbind_guide_mousewheel(_event=None):
                try:
                    dialog.unbind_all("<MouseWheel>")
                except Exception:
                    pass

            guide_canvas.bind("<Enter>", _bind_guide_mousewheel)
            guide_canvas.bind("<Leave>", _unbind_guide_mousewheel)
            text_frame.bind("<Enter>", _bind_guide_mousewheel)
            text_frame.bind("<Leave>", _unbind_guide_mousewheel)

            for line in lines:
                stripped = line.strip()
                if stripped in url_map:
                    row = ttk.Frame(text_frame, style="Dark.TFrame")
                    row.pack(anchor="w", fill="x")
                    link = ttk.Label(
                        row,
                        text=stripped,
                        style="Dark.TLabel",
                        foreground="#60a5fa",
                        cursor="hand2",
                    )
                    link.pack(anchor="w")
                    link.bind("<Button-1>", lambda _e, u=stripped: show_link_dialog(u))
                else:
                    ttk.Label(
                        text_frame,
                        text=line,
                        style="Dark.TLabel",
                        justify="left",
                        wraplength=620,
                    ).pack(anchor="w")

            dialog.protocol(
                "WM_DELETE_WINDOW",
                lambda: (_unbind_guide_mousewheel(), dialog.destroy()),
            )
            dialog.update_idletasks()
            x = self.root.winfo_rootx() + max(
                0, (self.root.winfo_width() - dialog.winfo_width()) // 2
            )
            y = self.root.winfo_rooty() + max(
                0, (self.root.winfo_height() - dialog.winfo_height()) // 2
            )
            dialog.geometry(f"800x420+{x}+{y}")
            dialog.wait_window()
        except Exception as e:
            self.log(f"Guide dialog error: {e}")
            try:
                messagebox.showinfo(
                    self.tr("setup_guide_title"), self.tr("setup_guide_body")
                )
            except Exception:
                pass

    def _localized_license_display_text(self):
        """Return localized license text for dialogs."""
        raw_text = self._load_license_text()
        code = self.current_ui_lang_code()
        if code in {"ja", "en"}:
            return raw_text
        summary = self.tr("license_missing_summary")
        if raw_text and raw_text.strip() and raw_text.strip() != summary.strip():
            return f"{summary}\n\n--- {self.tr('license_original_header')} ---\n\n{raw_text}"
        return summary

    def _load_license_text(self):
        candidates = [
            Path(resource_path("LICENSE.txt")),
            Path(resource_path("LICENSE")),
            Path(resource_path("README.txt")),
            Path(sys.executable).resolve().parent / "LICENSE.txt",
            Path(sys.executable).resolve().parent / "LICENSE",
            Path(sys.executable).resolve().parent / "README.txt",
            Path.cwd() / "LICENSE.txt",
            Path.cwd() / "LICENSE",
            Path.cwd() / "README.txt",
        ]

        for candidate in candidates:
            try:
                if candidate.exists():
                    loaded_text = candidate.read_text(encoding="utf-8")
                    self.log(f"Loaded license text: {candidate}")
                    return loaded_text
            except Exception as e:
                self.log(f"Failed to read license text: {candidate} / {e}")

        return self.tr("license_missing_summary")

    def show_full_license_dialog(self):
        try:
            dialog = tk.Toplevel(self.root)
            dialog.title(self.tr("license_full_title"))
            dialog.transient(self.root)
            dialog.grab_set()
            dialog.resizable(True, True)
            dialog.geometry("820x620")
            dialog.minsize(760, 500)
            dialog.configure(bg="#0b1220")

            frame = ttk.Frame(dialog, padding=16, style="Dark.TFrame")
            frame.pack(fill="both", expand=True)

            ttk.Label(
                frame,
                text=self.tr("license_full_header"),
                style="Dark.TLabel",
                font=("Yu Gothic UI", 12, "bold"),
            ).pack(anchor="w", pady=(0, 10))

            license_text = ScrolledText(
                frame,
                wrap="word",
                bg="#071226",
                fg="#e5e7eb",
                insertbackground="#e5e7eb",
                relief="flat",
                borderwidth=0,
            )
            license_text.pack(fill="both", expand=True)

            license_text.insert("1.0", self._localized_license_display_text())
            license_text.configure(state="disabled")

            ttk.Button(
                frame,
                text=self.tr("details_close"),
                command=dialog.destroy,
                style="Dark.TButton",
            ).pack(anchor="e", pady=(12, 0))

            dialog.update_idletasks()
            x = self.root.winfo_rootx() + max(
                0, (self.root.winfo_width() - dialog.winfo_width()) // 2
            )
            y = self.root.winfo_rooty() + max(
                0, (self.root.winfo_height() - dialog.winfo_height()) // 2
            )
            dialog.geometry(f"{dialog.winfo_width()}x{dialog.winfo_height()}+{x}+{y}")
            dialog.wait_window()
        except Exception as e:
            self.log(f"Full license dialog error: {e}")
            try:
                messagebox.showinfo(APP_NAME, self._localized_license_display_text())
            except Exception:
                pass

    def show_about_dialog(self):
        try:
            dialog = tk.Toplevel(self.root)
            dialog.title(self.tr("about_title"))
            dialog.transient(self.root)
            dialog.grab_set()
            dialog.resizable(False, False)
            dialog.geometry("800x420")
            dialog.minsize(800, 420)
            dialog.maxsize(800, 420)
            dialog.configure(bg="#0b1220")

            frame = ttk.Frame(dialog, padding=16, style="Dark.TFrame")
            frame.pack(fill="both", expand=True)

            title_text = APP_NAME
            version_text = f"v{APP_VERSION}"

            summary_text = self.tr("about_summary")

            ttk.Label(
                frame,
                text=title_text,
                style="Dark.TLabel",
                font=("Yu Gothic UI", 14, "bold"),
            ).pack(anchor="w")

            ttk.Label(
                frame,
                text=version_text,
                style="Muted.TLabel",
            ).pack(anchor="w", pady=(2, 12))

            body = ScrolledText(
                frame,
                wrap="word",
                height=12,
                bg="#071226",
                fg="#e5e7eb",
                insertbackground="#e5e7eb",
                relief="flat",
                borderwidth=0,
            )
            body.pack(fill="both", expand=True)
            body.insert("1.0", summary_text)
            body.configure(state="disabled")

            button_row = ttk.Frame(frame, style="Dark.TFrame")
            button_row.pack(fill="x", pady=(12, 0))

            ttk.Button(
                button_row,
                text=self.tr("open_full_license"),
                command=self.show_full_license_dialog,
                style="Soft.TButton",
            ).pack(side="left")

            ttk.Button(
                button_row,
                text=self.tr("details_close"),
                command=dialog.destroy,
                style="Dark.TButton",
            ).pack(side="right")

            dialog.update_idletasks()
            x = self.root.winfo_rootx() + max(
                0, (self.root.winfo_width() - dialog.winfo_width()) // 2
            )
            y = self.root.winfo_rooty() + max(
                0, (self.root.winfo_height() - dialog.winfo_height()) // 2
            )
            dialog.geometry(f"800x420+{x}+{y}")
            dialog.wait_window()
        except Exception as e:
            self.log(f"About dialog error: {e}")
            try:
                messagebox.showinfo(APP_NAME, f"{APP_NAME} v{APP_VERSION}")
            except Exception:
                pass

    def has_valid_openai_api_key(self) -> bool:
        value = (self.openai_api_key.get() or "").strip()
        return value.startswith("sk-") and len(value) >= 20

    def update_chatgpt_checkbox_color(self):
        try:
            valid = self.has_valid_openai_api_key()
            if not valid:
                self.chk_chatgpt_natural.configure(
                    fg="#6b7280",
                    activeforeground="#6b7280",
                    bg="#111827",
                    activebackground="#111827",
                    selectcolor="#111827",
                )
            elif self.use_chatgpt_natural.get():
                self.chk_chatgpt_natural.configure(
                    fg="#3b82f6",
                    activeforeground="#60a5fa",
                    bg="#111827",
                    activebackground="#111827",
                    selectcolor="#111827",
                )
            else:
                self.chk_chatgpt_natural.configure(
                    fg="#ffffff",
                    activeforeground="#ffffff",
                    bg="#111827",
                    activebackground="#111827",
                    selectcolor="#111827",
                )
        except Exception:
            pass

    def update_chatgpt_tooltip(self):
        hint = (
            self.tr("chatgpt_tooltip_hint")
            if not self.has_valid_openai_api_key()
            else ""
        )
        try:
            self.create_tooltip(self.chk_chatgpt_natural, hint)
        except Exception:
            pass

    def show_chatgpt_key_required(self):
        try:
            messagebox.showinfo(APP_NAME, self.tr("chatgpt_key_required"))
        except Exception:
            pass

    def update_chatgpt_ui_state(self):
        valid = self.has_valid_openai_api_key()
        try:
            if not valid:
                self.use_chatgpt_natural.set(False)
            self.update_chatgpt_checkbox_color()
        except Exception:
            pass

    def on_chatgpt_checkbox_click(self, event=None):
        if not self.has_valid_openai_api_key():
            self.show_chatgpt_key_required()
            return "break"
        return None

    def save_runtime_preset_to_file(self):
        data = {
            "model_name": self.normalize_model_value(self.model_name.get()),
            "compute_device": self.compute_device.get(),
            "translation_preset": self.translation_preset.get(),
            "translation_mode": self.translation_mode.get(),
            "silence_seconds": self.silence_seconds.get(),
            "max_record_seconds": self.max_record_seconds.get(),
            "start_threshold": self.start_threshold.get(),
            "stop_threshold": self.stop_threshold.get(),
            "pre_roll_seconds": self.pre_roll_seconds.get(),
            "max_chat_len": self.max_chat_len.get(),
            "use_chatgpt_natural": self.use_chatgpt_natural.get(),
            "add_emotion": self.add_emotion.get(),
            "filter_unnatural": self.filter_unnatural.get(),
            "text_input_voice_overwrite": self.text_input_voice_overwrite.get(),
            "preview_mode": self.preview_mode.get(),
            "streaming_preview": self.streaming_preview.get(),
            "high_accuracy_vad": (
                self.high_accuracy_vad.get()
                if hasattr(self, "high_accuracy_vad")
                else True
            ),
            "prefix_message": self.prefix_message.get(),
        }
        CUSTOM_PRESET_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.log(self.tr("runtime_preset_saved"))

    def load_runtime_preset_from_file(self):
        if not CUSTOM_PRESET_FILE.exists():
            self.log(self.tr("runtime_preset_missing"))
            try:
                messagebox.showinfo(APP_NAME, self.tr("runtime_preset_missing"))
            except Exception:
                pass
            return
        data = json.loads(CUSTOM_PRESET_FILE.read_text(encoding="utf-8"))
        loaded_model = data.get("model_name", self.model_name.get())
        normalized_model = self.normalize_model_value(loaded_model)
        self.model_name.set(self.get_model_display_label(normalized_model))
        self.compute_device.set(data.get("compute_device", self.compute_device.get()))
        self.translation_preset.set(
            data.get("translation_preset", self.translation_preset.get())
        )
        self.translation_mode.set(
            data.get("translation_mode", self.translation_mode.get())
        )
        self.silence_seconds.set(
            data.get("silence_seconds", self.silence_seconds.get())
        )
        self.max_record_seconds.set(
            data.get("max_record_seconds", self.max_record_seconds.get())
        )
        self.start_threshold.set(
            data.get("start_threshold", self.start_threshold.get())
        )
        self.stop_threshold.set(data.get("stop_threshold", self.stop_threshold.get()))
        self.pre_roll_seconds.set(
            data.get("pre_roll_seconds", self.pre_roll_seconds.get())
        )
        self.max_chat_len.set(data.get("max_chat_len", self.max_chat_len.get()))
        self.use_chatgpt_natural.set(
            data.get("use_chatgpt_natural", self.use_chatgpt_natural.get())
        )
        self.add_emotion.set(data.get("add_emotion", self.add_emotion.get()))
        self.filter_unnatural.set(
            data.get("filter_unnatural", self.filter_unnatural.get())
        )
        self.preview_mode.set(data.get("preview_mode", self.preview_mode.get()))
        self.streaming_preview.set(
            data.get("streaming_preview", self.streaming_preview.get())
        )
        if hasattr(self, "high_accuracy_vad"):
            self.high_accuracy_vad.set(
                data.get("high_accuracy_vad", self.high_accuracy_vad.get())
            )
        self.prefix_message.set(data.get("prefix_message", self.prefix_message.get()))
        self.update_model_guidance_text()
        self.save_settings(write_log=False)
        self.log(self.tr("runtime_preset_loaded"))

    def get_log_filter_display_options(self):
        return [
            self.tr("log_filter_all"),
            self.tr("log_filter_error"),
            self.tr("log_filter_warn"),
            self.tr("log_filter_success"),
        ]

    def normalize_log_filter_value(self, value: str) -> str:
        mapping = {
            self.tr("log_filter_all"): "all",
            self.tr("log_filter_error"): "error",
            self.tr("log_filter_warn"): "warn",
            self.tr("log_filter_success"): "success",
            "すべて": "all",
            "エラーのみ": "error",
            "警告のみ": "warn",
            "成功のみ": "success",
            "All": "all",
            "Errors": "error",
            "Warnings": "warn",
            "Success": "success",
            "all": "all",
            "error": "error",
            "warn": "warn",
            "success": "success",
        }
        return mapping.get(value, "all")

    def get_log_filter_display_label(self, normalized_value: str) -> str:
        labels = {
            "all": self.tr("log_filter_all"),
            "error": self.tr("log_filter_error"),
            "warn": self.tr("log_filter_warn"),
            "success": self.tr("log_filter_success"),
        }
        return labels.get(normalized_value, self.tr("log_filter_all"))

    def _get_log_filter_options(self):
        return [
            self.tr("log_filter_all"),
            self.tr("log_filter_error"),
            self.tr("log_filter_warn"),
            self.tr("log_filter_success"),
        ]

    def _matches_log_filter(self, tag: str) -> bool:
        selected = self.log_filter_display.get()
        if selected == self.tr("log_filter_error"):
            return tag == "log_error"
        if selected == self.tr("log_filter_warn"):
            return tag == "log_warn"
        if selected == self.tr("log_filter_success"):
            return tag == "log_success"
        return True

    def _rerender_log_history(self):
        if not hasattr(self, "log_text"):
            return
        try:
            self.log_text.delete("1.0", "end")
            for line, tag in self.log_history:
                if self._matches_log_filter(tag):
                    self.log_text.insert("end", line, (tag,))
            self.log_text.see("end")
        except Exception:
            pass

    def resolve_compute_device(self) -> str:
        return "cpu"

    def get_cached_model_path(self, model_name: str):
        """Return a likely cached faster-whisper model directory/file if present."""
        try:
            candidates = [
                MODEL_DIR / model_name,
                MODEL_DIR / f"models--Systran--faster-whisper-{model_name}",
                MODEL_DIR / f"faster-whisper-{model_name}",
            ]
            for candidate in candidates:
                if candidate.exists():
                    return candidate
            if MODEL_DIR.exists():
                matches = list(MODEL_DIR.glob(f"**/*{model_name}*"))
                for candidate in matches:
                    if candidate.exists():
                        return candidate
        except Exception:
            pass
        return None

    def ensure_model_downloaded(self, model_name: str, timeout_seconds: int):
        """faster-whisper downloads models during WhisperModel initialization.

        Pro版では torch 依存の OpenAI whisper は使わず、
        faster-whisper にモデル取得を任せます。
        """
        existing = self.get_cached_model_path(model_name)
        if existing:
            self.log(f"faster-whisper model cache found: {existing}")
            self.update_model_cache_text()
            return existing

        if self.current_ui_lang_code() == "ja":
            self.set_model_status(
                f"{MODEL_DISPLAY_NAMES.get(model_name, model_name)} | 初回取得準備中…"
            )
            self.log(
                f"faster-whisper model will be downloaded on first load: {model_name}"
            )
        else:
            self.set_model_status(
                f"{MODEL_DISPLAY_NAMES.get(model_name, model_name)} | Preparing first download…"
            )
            self.log(
                f"faster-whisper model will be downloaded on first load: {model_name}"
            )
        self.root.after(0, lambda: self.model_download_progress_text.set("0.00GB / ?"))
        self.update_model_cache_text()
        return None

    def load_model_with_timeout(
        self, model_name: str, device: str, timeout_seconds: int
    ):
        self._set_status("status_loading")

        selected_device = getattr(self, "detected_device", detect_device())
        selected_compute_type = "float16" if selected_device == "cuda" else "int8"
        display_device = "GPU" if selected_device == "cuda" else "CPU"

        self.set_model_status(
            f"{MODEL_DISPLAY_NAMES.get(model_name, model_name)} | {self.tr('model_loading_state')} ({display_device})"
        )
        self.log(
            f"faster-whisper model load started: model={model_name}, "
            f"device={selected_device}, compute_type={selected_compute_type}, "
            f"timeout={timeout_seconds}s"
        )

        result = {}
        errors = {}
        finished = threading.Event()

        def runner():
            try:
                result["model"] = WhisperModel(
                    model_name,
                    device=selected_device,
                    compute_type=selected_compute_type,
                    download_root=str(MODEL_DIR),
                    cpu_threads=max(1, os.cpu_count() or 1),
                    num_workers=1,
                )
            except Exception as exc:
                errors["exception"] = exc
                errors["traceback"] = traceback.format_exc()
            finally:
                finished.set()

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()

        start_time = time.time()
        last_log = 0.0
        while not finished.wait(0.2):
            elapsed = time.time() - start_time
            if elapsed - last_log >= MODEL_DOWNLOAD_LOG_INTERVAL_SECONDS:
                last_log = elapsed
                self.log(
                    f"faster-whisper model load/download in progress... {elapsed:.1f}s"
                )
            if timeout_seconds > 0 and elapsed > timeout_seconds:
                raise TimeoutError(
                    f"faster-whisper model load timed out after {timeout_seconds} seconds. "
                    "Check network, firewall, antivirus settings, or try a smaller model."
                )
            if not self.running:
                raise RuntimeError("Model load was cancelled.")

        if errors:
            tb = errors.get("traceback", "")
            if tb:
                for line in tb.rstrip().splitlines():
                    self.log(f"[trace] {line}")
            raise RuntimeError(
                f"faster-whisper model load failed: {errors['exception']}"
            )

        self.log(
            f"faster-whisper model load completed: device={selected_device}, compute_type={selected_compute_type}"
        )
        self.resolved_compute_device = "GPU" if selected_device == "cuda" else "CPU"
        self.log(f"[SYSTEM] Device: {self.resolved_compute_device}")
        self.update_model_cache_text()
        return result["model"]

    def transcribe_audio_text(self, audio_np: np.ndarray, beam_size: int = 1) -> str:
        """Transcribe audio with faster-whisper and return plain text."""
        segments, _info = self.model.transcribe(
            audio_np,
            language=self._language_code(self.input_lang.get()),
            initial_prompt=self.build_initial_prompt(),
            temperature=0,
            condition_on_previous_text=False,
            without_timestamps=True,
            beam_size=beam_size,
        )
        return self.clean_text("".join(segment.text for segment in segments))

    def prepare_model(self):
        model_name = self.normalize_model_value(self.model_name.get())
        self.model_name.set(self.get_model_display_label(model_name))
        timeout_seconds = max(30, int(self.model_timeout_seconds.get()))
        self.resolved_compute_device = "GPU" if getattr(self, "detected_device", "cpu") == "cuda" else "CPU"
        self.update_current_mode_text()
        self.log(f"faster-whisper model directory: {MODEL_DIR}")
        self.log(
            f"Requested compute device: {self.compute_device.get()} / resolved: {self.resolved_compute_device}"
        )
        self.log(f"Runtime mode: {self.runtime_mode}")
        self.log("Low-latency mode: in-memory transcription / no ffmpeg subprocess")
        if self.model_name.get() == "Turbo (高精度・高負荷)":
            self.log(
                "turbo mode: high accuracy / heavy load. Base is recommended in crowded or heavy VRChat scenes."
            )
        self.update_model_cache_text()
        self.ensure_model_downloaded(model_name, MODEL_DOWNLOAD_TIMEOUT_SECONDS)
        self.model = self.load_model_with_timeout(
            model_name, self.resolved_compute_device, timeout_seconds
        )
        self.model_ready = True
        self.set_model_status(
            f"{MODEL_DISPLAY_NAMES.get(model_name, model_name)} | {self.tr('model_ready')} ({self.resolved_compute_device})"
        )
        self.log(self.tr("model_loaded"))
        self.update_model_cache_text()

    def on_model_changed(self, event=None):
        selected = self.normalize_model_value(self.model_name.get())
        self.model_name.set(self.get_model_display_label(selected))
        self.update_model_guidance_text()
        self.update_current_mode_text()

        if (
            selected in {"medium", "large"}
            and selected not in self._warned_heavy_models
        ):
            self._warned_heavy_models.add(selected)
            try:
                if selected == "medium":
                    msg = "medium はかなり高精度ですが重く、初回ダウンロードにも時間がかかります。"
                    if self.current_ui_lang_code() != "ja":
                        msg = "medium is very accurate, but it is heavy and the first download may take a while."
                else:
                    msg = "large は最高精度ですがかなり重く、初回ダウンロードにも時間がかかります。高性能PC向けです。"
                    if self.current_ui_lang_code() != "ja":
                        msg = "large has the highest accuracy, but it is very heavy and the first download may take a while. Recommended for high-end PCs."
                self.show_info_popup_once(f"heavy_model_{selected}", APP_NAME, msg)
            except Exception:
                pass

    def update_model_guidance_text(self):
        selected = self.normalize_model_value(self.model_name.get())
        if selected == "tiny":
            selected_label = self.tr("model_tiny_label")
            primary = (
                "tiny は最も軽いですが精度は控えめです。"
                if self.current_ui_lang_code() == "ja"
                else "tiny is the lightest but less accurate."
            )
        elif selected == "small":
            selected_label = self.tr("model_small_label")
            primary = (
                "small は高精度ですが少し重くなります。"
                if self.current_ui_lang_code() == "ja"
                else "small is more accurate but a bit heavier."
            )
        elif selected == "medium":
            selected_label = self.tr("model_medium_label")
            primary = (
                "medium はかなり高精度ですが重くなります。"
                if self.current_ui_lang_code() == "ja"
                else "medium is very accurate but heavier."
            )
        elif selected == "large":
            selected_label = self.tr("model_large_label")
            primary = (
                "large は最高精度ですがかなり重く、高性能PC向けです。"
                if self.current_ui_lang_code() == "ja"
                else "large has the highest accuracy but is very heavy and best for high-end PCs."
            )
        else:
            selected_label = self.tr("model_base_label")
            primary = self.tr("model_hint_base_cpu")
        combined = f"{self.tr('model_hint_selected', model=selected_label)} / {primary}"
        self.model_guidance_text.set(combined)

    def update_translation_mode_text(self):
        value = (
            self.tr("translation_mode_chatgpt")
            if self.use_chatgpt_natural.get()
            else self.tr("translation_mode_google")
        )
        self.translation_mode_status_text.set(value)

    def update_update_status_text(self, text_value: str):
        if isinstance(text_value, str) and text_value.startswith("update_"):
            self._last_update_status_key = text_value
            localized = self.tr(text_value)
        elif text_value in {"", None, "待機中", "Idle"}:
            self._last_update_status_key = "update_idle"
            localized = self.tr("update_idle")
        else:
            localized = text_value
        self.root.after(0, lambda: self.update_status_text.set(localized))

    def _configure_log_tags(self):
        if not hasattr(self, "log_text"):
            return
        try:
            self.log_text.tag_configure(
                "log_error", foreground="#ff4d4f", font=LOG_FONT_BOLD
            )
            self.log_text.tag_configure("log_warn", foreground="#fde047")
            self.log_text.tag_configure("log_success", foreground="#22c55e")
            self.log_text.tag_configure("log_info", foreground="#60a5fa")
            self.log_text.tag_configure("log_diag", foreground="#a78bfa")
            self.log_text.tag_configure("log_default", foreground="#e5e7eb")
        except Exception:
            pass

    def _classify_log_tag(self, line: str) -> str:
        """Return a color tag for a log line.

        The UI can be switched to Japanese, English, Traditional Chinese,
        Simplified Chinese, or Korean. Log messages are localized too, so
        the classifier must recognize localized keywords instead of only
        English/Japanese words.
        """
        text = str(line or "")
        lower = text.lower()

        error_keys = [
            "error",
            "traceback",
            "failed",
            "failure",
            "exception",
            "permissionerror",
            "[errno]",
            "エラー",
            "失敗",
            "認識できません",
            "不自然",
            "錯誤",
            "無法",
            "错误",
            "无法",
            "오류",
            "에러",
            "실패",
            "인식할 수",
            "예외",
        ]
        warn_keys = [
            "warn",
            "warning",
            "timeout",
            "timed out",
            "fallback",
            "quota",
            "limit",
            "警告",
            "注意",
            "上限",
            "残高不足",
            "額度",
            "额度",
            "경고",
            "주의",
            "시간 초과",
            "한도",
        ]
        success_keys = [
            "sent:",
            "success",
            "model loaded",
            "update applied",
            "download complete",
            "saved",
            "送信:",
            "送信",
            "成功",
            "完了",
            "保存済み",
            "保存しました",
            "読み込み完了",
            "已傳送",
            "傳送成功",
            "已儲存",
            "讀取完成",
            "已发送",
            "发送成功",
            "已保存",
            "读取完成",
            "전송",
            "성공",
            "완료",
            "저장",
            "로드 완료",
            "불러오기 완료",
        ]
        diag_keys = [
            "[diag]",
            "cuda_",
            "runtime_mode",
            "selected_compute_device",
            "model cache",
            "model_hint",
            "モデル状態",
            "モデル案内",
            "模型狀態",
            "模型状态",
            "模型建議",
            "模型建议",
            "모델",
        ]
        info_keys = [
            "update",
            "loading",
            "downloading",
            "recording",
            "transcribing",
            "translating",
            "starting",
            "started",
            "stopped",
            "waiting",
            "checking",
            "idle",
            "更新",
            "読込",
            "読み込み",
            "取得",
            "録音",
            "文字起こし",
            "翻訳",
            "開始",
            "停止",
            "待機",
            "讀取",
            "下載",
            "錄音",
            "語音辨識",
            "翻譯",
            "读取",
            "下载",
            "录音",
            "语音识别",
            "翻译",
            "开始",
            "停止",
            "待机",
            "업데이트",
            "로딩",
            "다운로드",
            "녹음",
            "음성 인식",
            "번역",
            "시작",
            "정지",
            "중지",
            "대기",
        ]

        if any(key in lower or key in text for key in error_keys):
            return "log_error"
        if any(key in lower or key in text for key in warn_keys):
            return "log_warn"
        if any(key in lower or key in text for key in success_keys):
            return "log_success"
        if any(key in lower or key in text for key in diag_keys):
            return "log_diag"
        if any(key in lower or key in text for key in info_keys):
            return "log_info"
        return "log_default"

    def _startup_update_check(self):
        return

    def open_readme_page(self):
        import webbrowser

        try:
            webbrowser.open(README_URL)
            self.log("Opened README page")
        except Exception as e:
            self.log_exception("Failed to open README page", e)

    def check_updates_manual(self):
        self.open_update_site_dialog()

    def open_gpu_upgrade_dialog(self):
        import webbrowser

        answer = messagebox.askyesno(
            self.tr("gpu_upgrade_title"), self.tr("gpu_upgrade_message")
        )
        if answer:
            webbrowser.open("https://magicalshooter.itch.io/vrc-misa-translator")
            self.log("Opened GPU version page: itch.io")

    def open_update_site_dialog(self):
        import webbrowser

        dialog = tk.Toplevel(self.root)
        dialog.title(self.tr("update_site_title"))
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.configure(bg="#0b1220")

        frame = ttk.Frame(dialog, padding=14, style="Dark.TFrame")
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text=self.tr("update_site_message"),
            style="Dark.TLabel",
            justify="left",
            wraplength=300,
        ).pack(anchor="w", pady=(0, 10))

        def open_booth():
            webbrowser.open("https://magicalshooter.booth.pm/items/8172536")
            self.log("Opened update page: Booth")
            dialog.destroy()

        def open_itch():
            webbrowser.open("https://magicalshooter.itch.io/vrc-misa-translator")
            self.log("Opened update page: itch.io")
            dialog.destroy()

        ttk.Button(
            frame,
            text=self.tr("update_site_booth"),
            command=open_booth,
            style="Dark.TButton",
            width=18,
        ).pack(anchor="w", fill="x", pady=(0, 6))

        ttk.Button(
            frame,
            text=self.tr("update_site_itch"),
            command=open_itch,
            style="Dark.TButton",
            width=18,
        ).pack(anchor="w", fill="x", pady=(0, 14))

        ttk.Button(
            frame,
            text="OK",
            command=dialog.destroy,
            style="Dark.TButton",
            width=10,
        ).pack(anchor="e", pady=(8, 0))

        dialog.update_idletasks()
        width = 340
        height = max(185, dialog.winfo_height())
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - width) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - height) // 2)
        dialog.geometry(f"{width}x{height}+{x}+{y}")

    def _normalize_version(self, value: str):
        value = (value or "").strip().lower().lstrip("v")
        parts = []
        for part in re.split(r"[^0-9]+", value):
            if part:
                parts.append(int(part))
        return tuple(parts or [0])

    def _is_newer_version(self, candidate: str) -> bool:
        return self._normalize_version(candidate) > self._normalize_version(APP_VERSION)

    def _fetch_update_info(self):
        if not UPDATE_INFO_URL:
            raise RuntimeError(self.tr("update_not_configured"))
        req = urllib.request.Request(
            UPDATE_INFO_URL, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"}
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            payload = response.read().decode("utf-8")
        data = json.loads(payload)
        return {
            "version": str(
                data.get("version") or data.get("latest_version") or ""
            ).strip(),
            "notes": str(data.get("notes") or "").strip(),
            "download_url": str(data.get("download_url") or "").strip(),
            "sha256": str(data.get("sha256") or "").strip().lower(),
        }

    def check_for_updates(self, user_initiated: bool = False, auto_apply: bool = False):
        auto_apply = False  # Free edition: no automatic update application
        self.update_update_status_text(self.tr("update_checking"))

        def runner():
            try:
                info = self._fetch_update_info()
                version = info.get("version")
                notes = info.get("notes")
                self.latest_update_info = info
                if version and self._is_newer_version(version):
                    if notes:
                        self.update_update_status_text(
                            self.tr(
                                "update_available_notes", version=version, notes=notes
                            )
                        )
                    else:
                        self.update_update_status_text(
                            self.tr("update_available", version=version)
                        )
                    if auto_apply and self.auto_update_enabled.get():
                        self.root.after(
                            0, lambda: self.download_and_apply_update(info, silent=True)
                        )
                    elif user_initiated:
                        self.root.after(0, lambda: self._prompt_update_action(info))
                else:
                    self.update_update_status_text(self.tr("already_latest"))
                    if user_initiated:
                        self.root.after(
                            0,
                            lambda: messagebox.showinfo(
                                APP_NAME, self.tr("already_latest")
                            ),
                        )
            except Exception as exc:
                self.update_update_status_text(self.tr("update_error", error=str(exc)))
                if user_initiated:
                    self.root.after(
                        0,
                        lambda: messagebox.showerror(
                            self.tr("error_title"),
                            self.tr("update_error", error=str(exc)),
                        ),
                    )

        threading.Thread(target=runner, daemon=True).start()

    def _prompt_update_action(self, info: dict):
        version = info.get("version") or "?"
        notes = info.get("notes") or ""
        body = self.tr("update_available", version=version)
        if notes:
            body += f"\n\n{notes}"
        if messagebox.askyesno(
            APP_NAME, body + "\n\n" + self.tr("update_restart_prompt")
        ):
            self.download_and_apply_update(info, silent=False)
        elif info.get("download_url"):
            if messagebox.askyesno(APP_NAME, self.tr("update_open_page")):
                webbrowser.open(info["download_url"])

    def _verify_sha256(self, file_path: Path, expected_hash: str):
        if not expected_hash:
            return True
        digest = hashlib.sha256()
        with open(file_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest().lower() == expected_hash.lower()

    def download_and_apply_update(self, info: dict, silent: bool = False):
        download_url = info.get("download_url")
        if not download_url:
            if not silent:
                messagebox.showerror(
                    self.tr("error_title"), self.tr("update_not_configured")
                )
            return
        self.update_update_status_text(self.tr("update_download_start"))

        def runner():
            try:
                filename = (
                    os.path.basename(download_url.split("?")[0]) or "update_package.zip"
                )
                dest = UPDATE_DOWNLOAD_DIR / filename
                with urllib.request.urlopen(download_url, timeout=30) as response, open(
                    dest, "wb"
                ) as fh:
                    shutil.copyfileobj(response, fh)
                if not self._verify_sha256(dest, info.get("sha256", "")):
                    raise RuntimeError("SHA256 mismatch")
                self.update_update_status_text(self.tr("update_downloaded"))
                self.root.after(
                    0, lambda: self._apply_update_package(dest, silent=silent)
                )
            except Exception as exc:
                self.update_update_status_text(self.tr("update_error", error=str(exc)))
                if not silent:
                    self.root.after(
                        0,
                        lambda: messagebox.showerror(
                            self.tr("error_title"),
                            self.tr("update_error", error=str(exc)),
                        ),
                    )

        threading.Thread(target=runner, daemon=True).start()

    def _find_update_source_root(self, extracted_dir: Path) -> Path:
        exe_name = (
            Path(sys.executable).name
            if getattr(sys, "frozen", False)
            else f"{APP_NAME}.exe"
        )
        for root, _dirs, files in os.walk(extracted_dir):
            if exe_name in files:
                return Path(root)
        children = [p for p in extracted_dir.iterdir() if p.is_dir()]
        if len(children) == 1:
            return children[0]
        return extracted_dir

    def _apply_update_package(self, package_path: Path, silent: bool = False):
        if not getattr(sys, "frozen", False):
            self.update_update_status_text(self.tr("update_not_supported"))
            webbrowser.open(package_path.parent.as_uri())
            return
        if self.runtime_mode.startswith("pyinstaller-onefile"):
            self.update_update_status_text(self.tr("update_not_supported"))
            webbrowser.open(package_path.parent.as_uri())
            return
        if package_path.suffix.lower() != ".zip":
            self.update_update_status_text(self.tr("update_not_supported"))
            webbrowser.open(package_path.parent.as_uri())
            return

        app_dir = Path(sys.executable).resolve().parent
        stage_dir = UPDATE_DOWNLOAD_DIR / "stage"
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)
        stage_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(package_path, "r") as zf:
            zf.extractall(stage_dir)

        source_root = self._find_update_source_root(stage_dir)
        updater_bat = UPDATE_DOWNLOAD_DIR / "apply_update.bat"
        exe_path = Path(sys.executable).resolve()
        bat = f'@echo off\nchcp 65001 >nul\nset SRC={source_root}\nset DST={app_dir}\ntimeout /t 2 /nobreak >nul\nrobocopy "%SRC%" "%DST%" /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP\nstart "" "{exe_path}"\n'
        updater_bat.write_text(bat, encoding="utf-8")

        if silent or messagebox.askyesno(APP_NAME, self.tr("update_restart_prompt")):
            self.update_update_status_text(self.tr("update_ready_restart"))
            subprocess.Popen(["cmd", "/c", str(updater_bat)], creationflags=0x08000000)
            if not silent:
                messagebox.showinfo(APP_NAME, self.tr("update_applied_message"))
            self.on_close()

    def save_blacklist_from_ui(self, raw_text=None):
        if raw_text is None:
            if hasattr(self, "blacklist_text"):
                raw = self.blacklist_text.get("1.0", "end").strip()
            else:
                raw = self.blacklist_text_var.get().strip()
        else:
            raw = str(raw_text).strip()

        items = [line.strip() for line in raw.splitlines() if line.strip()]
        self.blacklist_phrases = items if items else list(DEFAULT_BLACKLIST_PHRASES)
        self.blacklist_text_var.set("\n".join(self.blacklist_phrases))
        self.save_settings(write_log=False)
        self.log(self.tr("blacklist_saved"))

    def _normalized_ui_lang_key(self):
        raw_lang = self.ui_lang.get() if hasattr(self, "ui_lang") else "日本語"
        try:
            raw_lang = self._base_language_name((raw_lang or "").strip())
        except Exception:
            raw_lang = (raw_lang or "").strip()

        lang_map = {
            "ja": "日本語",
            "日本語": "日本語",
            "en": "English",
            "English": "English",
            "英語": "English",
            "zh-TW": "繁體中文",
            "zh-tw": "繁體中文",
            "繁體中文": "繁體中文",
            "繁体字中国語": "繁體中文",
            "zh-CN": "简体中文",
            "zh-cn": "简体中文",
            "简体中文": "简体中文",
            "簡体字中国語": "简体中文",
            "ko": "한국어",
            "한국어": "한국어",
            "韓国語": "한국어",
        }
        return lang_map.get(raw_lang, raw_lang)

    def tr(self, key: str, **kwargs) -> str:
        lang_key = self._normalized_ui_lang_key()

        # Prefer the exact mapped table, then English, then Japanese.
        # English fallback avoids showing Japanese text in newly added UI languages
        # when a translation key is still missing.
        table = UI_TEXT.get(lang_key)
        if not isinstance(table, dict):
            table = UI_TEXT.get("English") or UI_TEXT.get("日本語") or {}

        fallback_en = UI_TEXT.get("English") or {}
        fallback_ja = UI_TEXT.get("日本語") or {}

        text = table.get(key)
        if text is None:
            text = fallback_en.get(key)
        if text is None:
            text = fallback_ja.get(key, key)

        try:
            return text.format(**kwargs) if kwargs else text
        except Exception:
            return text

    def load_settings(self):
        if not SETTINGS_FILE.exists():
            return
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            loaded_model = data.get("model_name", self.model_name.get())
            normalized_model = self.normalize_model_value(loaded_model)
            self.model_name.set(
                self.tr("model_small_label")
                if normalized_model == "small"
                else self.tr("model_base_label")
            )
            self.input_lang.set(data.get("input_lang", self.input_lang.get()))
            self.target_lang.set(data.get("target_lang", self.target_lang.get()))
            saved_ui_lang = data.get(
                "ui_lang", data.get("ui_lang_display", self.ui_lang.get())
            )
            saved_ui_lang = self._base_language_name(str(saved_ui_lang or "").strip())
            if saved_ui_lang in {"ja", "日本語"}:
                self.ui_lang.set("日本語")
            elif saved_ui_lang in {"en", "English", "英語"}:
                self.ui_lang.set("English")
            elif saved_ui_lang in {"zh-TW", "zh-tw", "繁體中文", "繁体字中国語"}:
                self.ui_lang.set("繁體中文")
            elif saved_ui_lang in {"zh-CN", "zh-cn", "简体中文", "簡体字中国語"}:
                self.ui_lang.set("简体中文")
            elif saved_ui_lang in {"ko", "한국어", "韓国語"}:
                self.ui_lang.set("한국어")
            else:
                self.ui_lang.set("日本語")
            self.osc_ip.set(data.get("osc_ip", self.osc_ip.get()))
            self.osc_port.set(data.get("osc_port", self.osc_port.get()))
            self.send_notification.set(
                data.get("send_notification", self.send_notification.get())
            )
            self.mic_device.set(str(data.get("mic_device", self.mic_device.get())))
            self.start_threshold.set(
                data.get("start_threshold", self.start_threshold.get())
            )
            self.stop_threshold.set(
                data.get("stop_threshold", self.stop_threshold.get())
            )
            self.silence_seconds.set(
                data.get("silence_seconds", self.silence_seconds.get())
            )
            self.max_record_seconds.set(
                data.get("max_record_seconds", self.max_record_seconds.get())
            )
            self.min_record_seconds.set(
                data.get("min_record_seconds", self.min_record_seconds.get())
            )
            self.pre_roll_seconds.set(
                data.get("pre_roll_seconds", self.pre_roll_seconds.get())
            )
            self.skip_same_seconds.set(
                data.get("skip_same_seconds", self.skip_same_seconds.get())
            )
            self.save_log.set(data.get("save_log", self.save_log.get()))
            self.prefix_message.set(
                data.get("prefix_message", self.prefix_message.get())
            )
            self.filter_unnatural.set(
                data.get("filter_unnatural", self.filter_unnatural.get())
            )
            self.text_input_voice_overwrite.set(
                data.get(
                    "text_input_voice_overwrite", self.text_input_voice_overwrite.get()
                )
            )
            self.streaming_preview.set(
                data.get("streaming_preview", self.streaming_preview.get())
            )
            if hasattr(self, "high_accuracy_vad"):
                self.high_accuracy_vad.set(
                    data.get("high_accuracy_vad", self.high_accuracy_vad.get())
                )
            self.vad_setup_done = bool(data.get("vad_setup_done", False))
            self.max_chat_len.set(data.get("max_chat_len", self.max_chat_len.get()))
            self.translation_mode.set(
                data.get("translation_mode", self.translation_mode.get())
            )
            self.translation_preset.set(
                data.get("translation_preset", self.translation_preset.get())
            )
            self.use_chatgpt_natural.set(
                data.get("use_chatgpt_natural", self.use_chatgpt_natural.get())
            )
            self.add_emotion.set(data.get("add_emotion", self.add_emotion.get()))
            self.tone_style.set(data.get("tone_style", self.tone_style.get()))
            self.openai_api_key.set(
                data.get("openai_api_key", self.openai_api_key.get())
            )
            self.compute_device.set(
                data.get("compute_device", self.compute_device.get())
            )
            self.model_timeout_seconds.set(
                data.get("model_timeout_seconds", self.model_timeout_seconds.get())
            )
            self.auto_update_enabled.set(False)  # Free edition: auto update disabled
            self.popup_suppressions = (
                data.get("popup_suppressions", {})
                if isinstance(data.get("popup_suppressions", {}), dict)
                else {}
            )
            self.chatgpt_cost_date = str(
                data.get("chatgpt_cost_date", self._today_str())
            )
            self.chatgpt_today_cost_value = float(
                data.get("chatgpt_today_cost_value", 0.0) or 0.0
            )
            self.cost_alert_enabled.set(
                data.get("cost_alert_enabled", self.cost_alert_enabled.get())
            )
            self.cost_alert_warning.set(
                float(
                    data.get("cost_alert_warning", self.cost_alert_warning.get())
                    or self.cost_alert_warning.get()
                )
            )
            self.cost_alert_danger.set(
                float(
                    data.get("cost_alert_danger", self.cost_alert_danger.get())
                    or self.cost_alert_danger.get()
                )
            )
            self.cost_spike_enabled.set(
                data.get("cost_spike_enabled", self.cost_spike_enabled.get())
            )
            self.cost_spike_threshold.set(
                float(
                    data.get("cost_spike_threshold", self.cost_spike_threshold.get())
                    or self.cost_spike_threshold.get()
                )
            )
            self.cost_warning_alerted_date = str(
                data.get("cost_warning_alerted_date", "") or ""
            )
            self.cost_danger_alerted_date = str(
                data.get("cost_danger_alerted_date", "") or ""
            )
            saved_blacklist = data.get("blacklist_phrases")
            if isinstance(saved_blacklist, list) and saved_blacklist:
                self.blacklist_phrases = saved_blacklist
                self.blacklist_text_var.set("\n".join(self.blacklist_phrases))
        except Exception:
            pass

    def _sanitize_runtime_options(self):
        normalized_model = self.normalize_model_value(self.model_name.get())
        self.model_name.set(self.get_model_display_label(normalized_model))
        if self.compute_device.get() not in {"AUTO", "CPU", "cuda"}:
            self.compute_device.set("AUTO")

    def _today_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _initialize_chatgpt_cost_tracking(self):
        self._rollover_chatgpt_cost_if_needed(log_event=False)
        self.update_chatgpt_cost_display()

    def _rollover_chatgpt_cost_if_needed(self, log_event: bool = True):
        today = self._today_str()
        if not getattr(self, "chatgpt_cost_date", None):
            self.chatgpt_cost_date = today
            return
        if self.chatgpt_cost_date == today:
            return

        previous_date = self.chatgpt_cost_date
        previous_cost = float(getattr(self, "chatgpt_today_cost_value", 0.0) or 0.0)
        if previous_cost > 0:
            wrote = self._append_chatgpt_daily_cost_history(
                previous_date, previous_cost
            )
            if wrote and log_event:
                self.log(
                    f"ChatGPT cost history saved: {previous_date}, ${previous_cost:.4f}"
                )

        self.chatgpt_cost_date = today
        self.chatgpt_today_cost_value = 0.0
        self.cost_warning_alerted_date = ""
        self.cost_danger_alerted_date = ""
        self.update_chatgpt_cost_display()

    def _append_chatgpt_daily_cost_history(
        self, date_str: str, cost_usd: float
    ) -> bool:
        try:
            CHATGPT_COST_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            last_date = None
            file_exists = CHATGPT_COST_HISTORY_FILE.exists()

            if file_exists:
                with open(
                    CHATGPT_COST_HISTORY_FILE, "r", encoding="utf-8", newline=""
                ) as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        value = (row.get("date") or "").strip()
                        if value:
                            last_date = value

            if last_date == date_str:
                return False

            with open(
                CHATGPT_COST_HISTORY_FILE, "a", encoding="utf-8", newline=""
            ) as f:
                writer = csv.writer(f)
                if not file_exists or CHATGPT_COST_HISTORY_FILE.stat().st_size == 0:
                    writer.writerow(["date", "cost_usd"])
                writer.writerow([date_str, f"{float(cost_usd):.4f}"])
            return True
        except Exception as e:
            self.log(f"Failed to write ChatGPT cost history: {e}")
            return False

    def _get_model_pricing(self, model_name: str) -> dict:
        return OPENAI_PRICING_USD_PER_1M_TOKENS.get(
            model_name, OPENAI_PRICING_USD_PER_1M_TOKENS["gpt-4o-mini"]
        )

    def add_chatgpt_cost_from_usage(self, usage, model_name: str = "gpt-4o-mini"):
        if usage is None:
            return

        self._rollover_chatgpt_cost_if_needed()

        input_tokens = getattr(usage, "prompt_tokens", None)
        output_tokens = getattr(usage, "completion_tokens", None)

        if input_tokens is None and isinstance(usage, dict):
            input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        if output_tokens is None and isinstance(usage, dict):
            output_tokens = (
                usage.get("completion_tokens") or usage.get("output_tokens") or 0
            )

        try:
            input_tokens = int(input_tokens or 0)
            output_tokens = int(output_tokens or 0)
        except Exception:
            return

        pricing = self._get_model_pricing(model_name)
        added_cost = (
            input_tokens * pricing["input"] + output_tokens * pricing["output"]
        ) / 1_000_000
        if added_cost <= 0:
            return

        self.chatgpt_today_cost_value = float(self.chatgpt_today_cost_value) + float(
            added_cost
        )
        self.update_chatgpt_cost_display()
        self._evaluate_chatgpt_cost_alerts(single_cost=float(added_cost))

    def _resolve_sound_path(self, filename: str) -> Path | None:
        candidates = [
            APP_DIR / filename,
            Path(sys.executable).resolve().parent / filename,
            Path.cwd() / filename,
            Path(resource_path(filename)),
            Path(filename),
        ]
        for candidate in candidates:
            try:
                if candidate.exists():
                    return candidate
            except Exception:
                pass
        return None

    def init_sound_system(self):
        if pygame is None:
            return
        if self._sound_ready:
            return
        try:
            pygame.mixer.init()
            self._sound_ready = True
            self.log("Sound system initialized")
        except Exception as e:
            self.log(f"Sound init failed: {e}")

    def play_cost_sound(self, level: str = "notice"):
        if not self.cost_sound_enabled.get():
            return
        if pygame is None:
            return

        now = time.time()
        if now - float(getattr(self, "_last_sound_time", 0.0) or 0.0) < 0.8:
            return

        self.init_sound_system()
        if not self._sound_ready:
            return

        try:
            filename = CHATGPT_NOTICE_SOUND_FILE
            if level == "warning":
                filename = CHATGPT_NOTICE_SOUND_FILE
            elif level == "danger":
                filename = CHATGPT_WARNING_SOUND_FILE

            sound_path = self._resolve_sound_path(filename)
            if sound_path is None and filename == CHATGPT_WARNING_SOUND_FILE:
                sound_path = self._resolve_sound_path(CHATGPT_NOTICE_SOUND_FILE)
            if sound_path is None:
                self.log(f"Sound file not found: {filename}")
                return

            sound = pygame.mixer.Sound(str(sound_path))
            sound.set_volume(
                max(0.0, min(1.0, float(self.cost_sound_volume.get()) / 100.0))
            )
            sound.play()
            self._last_sound_time = now
        except Exception as e:
            self.log(f"Sound playback failed: {e}")

    def test_notice_sound(self, event=None):
        try:
            self.play_cost_sound("notice")
        except Exception as e:
            self.log(f"Sound test failed: {e}")
        return "break"

    def show_toast(self, message: str, level: str = "notice"):
        if not getattr(self, "popup_enabled", tk.BooleanVar(value=True)).get():
            return

        def _show():
            try:
                toast = tk.Toplevel(self.root)
                toast.overrideredirect(True)
                toast.attributes("-topmost", True)
                toast.configure(bg="#111827")

                border = "#38bdf8"
                if level == "warning":
                    border = "#facc15"
                elif level == "danger":
                    border = "#ef4444"

                frame = tk.Frame(
                    toast,
                    bg="#111827",
                    highlightthickness=1,
                    highlightbackground=border,
                )
                frame.pack(fill="both", expand=True)

                label = tk.Label(
                    frame,
                    text=message,
                    bg="#111827",
                    fg="#e5e7eb",
                    font=("Yu Gothic UI", 10),
                    padx=12,
                    pady=8,
                    justify="left",
                    anchor="w",
                )
                label.pack(fill="both", expand=True)

                toast.update_idletasks()

                base_x = (
                    self.root.winfo_rootx()
                    + self.root.winfo_width()
                    - toast.winfo_width()
                    - 18
                )
                base_y = (
                    self.root.winfo_rooty()
                    + self.root.winfo_height()
                    - toast.winfo_height()
                    - 18
                )

                active_toasts = []
                for existing in getattr(self, "_toast_windows", []):
                    try:
                        if existing.winfo_exists():
                            active_toasts.append(existing)
                    except Exception:
                        pass
                self._toast_windows = active_toasts

                offset_y = len(self._toast_windows) * (toast.winfo_height() + 10)
                toast.geometry(f"+{base_x}+{base_y - offset_y}")
                self._toast_windows.append(toast)

                def _destroy_toast():
                    try:
                        if toast in self._toast_windows:
                            self._toast_windows.remove(toast)
                    except Exception:
                        pass
                    try:
                        toast.destroy()
                    except Exception:
                        pass

                toast.after(1800, _destroy_toast)
            except Exception as e:
                self.log(f"Toast failed: {e}")

        self.root.after(0, _show)

    def _show_cost_alert(self, message: str, level: str = "warning"):
        self.show_toast(message, level=level)
        self.play_cost_sound(level=level)

    def _evaluate_chatgpt_cost_alerts(self, single_cost: float | None = None):
        today = self._today_str()
        if self.cost_warning_alerted_date != today:
            self.cost_warning_alerted_date = ""
        if self.cost_danger_alerted_date != today:
            self.cost_danger_alerted_date = ""

        today_cost = float(getattr(self, "chatgpt_today_cost_value", 0.0) or 0.0)

        if self.cost_alert_enabled.get():
            danger_amount = float(self.cost_alert_danger.get() or 0.0)
            warning_amount = float(self.cost_alert_warning.get() or 0.0)

            if (
                danger_amount > 0
                and today_cost >= danger_amount
                and self.cost_danger_alerted_date != today
            ):
                self.cost_danger_alerted_date = today
                self.log(f"ChatGPT danger cost alert triggered: ${today_cost:.4f}")
                self._show_cost_alert(
                    self.tr("cost_toast_danger_message", amount=danger_amount),
                    level="danger",
                )
            elif (
                warning_amount > 0
                and today_cost >= warning_amount
                and self.cost_warning_alerted_date != today
            ):
                self.cost_warning_alerted_date = today
                self.log(f"ChatGPT warning cost alert triggered: ${today_cost:.4f}")
                self._show_cost_alert(
                    self.tr("cost_toast_warning_message", amount=warning_amount),
                    level="warning",
                )

        if self.cost_spike_enabled.get() and single_cost is not None:
            threshold = float(self.cost_spike_threshold.get() or 0.0)
            if threshold > 0 and float(single_cost) >= threshold:
                self.log(
                    f"ChatGPT single request cost alert triggered: ${float(single_cost):.4f}"
                )
                self._show_cost_alert(
                    self.tr("cost_toast_spike_message", amount=float(single_cost)),
                    level="warning",
                )

    def _get_chatgpt_cost_color(self) -> str:
        try:
            amount = float(getattr(self, "chatgpt_today_cost_value", 0.0) or 0.0)
            danger_amount = float(self.cost_alert_danger.get() or 0.0)
            warning_amount = float(self.cost_alert_warning.get() or 0.0)
            if danger_amount > 0 and amount >= danger_amount:
                return "#ef4444"
            if warning_amount > 0 and amount >= warning_amount:
                return "#facc15"
        except Exception:
            pass
        return "#ffffff"

    def update_chatgpt_cost_display(self):
        try:
            self._rollover_chatgpt_cost_if_needed(log_event=False)
        except Exception:
            pass

        amount = float(getattr(self, "chatgpt_today_cost_value", 0.0) or 0.0)

        amount_text = f"${amount:.4f}"

        display = (
            f"{self.tr('chatgpt_cost_label')} {amount_text} ({self.tr('today_short')})"
        )

        try:
            self.chatgpt_cost_text.set(display)
            if hasattr(self, "chatgpt_cost_label"):
                self.chatgpt_cost_label.configure(fg=self._get_chatgpt_cost_color())
        except Exception:
            pass

    def save_settings(self, write_log: bool = True):
        data = {
            "model_name": self.normalize_model_value(self.model_name.get()),
            "input_lang": self.input_lang.get(),
            "target_lang": self.target_lang.get(),
            "ui_lang": self.current_ui_lang_code(),
            "ui_lang_display": self.current_ui_lang_display(),
            "osc_ip": self.osc_ip.get(),
            "osc_port": self.osc_port.get(),
            "send_notification": self.send_notification.get(),
            "mic_device": self.mic_device.get(),
            "start_threshold": self.start_threshold.get(),
            "stop_threshold": self.stop_threshold.get(),
            "silence_seconds": self.silence_seconds.get(),
            "max_record_seconds": self.max_record_seconds.get(),
            "min_record_seconds": self.min_record_seconds.get(),
            "pre_roll_seconds": self.pre_roll_seconds.get(),
            "skip_same_seconds": self.skip_same_seconds.get(),
            "save_log": self.save_log.get(),
            "prefix_message": self.prefix_message.get(),
            "filter_unnatural": self.filter_unnatural.get(),
            "text_input_voice_overwrite": self.text_input_voice_overwrite.get(),
            "streaming_preview": self.streaming_preview.get(),
            "high_accuracy_vad": (
                self.high_accuracy_vad.get()
                if hasattr(self, "high_accuracy_vad")
                else True
            ),
            "vad_setup_done": bool(getattr(self, "vad_setup_done", False)),
            "max_chat_len": self.max_chat_len.get(),
            "translation_mode": self.translation_mode.get(),
            "translation_preset": self.translation_preset.get(),
            "use_chatgpt_natural": self.use_chatgpt_natural.get(),
            "add_emotion": self.add_emotion.get(),
            "tone_style": self.tone_style.get(),
            "openai_api_key": self.openai_api_key.get(),
            "compute_device": self.compute_device.get(),
            "model_timeout_seconds": self.model_timeout_seconds.get(),
            "auto_update_enabled": False,
            "popup_suppressions": self.popup_suppressions,
            "blacklist_phrases": self.blacklist_phrases,
            "chatgpt_cost_date": self.chatgpt_cost_date,
            "chatgpt_today_cost_value": self.chatgpt_today_cost_value,
            "cost_alert_enabled": self.cost_alert_enabled.get(),
            "cost_alert_warning": self.cost_alert_warning.get(),
            "cost_alert_danger": self.cost_alert_danger.get(),
            "cost_spike_enabled": self.cost_spike_enabled.get(),
            "cost_spike_threshold": self.cost_spike_threshold.get(),
            "cost_warning_alerted_date": self.cost_warning_alerted_date,
            "cost_danger_alerted_date": self.cost_danger_alerted_date,
        }
        try:
            SETTINGS_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if write_log:
                self.log(self.tr("settings_saved"))
        except Exception as e:
            if write_log:
                self.log(f"{self.tr('settings_save_failed')}: {e}")

    def delete_selected_model(self):
        model_name = self.normalize_model_value(self.model_name.get())
        path = self.get_cached_model_path(model_name)
        display_name = MODEL_DISPLAY_NAMES.get(model_name, model_name)

        if path is None or not path.exists():
            message = self.tr("delete_model_missing", model=display_name)
            self.log(message)
            try:
                self.show_info_popup_once(
                    f"delete_missing_{model_name}", APP_NAME, message
                )
            except Exception:
                pass
            self.update_model_cache_text()
            return

        try:
            confirmed = messagebox.askyesno(
                APP_NAME,
                self.tr("delete_model_confirm", model=display_name),
            )
        except Exception:
            confirmed = True

        if not confirmed:
            return

        try:
            path.unlink()
            self.model_ready = False
            self.model = None
            self.update_model_cache_text()
            self.set_model_status(self.tr("status_stopped"))
            self.log(self.tr("delete_model_done", model=display_name))
            try:
                self.show_info_popup_once(
                    f"delete_done_{model_name}",
                    APP_NAME,
                    self.tr("delete_model_done", model=display_name),
                )
            except Exception:
                pass
        except Exception as e:
            self.log(self.tr("delete_model_failed", error=str(e)))
            try:
                messagebox.showerror(
                    self.tr("error_title"), self.tr("delete_model_failed", error=str(e))
                )
            except Exception:
                pass

    def on_close(self):
        self._rollover_chatgpt_cost_if_needed(log_event=False)
        self.save_settings(write_log=False)
        self.running = False
        self.root.destroy()

    def toggle_api_key_visibility(self):
        """Toggle OpenAI API key masking in the main window."""
        try:
            visible = not bool(self.api_key_visible.get())
            self.api_key_visible.set(visible)
            if hasattr(self, "api_key_entry"):
                self.api_key_entry.configure(show="" if visible else "*")
            if hasattr(self, "btn_toggle_api_key"):
                self.btn_toggle_api_key.configure(text="🙈" if visible else "👁")
        except Exception:
            pass

    def update_api_key_tooltip(self):
        try:
            if hasattr(self, "btn_toggle_api_key"):
                self.create_tooltip(
                    self.btn_toggle_api_key, self.tr("api_key_visibility_tooltip")
                )
        except Exception:
            pass

    def create_tooltip(self, widget, text):
        """Create or replace a tooltip for a widget.

        Previous versions used add="+" repeatedly, so changing the UI language
        could leave old tooltip callbacks alive.  This replaces the tooltip
        bindings each time to avoid mixed-language / clipped tooltip text.
        """
        try:
            old = getattr(widget, "_misa_tooltip_window", None)
            if old is not None:
                try:
                    old.destroy()
                except Exception:
                    pass
            widget._misa_tooltip_window = None
        except Exception:
            pass

        tooltip_text = text or ""

        def show(_event=None):
            if not tooltip_text:
                return
            try:
                old = getattr(widget, "_misa_tooltip_window", None)
                if old is not None:
                    return

                x = widget.winfo_rootx() + 12
                y = widget.winfo_rooty() + widget.winfo_height() + 4
                win = tk.Toplevel(widget)
                win.wm_overrideredirect(True)
                win.configure(bg="#0b1220")
                win.wm_geometry(f"+{x}+{y}")

                label = tk.Label(
                    win,
                    text=tooltip_text,
                    justify="left",
                    bg="#0b1220",
                    fg="#e5e7eb",
                    relief="solid",
                    borderwidth=1,
                    padx=8,
                    pady=4,
                    wraplength=420,
                )
                label.pack()
                widget._misa_tooltip_window = win
            except Exception:
                try:
                    widget._misa_tooltip_window = None
                except Exception:
                    pass

        def hide(_event=None):
            try:
                win = getattr(widget, "_misa_tooltip_window", None)
                if win is not None:
                    try:
                        win.destroy()
                    except Exception:
                        pass
                widget._misa_tooltip_window = None
            except Exception:
                pass

        try:
            widget.bind("<Enter>", show)
            widget.bind("<Leave>", hide)
            widget.bind("<ButtonPress>", hide)
        except Exception:
            pass

    def _build_ui(self):
        self.style = ttk.Style()

        self.style.configure("Muted.TCheckbutton", foreground="#9ca3af")
        self.style.configure(
            "ChatGPTOff.TCheckbutton",
            background="#1f2937",
            foreground="#ffffff",
            font=("Segoe UI", 10, "bold"),
        )
        self.style.configure(
            "ChatGPTOn.TCheckbutton",
            background="#1f2937",
            foreground="#60a5fa",
            font=("Segoe UI", 10, "bold"),
        )
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        bg = "#111827"
        panel = "#1f2937"
        panel_alt = "#0f172a"
        text_bg = "#0b1220"
        fg = "#e5e7eb"
        muted = "#94a3b8"
        accent = "#60a5fa"
        border = "#334155"

        self.root.configure(bg=bg)
        self.style.configure("Dark.TFrame", background=bg)
        self.style.configure("Panel.TFrame", background=panel)
        self.style.configure(
            "Dark.TLabelframe", background=panel, foreground=fg, bordercolor=border
        )
        self.style.configure("Dark.TLabelframe.Label", background=panel, foreground=fg)
        self.style.configure("Dark.TLabel", background=bg, foreground=fg)
        self.style.configure("Muted.TLabel", background=panel, foreground=muted)
        self.style.configure(
            "Dark.TButton",
            background=panel_alt,
            foreground=fg,
            bordercolor=border,
            focusthickness=1,
            focuscolor=accent,
            padding=(12, 8),
        )
        self.style.map("Dark.TButton", background=[("active", "#1e293b")])
        self.style.configure(
            "Soft.TButton",
            background="#162033",
            foreground="#cbd5e1",
            bordercolor=border,
            focusthickness=1,
            focuscolor=accent,
            padding=(8, 4),
        )
        self.style.map("Soft.TButton", background=[("active", "#1e293b")])
        self.style.configure(
            "Primary.Dark.TButton",
            background=accent,
            foreground="#0b1220",
            bordercolor=accent,
            padding=(14, 9),
        )
        self.style.map("Primary.Dark.TButton", background=[("active", "#93c5fd")])
        self.style.configure("Dark.TCheckbutton", background=panel, foreground=fg)
        self.style.configure(
            "ChatGPTOff.TCheckbutton",
            background=panel,
            foreground="#ffffff",
            font=("Segoe UI", 10, "bold"),
        )
        self.style.map("ChatGPTOff.TCheckbutton", background=[("active", panel)])
        self.style.map("ChatGPTOn.TCheckbutton", background=[("active", panel)])
        self.style.configure(
            "ChatGPTDisabled.TCheckbutton",
            background=panel,
            foreground="#8b93a1",
            font=("Segoe UI", 10, "bold"),
        )
        self.style.map("ChatGPTDisabled.TCheckbutton", background=[("active", panel)])
        self.style.map("Dark.TCheckbutton", background=[("active", panel)])
        self.style.configure(
            "Dark.TEntry", fieldbackground=text_bg, foreground=fg, bordercolor=border
        )
        self.style.configure(
            "Dark.TCombobox",
            fieldbackground=text_bg,
            foreground=fg,
            arrowcolor=fg,
            bordercolor=border,
        )
        self.style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", text_bg)],
            foreground=[("readonly", fg)],
        )
        self.style.configure("Dark.TPanedwindow", background=bg)

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=0)
        self.root.rowconfigure(3, weight=1)
        self.root.rowconfigure(4, weight=0)

        header = ttk.Frame(self.root, padding=12, style="Dark.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        header_top = ttk.Frame(header, style="Dark.TFrame")
        header_top.grid(row=0, column=0, sticky="ew")

        header_bottom = ttk.Frame(header, style="Dark.TFrame")
        header_bottom.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        self.header_title_wrap = ttk.Frame(header_top, style="Dark.TFrame")
        self.header_title_wrap.pack(side="left")

        self.header_icon_label = ttk.Label(
            self.header_title_wrap, text="", style="Dark.TLabel"
        )
        self.header_icon_label.pack(side="left", padx=(0, 8), pady=(2, 0))

        self.title_label = ttk.Label(
            self.header_title_wrap,
            text="",
            font=("Segoe UI", 18, "bold"),
            style="Dark.TLabel",
        )
        self.title_label.pack(side="left")

        header_right = ttk.Frame(header_top, style="Dark.TFrame")
        header_right.pack(side="right")

        def _make_header_link(parent, text, command, right_pad=10):
            label = tk.Label(
                parent,
                text=text,
                bg=bg,
                fg="#cbd5e1",
                activebackground=bg,
                activeforeground="#93c5fd",
                cursor="hand2",
                font=("Segoe UI", 9),
                padx=2,
                pady=2,
            )
            label.pack(side="left", padx=(0, right_pad))
            label.bind("<Button-1>", lambda _event: command())
            label.bind("<Enter>", lambda _event, w=label: w.configure(fg="#93c5fd"))
            label.bind("<Leave>", lambda _event, w=label: w.configure(fg="#cbd5e1"))
            return label

        calibration_frame = ttk.Frame(header_bottom, style="Dark.TFrame")
        calibration_frame.pack(side="left")

        self.btn_calibrate = _make_header_link(
            calibration_frame,
            self.tr("calibrate_button"),
            self.run_calibration,
            right_pad=0,
        )

        nav_frame = ttk.Frame(header_bottom, style="Dark.TFrame")
        nav_frame.pack(side="right")

        self.btn_readme = _make_header_link(
            nav_frame,
            self.tr("readme_button"),
            self.open_readme_page,
            right_pad=12,
        )

        self.btn_check_updates = _make_header_link(
            nav_frame,
            self.tr("update_button_short"),
            self.check_updates_manual,
            right_pad=12,
        )

        self.btn_license = _make_header_link(
            nav_frame,
            self.tr("license_button"),
            self.show_about_dialog,
            right_pad=0,
        )

        self.header_ui_lang_wrap = ttk.Frame(header_right, style="Dark.TFrame")
        self.header_ui_lang_wrap.pack(side="left", padx=(0, 18))

        status_wrap = ttk.Frame(header_right, style="Dark.TFrame")
        status_wrap.pack(side="left")
        self.state_dot_label = tk.Label(
            status_wrap, text="●", font=("Segoe UI", 16, "bold"), bg=bg, fg="#64748b"
        )
        self.state_dot_label.pack(side="left", padx=(0, 4))
        ttk.Label(
            status_wrap,
            textvariable=self.status_text,
            font=("Segoe UI", 11),
            style="Dark.TLabel",
        ).pack(side="left")

        self.controls = ttk.LabelFrame(
            self.root, text="", padding=12, style="Dark.TLabelframe"
        )
        self.controls.grid(row=1, column=0, sticky="ew", padx=12)
        for i in range(7):
            self.controls.columnconfigure(i, weight=1)
        # Wider microphone area for long USB device names in distributed builds.
        self.controls.columnconfigure(4, weight=2)
        self.controls.columnconfigure(5, weight=1)

        self.label_model = ttk.Label(self.controls, text="", style="Muted.TLabel")
        self.label_model.grid(row=0, column=0, sticky="w")
        self.model_combo = ttk.Combobox(
            self.controls,
            textvariable=self.model_name,
            values=SUPPORTED_MODEL_NAMES,
            state="readonly",
            width=14,
            style="Dark.TCombobox",
        )
        self.model_combo.grid(row=1, column=0, sticky="ew", padx=(0, 4))
        self.model_combo.bind("<<ComboboxSelected>>", self.on_model_changed)

        self.label_input_lang = ttk.Label(self.controls, text="", style="Muted.TLabel")
        self.label_input_lang.grid(row=0, column=1, sticky="w")
        self.input_lang_combo = ttk.Combobox(
            self.controls,
            textvariable=self.input_lang,
            values=list(LANGUAGE_OPTIONS.keys()),
            state="readonly",
            width=16,
            style="Dark.TCombobox",
        )
        self.input_lang_combo.grid(row=1, column=1, sticky="ew", padx=(0, 4))

        self.label_target_lang = ttk.Label(self.controls, text="", style="Muted.TLabel")
        self.label_target_lang.grid(row=0, column=2, sticky="w")
        self.target_lang_combo = ttk.Combobox(
            self.controls,
            textvariable=self.target_lang,
            values=list(LANGUAGE_OPTIONS.keys()),
            state="readonly",
            width=16,
            style="Dark.TCombobox",
        )
        self.target_lang_combo.grid(row=1, column=2, sticky="ew", padx=(0, 4))

        self.label_ui_lang = ttk.Label(
            self.header_ui_lang_wrap, text="", style="Muted.TLabel"
        )
        self.label_ui_lang.pack(side="left", padx=(0, 6))
        self.ui_lang_combo = ttk.Combobox(
            self.header_ui_lang_wrap,
            textvariable=self.ui_lang,
            values=["日本語", "English", "繁體中文", "简体中文", "한국어"],
            state="readonly",
            width=12,
            style="Dark.TCombobox",
        )
        self.ui_lang_combo.pack(side="left")
        self.ui_lang_combo.bind(
            "<<ComboboxSelected>>", lambda e: self.refresh_ui_language()
        )

        self.label_mic = ttk.Label(self.controls, text="", style="Muted.TLabel")
        self.label_mic.grid(row=0, column=4, sticky="w")
        self.mic_combo = ttk.Combobox(
            self.controls,
            textvariable=self.mic_device,
            values=[],
            state="readonly",
            width=34,
            style="Dark.TCombobox",
        )
        self.mic_combo.grid(row=1, column=4, columnspan=2, sticky="ew", padx=(0, 0))
        self.mic_combo.bind("<<ComboboxSelected>>", lambda e: self._on_mic_selected())

        self.label_preset = ttk.Label(self.controls, text="", style="Muted.TLabel")
        self.label_preset.grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.preset_combo = ttk.Combobox(
            self.controls,
            textvariable=self.translation_preset,
            values=self.get_preset_display_options(),
            state="readonly",
            width=16,
            style="Dark.TCombobox",
        )
        self.preset_combo.grid(row=3, column=0, sticky="ew", padx=(0, 4))
        self.preset_combo.bind(
            "<<ComboboxSelected>>",
            lambda e: self.apply_preset(self.translation_preset.get()),
        )

        self.label_tone = ttk.Label(self.controls, text="", style="Muted.TLabel")
        self.label_tone.grid(row=2, column=1, sticky="w", pady=(10, 0))
        self.tone_combo = ttk.Combobox(
            self.controls,
            textvariable=self.tone_style,
            values=self.get_tone_display_options(),
            state="readonly",
            width=12,
            style="Dark.TCombobox",
        )
        self.tone_combo.grid(row=3, column=1, sticky="ew", padx=(0, 4))

        self.label_api = ttk.Label(self.controls, text="", style="Muted.TLabel")
        self.label_api.grid(row=2, column=2, columnspan=4, sticky="w", pady=(10, 0))
        self.api_key_entry = ttk.Entry(
            self.controls,
            textvariable=self.openai_api_key,
            style="Dark.TEntry",
            show="*",
        )
        self.api_key_entry.grid(row=3, column=2, columnspan=3, sticky="ew", padx=(0, 4))
        self.btn_toggle_api_key = tk.Label(
            self.controls,
            text="👁",
            bg=panel,
            fg=fg,
            activebackground=panel,
            activeforeground=accent,
            font=("Segoe UI Emoji", 12),
            cursor="hand2",
            padx=6,
            pady=0,
            bd=0,
            relief="flat",
            highlightthickness=0,
        )
        self.btn_toggle_api_key.grid(row=3, column=5, sticky="w", padx=(2, 4))
        self.btn_toggle_api_key.bind(
            "<Button-1>", lambda _e: self.toggle_api_key_visibility()
        )
        self.btn_toggle_api_key.bind(
            "<Enter>", lambda _e: self.btn_toggle_api_key.configure(fg=accent)
        )
        self.btn_toggle_api_key.bind(
            "<Leave>", lambda _e: self.btn_toggle_api_key.configure(fg=fg)
        )
        try:
            self.create_tooltip(
                self.btn_toggle_api_key, self.tr("api_key_visibility_tooltip")
            )
        except Exception:
            pass

        buttons = ttk.Frame(self.controls, style="Panel.TFrame")
        buttons.grid(row=4, column=0, columnspan=6, sticky="ew", pady=(12, 0))
        buttons.columnconfigure(6, weight=1)

        self.btn_start = ttk.Button(
            buttons, text="", command=self.start, style="Dark.TButton"
        )
        self.btn_start.grid(row=0, column=0, padx=(0, 4))
        self.btn_stop = ttk.Button(
            buttons, text="", command=self.stop, style="Dark.TButton"
        )
        self.btn_stop.grid(row=0, column=1, padx=(0, 8))
        self.btn_details_settings = ttk.Button(
            buttons, text="", command=self.open_details_settings, style="Dark.TButton"
        )
        self.btn_details_settings.grid(row=0, column=2, padx=(0, 8))
        self.chk_preview_mode = ttk.Checkbutton(
            buttons, text="", variable=self.preview_mode, style="Dark.TCheckbutton"
        )
        self.chk_preview_mode.grid(row=0, column=3, padx=(0, 8), sticky="w")
        self.chk_chatgpt_natural = tk.Checkbutton(
            buttons,
            text="",
            variable=self.use_chatgpt_natural,
            fg="#9ca3af",
            bg="#111827",
            activeforeground="#9ca3af",
            activebackground="#111827",
            selectcolor="#111827",
            highlightthickness=0,
            bd=0,
            relief="flat",
            anchor="w",
            font=("Yu Gothic UI", 10),
        )
        # ChatGPT modern group (flat frame + full-height accent line)
        self.chatgpt_group = tk.Frame(buttons, bg=panel, bd=0, highlightthickness=0)
        self.chatgpt_group.grid(row=0, column=4, padx=(0, 8), sticky="w")

        self.chatgpt_group.grid_columnconfigure(1, weight=1)

        self.chatgpt_group_line = tk.Frame(
            self.chatgpt_group, width=2, bg="#3b82f6", bd=0, highlightthickness=0
        )
        self.chatgpt_group_line.grid(
            row=0, column=0, rowspan=2, sticky="ns", padx=(0, 8)
        )

        self.chatgpt_group_label = tk.Label(
            self.chatgpt_group,
            text="ChatGPT",
            fg="#60a5fa",
            bg=panel,
            font=("Yu Gothic UI", 9, "bold"),
            bd=0,
            highlightthickness=0,
            anchor="w",
        )
        self.chatgpt_group_label.grid(row=0, column=1, sticky="w", pady=(0, 2))

        self.chatgpt_cost_label = tk.Label(
            self.chatgpt_group,
            textvariable=self.chatgpt_cost_text,
            fg="#cbd5e1",
            bg=panel,
            font=("Yu Gothic UI", 9),
            bd=0,
            highlightthickness=0,
            anchor="w",
        )
        self.chatgpt_cost_label.grid(
            row=0, column=2, sticky="w", pady=(0, 2), padx=(8, 0)
        )

        self.chatgpt_history_link = tk.Label(
            self.chatgpt_group,
            text="",
            fg="#7dd3fc",
            bg=panel,
            font=("Yu Gothic UI", 8, "underline"),
            bd=0,
            highlightthickness=0,
            anchor="w",
            cursor="hand2",
        )
        self.chatgpt_history_link.grid(
            row=0, column=3, sticky="w", pady=(0, 2), padx=(8, 0)
        )
        self.chatgpt_history_link.bind(
            "<Button-1>", lambda _e: self.open_chatgpt_cost_history()
        )
        self.chatgpt_history_link.bind(
            "<Enter>", lambda _e: self.chatgpt_history_link.configure(fg="#bae6fd")
        )
        self.chatgpt_history_link.bind(
            "<Leave>", lambda _e: self.chatgpt_history_link.configure(fg="#7dd3fc")
        )

        self.chatgpt_group_inner = tk.Frame(
            self.chatgpt_group, bg=panel, bd=0, highlightthickness=0
        )
        self.chatgpt_group_inner.grid(row=1, column=1, columnspan=3, sticky="w")

        # ChatGPT mode checkbox (inside inner frame)
        self.chk_chatgpt_natural = tk.Checkbutton(
            self.chatgpt_group_inner,
            text="",
            variable=self.use_chatgpt_natural,
            fg="#9ca3af",
            bg=panel,
            activeforeground="#9ca3af",
            activebackground=panel,
            selectcolor=panel,
            highlightthickness=0,
            bd=0,
            relief="flat",
            anchor="w",
            font=("Yu Gothic UI", 10),
        )
        self.chk_chatgpt_natural.grid(row=0, column=0, padx=(0, 8), sticky="w")
        self.chk_chatgpt_natural.bind("<Button-1>", self.on_chatgpt_checkbox_click)

        top_panel = ttk.Frame(self.root, style="Dark.TFrame", height=312)
        top_panel.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 6))
        top_panel.grid_propagate(False)

        bottom_panel = ttk.Frame(self.root, style="Dark.TFrame")
        bottom_panel.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 8))

        top_panel.columnconfigure(0, weight=1)
        top_panel.columnconfigure(1, weight=1)
        top_panel.rowconfigure(1, weight=1)
        bottom_panel.columnconfigure(0, weight=1)
        bottom_panel.rowconfigure(0, weight=1)

        self.info_frame = ttk.LabelFrame(
            top_panel, text="", padding=8, style="Dark.TLabelframe"
        )
        self.info_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.info_frame.columnconfigure(1, weight=3)
        self.info_frame.columnconfigure(3, weight=2)

        self.label_input_level = ttk.Label(
            self.info_frame, text="", style="Muted.TLabel"
        )
        self.label_input_level.grid(row=0, column=0, sticky="w", padx=(0, 4), pady=2)
        ttk.Label(
            self.info_frame, textvariable=self.level_text, style="Dark.TLabel"
        ).grid(row=0, column=1, sticky="w", pady=2)

        self.label_model_cache = ttk.Label(
            self.info_frame, text="", style="Muted.TLabel"
        )
        self.label_model_cache.grid(row=0, column=2, sticky="w", padx=(16, 8), pady=2)
        ttk.Label(
            self.info_frame,
            textvariable=self.model_cache_text,
            wraplength=320,
            justify="left",
            style="Dark.TLabel",
        ).grid(row=0, column=3, sticky="w", pady=2)

        self.label_device = ttk.Label(self.info_frame, text="", style="Muted.TLabel")
        self.label_device.grid(row=1, column=0, sticky="w", padx=(0, 4), pady=2)
        ttk.Label(
            self.info_frame,
            textvariable=self.detected_device_text,
            wraplength=520,
            justify="left",
            style="Dark.TLabel",
        ).grid(row=1, column=1, sticky="w", pady=2)

        self.label_model_guidance = ttk.Label(
            self.info_frame, text="", style="Muted.TLabel"
        )
        self.label_model_guidance.grid(
            row=1, column=2, sticky="w", padx=(16, 8), pady=2
        )
        ttk.Label(
            self.info_frame,
            textvariable=self.model_guidance_text,
            wraplength=320,
            justify="left",
            style="Dark.TLabel",
        ).grid(row=1, column=3, sticky="w", pady=2)

        self.label_model_download = ttk.Label(
            self.info_frame, text="", style="Muted.TLabel"
        )
        self.label_model_download.grid(row=2, column=0, sticky="w", padx=(0, 4), pady=2)
        ttk.Label(
            self.info_frame,
            textvariable=self.model_download_progress_text,
            wraplength=520,
            justify="left",
            style="Dark.TLabel",
        ).grid(row=2, column=1, sticky="w", pady=2)

        self.label_translation_mode = ttk.Label(
            self.info_frame, text="", style="Muted.TLabel"
        )
        self.label_translation_mode.grid(
            row=2, column=2, sticky="w", padx=(16, 8), pady=2
        )
        ttk.Label(
            self.info_frame,
            textvariable=self.translation_mode_status_text,
            wraplength=320,
            justify="left",
            style="Dark.TLabel",
        ).grid(row=2, column=3, sticky="w", pady=2)

        self.label_model_status = ttk.Label(
            self.info_frame, text="", style="Muted.TLabel"
        )
        self.label_model_status.grid(row=3, column=0, sticky="w", padx=(0, 4), pady=2)
        ttk.Label(
            self.info_frame,
            textvariable=self.model_status_text,
            wraplength=860,
            justify="left",
            style="Dark.TLabel",
        ).grid(row=3, column=1, columnspan=3, sticky="w", pady=2)

        self.label_current_mode = ttk.Label(
            self.info_frame, text="", style="Muted.TLabel"
        )
        self.label_current_mode.grid(row=4, column=0, sticky="w", padx=(0, 4), pady=2)
        ttk.Label(
            self.info_frame,
            textvariable=self.current_mode_text,
            wraplength=520,
            justify="left",
            style="Dark.TLabel",
        ).grid(row=4, column=1, sticky="w", pady=2)

        self.label_processing_time = ttk.Label(
            self.info_frame, text="", style="Muted.TLabel"
        )
        self.label_processing_time.grid(
            row=4, column=2, sticky="w", padx=(16, 8), pady=2
        )
        self.processing_time_value_label = ttk.Label(
            self.info_frame,
            textvariable=self.processing_time_text,
            wraplength=320,
            justify="left",
            style="Dark.TLabel",
        )
        self.processing_time_value_label.grid(row=4, column=3, sticky="w", pady=2)

        self.original_frame = ttk.LabelFrame(
            top_panel, text="", padding=8, style="Dark.TLabelframe"
        )
        self.original_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self.translated_frame = ttk.LabelFrame(
            top_panel, text="", padding=8, style="Dark.TLabelframe"
        )
        self.translated_frame.grid(row=1, column=1, sticky="nsew", padx=(6, 0))

        self.original_text = ScrolledText(
            self.original_frame,
            wrap="word",
            font=("Yu Gothic UI", 11),
            height=5,
            bg=text_bg,
            fg=fg,
            insertbackground=fg,
            relief="flat",
            borderwidth=0,
        )
        self.original_text.pack(fill="both", expand=True)
        self.original_text.bind("<Return>", self.handle_manual_text_input_enter)

        self.translated_text = ScrolledText(
            self.translated_frame,
            wrap="word",
            font=("Yu Gothic UI", 11),
            height=5,
            bg=text_bg,
            fg=fg,
            insertbackground=fg,
            relief="flat",
            borderwidth=0,
        )
        self.translated_text.pack(fill="both", expand=True)

        # Sub action row below the original/translated boxes.
        # Use a text-only link style so this optional blacklist action does not dominate the UI.
        self.manual_action_row = tk.Frame(top_panel, bg=bg, bd=0, highlightthickness=0)
        self.manual_action_row.grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(2, 0)
        )
        self.btn_add_original_to_blacklist = tk.Label(
            self.manual_action_row,
            text=self.tr("add_original_to_blacklist"),
            bg=bg,
            fg="#e5e7eb",
            activeforeground="#f87171",
            cursor="hand2",
            font=("Yu Gothic UI", 9),
            padx=0,
            pady=0,
        )
        self.btn_add_original_to_blacklist.pack(anchor="w", padx=(2, 0), pady=(0, 0))
        self.btn_add_original_to_blacklist.bind(
            "<Button-1>", lambda _e: self.add_original_to_blacklist_from_original()
        )
        self.btn_add_original_to_blacklist.bind(
            "<Enter>",
            lambda _e: self.btn_add_original_to_blacklist.configure(fg="#f87171"),
        )
        self.btn_add_original_to_blacklist.bind(
            "<Leave>",
            lambda _e: self.btn_add_original_to_blacklist.configure(fg="#e5e7eb"),
        )

        self.log_frame = ttk.LabelFrame(
            bottom_panel, text="", padding=6, style="Dark.TLabelframe"
        )
        self.log_frame.pack(fill="both", expand=True)
        self.log_text = ScrolledText(
            self.log_frame,
            wrap="word",
            font=LOG_FONT,
            height=10,
            bg=text_bg,
            fg=fg,
            insertbackground=fg,
            relief="flat",
            borderwidth=0,
        )
        self.log_text.pack(fill="both", expand=True, pady=(2, 0))
        self.log_expanded = True
        self._configure_log_tags()

        self.footer_frame = ttk.Frame(
            self.root, padding=(10, 2, 10, 6), style="Dark.TFrame"
        )
        self.footer_frame.grid(row=4, column=0, sticky="ew")
        self.footer_frame.columnconfigure(0, weight=1)

        # Row 0: buttons and filter
        self.footer_top = ttk.Frame(self.footer_frame, style="Dark.TFrame")
        self.footer_top.grid(row=0, column=0, sticky="ew")
        self.footer_top.columnconfigure(0, weight=1)
        self.footer_top.columnconfigure(1, weight=0)
        self.footer_top.columnconfigure(2, weight=0)

        self.utility_buttons = ttk.Frame(self.footer_top, style="Dark.TFrame")
        self.utility_buttons.grid(row=0, column=1, sticky="e")
        for i in range(5):
            self.utility_buttons.columnconfigure(i, weight=1)

        self.btn_open_log_folder = ttk.Button(
            self.utility_buttons,
            text="",
            command=self.open_log_folder,
            style="Dark.TButton",
        )
        self.btn_open_log_folder.grid(row=0, column=0, padx=2, sticky="ew")

        self.btn_refresh_bottom = ttk.Button(
            self.utility_buttons,
            text="",
            command=self._refresh_devices,
            style="Dark.TButton",
        )
        self.btn_refresh_bottom.grid(row=0, column=1, padx=2, sticky="ew")

        self.btn_test_osc_bottom = ttk.Button(
            self.utility_buttons, text="", command=self._test_send, style="Dark.TButton"
        )
        self.btn_test_osc_bottom.grid(row=0, column=2, padx=2, sticky="ew")

        self.btn_delete_model_bottom = ttk.Button(
            self.utility_buttons,
            text="",
            command=self.delete_selected_model,
            style="Dark.TButton",
        )
        self.btn_delete_model_bottom.grid(row=0, column=3, padx=2, sticky="ew")

        self.btn_setup_guide = ttk.Button(
            self.utility_buttons,
            text="",
            command=self.show_setup_guide,
            style="Dark.TButton",
        )
        self.btn_setup_guide.grid(row=0, column=4, padx=(2, 8), sticky="ew")

        self.footer_filter = ttk.Frame(self.footer_top, style="Dark.TFrame")
        self.footer_filter.grid(row=0, column=2, sticky="e")

        self.label_log_filter = ttk.Label(
            self.footer_filter, text="", style="Muted.TLabel"
        )
        self.label_log_filter.grid(row=0, column=0, padx=(0, 4))

        self.log_filter_combo = ttk.Combobox(
            self.footer_filter,
            textvariable=self.log_filter_display,
            state="readonly",
            width=8,
            style="Dark.TCombobox",
        )
        self.log_filter_combo.grid(row=0, column=1, sticky="e")
        self.log_filter_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self._rerender_log_history()
        )

        # Row 1: hint only
        self.footer_hint = ttk.Label(
            self.footer_frame,
            text="",
            style="Muted.TLabel",
            wraplength=760,
            justify="left",
        )
        self.footer_hint.grid(row=1, column=0, sticky="w", pady=(6, 0))

    def refresh_ui_language(self):
        title_text = f"{self.tr('app_title')} v{APP_VERSION}"
        self.root.title(title_text)
        if hasattr(self, "ui_lang_combo"):
            self.ui_lang_combo.configure(
                values=["日本語", "English", "繁體中文", "简体中文", "한국어"]
            )
        self.title_label.configure(text=self.tr("app_title"))
        self.controls.configure(text=self.tr("settings"))
        self.label_model.configure(
            text=f"{self.tr('model')} ({self.tr('speech_recognition_suffix')})"
        )
        self.label_input_lang.configure(text=self.tr("input_language"))
        self.label_target_lang.configure(text=self.tr("target_language"))
        if hasattr(self, "label_ui_lang"):
            self.label_ui_lang.configure(text=self.tr("ui_language"))
        self.label_mic.configure(text=self.tr("mic_number"))
        if hasattr(self, "btn_calibrate"):
            self.btn_calibrate.configure(text=self.tr("calibrate_button"))
        if hasattr(self, "btn_readme"):
            self.btn_readme.configure(text=self.tr("readme_button"))
        if hasattr(self, "btn_check_updates"):
            self.btn_check_updates.configure(text=self.tr("update_button_short"))
        if hasattr(self, "btn_license"):
            self.btn_license.configure(text=self.tr("license_button"))

        if hasattr(self, "label_start_threshold"):
            self.label_start_threshold.configure(text=self.tr("start_threshold"))
        if hasattr(self, "label_stop_threshold"):
            self.label_stop_threshold.configure(text=self.tr("stop_threshold"))
        if hasattr(self, "label_silence"):
            self.label_silence.configure(text=self.tr("silence_seconds"))
        if hasattr(self, "label_max_record"):
            self.label_max_record.configure(text=self.tr("max_record_seconds"))
        if hasattr(self, "label_pre_roll"):
            self.label_pre_roll.configure(text=self.tr("pre_roll_seconds"))
        if hasattr(self, "label_max_chat"):
            self.label_max_chat.configure(text=self.tr("max_chat_len"))

        self._refresh_language_comboboxes()
        self.label_preset.configure(text=self.tr("preset"))
        self.label_tone.configure(text=self.tr("tone"))
        if hasattr(self, "label_api"):
            self.label_api.configure(
                text=f"{self.tr('openai_api_key')} ({self.tr('api_key_optional_note')})"
            )
        self.update_api_key_tooltip()
        if hasattr(self, "blacklist_frame"):
            self.blacklist_frame.configure(text=self.tr("blacklist"))
        if hasattr(self, "btn_save_blacklist"):
            self.btn_save_blacklist.configure(text=self.tr("save_blacklist"))
        self.chk_chatgpt_natural.configure(
            text=self.tr("chatgpt_translation_mode_short")
        )
        if hasattr(self, "chatgpt_history_link"):
            self.chatgpt_history_link.configure(text=self.tr("open_usage_history"))
        self.update_chatgpt_cost_display()
        self.update_chatgpt_checkbox_color()
        if hasattr(self, "chk_preview_mode"):
            self.chk_preview_mode.configure(text=self.tr("preview_mode"))
        if hasattr(self, "chk_streaming_preview_details"):
            self.chk_streaming_preview_details.configure(
                text=self.tr("streaming_preview")
            )
            self.create_tooltip(
                self.chk_streaming_preview_details, self.tr("streaming_preview_tooltip")
            )
        self.update_chatgpt_tooltip()
        if hasattr(self, "btn_refresh_bottom"):
            self.btn_open_log_folder.configure(text=self.tr("open_logs_button"))
        self.btn_refresh_bottom.configure(text=self.tr("refresh_mics"))
        if hasattr(self, "btn_test_osc_bottom"):
            self.btn_test_osc_bottom.configure(text=self.tr("test_osc"))
        if hasattr(self, "btn_delete_model_bottom"):
            self.btn_delete_model_bottom.configure(text=self.tr("delete_model_button"))
        self.btn_start.configure(text=self.tr("start"))
        self.btn_stop.configure(text=self.tr("stop"))
        if hasattr(self, "btn_details_settings"):
            self.btn_details_settings.configure(text=self.tr("advanced_settings"))
        self.info_frame.configure(text=self.tr("status_group"))
        self.original_frame.configure(text=self.tr("original_input"))
        if hasattr(self, "btn_add_original_to_blacklist"):
            self.btn_add_original_to_blacklist.configure(
                text=self.tr("add_original_to_blacklist")
            )
        self.translated_frame.configure(text=self.tr("translated"))
        self.log_frame.configure(text=self.tr("log"))
        self.label_input_level.configure(text=self.tr("input_level"))
        self.label_device.configure(text=self.tr("device"))
        self.label_model_cache.configure(text=self.tr("model_cache_state"))
        self.label_model_guidance.configure(text=self.tr("model_guidance"))
        self.label_model_download.configure(
            text=self.tr("model_download_progress_label")
        )
        self.label_translation_mode.configure(text=self.tr("translation_mode_label"))
        self.label_model_status.configure(text=self.tr("model_status"))
        self.label_current_mode.configure(text=self.tr("current_mode_label"))
        self.label_processing_time.configure(text=self.tr("processing_time_label"))
        self.model_combo.configure(values=self.get_model_display_options())
        self.model_name.set(
            self.get_model_display_label(
                self.normalize_model_value(self.model_name.get())
            )
        )
        self.preset_combo.configure(values=self.get_preset_display_options())
        self.tone_combo.configure(values=self.get_tone_display_options())

        current_preset_internal = self.normalize_preset_value(
            self.translation_preset.get()
        )
        if current_preset_internal == "最速（リアルタイム）":
            self.translation_preset.set(self.tr("preset_fast_label"))
        elif current_preset_internal == "標準":
            self.translation_preset.set(self.tr("preset_normal_label"))
        elif current_preset_internal == "高品質":
            self.translation_preset.set(self.tr("preset_quality_label"))
        else:
            # Old saved "Accuracy First" values are displayed as High Quality in the new 3-preset UI.
            self.translation_preset.set(self.tr("preset_quality_label"))

        current_tone_internal = self.normalize_tone_value(self.tone_style.get())
        if current_tone_internal == "丁寧":
            self.tone_style.set(self.tr("tone_polite_label"))
        else:
            self.tone_style.set(self.tr("tone_friendly_label"))

        self._set_status(getattr(self, "_current_status_key", "status_stopped"))
        if hasattr(self, "_last_model_status_key") and self._last_model_status_key:
            self.set_model_status(self.tr(self._last_model_status_key))
        self._rerender_log_history()
        self._update_footer_hint()
        if hasattr(self, "btn_check_updates"):
            self.btn_check_updates.configure(text=self.tr("update_button_short"))
        self.btn_setup_guide.configure(text=self.tr("guide_button"))
        if hasattr(self, "label_log_filter"):
            self.label_log_filter.configure(text=self.tr("log_filter_label"))
        if hasattr(self, "log_filter_combo") and hasattr(
            self, "get_log_filter_display_options"
        ):
            self.log_filter_combo.configure(
                values=self.get_log_filter_display_options()
            )
        if (
            hasattr(self, "log_filter_display")
            and hasattr(self, "normalize_log_filter_value")
            and hasattr(self, "get_log_filter_display_label")
        ):
            current_log_filter = self.normalize_log_filter_value(
                self.log_filter_display.get()
            )
            self.log_filter_display.set(
                self.get_log_filter_display_label(current_log_filter)
            )

        self.update_model_guidance_text()
        self.update_translation_mode_text()
        self.update_current_mode_text()
        self.update_model_cache_text()
        try:
            if self.model_ready:
                self.set_model_status(
                    f"{MODEL_DISPLAY_NAMES.get(self.normalize_model_value(self.model_name.get()), self.normalize_model_value(self.model_name.get()))} | {self.tr('model_ready')} ({getattr(self, 'resolved_compute_device', 'cpu')})"
                )
            elif not self.running:
                self.set_model_status(self.tr("status_stopped"))
        except Exception:
            pass
        if hasattr(self, "_rerender_log_history"):
            self._rerender_log_history()

    def open_log_folder(self):
        try:
            folder = os.path.dirname(os.path.abspath(LOG_FILE))
            if os.name == "nt":
                os.startfile(folder)
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            self.log(f"Failed to open log folder: {e}")

    def open_chatgpt_cost_history(self):
        try:
            history_path = Path(CHATGPT_COST_HISTORY_FILE)
            history_path.parent.mkdir(parents=True, exist_ok=True)
            if not history_path.exists():
                with open(history_path, "w", encoding="utf-8", newline="") as f:
                    f.write("date,cost_usd\n")
            if os.name == "nt":
                os.startfile(str(history_path))
            else:
                subprocess.Popen(["xdg-open", str(history_path)])
        except Exception as e:
            self.log(f"Failed to open ChatGPT cost history: {e}")

    def _trim_log_lines(self):
        try:
            total_lines = int(float(self.log_text.index("end-1c").split(".")[0]))
            if total_lines > MAX_LOG_LINES:
                remove_to = total_lines - MAX_LOG_LINES
                self.log_text.delete("1.0", f"{remove_to + 1}.0")
        except Exception:
            pass

    def log(self, text: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {text}\n"
        self.root.after(0, lambda: self._append_log(line))
        if self.save_log.get():
            try:
                with open(LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(line)
            except Exception:
                pass

    def _append_log(self, line: str):
        tag = self._classify_log_tag(line)
        self.log_history.append((line, tag))
        if len(self.log_history) > 1500:
            self.log_history = self.log_history[-1500:]
        self._rerender_log_history()

    def _update_start_button_appearance(self):
        if hasattr(self, "btn_start"):
            style_name = "Primary.Dark.TButton" if self.running else "Dark.TButton"
            try:
                self.btn_start.configure(style=style_name)
            except Exception:
                pass

    def get_preset_display_options(self):
        # v1.11.2: Keep the preset UI simple: Fast / Normal / High Quality.
        # The old standalone "Accuracy First" preset is hidden from the UI to avoid confusion.
        return [
            self.tr("preset_fast_label"),
            self.tr("preset_normal_label"),
            self.tr("preset_quality_label"),
        ]

    def normalize_preset_value(self, value):
        value = (value or "").strip()
        mapping = {
            self.tr("preset_fast_label"): "最速（リアルタイム）",
            self.tr("preset_normal_label"): "標準",
            self.tr("preset_quality_label"): "高品質",
            self.tr("preset_accuracy_label"): "高品質",
            "Fast (Real-time)": "最速（リアルタイム）",
            "Fast: quick response": "最速（リアルタイム）",
            "最速：反応が速い": "最速（リアルタイム）",
            "Normal": "標準",
            "Normal: balanced": "標準",
            "通常：バランス": "標準",
            "High Quality": "高品質",
            "High Quality: accuracy first": "高品質",
            "高品質：精度優先": "高品質",
            "Accuracy First": "高品質",
            "最速（リアルタイム）": "最速（リアルタイム）",
            "最速": "最速（リアルタイム）",
            "標準": "標準",
            "通常": "標準",
            "高品質": "高品質",
            "精度優先": "高品質",
        }
        return mapping.get(value, "最速（リアルタイム）")

    def get_tone_display_options(self):
        return [self.tr("tone_friendly_label"), self.tr("tone_polite_label")]

    def normalize_tone_value(self, value):
        value = (value or "").strip()
        mapping = {
            self.tr("tone_friendly_label"): "フレンドリー",
            self.tr("tone_polite_label"): "丁寧",
            "Friendly": "フレンドリー",
            "Polite": "丁寧",
            "フレンドリー": "フレンドリー",
            "丁寧": "丁寧",
        }
        return mapping.get(value, "フレンドリー")

    def get_display_device_options(self):
        return [self.tr("device_cpu")]

    def normalize_display_device_value(self, value):
        return "cpu"

    def get_model_display_options(self):
        return [
            self.tr("model_tiny_label"),
            self.tr("model_base_label"),
            self.tr("model_small_label"),
            self.tr("model_medium_label"),
            self.tr("model_large_label"),
        ]

    def normalize_model_value(self, value):
        value = (value or "").strip()
        if value in {
            self.tr("model_tiny_label"),
            "tiny",
            "tiny（超軽量）",
            "tiny (Ultra Light)",
        }:
            return "tiny"
        if value in {
            self.tr("model_small_label"),
            "small",
            "small（高精度）",
            "small (High Accuracy)",
        }:
            return "small"
        if value in {
            self.tr("model_medium_label"),
            "medium",
            "medium（かなり高精度・重い）",
            "medium (Very Accurate / Heavy)",
        }:
            return "medium"
        if value in {
            self.tr("model_large_label"),
            "large",
            "large（最高精度・かなり重い）",
            "large (Highest Accuracy / Very Heavy)",
        }:
            return "large"
        return "base"

    def get_model_display_label(self, model_name):
        display_map = {
            "tiny": self.tr("model_tiny_label"),
            "base": self.tr("model_base_label"),
            "small": self.tr("model_small_label"),
            "medium": self.tr("model_medium_label"),
            "large": self.tr("model_large_label"),
        }
        return display_map.get(
            self.normalize_model_value(model_name), self.tr("model_base_label")
        )

    def get_localized_update_status_text(self, status_key: str) -> str:
        mapping = {
            "update_idle": self.tr("update_idle_short"),
        }
        return mapping.get(
            status_key, self.tr(status_key) if isinstance(status_key, str) else ""
        )

    def get_localized_status_text(self, status_key: str) -> str:
        ui = ""
        try:
            ui = self.ui_lang.get() or ""
        except Exception:
            ui = ""

        if ui in {"日本語", "ja"}:
            table = {
                "status_waiting": "待機中",
                "status_stopped": "停止中",
                "status_transcribing": "文字起こし中",
                "status_translating": "翻訳中",
                "status_sending": "送信中",
                "status_error": "エラー",
            }
        else:
            table = {
                "status_waiting": "Waiting",
                "status_stopped": "Stopped",
                "status_transcribing": "Transcribing",
                "status_translating": "Translating",
                "status_sending": "Sending",
                "status_error": "Error",
            }
        return table.get(status_key, table.get("status_stopped", "Stopped"))

    def update_current_mode_text(self):
        resolved = getattr(self, "resolved_compute_device", "CPU")
        model_name = self.get_model_display_label(self.model_name.get())
        text_value = f"{model_name} / {resolved}"
        self.root.after(0, lambda: self.current_mode_text.set(text_value))

    def set_processing_time(self, seconds_value: float | None):
        if seconds_value is None:
            display_text = "-"
            color = "#e5e7eb"
        else:
            if seconds_value < 1.5:
                status = "高速" if self.current_ui_lang_code() == "ja" else "Fast"
                color = "#22c55e"
            elif seconds_value < 3.0:
                status = "通常" if self.current_ui_lang_code() == "ja" else "Normal"
                color = "#e5e7eb"
            else:
                status = "重い" if self.current_ui_lang_code() == "ja" else "Heavy"
                color = "#ef4444"
            display_text = (
                f"{seconds_value:.2f}s（{status}）"
                if self.current_ui_lang_code() == "ja"
                else f"{seconds_value:.2f}s ({status})"
            )

        self.root.after(0, lambda: self.processing_time_text.set(display_text))

        def _apply_color():
            try:
                self.processing_time_value_label.configure(foreground=color)
            except Exception:
                pass

        self.root.after(0, _apply_color)

    def _set_status(self, status_key: str):
        self._current_status_key = status_key
        self._last_status_key = status_key
        color_map = {
            "status_stopped": "#64748b",
            "status_waiting": "#60a5fa",
            "status_recording": "#22c55e",
            "status_loading": "#a78bfa",
            "status_downloading": "#38bdf8",
            "status_transcribing": "#f59e0b",
            "status_translating": "#f97316",
            "status_sending": "#ef4444",
            "status_error": "#f43f5e",
        }
        color = color_map.get(status_key, "#94a3b8")
        display_text = self.tr(status_key)

        def update_status():
            self.status_text.set(display_text)
            if status_key == "status_error":
                self.model_status_text.set(display_text)
            try:
                self.state_dot_label.configure(fg=color)
            except Exception:
                pass

        self.root.after(0, update_status)

    def _set_level(self, rms: float):
        self.root.after(0, lambda: self.level_text.set(f"RMS: {rms:.6f}"))

    def _replace_text(self, widget: ScrolledText, text: str):
        widget.delete("1.0", "end")
        widget.insert("1.0", text)

    def _set_original(self, text: str):
        self.root.after(0, lambda: self._replace_text(self.original_text, text))

    def _set_translated(self, text: str):
        self.root.after(0, lambda: self._replace_text(self.translated_text, text))

    def _get_original_text_value(self) -> str:
        try:
            return self.original_text.get("1.0", "end-1c")
        except Exception:
            return ""

    def _manual_text_is_active(self) -> bool:
        try:
            widget_in_focus = self.root.focus_get()
            current_text = self.clean_text(self._get_original_text_value())
            return widget_in_focus == self.original_text and bool(current_text)
        except Exception:
            return False

    def add_original_to_blacklist_from_original(self):
        try:
            raw_text = self._get_original_text_value()
            text = self.clean_text(raw_text).replace("\n", " ").strip()
            text = re.sub(r"\s+", " ", text)
            if not text:
                self.log(self.tr("add_original_to_blacklist_empty"))
                return

            current_items = [
                item.strip() for item in self.blacklist_phrases if str(item).strip()
            ]
            current_keys = {item.casefold() for item in current_items}
            if text.casefold() in current_keys:
                self.log(self.tr("add_original_to_blacklist_duplicate", text=text))
                return

            confirmed = messagebox.askyesno(
                self.tr("add_original_to_blacklist_confirm_title"),
                self.tr("add_original_to_blacklist_confirm_message", text=text),
                parent=self.root,
            )
            if not confirmed:
                self.log(self.tr("add_original_to_blacklist_cancelled"))
                return

            current_items.append(text)
            self.save_blacklist_from_ui("\n".join(current_items))
            self.log(self.tr("add_original_to_blacklist_done", text=text))
        except Exception as e:
            self.log(f"Failed to add original text to blacklist: {e}")

    def save_chat_log_pair(
        self, original_text: str, translated_text: str, source: str = "voice"
    ):
        """Save original and translated text to a plain .txt chat log.

        The file is placed in the same folder opened by the existing Log Folder button.
        """
        try:
            original_text = (original_text or "").strip()
            translated_text = (translated_text or "").strip()
            if not original_text and not translated_text:
                return
            timestamp = datetime.now().strftime("%H:%M:%S")
            source_label = "Manual" if source == "manual" else "Voice"
            with open(CHAT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {source_label}\n")
                f.write(f"原文: {original_text}\n")
                f.write(f"翻訳: {translated_text}\n")
                f.write("\n")
        except Exception as e:
            try:
                self.log(f"Failed to save chat log: {e}")
            except Exception:
                pass

    def handle_manual_text_input_enter(self, event=None):
        try:
            original_text = self.clean_text(self._get_original_text_value())
            if not original_text:
                return "break"

            cycle_started_at = time.time()
            self._set_status("status_translating")
            translated_text = self.translate_text_value(
                original_text, input_source="manual"
            )
            self._set_translated(translated_text)

            if not self.confirm_preview_and_send(original_text, translated_text):
                self.log(
                    "Preview cancelled"
                    if self.current_ui_lang_code() != "ja"
                    else "プレビューで送信をキャンセルしました"
                )
                self._set_status("status_waiting" if self.running else "status_stopped")
                return "break"

            self._set_status("status_sending")
            self.send_to_vrchat(translated_text)
            self.log(self.tr("sent_log", text=translated_text))
            self.save_chat_log_pair(original_text, translated_text, source="manual")

            self.last_sent_text = original_text
            self.last_sent_time = time.time()
            self.original_text.delete("1.0", "end")
            total_elapsed = time.time() - cycle_started_at
            self.set_processing_time(total_elapsed)
            self._set_status("status_waiting" if self.running else "status_stopped")
            self.save_settings(write_log=False)
        except Exception as e:
            self.log_exception("Manual text input error", e)
            self.show_error_async(str(e))
            self._set_status("status_error")
        return "break"

    def _get_input_device_entries(self, devices):
        entries = []
        for idx, device in enumerate(devices):
            try:
                max_inputs = int(device.get("max_input_channels", 0))
            except Exception:
                max_inputs = 0
            if max_inputs > 0:
                name = device.get("name", "Unknown Device")
                display = f"【{idx}】 {name}"
                entries.append((display, idx, name))
        return entries

    def _selected_mic_index(self) -> int:
        current = self.mic_device.get()
        if current in self.mic_device_map:
            return self.mic_device_map[current]
        match = re.match(r"^【(\d+)】", current)
        if match:
            return int(match.group(1))
        if current.isdigit():
            return int(current)
        raise ValueError(self.tr("no_input_device"))

    def _filter_mic_choices(self):
        query = self.mic_search.get().strip().lower()
        if not self._all_input_devices:
            self.mic_combo.configure(values=[])
            self.mic_device_map = {}
            self.detected_device_text.set(self.tr("no_input_device"))
            return

        filtered = []
        for display, idx, name in self._all_input_devices:
            hay = f"{display} {name}".lower()
            if not query or query in hay:
                filtered.append((display, idx, name))

        if not filtered:
            filtered = self._all_input_devices

        self.mic_device_map = {display: idx for display, idx, _name in filtered}
        values = [display for display, _idx, _name in filtered]
        self.mic_combo.configure(values=values)

        current = self.mic_device.get()
        if current not in values:
            if values:
                self.mic_device.set(values[0])
                self._on_mic_selected()
            else:
                self.mic_device.set("")
                self.detected_device_text.set(self.tr("no_input_device"))

    def _on_mic_selected(self):
        selected = self.mic_device.get().strip()
        self.detected_device_text.set(
            selected if selected else self.tr("no_input_device")
        )
        self.save_settings(write_log=False)

    def _refresh_devices(self):
        try:
            devices = sd.query_devices()
            self.log(self.tr("device_refresh_ok"))
            self._all_input_devices = self._get_input_device_entries(devices)

            saved = self.mic_device.get().strip()
            if saved.isdigit():
                for display, idx, _name in self._all_input_devices:
                    if idx == int(saved):
                        saved = display
                        break

            self.mic_device.set(saved)
            self._filter_mic_choices()
            combo_values = list(self.mic_combo.cget("values"))
            if not self.mic_device.get() and combo_values:
                self.mic_device.set(combo_values[0])
            self._on_mic_selected()
        except Exception as e:
            self.log(f"{self.tr('device_refresh_failed')}: {e}")

    def _test_send(self):
        try:
            client = SimpleUDPClient(self.osc_ip.get(), int(self.osc_port.get()))
            client.send_message(
                "/chatbox/input",
                [self.tr("osc_test_message"), True, self.send_notification.get()],
            )
            self.log(self.tr("osc_test_success"))
        except Exception as e:
            messagebox.showerror(self.tr("error_title"), str(e))
            self.log(f"{self.tr('osc_test_failure')}: {e}")

    def clean_text(self, text: str) -> str:
        return " ".join(text.strip().split())

    def build_initial_prompt(self) -> str:
        code = self._language_code(self.input_lang.get())
        return LANGUAGE_PROMPTS.get(code, LANGUAGE_PROMPTS["en"])

    def is_blacklisted(self, text: str) -> bool:
        lower = text.lower().strip()
        for phrase in self.blacklist_phrases:
            if phrase.lower() in lower or phrase in text:
                return True
        return False

    def is_probable_hallucination(self, text: str) -> bool:
        lower = text.lower().strip()
        for phrase in COMMON_HALLUCINATIONS:
            if phrase in lower or phrase in text:
                return True
        if re.fullmatch(r"([a-zA-Zぁ-んァ-ン一-龯])\1{5,}", text):
            return True
        if re.search(r"(.{1,8})\1{3,}", text):
            return True
        return False

    def shorten_text(self, text: str) -> str:
        max_len = max(10, int(self.max_chat_len.get()))
        if len(text) > max_len:
            return text[:max_len] + "..."
        return text

    def is_valid_text(self, text: str) -> bool:
        if len(text) < 2:
            return False
        if text.count(" ") > 10:
            return False
        if self.filter_unnatural.get() and self.is_probable_hallucination(text):
            return False
        return True

    def build_tone_instruction(self) -> str:
        if self.tone_style.get() == "丁寧":
            return "Polite, soft, and respectful tone."
        return "Friendly, casual, natural conversational tone."

    def translate_with_chatgpt(self, text: str) -> str:
        api_key = self.openai_api_key.get().strip()
        if not api_key or OpenAI is None:
            raise RuntimeError("OpenAI API key is not set.")

        client = OpenAI(api_key=api_key)
        target_label = self.target_lang.get()
        tone_instruction = self.build_tone_instruction()
        emotion_instruction = "Do not add extra emotional wording."

        prompt = (
            f"Translate the following message into {target_label}. "
            f"Make it short and suitable for VRChat chatbox. "
            f"{tone_instruction} {emotion_instruction} "
            f"Avoid stiff literal translation.\n\n"
            f"Input:\n{text}"
        )

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a skilled conversational translator for live voice chat.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
        self.add_chatgpt_cost_from_usage(
            getattr(response, "usage", None), model_name="gpt-4o-mini"
        )
        return response.choices[0].message.content.strip()

    def translate_text_value(self, text: str, input_source: str = "voice") -> str:
        # ChatGPT is used ONLY when the ChatGPT checkbox is ON and a valid API key exists.
        # Presets such as High Quality / Natural must not force ChatGPT, because users may use them without an API key.
        use_natural = bool(
            getattr(self, "use_chatgpt_natural", None) is not None
            and self.use_chatgpt_natural.get()
            and self.has_valid_openai_api_key()
        )

        def _translate_with_google(value: str) -> str:
            source_display = self.input_lang.get()
            target_display = self.target_lang.get()
            source_code = self._language_code(source_display)
            target_code = self._language_code(target_display)

            self.log(
                f"[diag] GOOGLE translate source={source_display} ({source_code}) "
                f"target={target_display} ({target_code})"
            )

            return GoogleTranslator(
                source=source_code,
                target=target_code,
            ).translate(value)

        try:
            if use_natural:
                try:
                    target_display = self.target_lang.get()
                    target_code = self._language_code(target_display)
                    self.log(
                        f"[diag] ChatGPT translate target_display={target_display} target_code={target_code}"
                    )

                    translated = self.translate_with_chatgpt(text)

                except Exception as e:
                    err = str(e)
                    if "insufficient_quota" in err or "429" in err:
                        self.log(self.tr("chatgpt_quota_fallback"))
                        try:
                            self.use_chatgpt_natural.set(False)
                            self.update_chatgpt_ui_state()
                        except Exception:
                            pass
                        translated = _translate_with_google(text)
                    else:
                        self.log(f"ChatGPT error: {e}")
                        translated = _translate_with_google(text)
            else:
                translated = _translate_with_google(text)

        except TranslationNotFound:
            self.log(f"Translation skipped (not found): {text}")
            translated = text

        except Exception as e:
            cleaned = text.replace("…", "").strip()
            if cleaned and cleaned != text:
                try:
                    translated = _translate_with_google(cleaned)
                except TranslationNotFound:
                    self.log(f"Translation skipped (not found after cleanup): {text}")
                    translated = text
                except Exception:
                    raise e
            else:
                raise e

        translated = self.clean_text(translated)
        translated = self.shorten_text(translated)

        if self.prefix_message.get():
            prefix_mark = "✏️" if input_source == "manual" else "💬"
            translated = f"{prefix_mark} {translated}"

        return translated

    def show_first_launch_guide(self):
        if not getattr(self, "first_launch_flag", tk.BooleanVar(value=False)).get():
            return
        messagebox.showinfo(
            "Welcome",
            "初回ガイド\n\n・base：軽量・高速\n・small：高精度\n\nまずはbaseで動作確認後、精度が必要ならsmallをおすすめします。",
        )
        self.first_launch_flag.set(False)

    def show_small_recommend(self):
        if self.small_recommend_shown.get():
            return
        messagebox.showinfo(
            "Tip",
            "精度を上げたい場合は『small（高精度）』モデルの使用をおすすめします。",
        )
        self.small_recommend_shown.set(True)

    def apply_accuracy_profile(self):
        self.start_threshold.set(0.012)
        self.stop_threshold.set(0.010)
        self.silence_seconds.set(1.0)
        self.pre_roll_seconds.set(0.8)
        self.max_record_seconds.set(8.0)
        self.min_record_seconds.set(1.2)
        self.translation_preset.set(self.tr("preset_normal_label"))
        self.log(self.tr("accuracy_applied"))
        self.save_settings(write_log=False)

    def apply_preset(self, preset_name: str):
        normalized = self.normalize_preset_value(preset_name)
        preset = TRANSLATION_PRESETS.get(normalized)
        if not preset:
            return
        self._applying_preset = True
        try:
            if normalized == "最速（リアルタイム）":
                self.translation_preset.set(self.tr("preset_fast_label"))
            elif normalized == "標準":
                self.translation_preset.set(self.tr("preset_normal_label"))
            elif normalized == "高品質":
                self.translation_preset.set(self.tr("preset_quality_label"))
            else:
                # Old saved "Accuracy First" values are displayed as High Quality in the new 3-preset UI.
                self.translation_preset.set(self.tr("preset_quality_label"))
            self.translation_mode.set(preset["translation_mode"])
            self.silence_seconds.set(preset["silence_seconds"])
            self.max_record_seconds.set(preset["max_record_seconds"])
            self.start_threshold.set(preset["start_threshold"])
            self.stop_threshold.set(preset["stop_threshold"])
            self.pre_roll_seconds.set(preset["pre_roll_seconds"])
            if "min_record_seconds" in preset:
                self.min_record_seconds.set(preset["min_record_seconds"])
            self.update_model_guidance_text()
            self.update_current_mode_text()
        finally:
            self._applying_preset = False
        self.save_settings(write_log=False)
        self.log(f"Preset applied: {preset_name}")

    def confirm_preview_and_send(
        self, original_text: str, translated_text: str
    ) -> bool:
        if not self.preview_mode.get():
            return True
        decision = {"send": False}
        try:
            dialog = tk.Toplevel(self.root)
            dialog.title(self.tr("preview_title"))
            dialog.transient(self.root)
            dialog.grab_set()
            dialog.resizable(False, False)
            dialog.configure(bg="#0b1220")

            frame = ttk.Frame(dialog, padding=16, style="Dark.TFrame")
            frame.pack(fill="both", expand=True)

            ttk.Label(frame, text=self.tr("original_label"), style="Muted.TLabel").pack(
                anchor="w"
            )
            original_box = tk.Text(
                frame,
                height=4,
                width=72,
                bg="#081225",
                fg="#e5e7eb",
                insertbackground="#e5e7eb",
                wrap="word",
            )
            original_box.pack(fill="x", pady=(2, 8))
            original_box.insert("1.0", original_text)
            original_box.configure(state="disabled")

            ttk.Label(
                frame, text=self.tr("translated_label"), style="Muted.TLabel"
            ).pack(anchor="w")
            translated_box = tk.Text(
                frame,
                height=4,
                width=72,
                bg="#081225",
                fg="#e5e7eb",
                insertbackground="#e5e7eb",
                wrap="word",
            )
            translated_box.pack(fill="x", pady=(2, 12))
            translated_box.insert("1.0", translated_text)
            translated_box.configure(state="disabled")

            btns = ttk.Frame(frame, style="Dark.TFrame")
            btns.pack(fill="x")

            def do_send():
                decision["send"] = True
                dialog.destroy()

            ttk.Button(
                btns,
                text=self.tr("preview_send"),
                command=do_send,
                style="Dark.TButton",
            ).pack(side="left", padx=(0, 4))
            ttk.Button(
                btns,
                text=self.tr("preview_cancel"),
                command=dialog.destroy,
                style="Dark.TButton",
            ).pack(side="left", padx=(0, 4))

            dialog.update_idletasks()
            x = self.root.winfo_rootx() + max(
                0, (self.root.winfo_width() - dialog.winfo_width()) // 2
            )
            y = self.root.winfo_rooty() + max(
                0, (self.root.winfo_height() - dialog.winfo_height()) // 2
            )
            dialog.geometry(f"800x420+{x}+{y}")
            dialog.wait_window()
        except Exception as e:
            self.log(f"Preview dialog error: {e}")
            return True
        return decision["send"]

    def send_to_vrchat(self, text: str):
        client = SimpleUDPClient(self.osc_ip.get(), int(self.osc_port.get()))
        client.send_message(
            "/chatbox/input", [text, True, self.send_notification.get()]
        )

    def save_temp_wav(self, audio_data):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as f:
            temp_path = f.name
        sf.write(temp_path, audio_data, 16000)
        return temp_path

    def auto_record_once(self):
        """Record one utterance.

        v1.11.2 streaming test:
        - while recording, periodically transcribes recent audio and shows partial text in the Original box when Live recognition is enabled
        - final transcription / translation / VRChat sending is still handled by worker()
        - this keeps behavior safe: partial text is preview only, final text is what gets sent
        """
        samplerate = 16000
        channels = 1
        # 480 samples at 16 kHz = 30 ms, which is compatible with WebRTC VAD.
        blocksize = 480
        start_threshold = float(self.start_threshold.get())
        stop_threshold = float(self.stop_threshold.get())
        silence_seconds = float(self.silence_seconds.get())
        max_record_seconds = float(self.max_record_seconds.get())
        min_record_seconds = float(self.min_record_seconds.get())
        pre_roll_seconds = float(self.pre_roll_seconds.get())
        mic_device = self._selected_mic_index()

        # Voice Activity Detection tuning.
        vad = None
        use_high_accuracy_vad = bool(
            getattr(self, "high_accuracy_vad", tk.BooleanVar(value=True)).get()
        )
        vad_available = bool(
            use_high_accuracy_vad and USE_WEBRTC_VAD and webrtcvad is not None
        )
        if vad_available:
            try:
                vad = webrtcvad.Vad(int(WEBRTC_VAD_MODE))
            except Exception as e:
                vad = None
                vad_available = False
                self.log(f"[VAD] disabled: {e}")
        elif use_high_accuracy_vad and USE_WEBRTC_VAD:
            self.log("[VAD] webrtcvad not installed; using RMS gate")
        elif not use_high_accuracy_vad:
            self.log("[VAD] lightweight RMS gate enabled")

        def is_speech_block(block, level_value, threshold_value):
            """Return True when the block looks like human speech.

            WebRTC VAD is used when available. RMS is kept as a safety gate so
            tiny background noise does not trigger recording. If VAD fails for
            any reason, this falls back to the old RMS behavior.
            """
            if vad is None:
                return level_value >= threshold_value
            try:
                mono = np.asarray(block, dtype=np.float32).reshape(-1)
                mono = np.clip(mono, -1.0, 1.0)
                pcm16 = (mono * 32767).astype(np.int16).tobytes()
                speech = vad.is_speech(pcm16, samplerate)
                return speech and level_value >= (
                    threshold_value * float(VAD_START_RMS_FACTOR)
                )
            except Exception:
                return level_value >= threshold_value

        # Streaming preview tuning.
        # These constants are defined near the top of this file so they are easy to adjust.
        partial_interval_seconds = PARTIAL_INTERVAL_SEC
        partial_recent_seconds = PARTIAL_RECENT_SEC
        partial_min_seconds = PARTIAL_MIN_SEC
        last_partial_time = 0.0
        last_partial_text = ""

        audio_queue = queue.Queue()

        def callback(indata, frames, time_info, status):
            if status:
                self.log(f"{self.tr('audio_status')}: {status}")
            audio_queue.put(indata.copy())
            rms = float(np.sqrt(np.mean(indata**2)))
            self._set_level(rms)

        pre_roll_blocks = max(1, int((pre_roll_seconds * samplerate) / blocksize))
        pre_roll_buffer = collections.deque(maxlen=pre_roll_blocks)

        with sd.InputStream(
            samplerate=samplerate,
            channels=channels,
            dtype="float32",
            callback=callback,
            device=mic_device,
            blocksize=blocksize,
        ):
            recording = False
            chunks = []
            start_time = None
            silence_start = None
            self._set_status("status_waiting")
            self.log(
                "[VAD] WebRTC VAD enabled"
                if vad is not None
                else "[VAD] RMS gate enabled"
            )

            while self.running:
                try:
                    indata = audio_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                level = float(np.sqrt(np.mean(indata**2)))
                speech_detected = is_speech_block(indata, level, start_threshold)

                if not recording:
                    pre_roll_buffer.append(indata)
                    if speech_detected:
                        recording = True
                        start_time = time.time()
                        silence_start = None
                        last_partial_time = 0.0
                        last_partial_text = ""
                        chunks.extend(list(pre_roll_buffer))
                        chunks.append(indata)
                        self._set_status("status_recording")
                        self.log(self.tr("recording_started"))
                else:
                    chunks.append(indata)
                    now_time = time.time()

                    # === partial transcription preview ===
                    # Do not send this text.  It only updates the Original box so users can see recognition early.
                    try:
                        elapsed_record = now_time - start_time if start_time else 0.0
                        if (
                            self.streaming_preview.get()
                            and elapsed_record >= partial_min_seconds
                            and (now_time - last_partial_time)
                            >= partial_interval_seconds
                        ):
                            recent_blocks = max(
                                1,
                                int((partial_recent_seconds * samplerate) / blocksize),
                            )
                            recent_audio = np.concatenate(
                                chunks[-recent_blocks:], axis=0
                            )
                            recent_audio_np = np.asarray(
                                recent_audio, dtype=np.float32
                            ).squeeze()
                            self._set_status("status_transcribing")
                            partial_text = self.transcribe_audio_text(
                                recent_audio_np, beam_size=1
                            )
                            if (
                                partial_text
                                and partial_text != last_partial_text
                                and not self.is_blacklisted(partial_text)
                            ):
                                current_original_text = self.clean_text(
                                    self._get_original_text_value()
                                )
                                if (
                                    self.text_input_voice_overwrite.get()
                                    or not current_original_text
                                    or current_original_text == last_partial_text
                                ):
                                    self._set_original(partial_text)
                                last_partial_text = partial_text
                            last_partial_time = now_time
                            self._set_status("status_recording")
                    except Exception as e:
                        # Partial preview must never stop the main recording/transcription flow.
                        self.log(f"[PARTIAL] preview skipped: {e}")
                        self._set_status("status_recording")
                        last_partial_time = now_time

                    if time.time() - start_time >= max_record_seconds:
                        self.log(self.tr("max_record_reached"))
                        break

                    # Stop recording when human speech is no longer detected.
                    # The old RMS gate is kept as fallback when VAD is unavailable.
                    speech_continues = is_speech_block(indata, level, stop_threshold)
                    if not speech_continues:
                        if silence_start is None:
                            silence_start = time.time()
                        elif time.time() - silence_start >= silence_seconds:
                            self.log(self.tr("recording_stopped"))
                            break
                    else:
                        silence_start = None

        if not chunks:
            return None

        audio = np.concatenate(chunks, axis=0)
        duration = len(audio) / samplerate
        peak = float(np.max(np.abs(audio)))
        rms = float(np.sqrt(np.mean(audio**2)))
        self.log(self.tr("record_info", duration=duration, peak=peak, rms=rms))

        if duration < min_record_seconds:
            self.log(self.tr("too_short"))
            return None
        if peak < 0.008 and rms < 0.0025:
            self.log(self.tr("too_silent"))
            return None
        return audio

    def worker(self):
        try:
            self.model_ready = False
            self.log(
                self.tr(
                    "model_loading_log",
                    model=MODEL_DISPLAY_NAMES.get(
                        self.model_name.get(), self.model_name.get()
                    ),
                )
            )
            self.prepare_model()

            while self.running:
                audio = self.auto_record_once()
                if not self.running:
                    break
                if audio is None:
                    continue

                cycle_started_at = time.time()
                self._set_status("status_transcribing")
                audio_np = np.asarray(audio, dtype=np.float32).squeeze()
                original_text = self.transcribe_audio_text(audio_np, beam_size=1)
                if not original_text:
                    self.log(self.tr("transcribe_failed"))
                    continue
                if self.is_blacklisted(original_text):
                    self.log(f"[BLACKLIST] {original_text}")
                    continue
                if not self.is_valid_text(original_text):
                    self.log(self.tr("invalid_text", text=original_text))
                    continue

                now = time.time()
                if original_text == self.last_sent_text and (
                    now - self.last_sent_time
                ) < float(self.skip_same_seconds.get()):
                    self.log(self.tr("duplicate_text"))
                    continue

                current_original_text = self.clean_text(self._get_original_text_value())
                if not self.text_input_voice_overwrite.get() and current_original_text:
                    self.log(
                        "テキスト入力中のため、音声入力をスキップしました"
                        if self.current_ui_lang_code() == "ja"
                        else "Skipped voice input because manual text is being edited"
                    )
                    self._set_status("status_waiting")
                    continue

                if self.text_input_voice_overwrite.get() or not current_original_text:
                    self._set_original(original_text)
                self._set_status("status_translating")
                translated_text = self.translate_text_value(
                    original_text, input_source="voice"
                )
                self._set_translated(translated_text)

                if not self.confirm_preview_and_send(original_text, translated_text):
                    self.log(
                        "Preview cancelled"
                        if self.current_ui_lang_code() != "ja"
                        else "プレビューで送信をキャンセルしました"
                    )
                    self._set_status("status_waiting")
                    continue

                self._set_status("status_sending")
                self.send_to_vrchat(translated_text)
                self.log(self.tr("sent_log", text=translated_text))
                self.save_chat_log_pair(original_text, translated_text, source="voice")

                self.last_sent_text = original_text
                self.last_sent_time = now
                total_elapsed = time.time() - cycle_started_at
                self.set_processing_time(total_elapsed)
                self.log(
                    f"[PERF] total {total_elapsed:.2f}s / mode={self.model_name.get()} / device={self.compute_device.get()} -> {self.resolved_compute_device}"
                )
                self._set_status("status_waiting")
                self.save_settings(write_log=False)
        except Exception as e:
            self.log_exception("Error", e)
            self.show_error_async(str(e))
            self._set_status("status_error")
        finally:
            self.running = False
            self._update_start_button_appearance()
            self._set_status("status_stopped")

    def start(self):
        if self.running:
            return
        self.save_settings(write_log=False)
        self.running = True
        self._update_start_button_appearance()
        self.set_model_status(self.tr("status_loading"))
        self.worker_thread = threading.Thread(target=self.worker, daemon=True)
        self.worker_thread.start()
        self.log(
            f"{self.tr('start_log')} | model={self.model_name.get()} | device={self.compute_device.get()}"
        )

    def stop(self):
        if not self.running:
            return
        self.running = False
        self.model_ready = False
        self.save_settings(write_log=False)
        self.log(self.tr("stop_log"))
        self.set_model_status(self.tr("status_stopped"))
        self._set_status("status_stopped")


# --- v1.11.2 final UI language patch ---
# Source of truth is always self.ui_lang StringVar; older staged patches were collapsed here.
_FINAL_LANG_CODES = {
    "日本語": "ja",
    "English": "en",
    "繁體中文": "zh-TW",
    "繁体中文": "zh-TW",
    "简体中文": "zh-CN",
    "簡体中文": "zh-CN",
    "한국어": "ko",
}
_FINAL_LANG_KEYS = {
    "ja": "日本語",
    "en": "English",
    "zh-TW": "繁體中文",
    "zh-CN": "简体中文",
    "ko": "한국어",
}
_FINAL_PRESET_LABELS = {
    "ja": ("最速：反応が速い", "通常：バランス", "高品質：精度優先"),
    "en": ("Fast: quick response", "Normal: balanced", "High Quality: accuracy first"),
    "zh-TW": ("最快：反應快", "標準：平衡", "高品質：精度優先"),
    "zh-CN": ("最快：反应快", "标准：平衡", "高品质：精度优先"),
    "ko": ("최속: 반응 빠름", "보통: 균형", "고품질: 정확도 우선"),
}
_FINAL_LOG_LABELS = {
    "ja": {
        "label": "ログ表示",
        "all": "すべて",
        "error": "エラーのみ",
        "warn": "警告のみ",
        "success": "成功のみ",
    },
    "en": {
        "label": "Log",
        "all": "All",
        "error": "Errors",
        "warn": "Warnings",
        "success": "Success",
    },
    "zh-TW": {
        "label": "日誌顯示",
        "all": "全部",
        "error": "僅錯誤",
        "warn": "僅警告",
        "success": "僅成功",
    },
    "zh-CN": {
        "label": "日志显示",
        "all": "全部",
        "error": "仅错误",
        "warn": "仅警告",
        "success": "仅成功",
    },
    "ko": {
        "label": "로그 표시",
        "all": "전체",
        "error": "오류만",
        "warn": "경고만",
        "success": "성공만",
    },
}
for _code, _key in _FINAL_LANG_KEYS.items():
    _p = _FINAL_PRESET_LABELS[_code]
    UI_TEXT.setdefault(_key, {}).update(
        {
            "preset_fast_label": _p[0],
            "preset_normal_label": _p[1],
            "preset_quality_label": _p[2],
            "streaming_preview": {
                "ja": "リアルタイム字幕",
                "en": "Live captions",
                "zh-TW": "即時字幕",
                "zh-CN": "实时字幕",
                "ko": "실시간 자막",
            }[_code],
            "streaming_preview_tooltip": {
                "ja": "話している途中から字幕を表示します。OFFの場合は確定後のみ表示します。",
                "en": "Shows captions while you are speaking. When off, text appears only after recognition is finalized.",
                "zh-TW": "說話途中也會顯示字幕。關閉時只會在辨識完成後顯示。",
                "zh-CN": "说话途中也会显示字幕。关闭时只会在识别完成后显示。",
                "ko": "말하는 도중에도 자막을 표시합니다. OFF일 때는 인식이 확정된 뒤에만 표시됩니다.",
            }[_code],
        }
    )


def _final_ui_strip(v):
    return re.sub(r"\s*\([^)]*\)$", "", str(v or "").strip()).strip()


def _final_ui_code(self):
    try:
        raw = _final_ui_strip(self.ui_lang.get())
    except Exception:
        raw = "日本語"
    return _FINAL_LANG_CODES.get(raw, "ja")


def _final_ui_key(self):
    return _FINAL_LANG_KEYS.get(_final_ui_code(self), "日本語")


def _final_tr(self, key, **kwargs):
    lang_key = _final_ui_key(self)
    table = UI_TEXT.get(lang_key, {})
    text = table.get(key)
    if text is None:
        text = (
            UI_TEXT.get("English", {}).get(key)
            if lang_key != "日本語"
            else UI_TEXT.get("日本語", {}).get(key)
        )
    if text is None:
        text = (
            UI_TEXT.get("日本語", {}).get(key)
            or UI_TEXT.get("English", {}).get(key)
            or key
        )
    try:
        return text.format(**kwargs) if kwargs else text
    except Exception:
        return text


def _final_preset_internal(v):
    v = str(v or "").strip()
    fast = {
        "最速（リアルタイム）",
        "最速",
        "最速：反応が速い",
        "Fast (Real-time)",
        "Fast",
        "Fast: quick response",
        "最快（即時）",
        "最快",
        "最快：反應快",
        "最快：反应快",
        "최속（실시간）",
        "최속",
        "최속: 반응 빠름",
    }
    normal = {
        "標準",
        "通常",
        "通常：バランス",
        "Normal",
        "Normal: balanced",
        "標準：平衡",
        "标准：平衡",
        "보통",
        "표준",
        "보통: 균형",
    }
    quality = {
        "高品質",
        "精度優先",
        "高品質：精度優先",
        "High Quality",
        "High Quality: accuracy first",
        "Accuracy First",
        "高品质",
        "高品质：精度优先",
        "고품질",
        "정확도 우선",
        "고품질: 정확도 우선",
    }
    if v in fast:
        return "最速（リアルタイム）"
    if v in quality:
        return "高品質"
    if v in normal:
        return "標準"
    return "標準"


def _final_get_preset_options(self):
    return list(
        _FINAL_PRESET_LABELS.get(_final_ui_code(self), _FINAL_PRESET_LABELS["ja"])
    )


def _final_log_key(v):
    v = str(v or "").strip()
    sets = {
        "all": {"all", "All", "すべて", "全部", "전체"},
        "error": {"error", "Errors", "エラーのみ", "僅錯誤", "仅错误", "오류만"},
        "warn": {"warn", "Warnings", "警告のみ", "僅警告", "仅警告", "경고만"},
        "success": {"success", "Success", "成功のみ", "僅成功", "仅成功", "성공만"},
    }
    for k, s in sets.items():
        if v in s:
            return k
    return "all"


def _final_get_log_options(self):
    l = _FINAL_LOG_LABELS.get(_final_ui_code(self), _FINAL_LOG_LABELS["ja"])
    return [l["all"], l["error"], l["warn"], l["success"]]


def _final_log_label(self, k):
    l = _FINAL_LOG_LABELS.get(_final_ui_code(self), _FINAL_LOG_LABELS["ja"])
    return l.get(k, l["all"])


def _final_cfg(obj, **kw):
    try:
        if obj is not None:
            obj.configure(**kw)
    except Exception:
        pass


def _final_refresh_lang_combos(self):
    try:
        values = [
            self._format_language_display(name) for name in LANGUAGE_OPTIONS.keys()
        ]
        self.input_lang_combo.configure(values=values)
        self.target_lang_combo.configure(values=values)
        self.input_lang.set(
            self._format_language_display(
                self._base_language_name(self.input_lang.get())
            )
        )
        self.target_lang.set(
            self._format_language_display(
                self._base_language_name(self.target_lang.get())
            )
        )
    except Exception:
        pass


def _final_refresh_ui(self):
    # Never call older refresh_ui_language versions here; they may schedule stale language updates.
    code = _final_ui_code(self)
    display = _FINAL_LANG_KEYS.get(code, "日本語")
    try:
        self.ui_lang.set(display)
        self.ui_lang_combo.configure(
            values=["日本語", "English", "繁體中文", "简体中文", "한국어"]
        )
        self.ui_lang_combo.set(display)
    except Exception:
        pass
    try:
        self.root.title(f"{self.tr('app_title')} v{APP_VERSION}")
    except Exception:
        pass

    for attr, key in [
        ("title_label", "app_title"),
        ("label_input_lang", "input_language"),
        ("label_target_lang", "target_language"),
        ("label_ui_lang", "ui_language"),
        ("label_mic", "mic_number"),
        ("btn_readme", "readme_button"),
        ("btn_check_updates", "update_button_short"),
        ("btn_license", "license_button"),
        ("label_start_threshold", "start_threshold"),
        ("label_stop_threshold", "stop_threshold"),
        ("label_silence", "silence_seconds"),
        ("label_max_record", "max_record_seconds"),
        ("label_pre_roll", "pre_roll_seconds"),
        ("label_max_chat", "max_chat_len"),
        ("label_preset", "preset"),
        ("label_tone", "tone"),
        ("btn_save_blacklist", "save_blacklist"),
        ("chatgpt_history_link", "open_usage_history"),
        ("chk_preview_mode", "preview_mode"),
        ("btn_open_log_folder", "open_logs_button"),
        ("btn_refresh_bottom", "refresh_mics"),
        ("btn_test_osc_bottom", "test_osc"),
        ("btn_delete_model_bottom", "delete_model_button"),
        ("btn_start", "start"),
        ("btn_stop", "stop"),
        ("btn_details_settings", "advanced_settings"),
        ("btn_add_original_to_blacklist", "add_original_to_blacklist"),
        ("label_input_level", "input_level"),
        ("label_device", "device"),
        ("label_model_cache", "model_cache_state"),
        ("label_model_guidance", "model_guidance"),
        ("label_model_download", "model_download_progress_label"),
        ("label_translation_mode", "translation_mode_label"),
        ("label_model_status", "model_status"),
        ("label_current_mode", "current_mode_label"),
        ("label_processing_time", "processing_time_label"),
        ("btn_setup_guide", "guide_button"),
    ]:
        _final_cfg(getattr(self, attr, None), text=self.tr(key))
    for attr, key in [
        ("controls", "settings"),
        ("info_frame", "status_group"),
        ("original_frame", "original_input"),
        ("translated_frame", "translated"),
        ("log_frame", "log"),
        ("blacklist_frame", "blacklist"),
    ]:
        _final_cfg(getattr(self, attr, None), text=self.tr(key))
    _final_cfg(
        getattr(self, "label_model", None),
        text=f"{self.tr('model')} ({self.tr('speech_recognition_suffix')})",
    )
    _final_cfg(
        getattr(self, "label_api", None),
        text=f"{self.tr('openai_api_key')} ({self.tr('api_key_optional_note')})",
    )
    try:
        self.update_api_key_tooltip()
    except Exception:
        pass
    try:
        self.chk_chatgpt_natural.configure(
            text=self.tr("chatgpt_translation_mode_short")
        )
    except Exception:
        pass
    try:
        if hasattr(self, "chk_streaming_preview_details"):
            self.chk_streaming_preview_details.configure(
                text=self.tr("streaming_preview")
            )
            self.create_tooltip(
                self.chk_streaming_preview_details, self.tr("streaming_preview_tooltip")
            )
    except Exception:
        pass

    _final_refresh_lang_combos(self)
    try:
        self.model_combo.configure(values=self.get_model_display_options())
        self.model_name.set(
            self.get_model_display_label(
                self.normalize_model_value(self.model_name.get())
            )
        )
    except Exception:
        pass
    try:
        internal = _final_preset_internal(self.translation_preset.get())
        labels = _FINAL_PRESET_LABELS.get(code, _FINAL_PRESET_LABELS["ja"])
        idx = {"最速（リアルタイム）": 0, "標準": 1, "高品質": 2}.get(internal, 1)
        self.preset_combo.configure(values=list(labels))
        self.translation_preset.set(labels[idx])
    except Exception:
        pass
    try:
        self.tone_combo.configure(values=self.get_tone_display_options())
        tone = self.normalize_tone_value(self.tone_style.get())
        self.tone_style.set(
            self.tr("tone_polite_label")
            if tone == "丁寧"
            else self.tr("tone_friendly_label")
        )
    except Exception:
        pass
    try:
        lf = _FINAL_LOG_LABELS.get(code, _FINAL_LOG_LABELS["ja"])
        _final_cfg(getattr(self, "label_log_filter", None), text=lf["label"])
        cur = _final_log_key(self.log_filter_display.get())
        self.log_filter_combo.configure(
            values=[lf["all"], lf["error"], lf["warn"], lf["success"]]
        )
        self.log_filter_display.set(lf.get(cur, lf["all"]))
    except Exception:
        pass
    for fn in (
        "update_chatgpt_cost_display",
        "update_chatgpt_checkbox_color",
        "update_chatgpt_tooltip",
        "update_model_guidance_text",
        "update_translation_mode_text",
        "update_current_mode_text",
        "update_model_cache_text",
        "_update_footer_hint",
        "_rerender_log_history",
    ):
        try:
            getattr(self, fn)()
        except Exception:
            pass
    try:
        self._set_status(getattr(self, "_current_status_key", "status_stopped"))
    except Exception:
        pass
    try:
        if self.model_ready:
            self.set_model_status(
                f"{MODEL_DISPLAY_NAMES.get(self.normalize_model_value(self.model_name.get()), self.normalize_model_value(self.model_name.get()))} | {self.tr('model_ready')} ({getattr(self, 'resolved_compute_device', 'cpu')})"
            )
        elif not self.running:
            self.set_model_status(self.tr("status_stopped"))
    except Exception:
        pass


def _final_apply_preset(self, preset_name):
    internal = _final_preset_internal(preset_name)
    preset = TRANSLATION_PRESETS.get(internal)
    if not preset:
        return
    self._applying_preset = True
    try:
        labels = _FINAL_PRESET_LABELS.get(
            _final_ui_code(self), _FINAL_PRESET_LABELS["ja"]
        )
        idx = {"最速（リアルタイム）": 0, "標準": 1, "高品質": 2}.get(internal, 1)
        self.translation_preset.set(labels[idx])
        self.translation_mode.set(
            preset.get("translation_mode", self.translation_mode.get())
        )
        self.silence_seconds.set(
            preset.get("silence_seconds", self.silence_seconds.get())
        )
        self.max_record_seconds.set(
            preset.get("max_record_seconds", self.max_record_seconds.get())
        )
        self.start_threshold.set(
            preset.get("start_threshold", self.start_threshold.get())
        )
        self.stop_threshold.set(preset.get("stop_threshold", self.stop_threshold.get()))
        self.pre_roll_seconds.set(
            preset.get("pre_roll_seconds", self.pre_roll_seconds.get())
        )
        if "min_record_seconds" in preset:
            self.min_record_seconds.set(preset["min_record_seconds"])
    finally:
        self._applying_preset = False


VRChatTranslatorGUI.current_ui_lang_code = _final_ui_code
VRChatTranslatorGUI.current_ui_lang_display = _final_ui_key
VRChatTranslatorGUI.current_ui_text_key = _final_ui_key
VRChatTranslatorGUI._normalized_ui_lang_key = _final_ui_key
VRChatTranslatorGUI.tr = _final_tr
VRChatTranslatorGUI.get_preset_display_options = _final_get_preset_options
VRChatTranslatorGUI.normalize_preset_value = lambda self, value: _final_preset_internal(
    value
)
VRChatTranslatorGUI.get_log_filter_display_options = _final_get_log_options
VRChatTranslatorGUI.normalize_log_filter_value = lambda self, value: _final_log_key(
    value
)
VRChatTranslatorGUI.get_log_filter_display_label = _final_log_label
VRChatTranslatorGUI.refresh_ui_language = _final_refresh_ui
VRChatTranslatorGUI.apply_preset = _final_apply_preset

# --- v1.11.2 VAD first setup + UI wording ---
_VAD_LANG_TEXT = {
    "日本語": {
        "high_accuracy_vad": "高精度音声検出",
        "high_accuracy_vad_tooltip": "ON: 自然な発話区切り（おすすめ） / OFF: 軽量・安定（低スペック向け）",
        "vad_first_title": "音声検出の設定",
        "vad_first_message": "このアプリは音声の区切りを自動検出できます。\n初回のみ表示されます。\n\n高精度（自然な区切り）：\n自然なタイミングで発話を区切ります。\n\n軽量（動作が軽い）：\nシンプルで安定した動作になります。\n\nどちらを使用しますか？",
        "vad_first_yes": "高精度（自然な区切り）",
        "vad_first_no": "軽量（動作が軽い）",
    },
    "English": {
        "high_accuracy_vad": "High-accuracy speech detection",
        "high_accuracy_vad_tooltip": "ON: Natural speech segmentation (recommended) / OFF: Lightweight and stable",
        "vad_first_title": "Speech Detection Setup",
        "vad_first_message": "This app can automatically detect when you finish speaking.\nShown only on first setup.\n\nHigh Accuracy (Natural segmentation):\nMore natural speech segmentation.\n\nLightweight (Lower load):\nSimpler and more stable behavior.\n\nWhich mode would you like?",
        "vad_first_yes": "High Accuracy (Natural segmentation)",
        "vad_first_no": "Lightweight (Lower load)",
    },
    "繁體中文": {
        "high_accuracy_vad": "高精度語音偵測",
        "high_accuracy_vad_tooltip": "ON：較自然的發話分段（推薦） / OFF：較輕量、穩定",
        "vad_first_title": "語音偵測模式",
        "vad_first_message": "此應用程式可以自動偵測發話結束。\n僅在初次設定時顯示。\n\n高精度（自然分段）：\n以較自然的時機分段。\n\n輕量（負載較低）：\n較簡單且穩定。\n\n要使用哪一種模式？",
        "vad_first_yes": "高精度（自然分段）",
        "vad_first_no": "輕量（負載較低）",
    },
    "简体中文": {
        "high_accuracy_vad": "高精度语音检测",
        "high_accuracy_vad_tooltip": "ON：更自然的发话分段（推荐） / OFF：更轻量、稳定",
        "vad_first_title": "语音检测模式",
        "vad_first_message": "此应用可以自动检测你何时说完。\n仅在首次设置时显示。\n\n高精度（自然分段）：\n更自然地分段语音。\n\n轻量（负载更低）：\n更简单且稳定。\n\n要使用哪种模式？",
        "vad_first_yes": "高精度（自然分段）",
        "vad_first_no": "轻量（负载更低）",
    },
    "한국어": {
        "high_accuracy_vad": "고정밀 음성 감지",
        "high_accuracy_vad_tooltip": "ON: 자연스러운 발화 구분（추천） / OFF: 가볍고 안정적",
        "vad_first_title": "음성 감지 모드",
        "vad_first_message": "이 앱은 말이 끝나는 타이밍을 자동으로 감지할 수 있습니다.\n첫 설정 때만 표시됩니다.\n\n고정밀（자연스러운 구분）：\n더 자연스러운 타이밍으로 발화를 구분합니다.\n\n경량（낮은 부하）：\n더 단순하고 안정적으로 동작합니다.\n\n어느 모드를 사용할까요?",
        "vad_first_yes": "고정밀（자연스러운 구분）",
        "vad_first_no": "경량（낮은 부하）",
    },
}
for _lang, _vals in _VAD_LANG_TEXT.items():
    UI_TEXT.setdefault(_lang, {}).update(_vals)

_prev_vad_refresh_ui_language = VRChatTranslatorGUI.refresh_ui_language


def _vad_refresh_ui_language(self):
    _prev_vad_refresh_ui_language(self)
    try:
        if hasattr(self, "chk_high_accuracy_vad_details"):
            self.chk_high_accuracy_vad_details.configure(
                text=self.tr("high_accuracy_vad")
            )
            self.create_tooltip(
                self.chk_high_accuracy_vad_details, self.tr("high_accuracy_vad_tooltip")
            )
    except Exception:
        pass


VRChatTranslatorGUI.refresh_ui_language = _vad_refresh_ui_language


def _vad_show_first_launch_guide(self):
    """First-run speech detection setup dialog.

    Use plain tk widgets here instead of ttk styles, because this dialog may be
    shown before all ttk styles are fully refreshed. This avoids the blank white
    popup issue on direct .py launch.
    """
    try:
        flag = getattr(self, "first_launch_flag", None)
        if flag is not None and not flag.get():
            return
    except Exception:
        return

    dialog = tk.Toplevel(self.root)
    dialog.title(self.tr("vad_first_title"))
    dialog.configure(bg="#111827")
    dialog.transient(self.root)
    dialog.grab_set()
    try:
        dialog.resizable(False, False)
    except Exception:
        pass

    frame = tk.Frame(dialog, bg="#111827", padx=18, pady=18)
    frame.pack(fill="both", expand=True)

    title = tk.Label(
        frame,
        text=self.tr("vad_first_title"),
        bg="#111827",
        fg="#f8fafc",
        font=("Segoe UI", 14, "bold"),
        anchor="w",
        justify="left",
    )
    title.pack(anchor="w", fill="x", pady=(0, 10))

    message = tk.Label(
        frame,
        text=self.tr("vad_first_message"),
        bg="#111827",
        fg="#cbd5e1",
        font=("Segoe UI", 10),
        anchor="w",
        justify="left",
        wraplength=480,
    )
    message.pack(anchor="w", fill="x")

    btns = tk.Frame(frame, bg="#111827")
    btns.pack(fill="x", pady=(18, 0))

    def choose(value):
        try:
            self.high_accuracy_vad.set(bool(value))
        except Exception:
            pass
        try:
            self.vad_setup_done = True
        except Exception:
            pass
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            self.save_settings(write_log=False)
            try:
                self.log(f"初回音声検出設定を保存しました: {SETTINGS_FILE}")
            except Exception:
                pass
        except Exception as e:
            try:
                self.log(f"初回音声検出設定の保存に失敗しました: {e}")
            except Exception:
                pass
        try:
            self.first_launch_flag.set(False)
        except Exception:
            pass
        try:
            dialog.grab_release()
        except Exception:
            pass
        dialog.destroy()

    yes_btn = tk.Button(
        btns,
        text=self.tr("vad_first_yes"),
        command=lambda: choose(True),
        bg="#3b82f6",
        fg="#ffffff",
        activebackground="#2563eb",
        activeforeground="#ffffff",
        relief="flat",
        bd=0,
        padx=16,
        pady=8,
        cursor="hand2",
        font=("Segoe UI", 10, "bold"),
    )
    yes_btn.pack(fill="x", pady=(0, 8))
    try:
        yes_btn.focus_set()
    except Exception:
        pass

    no_btn = tk.Button(
        btns,
        text=self.tr("vad_first_no"),
        command=lambda: choose(False),
        bg="#1f2937",
        fg="#e5e7eb",
        activebackground="#374151",
        activeforeground="#ffffff",
        relief="flat",
        bd=0,
        padx=16,
        pady=8,
        cursor="hand2",
        font=("Segoe UI", 10),
    )
    no_btn.pack(fill="x")

    try:
        dialog.protocol("WM_DELETE_WINDOW", lambda: choose(True))
    except Exception:
        pass

    try:
        dialog.update_idletasks()
        x = (
            self.root.winfo_rootx()
            + (self.root.winfo_width() - dialog.winfo_width()) // 2
        )
        y = (
            self.root.winfo_rooty()
            + (self.root.winfo_height() - dialog.winfo_height()) // 2
        )
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
    except Exception:
        pass


VRChatTranslatorGUI.show_first_launch_guide = _vad_show_first_launch_guide
# --- End v1.11.2 VAD first setup + UI wording ---

# --- End v1.11.2 FINAL UI LANGUAGE FIX ---


# --- v1.12.1 Calibration feature patch ---
_CALIBRATION_LANG_TEXT = {
    "日本語": {
        "calibrate_button": "キャリブレーション",
        "calibration_title": "音声キャリブレーション",
        "calibration_busy": "キャリブレーションは停止中に実行してください。",
        "calibration_silence_prompt": "これから環境ノイズを測定します。\n\n3秒間、話さずに静かにしてください。\n\n※音声データは保存されません。測定値だけを設定に保存します。",
        "calibration_speech_prompt": "次に話し方を測定します。\n\n5秒以内で、普段VRChatで話す声量と距離で次のように読んでください：\n\n「こんにちは。音声認識のテストをしています。今日は自然な速さで話しています。」\n\n準備できたらOKを押してください。",
        "calibration_started": "音声キャリブレーションを開始します。",
        "calibration_silence_log": "環境ノイズを測定中です。3秒間静かにしてください。",
        "calibration_speech_log": "発話サンプルを測定中です。普段の声量で話してください。",
        "calibration_complete": "キャリブレーション完了: ノイズRMS={noise:.6f} / 発話RMS={speech:.6f} / 開始しきい値={start:.4f} / 終了しきい値={stop:.4f} / 無音終了={silence:.2f}s",
        "calibration_failed": "キャリブレーションに失敗しました: {error}",
        "calibration_done_message": "キャリブレーションが完了しました。\n\n開始しきい値: {start:.4f}\n終了しきい値: {stop:.4f}\n無音終了秒数: {silence:.2f}s\n\n音声データは保存していません。",
        "calibration_hint": "ヒント: キャリブレーションを行うと、マイク音量や部屋ノイズに合わせて音声検出を自動調整できます。",
        "calibration_guide": "\n\n【音声キャリブレーションについて】\n・画面上部の「キャリブレーション」を押すと、環境ノイズと普段の話し方を測定します。\n・音声データは保存されません。開始しきい値、終了しきい値、無音終了秒数などの測定値だけを設定に保存します。\n・マイクを変えた時、部屋のノイズが変わった時、誤爆や途切れが気になる時に実行してください。",
        "vad_first_extra_note": "\n\n※この設定は詳細設定でいつでも変更できます。",
    },
    "English": {
        "calibrate_button": "Calibrate",
        "calibration_title": "Voice Calibration",
        "calibration_busy": "Please run calibration while the translator is stopped.",
        "calibration_silence_prompt": "The app will measure background noise.\n\nPlease stay silent for 3 seconds.\n\nAudio data will not be saved. Only calibration values are saved.",
        "calibration_speech_prompt": 'Next, the app will measure your speaking style.\n\nWithin 5 seconds, read this in your normal VRChat speaking voice:\n\n"Hello. I am testing speech recognition. I am speaking at a natural speed today."\n\nPress OK when you are ready.',
        "calibration_started": "Starting voice calibration.",
        "calibration_silence_log": "Measuring background noise. Please stay silent for 3 seconds.",
        "calibration_speech_log": "Measuring speech sample. Please speak at your normal volume.",
        "calibration_complete": "Calibration complete: noise RMS={noise:.6f} / speech RMS={speech:.6f} / start threshold={start:.4f} / stop threshold={stop:.4f} / silence={silence:.2f}s",
        "calibration_failed": "Calibration failed: {error}",
        "calibration_done_message": "Calibration is complete.\n\nStart threshold: {start:.4f}\nStop threshold: {stop:.4f}\nSilence seconds: {silence:.2f}s\n\nAudio data was not saved.",
        "calibration_hint": "Tip: Calibration adjusts speech detection to your microphone volume and room noise.",
        "calibration_guide": '\n\n[About Voice Calibration]\n- Press "Calibrate" at the top of the window to measure background noise and your normal speaking style.\n- Audio data is not saved. Only calibration values such as start threshold, stop threshold, and silence seconds are saved.\n- Run it when you change microphones, move to a noisier room, or notice false starts / cut-off speech.',
        "vad_first_extra_note": "\n\nYou can change this setting anytime in Advanced Settings.",
    },
    "繁體中文": {
        "calibrate_button": "校準",
        "calibration_title": "語音校準",
        "calibration_busy": "請在停止翻譯時執行校準。",
        "calibration_silence_prompt": "接下來會測量環境噪音。\n\n請保持安靜 3 秒。\n\n不會保存音訊資料，只會保存校準數值。",
        "calibration_speech_prompt": "接下來會測量您的說話方式。\n\n請在 5 秒內，用平常在 VRChat 說話的音量與距離朗讀：\n\n「你好。正在測試語音辨識。今天我用自然的速度說話。」\n\n準備好後請按 OK。",
        "calibration_started": "開始語音校準。",
        "calibration_silence_log": "正在測量環境噪音。請保持安靜 3 秒。",
        "calibration_speech_log": "正在測量發話樣本。請用平常的音量說話。",
        "calibration_complete": "校準完成: 噪音RMS={noise:.6f} / 發話RMS={speech:.6f} / 開始閾值={start:.4f} / 結束閾值={stop:.4f} / 靜音結束={silence:.2f}s",
        "calibration_failed": "校準失敗: {error}",
        "calibration_done_message": "校準完成。\n\n開始閾值: {start:.4f}\n結束閾值: {stop:.4f}\n靜音結束秒數: {silence:.2f}s\n\n音訊資料沒有被保存。",
        "calibration_hint": "提示: 校準可依照麥克風音量與環境噪音自動調整語音偵測。",
        "calibration_guide": "\n\n【關於語音校準】\n・按下畫面上方的「校準」可測量環境噪音與平常的說話方式。\n・不會保存音訊資料，只會保存開始閾值、結束閾值、靜音結束秒數等校準數值。\n・更換麥克風、環境變吵、或發生誤啟動與語音被切斷時，建議執行校準。",
        "vad_first_extra_note": "\n\n※此設定可隨時在詳細設定中變更。",
    },
    "简体中文": {
        "calibrate_button": "校准",
        "calibration_title": "语音校准",
        "calibration_busy": "请在停止翻译时执行校准。",
        "calibration_silence_prompt": "接下来会测量环境噪音。\n\n请保持安静 3 秒。\n\n不会保存音频数据，只会保存校准数值。",
        "calibration_speech_prompt": "接下来会测量您的说话方式。\n\n请在 5 秒内，用平常在 VRChat 说话的音量和距离朗读：\n\n“你好。正在测试语音识别。今天我用自然的速度说话。”\n\n准备好后请按 OK。",
        "calibration_started": "开始语音校准。",
        "calibration_silence_log": "正在测量环境噪音。请保持安静 3 秒。",
        "calibration_speech_log": "正在测量发话样本。请用平常的音量说话。",
        "calibration_complete": "校准完成: 噪音RMS={noise:.6f} / 发话RMS={speech:.6f} / 开始阈值={start:.4f} / 结束阈值={stop:.4f} / 静音结束={silence:.2f}s",
        "calibration_failed": "校准失败: {error}",
        "calibration_done_message": "校准完成。\n\n开始阈值: {start:.4f}\n结束阈值: {stop:.4f}\n静音结束秒数: {silence:.2f}s\n\n音频数据没有被保存。",
        "calibration_hint": "提示: 校准可以根据麦克风音量和环境噪音自动调整语音检测。",
        "calibration_guide": "\n\n【关于语音校准】\n・点击窗口上方的“校准”，可以测量环境噪音和平常的说话方式。\n・不会保存音频数据，只会保存开始阈值、结束阈值、静音结束秒数等校准数值。\n・更换麦克风、环境变吵、或出现误启动与语音被切断时，建议执行校准。",
        "vad_first_extra_note": "\n\n※此设置可随时在详细设置中更改。",
    },
    "한국어": {
        "calibrate_button": "캘리브레이션",
        "calibration_title": "음성 캘리브레이션",
        "calibration_busy": "번역이 정지된 상태에서 캘리브레이션을 실행해 주세요.",
        "calibration_silence_prompt": "이제 주변 소음을 측정합니다.\n\n3초 동안 말하지 말고 조용히 있어 주세요.\n\n음성 데이터는 저장되지 않으며, 측정값만 설정에 저장됩니다.",
        "calibration_speech_prompt": "다음으로 평소 말하는 방식을 측정합니다.\n\n5초 안에 VRChat에서 평소 말하는 음량과 거리로 다음 문장을 읽어 주세요:\n\n“안녕하세요. 음성 인식 테스트를 하고 있습니다. 오늘은 자연스러운 속도로 말하고 있습니다.”\n\n준비되면 OK를 눌러 주세요.",
        "calibration_started": "음성 캘리브레이션을 시작합니다.",
        "calibration_silence_log": "주변 소음을 측정 중입니다. 3초 동안 조용히 있어 주세요.",
        "calibration_speech_log": "발화 샘플을 측정 중입니다. 평소 음량으로 말해 주세요.",
        "calibration_complete": "캘리브레이션 완료: 소음RMS={noise:.6f} / 발화RMS={speech:.6f} / 시작 임계값={start:.4f} / 종료 임계값={stop:.4f} / 무음 종료={silence:.2f}s",
        "calibration_failed": "캘리브레이션 실패: {error}",
        "calibration_done_message": "캘리브레이션이 완료되었습니다.\n\n시작 임계값: {start:.4f}\n종료 임계값: {stop:.4f}\n무음 종료 초: {silence:.2f}s\n\n음성 데이터는 저장되지 않았습니다.",
        "calibration_hint": "팁: 캘리브레이션을 실행하면 마이크 음량과 주변 소음에 맞춰 음성 감지가 자동 조정됩니다.",
        "calibration_guide": "\n\n【음성 캘리브레이션 안내】\n・화면 상단의 “캘리브레이션”을 누르면 주변 소음과 평소 말하는 방식을 측정합니다.\n・음성 데이터는 저장되지 않습니다. 시작 임계값, 종료 임계값, 무음 종료 초 등의 측정값만 설정에 저장됩니다.\n・마이크를 바꿨거나, 방이 시끄러워졌거나, 오작동이나 말 끊김이 신경 쓰일 때 실행해 주세요.",
        "vad_first_extra_note": "\n\n※이 설정은 상세 설정에서 언제든지 변경할 수 있습니다.",
    },
}

for _lang, _vals in _CALIBRATION_LANG_TEXT.items():
    UI_TEXT.setdefault(_lang, {}).update(_vals)
    try:
        # Add calibration hints without duplicating them on repeated imports.
        hints = UI_TEXT[_lang].setdefault("footer_hints", [])
        hint = _vals.get("calibration_hint")
        if isinstance(hints, list) and hint and hint not in hints:
            hints.append(hint)

        guide = _vals.get("calibration_guide")
        if (
            guide
            and "setup_guide_body" in UI_TEXT[_lang]
            and guide not in UI_TEXT[_lang]["setup_guide_body"]
        ):
            UI_TEXT[_lang]["setup_guide_body"] += guide

        extra = _vals.get("vad_first_extra_note")
        if (
            extra
            and "vad_first_message" in UI_TEXT[_lang]
            and extra not in UI_TEXT[_lang]["vad_first_message"]
        ):
            UI_TEXT[_lang]["vad_first_message"] += extra
    except Exception:
        pass


def _calibration_measure_rms(
    self, seconds: float, samplerate: int, channels: int, device
):
    """Record a short sample and return (rms, peak).

    The recorded audio is kept only in memory for calibration and is discarded
    immediately after RMS/peak calculation.
    """
    frames = max(1, int(float(seconds) * int(samplerate)))
    audio = sd.rec(
        frames,
        samplerate=samplerate,
        channels=channels,
        dtype="float32",
        device=device,
    )
    sd.wait()
    arr = np.asarray(audio, dtype=np.float32)
    if arr.size <= 0:
        return 0.0, 0.0
    rms = float(np.sqrt(np.mean(arr**2)))
    peak = float(np.max(np.abs(arr)))
    return rms, peak


def _calibration_pick_silence_seconds(
    self, speech_rms: float, noise_rms: float
) -> float:
    """Choose a gentle silence duration based on speaking/noise ratio."""
    try:
        ratio = float(speech_rms) / max(float(noise_rms), 1e-6)
    except Exception:
        ratio = 10.0
    if ratio < 3.0:
        return 1.1
    if ratio > 12.0:
        return 0.75
    return 0.9


def _calibration_run(self):
    if getattr(self, "running", False):
        messagebox.showinfo(self.tr("calibration_title"), self.tr("calibration_busy"))
        return

    try:
        mic_device = self._selected_mic_index()
    except Exception as e:
        messagebox.showerror(
            self.tr("error_title"), f"{self.tr('no_input_device')}\n\n{e}"
        )
        return

    samplerate = 16000
    channels = 1

    try:
        self.log(self.tr("calibration_started"))
        messagebox.showinfo(
            self.tr("calibration_title"), self.tr("calibration_silence_prompt")
        )
        self.log(self.tr("calibration_silence_log"))
        self._set_status("status_waiting")
        self.root.update_idletasks()
        noise_rms, noise_peak = self._calibration_measure_rms(
            3.0, samplerate, channels, mic_device
        )

        messagebox.showinfo(
            self.tr("calibration_title"), self.tr("calibration_speech_prompt")
        )
        self.log(self.tr("calibration_speech_log"))
        self._set_status("status_recording")
        self.root.update_idletasks()
        speech_rms, speech_peak = self._calibration_measure_rms(
            5.0, samplerate, channels, mic_device
        )

        # Use both room noise and the user's natural speaking level.
        # Values are clamped so a bad sample cannot make the app unusable.
        start_threshold = max(0.004, noise_rms * 3.5, speech_rms * 0.18)
        start_threshold = min(max(start_threshold, 0.004), 0.080)

        stop_threshold = max(0.003, noise_rms * 2.2, start_threshold * 0.55)
        stop_threshold = min(
            max(stop_threshold, 0.003), max(0.003, start_threshold * 0.90)
        )

        silence_seconds = self._calibration_pick_silence_seconds(speech_rms, noise_rms)

        self.start_threshold.set(round(start_threshold, 4))
        self.stop_threshold.set(round(stop_threshold, 4))
        self.silence_seconds.set(round(silence_seconds, 2))

        try:
            if hasattr(self, "translation_preset"):
                self.translation_preset.set(self.tr("custom_preset"))
        except Exception:
            pass

        self.save_settings(write_log=False)
        self._set_status("status_stopped")

        msg = self.tr("calibration_complete").format(
            noise=noise_rms,
            speech=speech_rms,
            start=start_threshold,
            stop=stop_threshold,
            silence=silence_seconds,
        )
        self.log(msg)
        messagebox.showinfo(
            self.tr("calibration_title"),
            self.tr("calibration_done_message").format(
                start=start_threshold,
                stop=stop_threshold,
                silence=silence_seconds,
            ),
        )
    except Exception as e:
        try:
            self._set_status("status_stopped")
        except Exception:
            pass
        self.log(self.tr("calibration_failed").format(error=e))
        messagebox.showerror(
            self.tr("error_title"),
            self.tr("calibration_failed").format(error=e),
        )


VRChatTranslatorGUI._calibration_measure_rms = _calibration_measure_rms
VRChatTranslatorGUI._calibration_pick_silence_seconds = (
    _calibration_pick_silence_seconds
)
VRChatTranslatorGUI.run_calibration = _calibration_run
# --- End v1.12.1 Calibration feature patch ---


# ===== Entry point =====
def main():
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "VRC.Misa.Translator"
        )
    except Exception:
        pass

    root = tk.Tk()
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    app = VRChatTranslatorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
