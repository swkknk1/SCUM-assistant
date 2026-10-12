# -*- coding: utf-8 -*-
"""
====================================================================================================
项目名称: SCUM 专用服务器智能伴侣助手 (SCUM Server Companion Assistant)
许可协议: CC BY-NC-SA 4.0 (知识共享: 署名-非商业性使用-相同方式共享，严禁商用倒卖)
适用游戏: SCUM (人渣) 专用独立服务端 (Dedicated Server)
运行环境: Windows 10 / 11 / Windows Server (Python 3.8+)
依赖说明: 100% 纯原生 Python 标准库，零第三方外部库依赖 (无需 pip install 任何包)
====================================================================================================

【项目核心原理与架构】
1. 非侵入式工作机制: 
   - 不修改游戏内存、不注入 DLL、不破坏 EAC 反作弊，保证服务器与服主账号 100% 安全。
2. 实时日志流监听 (Log Tailer): 
   - 采用多线程非阻塞游标监听 SCUM 服务端 UTF-16LE 编码的日志文件 (chat_*.log, login_*.log, kill_*.log)。
3. 本地 SQLite 无锁只读查询 (Database Reader): 
   - 利用 SQLite URI 'mode=ro' 只读模式直接读取 SCUM.db，实时精准获取玩家三维坐标与银行现金余额，
     完全避免与运行中的服务端发生数据库文件写锁冲突。
4. 虚幻引擎硬件扫描码级键鼠模拟 (DirectInput Simulator): 
   - 针对虚幻引擎 4 (Unreal Engine 4) DirectInput 输入机制，结合硬件扫描码 (Scan Code)、
     窗口激活状态检测、剪贴板重试机制与精确延时，毫秒级将管理员指令打入游戏聊天框。
5. 全功能自定义路由系统 (Custom Command Router): 
   - 所有玩家聊天指令均支持在 assistant_config.json 中自定义，可自由配置触发词 (如带感叹号 !p 或不带感叹号 p)、
     是否启用、价格、车辆型号以及通报模板。

====================================================================================================
【代码结构索引】
- [模块 1/8] Win32 底层 API 与虚幻引擎硬件扫描码定义 (Win32 & DirectInput)
- [模块 2/8] 配置文件、家标记与玩家数据持久化 (Config & Storage)
- [模块 3/8] SCUM 本地 SQLite 数据库只读接口 (Database Reader)
- [模块 4/8] 武器名称美化与本地化字典 (Weapon Localization)
- [模块 5/8] 在线玩家会话与数字编号管理 (Player & Session Manager)
- [模块 6/8] 核心指令解析器与业务逻辑分发 (Command Dispatcher & Router)
- [模块 7/8] 日志文件流式监控线程 (Log File Tailer & Event Watcher)
- [模块 8/8] 后台执行队列、热键控制与主程序入口 (Execution Engine & Main)
====================================================================================================
"""

import os
import sys
import time
import json
import re
import glob
import sqlite3
import threading
import winsound
import ctypes
from ctypes import wintypes


# ==================================================================================================
# [模块 1/8] Win32 底层 API 与虚幻引擎硬件扫描码定义
# ==================================================================================================
# 说明：虚幻引擎与大多数 3D 游戏采用 DirectInput 读取键盘输入，常规的虚拟按键 (Virtual Key)
# 容易被游戏忽略。此处必须提供键盘硬件扫描码 (Hardware Scan Code) 才能 100% 稳定生效。

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Win32 内存与剪贴板函数签名
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.keybd_event.argtypes = [ctypes.c_byte, ctypes.c_byte, ctypes.c_ulong, ctypes.c_void_p]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

# 常用虚拟键码 (Virtual Key Codes)
VK_T = 0x54
VK_CONTROL = 0x11
VK_V = 0x56
VK_RETURN = 0x0D
VK_F1 = 0x70       # F1: 手动确认执行队列中下一条指令
VK_F2 = 0x71       # F2: 一键切换全自动秒发 / F1手动确认模式

# 虚幻引擎 DirectInput 硬件扫描码 (Hardware Scan Codes)
SCAN_T = 0x14      # T 键 (默认游戏聊天框热键)
SCAN_CTRL = 0x1D   # Ctrl 键
SCAN_A = 0x1E      # A 键 (全选)
SCAN_V = 0x2F      # V 键 (粘贴)
SCAN_BACK = 0x0E   # Backspace 退格键 (清空残留字符)
SCAN_ENTER = 0x1C  # 回车键 (发送并关闭聊天框)


def set_clipboard_text(text: str) -> bool:
    """
    将指定的 Unicode 文本安全写入 Windows 系统剪贴板 (带重试保护)。
    :param text: 待写入的指令或公告文本
    :return: 写入成功返回 True，失败返回 False
    """
    for _ in range(5):
        if user32.OpenClipboard(None):
            try:
                user32.EmptyClipboard()
                # Windows 剪贴板 Unicode 格式 (CF_UNICODETEXT = 13)，必须以 null 字符结尾并使用 UTF-16LE 编码
                encoded = (text + '\0').encode('utf-16le')
                # GMEM_MOVEABLE (0x0002) | GMEM_ZEROINIT (0x0040) = 0x0042
                h_mem = kernel32.GlobalAlloc(0x0042, len(encoded))
                if h_mem:
                    ptr = kernel32.GlobalLock(h_mem)
                    ctypes.memmove(ptr, encoded, len(encoded))
                    kernel32.GlobalUnlock(h_mem)
                    user32.SetClipboardData(13, h_mem)
                    return True
            finally:
                user32.CloseClipboard()
        time.sleep(0.02)
    return False


def is_scum_active() -> bool:
    """
    检测当前屏幕处于最前台焦点的窗口是否为 SCUM 游戏客户端。
    避免服主切到微信/浏览器时误将按键发送到其他窗口。
    """
    hwnd = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(hwnd)
    if length > 0:
        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buff, length + 1)
        title = buff.value.lower()
        # 排除包含 "server"、"assistant"、"cmd" 等控制台标题
        if "scum" in title and "server" not in title and "assistant" not in title and "cmd" not in title:
            return True
    return False


