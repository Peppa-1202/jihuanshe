#!/usr/bin/env python3
r"""集换社：洛卡纳英文 Phase3 全系列版。

基于用户 PMCG fix9 的逐卡采集和数量约束跨屏合并逻辑，合入原无数量父产品的
聚合商品区回退逻辑。Google 同步版：保留 jhs_tables.py 和 Phase1/2 文件在同一目录。
默认输入：同目录 jhs_lorcana_en_phase2_product_details.csv。
默认覆盖该表的全部系列：已完成单详情、有/无数量产品、多子类父产品。
“英文”表示 App 首页入口，保留其中的简体中文、推广卡、周边商品等所有分类。
不按卡牌实际语言排除；保留每条记录的“语言”，中文名、英文名按原页面存储。
本版撤销 fix3 的语言过滤，不清理旧结果；若曾运行 fix3，缺少断点的产品会重新抓取。
若希望数量差异不阻塞后续产品，运行时加 --continue-on-mismatch；差异仍明确记录。
失败或缺少产品名称的 Phase2 记录列入 *_unprocessed.csv，不伪造卡池结果。
卡牌与非卡分开；保留中文名/英文名及产品关系，不跨产品去重。
明确的纯周边产品：实际为 0 卡且存在非卡商品时，卡牌张数核对不适用，商品总数仍核对。
所有输出使用 jhs_lorcana_en_phase3_all_ 前缀，不沿用旧版局部卡池断点。

先手动进入“迪士尼洛卡纳 → 英文”首页，在 CMD 中测试：
C:\ProgramData\miniconda3\envs\jh_env\python.exe -u C:\platform-tools\jhs_lorcana_en_phase3_all_fix7.py --limit 1
测试完成后去掉 --limit 1；再次运行会跳过已完成产品；--redo 强制重抓。
如需限制一个系列：--series "补充包"；默认不限制。
运行时保持 root 关闭，不操作模拟器，不用 Excel/WPS 占用输出文件。

Phase2 的数量字段只作来源比较，绝不作提前结束、上限或重叠推算依据。
明确 END 的采集标记完成；有数量差异时仍注明待复核。
没有 END 时，需多次加强滚动且文本和坐标稳定才保存，状态仍为待复核。
跨版本复用断点，不再仅因版本变化重跑：旧版完成且卡牌数、总项目数都一致可沿用。
有明确 END 且数量无异常的结果可沿用；失败、数量异常、无 END 且无数量依据的结果重抓。
复用前还核对实际卡牌/非卡 CSV 明细条数；明细缺失或不符时重抓该产品。
尚未连接本机模拟器实测；此脚本不保证覆盖 Phase2 清单外的产品。
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
REMOTE_XML = "/sdcard/Download/jhs_lorcana_en_phase3.xml"
SCRIPT_VERSION = "2026-11-19-lorcana-entry-phase3-all-v8-noncard-sheets"
TARGET_LANGUAGE = "英文"
_LAST_POOL_END = "未确认"
REVIEW_EXIT_CODE = 3
PRODUCT_FAILURE_EXIT_CODE = 4

CARD_COLUMNS = [
    "系列",
    "详情编号",
    "详情名称",
    "页面张数",
    "抓取序号",
    "中文名",
    "英文名",
    "编号显示",
    "编号状态",
    "卡号",
    "稀有度",
    "语言",
    "卡图状态",
    "全部可见文字",
]

NONCARD_COLUMNS = [
    "系列",
    "详情编号",
    "详情名称",
    "页面张数",
    "项目序号",
    "中文名",
    "英文名",
    "编号显示",
    "项目类型",
    "语言",
    "全部可见文字",
]

CHECK_COLUMNS = [
    "系列",
    "详情编号",
    "详情名称",
    "页面张数",
    "收录商品数量",
    "抓取卡牌数",
    "抓取非卡数",
    "抓取总项目数",
    "卡牌核对",
    "总项目核对",
    "完成依据",
    "抓取版本",
    "统计状态",
    "统计时间",
    "备注",
]

NON_CARD_RARITIES = {
    "徽章", "徽章套装", "徽章套裝", "收纳盒", "收納盒", "玩偶",
    "毛绒玩具", "毛絨玩具", "钥匙扣", "鑰匙扣", "挂件", "掛件",
    "立牌", "手办", "手辦",
    "原盒",
    "原包",
    "卡盒",
    "卡箱",
    "贴纸",
    "貼紙",
    "礼盒",
    "禮盒",
    "周边",
    "周邊",
    "卡套",
    "牌套",
    "卡册",
    "卡冊",
    "卡垫",
    "卡墊",
    "桌垫",
    "桌墊",
    "配件",
    "其他",
    "骰子",
    "指示物盒",
    "指示物",
    "硬币",
    "硬幣",
}

RARITY_TOKENS = {
    "C", "U", "R", "RR", "RRR", "SR", "HR", "UR", "AR", "SAR",
    "CHR", "CSR", "TR", "PR", "PROMO", "A", "S", "K", "ACE",
}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_adb(
    adb_path: str,
    serial: str,
    *args: str,
    check: bool = True,
    timeout_seconds: float = 20.0,
) -> str:
    """执行 ADB，并设置硬超时，避免 uiautomator/ADB 卡死后脚本永久无输出。"""
    command = [adb_path, "-s", serial, *args]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"ADB 命令超过 {timeout_seconds:.0f} 秒仍未返回：{' '.join(command)}"
        ) from exc
    output = (completed.stdout or "") + (completed.stderr or "")
    if check and completed.returncode != 0:
        raise RuntimeError(f"ADB 命令失败：{' '.join(command)}\n{output.strip()}")
    return output.strip()


def dump_current_page(adb_path: str, serial: str, local_xml: Path) -> None:
    last_error = ""
    for attempt in range(1, 5):
        try:
            run_adb(
                adb_path, serial, "shell", "uiautomator", "dump", REMOTE_XML,
                timeout_seconds=15.0,
            )
            run_adb(
                adb_path, serial, "pull", REMOTE_XML, str(local_xml),
                timeout_seconds=10.0,
            )
            if local_xml.exists() and local_xml.stat().st_size > 100:
                return
            last_error = "uiautomator/pull 已返回，但本地 XML 为空或过小"
        except RuntimeError as exc:
            last_error = str(exc)
            print(
                f"      当前页面读取失败，重试 {attempt}/4：{last_error}",
                flush=True,
            )
        if attempt < 4:
            time.sleep(0.55 + (attempt - 1) * 0.3)
    raise RuntimeError(f"无法取得当前页面结构。{last_error}")


def parse_bounds(value: str) -> tuple[int, int, int, int]:
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", value or "")
    if not match:
        return 0, 0, 0, 0
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def all_texts(root: ET.Element | None) -> list[str]:
    if root is None:
        return []
    values: list[str] = []
    for node in root.iter("node"):
        value = node.attrib.get("text", "").strip()
        if value:
            values.append(value)
    return values


def find_node(root: ET.Element, resource_id: str) -> ET.Element | None:
    for node in root.iter("node"):
        if node.attrib.get("resource-id") == resource_id:
            return node
    return None


def child_text(root: ET.Element, resource_id: str) -> str:
    node = find_node(root, resource_id)
    return node.attrib.get("text", "").strip() if node is not None else ""


def normalize_label(value: str) -> str:
    return re.sub(
        r"[\s\u3000\-‐‑‒–—―·・:：,，.。'\"“”‘’「」『』【】\[\]（）()&＆]+",
        "",
        (value or "").casefold(),
    )


def same_label(left: str, right: str) -> bool:
    return bool(left and right and normalize_label(left) == normalize_label(right))


def parse_int(value: object) -> int | None:
    text = "" if value is None else str(value).strip()
    if not text or text.casefold() == "nan":
        return None
    match = re.search(r"\d[\d,]*(?:\.0+)?", text)
    if not match:
        return None
    return int(float(match.group(0).replace(",", "")))


def tap_xy(adb_path: str, serial: str, x: int, y: int) -> None:
    run_adb(adb_path, serial, "shell", "input", "tap", str(x), str(y))


def tap_node_center(adb_path: str, serial: str, node: ET.Element) -> None:
    left, top, right, bottom = parse_bounds(node.attrib.get("bounds", ""))
    if right <= left or bottom <= top:
        raise RuntimeError("目标没有有效坐标。")
    tap_xy(adb_path, serial, (left + right) // 2, (top + bottom) // 2)


CSV_SAVE_RETRY_COUNT = 30
CSV_SAVE_RETRY_INTERVAL = 2.0


def wait_for_csv_writable(
    path: Path,
    retries: int = CSV_SAVE_RETRY_COUNT,
    interval: float = CSV_SAVE_RETRY_INTERVAL,
) -> None:
    """在开始耗时抓取前确认已有 CSV 没有被 Excel/WPS 独占。"""
    if not path.exists():
        return

    last_exc: PermissionError | None = None
    for attempt in range(1, retries + 1):
        try:
            # 只打开不写入，用来探测 Windows 独占锁；不会改动文件内容。
            with path.open("a", encoding="utf-8-sig"):
                pass
            if attempt > 1:
                print(f"      输出文件已解除占用：{path}", flush=True)
            return
        except PermissionError as exc:
            last_exc = exc
            if attempt == 1:
                print(
                    f"      输出文件被占用：{path}。请关闭 Excel/WPS/资源管理器预览，脚本会自动等待重试。",
                    flush=True,
                )
            if attempt < retries:
                if attempt == 1 or attempt % 5 == 0:
                    print(
                        f"      等待文件解锁……第 {attempt}/{retries} 次",
                        flush=True,
                    )
                time.sleep(interval)

    raise RuntimeError(
        f"输出 CSV 持续被占用，等待 {retries * interval:.0f} 秒后仍无法写入：{path}。"
    ) from last_exc


def write_csv(path: Path, columns: list[str], rows: Iterable[dict[str, object]]) -> None:
    """原子写 CSV；若 Windows 因 Excel/WPS 锁文件导致 replace 失败则自动等待重试。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    rows = list(rows)

    with tmp.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    last_exc: PermissionError | None = None
    for attempt in range(1, CSV_SAVE_RETRY_COUNT + 1):
        try:
            tmp.replace(path)
            if attempt > 1:
                print(f"      CSV 已成功写入：{path}", flush=True)
            return
        except PermissionError as exc:
            last_exc = exc
            if attempt == 1:
                print(
                    f"      CSV 正被 Excel/WPS 占用：{path}。请关闭文件，已抓取数据保留在内存和临时文件中，自动等待重试。",
                    flush=True,
                )
            if attempt < CSV_SAVE_RETRY_COUNT:
                if attempt == 1 or attempt % 5 == 0:
                    print(
                        f"      保存重试……第 {attempt}/{CSV_SAVE_RETRY_COUNT} 次",
                        flush=True,
                    )
                time.sleep(CSV_SAVE_RETRY_INTERVAL)

    raise RuntimeError(
        f"CSV 持续被占用，等待 {CSV_SAVE_RETRY_COUNT * CSV_SAVE_RETRY_INTERVAL:.0f} 秒后仍无法替换：{path}；"
        f"已抓取结果仍保留在临时文件 {tmp}。"
    ) from last_exc


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def product_key(series: str, code: str, name: str) -> str:
    return "\x1f".join((series.strip(), code.strip(), name.strip()))


def save_xml_copy(source: Path, target: Path) -> None:
    try:
        if source.exists():
            target.write_bytes(source.read_bytes())
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 第二步表格
# ---------------------------------------------------------------------------

def target_skip_reason(row: dict[str, str]) -> str:
    if not row.get("系列", "").strip():
        return "缺少系列"
    state = row.get("统计状态", "").strip()
    if not (state.startswith("完成") or state.startswith("多子类")):
        return "Phase2 未完成：" + (state or "空状态")
    if not (row.get("详情名称", "").strip() or row.get("列表名称", "").strip()):
        return "缺少可定位的产品名称"
    return ""


def load_targets(details_path: Path, series_list: list[str]) -> list[dict[str, str]]:
    """覆盖所有系列的单详情与多子类父产品，保留表格顺序。"""
    rows = []
    seen = set()
    for row in read_csv(details_path):
        if series_list and not any(same_label(row.get("系列", ""), series) for series in series_list):
            continue
        if target_skip_reason(row):
            continue
        key = product_key(row.get("系列", ""),
                          row.get("详情编号", "").strip() or row.get("列表编号", ""),
                          row.get("详情名称", "").strip() or row.get("列表名称", ""))
        if key not in seen:
            rows.append(row)
            seen.add(key)
    return rows


