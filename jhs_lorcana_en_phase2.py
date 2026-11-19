#!/usr/bin/env python3
"""集换社：迪士尼洛卡纳英文专用版。

Phase2：读取 Phase1 清单，抓取单详情字段；多子类父产品标记待处理，支持断点续跑。
先手动进入“迪士尼洛卡纳 → 英文”首页，再运行本脚本。
输出使用 jhs_lorcana_en_ 前缀，与宝可梦结果分开。
仅依赖 Python 标准库及 ADB；不需要 root。
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Iterable


DEFAULT_ADB = r"C:\platform-tools\adb.exe"
DEFAULT_SERIAL = "emulator-5554"
REMOTE_XML = "/sdcard/Download/jhs_lorcana_en_phase2.xml"
SCRIPT_VERSION = "2026-11-11-lorcana-en-phase2-v1"
LANGUAGE_CHOICES = ("英文",)

DETAIL_COLUMNS = [
    "系列",
    "列表编号",
    "列表名称",
    "首页编号",
    "首页名称",
    "入口类型",
    "详情编号",
    "详情名称",
    "发售日期",
    "页面张数",
    "收录商品数量",
    "子类数量",
    "统计状态",
    "统计时间",
    "备注",
]

MULTI_COLUMNS = [
    "系列",
    "父列表编号",
    "父列表名称",
    "统计状态",
    "统计时间",
    "备注",
]


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_adb(adb_path: str, serial: str, *args: str, check: bool = True) -> str:
    command = [adb_path, "-s", serial, *args]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if check and completed.returncode != 0:
        raise RuntimeError(f"ADB 命令失败：{' '.join(command)}\n{output.strip()}")
    return output.strip()


def dump_current_page(adb_path: str, serial: str, local_xml: Path) -> None:
    last_error = ""
    for attempt in range(4):
        try:
            run_adb(adb_path, serial, "shell", "uiautomator", "dump", REMOTE_XML)
            run_adb(adb_path, serial, "pull", REMOTE_XML, str(local_xml))
            if local_xml.exists() and local_xml.stat().st_size > 100:
                return
        except RuntimeError as exc:
            last_error = str(exc)
        time.sleep(0.8 + attempt * 0.4)
    raise RuntimeError(f"无法取得当前页面结构。{last_error}")


def parse_bounds(value: str) -> tuple[int, int, int, int]:
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", value or "")
    if not match:
        return 0, 0, 0, 0
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def all_texts(item: ET.Element) -> list[str]:
    return [
        node.attrib.get("text", "").strip()
        for node in item.iter("node")
        if node.attrib.get("text", "").strip()
    ]


def validate_lorcana_home(root: ET.Element, expected_language: str = "英文") -> None:
    """校验游戏及语言；兼容游戏和语言合并在同一文本节点的布局。"""
    texts = all_texts(root)
    normalized = [re.sub(r"\s+", "", text).casefold() for text in texts]
    if not any(alias in text for text in normalized
               for alias in ("洛卡纳", "洛卡那", "lorcana")):
        raise RuntimeError("当前卡牌库不是迪士尼洛卡纳；请先手动选择“迪士尼洛卡纳 → 英文”。")
    if expected_language and not any(expected_language in text for text in texts):
        raise RuntimeError(f"当前语言未识别为“{expected_language}”；请确认顶部语言后重试。")


def find_node(root: ET.Element, resource_id: str) -> ET.Element | None:
    for node in root.iter("node"):
        if node.attrib.get("resource-id") == resource_id:
            return node
    return None


def first_text(root: ET.Element, resource_id: str) -> str:
    for node in root.iter("node"):
        if node.attrib.get("resource-id") == resource_id:
            value = node.attrib.get("text", "").strip()
            if value:
                return value
    return ""


def child_text(item: ET.Element, resource_id: str) -> str:
    for node in item.iter("node"):
        if node.attrib.get("resource-id") == resource_id:
            return node.attrib.get("text", "").strip()
    return ""


def descendant_texts(root: ET.Element, resource_id: str) -> list[str]:
    node = find_node(root, resource_id)
    return all_texts(node) if node is not None else []


def first_matching(values: Iterable[str], pattern: str) -> str:
    regex = re.compile(pattern)
    for value in values:
        match = regex.search(value)
        if match:
            return match.group(1) if match.lastindex else match.group(0)
    return ""


def parse_int(value: str) -> int | None:
    match = re.search(r"\d[\d,]*", value or "")
    if not match:
        return None
    return int(match.group(0).replace(",", ""))


def normalize_label(value: str) -> str:
    return re.sub(
        r"[\s\u3000\-‐‑‒–—―·・:：,，.。'\"“”‘’「」『』【】\[\]（）()&＆]+",
        "",
        (value or "").casefold(),
    )


def product_key(section: str, code: str, name: str) -> str:
    return "\x1f".join((section.strip(), code.strip(), name.strip()))


def write_csv(path: Path, columns: list[str], rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temp_path.replace(path)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def tap_xy(adb_path: str, serial: str, x: int, y: int) -> None:
    run_adb(adb_path, serial, "shell", "input", "tap", str(x), str(y))


def tap_node_center(adb_path: str, serial: str, node: ET.Element) -> None:
    left, top, right, bottom = parse_bounds(node.attrib.get("bounds", ""))
    if right <= left or bottom <= top:
        raise RuntimeError("目标按钮没有有效坐标。")
    tap_xy(adb_path, serial, (left + right) // 2, (top + bottom) // 2)


def swipe_node_horizontal(
    adb_path: str,
    serial: str,
    node: ET.Element,
    direction: str,
) -> None:
    left, top, right, bottom = parse_bounds(node.attrib.get("bounds", ""))
    if right - left < 100 or bottom <= top:
        raise RuntimeError("横向列表没有有效坐标。")
    inset = min(220, max(50, (right - left) // 5))
    start_x, end_x = (
        (right - inset, left + inset)
        if direction == "left"
        else (left + inset, right - inset)
    )
    run_adb(
        adb_path,
        serial,
        "shell",
        "input",
        "swipe",
        str(start_x),
        str((top + bottom) // 2),
        str(end_x),
        str((top + bottom) // 2),
        "500",
    )


def node_signature(node: ET.Element | None) -> tuple[tuple[str, ...], ...]:
    if node is None:
        return ()
    return tuple(
        (child.attrib.get("bounds", ""), *all_texts(child))
        for child in list(node)
    )


# ---------------------------------------------------------------------------
# 文件名 / 断点
# ---------------------------------------------------------------------------


def default_inventory_name(language: str) -> str:
    return "jhs_lorcana_en_product_inventory.csv"


def default_output_names(language: str) -> tuple[str, str]:
    return "jhs_lorcana_en_phase2_product_details.csv", "jhs_lorcana_en_phase2_multi_parents.csv"


def load_inventory(path: Path) -> OrderedDict[str, dict[str, str]]:
    inventory: OrderedDict[str, dict[str, str]] = OrderedDict()
    for row in read_csv(path):
        section = row.get("系列", "").strip()
        code = row.get("列表编号", "").strip()
        name = row.get("列表名称", "").strip()
        if not section or not name:
            continue
        key = row.get("产品唯一键", "") or product_key(section, code, name)
        inventory[key] = row
    return inventory


def load_detail_results(path: Path) -> OrderedDict[str, dict[str, str]]:
    result: OrderedDict[str, dict[str, str]] = OrderedDict()
    for row in read_csv(path):
        key = product_key(
            row.get("系列", ""), row.get("列表编号", ""), row.get("列表名称", "")
        )
        result[key] = row
    return result


def result_finished(row: dict[str, str] | None) -> bool:
    if not row:
        return False
    return row.get("统计状态", "") in {
        "完成",
        "完成（有缺项）",
        "多子类待处理",
    }


def reconcile_results_to_inventory(
    inventory: OrderedDict[str, dict[str, str]],
    results: OrderedDict[str, dict[str, str]],
) -> tuple[OrderedDict[str, dict[str, str]], int]:
    """把旧第二步结果映射到刚刷新的第一步 inventory。

    第一优先级仍是“系列+编号+名称”完全一致。若 App 只改了显示名称，
    对当前系列内唯一的编号，允许用“系列+编号”确认它仍是同一个产品，
    从而避免把整批旧产品重新抓一遍。重复编号（例如 M-P / SV-P）绝不
    仅靠编号自动继承，必须依赖名称，避免串产品。
    """
    source = list(results.items())
    mapped: OrderedDict[str, dict[str, str]] = OrderedDict()
    used_old_keys: set[str] = set()
    reused = 0

    for inv_key, inv_row in inventory.items():
        exact = results.get(inv_key)
        if exact is not None:
            mapped[inv_key] = exact
            used_old_keys.add(inv_key)
            continue

        section = inv_row.get("系列", "")
        code = inv_row.get("列表编号", "")
        name = inv_row.get("列表名称", "")
        section_norm = normalize_label(section)
        code_norm = normalize_label(code)
        name_norm = normalize_label(name)
        code_unique = bool(code_norm) and section_code_occurrences(section, code, inventory) == 1

        candidates: list[tuple[int, str, dict[str, str]]] = []
        for old_key, old_row in source:
            if old_key in used_old_keys or not result_finished(old_row):
                continue
            if normalize_label(old_row.get("系列", "")) != section_norm:
                continue
            old_code = normalize_label(old_row.get("列表编号", ""))
            old_name = normalize_label(old_row.get("列表名称", ""))

            score = 0
            if code_norm and old_code == code_norm and old_name == name_norm:
                score = 100
            elif code_unique and old_code == code_norm:
                score = 80
            elif name_norm and old_name == name_norm:
                score = 60
            if score:
                candidates.append((score, old_key, old_row))

        if not candidates:
            continue
        candidates.sort(key=lambda item: item[0], reverse=True)
        best_score = candidates[0][0]
        best = [item for item in candidates if item[0] == best_score]
        if len(best) != 1:
            continue

        _, old_key, old_row = best[0]
        cloned = dict(old_row)
        cloned["系列"] = section
        cloned["列表编号"] = code
        cloned["列表名称"] = name
        mapped[inv_key] = cloned
        used_old_keys.add(old_key)
        reused += 1

    return mapped, reused


def save_outputs(
    inventory: OrderedDict[str, dict[str, str]],
    results: OrderedDict[str, dict[str, str]],
    multi_rows: list[dict[str, object]],
    details_path: Path,
    multi_path: Path,
) -> None:
    ordered_results = [results[key] for key in inventory if key in results]
    write_csv(details_path, DETAIL_COLUMNS, ordered_results)
    write_csv(multi_path, MULTI_COLUMNS, multi_rows)


# ---------------------------------------------------------------------------
# 卡牌库首页导航
# ---------------------------------------------------------------------------


def is_library_home(root: ET.Element) -> bool:
    return (
        find_node(root, "com.jihuanshe:id/categoryBtn") is not None
        and find_node(root, "com.jihuanshe:id/packRv") is not None
    )


def ensure_library_home(
    adb_path: str,
    serial: str,
    local_xml: Path,
    wait_seconds: float,
    expected_language: str,
) -> ET.Element:
    """回到卡牌库首页，不点击“分类”。"""
    for _ in range(8):
        dump_current_page(adb_path, serial, local_xml)
        root = ET.parse(local_xml).getroot()
        if is_library_home(root):
            validate_lorcana_home(root, expected_language)
            return root

        is_jihuanshe_page = any(
            node.attrib.get("package") == "com.jihuanshe" for node in root.iter("node")
        )
        if is_jihuanshe_page:
            run_adb(adb_path, serial, "shell", "input", "keyevent", "4")
            time.sleep(wait_seconds)
            continue
        break
    raise RuntimeError("无法返回迪士尼洛卡纳卡牌库首页；请手动进入首页后重试。")


def reset_horizontal_to_start(
    adb_path: str,
    serial: str,
    local_xml: Path,
    resource_id: str,
    wait_seconds: float,
    expected_language: str,
    max_swipes: int = 28,
) -> ET.Element:
    previous: tuple[tuple[str, ...], ...] | None = None
    for _ in range(max_swipes):
        root = ensure_library_home(
            adb_path, serial, local_xml, wait_seconds, expected_language
        )
        strip = find_node(root, resource_id)
        if strip is None:
            raise RuntimeError(f"首页缺少横向列表：{resource_id}")
        signature = node_signature(strip)
        if signature and signature == previous:
            return root
        previous = signature
        swipe_node_horizontal(adb_path, serial, strip, "right")
        time.sleep(wait_seconds)
    raise RuntimeError(f"横向列表 {resource_id} 无法回到起点。")


def select_home_series(
    adb_path: str,
    serial: str,
    local_xml: Path,
    section: str,
    wait_seconds: float,
    expected_language: str,
) -> None:
    reset_horizontal_to_start(
        adb_path,
        serial,
        local_xml,
        "com.jihuanshe:id/scroll_view",
        wait_seconds,
        expected_language,
    )
    previous: tuple[tuple[str, ...], ...] | None = None
    for _ in range(36):
        root = ensure_library_home(
            adb_path, serial, local_xml, wait_seconds, expected_language
        )
        container = find_node(root, "com.jihuanshe:id/title_container")
        scroll_view = find_node(root, "com.jihuanshe:id/scroll_view")
        if container is None or scroll_view is None:
            raise RuntimeError("首页没有大系列标签栏。")
        for node in list(container):
            text = node.attrib.get("text", "").strip()
            if normalize_label(text) != normalize_label(section):
                continue
            left, top, right, bottom = parse_bounds(node.attrib.get("bounds", ""))
            if right - left >= 20 and bottom > top:
                tap_node_center(adb_path, serial, node)
                time.sleep(wait_seconds)
                return
        signature = node_signature(container)
        if signature and signature == previous:
            break
        previous = signature
        swipe_node_horizontal(adb_path, serial, scroll_view, "left")
        time.sleep(wait_seconds)
    raise RuntimeError(f"首页大系列标签栏中找不到：{section}")


def parse_home_products(root: ET.Element) -> list[dict[str, object]]:
    strip = find_node(root, "com.jihuanshe:id/packRv")
    if strip is None:
        raise RuntimeError("当前不是卡牌库首页：缺少产品横向栏。")
    products: list[dict[str, object]] = []
    for item in list(strip):
        if item.attrib.get("clickable") != "true":
            continue
        texts = all_texts(item)
        if not texts:
            continue
        left, top, right, bottom = parse_bounds(item.attrib.get("bounds", ""))
        if right - left < 24 or bottom <= top:
            continue

        # 原首页布局通常是“名称 ... 编号”；优先使用稳定 resource-id，找不到再回退。
        code = child_text(item, "com.jihuanshe:id/numberTv")
        if not code:
            code = child_text(item, "com.jihuanshe:id/packNumTv")
        if not code and len(texts) >= 2:
            code = texts[-1]

        name_candidates = [value for value in texts if value != code]
        name = name_candidates[0] if name_candidates else (texts[0] if texts else "")
        if not name:
            continue

        tap_points: list[tuple[int, int]] = []
        image_node = find_node(item, "com.jihuanshe:id/packIv")
        if image_node is not None:
            i_left, i_top, i_right, i_bottom = parse_bounds(
                image_node.attrib.get("bounds", "")
            )
            if i_right > i_left and i_bottom > i_top:
                tap_points.append(((i_left + i_right) // 2, (i_top + i_bottom) // 2))
        tap_points.append(((left + right) // 2, (top + bottom) // 2))
        products.append(
            {
                "名称": name,
                "编号": code,
                "x": (left + right) // 2,
                "y": (top + bottom) // 2,
                "点击坐标": list(dict.fromkeys(tap_points)),
            }
        )
    return products


def normalized_home_name(value: str) -> str:
    """Normalize a homepage label while ignoring visual ellipsis."""
    return normalize_label((value or "").replace("…", ""))


def homepage_name_match_strength(home_name_raw: str, inventory_name_raw: str) -> int:
    """Return 3 for exact name, 2 for a trustworthy truncated-prefix match.

    Homepage cards often render names such as ``SV PRO...`` or ``漆黑伏特...``.
    For duplicate product codes, name compatibility is mandatory; code-only matching
    is intentionally forbidden because several distinct products can all be ``SV-P``.
    """
    home_name = normalized_home_name(home_name_raw)
    inv_name = normalized_home_name(inventory_name_raw)
    if not home_name or not inv_name:
        return 0
    if home_name == inv_name:
        return 3

    raw = (home_name_raw or "").strip()
    visibly_truncated = "..." in raw or "…" in raw
    # 只有首页文本真的带省略号时，才允许“短前缀 -> 完整名称”匹配。
    # 例如“30周年庆典”本身是一个完整产品名，不能因此误匹配
    # “30周年庆典 幻彩未来纪念礼盒”。
    if visibly_truncated and len(home_name) >= 4 and inv_name.startswith(home_name):
        return 2
    return 0


def section_code_occurrences(
    section: str,
    code: str,
    inventory: OrderedDict[str, dict[str, str]],
) -> int:
    target_section = normalize_label(section)
    target_code = normalize_label(code)
    if not target_code:
        return 0
    return sum(
        1
        for row in inventory.values()
        if normalize_label(row.get("系列", "")) == target_section
        and normalize_label(row.get("列表编号", "")) == target_code
    )


def match_home_product(
    section: str,
    home_product: dict[str, object],
    inventory: OrderedDict[str, dict[str, str]],
    pending_keys: set[str],
) -> str | None:
    """Match one homepage card to one inventory row without code-only ambiguity.

    Important: duplicate-code detection uses the *entire section inventory*, not just
    ``pending_keys``.  ``--only`` may leave one pending row, but that must never make
    the first visible ``SV-P`` card look like the requested ``SV-P`` product.
    """
    home_code_raw = str(home_product.get("编号", ""))
    home_name_raw = str(home_product.get("名称", ""))
    home_code = normalize_label(home_code_raw)
    duplicate_code = section_code_occurrences(section, home_code_raw, inventory) > 1
    scored: list[tuple[int, str]] = []

    for key in pending_keys:
        row = inventory[key]
        if normalize_label(row.get("系列", "")) != normalize_label(section):
            continue
        inv_code_raw = row.get("列表编号", "")
        inv_name_raw = row.get("列表名称", "")
        inv_code = normalize_label(inv_code_raw)
        code_equal = bool(inv_code and home_code and inv_code == home_code)

        # 两边都明确给了编号时，编号不同就直接拒绝。
        # 这条硬保护可防止 M-P“30周年庆典 幻彩未来纪念礼盒”
        # 因名称前缀相似而误点到 M6a“30周年庆典”。
        if inv_code and home_code and not code_equal:
            continue

        name_strength = homepage_name_match_strength(home_name_raw, inv_name_raw)

        # Exact/truncated compatible name is authoritative.  Matching code further
        # raises confidence but is not required for rare homepage code omissions.
        if name_strength == 3:
            score = 100 + (20 if code_equal else 0)
        elif name_strength == 2:
            score = 80 + (20 if code_equal else 0)
        elif code_equal and not duplicate_code:
            # Code-only is allowed only when that code is unique in the whole series.
            score = 40
        else:
            continue
        scored.append((score, key))

    if not scored:
        return None
    scored.sort(reverse=True)
    best_score = scored[0][0]
    best = [key for score, key in scored if score == best_score]
    return best[0] if len(best) == 1 else None


def assert_home_product_matches_inventory(
    section: str,
    home_product: dict[str, object],
    row: dict[str, str],
    inventory: OrderedDict[str, dict[str, str]],
) -> None:
    """Fail closed before clicking if a duplicate-code homepage card is ambiguous."""
    home_code = str(home_product.get("编号", ""))
    home_name = str(home_product.get("名称", ""))
    inv_code = row.get("列表编号", "")
    inv_name = row.get("列表名称", "")
    code_equal = normalize_label(home_code) == normalize_label(inv_code) and bool(inv_code)
    name_strength = homepage_name_match_strength(home_name, inv_name)
    duplicate_code = section_code_occurrences(section, inv_code, inventory) > 1

    if name_strength:
        return
    if code_equal and not duplicate_code:
        return
    raise RuntimeError(
        "首页产品身份校验失败，拒绝点击可能的同编号错误产品："
        f"清单={inv_code} {inv_name}；首页={home_code} {home_name}"
    )


# ---------------------------------------------------------------------------
# 下方详情入口 / 子类识别
# ---------------------------------------------------------------------------


def find_matching_summary(root: ET.Element, product_name: str) -> ET.Element | None:
    """找首页产品栏下方、名称与父产品相同的单一详情卡片。"""
    strip = find_node(root, "com.jihuanshe:id/packRv")
    if strip is None:
        return None
    _, _, _, strip_bottom = parse_bounds(strip.attrib.get("bounds", ""))
    parent_map = {
        child: parent
        for parent in root.iter("node")
        for child in list(parent)
    }
    matches: list[ET.Element] = []
    target = normalize_label(product_name)
    for node in root.iter("node"):
        if normalize_label(node.attrib.get("text", "")) != target:
            continue
        left, top, right, bottom = parse_bounds(node.attrib.get("bounds", ""))
        if right > left and bottom > top and top >= strip_bottom - 4:
            matches.append(node)
    if not matches:
        return None
    text_node = max(
        matches,
        key=lambda node: parse_bounds(node.attrib.get("bounds", ""))[1],
    )
    current = text_node
    while current in parent_map:
        parent = parent_map[current]
        if parent.attrib.get("clickable") == "true":
            left, top, right, bottom = parse_bounds(parent.attrib.get("bounds", ""))
            if right > left and bottom > top and top >= strip_bottom - 12:
                return parent
        current = parent
    return text_node


def parse_child_entries(root: ET.Element) -> list[dict[str, object]]:
    strip = find_node(root, "com.jihuanshe:id/childPackRv")
    if strip is None:
        return []
    children: list[dict[str, object]] = []
    for item in list(strip):
        if item.attrib.get("clickable") != "true":
            continue
        name = child_text(item, "com.jihuanshe:id/titleTv")
        code = child_text(item, "com.jihuanshe:id/numberTv")
        date = child_text(item, "com.jihuanshe:id/dateTv")
        texts = all_texts(item)
        if not name:
            # 兼容布局变更：去掉 code/date 后取剩余最后一个可见文本。
            candidates = [value for value in texts if value not in {code, date}]
            name = candidates[-1] if candidates else ""
        left, top, right, bottom = parse_bounds(item.attrib.get("bounds", ""))
        if not name or right - left < 20 or bottom <= top:
            continue
        children.append(
            {
                "名称": name,
                "编号": code,
                "发售日期": date,
                "x": (left + right) // 2,
                "y": (top + bottom) // 2,
            }
        )
    return children


def child_strip_signature(root: ET.Element) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (
            str(child.get("编号", "")),
            str(child.get("名称", "")),
            str(child.get("发售日期", "")),
        )
        for child in parse_child_entries(root)
    )


def selected_home_product_matches(
    root: ET.Element,
    home_product: dict[str, object],
) -> bool:
    """Return True when packRv itself says the target parent is selected.

    This is important for series where selecting the big category automatically
    selects its first product before we tap it.  In that case childPackRv is
    already populated in the baseline XML, so a signature-change test alone
    cannot prove ownership of the child strip.
    """
    strip = find_node(root, "com.jihuanshe:id/packRv")
    if strip is None:
        return False

    target_name = str(home_product.get("名称", ""))
    target_code = normalize_label(str(home_product.get("编号", "")))

    for item in list(strip):
        if item.attrib.get("selected") != "true":
            continue
        texts = all_texts(item)
        code = child_text(item, "com.jihuanshe:id/numberTv")
        if not code:
            code = child_text(item, "com.jihuanshe:id/packNumTv")
        if not code and len(texts) >= 2:
            code = texts[-1]
        name_candidates = [value for value in texts if value != code]
        name = name_candidates[0] if name_candidates else (texts[0] if texts else "")

        if homepage_name_match_strength(name, target_name):
            return True

        # Fail closed for name mismatches.  Code-only is not sufficient here,
        # because the same code can identify several distinct homepage products.
        if target_code and normalize_label(code) == target_code:
            continue

    return False


def child_strip_matches_product(
    root: ET.Element,
    home_product: dict[str, object],
    baseline_signature: tuple[tuple[str, str, str], ...],
) -> bool:
    children = parse_child_entries(root)
    if not children:
        return False

    # Strongest signal: the homepage product card is explicitly selected.
    # This also handles the first product being auto-selected when a category
    # tab is opened, where baseline and current child-strip signatures are equal.
    if selected_home_product_matches(root, home_product):
        return True

    current_signature = child_strip_signature(root)
    if current_signature and current_signature != baseline_signature:
        return True

    target_code = normalize_label(str(home_product.get("编号", "")))
    child_codes = {
        normalize_label(str(child.get("编号", "")))
        for child in children
        if str(child.get("编号", "")).strip()
    }
    if target_code and child_codes:
        return any(
            child_code == target_code
            or child_code.startswith(target_code)
            or target_code.startswith(child_code)
            for child_code in child_codes
        )
    return False


def reset_child_strip_to_start(
    args: argparse.Namespace,
    local_xml: Path,
) -> None:
    previous: tuple[tuple[str, ...], ...] | None = None
    for _ in range(args.max_child_swipes):
        dump_current_page(args.adb, args.serial, local_xml)
        root = ET.parse(local_xml).getroot()
        strip = find_node(root, "com.jihuanshe:id/childPackRv")
        if strip is None:
            raise RuntimeError("子类横向栏消失。")
        sig = node_signature(strip)
        if sig and sig == previous:
            return
        previous = sig
        swipe_node_horizontal(args.adb, args.serial, strip, "right")
        time.sleep(args.page_wait)
    raise RuntimeError("子类横向栏无法回到起点。")


def child_identity(child: dict[str, object]) -> tuple[str, str, str]:
    return (
        str(child.get("编号", "")),
        str(child.get("名称", "")),
        str(child.get("发售日期", "")),
    )


def classify_child_strip_quick(
    args: argparse.Namespace,
    local_xml: Path,
    initial_root: ET.Element | None = None,
) -> dict[str, object]:
    """快速判断子类是 1 个还是 >=2 个，不枚举完整多子类清单。

    v4 的策略：
    - 当前画面一旦看到 >=2 个不同子类，立刻判为多子类。
    - 若只看到 1 个，且 childPackRv 明确 scrollable=false，则只做一次快速
      稳定确认；两次结果一致即可判为单子类，不再固定探测 3 屏。
    - 只有 scrollable=true 且当前只看到 1 个时，才主动横向滑动探测。

    initial_root 来自“刚刚选中产品”的真实首页 XML，可避免一次额外 dump。
    """
    seen: OrderedDict[tuple[str, str, str], dict[str, object]] = OrderedDict()
    previous_signature: tuple[tuple[str, str, str], ...] | None = None
    probe_limit = max(1, min(args.child_probe_swipes, args.max_child_swipes))
    root = initial_root

    for page in range(probe_limit + 1):
        if root is None:
            dump_current_page(args.adb, args.serial, local_xml)
            root = ET.parse(local_xml).getroot()

        strip = find_node(root, "com.jihuanshe:id/childPackRv")
        if strip is None:
            raise RuntimeError("子类横向栏消失。")
        children = parse_child_entries(root)
        signature = tuple(child_identity(child) for child in children)
        for child in children:
            seen.setdefault(child_identity(child), child)

        scrollable = strip.attrib.get("scrollable") == "true"
        print(
            f"      子类快速判定·第 {page + 1} 屏："
            f"当前可见 {len(children)}，已见不同子类 {len(seen)}，"
            f"可横向滚动 {'是' if scrollable else '否'}",
            flush=True,
        )

        if len(seen) >= 2:
            print(
                "      已确认至少 2 个子类；立即标记为多子类，不再横向枚举全部子类。",
                flush=True,
            )
            return {"类型": "多子类", "样例": list(seen.values())[:2]}

        # 只有一个子类且 RecyclerView 报告不可横向滚动时，仍主动尝试一次横滑。
        # 这样即使某些 Android 版本把 scrollable 错报为 false，也不会仅凭该属性
        # 把“当前只露出一个、后面仍有子类”的父产品误判成单子类。
        if len(seen) == 1 and not scrollable:
            swipe_node_horizontal(args.adb, args.serial, strip, "left")
            time.sleep(args.child_stable_wait)
            dump_current_page(args.adb, args.serial, local_xml)
            confirm_root = ET.parse(local_xml).getroot()
            confirm_strip = find_node(confirm_root, "com.jihuanshe:id/childPackRv")
            if confirm_strip is None:
                raise RuntimeError("单子类稳定确认时子类横向栏消失。")
            confirm_children = parse_child_entries(confirm_root)
            confirm_ids = tuple(child_identity(child) for child in confirm_children)
            confirm_seen = OrderedDict((child_identity(child), child) for child in confirm_children)
            confirm_scrollable = confirm_strip.attrib.get("scrollable") == "true"

            if len(confirm_seen) >= 2:
                print(
                    "      稳定确认时出现第 2 个子类；判定为多子类。",
                    flush=True,
                )
                return {"类型": "多子类", "样例": list(confirm_seen.values())[:2]}

            if (
                len(confirm_seen) == 1
                and not confirm_scrollable
                and confirm_ids == signature
            ):
                only_child = next(iter(confirm_seen.values()))
                print(
                    "      单子类栏快速横滑后仍稳定且不可继续滚动；按单子类处理。",
                    flush=True,
                )
                return {"类型": "单子类", "子类": only_child, "root": confirm_root}

            # 稳定确认后变成可滚动，说明初始状态可能尚未加载完整；继续主动探测。
            root = confirm_root
            previous_signature = confirm_ids
            continue

        # scrollable=true 且只看到一个：主动横向探测。只要出现第二个就立即退出。
        if len(seen) == 1 and scrollable:
            if page >= probe_limit:
                break
            previous_signature = signature
            swipe_node_horizontal(args.adb, args.serial, strip, "left")
            time.sleep(args.child_probe_wait)
            root = None
            continue

        # 当前还没有可用子类，短暂等待异步加载。
        if not children:
            if page >= probe_limit:
                break
            time.sleep(args.child_probe_wait)
            root = None
            continue

        # 兜底：签名已经稳定且只有一个 identity，接受为单子类。
        if len(seen) == 1 and signature and signature == previous_signature:
            return {"类型": "单子类", "子类": next(iter(seen.values())), "root": root}

        previous_signature = signature
        if page >= probe_limit:
            break
        time.sleep(args.child_probe_wait)
        root = None

    if len(seen) >= 2:
        return {"类型": "多子类", "样例": list(seen.values())[:2]}
    if len(seen) == 1:
        return {"类型": "单子类", "子类": next(iter(seen.values())), "root": root}
    raise RuntimeError("子类横向栏没有读取到任何子类。")


# ---------------------------------------------------------------------------
# 详情页读取
# ---------------------------------------------------------------------------


def detail_header_from_root(root: ET.Element) -> dict[str, object]:
    name = first_text(root, "com.jihuanshe:id/tvNameCN")
    code = first_text(root, "com.jihuanshe:id/tvNumber")
    if not name and not code:
        raise RuntimeError("当前不是产品详情页。")

    date = first_matching(
        descendant_texts(root, "com.jihuanshe:id/llDate"),
        r"(\d{4}-\d{1,2}-\d{1,2})",
    )
    count_texts = descendant_texts(root, "com.jihuanshe:id/llCount")
    page_count_raw = next(
        (value for value in count_texts if re.search(r"\d[\d,]*\s*张", value)),
        "",
    )
    if not page_count_raw:
        page_count_raw = " ".join(count_texts)
    recorded_raw = first_text(root, "com.jihuanshe:id/tvTopCommentsNum")

    return {
        "详情名称": name,
        "详情编号": code,
        "发售日期": date,
        "页面张数": parse_int(page_count_raw),
        "收录商品数量": parse_int(recorded_raw),
    }


def wait_for_detail_header(
    args: argparse.Namespace,
    local_xml: Path,
) -> dict[str, object]:
    """读取详情页头；字段齐全立即返回，部分字段稳定后再接受。"""
    deadline = time.monotonic() + args.header_timeout
    started = time.monotonic()
    last: dict[str, object] | None = None
    last_signature: tuple[object, ...] | None = None
    stable_repeats = 0
    last_error = ""

    while time.monotonic() < deadline:
        try:
            dump_current_page(args.adb, args.serial, local_xml)
            root = ET.parse(local_xml).getroot()
            header = detail_header_from_root(root)
            last = header
            signature = (
                header.get("详情编号"),
                header.get("详情名称"),
                header.get("发售日期"),
                header.get("页面张数"),
                header.get("收录商品数量"),
            )

            complete = (
                bool(header.get("发售日期"))
                and header.get("页面张数") is not None
                and header.get("收录商品数量") is not None
            )
            if complete:
                return header

            has_any_stat = (
                bool(header.get("发售日期"))
                or header.get("页面张数") is not None
                or header.get("收录商品数量") is not None
            )
            if signature == last_signature:
                stable_repeats += 1
            else:
                stable_repeats = 0
            last_signature = signature

            # 对页面确实缺字段的产品，不必硬等完整 header_timeout；
            # 但至少给异步字段一个短暂加载窗口，并要求连续稳定。
            if (
                has_any_stat
                and time.monotonic() - started >= args.detail_min_wait
                and stable_repeats >= args.detail_stable_repeats
            ):
                return header
        except Exception as exc:
            last_error = str(exc)

        time.sleep(args.detail_poll_wait)

    if last is not None:
        return last
    raise RuntimeError(f"进入详情页后未能读取页头。{last_error}")


# ---------------------------------------------------------------------------
# 单产品处理
# ---------------------------------------------------------------------------


def click_home_product_and_classify(
    args: argparse.Namespace,
    local_xml: Path,
    home_product: dict[str, object],
    root_before: ET.Element | None = None,
) -> dict[str, object]:
    """点击首页产品，只分类为：详情 / 单子类 / 多子类。

    v4 会在信息足够时提前结束等待：
    - 子类栏一出现就立即进入快速分类；
    - 同名详情卡片仅等待一个短保护窗口，确认没有迟到的子类栏后就点击；
    - 不再无条件等待完整 summary_timeout。
    """
    if root_before is None:
        root_before = ensure_library_home(
            args.adb, args.serial, local_xml, args.page_wait, args.language
        )
    baseline_children = child_strip_signature(root_before)
    raw_points = home_product.get("点击坐标")
    points = (
        list(raw_points)
        if isinstance(raw_points, list)
        else [(int(home_product["x"]), int(home_product["y"]))]
    )

    last_message = "点击后页面没有变化"
    for x, y in points:
        tap_xy(args.adb, args.serial, int(x), int(y))
        summary_first_seen: float | None = None
        deadline = time.monotonic() + max(args.summary_timeout, args.open_wait, 2.0)

        while time.monotonic() < deadline:
            time.sleep(args.select_poll_wait)
            dump_current_page(args.adb, args.serial, local_xml)
            root = ET.parse(local_xml).getroot()

            # 有些首页卡片会直接打开详情。
            if (
                first_text(root, "com.jihuanshe:id/tvNameCN")
                or first_text(root, "com.jihuanshe:id/tvNumber")
            ):
                return {
                    "类型": "详情",
                    "入口类型": "直接详情",
                    "header": wait_for_detail_header(args, local_xml),
                }

            if not is_library_home(root):
                last_message = "点击后进入了非首页、非详情页面"
                continue

            # 子类栏一旦确认属于当前产品，立即分类；不再等完整观察窗口。
            child_strip = find_node(root, "com.jihuanshe:id/childPackRv")
            if child_strip is not None and child_strip_matches_product(
                root, home_product, baseline_children
            ):
                classified = classify_child_strip_quick(
                    args, local_xml, initial_root=root
                )
                if classified.get("类型") == "多子类":
                    return {
                        "类型": "多子类",
                        "入口类型": "多子类待处理",
                        "样例": classified.get("样例", []),
                    }

                target = classified.get("子类")
                if not isinstance(target, dict):
                    raise RuntimeError("单子类判定没有返回有效子类。")
                target_identity = child_identity(target)

                # 优先复用分类阶段最后一次真实 XML，避免“回到起点 + 再 dump”。
                classified_root = classified.get("root")
                current_root = classified_root if isinstance(classified_root, ET.Element) else None
                if current_root is None:
                    dump_current_page(args.adb, args.serial, local_xml)
                    current_root = ET.parse(local_xml).getroot()
                visible = parse_child_entries(current_root)
                chosen = next(
                    (child for child in visible if child_identity(child) == target_identity),
                    None,
                )

                # 只有主动横向探测把唯一子类移出可点击区域时才回到起点。
                if chosen is None:
                    reset_child_strip_to_start(args, local_xml)
                    dump_current_page(args.adb, args.serial, local_xml)
                    current_root = ET.parse(local_xml).getroot()
                    visible = parse_child_entries(current_root)
                    chosen = next(
                        (child for child in visible if child_identity(child) == target_identity),
                        None,
                    )
                if chosen is None:
                    raise RuntimeError("唯一子类没有出现在可点击区域。")

                print(
                    f"      确认只有 1 个子类：{target.get('名称', '')}；进入详情……",
                    flush=True,
                )
                tap_xy(args.adb, args.serial, int(chosen["x"]), int(chosen["y"]))
                return {
                    "类型": "详情",
                    "入口类型": "单一子类",
                    "header": wait_for_detail_header(args, local_xml),
                    "子类": [target],
                }

            summary = find_matching_summary(root, str(home_product.get("名称", "")))
            if summary is not None:
                if summary_first_seen is None:
                    summary_first_seen = time.monotonic()
                    last_message = "检测到同名详情卡片，短暂等待迟到的子类栏"
                elif time.monotonic() - summary_first_seen >= args.summary_guard_wait:
                    print(
                        "      同名详情卡片稳定，保护窗口内未出现子类栏；进入详情页读取……",
                        flush=True,
                    )
                    tap_node_center(args.adb, args.serial, summary)
                    return {
                        "类型": "详情",
                        "入口类型": "单一详情卡片",
                        "header": wait_for_detail_header(args, local_xml),
                    }
            else:
                summary_first_seen = None
                last_message = "尚未检测到当前产品的详情入口"

        # 超时前若已经见过 summary，但保护窗口未触发，再做一次新鲜 dump 后点击。
        dump_current_page(args.adb, args.serial, local_xml)
        root = ET.parse(local_xml).getroot()
        if is_library_home(root):
            child_strip = find_node(root, "com.jihuanshe:id/childPackRv")
            if child_strip is not None and child_strip_matches_product(
                root, home_product, baseline_children
            ):
                classified = classify_child_strip_quick(args, local_xml, initial_root=root)
                if classified.get("类型") == "多子类":
                    return {
                        "类型": "多子类",
                        "入口类型": "多子类待处理",
                        "样例": classified.get("样例", []),
                    }
            summary = find_matching_summary(root, str(home_product.get("名称", "")))
            if summary is not None:
                print("      确认只有单一详情卡片，进入详情页读取……", flush=True)
                tap_node_center(args.adb, args.serial, summary)
                return {
                    "类型": "详情",
                    "入口类型": "单一详情卡片",
                    "header": wait_for_detail_header(args, local_xml),
                }

        # 这个点击点没有选中产品，恢复首页后尝试下一点击坐标。
        root_before = ensure_library_home(
            args.adb, args.serial, local_xml, args.page_wait, args.language
        )
        baseline_children = child_strip_signature(root_before)

    raise RuntimeError(f"无法判断产品入口类型：{last_message}")


def replace_multi_parent(
    multi_rows: list[dict[str, object]],
    section: str,
    code: str,
    name: str,
) -> None:
    """多子类本阶段只保留父产品名称，不保存/不枚举全部子类。"""
    multi_rows[:] = [
        row
        for row in multi_rows
        if not (
            normalize_label(str(row.get("系列", ""))) == normalize_label(section)
            and str(row.get("父列表编号", "")) == code
            and str(row.get("父列表名称", "")) == name
        )
    ]
    multi_rows.append(
        {
            "系列": section,
            "父列表编号": code,
            "父列表名称": name,
            "统计状态": "多子类待处理",
            "统计时间": now_text(),
            "备注": "已确认至少 2 个子类；本阶段不枚举子类、不统计商品格。",
        }
    )


def process_product(
    args: argparse.Namespace,
    local_xml: Path,
    key: str,
    home_product: dict[str, object],
    home_root: ET.Element | None,
    inventory: OrderedDict[str, dict[str, str]],
    results: OrderedDict[str, dict[str, str]],
    multi_rows: list[dict[str, object]],
    details_path: Path,
    multi_path: Path,
) -> None:
    row = inventory[key]
    section = row.get("系列", "")
    list_code = row.get("列表编号", "")
    list_name = row.get("列表名称", "")
    status = "失败"
    note = ""
    entry_type = ""
    detail_code = ""
    detail_name = ""
    release_date = ""
    page_count: int | None = None
    recorded_count: int | None = None
    child_count = 0
    entered_detail = False
    failed = False

    try:
        assert_home_product_matches_inventory(
            section, home_product, row, inventory
        )
        print(
            f"      首页身份确认：{home_product.get('编号', '')} "
            f"{home_product.get('名称', '')}",
            flush=True,
        )
        selected = click_home_product_and_classify(args, local_xml, home_product, root_before=home_root)
        entry_type = str(selected.get("入口类型", ""))
        if selected.get("类型") == "多子类":
            replace_multi_parent(multi_rows, section, list_code, list_name)
            status = "多子类待处理"
            child_count = 0  # 不保存伪精确数量；这里只确认“>=2”。
            note = "已确认至少 2 个子类；本阶段只保留父产品名称，不枚举子类，不统计商品格。"
            print(
                "      多子类已确认；只保存父产品名称，后续处理。",
                flush=True,
            )
        else:
            entered_detail = True
            header = selected.get("header")
            if not isinstance(header, dict):
                raise RuntimeError("详情入口没有返回有效页头。")
            detail_code = str(header.get("详情编号", ""))
            detail_name = str(header.get("详情名称", ""))
            release_date = str(header.get("发售日期", ""))
            page_count = header.get("页面张数")  # type: ignore[assignment]
            recorded_count = header.get("收录商品数量")  # type: ignore[assignment]
            child_list = selected.get("子类")
            child_count = len(child_list) if isinstance(child_list, list) else 1

            missing: list[str] = []
            if not release_date:
                missing.append("页面未显示发售日期")
            if page_count is None:
                missing.append("页面未读取到张数")
            if recorded_count is None:
                missing.append("页面未读取到收录商品数量")
            note = "；".join(missing)
            status = "完成" if not missing else "完成（有缺项）"
            print(
                "      单详情完成："
                f"日期 {release_date or '-'}，"
                f"页面张数 {page_count if page_count is not None else '-'}，"
                f"收录商品 {recorded_count if recorded_count is not None else '-'}",
                flush=True,
            )
    except Exception as exc:
        failed = True
        note = str(exc).replace("\n", " ")[:700]
        print(f"      失败：{note}", flush=True)
        try:
            debug_path = details_path.with_name("jhs_phase2_last_failed.xml")
            debug_path.write_bytes(local_xml.read_bytes())
            print(f"      已保存诊断 XML：{debug_path}", flush=True)
        except OSError:
            pass
    finally:
        # 成功进入详情时直接按一次 Back，不再先 dump 详情页再决定返回；
        # 下一轮主循环会统一 ensure_library_home 做最终确认。
        if entered_detail:
            run_adb(
                args.adb,
                args.serial,
                "shell",
                "input",
                "keyevent",
                "4",
                check=False,
            )
            time.sleep(args.back_wait)
        elif failed:
            # 失败时页面状态不确定，仍走保守恢复。
            try:
                ensure_library_home(
                    args.adb, args.serial, local_xml, args.page_wait, args.language
                )
            except Exception:
                run_adb(
                    args.adb,
                    args.serial,
                    "shell",
                    "input",
                    "keyevent",
                    "4",
                    check=False,
                )
                time.sleep(args.back_wait)

    results[key] = {
        "系列": section,
        "列表编号": list_code,
        "列表名称": list_name,
        "首页编号": str(home_product.get("编号", "")),
        "首页名称": str(home_product.get("名称", "")),
        "入口类型": entry_type,
        "详情编号": detail_code,
        "详情名称": detail_name,
        "发售日期": release_date,
        "页面张数": "" if page_count is None else str(page_count),
        "收录商品数量": "" if recorded_count is None else str(recorded_count),
        "子类数量": "" if child_count == 0 else str(child_count),
        "统计状态": status,
        "统计时间": now_text(),
        "备注": note,
    }
    save_outputs(inventory, results, multi_rows, details_path, multi_path)


# ---------------------------------------------------------------------------
# 主扫描
# ---------------------------------------------------------------------------


def only_filter_matches(query: str, code: str, name: str) -> bool:
    query = (query or "").strip()
    if not query:
        return True
    if normalize_label(query) == normalize_label(code):
        return True
    return normalize_label(query) in normalize_label(name)


def ordered_sections(
    inventory: OrderedDict[str, dict[str, str]],
    start_series: str,
) -> list[str]:
    sections: list[str] = []
    for row in inventory.values():
        section = row.get("系列", "")
        if section and section not in sections:
            sections.append(section)
    if not start_series:
        return sections
    target = normalize_label(start_series)
    for index, section in enumerate(sections):
        if normalize_label(section) == target:
            return sections[index:]
    raise RuntimeError(f"清单中找不到起始系列：{start_series}")


def scan_phase2(
    args: argparse.Namespace,
    local_xml: Path,
    inventory: OrderedDict[str, dict[str, str]],
    results: OrderedDict[str, dict[str, str]],
    multi_rows: list[dict[str, object]],
    details_path: Path,
    multi_path: Path,
) -> int:
    ensure_library_home(
        args.adb, args.serial, local_xml, args.page_wait, args.language
    )
    sections = ordered_sections(inventory, args.start_series)
    attempted = 0
    attempted_this_run: set[str] = set()

    print("\n第二步：单详情直读 / 多子类父产品只保留名称。", flush=True)
    print("本版本不会统计多子类父产品的商品格。", flush=True)

    for section in sections:
        pending = {
            key
            for key, row in inventory.items()
            if normalize_label(row.get("系列", "")) == normalize_label(section)
            and only_filter_matches(
                args.only, row.get("列表编号", ""), row.get("列表名称", "")
            )
            and key not in attempted_this_run
            and (args.retry_all or args.force_only or not result_finished(results.get(key)))
        }
        if not pending:
            continue

        print(f"\n定位首页大系列：{section}", flush=True)
        try:
            select_home_series(
                args.adb,
                args.serial,
                local_xml,
                section,
                args.page_wait,
                args.language,
            )
        except Exception as exc:
            print(f"  系列定位失败：{exc}", flush=True)
            continue

        reset_horizontal_to_start(
            args.adb,
            args.serial,
            local_xml,
            "com.jihuanshe:id/packRv",
            args.page_wait,
            args.language,
        )
        previous_signature: tuple[tuple[str, str], ...] | None = None
        unchanged = 0

        for strip_page in range(args.max_list_swipes + 1):
            root = ensure_library_home(
                args.adb, args.serial, local_xml, args.page_wait, args.language
            )
            products = parse_home_products(root)
            signature = tuple(
                (str(item.get("编号", "")), str(item.get("名称", "")))
                for item in products
            )

            remaining = {
                key
                for key in pending
                if key not in attempted_this_run
                and (args.retry_all or args.force_only or not result_finished(results.get(key)))
            }
            if not remaining:
                break

            selected: tuple[str, dict[str, object]] | None = None
            for product in products:
                key = match_home_product(section, product, inventory, remaining)
                if key is not None:
                    selected = (key, product)
                    break

            if selected is not None:
                if args.limit and attempted >= args.limit:
                    return attempted
                key, product = selected
                attempted += 1
                attempted_this_run.add(key)
                row = inventory[key]
                print(
                    f"\n[{attempted}] {section} | "
                    f"{row.get('列表编号', '')} {row.get('列表名称', '')}",
                    flush=True,
                )
                process_product(
                    args,
                    local_xml,
                    key,
                    product,
                    root,
                    inventory,
                    results,
                    multi_rows,
                    details_path,
                    multi_path,
                )
                previous_signature = None
                unchanged = 0
                continue

            unchanged = unchanged + 1 if signature and signature == previous_signature else 0
            if unchanged >= 2:
                break
            if strip_page >= args.max_list_swipes:
                raise RuntimeError(f"{section} 首页产品横向栏达到最大滚动次数。")
            previous_signature = signature
            strip = find_node(root, "com.jihuanshe:id/packRv")
            if strip is None:
                raise RuntimeError("首页产品横向栏消失。")
            swipe_node_horizontal(args.adb, args.serial, strip, "left")
            time.sleep(args.page_wait)

        not_found = [
            key
            for key in pending
            if key not in attempted_this_run
            and (args.retry_all or args.force_only or not result_finished(results.get(key)))
        ]
        if not_found:
            print(
                f"  提示：{section} 仍有 {len(not_found)} 个清单产品没有在首页匹配到。",
                flush=True,
            )

    return attempted


def main() -> int:
    parser = argparse.ArgumentParser(
        description="集换社迪士尼洛卡纳第二步：单详情直读，多子类只保存父产品名称。"
    )
    parser.add_argument("--adb", default=DEFAULT_ADB, help="adb.exe 完整路径")
    parser.add_argument("--serial", default=DEFAULT_SERIAL, help="ADB 设备序列号")
    parser.add_argument(
        "--language",
        choices=LANGUAGE_CHOICES,
        default="英文",
        help="当前手动选择的迪士尼洛卡纳语言",
    )
    parser.add_argument("--input", default="", help="第 1 步产品清单 CSV；默认按语言自动选择")
    parser.add_argument("--output", default="", help="单详情/父产品状态输出 CSV")
    parser.add_argument("--multi-output", default="", help="多子类父产品清单输出 CSV")
    parser.add_argument("--only", default="", help="只测试一个编号或名称，例如 --only HYC")
    parser.add_argument("--start-series", default="", help="从指定大系列开始")
    parser.add_argument("--limit", type=int, default=0, help="本次最多处理多少个产品；0 不限制")
    parser.add_argument("--retry-all", action="store_true", help="忽略断点，重新处理已完成产品")
    parser.add_argument(
        "--force-only",
        action="store_true",
        help="与 --only 配合：只强制重抓匹配到的已完成产品，不清空其它断点结果",
    )
    parser.add_argument("--page-wait", type=float, default=0.9, help="首页/系列横向滚动后的等待秒数")
    parser.add_argument("--open-wait", type=float, default=1.2, help="点击首页产品后的基础等待秒数")
    parser.add_argument("--summary-timeout", type=float, default=3.2, help="产品入口识别的最长秒数")
    parser.add_argument("--summary-guard-wait", type=float, default=1.5, help="同名详情卡出现后等待迟到子类栏的保护秒数")
    parser.add_argument("--select-poll-wait", type=float, default=0.25, help="点击产品后两次 UI dump 之间的最小等待")
    parser.add_argument("--header-timeout", type=float, default=6.0)
    parser.add_argument("--detail-poll-wait", type=float, default=0.30, help="详情页异步字段轮询间隔")
    parser.add_argument("--detail-min-wait", type=float, default=0.9, help="详情字段不完整时至少等待多久再接受稳定结果")
    parser.add_argument("--detail-stable-repeats", type=int, default=1, help="详情字段不完整时连续相同多少次后接受")
    parser.add_argument("--back-wait", type=float, default=0.7, help="详情页按 Back 后的等待秒数")
    parser.add_argument("--max-list-swipes", type=int, default=160)
    parser.add_argument("--max-child-swipes", type=int, default=80)
    parser.add_argument("--child-probe-swipes", type=int, default=3, help="只有子类栏可横向滚动且当前仅 1 个时的最大探测次数")
    parser.add_argument("--child-probe-wait", type=float, default=0.55, help="子类主动横向探测后的等待秒数")
    parser.add_argument("--child-stable-wait", type=float, default=0.45, help="不可滚动单子类的快速稳定确认等待秒数")
    args = parser.parse_args()

    if args.force_only and not args.only.strip():
        print("--force-only 必须与 --only 一起使用，避免误重抓全部产品。", file=sys.stderr)
        return 2

    if not Path(args.adb).exists():
        print(f"找不到 adb.exe：{args.adb}", file=sys.stderr)
        return 1

    script_dir = Path(__file__).resolve().parent
    inventory_path = Path(args.input) if args.input else script_dir / default_inventory_name(args.language)
    default_details, default_multi = default_output_names(args.language)
    details_path = Path(args.output) if args.output else script_dir / default_details
    multi_path = Path(args.multi_output) if args.multi_output else script_dir / default_multi

    if not inventory_path.exists():
        print(f"找不到第 1 步产品清单：{inventory_path}", file=sys.stderr)
        return 1

    inventory = load_inventory(inventory_path)
    if not inventory:
        print(f"产品清单为空：{inventory_path}", file=sys.stderr)
        return 1
    results = load_detail_results(details_path)
    multi_rows: list[dict[str, object]] = [dict(row) for row in read_csv(multi_path)]

    if args.retry_all:
        results.clear()
        multi_rows.clear()
    else:
        results, reused_count = reconcile_results_to_inventory(inventory, results)
        if reused_count:
            print(f"增量对齐：{reused_count} 个旧产品仅显示名称/原始文本变化，按唯一编号沿用已有第二步结果，不重新抓取。")

    print(f"脚本版本：{SCRIPT_VERSION}")
    print(f"目标语言：{args.language}")
    print(f"产品清单：{inventory_path}")
    print(f"单详情输出：{details_path}")
    print(f"多子类输出：{multi_path}")
    print("规则：单一详情直接读取；检测到 >=2 个子类时只保存父产品名称，不枚举子类、不数商品格。")
    print("速度策略：不可滚动的单子类只做一次快速稳定确认；多子类看到第 2 个立即结束。")
    if args.force_only:
        print(f"定向重抓：{args.only}（保留其它已完成结果）")

    try:
        with tempfile.TemporaryDirectory(prefix="jhs_phase2_") as temp_dir:
            local_xml = Path(temp_dir) / "page.xml"
            attempted = scan_phase2(
                args,
                local_xml,
                inventory,
                results,
                multi_rows,
                details_path,
                multi_path,
            )
            save_outputs(inventory, results, multi_rows, details_path, multi_path)
            finished = sum(result_finished(results.get(key)) for key in inventory)
            multi_count = sum(
                1
                for key in inventory
                if results.get(key, {}).get("统计状态") == "多子类待处理"
            )
            print("\n本次第二步结束")
            print(f"本次尝试：{attempted}")
            print(f"已完成/已分类：{finished} / {len(inventory)}")
            print(f"其中多子类待处理：{multi_count}")
            print(f"单详情结果：{details_path}")
            print(f"多子类父产品：{multi_path}")
            return 0
    except KeyboardInterrupt:
        save_outputs(inventory, results, multi_rows, details_path, multi_path)
        print("\n已手动停止；已完成产品已经保存。")
        return 130
    except Exception as exc:
        save_outputs(inventory, results, multi_rows, details_path, multi_path)
        print(f"\n扫描停止：{exc}", file=sys.stderr)
        print("已完成产品已经保存。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