def send_chat_command(cmd_text: str) -> bool:
    """
    模拟键盘硬件操作将指令自动输入 SCUM 游戏聊天框并回车执行。
    工作流程:
      1. 写入剪贴板
      2. 模拟按下 T 键打开聊天框
      3. 延时等待游戏 UI 完成渲染与输入焦点获取 (0.22s)
      4. Ctrl + A 然后 Backspace 清空输入框中的所有残留内容 (如被游戏误吞的 t)
      5. Ctrl + V 粘贴指令
      6. 回车提交执行 (保持 80ms 确保必被捕获)
    """
    if not set_clipboard_text(cmd_text):
        print(f"[错误] 剪贴板写入失败，无法发送: {cmd_text}")
        return False

    # 1. 模拟按 T 打开聊天框
    user32.keybd_event(VK_T, SCAN_T, 0, 0)
    time.sleep(0.04)
    user32.keybd_event(VK_T, SCAN_T, 2, 0)  # KEYEVENTF_KEYUP = 2

    # 关键延时：虚幻引擎渲染聊天 UI 动画并获取焦点需要约 180~250ms
    time.sleep(0.22)

    # 2. 全选并删除输入框残留文本 (Ctrl + A -> Backspace)
    user32.keybd_event(VK_CONTROL, SCAN_CTRL, 0, 0)
    user32.keybd_event(0x41, SCAN_A, 0, 0)
    time.sleep(0.03)
    user32.keybd_event(0x41, SCAN_A, 2, 0)
    user32.keybd_event(VK_CONTROL, SCAN_CTRL, 2, 0)
    time.sleep(0.03)

    user32.keybd_event(0x08, SCAN_BACK, 0, 0)
    time.sleep(0.03)
    user32.keybd_event(0x08, SCAN_BACK, 2, 0)
    time.sleep(0.05)

    # 3. 粘贴指令 (Ctrl + V)
    user32.keybd_event(VK_CONTROL, SCAN_CTRL, 0, 0)
    user32.keybd_event(VK_V, SCAN_V, 0, 0)
    time.sleep(0.04)
    user32.keybd_event(VK_V, SCAN_V, 2, 0)
    user32.keybd_event(VK_CONTROL, SCAN_CTRL, 2, 0)
    time.sleep(0.08)

    # 4. 回车执行 (Enter 扫描码 0x1C，保持 80ms 确保稳定关闭输入框)
    user32.keybd_event(VK_RETURN, SCAN_ENTER, 0, 0)
    time.sleep(0.08)
    user32.keybd_event(VK_RETURN, SCAN_ENTER, 2, 0)
    time.sleep(0.15)
    return True


# ==================================================================================================
# [模块 2/8] 配置文件、家标记与玩家数据持久化
# ==================================================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "assistant_config.json")
HOMES_FILE = os.path.join(BASE_DIR, "homes.json")
PLAYER_DATA_FILE = os.path.join(BASE_DIR, "player_data.json")

# 默认全局配置 (若配置文件不存在或字段缺失，将自动补齐)
DEFAULT_CONFIG = {
    # 服主 64 位 SteamID (享有免扣费等特权)
    "admin_steam_id": "76561199057523836",

    # 服务端路径 (留空表示自动使用脚本同目录下的 ./server 路径)
    # 如你的服务端在其他目录，可在此填写如: "C:\\scum_server"
    "server_dir": "",

    # 传送与回城费用 (游戏内金币)
    "teleport_cost": 4000,
    "home_teleport_cost": 4000,

    # 默认赠送每个新玩家的免费福利次数
    "free_teleport_count": 1,
    "free_home_count": 1,

    # ======【核心：全功能自定义玩家指令系统】======
    # 其他服主可以在此自由修改或扩展触发词，例如将 "!p" 改为 "p"，或者加上中文指令 "查人"
    "commands": {
        "help": {
            "enabled": True,
            "triggers": ["!help", "!cmd", "!帮助", "!menu", "!指令", "help", "帮助"],
            "description": "显示常用指令与帮助菜单"
        },
        "player_list": {
            "enabled": True,
            "triggers": ["!p", "p", "!list", "list", "!player", "!players", "!查", "查人", "在线"],
            "description": "查询所有玩家的数字编号与在线列表"
        },
        "teleport": {
            "enabled": True,
            "triggers": ["!to", "to", "!tp", "tp", "!传", "传"],
            "description": "传送到指定编号或名字的队友身旁"
        },
        "set_home": {
            "enabled": True,
            "triggers": ["!sethome", "sethome", "!sh", "sh", "!设家", "设家", "定点"],
            "description": "记录当前站立坐标为基地/家"
        },
        "home": {
            "enabled": True,
            "triggers": ["!home", "home", "!h", "h", "!回家", "回家", "回城"],
            "description": "传送回自己设置的基地坐标"
        },
        "welfare": {
            "enabled": True,
            "triggers": ["!free", "free", "!welfare", "!福利", "福利", "!免费", "免费"],
            "description": "查看自己剩余的免费传送次数"
        },
        "buy_vehicle": {
            "enabled": True,
            "triggers": ["!bc", "bc", "!buycar", "buycar", "!买车", "买车", "提车"],
            "description": "消耗游戏币一键在身边刷出满配载具"
        },
        "buy_item": {
            "enabled": True,
            "triggers": ["!buy", "buy", "!shop", "shop", "!买", "买", "!购买", "购买", "!物资", "物资"],
            "description": "消耗游戏币一键在身边购买物资/装备 (安静生成不全服通报)"
        }
    },

    # 可购买载具列表 (可自由增减、改名、改价、改模型与别名)
    "vehicles": {
        "1": {
            "name": "巡航摩托",
            "model": "BPC_Cruiser",
            "cost": 12000,
            "aliases": ["1", "moto", "cruiser", "摩托", "摩托车"]
        },
        "2": {
            "name": "雷杰皮卡",
            "model": "BPC_Rager",
            "cost": 35000,
            "aliases": ["2", "rager", "car", "pickup", "皮卡", "汽车"]
        },
        "3": {
            "name": "水陆两栖飞机",
            "model": "BPC_Kinglet_Mariner",
            "cost": 68000,
            "aliases": ["3", "plane", "mariner", "飞机", "水陆飞机"]
        }
    },

    # 可购买物资/装备商品库 (支持服主照着模板自由增减、改名、改价、改ID与别名)
    "items": {
        "1": {
            "name": "巴雷特M82重狙",
            "item_id": "Weapon_M82A1",
            "count": 1,
            "cost": 15000,
            "aliases": ["1", "m82", "巴雷特", "大狙"]
        },
        "2": {
            "name": ".50穿甲弹药箱",
            "item_id": "Cal_50_BMG_Ammobox",
            "count": 2,
            "cost": 4000,
            "aliases": ["2", "50bmg", "大狙子弹", "狙击弹"]
        },
        "3": {
            "name": "军用大急救箱",
            "item_id": "Emergency_Bandage_Big",
            "count": 2,
            "cost": 2500,
            "aliases": ["3", "med", "急救包", "绷带"]
        },
        "4": {
            "name": "高级工具箱",
            "item_id": "Tool_Box",
            "count": 1,
            "cost": 5000,
            "aliases": ["4", "toolbox", "工具箱"]
        }
    },

    # 功能开关
    "auto_execute": True,          # 是否开启全自动发送 (True: 聚焦时秒发; False: 需按 F1 确认)
    "welcome_announcement": True,  # 玩家上线全服欢迎通报
    "kill_announcement": True,     # PvP击杀与超远距离狙杀全服通报
    "announce_npc_kills": False,   # 是否播报被丧尸/NPC杀死的事件 (默认关闭防止刷屏)
    "announce_item_purchase": False, # 是否全服通报购买商品成功 (默认False关闭，安静生成不刷屏)
    "sound_alert": False           # 指令入队时是否有系统蜂鸣声提示
}