def load_skipped_multichild(details_path: Path, series_list: list[str]) -> list[dict[str, str]]:
    source_rows = read_csv(details_path)
    result: list[dict[str, str]] = []
    for series in series_list:
        for row in source_rows:
            if not same_label(row.get("系列", ""), series):
                continue
            if row.get("统计状态", "").startswith("多子类"):
                result.append(row)
    return result


def load_skipped_zero_card(details_path: Path, series_list: list[str]) -> list[dict[str, str]]:
    source_rows = read_csv(details_path)
    result: list[dict[str, str]] = []
    for series in series_list:
        for row in source_rows:
            if not same_label(row.get("系列", ""), series):
                continue
            if not row.get("统计状态", "").startswith("完成"):
                continue
            expected = parse_int(row.get("页面张数", ""))
            if expected == 0 and row.get("详情编号", "").strip():
                result.append(row)
    return result


def parse_series_args(values: list[str] | None) -> list[str]:
    if not values:
        return []  # 主流程从本次 Phase2 表格动态读取系列
    result: list[str] = []
    for value in values:
        for part in re.split(r"[,，]", value):
            part = part.strip()
            if part and not any(same_label(part, old) for old in result):
                result.append(part)
    return result


# ---------------------------------------------------------------------------
# 分类页导航：沿用第一步已验证的 kindRv / pack 边界
# ---------------------------------------------------------------------------

def validate_lorcana_home(root: ET.Element) -> None:
    texts = all_texts(root)
    normalized = [re.sub(r"\s+", "", text).casefold() for text in texts]
    if not any(alias in text for text in normalized
               for alias in ("洛卡纳", "洛卡那", "lorcana")):
        raise RuntimeError("当前不是迪士尼洛卡纳卡牌库；请手动选择“迪士尼洛卡纳 → 英文”。")
    if not any(TARGET_LANGUAGE in text for text in texts):
        raise RuntimeError("当前语言未识别为英文；请确认顶部语言后重试。")


def is_category_page(root: ET.Element) -> bool:
    return find_node(root, "com.jihuanshe:id/pack") is not None


def is_library_home(root: ET.Element) -> bool:
    return find_node(root, "com.jihuanshe:id/categoryBtn") is not None


def ensure_category_page(
    adb_path: str,
    serial: str,
    local_xml: Path,
    wait_seconds: float,
) -> ET.Element:
    for _ in range(8):
        dump_current_page(adb_path, serial, local_xml)
        root = ET.parse(local_xml).getroot()

        if is_category_page(root):
            return root

        category_button = find_node(root, "com.jihuanshe:id/categoryBtn")
        if category_button is not None:
            validate_lorcana_home(root)
            print("检测到迪士尼洛卡纳英文卡牌库首页，点击“分类”……", flush=True)
            tap_node_center(adb_path, serial, category_button)
            time.sleep(wait_seconds)
            continue

        # 卡牌列表/其他集换社内部页面：返回。
        if any(
            node.attrib.get("package") == "com.jihuanshe"
            for node in root.iter("node")
        ):
            run_adb(adb_path, serial, "shell", "input", "keyevent", "4")
            time.sleep(wait_seconds)
            continue

        break

    raise RuntimeError("无法进入“分类”页；请手动回到迪士尼洛卡纳英文卡牌库首页后重试。")


def _node_intersects_viewport(item: ET.Element, viewport: ET.Element) -> bool:
    left, top, right, bottom = parse_bounds(item.attrib.get("bounds", ""))
    v_left, v_top, v_right, v_bottom = parse_bounds(viewport.attrib.get("bounds", ""))
    if right <= left or bottom <= top:
        return False
    if v_right <= v_left or v_bottom <= v_top:
        return True
    return right > v_left and left < v_right and bottom > v_top and top < v_bottom


def _kindrv_entry(item: ET.Element) -> tuple[str, ET.Element] | None:
    candidates: list[ET.Element] = []
    if item.attrib.get("class") == "android.widget.TextView":
        candidates.append(item)
    candidates.extend(
        node
        for node in item.iter("node")
        if node is not item and node.attrib.get("class") == "android.widget.TextView"
    )
    for node in candidates:
        text = node.attrib.get("text", "").strip()
        if not text:
            continue
        left, top, right, bottom = parse_bounds(node.attrib.get("bounds", ""))
        if right <= left or bottom <= top:
            continue
        i_left, i_top, i_right, i_bottom = parse_bounds(item.attrib.get("bounds", ""))
        return text, item if i_right > i_left and i_bottom > i_top else node
    return None


def visible_left_series(root: ET.Element) -> list[tuple[str, ET.Element]]:
    kind_rv = find_node(root, "com.jihuanshe:id/kindRv")
    if kind_rv is None:
        raise RuntimeError("分类页缺少左侧 kindRv。")
    result: list[tuple[str, ET.Element]] = []
    for item in list(kind_rv):
        if not _node_intersects_viewport(item, kind_rv):
            continue
        parsed = _kindrv_entry(item)
        if parsed:
            result.append(parsed)
    result.sort(key=lambda pair: parse_bounds(pair[1].attrib.get("bounds", ""))[1])
    return result


def swipe_kindrv(
    adb_path: str,
    serial: str,
    kind_rv: ET.Element,
    direction: str,
) -> None:
    left, top, right, bottom = parse_bounds(kind_rv.attrib.get("bounds", ""))
    x = (left + right) // 2
    inset = min(180, max(48, (bottom - top) // 7))
    if direction == "up":
        start_y, end_y = bottom - inset, top + inset
    else:
        start_y, end_y = top + inset, bottom - inset
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
        "420",
    )


def direct_pack_headers(pack: ET.Element) -> list[str]:
    values: list[str] = []
    for item in list(pack):
        if item.attrib.get("class") == "android.widget.TextView":
            text = item.attrib.get("text", "").strip()
            if text:
                values.append(text)
    return values


def wait_for_series_header(
    adb_path: str,
    serial: str,
    local_xml: Path,
    series: str,
    timeout: float,
) -> ET.Element | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        dump_current_page(adb_path, serial, local_xml)
        root = ET.parse(local_xml).getroot()
        pack = find_node(root, "com.jihuanshe:id/pack")
        if pack is not None and any(
            same_label(value, series) for value in direct_pack_headers(pack)
        ):
            return root
        time.sleep(0.25)
    return None


def select_series(
    adb_path: str,
    serial: str,
    local_xml: Path,
    series: str,
    wait_seconds: float,
    max_swipes: int,
) -> ET.Element:
    for _ in range(max_swipes + 1):
        root = ensure_category_page(adb_path, serial, local_xml, wait_seconds)
        for text, node in visible_left_series(root):
            if same_label(text, series):
                # 模拟器在长批次中偶发吞掉一次点击。不要一次失败就终止；
                # 最多重试 4 次，并且每次都重新取得当前左侧节点坐标。
                for tap_attempt in range(1, 5):
                    current_root = ensure_category_page(
                        adb_path, serial, local_xml, wait_seconds
                    )
                    current_node = None
                    for current_text, candidate in visible_left_series(current_root):
                        if same_label(current_text, series):
                            current_node = candidate
                            break
                    if current_node is None:
                        break

                    if tap_attempt > 1:
                        print(
                            f"      左侧“{series}”点击后右侧未切换，重试 {tap_attempt}/4……",
                            flush=True,
                        )

                    tap_node_center(adb_path, serial, current_node)
                    anchored = wait_for_series_header(
                        adb_path,
                        serial,
                        local_xml,
                        series,
                        max(2.0, wait_seconds * 4),
                    )
                    if anchored is not None:
                        return anchored

                raise RuntimeError(
                    f"已点击左侧“{series}”4 次，但右侧仍没有定位到该系列。"
                )

        kind_rv = find_node(root, "com.jihuanshe:id/kindRv")
        if kind_rv is None or kind_rv.attrib.get("scrollable") != "true":
            break
        swipe_kindrv(adb_path, serial, kind_rv, "up")
        time.sleep(wait_seconds)

    raise RuntimeError(f"左侧找不到：{series}")


def pack_product_name(item: ET.Element, code: str) -> str:
    texts = [value for value in all_texts(item) if value.strip()]
    # 分类页通常 numberTv 是编号，剩下的文本是名称。
    candidates = [value for value in texts if value.strip() != code.strip()]
    if not candidates:
        return ""
    # 右侧一行可能带其他轻量文字，名称通常是最后一个可见文本。
    return candidates[-1].strip()


def visible_pack_products(pack: ET.Element) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in list(pack):
        if not _node_intersects_viewport(item, pack):
            continue
        code = child_text(item, "com.jihuanshe:id/numberTv").strip()
        if not code and item.attrib.get("class") == "android.widget.TextView":
            continue
        name = pack_product_name(item, code)
        if not name:
            continue
        left, top, right, bottom = parse_bounds(item.attrib.get("bounds", ""))
        if right <= left or bottom <= top:
            continue
        rows.append(
            {
                "编号": code,
                "名称": name,
                "node": item,
                "top": top,
            }
        )
    rows.sort(key=lambda row: int(row["top"]))
    return rows


def swipe_pack_up(adb_path: str, serial: str, pack: ET.Element) -> None:
    left, top, right, bottom = parse_bounds(pack.attrib.get("bounds", ""))
    if right <= left or bottom - top < 160:
        raise RuntimeError("右侧 pack 没有有效坐标。")
    x = int(left + (right - left) * 0.65)
    start_y = int(top + (bottom - top) * 0.80)
    end_y = int(top + (bottom - top) * 0.48)
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
        "420",
    )


def open_category_product(
    adb_path: str,
    serial: str,
    local_xml: Path,
    series: str,
    code: str,
    name: str,
    wait_seconds: float,
    max_swipes: int,
) -> None:
    select_series(
        adb_path,
        serial,
        local_xml,
        series,
        wait_seconds,
        max_swipes=35,
    )

    previous_signature: tuple[tuple[str, str], ...] | None = None
    unchanged = 0

    for page in range(max_swipes + 1):
        dump_current_page(adb_path, serial, local_xml)
        root = ET.parse(local_xml).getroot()
        pack = find_node(root, "com.jihuanshe:id/pack")
        if pack is None:
            raise RuntimeError("分类页缺少右侧 pack。")

        rows = visible_pack_products(pack)
        signature = tuple((str(r["编号"]), str(r["名称"])) for r in rows)

        exact = [
            r for r in rows
            if str(r["编号"]).strip() == code.strip()
            and same_label(str(r["名称"]), name)
        ]
        if exact:
            print(
                f"      分类页命中：{exact[0]['编号']} {exact[0]['名称']}",
                flush=True,
            )

            # 长批次运行时，模拟器偶尔会吞掉一次 input tap：
            # XML 中已经精确找到目标行，但点击后仍停在分类页。
            # 旧版会直接 return，随后 wait_for_card_list 等到超时，
            # 最终留下“目标产品明明在屏幕上”的失败 XML。
            #
            # v4 在这里先验证页面是否真的离开分类页；若点击未生效，
            # 就重新 dump、重新定位同一目标行并再次点击。最多 4 次。
            for tap_attempt in range(1, 5):
                current_target = exact[0]
                if tap_attempt > 1:
                    dump_current_page(adb_path, serial, local_xml)
                    retry_root = ET.parse(local_xml).getroot()

                    # 若已经进入卡牌列表/其他产品页，说明前一次点击只是跳转较慢。
                    retry_texts = all_texts(retry_root)
                    if "卡牌列表" in retry_texts or not is_category_page(retry_root):
                        return

                    retry_pack = find_node(retry_root, "com.jihuanshe:id/pack")
                    if retry_pack is None:
                        return

                    retry_rows = visible_pack_products(retry_pack)
                    retry_exact = [
                        r for r in retry_rows
                        if str(r["编号"]).strip() == code.strip()
                        and same_label(str(r["名称"]), name)
                    ]
                    if not retry_exact:
                        raise RuntimeError(
                            f"目标产品第一次点击未跳转，重试时该行已不在当前屏：{code} {name}"
                        )
                    current_target = retry_exact[0]
                    print(
                        f"      目标行点击后仍停留分类页，重试点击 {tap_attempt}/4……",
                        flush=True,
                    )

                tap_node_center(
                    adb_path,
                    serial,
                    current_target["node"],  # type: ignore[arg-type]
                )

                # 这里只确认“点击确实发生了页面跳转”；卡牌数据本身可能需要
                # 更久加载，后面的 wait_for_card_list() 仍会最长等待约 30 秒。
                deadline = time.monotonic() + max(2.5, wait_seconds * 7)
                while time.monotonic() < deadline:
                    time.sleep(0.25)
                    dump_current_page(adb_path, serial, local_xml)
                    opened_root = ET.parse(local_xml).getroot()
                    opened_texts = all_texts(opened_root)
                    if "卡牌列表" in opened_texts:
                        return
                    if not is_category_page(opened_root):
                        return

            raise RuntimeError(
                f"分类页已命中目标产品，但连续 4 次点击后仍未进入产品页：{code} {name}"
            )

        # 同编号但名称不同绝不点击。
        same_code = [
            f"{r['编号']} {r['名称']}"
            for r in rows
            if str(r["编号"]).strip() == code.strip()
        ]
        if same_code:
            print(
                "      当前屏有同编号但名称不同，跳过：" + "；".join(same_code),
                flush=True,
            )

        if signature and signature == previous_signature:
            unchanged += 1
            if unchanged >= 2:
                break
        else:
            unchanged = 0
        previous_signature = signature

        if page >= max_swipes:
            break
        if pack.attrib.get("scrollable") != "true" and page > 0:
            break

        swipe_pack_up(adb_path, serial, pack)
        time.sleep(wait_seconds)

    raise RuntimeError(f"分类页右侧找不到目标产品：{code} {name}")


