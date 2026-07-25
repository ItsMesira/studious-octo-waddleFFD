#!/usr/bin/env python3
"""
FishFeeder Discord Bot for Raspberry Pi (3B / 3B+)
- L298N motor driver (ENA PWM, IN1/IN2 direction)
- TS (limit) switch support for mechanical/digital stop
- Optional INA219 battery monitoring (voltage/current/shunt)
- BackgroundScheduler for timed feeds
- Discord bot commands: feed, reverse, stop, status, schedule_*

The GPIO and I2C layout is identical on Raspberry Pi 3B and 3B+,
so this bot runs on both models. Pi 3B+ adds dual-band WiFi and
better thermal monitoring, which the bot can report via !pi_info.
"""

import os
import io
import json
import zipfile
import asyncio
import logging
import subprocess
import signal
import shutil
import ast
import tempfile
import sys
import time
from datetime import datetime, timezone, timedelta

try:
    import aiohttp
except ImportError:
    aiohttp = None

import RPi.GPIO as GPIO

# Optional INA219
try:
    from ina219 import INA219, DeviceRangeError
except Exception:
    INA219 = None
    DeviceRangeError = None

from apscheduler.schedulers.background import BackgroundScheduler

import discord
from discord.ext import commands

# ----------------- CONFIG -----------------
REPO_DIR = "/home/sira/fishfeeder"

