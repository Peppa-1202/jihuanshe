#!/usr/bin/env python3
"""集换社：迪士尼洛卡纳英文专用版。

Phase1：从分类页动态发现左侧大类，扫描产品清单；保留双遍核验及 --resume。
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
REMOTE_XML = "/sdcard/Download/jhs_lorcana_en_phase1.xml"
SCRIPT_VERSION = "2026-11-11-lorcana-en-phase1-v1"

LANGUAGE_CHOICES = ("英文",)


def default_output_names(language: str) -> tuple[str, str]:
    return "jhs_lorcana_en_product_inventory.csv", "jhs_lorcana_en_inventory_counts.csv"


INVENTORY_COLUMNS = [
    "系列",
    "列表编号",
    "列表名称",
    "产品唯一键",
    "发现顺序",
    "清单扫描时间",
]

COUNT_COLUMNS = ["系列", "产品数量"]


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


def child_text(item: ET.Element, resource_id: str) -> str:
    for node in item.iter("node"):
        if node.attrib.get("resource-id") == resource_id:
            return node.attrib.get("text", "").strip()
    return ""


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


def normalize_label(value: str) -> str:
    """用于系列名称比较；忽略空格、常见标点以及全/半角 &。"""
    return re.sub(
        r"[\s\u3000\-‐‑‒–—―·・:：,，.。'\"“”‘’「」『』【】\[\]（）()&＆]+",
        "",
        (value or "").casefold(),
    )


def same_label(left: str, right: str) -> bool:
    return normalize_label(left) == normalize_label(right)


def product_key(section: str, list_code: str, list_name: str) -> str:
    return "\x1f".join((section.strip(), list_code.strip(), list_name.strip()))


def write_csv(path: Path, columns: list[str], rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temp_path.replace(path)


def load_reference_identities(path: Path, target: str) -> set[tuple[str, str]]:
    """Load identities belonging to every series except target from an old inventory.

    This is used by --only-series for the final series.  Because the rest of the
    series are intentionally not rescanned in that mode, their verified identities
    must come from the previous inventory so stale RecyclerView children can still
    be recognized as a boundary.
    """
    if not path.exists() or path.stat().st_size == 0:
        return set()
    identities: set[tuple[str, str]] = set()
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            section = str(row.get("系列", ""))
            if same_label(section, target):
                continue
            code = str(row.get("列表编号", "")).strip()
            name = str(row.get("列表名称", "")).strip()
            if code or name:
                identities.add((code, name))
    return identities


def load_resume_prefix(
    path: Path,
    discovered_order: list[str],
) -> OrderedDict[str, list[dict[str, object]]]:
    """Load only the consecutive already-saved series prefix from an inventory CSV.

    The scanner writes the file only after a series has passed all verification
    passes, so a consecutive prefix is a safe restart checkpoint.  Anything after
    the first missing discovered series is deliberately ignored to avoid mixing a
    stale older run into the new scan.
    """
    result: OrderedDict[str, list[dict[str, object]]] = OrderedDict()
    if not path.exists() or path.stat().st_size == 0:
        return result

    grouped: OrderedDict[str, list[dict[str, object]]] = OrderedDict()
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            section = str(row.get("系列", "")).strip()
            code = str(row.get("列表编号", "")).strip()
            name = str(row.get("列表名称", "")).strip()
            if not section or not name:
                continue
            grouped.setdefault(section, []).append(
                {
                    "系列": section,
                    "列表编号": code,
                    "列表名称": name,
                    "x": 0,
                    "y": 0,
                }
            )

    for expected in discovered_order:
        actual_key = next((key for key in grouped if same_label(key, expected)), "")
        if not actual_key:
            break
        rows = grouped[actual_key]
        if not rows:
            break
        # Normalize the section label to the current UI spelling/order.
        normalized_rows = []
        for row in rows:
            copy = dict(row)
            copy["系列"] = expected
            normalized_rows.append(copy)
        result[expected] = normalized_rows

    return result


def screen_size(adb_path: str, serial: str) -> tuple[int, int]:
    output = run_adb(adb_path, serial, "shell", "wm", "size")
    matches = re.findall(r"(\d+)x(\d+)", output)
    if not matches:
        return 900, 1600
    width, height = matches[-1]
    return int(width), int(height)


def tap_xy(adb_path: str, serial: str, x: int, y: int) -> None:
    run_adb(adb_path, serial, "shell", "input", "tap", str(x), str(y))


def tap_node_center(adb_path: str, serial: str, node: ET.Element) -> None:
    left, top, right, bottom = parse_bounds(node.attrib.get("bounds", ""))
    if right <= left or bottom <= top:
        raise RuntimeError("目标没有有效坐标。")
    tap_xy(adb_path, serial, (left + right) // 2, (top + bottom) // 2)


def swipe_left_series(adb_path: str, serial: str, direction: str = "up") -> None:
    """只在左侧系列栏内滑动，避免带动右侧小类列表。"""
    width, height = screen_size(adb_path, serial)
    x = int(width * 0.12)
    if direction == "up":
        start_y, end_y = int(height * 0.82), int(height * 0.35)
    else:
        start_y, end_y = int(height * 0.35), int(height * 0.82)
    run_adb(
        adb_path,
        serial,
        "shell",
        "input",
        "swipe",
        str(x),
        str(start_y),
        str(x),
        str(end_y),
        "450",
    )


def swipe_right_pack_up(adb_path: str, serial: str, pack: ET.Element) -> None:
    """在右侧小类区域小步上滑，保留足够重叠，降低漏行风险。"""
    left, top, right, bottom = parse_bounds(pack.attrib.get("bounds", ""))
    if right <= left or bottom - top < 180:
        raise RuntimeError("右侧小类列表没有有效坐标。")

    # 只滚动约 1/3 屏，刻意保留大量重叠；速度慢一点，但更不容易漏小类。
    x = int(left + (right - left) * 0.62)
    start_y = int(top + (bottom - top) * 0.78)
    end_y = int(top + (bottom - top) * 0.46)
    run_adb(
        adb_path,
        serial,
        "shell",
        "input",
        "swipe",
        str(x),
        str(start_y),
        str(x),
        str(end_y),
        "450",
    )


# ---------------------------------------------------------------------------
# 进入分类页 / 左侧大类选择
# ---------------------------------------------------------------------------


def is_category_page(root: ET.Element) -> bool:
    return find_node(root, "com.jihuanshe:id/pack") is not None


def is_library_home(root: ET.Element) -> bool:
    return find_node(root, "com.jihuanshe:id/categoryBtn") is not None


def ensure_category_page(
    adb_path: str,
    serial: str,
    local_xml: Path,
    wait_seconds: float,
    expected_language: str = "",
) -> ET.Element:
    """确保当前在“分类”页；如果在首页则点分类，如果在详情页则后退。"""
    for _ in range(7):
        dump_current_page(adb_path, serial, local_xml)
        root = ET.parse(local_xml).getroot()

        if is_category_page(root):
            return root

        category_button = find_node(root, "com.jihuanshe:id/categoryBtn")
        if category_button is not None:
            validate_lorcana_home(root, expected_language or "英文")
            print(
                f"检测到迪士尼洛卡纳{expected_language or ''}卡牌库首页，点击“分类”……",
                flush=True,
            )
            tap_node_center(adb_path, serial, category_button)
            time.sleep(wait_seconds)
            continue

        # 仍在集换社内部页面时，尝试返回卡牌库。
        is_jihuanshe_page = any(
            node.attrib.get("package") == "com.jihuanshe" for node in root.iter("node")
        )
        if is_jihuanshe_page:
            run_adb(adb_path, serial, "shell", "input", "keyevent", "4")
            time.sleep(wait_seconds)
            continue
        break

    raise RuntimeError("无法进入卡牌库“分类”页；请手动打开 迪士尼洛卡纳 -> 目标语言 -> 卡牌库首页 后重试。")


def direct_pack_headers(pack: ET.Element) -> list[str]:
    headers: list[str] = []
    for item in list(pack):
        if item.attrib.get("class") != "android.widget.TextView":
            continue
        text = item.attrib.get("text", "").strip()
        if text:
            headers.append(text)
    return headers


def _left_column_boundary_x(root: ET.Element, screen_width: int) -> float:
    """Return the real left/right split of the category page.

    The old implementation used ``wm size * 0.34``.  On some emulator/device
    configurations the value returned by ``wm size`` is not in the same logical
    coordinate space as UIAutomator bounds.  That made right-side product codes
    such as HOT / HM-YB / CBB6C look as if they were left-side category names.

    ``com.jihuanshe:id/pack`` is the right product list itself, so its *left
    edge* is the authoritative divider between the two columns.
    """
    pack = find_node(root, "com.jihuanshe:id/pack")
    if pack is not None:
        pack_left, _, pack_right, _ = parse_bounds(pack.attrib.get("bounds", ""))
        if pack_right > pack_left and pack_left > 20:
            return float(pack_left)
    # Compatibility fallback for an unexpected app build without usable pack bounds.
    return float(screen_width) * 0.34


def find_left_series_node(
    root: ET.Element,
    target: str,
    screen_width: int,
) -> ET.Element | None:
    """Find a category label strictly on the left side of the pack divider."""
    boundary_x = _left_column_boundary_x(root, screen_width)
    matches: list[ET.Element] = []
    for node in root.iter("node"):
        text = node.attrib.get("text", "").strip()
        if not text or not same_label(text, target):
            continue
        left, top, right, bottom = parse_bounds(node.attrib.get("bounds", ""))
        if right <= left or bottom <= top:
            continue
        center_x = (left + right) / 2
        # A right-side title/product starts at or to the right of pack_left.
        # Use the actual pack boundary instead of a percentage of wm size.
        if center_x < boundary_x - 2:
            matches.append(node)
    if not matches:
        return None
    return min(matches, key=lambda node: parse_bounds(node.attrib.get("bounds", ""))[0])


def visible_left_series_nodes(
    root: ET.Element,
    screen_width: int,
) -> list[tuple[str, ET.Element]]:
    """Read only the actual children of the left ``kindRv`` category list.

    v9 could still mix categories after switching languages because it searched every
    TextView in the whole hierarchy and a RecyclerView refresh can temporarily keep
    stale views alive.  ``kindRv`` is the authoritative left category list, so use
    its direct children first.  The old geometry scan remains only as a compatibility
    fallback for unexpected app builds where ``kindRv`` is absent.
    """
    pack = find_node(root, "com.jihuanshe:id/pack")
    pack_top = 0
    pack_bottom = 10**9
    if pack is not None:
        _, pack_top, _, pack_bottom = parse_bounds(pack.attrib.get("bounds", ""))
    boundary_x = _left_column_boundary_x(root, screen_width)

    kind_rv = find_node(root, "com.jihuanshe:id/kindRv")
    candidates = list(kind_rv) if kind_rv is not None else list(root.iter("node"))

    result: list[tuple[str, ET.Element]] = []
    seen: set[str] = set()
    for node in candidates:
        if node.attrib.get("class") != "android.widget.TextView":
            continue
        text = node.attrib.get("text", "").strip()
        if not text:
            continue
        left, top, right, bottom = parse_bounds(node.attrib.get("bounds", ""))
        if right <= left or bottom <= top:
            continue
        center_x = (left + right) / 2
        if center_x >= boundary_x - 2:
            continue
        center_y = (top + bottom) / 2
        if center_y < pack_top - 8 or center_y > pack_bottom + 8:
            continue
        normalized = normalize_label(text)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append((text, node))
    result.sort(key=lambda pair: parse_bounds(pair[1].attrib.get("bounds", ""))[1])
    return result


def wait_left_series_stable(
    adb_path: str,
    serial: str,
    local_xml: Path,
    wait_seconds: float,
    required_equal_dumps: int = 3,
    max_dumps: int = 12,
) -> tuple[str, ...]:
    """Wait until the freshly opened language's left list stops changing.

    Language switching is asynchronous.  A first UIAutomator dump can still contain
    the previous language's RecyclerView children; accumulating those immediately is
    exactly how a 繁中 run acquired the eight 简中 categories.
    """
    width, _ = screen_size(adb_path, serial)
    previous: tuple[str, ...] | None = None
    equal_count = 0
    for _ in range(max_dumps):
        dump_current_page(adb_path, serial, local_xml)
        root = ET.parse(local_xml).getroot()
        if not is_category_page(root):
            time.sleep(max(0.25, wait_seconds * 0.5))
            continue
        signature = tuple(
            normalize_label(text) for text, _ in visible_left_series_nodes(root, width)
        )
        if not signature:
            previous = None
            equal_count = 0
        elif signature == previous:
            equal_count += 1
            if equal_count >= required_equal_dumps - 1:
                return signature
        else:
            previous = signature
            equal_count = 1
        time.sleep(max(0.35, wait_seconds * 0.6))
    raise RuntimeError("切换语言后左侧大类仍在变化，拒绝混合不同语言的大类；请稍后重试。")


def prepare_fresh_category_page(
    adb_path: str,
    serial: str,
    local_xml: Path,
    wait_seconds: float,
    expected_language: str,
) -> ET.Element:
    """Force a clean homepage -> category transition for the requested language.

    ``ensure_category_page`` intentionally returns immediately when already on a
    category page, which means it cannot verify the language there.  After a manual
    language switch this can leave old adapter rows in the hierarchy.  For the first
    discovery only, return to the library homepage, verify the language label there,
    reopen 分类, then require the left list to become stable before scanning.
    """
    for _ in range(4):
        dump_current_page(adb_path, serial, local_xml)
        root = ET.parse(local_xml).getroot()
        if is_library_home(root):
            break
        if is_category_page(root):
            print("启动时已在分类页：先返回卡牌库首页确认语言并刷新分类列表……", flush=True)
            run_adb(adb_path, serial, "shell", "input", "keyevent", "4")
            time.sleep(wait_seconds)
            continue
        is_jihuanshe_page = any(
            node.attrib.get("package") == "com.jihuanshe" for node in root.iter("node")
        )
        if is_jihuanshe_page:
            run_adb(adb_path, serial, "shell", "input", "keyevent", "4")
            time.sleep(wait_seconds)
            continue
        break

    root = ensure_category_page(
        adb_path,
        serial,
        local_xml,
        wait_seconds,
        expected_language=expected_language,
    )
    # Give the newly selected language adapter time to replace any previous children.
    wait_left_series_stable(adb_path, serial, local_xml, wait_seconds)
    dump_current_page(adb_path, serial, local_xml)
    return ET.parse(local_xml).getroot()


def reset_left_series_to_top(
    adb_path: str,
    serial: str,
    local_xml: Path,
    wait_seconds: float,
    max_swipes: int,
) -> None:
    """把左侧大类栏滚回顶部。"""
    width, _ = screen_size(adb_path, serial)
    previous: tuple[str, ...] | None = None
    unchanged = 0
    for _ in range(max_swipes + 1):
        root = ensure_category_page(adb_path, serial, local_xml, wait_seconds)
        signature = tuple(
            normalize_label(text) for text, _ in visible_left_series_nodes(root, width)
        )
        if signature and signature == previous:
            unchanged += 1
            if unchanged >= 2:
                return
        else:
            unchanged = 0
        previous = signature
        swipe_left_series(adb_path, serial, "down")
        time.sleep(wait_seconds)
    raise RuntimeError("左侧大类栏无法可靠回到顶部。")


def discover_series_order(
    adb_path: str,
    serial: str,
    local_xml: Path,
    wait_seconds: float,
    max_swipes: int,
) -> list[str]:
    """从左栏自动发现当前语言实际存在的大类顺序。"""
    reset_left_series_to_top(
        adb_path, serial, local_xml, wait_seconds, max_swipes
    )
    width, _ = screen_size(adb_path, serial)
    root_for_boundary = ensure_category_page(adb_path, serial, local_xml, wait_seconds)
    boundary_x = _left_column_boundary_x(root_for_boundary, width)
    pack = find_node(root_for_boundary, "com.jihuanshe:id/pack")
    if pack is not None:
        pack_bounds = pack.attrib.get("bounds", "")
        print(
            f"左/右栏实际分界：x={boundary_x:.0f}（pack bounds={pack_bounds}）",
            flush=True,
        )
    else:
        print(
            f"左/右栏分界回退值：x={boundary_x:.0f}（未取得 pack bounds）",
            flush=True,
        )
    discovered: list[str] = []
    seen: set[str] = set()
    previous_signature: tuple[str, ...] | None = None
    unchanged = 0

    for _ in range(max_swipes + 1):
        root = ensure_category_page(adb_path, serial, local_xml, wait_seconds)
        visible = visible_left_series_nodes(root, width)
        signature = tuple(normalize_label(text) for text, _ in visible)
        for text, _ in visible:
            # “近期更新”是重复入口，不属于正式系列。
            if same_label(text, "近期更新"):
                continue
            norm = normalize_label(text)
            if norm and norm not in seen:
                seen.add(norm)
                discovered.append(text)

        if signature and signature == previous_signature:
            unchanged += 1
        else:
            unchanged = 0
        if unchanged >= 2:
            break
        previous_signature = signature
        swipe_left_series(adb_path, serial, "up")
        time.sleep(wait_seconds)
    else:
        raise RuntimeError("自动发现左侧大类达到最大滚动次数，可能尚未到达底部。")

    if not discovered:
        raise RuntimeError("没有从左侧栏发现任何正式大类。")

    reset_left_series_to_top(
        adb_path, serial, local_xml, wait_seconds, max_swipes
    )
    return discovered


def wait_for_target_header(
    adb_path: str,
    serial: str,
    local_xml: Path,
    target: str,
    timeout_seconds: float,
) -> ET.Element | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        dump_current_page(adb_path, serial, local_xml)
        root = ET.parse(local_xml).getroot()
        pack = find_node(root, "com.jihuanshe:id/pack")
        if pack is not None and any(same_label(header, target) for header in direct_pack_headers(pack)):
            return root
        time.sleep(0.35)
    return None


def select_series(
    adb_path: str,
    serial: str,
    local_xml: Path,
    target: str,
    wait_seconds: float,
    max_left_swipes: int,
    force_reselect: bool = False,
) -> ET.Element:
    """在左侧找到并点击 target，并确认右侧已经定位到该大类标题。"""
    width, _ = screen_size(adb_path, serial)

    for _ in range(max_left_swipes + 1):
        root = ensure_category_page(adb_path, serial, local_xml, wait_seconds)
        node = find_left_series_node(root, target, width)
        if node is not None:
            # 先直接点击 target。多数版本即使已经选中，也会重新把右侧定位到该段顶部。
            tap_node_center(adb_path, serial, node)
            anchored = wait_for_target_header(
                adb_path,
                serial,
                local_xml,
                target,
                max(2.5, wait_seconds * 4),
            )
            if anchored is not None:
                return anchored

            # 某些版本“重复点当前选中项”不会回顶。第二遍核验时，
            # 先点离 target 最近的另一个可见大类，再点回来，强制触发定位。
            if force_reselect:
                dump_current_page(adb_path, serial, local_xml)
                retry_root = ET.parse(local_xml).getroot()
                target_bounds = parse_bounds(node.attrib.get("bounds", ""))
                target_y = (target_bounds[1] + target_bounds[3]) // 2
                alternatives = [
                    pair
                    for pair in visible_left_series_nodes(retry_root, width)
                    if not same_label(pair[0], target)
                ]
                alternatives.sort(
                    key=lambda pair: abs(
                        (
                            parse_bounds(pair[1].attrib.get("bounds", ""))[1]
                            + parse_bounds(pair[1].attrib.get("bounds", ""))[3]
                        )
                        // 2
                        - target_y
                    )
                )
                if alternatives:
                    _, other_node = alternatives[0]
                    tap_node_center(adb_path, serial, other_node)
                    time.sleep(max(0.35, wait_seconds * 0.6))
                    dump_current_page(adb_path, serial, local_xml)
                    retry_root = ET.parse(local_xml).getroot()
                    retry_target = find_left_series_node(retry_root, target, width)
                    if retry_target is not None:
                        tap_node_center(adb_path, serial, retry_target)
                        anchored = wait_for_target_header(
                            adb_path,
                            serial,
                            local_xml,
                            target,
                            max(2.5, wait_seconds * 4),
                        )
                        if anchored is not None:
                            return anchored

            raise RuntimeError(f"已点击左侧“{target}”，但右侧没有定位到该大类标题。")

        # 目标还没出现在左栏可视区，继续向下滚左栏。
        swipe_left_series(adb_path, serial, "up")
        time.sleep(wait_seconds)

    raise RuntimeError(f"左侧大类列表找不到：{target}")


# ---------------------------------------------------------------------------
# 右侧当前大类的小类扫描
# ---------------------------------------------------------------------------


def _save_empty_scan_debug_xml(local_xml: Path, target: str) -> Path | None:
    """When a series unexpectedly parses as zero, preserve the exact UI dump.

    This makes the next diagnosis evidence-based instead of guessing from a screenshot.
    """
    safe = re.sub(r'[<>:"/\\|?*]+', "_", target).strip() or "series"
    path = Path.cwd() / f"jhs_debug_empty_{safe}.xml"
    try:
        path.write_bytes(local_xml.read_bytes())
        return path
    except OSError:
        return None


def _child_is_in_pack_viewport(item: ET.Element, pack: ET.Element) -> bool:
    """只接受真正落在右侧 RecyclerView 可视区域内的 child。

    RecyclerView 会保留少量已经离屏/预加载的 child。它们仍可能出现在
    UIAutomator XML 里；如果不做几何过滤，尤其最后一个系列没有“下一个标题”
    作为边界时，旧 child 会被误算成当前系列。
    """
    left, top, right, bottom = parse_bounds(item.attrib.get("bounds", ""))
    pack_left, pack_top, pack_right, pack_bottom = parse_bounds(
        pack.attrib.get("bounds", "")
    )
    if right <= left or bottom <= top:
        return False
    if pack_right <= pack_left or pack_bottom <= pack_top:
        return True
    # 至少与 pack 的可视矩形有实际交集。部分露出的最后一行仍然保留。
    return (
        right > pack_left
        and left < pack_right
        and bottom > pack_top
        and top < pack_bottom
    )


def _ordered_visible_pack_items(
    root: ET.Element,
    known_series_headers: Iterable[str],
) -> tuple[ET.Element, list[tuple[str, dict[str, object]]]]:
    """把右侧当前屏转换成严格的视觉顺序 token。

    token 类型只有 HEADER / PRODUCT。

    关键规则：不能再把“所有 direct TextView”都当成大类标题。简中“联名商品”
    的鞋类/衣服/裤子等无编号小类，在当前 App 结构里也可能直接以 TextView
    出现在 pack 下面。只有文字能匹配左栏已发现的大类（以及近期更新）时，
    才能判定为 HEADER；其他可见 TextView 一律按无编号 PRODUCT 处理。
    """
    header_norms = {normalize_label(value) for value in known_series_headers if value}
    header_norms.add(normalize_label("近期更新"))

    pack = find_node(root, "com.jihuanshe:id/pack")
    if pack is None:
        raise RuntimeError("当前不是分类页：缺少右侧小类列表 pack。")

    sortable: list[tuple[int, int, int, ET.Element]] = []
    for index, item in enumerate(list(pack)):
        if not _child_is_in_pack_viewport(item, pack):
            continue
        left, top, _, _ = parse_bounds(item.attrib.get("bounds", ""))
        sortable.append((top, left, index, item))

    tokens: list[tuple[str, dict[str, object]]] = []
    for _, _, _, item in sorted(sortable):
        if item.attrib.get("class") == "android.widget.TextView":
            text = item.attrib.get("text", "").strip()
            if not text:
                continue
            if normalize_label(text) in header_norms:
                tokens.append(("HEADER", {"文字": text}))
            else:
                # 简中联名商品等无编号行可能本身就是 pack 的 direct TextView。
                # 它不是大类标题时，就应当作为一个真实小类保存，编号留空。
                left, top, right, bottom = parse_bounds(item.attrib.get("bounds", ""))
                if bottom - top >= 24:
                    tokens.append(
                        (
                            "PRODUCT",
                            {
                                "列表编号": "",
                                "列表名称": text,
                                "无编号": True,
                                "x": (left + right) // 2,
                                "y": (top + bottom) // 2,
                            },
                        )
                    )
            continue

        texts = [value.strip() for value in all_texts(item) if value.strip()]
        code = child_text(item, "com.jihuanshe:id/numberTv").strip()
        left, top, right, bottom = parse_bounds(item.attrib.get("bounds", ""))
        if bottom - top < 24 or not texts:
            continue

        if code:
            # Most rows expose ``numberTv`` as the code and a different sibling
            # TextView as the name.  简中“联名商品” is a real exception: the
            # merchandise category is duplicated in both fields, e.g.
            # ``numberTv=鞋类`` and the visible name is also ``鞋类``.  Removing
            # every value equal to code therefore erased the only valid name and
            # made the whole series parse as zero.  Prefer a distinct label when
            # one exists; otherwise, if the row visibly contains the code at least
            # twice, accept that same text as both list code and list name.
            names = [value for value in texts if value != code]
            if names:
                name = names[-1].strip()
            elif sum(value == code for value in texts) >= 2:
                name = code
            else:
                name = ""
        else:
            # Some merchandise categories (confirmed on 简中“联名商品”) have no
            # numberTv at all.  Their direct pack child still represents one real
            # list entry, e.g. “鞋类 / 鞋类”, “衣服 / 衣服”.  Treat the visible
            # black/rightmost label as the name and keep the code genuinely blank
            # instead of silently dropping the row.  A blank code is supported by
            # product_key because the series+name still form a stable identity.
            name = texts[-1].strip()

        if not name:
            continue
        tokens.append(
            (
                "PRODUCT",
                {
                    "列表编号": code,
                    "列表名称": name,
                    "无编号": not bool(code),
                    "x": (left + right) // 2,
                    "y": (top + bottom) // 2,
                },
            )
        )
    return pack, tokens


def parse_target_pack(
    root: ET.Element,
    target: str,
    entered_target: bool,
    known_series_headers: Iterable[str],
) -> tuple[
    bool,
    bool,
    list[dict[str, object]],
    tuple[tuple[str, ...], ...],
]:
    """解析普通（非最后一个）大类的当前屏。"""
    _, tokens = _ordered_visible_pack_items(root, known_series_headers)

    products: list[dict[str, object]] = []
    signature: list[tuple[str, ...]] = []
    reached_next_header = False
    current_section = target if entered_target else ""

    for kind, data in tokens:
        if kind == "HEADER":
            header = str(data.get("文字", ""))
            signature.append(("HEADER", header))
            if same_label(header, target):
                entered_target = True
                current_section = target
                continue
            if entered_target:
                reached_next_header = True
                current_section = header
                continue
            current_section = header
            continue

        code = str(data.get("列表编号", ""))
        name = str(data.get("列表名称", ""))
        signature.append(("PRODUCT", current_section, code, name))

        if reached_next_header:
            continue
        if not entered_target or not same_label(current_section, target):
            continue
        products.append(
            {
                "系列": target,
                "列表编号": code,
                "列表名称": name,
                "x": data.get("x", 0),
                "y": data.get("y", 0),
            }
        )

    return entered_target, reached_next_header, products, tuple(signature)


def _product_identity(row: dict[str, object]) -> tuple[str, str]:
    return (str(row.get("列表编号", "")), str(row.get("列表名称", "")))


def _merge_last_series_page(
    collected: list[dict[str, object]],
    page_rows: list[dict[str, object]],
    target_header_visible: bool,
    previous_series_ids: set[tuple[str, str]],
) -> tuple[list[dict[str, object]], int, int, bool]:
    """合并最后一个系列的一屏，只接受能被上一屏“锚定”的连续后继。

    最后一个系列没有下一个大类标题，所以不能再用 entered_target=True 后
    “后面所有编号行都算当前系列”的规则。第二屏开始必须与已经收集的尾部
    有重叠；重叠之前的行全部视为 RecyclerView 邻接/缓存行，不计数。
    """
    if not collected:
        # 第一屏虽然有“周边 & 其他”标题作为强锚点，但 RecyclerView 仍可能
        # 在标题之后保留已经回收/缓存的旧 child。实测这些旧 child 会把
        # PMCG 初代末尾 12 项再次暴露出来，因此第一屏也必须做“前序系列边界”检查。
        safe_first: list[dict[str, object]] = []
        hit_previous_series_boundary = False
        for row in page_rows:
            identity = _product_identity(row)
            if identity in previous_series_ids:
                hit_previous_series_boundary = True
                break
            safe_first.append(row)
        return (
            safe_first,
            len(safe_first),
            0,
            hit_previous_series_boundary,
        )

    old_ids = [_product_identity(row) for row in collected]
    page_ids = [_product_identity(row) for row in page_rows]

    # 目标标题还在屏内时，page_rows 本身已经严格从标题后开始。
    # 找最大 suffix/prefix overlap 即可。
    if target_header_visible:
        for size in range(min(len(old_ids), len(page_ids)), 0, -1):
            if old_ids[-size:] == page_ids[:size]:
                new_rows = page_rows[size:]
                return collected + new_rows, len(new_rows), size, False
        if page_ids == old_ids[: len(page_ids)]:
            return collected, 0, len(page_ids), False
        raise RuntimeError(
            "最后一个系列仍能看到标题，但当前屏与上一屏没有可靠重叠；"
            "为避免误计，本次不接受结果。"
        )

    # 标题已经滚出屏幕后，当前 XML 可能在前面保留上一系列/缓存 child。
    # 因此允许 overlap 从 page_rows 的任意位置开始，但只接收 overlap 后面的行。
    best_size = 0
    best_start = -1
    for size in range(min(len(old_ids), len(page_ids)), 0, -1):
        suffix = old_ids[-size:]
        for start in range(0, len(page_ids) - size + 1):
            if page_ids[start : start + size] == suffix:
                best_size = size
                best_start = start
                break
        if best_size:
            break

    if best_size == 0:
        if not page_rows:
            return collected, 0, 0, False

        # 最后一个系列之后没有下一个标题。RecyclerView 在滚到底时可能把前面
        # 系列（实测为 PMCG 初代）的缓存 child 暴露出来。如果整屏都是已经在
        # 前面系列确认过的产品，这就是可靠的“逻辑底部”，不能再继续计入周边。
        if page_ids and all(identity in previous_series_ids for identity in page_ids):
            return collected, 0, 0, True

        raise RuntimeError(
            "最后一个系列滚动后，当前屏无法与上一屏已确认的小类建立重叠；"
            "拒绝把这些行直接算入当前系列。"
        )

    new_rows = page_rows[best_start + best_size :]
    known = set(old_ids)
    safe_new: list[dict[str, object]] = []
    hit_previous_series_boundary = False
    for row in new_rows:
        identity = _product_identity(row)
        if identity in known:
            continue

        # 关键边界：滚动后若出现一个“编号+名称”已经属于前面大类的产品，
        # 说明 RecyclerView 已经越过周边真实内容，开始暴露旧缓存/邻接行。
        # 从这一行起全部停止，不再把后面的内容归到“周边 & 其他”。
        if identity in previous_series_ids:
            hit_previous_series_boundary = True
            break

        safe_new.append(row)
        known.add(identity)

    return (
        collected + safe_new,
        len(safe_new),
        best_size,
        hit_previous_series_boundary,
    )


def parse_last_series_page(
    root: ET.Element,
    target: str,
    known_series_headers: Iterable[str],
) -> tuple[bool, list[dict[str, object]], tuple[tuple[str, ...], ...]]:
    """读取最后一个大类当前屏，不猜测无标题行的归属。

    如果 target 标题可见，只返回标题之后的产品；如果标题已滚出屏幕，则返回
    当前屏所有产品作为“候选”，后续必须通过 overlap 才允许并入结果。
    """
    _, tokens = _ordered_visible_pack_items(root, known_series_headers)
    target_header_visible = False
    after_target = False
    rows_after_header: list[dict[str, object]] = []
    all_products: list[dict[str, object]] = []
    signature: list[tuple[str, ...]] = []

    for kind, data in tokens:
        if kind == "HEADER":
            header = str(data.get("文字", ""))
            signature.append(("HEADER", header))
            if same_label(header, target):
                target_header_visible = True
                after_target = True
            elif after_target:
                # 理论上最后系列后不应再有其它大类；一旦出现就作为硬边界。
                after_target = False
            continue

        code = str(data.get("列表编号", ""))
        name = str(data.get("列表名称", ""))
        signature.append(("PRODUCT", code, name))
        row = {
            "系列": target,
            "列表编号": code,
            "列表名称": name,
            "x": data.get("x", 0),
            "y": data.get("y", 0),
        }
        all_products.append(row)
        if after_target:
            rows_after_header.append(row)

    return (
        target_header_visible,
        rows_after_header if target_header_visible else all_products,
        tuple(signature),
    )


def scan_one_series_once(
    adb_path: str,
    serial: str,
    local_xml: Path,
    target: str,
    wait_seconds: float,
    max_left_swipes: int,
    max_right_swipes: int,
    is_last_series: bool,
    force_reselect: bool,
    pass_number: int,
    previous_series_ids: set[tuple[str, str]],
    known_series_headers: Iterable[str],
) -> list[dict[str, object]]:
    select_series(
        adb_path,
        serial,
        local_xml,
        target,
        wait_seconds,
        max_left_swipes,
        force_reselect=force_reselect,
    )

    # 最后一个大类没有“下一个标题”作为天然终点，必须走独立逻辑：
    # 第一屏由“周边 & 其他”标题锚定，后续屏只能靠与上一屏重叠来证明是连续后继。
    if is_last_series:
        collected_last: list[dict[str, object]] = []
        previous_signature: tuple[tuple[str, ...], ...] | None = None
        unchanged = 0

        for page_number in range(max_right_swipes + 1):
            dump_current_page(adb_path, serial, local_xml)
            root = ET.parse(local_xml).getroot()
            target_header_visible, candidates, signature = parse_last_series_page(
                root, target, known_series_headers
            )
            if page_number == 0 and not target_header_visible:
                raise RuntimeError(f"右侧没有看到“{target}”标题，拒绝开始计数。")

            (
                collected_last,
                added,
                overlap,
                hit_previous_series_boundary,
            ) = _merge_last_series_page(
                collected_last,
                candidates,
                target_header_visible,
                previous_series_ids,
            )
            print(
                f"    第 {pass_number} 遍·右侧第 {page_number + 1} 屏："
                f"重叠 {overlap}，新增 {added}，本系列累计 {len(collected_last)}",
                flush=True,
            )

            if hit_previous_series_boundary:
                print(
                    "    检测到已归属于前面大类的产品，判定已越过“周边 & 其他”真实底部；停止。",
                    flush=True,
                )
                break

            if signature and signature == previous_signature and added == 0:
                unchanged += 1
            else:
                unchanged = 0
            if unchanged >= 2:
                break

            if page_number >= max_right_swipes:
                raise RuntimeError(f"{target} 达到最大右侧滚动次数，可能尚未扫描完整。")

            previous_signature = signature
            pack = find_node(root, "com.jihuanshe:id/pack")
            if pack is None:
                raise RuntimeError("右侧小类列表突然消失。")
            swipe_right_pack_up(adb_path, serial, pack)
            time.sleep(wait_seconds)

        if not collected_last:
            debug_path = _save_empty_scan_debug_xml(local_xml, target)
            suffix = f"；已保存诊断 XML：{debug_path}" if debug_path else ""
            raise RuntimeError(f"{target} 没有读取到任何小类，拒绝保存 0{suffix}。")
        return collected_last

    collected: OrderedDict[str, dict[str, object]] = OrderedDict()
    entered_target = False
    previous_signature: tuple[tuple[str, ...], ...] | None = None
    unchanged = 0
    saw_next_header = False

    for page_number in range(max_right_swipes + 1):
        dump_current_page(adb_path, serial, local_xml)
        root = ET.parse(local_xml).getroot()
        (
            entered_target,
            reached_next_header,
            page_products,
            signature,
        ) = parse_target_pack(root, target, entered_target, known_series_headers)

        if page_number == 0 and not entered_target:
            raise RuntimeError(f"右侧没有看到“{target}”标题，拒绝开始计数。")

        before = len(collected)
        for product in page_products:
            code = str(product["列表编号"])
            name = str(product["列表名称"])
            key = product_key(target, code, name)
            if key not in collected:
                collected[key] = product
        added = len(collected) - before

        print(
            f"    第 {pass_number} 遍·右侧第 {page_number + 1} 屏："
            f"新增 {added}，本系列累计 {len(collected)}",
            flush=True,
        )

        if reached_next_header:
            saw_next_header = True
            break

        if signature and signature == previous_signature and added == 0:
            unchanged += 1
        else:
            unchanged = 0

        if unchanged >= 2:
            raise RuntimeError(
                f"{target} 右侧列表已经不再移动，但尚未看到下一个大类标题；"
                "无法证明本系列已完整扫描。"
            )

        if page_number >= max_right_swipes:
            raise RuntimeError(f"{target} 达到最大右侧滚动次数，可能尚未扫描完整。")

        previous_signature = signature
        pack = find_node(root, "com.jihuanshe:id/pack")
        if pack is None:
            raise RuntimeError("右侧小类列表突然消失。")
        swipe_right_pack_up(adb_path, serial, pack)
        time.sleep(wait_seconds)

    if not collected:
        debug_path = _save_empty_scan_debug_xml(local_xml, target)
        suffix = f"；已保存诊断 XML：{debug_path}" if debug_path else ""
        raise RuntimeError(f"{target} 没有读取到任何小类，拒绝保存 0{suffix}。")
    if not saw_next_header:
        raise RuntimeError(f"{target} 未看到下一个大类标题，无法确认完整。")

    return list(collected.values())


def series_identity(rows: list[dict[str, object]]) -> list[tuple[str, str]]:
    return [
        (str(row.get("列表编号", "")), str(row.get("列表名称", "")))
        for row in rows
    ]


def scan_one_series_verified(
    args: argparse.Namespace,
    local_xml: Path,
    target: str,
    is_last_series: bool,
    previous_series_ids: set[tuple[str, str]],
    known_series_headers: Iterable[str],
) -> list[dict[str, object]]:
    accepted: list[dict[str, object]] | None = None

    for pass_number in range(1, args.verify_passes + 1):
        print(f"\n[{target}] 开始第 {pass_number}/{args.verify_passes} 遍扫描", flush=True)
        rows = scan_one_series_once(
            args.adb,
            args.serial,
            local_xml,
            target,
            args.page_wait,
            args.max_left_swipes,
            args.max_right_swipes,
            is_last_series,
            force_reselect=(pass_number > 1),
            pass_number=pass_number,
            previous_series_ids=previous_series_ids,
            known_series_headers=known_series_headers,
        )
        # 最后一个系列增加硬性后置校验：任何“编号+名称”若已经属于前面
        # 核验通过的大类，就绝不能被保存到“周边 & 其他”。这样即使 UIAutomator
        # 后续又出现新的缓存形态，也会直接报错而不是悄悄得到 16。
        if is_last_series:
            leaked = [
                identity
                for identity in series_identity(rows)
                if identity in previous_series_ids
            ]
            if leaked:
                preview = "、".join(f"{code} {name}" for code, name in leaked[:5])
                raise RuntimeError(
                    f"{target} 检测到 {len(leaked)} 个已属于前面大类的缓存项：{preview}；"
                    "拒绝保存本次结果。"
                )

        print(f"    第 {pass_number} 遍读取到 {len(rows)} 个小类", flush=True)

        if accepted is None:
            accepted = rows
            continue

        if series_identity(accepted) != series_identity(rows):
            first = series_identity(accepted)
            second = series_identity(rows)
            mismatch_at = next(
                (
                    index
                    for index in range(min(len(first), len(second)))
                    if first[index] != second[index]
                ),
                min(len(first), len(second)),
            )
            first_value = first[mismatch_at] if mismatch_at < len(first) else "<结束>"
            second_value = second[mismatch_at] if mismatch_at < len(second) else "<结束>"
            raise RuntimeError(
                f"{target} 两遍扫描不一致：第1遍 {len(first)} 个，第{pass_number}遍 {len(second)} 个；"
                f"首次差异位置 {mismatch_at + 1}：{first_value!r} != {second_value!r}。"
            )

    assert accepted is not None
    return accepted


# ---------------------------------------------------------------------------
# 保存 / 主流程
# ---------------------------------------------------------------------------


def save_verified_inventory(
    inventory_path: Path,
    counts_path: Path,
    per_series: OrderedDict[str, list[dict[str, object]]],
) -> OrderedDict[str, dict[str, object]]:
    inventory: OrderedDict[str, dict[str, object]] = OrderedDict()
    scan_time = now_text()

    for series, series_rows in per_series.items():
        for product in series_rows:
            code = str(product.get("列表编号", ""))
            name = str(product.get("列表名称", ""))
            key = product_key(series, code, name)
            if key in inventory:
                continue
            inventory[key] = {
                "系列": series,
                "列表编号": code,
                "列表名称": name,
                "产品唯一键": key,
                "发现顺序": len(inventory) + 1,
                "清单扫描时间": scan_time,
            }

    write_csv(inventory_path, INVENTORY_COLUMNS, inventory.values())
    write_csv(
        counts_path,
        COUNT_COLUMNS,
        [
            {"系列": series, "产品数量": len(rows)}
            for series, rows in per_series.items()
        ]
        + [{"系列": "总计", "产品数量": len(inventory)}],
    )
    return inventory


def print_counts(per_series: OrderedDict[str, list[dict[str, object]]]) -> None:
    total = sum(len(rows) for rows in per_series.values())
    print("\n产品清单扫描完成：", flush=True)
    for series, rows in per_series.items():
        print(f"  {series}: {len(rows)}")
    print(f"  总计: {total}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="集换社迪士尼洛卡纳英文：动态发现左侧大类并逐项扫描右侧小类产品清单。"
    )
    parser.add_argument("--adb", default=DEFAULT_ADB, help="adb.exe 完整路径")
    parser.add_argument("--serial", default=DEFAULT_SERIAL, help="ADB 设备序列号")
    parser.add_argument(
        "--language",
        choices=LANGUAGE_CHOICES,
        default="英文",
        help="当前手动选择的卡牌库语言：英文；本版只采集洛卡纳英文",
    )
    parser.add_argument(
        "--discover-only",
        action="store_true",
        help="只自动读取并打印左侧大类顺序，不扫描右侧产品",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="从当前语言已有清单的连续已完成大类断点继续；不会重新扫描这些已核验大类",
    )
    parser.add_argument(
        "--verify-passes",
        type=int,
        default=2,
        help="每个大类完整扫描几遍并要求结果完全一致；默认 2",
    )
    parser.add_argument(
        "--page-wait",
        type=float,
        default=0.8,
        help="每次点击/滚动后的等待秒数；网络慢可调到 1.2~1.5",
    )
    parser.add_argument(
        "--max-left-swipes",
        type=int,
        default=40,
        help="寻找一个左侧大类时允许的最大左栏滚动次数",
    )
    parser.add_argument(
        "--max-right-swipes",
        type=int,
        default=160,
        help="单个大类右侧小类列表允许的最大滚动次数",
    )
    parser.add_argument(
        "--output",
        default="",
        help="产品清单 CSV 路径；默认保存在脚本旁边 jhs_lorcana_en_product_inventory.csv",
    )
    parser.add_argument(
        "--counts-output",
        default="",
        help="系列数量 CSV 路径；默认保存在脚本旁边 jhs_lorcana_en_inventory_counts.csv",
    )
    parser.add_argument(
        "--only-series",
        default="",
        help='只扫描一个左侧大类，例如 --only-series "周边 & 其他"',
    )
    parser.add_argument(
        "--reference-inventory",
        default="",
        help=(
            "--only-series 扫描最后一个大类时，用旧清单识别前序系列缓存项；"
            "默认读取脚本旁边 jhs_lorcana_en_product_inventory.csv"
        ),
    )
    args = parser.parse_args()

    if args.verify_passes < 1:
        parser.error("--verify-passes 必须 >= 1")
    if not Path(args.adb).exists():
        print(f"找不到 adb.exe：{args.adb}", file=sys.stderr)
        return 1

    script_dir = Path(__file__).resolve().parent

    requested_only = (args.only_series or "").strip()
    only_series = ""

    default_inventory_name, default_counts_name = default_output_names(args.language)

    if requested_only:
        # Test mode must never overwrite the full reference inventory by default.
        inventory_path = (
            Path(args.output)
            if args.output
            else script_dir / f"jhs_lorcana_en_only_series_inventory.csv"
        )
        counts_path = (
            Path(args.counts_output)
            if args.counts_output
            else script_dir / f"jhs_lorcana_en_only_series_counts.csv"
        )
    else:
        inventory_path = (
            Path(args.output)
            if args.output
            else script_dir / default_inventory_name
        )
        counts_path = (
            Path(args.counts_output)
            if args.counts_output
            else script_dir / default_counts_name
        )

    reference_path = (
        Path(args.reference_inventory)
        if args.reference_inventory
        else script_dir / default_inventory_name
    )

    per_series: OrderedDict[str, list[dict[str, object]]] = OrderedDict()

    try:
        with tempfile.TemporaryDirectory(prefix="jhs_inventory_") as temp_dir:
            local_xml = Path(temp_dir) / "page.xml"
            prepare_fresh_category_page(
                args.adb,
                args.serial,
                local_xml,
                args.page_wait,
                expected_language=args.language,
            )

            print(f"脚本版本：{SCRIPT_VERSION}", flush=True)
            print(f"目标语言：{args.language}", flush=True)
            discovered_order = discover_series_order(
                args.adb,
                args.serial,
                local_xml,
                args.page_wait,
                args.max_left_swipes,
            )
            print("自动发现左侧大类顺序：", flush=True)
            for idx, name in enumerate(discovered_order, start=1):
                print(f"  {idx:02d}. {name}", flush=True)

            if args.discover_only:
                print("\n--discover-only：只读取大类顺序，本次不扫描产品。", flush=True)
                return 0

            if args.resume and requested_only:
                raise RuntimeError("--resume 与 --only-series 不要同时使用。")

            if args.resume:
                per_series = load_resume_prefix(inventory_path, discovered_order)
                if per_series:
                    resumed_products = sum(len(rows) for rows in per_series.values())
                    print(
                        f"断点恢复：已载入 {len(per_series)} 个已核验大类，"
                        f"产品 {resumed_products} 个；将从下一个大类继续。",
                        flush=True,
                    )
                else:
                    print("断点恢复：没有找到可继续的已完成大类，将从头扫描。", flush=True)

            if requested_only:
                for candidate in discovered_order:
                    if same_label(candidate, requested_only):
                        only_series = candidate
                        break
                if not only_series:
                    raise RuntimeError(
                        "--only-series 不存在；当前语言可选："
                        + "、".join(discovered_order)
                    )

            if only_series:
                print(f"测试模式：只扫描 {only_series}", flush=True)
            else:
                print("第一功能：按左侧大类逐个扫描右侧小类。", flush=True)
            print(
                f"准确性策略：每个大类扫描 {args.verify_passes} 遍，"
                "只有编号/名称/顺序完全一致才保存。",
                flush=True,
            )

            if only_series:
                series_targets = [only_series]
            elif args.resume and per_series:
                completed_labels = set(per_series.keys())
                series_targets = [
                    series for series in discovered_order if series not in completed_labels
                ]
                if not series_targets:
                    print("当前语言所有大类都已经在断点清单中完成。", flush=True)
                    print_counts(per_series)
                    return 0
            else:
                series_targets = discovered_order

            for series in series_targets:
                index = discovered_order.index(series)
                is_last = index == len(discovered_order) - 1

                if only_series and is_last:
                    previous_series_ids = load_reference_identities(reference_path, series)
                    if not previous_series_ids:
                        raise RuntimeError(
                            f"只测 {series} 时需要旧完整清单来识别前序系列缓存；"
                            f"当前没有读到：{reference_path}。"
                            "请用 --reference-inventory 指定上一轮生成的 jhs_lorcana_en_product_inventory.csv。"
                        )
                    print(
                        f"前序系列边界参考：{reference_path}，"
                        f"载入 {len(previous_series_ids)} 个编号+名称。",
                        flush=True,
                    )
                else:
                    previous_series_ids = {
                        _product_identity(product)
                        for accepted_rows in per_series.values()
                        for product in accepted_rows
                    }

                rows = scan_one_series_verified(
                    args,
                    local_xml,
                    series,
                    is_last_series=is_last,
                    previous_series_ids=previous_series_ids,
                    known_series_headers=discovered_order,
                )
                per_series[series] = rows
                inventory = save_verified_inventory(
                    inventory_path,
                    counts_path,
                    per_series,
                )
                print(
                    f"  ✓ {series}: {len(rows)} 个；"
                    f"已核验系列累计 {len(per_series)}，产品累计 {len(inventory)}",
                    flush=True,
                )

            print_counts(per_series)
            print(f"\n本次输出清单：{inventory_path}")
            print(f"本次系列数量：{counts_path}")
            if only_series:
                print("测试模式不会覆盖完整 jhs_lorcana_en_product_inventory.csv。", flush=True)
            return 0

    except KeyboardInterrupt:
        save_verified_inventory(inventory_path, counts_path, per_series)
        print("\n已手动停止；已经完成双遍核验的大类已保存。", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        save_verified_inventory(inventory_path, counts_path, per_series)
        print(f"\n扫描停止：{exc}", file=sys.stderr)
        print("已经核验完成的大类仍然保存在 CSV 中。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