# ---------------------------------------------------------------------------
# 卡牌列表解析
# ---------------------------------------------------------------------------

def has_english_name(value: str) -> bool:
    return bool(re.search(r"[A-Za-z]", value or "")) and not bool(
        re.search(r"[\u3040-\u30ff\u3400-\u9fff]", value or "")
    )


def is_language_text(value: str) -> bool:
    return value.strip().casefold() in {"日", "中", "英", "繁", "简", "英文", "英语", "en", "eng", "english"}


def looks_like_rarity(value: str) -> bool:
    stripped = value.strip().upper()
    if stripped in RARITY_TOKENS:
        return True
    return bool(re.fullmatch(r"[●◆★☆]{0,2}\s*[A-Z]{1,5}", stripped))


def split_number_display(value: str) -> tuple[str, str]:
    text = value.strip()
    status = "无编号" if "无编号" in text else ""
    match = re.search(r"#\s*([0-9A-Za-z]+)", text)
    card_no = f"#{match.group(1)}" if match else ""
    if not card_no:
        slash_number = re.search(r"(?<![A-Za-z0-9])\d+\s*/\s*\d+(?![A-Za-z0-9])", text)
        if slash_number:
            card_no = re.sub(r"\s+", "", slash_number.group(0))
    return status, card_no


def classify_noncard(rarity: str, texts: list[str]) -> bool:
    """优先用 ll_vision 的“类型/稀有度”判断非卡，避免商品名误触发。"""
    norm_known = {normalize_label(v) for v in NON_CARD_RARITIES}
    rarity_norm = normalize_label(rarity)
    if rarity_norm in norm_known:
        return True
    # App 改版、rarity 没单独拆出来时才退回到其他可见文字。
    if not rarity_norm:
        for value in texts:
            norm = normalize_label(value)
            if norm in norm_known:
                return True
    return False


def ordered_text_nodes(item: ET.Element) -> list[tuple[int, int, str, str]]:
    rows: list[tuple[int, int, str, str]] = []
    for node in item.iter("node"):
        text = node.attrib.get("text", "").strip()
        if not text:
            continue
        left, top, right, bottom = parse_bounds(node.attrib.get("bounds", ""))
        if right <= left or bottom <= top:
            continue
        rows.append(
            (
                top,
                left,
                text,
                node.attrib.get("resource-id", ""),
            )
        )
    rows.sort()
    return rows


MISSING_CARD_IMAGE_MARKERS = (
    "卡图暂缺",
    "卡圖暫缺",
    "卡图缺失",
    "卡圖缺失",
)


def detect_missing_card_image(item: ET.Element) -> bool:
    """判断当前卡牌行是否显示“卡图暂缺”占位。

    优先读取 UIAutomator 能看到的 text / content-desc。当前 APP 的缺图占位
    会显示“卡图暂缺”；若后续页面改成繁体或“卡图缺失”，也一并兼容。
    这里只标注状态，不参与卡牌身份和跨屏去重。
    """
    for node in item.iter("node"):
        for attr in ("text", "content-desc"):
            value = node.attrib.get(attr, "").strip()
            if not value:
                continue
            compact = re.sub(r"\s+", "", value)
            if any(marker in compact for marker in MISSING_CARD_IMAGE_MARKERS):
                return True
    return False


def parse_card_item(item: ET.Element) -> dict[str, object] | None:
    """解析卡牌列表的一行。

    当前洛卡纳英文版卡牌列表真实页面结构：
      officialNameTv = 中文名
      nwNameTv       = 英文名
      versionTv      = 编号显示（如“无编号 #013”或 OPG2-Y01）
      rarityTv       = 稀有度/商品类型（如 C、原盒、原包）
      lanTv          = 语言（如 日）

    同时保留旧布局 fallback，避免其他系列页面结构略有差异。
    """
    texts = all_texts(item)
    if not texts:
        return None

    # 真实卡牌列表优先使用明确 resource-id。
    official_node = find_node(item, "com.jihuanshe:id/officialNameTv")
    nw_node = find_node(item, "com.jihuanshe:id/nwNameTv")
    version_node = find_node(item, "com.jihuanshe:id/versionTv")
    rarity_node = find_node(item, "com.jihuanshe:id/rarityTv")
    lan_node = find_node(item, "com.jihuanshe:id/lanTv")

    chinese_name = (official_node.attrib.get("text", "") if official_node is not None else "").strip()
    english_name = (nw_node.attrib.get("text", "") if nw_node is not None else "").strip()
    number_display = (version_node.attrib.get("text", "") if version_node is not None else "").strip()
    rarity = (rarity_node.attrib.get("text", "") if rarity_node is not None else "").strip()
    language = (lan_node.attrib.get("text", "") if lan_node is not None else "").strip()

    # 卡牌列表滚动时会把上一行/下一行的一部分节点留在 XML 中。
    # v5 为了排除半截行，曾要求 official/nw/version/rarity/lan 五个文本全部非空。
    # 这会误伤“完整显示但官方名或英文名其中一项本来就是空”的合法旧卡。
    # v6 把“是否完整显示”交给 page_card_rows() 的几何高度判断；在字段层面只
    # 要求编号、稀有度、语言齐全，并且两个名称至少有一个可见。缺失的名称保持空，
    # 不再用另一种语言硬回填，以免制造假数据。
    modern_nodes_present = any(
        node is not None
        for node in (official_node, nw_node, version_node, rarity_node, lan_node)
    )
    if modern_nodes_present:
        if not (number_display and rarity and language):
            return None
        if not (chinese_name or english_name):
            return None

    # 旧布局 fallback。
    vision = find_node(item, "com.jihuanshe:id/ll_vision")
    vision_texts = all_texts(vision)
    if not number_display or not rarity:
        if len(vision_texts) >= 2:
            number_display = number_display or vision_texts[0].strip()
            rarity = rarity or vision_texts[1].strip()
        else:
            number_display = number_display or next(
                (
                    value for value in texts
                    if "无编号" in value
                    or re.search(r"#\s*[0-9A-Za-z]+", value)
                    or re.search(r"[A-Za-z0-9]+-[A-Za-z0-9]+", value)
                    or re.search(r"\d+\s*/\s*\d+", value)
                ),
                "",
            )
            rarity = rarity or next((value for value in texts if looks_like_rarity(value)), "")

    ordered = ordered_text_nodes(item)
    ordered_values = [row[2] for row in ordered]

    if not chinese_name and not modern_nodes_present:
        fallback_name = child_text(item, "com.jihuanshe:id/name").strip()
        if has_english_name(fallback_name):
            english_name = english_name or fallback_name
        else:
            chinese_name = fallback_name
    if not chinese_name and not modern_nodes_present:
        excluded = {
            number_display.strip(),
            rarity.strip(),
            language.strip(),
        }
        chinese_name = next(
            (
                value for value in ordered_values
                if value.strip()
                and value.strip() not in excluded
                and not is_language_text(value)
                and not has_english_name(value)
            ),
            "",
        )

    if not language:
        language = child_text(item, "com.jihuanshe:id/tvLan").strip()
    if not language:
        language = next((value for value in texts if is_language_text(value)), "")

    if not english_name and not modern_nodes_present:
        excluded_norm = {
            normalize_label(chinese_name),
            normalize_label(number_display),
            normalize_label(rarity),
            normalize_label(language),
        }
        en_candidates = [
            value
            for value in ordered_values
            if normalize_label(value) not in excluded_norm
            and not is_language_text(value)
        ]
        if en_candidates:
            english_name = next((v for v in en_candidates if has_english_name(v)), "")

    # 部分露出的最后一行可能只有名称、尚未显示编号/稀有度；
    # 这种行等下一次滚动完整显示后再收录，避免产生半条记录。
    if not number_display or not rarity:
        return None
    if not (chinese_name or english_name):
        return None

    is_noncard = classify_noncard(rarity, texts)
    missing_image = detect_missing_card_image(item)
    number_status, card_no = split_number_display(number_display)

    identity = (
        normalize_label(chinese_name),
        normalize_label(english_name),
        normalize_label(number_display),
        normalize_label(rarity),
        normalize_label(language),
    )

    return {
        "中文名": chinese_name,
        "英文名": english_name,
        "编号显示": number_display,
        "编号状态": number_status,
        "卡号": card_no,
        "稀有度": rarity,
        "语言": language,
        "卡图状态": "卡图暂缺" if missing_image and not is_noncard else "",
        "全部可见文字": " | ".join(texts),
        "非卡": is_noncard,
        "_identity": identity,
    }

def find_card_recycler(root: ET.Element) -> ET.Element | None:
    """定位真实卡牌列表 RecyclerView。

    该卡牌列表 RecyclerView 本身没有 resource-id，因此不能按 id 找。
    改为根据其直接子项是否包含 officialNameTv / nwNameTv / versionTv / rarityTv
    来识别。
    """
    best: tuple[int, ET.Element] | None = None
    for node in root.iter("node"):
        if node.attrib.get("class") != "androidx.recyclerview.widget.RecyclerView":
            continue
        left, top, right, bottom = parse_bounds(node.attrib.get("bounds", ""))
        if right <= left or bottom <= top:
            continue

        materialized = 0
        strong_rows = 0
        for item in list(node):
            has_name = (
                find_node(item, "com.jihuanshe:id/officialNameTv") is not None
                or find_node(item, "com.jihuanshe:id/nwNameTv") is not None
                or find_node(item, "com.jihuanshe:id/name") is not None
            )
            has_meta = (
                find_node(item, "com.jihuanshe:id/versionTv") is not None
                or find_node(item, "com.jihuanshe:id/rarityTv") is not None
                or find_node(item, "com.jihuanshe:id/ll_vision") is not None
            )
            if has_name:
                materialized += 1
            if has_name and has_meta:
                strong_rows += 1

        if materialized == 0:
            continue

        area = (right - left) * (bottom - top)
        score = strong_rows * 100_000_000 + materialized * 10_000_000 + area
        if best is None or score > best[0]:
            best = (score, node)
    return best[1] if best else None