def load_dotenv():
    env_path = os.path.join(REPO_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, val = line.split('=', 1)
                    val = val.strip().strip('"').strip("'")
                    os.environ[key.strip()] = val

load_dotenv()

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", 0))

# GPIO pins (BCM)
PIN_ENA = 18  # PWM
PIN_IN1 = 23  # forward
PIN_IN2 = 24  # reverse
TS_PIN = 25   # limit switch input (optional)

PWM_FREQ = 1000  # Hz

# Feed durations
FEED_DURATION_DEFAULT = 5   # seconds
FEED_DURATION_MAX = 30      # seconds cap

# Default battery thresholds (can be changed via !battery_config)
BATTERY_MIN_VOLTAGE = 6.5
BATTERY_MAX_CURRENT_MA = 1200
LOW_BATTERY_WARNING_THRESHOLD = 6.4  # Warn user below this voltage
BATTERY_FULL_VOLTAGE = None          # Voltage considered 100%
BATTERY_EMPTY_VOLTAGE = None         # Voltage considered 0%

# INA219 config
SHUNT_OHMS = 0.1
INA219_ADDR = 0x40
MAX_EXPECTED_AMPS = 0.5
INA219_ADDRESSES = [INA219_ADDR, 0x41, 0x44, 0x45]

# Persistence
SCHEDULE_FILE = os.path.join(REPO_DIR, "schedules.json")
STATE_FILE = os.path.join(REPO_DIR, "state.json")
AUTHORIZED_FILE = os.path.join(REPO_DIR, "authorized_users.json")
BATTERY_CONFIG_FILE = os.path.join(REPO_DIR, "battery_config.json")
WIFI_CONFIG_FILE = os.path.join(REPO_DIR, "wifi_config.json")
UI_CONFIG_FILE = os.path.join(REPO_DIR, "ui_config.json") # Stores persistent channel ID
SHARED_STATE_FILE = os.path.join(REPO_DIR, "shared_state.json") # For GUI live status
COMMAND_FILE = os.path.join(REPO_DIR, "command.json") # For web dashboard controls
DEV_PATCH_INSTALLER = os.path.join(REPO_DIR, "dev_patch_installer.py")
NGROK_CONFIG_FILE = os.path.join(REPO_DIR, "ngrok_config.json")
DEV_PATCH_VERSION_FILE = os.path.join(REPO_DIR, ".dev_patch_version")
AUTO_UPDATE_FILE = os.path.join(REPO_DIR, "auto_update.json")
BOT_VERSION = "2.4.2"
GIT_REMOTE = "origin"
GIT_BRANCH = "main"

# Failsafe: auto feed if no feed in this many hours
FAILSAFE_HOURS = 24

# -----------------------------------------
# Logging
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("fishfeeder")

# ----------------- Localization -----------------
CURRENT_LANG = "en"

TRANSLATIONS = {
    "en": {
        "bot_online": "🤖 **FishFeeder Bot Online**",
        "control_panel": "🎛️ **Control Panel**",
        "restarted": "🔄 Restarting bot service...",
        "manual_feed_title": "Manual Feed",
        "duration_label": "Duration (seconds)",
        "invalid_number": "❌ Invalid number.",
        "max_duration": "❌ Max duration is {max}s.",
        "motor_busy": "❌ Motor is busy/feeding.",
        "battery_unsafe": "❌ Battery unsafe: {msg}",
        "starting_feed": "🐟 Starting feed for {seconds}s...",
        "manual_feed_log": "👤 Manual feed by {user} ({seconds}s)",
        "sched_add_title": "Add Schedule",
        "hour_label": "Hour (0-23)",
        "minute_label": "Minute (0-59)",
        "invalid_time": "❌ Invalid time.",
        "sched_added": "✅ Added schedule: {h:02d}:{m:02d} for {d}s",
        "sched_log": "📅 Schedule added by {user}: {h:02d}:{m:02d}",
        "rev_title": "Reverse Motor",
        "reversing": "🔄 Reversing for {seconds}s...",
        "rev_log": "🔄 Reverse triggered by {user}",
        "bat_title": "Battery Config",
        "bat_updated": "✅ Battery config updated.",
        "bat_log": "⚙️ Battery config update by {user}",
        "auth_title": "Authorize User",
        "user_id_label": "User ID",
        "only_owner": "⛔ Owner only.",
        "auth_success": "✅ Authorized {uid}",
        "auth_log": "🔓 Authorized {uid} via UI",
        "menu_placeholder": "⚙️ Configuration & Tools...",
        "opt_add_sched": "Add Schedule",
        "opt_rem_sched": "Remove Schedule",
        "opt_force": "Force Feed",
        "opt_rev": "Reverse Motor",
        "opt_bat": "Battery Settings",
        "opt_auth": "Authorize User",
        "opt_update": "📥 Update via File",
        "opt_restart": "Restart Bot",
        "btn_feed": "Feed Now",
        "btn_status": "Status",
        "btn_stop": "Stop Motor",
        "btn_sched": "Schedules",
        "btn_wifi": "WiFi",
        "status_title": "**📊 System Status**",
        "st_bat": "🔋 Battery",
        "st_motor": "⚙️ Motor",
        "st_sw": "🛑 Switch",
        "st_last": "🕒 Last Feed",
        "st_feeding": "Feeding 🟢",
        "st_idle": "Idle ⚪",
        "st_pressed": "PRESSED 🔴",
        "st_open": "Open 🟢",
        "st_never": "Never",
        "lang_set": "✅ Language set to: {lang}",
        "lang_invalid": "❌ Invalid language. Options: en, th, zh",
        "cmd_lang_desc": "Change language (en, th, zh)",
        "sched_none": "No schedules set.",
        "sched_active": "**📅 Active Schedules**",
        "wifi_status": "📡 **WiFi Status**",
        "wifi_ssid": "SSID",
        "wifi_ip": "IP",
        "force_warning": "⚠️ **FORCE FEED WARNING**\nSafety checks will be bypassed.",
        "force_confirm": "Confirm Force Feed",
        "force_executing": "Starting FORCE FEED (Safety bypassed)!",
        "force_log": "⚠️ **FORCE FEED** executed by {user}",
        "owner_only": "⛔ Authorized users only.",
        "update_prompt": "📤 **Upload Update**: Please upload the new `fishfeeder_bot.py` file to this channel now. (timeout 60s)",
        "not_py_file": "❌ Not a .py file!",
        "downloading": "⬇️ Downloading {filename}...",
        "syntax_error": "❌ **SYNTAX ERROR** in uploaded file!\nLine {lineno}: {msg}",
        "update_success": "✅ Update successful! Restarting bot...",
        "update_log": "🔄 File update via GUI by {user}",
        "update_timeout": "⏱️ Update timed out.",
        "emg_stop": "🛑 **EMERGENCY STOP EXECUTED**",
        "emg_stop_log": "🛑 Emergency stop by {user}",
        "sched_rem_select": "Select schedule to remove...",
        "sched_rem_success": "🗑️ Removed schedule {id}",
        "sched_rem_log": "🗑️ Schedule removed by {user}",
        "nothing_selected": "Nothing selected",
        "no_schedules": "No schedules",
        "opt_lang": "Change Language",
        "choose_lang": "Select Language:",
        "ts_boot_warn": "⚠️ TS switch pressed on startup! Reversing to clear...",
        "ts_boot_clearing": "⚠️ TS switch pressed on startup - reversing motor to clear position",
        "ts_boot_fail": "⚠️ TS switch still pressed after 5s reverse - check mechanism!",
        "ts_boot_clear": "✅ TS switch cleared - ready to operate",
        "feed_complete": "Feed complete.",
        "reverse_complete": "Reverse complete.",
        "already_feeding": "Already feeding.",
        "feed_max_limit": "Max feed is {max}s.",
        "killswitch_warn": "🚨 **EMERGENCY KILLSWITCH**\n⚠️ This will:\n• Stop motor immediately\n• Halt all running tasks\n• Restart the bot service\n\n**Reply with `RESTART` to confirm (15s timeout)**",
        "killswitch_activated": "🚨 **KILLSWITCH ACTIVATED**",
        "killswitch_log": "🚨 KILLSWITCH activated by {user} - Stopping all operations and restarting",
        "killswitch_cancel": "❌ Killswitch cancelled - timeout",
        "bat_mon_start_inf": "📊 Starting **infinite** battery monitor (interval: {int}s) in log channel...\n💡 Reply with `stop` to end monitoring.",
        "bat_mon_start": "📊 Starting battery monitor for {dur}s (interval: {int}s) in log channel...",
        "bat_mon_header": "🔋 **Battery Monitor Starting...**",
        "bat_mon_stop": "⏹️ Stopping battery monitor...",
        "bat_mon_title": "🔋 **Real-Time Battery Monitor**",
        "bat_mon_update": "Update: **#{i}** (infinite mode - reply 'stop' to end)",
        "bat_mon_elapsed": "Elapsed: **{s}s**",
        "bat_mon_time_rem": "Time remaining: **{s}s**",
        "bat_mon_stopped": "⏹️ **Monitoring stopped** (Total updates: {i})",
        "bat_mon_complete": "✅ **Monitoring complete**",
        "bat_mon_finished": "✅ Battery monitoring finished.",
        "bat_mon_error": "❌ Monitor error: {e}",
        "wifi_adding": "📡 **Adding WiFi Network:** `{ssid}`",
        "wifi_backup": "1️⃣ Backing up current network...",
        "wifi_backed_up": "✅ Current network `{ssid}` backed up",
        "wifi_no_backup": "⚠️ No current network to backup",
        "wifi_connecting": "2️⃣ Connecting to new network...",
        "wifi_testing": "3️⃣ Testing connection...",
        "wifi_connected": "✅ **Successfully connected to `{ssid}`!**",
        "wifi_conn_log": "📡 {user} successfully connected to WiFi network: {ssid}",
        "wifi_test_fail": "❌ Connection test failed!",
        "wifi_restoring": "4️⃣ Attempting to restore previous network...",
        "wifi_restored": "✅ Restored connection to `{ssid}`",
        "wifi_restored_log": "⚠️ {user} failed to connect to {ssid}, restored to {last}",
        "wifi_restored_fail": "❌ Failed to restore previous network!",
        "wifi_restored_fail_log": "🚨 {user} WiFi connection failed - no network available!",
        "wifi_connect_fail": "❌ Failed to connect to new network!",
        "check_time": "Current time (GMT+7): {time}",
        "check_ip": "📡 **IP Addresses:**\n```{ips}```",
        "no_ip": "⚠️ No IP addresses found",
        "bat_not_available": "INA219 not available. Ensure I2C is enabled.",
        "bat_read_failed": "INA219 read failed (I/O).",
        "bat_msg": "Voltage: {v:.2f}V{p}, Current: {c}, Shunt: {s:.1f}mV",
        "bat_current_na": "N/A",
        "sched_cmd_added": "Scheduled daily feed at {h:02d}:{m:02d} for {d}s",
        "sched_list_title": "Schedules:",
        "sched_list_empty": "No schedules.",
        "sched_usage_rem": "Usage: !schedule_remove <hh> <mm> OR !schedule_remove all",
        "sched_invalid_fmt": "Invalid format. Usage: !schedule_remove <hh> <mm> OR !schedule_remove all",
        "sched_cleared_all": "All schedules cleared.",
        "wifi_status_conn": "📡 **WiFi Status:** Connected to `{ssid}` ✅",
        "wifi_status_no_inet": "📡 **WiFi Status:** Connected to `{ssid}` but no internet ❌",
        "wifi_status_disconn": "📡 **WiFi Status:** Not connected to any network ❌",
        "bat_conf_header": "🔋 **Battery Configuration**",
        "bat_conf_pct_on": "✅ **Percentage display is ENABLED**",
        "bat_conf_pct_off": "⚠️ **Percentage display is DISABLED**\n💡 **To enable:** Set `full` and `empty` voltages.",
        "bat_conf_usage": "❌ Please provide a value. Example: `!battery_config full 8.4`",
        "bat_conf_invalid_v": "❌ Invalid voltage. Must be between 0-15V",
        "bat_conf_updated": "✅ {setting} updated: {old} → {new}",
        "bat_conf_unknown": "❌ Unknown setting `{setting}`. Valid: `min`, `warning`, `max_current`, `full`, `empty`",
        "update_pulling": "Pulling latest and restarting...",
        "update_git_out": "Git pull output:\n```\n{out}\n```",
        "update_validating": "🔍 Validating code syntax...",
        "update_rollback": "⚠️ Update cancelled - rolled back to previous version",
        "update_failed": "Update failed: {e}",
        "auth_owner_already": "⚠️ Owner is already authorized by default.",
        "auth_already": "ℹ️ User ID `{uid}` is already authorized.",
        "auth_cmd_success": "✅ User ID `{uid}` has been authorized.",
        "auth_cmd_log": "🔓 {user} authorized user ID: {uid}",
        "auth_cant_deauth_owner": "⚠️ Cannot deauthorize the owner.",
        "auth_not_in_list": "ℹ️ User ID `{uid}` is not in the authorized list.",
        "auth_deauth_success": "✅ User ID `{uid}` has been removed from authorized list.",
        "auth_deauth_log": "🔒 {user} deauthorized user ID: {uid}",
        "auth_list_title": "📋 **Authorized Users:**",
        "auth_list_empty": "📋 **Authorized Users:** None\n\n_Only the owner has access._",
        "auth_list_unknown": "Unknown User",
        "st_pi": "🖥️ Pi",
        "st_cpu_temp": "🌡️ CPU Temp",
        "pi_info_title": "🖥️ **Raspberry Pi Info**",
        "pi_info_model": "**Model:**",
        "pi_info_temp": "**CPU Temp:**",
        "pi_info_throttled": "**Throttled:**",
        "pi_info_throttle_flags": "**Throttle Flags:**",
        "pi_info_none": "None",
        "pi_info_na": "N/A (vcgencmd not available)",
        "download_title": "📥 **Download Files**",
        "download_usage": "Usage: `!download <type>`\n\n**Available types:**\n• `bot` - Current bot script (fishfeeder_bot.py)\n• `backup` - Last backup file\n• `config` - All config files (zip)\n• `all` - Bot + backup + all configs (zip)\n• `schedules` - schedules.json\n• `state` - state.json\n• `authorized` - authorized_users.json\n• `battery` - battery_config.json\n• `wifi` - wifi_config.json\n• `ui` - ui_config.json\n• `devpatch` - Dev patch installer",
        "download_bot": "📥 Downloading current bot script...",
        "download_backup": "📥 Downloading backup file...",
        "download_config": "📥 Downloading config files (zip)...",
        "download_all": "📥 Downloading all files (zip)...",
        "download_file_sent": "✅ File sent: `{filename}`",
        "download_not_found": "❌ File not found: `{filename}`",
        "download_no_backup": "❌ No backup file exists. Run `!update_file` to create one.",
        "download_log": "📥 {user} downloaded: {what}",
        "download_error": "❌ Download error: {e}",
        "opt_download": "📥 Download Files",
    },
    "th": {
        "bot_online": "🤖 **บอทให้อาหารปลา ออนไลน์**",
        "control_panel": "🎛️ **แผงควบคุม**",
        "restarted": "🔄 กำลังรีสตาร์ทบอท...",
        "manual_feed_title": "ให้อาหารทันที",
        "duration_label": "ระยะเวลา (วินาที)",
        "invalid_number": "❌ ตัวเลขไม่ถูกต้อง",
        "max_duration": "❌ นานเกินไป สูงสุด {max} วินาที",
        "motor_busy": "❌ มอเตอร์ทำงานอยู่",
        "battery_unsafe": "❌ แบตเตอรี่ไม่ปลอดภัย: {msg}",
        "starting_feed": "🐟 กำลังให้อาหาร {seconds} วินาที...",
        "manual_feed_log": "👤 สั่งให้อาหารโดย {user} ({seconds} วินาที)",
        "sched_add_title": "เพิ่มตารางเวลา",
        "hour_label": "ชั่วโมง (0-23)",
        "minute_label": "นาที (0-59)",
        "invalid_time": "❌ เวลาไม่ถูกต้อง",
        "sched_added": "✅ เพิ่มตาราง: {h:02d}:{m:02d} นาน {d} วินาที",
        "sched_log": "📅 เพิ่มตารางโดย {user}: {h:02d}:{m:02d}",
        "rev_title": "หมุนย้อนกลับ",
        "reversing": "🔄 กำลังหมุนกลับ {seconds} วินาที...",
        "rev_log": "🔄 สั่งหมุนกลับโดย {user}",
        "bat_title": "ตั้งค่าแบตเตอรี่",
        "bat_updated": "✅ อัปเดตค่าแบตเตอรี่แล้ว",
        "bat_log": "⚙️ ตั้งค่าแบตเตอรี่โดย {user}",
        "auth_title": "อนุญาตผู้ใช้",
        "user_id_label": "ไอดีผู้ใช้ (User ID)",
        "only_owner": "⛔ เฉพาะเจ้าของเท่านั้น",
        "auth_success": "✅ อนุญาต {uid} แล้ว",
        "auth_log": "🔓 อนุญาต {uid} ผ่าน UI",
        "menu_placeholder": "⚙️ ตั้งค่าและเครื่องมือ...",
        "opt_add_sched": "เพิ่มตารางเวลา",
        "opt_rem_sched": "ลบตารางเวลา",
        "opt_force": "บังคับให้อาหาร (Force Feed)",
        "opt_rev": "หมุนมอเตอร์กลับ",
        "opt_bat": "ตั้งค่าแบตเตอรี่",
        "opt_auth": "อนุญาตผู้ใช้",
        "opt_update": "📥 อัปเดตผ่านไฟล์",
        "opt_restart": "รีสตาร์ทบอท",
        "btn_feed": "ให้อาหาร",
        "btn_status": "สถานะ",
        "btn_stop": "หยุดมอเตอร์",
        "btn_sched": "ดูตาราง",
        "btn_wifi": "WiFi",
        "status_title": "**📊 สถานะระบบ**",
        "st_bat": "🔋 แบตเตอรี่",
        "st_motor": "⚙️ มอเตอร์",
        "st_sw": "🛑 สวิตช์",
        "st_last": "🕒 ให้อาหารล่าสุด",
        "st_feeding": "กำลังทำงาน 🟢",
        "st_idle": "ว่าง ⚪",
        "st_pressed": "ถูกกด 🔴",
        "st_open": "ปกติ 🟢",
        "st_never": "ไม่เคย",
        "lang_set": "✅ เปลี่ยนภาษาเป็น: {lang}",
        "lang_invalid": "❌ ภาษาไม่ถูกต้อง เลือก: en, th, zh",
        "cmd_lang_desc": "เปลี่ยนภาษา (en, th, zh)",
        "sched_none": "ไม่มีตารางเวลา",
        "sched_active": "**📅 ตารางเวลาที่ตั้งไว้**",
        "wifi_status": "📡 **สถานะ WiFi**",
        "wifi_ssid": "ชื่อ WiFi",
        "wifi_ip": "ไอพี",
        "force_warning": "⚠️ **คำเตือน: บังคับให้อาหาร**\nระบบความปลอดภัยจะถูกปิดใช้งาน",
        "force_confirm": "ยืนยันการบังคับ",
        "force_executing": "กำลังบังคับให้อาหาร (ข้ามความปลอดภัย)!",
        "force_log": "⚠️ **บังคับให้อาหาร** โดย {user}",
        "owner_only": "⛔ เฉพาะผู้ได้รับอนุญาต",
        "update_prompt": "📥 **อัปเดตระบบ**: โปรดอัปโหลดไฟล์ `fishfeeder_bot.py` ใหม่ที่ช่องนี้ (หมดเวลา 60s)",
        "not_py_file": "❌ ไม่ใช่ไฟล์ .py!",
        "downloading": "⬇️ กำลังดาวน์โหลด {filename}...",
        "syntax_error": "❌ **โค้ดผิดพลาด (Syntax Error)**!\nบรรทัด {lineno}: {msg}",
        "update_success": "✅ อัปเดตสำเร็จ! กำลังรีสตาร์ท...",
        "update_log": "🔄 อัปเดตไฟล์ผ่าน GUI โดย {user}",
        "update_timeout": "⏱️ หมดเวลาการอัปเดต",
        "emg_stop": "🛑 **หยุดฉุกเฉินทำงาน**",
        "emg_stop_log": "🛑 สั่งหยุดฉุกเฉินโดย {user}",
        "sched_rem_select": "เลือกตารางที่ต้องการลบ...",
        "sched_rem_success": "🗑️ ลบตาราง {id} แล้ว",
        "sched_rem_log": "🗑️ ลบตารางโดย {user}",
        "nothing_selected": "ไม่ได้เลือก",
        "no_schedules": "ไม่มีตาราง",
        "opt_lang": "เปลี่ยนภาษา",
        "choose_lang": "เลือกภาษา:",
        "ts_boot_warn": "⚠️ สวิตช์ TS ถูกกดตอนเปิดเครื่อง! กำลังหมุนกลับเพื่อแก้ไข...",
        "ts_boot_clearing": "⚠️ สวิตช์ TS ถูกกด - กำลังหมุนกลับ",
        "ts_boot_fail": "⚠️ สวิตช์ TS ยังคงถูกกดหลังหมุนกลับ 5 วินาที - ตรวจสอบกลไก!",
        "ts_boot_clear": "✅ สวิตช์ TS ปกติ - พร้อมใช้งาน",
        "feed_complete": "ให้อาหารเสร็จสิ้น",
        "reverse_complete": "หมุนกลับเสร็จสิ้น",
        "already_feeding": "กำลังให้อาหารอยู่",
        "feed_max_limit": "ให้อาหารได้สูงสุด {max} วินาที",
        "killswitch_warn": "🚨 **หยุดฉุกเฉิน (Killswitch)**\n⚠️ คำสั่งนี้จะ:\n• หยุดมอเตอร์ทันที\n• หยุดการทำงานทั้งหมด\n• รีสตาร์ทบอท\n\n**ตอบกลับด้วย `RESTART` เพื่อยืนยัน (ภายใน 15s)**",
        "killswitch_activated": "🚨 **เปิดใช้งาน Killswitch**",
        "killswitch_log": "🚨 Killswitch สั่งการโดย {user} - กำลังรีสตาร์ทระบบ",
        "killswitch_cancel": "❌ ยกเลิก Killswitch - หมดเวลา",
        "bat_mon_start_inf": "📊 เริ่มตรวจสอบแบตเตอรี่ **แบบต่อเนื่อง** (ทุก {int} วิ) ...\n💡 ตอบกลับ `stop` เพื่อหยุด",
        "bat_mon_start": "📊 เริ่มตรวจสอบแบตเตอรี่ {dur} วินาที (ทุก {int} วิ)...",
        "bat_mon_header": "🔋 **เริ่มตรวจสอบแบตเตอรี่...**",
        "bat_mon_stop": "⏹️ กำลังหยุดการตรวจสอบ...",
        "bat_mon_title": "🔋 **สถานะแบตเตอรี่ Real-Time**",
        "bat_mon_update": "อัปเดต: **#{i}** (แบบต่อเนื่อง - พิมพ์ 'stop' เพื่อหยุด)",
        "bat_mon_elapsed": "ผ่านไป: **{s} วินาที**",
        "bat_mon_time_rem": "เหลือเวลา: **{s} วินาที**",
        "bat_mon_stopped": "⏹️ **หยุดการตรวจสอบ** (อัปเดต {i} ครั้ง)",
        "bat_mon_complete": "✅ **ตรวจสอบเสร็จสิ้น**",
        "bat_mon_finished": "✅ จบการทำงาน",
        "bat_mon_error": "❌ เกิดข้อผิดพลาด: {e}",
        "wifi_adding": "📡 **กำลังเพิ่ม WiFi:** `{ssid}`",
        "wifi_backup": "1️⃣ สำรองข้อมูล WiFi ปัจจุบัน...",
        "wifi_backed_up": "✅ สำรองข้อมูล `{ssid}` แล้ว",
        "wifi_no_backup": "⚠️ ไม่มี WiFi เดิมให้สำรอง",
        "wifi_connecting": "2️⃣ กำลังเชื่อมต่อ WiFi ใหม่...",
        "wifi_testing": "3️⃣ ทดสอบการเชื่อมต่อ...",
        "wifi_connected": "✅ **เชื่อมต่อ `{ssid}` สำเร็จ!**",
        "wifi_conn_log": "📡 {user} เชื่อมต่อ WiFi สำเร็จ: {ssid}",
        "wifi_test_fail": "❌ ทดสอบการเชื่อมต่อล้มเหลว!",
        "wifi_restoring": "4️⃣ กำลังกู้คืน WiFi เดิม...",
        "wifi_restored": "✅ กู้คืนการเชื่อมต่อ `{ssid}` แล้ว",
        "wifi_restored_log": "⚠️ {user} เชื่อมต่อ {ssid} ไม่สำเร็จ, กู้คืนเป็น {last}",
        "wifi_restored_fail": "❌ กู้คืน WiFi เดิมไม่สำเร็จ!",
        "wifi_restored_fail_log": "🚨 {user} เชื่อมต่อ WiFi ล้มเหลว - ไม่มีเครือข่าย!",
        "wifi_connect_fail": "❌ เชื่อมต่อ WiFi ใหม่ไม่สำเร็จ!",
        "check_time": "เวลาปัจจุบัน (GMT+7): {time}",
        "check_ip": "📡 **IP Address:**\n```{ips}```",
        "no_ip": "⚠️ ไม่พบ IP Address",
        "bat_not_available": "ไม่พบ INA219 โปรดเปิด I2C",
        "bat_read_failed": "อ่านค่า INA219 ล้มเหลว",
        "bat_msg": "แรงดัน: {v:.2f}V{p}, กระแส: {c}, Shunt: {s:.1f}mV",
        "bat_current_na": "N/A",
        "sched_cmd_added": "ตั้งเวลาให้อาหารทุกวันที่ {h:02d}:{m:02d} นาน {d} วินาที",
        "sched_list_title": "รายการตารางเวลา:",
        "sched_list_empty": "ไม่มีตารางเวลา",
        "sched_usage_rem": "วิธีใช้: !schedule_remove <hh> <mm> หรือ !schedule_remove all",
        "sched_invalid_fmt": "รูปแบบผิด วิธีใช้: !schedule_remove <hh> <mm>",
        "sched_cleared_all": "ลบตารางเวลาทั้งหมดแล้ว",
        "wifi_status_conn": "📡 **WiFi:** เชื่อมต่อ `{ssid}` แล้ว ✅",
        "wifi_status_no_inet": "📡 **WiFi:** เชื่อมต่อ `{ssid}` แต่ไม่มีเน็ต ❌",
        "wifi_status_disconn": "📡 **WiFi:** ไม่ได้เชื่อมต่อ ❌",
        "bat_conf_header": "🔋 **การตั้งค่าแบตเตอรี่**",
        "bat_conf_pct_on": "✅ **การแสดงเปอร์เซ็นต์เปิดใช้งานอยู่**",
        "bat_conf_pct_off": "⚠️ **การแสดงเปอร์เซ็นต์ปิดอยู่**\n💡 **วิธีเปิด:** ตั้งค่าแรงดัน `full` และ `empty`",
        "bat_conf_usage": "❌ โปรดระบุค่า ตัวอย่าง: `!battery_config full 8.4`",
        "bat_conf_invalid_v": "❌ แรงดันไฟไม่ถูกต้อง (0-15V)",
        "bat_conf_updated": "✅ {setting} อัปเดต: {old} → {new}",
        "bat_conf_unknown": "❌ ไม่รู้จักการตั้งค่า `{setting}`",
        "update_pulling": "กำลังดึงข้อมูลล่าสุดและรีสตาร์ท...",
        "update_git_out": "Git output:\n```\n{out}\n```",
        "update_validating": "🔍 ตรวจสอบความถูกต้องของโค้ด...",
        "update_rollback": "⚠️ อัปเดตถูกยกเลิก - ย้อนกลับเป็นเวอร์ชันก่อนหน้า",
        "update_failed": "อัปเดตล้มเหลว: {e}",
        "auth_owner_already": "⚠️ เจ้าของได้รับอนุญาตอยู่แล้ว",
        "auth_already": "ℹ️ ผู้ใช้ `{uid}` ได้รับอนุญาตอยู่แล้ว",
        "auth_cmd_success": "✅ อนุญาตผู้ใช้ `{uid}` แล้ว",
        "auth_cmd_log": "🔓 {user} อนุญาตผู้ใช้: {uid}",
        "auth_cant_deauth_owner": "⚠️ ไม่สามารถถอนสิทธิ์เจ้าของได้",
        "auth_not_in_list": "ℹ️ ผู้ใช้ `{uid}` ไม่อยู่ในรายการ",
        "auth_deauth_success": "✅ ถอนสิทธิ์ผู้ใช้ `{uid}` แล้ว",
        "auth_deauth_log": "🔒 {user} ถอนสิทธิ์ผู้ใช้: {uid}",
        "auth_list_title": "📋 **รายชื่อผู้ได้รับอนุญาต:**",
        "auth_list_empty": "📋 **รายชื่อผู้ได้รับอนุญาต:** ไม่มี\n\n_เฉพาะเจ้าของเท่านั้นที่ดูได้_",
        "auth_list_unknown": "ผู้ใช้ไม่ทราบชื่อ",
        "st_pi": "🖥️ Pi",
        "st_cpu_temp": "🌡️ อุณหภูมิ CPU",
        "pi_info_title": "🖥️ **ข้อมูล Raspberry Pi**",
        "pi_info_model": "**รุ่น:**",
        "pi_info_temp": "**อุณหภูมิ CPU:**",
        "pi_info_throttled": "**Throttled:**",
        "pi_info_throttle_flags": "**สถานะ Throttle:**",
        "pi_info_none": "ไม่มี",
        "pi_info_na": "N/A (vcgencmd ไม่พร้อมใช้งาน)",
        "download_title": "📥 **ดาวน์โหลดไฟล์**",
        "download_usage": "วิธีใช้: `!download <ประเภท>`\n\n**ประเภทที่มี:**\n• `bot` - โค้ดบอทปัจจุบัน (fishfeeder_bot.py)\n• `backup` - ไฟล์สำรองล่าสุด\n• `config` - ไฟล์ตั้งค่าทั้งหมด (zip)\n• `all` - บอท + สำรอง + ตั้งค่าทั้งหมด (zip)\n• `schedules` - schedules.json\n• `state` - state.json\n• `authorized` - authorized_users.json\n• `battery` - battery_config.json\n• `wifi` - wifi_config.json\n• `ui` - ui_config.json\n• `devpatch` - ไฟล์ติดตั้ง dev patch",
        "download_bot": "📥 กำลังดาวน์โหลดโค้ดบอท...",
        "download_backup": "📥 กำลังดาวน์โหลดไฟล์สำรอง...",
        "download_config": "📥 กำลังดาวน์โหลดไฟล์ตั้งค่า (zip)...",
        "download_all": "📥 กำลังดาวน์โหลดไฟล์ทั้งหมด (zip)...",
        "download_file_sent": "✅ ส่งไฟล์แล้ว: `{filename}`",
        "download_not_found": "❌ ไม่พบไฟล์: `{filename}`",
        "download_no_backup": "❌ ไม่มีไฟล์สำรอง ใช้ `!update_file` เพื่อสร้าง",
        "download_log": "📥 {user} ดาวน์โหลด: {what}",
        "download_error": "❌ ข้อผิดพลาด: {e}",
        "opt_download": "📥 ดาวน์โหลดไฟล์",
    },
    "zh": {
        "bot_online": "🤖 **喂鱼机器人在线**",
        "control_panel": "🎛️ **控制面板**",
        "restarted": "🔄 正在重启机器人服务...",
        "manual_feed_title": "手动喂食",
        "duration_label": "时长 (秒)",
        "invalid_number": "❌ 无效数字",
        "max_duration": "❌ 时长过长，最大 {max} 秒",
        "motor_busy": "❌ 电机忙碌/正在喂食",
        "battery_unsafe": "❌ 电池不安全: {msg}",
        "starting_feed": "🐟 开始喂食 {seconds} 秒...",
        "manual_feed_log": "👤 手动喂食 - 用户 {user} ({seconds} 秒)",
        "sched_add_title": "添加日程",
        "hour_label": "小时 (0-23)",
        "minute_label": "分钟 (0-59)",
        "invalid_time": "❌ 时间无效",
        "sched_added": "✅ 已添加日程: {h:02d}:{m:02d} 持续 {d} 秒",
        "sched_log": "📅 添加日程 - 用户 {user}: {h:02d}:{m:02d}",
        "rev_title": "反转电机",
        "reversing": "🔄 正在反转 {seconds} 秒...",
        "rev_log": "🔄 反转电机 - 用户 {user}",
        "bat_title": "电池设置",
        "bat_updated": "✅ 电池配置已更新",
        "bat_log": "⚙️ 电池配置更新 - 用户 {user}",
        "auth_title": "授权用户",
        "user_id_label": "用户 ID",
        "only_owner": "⛔ 仅限所有者",
        "auth_success": "✅ 已授权 {uid}",
        "auth_log": "🔓 通过 UI 授权 {uid}",
        "menu_placeholder": "⚙️ 配置与工具...",
        "opt_add_sched": "添加日程",
        "opt_rem_sched": "删除日程",
        "opt_force": "强制喂食",
        "opt_rev": "反转电机",
        "opt_bat": "电池设置",
        "opt_auth": "授权用户",
        "opt_update": "📥 通过文件更新",
        "opt_restart": "重启机器人",
        "btn_feed": "立即喂食",
        "btn_status": "状态",
        "btn_stop": "停止电机",
        "btn_sched": "日程表",
        "btn_wifi": "WiFi",
        "status_title": "**📊 系统状态**",
        "st_bat": "🔋 电池",
        "st_motor": "⚙️ 电机",
        "st_sw": "🛑 开关",
        "st_last": "🕒 上次喂食",
        "st_feeding": "喂食中 🟢",
        "st_idle": "空闲 ⚪",
        "st_pressed": "按下 🔴",
        "st_open": "断开 🟢",
        "st_never": "从未",
        "lang_set": "✅ 语言已设置为: {lang}",
        "lang_invalid": "❌ 无效语言。选项: en, th, zh",
        "cmd_lang_desc": "更改语言 (en, th, zh)",
        "sched_none": "未设置日程",
        "sched_active": "**📅 当前日程**",
        "wifi_status": "📡 **WiFi 状态**",
        "wifi_ssid": "SSID",
        "wifi_ip": "IP",
        "force_warning": "⚠️ **强制喂食警告**\n安全检查将被绕过。",
        "force_confirm": "确认强制喂食",
        "force_executing": "开始强制喂食 (绕过安全检查)!",
        "force_log": "⚠️ **强制喂食** 执行者 {user}",
        "owner_only": "⛔ 仅限授权用户",
        "update_prompt": "📥 **上传更新**: 请现在上传新的 `fishfeeder_bot.py` 文件到此频道。(超时 60s)",
        "not_py_file": "❌ 不是 .py 文件!",
        "downloading": "⬇️ 正在下载 {filename}...",
        "syntax_error": "❌ **语法错误**!\n行 {lineno}: {msg}",
        "update_success": "✅ 更新成功! 正在重启机器人...",
        "update_log": "🔄 通过 GUI 更新文件 - 用户 {user}",
        "update_timeout": "⏱️ 更新超时",
        "emg_stop": "🛑 **执行紧急停止**",
        "emg_stop_log": "🛑 紧急停止 - 用户 {user}",
        "sched_rem_select": "选择要删除的日程...",
        "sched_rem_success": "🗑️ 已删除日程 {id}",
        "sched_rem_log": "🗑️ 删除日程 - 用户 {user}",
        "nothing_selected": "未选择",
        "no_schedules": "无日程",
        "opt_lang": "更改语言",
        "choose_lang": "选择语言:",
        "ts_boot_warn": "⚠️ 启动时检测到 TS 开关按下！正在反转以清除...",
        "ts_boot_clearing": "⚠️ TS 开关按下 - 正在反转电机",
        "ts_boot_fail": "⚠️ 反转 5 秒后 TS 开关仍按下 - 请检查机械结构！",
        "ts_boot_clear": "✅ TS 开关已清除 - 准备就绪",
        "feed_complete": "喂食完成。",
        "reverse_complete": "反转完成。",
        "already_feeding": "正在喂食中。",
        "feed_max_limit": "最大喂食时长为 {max} 秒。",
        "killswitch_warn": "🚨 **紧急终止 (Killswitch)**\n⚠️ 此操作将：\n• 立即停止电机\n• 停止所有任务\n• 重启机器人服务\n\n**回复 `RESTART` 确认 (15秒超时)**",
        "killswitch_activated": "🚨 **KILLSWITCH 已激活**",
        "killswitch_log": "🚨 用户 {user} 激活了 KILLSWITCH - 停止所有操作并重启",
        "killswitch_cancel": "❌ Killswitch 已取消 - 超时",
        "bat_mon_start_inf": "📊 开始 **无限** 电池监控 (间隔: {int}秒) ...\n💡 回复 `stop` 停止监控。",
        "bat_mon_start": "📊 开始电池监控 {dur} 秒 (间隔: {int}秒)...",
        "bat_mon_header": "🔋 **电池监控启动...**",
        "bat_mon_stop": "⏹️ 停止电池监控...",
        "bat_mon_title": "🔋 **实时电池监控**",
        "bat_mon_update": "更新: **#{i}** (无限模式 - 回复 'stop' 停止)",
        "bat_mon_elapsed": "已用时间: **{s}秒**",
        "bat_mon_time_rem": "剩余时间: **{s}秒**",
        "bat_mon_stopped": "⏹️ **监控停止** (更新次数: {i})",
        "bat_mon_complete": "✅ **监控完成**",
        "bat_mon_finished": "✅ 电池监控结束。",
        "bat_mon_error": "❌ 监控错误: {e}",
        "wifi_adding": "📡 **正在添加 WiFi:** `{ssid}`",
        "wifi_backup": "1️⃣ 备份当前网络...",
        "wifi_backed_up": "✅ 当前网络 `{ssid}` 已备份",
        "wifi_no_backup": "⚠️ 无当前网络可备份",
        "wifi_connecting": "2️⃣ 正在连接新网络...",
        "wifi_testing": "3️⃣ 测试连接...",
        "wifi_connected": "✅ **成功连接到 `{ssid}`!**",
        "wifi_conn_log": "📡 {user} 成功连接到 WiFi: {ssid}",
        "wifi_test_fail": "❌ 连接测试失败!",
        "wifi_restoring": "4️⃣ 尝试恢复之前的网络...",
        "wifi_restored": "✅ 已恢复连接到 `{ssid}`",
        "wifi_restored_log": "⚠️ {user} 连接 {ssid} 失败，已恢复到 {last}",
        "wifi_restored_fail": "❌ 恢复之前的网络失败!",
        "wifi_restored_fail_log": "🚨 {user} WiFi 连接失败 - 无可用网络!",
        "wifi_connect_fail": "❌ 连接新网络失败!",
        "check_time": "当前时间 (GMT+7): {time}",
        "check_ip": "📡 **IP 地址:**\n```{ips}```",
        "no_ip": "⚠️ 未找到 IP 地址",
        "bat_not_available": "INA219 不可用。请确保已启用 I2C。",
        "bat_read_failed": "INA219 读取失败 (I/O)。",
        "bat_msg": "电压: {v:.2f}V{p}, 电流: {c}, 分流: {s:.1f}mV",
        "bat_current_na": "N/A",
        "sched_cmd_added": "已将每日喂食安排在 {h:02d}:{m:02d} 持续 {d} 秒",
        "sched_list_title": "日程表:",
        "sched_list_empty": "无日程。",
        "sched_usage_rem": "用法: !schedule_remove <hh> <mm> 或 !schedule_remove all",
        "sched_invalid_fmt": "格式无效。用法: !schedule_remove <hh> <mm>",
        "sched_cleared_all": "已清除所有日程。",
        "wifi_status_conn": "📡 **WiFi状态:** 已连接 `{ssid}` ✅",
        "wifi_status_no_inet": "📡 **WiFi状态:** 已连接 `{ssid}` 但无网络 ❌",
        "wifi_status_disconn": "📡 **WiFi状态:** 未连接任何网络 ❌",
        "bat_conf_header": "🔋 **电池配置**",
        "bat_conf_pct_on": "✅ **百分比显示已启用**",
        "bat_conf_pct_off": "⚠️ **百分比显示已禁用**\n💡 **启用:** 设置 `full` 和 `empty` 电压。",
        "bat_conf_usage": "❌ 请提供一个值。例如: `!battery_config full 8.4`",
        "bat_conf_invalid_v": "❌ 无效电压。必须在 0-15V 之间",
        "bat_conf_updated": "✅ {setting} 已更新: {old} → {new}",
        "bat_conf_unknown": "❌ 未知设置 `{setting}`",
        "update_pulling": "正在拉取最新代码并重启...",
        "update_git_out": "Git 输出:\n```\n{out}\n```",
        "update_validating": "🔍 验证代码语法...",
        "update_rollback": "⚠️ 更新取消 - 已回滚到先前版本",
        "update_failed": "更新失败: {e}",
        "auth_owner_already": "⚠️ 所有者默认已授权。",
        "auth_already": "ℹ️ 用户 `{uid}` 已授权。",
        "auth_cmd_success": "✅ 用户 `{uid}` 已授权。",
        "auth_cmd_log": "🔓 {user} 授权了用户: {uid}",
        "auth_cant_deauth_owner": "⚠️ 无法取消授权所有者。",
        "auth_not_in_list": "ℹ️ 用户 `{uid}` 不在授权列表中。",
        "auth_deauth_success": "✅ 用户 `{uid}` 已从授权列表中移除。",
        "auth_deauth_log": "🔒 {user} 取消授权用户: {uid}",
        "auth_list_title": "📋 **授权用户:**",
        "auth_list_empty": "📋 **授权用户:** 无\n\n_仅限所有者可见。_",
        "auth_list_unknown": "未知用户",
        "st_pi": "🖥️ Pi",
        "st_cpu_temp": "🌡️ CPU 温度",
        "pi_info_title": "🖥️ **Raspberry Pi 信息**",
        "pi_info_model": "**型号:**",
        "pi_info_temp": "**CPU 温度:**",
        "pi_info_throttled": "**Throttled:**",
        "pi_info_throttle_flags": "**Throttle 标志:**",
        "pi_info_none": "无",
        "pi_info_na": "N/A (vcgencmd 不可用)",
        "download_title": "📥 **下载文件**",
        "download_usage": "用法: `!download <类型>`\n\n**可用类型:**\n• `bot` - 当前机器人脚本 (fishfeeder_bot.py)\n• `backup` - 上次备份文件\n• `config` - 所有配置文件 (zip)\n• `all` - 机器人 + 备份 + 所有配置 (zip)\n• `schedules` - schedules.json\n• `state` - state.json\n• `authorized` - authorized_users.json\n• `battery` - battery_config.json\n• `wifi` - wifi_config.json\n• `ui` - ui_config.json\n• `devpatch` - 开发者补丁安装程序",
        "download_bot": "📥 正在下载当前机器人脚本...",
        "download_backup": "📥 正在下载备份文件...",
        "download_config": "📥 正在下载配置文件 (zip)...",
        "download_all": "📥 正在下载所有文件 (zip)...",
        "download_file_sent": "✅ 已发送文件: `{filename}`",
        "download_not_found": "❌ 文件未找到: `{filename}`",
        "download_no_backup": "❌ 没有备份文件。运行 `!update_file` 来创建。",
        "download_log": "📥 {user} 下载了: {what}",
        "download_error": "❌ 下载错误: {e}",
        "opt_download": "📥 下载文件",
    }
}

def t(key, **kwargs):
    """Get translated string."""
    lang_dict = TRANSLATIONS.get(CURRENT_LANG, TRANSLATIONS["en"])
    template = lang_dict.get(key, TRANSLATIONS["en"].get(key, key))
    try:
        return template.format(**kwargs)
    except Exception:
        return template

def load_language_config():
    global CURRENT_LANG
    try:
        config = load_ui_config()
        CURRENT_LANG = config.get("language", "en")
        if CURRENT_LANG not in TRANSLATIONS:
            CURRENT_LANG = "en"
        logger.info(f"Loaded language: {CURRENT_LANG}")
    except Exception as e:
        logger.warning(f"Error loading language: {e}")

def save_language_config(lang):
    global CURRENT_LANG
    if lang not in TRANSLATIONS:
        return False
    CURRENT_LANG = lang
    try:
        config = load_ui_config()
        config["language"] = lang
        save_ui_config(config)
        return True
    except Exception as e:
        logger.warning(f"Error saving language: {e}")
        return False


def get_pi_model():
    """Detect the Raspberry Pi model from device-tree or cpuinfo."""
    try:
        # Preferred source on Raspberry Pi OS
        model_path = "/proc/device-tree/model"
        if os.path.exists(model_path):
            with open(model_path, "r", encoding="utf-8") as f:
                return f.read().strip().strip("\x00")
    except Exception:
        pass

    try:
        # Fallback for older images / other distros
        with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("Model"):
                    return line.split(":", 1)[1].strip()
                if line.startswith("Hardware"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass

    return "Unknown Raspberry Pi"


def get_cpu_temp():
    """Read CPU temperature in Celsius from thermal zone."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r", encoding="utf-8") as f:
            temp_raw = f.read().strip()
            return float(temp_raw) / 1000.0
    except Exception:
        return None


def get_throttled_flags():
    """Return raw throttled value from vcgencmd (hex string) or None."""
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True, text=True, timeout=5, check=False
        )
        if result.returncode == 0:
            out = result.stdout.strip()
            # vcgencmd get_throttled returns "throttled=0x12345"
            if "=" in out:
                return out.split("=", 1)[1].strip()
            return out
    except Exception:
        pass
    return None


THROTTLE_FLAG_MEANINGS = {
    0: "Under-voltage detected",
    1: "Arm frequency capped",
    2: "Currently throttled",
    3: "Soft temperature limit active",
    16: "Under-voltage has occurred",
    17: "Arm frequency capping has occurred",
    18: "Throttling has occurred",
    19: "Soft temperature limit has occurred",
}


def decode_throttled_flags(flags_hex):
    """Decode a vcgencmd throttled hex value into human-readable strings."""
    try:
        flags = int(flags_hex, 16)
    except (ValueError, TypeError):
        return []

    active = []
    for bit, meaning in THROTTLE_FLAG_MEANINGS.items():
        if flags & (1 << bit):
            active.append(meaning)
    return active


# ---------------- GPIO setup ----------------
GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)

try:
    GPIO.setup(PIN_ENA, GPIO.OUT)
    GPIO.setup(PIN_IN1, GPIO.OUT)
    GPIO.setup(PIN_IN2, GPIO.OUT)
except Exception as e:
    logger.warning("GPIO setup warning: %s", e)

# Limit switch input (pull-up; pressed => LOW)
try:
    GPIO.setup(TS_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
except Exception as e:
    logger.warning("TS switch setup warning: %s", e)

# PWM
pwm = GPIO.PWM(PIN_ENA, PWM_FREQ)
try:
    pwm.start(0)
except Exception as e:
    logger.warning("PWM start warning: %s", e)

# ---------------- INA219 init (optional) ----------------
have_ina = False
ina = None
ina_addr = None
if INA219 is not None:
    try:
        ina = INA219(SHUNT_OHMS, MAX_EXPECTED_AMPS, address=INA219_ADDR)
        ina.configure()
        have_ina = True
        ina_addr = INA219_ADDR
        logger.info("INA219 initialized successfully")
    except OSError as e:
        logger.warning("INA219 I/O error (bus): %s", e)
        have_ina = False
    except Exception as e:
        logger.warning("INA219 init failed: %s", e)
        have_ina = False
else:
    logger.info("ina219 library not available, skipping voltage monitoring")

# ---------------- scheduler & state ----------------
scheduler = BackgroundScheduler()
last_feed_time = None
feeding_lock = asyncio.Lock()

# Battery monitoring state
last_voltage = None
charging_detected = False
low_battery_warned = False

# Authorized users (for owner commands)
authorized_users = set()

# ---------------- persistence helpers ----------------
def load_json_file(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.warning("Failed to load %s: %s", path, e)
    return default

def validate_code(code_string):
    """
    Perform deep validation of code before applying it.
    Returns (bool, error_message)
    """
    # 1. Basic AST parse (Syntax check)
    try:
        ast.parse(code_string)
    except SyntaxError as e:
        return False, f"Syntax Error (line {e.lineno}): {e.msg}\n{e.text}"
    except Exception as e:
        return False, f"AST Parse error: {e}"

    # 2. Deep compilation and dry-run import check
    with tempfile.NamedTemporaryFile(suffix=".py", mode='w', delete=False) as tmp:
        tmp_path = tmp.name
        tmp.write(code_string)

    try:
        # Check if it compiles via py_compile
        subprocess.run([sys.executable, "-m", "py_compile", tmp_path], check=True, capture_output=True)

        # Try a "dry-run" import check
        # This will catch missing function definitions or top-level NameErrors
        # We use a subprocess so any fatal errors don't kill the main bot.
        check_cmd = [sys.executable, "-c", f"import sys, os; sys.path.insert(0, os.path.dirname('{tmp_path}')); import {os.path.basename(tmp_path)[:-3]}"]
        res = subprocess.run(check_cmd, capture_output=True, text=True, timeout=12)

        if res.returncode != 0:
            return False, f"Import/Runtime Error:\n{res.stderr}"

        return True, "OK"
    except subprocess.CalledProcessError as e:
        return False, f"Deep Compilation Failed:\n{e.stderr.decode() if e.stderr else str(e)}"
    except Exception as e:
        return False, f"Validation system failure: {e}"
    finally:
        if os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except: pass

def save_json_file(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning("Failed to save %s: %s", path, e)


def load_authorized_users():
    global authorized_users
    data = load_json_file(AUTHORIZED_FILE, [])
    authorized_users = set(data)
    if authorized_users:
        logger.info(f"Loaded {len(authorized_users)} authorized users")


def save_authorized_users():
    try:
        save_json_file(AUTHORIZED_FILE, list(authorized_users))
    except Exception as e:
        logger.warning("Failed to save authorized users: %s", e)


def load_battery_config():
    """Load battery configuration from file."""
    global BATTERY_MIN_VOLTAGE, BATTERY_MAX_CURRENT_MA, LOW_BATTERY_WARNING_THRESHOLD, BATTERY_FULL_VOLTAGE, BATTERY_EMPTY_VOLTAGE
    config = load_json_file(BATTERY_CONFIG_FILE, {})

    if config:
        BATTERY_MIN_VOLTAGE = config.get("min_voltage", BATTERY_MIN_VOLTAGE)
        BATTERY_MAX_CURRENT_MA = config.get("max_current_ma", BATTERY_MAX_CURRENT_MA)
        LOW_BATTERY_WARNING_THRESHOLD = config.get("warning_threshold", LOW_BATTERY_WARNING_THRESHOLD)
        BATTERY_FULL_VOLTAGE = config.get("full_voltage", BATTERY_FULL_VOLTAGE)
        BATTERY_EMPTY_VOLTAGE = config.get("empty_voltage", BATTERY_EMPTY_VOLTAGE)
        logger.info(f"Loaded battery config: min={BATTERY_MIN_VOLTAGE}V, full={BATTERY_FULL_VOLTAGE}V, empty={BATTERY_EMPTY_VOLTAGE}V")


def save_battery_config():
    """Save current battery configuration to file."""
    config = {
        "min_voltage": BATTERY_MIN_VOLTAGE,
        "max_current_ma": BATTERY_MAX_CURRENT_MA,
        "warning_threshold": LOW_BATTERY_WARNING_THRESHOLD,
        "full_voltage": BATTERY_FULL_VOLTAGE,
        "empty_voltage": BATTERY_EMPTY_VOLTAGE
    }
    save_json_file(BATTERY_CONFIG_FILE, config)
    logger.info("Battery config saved")

def load_wifi_config():
    """Load WiFi configuration from file."""
    return load_json_file(WIFI_CONFIG_FILE, {})

def save_wifi_config(config):
    """Save WiFi configuration to file."""
    save_json_file(WIFI_CONFIG_FILE, config)
    logger.info("WiFi config saved")

def load_ui_config():
    """Load UI configuration."""
    return load_json_file(UI_CONFIG_FILE, {})

def save_ui_config(data):
    """Save UI configuration."""
    save_json_file(UI_CONFIG_FILE, data)
    logger.info("UI config saved")

def write_shared_state(**kwargs):
    try:
        state = load_json_file(SHARED_STATE_FILE, {})
        state.update(kwargs)
        state["ts"] = time.time()
        save_json_file(SHARED_STATE_FILE, state)
    except Exception:
        pass

def load_state():
    global last_feed_time
    d = load_json_file(STATE_FILE, {})
    if d.get("last_feed_time"):
        try:
            last_feed_time = datetime.fromisoformat(d["last_feed_time"])
            logger.info("Loaded last_feed_time: %s", last_feed_time)
        except Exception as e:
            logger.warning("Invalid last_feed_time format in state: %s", e)

def save_state():
    try:
        d = {"last_feed_time": last_feed_time.isoformat() if last_feed_time else None}
        save_json_file(STATE_FILE, d)
    except Exception as e:
        logger.warning("Failed to save state: %s", e)

# ----------------- hardware helpers -----------------
_last_ts_state = None

def ts_pressed():
    global _last_ts_state
    try:
        val = GPIO.input(TS_PIN) == GPIO.LOW
        new_state = "PRESSED" if val else "OPEN"
        if new_state != _last_ts_state:
            _last_ts_state = new_state
            write_shared_state(sensor=new_state)
        return val
    except Exception:
        return False


def set_speed_percent(percent):
    pct = max(0, min(100, percent))
    try:
        pwm.ChangeDutyCycle(pct)
    except Exception as e:
        logger.warning("Failed to set PWM: %s", e)


def run_forward(percent=100):
    logger.debug("run_forward %d%%", percent)
    set_speed_percent(percent)
    GPIO.output(PIN_IN1, GPIO.HIGH)
    GPIO.output(PIN_IN2, GPIO.LOW)
    write_shared_state(motor="FORWARD", motor_pct=percent)


def run_reverse(percent=100):
    logger.debug("run_reverse %d%%", percent)
    set_speed_percent(percent)
    GPIO.output(PIN_IN1, GPIO.LOW)
    GPIO.output(PIN_IN2, GPIO.HIGH)
    write_shared_state(motor="REVERSE", motor_pct=percent)


def stop_motor():
    logger.debug("stop_motor")
    set_speed_percent(0)
    GPIO.output(PIN_IN1, GPIO.LOW)
    GPIO.output(PIN_IN2, GPIO.LOW)
    write_shared_state(motor="STOPPED", motor_pct=0)


def ensure_ina_ready():
    global ina, have_ina, ina_addr
    if have_ina and ina is not None:
        return True
    if INA219 is None:
        return False
    # Try known addresses
    for addr in INA219_ADDRESSES:
        try:
            tmp = INA219(SHUNT_OHMS, MAX_EXPECTED_AMPS, address=addr)
            tmp.configure()
            ina = tmp
            have_ina = True
            ina_addr = addr
            logger.info(f"INA219 initialized at 0x{addr:02x}")
            return True
        except OSError as e:
            logger.warning(f"INA219 I/O error during init at 0x{addr:02x}: {e}")
            continue
        except Exception as e:
            logger.warning(f"INA219 init failed at 0x{addr:02x}: {e}")
            continue
    have_ina = False
    return False

# ----------------- WiFi Management Functions -----------------
def get_current_wifi_ssid():
    """Get the currently connected WiFi SSID."""
    try:
        result = subprocess.run(["iwgetid", "-r"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            ssid = result.stdout.strip()
            return ssid if ssid else None
        return None
    except Exception as e:
        logger.warning(f"Failed to get current WiFi SSID: {e}")
        return None

def scan_wifi_networks():
    """Scan for available WiFi networks."""
    try:
        result = subprocess.run(["sudo", "iwlist", "scan"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            networks = []
            lines = result.stdout.split('\n')
            for line in lines:
                if 'ESSID:' in line:
                    ssid = line.split('ESSID:"')[1].split('"')[0] if '"' in line else ""
                    if ssid and ssid not in networks:
                        networks.append(ssid)
            return networks
        return []
    except Exception as e:
        logger.warning(f"WiFi scan failed: {e}")
        return []

def test_wifi_connection():
    """Test if WiFi connection is working by pinging Google DNS."""
    try:
        result = subprocess.run(["ping", "-c", "1", "-W", "3", "8.8.8.8"],
                              capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except Exception as e:
        logger.warning(f"WiFi connection test failed: {e}")
        return False

def backup_current_network():
    """Backup current network configuration."""
    current_ssid = get_current_wifi_ssid()
    if current_ssid:
        wifi_config = load_wifi_config()
        wifi_config["last_network"] = current_ssid
        save_wifi_config(wifi_config)
        logger.info(f"Backed up current network: {current_ssid}")
        return current_ssid
    return None

def connect_to_wifi(ssid, password):
    """Connect to WiFi network using wpa_supplicant."""
    try:
        # Create temporary wpa_supplicant config
        temp_config = f"/tmp/wifi_{ssid}.conf"
        with open(temp_config, 'w') as f:
            f.write(f'network={{\n    ssid="{ssid}"\n    psk="{password}"\n}}\n')

        # Copy to wpa_supplicant.conf
        subprocess.run(["sudo", "cp", temp_config, "/etc/wpa_supplicant/wpa_supplicant.conf"],
                      check=True)

        # Restart network interface
        subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "reconfigure"], check=True)

        # Wait for connection
        time.sleep(10)

        # Clean up temp file
        os.remove(temp_config)

        return True
    except Exception as e:
        logger.error(f"Failed to connect to WiFi {ssid}: {e}")
        return False

def restore_last_network():
    """Restore connection to the last known working network."""
    wifi_config = load_wifi_config()
    last_network = wifi_config.get("last_network")
    if not last_network:
        logger.warning("No last network to restore")
        return False

    try:
        # Try to reconnect using existing wpa_supplicant configuration
        subprocess.run(["sudo", "wpa_cli", "-i", "wlan0", "reconfigure"], check=True)
        time.sleep(10)

        # Verify connection
        if test_wifi_connection():
            logger.info(f"Successfully restored connection to {last_network}")
            return True
        else:
            logger.warning(f"Failed to restore connection to {last_network}")
            return False
    except Exception as e:
        logger.error(f"Failed to restore last network: {e}")
        return False

def read_battery():
    global ina, have_ina
    if not ensure_ina_ready():
        write_shared_state(battery_voltage=None, battery_current=None, battery_shunt=None)
        return None
    try:
        v_bus = float(ina.voltage())
        shunt_mv = float(ina.shunt_voltage())
        current_ma = None
        try:
            current_ma = float(ina.current())
        except Exception as e:
            # Try to recover from range issues
            if DeviceRangeError is not None and isinstance(e, DeviceRangeError):
                try:
                    ina.configure(voltage_range=ina.RANGE_16V, gain=ina.GAIN_AUTO)
                    current_ma = float(ina.current())
                except Exception:
                    current_ma = None
            else:
                current_ma = None
        result = {
            "voltage": v_bus,
            "current_ma": current_ma,
            "shunt_mv": shunt_mv,
        }
        write_shared_state(battery_voltage=v_bus, battery_current=current_ma, battery_shunt=shunt_mv)
        return result
    except Exception as e:
        logger.warning("INA219 read error: %s", e)
        have_ina = False
        ina = None
        write_shared_state(battery_voltage=None, battery_current=None, battery_shunt=None)
        return None



# ----------------- UI Classes -----------------

class FeedModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title=t("manual_feed_title"))
        self.duration = discord.ui.TextInput(
            label=t("duration_label"),
            placeholder="5",
            default="5",
            min_length=1,
            max_length=2,
        )
        self.add_item(self.duration)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            seconds = int(self.duration.value)
        except ValueError:
            await interaction.response.send_message(t("invalid_number"), ephemeral=True)
            return

        if seconds > FEED_DURATION_MAX:
             await interaction.response.send_message(t("max_duration", max=FEED_DURATION_MAX), ephemeral=True)
             return

        if feeding_lock.locked():
            await interaction.response.send_message(t("motor_busy"), ephemeral=True)
            return

        ok, msg = battery_ok()
        if not ok:
            await interaction.response.send_message(t("battery_unsafe", msg=msg), ephemeral=True)
            return

        await interaction.response.send_message(t("starting_feed", seconds=seconds), ephemeral=True)
        # Run actual feed in background
        async with feeding_lock:
             await motor_run_with_limit(seconds, forward=True, percent=100)

        await notify_log_channel(t("manual_feed_log", user=interaction.user.display_name, seconds=seconds))

class ScheduleAddModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title=t("sched_add_title"))
        self.hour = discord.ui.TextInput(label=t("hour_label"), placeholder="07", min_length=1, max_length=2)
        self.minute = discord.ui.TextInput(label=t("minute_label"), placeholder="30", min_length=1, max_length=2)
        self.duration = discord.ui.TextInput(label=t("duration_label"), placeholder="5", default="5", min_length=1, max_length=2)
        self.add_item(self.hour)
        self.add_item(self.minute)
        self.add_item(self.duration)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            h = int(self.hour.value)
            m = int(self.minute.value)
            d = int(self.duration.value)
        except ValueError:
             await interaction.response.send_message(t("invalid_number"), ephemeral=True)
             return

        if not (0 <= h <= 23) or not (0 <= m <= 59):
            await interaction.response.send_message(t("invalid_time"), ephemeral=True)
            return

        add_schedule_job(h, m, d)

        await interaction.response.send_message(t("sched_added", h=h, m=m, d=d), ephemeral=True)
        await notify_log_channel(t("sched_log", user=interaction.user.display_name, h=h, m=m))

class ReverseModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title=t("rev_title"))
        self.duration = discord.ui.TextInput(label=t("duration_label"), placeholder="3", default="3")
        self.add_item(self.duration)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            seconds = int(self.duration.value)
        except ValueError:
            await interaction.response.send_message(t("invalid_number"), ephemeral=True)
            return

        if seconds > 10: seconds = 10 # Safety cap for reverse UI

        if feeding_lock.locked():
             await interaction.response.send_message(t("motor_busy"), ephemeral=True)
             return

        await interaction.response.send_message(t("reversing", seconds=seconds), ephemeral=True)
        async with feeding_lock:
             await motor_run_with_limit(seconds, forward=False, percent=100)

        await notify_log_channel(t("rev_log", user=interaction.user.display_name))

class BatteryConfigModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title=t("bat_title"))
        self.min_v = discord.ui.TextInput(label="Min Voltage (Cutoff)", placeholder="6.0")
        self.warn_v = discord.ui.TextInput(label="Warning Voltage", placeholder="6.4")
        self.max_c = discord.ui.TextInput(label="Max Current (mA)", placeholder="1200")

        self.min_v.default = str(BATTERY_MIN_VOLTAGE)
        self.warn_v.default = str(LOW_BATTERY_WARNING_THRESHOLD)
        self.max_c.default = str(BATTERY_MAX_CURRENT_MA)

        self.add_item(self.min_v)
        self.add_item(self.warn_v)
        self.add_item(self.max_c)

    async def on_submit(self, interaction: discord.Interaction):
        global BATTERY_MIN_VOLTAGE, LOW_BATTERY_WARNING_THRESHOLD, BATTERY_MAX_CURRENT_MA
        try:
            v_min = float(self.min_v.value)
            v_warn = float(self.warn_v.value)
            c_max = int(self.max_c.value)
        except ValueError:
            await interaction.response.send_message(t("invalid_number"), ephemeral=True)
            return

        BATTERY_MIN_VOLTAGE = v_min
        LOW_BATTERY_WARNING_THRESHOLD = v_warn
        BATTERY_MAX_CURRENT_MA = c_max
        save_battery_config()

        await interaction.response.send_message(t("bat_updated"), ephemeral=True)
        await notify_log_channel(t("bat_log", user=interaction.user.display_name))

class AuthModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title=t("auth_title"))
        self.user_id = discord.ui.TextInput(label=t("user_id_label"), placeholder="1234567890")
        self.add_item(self.user_id)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(t("only_owner"), ephemeral=True)
            return
        try:
            uid = int(self.user_id.value)
            authorized_users.add(uid)
            save_authorized_users()
            await interaction.response.send_message(t("auth_success", uid=uid), ephemeral=True)
            await notify_log_channel(t("auth_log", uid=uid))
        except ValueError:
             await interaction.response.send_message("Invalid ID", ephemeral=True)

class LanguageSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="English", value="en", emoji="🇺🇸"),
            discord.SelectOption(label="ไทย (Thai)", value="th", emoji="🇹🇭"),
            discord.SelectOption(label="中文 (Chinese)", value="zh", emoji="🇨🇳"),
        ]
        super().__init__(placeholder=t("choose_lang"), min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        lang = self.values[0]
        if save_language_config(lang):
             await interaction.response.send_message(t("lang_set", lang=lang), ephemeral=True)
        else:
             await interaction.response.send_message("Error setting language", ephemeral=True)

class LanguageSelectView(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.add_item(LanguageSelect())

class ScheduleRemoveSelect(discord.ui.Select):
    def __init__(self, schedules):
        options = []
        for s in schedules:
            lbl = f"{s['hour']:02d}:{s['minute']:02d} ({s['duration']}s)"
            options.append(discord.SelectOption(label=lbl, value=s['id'], emoji="🕒"))

        if not options:
            options.append(discord.SelectOption(label=t("no_schedules"), value="none"))

        super().__init__(placeholder=t("sched_rem_select"), min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        job_id = self.values[0]
        if job_id == "none":
            await interaction.response.send_message(t("nothing_selected"), ephemeral=True)
            return

        try: scheduler.remove_job(job_id)
        except: pass

        schedules = load_json_file(SCHEDULE_FILE, [])
        schedules = [s for s in schedules if s.get("id") != job_id]
        save_json_file(SCHEDULE_FILE, schedules)

        await interaction.response.send_message(t("sched_rem_success", id=job_id), ephemeral=True)
        await notify_log_channel(t("sched_rem_log", user=interaction.user.display_name))

class ScheduleRemoveView(discord.ui.View):
    def __init__(self):
        super().__init__()
        schedules = load_json_file(SCHEDULE_FILE, [])
        self.add_item(ScheduleRemoveSelect(schedules))

class ForceFeedConfirmation(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

        self.confirm_btn = discord.ui.Button(label=t("force_confirm"), style=discord.ButtonStyle.danger)
        self.confirm_btn.callback = self.confirm
        self.add_item(self.confirm_btn)

    async def confirm(self, interaction: discord.Interaction):
        await interaction.response.send_message(t("force_executing"), ephemeral=True)
        async with feeding_lock:
             await motor_run_with_limit(5, forward=True, percent=100, ignore_ts=True)
        await notify_log_channel(t("force_log", user=interaction.user.display_name))
        self.stop()

class MenuDropdown(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=t("opt_add_sched"), emoji="➕", value="add_schedule"),
            discord.SelectOption(label=t("opt_rem_sched"), emoji="➖", value="remove_schedule"),
            discord.SelectOption(label=t("opt_lang"), emoji="🌐", value="change_lang"),
        ]
        super().__init__(placeholder=t("menu_placeholder"), min_values=1, max_values=1, options=options, custom_id="menu_dropdown")

    async def callback(self, interaction: discord.Interaction):
        val = self.values[0]
        if val == "add_schedule":
            await interaction.response.send_modal(ScheduleAddModal())
        elif val == "remove_schedule":
            await interaction.response.send_message(t("sched_rem_select"), view=ScheduleRemoveView(), ephemeral=True)
        elif val == "change_lang":
            await interaction.response.send_message(t("choose_lang"), view=LanguageSelectView(), ephemeral=True)

class DevMenuDropdown(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=t("opt_force"), emoji="⚠️", value="force_feed"),
            discord.SelectOption(label=t("opt_rev"), emoji="🔄", value="reverse"),
            discord.SelectOption(label=t("opt_bat"), emoji="🔋", value="battery_config"),
            discord.SelectOption(label=t("opt_auth"), emoji="🔐", value="authorize"),
            discord.SelectOption(label=t("opt_update"), emoji="📤", value="update_file"),
            discord.SelectOption(label=t("opt_download"), emoji="📥", value="download"),
            discord.SelectOption(label=t("opt_restart"), emoji="☠️", value="restart"),
        ]
        super().__init__(placeholder="🛠️ Developer Tools...", min_values=1, max_values=1, options=options, custom_id="dev_menu_dropdown")

    async def callback(self, interaction: discord.Interaction):
        val = self.values[0]
        if val == "force_feed":
            await interaction.response.send_message(t("force_warning"), view=ForceFeedConfirmation(), ephemeral=True)
        elif val == "reverse":
            await interaction.response.send_modal(ReverseModal())
        elif val == "battery_config":
            await interaction.response.send_modal(BatteryConfigModal())
        elif val == "authorize":
            await interaction.response.send_modal(AuthModal())
        elif val == "download":
            await interaction.response.send_message(f"{t('download_title')}\n{t('download_usage')}", ephemeral=True)
        elif val == "restart":
            await interaction.response.send_message(t("restarted"), ephemeral=True)
            subprocess.run(["sudo", "systemctl", "restart", "fishfeeder.service"])
        elif val == "update_file":
            if interaction.user.id != OWNER_ID and interaction.user.id not in authorized_users:
                await interaction.response.send_message(t("owner_only"), ephemeral=True)
                return
            await interaction.response.send_message(t("update_prompt"), ephemeral=True)

            def check(m):
                return m.author.id == interaction.user.id and len(m.attachments) > 0 and m.channel.id == interaction.channel.id

            try:
                msg = await bot.wait_for('message', check=check, timeout=60.0)
                attachment = msg.attachments[0]

                if not attachment.filename.endswith('.py'):
                    await interaction.followup.send(t("not_py_file"), ephemeral=True)
                    return

                await interaction.followup.send(t("downloading", filename=attachment.filename), ephemeral=True)

                # Download logic (duplicated from cmd_update_file for safety/independence)
                if aiohttp:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(attachment.url) as resp:
                            if resp.status == 200:
                                new_code = await resp.text()

                                # Syntax check
                                try:
                                    import ast
                                    ast.parse(new_code)
                                except SyntaxError as e:
                                    await interaction.followup.send(t("syntax_error", lineno=e.lineno, msg=e.msg), ephemeral=True)
                                    return

                                # Backup
                                shutil.copy(__file__, __file__ + ".backup")

                                # Write
                                with open(__file__, 'w') as f:
                                    f.write(new_code)

                                await interaction.followup.send(t("update_success"), ephemeral=True)
                                await notify_log_channel(t("update_log", user=interaction.user.display_name))
                                subprocess.run(["sudo", "systemctl", "restart", "fishfeeder.service"])
                            else:
                                await interaction.followup.send("❌ Download failed.", ephemeral=True)
                else:
                    await interaction.followup.send("❌ `aiohttp` library missing. Cannot download.", ephemeral=True)

            except asyncio.TimeoutError:
                await interaction.followup.send(t("update_timeout"), ephemeral=True)

class DevControlPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) # Persistent view
        self.add_item(DevMenuDropdown())

class ControlPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) # Persistent view
        # Add Menu with dynamic elements
        self.add_item(MenuDropdown())

        # Add Buttons (Defined manually to allow dynamic labels)
        btn_feed = discord.ui.Button(label=t("btn_feed"), style=discord.ButtonStyle.green, custom_id="btn_feed", emoji="🐟")
        btn_feed.callback = self.btn_feed
        self.add_item(btn_feed)

        btn_status = discord.ui.Button(label=t("btn_status"), style=discord.ButtonStyle.blurple, custom_id="btn_status", emoji="📊")
        btn_status.callback = self.btn_status
        self.add_item(btn_status)

        btn_stop = discord.ui.Button(label=t("btn_stop"), style=discord.ButtonStyle.red, custom_id="btn_stop", emoji="🛑")
        btn_stop.callback = self.btn_stop
        self.add_item(btn_stop)

        btn_schedules = discord.ui.Button(label=t("btn_sched"), style=discord.ButtonStyle.gray, custom_id="btn_schedules", emoji="📅")
        btn_schedules.callback = self.btn_schedules
        self.add_item(btn_schedules)

        btn_wifi = discord.ui.Button(label=t("btn_wifi"), style=discord.ButtonStyle.gray, custom_id="btn_wifi", emoji="📡")
        btn_wifi.callback = self.btn_wifi
        self.add_item(btn_wifi)

    async def btn_feed(self, interaction: discord.Interaction):
        await interaction.response.send_modal(FeedModal())

    async def btn_status(self, interaction: discord.Interaction):
        # Battery info
        v = read_battery()
        bat_str = "N/A"
        if v:
            c_str = f"{v['current_ma']:.0f}mA" if v['current_ma'] is not None else "N/A"
            bat_str = f"{v['voltage']:.2f}V | {c_str}"

        # Motor info
        motor_state = t("st_feeding") if feeding_lock.locked() else t("st_idle")

        # TS Info
        ts_state = t("st_pressed") if ts_pressed() else t("st_open")

        # Time
        last_f = last_feed_time.strftime("%H:%M") if last_feed_time else t("st_never")

        msg = f"{t('status_title')}\n" \
              f"{t('st_bat')}: `{bat_str}`\n" \
              f"{t('st_motor')}: `{motor_state}`\n" \
              f"{t('st_sw')}: `{ts_state}`\n" \
              f"{t('st_last')}: `{last_f}`"
        await interaction.response.send_message(msg, ephemeral=True)

    async def btn_stop(self, interaction: discord.Interaction):
        stop_motor()
        await interaction.response.send_message(t("emg_stop"), ephemeral=True)
        await notify_log_channel(t("emg_stop_log", user=interaction.user.display_name))

    async def btn_schedules(self, interaction: discord.Interaction):
        schedules = load_json_file(SCHEDULE_FILE, [])
        if not schedules:
            await interaction.response.send_message(t("sched_none"), ephemeral=True)
            return

        txt = f"{t('sched_active')}\n"
        for s in schedules:
            txt += f"• `{s['hour']:02d}:{s['minute']:02d}` ({s['duration']}s)\n"
        await interaction.response.send_message(txt, ephemeral=True)

    async def btn_wifi(self, interaction: discord.Interaction):
        ssid = get_current_wifi_ssid()
        # Get IP
        ip = subprocess.getoutput("hostname -I").strip()
        await interaction.response.send_message(f"{t('wifi_status')}\n{t('wifi_ssid')}: `{ssid}`\n{t('wifi_ip')}: `{ip}`", ephemeral=True)

# ----------------- bot setup -----------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)  # Disable default help

async def check_and_clear_ts_on_boot():
    """Check if TS switch is pressed on startup and reverse motor to clear it."""
    try:
        if ts_pressed():
            logger.warning("TS switch pressed on startup! Reversing to clear...")
            await notify_log_channel(t("ts_boot_warn"))

            # Reverse motor until switch is released or timeout (max 5 seconds)
            run_reverse(50)  # Run at 50% speed for gentler operation
            max_wait = 5.0
            elapsed = 0.0
            step = 0.1

            while ts_pressed() and elapsed < max_wait:
                await asyncio.sleep(step)
                elapsed += step

            stop_motor()

            if ts_pressed():
                # Still pressed after timeout
                await notify_log_channel(t("ts_boot_fail"))
                logger.warning("TS switch still pressed after reverse timeout")
            else:
                # Successfully cleared
                await notify_log_channel(t("ts_boot_clear"))
                logger.info(f"TS switch cleared after {elapsed:.1f}s")
        else:
            logger.info("TS switch check: not pressed, ready to operate")
    except Exception as e:
        logger.exception(f"Error in check_and_clear_ts_on_boot: {e}")
        stop_motor()  # Ensure motor is stopped on error


async def report_battery_on_boot():
    try:
        if not ensure_ina_ready():
            await notify_log_channel("\ud83d\udd0b Boot battery: INA219 not available (install library or check I2C).")
            return
        v = read_battery()
        if not v:
            await notify_log_channel("\ud83d\udd0b Boot battery: read failed (I/O).")
            return
        c_str = f"{v['current_ma']:.0f}mA" if v['current_ma'] is not None else "N/A"
        await notify_log_channel(
            f"\ud83d\udd0b Boot battery: {v['voltage']:.2f}V, {c_str}, {v['shunt_mv']:.0f}mV"
        )
    except Exception as e:
        logger.warning("report_battery_on_boot failed: %s", e)

async def notify_log_channel(message: str):
    if LOG_CHANNEL_ID:
        try:
            ch = bot.get_channel(LOG_CHANNEL_ID)
            if ch is None:
                ch = await bot.fetch_channel(LOG_CHANNEL_ID)
            await ch.send(message)
        except Exception as e:
            logger.warning("notify_log_channel failed: %s", e)

async def web_command_poller():
    """Poll command.json and execute feed/reverse/stop commands from the web dashboard."""
    while True:
        try:
            if os.path.exists(COMMAND_FILE):
                with open(COMMAND_FILE) as f:
                    cmd = json.load(f)
                if cmd.get("executed"):
                    os.remove(COMMAND_FILE)
                    await asyncio.sleep(1)
                    continue
                action = cmd.get("action")
                seconds = int(cmd.get("seconds", 5))
                if action == "feed":
                    await motor_run_with_limit(seconds, forward=True)
                    await notify_log_channel(f"Web: fed for {seconds}s")
                elif action == "reverse":
                    await motor_run_with_limit(seconds, forward=False)
                    await notify_log_channel(f"Web: reversed for {seconds}s")
                elif action == "stop":
                    stop_motor()
                    await notify_log_channel("Web: motor stopped")
                elif action == "kill":
                    stop_motor()
                    await notify_log_channel("Web: emergency kill")
                cmd["executed"] = True
                with open(COMMAND_FILE, "w") as f:
                    json.dump(cmd, f)
        except Exception as e:
            logger.debug("web_command_poller: %s", e)
        await asyncio.sleep(1)

# -----------------------------------------
# Git Auto-Update
# -----------------------------------------

def is_auto_update_enabled():
    try:
        data = load_json_file(AUTO_UPDATE_FILE, {"enabled": True})
        return data.get("enabled", True)
    except Exception:
        return True

def set_auto_update_enabled(enabled):
    save_json_file(AUTO_UPDATE_FILE, {"enabled": enabled})
    write_shared_state(auto_update_enabled=enabled)

def _extract_var_from_file(filepath, var_name):
    """Extract a string or tuple constant assigned to var_name using AST."""
    try:
        with open(filepath) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == var_name:
                        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                            return node.value.value
                        if isinstance(node.value, ast.Tuple):
                            return tuple(ast.literal_eval(e) for e in node.value.elts)
    except Exception:
        pass
    return None

def _install_hdmi_from_patch(patch_path):
    """Extract and install HDMI GUI from a dev_patch_installer.py file."""
    gui_code = _extract_var_from_file(patch_path, "GUI_CODE")
    autostart = _extract_var_from_file(patch_path, "AUTOSTART_CONFIG")
    if not gui_code or not autostart:
        logger.warning("HDMI install: GUI_CODE or AUTOSTART_CONFIG not found in patch")
        return False
    try:
        gui_file = os.path.join(REPO_DIR, "pi_gui.py")
        with open(gui_file, "w") as f:
            f.write(gui_code)
        autostart_dir = os.path.expanduser("~/.config/autostart")
        os.makedirs(autostart_dir, exist_ok=True)
        python_path = "/home/sira/feederbot/bin/python"
        if not os.path.exists(python_path):
            python_path = "/usr/bin/python3"
        with open(os.path.join(autostart_dir, "fishfeeder_gui.desktop"), "w") as f:
            f.write(autostart.format(gui_path=gui_file, python_path=python_path))
        logger.info("HDMI GUI auto-installed from dev patch")
        return True
    except Exception as e:
        logger.error("Failed to auto-install HDMI: %s", e)
        return False

def _install_web_from_patch(patch_path):
    """Extract and install Web Dashboard from a dev_patch_installer.py file."""
    web_code = _extract_var_from_file(patch_path, "WEB_CODE")
    web_svc = _extract_var_from_file(patch_path, "WEB_SERVICE")
    if not web_code or not web_svc:
        logger.warning("Web install: WEB_CODE or WEB_SERVICE not found in patch")
        return False
    try:
        web_file = os.path.join(REPO_DIR, "web_dashboard.py")
        with open(web_file, "w") as f:
            f.write(web_code)
        svc_path = "/etc/systemd/system/web_dashboard.service"
        subprocess.run(["sudo", "tee", svc_path], input=web_svc.encode(), check=True)
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        subprocess.run(["sudo", "systemctl", "enable", "web_dashboard"], check=True)
        subprocess.run(["sudo", "systemctl", "restart", "web_dashboard"], check=True)

        ngrok_cfg = load_json_file(NGROK_CONFIG_FILE, {})
        ngrok_auth = ngrok_cfg.get("auth") or os.environ.get("NGROK_AUTH", "")
        ngrok_domain = ngrok_cfg.get("domain") or os.environ.get("NGROK_DOMAIN", "")
        ngrok_path = subprocess.run(["which", "ngrok"], capture_output=True, text=True, timeout=5).stdout.strip()
        if ngrok_path and ngrok_auth and ngrok_domain:
            subprocess.run(["pkill", "-f", "ngrok"], capture_output=True)
            subprocess.run([ngrok_path, "config", "add-authtoken", ngrok_auth], capture_output=True, timeout=10)
            subprocess.Popen([ngrok_path, "http", "--url=" + ngrok_domain, "5000"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        logger.info("Web Dashboard auto-installed from dev patch")
        return True
    except Exception as e:
        logger.error("Failed to auto-install Web: %s", e)
        return False

def _git(*args, **kwargs):
    kwargs.setdefault("cwd", REPO_DIR)
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("timeout", 30)
    return subprocess.run(["git", *args], **kwargs)

async def process_git_update():
    try:
        r = _git("diff", "--name-only", f"HEAD..{GIT_REMOTE}/{GIT_BRANCH}")
        changed = [f.strip() for f in r.stdout.strip().split("\n") if f.strip()]
        needs_bot = "fishfeeder_bot.py" in changed
        needs_patch = "dev_patch_installer.py" in changed

        if not needs_bot and not needs_patch:
            _git("pull", GIT_REMOTE, GIT_BRANCH)
            write_shared_state(update_status="up_to_date")
            return

        r = _git("log", "--oneline", f"HEAD..{GIT_REMOTE}/{GIT_BRANCH}")
        commits = [l.strip() for l in r.stdout.strip().split("\n") if l.strip()]
        is_new = needs_patch and not os.path.exists(DEV_PATCH_VERSION_FILE)

        if needs_bot:
            write_shared_state(update_status="updating_bot", update_component="bot",
                               update_changes=commits, bot_version=BOT_VERSION)
            await asyncio.sleep(1)
            _git("pull", GIT_REMOTE, GIT_BRANCH)
            write_shared_state(update_status="restarting", last_updated=time.time())
            await asyncio.sleep(1)
            subprocess.run(["sudo", "systemctl", "restart", "fishfeeder.service"])
            sys.exit(0)

        if needs_patch:
            update_type = "Dev Patch"
            try:
                r3 = _git("show", f"{GIT_REMOTE}/{GIT_BRANCH}:dev_patch_installer.py", timeout=10)
                for line in r3.stdout.split("\n"):
                    if "UPDATE_TYPE:" in line:
                        update_type = line.split("UPDATE_TYPE:", 1)[1].strip()
                        break
            except Exception:
                pass
            status = "installing_dev_patch" if is_new else "updating_dev_patch"
            write_shared_state(update_status=status, update_component="dev_patch",
                               update_changes=commits, update_type=update_type)
            await asyncio.sleep(1)
            _git("checkout", f"{GIT_REMOTE}/{GIT_BRANCH}", "--", "dev_patch_installer.py")

            # Auto-install components based on AUTO_DEV_PATCH_INSTALL_CHOICES
            choices = _extract_var_from_file(DEV_PATCH_INSTALLER, "AUTO_DEV_PATCH_INSTALL_CHOICES")
            if isinstance(choices, tuple):
                if 'HDMI' in choices:
                    _install_hdmi_from_patch(DEV_PATCH_INSTALLER)
                if 'WEB' in choices:
                    _install_web_from_patch(DEV_PATCH_INSTALLER)

            with open(DEV_PATCH_VERSION_FILE, "w") as f:
                f.write("1")
            write_shared_state(update_status="up_to_date", last_updated=time.time(),
                               update_component=None, update_changes=[], update_type="")
    except Exception as e:
        logger.error("process_git_update: %s", e)
        write_shared_state(update_status="error")

async def git_update_checker():
    last_fetch = 0.0
    behind = 0
    while True:
        try:
            if not is_auto_update_enabled():
                await asyncio.sleep(2)
                continue
            now = time.time()
            if now - last_fetch >= 60:
                r = _git("fetch", GIT_REMOTE, GIT_BRANCH, timeout=30)
                if r.returncode == 0:
                    r2 = _git("rev-list", "--count", f"HEAD..{GIT_REMOTE}/{GIT_BRANCH}")
                    behind = int(r2.stdout.strip()) if r2.stdout.strip() else 0
                    last_fetch = now
                    write_shared_state(last_update_check=now)
                    if behind == 0:
                        write_shared_state(update_status="up_to_date")
                else:
                    logger.debug("git fetch: %s", r.stderr)
            if behind > 0:
                await process_git_update()
                behind = 0
        except Exception as e:
            logger.debug("git_update_checker: %s", e)
        await asyncio.sleep(2)

# ----------------- motor run coroutine -----------------
async def motor_run_with_limit(seconds: int, forward: bool = True, percent: int = 100, ignore_ts: bool = False, ts_grace_period: float = 0.0):
    global last_feed_time
    if seconds < 1:
        seconds = 1

    # Check TS switch before start (unless ignoring or grace period allows starting)
    if not ignore_ts and ts_grace_period <= 0 and ts_pressed():
        logger.info("TS pressed before start; aborting motor run")
        await notify_log_channel("TS switch pressed; aborting motor run")
        return

    if forward:
        run_forward(percent)
    else:
        run_reverse(percent)

    start = datetime.now(timezone.utc)
    remaining = float(seconds)
    step = 0.1
    try:
        while remaining > 0:
            # Check TS switch during run (unless ignoring or within grace period)
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            if not ignore_ts and elapsed > ts_grace_period and ts_pressed():
                logger.info("TS pressed; stopping motor early")
                await notify_log_channel("TS switch pressed; motor stopped early")
                break
            await asyncio.sleep(step)
            remaining -= step
    finally:
        stop_motor()
        write_shared_state(motor="IDLE", last_feed=time.time())
        last_feed_time = datetime.now(timezone.utc)
        save_state()
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        logger.info("Motor run complete (elapsed %.2fs)", elapsed)

# ----------------- scheduled job -----------------
async def scheduled_feed_job(duration_sec=FEED_DURATION_DEFAULT):
    async with feeding_lock:
        try:
            logger.info("Scheduled feed: running for %ds", duration_sec)
            await notify_log_channel(f"\U0001F41F Scheduled feed started for {duration_sec}s")
            await motor_run_with_limit(duration_sec, forward=True, percent=100)
            await notify_log_channel("\U0001F41F Scheduled feed finished")
        except Exception as e:
            logger.exception("Error in scheduled_feed_job: %s", e)
            stop_motor()


def schedule_all_from_file():
    schedules = load_json_file(SCHEDULE_FILE, [])
    for s in schedules:
        try:
            job_id = s.get("id")
            hour = int(s["hour"])
            minute = int(s["minute"])
            duration = int(s.get("duration", FEED_DURATION_DEFAULT))

            def job_wrapper(d=duration):
                if bot.loop and bot.loop.is_running():
                    asyncio.run_coroutine_threadsafe(scheduled_feed_job(d), bot.loop)
                else:
                    asyncio.run(scheduled_feed_job(d))

            scheduler.add_job(job_wrapper, "cron", hour=hour, minute=minute, id=job_id)
            logger.info("Loaded schedule %s at %02d:%02d dur %ds", job_id, hour, minute, duration)
        except Exception as e:
            logger.warning("Failed to add schedule entry %s : %s", s, e)


def add_schedule_job(hour: int, minute: int, duration: int):
    """Add a scheduled feed to the APScheduler and persist it."""
    job_id = f"feed_{hour:02d}{minute:02d}"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass

    def job_func(dur=duration):
        if bot.loop and bot.loop.is_running():
            asyncio.run_coroutine_threadsafe(scheduled_feed_job(dur), bot.loop)
        else:
            asyncio.run(scheduled_feed_job(dur))

    scheduler.add_job(job_func, "cron", hour=hour, minute=minute, id=job_id)

    schedules = load_json_file(SCHEDULE_FILE, [])
    schedules = [s for s in schedules if s.get("id") != job_id]
    schedules.append({"id": job_id, "hour": hour, "minute": minute, "duration": duration})
    save_json_file(SCHEDULE_FILE, schedules)
    return job_id

# ----------------- Initialization -----------------
def initialize_data():
    """Load all config and state files."""
    os.makedirs(REPO_DIR, exist_ok=True)
    load_language_config()
    load_battery_config()
    load_state()
    load_authorized_users()
    schedule_all_from_file()
    logger.info("Initialization complete")

# ----------------- permission check decorator -----------------
def owner_check():
    def predicate(ctx):
        return ctx.author and (ctx.author.id == OWNER_ID or ctx.author.id in authorized_users)
    return commands.check(predicate)

# ----------------- events & commands -----------------
@bot.event
async def on_ready():
    logger.info("Bot logged in as %s (id=%s)", bot.user, bot.user.id)
    bot.add_view(ControlPanel())

    # Log Raspberry Pi hardware info on startup
    pi_model = get_pi_model()
    cpu_temp = get_cpu_temp()
    logger.info("Hardware: %s", pi_model)
    if cpu_temp is not None:
        logger.info("CPU temp: %.1f°C", cpu_temp)

    # Load UI config & send panel to last known channel
    ui_conf = load_ui_config()
    cid = ui_conf.get("channel_id", LOG_CHANNEL_ID)
    channel = bot.get_channel(cid)
    if not channel:
        try: channel = await bot.fetch_channel(cid)
        except: channel = None

    if channel:
        try:
            await channel.send(t("bot_online"))
            await channel.send(t("control_panel"), view=ControlPanel())
            logger.info("Control Panel sent to channel %s", cid)
        except Exception as e:
            logger.error("Failed to send startup GUI: %s", e)
    else:
        logger.warning("No valid channel found to send GUI (cid: %s)", cid)

    # Check battery & TS
    v = read_battery()
    if v: logger.info("INA219 Ready: %.2fV", v['voltage'])

    if ts_pressed():
         await check_and_clear_ts_on_boot()

    # Start web command poller
    bot.loop.create_task(web_command_poller())

    # Auto-start web dashboard if installed (check port 5000 via socket)
    web_dash = os.path.join(REPO_DIR, "web_dashboard.py")
    if os.path.exists(web_dash):
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            port_free = sock.connect_ex(('127.0.0.1', 5000)) != 0
            sock.close()
            if port_free:
                subprocess.Popen([sys.executable, web_dash], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                logger.info("Web dashboard auto-started on boot (port 5000 was free)")
            else:
                logger.info("Web dashboard already running on port 5000")
        except Exception as e:
            logger.warning("Web dashboard auto-start check failed: %s", e)
    else:
        logger.info("Web dashboard not installed (web_dashboard.py not found)")

        # Auto-start ngrok tunnel if web dashboard installed & config exists
    if os.path.exists(web_dash) and os.path.exists(NGROK_CONFIG_FILE):
        try:
            with open(NGROK_CONFIG_FILE) as f:
                ngrok_cfg = json.load(f)
            ngrok_path = subprocess.run(["which", "ngrok"], capture_output=True, text=True, timeout=5).stdout.strip()
            if ngrok_path:
                subprocess.run(["pkill", "-f", "ngrok"], capture_output=True)
                subprocess.run([ngrok_path, "config", "add-authtoken", ngrok_cfg["auth"]], capture_output=True, timeout=10)
                subprocess.Popen([ngrok_path, "http", "--url=" + ngrok_cfg["domain"], "5000"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                logger.info("Ngrok tunnel started: %s", ngrok_cfg["domain"])
        except Exception as e:
            logger.warning("Ngrok auto-start: %s", e)

    # Start git auto-update checker
    write_shared_state(bot_version=BOT_VERSION, update_status="up_to_date")
    installer_ver = None
    if os.path.exists(DEV_PATCH_VERSION_FILE):
        with open(DEV_PATCH_VERSION_FILE) as f:
            installer_ver = f.read().strip()
        write_shared_state(installer_version=installer_ver)
    bot.loop.create_task(git_update_checker())
    logger.info("Git auto-update checker started")

@bot.event
async def on_connect():
    # Keep lang loaded on reconnects
    load_language_config()

# ----------------- Command Error Handler -----------------
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("⛔ You do not have permission to use this command.")
    else:
        logger.error("Command error: %s", error)
        try: await ctx.send(f"❌ Error: {error}")
        except: pass

@bot.command(name="defaultcon")
@owner_check()
async def cmd_defaultcon(ctx):
    """Set the current channel as the default control panel location."""
    # Delete old panel if possible (logic omitted for simplicity, just setting new)

    # Save new channel
    cfg = load_ui_config()
    cfg["channel_id"] = ctx.channel.id
    save_ui_config(cfg)

    await ctx.send("✅ This channel is now the **Control Panel Default**.")
    await ctx.send("🎛️ **Control Panel**", view=ControlPanel())


async def check_failsafe_on_start():
    global last_feed_time
    if last_feed_time is None:
        logger.info("No last feed found on start; performing failsafe feed")
        await notify_log_channel("No last feed recorded; performing initial failsafe feed")
        await scheduled_feed_job(FEED_DURATION_DEFAULT)
        return
    delta = datetime.now(timezone.utc) - last_feed_time
    if delta >= timedelta(hours=FAILSAFE_HOURS):
        logger.info("Failsafe triggered (last feed %s)", delta)
        await notify_log_channel("Failsafe: no feed recently, performing auto-feed")
        await scheduled_feed_job(FEED_DURATION_DEFAULT)


@bot.command(name="feed")
@owner_check()
async def cmd_feed(ctx, seconds: int = FEED_DURATION_DEFAULT):
    if seconds > FEED_DURATION_MAX:
        await ctx.send(t("feed_max_limit", max=FEED_DURATION_MAX))
        return
    if feeding_lock.locked():
        await ctx.send(t("already_feeding"))
        return
    ok, msg = battery_ok()
    if not ok:
        await ctx.send(t("battery_unsafe", msg=msg))
        return
    await ctx.send(t("starting_feed", seconds=seconds))
    async with feeding_lock:
        await motor_run_with_limit(seconds, forward=True, percent=100, ts_grace_period=1.5)
        await ctx.send(t("feed_complete"))


@bot.command(name="reverse")
@owner_check()
async def cmd_reverse(ctx, seconds: int = 3):
    if seconds > FEED_DURATION_MAX:
        seconds = FEED_DURATION_MAX
    if feeding_lock.locked():
        await ctx.send(t("already_feeding"))
        return
    ok, msg = battery_ok()
    if not ok:
        await ctx.send(t("battery_unsafe", msg=msg))
        return
    await ctx.send(t("reversing", seconds=seconds))
    async with feeding_lock:
        await motor_run_with_limit(seconds, forward=False, percent=100)
        await ctx.send(t("reverse_complete"))


@bot.command(name="stop")
@owner_check()
async def cmd_stop(ctx):
    stop_motor()
    await ctx.send(t("emg_stop"))
    await notify_log_channel(t("emg_stop_log", user=ctx.author))


@bot.command(name="killswitch")
@owner_check()
async def cmd_killswitch(ctx):
    """EMERGENCY: Stop all operations and restart bot."""
    await ctx.send(t("killswitch_warn"))

    def check(m):
        return m.author.id == OWNER_ID and m.channel.id == ctx.channel.id and m.content == 'RESTART'

    try:
        msg = await bot.wait_for('message', check=check, timeout=15.0)
        if msg:
            # Emergency stop motor
            stop_motor()
            await ctx.send(t("killswitch_activated"))
            await notify_log_channel(t("killswitch_log", user=ctx.author))

            # Give time for message to send
            await asyncio.sleep(1)

            # Restart service
            subprocess.run(["sudo", "systemctl", "restart", "fishfeeder.service"])
    except asyncio.TimeoutError:
        await ctx.send(t("killswitch_cancel"))


@bot.command(name="feed_force")
@owner_check()
async def cmd_feed_force(ctx, seconds: int = FEED_DURATION_DEFAULT):
    """Force feed without safety checks - requires confirmation."""
    if seconds > FEED_DURATION_MAX:
        await ctx.send(t("feed_max_limit", max=FEED_DURATION_MAX))
        return

    if feeding_lock.locked():
        await ctx.send(t("already_feeding"))
        return

    # Show current battery and TS status
    v = read_battery()
    if v:
        c_str = f"{v['current_ma']:.0f}mA" if v['current_ma'] is not None else "N/A"
        battery_msg = f"Battery: {v['voltage']:.2f}V, {c_str}"
        if v['voltage'] < BATTERY_MIN_VOLTAGE:
            battery_msg += f" ⚠️ (Below safe threshold {BATTERY_MIN_VOLTAGE:.2f}V)"
    else:
        battery_msg = "Battery: N/A"

    ts_msg = f"TS Switch: {'PRESSED' if ts_pressed() else 'Not pressed'}"

    await ctx.send(t("force_warning")) # Using simple warning, keeping complex msg in English/Partial for now or use placeholders if needed.
    # Actually, the original message was very complex. I'll rely on the simple warning + the interaction via confirm buttons in GUI.
    # But for CLI, we need proper text. Let's keep the existing complex english text for the detailed status part or localize it later.
    # For now, let's use the translated warning and the prompts.

    await ctx.send(f"{battery_msg}\n{ts_msg}\nFeed duration: {seconds}s\n\n**Safety bypasses available:**\n• Battery check - ALWAYS bypassed\n• TS limit switch - Reply 'yes ts' to also bypass\n\nReply with:\n`yes` = Bypass battery only\n`yes ts` = Bypass battery AND TS switch\n(30s timeout)")

    # Wait for confirmation
    def check(m):
        return m.author.id == OWNER_ID and m.channel.id == ctx.channel.id

    try:
        msg = await bot.wait_for('message', check=check, timeout=30.0)
        response = msg.content.lower()

        if response == 'yes' or response == 'yes ts':
            ignore_ts = (response == 'yes ts')

            await ctx.send(t("force_executing"))

            async with feeding_lock:
                await motor_run_with_limit(seconds, forward=True, percent=100, ignore_ts=ignore_ts)
                await ctx.send(t("feed_complete"))

                await notify_log_channel(t("force_log", user=ctx.author))
        else:
            await ctx.send("❌ Feed cancelled.")
    except asyncio.TimeoutError:
        await ctx.send("⏱️ Timeout - feed cancelled.")


@bot.command(name="status")
async def cmd_status(ctx):
    v = read_battery()
    pi_model = get_pi_model()
    cpu_temp = get_cpu_temp()

    s = f"{t('st_pi')}: {pi_model}\n"
    if cpu_temp is not None:
        s += f"{t('st_cpu_temp')}: {cpu_temp:.1f}°C\n"
    s += f"{t('st_motor')}: {'Feeding' if feeding_lock.locked() else 'Idle'}\n"
    s += f"{t('st_last')}: {last_feed_time.isoformat() if last_feed_time else 'Never'}\n"
    s += f"{t('st_sw')}: {'PRESSED' if ts_pressed() else 'released'}\n"
    if v:
        c_str = f"{v['current_ma']:.0f}mA" if v['current_ma'] is not None else "N/A"
        s += f"{t('st_bat')}: {v['voltage']:.2f}V | {c_str} | Shunt {v['shunt_mv']:.0f}mV"
    else:
        s += f"{t('st_bat')}: N/A"
    await ctx.send(s)


@bot.command(name="schedule_add")
@owner_check()
async def cmd_schedule_add(ctx, hh: int, mm: int, duration: int = FEED_DURATION_DEFAULT):
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        await ctx.send(t("invalid_time"))
        return
    dur = max(1, min(duration, FEED_DURATION_MAX))

    job_id = add_schedule_job(hh, mm, dur)

    await ctx.send(t("sched_cmd_added", h=hh, m=mm, d=dur))
    await notify_log_channel(t("sched_log", user=ctx.author, h=hh, m=mm))


@bot.command(name="schedule_list")
@owner_check()
async def cmd_schedule_list(ctx):
    schedules = load_json_file(SCHEDULE_FILE, [])
    if not schedules:
        await ctx.send(t("sched_list_empty"))
        return
    lines = [f"{s['id']}: {s['hour']:02d}:{s['minute']:02d} for {s.get('duration', FEED_DURATION_DEFAULT)}s" for s in schedules]
    await ctx.send(t("sched_list_title") + "\n" + "\n".join(lines))


@bot.command(name="schedule_remove")
@owner_check()
async def cmd_schedule_remove(ctx, arg1: str, arg2: str = None):
    # Handle "all" case
    if arg1.lower() == "all":
        scheduler.remove_all_jobs()
        save_json_file(SCHEDULE_FILE, [])
        await ctx.send(t("sched_cleared_all"))
        await notify_log_channel(t("sched_cleared_all"))
        return

    # Handle standard "hh mm" case
    try:
        hh = int(arg1)
        if arg2 is None:
             await ctx.send(t("sched_usage_rem"))
             return
        mm = int(arg2)
    except ValueError:
        await ctx.send(t("sched_invalid_fmt"))
        return

    job_id = f"feed_{hh:02d}{mm:02d}"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    schedules = load_json_file(SCHEDULE_FILE, [])
    schedules = [s for s in schedules if s.get("id") != job_id]
    save_json_file(SCHEDULE_FILE, schedules)
    await ctx.send(t("sched_rem_success", id=job_id))
    await notify_log_channel(t("sched_rem_log", user=ctx.author))


@bot.command(name="schedule_clear")
@owner_check()
async def cmd_schedule_clear(ctx):
    scheduler.remove_all_jobs()
    save_json_file(SCHEDULE_FILE, [])
    await ctx.send(t("sched_cleared_all"))
    await notify_log_channel(t("sched_cleared_all"))



# ----------------- graceful shutdown -----------------
def cleanup_and_exit(signum=None, frame=None):
    logger.info("Shutdown initiated (signal %s)", signum)
    try:
        scheduler.shutdown(wait=False)
    except Exception as e:
        logger.warning("Scheduler shutdown issue: %s", e)
    try:
        pwm.stop()
    except Exception:
        pass
    try:
        GPIO.cleanup()
    except Exception as e:
        logger.warning("GPIO cleanup issue: %s", e)
    os._exit(0)

signal.signal(signal.SIGTERM, cleanup_and_exit)
signal.signal(signal.SIGINT, cleanup_and_exit)

# ----------------- main -----------------
def main():
    initialize_data()
    write_shared_state(motor="IDLE", sensor="OPEN", bot_version=BOT_VERSION, update_status="up_to_date")
    set_auto_update_enabled(is_auto_update_enabled())
    v = read_battery()
    if v:
        logger.info("Startup battery: %.2fV", v['voltage'])
    try:
        scheduler.add_job(read_battery, "interval", seconds=60, id="battery_poll")
    except Exception:
        pass
    try:
        scheduler.start()
    except Exception as e:
        logger.warning("Scheduler start warning: %s", e)

    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        logger.exception("Bot run error: %s", e)
    finally:
        cleanup_and_exit()

# moved main() invocation to the end of the file


# ----------------- Authorization Management -----------------
@bot.command(name="authorize")
async def cmd_authorize(ctx, user_id: int):
    """Add a user to authorized list (OWNER ONLY - bypasses owner_check)."""
    if ctx.author.id != OWNER_ID:
        await ctx.send(t("only_owner"))
        return

    if user_id == OWNER_ID:
        await ctx.send(t("auth_owner_already"))
        return

    if user_id in authorized_users:
        await ctx.send(t("auth_already", uid=user_id))
        return

    authorized_users.add(user_id)
    save_authorized_users()
    await ctx.send(t("auth_cmd_success", uid=user_id))
    await notify_log_channel(t("auth_cmd_log", user=ctx.author, uid=user_id))


@bot.command(name="deauthorize")
async def cmd_deauthorize(ctx, user_id: int):
    """Remove a user from authorized list (OWNER ONLY - bypasses owner_check)."""
    if ctx.author.id != OWNER_ID:
        await ctx.send(t("only_owner"))
        return

    if user_id == OWNER_ID:
        await ctx.send(t("auth_cant_deauth_owner"))
        return

    if user_id not in authorized_users:
        await ctx.send(t("auth_not_in_list", uid=user_id))
        return

    authorized_users.remove(user_id)
    save_authorized_users()
    await ctx.send(t("auth_deauth_success", uid=user_id))
    await notify_log_channel(t("auth_deauth_log", user=ctx.author, uid=user_id))


@bot.command(name="authorized_list")
async def cmd_authorized_list(ctx):
    """Show all authorized users (OWNER ONLY - bypasses owner_check)."""
    if ctx.author.id != OWNER_ID:
        await ctx.send(t("only_owner"))
        return

    if not authorized_users:
        await ctx.send(t("auth_list_empty"))
        return

    msg = t("auth_list_title") + "\n"
    for uid in sorted(authorized_users):
        try:
            user = await bot.fetch_user(uid)
            msg += f"• `{uid}` - {user.name}\n"
        except:
            msg += f"• `{uid}` - {t('auth_list_unknown')}\n"

    await ctx.send(msg)


@bot.command(name="help")
async def cmd_help(ctx):
    """Show all available commands with descriptions."""
    user_id = ctx.author.id
    is_authorized = (user_id == OWNER_ID or user_id in authorized_users)

    # Build comprehensive help message - split into multiple messages to avoid Discord limit
    msg1 = "🐟 **FishFeeder Bot - Command Reference**\n\n"

    # Public Commands
    msg1 += "**📢 PUBLIC COMMANDS** _(Anyone can use)_\n"
    msg1 += "```\n"
    msg1 += "!status          - Show bot status (motor, battery, last feed)\n"
    msg1 += "!battery         - Display current battery readings\n"
    msg1 += "!checktime       - Show current time (GMT+7)\n"
    msg1 += "!checkip         - Show Raspberry Pi IP addresses\n"
    msg1 += "!wifi_status     - Show current WiFi connection status\n"
    msg1 += "!help            - Show this help message\n"
    msg1 += "```\n"

    await ctx.send(msg1)

    # Owner/Authorized Commands
    if is_authorized:
        msg2 = "**🔒 OWNER/AUTHORIZED COMMANDS** _(Privileged access)_\n\n"

        msg2 += "_Feeding Controls:_\n"
        msg2 += "```\n"
        msg2 += "!feed [seconds]        - Feed fish (default 5s, max 30s)\n"
        msg2 += "!feed_force [seconds]  - Force feed (bypasses battery checks)\n"
        msg2 += "!reverse [seconds]     - Run motor in reverse (default 3s)\n"
        msg2 += "!feed_until [timeout]  - Feed until limit switch pressed\n"
        msg2 += "!stop                  - Emergency motor stop\n"
        msg2 += "!panel                 - Summon the Control Panel GUI\n"
        msg2 += "```\n"

        await ctx.send(msg2)

        msg3 = "_Scheduling:_\n"
        msg3 += "```\n"
        msg3 += "!schedule_add <hh> <mm> [dur]  - Add daily feeding schedule\n"
        msg3 += "!schedule_remove <hh> <mm>     - Remove specific schedule\n"
        msg3 += "!schedule_remove all           - Clear all schedules\n"
        msg3 += "!schedule_list                 - List all schedules\n"
        msg3 += "!schedule_clear                - Clear all schedules\n"
        msg3 += "```\n"

        await ctx.send(msg3)

        msg4 = "_Monitoring:_\n"
        msg4 += "```\n"
        msg4 += "!battery_monitor [int] [dur]   - Real-time battery monitor\n"
        msg4 += "!battery_config [setting] [val] - Configure battery thresholds\n"
        msg4 += "!i2c_scan                      - Scan I2C bus for devices\n"
        msg4 += "!pi_info                       - Show Pi model / temp / throttling\n"
        msg4 += "```\n"

        await ctx.send(msg4)

        msg5 = "_System:_\n"
        msg5 += "```\n"
        msg5 += "!add_wifi <SSID> <PASS>  - Add and connect to WiFi network\n"
        msg5 += "!wifi_scan               - Scan for available WiFi networks\n"
        msg5 += "!update                  - Pull from Git and restart\n"
        msg5 += "!update_file             - Upload new bot file via Discord\n"
        msg5 += "!download <type>         - Download bot/config files via Discord\n"
        msg5 += "!killswitch              - Emergency stop all & restart bot\n"
        msg5 += "```\n"

        await ctx.send(msg5)

        if user_id == OWNER_ID:
            msg6 = "_Authorization (Owner Only):_\n"
            msg6 += "```\n"
            msg6 += "!authorize <user_id>       - Grant user access to owner commands\n"
            msg6 += "!deauthorize <user_id>     - Revoke user access\n"
            msg6 += "!authorized_list           - Show all authorized users\n"
            msg6 += "```\n"

            await ctx.send(msg6)

        msg7 = "💡 **Tips:**\n"
        msg7 += "• Reply 'stop' to end infinite battery monitor\n"
        msg7 += "• Reply 'RESTART' to confirm killswitch\n"
        msg7 += "• Reply 'yes' to confirm force feed"

        await ctx.send(msg7)
    else:
        msg_unauth = "_You don't have access to owner commands. Contact the bot owner for authorization._"
        await ctx.send(msg_unauth)


def battery_ok():
    global last_voltage, charging_detected, low_battery_warned

    v = read_battery()
    if not v:
        return True, "Battery N/A"

    current_voltage = v["voltage"]

    # Detect charging (voltage suddenly jumps up)
    if last_voltage is not None:
        voltage_diff = current_voltage - last_voltage

        # Charging detected: voltage jump > 0.3V
        if voltage_diff > 0.3 and not charging_detected:
            charging_detected = True
            # Send notification asynchronously
            asyncio.create_task(notify_log_channel("🔋 Charging detected"))
            logger.info(f"Charging detected: voltage jumped from {last_voltage:.2f}V to {current_voltage:.2f}V")

        # Reset charging flag if voltage drops (unplugging charger)
        elif voltage_diff < -0.2 and charging_detected:
            charging_detected = False
            asyncio.create_task(notify_log_channel("🔌 Charger disconnected"))
            logger.info(f"Charger disconnected: voltage dropped from {last_voltage:.2f}V to {current_voltage:.2f}V")

    # Update last voltage
    last_voltage = current_voltage

    # Low battery warning - tag owner
    if current_voltage < LOW_BATTERY_WARNING_THRESHOLD:
        if not low_battery_warned:
            low_battery_warned = True
            asyncio.create_task(notify_log_channel(f"⚠️ <@{OWNER_ID}> Low battery warning: {current_voltage:.2f}V (threshold: {LOW_BATTERY_WARNING_THRESHOLD:.2f}V)"))
            logger.warning(f"Low battery: {current_voltage:.2f}V")
    else:
        # Reset warning flag when battery recovers
        if low_battery_warned and current_voltage > LOW_BATTERY_WARNING_THRESHOLD + 0.2:
            low_battery_warned = False
            asyncio.create_task(notify_log_channel(f"✅ Battery recovered: {current_voltage:.2f}V"))

    # Normal battery checks
    if v["voltage"] < BATTERY_MIN_VOLTAGE:
        return False, f"Low voltage {v['voltage']:.2f}V < {BATTERY_MIN_VOLTAGE:.2f}V"
    if v["current_ma"] is not None and v["current_ma"] > BATTERY_MAX_CURRENT_MA:
        return False, f"High current {v['current_ma']:.0f}mA > {BATTERY_MAX_CURRENT_MA:.0f}mA"
    c_str = f"{v['current_ma']:.0f}mA" if v["current_ma"] is not None else "N/A"
    return True, f"{v['voltage']:.2f}V, {c_str}"

async def motor_run_until_switch(max_seconds: int, forward: bool = True, percent: int = 100):
    """Run motor until TS switch is pressed or max_seconds reached."""
    await motor_run_with_limit(max_seconds, forward=forward, percent=percent, ts_grace_period=0.0)

@bot.command(name="feed_until")
@owner_check()
async def cmd_feed_until(ctx, timeout: int = FEED_DURATION_DEFAULT):
    """Run forward until TS pressed or timeout reached."""
    if feeding_lock.locked():
        await ctx.send("Already feeding.")
        return
    ok, msg = battery_ok()
    if not ok:
        await ctx.send(f"Battery unsafe: {msg}")
        return
    await ctx.send(f"Feeding until switch or {timeout}s timeout...")
    async with feeding_lock:
        await motor_run_until_switch(timeout, forward=True, percent=100)
        await ctx.send("Feed-until complete.")

@bot.command(name="battery")
async def cmd_battery(ctx):
    if not ensure_ina_ready():
        await ctx.send(t("bat_not_available"))
        return
    v = read_battery()
    if not v:
        await ctx.send(t("bat_read_failed"))
        return

    voltage = v.get("voltage", 0)
    current_ma = v.get("current_ma")
    shunt_mv = v.get("shunt_mv", 0)

    pct_str = ""
    if BATTERY_FULL_VOLTAGE and BATTERY_EMPTY_VOLTAGE:
        try:
             pct = (voltage - BATTERY_EMPTY_VOLTAGE) / (BATTERY_FULL_VOLTAGE - BATTERY_EMPTY_VOLTAGE) * 100
             pct = max(0, min(100, pct))
             pct_str = f" (**{pct:.1f}%**)"
        except Exception:
             pass

    c_str = f"{current_ma:.0f}mA" if current_ma is not None else t("bat_current_na")
    await ctx.send(t("bat_msg", v=voltage, p=pct_str, c=c_str, s=shunt_mv))


@bot.command(name="panel")
@owner_check()
async def cmd_panel(ctx):
    """Summon the Control Panel in the current channel."""
    await ctx.send(t("control_panel"), view=ControlPanel())

@bot.command(name="dev_ui")
@owner_check()
async def cmd_dev_ui(ctx):
    """Summon the Developer Control Panel in the current channel."""
    await ctx.send("🛠️ **Developer Tools Panel**", view=DevControlPanel())

@bot.command(name="debug_io")
@owner_check()
async def cmd_debug_io(ctx):
    """Deep hardware diagnostic: GPIO, I2C, and Pi health."""
    await ctx.send("🔍 **Starting hardware diagnostic...**")

    # Check GPIO states
    try:
        ina_status = "ENABLED" if ensure_ina_ready() else "NOT FOUND"
        ts_status = "PRESSED" if ts_pressed() else "Open"
        cpu_temp = get_cpu_temp()
        throttled = get_throttled_flags()

        msg = f"**System Report:**\n"
        msg += f"- Pi Model: `{get_pi_model()}`\n"
        msg += f"- CPU Temp: `{cpu_temp:.1f}°C`\n" if cpu_temp is not None else "- CPU Temp: `N/A`\n"
        msg += f"- Throttled: `{throttled}`\n" if throttled else "- Throttled: `N/A`\n"
        msg += f"- INA219: `{ina_status}`\n"
        msg += f"- TS Switch: `{ts_status}`\n"

        if throttled:
            decoded = decode_throttled_flags(throttled)
            if decoded:
                msg += "- Throttle flags: " + ", ".join(decoded) + "\n"
            else:
                msg += "- Throttle flags: None\n"

        # Check I2C bus manually
        i2c_check = subprocess.run(["i2cdetect", "-y", "1"], capture_output=True, text=True)
        msg += f"\n**I2C Scan (-y 1):**\n```\n{i2c_check.stdout[:500]}\n```"

        # Check GPIO pins
        pins = [PIN_ENA, PIN_IN1, PIN_IN2, TS_PIN]
        msg += "\n**GPIO Configuration:**\n"
        for p in pins:
            msg += f"• Pin {p}: Level {'HIGH' if GPIO.input(p) else 'LOW'}\n"

        await ctx.send(msg)
    except Exception as e:
        await ctx.send(f"❌ Diagnostic failed: {e}")

@bot.command(name="battery_config")
@owner_check()
async def cmd_battery_config(ctx, setting: str = None, value: float = None):
    """View or change battery threshold settings."""
    global BATTERY_MIN_VOLTAGE, BATTERY_MAX_CURRENT_MA, LOW_BATTERY_WARNING_THRESHOLD, BATTERY_FULL_VOLTAGE, BATTERY_EMPTY_VOLTAGE

    # No arguments - show current config
    if setting is None:
        msg = f"{t('bat_conf_header')}\n```\n"
        msg += f"Safety Cutoff:       {BATTERY_MIN_VOLTAGE:.2f}V\n"
        msg += f"Warning Threshold:   {LOW_BATTERY_WARNING_THRESHOLD:.2f}V\n"
        msg += f"Max Current:         {BATTERY_MAX_CURRENT_MA:.0f}mA\n"
        msg += f"100% Ref Voltage:    {f'{BATTERY_FULL_VOLTAGE:.2f}V' if BATTERY_FULL_VOLTAGE else 'Not Set'}\n"
        msg += f"0% Ref Voltage:      {f'{BATTERY_EMPTY_VOLTAGE:.2f}V' if BATTERY_EMPTY_VOLTAGE else 'Not Set'}\n"
        msg += "```\n"
        msg += t("bat_conf_pct_on") if BATTERY_FULL_VOLTAGE and BATTERY_EMPTY_VOLTAGE else t("bat_conf_pct_off")
        await ctx.send(msg)
        return

    if value is None:
        await ctx.send(t("bat_conf_usage"))
        return

    setting = setting.lower()

    # ponytail: dict-driven config map avoids five near-identical branches
    config_map = {
        ("min_voltage", "min", "cutoff"): ("BATTERY_MIN_VOLTAGE", 0, 15, "Min/Cutoff", "min_voltage", "{:.2f}V"),
        ("warning", "warn"): ("LOW_BATTERY_WARNING_THRESHOLD", 0, 15, "Warning", "warning", "{:.2f}V"),
        ("max_current", "maxcurrent", "current"): ("BATTERY_MAX_CURRENT_MA", 0, 10000, "Max Current", "max_current", "{:.0f}mA"),
        ("full", "full_voltage", "max"): ("BATTERY_FULL_VOLTAGE", 0, 15, "100% Ref", "full_voltage", "{:.2f}V"),
        ("empty", "empty_voltage", "low"): ("BATTERY_EMPTY_VOLTAGE", 0, 15, "0% Ref", "empty_voltage", "{:.2f}V"),
    }

    for aliases, (var_name, v_min, v_max, label, log_key, fmt) in config_map.items():
        if setting in aliases:
            if value < v_min or value > v_max:
                await ctx.send(t("bat_conf_invalid_v") + (f" ({v_min}-{v_max})" if v_max != 15 else ""))
                return
            old_val = globals()[var_name]
            if old_val is None:
                old_val = 0
            new_val = int(value) if var_name == "BATTERY_MAX_CURRENT_MA" else value
            globals()[var_name] = new_val
            save_battery_config()
            await ctx.send(t("bat_conf_updated", setting=label, old=fmt.format(old_val), new=fmt.format(new_val)))
            await notify_log_channel(t("bat_conf_updated", setting=log_key, old=fmt.format(old_val), new=fmt.format(new_val)))
            return

    await ctx.send(t("bat_conf_unknown", setting=setting))

@bot.command(name="battery_monitor")
@owner_check()
async def cmd_battery_monitor(ctx, interval: float = 1.0, duration: int = 30):
    """Monitor battery in real-time - updates to log channel.

    Args:
        interval: Seconds between updates (default 1.0, min 0.1)
        duration: Total duration in seconds (default 30, set to 0 for infinite)

    Usage:
        !battery_monitor           -> Monitor for 30s, updating every 1s
        !battery_monitor 0.5       -> Monitor for 30s, updating every 0.5s
        !battery_monitor 2 60      -> Monitor for 60s, updating every 2s
        !battery_monitor 1 0       -> Monitor infinitely, updating every 1s (Reply 'stop' to end)
    """
    # Validate interval
    if interval < 0.1:
        interval = 0.1
    if interval > 60:
        interval = 60

    infinite_mode = (duration == 0)

    if not ensure_ina_ready():
        await ctx.send("Battery: INA219 not available.")
        return

    # Get log channel
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        await ctx.send("❌ Log channel not found.")
        return

    if infinite_mode:
        await ctx.send(t("bat_mon_start_inf", int=interval))
        iterations = None  # Infinite
    else:
        iterations = int(duration / interval)
        await ctx.send(t("bat_mon_start", dur=duration, int=interval))

    # Create initial message in log channel
    msg = await log_channel.send(t("bat_mon_header"))

    # Monitor state
    monitoring = True
    i = 0
    stop_task = None

    def check_stop(m):
        return m.author.id == OWNER_ID and m.channel.id == ctx.channel.id and m.content.lower() == 'stop'

    # Start listening for stop command if infinite mode
    if infinite_mode:
        stop_task = asyncio.create_task(bot.wait_for('message', check=check_stop))

    try:
        while monitoring:
            # Check if stop message received (infinite mode only)
            if infinite_mode and stop_task and stop_task.done():
                monitoring = False
                await ctx.send(t("bat_mon_stop"))
                break

            # Check if duration elapsed (finite mode)
            if not infinite_mode and i >= iterations:
                monitoring = False
                break

            # Read battery
            v = read_battery()
            if v:
                voltage = v.get("voltage", 0)
                current_ma = v.get("current_ma", 0)
                shunt_mv = v.get("shunt_mv", 0)

                # Format message
                text = f"{t('bat_mon_title')}\n"
                text += f"```\n"
                text += f"Voltage:  {voltage:.3f}V\n"
                text += f"Current:  {current_ma:>6.0f}mA\n"
                text += f"Shunt:    {shunt_mv:>6.1f}mV\n"
                text += f"Address:  0x{ina_addr:02x}\n" if ina_addr is not None else "Address: N/A\n"
                text += f"Interval: {interval}s\n"
                text += f"```\n"

                if infinite_mode:
                    text += f"{t('bat_mon_update', i=i+1)}\n"
                    elapsed = (i + 1) * interval
                    text += t("bat_mon_elapsed", s=f"{elapsed:.1f}")
                else:
                    # Progress bar for finite mode
                    progress = int((i + 1) / iterations * 20)
                    bar = "█" * progress + "░" * (20 - progress)
                    text += f"Progress: [{bar}] {i+1}/{iterations}\n"
                    rem = duration - ((i + 1) * interval)
                    text += t("bat_mon_time_rem", s=f"{rem:.1f}")

                await msg.edit(content=text)
            else:
                await msg.edit(content=f"🔋 Battery Monitor - Read failed at update #{i+1}")

            i += 1
            await asyncio.sleep(interval)

        # Cancel stop task if still running
        if stop_task and not stop_task.done():
            stop_task.cancel()

        # Final message
        if infinite_mode:
            await msg.edit(content=f"{msg.content}\n\n{t('bat_mon_stopped', i=i)}")
        else:
            await msg.edit(content=f"{msg.content}\n\n{t('bat_mon_complete')}")

        await ctx.send(t("bat_mon_finished"))

    except Exception as e:
        # Cancel stop task on error
        if stop_task and not stop_task.done():
            stop_task.cancel()
        await ctx.send(t("bat_mon_error", e=e))
        await msg.edit(content=f"{msg.content}\n\n❌ **Monitoring stopped due to error**")


@bot.command(name="i2c_scan")
async def cmd_i2c_scan(ctx):
    try:
        out = subprocess.run(["i2cdetect", "-y", "1"], capture_output=True, text=True, timeout=5)
        if out.returncode != 0:
            await ctx.send(f"i2cdetect error: {out.stderr[:200]}")
            return
        text = out.stdout
        # Trim if very long
        if len(text) > 1800:
            text = text[:1800] + "\n...trimmed..."
        await ctx.send(f"```\n{text}\n```")
    except Exception as e:
        await ctx.send(f"i2c_scan failed: {e}")

@bot.command(name="update_file")
@owner_check()
async def cmd_update_file(ctx):
    """Update bot by uploading new Python file via Discord."""
    if aiohttp is None:
        await ctx.send("❌ aiohttp library not installed. Run: pip install aiohttp")
        return

    await ctx.send("📤 Please upload the new fishfeeder_bot.py file (60 second timeout)...")

    def check(m):
        return m.author.id == OWNER_ID and len(m.attachments) > 0

    try:
        msg = await bot.wait_for('message', check=check, timeout=60.0)
        attachment = msg.attachments[0]

        if not attachment.filename.endswith('.py'):
            await ctx.send("❌ Please upload a .py file!")
            return

        await ctx.send(f"⬇️ Downloading {attachment.filename}...")

        # Download the file
        async with aiohttp.ClientSession() as session:
            async with session.get(attachment.url) as resp:
                if resp.status == 200:
                    new_code = await resp.text()

                    # Basic validation
                    if 'import discord' not in new_code or 'bot.run' not in new_code:
                        await ctx.send("⚠️ File doesn't look like valid bot code! Update cancelled.")
                        return

                    # SAFETY VALIDATION
                    await ctx.send("🔍 **Running Multi-Stage Safety Check...**")
                    ok, msg = validate_code(new_code)

                    if not ok:
                        await ctx.send(f"❌ **SAFETY BLOCK!** New file rejected.\nReason:\n```\n{msg}\n```\n⚠️ System untouched.")
                        await notify_log_channel(f"⚠️ File upload blocked by safety guard: {msg}")
                        return

                    await ctx.send("✅ Safety checks passed!")

                    # Backup old file
                    try:
                        backup_path = __file__ + ".backup"
                        shutil.copy(__file__, backup_path)
                        logger.info(f"Backed up current file to {backup_path}")
                    except Exception as e:
                        logger.warning(f"Backup failed: {e}")

                    # Write new file
                    with open(__file__, 'w') as f:
                        f.write(new_code)

                    await ctx.send("✅ File updated! Restarting bot...")
                    await notify_log_channel(f"🔄 File update from {ctx.author} - restarting")

                    # Restart
                    subprocess.run(["sudo", "systemctl", "restart", "fishfeeder.service"])
                else:
                    await ctx.send(f"❌ Failed to download file: HTTP {resp.status}")

    except asyncio.TimeoutError:
        await ctx.send("⏱️ Timeout - no file received within 60 seconds")
    except Exception as e:
        logger.exception(f"Error in update_file: {e}")
        await ctx.send(f"❌ Error: {e}")

@bot.command(name="checktime")
async def cmd_checktime(ctx):
    tz = timezone(timedelta(hours=7))
    now = datetime.now(tz)
    await ctx.send(t("check_time", time=now.strftime('%Y-%m-%d %H:%M:%S')))


@bot.command(name="checkip")
async def cmd_checkip(ctx):
    """Show Raspberry Pi IP addresses."""
    try:
        result = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            ips = result.stdout.strip()
            if ips:
                await ctx.send(t("check_ip", ips=ips))
            else:
                await ctx.send(t("no_ip"))
        else:
            await ctx.send(f"❌ Command failed: {result.stderr[:200]}")
    except subprocess.TimeoutExpired:
        await ctx.send("❌ Command timed out")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")


@bot.command(name="add_wifi")
@owner_check()
async def cmd_add_wifi(ctx, ssid: str, password: str):
    """Add and connect to a new WiFi network with fallback support.

    Usage: !add_wifi <SSID> <PASSWORD>

    This command will:
    1. Backup the current network connection
    2. Attempt to connect to the new network
    3. Test the connection
    4. If failed, restore the previous network
    5. Alert the user of the result
    """
    if not ssid or not password:
        await ctx.send("❌ Usage: `!add_wifi <SSID> <PASSWORD>`")
        return

    await ctx.send(t("wifi_adding", ssid=ssid))
    await ctx.send(t("wifi_backup"))

    # Backup current network
    last_network = backup_current_network()
    if last_network:
        await ctx.send(t("wifi_backed_up", ssid=last_network))
    else:
        await ctx.send(t("wifi_no_backup"))

    await ctx.send(t("wifi_connecting"))

    # Try to connect to new network
    if connect_to_wifi(ssid, password):
        await ctx.send(t("wifi_testing"))

        # Test the connection
        if test_wifi_connection():
            await ctx.send(t("wifi_connected", ssid=ssid))
            await notify_log_channel(t("wifi_conn_log", user=ctx.author, ssid=ssid))

            # Save new network as current
            wifi_config = load_wifi_config()
            wifi_config["current_network"] = ssid
            save_wifi_config(wifi_config)
        else:
            await ctx.send(t("wifi_test_fail"))
            await ctx.send(t("wifi_restoring"))

            # Fallback to last network
            if restore_last_network():
                await ctx.send(t("wifi_restored", ssid=last_network))
                await notify_log_channel(t("wifi_restored_log", user=ctx.author, ssid=ssid, last=last_network))
            else:
                await ctx.send(t("wifi_restored_fail"))
                await notify_log_channel(t("wifi_restored_fail_log", user=ctx.author))
    else:
        await ctx.send(t("wifi_connect_fail"))
        await ctx.send(t("wifi_restoring"))

        # Fallback to last network
        if last_network and restore_last_network():
            await ctx.send(t("wifi_restored", ssid=last_network))
            await notify_log_channel(t("wifi_restored_log", user=ctx.author, ssid=ssid, last=last_network))
        else:
            await ctx.send(t("wifi_restored_fail"))
            await notify_log_channel(t("wifi_restored_fail_log", user=ctx.author))


@bot.command(name="wifi_status")
async def cmd_wifi_status(ctx):
    """Show current WiFi connection status."""
    current_ssid = get_current_wifi_ssid()
    if current_ssid:
        if test_wifi_connection():
            await ctx.send(t("wifi_status_conn", ssid=current_ssid))
        else:
            await ctx.send(t("wifi_status_no_inet", ssid=current_ssid))
    else:
        await ctx.send(t("wifi_status_disconn"))


@bot.command(name="wifi_scan")
@owner_check()
async def cmd_wifi_scan(ctx):
    """Scan for available WiFi networks."""
    await ctx.send("🔍 Scanning for WiFi networks...")

    networks = scan_wifi_networks()
    if networks:
        # Limit to first 20 networks to avoid message length issues
        display_networks = networks[:20]
        network_list = "\n".join([f"• `{ssid}`" for ssid in display_networks])

        if len(networks) > 20:
            network_list += f"\n... and {len(networks) - 20} more networks"

        await ctx.send(f"📡 **Available Networks ({len(networks)} found):**\n{network_list}")
    else:
        await ctx.send("❌ No networks found or scan failed")


@bot.command(name="changelang", aliases=["set_lang", "lang"])
async def cmd_changelang(ctx, lang: str = None):
    """Change bot language (en, th, zh)."""
    if not lang:
        await ctx.send(t("cmd_lang_desc"))
        return

    lang = lang.lower()
    if lang in TRANSLATIONS:
        save_language_config(lang)
        await ctx.send(t("lang_set", lang=lang))
        # Redeploy control panel if this was in the control panel channel
        # We can't easily know if we should redeploy, but maybe just send a message saying "Please restart or reload panel"
        # Or better yet, just let the user know.

        # If the user is in the log channel, we might want to refresh the panel.
        # But for now just confirming is enough.
    else:
        await ctx.send(t("lang_invalid"))


@bot.command(name="pi_info")
@owner_check()
async def cmd_pi_info(ctx):
    """Show Raspberry Pi hardware details and thermal state."""
    pi_model = get_pi_model()
    cpu_temp = get_cpu_temp()
    throttled = get_throttled_flags()

    msg = f"{t('pi_info_title')}\n"
    msg += f"{t('pi_info_model')} `{pi_model}`\n"
    msg += f"{t('pi_info_temp')} `{cpu_temp:.1f}°C`\n" if cpu_temp is not None else f"{t('pi_info_temp')} `{t('pi_info_na')}`\n"

    if throttled:
        msg += f"{t('pi_info_throttled')} `{throttled}`\n"
        decoded = decode_throttled_flags(throttled)
        if decoded:
            msg += f"{t('pi_info_throttle_flags')}\n" + "\n".join(f"• {flag}" for flag in decoded)
        else:
            msg += f"{t('pi_info_throttle_flags')} {t('pi_info_none')}"
    else:
        msg += f"{t('pi_info_throttled')} `{t('pi_info_na')}`"

    await ctx.send(msg)


@bot.command(name="download")
@owner_check()
async def cmd_download(ctx, filetype: str = None):
    """Download bot files or config via Discord.

    Usage:
        !download              - Show available types
        !download bot          - Current bot script
        !download backup       - Last backup file
        !download config       - All config files (zip)
        !download all          - Bot + backup + all configs (zip)
        !download schedules    - schedules.json
        !download state        - state.json
        !download authorized   - authorized_users.json
        !download battery      - battery_config.json
        !download wifi         - wifi_config.json
        !download ui           - ui_config.json
        !download devpatch     - dev_patch_installer.py
    """
    if filetype is None:
        await ctx.send(f"{t('download_title')}\n{t('download_usage')}")
        return

    filetype = filetype.lower()

    # Map short names to config file paths
    config_map = {
        "schedules": SCHEDULE_FILE,
        "state": STATE_FILE,
        "authorized": AUTHORIZED_FILE,
        "battery": BATTERY_CONFIG_FILE,
        "wifi": WIFI_CONFIG_FILE,
        "ui": UI_CONFIG_FILE,
        "devpatch": DEV_PATCH_INSTALLER,
    }

    try:
        if filetype == "bot":
            # Send the current bot script
            bot_path = __file__
            if not os.path.exists(bot_path):
                await ctx.send(t("download_not_found", filename="fishfeeder_bot.py"))
                return
            await ctx.send(t("download_bot"))
            await ctx.send(file=discord.File(bot_path, filename="fishfeeder_bot.py"))
            await ctx.send(t("download_file_sent", filename="fishfeeder_bot.py"))
            await notify_log_channel(t("download_log", user=ctx.author, what="bot script"))

        elif filetype == "backup":
            # Send the backup file if it exists
            backup_path = __file__ + ".backup"
            if not os.path.exists(backup_path):
                await ctx.send(t("download_no_backup"))
                return
            await ctx.send(t("download_backup"))
            await ctx.send(file=discord.File(backup_path, filename="fishfeeder_bot.py.backup"))
            await ctx.send(t("download_file_sent", filename="fishfeeder_bot.py.backup"))
            await notify_log_channel(t("download_log", user=ctx.author, what="backup file"))

        elif filetype == "config":
            # Zip all config files
            await ctx.send(t("download_config"))
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                for name, path in config_map.items():
                    if os.path.exists(path):
                        zf.write(path, os.path.basename(path))
            zip_buffer.seek(0)
            await ctx.send(file=discord.File(zip_buffer, filename="config_files.zip"))
            await ctx.send(t("download_file_sent", filename="config_files.zip"))
            await notify_log_channel(t("download_log", user=ctx.author, what="config zip"))

        elif filetype == "all":
            # Zip bot + backup + all config files
            await ctx.send(t("download_all"))
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                # Bot script
                if os.path.exists(__file__):
                    zf.write(__file__, "fishfeeder_bot.py")
                # Backup
                backup_path = __file__ + ".backup"
                if os.path.exists(backup_path):
                    zf.write(backup_path, "fishfeeder_bot.py.backup")
                # Config files
                for name, path in config_map.items():
                    if os.path.exists(path):
                        zf.write(path, os.path.basename(path))
            zip_buffer.seek(0)
            await ctx.send(file=discord.File(zip_buffer, filename="fishfeeder_all.zip"))
            await ctx.send(t("download_file_sent", filename="fishfeeder_all.zip"))
            await notify_log_channel(t("download_log", user=ctx.author, what="all files zip"))

        elif filetype in config_map:
            # Send a specific config file
            path = config_map[filetype]
            filename = os.path.basename(path)
            if not os.path.exists(path):
                await ctx.send(t("download_not_found", filename=filename))
                return
            await ctx.send(t("download_bot"))
            await ctx.send(file=discord.File(path, filename=filename))
            await ctx.send(t("download_file_sent", filename=filename))
            await notify_log_channel(t("download_log", user=ctx.author, what=filename))

        else:
            await ctx.send(f"❌ Unknown type `{filetype}`.\n{t('download_usage')}")

    except Exception as e:
        logger.exception("Download error: %s", e)
        await ctx.send(t("download_error", e=e))

# ----------------- Dev Patch Upload -----------------
@bot.command(name="devpatchupload")
async def cmd_devpatch_upload(ctx, action: str = None):
    """Upload a dev patch installer to install developer tools (web dashboard, HDMI GUI, ngrok)."""
    if action is None:
        await ctx.send("📤 **Dev Patch Uploader**\n"
                       "`!devpatchupload install` — upload and run the installer\n"
                       "`!devpatchupload get` — download the dev patch installer file")
        return

    if action == "get":
        if os.path.exists(DEV_PATCH_INSTALLER):
            await ctx.send(file=discord.File(DEV_PATCH_INSTALLER, filename="dev_patch_installer.py"))
        else:
            await ctx.send("❌ Installer file not found on the Pi. You can download it from the repo.")
        return

    if action == "install":
        if ctx.author.id != OWNER_ID:
            await ctx.send("⛔ Owner only.")
            return

        await ctx.send("📤 Upload the dev patch installer `.py` file (60s timeout)...")

        def check(m):
            return m.author.id == OWNER_ID and len(m.attachments) > 0

        try:
            msg = await bot.wait_for("message", check=check, timeout=60.0)
            attachment = msg.attachments[0]

            if not attachment.filename.endswith(".py"):
                await ctx.send("❌ Must be a .py file!")
                return

            if aiohttp is None:
                await ctx.send("❌ aiohttp not available.")
                return

            async with aiohttp.ClientSession() as session:
                async with session.get(attachment.url) as resp:
                    if resp.status != 200:
                        await ctx.send("❌ Download failed.")
                        return
                    code = await resp.text()

            # Validate
            if "import discord" not in code or "bot.run" not in code:
                await ctx.send("⚠️ Not a valid dev patch installer.")
                return

            ok, err = validate_code(code)
            if not ok:
                await ctx.send(f"❌ Safety block: {err}")
                return

            # Backup current bot
            shutil.copy(__file__, __file__ + ".backup")
            logger.info(f"Backed up to {__file__}.backup")

            # Write uploaded installer as the new bot
            with open(__file__, "w") as f:
                f.write(code)

            await ctx.send("✅ Dev patch uploaded! Restarting to activate installer...")
            await notify_log_channel(f"🛠️ Dev patch uploaded by {ctx.author}")
            subprocess.run(["sudo", "systemctl", "restart", "fishfeeder.service"])

        except asyncio.TimeoutError:
            await ctx.send("⏱️ Timeout.")

    else:
        await ctx.send(f"❌ Unknown action `{action}`. Use `install` or `get`.")


# -----------------------------------------
# !update command group
# -----------------------------------------

@owner_check()
@bot.group(name="update", invoke_without_command=True)
async def cmd_update(ctx):
    """Manage auto-update system."""
    await ctx.send_help(ctx.command)

@cmd_update.command(name="status")
async def update_status(ctx):
    """Show current update version and status."""
    s = load_json_file(SHARED_STATE_FILE, {})
    enabled = is_auto_update_enabled()
    last_check = s.get("last_update_check", 0)
    last_upd = s.get("last_updated", 0)
    check_ts = datetime.fromtimestamp(last_check).strftime("%H:%M %d/%m") if last_check else "Never"
    upd_ts = datetime.fromtimestamp(last_upd).strftime("%H:%M %d/%m") if last_upd else "Never"
    installer_ver = s.get("installer_version")
    if not installer_ver and os.path.exists(DEV_PATCH_VERSION_FILE):
        with open(DEV_PATCH_VERSION_FILE) as f:
            installer_ver = f.read().strip()
    patch_line = f"\n**Patch Version:** v{installer_ver}" if installer_ver else ""
    await ctx.send(
        f"**Bot Version:** v{BOT_VERSION}{patch_line}\n"
        f"**Auto-Update:** {'✅ ON' if enabled else '❌ OFF'}\n"
        f"**Status:** {s.get('update_status', 'unknown')}\n"
        f"**Last Check:** {check_ts}\n"
        f"**Last Update:** {upd_ts}"
    )

@cmd_update.command(name="check")
async def update_check(ctx):
    """Force check for updates now."""
    await ctx.send("🔍 Checking for updates...")
    try:
        r = _git("fetch", GIT_REMOTE, GIT_BRANCH, timeout=30)
        if r.returncode != 0:
            await ctx.send(f"❌ git fetch failed: {r.stderr}")
            return
        r2 = _git("rev-list", "--count", f"HEAD..{GIT_REMOTE}/{GIT_BRANCH}")
        behind = int(r2.stdout.strip()) if r2.stdout.strip() else 0
        if behind > 0:
            await ctx.send(f"📥 {behind} new commit(s). Auto-updating...")
            await process_git_update()
        else:
            write_shared_state(update_status="up_to_date", last_update_check=time.time())
            await ctx.send("✅ Already up to date.")
    except Exception as e:
        await ctx.send(f"❌ Update check failed: {e}")

@cmd_update.command(name="toggle")
async def update_toggle(ctx):
    """Enable/disable auto-update."""
    enabled = not is_auto_update_enabled()
    set_auto_update_enabled(enabled)
    await ctx.send(f"{'✅' if enabled else '❌'} Auto-update {'enabled' if enabled else 'disabled'}")


@owner_check()
@bot.command(name="shutdown")
async def cmd_shutdown(ctx):
    stop_motor()
    await ctx.send("\U0001F4A4 Shutting down Pi...")
    subprocess.run(["sudo", "poweroff"])

if __name__ == "__main__":
    main()