def deep_merge(default_dict: dict, user_dict: dict) -> dict:
    """递归合并字典，确保在保留用户自定义项的同时，自动补齐新增的配置字段"""
    merged = dict(default_dict)
    for k, v in user_dict.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def load_config() -> dict:
    """加载配置文件，若不存在则生成默认配置"""
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
        return DEFAULT_CONFIG
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
            return deep_merge(DEFAULT_CONFIG, user_cfg)
    except Exception as e:
        print(f"[配置警告] 加载配置文件异常，使用默认设置: {e}")
        return DEFAULT_CONFIG


def save_config(cfg: dict):
    """保存配置到文件"""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)


def load_homes() -> dict:
    """加载玩家基地/家的坐标字典"""
    if not os.path.exists(HOMES_FILE):
        return {}
    try:
        with open(HOMES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_homes(homes: dict):
    """保存玩家基地坐标"""
    with open(HOMES_FILE, "w", encoding="utf-8") as f:
        json.dump(homes, f, indent=4, ensure_ascii=False)


def load_player_data() -> dict:
    """加载玩家福利数据 (免费次数等)"""
    if not os.path.exists(PLAYER_DATA_FILE):
        return {}
    try:
        with open(PLAYER_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_player_data(data: dict):
    """保存玩家福利数据"""
    with open(PLAYER_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def get_player_perks(player_data: dict, sid: str, cfg: dict) -> dict:
    """获取指定玩家的福利数据，若首次加入则初始化默认免费次数"""
    sid = str(sid)
    admin_sid = str(cfg.get("admin_steam_id", ""))
    
    if sid not in player_data:
        # 服主拥有无限免费特权 (999999 次)，普通玩家读取配置赠送次数
        is_admin = (sid == admin_sid)
        player_data[sid] = {
            "free_to": 999999 if is_admin else cfg.get("free_teleport_count", 1),
            "free_home": 999999 if is_admin else cfg.get("free_home_count", 1)
        }
        save_player_data(player_data)
    else:
        if "free_to" not in player_data[sid]:
            player_data[sid]["free_to"] = cfg.get("free_teleport_count", 1)
        if "free_home" not in player_data[sid]:
            player_data[sid]["free_home"] = cfg.get("free_home_count", 1)
    return player_data[sid]


def resolve_paths(cfg: dict):
    """
    根据配置动态解析 SCUM 服务的日志目录与数据库路径。
    支持服主在 config 中自定义 server_dir 路径。
    """
    server_dir = cfg.get("server_dir", "").strip()
    if server_dir and os.path.isdir(server_dir):
        base_save = os.path.join(server_dir, "SCUM", "Saved", "SaveFiles")
    else:
        base_save = os.path.join(BASE_DIR, "server", "SCUM", "Saved", "SaveFiles")

    log_dir = os.path.join(base_save, "Logs")
    db_path = os.path.join(base_save, "SCUM.db")
    return log_dir, db_path


# ==================================================================================================
# [模块 3/8] SCUM 本地 SQLite 数据库只读接口
# ==================================================================================================
# 说明：SCUM 服务端在运行时会开启 WAL 模式独占写入 SCUM.db。
# 如果直接 sqlite3.connect(DB_PATH) 会导致锁库甚至引发服务端卡死。
# 必须使用 URI 模式并指定 mode=ro (Read-Only)，实现零等待、无锁并发只读查询。

def get_db_connection(db_path: str):
    """建立只读 SQLite 连接"""
    db_uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    return sqlite3.connect(db_uri, uri=True, timeout=5)


def get_player_coords(db_path: str, steam_id: str):
    """
    从 SCUM.db 数据库实时抓取玩家最新的世界空间三维坐标 (X, Y, Z)。
    :return: (x, y, z) 浮点元组，若未查询到则返回 None
    """
    try:
        conn = get_db_connection(db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT e.location_x, e.location_y, e.location_z
            FROM user_profile up
            JOIN prisoner p ON p.user_profile_id = up.id
            JOIN prisoner_entity pe ON pe.prisoner_id = p.id
            JOIN entity e ON e.id = pe.entity_id
            WHERE up.user_id = ?
            ORDER BY e.id DESC LIMIT 1;
        """, (str(steam_id),))
        row = cur.fetchone()
        conn.close()
        return row
    except Exception as e:
        print(f"[DB 错误] 获取玩家坐标失败 (SteamID: {steam_id}): {e}")
        return None


def get_player_balance(db_path: str, steam_id: str) -> int:
    """
    从 SCUM.db 数据库实时读取玩家当前的现金钱包/银行账户余额 (currency_type = 1 为通用现金)。
    :return: 整数余额金额，若查询失败则返回 0
    """
    try:
        conn = get_db_connection(db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT c.account_balance
            FROM bank_account_registry_currencies c
            JOIN user_profile up ON up.id = c.user_profile_id OR up.id = c.bank_account_id
            WHERE up.user_id = ? AND c.currency_type = 1
            LIMIT 1;
        """, (str(steam_id),))
        row = cur.fetchone()
        conn.close()
        if row is not None:
            return int(row[0])
        return 0
    except Exception as e:
        print(f"[DB 错误] 获取金钱失败 (SteamID: {steam_id}): {e}")
        return 0


# ==================================================================================================
# [模块 4/8] 武器名称美化与本地化字典
# ==================================================================================================
def clean_weapon_name(raw_name: str) -> str:
    """将 SCUM 内部武器蓝图名转为玩家熟悉的中文武器名"""
    w = raw_name.replace("Weapon_", "").replace("_C", "")
    translations = {
        "SDASS": "SDASS霰弹枪",
        "DEagle_50": "沙漠之鹰.50",
        "DEagle_357": "沙漠之鹰.357",
        "AK47": "AK-47",
        "AK15": "AK-15",
        "AKS_74U": "AKS-74U",
        "M16A4": "M16A4",
        "M1_Garand": "M1加兰德",
        "AS_Val": "巨浪AS-Val",
        "VSS_VZ": "VSS微声狙",
        "SVD_Dragunov": "SVD德拉贡诺夫",
        "AWM": "AWM重狙",
        "Hunter85": "猎枪Hunter85",
        "M82A1": "巴雷特M82A1",
        "MosinNagant": "莫辛纳甘",
        "Kar98k": "毛瑟Kar98k",
        "MP5": "MP5冲锋枪",
        "UMP45": "UMP45冲锋枪",
        "Block21": "格洛克Block21",
        "HS9": "春田HS9",
        "M9": "贝雷塔M9",
        "RPK": "RPK轻机枪",
        "SCAR": "SCAR突击步枪",
        "VHS2": "VHS-2突击步枪",
        "MK18": "MK18突击步枪",
        "Crossbow": "十字弩",
        "Bow": "反曲弓",
        "CompoundBow": "复合弓",
        "Katana": "武士刀",
        "BaseballBat": "棒球棍",
        "Sledgehammer": "大铁锤"
    }
    return translations.get(w, w)


# ==================================================================================================
# [模块 5/8] 在线玩家会话与数字编号管理
# ==================================================================================================
# 说明：游戏内玩家名字可能有特殊字符、重名或非常长，打字极不方便。
# 助手为每一个进入服务器的玩家分配简短的纯数字编号 [1], [2], [3]...，
# 传送时玩家只需输入 "!To 2" 即可秒传，大幅提升操作体验。

class PlayerManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.online_players = {}  # steam_id -> {"name": name, "num": int}
        self.num_map = {}         # num -> steam_id
        self.next_num = 1

    def add_player(self, steam_id: str, name: str) -> int:
        """注册或更新玩家在线状态，并分配数字编号"""
        with self.lock:
            sid = str(steam_id)
            if sid not in self.online_players:
                num = self.next_num
                self.next_num += 1
                self.online_players[sid] = {"name": name, "num": num}
                self.num_map[num] = sid
                return num
            else:
                self.online_players[sid]["name"] = name
                return self.online_players[sid]["num"]

    def remove_player(self, steam_id: str):
        """玩家离线时移除记录"""
        with self.lock:
            sid = str(steam_id)
            if sid in self.online_players:
                num = self.online_players[sid]["num"]
                del self.online_players[sid]
                if num in self.num_map:
                    del self.num_map[num]

    def find_player(self, query: str):
        """
        支持通过纯数字编号或玩家模糊名字进行查找。
        :return: (steam_id, player_name) 或 (None, None)
        """
        with self.lock:
            query_str = str(query).strip().lower()
            if query_str.isdigit():
                num = int(query_str)
                steam_id = self.num_map.get(num)
                if steam_id:
                    return steam_id, self.online_players[steam_id]["name"]

            # 按名字模糊匹配
            for sid, info in self.online_players.items():
                pname = info["name"].lower()
                if query_str == pname or pname.startswith(query_str) or query_str in pname:
                    return sid, info["name"]

            return None, None

    def get_list_str(self) -> str:
        """格式化当前在线玩家列表广播字符串"""
        with self.lock:
            if not self.online_players:
                return "当前暂无在线玩家"
            items = []
            for sid, info in sorted(self.online_players.items(), key=lambda x: x[1]["num"]):
                items.append(f"[{info['num']}] {info['name']}")
            return "在线玩家: " + " | ".join(items)


# ==================================================================================================
# [模块 6/8] 核心指令解析器与业务逻辑分发 (Command Dispatcher & Router)
# ==================================================================================================
class AssistantEngine:
    def __init__(self):
        self.config = load_config()
        self.homes = load_homes()
        self.player_data = load_player_data()
        self.players = PlayerManager()
        self.cmd_queue = []
        self.queue_lock = threading.Lock()
        self.running = True
        self.auto_mode = self.config.get("auto_execute", True)
        self.admin_sid = str(self.config.get("admin_steam_id", "76561199057523836"))
        self.log_dir, self.db_path = resolve_paths(self.config)

    def queue_command(self, cmd_text: str, desc: str = ""):
        """将生成的控制台/聊天命令压入待发送队列"""
        with self.queue_lock:
            self.cmd_queue.append((cmd_text, desc))
            print(f">>> [指令入队] {desc} -> {cmd_text}")
        if self.config.get("sound_alert", False):
            try:
                winsound.Beep(900, 100)
            except Exception:
                pass

    def match_cmd(self, cmd_key: str, msg_text: str, has_arg: bool = False):
        """
        全自定义指令匹配器:
        根据 assistant_config.json 中的 triggers 配置，智能匹配玩家消息。
        :param cmd_key: 配置中的命令标识 (如 "teleport", "player_list")
        :param msg_text: 原始聊天消息
        :param has_arg: 该指令是否接受后续参数 (例如 !To 2 中的 2)
        :return: (matched: bool, argument: str, matched_trigger: str)
        """
        cmd_cfg = self.config.get("commands", {}).get(cmd_key, {})
        if not cmd_cfg.get("enabled", True):
            return False, "", ""

        triggers = cmd_cfg.get("triggers", [])
        cleaned = msg_text.strip()
        low = cleaned.lower()

        for trigger in triggers:
            t = trigger.strip().lower()
            if not t:
                continue

            if not has_arg:
                # 无参数指令: 要求消息整体等于触发词
                if low == t:
                    return True, "", trigger
            else:
                # 带参数指令:
                # 1. 玩家仅输入了触发词本身 (如输入了 "!To" 但没写目标)
                if low == t:
                    return True, "", trigger
                # 2. 玩家输入了触发词并附带空格和参数 (如 "!To 2" 或 "传 2")
                if low.startswith(t + " ") or low.startswith(t + "　"):
                    arg = cleaned[len(t):].strip()
                    return True, arg, trigger

        return False, "", ""

    def get_dynamic_help_text(self) -> str:
        """根据当前配置的第一个触发词，动态生成帮助公告内容"""
        cfg_cmds = self.config.get("commands", {})

        def first_trig(key, fallback):
            cmd = cfg_cmds.get(key, {})
            if not cmd.get("enabled", True):
                return None
            trigs = cmd.get("triggers", [])
            return trigs[0] if trigs else fallback

        items = []
        p_trig = first_trig("player_list", "!p")
        if p_trig: items.append(f"{p_trig}(查编号)")

        to_trig = first_trig("teleport", "!To")
        if to_trig: items.append(f"{to_trig}(传队友)")

        sh_trig = first_trig("set_home", "!sethome")
        if sh_trig: items.append(f"{sh_trig}(设家)")

        h_trig = first_trig("home", "!home")
        if h_trig: items.append(f"{h_trig}(回城)")

        free_trig = first_trig("welfare", "!free")
        if free_trig: items.append(f"{free_trig}(查福利)")

        bc_trig = first_trig("buy_vehicle", "!BC")
        if bc_trig: items.append(f"{bc_trig}(买车)")

        buy_trig = first_trig("buy_item", "!buy")
        if buy_trig: items.append(f"{buy_trig}(买装备)")

        return "#Announce [指令] " + " | ".join(items)

    def match_vehicle(self, arg: str):
        """匹配玩家输入的买车参数 (支持按编号 1/2/3 或中英文别名匹配)"""
        arg_clean = arg.strip().lower()
        vehicles_cfg = self.config.get("vehicles", {})
        for key, vdata in vehicles_cfg.items():
            if arg_clean == key.lower():
                return vdata
            for alias in vdata.get("aliases", []):
                if arg_clean == alias.lower():
                    return vdata
        return None

    def match_item(self, arg: str):
        """匹配玩家输入的买物资/商品参数 (支持按编号 1/2/3 或中英文别名匹配)"""
        arg_clean = arg.strip().lower()
        items_cfg = self.config.get("items", {})
        for key, idata in items_cfg.items():
            if arg_clean == key.lower():
                return idata
            for alias in idata.get("aliases", []):
                if arg_clean == alias.lower():
                    return idata
        return None

    def handle_chat(self, steam_id: str, name: str, msg: str):
        """
        聊天指令核心分发处理函数
        当玩家在游戏聊天框发言时，实时捕获并匹配相应功能
        """
        msg = msg.strip()
        print(f"[聊天] {name}({steam_id}): {msg}")

        # 确保发言玩家在线登记与福利初始化
        self.players.add_player(steam_id, name)
        perks = get_player_perks(self.player_data, steam_id, self.config)

        # --------------------------------------------------------------------------------------
        # 0. 帮助菜单指令 (默认: !help / !cmd / 帮助)
        # --------------------------------------------------------------------------------------
        matched_help, _, _ = self.match_cmd("help", msg, has_arg=False)
        if matched_help:
            help_announcement = self.get_dynamic_help_text()
            self.queue_command(help_announcement, "帮助菜单播报")
            return

        # --------------------------------------------------------------------------------------
        # 1. 查询免费福利 (默认: !free / !福利 / 免费)
        # --------------------------------------------------------------------------------------
        matched_free, _, _ = self.match_cmd("welfare", msg, has_arg=False)
        if matched_free:
            ft = perks.get("free_to", 0)
            fh = perks.get("free_home", 0)
            self.queue_command(
                f"#Announce [{name} 传送福利] 免费队友传送剩余: {ft} 次 | 免费传送回城剩余: {fh} 次",
                "查询免费福利"
            )
            return

        # --------------------------------------------------------------------------------------
        # 2. 查询玩家数字编号与在线列表 (默认: !p / p / !list / 查人)
        # --------------------------------------------------------------------------------------
        matched_p, _, _ = self.match_cmd("player_list", msg, has_arg=False)
        if matched_p:
            list_text = self.players.get_list_str()
            self.queue_command(f"#Announce {list_text}", "玩家列表播报")
            return

        # --------------------------------------------------------------------------------------
        # 3. 队友传送 (默认: !To <编号/名字> 或 to <编号/名字> 或 传 2)
        # --------------------------------------------------------------------------------------
        matched_to, to_arg, to_trig = self.match_cmd("teleport", msg, has_arg=True)
        if matched_to:
            if not to_arg:
                p_trig = self.config.get("commands", {}).get("player_list", {}).get("triggers", ["!p"])[0]
                self.queue_command(
                    f"#Announce [提示] 请输入目标编号或名字，例如: {to_trig} 2 (输入 {p_trig} 可查在线编号)",
                    "传送格式错误"
                )
                return

            target_sid, target_name = self.players.find_player(to_arg)
            if not target_sid:
                p_trig = self.config.get("commands", {}).get("player_list", {}).get("triggers", ["!p"])[0]
                self.queue_command(
                    f"#Announce [提示] 未找到目标玩家 [{to_arg}]，可输入 {p_trig} 查看在线玩家编号",
                    "未找到玩家"
                )
                return

            if str(target_sid) == str(steam_id):
                self.queue_command(f"#Announce [提示] 无法传送到自己！", "传送自己报错")
                return

            # 判断是否有免费传送次数 (服主为 999999 次)
            if perks.get("free_to", 0) > 0:
                if str(steam_id) != self.admin_sid:
                    perks["free_to"] -= 1
                    save_player_data(self.player_data)
                self.queue_command(f"#TeleportTo {target_name} {name}", f"{name} 免费传送到 {target_name}")
                return

            # 正常扣费传送
            cost = self.config.get("teleport_cost", 4000)
            balance = get_player_balance(self.db_path, steam_id)
            if balance < cost:
                self.queue_command(
                    f"#Announce [传送失败] {name} 余额不足 {cost} 元 (当前: {balance} 元)，无法传送！",
                    "传送余额不足"
                )
                return

            self.queue_command(f"#ChangeCurrencyBalance Normal -{cost} {name}", f"扣除传送费 {cost}")
            self.queue_command(f"#TeleportTo {target_name} {name}", f"{name} 传送到 {target_name}")
            return

        # --------------------------------------------------------------------------------------
        # 4. 记录家/基地 (默认: !sethome / sethome / !sh / 设家)
        # --------------------------------------------------------------------------------------
        matched_sh, _, _ = self.match_cmd("set_home", msg, has_arg=False)
        if matched_sh:
            coords = get_player_coords(self.db_path, steam_id)
            if coords:
                x, y, z = coords
                self.homes[steam_id] = {"x": x, "y": y, "z": z, "name": name}
                save_homes(self.homes)
                h_trig = self.config.get("commands", {}).get("home", {}).get("triggers", ["!home"])[0]
                sh_trig = self.config.get("commands", {}).get("set_home", {}).get("triggers", ["!sethome"])[0]
                self.queue_command(
                    f"#Announce [基地记录] {name} 成功将当前位置标记为家！输入 {h_trig} 可随时回家。",
                    "记录家成功"
                )
            else:
                sh_trig = self.config.get("commands", {}).get("set_home", {}).get("triggers", ["!sethome"])[0]
                self.queue_command(
                    f"#Announce [提示] 坐标同步中，请在游戏内走动几步后再试一次 {sh_trig}",
                    "坐标未抓取到"
                )
            return

        # --------------------------------------------------------------------------------------
        # 5. 传送回基地/家 (默认: !home / home / !h / 回家)
        # --------------------------------------------------------------------------------------
        matched_home, _, _ = self.match_cmd("home", msg, has_arg=False)
        if matched_home:
            home_data = self.homes.get(steam_id)
            sh_trig = self.config.get("commands", {}).get("set_home", {}).get("triggers", ["!sethome"])[0]
            if not home_data:
                self.queue_command(
                    f"#Announce [提示] {name} 尚未设置家，请先在你的基地输入 {sh_trig} 记录坐标！",
                    "无家提示"
                )
                return

            x, y, z = home_data["x"], home_data["y"], home_data["z"]

            # 判断是否有免费回城次数 (服主为 999999 次)
            if perks.get("free_home", 0) > 0:
                if str(steam_id) != self.admin_sid:
                    perks["free_home"] -= 1
                    save_player_data(self.player_data)
                self.queue_command(f"#Teleport {x:.1f} {y:.1f} {z:.1f} {name}", f"{name} 免费传送回家")
                return

            # 正常扣费回城
            cost = self.config.get("home_teleport_cost", 4000)
            balance = get_player_balance(self.db_path, steam_id)
            if balance < cost:
                self.queue_command(
                    f"#Announce [回城失败] {name} 余额不足 {cost} 元 (当前: {balance} 元)，无法传送回家！",
                    "回城余额不足"
                )
                return

            self.queue_command(f"#ChangeCurrencyBalance Normal -{cost} {name}", f"扣除回家传送费 {cost}")
            self.queue_command(f"#Teleport {x:.1f} {y:.1f} {z:.1f} {name}", f"{name} 传送回家")
            return

        # --------------------------------------------------------------------------------------
        # 6. 一键购车 (默认: !bc 1 / bc 1 / !buycar / 买车)
        # --------------------------------------------------------------------------------------
        matched_bc, bc_arg, bc_trig = self.match_cmd("buy_vehicle", msg, has_arg=True)
        if matched_bc:
            # 未输入车型参数时显示购车菜单
            if not bc_arg:
                v_list = []
                for k, v in self.config.get("vehicles", {}).items():
                    cost_k = v["cost"] // 1000
                    v_list.append(f"{bc_trig} {k}({v['name']}:{cost_k}k)")
                menu_str = " | ".join(v_list)
                self.queue_command(f"#Announce 【一键提车】{menu_str}", "购车菜单提示")
                return

            veh_info = self.match_vehicle(bc_arg)
            if not veh_info:
                self.queue_command(
                    f"#Announce [提车提示] 未知车型 [{bc_arg}]！输入 {bc_trig} 可查看所有车型列表",
                    "车型匹配失败"
                )
                return

            veh_name = veh_info["name"]
            veh_model = veh_info["model"]
            cost = veh_info["cost"]

            # 资金检查 (服主购车免扣费)
            balance = get_player_balance(self.db_path, steam_id)
            if str(steam_id) != self.admin_sid and balance < cost:
                self.queue_command(
                    f"#Announce [购车失败] {name} 余额不足 {cost} 元 (当前余额: {balance} 元)，快去搜刮攒钱吧！",
                    "购车余额不足"
                )
                return

            # 获取玩家位置
            coords = get_player_coords(self.db_path, steam_id)
            if not coords:
                self.queue_command(
                    f"#Announce [购车提示] 正在同步您的实时位置，请在原地走动两步后再输入 {bc_trig} {bc_arg}",
                    "购车坐标同步中"
                )
                return

            x, y, z = coords
            # 载具生成在玩家身侧偏移约 2.5 米，略微抬高 0.3 米防止刷进地底
            spawn_x = x + 250.0
            spawn_y = y + 250.0
            spawn_z = z + 30.0

            # 扣款
            if str(steam_id) != self.admin_sid:
                self.queue_command(f"#ChangeCurrencyBalance Normal -{cost} {name}", f"扣除提车费 {cost}")

            # 刷出载具 (带 Location 与 Modifier Full 满配件、满燃油、满电池)
            spawn_cmd = f'#SpawnVehicle {veh_model} 1 Modifier Full Location "{{X={spawn_x:.1f} Y={spawn_y:.1f} Z={spawn_z:.1f}}}"'
            self.queue_command(spawn_cmd, f"为 {name} 刷车: {veh_name}")

            # 公告通报
            cost_tip = "服主免费" if str(steam_id) == self.admin_sid else f"扣除 {cost} 元"
            self.queue_command(
                f"#Announce [一键提车] 崭新的 {veh_name} 已空降在 {name} 身边！({cost_tip})",
                "提车成功通知"
            )
            return

        # --------------------------------------------------------------------------------------
        # 7. 一键购买物品/商城 (默认: !buy 1 / buy 1 / !shop / 买 1)
        # --------------------------------------------------------------------------------------
        matched_buy, buy_arg, buy_trig = self.match_cmd("buy_item", msg, has_arg=True)
        if matched_buy:
            # 未输入参数时显示商品列表菜单
            if not buy_arg:
                i_list = []
                for k, v in self.config.get("items", {}).items():
                    cost_val = v.get("cost", 0)
                    cost_k = f"{cost_val // 1000}k" if cost_val >= 1000 else f"{cost_val}元"
                    i_list.append(f"{buy_trig} {k}({v['name']}:{cost_k})")
                menu_str = " | ".join(i_list)
                self.queue_command(f"#Announce 【物资商城】{menu_str}", "商品菜单提示")
                return

            item_info = self.match_item(buy_arg)
            if not item_info:
                self.queue_command(
                    f"#Announce [商城提示] 未知商品 [{buy_arg}]！输入 {buy_trig} 可查看所有商品列表",
                    "商品匹配失败"
                )
                return

            item_name = item_info["name"]
            item_id = item_info["item_id"]
            count = item_info.get("count", 1)
            cost = item_info["cost"]

            # 资金检查 (服主免扣费)
            balance = get_player_balance(self.db_path, steam_id)
            if str(steam_id) != self.admin_sid and balance < cost:
                self.queue_command(
                    f"#Announce [购买失败] {name} 余额不足 {cost} 元 (当前余额: {balance} 元)",
                    "购买余额不足"
                )
                return

            # 获取玩家位置
            coords = get_player_coords(self.db_path, steam_id)
            if not coords:
                self.queue_command(
                    f"#Announce [商城提示] 正在同步您的实时位置，请走动两步后再输入 {buy_trig} {buy_arg}",
                    "商品坐标同步中"
                )
                return

            x, y, z = coords
            # 物资生成在玩家身边约 0.8 米处，略微抬高 0.15 米
            spawn_x = x + 80.0
            spawn_y = y + 80.0
            spawn_z = z + 15.0

            # 扣款
            if str(steam_id) != self.admin_sid:
                self.queue_command(f"#ChangeCurrencyBalance Normal -{cost} {name}", f"扣除购买费 {cost}")

            # 刷出物品 (无需全服通报，直接安静生成在脚边)
            spawn_cmd = f'#SpawnItem {item_id} {count} Location "{{X={spawn_x:.1f} Y={spawn_y:.1f} Z={spawn_z:.1f}}}"'
            self.queue_command(spawn_cmd, f"为 {name} 刷出商品: {item_name} x{count}")

            # 默认不全服通报购买成功 (满足隐私与防刷屏要求)
            if self.config.get("announce_item_purchase", False):
                self.queue_command(f"#Announce [物资购买] {name} 成功购买了 {item_name} x{count}", "购买成功通报")
            return

        # ======================================================================================
        # 【开发者扩展点：如何在此处新增一个自定义功能/指令？】
        # --------------------------------------------------------------------------------------
        # 示例：假设你想添加一个 "!suicide" 或 "自杀" 指令：
        # 1. 在 assistant_config.json 的 "commands" 下添加:
        #    "suicide": {"enabled": true, "triggers": ["!suicide", "!自杀", "自杀"], "description": "自杀重生"}
        # 2. 在下方写:
        #    matched_suicide, _, _ = self.match_cmd("suicide", msg, has_arg=False)
        #    if matched_suicide:
        #        self.queue_command(f"#Kill {name}", f"玩家 {name} 请求自杀")
        #        return
        # ======================================================================================

    def handle_login(self, steam_id: str, name: str):
        """玩家登录进服事件处理"""
        num = self.players.add_player(steam_id, name)
        get_player_perks(self.player_data, steam_id, self.config)
        print(f"[上线] 玩家入场: [{num}] {name} ({steam_id})")
        if self.config.get("welcome_announcement", True):
            count = len(self.players.online_players)
            self.queue_command(
                f"#Announce 欢迎玩家 {name} 进服！(新人送队友与回城各1次免费) 在线: {count} 人",
                "进服欢迎播报"
            )

    def handle_logout(self, steam_id: str):
        """玩家下线离开事件处理"""
        self.players.remove_player(steam_id)
        print(f"[离线] 玩家离开: ({steam_id})，当前在线: {len(self.players.online_players)} 人")

    def handle_kill(self, killer_name: str, victim_name: str, weapon: str, dist_m: float, is_npc: bool):
        """击杀与狙击事件处理"""
        if not self.config.get("kill_announcement", True):
            return
        clean_wp = clean_weapon_name(weapon)
        if is_npc:
            if self.config.get("announce_npc_kills", False):
                self.queue_command(f"#Announce [阵亡] 玩家 {victim_name} 不幸被丧尸/敌人击杀！", "NPC击杀播报")
        elif dist_m >= 100.0:
            self.queue_command(
                f"#Announce [神射远狙] {killer_name} 使用 {clean_wp} 在 {dist_m:.1f}米 外精准击杀了 {victim_name}！",
                "远狙播报"
            )
        else:
            self.queue_command(
                f"#Announce [击杀播报] {killer_name} 使用 {clean_wp} 击杀了 {victim_name} (距离: {dist_m:.1f}米)",
                "普通击杀播报"
            )


# ==================================================================================================
# [模块 7/8] 日志文件流式监控线程 (Log File Tailer & Event Watcher)
# ==================================================================================================
def get_latest_file(pattern: str):
    """获取目录下最新修改的日志文件路径"""
    files = glob.glob(pattern)
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def init_online_players_from_log(engine: AssistantEngine):
    """启动助手时，通过扫描最新登录日志恢复当前在线玩家列表与福利状态"""
    latest_login = get_latest_file(os.path.join(engine.log_dir, "login_*.log"))
    if not latest_login or not os.path.exists(latest_login):
        return
    try:
        with open(latest_login, "rb") as f:
            data = f.read().decode("utf-16le", errors="ignore")
        for line in data.splitlines():
            if "logged in at:" in line:
                m = re.search(r"(\d{17}):([^\'\(\)]+)", line)
                if m:
                    sid, name = m.group(1), m.group(2).strip()
                    engine.players.add_player(sid, name)
                    get_player_perks(engine.player_data, sid, engine.config)
            elif "logged out at:" in line:
                m = re.search(r"(\d{17}):([^\'\(\)]+)", line)
                if m:
                    sid = m.group(1)
                    engine.players.remove_player(sid)
        print(f"[玩家初始化] 已加载在线玩家: {len(engine.players.online_players)} 人")
        for sid, info in engine.players.online_players.items():
            print(f"  -> [{info['num']}] {info['name']} ({sid})")
    except Exception as e:
        print(f"[玩家初始化警告] 读取登录日志异常: {e}")


def tail_logs(engine: AssistantEngine):
    """
    流式监听 SCUM 实时日志的主工作循环。
    监控 chat_*.log (聊天/指令), login_*.log (进出服), kill_*.log (击杀)。
    """
    chat_file, chat_pos = None, 0
    login_file, login_pos = None, 0
    kill_file, kill_pos = None, 0

    print("[助手监控] 正在启动日志实时监听...")

    while engine.running:
        try:
            # 1. 监控 chat_*.log
            latest_chat = get_latest_file(os.path.join(engine.log_dir, "chat_*.log"))
            if latest_chat != chat_file:
                chat_file = latest_chat
                chat_pos = os.path.getsize(chat_file) if chat_file else 0
            elif chat_file and os.path.exists(chat_file):
                size = os.path.getsize(chat_file)
                if size > chat_pos:
                    with open(chat_file, "rb") as f:
                        f.seek(chat_pos)
                        data = f.read(size - chat_pos)
                        chat_pos = size
                    text = data.decode("utf-16le", errors="ignore")
                    for line in text.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        # 日志格式: 2026.10.09-11.05.49: '76561198794318523:kLuTz(6)' 'Local: !To 2'
                        m = re.search(r"'(\d{17}):([^']+?)\(\d+\)'\s+'[^:]*:\s*(.+)'", line)
                        if m:
                            sid, name, content = m.group(1), m.group(2).strip(), m.group(3).strip()
                            engine.handle_chat(sid, name, content)

            # 2. 监控 login_*.log
            latest_login = get_latest_file(os.path.join(engine.log_dir, "login_*.log"))
            if latest_login != login_file:
                login_file = latest_login
                login_pos = os.path.getsize(login_file) if login_file else 0
            elif login_file and os.path.exists(login_file):
                size = os.path.getsize(login_file)
                if size > login_pos:
                    with open(login_file, "rb") as f:
                        f.seek(login_pos)
                        data = f.read(size - login_pos)
                        login_pos = size
                    text = data.decode("utf-16le", errors="ignore")
                    for line in text.splitlines():
                        if "logged in at:" in line:
                            m = re.search(r"(\d{17}):([^\'\(\)]+)", line)
                            if m:
                                sid, name = m.group(1), m.group(2).strip()
                                engine.handle_login(sid, name)
                        elif "logged out at:" in line:
                            m = re.search(r"(\d{17}):([^\'\(\)]+)", line)
                            if m:
                                sid = m.group(1)
                                engine.handle_logout(sid)

            # 3. 监控 kill_*.log
            latest_kill = get_latest_file(os.path.join(engine.log_dir, "kill_*.log"))
            if latest_kill != kill_file:
                kill_file = latest_kill
                kill_pos = os.path.getsize(kill_file) if kill_file else 0
            elif kill_file and os.path.exists(kill_file):
                size = os.path.getsize(kill_file)
                if size > kill_pos:
                    with open(kill_file, "rb") as f:
                        f.seek(kill_pos)
                        data = f.read(size - kill_pos)
                        kill_pos = size
                    text = data.decode("utf-16le", errors="ignore")
                    for line in text.splitlines():
                        if line.startswith("Died:"):
                            # 格式: Died: VOID (76561199196682704), Killer: IKARI (76561199218206184) Weapon: Weapon_AK47 ... Distance: 154.2 m
                            m = re.search(
                                r"Died:\s+([^(]+)\s+\(([^)]+)\),\s+Killer:\s+([^(]+)\s+\(([^)]+)\).*Weapon:\s*([^\s\[]+).*Distance:\s*([\d\.]+)\s*m",
                                line
                            )
                            if m:
                                victim = m.group(1).strip()
                                killer = m.group(3).strip()
                                killer_tag = m.group(4).strip()
                                weapon = m.group(5)
                                dist = float(m.group(6))
                                is_npc = (killer_tag == "NPC") or ("BP_Guard" in killer) or ("Puppet" in killer) or ("Zombie" in killer)
                                engine.handle_kill(killer, victim, weapon, dist, is_npc)

        except Exception:
            pass

        time.sleep(0.5)


# ==================================================================================================
# [模块 8/8] 后台执行队列、热键控制与主程序入口
# ==================================================================================================
def run_executor_loop(engine: AssistantEngine):
    """后台循环处理待发送的管理员指令队列"""
    while engine.running:
        # F2 快捷键切换 [全自动秒发] / [F1手动确认] 模式
        if user32.GetAsyncKeyState(VK_F2) & 0x8000:
            engine.auto_mode = not engine.auto_mode
            engine.config["auto_execute"] = engine.auto_mode
            save_config(engine.config)
            mode_str = "全自动秒发模式" if engine.auto_mode else "F1按键确认模式"
            print(f"\n[模式切换] 当前已切换为: 【{mode_str}】\n")
            try:
                winsound.Beep(1200, 150)
            except Exception:
                pass
            time.sleep(0.4)

        # 检查队列
        item_to_run = None
        with engine.queue_lock:
            if engine.cmd_queue:
                f1_pressed = (user32.GetAsyncKeyState(VK_F1) & 0x8000) != 0
                if (engine.auto_mode and is_scum_active()) or f1_pressed:
                    item_to_run = engine.cmd_queue.pop(0)

        if item_to_run:
            cmd_text, desc = item_to_run
            print(f"[执行中] 正在发送到游戏: {cmd_text} ({desc})")
            send_chat_command(cmd_text)
            time.sleep(0.70)

        time.sleep(0.05)


def print_banner(engine: AssistantEngine):
    """打印控制台欢迎信息与指令对照表"""
    os.system("title SCUM 服主一键伴侣助手")
    print("=" * 70)
    print("           SCUM 服主一键伴侣助手 (Server Companion Assistant)")
    print("           开源版本 | 零第三方依赖 | 支持全功能指令自定义")
    print("=" * 70)

    cfg_cmds = engine.config.get("commands", {})
    def get_cmd_repr(key, default_repr):
        c = cfg_cmds.get(key, {})
        if not c.get("enabled", True):
            return "已禁用"
        return " / ".join(c.get("triggers", [default_repr])[:3])

    print("当前已启用的玩家指令:")
    print(f"  [1] 玩家传送: {get_cmd_repr('teleport', '!to')} <编号/名字>")
    print(f"  [2] 基地回城: {get_cmd_repr('set_home', '!sethome')} (设家) / {get_cmd_repr('home', '!home')} (回城)")
    print(f"  [3] 福利查询: {get_cmd_repr('welfare', '!free')}")
    print(f"  [4] 一键购车: {get_cmd_repr('buy_vehicle', '!bc')} 1(摩托) / 2(皮卡) / 3(飞机)")
    print(f"  [5] 一键买物: {get_cmd_repr('buy_item', '!buy')} 1(巴雷特) / 2(弹药) / 3(急救包)")
    print(f"  [6] 编号列表: {get_cmd_repr('player_list', '!p')}")
    print(f"  [7] 菜单帮助: {get_cmd_repr('help', '!help')}")
    print(f"  [8] 广播播报: 进服欢迎通报、PvP与神射远狙击杀通报")
    print("-" * 70)
    print("控制快捷键:")
    print("  [F1 键]: 手动确认并立即执行下一条待发指令")
    print("  [F2 键]: 一键切换 【全自动秒发】 / 【F1手动确认】 模式")
    print("=" * 70)


def main():
    engine = AssistantEngine()
    print_banner(engine)

    # 预加载当前在线玩家与福利
    init_online_players_from_log(engine)

    mode_text = "【全自动秒发模式】(SCUM窗口激活时秒发)" if engine.auto_mode else "【F1按键确认模式】"
    print(f"\n当前工作模式: {mode_text}\n")

    # 启动日志监听线程
    t_log = threading.Thread(target=tail_logs, args=(engine,), daemon=True)
    t_log.start()

    # 启动指令发送线程
    t_exec = threading.Thread(target=run_executor_loop, args=(engine,), daemon=True)
    t_exec.start()

    print("[状态] 助手已成功就绪！你可以切回游戏正常游玩。")
    print("提示：保持本控制台窗口在后台运行即可，无需关闭。\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n正在退出服主助手...")
        engine.running = False


if __name__ == "__main__":
    main()