def page_card_rows(root: ET.Element) -> tuple[list[dict[str, object]], ET.Element | None]:
    recycler = find_card_recycler(root)
    if recycler is None:
        return [], None

    r_left, r_top, r_right, r_bottom = parse_bounds(recycler.attrib.get("bounds", ""))

    # 先收集看起来属于卡牌/商品行的直接子项，并用同屏完整行高度估算标准高度。
    # 实测该类卡牌列表完整行约 266px；滚动边缘的残行可能只剩 1~180px。
    candidates: list[tuple[int, int, int, ET.Element]] = []
    heights: list[int] = []
    for item in list(recycler):
        left, top, right, bottom = parse_bounds(item.attrib.get("bounds", ""))
        if right <= left or bottom <= top:
            continue
        if r_right > r_left and r_bottom > r_top:
            if not (
                right > r_left
                and left < r_right
                and bottom > r_top
                and top < r_bottom
            ):
                continue

        has_modern_meta = any(
            find_node(item, rid) is not None
            for rid in (
                "com.jihuanshe:id/officialNameTv",
                "com.jihuanshe:id/nwNameTv",
                "com.jihuanshe:id/versionTv",
                "com.jihuanshe:id/rarityTv",
                "com.jihuanshe:id/lanTv",
            )
        )
        has_old_meta = (
            find_node(item, "com.jihuanshe:id/name") is not None
            or find_node(item, "com.jihuanshe:id/ll_vision") is not None
        )
        if not (has_modern_meta or has_old_meta):
            continue

        height = bottom - top
        candidates.append((top, left, height, item))
        heights.append(height)

    if not candidates:
        return [], recycler

    # 用本屏较高的候选行估算“完整行”高度。不能简单取中位数，因为底部/顶部
    # 可能同时有残行；完整行通常占同屏候选中的最高一档。
    sorted_heights = sorted(heights, reverse=True)
    top_band = sorted_heights[: max(1, min(5, len(sorted_heights)))]
    typical_height = int(round(sum(top_band) / len(top_band)))
    # 给长短布局留余量，但把 154px/38px 这种真实残行排除。
    min_complete_height = max(120, int(typical_height * 0.72))

    sortable: list[tuple[int, int, ET.Element]] = []
    for top, left, height, item in candidates:
        if height < min_complete_height:
            continue
        sortable.append((top, left, item))

    rows: list[dict[str, object]] = []
    for top, left, item in sorted(sortable):
        _, _, _, bottom = parse_bounds(item.attrib.get("bounds", ""))
        height = max(0, bottom - top)
        parsed = parse_card_item(item)
        if parsed is not None:
            parsed["_row_top"] = top
            parsed["_row_bottom"] = bottom
            parsed["_row_height"] = height
            # 完整行实测约 266px。贴住 RecyclerView 顶部且明显低于本屏完整行
            # 高度的行，是上一屏正在滚出的残留行。保留它用于重叠匹配，但不能
            # 在匹配失败时当成新条目。
            parsed["_top_boundary_partial"] = bool(
                top <= r_top + 2
                and height < max(min_complete_height, int(typical_height * 0.90))
            )
            rows.append(parsed)
    return rows, recycler


def wait_for_card_list(
    adb_path: str,
    serial: str,
    local_xml: Path,
    timeout: float,
) -> ET.Element:
    """等待卡牌列表真正 materialize 出卡牌行。

    PROMO 父产品可能先进入“卡牌列表”壳页面，此时 RecyclerView 已经存在，
    但仍是 scrollable=false / 0 children；几秒后网络数据才填充。
    旧版看到 RecyclerView 就立即返回，随后把这个瞬时空状态误判为“空父列表”。
    v3 必须等到真正出现可解析行；只有已经出现明确 - END - 且仍无行时，
    才允许把它交给后续“真实空列表”逻辑。
    """
    deadline = time.monotonic() + timeout
    last_root: ET.Element | None = None
    saw_card_page = False
    empty_samples = 0
    while time.monotonic() < deadline:
        dump_current_page(adb_path, serial, local_xml)
        root = ET.parse(local_xml).getroot()
        last_root = root
        texts = all_texts(root)
        rows, recycler = page_card_rows(root)
        on_card_page = "卡牌列表" in texts
        saw_card_page = saw_card_page or on_card_page

        if rows:
            return root

        if on_card_page and recycler is not None:
            empty_samples += 1
            # 明确到底且没有任何卡牌行，才可能是真空列表。
            if "- END -" in texts:
                return root
            # 仅看到空 RecyclerView 不是空列表证据；PROMO 大列表常会延迟加载。
            if empty_samples in (8, 20, 40):
                print(
                    f"      卡牌列表已打开但数据尚未加载，继续等待……（空屏采样 {empty_samples} 次）",
                    flush=True,
                )

        time.sleep(0.35)

    if saw_card_page:
        raise RuntimeError(
            f"卡牌列表已打开，但等待 {timeout:.1f} 秒后仍没有加载出卡牌行。"
        )
    raise RuntimeError("点击产品后没有进入卡牌列表。")


def stable_card_page(
    adb_path: str,
    serial: str,
    local_xml: Path,
    wait_seconds: float,
    max_samples: int = 16,
) -> tuple[ET.Element, list[dict[str, object]], ET.Element]:
    previous: tuple[tuple[object, ...], ...] | None = None
    stable = 0
    last: tuple[ET.Element, list[dict[str, object]], ET.Element] | None = None

    for _ in range(max_samples):
        dump_current_page(adb_path, serial, local_xml)
        root = ET.parse(local_xml).getroot()
        rows, recycler = page_card_rows(root)
        if recycler is None or not rows:
            previous = None
            stable = 0
            time.sleep(wait_seconds)
            continue

        signature = tuple(row["_identity"] for row in rows)
        last = (root, rows, recycler)
        if signature == previous:
            stable += 1
            if stable >= 1:  # 两次连续 dump 一致即可
                return last
        else:
            stable = 0
        previous = signature
        time.sleep(wait_seconds)

    if last is not None:
        return last
    raise RuntimeError("卡牌列表当前屏一直没有稳定的卡牌行。")


def swipe_card_recycler_up(
    adb_path: str,
    serial: str,
    recycler: ET.Element,
    *,
    recovery: bool = False,
) -> None:
    left, top, right, bottom = parse_bounds(recycler.attrib.get("bounds", ""))
    if right <= left or bottom - top < 180:
        raise RuntimeError("卡牌列表 RecyclerView 没有有效坐标。")
    x = int(left + (right - left) * 0.58)
    if recovery:
        # 卡在同一屏时使用更大幅度的恢复滚动；正常抓取仍使用小步滚动保留重叠。
        start_y = int(top + (bottom - top) * 0.86)
        end_y = int(top + (bottom - top) * 0.32)
        duration = "650"
    else:
        # 小步滚动，保留大量重叠，减少虚拟列表漏项。
        start_y = int(top + (bottom - top) * 0.78)
        end_y = int(top + (bottom - top) * 0.50)
        duration = "420"
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
        duration,
    )


def _rows_compatible_for_overlap(
    old: dict[str, object],
    cur: dict[str, object],
) -> bool:
    """判断两屏边界上的两行是否可以视为同一个列表项。

    RecyclerView 在滚动边缘会裁掉名称节点：同一张卡上一屏可能同时有
    officialNameTv + nwNameTv，下一屏只剩 nwNameTv。若只比较完整 _identity，
    这种边界行会突然“不相等”，从而让整屏重叠变成 0。

    这里要求编号/稀有度/语言不冲突，并且至少有一个双方都可见的名称一致。
    这样允许“完整行 ↔ 裁切行”匹配，但不会仅凭所有旧卡共有的
    “无编号 / 无标记 / 日”就把不同卡误合并。
    """
    for key in ("编号显示", "稀有度", "语言"):
        a = normalize_label(str(old.get(key, "")))
        b = normalize_label(str(cur.get(key, "")))
        if a and b and a != b:
            return False

    shared_name = False
    for key in ("中文名", "英文名"):
        a = normalize_label(str(old.get(key, "")))
        b = normalize_label(str(cur.get(key, "")))
        if a and b:
            if a != b:
                return False
            shared_name = True

    return shared_name


def _max_suffix_prefix_overlap_rows(
    collected_rows: list[dict[str, object]],
    page_rows: list[dict[str, object]],
) -> int:
    """找已累计序列尾部与当前屏头部的最长“兼容重叠”。"""
    if not collected_rows or not page_rows:
        return 0
    limit = min(len(collected_rows), len(page_rows))
    for size in range(limit, 0, -1):
        left = collected_rows[-size:]
        right = page_rows[:size]
        if all(_rows_compatible_for_overlap(a, b) for a, b in zip(left, right)):
            return size
    return 0


def _find_page_overlap(
    collected_rows: list[dict[str, object]],
    page_rows: list[dict[str, object]],
) -> tuple[int, int]:
    """返回 (顶部残行跳过数, 与已累计尾部的重叠数)。

    正常情况从当前屏第 0 行开始做后缀/前缀匹配。若第 0 行是贴着 RecyclerView
    顶部的明显裁切残行，则额外尝试跳过它后再匹配。这样即使残行只剩一个名称，
    也不会导致整屏被当成 8 个全新条目。
    """
    if not page_rows:
        return 0, 0

    max_skip = 0
    # 最多允许跳过 2 个明确标记为顶部裁切的边界行；正常完整行绝不跳。
    while (
        max_skip < min(2, len(page_rows))
        and bool(page_rows[max_skip].get("_top_boundary_partial"))
    ):
        max_skip += 1

    best_skip = 0
    best_overlap = _max_suffix_prefix_overlap_rows(collected_rows, page_rows)
    for skip in range(1, max_skip + 1):
        overlap = _max_suffix_prefix_overlap_rows(collected_rows, page_rows[skip:])
        if overlap > best_overlap:
            best_skip = skip
            best_overlap = overlap

    # 若裁切行本身无法参与匹配，也宁可把它作为旧屏残留丢掉，
    # 不把一个缺字段的顶部半行追加成“新卡”。
    if best_overlap == 0 and max_skip > 0:
        best_skip = max_skip

    return best_skip, best_overlap


def _identity_duplicate_extra(rows: list[dict[str, object]]) -> int:
    counts: dict[tuple[object, ...], int] = {}
    for row in rows:
        key = row["_identity"]
        counts[key] = counts.get(key, 0) + 1
    return sum(max(0, count - 1) for count in counts.values())


def is_basic_energy_card(row: dict[str, object]) -> bool:
    """洛卡纳英文版不应用宝可梦基础能量数量口径例外。"""
    return False


def basic_energy_count(cards: list[dict[str, object]]) -> int:
    return sum(1 for row in cards if is_basic_energy_card(row))


def basic_energy_page_count_gap(
    cards: list[dict[str, object]],
    noncards: list[dict[str, object]],
    expected_cards: int,
    expected_total: int | None,
) -> int:
    """返回“页面张数未计入基础能量”的差值；否则返回 0。

    判定非常保守：
    1) 实际总项目必须精确等于 APP 的“收录商品数量”；
    2) 实际卡牌数必须大于页面张数；
    3) 多出来的张数必须恰好等于抓到的基础能量张数。

    这样不会把普通的重复抓取/跨屏合并错误静默放过。
    """
    if expected_total is None:
        return 0
    actual_total = len(cards) + len(noncards)
    extra = len(cards) - expected_cards
    if extra <= 0 or actual_total != expected_total:
        return 0
    return extra if basic_energy_count(cards) == extra else 0



# ---------------------------------------------------------------------------
# 多子类父产品兜底：分类页父入口为空时，从首页父产品聚合商品区抓取
# ---------------------------------------------------------------------------

HOME_ITEM_NAME_IDS = (
    "com.jihuanshe:id/officialNameTv",
    "com.jihuanshe:id/productNameTv",
    "com.jihuanshe:id/packNameTv",
    "com.jihuanshe:id/name",
    "com.jihuanshe:id/titleTv",
)
HOME_ITEM_CODE_IDS = (
    "com.jihuanshe:id/versionTv",
    "com.jihuanshe:id/productNumberTv",
    "com.jihuanshe:id/packNumTv",
    "com.jihuanshe:id/numberTv",
)
HOME_ITEM_TYPE_IDS = (
    "com.jihuanshe:id/productRarityTv",
    "com.jihuanshe:id/packRarityTv",
    "com.jihuanshe:id/rarityTv",
)


def node_signature(node: ET.Element) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (child.attrib.get("bounds", ""), *all_texts(child))
        for child in list(node)
    )


def child_texts(root: ET.Element, resource_id: str) -> list[str]:
    values: list[str] = []
    for node in root.iter("node"):
        if node.attrib.get("resource-id") == resource_id:
            value = node.attrib.get("text", "").strip()
            if value:
                values.append(value)
    return values


def first_text_from_ids(root: ET.Element, resource_ids: tuple[str, ...]) -> str:
    for resource_id in resource_ids:
        value = child_text(root, resource_id).strip()
        if value:
            return value
    return ""


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
    if direction == "left":
        start_x, end_x = right - inset, left + inset
    else:
        start_x, end_x = left + inset, right - inset
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


def swipe_node_vertical(
    adb_path: str,
    serial: str,
    node: ET.Element,
    direction: str = "up",
) -> None:
    left, top, right, bottom = parse_bounds(node.attrib.get("bounds", ""))
    if right - left < 80 or bottom - top < 180:
        raise RuntimeError("纵向商品列表没有有效坐标。")
    inset = min(160, max(50, (bottom - top) // 6))
    if direction == "up":
        start_y, end_y = bottom - inset, top + inset
    else:
        start_y, end_y = top + inset, bottom - inset
    run_adb(
        adb_path,
        serial,
        "shell",
        "input",
        "swipe",
        str((left + right) // 2),
        str(start_y),
        str((left + right) // 2),
        str(end_y),
        "500",
    )


def ensure_library_home_page(
    adb_path: str,
    serial: str,
    local_xml: Path,
    wait_seconds: float,
) -> ET.Element:
    """从分类页/空卡牌列表/详情页返回迪士尼洛卡纳英文卡牌库首页。"""
    for _ in range(10):
        dump_current_page(adb_path, serial, local_xml)
        root = ET.parse(local_xml).getroot()
        if (
            find_node(root, "com.jihuanshe:id/categoryBtn") is not None
            and find_node(root, "com.jihuanshe:id/packRv") is not None
        ):
            validate_lorcana_home(root)
            return root

        if any(
            node.attrib.get("package") == "com.jihuanshe"
            for node in root.iter("node")
        ):
            run_adb(adb_path, serial, "shell", "input", "keyevent", "4")
            time.sleep(wait_seconds)
            continue
        break
    raise RuntimeError("无法返回迪士尼洛卡纳英文卡牌库首页。")


def reset_home_strip_to_start(
    adb_path: str,
    serial: str,
    local_xml: Path,
    resource_id: str,
    wait_seconds: float,
    max_swipes: int = 36,
) -> ET.Element:
    previous: tuple[tuple[str, ...], ...] | None = None
    unchanged = 0
    for _ in range(max_swipes + 1):
        root = ensure_library_home_page(adb_path, serial, local_xml, wait_seconds)
        strip = find_node(root, resource_id)
        if strip is None:
            raise RuntimeError(f"首页缺少横向列表：{resource_id}")
        signature = node_signature(strip)
        if strip.attrib.get("scrollable") != "true":
            return root
        unchanged = unchanged + 1 if signature and signature == previous else 0
        if unchanged >= 1:
            return root
        previous = signature
        swipe_node_horizontal(adb_path, serial, strip, "right")
        time.sleep(wait_seconds)
    raise RuntimeError(f"横向列表 {resource_id} 无法回到起点。")


def select_home_series_for_parent(
    adb_path: str,
    serial: str,
    local_xml: Path,
    series: str,
    wait_seconds: float,
) -> None:
    reset_home_strip_to_start(
        adb_path,
        serial,
        local_xml,
        "com.jihuanshe:id/scroll_view",
        wait_seconds,
    )
    previous: tuple[tuple[str, ...], ...] | None = None
    unchanged = 0
    for _ in range(40):
        root = ensure_library_home_page(adb_path, serial, local_xml, wait_seconds)
        container = find_node(root, "com.jihuanshe:id/title_container")
        strip = find_node(root, "com.jihuanshe:id/scroll_view")
        if container is None or strip is None:
            raise RuntimeError("首页缺少大系列横向栏。")
        for node in list(container):
            value = node.attrib.get("text", "").strip()
            if same_label(value, series):
                tap_node_center(adb_path, serial, node)
                time.sleep(wait_seconds)
                return
        signature = node_signature(container)
        unchanged = unchanged + 1 if signature and signature == previous else 0
        if unchanged >= 2:
            break
        previous = signature
        swipe_node_horizontal(adb_path, serial, strip, "left")
        time.sleep(wait_seconds)
    raise RuntimeError(f"首页大系列栏找不到：{series}")


def parse_home_products_for_parent(root: ET.Element) -> list[dict[str, object]]:
    strip = find_node(root, "com.jihuanshe:id/packRv")
    if strip is None:
        return []
    result: list[dict[str, object]] = []
    for item in list(strip):
        if item.attrib.get("clickable") != "true":
            continue
        texts = all_texts(item)
        if not texts:
            continue
        left, top, right, bottom = parse_bounds(item.attrib.get("bounds", ""))
        if right - left < 24 or bottom <= top:
            continue
        code = child_text(item, "com.jihuanshe:id/numberTv").strip()
        if not code:
            code = child_text(item, "com.jihuanshe:id/packNumTv").strip()
        if not code and len(texts) >= 2:
            code = texts[-1]
        names = [value for value in texts if value != code]
        name = names[0].strip() if names else (texts[0].strip() if texts else "")
        if not name:
            continue
        image_node = find_node(item, "com.jihuanshe:id/packIv")
        points: list[tuple[int, int]] = []
        if image_node is not None:
            il, it, ir, ib = parse_bounds(image_node.attrib.get("bounds", ""))
            if ir > il and ib > it:
                points.append(((il + ir) // 2, (it + ib) // 2))
        points.append(((left + right) // 2, (top + bottom) // 2))
        result.append(
            {
                "编号": code,
                "名称": name,
                "node": item,
                "点击坐标": list(dict.fromkeys(points)),
            }
        )
    return result


def homepage_name_match_strength(home_name_raw: str, target_name_raw: str) -> int:
    home_name = normalize_label((home_name_raw or "").replace("…", ""))
    target_name = normalize_label((target_name_raw or "").replace("…", ""))
    if not home_name or not target_name:
        return 0
    if home_name == target_name:
        return 3
    raw = (home_name_raw or "").strip()
    truncated = "..." in raw or "…" in raw
    if len(home_name) >= 4 and target_name.startswith(home_name):
        if truncated or len(home_name) < len(target_name):
            return 2
    return 0


def selected_home_parent_matches(
    root: ET.Element,
    target_code: str,
    target_name: str,
) -> bool:
    strip = find_node(root, "com.jihuanshe:id/packRv")
    if strip is None:
        return False
    for item in list(strip):
        if item.attrib.get("selected") != "true":
            continue
        texts = all_texts(item)
        code = child_text(item, "com.jihuanshe:id/numberTv").strip()
        if not code:
            code = child_text(item, "com.jihuanshe:id/packNumTv").strip()
        if not code and len(texts) >= 2:
            code = texts[-1]
        names = [value for value in texts if value != code]
        name = names[0].strip() if names else (texts[0].strip() if texts else "")
        if homepage_name_match_strength(name, target_name):
            return True
        if code and target_code and normalize_label(code) == normalize_label(target_code):
            # 同编号可能对应多个不同父产品；名称不兼容时拒绝 code-only。
            continue
    return False


def activate_home_parent_for_grid(
    adb_path: str,
    serial: str,
    local_xml: Path,
    series: str,
    code: str,
    name: str,
    wait_seconds: float,
    max_swipes: int,
) -> ET.Element:
    select_home_series_for_parent(
        adb_path, serial, local_xml, series, wait_seconds
    )
    reset_home_strip_to_start(
        adb_path,
        serial,
        local_xml,
        "com.jihuanshe:id/packRv",
        wait_seconds,
        max_swipes=max(36, max_swipes),
    )

    previous: tuple[tuple[str, str], ...] | None = None
    unchanged = 0
    for _ in range(max_swipes + 1):
        root = ensure_library_home_page(adb_path, serial, local_xml, wait_seconds)
        products = parse_home_products_for_parent(root)
        signature = tuple(
            (str(item.get("编号", "")), str(item.get("名称", "")))
            for item in products
        )
        candidates = [
            item
            for item in products
            if normalize_label(str(item.get("编号", ""))) == normalize_label(code)
            and homepage_name_match_strength(str(item.get("名称", "")), name)
        ]
        if candidates:
            target = max(
                candidates,
                key=lambda item: homepage_name_match_strength(
                    str(item.get("名称", "")), name
                ),
            )
            points = target.get("点击坐标")
            if not isinstance(points, list) or not points:
                node = target.get("node")
                if isinstance(node, ET.Element):
                    tap_node_center(adb_path, serial, node)
                else:
                    raise RuntimeError("首页父产品没有可点击坐标。")
            else:
                x, y = points[0]
                tap_xy(adb_path, serial, int(x), int(y))
            deadline = time.monotonic() + max(4.0, wait_seconds * 10)
            while time.monotonic() < deadline:
                time.sleep(max(0.20, wait_seconds))
                dump_current_page(adb_path, serial, local_xml)
                selected_root = ET.parse(local_xml).getroot()
                if not is_library_home(selected_root):
                    continue
                child_strip = find_node(selected_root, "com.jihuanshe:id/childPackRv")
                if (
                    child_strip is not None
                    and selected_home_parent_matches(selected_root, code, name)
                ):
                    return selected_root
            raise RuntimeError(
                f"首页已点击父产品，但没有稳定出现其 childPackRv：{code} {name}"
            )

        unchanged = unchanged + 1 if signature and signature == previous else 0
        if unchanged >= 2:
            break
        previous = signature
        strip = find_node(root, "com.jihuanshe:id/packRv")
        if strip is None or strip.attrib.get("scrollable") != "true":
            break
        swipe_node_horizontal(adb_path, serial, strip, "left")
        time.sleep(wait_seconds)
    raise RuntimeError(f"首页产品栏找不到父产品：{code} {name}")


def find_parent_grid_recycler(root: ET.Element) -> ET.Element | None:
    """找首页父产品下方商品区所属的纵向 RecyclerView。"""
    list_view = find_node(root, "com.jihuanshe:id/listView")
    if list_view is None:
        return None
    fallback: ET.Element | None = None
    best: tuple[int, ET.Element] | None = None
    for node in list_view.iter("node"):
        if node.attrib.get("class") != "androidx.recyclerview.widget.RecyclerView":
            continue
        if node.attrib.get("resource-id", "") in {
            "com.jihuanshe:id/packRv",
            "com.jihuanshe:id/childPackRv",
        }:
            continue
        left, top, right, bottom = parse_bounds(node.attrib.get("bounds", ""))
        if right <= left or bottom <= top:
            continue
        if fallback is None:
            fallback = node
        area = (right - left) * (bottom - top)
        score = area + (10_000_000 if node.attrib.get("scrollable") == "true" else 0)
        if best is None or score > best[0]:
            best = (score, node)
    return best[1] if best is not None else fallback


def parse_parent_grid_item(item: ET.Element) -> dict[str, object] | None:
    """把首页聚合商品格转换成与卡牌列表相同的输出字段。"""
    # 外层首页 header 本身也是 RecyclerView 的直接子项，里面嵌套 packRv /
    # childPackRv / 分类按钮。它不是商品格，必须先排除，否则会把首页标题、
    # 推荐商品等文字误拼成一张假卡。
    if (
        find_node(item, "com.jihuanshe:id/packRv") is not None
        or find_node(item, "com.jihuanshe:id/childPackRv") is not None
        or find_node(item, "com.jihuanshe:id/categoryBtn") is not None
    ):
        return None

    # 若聚合区复用了卡牌列表行布局，直接使用已经实机验证过的解析器。
    parsed = parse_card_item(item)
    if parsed is not None:
        return parsed

    has_tile_marker = (
        find_node(item, "com.jihuanshe:id/imgIv") is not None
        or find_node(item, "com.jihuanshe:id/ll_vision") is not None
        or (
            find_node(item, "com.jihuanshe:id/coverIv") is not None
            and (
                find_node(item, "com.jihuanshe:id/versionTv") is not None
                or find_node(item, "com.jihuanshe:id/rarityTv") is not None
            )
        )
    )
    if not has_tile_marker:
        return None

    texts = all_texts(item)
    if not texts:
        return None

    chinese_name = child_text(item, "com.jihuanshe:id/officialNameTv").strip()
    english_name = child_text(item, "com.jihuanshe:id/nwNameTv").strip()
    fallback_name = first_text_from_ids(item, HOME_ITEM_NAME_IDS).strip()
    if fallback_name and not (chinese_name or english_name):
        if has_english_name(fallback_name):
            english_name = fallback_name
        else:
            chinese_name = fallback_name

    number_display = first_text_from_ids(item, HOME_ITEM_CODE_IDS).strip()
    rarity = first_text_from_ids(item, HOME_ITEM_TYPE_IDS).strip()
    language = child_text(item, "com.jihuanshe:id/lanTv").strip()
    if not language:
        language = child_text(item, "com.jihuanshe:id/tvLan").strip()

    vision = find_node(item, "com.jihuanshe:id/ll_vision")
    vision_texts = all_texts(vision) if vision is not None else []
    if not number_display and vision_texts:
        number_display = vision_texts[0].strip()
    if not rarity and len(vision_texts) >= 2:
        rarity = vision_texts[1].strip()

    if not language:
        language = next((value for value in texts if is_language_text(value)), "")

    if not rarity:
        known_noncard = {normalize_label(value) for value in NON_CARD_RARITIES}
        rarity = next(
            (
                value
                for value in texts
                if looks_like_rarity(value)
                or normalize_label(value) in known_noncard
                or normalize_label(value) == "promo"
            ),
            "",
        ).strip()

    if not number_display:
        excluded = {
            normalize_label(chinese_name),
            normalize_label(english_name),
            normalize_label(rarity),
            normalize_label(language),
        }
        number_display = next(
            (
                value
                for value in texts
                if normalize_label(value) not in excluded
                and (
                    "无编号" in value
                    or "無編號" in value
                    or re.search(r"#\s*[0-9A-Za-z]+", value)
                    or re.search(r"[A-Za-z][A-Za-z0-9+./_-]*[-/][A-Za-z0-9+./_-]+", value)
                )
            ),
            "",
        ).strip()

    if not (chinese_name or english_name):
        excluded = {
            normalize_label(number_display),
            normalize_label(rarity),
            normalize_label(language),
        }
        candidate = next(
            (
                value for value in texts
                if normalize_label(value) not in excluded
                and not is_language_text(value)
            ),
            "",
        ).strip()
        if has_english_name(candidate):
            english_name = candidate
        else:
            chinese_name = candidate

    # 聚合商品格至少需要“名称 + 编号/显示号 + 类型/稀有度”才能安全入库。
    if not (chinese_name or english_name) or not number_display or not rarity:
        return None

    is_noncard = classify_noncard(rarity, texts)
    missing_image = detect_missing_card_image(item)
    number_status, card_no = split_number_display(number_display)
    identity = (
        normalize_label(chinese_name),
        normalize_label(english_name),
        normalize_label(number_display),
        normalize_label(rarity),
        normalize_label(language),
    )
    return {
        "中文名": chinese_name,
        "英文名": english_name,
        "编号显示": number_display,
        "编号状态": number_status,
        "卡号": card_no,
        "稀有度": rarity,
        "语言": language,
        "卡图状态": "卡图暂缺" if missing_image and not is_noncard else "",
        "全部可见文字": " | ".join(texts),
        "非卡": is_noncard,
        "_identity": identity,
    }


def parent_grid_page_rows(
    root: ET.Element,
) -> tuple[list[dict[str, object]], ET.Element | None]:
    recycler = find_parent_grid_recycler(root)
    if recycler is None:
        return [], None
    r_left, r_top, r_right, r_bottom = parse_bounds(recycler.attrib.get("bounds", ""))
    candidates: list[tuple[int, int, int, dict[str, object]]] = []
    heights: list[int] = []
    for item in list(recycler):
        left, top, right, bottom = parse_bounds(item.attrib.get("bounds", ""))
        if right <= left or bottom <= top:
            continue
        if r_right > r_left and r_bottom > r_top:
            if not (right > r_left and left < r_right and bottom > r_top and top < r_bottom):
                continue
        parsed = parse_parent_grid_item(item)
        if parsed is None:
            continue
        height = bottom - top
        candidates.append((top, left, height, parsed))
        heights.append(height)

    if not candidates:
        return [], recycler

    sorted_heights = sorted(heights, reverse=True)
    top_band = sorted_heights[: max(1, min(8, len(sorted_heights)))]
    typical_height = int(round(sum(top_band) / len(top_band)))
    min_complete_height = max(70, int(typical_height * 0.60))

    rows: list[dict[str, object]] = []
    for top, left, height, parsed in sorted(candidates, key=lambda value: (value[0], value[1])):
        if height < min_complete_height:
            continue
        parsed["_row_top"] = top
        parsed["_row_height"] = height
        parsed["_top_boundary_partial"] = bool(
            top <= r_top + 2
            and height < max(min_complete_height, int(typical_height * 0.88))
        )
        rows.append(parsed)
    return rows, recycler


def reveal_parent_grid(
    adb_path: str,
    serial: str,
    local_xml: Path,
    wait_seconds: float,
    max_swipes: int,
) -> None:
    limit = min(max_swipes, 30)
    for step in range(limit + 1):
        dump_current_page(adb_path, serial, local_xml)
        root = ET.parse(local_xml).getroot()
        rows, recycler = parent_grid_page_rows(root)
        if rows:
            if step:
                print(
                    f"      分类父入口为空；已从首页父产品越过子类栏，找到聚合商品区（滚动 {step} 次）。",
                    flush=True,
                )
            return
        if recycler is None:
            time.sleep(wait_seconds)
            continue
        if step >= limit:
            break
        swipe_node_vertical(adb_path, serial, recycler, "up")
        time.sleep(wait_seconds)
    raise RuntimeError("首页父产品下方一直没有出现可解析的聚合商品区。")


def stable_parent_grid_page(
    adb_path: str,
    serial: str,
    local_xml: Path,
    wait_seconds: float,
    max_samples: int = 10,
) -> tuple[ET.Element, list[dict[str, object]], ET.Element]:
    previous: tuple[tuple[object, ...], ...] | None = None
    stable = 0
    last: tuple[ET.Element, list[dict[str, object]], ET.Element] | None = None
    for _ in range(max_samples):
        dump_current_page(adb_path, serial, local_xml)
        root = ET.parse(local_xml).getroot()
        rows, recycler = parent_grid_page_rows(root)
        if recycler is None or not rows:
            previous = None
            stable = 0
            time.sleep(wait_seconds)
            continue
        signature = tuple(row["_identity"] for row in rows)
        last = (root, rows, recycler)
        if signature == previous:
            stable += 1
            if stable >= 1:
                return last
        else:
            stable = 0
        previous = signature
        time.sleep(wait_seconds)
    if last is not None:
        return last
    raise RuntimeError("首页父产品聚合商品区当前屏一直没有稳定条目。")


def collect_parent_aggregate_pool(
    adb_path: str,
    serial: str,
    local_xml: Path,
    series: str,
    code: str,
    name: str,
    wait_seconds: float,
    max_home_swipes: int,
    max_grid_swipes: int,
    debug_first_page: Path,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """分类父入口为空时，改抓首页父产品下方的聚合商品区。"""
    global _LAST_POOL_END
    _LAST_POOL_END = "未确认"
    ensure_library_home_page(adb_path, serial, local_xml, wait_seconds)
    activate_home_parent_for_grid(
        adb_path,
        serial,
        local_xml,
        series,
        code,
        name,
        wait_seconds,
        max_home_swipes,
    )
    reveal_parent_grid(
        adb_path,
        serial,
        local_xml,
        wait_seconds,
        max_grid_swipes,
    )

    dump_current_page(adb_path, serial, local_xml)
    save_xml_copy(local_xml, debug_first_page)

    collected: list[dict[str, object]] = []
    previous_signature: tuple[tuple[object, ...], ...] | None = None
    no_progress = 0

    for page in range(max_grid_swipes + 1):
        root, page_rows, recycler = stable_parent_grid_page(
            adb_path, serial, local_xml, wait_seconds
        )
        signature = tuple(row["_identity"] for row in page_rows)
        if not collected:
            boundary_skip = 0
            overlap = 0
            new_rows = list(page_rows)
        else:
            boundary_skip, overlap = _find_page_overlap(collected, page_rows)
            if overlap == 0 and signature != previous_signature:
                # 聚合区同样采用小步滚动，正常应有相邻屏重叠。没有重叠时
                # 宁可停止让用户检查，也不把整屏静默重复追加。
                raise RuntimeError(
                    "首页父产品聚合商品区滚动前后没有可确认的重叠条目；"
                    "无法保证逐卡结果不重不漏。"
                )
            if overlap > 0:
                old_overlap = collected[-overlap:]
                cur_overlap = page_rows[boundary_skip:boundary_skip + overlap]
                for old_row, cur_row in zip(old_overlap, cur_overlap):
                    if (
                        not old_row.get("卡图状态")
                        and cur_row.get("卡图状态") == "卡图暂缺"
                    ):
                        old_row["卡图状态"] = "卡图暂缺"
            new_rows = list(page_rows[boundary_skip + overlap:])

        collected.extend(new_rows)
        cards_so_far = sum(not bool(row.get("非卡")) for row in collected)
        noncards_so_far = sum(bool(row.get("非卡")) for row in collected)
        missing_images = sum(
            row.get("卡图状态") == "卡图暂缺" and not bool(row.get("非卡"))
            for row in collected
        )
        extra = _identity_duplicate_extra(collected)
        notes: list[str] = []
        if boundary_skip:
            notes.append(f"顶部残项跳过 {boundary_skip}")
        if missing_images:
            notes.append(f"卡图暂缺 {missing_images}")
        if extra:
            notes.append(f"同文字独立项 {extra}")
        suffix = "，" + "，".join(notes) if notes else ""
        print(
            f"      父页面商品区第 {page + 1} 屏：新增 {len(new_rows)}，"
            f"屏间重叠 {overlap}，累计项目 {len(collected)}"
            f"（卡 {cards_so_far} / 非卡 {noncards_so_far}）{suffix}",
            flush=True,
        )

        if "- END -" in all_texts(root):
            _LAST_POOL_END = "END（父商品区）"
            break
        if signature == previous_signature and not new_rows:
            no_progress += 1
        else:
            no_progress = 0
        if no_progress >= 8:
            raise RuntimeError(
                "首页父产品聚合商品区连续 8 次无进展且未出现 - END -；"
                "本次不保存为完成，避免截断。"
            )
        previous_signature = signature
        if page >= max_grid_swipes:
            raise RuntimeError("首页父产品聚合商品区达到最大滚动次数，可能尚未到底。")
        swipe_node_vertical(adb_path, serial, recycler, "up")
        time.sleep(wait_seconds)

    cards = [row for row in collected if not bool(row.get("非卡"))]
    noncards = [row for row in collected if bool(row.get("非卡"))]
    if not cards and not noncards:
        raise RuntimeError("首页父产品聚合商品区没有抓到任何条目。")
    return cards, noncards


def current_card_list_is_explicitly_empty(local_xml: Path) -> bool:
    """只把“明确到底仍无行”的页面视为真实空列表。

    单个 scrollable=false / 0 children 的 RecyclerView 可能只是网络数据尚未填充，
    不能据此判断为空。
    """
    try:
        root = ET.parse(local_xml).getroot()
    except Exception:
        return False
    texts = all_texts(root)
    if "卡牌列表" not in texts:
        return False
    rows, recycler = page_card_rows(root)
    if rows:
        return False
    # 只有 App 自己明确给出 END/noMore，才允许走父页面 fallback。
    if "- END -" in texts:
        return True
    no_more = find_node(root, "com.jihuanshe:id/noMoreTv")
    return bool(no_more is not None and (no_more.attrib.get("text") or "").strip())

def collect_one_card_pool(
    adb_path: str,
    serial: str,
    local_xml: Path,
    wait_seconds: float,
    max_swipes: int,
    expected_total: int | None,
    debug_first_page: Path,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    global _LAST_POOL_END
    _LAST_POOL_END = "未确认"
    expected_total = None  # 未经确认的 Phase2 数字不参与采集边界或重叠合并。
    wait_for_card_list(
        adb_path,
        serial,
        local_xml,
        timeout=max(3.0, wait_seconds * 7),
    )

    # 永远保存第一个产品页第一屏 XML，方便后续校验字段映射。
    dump_current_page(adb_path, serial, local_xml)
    save_xml_copy(local_xml, debug_first_page)

    # v6 及以前使用 OrderedDict[_identity] 全局去重。
    # 这对补充包通常没问题，但会把“文字字段完全相同、图片不同”的旧构筑卡
    # 合并成一条。v7 改为“有序序列 + 相邻屏最长重叠合并”。
    collected_rows: list[dict[str, object]] = []
    collected_ids: list[tuple[object, ...]] = []
    previous_page_signature: tuple[tuple[object, ...], ...] | None = None
    no_progress = 0
    previous_viewport = None
    viewport_stable = 0
    recovery_swipes = 0

    for page in range(max_swipes + 1):
        page_root, page_rows, recycler = stable_card_page(
            adb_path,
            serial,
            local_xml,
            wait_seconds,
        )
        page_ids = [row["_identity"] for row in page_rows]
        signature = tuple(page_ids)

        inferred_overlap = 0
        if not collected_rows:
            boundary_skip = 0
            overlap = 0
            new_rows = list(page_rows)
        else:
            boundary_skip, overlap = _find_page_overlap(collected_rows, page_rows)

            new_rows = list(page_rows[boundary_skip + overlap:])

        if new_rows:
            collected_rows.extend(new_rows)
            collected_ids.extend(row["_identity"] for row in new_rows)
        added = len(new_rows)

        card_so_far = sum(not bool(row["非卡"]) for row in collected_rows)
        noncard_so_far = sum(bool(row["非卡"]) for row in collected_rows)
        duplicate_extra = _identity_duplicate_extra(collected_rows)
        duplicate_note = (
            f"，其中同文字独立项 {duplicate_extra}"
            if duplicate_extra
            else ""
        )
        boundary_note = (
            f"，顶部残行跳过 {boundary_skip}"
            if boundary_skip
            else ""
        )
        inferred_note = (
            f"，数量约束补足重叠 {inferred_overlap}"
            if inferred_overlap
            else ""
        )
        print(
            f"      卡池第 {page + 1} 屏：新增 {added}，屏间重叠 {overlap}"
            f"{boundary_note}{inferred_note}，累计项目 {len(collected_rows)}"
            f"（卡 {card_so_far} / 非卡 {noncard_so_far}）"
            f"{duplicate_note}",
            flush=True,
        )

        # App 到底部时会显示 - END -。优先使用明确的底部标记，
        # 不再仅凭“连续两屏没有新增”过早停止。
        end_reached = "- END -" in all_texts(page_root)
        if end_reached:
            _LAST_POOL_END = "END"
            break

        if signature == previous_page_signature and added == 0:
            no_progress += 1
        else:
            no_progress = 0
        viewport = node_signature(recycler)
        viewport_stable = viewport_stable + 1 if viewport and viewport == previous_viewport else 0
        previous_viewport = viewport
        loading = any(re.search(r"加载中|正在加载|网络异常|点击重试|loading", value, re.I)
                      for value in all_texts(page_root))
        if no_progress >= 8 and viewport_stable >= 5 and recovery_swipes >= 2 and not loading and collected_rows:
            _LAST_POOL_END = "稳定无END（待复核）"
            print("      未见 END；多次加强滚动后列表内容与坐标均稳定。保存实际明细，但标记待复核，不当作明确完成。", flush=True)
            break
        if no_progress >= 12:
            raise RuntimeError("列表持续无进展，尚无 END 或稳定底部依据；不标记完成。")
        previous_page_signature = signature

        if page >= max_swipes:
            raise RuntimeError("卡池达到最大滚动次数，可能尚未到达底部。")

        recovery = no_progress > 0 and no_progress % 3 == 0
        if recovery:
            recovery_swipes += 1
        swipe_card_recycler_up(adb_path, serial, recycler, recovery=recovery)
        time.sleep(max(wait_seconds, 0.8) if recovery else wait_seconds)

    cards = [row for row in collected_rows if not bool(row["非卡"])]
    noncards = [row for row in collected_rows if bool(row["非卡"])]
    return cards, noncards


# ---------------------------------------------------------------------------
# 输出 / 断点
# ---------------------------------------------------------------------------

def default_output_paths() -> tuple[Path, Path, Path]:
    return (
        Path("jhs_lorcana_en_phase3_all_cards.csv"),
        Path("jhs_lorcana_en_phase3_all_noncard.csv"),
        Path("jhs_lorcana_en_phase3_all_product_check.csv"),
    )


def load_check_map(path: Path) -> OrderedDict[str, dict[str, str]]:
    result: OrderedDict[str, dict[str, str]] = OrderedDict()
    for row in read_csv(path):
        key = product_key(
            row.get("系列", ""),
            row.get("详情编号", ""),
            row.get("详情名称", ""),
        )
        result[key] = row
    return result


def replace_product_rows(
    existing: list[dict[str, str]],
    series: str,
    code: str,
    name: str,
    new_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in existing:
        if product_key(
            row.get("系列", ""),
            row.get("详情编号", ""),
            row.get("详情名称", ""),
        ) == product_key(series, code, name):
            continue
        result.append(dict(row))
    result.extend(new_rows)
    return result


def save_product_result(
    cards_path: Path,
    noncards_path: Path,
    checks_path: Path,
    series: str,
    code: str,
    name: str,
    expected_cards: int | None,
    expected_total: int | None,
    cards: list[dict[str, object]],
    noncards: list[dict[str, object]],
    status: str,
    note: str,
) -> tuple[str, str]:
    card_rows: list[dict[str, object]] = []
    for index, row in enumerate(cards, start=1):
        card_rows.append(
            {
                "系列": series,
                "详情编号": code,
                "详情名称": name,
                "页面张数": "" if expected_cards is None else expected_cards,
                "抓取序号": index,
                **{k: row.get(k, "") for k in (
                    "中文名", "英文名", "编号显示", "编号状态",
                    "卡号", "稀有度", "语言", "卡图状态", "全部可见文字",
                )},
            }
        )

    noncard_rows: list[dict[str, object]] = []
    for index, row in enumerate(noncards, start=1):
        noncard_rows.append(
            {
                "系列": series,
                "详情编号": code,
                "详情名称": name,
                "页面张数": "" if expected_cards is None else expected_cards,
                "项目序号": index,
                "中文名": row.get("中文名", ""),
                "英文名": row.get("英文名", ""),
                "编号显示": row.get("编号显示", ""),
                "项目类型": row.get("稀有度", ""),
                "语言": row.get("语言", ""),
                "全部可见文字": row.get("全部可见文字", ""),
            }
        )

    existing_cards = read_csv(cards_path)
    existing_noncards = read_csv(noncards_path)
    merged_cards = replace_product_rows(
        existing_cards, series, code, name, card_rows
    )
    merged_noncards = replace_product_rows(
        existing_noncards, series, code, name, noncard_rows
    )
    write_csv(cards_path, CARD_COLUMNS, merged_cards)
    write_csv(noncards_path, NONCARD_COLUMNS, merged_noncards)

    card_check, total_check = evaluate_counts(cards, noncards, expected_cards, expected_total, series, name)

    checks = load_check_map(checks_path)
    key = product_key(series, code, name)
    checks[key] = {
        "系列": series,
        "详情编号": code,
        "详情名称": name,
        "页面张数": "" if expected_cards is None else expected_cards,
        "收录商品数量": "" if expected_total is None else expected_total,
        "抓取卡牌数": len(cards),
        "抓取非卡数": len(noncards),
        "抓取总项目数": len(cards) + len(noncards),
        "卡牌核对": card_check,
        "总项目核对": total_check,
        "完成依据": _LAST_POOL_END,
        "抓取版本": SCRIPT_VERSION,
        "统计状态": status,
        "统计时间": now_text(),
        "备注": note,
    }
    write_csv(checks_path, CHECK_COLUMNS, checks.values())
    return card_check, total_check


def evaluate_counts(cards, noncards, expected_cards, expected_total, series="", name=""):
    # 只有产品身份明确属于周边，实际无卡且抓到真实非卡商品时，才豁免卡牌张数。
    # 普通卡池若被错误识别成全非卡，仍保留数量不一致，避免掩盖漏卡。
    merchandise = any(token in str(series) + " " + str(name)
                      for token in ("周边商品", "周邊商品", "官方周边", "官方周邊", "Official Supply"))
    pure_merchandise = merchandise and not cards and bool(noncards)
    card_check = ("未提供（不核对）" if expected_cards is None else
                  "一致" if len(cards) == expected_cards else "不一致")
    if pure_merchandise:
        card_check = "不适用（纯周边产品）"
    total_check = ("未提供（不核对）" if expected_total is None else
                   "一致" if len(cards) + len(noncards) == expected_total else "不一致")
    return card_check, total_check


def finished_and_matched(row: dict[str, str] | None) -> bool:
    """跨版本复用：失败、数量异常、无完整性依据的产品继续重抓。"""
    if not row or not row.get("统计状态", "").startswith("完成"):
        return False
    card_check = row.get("卡牌核对", "").strip()
    total_check = row.get("总项目核对", "").strip()
    accepted = {"一致", "未提供", "未提供（不核对）", "不适用（纯周边产品）"}
    if card_check not in accepted or total_check not in accepted:
        return False
    actual_cards = parse_int(row.get("抓取卡牌数", ""))
    actual_noncards = parse_int(row.get("抓取非卡数", ""))
    actual_total = parse_int(row.get("抓取总项目数", ""))
    if any(value is None for value in (actual_cards, actual_noncards, actual_total)):
        return False
    if actual_cards + actual_noncards != actual_total:
        return False
    if card_check == "一致" and parse_int(row.get("页面张数", "")) != actual_cards:
        return False
    if total_check == "一致" and parse_int(row.get("收录商品数量", "")) != actual_total:
        return False
    if card_check == "不适用（纯周边产品）" and (actual_cards != 0 or actual_noncards <= 0):
        return False
    evidence = row.get("完成依据", "").strip()
    if evidence:
        # 明确记录无 END 的结果不依赖数字巧合复用。
        return evidence.startswith("END")
    # 旧版没有 END 字段：卡牌数与总项目数都核对一致，可按用户要求沿用。
    return total_check == "一致" and card_check in {"一致", "不适用（纯周边产品）"}


def reusable_product_keys(checks, cards_path: Path, noncards_path: Path) -> set[str]:
    """核对断点和实际明细，避免 CSV 丢失/缺行却只凭核对表跳过。"""
    card_counts = {}
    noncard_counts = {}
    for path, counts in ((cards_path, card_counts), (noncards_path, noncard_counts)):
        for row in read_csv(path):
            key = product_key(row.get("系列", ""), row.get("详情编号", ""), row.get("详情名称", ""))
            counts[key] = counts.get(key, 0) + 1
    return {key for key, row in checks.items()
            if finished_and_matched(row)
            and card_counts.get(key, 0) == parse_int(row.get("抓取卡牌数", ""))
            and noncard_counts.get(key, 0) == parse_int(row.get("抓取非卡数", ""))}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    cards_default, noncards_default, checks_default = default_output_paths()
    parser = argparse.ArgumentParser(
        description="抓取洛卡纳英文所有系列的单详情及多子类父产品卡池，按页面提供的数量核对。"
    )
    parser.add_argument(
        "--details",
        default="jhs_lorcana_en_phase2_product_details.csv",
        help="第二步产品详情 CSV。",
    )
    parser.add_argument(
        "--series",
        action="append",
        default=None,
        help=(
            "指定系列；可重复使用，也可用逗号分隔。"
            "不填写时默认处理 Phase2 表中的所有系列。"
        ),
    )
    parser.add_argument(
        "--only",
        default="",
        help="只处理一个目标；可填父产品列表编号或名称，例如 PR，或 Phase2 中实际的父产品名称。",
    )
    parser.set_defaults(continue_on_mismatch=True)
    parser.add_argument("--output", default=str(cards_default))
    parser.add_argument("--noncard-output", default=str(noncards_default))
    parser.add_argument("--check-output", default=str(checks_default))
    parser.add_argument("--adb", default=DEFAULT_ADB)
    parser.add_argument("--serial", default=DEFAULT_SERIAL)
    parser.add_argument("--wait", type=float, default=0.38)
    parser.add_argument("--max-pack-swipes", type=int, default=80)
    parser.add_argument("--max-card-swipes", type=int, default=1000)
    parser.add_argument(
        "--continue-on-mismatch",
        action="store_true",
        help="数量差异保留待复核并继续下一产品（本版默认启用）；实际抓取失败仍写失败状态。",
    )
    parser.add_argument(
        "--redo",
        action="store_true",
        help="重新抓取已经核对一致的产品。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="本次最多处理几个产品；0 表示不限。",
    )
    parser.add_argument("--report-review", action="store_true", help=argparse.SUPPRESS)
    return parser


def main() -> int:
    args = build_parser().parse_args()

    script_dir = Path(__file__).resolve().parent
    def resolve_path(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else script_dir / path

    details_path = resolve_path(args.details)
    cards_path = resolve_path(args.output)
    noncards_path = resolve_path(args.noncard_output)
    checks_path = resolve_path(args.check_output)

    if not details_path.exists():
        raise SystemExit(f"找不到第二步表格：{details_path.resolve()}")

    series_list = parse_series_args(args.series)
    if not series_list:
        series_list = list(dict.fromkeys(
            row.get("系列", "").strip() for row in read_csv(details_path)
            if row.get("系列", "").strip()
        ))
    targets = load_targets(details_path, series_list)
    excluded = []
    for source_row in read_csv(details_path):
        if series_list and not any(same_label(source_row.get("系列", ""), series) for series in series_list):
            continue
        reason = target_skip_reason(source_row)
        if reason:
            excluded.append({**source_row, "未处理原因": reason})
    excluded_path = checks_path.with_name(checks_path.stem + "_unprocessed.csv")
    write_csv(excluded_path, ["系列", "列表编号", "列表名称", "详情编号", "详情名称", "统计状态", "未处理原因"], excluded)
    print(f"Phase2 未就绪记录：{len(excluded)} 条；清单：{excluded_path.resolve()}", flush=True)
    skipped_multichild: list[dict[str, str]] = []
    skipped_zero_card: list[dict[str, str]] = []
    if args.only:
        needle = normalize_label(args.only)
        targets = [
            row for row in targets
            if needle == normalize_label(row.get("详情编号", ""))
            or needle in normalize_label(row.get("详情名称", ""))
            or needle == normalize_label(row.get("列表编号", ""))
            or needle in normalize_label(row.get("列表名称", ""))
        ]

    if not targets:
        if args.report_review and excluded and not args.only:
            print("没有就绪的产品，本次保留已有结果并报告待复核清单。", flush=True)
            return REVIEW_EXIT_CODE
        raise SystemExit(
            f"没有找到可抓取目标。系列={series_list!r}，only={args.only!r}"
        )

    print(f"脚本版本：{SCRIPT_VERSION}", flush=True)
    print(f"首页入口语言：{TARGET_LANGUAGE}（包含该入口下简中等全部分类）", flush=True)
    print(f"目标系列：{' / '.join(series_list)}", flush=True)
    print(f"第二步表格：{details_path.resolve()}", flush=True)
    print("目标清单：本次 Phase2 表内全部分类，包含简体中文；按卡牌实际语言原样保存。未完成记录另列清单。", flush=True)
    print(f"卡牌输出：{cards_path.resolve()}", flush=True)
    print(f"非卡输出：{noncards_path.resolve()}", flush=True)
    print(f"核对输出：{checks_path.resolve()}", flush=True)
    no_count_targets = sum(
        parse_int(row.get("页面张数", "")) is None
        for row in targets
    )
    print(
        f"本次目标：{len(targets)} 个；有页面张数 {len(targets)-no_count_targets} 个，无页面张数 {no_count_targets} 个。提供数量的字段逐项核对，未提供的字段不推算。",
        flush=True,
    )

    if skipped_zero_card and not args.only:
        print(
            f"另有 {len(skipped_zero_card)} 个页面张数为 0 的纯周边/无卡条目，本脚本跳过。",
            flush=True,
        )
        for row in skipped_zero_card:
            print(
                f"      零卡跳过：{row.get('系列','')} | "
                f"{row.get('详情编号','')} {row.get('详情名称','')}",
                flush=True,
            )

    # Correct old checkpoints before deciding which products can be reused.
    from jhs_tables import repair_csv_files
    repaired = repair_csv_files(cards_path, noncards_path, checks_path)
    if repaired["moved"]:
        print(f"已纠正旧结果中的非卡商品：{repaired['moved']} 条。", flush=True)
    checks = load_check_map(checks_path)
    reusable_keys = reusable_product_keys(checks, cards_path, noncards_path)
    reusable_count = sum(product_key(
        row.get("系列", ""), row.get("详情编号", "").strip() or row.get("列表编号", "").strip(),
        row.get("详情名称", "").strip() or row.get("列表名称", "").strip()) in reusable_keys for row in targets)
    print(f"断点检查：目标 {len(targets)} 个，可沿用 {reusable_count} 个，需处理 {len(targets)-reusable_count} 个。", flush=True)
    if args.redo:
        print("已指定 --redo：本次强制重抓全部所选产品。", flush=True)
    elif reusable_count == len(targets):
        print("全部所选产品已有可复用结果，无需操作模拟器。", flush=True)
        if excluded:
            return REVIEW_EXIT_CODE if args.report_review else 1
        return 0
    attempted = 0
    matched = 0
    product_failed = False

    with tempfile.TemporaryDirectory(prefix="jhs_phase3_") as temp_dir:
        local_xml = Path(temp_dir) / "page.xml"

        print(
            f"正在读取模拟器 {args.serial} 当前页面并进入“分类”页……",
            flush=True,
        )
        ensure_library_home_page(args.adb, args.serial, local_xml, args.wait)
        ensure_category_page(
            args.adb,
            args.serial,
            local_xml,
            args.wait,
        )
        print("已进入“分类”页，开始处理目标。", flush=True)

        for row in targets:
            series = row.get("系列", "").strip()
            # 单详情保留详情身份；多子类父产品使用 Phase1 列表身份。
            code = (row.get("详情编号", "").strip()
                    or row.get("列表编号", "").strip())
            name = (row.get("详情名称", "").strip()
                    or row.get("列表名称", "").strip())
            category_code = row.get("列表编号", "").strip() or code
            category_name = row.get("列表名称", "").strip() or name
            expected_cards = parse_int(row.get("页面张数", ""))
            expected_total = parse_int(row.get("收录商品数量", ""))
            no_count_mode = expected_cards is None
            # Phase2 数量口径尚未核实，不作为上限或跨屏重叠推算依据。
            guard_total = None

            key = product_key(series, code, name)
            if not args.redo and key in reusable_keys:
                print(f"沿用已有结果，不重抓：{series} | {code} {name}", flush=True)
                continue

            if args.limit and attempted >= args.limit:
                break

            # 先确认输出文件未被 Excel/WPS 锁定，避免整套卡池抓完后才发现无法保存。
            for output_path in (cards_path, noncards_path, checks_path):
                wait_for_csv_writable(output_path)

            attempted += 1

            print("", flush=True)
            if no_count_mode:
                print(
                    f"[{attempted}] {series} | {code} {name} | "
                    f"页面张数 未提供 | 收录商品 {expected_total if expected_total is not None else '未提供'}",
                    flush=True,
                )
            else:
                print(
                    f"[{attempted}] {series} | {code} {name} | "
                    f"页面张数 {expected_cards}"
                    + (
                        f" | 收录商品 {expected_total}"
                        if expected_total is not None
                        else ""
                    ),
                    flush=True,
                )
            if category_code != code or not same_label(category_name, name):
                print(
                    f"      分类页入口：{category_code} {category_name} "
                    f"-> 详情：{code} {name}",
                    flush=True,
                )

            try:
                ensure_category_page(
                    args.adb,
                    args.serial,
                    local_xml,
                    args.wait,
                )
                open_category_product(
                    args.adb,
                    args.serial,
                    local_xml,
                    series,
                    category_code,
                    category_name,
                    args.wait,
                    args.max_pack_swipes,
                )

                debug_first = script_dir / Path(
                    f"jhs_lorcana_en_phase3_all_debug_first_{re.sub(r'[^0-9A-Za-z_-]+', '_', code) or 'item'}.xml"
                )
                collection_note = ""
                try:
                    cards, noncards = collect_one_card_pool(
                        args.adb,
                        args.serial,
                        local_xml,
                        args.wait,
                        args.max_card_swipes,
                        guard_total,
                        debug_first,
                    )
                except RuntimeError as card_list_exc:
                    if no_count_mode and current_card_list_is_explicitly_empty(local_xml):
                        print(
                            "      分类页父入口打开的是空卡牌列表；"
                            "改从首页选中同一父产品，直接抓取其下方聚合商品区……",
                            flush=True,
                        )
                        fallback_debug = script_dir / Path(
                            f"jhs_lorcana_en_phase3_all_debug_parent_grid_{re.sub(r'[^0-9A-Za-z_-]+', '_', code) or 'item'}.xml"
                        )
                        cards, noncards = collect_parent_aggregate_pool(
                            args.adb,
                            args.serial,
                            local_xml,
                            series,
                            category_code,
                            category_name,
                            args.wait,
                            args.max_pack_swipes,
                            args.max_card_swipes,
                            fallback_debug,
                        )
                        collection_note = (
                            "分类页父入口的卡牌列表为空；已改从首页父产品的聚合商品区逐项抓取。"
                        )
                    else:
                        raise card_list_exc

                actual_total = len(cards) + len(noncards)
                card_check, total_check = evaluate_counts(cards, noncards, expected_cards, expected_total, series, name)
                overall_ok = "不一致" not in (card_check, total_check)
                if not _LAST_POOL_END.startswith("END"):
                    status = "待复核（未见END）"
                elif not overall_ok:
                    status = "抓取完成（END，数量待复核）"
                elif card_check == "不适用（纯周边产品）":
                    status = "完成（纯周边产品）"
                else:
                    status = "完成（无预期数量）" if no_count_mode else "完成"
                note = (f"页面张数={expected_cards if expected_cards is not None else '未提供'}，抓取卡牌={len(cards)}；"
                        f"收录商品={expected_total if expected_total is not None else '未提供'}，抓取总项目={actual_total}。")
                if card_check == "不适用（纯周边产品）":
                    note += " 产品明确为周边，且实际全部为非卡商品；页面张数仅保留来源值，不用作卡牌数量预期。商品总数仍按原值核对。"
                note += f" 完成依据={_LAST_POOL_END}；Phase2 数量仅作来源比较，未用于上限或重叠推算。"
                if collection_note:
                    note += " " + collection_note

                card_check, total_check = save_product_result(
                    cards_path,
                    noncards_path,
                    checks_path,
                    series,
                    code,
                    name,
                    expected_cards,
                    expected_total,
                    cards,
                    noncards,
                    status,
                    note,
                )

                print(
                    f"      抓取结果：卡牌 {len(cards)}，非卡 {len(noncards)}，"
                    f"总项目 {len(cards) + len(noncards)}",
                    flush=True,
                )
                print(f"      状态：{status}；依据：{_LAST_POOL_END}", flush=True)
                if no_count_mode:
                    print(
                        f"      核对：页面张数 {card_check}；收录商品 {total_check}；"
                        f"实际卡牌 {len(cards)}，非卡 {len(noncards)}，总项目 {actual_total}",
                        flush=True,
                    )
                else:
                    print(
                        f"      核对：页面张数 {expected_cards} -> {card_check}"
                        + (
                            f"；收录商品 {expected_total} -> {total_check}"
                            if expected_total is not None
                            else ""
                        ),
                        flush=True,
                    )

                if overall_ok:
                    matched += 1
                else:
                    failed_xml = script_dir / Path("jhs_lorcana_en_phase3_all_last_failed.xml")
                    save_xml_copy(local_xml, failed_xml)
                    print(
                        f"      来源数量存在差异，明细与核对结果已保存，继续下一产品。诊断 XML：{failed_xml.resolve()}",
                        flush=True,
                    )
                    if not args.continue_on_mismatch:
                        print(
                            "      当前数量存在差异，结果已保存。默认停止；可加 --continue-on-mismatch 保留差异并继续其它产品。",
                            flush=True,
                        )
                        break

            except KeyboardInterrupt:
                print("\n已手动停止；已完成产品均已保存。", flush=True)
                return 130
            except Exception as exc:
                product_failed = True
                try:
                    dump_current_page(args.adb, args.serial, local_xml)
                    failed_xml = script_dir / Path("jhs_lorcana_en_phase3_all_last_failed.xml")
                    save_xml_copy(local_xml, failed_xml)
                    print(
                        f"      失败：{exc}\n"
                        f"      已保存诊断 XML：{failed_xml.resolve()}",
                        flush=True,
                    )
                except Exception:
                    print(f"      失败：{exc}", flush=True)

                checks = load_check_map(checks_path)
                checks[key] = {
                    "系列": series,
                    "详情编号": code,
                    "详情名称": name,
                    "页面张数": "" if expected_cards is None else expected_cards,
                    "收录商品数量": "" if expected_total is None else expected_total,
                    "抓取卡牌数": "",
                    "抓取非卡数": "",
                    "抓取总项目数": "",
                    "卡牌核对": "未完成",
                    "总项目核对": "未完成",
                    "统计状态": "失败",
                    "统计时间": now_text(),
                    "备注": str(exc),
                }
                write_csv(checks_path, CHECK_COLUMNS, checks.values())
                if not args.continue_on_mismatch:
                    break

            # 每个产品结束后返回分类页。下一轮仍会重新严格定位 系列 + 编号 + 名称。
            try:
                ensure_category_page(
                    args.adb,
                    args.serial,
                    local_xml,
                    args.wait,
                )
            except Exception:
                pass
            checks = load_check_map(checks_path)

    print("", flush=True)
    print("本次第三步结束", flush=True)
    print(f"本次尝试：{attempted}", flush=True)
    print(f"本次数量比较一致：{matched}（采集是否完成另看完成依据）", flush=True)
    print(f"卡牌结果：{cards_path.resolve()}", flush=True)
    print(f"非卡结果：{noncards_path.resolve()}", flush=True)
    print(f"产品核对：{checks_path.resolve()}", flush=True)
    final_checks = load_check_map(checks_path)
    final_reusable = reusable_product_keys(final_checks, cards_path, noncards_path)
    remaining = sum(product_key(
        row.get("系列", ""), row.get("详情编号", "").strip() or row.get("列表编号", "").strip(),
        row.get("详情名称", "").strip() or row.get("列表名称", "").strip()) not in final_reusable for row in targets)
    print(f"本次所选目标仍未完成：{remaining}；Phase2 未就绪：{len(excluded)}。", flush=True)
    if product_failed:
        return PRODUCT_FAILURE_EXIT_CODE if args.report_review else 1
    if remaining or excluded:
        return REVIEW_EXIT_CODE if args.report_review else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
